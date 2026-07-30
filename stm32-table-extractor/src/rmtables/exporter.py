"""Build the ST Sidekick JSON-processor document from finalized LogicalTables
+ metadata (SIDEKICK_FORMAT_TASK.md).

Envelope (identical shape for the combined file and every per-table split
file): `{document, rev, url_pdf, references, package, family, core,
frequency, table_count, tables}` -- `tables` is the record array Sidekick's
`rootTagPath` points at.

Record shape: flat, no `metadata` object. Every field a Link URL/Label
Template might reference (`{{document}}`, `{{table_number}}`, `{{title}}`,
`{{url}}`, `{{url_pdf}}`, `{{page}}`, ...) is top-level on the record
itself, because `rootTagPath: tables` means the processor never sees
anything outside the array -- `document`, `rev`, and `url_pdf` are
therefore repeated on every record, not just the envelope. `table_content`
(`headers, rows, notes, legend, semantic_type, semantic`) is the one
remaining nested object, kept as-is. `semantic_type` is computed once and
written to both the top level and `table_content` -- never derived twice.

CLEANUP_TASK.md: there is no `units` field anywhere in this schema -- it
was inherited from the datasheet schema and was always `{}` for reference
manuals. Do not reintroduce a global `units` key; a `parameter`-typed
table's units belong inside its own `semantic` block as a per-parameter
`unit` field (already the case in `semantic.py`'s parameter extractor).

Conventions (fixed): `table_number` is a string; `page` is an int (the
table's start page); `columns` mirrors `table_content.headers`; `headers`
is the table's own first row, `table_content.rows` is DATA ONLY (the
header is never duplicated into it); a merged/spanned or otherwise missing
cell is the empty string `""`, never `null`; `notes` is the de-duplicated
(order-preserving) footnote list; every header cell, `columns` entry,
`title`, and `section_title` is whitespace-trimmed and internally
collapsed to single spaces (row data is left verbatim). `table_id` is
`{document}-T{table_number, zero-padded to 3}` (unnumbered:
`{document}-Tp{page}`), disambiguated further only in the rare case a
duplicate `table_number` survives merging.

RENAME_FIELDS_TASK.md: `table_id`/`text_helper`/`features` were named
`id`/`text`/`tags` prior to this rename -- values and structure are
unchanged, only the keys moved.

Register layouts are intentionally NOT part of this schema -- that track
is separate and incomplete (see RECOVERY_TASK.md); only `LogicalTable`
(caption_table) entries are ever passed in here.

`doc_stem()` (FILENAME_SCHEME_TASK.md) lives here too: the `{RM}_{Rev}`
naming stem shared by the CLI (combined output filename) and `split.py`
(per-manual folder + every per-table filename), so all three can never
drift out of sync.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

from .semantic import extract_semantic
from .semantic_classify import classify_table
from .tags import build_tags

logger = logging.getLogger(__name__)

NOTES_TRUNCATE = 200  # ~chars of joined notes folded into `text_helper`
WIDE_TABLE_COLS = 12  # more columns than this -> Shape B (wide, not necessarily a register map)
ID_NUMBER_WIDTH = 3  # zero-pad width for the numbered form of `table_id` (never truncates)
MAX_REGISTER_NAMES = 12  # TEXT_HELPER_FIX.md Shape A: cap the enumeration, "and {k} more" beyond
MAX_HEADER_NAMES = 8  # TEXT_HELPER_FIX.md Shape B: cap the column-name enumeration

# Filesystem-safe stem characters (FILENAME_SCHEME_TASK.md).
_STEM_UNSAFE_RE = re.compile(r"[^A-Za-z0-9._-]")


def _sanitize_stem_part(text: str, replacement: str = "") -> str:
    return _STEM_UNSAFE_RE.sub(replacement, text or "")


def _build_rev_part(rev: str) -> str:
    """`"Rev 6"` -> `"Rev6"`, `"Rev 21"` -> `"Rev21"`, a bare `"6.0"` ->
    `"Rev6.0"`, empty/unknown -> `""` (FILENAME_SCHEME_TASK.md). Whitespace
    is stripped entirely (not just trimmed) since `rev` is stored with an
    internal space (`"Rev 6"`); a `.` inside a version number is kept, every
    other unsafe character becomes `-`."""
    compact = re.sub(r"\s+", "", (rev or "").strip())
    if not compact:
        return ""
    if not re.match(r"(?i)^rev", compact):
        compact = "Rev" + compact
    return _sanitize_stem_part(compact, replacement="-")


def doc_stem(document: str, rev: str, pdf_path: str | None = None) -> str:
    """Builds the `{RM}_{Rev}` stem shared by the combined output file, the
    per-manual split-tables folder, and every per-table filename -- built
    ONCE here (rather than separately in the exporter/CLI/splitter) so all
    three are guaranteed consistent (FILENAME_SCHEME_TASK.md).

    Falls back to the sanitized PDF filename stem (logged at WARNING) if
    `document` is missing/empty. If `rev` is missing/empty, the revision
    segment is omitted entirely (also logged at WARNING) rather than
    inventing a placeholder like `RevNA`/`Rev0`.
    """
    rm_part = _sanitize_stem_part(document or "")
    if not rm_part:
        fallback = Path(pdf_path).stem if pdf_path else ""
        rm_part = _sanitize_stem_part(fallback) or "UNKNOWN"
        logger.warning(
            "doc_stem: document/name_datasheet is missing or empty; "
            "falling back to %r derived from the PDF filename", rm_part,
        )

    rev_part = _build_rev_part(rev)
    if not rev_part:
        logger.warning(
            "doc_stem: rev is missing/empty for document %r; omitting the "
            "revision segment from the output stem instead of inventing one",
            rm_part,
        )
        return rm_part
    return f"{rm_part}_{rev_part}"


def _dedupe(items: list) -> list:
    seen = set()
    out = []
    for item in items:
        if item not in seen:
            seen.add(item)
            out.append(item)
    return out


def _clean_row(row: list) -> list:
    """`None` (a merge-padding artifact for ragged same-page fragments) is
    never emitted -- the schema requires `""` for every missing cell."""
    return ["" if cell is None else cell for cell in row]


def _is_wide_table(headers: list) -> bool:
    """TEXT_HELPER_FIX.md: pure geometry (headers are mostly bit numbers, or
    more than ~12 columns) -- used ONLY to pick Shape B's "enumerate the
    columns" template for a wide table. It is NOT a register-map test (that
    is `semantic_type == "register_map"`, computed authoritatively by
    `classify_table`/`extract_semantic` upstream); this used to also gate
    the register-map text template directly, which is exactly what made 57
    non-register tables (e.g. RM0490 T142 "DLC coding in FDCAN") falsely
    describe themselves as register maps."""
    if len(headers) > WIDE_TABLE_COLS:
        return True
    if not headers:
        return False
    numeric = sum(1 for h in headers if re.fullmatch(r"\d+", (h or "").strip()))
    return numeric / len(headers) > 0.5


def _register_names_from_semantic(semantic: dict) -> list[str]:
    """Register names for Shape A's enumeration, sourced directly from
    `semantic["registers"][*]["name"]` (TEXT_HELPER_FIX.md) -- first-seen
    order, de-duplicated, blanks skipped. This is the SAME authoritative
    list `extract_register_map` already built; no header scanning needed.
    The deleted `_register_names` scanned for an exact `header.strip().lower()
    == "register"` match, which real ST headers never satisfy (e.g.
    RM0486's merged "Register name Reset value"), so it always returned []
    and fell back to the broken placeholder "the registers listed"."""
    names: list[str] = []
    seen: set[str] = set()
    for reg in semantic.get("registers", []) or []:
        name = (reg.get("name") or "").strip()
        if name and name not in seen:
            seen.add(name)
            names.append(name)
    return names


def _normalize_ws(s: str) -> str:
    """Strips leading/trailing whitespace and collapses internal runs to a
    single space (CLEANUP_TASK.md §3) -- applied to header cells,
    `columns` (the same values), `title`, and `section_title`. Row data is
    never passed through this."""
    return re.sub(r"\s+", " ", (s or "").strip())


# TITLE_FIDELITY_FIX.md Part 1: a printed body caption carries ST's own
# superscript footnote reference (e.g. "...width(1)(2)"), which ST's own
# List of Tables never does -- the marker isn't part of the table's name,
# it's a pointer to a footnote already captured in `notes`, and it pollutes
# the {{title}} used in Sidekick's link label. Pure-digit groups only:
# "Reset source identification (RCC_RSR)(1)" must keep "(RCC_RSR)" -- that
# parenthetical has letters, so `\d{1,2}` never matches it.
_FOOTNOTE_MARKER_RE = re.compile(r"\(\d{1,2}\)")


def _strip_footnote_markers(title: str) -> str:
    return _normalize_ws(_FOOTNOTE_MARKER_RE.sub("", title))


def _reconcile_title_with_lot(n, body_title: str, list_of_tables: dict) -> str:
    """TITLE_FIDELITY_FIX.md Part 2: a handful of body captions are damaged
    by the same rendering artifacts this project has fought throughout
    (an intra-word space, a lost subscript char) -- ST's own List of Tables
    is a clean, independent rendering of the same title, so it is used to
    repair those specific cases. `body_title` is already Part-1-stripped.

    - table absent from the LoT (18 across the corpus -- genuine extras ST
      left out of its own index): always keep the body caption.
    - identical to the LoT entry: keep the body title (nothing to do).
    - differs ONLY by letter case: keep the BODY title -- it is the actual
      printed caption; ST's own index inconsistently title-cases some ETH
      tables, so the LoT is the less trustworthy one here specifically.
    - differs any other way: the LoT is authoritative for the text (not for
      table_number/page -- ST's LoT is wrong at least once there, listing a
      table that doesn't exist in the body: RM0522 "T423 I3C instantiation").
    """
    entry = list_of_tables.get(n)
    if not entry:
        return body_title
    lot_title = _normalize_ws(entry[0])
    if not lot_title or body_title == lot_title:
        return body_title
    if body_title.lower() == lot_title.lower():
        return body_title
    logger.info(
        "table %s title taken from List of Tables (body caption damaged): %r -> %r",
        n, body_title, lot_title,
    )
    return lot_title


# A numbered footnote that is ITSELF a legend, e.g. "3. Legends: SB = start
# bit, STB = stop bit. ..." -- classify.py mirrors this verbatim into
# `legend` (alongside its unchanged copy in `notes`) so both fields agree on
# which table it belongs to; CLEANUP_TASK.md §4 wants only the CONTENT (the
# leading numbering and "Legend(s):" prefix stripped) kept in `legend`. A
# standalone "Legend:" line (captured separately by `legends.find_legends`)
# never carries a leading number, so it doesn't match this and passes
# through unchanged, exactly as before.
NUMBERED_LEGEND_RE = re.compile(r"^\(?\d+\)?[.)]\s*legends?\b.*?[:\-]\s*(.*)$", re.IGNORECASE | re.DOTALL)


def _clean_legend_entry(entry: str) -> str:
    m = NUMBERED_LEGEND_RE.match(entry)
    return m.group(1).strip() if m else entry


def _collapse_trailing_punctuation(text: str) -> str:
    """Strips trailing whitespace and collapses a run of two-or-more
    sentence-final periods (with or without spaces between them) at the
    very end into a single one -- CLEANUP_TASK.md §2: an appended note
    already ends with "." and the template unconditionally adds another
    (e.g. "...M bit value)." + "." -> "...M bit value).."). A lone trailing
    period is left untouched."""
    text = text.rstrip()
    return re.sub(r"\.(?:\s*\.)+$", ".", text)


def _append_notes_suffix(text: str, notes: list) -> str:
    """TEXT_HELPER_FIX.md: applied to all three shapes now (previously only
    the generic/Shape-C branch got it, so 186 register-map tables silently
    dropped their footnotes from the embedded text)."""
    if not notes:
        return text
    joined = "; ".join(notes)
    if len(joined) > NOTES_TRUNCATE:
        joined = joined[:NOTES_TRUNCATE].rstrip() + "..."
    return f"{text} Notes: {joined}."


def _build_text(n, name, section, section_title, page, headers, rows, notes, semantic_type, semantic) -> str:
    """Shape-adaptive, self-contained (name + section + page) summary of a
    table, meant to carry enough context on its own for retrieval.

    TEXT_HELPER_FIX.md: shape is chosen by `semantic_type` (the
    authoritative value `table_to_schema` already computed), not by column
    geometry -- Shape A only fires for a genuine `register_map`. Shape B
    (wide-but-not-a-register-map) and Shape C (everything else) both keep
    `section_title` and the notes suffix, matching Shape A -- previously
    only Shape C had either.
    """
    if semantic_type == "register_map":
        names = _register_names_from_semantic(semantic)
        text = (
            f'Table {n}, "{name}", in section {section} ({section_title}) on '
            f"page {page}. Register map: offsets, 32-bit field layout and reset values"
        )
        if names:
            shown = names[:MAX_REGISTER_NAMES]
            remaining = len(names) - len(shown)
            enum = ", ".join(shown)
            if remaining > 0:
                enum += f" and {remaining} more"
            text += f" for {len(names)} registers: {enum}."
        else:
            # Should not occur -- currently 0 of 4,892 registers are
            # unnamed -- but never invent the old "the registers listed"
            # placeholder; just end the sentence at "reset values."
            text += "."
    elif _is_wide_table(headers):
        shown = headers[:MAX_HEADER_NAMES]
        remaining = len(headers) - len(shown)
        header_enum = ", ".join(shown)
        if remaining > 0:
            header_enum += f", +{remaining} more"
        text = (
            f'Table {n}, "{name}", in section {section} ({section_title}) on '
            f"page {page}. {len(headers)} columns: {header_enum}. {len(rows)} data row(s)."
        )
    else:
        text = (
            f'Table {n}, "{name}", in section {section} ({section_title}) on '
            f"page {page}. Columns: {', '.join(headers)}. {len(rows)} data row(s)."
        )

    text = _append_notes_suffix(text, notes)
    return _collapse_trailing_punctuation(text)


def _make_id(document: str, table_number, page) -> str:
    """`{document}-T{table_number zero-padded to 3}`, or `{document}-Tp{page}`
    when there's no table number at all (SIDEKICK_FORMAT_TASK.md §2)."""
    if table_number is None:
        return f"{document}-Tp{page}"
    return f"{document}-T{table_number:0{ID_NUMBER_WIDTH}d}"


def _ensure_unique_ids(records: list[dict]) -> None:
    """Guarantees `table_id` uniqueness within the document, mutating in
    place -- a no-op for the normal case (every `table_number` already
    unique after MERGE_DUPLICATE_FIX.md's merge). Only engages if a
    duplicate `table_number` survived merging as a genuine parse problem
    (logged as an ERROR there); appends `_2`, `_3`, ... rather than leaving
    two records with the same `table_id`, which the KB relies on for
    dedupe/update."""
    seen: dict[str, int] = {}
    for r in records:
        base = r["table_id"]
        if base not in seen:
            seen[base] = 1
            continue
        seen[base] += 1
        r["table_id"] = f"{base}_{seen[base]}"


def table_to_schema(logical_table, document: str, rev: str, url_pdf: str, list_of_tables: dict | None = None) -> dict:
    """`logical_table` is a `model.LogicalTable`. `document`/`rev`/`url_pdf`
    are repeated onto the record itself (not just the envelope) since
    Sidekick's `rootTagPath: tables` means the processor never sees
    anything outside the array."""
    rows = [_clean_row(r) for r in logical_table.rows]
    headers = [_normalize_ws(h) for h in rows[0]] if rows else []
    body = rows[1:] if len(rows) > 1 else []
    page = logical_table.page_start
    n = logical_table.table_number
    name = _strip_footnote_markers(_normalize_ws(logical_table.caption or ""))
    name = _reconcile_title_with_lot(n, name, list_of_tables or {})
    section = logical_table.section_number or ""
    section_title = _normalize_ws(logical_table.section_title or "")
    notes = _dedupe(logical_table.notes)
    legend = _dedupe(_clean_legend_entry(e) for e in logical_table.legend)

    signal_type, signal = classify_table(name, headers, body)
    semantic_type, semantic = extract_semantic(signal_type, headers, body)
    logger.debug(
        "Table %s classified as %s (%s)%s",
        n, signal_type, signal,
        "" if semantic_type == signal_type else f" -> downgraded to {semantic_type}",
    )
    text_helper = _build_text(
        n, name, section, section_title, page, headers, body, notes, semantic_type, semantic,
    )
    features = build_tags(name, section_title, headers, body)

    return {
        "table_id": _make_id(document, n, page),
        "document": document,
        "rev": rev,
        "table_number": str(n),
        "title": name,
        "page": page,
        "section": section,
        "section_title": section_title,
        "semantic_type": semantic_type,
        "features": features,
        "url": f"{url_pdf}#page={page}",
        "url_pdf": url_pdf,
        "columns": headers,
        "text_helper": text_helper,
        "table_content": {
            "headers": headers,
            "rows": body,
            "notes": notes,
            "legend": legend,
            "semantic_type": semantic_type,
            "semantic": semantic,
        },
    }


def build_document(logical_tables: list, meta: dict, list_of_tables: dict | None = None) -> dict:
    document = meta["name_datasheet"]
    rev = meta["rev"]
    url_pdf = meta["url_pdf"]
    list_of_tables = list_of_tables or {}
    tables = [table_to_schema(lt, document, rev, url_pdf, list_of_tables) for lt in logical_tables]
    _ensure_unique_ids(tables)
    return {
        "document": document,
        "rev": rev,
        "url_pdf": url_pdf,
        "references": meta.get("references", ""),
        "package": meta.get("package", ""),
        "family": meta.get("family", ""),
        "core": meta.get("core", ""),
        "frequency": meta.get("frequency", ""),
        "table_count": len(tables),
        "tables": tables,
    }
