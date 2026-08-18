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
DEVICE_HEADER = re.compile(r"\bSTM32[A-Z]?\w*[Xx]\b|\bSTM8\w*[Xx]\b")
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

    Two shapes have to be told apart, because they look similar and mean
    opposite things:

    * **Alternatives.** A summary column can cover several variants at once --
      the F469 Flash row reads ``512 1024 2048`` for the whole
      ``STM32F469Ix`` column. The cell does not answer the question for one
      part, so it is left unread and the API fills in, marked. Taking the
      first number would confidently report the wrong Flash size.
    * **A value with a breakdown.** ``64 (48+16)`` is 64 kB of RAM, split
      across two banks; ``3/(2)(2)`` is three SPIs, two of them I2S-capable.
      Here the leading number *is* the answer.

    The two are distinguished by punctuation: a breakdown is parenthesised or
    slash-delimited, alternatives are bare numbers side by side.
    """
    if not text:
        return None
    stripped = re.sub(r"\([^)]*\)", " ", text)
    head = stripped.split("/", 1)[0]
    found = {n for n in _numbers(head)}
    if len(found) != 1:
        return None
    return found.pop()


def _strip_footnotes(text: str) -> str:
    """``1.8(1)`` is 1.8 with a footnote marker, not 1.81."""
    return re.sub(r"\(\d+\)", "", text or "").strip()


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


def _usart_uart(doc: Document) -> tuple[str | None, str | None]:
    """``USART \\n UART`` is one row carrying ``4\\n2``."""

    def read(rows, column):
        for row in rows[1:]:
            if "usart" in _row_key(row).casefold() and column < len(row):
                numbers = re.findall(r"\d+", (row[column] or ""))
                if len(numbers) >= 2:
                    return numbers[0], numbers[1]
                if numbers:
                    return numbers[0], None
        return None, None

    for fragment in doc.pinned:
        found = read(fragment.rows, fragment.column)
        if found[0] is not None:
            return found
    for fragment in doc.fragments:
        if fragment.column is not None:
            continue
        aligned = doc.column_in(fragment)
        if aligned is not None:
            found = read(fragment.rows, aligned)
            if found[0] is not None:
                return found
        seen = {read(fragment.rows, c) for c in fragment.family_columns}
        seen.discard((None, None))
        if len(seen) == 1:
            return seen.pop()
    return None, None


@reader("summary_i2s")
def read_i2s(doc: Document) -> Reading | None:
    """``3/(2)(2)`` in the SPI/(I2S) row: three SPIs, two I2S-capable."""
    raw = doc.across("comm. interfaces | spi")
    if not raw:
        return None
    match = re.search(r"\d+\s*/\s*\((\d+)\)", raw)
    if not match:
        return None
    return Reading(DATASHEET, match.group(1), doc.summary_caption)


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


FOOTNOTE_MARKER = re.compile(r"\(\d+\)")


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
    """
    for caption, rows in doc.all_named_tables(OPERATING_CONDITIONS):
        header = [_text_of(c).casefold() for c in rows[0]]
        if "min" not in header or "max" not in header:
            continue
        index = header.index(bound)
        for row in rows[1:]:
            symbol = _text_of(row[0]).replace(" ", "").casefold()
            label = _text_of(row[1]).casefold() if len(row) > 1 else ""
            if symbol != "vdd" or "standard operating voltage" not in label:
                continue
            if index >= len(row):
                continue
            raw = _text_of(row[index])
            value = _first_number(_strip_footnotes(raw))
            if not value:
                continue
            if FOOTNOTE_MARKER.search(raw):
                return Reading(
                    AMBIGUOUS,
                    conditions=f"{caption}: V_DD {bound} reads {raw!r} — the "
                    f"footnote qualifies the figure",
                )
            return Reading(DATASHEET, value, caption)
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
