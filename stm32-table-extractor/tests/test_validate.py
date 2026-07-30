"""RESERVED_FIELDS_TASK.md: the register_map field-coverage validator.

Operates on the actual exported rag_selective document (`table_content.
semantic.registers[].fields`), not the internal raw `tables_json` the rest
of `validate()` reconciles against the List of tables.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from rmtables.validate import validate


def _rag_doc(fields, semantic_type="register_map", register_name="FOO_REG", headers=None):
    if headers is None:
        headers = ["Offset", "Register"] + [str(n) for n in range(31, -1, -1)]
    return {
        "tables": [
            {
                "table_number": "1",
                "table_content": {
                    "semantic_type": semantic_type,
                    "headers": headers,
                    "semantic": {"registers": [{"name": register_name, "fields": fields}]},
                },
            }
        ]
    }


def test_field_coverage_reports_gap():
    # Only 31:8 covered -- bits 7..0 have no field entry at all.
    doc = _rag_doc([{"bits": "31:8", "name": "Res.", "reset": ""}])
    report = validate({"tables": []}, {}, doc)
    assert len(report.field_coverage_errors) == 1
    table_number, register, problem = report.field_coverage_errors[0]
    assert table_number == "1"
    assert register == "FOO_REG"
    assert "gap" in problem
    assert not report.is_clean()


def test_field_coverage_reports_overlap():
    # bits 15:8 double-counted by two overlapping entries.
    doc = _rag_doc([
        {"bits": "31:8", "name": "Res.", "reset": ""},
        {"bits": "15:0", "name": "DATA", "reset": ""},
    ])
    report = validate({"tables": []}, {}, doc)
    problems = [p for _, _, p in report.field_coverage_errors]
    assert any("overlap" in p for p in problems)


def test_field_coverage_clean_when_fields_exactly_cover_31_to_0():
    doc = _rag_doc([
        {"bits": "31:12", "name": "Res.", "reset": ""},
        {"bits": "11", "name": "UIFREMA", "reset": "0"},
        {"bits": "10", "name": "Res.", "reset": ""},
        {"bits": "9:8", "name": "CKD [1:0]", "reset": "00"},
        {"bits": "7", "name": "ARPE", "reset": "0"},
        {"bits": "6:4", "name": "Res.", "reset": ""},
        {"bits": "3", "name": "OPM", "reset": "0"},
        {"bits": "2", "name": "URS", "reset": "0"},
        {"bits": "1", "name": "UDIS", "reset": "0"},
        {"bits": "0", "name": "CEN", "reset": "0"},
    ])
    report = validate({"tables": []}, {}, doc)
    assert report.field_coverage_errors == []
    assert report.is_clean()


def test_field_coverage_ignores_non_register_map_tables():
    doc = _rag_doc([{"bits": "31:8", "name": "Res.", "reset": ""}], semantic_type="parameter")
    report = validate({"tables": []}, {}, doc)
    assert report.field_coverage_errors == []


def test_field_coverage_skipped_when_rag_doc_not_provided():
    report = validate({"tables": []}, {})
    assert report.field_coverage_errors == []


def test_field_coverage_uses_table_own_bit_span_not_hardcoded_32_bits():
    # Verified RM0490 artifact (Table 139, SPI/I2S register map): some real
    # register maps only print bits 15..0 -- a genuinely 16-bit-wide
    # register, not a parsing gap. The expected span must come from the
    # table's own headers, not an assumed 31..0.
    headers = ["Offset", "Register name"] + [str(n) for n in range(15, -1, -1)]
    doc = _rag_doc(
        [{"bits": "15:0", "name": "DATA", "reset": ""}],
        headers=headers,
    )
    report = validate({"tables": []}, {}, doc)
    assert report.field_coverage_errors == []
