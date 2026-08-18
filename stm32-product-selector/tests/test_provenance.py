"""Provenance invariants -- the guarantee behind validation item 4."""

from __future__ import annotations

import pytest

from stproducts.fieldmap import FIELD_MAP, coverage, spec_for
from stproducts.provenance import (
    AMBIGUOUS,
    API,
    DATASHEET,
    DERIVED,
    TOKENS,
    UNAVAILABLE,
    Reading,
    check_invariants,
)


def test_datasheet_reading_must_name_its_source():
    """No cell may claim it was read from a PDF without saying which table."""
    with pytest.raises(ValueError, match="source table"):
        Reading(DATASHEET, "128")
    with pytest.raises(ValueError, match="source table"):
        Reading(DERIVED, "12")
    assert Reading(DATASHEET, "128", "Table 2. ...").value == "128"


def test_datasheet_reading_must_carry_a_value():
    with pytest.raises(ValueError, match="must carry a value"):
        Reading(DATASHEET, None, "Table 2. ...")


def test_ambiguous_needs_no_source_but_records_evidence():
    reading = Reading(AMBIGUOUS, conditions="2.5 uA @ Standby, 25 C")
    assert reading.value is None
    assert reading.conditions


def test_unknown_token_rejected():
    with pytest.raises(ValueError, match="unknown provenance token"):
        Reading("GUESSED", "3", "Table 2")


def test_check_invariants_catches_sourceless_datasheet_cells():
    problems = check_invariants([DATASHEET, API], {"1": ""})
    assert problems and "no source table" in problems[0]
    assert check_invariants([DATASHEET, API], {"0": "Table 2. ..."}) == []


def test_every_token_is_one_of_five():
    assert set(TOKENS) == {DATASHEET, DERIVED, AMBIGUOUS, API, UNAVAILABLE}


class TestFieldMap:
    """The tiering is configuration; these assert what §3 of the spec says."""

    def test_the_nine_already_working_fields_are_datasheet(self):
        for key in [
            "Flash Size (kB) (Prog)", "RAM Size (kB)", "I2C typ", "SPI typ",
            "CAN (2.0)", "I/Os (High Current)", "USART typ", "UART typ", "Package",
        ]:
            assert spec_for(key).tier == DATASHEET, key
            assert spec_for(key).reader

    def test_added_datasheet_fields(self):
        for key in [
            "I2S typ", "Core", "Operating Frequency (MHz)",
            "Supply Voltage (V) min", "Supply Voltage (V) max",
            "Operating Temperature (°C) min", "Operating Temperature (°C) max",
        ]:
            assert spec_for(key).tier == DATASHEET, key

    def test_timers_are_derived_with_a_stated_rule(self):
        for key in ("Timers (16-bit) typ", "Timers (32-bit) typ"):
            spec = spec_for(key)
            assert spec.tier == DERIVED
            assert spec.reason, "a DERIVED field must state its rule"

    def test_electrical_and_converter_fields_are_ambiguous(self):
        for key in [
            "Supply Current (µA) (@ Lowest Power) typ",
            "Supply Current (µA) (Run Mode (per MHz)) typ",
            "A/D Converters 12-bit | Number of A/D Converters typ",
            "A/D Converters 12-bit | Number of Channels typ",
            "D/A Converters (12-bit) typ",
        ]:
            assert spec_for(key).tier == AMBIGUOUS, key
            assert spec_for(key).reason

    def test_api_fields_and_absence_fields(self):
        for key in ("Part Number", "General Description", "Marketing Status"):
            assert spec_for(key).tier == API, key
        for key in ("Dual-bank Flash", "Comparator", "Cryptography", "Security Functions"):
            spec = spec_for(key)
            assert spec.tier == API
            assert "absence" in spec.reason

    def test_unmapped_column_defaults_to_api_with_a_reason(self):
        spec = spec_for("Some Column ST Invented Yesterday")
        assert spec.tier == API
        assert spec.reason

    def test_coverage_groups_a_sheet_by_tier(self):
        grouped = coverage(["I2C typ", "Part Number", "Timers (16-bit) typ"])
        assert grouped[DATASHEET] == ["I2C typ"]
        assert grouped[API] == ["Part Number"]
        assert grouped[DERIVED] == ["Timers (16-bit) typ"]

    def test_every_mapped_reader_exists(self):
        from stproducts.extract import READERS

        for key, spec in FIELD_MAP.items():
            if spec.reader:
                assert spec.reader in READERS, f"{key} -> {spec.reader}"
