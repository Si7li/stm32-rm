"""Ruled-grid + caption extraction for errata sheets.

Every ST errata sheet shares the same skeleton: a captioned ruled table
("Table N. <Title>") with the caption printed above the grid, one table that
spans two pages (the "Summary of device limitations" status matrix continues
without a caption on the following page), numeric footnotes below the grid,
and possibly a "Legends:" line. This module recovers those grids
deterministically (mirrors rmtables.extract + rmtables.cells):

  - ruled grids via `find_tables` with pure-lines settings;
  - merged cells ("Status" spanning the two revision columns) are exploded so
    the spanned text repeats across every covered grid position;
  - phantom grids (the page-footer line + rules) are rejected on geometry;
  - caption line immediately above a grid opens a new table;
- a grid with no caption above it is merged into the preceding grid ONLY
      when it repeats that grid's own header row -- a true CONTINUATION
      fragment; uncaptioned embedded tables (ADC spec excerpts, workaround
      illustrations) are skipped and flagged for audit;
    - two-level headers (Table 3) are kept exactly as printed: the grid's first
      row is the `headers`, the column-label row stays the first DATA row.
"""

from __future__ import annotations

import logging
import re

logger = logging.getLogger("rmerrattables.tables")

CAPTION_RE = re.compile(r"^\s*Table\s+(\d+)\s*[.:]?\s*(.*)$")
CONTINUED_RE = re.compile(r"\(continued\)\s*$", re.IGNORECASE)
HEADING_RE = re.compile(r"^\s*(\d+(?:\.\d+)*)\s+([A-Z][A-Za-z0-9 /&()'\-+]*)\s*$")
FOOTNOTE_RE = re.compile(r"^\s*(\d+)\s*\.\s+(.*)$")
LEGEND_RE = re.compile(r"^\s*Legends?[:\-]?\s*(.*)$", re.IGNORECASE)
LEGEND_ITEM_RE = re.compile(r"^\s*([ANP\-])\s+(.*)$")
UNNUMBERED_RE = re.compile(r"^[A-Z][A-Za-z]+(?: [A-Za-z][A-Za-z0-9]*)+$")
FOOTER_RE = re.compile(r"^\s*ES\d{4}\b")

OPTS = {
    "vertical_strategy": "lines",
    "horizontal_strategy": "lines",
    "snap_tolerance": 3,
    "join_tolerance": 3,
    "edge_min_length": 3,
    "intersection_tolerance": 3,
}


# ── grid construction (rmtables.cells mirror) ───────────────────────────────

def cell_text(chars, cell, y_tol: float = 4.0) -> str:
    """Chars inside a drawn cell, grouped into lines by vertical position,
    ordered left-to-right, joined with newlines (whitespace collapsed by the
    exporter)."""
    inside = [
        c for c in chars
        if cell[0] - y_tol <= c["x0"] and c["x1"] <= cell[2] + y_tol
        and cell[1] - y_tol <= c["top"] and c["bottom"] <= cell[3] + y_tol
    ]
    if not inside:
        return ""
    inside.sort(key=lambda c: (round(c["top"] / y_tol), c["x0"]))
    lines: list[list] = []
    for c in inside:
        if lines and abs(c["top"] - lines[-1][0]) <= y_tol:
            lines[-1][1].append(c)
        else:
            lines.append([c["top"], [c]])
    return "\n".join("".join(c["text"] for c in line) for _top, line in lines)


def build_grid(table, chars) -> list[list[str]]:
    """Explode merged cells: each drawn cell's text fills every grid position
    its rectangle covers (rowspan + colspan); uncovered positions stay ""."""
    rows = table.rows
    if not rows:
        return []
    xs = sorted({round(e, 1) for r in rows for cell in r.cells
                 if cell for e in (cell[0], cell[2])})
    ys = sorted({round(r.bbox[1], 1) for r in rows} | {round(rows[-1].bbox[3], 1)})
    ncol = len(xs) - 1
    nrow = len(rows)
    if ncol < 1 or nrow < 1:
        return []

    def cols(x0, x1):
        return [i for i in range(ncol)
                if not (x1 <= xs[i] + 0.5 or x0 >= xs[i + 1] - 0.5)]

    def rws(top, bottom):
        return [i for i in range(nrow)
                if not (bottom <= ys[i] + 0.5 or top >= ys[i + 1] - 0.5)]

    grid = [["" for _ in range(ncol)] for _ in range(nrow)]
    seen = set()
    for r in rows:
        for cell in r.cells:
            if not cell:
                continue
            key = tuple(round(v, 1) for v in cell)
            if key in seen:
                continue
            seen.add(key)
            text = cell_text(chars, cell)
            for ri in rws(cell[1], cell[3]):
                for ci in cols(cell[0], cell[2]):
                    if grid[ri][ci] == "":
                        grid[ri][ci] = text
    return grid


# ── helpers ─────────────────────────────────────────────────────────────────

def _clean_cell(cell) -> str:
    return re.sub(r"\s+", " ", cell).strip() if cell else ""


def _clean_row(row: list) -> list:
    return [_clean_cell(c) for c in row]


def _row_equal(a: list, b: list) -> bool:
    return len(a) == len(b) and all(x == y for x, y in zip(a, b))


def _drop_repeated_header(rows: list, headers: list, label: list) -> list:
    """Continuation fragments repeat the grid's own header/label rows -- those
    duplicates and all-empty pad rows are removed so every row is kept exactly
    once. The fragment may print fewer status columns than the opening grid
    (its label then partially overlaps the parent's), so a row is dropped when
    its non-empty cells are a subset of the parent label's -- never a data row,
    whose cells are its own."""
    head = [_clean_cell(c) for c in headers]
    lab = {c for c in (_clean_row(label) if label else []) if c}
    return [
        row for row in rows
        if (cset := {c for c in _clean_row(row) if c})
        and not (cset <= lab)
        and not _row_equal(_clean_row(row), head)
    ]


def _grid_is_real(table) -> bool:
    """Reject phantom grids: the page-footer line glued to a long rule forms a
    full-width fake grid. Real grids sit inside the printable area."""
    x0, _y0, x1, _y1 = table.bbox
    if x0 < -1 or x1 > 650:  # spilling outside the printable area
        return False
    return True


def _line_center(line: dict) -> float:
    return (line.get("top", 0) + line.get("bottom", 0)) / 2


def _lookup_unnum_title(lines: list, cap_idx: int) -> str:
    """The 1-2 lines directly above a caption can be an unnumbered section
    title (e.g. "Revision history" above the last table)."""
    for i in range(cap_idx - 1, max(cap_idx - 3, -1), -1):
        text = (lines[i].get("text") or "").strip()
        if UNNUMBERED_RE.match(text):
            return text
    return ""


def _scan_legend_above(lines: list, cap_idx: int, heading_line: int) -> str:
    """The status-letter legend block (A/N/P/- lines) sits above the "Summary
    of device limitations" grid, between the section heading and the caption.
    Only a block of 2+ such lines is a legend, not a stray paragraph line."""
    top = heading_line + 1 if heading_line >= 0 else max(cap_idx - 12, 0)
    items = [
        m.group(1) + " " + m.group(2).strip()
        for i in range(top, cap_idx)
        for m in [LEGEND_ITEM_RE.match((lines[i].get("text") or "").strip())]
        if m
    ]
    return " / ".join(items) if len(items) >= 2 else ""


def extract_tables(pdf, start: int = 1, end: int | None = None) -> list[LogicalTable]:
    tables: list[LogicalTable] = []
    heading_number, heading_title, heading_line = "", "", -1
    if end is None:
        end = len(pdf.pages)

    for pno in range(max(start, 1), min(end, len(pdf.pages)) + 1):
        page = pdf.pages[pno - 1]
        lines = page.extract_text_lines() or []
        objs = [t for t in (page.find_tables(table_settings=OPTS) or [])
                if _grid_is_real(t)]

        spans = [(t.bbox[1], t.bbox[3]) for t in objs]
        grids = [(t, build_grid(t, page.chars)) for t in objs]
        grids = [(t, g) for t, g in grids if g and
                 sum(1 for row in g for c in row if _clean_cell(c)) >= 2]
        spans = [s for s, (t, g) in zip(spans, grids)]

        captions = [
            (idx, int(m.group(1)), m.group(2).strip())
            for idx, ln in enumerate(lines)
            for m in [CAPTION_RE.match((ln.get("text") or "").strip())]
            if m
        ]

        for idx, ln in enumerate(lines):
            if any(t <= _line_center(ln) <= b for t, b in spans):
                continue
            m = HEADING_RE.match((ln.get("text") or "").strip())
            if m and m.group(1)[0].isdigit():
                heading_number, heading_title, heading_line = (
                    m.group(1), m.group(2).strip(), idx)

        def triage_section(cap_idx: int):
            """Section header for a captioned grid: an unnumbered title right
            above the caption wins, else the most recent numbered heading."""
            unnum = _lookup_unnum_title(lines, cap_idx)
            if unnum:
                return "", unnum
            if heading_line >= 0:
                return heading_number, heading_title
            return "", ""

        grid_owner: LogicalTable | None = None

        for (t, grid), (top, _bottom) in zip(grids, spans):
            cap = max(
                (c for c in captions if lines[c[0]].get("bottom", 0) < top),
                key=lambda c: c[0],
                default=None,
            )
            if cap is None:
                # No caption above this grid. It is a CONTINUATION fragment of
                # the previous grid only when it repeats that grid's own header
                # row (ES0677 p.3, ES0661 p.3); an uncaptioned grid that does
                # NOT repeat it is a separate embedded table (ADC spec excerpt,
                # workaround illustration) -- kept out of the payload, recoverable
                # in the PDF, flagged for audit.
                if not tables:
                    logger.warning(
                        "page %d: ruled grid with no caption above it and no "
                        "table to continue; skipping (audit, no number to "
                        "attach it to)", pno,
                    )
                    continue
                if not _row_equal(_clean_row(grid[0]),
                                  _clean_row(tables[-1].headers)):
                    logger.warning(
                        "page %d: uncaptioned grid not matching the open table "
                        "headers; skipping (audit, embedded table)", pno,
                    )
                    continue
                body = _drop_repeated_header(
                    grid[1:], tables[-1].headers, tables[-1].rows[0]
                    if tables[-1].rows else [])
                tables[-1].rows.extend(_clean_row(r) for r in body)
                tables[-1].page_end = pno
                grid_owner = tables[-1]
                continue
            cap_idx, number, title = cap
            table = LogicalTable(number, title, pno)
            table.headers = _clean_row(grid[0])
            table.rows = [_clean_row(r) for r in grid[1:]]
            table.section, table.section_title = triage_section(cap_idx)
            table.legend = _scan_legend_above(lines, cap_idx, heading_line)
            tables.append(table)
            grid_owner = table

        if grid_owner and grids:
            grid_bottom = max(bottom for _t, bottom in spans) if spans else 0
            for ln in lines:
                if ln.get("top", 0) < grid_bottom or (
                        spans and any(t <= _line_center(ln) <= b for t, b in spans)):
                    continue
                text = (ln.get("text") or "").strip()
                if not text:
                    continue
                if FOOTER_RE.match(text):
                    break
                m = FOOTNOTE_RE.match(text)
                le = LEGEND_RE.match(text)
                if m:
                    grid_owner.notes.append(m.group(2).strip())
                elif le and le.group(1).strip():
                    grid_owner.legend = le.group(1).strip()
                elif grid_owner.notes:
                    grid_owner.notes[-1] += " " + text

    for t in tables:
        if t.rows and _row_equal(t.rows[0], t.headers):
            t.rows.pop(0)
        t.notes = _dedupe(t.notes)
    return tables


class LogicalTable:
    def __init__(self, number: int, title: str, page: int):
        self.number = number
        self.title = CONTINUED_RE.sub("", title).strip()
        self.page = page
        self.page_end = page
        self.section = ""
        self.section_title = ""
        self.headers: list = []
        self.rows: list = []
        self.notes: list = []
        self.legend = ""

    def to_json(self) -> dict:
        return {
            "number": self.number,
            "title": self.title,
            "page": self.page,
            "page_end": self.page_end,
            "section": self.section,
            "section_title": self.section_title,
            "headers": self.headers,
            "rows": self.rows,
            "notes": self.notes,
            "legend": self.legend,
        }


def _dedupe(items: list) -> list:
    seen = set()
    out = []
    for item in items:
        if item not in seen:
            seen.add(item)
            out.append(item)
    return out