"""A named source table is not enough: the named *row* must fit the column.

Regression cover for the CAN (FD) fabrication. ``CAN (2.0)`` and ``CAN (FD)``
were both configured to read the summary table's ``Comm. interfaces | CAN``
row, and rows are matched by substring, so both columns accepted whichever
CAN-ish row the datasheet happened to carry. On STM32F2 -- classic bxCAN only,
no CAN FD in the family at all -- that wrote ``CAN (FD) = 2`` for 26 parts and
labelled every one of them ``DATASHEET``.

The provenance invariant did not catch it, because a source table genuinely
*was* named. Three things now stand in the way, and each is tested here:

1. the row label is carried on the Reading and checked against the column;
2. two columns cannot read one source without something separating them,
   enforced at import time;
3. the diff reports appended columns on what was written, so a value like
   this cannot reach a workbook without appearing in the audit trail.
"""

from __future__ import annotations

import pathlib

import pytest

from stproducts.api import Column, Grid
from stproducts.compose import ComposedPart, ComposedSheet, compose_part
from stproducts.diffing import ADDED_COLUMN, UNCHANGED, compare
from stproducts.extract import Document, Fragment, PartExtraction, extract_part, read_summary_number
from stproducts.fieldmap import AliasedSourceError, FieldSpec, _build, spec_for
from stproducts.sheetio import read_original
from stproducts.provenance import DATASHEET, Reading


# --------------------------------------------------------------------------
# 1. The row label decides whether the reading counts for this column.
# --------------------------------------------------------------------------


class TestRowSupportsColumn:
    def test_plain_can_row_answers_for_can_20_only(self):
        """A row saying "CAN" is bxCAN. It asserts nothing about CAN FD."""
        label = "Comm. interfaces | CAN"
        assert spec_for("CAN (2.0)").row_supports(label) is True
        assert spec_for("CAN (FD)").row_supports(label) is False

    @pytest.mark.parametrize("label", ["Comm. interfaces | FDCAN", "Comm. interfaces | CAN FD"])
    def test_fdcan_row_answers_for_can_fd_only(self, label):
        """And the converse: an FDCAN count is not a count of 2.0B controllers."""
        assert spec_for("CAN (FD)").row_supports(label) is True
        assert spec_for("CAN (2.0)").row_supports(label) is False

    def test_columns_without_row_conditions_are_unaffected(self):
        spec = spec_for("I2C typ")
        assert spec.row_supports("Comm. interfaces | I2C") is True
        assert spec.row_supports("") is True

    def test_a_required_token_is_not_satisfied_by_an_unlabelled_row(self):
        """Missing evidence is not permission. An empty label cannot satisfy
        a requirement, or the check would pass exactly when it is needed."""
        assert spec_for("CAN (FD)").row_supports("") is False


class TestReadingCarriesItsRow:
    def _doc(self, row_label, value):
        """``row_label`` is the (left, second) pair ST splits the label across."""
        fragment = Fragment(
            caption="Table 3. STM32F207xx features and peripheral counts",
            page=14,
            rows=[["Feature", "", "STM32F207IE"], [*row_label, value]],
            family_columns=[2],
            column=2,
        )
        return Document(
            part="STM32F207IE",
            path=pathlib.Path(__file__),
            fragments=[fragment],
        )

    def test_summary_number_records_the_row_it_matched(self):
        reading = read_summary_number(self._doc(("Comm. interfaces", "CAN"), "2"), "comm. interfaces | can")
        assert reading.value == "2"
        assert reading.row == "Comm. interfaces | CAN"
        assert reading.source.startswith("Table 3.")

    def test_the_substring_match_that_caused_the_bug_still_happens(self):
        """A "CAN FD" row really does answer the plain-CAN needle, which is
        why the row label has to be checked rather than trusted."""
        reading = read_summary_number(
            self._doc(("Comm. interfaces", "CAN FD"), "3"), "comm. interfaces | can"
        )
        assert reading is not None and reading.value == "3"
        assert reading.row == "Comm. interfaces | CAN FD"
        # The reader is happy to hand it over; the field map is what refuses.
        assert spec_for("CAN (2.0)").row_supports(reading.row) is False

    def test_an_fdcan_row_does_not_match_the_plain_can_needle_at_all(self):
        """Worth recording, because it bounds what the fix can do.

        ``comm. interfaces | can`` is not a substring of
        ``comm. interfaces | fdcan``, so datasheets that spell the row
        "FDCAN" are simply not read and the column falls to the API. That is
        a coverage gap, not a correctness one -- nothing wrong is asserted --
        but it means CAN (FD) is API-sourced on the families that would
        actually have populated it.
        """
        reading = read_summary_number(
            self._doc(("Comm. interfaces", "FDCAN"), "3"), "comm. interfaces | can"
        )
        assert reading is None


# --------------------------------------------------------------------------
# 2. Aliasing is rejected at import time, so the class cannot come back.
# --------------------------------------------------------------------------


class TestAliasGuard:
    def test_the_shipped_field_map_is_clean(self):
        assert _build()  # raises AliasedSourceError if any source is aliased

    def test_two_columns_on_one_source_are_rejected(self, monkeypatch):
        """Reintroducing the original bug fails the build, not a workbook."""
        import stproducts.fieldmap as fieldmap

        monkeypatch.setattr(fieldmap, "_ROW_TOKENS", {})
        with pytest.raises(AliasedSourceError, match="CAN"):
            fieldmap._build()

    def test_mutually_exclusive_tokens_are_allowed(self):
        """The legitimate case: one source, told apart by the row label."""
        specs = {
            "A": FieldSpec(DATASHEET, "r", ("x",), requires_row_tokens=("fd",)),
            "B": FieldSpec(DATASHEET, "r", ("x",), forbids_row_tokens=("fd",)),
        }
        from stproducts.fieldmap import _check_no_silent_aliasing

        _check_no_silent_aliasing(specs)  # must not raise

    def test_an_explicit_opt_in_is_allowed(self):
        specs = {
            "A": FieldSpec(DATASHEET, "r", ("x",), shares_source=True),
            "B": FieldSpec(DATASHEET, "r", ("x",)),
        }
        from stproducts.fieldmap import _check_no_silent_aliasing

        _check_no_silent_aliasing(specs)  # must not raise


# --------------------------------------------------------------------------
# 3. extract_part drops an unsupported reading, and says so.
# --------------------------------------------------------------------------


def test_extract_part_rejects_and_records_an_unsupported_row(monkeypatch, tmp_path):
    """The whole path: a CAN row is offered for CAN (FD) and is refused."""
    import stproducts.extract as extract

    fragment = Fragment(
        caption="Table 3. STM32F207xx features and peripheral counts",
        page=14,
        rows=[["Feature", "", "STM32F207IE"], ["Comm. interfaces", "CAN", "2"]],
        family_columns=[2],
        column=2,
    )
    doc = Document(part="STM32F207IE", path=tmp_path / "f.pdf", fragments=[fragment])
    monkeypatch.setattr(extract, "open_document", lambda path, part: doc)

    result = extract_part(tmp_path / "f.pdf", "STM32F207IE", ["CAN (2.0)", "CAN (FD)"], {})

    assert result.readings["CAN (2.0)"].value == "2"
    assert "CAN (FD)" not in result.readings, "a CAN row must not fill CAN (FD)"
    assert result.rejected_rows["CAN (FD)"] == "Comm. interfaces | CAN"


# --------------------------------------------------------------------------
# 4. The diff reports appended columns on what was written.
# --------------------------------------------------------------------------


# The shared fixtures already carry an appended column: ``FPU`` is in the API
# and absent from the workbook. ST gives TEST001 a value for it and says
# nothing for TEST002, which is exactly the two cases that matter here.


class TestAppendedColumnsAreReportedOnWhatWasWritten:
    def _compare(self, workbook_path, grid, written, from_datasheet):
        from stproducts.compose import Cell

        sheet = read_original(workbook_path)
        keys = [*sheet.column_keys, "FPU", "Dual-bank Flash"]
        composed = ComposedSheet()
        for part in grid.part_numbers:
            composed.parts[part] = compose_part(part, grid, keys, None)
        # TEST002: ST is silent on FPU. Give it a datasheet-sourced value.
        composed.parts["TEST002"].cells["FPU"] = Cell(
            value=written,
            token=DATASHEET if from_datasheet else "API",
            source="Table 3. features and peripheral counts" if from_datasheet else "",
        )
        return compare(sheet, grid, composed)

    def test_a_datasheet_filled_appended_column_is_reported(self, workbook_path, grid):
        """The exact shape of the bug: ST says nothing, the datasheet fills
        the cell, the workbook ships it. It must appear in the diff."""
        result = self._compare(workbook_path, grid, "2", from_datasheet=True)
        added = [
            r for r in result.records
            if r.kind == ADDED_COLUMN and r.column == "FPU" and r.part == "TEST002"
        ]
        assert added, "a value written from the datasheet must reach the diff"
        assert added[0].new == "2"

    def test_an_empty_api_column_is_still_suppressed(self, workbook_path, grid):
        """The suppression that keeps the report readable must survive: an
        absent boolean renders "No", and a column of "No" is pure noise."""
        result = self._compare(workbook_path, grid, "No", from_datasheet=False)
        added = [
            r for r in result.records
            if r.kind == ADDED_COLUMN and r.column == "FPU" and r.part == "TEST002"
        ]
        assert not added

    def test_a_populated_api_column_is_reported_as_before(self, workbook_path, grid):
        """TEST001 does have an FPU value from ST; that behaviour is unchanged."""
        result = self._compare(workbook_path, grid, "No", from_datasheet=False)
        added = [
            r for r in result.records
            if r.kind == ADDED_COLUMN and r.column == "FPU" and r.part == "TEST001"
        ]
        assert added and added[0].new == "Yes"
