"""Merge datasheet readings with API values into the cells that get written.

The datasheet supplies every value it can; where it cannot, the API fills in
and the cell is marked. Nothing is written without a provenance token, and
no token claims ``DATASHEET`` without a table name behind it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .api import MULTI_SEP, Column, Grid
from .extract import PartExtraction, extract_part
from .fieldmap import spec_for
from .provenance import AMBIGUOUS, API, DATASHEET, DERIVED, UNAVAILABLE
from .values import BLANK_RENDERING, EQUIVALENCE, canon, is_blank, render


@dataclass
class Cell:
    """One written value, with where it came from."""

    value: str
    token: str
    source: str = ""
    conditions: str = ""
    api_value: str = ""
    datasheet_value: str | None = None

    @property
    def from_datasheet(self) -> bool:
        return self.token in (DATASHEET, DERIVED)


@dataclass
class ComposedPart:
    part: str
    cells: dict[str, Cell] = field(default_factory=dict)
    datasheet: Path | None = None
    summary_table: str = ""
    note: str | None = None


@dataclass
class ComposedSheet:
    parts: dict[str, ComposedPart] = field(default_factory=dict)
    #: part -> why it has no datasheet-sourced values
    without_datasheet: list[str] = field(default_factory=list)
    families_with_summary: int = 0
    families_without_summary: int = 0
    extraction_notes: dict[str, str] = field(default_factory=dict)

    def token_counts(self) -> dict[str, int]:
        counts = {t: 0 for t in (DATASHEET, DERIVED, AMBIGUOUS, API, UNAVAILABLE)}
        for composed in self.parts.values():
            for cell in composed.cells.values():
                counts[cell.token] = counts.get(cell.token, 0) + 1
        return counts


def api_tokens_for(value: object, column: Column | None) -> list[str]:
    """ST's set-valued cells, split into the tokens ST itself uses."""
    text = "" if value is None else str(value).strip()
    if not text or is_blank(text):
        return []
    if MULTI_SEP in text:
        parts = text.split(MULTI_SEP)
    elif column is not None and column.is_list and "," in text:
        parts = text.split(",")
    else:
        parts = [text]
    return [p.strip() for p in parts if p.strip()]


def compose_part(
    part: str,
    grid: Grid,
    column_keys: list[str],
    extraction: PartExtraction | None,
) -> ComposedPart:
    row = grid.rows_by_part()[part]
    by_key = grid.by_key()
    composed = ComposedPart(part=part)
    if extraction is not None:
        composed.datasheet = extraction.datasheet
        composed.summary_table = extraction.summary_table
        composed.note = extraction.note

    for key in column_keys:
        column = by_key.get(key)
        raw = row["cells"].get(column.id) if column is not None else None
        api_rendered = render(raw, column)
        spec = spec_for(key)
        reading = (extraction.readings.get(key) if extraction else None)

        if reading is not None and reading.token in (DATASHEET, DERIVED):
            # Render the datasheet's value through the same conventions the
            # rest of the sheet uses, so the file stays internally consistent.
            value = render(reading.value, column)
            written = value

            # Datasheet-first settles *disagreements*. It is not a reason to
            # downgrade notation when there is no disagreement: the datasheet
            # says "LQFP176", ST says "LQFP 176 24x24x1.4 mm", and both name
            # the same package. Writing the short form would ship a workbook
            # poorer than the one it replaces, losing the dimensions the
            # original carried, for no gain in accuracy. When the equivalence
            # test says the two agree, keep the fuller text.
            #
            # Provenance stays DATASHEET: the datasheet is still what
            # established the fact, and api_value/datasheet_value record both
            # renderings so nothing is hidden.
            if spec.equivalence and api_rendered != BLANK_RENDERING:
                agree = EQUIVALENCE[spec.equivalence](api_rendered, value)
                if agree and len(api_rendered) > len(value):
                    written = api_rendered

            composed.cells[key] = Cell(
                value=written,
                token=reading.token,
                source=reading.source,
                api_value=api_rendered,
                datasheet_value=value,
            )
            continue

        if reading is not None and reading.token == AMBIGUOUS:
            composed.cells[key] = Cell(
                value=api_rendered,
                token=AMBIGUOUS,
                conditions=reading.conditions,
                api_value=api_rendered,
            )
            continue

        # No datasheet reading: the API fills in, or nothing does.
        token = API
        if is_blank(raw) and api_rendered == BLANK_RENDERING:
            token = UNAVAILABLE
        composed.cells[key] = Cell(
            value=api_rendered,
            token=token,
            conditions=spec.reason if token == API else "",
            api_value=api_rendered,
        )
    return composed


def compose_sheet(
    grid: Grid,
    column_keys: list[str],
    datasheets: dict[str, Path],
) -> ComposedSheet:
    """Extract and compose every part in a grid.

    Datasheets are shared across a family, so each distinct PDF is opened
    once per *variant column* it is asked about -- the extraction is keyed by
    (file, part) because the pinned column differs per part.
    """
    sheet = ComposedSheet()
    by_key = grid.by_key()
    with_summary = set()
    without_summary = set()

    for row in grid.rows:
        part = row["part_number"]
        path = datasheets.get(part)
        extraction: PartExtraction | None = None
        if path is not None:
            api_tokens = {
                key: api_tokens_for(row["cells"].get(by_key[key].id), by_key[key])
                for key in column_keys
                if key in by_key
            }
            extraction = extract_part(path, part, column_keys, api_tokens)
            if extraction.datasheet_fields:
                with_summary.add(path.name)
            else:
                without_summary.add(path.name)
                if extraction.note:
                    sheet.extraction_notes[part] = extraction.note
        else:
            sheet.without_datasheet.append(part)

        sheet.parts[part] = compose_part(part, grid, column_keys, extraction)

    sheet.families_with_summary = len(with_summary)
    sheet.families_without_summary = len(without_summary - with_summary)
    return sheet


def api_only_sheet(grid: Grid, column_keys: list[str]) -> ComposedSheet:
    """The ``--source api`` path: every cell from the API, as before."""
    sheet = ComposedSheet()
    for row in grid.rows:
        sheet.parts[row["part_number"]] = compose_part(
            row["part_number"], grid, column_keys, None
        )
    return sheet
