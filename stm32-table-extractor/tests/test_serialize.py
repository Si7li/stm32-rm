import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from rmtables.serialize import serialize_caption_table, serialize_register_layout


def test_narrow_table_renders_markdown_and_linearized_lines():
    table = {
        "type": "caption_table",
        "table_number": 10,
        "caption": "Flash memory organization",
        "section_number": "3.3.1",
        "section_title": "Flash memory",
        "header": ["Bank", "Size", "Pages"],
        "rows": [
            ["Bank", "Size", "Pages"],
            ["Bank 1", "128K", "64"],
        ],
    }
    text = serialize_caption_table(table)
    assert "[3.3.1 Flash memory] Table 10. Flash memory organization" in text
    assert "| Bank | Size | Pages |" in text
    assert "Bank 1: Size=128K, Pages=64" in text


def test_wide_register_map_pairs_field_row_with_reset_row():
    header = ["Offset", "Register", "20", "19", "18", "17", "16"]
    table = {
        "type": "caption_table",
        "table_number": 26,
        "caption": "FLASH register map and reset values",
        "section_number": "4.7.14",
        "section_title": "FLASH register map",
        "header": header,
        "rows": [
            list(header),
            ["0x000", "FLASH_ACR", "Res.", "Res.", "DBG_SWEN", "Res.", "EMPTY"],
            [None, "Reset value", "", "", "1", "", "0"],
        ],
    }
    text = serialize_caption_table(table)
    assert "offset 0x000 -- FLASH_ACR" in text
    assert "bit 18 DBG_SWEN" in text
    assert "bit 16 EMPTY" in text
    assert "reset 10" in text
    assert "Res." not in text.split("\n")[-1]  # reserved bits excluded from field list


def test_register_layout_consolidates_multi_bit_field_and_appends_descriptions():
    reg = {
        "type": "register_layout",
        "register": "FLASH_ACR",
        "section_number": "4.7.1",
        "section_title": "FLASH access control register (FLASH_ACR)",
        "address_offset": "0x000",
        "reset_value": "0x00000600",
        "bits": [
            {"bit": 18, "field": "DBG_SWEN", "access": "rw"},
            {"bit": 17, "field": "Res.", "access": ""},
            {"bit": 2, "field": "LATENCY[2:0]", "access": "rw"},
            {"bit": 1, "field": "LATENCY[2:0]", "access": "rw"},
            {"bit": 0, "field": "LATENCY[2:0]", "access": "rw"},
        ],
        "field_descriptions": [
            {"bits": "18", "field": "DBG_SWEN", "text": "Debug access software enable"},
        ],
    }
    text = serialize_register_layout(reg)
    lines = text.split("\n")
    assert "Register FLASH_ACR (offset 0x000, reset 0x00000600)" in text
    assert "bit 18 DBG_SWEN (rw)" in lines
    assert "bit 2:0 LATENCY[2:0] (rw)" in lines  # consecutive same-field bits consolidated
    assert not any(line.startswith("bit 17") for line in lines)  # reserved bit skipped
    assert "bit 18 DBG_SWEN: Debug access software enable" in lines
