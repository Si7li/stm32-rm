"""Compare an original workbook against the values that will be written.

One record per ``(part, column)`` worth reporting.

Workbook-change classes -- "did this cell change, and how?":

``CHANGED``          both sides have a value and they genuinely differ --
                     a hand-entered cell that is wrong.
``BLANK_FILLED``     the original had no value, the new sheet has one.
``MISSING_FROM_ST``  the original had a value, neither source has one.
``ADDED_COLUMN``     a column appended from the API that the original lacked.
``NEW_PART``         a part ST lists that the original does not.
``NOT_IN_ST_DATA``   a part the original lists that ST does not. The row is
                     kept -- never silently dropped -- and flagged.

Source-attribution classes -- "which source won, and who was wrong?":

``DATASHEET_OVERRIDES_API``
    both sources have a value and they differ; the datasheet's is written.
``ORIGINAL_MATCHED_API_NOT_DATASHEET``
    the original workbook agreed with the API, and both disagree with the
    datasheet. This is an error propagated out of ST's own database rather
    than introduced by hand.

The two families answer different questions about the same cell, so a cell
can appear in both -- STM32F207IE's I2C is `CHANGED` (2 -> 3),
`DATASHEET_OVERRIDES_API` (API said 2) and
`ORIGINAL_MATCHED_API_NOT_DATASHEET` (the workbook had copied ST's 2). The
second family is by construction a subset of the first; both counts are
reported separately.

``UNCHANGED`` is counted but not written out. Everything is compared through
:func:`stproducts.values.canon`, so formatting noise is never reported.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

from .api import Column, Grid
from .compose import ComposedSheet
from .fieldmap import spec_for
from .sheetio import OriginalSheet
from .values import EQUIVALENCE, canon, is_blank, render

CHANGED = "CHANGED"
ADDED_COLUMN = "ADDED_COLUMN"
NEW_PART = "NEW_PART"
MISSING_FROM_ST = "MISSING_FROM_ST"
BLANK_FILLED = "BLANK_FILLED"
NOT_IN_ST_DATA = "NOT_IN_ST_DATA"
UNCHANGED = "UNCHANGED"

DATASHEET_OVERRIDES_API = "DATASHEET_OVERRIDES_API"
ORIGINAL_MATCHED_API_NOT_DATASHEET = "ORIGINAL_MATCHED_API_NOT_DATASHEET"

REPORTED_CLASSES = (
    CHANGED,
    BLANK_FILLED,
    MISSING_FROM_ST,
    ADDED_COLUMN,
    NEW_PART,
    NOT_IN_ST_DATA,
)

SOURCE_CLASSES = (DATASHEET_OVERRIDES_API, ORIGINAL_MATCHED_API_NOT_DATASHEET)


@dataclass(frozen=True)
class DiffRecord:
    part: str
    column: str
    old: str
    new: str
    kind: str


@dataclass
class DiffResult:
    stem: str
    records: list[DiffRecord] = field(default_factory=list)
    counts: Counter = field(default_factory=Counter)
    parts_compared: int = 0
    cells_compared: int = 0
    new_parts: list[str] = field(default_factory=list)
    missing_parts: list[str] = field(default_factory=list)
    original_columns: int = 0
    appended_columns: int = 0
    #: Which classes this run reports -- the API path keeps the original set
    #: so its output stays byte-identical to the pre-inversion tool.
    class_names: tuple = REPORTED_CLASSES

    @property
    def corrections(self) -> int:
        return self.counts[CHANGED]

    def summary(self) -> dict:
        return {
            "parts_compared": self.parts_compared,
            "cells_compared": self.cells_compared,
            "original_columns": self.original_columns,
            "appended_columns": self.appended_columns,
            "new_parts": len(self.new_parts),
            "parts_not_in_st": len(self.missing_parts),
            "classes": {k: self.counts.get(k, 0) for k in (*self.class_names, UNCHANGED)},
        }


def plan_columns(
    sheet: OriginalSheet, grid: Grid
) -> tuple[list[tuple[str, Column | None]], list[tuple[str, Column]]]:
    """Original columns in their original order, then the API's extras."""
    api_by_key = grid.by_key()
    original = [(c.key, api_by_key.get(c.key)) for c in sheet.columns]
    taken = {c.key for c in sheet.columns}
    appended = [(c.key, c) for c in grid.columns if c.key not in taken]
    return original, appended


def compare(
    sheet: OriginalSheet,
    grid: Grid,
    composed: ComposedSheet,
    *,
    with_source_classes: bool = False,
) -> DiffResult:
    result = DiffResult(
        stem=sheet.stem,
        class_names=(*REPORTED_CLASSES, *SOURCE_CLASSES)
        if with_source_classes
        else REPORTED_CLASSES,
    )
    original_cols, appended_cols = plan_columns(sheet, grid)
    result.original_columns = len(original_cols)
    result.appended_columns = len(appended_cols)

    api_rows = grid.rows_by_part()

    for part in sheet.parts:
        row = api_rows.get(part)
        if row is None:
            result.missing_parts.append(part)
            result.records.append(
                DiffRecord(part, "*", "(row kept from original)", "", NOT_IN_ST_DATA)
            )
            continue

        result.parts_compared += 1
        cells = composed.parts[part].cells
        for key, column in original_cols:
            old_raw = sheet.data[part].get(key)
            cell = cells[key]
            result.cells_compared += 1

            old_key = canon(old_raw, column)
            new_key = canon(cell.value, column)
            old_text = "" if old_raw is None else str(old_raw).strip()

            # A datasheet writes "LQFP64" where the workbook has ST's fuller
            # "LQFP 64 10x10x1.4 mm". The cell's text changes, but no value
            # does -- and calling that a wrong hand-entered cell would put 38
            # false rows in front of the real ones. Same principle as canon():
            # report a genuine value change, not a rendering difference.
            equivalence = spec_for(key).equivalence if cell.from_datasheet else None
            same = old_key == new_key or (
                equivalence is not None
                and bool(old_text)
                and EQUIVALENCE[equivalence](old_text, cell.value)
            )

            if same:
                result.counts[UNCHANGED] += 1
            else:
                if not old_key:
                    kind = BLANK_FILLED
                elif not new_key:
                    kind = MISSING_FROM_ST
                else:
                    kind = CHANGED
                result.counts[kind] += 1
                result.records.append(DiffRecord(part, key, old_text, cell.value, kind))

            if not with_source_classes or not cell.from_datasheet:
                continue

            api_key = canon(cell.api_value, column)
            if not api_key or api_key == new_key:
                continue
            if equivalence is not None and EQUIVALENCE[equivalence](
                cell.api_value, cell.value
            ):
                continue

            result.counts[DATASHEET_OVERRIDES_API] += 1
            result.records.append(
                DiffRecord(part, key, cell.api_value, cell.value, DATASHEET_OVERRIDES_API)
            )
            if old_key == api_key:
                result.counts[ORIGINAL_MATCHED_API_NOT_DATASHEET] += 1
                result.records.append(
                    DiffRecord(
                        part,
                        key,
                        f"{old_text} (workbook and API agree)",
                        cell.value,
                        ORIGINAL_MATCHED_API_NOT_DATASHEET,
                    )
                )

        for key, column in appended_cols:
            raw = row["cells"].get(column.id)
            # A caller may compose only the original columns, in which case
            # the appended ones are still pure API and render from the row.
            cell = cells.get(key)
            written = cell.value if cell is not None else render(raw, column)
            from_datasheet = cell is not None and cell.from_datasheet
            result.cells_compared += 1
            # What gets reported is the value actually WRITTEN, and whether
            # anything was written is judged on that value too. Judging it on
            # the API cell alone under-reported every appended column the API
            # is silent about but the datasheet fills -- 26 CAN (FD) cells
            # were written and counted UNCHANGED, so the run's most dangerous
            # values were precisely the ones its own audit trail omitted.
            #
            # The one suppression kept: an API column that is simply empty.
            # Blankness is judged on the raw cell rather than the rendered
            # one, because an absent boolean renders as "No", and listing "No"
            # for every part of a column ST never populated is pure noise.
            from_api_and_empty = is_blank(raw) and not from_datasheet
            if is_blank(written) or from_api_and_empty:
                result.counts[UNCHANGED] += 1
            else:
                result.counts[ADDED_COLUMN] += 1
                result.records.append(DiffRecord(part, key, "", written, ADDED_COLUMN))

    known = set(sheet.parts)
    for part in grid.part_numbers:
        if part in known:
            continue
        result.new_parts.append(part)
        result.counts[NEW_PART] += 1
        result.records.append(DiffRecord(part, "*", "", "(part absent from original)", NEW_PART))

    order = {p: i for i, p in enumerate(grid.part_numbers)}
    col_order = {k: i for i, (k, _) in enumerate([*original_cols, *appended_cols])}
    class_order = {name: i for i, name in enumerate((*REPORTED_CLASSES, *SOURCE_CLASSES))}
    result.records.sort(
        key=lambda r: (
            order.get(r.part, 10**6),
            col_order.get(r.column, -1),
            class_order.get(r.kind, 99),
            r.column,
        )
    )
    return result
