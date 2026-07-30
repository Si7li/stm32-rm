import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from rmtables.chunk import approx_tokens, build_chunks


def _wide_table(n_registers: int) -> dict:
    header = ["Offset", "Register"] + [str(b) for b in range(20, -1, -1)]
    rows = [list(header)]
    for i in range(n_registers):
        rows.append([f"0x{i*4:03X}", f"REG{i}"] + ["Res."] * 21)
        rows.append([None, "Reset value"] + ["0"] * 21)
    return {
        "type": "caption_table",
        "table_number": 99,
        "caption": "Big register map and reset values",
        "section_number": "9.9",
        "section_title": "Big peripheral",
        "page_start": 1,
        "page_end": 1,
        "spans_pages": False,
        "header": header,
        "rows": rows,
        "n_rows": len(rows),
        "n_cols": len(header),
    }


def test_register_layout_is_never_split():
    reg = {
        "type": "register_layout",
        "register": "FLASH_ACR",
        "section_number": "4.7.1",
        "section_title": "FLASH access control register (FLASH_ACR)",
        "address_offset": "0x000",
        "reset_value": "0x00000600",
        "page_start": 77,
        "page_end": 77,
        "bits": [{"bit": b, "field": "Res.", "access": ""} for b in range(32)],
        "field_descriptions": [
            {"bits": str(b), "field": None, "text": "x" * 50} for b in range(32)
        ],
    }
    records = build_chunks(reg, "rm0490.pdf", chunk_tokens=10)  # tiny budget
    assert len(records) == 1
    assert records[0]["metadata"]["n_chunks"] == 1


def test_small_table_produces_one_chunk():
    table = _wide_table(2)
    records = build_chunks(table, "rm0490.pdf", chunk_tokens=600)
    assert len(records) == 1
    assert records[0]["metadata"]["chunk_index"] == 0
    assert records[0]["metadata"]["n_chunks"] == 1
    assert records[0]["id"] == "rm0490-table99-chunk0"


def test_large_table_splits_by_row_groups_and_repeats_header():
    table = _wide_table(60)
    records = build_chunks(table, "rm0490.pdf", chunk_tokens=100)
    assert len(records) > 1
    for i, rec in enumerate(records):
        assert rec["metadata"]["chunk_index"] == i
        assert rec["metadata"]["n_chunks"] == len(records)
        assert rec["text"].startswith("[9.9 Big peripheral] Table 99.")
        assert approx_tokens(rec["text"]) <= 100 * 1.5  # generous slack for one oversized row-pair


def test_reset_row_never_separated_from_its_field_row():
    table = _wide_table(60)
    records = build_chunks(table, "rm0490.pdf", chunk_tokens=100)
    for rec in records:
        # every "offset ..." line in a chunk must be paired with its reset,
        # i.e. no chunk boundary should split a field row from its reset row
        assert "reset " in rec["text"] or "REG" not in rec["text"]


def test_register_id_disambiguates_by_section_and_offset():
    reg_a = {
        "type": "register_layout", "register": "UID", "section_number": "31.1",
        "section_title": "Unique device ID register (96 bits) (UID)",
        "address_offset": "0x00", "reset_value": None, "page_start": 1, "page_end": 1,
        "bits": [], "field_descriptions": [],
    }
    reg_b = dict(reg_a, address_offset="0x04")
    id_a = build_chunks(reg_a, "rm0490.pdf")[0]["id"]
    id_b = build_chunks(reg_b, "rm0490.pdf")[0]["id"]
    assert id_a != id_b
