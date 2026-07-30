import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from rmtables.headings import HeadingTracker
from rmtables.model import RawTable
from rmtables.registers import (
    RegisterMerger,
    bit_header_kind,
    parse_bit_description,
)


def test_bit_header_kind_detects_hi_and_lo():
    assert bit_header_kind("31 30 29 28 27 26 25 24 23 22 21 20 19 18 17 16") == "hi"
    assert bit_header_kind("15 14 13 12 11 10 9 8 7 6 5 4 3 2 1 0") == "lo"


def test_bit_header_kind_rejects_non_descending_or_wrong_length():
    assert bit_header_kind("31 30 29") is None  # too short
    assert bit_header_kind("31 30 29 28 27 26 25 24 23 22 21 20 19 18 17 15") is None  # not descending


def test_parse_bit_description_single_bit_with_field():
    d = parse_bit_description("Bit 18 DBG_SWEN: Debug access software enable")
    assert d == {"bits": "18", "field": "DBG_SWEN", "text": "Debug access software enable"}


def test_parse_bit_description_range_reserved():
    d = parse_bit_description("Bits 31:19 Reserved, must be kept at reset value.")
    assert d == {"bits": "31:19", "field": None, "text": "Reserved, must be kept at reset value."}


def test_parse_bit_description_range_with_bracket_field():
    d = parse_bit_description("Bits 2:0 LATENCY[2:0]: Latency")
    assert d == {"bits": "2:0", "field": "LATENCY", "text": "Latency"}


def _flash_acr_pages():
    """Reconstructs the verified FLASH_ACR layout from page 77 of RM0490."""
    heading_lines = [
        {"text": "4.7 FLASH registers", "top": 90},
        {"text": "4.7.1 FLASH access control register (FLASH_ACR)", "top": 121},
        {"text": "Address offset: 0x000", "top": 142},
        {"text": "Reset value: 0b0000 0000 0000 010X 0000 0110 0000 0000", "top": 160},
        {"text": "31 30 29 28 27 26 25 24 23 22 21 20 19 18 17 16", "top": 200},
        {"text": "15 14 13 12 11 10 9 8 7 6 5 4 3 2 1 0", "top": 247},
        {"text": "Bit 18 DBG_SWEN: Debug access software enable", "top": 315},
        {"text": "Bits 2:0 LATENCY[2:0]: Flash memory latency", "top": 400},
    ]
    hi_half = RawTable(
        page=77,
        bbox=(67.29, 210.45, 527.97, 244.47),
        rows=[
            ["Res."] * 13 + ["DBG_SWEN", "Res.", "EMPTY"],
            [""] * 13 + ["rw", "", "rw"],
        ],
    )
    lo_half = RawTable(
        page=77,
        bbox=(67.29, 257.49, 527.97, 283.47),
        rows=[
            ["Res.", "Res.", "Res.", "Res.", "ICRST", "Res.", "ICEN", "PRFTEN",
             "Res.", "Res.", "Res.", "Res.", "Res.", "LATENCY[2:0]", None, None],
            ["", "", "", "", "rw", "", "rw", "rw", "", "", "", "", "", "rw", "rw", "rw"],
        ],
    )
    return heading_lines, [hi_half, lo_half]


def test_register_merger_builds_full_32_bit_map_from_hi_lo_halves():
    heading_lines, raw_tables = _flash_acr_pages()
    tracker = HeadingTracker()
    tracker.start_page(heading_lines)

    merger = RegisterMerger()
    consumed = merger.process_page(77, raw_tables, heading_lines, tracker)
    tracker.finish_page()

    assert len(consumed) == 2
    registers = merger.finalize()
    assert len(registers) == 1
    reg = registers[0]
    assert reg.register == "FLASH_ACR"
    assert reg.section_number == "4.7.1"
    assert reg.address_offset == "0x000"
    assert reg.page_start == 77
    assert reg.page_end == 77
    assert reg.spans_pages is False

    bits_by_num = {b["bit"]: b for b in reg.bits}
    assert set(bits_by_num) == set(range(32))
    assert bits_by_num[18]["field"] == "DBG_SWEN"
    assert bits_by_num[18]["access"] == "rw"
    assert bits_by_num[2]["field"] == "LATENCY[2:0]"
    assert bits_by_num[0]["field"] == "LATENCY[2:0]"

    desc_by_bits = {d["bits"]: d for d in reg.field_descriptions}
    assert desc_by_bits["18"]["field"] == "DBG_SWEN"
    assert desc_by_bits["2:0"]["field"] == "LATENCY"


def test_register_merger_pairs_hi_half_across_page_break():
    page1_lines = [
        {"text": "18.4.23 TIM3 alternate function option register 1 (TIM3_AF1)", "top": 90},
        {"text": "Address offset: 0x60", "top": 110},
        {"text": "Reset value: 0x00000000", "top": 130},
        {"text": "31 30 29 28 27 26 25 24 23 22 21 20 19 18 17 16", "top": 150},
    ]
    hi_half = RawTable(
        page=100,
        bbox=(67.29, 160, 527.97, 190),
        rows=[["Res."] * 14 + ["ETRSEL[3:2]", None], [""] * 14 + ["rw", "rw"]],
    )
    merger = RegisterMerger()
    tracker = HeadingTracker()
    tracker.start_page(page1_lines)
    merger.process_page(100, [hi_half], page1_lines, tracker)
    tracker.finish_page()
    assert merger.current is not None  # lo half still pending, not finalized yet

    page2_lines = [{"text": "15 14 13 12 11 10 9 8 7 6 5 4 3 2 1 0", "top": 95}]
    lo_half = RawTable(
        page=101,
        bbox=(67.29, 105, 527.97, 135),
        rows=[["ETRSEL[1:0]", None] + ["Res."] * 14, ["rw", "rw"] + [""] * 14],
    )
    tracker.start_page(page2_lines)
    merger.process_page(101, [lo_half], page2_lines, tracker)

    registers = merger.finalize()
    assert len(registers) == 1
    reg = registers[0]
    assert reg.register == "TIM3_AF1"
    assert reg.page_start == 100
    assert reg.page_end == 101
    assert reg.spans_pages is True
    bits_by_num = {b["bit"]: b for b in reg.bits}
    assert set(bits_by_num) == set(range(32))
    # lo_half's field row is ["ETRSEL[1:0]", None, "Res." x14]: the field
    # occupies register bits 15:14 (its own "[1:0]" label numbers its bits
    # relative to the field itself, not the register).
    assert bits_by_num[15]["field"] == "ETRSEL[1:0]"
    assert bits_by_num[14]["field"] == "ETRSEL[1:0]"
    assert bits_by_num[13]["field"] == "Res."
