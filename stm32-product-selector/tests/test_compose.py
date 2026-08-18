"""Merging the two sources, and the diff classes that describe the merge."""

from __future__ import annotations

from stproducts.api import Column
from stproducts.compose import ComposedPart, ComposedSheet, api_tokens_for, compose_part
from stproducts.diffing import (
    CHANGED,
    DATASHEET_OVERRIDES_API,
    ORIGINAL_MATCHED_API_NOT_DATASHEET,
    UNCHANGED,
    compare,
)
from stproducts.extract import PartExtraction
from stproducts.provenance import AMBIGUOUS, API, DATASHEET, UNAVAILABLE, Reading
from stproducts.sheetio import read_original

KEYS = [
    "Part Number",
    "Marketing Status",
    "Flash Size (kB) (Prog)",
    "A/D Converters 12-bit | Number of A/D Converters typ",
    "A/D Converters 12-bit | Number of Channels typ",
]


def _extraction(part, readings):
    return PartExtraction(part=part, readings=readings, summary_table="Table 2. ...")


def test_datasheet_value_wins_and_is_marked(grid):
    composed = compose_part(
        "TEST001", grid, KEYS,
        _extraction("TEST001", {"Flash Size (kB) (Prog)": Reading(DATASHEET, "256", "Table 2")}),
    )
    cell = composed.cells["Flash Size (kB) (Prog)"]
    assert cell.value == "256"          # not the API's 128
    assert cell.token == DATASHEET
    assert cell.source == "Table 2"
    assert cell.api_value == "128"


def test_api_fills_in_where_the_datasheet_is_silent(grid):
    composed = compose_part("TEST001", grid, KEYS, _extraction("TEST001", {}))
    cell = composed.cells["Flash Size (kB) (Prog)"]
    assert cell.value == "128" and cell.token == API


def test_ambiguous_keeps_the_api_value_and_records_evidence(grid):
    composed = compose_part(
        "TEST001", grid, KEYS,
        _extraction("TEST001", {
            "Flash Size (kB) (Prog)": Reading(AMBIGUOUS, conditions="two grades offered"),
        }),
    )
    cell = composed.cells["Flash Size (kB) (Prog)"]
    assert cell.value == "128"
    assert cell.token == AMBIGUOUS
    assert cell.conditions == "two grades offered"


def test_unavailable_when_neither_source_has_a_value(grid):
    composed = compose_part("TEST003", grid, KEYS, None)
    cell = composed.cells["A/D Converters 12-bit | Number of Channels typ"]
    assert cell.value == "-" and cell.token == UNAVAILABLE


def test_every_cell_gets_exactly_one_token(grid):
    composed = compose_part("TEST001", grid, KEYS, None)
    assert set(composed.cells) == set(KEYS)
    assert all(c.token for c in composed.cells.values())


def test_api_tokens_split_st_multi_values():
    column = Column(id="1", name="X", order=1, show=True, type="multi", identifier="")
    assert api_tokens_for("A||B", column) == ["A", "B"]
    assert api_tokens_for("A, B", column) == ["A", "B"]
    assert api_tokens_for("-", column) == []
    assert api_tokens_for(None, column) == []


class TestSourceClasses:
    def _run(self, workbook_path, grid, datasheet_value):
        sheet = read_original(workbook_path)
        keys = sheet.column_keys + [
            c.key for c in grid.columns if c.key not in sheet.column_keys
        ]
        composed = ComposedSheet()
        for part in grid.part_numbers:
            readings = {}
            if part == "TEST002" and datasheet_value is not None:
                readings["Flash Size (kB) (Prog)"] = Reading(
                    DATASHEET, datasheet_value, "Table 2"
                )
            composed.parts[part] = compose_part(
                part, grid, keys, _extraction(part, readings)
            )
        return sheet, compare(sheet, grid, composed, with_source_classes=True)

    def test_override_recorded_when_sources_disagree(self, workbook_path, grid):
        """The STM32F207IE case in miniature: workbook and API agree on one
        value, the datasheet says another, and the datasheet is written."""
        # TEST002: workbook has 256.0, API has 256, datasheet says 512.
        _, result = self._run(workbook_path, grid, "512")
        kinds = {(r.part, r.column, r.kind) for r in result.records}
        assert ("TEST002", "Flash Size (kB) (Prog)", DATASHEET_OVERRIDES_API) in kinds
        assert (
            "TEST002", "Flash Size (kB) (Prog)", ORIGINAL_MATCHED_API_NOT_DATASHEET
        ) in kinds
        assert ("TEST002", "Flash Size (kB) (Prog)", CHANGED) in kinds

    def test_no_override_when_the_datasheet_confirms_the_api(self, workbook_path, grid):
        _, result = self._run(workbook_path, grid, "256")
        assert not [r for r in result.records if r.kind == DATASHEET_OVERRIDES_API]

    def test_original_matched_api_requires_the_workbook_to_agree(self, workbook_path, grid):
        """TEST001's workbook says 999 where the API says 128, so a datasheet
        value of 512 is an override but not a propagated ST error."""
        sheet = read_original(workbook_path)
        keys = sheet.column_keys
        composed = ComposedSheet()
        for part in grid.part_numbers:
            readings = (
                {"Flash Size (kB) (Prog)": Reading(DATASHEET, "512", "Table 2")}
                if part == "TEST001"
                else {}
            )
            composed.parts[part] = compose_part(part, grid, keys, _extraction(part, readings))
        result = compare(sheet, grid, composed, with_source_classes=True)
        kinds = {(r.part, r.column, r.kind) for r in result.records}
        assert ("TEST001", "Flash Size (kB) (Prog)", DATASHEET_OVERRIDES_API) in kinds
        assert (
            "TEST001", "Flash Size (kB) (Prog)", ORIGINAL_MATCHED_API_NOT_DATASHEET
        ) not in kinds

    def test_api_mode_reports_no_source_classes(self, workbook_path, grid):
        from stproducts.compose import api_only_sheet

        sheet = read_original(workbook_path)
        keys = sheet.column_keys + [
            c.key for c in grid.columns if c.key not in sheet.column_keys
        ]
        result = compare(sheet, grid, api_only_sheet(grid, keys), with_source_classes=False)
        assert DATASHEET_OVERRIDES_API not in result.summary()["classes"]
        assert not [r for r in result.records if r.kind in
                    (DATASHEET_OVERRIDES_API, ORIGINAL_MATCHED_API_NOT_DATASHEET)]


def test_package_notation_is_not_a_disagreement():
    from stproducts.values import package_equivalent

    assert package_equivalent("LQFP 64 10x10x1.4 mm", "LQFP64")
    assert package_equivalent(
        "LQFP 64 10x10x1.4 mm, WLCSP 66 4x3.7x0.6", "LQFP64, WLCSP64+2"
    )
    assert package_equivalent("LQFP 176 24x24x1.4 mm||UFBGA 176+25 10x10", "LQFP176, UFBGA176")
    # A genuinely different package is still a disagreement.
    assert not package_equivalent("LQFP 64 10x10x1.4 mm", "LQFP100")
    assert not package_equivalent("LQFP 64 10x10x1.4 mm", "LQFP64, UFBGA176")
