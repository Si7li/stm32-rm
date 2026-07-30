"""Page -> ruled-table grid extraction, with per-page memory hygiene."""

from __future__ import annotations

import logging

from .cells import cell_text
from .model import RawTable

logger = logging.getLogger(__name__)

TABLE_SETTINGS = {
    "vertical_strategy": "lines",
    "horizontal_strategy": "lines",
    "snap_tolerance": 3,
    "join_tolerance": 3,
    "edge_min_length": 3,
    "intersection_tolerance": 3,
}


def build_grid(table, chars) -> list:
    """Explode merged cells: each drawn cell's text fills every grid position
    its rectangle covers (rowspan + colspan). Empty cells stay "".

    pdfplumber reports a spanning cell once, at its rectangle's top-left
    "home" position; every other grid position it visually covers comes
    back as `None` in `row.cells`. Naively mapping `None -> None` (the prior
    behavior here) leaves those covered positions null even though the
    merged value visually repeats across them. This instead derives the
    grid's row/column boundaries from every drawn cell's edges, then, for
    each drawn cell, fills its text into every (row, col) position whose
    boundaries its rectangle overlaps.
    """
    rows = table.rows
    xs = sorted({round(e, 1) for r in rows for cell in r.cells if cell for e in (cell[0], cell[2])})
    ys = sorted({round(r.bbox[1], 1) for r in rows} | {round(rows[-1].bbox[3], 1)})
    ncol = len(xs) - 1
    nrow = len(rows)

    def cols(x0, x1):
        return [i for i in range(ncol) if not (x1 <= xs[i] + 0.5 or x0 >= xs[i + 1] - 0.5)]

    def rws(top, bottom):
        return [i for i in range(nrow) if not (bottom <= ys[i] + 0.5 or top >= ys[i + 1] - 0.5)]

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
            txt = cell_text(chars, cell)
            for ri in rws(cell[1], cell[3]):
                for ci in cols(cell[0], cell[2]):
                    if grid[ri][ci] == "":
                        grid[ri][ci] = txt
    return grid


def extract_page_tables(page, page_number: int) -> list[RawTable]:
    """Find every ruled table on a page and materialize its text grid.

    `page_number` is 1-indexed (matches the printed manual page numbers).
    Merged/spanned cells are filled by `build_grid` -- a genuinely empty
    cell is `""`, never `None`.
    """
    raw_tables = []
    try:
        found = page.find_tables(table_settings=TABLE_SETTINGS)
    except Exception:
        logger.warning("find_tables failed on page %d", page_number, exc_info=True)
        return raw_tables

    page_chars = page.chars
    for table in found:
        rows = build_grid(table, page_chars)
        raw_tables.append(RawTable(page=page_number, bbox=table.bbox, rows=rows))
    return raw_tables


def flush_page(page) -> None:
    """Release pdfplumber's per-page caches so long runs don't OOM."""
    try:
        page.flush_cache()
        page.get_textmap.cache_clear()
    except Exception:
        pass
