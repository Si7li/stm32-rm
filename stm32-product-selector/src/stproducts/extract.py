"""Read a part's values out of its datasheet.

This is ``verify.py`` promoted from spot-checker to extractor: the same
machinery, now the primary source rather than an optional cross-check.

The non-circular column selection is the point and is kept intact
-----------------------------------------------------------------
The device-summary table has one column per variant, headed with a wildcard
family (``STM32F205Rx``) and told apart by its Flash-size row. Picking that
column using the API's Flash Size would make every subsequent reading an echo
of the API. Instead the size is decoded from the **part number**, using the
map the datasheet itself prints in its ordering-information section::

    Flash memory size
    B = 128 Kbytes of Flash memory
    C = 256 Kbytes of Flash memory

``STM32F205RB`` -> code ``B`` -> 128 Kbytes -> the column whose Flash row
reads 128. Nothing here consults the API. Do not "simplify" this.

Every reading names the table it came from, which is what lets
:mod:`stproducts.provenance` guarantee that no cell claims ``DATASHEET``
without a PDF behind it.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

from .provenance import AMBIGUOUS, DATASHEET, DERIVED, Reading

logger = logging.getLogger("stproducts.extract")

SUMMARY_CAPTION = re.compile(r"features and peripheral counts", re.I)
#: A device-column header: ``STM32F205Rx`` (wildcarded) or an exact part
#: number -- the STM8 tables head their columns ``STM8L162M8``, no x.
DEVICE_HEADER = re.compile(r"\bSTM(?:32|8)[A-Z]\w*\b")
FLASH_CODE_LINE = re.compile(r"^\s*([A-Z])\s*=\s*([\d]+)\s*Kbytes?\b", re.I)
PART_SPLIT = re.compile(r"^(STM32[A-Z]\d{2,3}|STM8[A-Z]{1,3}\d{0,3})([A-Z])([0-9A-Z])", re.I)
OPERATING_CONDITIONS = re.compile(r"general operating conditions", re.I)
TIMER_COMPARISON = re.compile(r"timer feature comparison", re.I)

COVER = "cover page"

READERS: dict[str, callable] = {}


def reader(name: str):
    def register(fn):
        READERS[name] = fn
        return fn

    return register


def _text_of(cell) -> str:
    return re.sub(r"\s+", " ", (cell or "").strip())


def _first_number(text: str | None) -> str | None:
    if not text:
        return None
    match = re.search(r"-?\d+(?:\.\d+)?", text)
    return match.group(0) if match else None


def _numbers(text: str | None) -> list[str]:
    return re.findall(r"-?\d+(?:\.\d+)?", text or "")


def _sole_number(text: str | None) -> str | None:
    """The cell's value, but only when the cell states one.

    Three shapes have to be told apart, because they look similar and mean
    different things:

    * **Alternatives.** A summary column can cover several variants at once --
      the F469 Flash row reads ``512 1024 2048`` for the whole
      ``STM32F469Ix`` column. The cell does not answer the question for one
      part, so it is left unread and the API fills in, marked. Taking the
      first number would confidently report the wrong Flash size.
    * **A value with a breakdown.** ``64 (48+16)`` is 64 kB of RAM, split
      across two banks; ``3/(2)(2)`` is three SPIs, two of them I2S-capable.
      Here the leading number *is* the answer.
    * **Several values in one cell** -- ``2/16`` (converters/channels), GPIOs
      ``80/ 78`` (LDO/SMPS options). Taking the head before the slash used to
      report ``2`` for a cell that never said ``2`` alone; that truncation
      produced corrections that should not happen. A specific reader consumes
      these cells (positional mapping for packed fields, union for supply
      alternatives); the generic reader leaves them unread.
    """
    if not text:
        return None
    stripped = re.sub(r"\([^)]*\)", " ", text)
    found = {n for n in _numbers(stripped)}
    if len(found) != 1:
        return None
    return found.pop()


def _strip_footnotes(text: str) -> str:
    """``1.8(1)`` is 1.8 with a footnote marker, not 1.81."""
    return re.sub(r"\(\d+\)", "", text or "").strip()


#: ``2 (16-bit)``, ``2 (16 bits)``, STM8 ``3/(16-bit)``,
#: H5 ``2 (32 bits) and 8 (16 bits)`` -- every count annotated with its width.
_WIDTH_PAIR = re.compile(r"(\d+)\s*/?\s*\(\s*(\d+)\s*-?\s*bits?\s*\)", re.I)


def _width_pairs(text: str | None) -> list[tuple[int, int]]:
    """Every ``(count, width)`` pair the cell annotates, footnote markers gone."""
    stripped = _strip_footnotes(text or "")
    return [
        (int(m.group(1)), int(m.group(2)))
        for m in _WIDTH_PAIR.finditer(stripped)
    ]


def _consensus(doc: Document, compute):
    """This part's reading of an aggregated row family.

    ``compute(fragment, column)`` gathers the family from one variant column
    and returns None when the fragment carries none of its rows. A pinned or
    geometry-aligned column answers directly. An unpinned fragment whose
    variant columns all compute to the *same* answer needs no alignment --
    the merged-cell case that lets STM32F207IE read ``I2C = 3`` for the whole
    family -- so that unanimous answer is used. Columns that disagree leave
    the family unread rather than guessed.
    """
    for fragment in doc.pinned:
        result = compute(fragment, fragment.column)
        if result is not None:
            return result
    for fragment in doc.fragments:
        if fragment.column is not None:
            continue
        aligned = doc.column_in(fragment)
        if aligned is not None:
            result = compute(fragment, aligned)
            if result is not None:
                return result
            continue
        results = {compute(fragment, c) for c in fragment.family_columns}
        results.discard(None)
        if len(results) == 1:
            return results.pop()
    return None


def _family_matches(header: str, part: str) -> bool:
    """``STM32F205Rx`` matches ``STM32F205RB``; the trailing x is a wildcard."""
    header = header.strip()
    if not header:
        return False
    pattern = "^" + re.escape(header).replace("x", ".").replace("X", ".") + ".*$"
    return re.match(pattern, part, re.I) is not None


def _row_key(row: list) -> str:
    left = _text_of(row[0]) if row else ""
    second = _text_of(row[1]) if len(row) > 1 else ""
    return f"{left} | {second}" if second and second != left else left


def _lookup(rows: list[list], needle: str, column: int) -> str | None:
    value, _ = _lookup_row(rows, needle, column)
    return value


def _lookup_row(rows: list[list], needle: str, column: int) -> tuple[str | None, str]:
    """As :func:`_lookup`, but also returns the label of the row that matched.

    The label is what lets a caller check that the row it found actually
    speaks to the field being filled. Matching is substring-based, so the
    needle ``comm. interfaces | can`` matches a ``Comm. interfaces | CAN FD``
    row just as happily as a plain ``CAN`` one -- the two mean different
    things, and only the label can tell them apart.
    """
    for row in rows[1:]:
        key = _row_key(row)
        if needle.casefold() in key.casefold() and column < len(row):
            return _text_of(row[column]), key
    return None, ""


@dataclass
class Fragment:
    """One piece of the summary table, with its own header row.

    The table runs over several pages and each piece re-heads itself with a
    *different* set of device columns, so a fragment is column-pinned on its
    own terms rather than by carrying an index over from the previous page.

    ``edges`` holds the grid's column boundaries in page x-coordinates
    (``len(edges) == columns + 1``). Fragments of one table share an x-grid
    even when they differ in column *count*, which is what makes a
    continuation fragment readable -- see :meth:`Document.column_in`.
    """

    caption: str
    page: int
    rows: list[list]
    family_columns: list[int]
    edges: list[float] = field(default_factory=list)
    column: int | None = None

    def span(self, index: int) -> tuple[float, float] | None:
        if index + 1 < len(self.edges):
            return self.edges[index], self.edges[index + 1]
        return None


def flash_code_map(pdf) -> dict[str, int]:
    """The ordering-information flash-size legend, read from the datasheet."""
    mapping: dict[str, int] = {}
    for page in reversed(pdf.pages):
        text = page.extract_text() or ""
        if "Flash memory size" not in text:
            continue
        lines = text.split("\n")
        start = next(i for i, line in enumerate(lines) if "Flash memory size" in line)
        for line in lines[start + 1 : start + 14]:
            match = FLASH_CODE_LINE.match(line)
            if match:
                mapping[match.group(1).upper()] = int(match.group(2))
            elif mapping:
                break
        if mapping:
            return mapping
    return mapping


def _captions_above(page) -> list[tuple[float, str]]:
    out = []
    for line in page.extract_text_lines():
        text = re.sub(r"\s+", " ", line["text"]).strip()
        if text.lower().startswith("table") and "....." not in text:
            out.append((line["top"], text))
    return out


def _caption_for(captions: list[tuple[float, str]], top: float) -> str | None:
    above = [(t, c) for t, c in captions if t <= top + 2]
    return above[-1][1] if above else None


@dataclass
class ParsedDatasheet:
    """A datasheet parsed once, independent of which part is being read.

    One PDF serves a whole family -- ``stm32f205rb.pdf`` answers for all 38
    parts of the F2 series -- and parsing it costs a second or two, so it is
    parsed once and each part takes a cheap view over the result. Doing this
    per part instead made a single-file run take minutes.
    """

    path: Path
    cover_text: str = ""
    flash_codes: dict[str, int] = field(default_factory=dict)
    tables: list[tuple[str, list[list]]] = field(default_factory=list)
    #: (caption, page, rows, header, edges) for tables that look like summaries.
    summaries: list[tuple[str, int, list[list], list[str], list[float]]] = field(
        default_factory=list
    )


_PARSE_CACHE: dict[Path, ParsedDatasheet] = {}

#: Where parsed datasheets are persisted between runs. Set by the CLI.
_PARSE_CACHE_DIR: Path | None = None


def set_parse_cache_dir(path: Path | None) -> None:
    """Persist parsed datasheets here, so re-runs skip the PDF work.

    Parsing is the expensive half of a datasheet-first build -- a few hundred
    PDFs at a second or two each -- and it depends only on the file, so it
    survives between runs. Without this every run pays the full cost again.
    """
    global _PARSE_CACHE_DIR
    _PARSE_CACHE_DIR = Path(path) if path else None
    if _PARSE_CACHE_DIR:
        _PARSE_CACHE_DIR.mkdir(parents=True, exist_ok=True)


def _cache_slot(path: Path) -> Path | None:
    if _PARSE_CACHE_DIR is None:
        return None
    stat = path.stat()
    key = f"{path}|{stat.st_size}|{int(stat.st_mtime)}"
    digest = hashlib.sha256(key.encode()).hexdigest()[:16]
    return _PARSE_CACHE_DIR / f"{path.stem}-{digest}.json"


def _load_parsed(slot: Path | None, path: Path) -> ParsedDatasheet | None:
    if slot is None or not slot.exists():
        return None
    try:
        payload = json.loads(slot.read_text())
    except (json.JSONDecodeError, OSError):
        return None
    return ParsedDatasheet(
        path=path,
        cover_text=payload["cover_text"],
        flash_codes=payload["flash_codes"],
        tables=[(c, r) for c, r in payload["tables"]],
        summaries=[(c, n, r, h, e) for c, n, r, h, e in payload["summaries"]],
    )


def _store_parsed(slot: Path | None, parsed: ParsedDatasheet) -> None:
    if slot is None:
        return
    try:
        slot.write_text(
            json.dumps(
                {
                    "cover_text": parsed.cover_text,
                    "flash_codes": parsed.flash_codes,
                    "tables": parsed.tables,
                    "summaries": parsed.summaries,
                }
            )
        )
    except OSError as exc:  # noqa: BLE001 -- a cache miss is not a failure
        logger.debug("could not cache parse of %s: %s", parsed.path, exc)


def _column_edges(table) -> list[float]:
    """The grid's column boundaries, by the same rule ``build_grid`` uses."""
    return sorted(
        {
            round(edge, 1)
            for row in table.rows
            for cell in row.cells
            if cell
            for edge in (cell[0], cell[2])
        }
    )


def parse_datasheet(path: Path) -> ParsedDatasheet:
    """Parse a datasheet's cover, key tables and summary fragments, once."""
    import pdfplumber

    from rmtables.extract import TABLE_SETTINGS, build_grid, flush_page

    path = Path(path).resolve()
    cached = _PARSE_CACHE.get(path)
    if cached is not None:
        return cached

    slot = _cache_slot(path)
    from_disk = _load_parsed(slot, path)
    if from_disk is not None:
        _PARSE_CACHE[path] = from_disk
        return from_disk

    parsed = ParsedDatasheet(path=path)
    with pdfplumber.open(path) as pdf:
        parsed.cover_text = (pdf.pages[0].extract_text() or "") if pdf.pages else ""
        parsed.flash_codes = flash_code_map(pdf)

        for number, page in enumerate(pdf.pages, start=1):
            text = page.extract_text() or ""
            if not (
                SUMMARY_CAPTION.search(text)
                or OPERATING_CONDITIONS.search(text)
                or TIMER_COMPARISON.search(text)
            ):
                continue
            captions = _captions_above(page)
            try:
                found = page.find_tables(table_settings=TABLE_SETTINGS)
            except Exception:  # noqa: BLE001 -- a bad page must not kill the parse
                flush_page(page)
                continue
            chars = page.chars
            for table in found:
                rows = build_grid(table, chars)
                if not rows:
                    continue
                caption = _caption_for(captions, table.bbox[1]) or f"page {number}"
                parsed.tables.append((caption, rows))
                if not SUMMARY_CAPTION.search(caption):
                    continue
                header = [_text_of(c) for c in rows[0]]
                if any(DEVICE_HEADER.search(h) for h in header):
                    parsed.summaries.append(
                        (caption, number, rows, header, _column_edges(table))
                    )
            flush_page(page)

    _store_parsed(slot, parsed)
    _PARSE_CACHE[path] = parsed
    return parsed


@dataclass
class Document:
    """A per-part view over a parsed datasheet, with its columns pinned."""

    part: str
    path: Path
    cover_text: str = ""
    fragments: list[Fragment] = field(default_factory=list)
    tables: list[tuple[str, list[list]]] = field(default_factory=list)
    note: str | None = None

    @property
    def pinned(self) -> list[Fragment]:
        return [f for f in self.fragments if f.column is not None]

    @property
    def summary_caption(self) -> str:
        return self.fragments[0].caption if self.fragments else ""

    @property
    def has_summary(self) -> bool:
        """True when some fragment's column can be identified for this part."""
        return any(self.column_in(f) is not None for f in self.fragments)

    @property
    def pinned_span(self) -> tuple[float, float] | None:
        """The x-range of this part's column in the fragment that pinned it."""
        for fragment in self.pinned:
            span = fragment.span(fragment.column)
            if span:
                return span
        return None

    def column_in(self, fragment: Fragment) -> int | None:
        """This part's column in ``fragment``, pinned or aligned by geometry.

        A continuation fragment usually cannot be pinned: it re-heads itself
        with several variants of the family and carries no Flash row to tell
        them apart. But the fragments of one table share an x-grid, so the
        variant's column can be recovered by overlap even when the column
        *counts* differ.

        In the F2 datasheet the Flash row (page 14) gives ``STM32F205RB``
        the span x=303..338, while the Package row (page 15) has one cell
        spanning 303..374 -- covering both the 128 and 256 variants. Overlap
        picks that cell, giving ``LQFP64``; the 512 variant's 374..418 picks
        the neighbouring ``LQFP64/WLCSP64+2``. Aligning by column *index*
        instead would map 512 to the wrong cell.
        """
        if fragment.column is not None:
            return fragment.column
        span = self.pinned_span
        if span is not None:
            low, high = span
            best, best_overlap = None, 0.0
            for index in fragment.family_columns:
                edges = fragment.span(index)
                if not edges:
                    continue
                overlap = min(high, edges[1]) - max(low, edges[0])
                if overlap > best_overlap:
                    best, best_overlap = index, overlap
            if best is not None:
                return best
        # Last resort, and only when nothing was pinned to align against: a
        # fragment offering exactly one column for this family. Tried *after*
        # geometry, because a continuation often has one column per pin count
        # and picking it directly would answer for the wrong variant.
        if len(fragment.family_columns) == 1:
            return fragment.family_columns[0]
        return None

    def across(self, needle: str) -> str | None:
        """Read a summary row, preferring a pinned column."""
        return self.across_row(needle)[0]

    def across_row(self, needle: str) -> tuple[str | None, str]:
        """Read a summary row, preferring a pinned column; report the row label.

        Falls back to geometric alignment, and then to the rule that an
        unpinned fragment whose variant columns all agree needs no alignment
        -- which is exactly the merged-cell case that gives STM32F207IE its
        I2C = 3: the datasheet states one answer for the whole family.
        Rows that stay undecided are left unread rather than guessed.

        The second element is the label of the row that answered, so callers
        can verify the row speaks to the field they are filling.
        """
        for fragment in self.pinned:
            value, label = _lookup_row(fragment.rows, needle, fragment.column)
            if value:
                return value, label
        for fragment in self.fragments:
            if fragment.column is not None:
                continue
            aligned = self.column_in(fragment)
            if aligned is not None:
                value, label = _lookup_row(fragment.rows, needle, aligned)
                if value:
                    return value, label
            seen: dict[str, str] = {}
            for column in fragment.family_columns:
                value, label = _lookup_row(fragment.rows, needle, column)
                if value:
                    seen[value] = label
            if len(seen) == 1:
                value, label = next(iter(seen.items()))
                return value, label
        return None, ""

    def named_table(self, pattern: re.Pattern) -> tuple[str, list[list]] | None:
        for caption, rows in self.tables:
            if pattern.search(caption):
                return caption, rows
        return None

    def all_named_tables(self, pattern: re.Pattern) -> list[tuple[str, list[list]]]:
        return [(c, r) for c, r in self.tables if pattern.search(c)]


def open_document(path: Path, part: str) -> Document:
    """A view of a (cached) parsed datasheet, pinned to one part's columns."""
    parsed = parse_datasheet(Path(path))

    document = Document(
        part=part,
        path=parsed.path,
        cover_text=parsed.cover_text,
        tables=parsed.tables,
    )
    match = PART_SPLIT.match(part)
    expected_flash = (
        parsed.flash_codes.get(match.group(3).upper())
        if match and parsed.flash_codes
        else None
    )
    for caption, number, rows, header, edges in parsed.summaries:
        columns = [i for i, h in enumerate(header) if _family_matches(h, part)]
        if columns:
            document.fragments.append(Fragment(caption, number, rows, columns, edges))

    # Pin each fragment on its own terms, using the flash size decoded from
    # the part number -- never from the API. A column may cover several
    # variants at once ("512 1024 2048"), which still identifies it as this
    # part's column even though it cannot state this part's Flash size.
    for fragment in document.fragments:
        if expected_flash is None:
            continue
        for candidate in fragment.family_columns:
            listed = _numbers(_lookup(fragment.rows, "flash memory in kbytes", candidate))
            if any(int(float(n)) == expected_flash for n in listed):
                fragment.column = candidate
                break

    if not document.fragments:
        document.note = "no 'features and peripheral counts' table matched this part"
    elif not document.pinned:
        document.note = (
            f"could not pin a variant column (flash code -> "
            f"{expected_flash if expected_flash is not None else 'unknown'} kB)"
        )
    return document


# --------------------------------------------------------------------------
# Readers. Each returns a Reading or None (meaning "the datasheet is silent").
# --------------------------------------------------------------------------


@reader("summary_number")
def read_summary_number(doc: Document, needle: str) -> Reading | None:
    raw, label = doc.across_row(needle)
    value = _sole_number(raw)
    if value is None:
        return None
    return Reading(DATASHEET, value, doc.summary_caption, row=label)


@reader("summary_usart")
def read_usart(doc: Document, ) -> Reading | None:
    usart, _ = _usart_uart(doc)
    return Reading(DATASHEET, usart, doc.summary_caption) if usart else None


@reader("summary_uart")
def read_uart(doc: Document) -> Reading | None:
    _, uart = _usart_uart(doc)
    return Reading(DATASHEET, uart, doc.summary_caption) if uart else None


#: Interface tokens inside a comm row label, longest first so ``LPUART``
#: is never read as ``UART``. The numbers in the cell map positionally onto
#: THIS sequence -- the row label says which interfaces it is splitting.
_UART_TOKENS = re.compile(r"lpuart|usart|uart")


def _token_seq(label: str) -> list[str]:
    """The distinct interface tokens of a row label, in order."""
    seq: list[str] = []
    for token in _UART_TOKENS.findall(label):
        if not seq or seq[-1] != token:
            seq.append(token)
    return seq


def _usart_uart(doc: Document) -> tuple[str | None, str | None, str | None, str | None]:
    """USART, UART and LPUART counts, plus a note when the row lumps what
    ST splits.

    The cell's numbers map onto the interfaces the ROW LABEL names, never
    onto fixed positions. This is what keeps an LPUART out of ``UART typ``:
    STM32L431's row reads ``USART/LPUART = 3 1``, and taking "second number
    = UART" wrote UART = 1 for a part whose only async peripherals are two
    USARTs and one LPUART -- ST's own export puts ``-`` there.

    * label splits them (``USART \\n UART``, ``USART/UART/LPUART``,
      ``USART/LPUART``): each number answers the interface at its position;
      a label without ``uart`` yields no UART even when a second number is
      present.
    * separate per-interface rows (``USART = 3``, then ``UART = 2``,
      ``LPUART = 1``): each interface takes its own row.
    * one lumped number on the USART row and no other async row anywhere --
      the F105 reads ``USART = 5`` where ST publishes 3 + 2. The document
      counts a different thing than the columns ask; asserting 5 for USART
      reported an override that never existed. That case is returned as a
      note, and the callers record it as AMBIGUOUS evidence instead of a
      value.
    """

    def read(rows, column):
        usart = uart = lpuart = None
        note = None
        uart_alone: str | None = None
        lpuart_alone: str | None = None

        def token_seq_of(row):
            low = re.sub(r"\s+", " ", _row_key(row)).casefold()
            return low, _token_seq(low)

        scanned = [
            (row, column, *token_seq_of(row))
            for row in rows[1:]
            if column < len(row)
        ]
        # A table that separates LPUART or UART anywhere is not lumping.
        uart_row_exists = any(
            "uart" in low and "usart" not in low for _, _, low, _ in scanned
        )
        for row, col, low, seq in scanned:
            if not seq:
                continue
            numbers = _numbers(_strip_footnotes(row[col] or ""))
            mapped = dict(zip(seq, numbers))
            if "usart" in mapped and usart is None:
                if len(seq) == 1 and len(numbers) == 1 and not uart_row_exists:
                    # A lone USART number in a table with no UART/LPUART row
                    # at all is the combined asynchronous count (F105:
                    # 5 = 3 + 2). The document counts a different thing than
                    # any of the columns ask.
                    note = (
                        f"summary row reads USART = {numbers[0]} with no "
                        f"UART split; ST publishes USART and UART "
                        f"separately, so the cell's combined count answers "
                        f"neither column"
                    )
                    continue
                usart = mapped["usart"]
                if "uart" in seq:
                    uart = mapped.get("uart")
                if "lpuart" in seq:
                    lpuart = mapped.get("lpuart")
            elif seq == ["uart"] and uart_alone is None:
                # A dedicated UART row ("UART = 2"): answers ``UART typ``
                # when no USART row named a UART itself.
                sole = _sole_number(_strip_footnotes(row[col] or ""))
                if sole is not None:
                    uart_alone = sole
            elif seq == ["lpuart"] and lpuart_alone is None:
                sole = _sole_number(_strip_footnotes(row[col] or ""))
                if sole is not None:
                    lpuart_alone = sole
        return (
            usart,
            uart if uart is not None else uart_alone,
            lpuart if lpuart is not None else lpuart_alone,
            note,
        )

    def answers(found):
        return any(part is not None for part in found[:3]) or found[3] is not None

    for fragment in doc.pinned:
        found = read(fragment.rows, fragment.column)
        if answers(found):
            return found
    for fragment in doc.fragments:
        if fragment.column is not None:
            continue
        aligned = doc.column_in(fragment)
        if aligned is not None:
            found = read(fragment.rows, aligned)
            if answers(found):
                return found
        seen = {read(fragment.rows, c)[:3] for c in fragment.family_columns}
        seen.discard((None, None, None))
        if len(seen) == 1:
            return (*seen.pop(), None)
    return None, None, None, None


@reader("summary_usart")
def read_usart(doc: Document, ) -> Reading | None:
    usart, _, _, lumped = _usart_uart(doc)
    if lumped:
        return Reading(AMBIGUOUS, conditions=lumped)
    return Reading(DATASHEET, usart, doc.summary_caption) if usart else None


@reader("summary_uart")
def read_uart(doc: Document) -> Reading | None:
    _, uart, _, _ = _usart_uart(doc)
    return Reading(DATASHEET, uart, doc.summary_caption) if uart else None


@reader("summary_lpuart")
def read_lpuart(doc: Document) -> Reading | None:
    """The LPUART count -- a peripheral ST's selector does not carry at all.

    The datasheet states it either on its own ``LPUART`` row or as the last
    number of a combined ``USART/UART/LPUART`` row; this column surfaces it
    so the workbook answers with the document instead of losing the fact to
    a missing column. ``-`` means the datasheet states none."""
    _, _, lpuart, _ = _usart_uart(doc)
    return Reading(DATASHEET, lpuart, doc.summary_caption) if lpuart else None


@reader("summary_i2s")
def read_i2s(doc: Document) -> Reading | None:
    """The I2S half of the SPI/(I2S) row.

    Two notations: ``3/(2)(2)`` -- three SPIs, two I2S-capable -- and the
    packed pair ``5/4`` (H7), where the second number is the I2S count just
    as it is on the F2 table's ``SPI(I2S)(2) = 3(2)`` breakdown.
    """
    raw = doc.across("comm. interfaces | spi")
    if not raw:
        return None
    match = re.search(r"\d+\s*/\s*\((\d+)\)", raw)
    if match:
        return Reading(DATASHEET, match.group(1), doc.summary_caption)
    stripped = re.sub(r"\([^)]*\)", " ", raw)
    numbers = _numbers(stripped)
    if len(numbers) >= 2:
        return Reading(DATASHEET, numbers[1], doc.summary_caption)
    return None


@reader("summary_spi")
def read_spi(doc: Document) -> Reading | None:
    """The SPI count, from a bare number or the first of a packed pair.

    ``5/4`` on an ``SPI / I2S`` row is five SPIs and four I2S-capable; the
    old head-before-slash truncation got this right by accident. Keeping it
    here -- explicitly, and only for this row -- lets :func:`_sole_number`
    stop truncating everywhere else.
    """
    raw, label = doc.across_row("comm. interfaces | spi")
    if not raw:
        return None
    value = _sole_number(raw)
    if value is None:
        stripped = re.sub(r"\([^)]*\)", " ", raw)
        numbers = _numbers(stripped)
        if len(numbers) >= 2:
            value = numbers[0]
    if value is None:
        return None
    return Reading(DATASHEET, value, doc.summary_caption, row=label)


def _family_packages(doc: Document) -> list[str]:
    """Every package the summary table names for this part's family."""
    found: list[str] = []
    for fragment in doc.fragments:
        for row in fragment.rows[1:]:
            if not _row_key(row).casefold().startswith("package"):
                continue
            for column in fragment.family_columns:
                if column < len(row):
                    found += [
                        p.strip()
                        for p in re.split(r"[\n/,]", row[column] or "")
                        if p.strip()
                    ]
            break
    return sorted(dict.fromkeys(found))


@reader("summary_package")
def read_package(doc: Document) -> Reading | None:
    # A package list is only this part's when the column was tied to this
    # part -- by the flash code, or by geometry from a column that was.
    # Otherwise it is the whole family's list: the STM32MP2 datasheet offers
    # VFBGA225/VFBGA273/TFBGA289 across the family while ST lists two of them
    # for any given part, and asserting the union would report 41 overrides
    # that are really "the datasheet was answering a different question".
    if doc.pinned_span is None:
        listed = _family_packages(doc)
        if not listed:
            return None
        return Reading(
            AMBIGUOUS,
            conditions=f"{doc.summary_caption} lists {', '.join(listed)} for this "
            f"family; the table gives no key to tie a package to this part",
        )

    packages: list[str] = []
    for fragment in doc.fragments:
        column = doc.column_in(fragment)
        if column is None:
            continue
        for row in fragment.rows[1:]:
            if not _row_key(row).casefold().startswith("package"):
                continue
            if column < len(row):
                packages += [
                    p.strip() for p in re.split(r"[\n/,]", row[column] or "") if p.strip()
                ]
            break
    if not packages:
        return None
    unique = sorted(dict.fromkeys(packages))
    return Reading(DATASHEET, ", ".join(unique), doc.summary_caption)


CORE_ON_COVER = re.compile(
    r"(Arm|ARM)\s*®?\s*(?:32-bit\s*|64-bit\s*)?(Cortex)\s*®?\s*-\s*([AMR]\d+\+?)", re.I
)
FREQUENCY_ON_COVER = re.compile(r"\(\s*(\d{2,4})\s*MHz\s*max", re.I)


@reader("cover_core")
def read_core(doc: Document) -> Reading | None:
    """``Core: Arm® 32-bit Cortex®-M3 CPU`` -> ``Arm Cortex-M3``.

    Only when the cover names one core. A heterogeneous device names
    several -- the STM32MP1 cover has both Cortex-A7 and Cortex-M4 -- and the
    document does not say which one ST calls "the" core. ST in fact splits
    them, putting the Cortex-M4 in a separate ``Co-Processor type`` column,
    so asserting a combined value here would report an override on every
    such part. The cores found are recorded as evidence instead.
    """
    names = []
    for match in CORE_ON_COVER.finditer(doc.cover_text):
        name = f"Arm Cortex-{match.group(3).upper()}"
        if name not in names:
            names.append(name)
    if not names:
        return None
    if len(names) > 1:
        return Reading(
            AMBIGUOUS,
            conditions=f"cover names {', '.join(names)}; ST reports the "
            f"application core here and the others as co-processors",
        )
    return Reading(DATASHEET, names[0], COVER)


@reader("cover_frequency")
def read_frequency(doc: Document) -> Reading | None:
    matches = FREQUENCY_ON_COVER.findall(doc.cover_text)
    if not matches:
        return None
    return Reading(DATASHEET, str(max(int(m) for m in matches)), COVER)


@reader("operating_conditions_voltage")
def read_voltage(doc: Document, bound: str) -> Reading | None:
    """The ``V DD`` row of ``Table N. General operating conditions``.

    A footnote marker on the figure means the document is qualifying it, and
    the qualification is where ST's published value often comes from: the F4
    row reads ``1.8(1)`` while ST publishes 1.7, because footnote (1) gives
    the lower limit under a stated condition. Taking the bare number there
    would report 157 parts as ST being wrong when the datasheet in fact
    agrees with it, in the small print. So a footnoted figure is recorded as
    AMBIGUOUS with the raw cell, and the API's value stands.

    The footnote can also be *layered inside* the number itself: the L4/L4+/L5
    row reads ``1(.71)1`` because the lattice parse dropped the footnote glyph
    between the digits, and a footnote marker like ``(1)`` is wrapped in the
    decimal. ``_strip_footnotes`` cannot see that, so it is caught here: any
    parentheses in the cell disqualify the figure, no matter the shape.

    Some rows split the condition into several labelled sub-rows (the L1
    ``V DD`` row reads ``1.65`` for BOR disabled and ``1.8`` at power-on).
    Which one ST publishes is not visible from the grid, so distinct values
    across the condition rows are also recorded as AMBIGUOUS.
    """
    for caption, rows in doc.all_named_tables(OPERATING_CONDITIONS):
        header = [_text_of(c).casefold() for c in rows[0]]
        if "min" not in header or "max" not in header:
            continue
        index = header.index(bound)
        readings = []
        for row in rows[1:]:
            symbol = _text_of(row[0]).replace(" ", "").casefold()
            label = _text_of(row[1]).casefold() if len(row) > 1 else ""
            if symbol != "vdd" or "standard operating voltage" not in label:
                continue
            if index >= len(row):
                continue
            raw = _text_of(row[index])
            if re.search(r"\(", raw):
                return Reading(
                    AMBIGUOUS,
                    conditions=f"{caption}: V_DD {bound} reads {raw!r} — the "
                    f"figure is qualified or corrupted by a footnote",
                )
            value = _first_number(_strip_footnotes(raw))
            if value:
                readings.append((value, raw))
        if not readings:
            continue
        values = {value for value, _ in readings}
        if len(values) == 1:
            return Reading(DATASHEET, readings[0][0], caption)
        detail = "; ".join(f"{value} ({raw})" for value, raw in readings)
        return Reading(
            AMBIGUOUS,
            conditions=f"{caption}: V_DD {bound} varies by condition "
            f"({detail}) — the API's value stands",
        )
    return None


TEMPERATURE_RANGE = re.compile(r"(–|-|−)?\s*(\d+)\s*to\s*\+?\s*(–|-|−)?\s*(\d+)")


@reader("summary_temperature")
def read_temperature(doc: Document, bound: str) -> Reading | None:
    """The ``Operating temperatures`` row's *ambient* ranges.

    The row reads ``Ambient temperatures: -40 to +85 °C / -40 to +105 °C`` --
    two ranges, because the family is sold in two temperature grades chosen
    by an ordering-code suffix that is not part of the base part number. The
    minimum is the same in both, so it is settled; the maximum is not, and
    saying which one ST publishes for a given part would be a guess. So a
    multi-range row yields DATASHEET for min and AMBIGUOUS for max.
    """
    raw = None
    for fragment in doc.fragments:
        column = doc.column_in(fragment)
        if column is None:
            continue
        for row in fragment.rows[1:]:
            key = _row_key(row).casefold()
            if "temperature" not in key:
                continue
            if column < len(row) and "ambient" in (row[column] or "").casefold():
                raw = _text_of(row[column])
                break
        if raw:
            break
    if not raw:
        return None

    ranges = []
    for match in TEMPERATURE_RANGE.finditer(raw):
        low = -int(match.group(2)) if match.group(1) else int(match.group(2))
        high = -int(match.group(4)) if match.group(3) else int(match.group(4))
        ranges.append((low, high))
    if not ranges:
        return None

    values = {r[0] for r in ranges} if bound == "min" else {r[1] for r in ranges}
    if len(values) == 1:
        return Reading(DATASHEET, str(values.pop()), doc.summary_caption)
    return Reading(
        AMBIGUOUS,
        conditions=f"{raw} (datasheet offers {len(values)} grades; "
        f"the ordering-code suffix selects one)",
    )


@reader("supply_current")
def read_supply_current(doc: Document, mode: str) -> Reading | None:
    """Record what the datasheet has, without picking a row.

    Current consumption appears only as tables spread across temperature,
    voltage and mode, and nothing in the document identifies the row ST
    publishes. So this records the candidate tables as evidence and leaves
    the value to the API.
    """
    wanted = re.compile(
        r"current consumption in run mode" if mode == "run mode" else r"current consumption",
        re.I,
    )
    captions = [c for c, _ in doc.tables if wanted.search(c)]
    if not captions:
        return None
    return Reading(
        AMBIGUOUS,
        conditions="; ".join(sorted(dict.fromkeys(captions))[:4]),
    )


# --- derived -------------------------------------------------------------


TIMER_CLASS_ROWS = {
    "general purpose": "timers | general-purpose",
    "advanced-control": "timers | advanced-control",
    "basic": "timers | basic",
}


def _timer_inventory(doc: Document) -> tuple[dict[str, list[tuple[str, int]]], str] | None:
    """From ``Table N. Timer feature comparison``: class -> [(timer, width)]."""
    found: dict[str, list[tuple[str, int]]] = {}
    caption_used = ""
    for caption, rows in doc.all_named_tables(TIMER_COMPARISON):
        header = [_text_of(c).casefold() for c in rows[0]]
        if not header or "timer type" not in header[0]:
            continue
        try:
            width_col = next(
                i for i, h in enumerate(header) if "resolution" in h
            )
        except StopIteration:
            continue
        caption_used = caption
        for row in rows[1:]:
            klass = _text_of(row[0]).casefold().replace("- ", "-")
            klass = re.sub(r"\s+", " ", klass)
            if not klass or klass == "timer type":
                continue
            if width_col >= len(row):
                continue
            width = _first_number(_text_of(row[width_col]))
            if not width:
                continue
            names = [
                n.strip()
                for n in re.split(r"[,\n]", _text_of(row[1]) if len(row) > 1 else "")
                if n.strip()
            ]
            for name in names:
                found.setdefault(klass, []).append((name, int(width)))
    return (found, caption_used) if found else None


@reader("derived_timer_split")
def read_timer_split(doc: Document, width: int) -> Reading | None:
    """Join the summary table's per-class counts with the timer inventory.

    The summary table says how many general-purpose / advanced-control /
    basic timers a variant has; ``Table N. Timer feature comparison`` says
    how wide each named timer is, for the family. Joining them gives the
    16/32-bit split -- but only when the per-class counts agree, because a
    variant with fewer timers than the family does not say *which* ones it
    drops. When they disagree the field falls back to AMBIGUOUS rather than
    guessing, exactly as the build spec requires.
    """
    inventory = _timer_inventory(doc)
    if inventory is None:
        return None
    table, caption = inventory

    def klass_of(name: str) -> str | None:
        for klass in table:
            if name in klass or klass in name:
                return klass
        return None

    mismatches = []
    checked = 0
    for label, needle in TIMER_CLASS_ROWS.items():
        expected = _sole_number(doc.across(needle))
        if expected is None:
            continue
        checked += 1
        klass = klass_of(label)
        listed = len(table.get(klass, [])) if klass else 0
        if int(float(expected)) != listed:
            mismatches.append(f"{label}: summary {expected}, inventory {listed}")

    if not checked:
        # Nothing to join against: the summary table has no per-class timer
        # counts for this variant, so the family-wide inventory says nothing
        # about what *this* part carries. Without this guard the check passes
        # vacuously and the family's total is asserted for every variant.
        return Reading(
            AMBIGUOUS,
            conditions=(
                f"{caption} lists the family's timers, but the summary table "
                f"gives no per-class counts for this variant to join against"
            ),
        )

    if mismatches:
        return Reading(
            AMBIGUOUS,
            conditions=(
                f"{caption} lists the family's timers, but this variant's "
                f"counts differ ({'; '.join(mismatches)}), and the table does "
                f"not say which timers the variant omits"
            ),
        )

    count = sum(
        1 for entries in table.values() for _, bits in entries if bits == width
    )
    if count == 0 and width == 32:
        # A family with no 32-bit timers is a real answer, not a failure.
        return Reading(DERIVED, "0", caption)
    if count == 0:
        return None
    return Reading(DERIVED, str(count), caption)


# --- multi-type sub-rows: aggregate every type, never just the first ------


_TIMER_ROW_SKIP = re.compile(r"systick|watchdog|pwm|rtc", re.I)


def _timer_rows_of(fragment: Fragment, column: int):
    """The timer rows of one variant column, as ``(label, width pairs)``.

    Also counts rows that carry a bare number with NO width annotation --
    a table annotating only some of its timer rows states an incomplete
    sum, and an incomplete sum must not be asserted.
    """
    found: list[tuple[str, list[tuple[int, int]]]] = []
    seen: set[tuple[str, tuple[tuple[int, int], ...]]] = set()
    unannotated = 0
    for row in fragment.rows[1:]:
        key = _row_key(row)
        low = re.sub(r"\s+", " ", key).casefold()
        if not low.startswith("timer") or _TIMER_ROW_SKIP.search(low):
            continue
        if column >= len(row):
            continue
        cell = _strip_footnotes(_text_of(row[column]))
        pairs = _width_pairs(cell)
        if pairs:
            signature = (low, tuple(sorted(pairs)))
            if signature in seen:
                continue
            seen.add(signature)
            found.append((low, pairs))
        elif _sole_number(cell) is not None:
            unannotated += 1
    return found, unannotated


@reader("derived_timer_width")
def read_timer_width(doc: Document, width: int) -> Reading | None:
    """Sum of the summary table's counts annotated with this timer width.

    ST annotates each timer row's count with its counter resolution --
    ``Basic = 2 (16-bit)``, ``General purpose = 5 (16-bit) 1 (32-bit)``,
    STM8 ``3/(16-bit)`` -- and the selector's ``Timers (16-bit) typ`` column
    aggregates exactly these. The first reader took only the FIRST matching
    row (G431 missed the low-power timer, 9 against ST's 10).

    Where the table carries no annotations at all (F2-style class counts),
    there is nothing to sum and the inventory join below answers instead.
    Note that ST's own selector does not always agree with its own annotated
    sums -- H543's datasheet adds up to 10 x 16-bit while ST publishes 5 --
    and this reader writes the document's statement, leaving the diff to
    report the disagreement.
    """

    def compute(fragment, column):
        rows, unannotated = _timer_rows_of(fragment, column)
        if not rows:
            return None
        if unannotated:
            # Some timer rows carry counts without widths (F301's unmarked
            # Basic row): the annotated sum is known-incomplete, so it
            # asserts nothing and the inventory join answers instead.
            return None
        total = sum(
            count for _, pairs in rows for count, bits in pairs if bits == width
        )
        detail = ", ".join(
            f"{label}: {count} x {bits}-bit"
            for label, pairs in rows
            for count, bits in pairs
            if bits == width
        )
        return total, detail

    found = _consensus(doc, compute)
    if found is None:
        return read_timer_split(doc, width)
    total, detail = found
    if total == 0:
        if width == 32:
            # Annotated rows that never say 32-bit: a family without one is a
            # real answer, not a failure -- same rule as the inventory join.
            return Reading(
                DERIVED,
                "0",
                doc.summary_caption,
                conditions="no timer row carries a 32-bit annotation",
            )
        return None
    return Reading(
        DERIVED,
        str(total),
        doc.summary_caption,
        conditions=f"sum of the width-annotated timer counts ({detail})",
    )


_GPIO_ROW_SKIP = re.compile(r"wake|supplied by", re.I)
_SUPPLY_OPTION = re.compile(r"ldo|smps|legacy", re.I)


def _gpio_options(raw: str) -> list[int | None] | None:
    """The numbers of one GPIO cell, one per option.

    ``26`` is one value; ``80/ 78`` is LDO vs SMPS; ``9 (UFQFPN32)/10
    (LQFP32)`` qualifies each option with its package; ``NA`` marks an
    option the row does not offer. Anything not numeric -- ``yes``, prose --
    leaves the row unread rather than guessed.
    """
    text = _strip_footnotes(_text_of(raw))
    # Options may be separated by a line break as well as a slash:
    # ``9 (UFQFPN32)\n10 (LQFP32)`` -- ``_text_of`` leaves ``) 9``-style
    # junctions, which are option boundaries, not one number.
    text = re.sub(r"\)\s+(?=\d+\s*\()", ") / ", text)
    options: list[int | None] = []
    for part in text.split("/"):
        part = re.sub(r"\([^)]*\)", " ", part).strip()
        if not part:
            continue
        if part.casefold() in ("na", "n/a", "-"):
            options.append(None)
            continue
        if not part.isdigit():
            return None
        options.append(int(part))
    return options or None


def _componentwise_sum(gathered: list[tuple[str, list[int | None]]]):
    """Positional sum across type rows that carry per-option values.

    F301's Normal row reads ``9 (UFQFPN32)/10 (LQFP32)`` beside a flat
    5V-tolerant 15: the part's totals are 24 or 25 depending on package --
    exactly the ``24||25`` ST publishes. Single-valued rows broadcast;
    option columns containing an NA cannot be completed and stay unread.
    """
    length = max(len(opts) for _, opts in gathered)
    if any(len(opts) > 1 and len(opts) != length for _, opts in gathered):
        return None
    totals: list[int] = []
    for index in range(length):
        total = 0
        for _, opts in gathered:
            values = opts if len(opts) > 1 else opts * length
            if values[index] is None:
                return None
            total += values[index]
        totals.append(total)
    return totals


def _gpio_rows_of(fragment: Fragment, column: int):
    """ ``(gathered rows, supply-option mode?)`` for one column.

    Supply mode is decided by the row LABELS alone (``Legacy``, ``SMPS``,
    ``LDO``): slash cells on plain type rows are per-package options that
    sum positionally, not alternatives.
    """
    gathered: list[tuple[str, list[int | None]]] = []
    seen: set[tuple[str, tuple[int | None, ...]]] = set()
    supply = False
    for row in fragment.rows[1:]:
        key = _row_key(row)
        label = re.sub(r"\s+", " ", key).casefold()
        if not label.startswith("gpio"):
            continue
        if _GPIO_ROW_SKIP.search(label):
            continue
        if column >= len(row):
            continue
        options = _gpio_options(row[column])
        if options is None:
            continue
        if _SUPPLY_OPTION.search(label):
            supply = True
        signature = (label, tuple(options))
        if signature in seen:
            continue
        seen.add(signature)
        gathered.append((label, options))
    return gathered, supply


@reader("summary_gpio_total")
def read_gpio_total(doc: Document) -> Reading | None:
    """All GPIO sub-rows aggregated -- summed or unioned, never first-only.

    Type rows are complementary and add up positionally: F334's ``Normal
    I/Os = 20`` plus ``5-Volt tolerant = 17`` is the 37 ST publishes, and
    F301's package-qualified ``9 (UFQFPN32)/10 (LQFP32)`` beside a flat 15
    gives ``24, 25`` -- ST's own multivalue. Supply-option rows
    (``Legacy``/``SMPS``, ``(LDO / SMPS)``, cells like ``80/ 78``) are
    mutually exclusive alternatives instead, and ST publishes their UNION --
    ``78, 80`` for a part sellable either way. Wake-up-pin rows and
    ``supplied by VDDIO2`` subset rows answer other questions and stay out
    of both.
    """

    def compute(fragment, column):
        gathered, supply = _gpio_rows_of(fragment, column)
        if not gathered:
            return None
        labels = "; ".join(label for label, _ in gathered)
        if supply:
            values = sorted(
                {n for _, opts in gathered for n in opts if n is not None}
            )
            if not values:
                return None
            written = ", ".join(str(n) for n in values)
            note = f"supply alternatives {'/'.join(str(n) for n in values)}"
        else:
            totals = _componentwise_sum(gathered)
            if not totals:
                return None
            written = ", ".join(str(n) for n in sorted(set(totals)))
            note = "sum of type sub-rows"
        return written, note, labels

    found = _consensus(doc, compute)
    if found is None:
        return None
    written, note, labels = found
    return Reading(
        DATASHEET,
        written,
        doc.summary_caption,
        row=labels,
        conditions=note,
    )


#: Two facts packed into one cell: ``2/16``, ``2\n16`` -- converters, then
#: channels. (The printed table separates them with a slash or a line break;
#: ``_text_of`` has already collapsed the newline to a space.)
_ADC_PAIR = re.compile(r"^(\d+)\s*[/\s]\s*(\d+)$")


def _adc_rows_of(fragment: Fragment, column: int, width: str):
    """This fragment's ADC cells for one bit-width group, in table order.

    Deduplicated by ``(label, cell)`` so a continuation repeating a row does
    not double it, but two genuinely distinct rows sharing a label (the F2
    table stacks ``12-bit ADC / Number of channels`` twice: converters, then
    channels) both survive.
    """
    found: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    marker = width if width.endswith("-bit") else f"{width}-bit"
    for row in fragment.rows[1:]:
        key = _row_key(row)
        low = re.sub(r"\s+", " ", key).casefold()
        if "adc" not in low or marker not in low:
            continue
        if column >= len(row):
            continue
        raw = _strip_footnotes(_text_of(row[column]))
        if not raw:
            continue
        signature = (low, raw)
        if signature in seen:
            continue
        seen.add(signature)
        found.append((low, raw))
    return found


def _adc_answer(rows: list[tuple[str, str]]) -> tuple[str | None, str | None]:
    """(converters, channels) from one column's ADC rows, or (None, None)."""
    converter_row = next(
        (
            raw
            for label, raw in rows
            if ("number of adc" in label or label.rstrip("s").endswith("adc"))
            and "channel" not in label
        ),
        None,
    )
    if converter_row is not None:
        value = _sole_number(converter_row)
        # A dedicated converter-count row means the neighbouring channel
        # cells answer per converter (H7's fast/slow pairs), not in total.
        return (value, None) if value else (None, None)

    for label, raw in rows:
        pair = _ADC_PAIR.match(raw.strip())
        if pair and "channel" in label:
            return pair.group(1), pair.group(2)
    by_label: dict[str, list[str]] = {}
    for label, raw in rows:
        by_label.setdefault(label, []).append(raw)
    for label, raws in by_label.items():
        if (
            len(raws) >= 2
            and "channel" in label
            and all(_sole_number(r) for r in raws[:2])
        ):
            return _sole_number(raws[0]), _sole_number(raws[1])
    return None, None


@reader("summary_adc_pair")
def read_adc_pair(doc: Document, width: str, which: str) -> Reading | None:
    """Converters / channels from the ADC rows of one bit-width group.

    The summary table packs both facts into one cell -- F105's
    ``12-bit ADC Number of channels = 2/16`` is two converters with sixteen
    channels, and ST publishes exactly those two numbers in the two grouped
    columns. Positional mapping, stated on the cell itself.
    """

    def compute(fragment, column):
        rows = _adc_rows_of(fragment, column, width)
        if not rows:
            return None
        converters, channels = _adc_answer(rows)
        if converters is None and channels is None:
            return None
        return converters, channels

    found = _consensus(doc, compute)
    if found is None:
        return None
    converters, channels = found
    picked = converters if which == "converters" else channels
    if picked is None:
        return None
    return Reading(DATASHEET, str(picked), doc.summary_caption)


@reader("summary_dac")
def read_dac(doc: Document) -> Reading | None:
    """What ST's ``D/A Converters (12-bit) typ`` column publishes.

    ST's own convention varies by family and each table encodes which one it
    means: where a controller/converters count row exists it is the answer
    (H523: ``12-bit DAC controller = 1``, ST publishes 1), otherwise the
    channel count is (F105 ``Yes\\n2`` -> 2, H733 ``Number of channels = 2``
    -> 2). A bare number on some other DAC row (comparators, op-amps) is
    not this fact."""

    def compute(fragment, column):
        controller = channels = None
        for row in fragment.rows[1:]:
            key = _row_key(row)
            low = re.sub(r"\s+", " ", key).casefold()
            if "dac" not in low or column >= len(row):
                continue
            raw = _strip_footnotes(_text_of(row[column]))
            if "controller" in low or "number of dac" in low:
                value = _sole_number(raw)
                if value is not None and controller is None:
                    controller = value
            elif "channel" in low:
                value = _sole_number(raw)
                if value is not None and channels is None:
                    channels = value
            elif "present" in low or low.rstrip("s").endswith("dac"):
                pair = re.fullmatch(r"(?:yes|no)?\s*(\d+)", raw, re.I)
                if pair and channels is None:
                    channels = pair.group(1)
        return controller if controller is not None else channels

    value = _consensus(doc, compute)
    if value is None:
        return None
    return Reading(DATASHEET, str(value), doc.summary_caption)


# --- set-valued fields the summary table can only partly speak to ---------

#: ST token -> the summary-table row that decides it, for each set field.
SPEAKABLE = {
    "additional_interfaces": {
        "SD/MMC": "sdio",
        "Ethernet": "ethernet",
        "Parallel camera interface": "camera interface",
    },
    "usb_type": {
        "USB OTG FS": "usb otg fs",
        "USB OTG HS": "usb otg hs",
    },
    "other_timer_functions": {
        "RTC": "rtc",
        "IWDG": "iwdg",
        "WWDG": "wwdg",
    },
}


def _yes(text: str | None) -> bool | None:
    if not text:
        return None
    low = text.casefold()
    if low.startswith("yes"):
        return True
    if low.startswith("no"):
        return False
    return None


@reader("read_speakable_set")
def read_speakable_set(doc: Document, which: str, api_tokens: list[str]) -> Reading | None:
    """Answer a set-valued field only when the table knows every token in play.

    ST's vocabulary for these columns is wider than the summary table: it
    writes ``SAI``, ``DFSDM``, ``S/PDIF``, ``SysTick``, ``USB Type-C`` and
    more, none of which the table mentions. Composing a value from the rows
    that do exist would write something *incomplete* and then report it as an
    override against ST on nearly every part.

    So: if every token ST uses for this part is one the table can decide, the
    datasheet supplies the value. Otherwise the API value stands and the rows
    that were read are recorded as evidence.
    """
    vocabulary = SPEAKABLE.get(which)
    if not vocabulary or not doc.has_summary:
        return None

    evidence: dict[str, bool] = {}
    for token, needle in vocabulary.items():
        state = _yes(doc.across(needle))
        if state is not None:
            evidence[token] = state
    if not evidence:
        return None

    recorded = ", ".join(f"{t}={'Yes' if v else 'No'}" for t, v in sorted(evidence.items()))
    unspeakable = [t for t in api_tokens if t not in vocabulary]
    if unspeakable:
        return Reading(
            AMBIGUOUS,
            conditions=(
                f"{doc.summary_caption} decides {recorded}; ST also lists "
                f"{', '.join(unspeakable)}, which the table does not mention"
            ),
        )

    present = sorted(t for t, v in evidence.items() if v)
    return Reading(DATASHEET, ", ".join(present) if present else "", doc.summary_caption)


@dataclass
class PartExtraction:
    """Everything the datasheet yielded for one part."""

    part: str
    datasheet: Path | None = None
    readings: dict[str, Reading] = field(default_factory=dict)
    summary_table: str = ""
    note: str | None = None
    #: Readings dropped because the row they came from does not speak to the
    #: column, as ``column key -> row label``. Reported, not silently binned.
    rejected_rows: dict[str, str] = field(default_factory=dict)

    @property
    def datasheet_fields(self) -> int:
        return sum(1 for r in self.readings.values() if r.token in (DATASHEET, DERIVED))


def extract_part(
    path: Path,
    part: str,
    column_keys: list[str],
    api_tokens: dict[str, list[str]],
) -> PartExtraction:
    """Run every configured reader for one part against its datasheet."""
    from .fieldmap import spec_for

    result = PartExtraction(part=part, datasheet=Path(path))
    try:
        document = open_document(Path(path), part)
    except Exception as exc:  # noqa: BLE001 -- a broken PDF must not kill the run
        result.note = f"could not read datasheet: {exc}"
        return result

    result.summary_table = document.summary_caption
    result.note = document.note

    for key in column_keys:
        spec = spec_for(key)
        if not spec.reader:
            continue
        fn = READERS.get(spec.reader)
        if fn is None:
            continue
        args = list(spec.args)
        if spec.reader == "read_speakable_set":
            args.append(api_tokens.get(key, []))
        try:
            reading = fn(document, *args)
        except Exception as exc:  # noqa: BLE001 -- one bad field must not kill the part
            logger.debug("reader %s failed for %s/%s: %s", spec.reader, part, key, exc)
            continue
        if reading is None:
            continue
        # Naming a source table is not enough: the row that answered has to
        # speak to this column. A "CAN" row cannot fill "CAN (FD)".
        if reading.token in (DATASHEET, DERIVED) and not spec.row_supports(reading.row):
            logger.debug(
                "%s/%s: row %r does not support this column; falling back",
                part, key, reading.row,
            )
            result.rejected_rows[key] = reading.row
            continue
        result.readings[key] = reading
    return result
