"""``Supply Voltage (V) min`` reads must never fabricate a bare number.

Two corruption shapes hit ``read_voltage``:

1. A footnote layered *inside* the figure. The L4/L4+/L5 datasheets render
   the V DD min as ``1(.71)1`` (footnote glyph between digits); stripping
   footnotes cannot see that, so the old code read ``1`` and wrote it as
   DATASHEET. Any parentheses in the cell must disqualify the figure.
2. Condition-split rows. The L1 datasheet splits the ``V DD`` standard
   operating voltage row into three labelled sub-rows (BOR disabled 1.65 /
   enabled 1.8). Picking the first printed row asserted a value ST does not
   publish. Distinct values across the condition rows must be AMBIGUOUS.
"""

from __future__ import annotations

import pathlib

import pytest

from stproducts.extract import Document, read_voltage
from stproducts.provenance import AMBIGUOUS, DATASHEET


def _doc(vdd_cells: list[str], condition_rows: list[list[str]] | None = None) -> Document:
    if condition_rows is None:
        condition_rows = [
            ["V DD", "Standard operating voltage", "BOR detector enabled, at power-on", *vdd_cells]
        ]
    rows = [["Symbol", "Parameter", "Conditions", "Min", "Max", "Unit"], *condition_rows]
    return Document(
        part="STM32L100RC",
        path=pathlib.Path(__file__),
        tables=[("Table 12. General operating conditions", rows)],
    )


class TestFootnoteMangles:
    @pytest.mark.parametrize("raw", ["1(.71)1", "1(.17)1", "1(.)1"])
    def test_mangled_footnote_never_reads_the_bare_integer(self, raw):
        """``1(.71)1`` is 1.71 with the footnote inside, not ``1``."""
        reading = read_voltage(_doc([raw, "3.6"]), "min")
        assert reading is not None
        assert reading.token == AMBIGUOUS
        assert reading.value is None
        assert raw in reading.conditions

    def test_clean_footnote_is_ambiguous_too(self):
        reading = read_voltage(_doc(["1.71(1)", "3.6"]), "min")
        assert reading is not None
        assert reading.token == AMBIGUOUS
        assert reading.value is None


class TestConditionSplitRows:
    def test_distinct_min_values_are_ambiguous(self):
        doc = _doc(
            [],
            condition_rows=[
                ["V DD", "Standard operating voltage", "BOR detector disabled", "1.65", "3.6", "V"],
                ["V DD", "Standard operating voltage", "BOR detector enabled, at power-on", "1.8", "3.6", "V"],
                ["V DD", "Standard operating voltage", "BOR detector disabled, after power-on", "1.65", "3.6", "V"],
            ],
        )
        reading = read_voltage(doc, "min")
        assert reading is not None
        assert reading.token == AMBIGUOUS
        assert reading.value is None
        assert "1.65" in reading.conditions and "1.8" in reading.conditions

    def test_identical_values_are_not_ambiguous(self):
        doc = _doc(
            [],
            condition_rows=[
                ["V DD", "Standard operating voltage", "BOR detector disabled", "1.8", "3.6", "V"],
                ["V DD", "Standard operating voltage", "BOR detector enabled, at power-on", "1.8", "3.6", "V"],
            ],
        )
        reading = read_voltage(doc, "min")
        assert reading is not None
        assert reading.token == DATASHEET
        assert reading.value == "1.8"


class TestPlainValue:
    def test_clean_single_figure_is_datasheet(self):
        reading = read_voltage(_doc(["1.65", "3.6"]), "min")
        assert reading is not None
        assert reading.token == DATASHEET
        assert reading.value == "1.65"

    def test_dash_cell_is_ignored(self):
        """A ``-`` min (no V DD row for that bound) yields no reading."""
        assert read_voltage(_doc(["-", "3.6"]), "min") is None
