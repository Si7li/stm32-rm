"""Continuation-table merging.

ST splits long tables across pages -- sometimes reprinting the caption with
a trailing "(continued)", but not always, and often re-rendering the header
row with a different number of ruled columns on the continuation page (a
spanned header cell splitting differently). This module walks tables in
document order and stitches those segments back into one logical table.

MERGE_DUPLICATE_FIX.md: the merge decision is identity-based -- same
caption `table_number`, starting on the same page or the very next page
after the previous segment's last page. It does NOT require matching
headers, matching column counts, or a "(continued)" marker (that marker,
when present, is corroborating evidence only, never a requirement).
Column-count mismatches are reconciled by right-padding, never by refusing
to merge.

Callers (classify.py) resolve captions before handing raw tables here, so
every raw table passed to `process_page` already has a matched caption --
uncaptioned grids are routed to RegisterMerger or dropped upstream and never
reach this class.
"""

from __future__ import annotations

import logging
from collections import Counter, defaultdict

from .model import LogicalTable

logger = logging.getLogger(__name__)

HEADER_REPEAT_CELL_MATCH_RATIO = 0.8


def _pad_row(row: list, width: int) -> list:
    return row + [None] * (width - len(row))


def _row_width(rows: list) -> int:
    return max((len(r) for r in rows), default=0)


def _normalize_cell(cell) -> str:
    return " ".join((cell or "").split())


def _is_repeated_header(row: list, header: list) -> bool:
    """A continuation's first row counts as a repeated header
    (MERGE_DUPLICATE_FIX.md §3) if it's equal to the logical header
    (exactly, or after whitespace normalization), or if at least 80% of
    ITS OWN non-empty cells match the header -- membership-based (each
    header cell can satisfy at most one row cell; a header cell repeated
    twice, e.g. a spanned "PUPD(i) [1:0]" column, can match the row's
    "PUPD(i) [1:0]" at most twice), not positional. Verified real case
    (RM0490 Table 38): the two segments' ruled grids split a grouped
    column differently, so the reprinted header's cells land at different
    POSITIONS than the first segment's header -- the exact scenario this
    whole fix is about, so a strict position-for-position comparison would
    almost never recognize a repeated header on precisely the tables this
    is meant to catch."""
    if row == header:
        return True
    norm_row = [_normalize_cell(c) for c in row]
    norm_header = [_normalize_cell(c) for c in header]
    if norm_row == norm_header:
        return True
    non_empty_row_cells = [c for c in norm_row if c]
    if not non_empty_row_cells:
        return False
    available = Counter(c for c in norm_header if c)
    matches = 0
    for cell in non_empty_row_cells:
        if available.get(cell, 0) > 0:
            matches += 1
            available[cell] -= 1
    return matches / len(non_empty_row_cells) >= HEADER_REPEAT_CELL_MATCH_RATIO


class TableMerger:
    def __init__(self):
        self.current: LogicalTable | None = None
        self.finalized: list[LogicalTable] = []

    def process_page(self, page_number: int, captioned_tables) -> None:
        """`captioned_tables` is an iterable of (RawTable, Caption, section, notes,
        legend) 5-tuples, where `section` is an (section_number, section_title)
        tuple or None, `notes` is this raw table's own footnote list, and `legend`
        is any legend text already position-matched to this same raw table on
        this same page (may both be empty)."""
        for raw_table, caption, section, notes, legend in captioned_tables:
            number = caption.number

            merge_ok = False
            if self.current is not None and number == self.current.table_number:
                same_page = page_number == self.current.page_end
                next_page = page_number == self.current.page_end + 1
                merge_ok = same_page or next_page

            if merge_ok:
                new_rows = list(raw_table.rows)
                header = self.current.rows[0] if self.current.rows else []
                if new_rows and _is_repeated_header(new_rows[0], header):
                    new_rows = new_rows[1:]

                width = max(_row_width(self.current.rows), _row_width(new_rows))
                if width > _row_width(self.current.rows):
                    self.current.rows = [_pad_row(r, width) for r in self.current.rows]
                new_rows = [_pad_row(r, width) for r in new_rows]

                self.current.rows.extend(new_rows)
                self.current.notes.extend(notes)
                self.current.legend.extend(legend)
                self.current.page_end = page_number
                self.current.spans_pages = self.current.spans_pages or (
                    page_number != self.current.page_start
                )
                continue

            if self.current is not None:
                self.finalized.append(self.current)

            section_number, section_title = section if section else (None, None)
            self.current = LogicalTable(
                table_number=number,
                caption=caption.text,
                page_start=page_number,
                page_end=page_number,
                spans_pages=False,
                rows=list(raw_table.rows),
                section_number=section_number,
                section_title=section_title,
                notes=list(notes),
                legend=list(legend),
            )

    def attach_legend(self, table_number: int, text: str) -> bool:
        """Attach `text` to the LogicalTable with this `table_number` --
        current (open) or already finalized. Used for an explicit `Legend
        for Table N:` reference, which is position-independent and so may
        need to reach back into an already-closed table (or, per the
        caller's retry, forward into one not created yet). Returns False if
        no such table exists among current/finalized yet."""
        if self.current is not None and self.current.table_number == table_number:
            self.current.legend.append(text)
            return True
        for t in reversed(self.finalized):
            if t.table_number == table_number:
                t.legend.append(text)
                return True
        return False

    def _check_duplicate_table_numbers(self) -> None:
        """MERGE_DUPLICATE_FIX.md: a duplicate `table_number` surviving the
        merge above indicates a genuine parse problem (e.g. another
        table's caption interrupted the continuation), not a normal
        multi-page table -- logged loudly, but both objects are kept so no
        data is lost (the splitter's `_p{page}` suffix still disambiguates
        their filenames)."""
        by_number = defaultdict(list)
        for t in self.finalized:
            if t.table_number is not None:
                by_number[t.table_number].append(t)
        for number, group in by_number.items():
            if len(group) > 1:
                pages = ", ".join(f"{t.page_start}-{t.page_end}" for t in group)
                logger.error(
                    "table_number %s appears in %d separate objects after merging "
                    "(pages: %s) -- likely a genuine parse problem, not a normal "
                    "continuation; keeping all objects",
                    number, len(group), pages,
                )

    def finalize(self) -> list[LogicalTable]:
        if self.current is not None:
            self.finalized.append(self.current)
            self.current = None
        self._check_duplicate_table_numbers()
        return self.finalized
