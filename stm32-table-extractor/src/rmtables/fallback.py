"""Deterministic fallback for pages where ruled-line extraction finds nothing.

Still pure pdfplumber, still offline -- no LLM. Two rungs, in order:

1. Retry `find_tables` with a text-alignment strategy instead of drawn lines.
2. Cluster `page.extract_words()` by x0 (columns) and top (rows).

Only invoked when `--text-fallback` is passed AND the default lines-strategy
extraction looks like it missed a table that a caption says should be there.
"""

from __future__ import annotations

import logging
import re

from .cells import cell_text
from .model import RawTable

logger = logging.getLogger(__name__)

TEXT_TABLE_SETTINGS = {
    "vertical_strategy": "text",
    "horizontal_strategy": "text",
    "snap_tolerance": 4,
}

CAPTION_RE = re.compile(r"^\s*Table\s+\d+\s*\.")


def page_looks_like_it_has_a_table(page) -> bool:
    """Heuristic: a Table N. caption is present but find_tables found nothing."""
    for line in page.extract_text_lines():
        if CAPTION_RE.match(line["text"]):
            return True
    return False


def extract_via_text_strategy(page, page_number: int) -> list[RawTable]:
    raw_tables = []
    try:
        found = page.find_tables(table_settings=TEXT_TABLE_SETTINGS)
    except Exception:
        logger.warning(
            "text-strategy find_tables failed on page %d", page_number, exc_info=True
        )
        found = []

    page_chars = page.chars
    for table in found:
        rows = []
        for row in table.rows:
            rows.append(
                [cell_text(page_chars, cell) if cell else None for cell in row.cells]
            )
        raw_tables.append(RawTable(page=page_number, bbox=table.bbox, rows=rows))

    if raw_tables:
        logger.info("text-strategy fallback recovered a table on page %d", page_number)
        return raw_tables

    return _cluster_words_fallback(page, page_number)


def _cluster_words_fallback(
    page, page_number: int, row_tol: float = 3.0, col_tol: float = 5.0
) -> list[RawTable]:
    """Last-resort grid reconstruction from word bounding boxes.

    Groups words into rows by `top` proximity, then into columns by
    clustering the set of x0 values seen across those rows. This recovers a
    plausible grid for borderless/partially-ruled tables; it is intentionally
    conservative and logged so runs stay auditable.
    """
    words = page.extract_words(use_text_flow=False, keep_blank_chars=False)
    if not words:
        return []

    words.sort(key=lambda w: (w["top"], w["x0"]))
    row_buckets: list[list[dict]] = []
    for w in words:
        if row_buckets and abs(w["top"] - row_buckets[-1][0]["top"]) <= row_tol:
            row_buckets[-1].append(w)
        else:
            row_buckets.append([w])

    col_x0s = sorted({w["x0"] for w in words})
    columns: list[float] = []
    for x in col_x0s:
        if not columns or x - columns[-1] > col_tol:
            columns.append(x)

    def col_index(x0: float) -> int:
        best_i, best_d = 0, float("inf")
        for i, cx in enumerate(columns):
            d = abs(x0 - cx)
            if d < best_d:
                best_i, best_d = i, d
        return best_i

    grid_rows = []
    for bucket in row_buckets:
        row = [None] * len(columns)
        for w in bucket:
            i = col_index(w["x0"])
            row[i] = (row[i] + " " + w["text"]) if row[i] else w["text"]
        grid_rows.append(row)

    logger.info(
        "word-clustering fallback built a %dx%d grid on page %d",
        len(grid_rows),
        len(columns),
        page_number,
    )
    bbox = (
        min(w["x0"] for w in words),
        min(w["top"] for w in words),
        max(w["x1"] for w in words),
        max(w["bottom"] for w in words),
    )
    return [RawTable(page=page_number, bbox=bbox, rows=grid_rows)]
