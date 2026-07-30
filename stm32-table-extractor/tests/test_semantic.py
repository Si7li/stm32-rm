"""Golden-set tests for semantic table typing (SEMANTIC_BUILD_TASK.md).

Each fixture below is a real header/row slice taken directly from RM0490 or
RM0008's actual extracted output (not synthesized), so these lock in
verified real-world behavior, not idealized cases.
"""

import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from rmtables.semantic import (
    extract_alternate_function,
    extract_feature_matrix,
    extract_interrupt_vector,
    extract_memory_map,
    extract_parameter,
    extract_register_map,
)
from rmtables.semantic_classify import classify_table

# ------------------------------------------------------------- register_map
# RM0490 Table 26 "FLASH register map and reset values" (first 2 registers).

REGISTER_MAP_HEADERS = ["Offset", "Register"] + [str(n) for n in range(31, -1, -1)]
REGISTER_MAP_ROWS = [
    ["0x000", "FLASH_ACR", "Res.", "Res.", "Res.", "Res.", "Res.", "Res.", "Res.", "Res.",
     "Res.", "Res.", "Res.", "Res.", "Res.", "DBG_SWEN", "Res.", "EMPTY", "Res.", "Res.",
     "Res.", "Res.", "ICRST", "Res.", "ICEN", "PRFTEN", "Res.", "Res.", "Res.", "Res.",
     "Res.", "LATENCY\n[2:0]", "LATENCY\n[2:0]", "LATENCY\n[2:0]"],
    ["0x000", "Reset value", "", "", "", "", "", "", "", "", "", "", "", "", "", "1", "",
     "X", "", "", "", "", "0", "", "1", "0", "", "", "", "", "", "0", "0", "0"],
    ["0x004", "Reserved"] + ["Res."] * 32,
]


def test_classify_register_map_from_header_signature():
    assert classify_table("FLASH register map and reset values", REGISTER_MAP_HEADERS, REGISTER_MAP_ROWS)[0] == "register_map"


def test_register_map_pairs_field_row_with_reset_row():
    result = extract_register_map(REGISTER_MAP_HEADERS, REGISTER_MAP_ROWS)
    registers = result["registers"]
    assert registers[0]["offset"] == "0x000"
    assert registers[0]["name"] == "FLASH_ACR"
    assert {"bits": "18", "name": "DBG_SWEN", "reset": "1"} in registers[0]["fields"]
    assert {"bits": "2:0", "name": "LATENCY[2:0]", "reset": "000"} in registers[0]["fields"]
    assert {"bits": "16", "name": "EMPTY", "reset": "X"} in registers[0]["fields"]
    # bit 16 ("EMPTY") is undefined ("X") -> the whole register keeps the
    # raw MSB->LSB bit-string (reserved bits filled as '0'), never a faked hex
    assert registers[0]["reset_value"] == "000000000000010X0000001000000000"
    assert len(registers[0]["reset_value"]) == 32


def test_register_map_reserved_register_has_single_reserved_field():
    # RESERVED_FIELDS_TASK.md: a wholly-reserved register is now ONE
    # reserved field spanning its full width, not an empty list -- so
    # `fields` still covers 31..0 even for a register with no named bits.
    result = extract_register_map(REGISTER_MAP_HEADERS, REGISTER_MAP_ROWS)
    reserved = result["registers"][1]
    assert reserved["name"] == "Reserved"
    assert reserved["fields"] == [{"bits": "31:0", "name": "Res.", "reset": ""}]
    assert reserved["reset_value"] == ""


def test_register_map_reset_value_hex_packs_when_unambiguous():
    headers = ["Offset", "Register"] + [str(n) for n in range(31, -1, -1)]
    rows = [
        ["0x008", "FLASH_KEYR"] + ["KEYR[31:0]"] * 32,
        ["0x008", "Reset value"] + ["0"] * 32,
    ]
    result = extract_register_map(headers, rows)
    assert result["registers"][0]["reset_value"] == "0x00000000"


def test_register_map_handles_hyphenated_offset_header_variant():
    # Verified RM0490 artifact: a narrow "Offset" header wraps to "Off-\nset".
    headers = ["Off-\nset", "Register"] + [str(n) for n in range(31, -1, -1)]
    result = extract_register_map(headers, REGISTER_MAP_ROWS)
    assert result is not None
    assert result["registers"][0]["offset"] == "0x000"


def test_register_map_handles_blank_reset_row_label():
    # Verified RM0008 artifact (DAC register map): the "Reset value" label
    # is blank on one row, but the offset still matches the field row above.
    headers = ["Offset", "Register"] + [str(n) for n in range(7, -1, -1)]
    rows = [
        ["0x1C", "DAC_DHR8R2"] + ["Reserved"] * 8,
        ["0x1C", ""] + ["Reserved"] * 8,
    ]
    result = extract_register_map(headers, rows)
    assert len(result["registers"]) == 1
    assert result["registers"][0]["name"] == "DAC_DHR8R2"


# ---------------------------------------------------- REGISTER_MAP_FIX.md
# RM0008 Table 10 "CRC calculation unit register map and reset values" --
# exact real headers/rows (grouped bit-range headers "31-24"/"23-16"/"15-8",
# and a "Reset \nvalue" label with an embedded newline).

CRC_HEADERS = ["Offset", "Register", "31-24", "23-16", "15-8", "7", "6", "5", "4", "3", "2", "1", "0"]
CRC_ROWS = [
    ["0x00", "CRC_DR", "Data register", "Data register", "Data register", "Data register",
     "Data register", "Data register", "Data register", "Data register", "Data register",
     "Data register", "Data register"],
    ["0x00", "Reset \nvalue", "0xFFFF FFFF", "0xFFFF FFFF", "0xFFFF FFFF", "0xFFFF FFFF",
     "0xFFFF FFFF", "0xFFFF FFFF", "0xFFFF FFFF", "0xFFFF FFFF", "0xFFFF FFFF",
     "0xFFFF FFFF", "0xFFFF FFFF"],
    ["0x04", "CRC_IDR", "Reserved", "Reserved", "Reserved", "Independent data register",
     "Independent data register", "Independent data register", "Independent data register",
     "Independent data register", "Independent data register", "Independent data register",
     "Independent data register"],
    ["0x04", "Reset \nvalue", "Reserved", "Reserved", "Reserved", "0x00", "0x00", "0x00",
     "0x00", "0x00", "0x00", "0x00", "0x00"],
    ["0x08", "CRC_CR", "Reserved", "Reserved", "Reserved", "Reserved", "Reserved", "Reserved",
     "Reserved", "Reserved", "Reserved", "Reserved", "RESET"],
    ["0x08", "Reset \nvalue", "Reserved", "Reserved", "Reserved", "Reserved", "Reserved",
     "Reserved", "Reserved", "Reserved", "Reserved", "Reserved", "0"],
]


def test_register_map_fix_reset_row_folds_with_embedded_newline_no_pseudo_register():
    result = extract_register_map(CRC_HEADERS, CRC_ROWS)
    names = [r["name"] for r in result["registers"]]
    # 0 pseudo "Reset value" registers -- only the 3 real registers remain.
    assert names == ["CRC_DR", "CRC_IDR", "CRC_CR"]
    assert not any("reset" in n.lower() and "value" in n.lower() for n in names)


def test_register_map_fix_grouped_bit_range_header_gives_full_span():
    result = extract_register_map(CRC_HEADERS, CRC_ROWS)
    crc_dr = result["registers"][0]
    assert crc_dr["offset"] == "0x00"
    assert crc_dr["name"] == "CRC_DR"
    # "Data register" spans the grouped 31-24/23-16/15-8 headers plus the
    # single-bit 7..0 headers -- the full register width, not just 7:0.
    assert crc_dr["fields"] == [{"bits": "31:0", "name": "Data register", "reset": "1" * 32}]
    # REGISTER_RESET_FIX.md: hex-style reset row -> normalized "0x........",
    # never the raw "0xFFFF FFFF" (with its embedded space) nor a bare "0".
    assert crc_dr["reset_value"] == "0xFFFFFFFF"


def test_register_map_fix_reserved_grouped_columns_included_as_res_field():
    result = extract_register_map(CRC_HEADERS, CRC_ROWS)
    crc_idr = result["registers"][1]
    # RESERVED_FIELDS_TASK.md: "Reserved" (bits 31:8, grouped headers
    # "31-24"/"23-16"/"15-8") is now an explicit "Res." entry ahead of the
    # actual "Independent data register" field (bits 7:0), MSB->LSB.
    assert crc_idr["fields"] == [
        {"bits": "31:8", "name": "Res.", "reset": ""},
        {"bits": "7:0", "name": "Independent data register", "reset": "00000000"},
    ]
    # Reserved bits 31:8 count as 0 -> a full clean 32-bit hex, not just "0x00".
    assert crc_idr["reset_value"] == "0x00000000"


def test_register_map_fix_single_bit_field_yields_single_number():
    result = extract_register_map(CRC_HEADERS, CRC_ROWS)
    crc_cr = result["registers"][2]
    assert crc_cr["fields"] == [
        {"bits": "31:1", "name": "Res.", "reset": ""},
        {"bits": "0", "name": "RESET", "reset": "0"},
    ]
    assert crc_cr["reset_value"] == "0x00000000"


def test_parse_bit_header_single_and_grouped_range():
    from rmtables.semantic import _parse_bit_header

    assert _parse_bit_header("7") == (7, 7)
    assert _parse_bit_header("31-24") == (31, 24)
    assert _parse_bit_header("31:24") == (31, 24)
    assert _parse_bit_header("Register") is None


def test_parse_bit_header_accepts_space_separated_range():
    # CELL_TEXT_ASSEMBLY_FIX.md: fixing the cell-text gap-space bug turns a
    # header that used to render fused ("3124") into space-separated
    # ("31 24") -- the grouped-header expansion must accept it exactly like
    # the already-supported "31-24"/"31:24" forms.
    from rmtables.semantic import _parse_bit_header

    assert _parse_bit_header("31 24") == (31, 24)
    assert _parse_bit_header("23  16") == (23, 16)  # multiple spaces too


def test_register_map_fix_compound_label_containing_reset_value_is_not_an_orphan():
    # Verified RM0008 artifact (GPIO register map, Table 59): the
    # Register-column cell is the SAME compound label, e.g.
    # "GPIOx\n_CRL\nReset value", on BOTH the field row and its reset row --
    # not a clean "Reset value" on the reset row alone. A naive "contains
    # reset value" orphan check would treat every row as a reset row with
    # nothing to pair, emptying the whole table; the field row must still
    # become a register, and the row after it must still pair as its reset.
    headers = ["Offset", "Register"] + [str(n) for n in range(3, -1, -1)]
    rows = [
        ["0x00", "GPIOx\n_CRL\nReset value", "CNF", "CNF", "MODE", "MODE"],
        ["0x00", "GPIOx\n_CRL\nReset value", "0", "1", "0", "0"],
    ]
    result = extract_register_map(headers, rows)
    assert result is not None
    assert len(result["registers"]) == 1
    reg = result["registers"][0]
    assert reg["fields"] == [
        {"bits": "3:2", "name": "CNF", "reset": "01"},
        {"bits": "1:0", "name": "MODE", "reset": "00"},
    ]
    # unambiguous 0/1 bits -> hex-packed, same as any other clean reset row
    assert reg["reset_value"] == "0x4"


def test_clean_register_name_strips_trailing_reset_value_and_paren_qualifier():
    from rmtables.semantic import _clean_register_name

    assert _clean_register_name("Reset \nvalue") == ""
    assert _clean_register_name("Reset value") == ""
    assert _clean_register_name("GPIOx\n_CRL\nReset value") == "GPIOx_CRL"
    assert _clean_register_name("TIMx_CR1 Reset value") == "TIMx_CR1"
    # verified RM0490 artifact: the qualifier trails "reset value" itself
    assert _clean_register_name("Reset value\n(ports other than A)") == ""
    assert _clean_register_name("Reset value (port A)") == ""


# ----------------------------------------------------- REGISTER_RESET_FIX.md
# RM0490 Table 54 "DMAMUX register map and reset values" -- exact real
# headers/rows for DMAMUX_C0CR: a per-bit reset row (each bit-number header
# column has its own '0'/'1'/'X'/'' cell), with reserved gaps between fields.

DMAMUX_HEADERS = ["Offset", "Register"] + [str(n) for n in range(31, -1, -1)]
DMAMUX_C0CR_ROWS = [
    ["0x000", "DMAMUX_C0CR", "Res.", "Res.", "Res.", "SYNC_ID[4:0]", "SYNC_ID[4:0]",
     "SYNC_ID[4:0]", "SYNC_ID[4:0]", "SYNC_ID[4:0]", "NBREQ[4:0]", "NBREQ[4:0]",
     "NBREQ[4:0]", "NBREQ[4:0]", "NBREQ[4:0]", "SPOL\n[1:0]", "SPOL\n[1:0]", "SE",
     "Res.", "Res.", "Res.", "Res.", "Res.", "Res.", "EGE", "SOIE", "Res.", "Res.",
     "DMAREQ_ID[5:0]", "DMAREQ_ID[5:0]", "DMAREQ_ID[5:0]", "DMAREQ_ID[5:0]",
     "DMAREQ_ID[5:0]", "DMAREQ_ID[5:0]"],
    ["0x000", "Reset value", "", "", "", "0", "0", "0", "0", "0", "0", "0", "0", "0",
     "0", "0", "0", "0", "", "", "", "", "", "", "0", "0", "", "", "0", "0", "0", "0",
     "0", "0"],
]


def test_register_reset_fix_dmamux_c0cr_gives_clean_zero_hex_with_per_field_resets():
    result = extract_register_map(DMAMUX_HEADERS, DMAMUX_C0CR_ROWS)
    reg = result["registers"][0]
    assert reg["name"] == "DMAMUX_C0CR"
    # Acceptance: 0x00000000, NOT a lone "0".
    assert reg["reset_value"] == "0x00000000"
    fields_by_name = {f["name"]: f for f in reg["fields"]}
    assert fields_by_name["SYNC_ID[4:0]"] == {"bits": "28:24", "name": "SYNC_ID[4:0]", "reset": "00000"}
    assert fields_by_name["NBREQ[4:0]"]["reset"] == "00000"
    assert fields_by_name["SPOL[1:0]"]["reset"] == "00"
    assert fields_by_name["SE"]["reset"] == "0"
    assert fields_by_name["EGE"]["reset"] == "0"
    assert fields_by_name["SOIE"]["reset"] == "0"
    assert fields_by_name["DMAREQ_ID[5:0]"]["reset"] == "000000"
    # every NAMED field has a reset slice; no leftover blanks
    assert all(f["reset"] for f in reg["fields"] if f["name"] != "Res.")
    # RESERVED_FIELDS_TASK.md: the reserved gaps between fields (31:29,
    # 15:10, 7:6) are now explicit "Res." entries with reset == "", and the
    # whole set of fields covers 31..0 with no gaps or overlaps.
    reserved_runs = [f["bits"] for f in reg["fields"] if f["name"] == "Res."]
    assert reserved_runs == ["31:29", "15:10", "7:6"]
    assert all(f["reset"] == "" for f in reg["fields"] if f["name"] == "Res.")
    covered = set()
    for f in reg["fields"]:
        hi, lo = (int(x) for x in f["bits"].split(":")) if ":" in f["bits"] else (int(f["bits"]),) * 2
        covered |= set(range(lo, hi + 1))
    assert covered == set(range(32))


def test_register_reset_fix_nonzero_bits_yield_correct_hex_not_raw_bitstring():
    # bits 23:12 set -> 0x00FFF000, not the raw "111111111111000000000000".
    headers = ["Offset", "Register"] + [str(n) for n in range(31, -1, -1)]
    ones = ["1"] * 12   # bits 23..12
    zeros_hi = ["0"] * 8   # bits 31..24
    zeros_lo = ["0"] * 12  # bits 11..0
    rows = [
        ["0x00", "TEST_REG"] + ["Res."] * 8 + ["FIELD[11:0]"] * 12 + ["Res."] * 12,
        ["0x00", "Reset value"] + zeros_hi + ones + zeros_lo,
    ]
    result = extract_register_map(headers, rows)
    reg = result["registers"][0]
    assert reg["reset_value"] == "0x00FFF000"
    assert "1" * 24 not in reg["reset_value"]


def test_register_reset_fix_hex_style_reset_row_normalizes_and_slices_fields():
    result = extract_register_map(CRC_HEADERS, CRC_ROWS)
    crc_dr = result["registers"][0]
    # CRC (hex style) -> normalized "0xFFFFFFFF", field sliced from it.
    assert crc_dr["reset_value"] == "0xFFFFFFFF"
    assert crc_dr["fields"][0]["reset"] == "1" * 32


def test_register_reset_fix_undefined_x_bit_keeps_bitstring_not_fake_hex():
    result = extract_register_map(REGISTER_MAP_HEADERS, REGISTER_MAP_ROWS)
    flash_acr = result["registers"][0]
    assert "X" in flash_acr["reset_value"]
    assert not flash_acr["reset_value"].startswith("0x")
    empty_field = next(f for f in flash_acr["fields"] if f["name"] == "EMPTY")
    assert empty_field["reset"] == "X"


def test_register_reset_fix_no_reset_row_yields_empty_string_not_zero():
    headers = ["Offset", "Register"] + [str(n) for n in range(3, -1, -1)]
    rows = [["0x00", "LONELY_REG", "A", "A", "B", "B"]]
    result = extract_register_map(headers, rows)
    reg = result["registers"][0]
    assert reg["reset_value"] == ""
    assert all(f["reset"] == "" for f in reg["fields"])


def test_register_reset_fix_grouped_header_expansion_in_reset_bit_map():
    # A grouped header column ("31-24") is expanded to its individual bit
    # indices when mapping the reset row, exactly like field bit-ranges --
    # a hex token spanning the group decodes across that group's own width.
    from rmtables.semantic import _reset_bit_map

    bit_cols = [(0, 31, 24), (1, 23, 16), (2, 15, 8), (3, 7, 0)]
    bitmap = _reset_bit_map(bit_cols, ["0xAB", "Reserved", "Reserved", "0x01"])
    assert [bitmap[b] for b in range(31, 23, -1)] == list("10101011")  # 0xAB
    assert all(b not in bitmap for b in range(8, 24))  # Reserved -> no entry
    assert [bitmap[b] for b in range(7, -1, -1)] == list("00000001")  # 0x01


# ----------------------------------------------------- RESERVED_FIELDS_TASK.md
# Reserved bit runs are now explicit `{"bits", "name": "Res.", "reset": ""}`
# entries, interleaved with named fields MSB->LSB so `fields` covers 31..0.

def test_reserved_fields_groups_single_and_multi_bit_runs():
    from rmtables.semantic import _build_fields

    # bit4 alone reserved, bits 2:1 a two-bit reserved run -- named fields
    # (A, B, C) break the runs apart.
    bit_cols = [(0, 4, 4), (1, 3, 3), (2, 2, 2), (3, 1, 1), (4, 0, 0)]
    values = ["Res.", "A", "Res.", "Res.", "B"]
    assert _build_fields(bit_cols, values) == [
        {"bits": "4", "name": "Res."},
        {"bits": "3", "name": "A"},
        {"bits": "2:1", "name": "Res."},
        {"bits": "0", "name": "B"},
    ]


def test_reserved_fields_normalizes_reserved_spelling_and_blank_cells():
    from rmtables.semantic import _build_fields

    # "Reserved", "res.", "Res" (no period), and a wholly blank cell are all
    # the same reserved marker and group into ONE run under the canonical
    # "Res." name, regardless of exact spelling.
    bit_cols = [(0, 4, 4), (1, 3, 3), (2, 2, 2), (3, 1, 1), (4, 0, 0)]
    values = ["Reserved", "res.", "Res", "", "A"]
    assert _build_fields(bit_cols, values) == [
        {"bits": "4:1", "name": "Res."},
        {"bits": "0", "name": "A"},
    ]


def test_reserved_fields_keeps_partially_named_cell_literal():
    from rmtables.semantic import _build_fields

    # "UIFCPY or Res." is partially named -- it must NOT be forced to
    # "Res." even though it contains the reserved word.
    bit_cols = [(0, 1, 1), (1, 0, 0)]
    values = ["UIFCPY or Res.", "CEN"]
    assert _build_fields(bit_cols, values) == [
        {"bits": "1", "name": "UIFCPY or Res."},
        {"bits": "0", "name": "CEN"},
    ]


def _field_coverage(fields: list[dict]) -> set[int]:
    covered = set()
    for f in fields:
        if ":" in f["bits"]:
            hi, lo = (int(x) for x in f["bits"].split(":"))
        else:
            hi = lo = int(f["bits"])
        covered |= set(range(lo, hi + 1))
    return covered


def test_reserved_fields_full_coverage_no_gaps_no_overlaps():
    headers = ["Offset", "Register"] + [str(n) for n in range(31, -1, -1)]
    rows = [
        ["0x00", "TEST_REG"] + ["Res."] * 8 + ["FIELD[11:0]"] * 12 + ["Res."] * 12,
    ]
    result = extract_register_map(headers, rows)
    fields = result["registers"][0]["fields"]
    assert _field_coverage(fields) == set(range(32))
    # no overlaps: the covered set's size must equal the sum of each run's width
    total_width = sum(
        (int(f["bits"].split(":")[0]) - int(f["bits"].split(":")[1]) + 1)
        if ":" in f["bits"] else 1
        for f in fields
    )
    assert total_width == 32


def test_reserved_fields_reset_is_blank_while_reset_value_stays_zero_filled():
    result = extract_register_map(CRC_HEADERS, CRC_ROWS)
    crc_idr = result["registers"][1]
    reserved_field = next(f for f in crc_idr["fields"] if f["name"] == "Res.")
    assert reserved_field["reset"] == ""
    # register-level reset_value still 0-fills the reserved span by convention.
    assert crc_idr["reset_value"] == "0x00000000"


TIM15_CR1_HEADERS = ["Offset", "Register name"] + [str(n) for n in range(31, -1, -1)]
TIM15_CR1_ROWS = [
    ["0x00", "TIM15_CR1"] + ["Res."] * 20 + [
        "UIFREMA", "Res.", "CKD \n[1:0]", "CKD \n[1:0]", "ARPE", "Res.", "Res.", "Res.",
        "OPM", "URS", "UDIS", "CEN",
    ],
    ["0x00", "Reset value"] + [""] * 20 + ["0", "", "0", "0", "0", "", "", "", "0", "0", "0", "0"],
]


def test_reserved_fields_tim15_cr1_matches_acceptance_block():
    # RESERVED_FIELDS_TASK.md acceptance block, verbatim -- real RM0490 row.
    result = extract_register_map(TIM15_CR1_HEADERS, TIM15_CR1_ROWS)
    reg = result["registers"][0]
    assert reg["name"] == "TIM15_CR1"
    assert reg["reset_value"] == "0x00000000"
    assert reg["fields"] == [
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
    ]


# --------------------------------------------------- REGISTER_MAP_FIX_2.md
# Follow-up: reset rows whose name cell is "<REGISTER> Reset value" (a
# substring, not the whole cell) still leaked as pseudo-registers.

def test_register_map_fix2_combined_name_reset_row_folds_not_a_pseudo_register():
    # "FOO_CR Reset value" -- combined register name + reset-value phrase
    # on ONE row, immediately after FOO_CR's own (differently-named) field
    # row -- must fold into FOO_CR, not become its own "FOO_CR Reset value"
    # register.
    headers = ["Offset", "Register"] + [str(n) for n in range(3, -1, -1)]
    rows = [
        ["0x00", "FOO_CR", "EN", "EN", "MODE", "MODE"],
        ["0x00", "FOO_CR Reset value", "1", "1", "0", "0"],
    ]
    result = extract_register_map(headers, rows)
    names = [r["name"] for r in result["registers"]]
    assert names == ["FOO_CR"]
    reg = result["registers"][0]
    assert reg["reset_value"] == "0xC"
    assert reg["fields"] == [
        {"bits": "3:2", "name": "EN", "reset": "11"},
        {"bits": "1:0", "name": "MODE", "reset": "00"},
    ]


def test_register_map_fix2_parenthetical_qualifier_reset_row_folds_into_preceding():
    # Verified RM0490 artifact (GPIO boundary tables): a register can print
    # TWO reset rows with different port qualifiers -- neither has a
    # register name of its own, so both must fall back to the immediately
    # preceding emitted register (GPIOx_MODER), never become their own
    # "Reset value (...)" pseudo-registers.
    headers = ["Offset", "Register name"] + [str(n) for n in range(3, -1, -1)]
    rows = [
        ["0x00", "GPIOx_MODER\n(x = A, B, C, D, F)", "MODER", "MODER", "MODER", "MODER"],
        ["0x00", "Reset value (port A)", "1", "0", "1", "0"],
        ["0x00", "Reset value\n(ports other than A)", "0", "1", "0", "1"],
    ]
    result = extract_register_map(headers, rows)
    names = [r["name"] for r in result["registers"]]
    assert names == ["GPIOx_MODER (x = A, B, C, D, F)"]
    assert not any(re.search(r"reset\s*value", n, re.I) for n in names)
    # last-qualifier-wins is an accepted limitation -- the schema has only
    # one reset_value slot per register, and the acceptance test only
    # requires no pseudo-register and no garbage, not per-qualifier values.
    reg = result["registers"][0]
    assert reg["reset_value"] == "0x5"
    assert reg["fields"] == [{"bits": "3:0", "name": "MODER", "reset": "0101"}]


def test_register_map_fix2_no_reset_value_ever_contains_field_label_text():
    # TIMx_CR1-style: a single row carries the register name AND "reset
    # value" in its own cell, with NO separate field row and NO clean
    # reset constant among its own bit values (field-name/reset-digit pairs
    # stacked in one cell) -- must become its own (cleanly-named) register
    # with reset_value "", never label soup.
    headers = ["Offset", "Register"] + [str(n) for n in range(3, -1, -1)]
    rows = [
        ["0x00", "TIMx_CR1 Reset value", "TS[2:0]\n000", "TS[2:0]\n000", "Reserved", "0\nCEN"],
    ]
    result = extract_register_map(headers, rows)
    assert len(result["registers"]) == 1
    reg = result["registers"][0]
    assert reg["name"] == "TIMx_CR1"
    assert reg["reset_value"] == ""
    for f in reg["fields"]:
        assert "[" not in f["bits"]
    assert "[" not in reg["reset_value"] and "\n" not in reg["reset_value"]


def test_register_map_fix2_bracketless_field_name_never_leaks_into_reset_value():
    # A field name that happens NOT to contain brackets/newlines (e.g. a
    # short mnemonic like "CNF"/"MODE") must still be rejected from the
    # reset bitmap -- the safety check is a positive allowlist (hex tokens
    # or single bit characters), not merely "no brackets found".
    from rmtables.semantic import _reset_bit_map

    bit_cols = [(0, 3, 2), (1, 1, 0)]
    assert _reset_bit_map(bit_cols, ["CNF", "MODE"]) == {}


# --------------------------------------------------------- alternate_function
# RM0008 Table 34 "CAN1 alternate function remapping".

ALT_FUNCTION_HEADERS = [
    "(1)Alternate function",
    'CAN_REMAP[1:0] = \n"00"',
    'CAN_REMAP[1:0] = \n(2)"10" ',
    'CAN_REMAP[1:0] = \n(3)"11"',
]
ALT_FUNCTION_ROWS = [
    ["CAN1_RX or CAN_RX", "PA11", "PB8", "PD0"],
    ["CAN1_TX or CAN_RX", "PA12", "PB9", "PD1"],
]


def test_classify_alternate_function_from_caption():
    signal_type, _ = classify_table("CAN1 alternate function remapping", ALT_FUNCTION_HEADERS, ALT_FUNCTION_ROWS)
    assert signal_type == "alternate_function"


def test_alternate_function_remap_configs_keyed_by_value_not_position():
    result = extract_alternate_function(ALT_FUNCTION_HEADERS, ALT_FUNCTION_ROWS)
    functions = result["functions"]
    assert functions[0]["function"] == "CAN1_RX or CAN_RX"
    # a footnote marker landing *inside* the header (between "=" and the
    # quoted value) must not corrupt the extracted config key
    assert functions[0]["configs"] == {"00": "PA11", "10": "PB8", "11": "PD0"}


def test_alternate_function_pin_shape():
    headers = ["Pin", "AF0", "AF1", "AF7"]
    rows = [["PA0", "SYS", "TIM2", "USART1"]]
    result = extract_alternate_function(headers, rows)
    assert result == {"pins": [{"pin": "PA0", "functions": {"AF0": "SYS", "AF1": "TIM2", "AF7": "USART1"}}]}


# ----------------------------------------------------------- interrupt_vector
# RM0490 Table 56 "Vector table" (first 3 entries).

INTERRUPT_HEADERS = ["Position", "Priority", "Type of \npriority", "Acronym", "Description", "Address"]
INTERRUPT_ROWS = [
    ["-", "-", "-", "-", "Reserved", "0x0000_0000"],
    ["-", "-3", "fixed", "Reset", "Reset", "0x0000_0004"],
    ["-", "-2", "fixed", "NMI_Handler", "Non maskable interrupt.", "0x0000_0008"],
]


def test_classify_interrupt_vector_from_headers():
    assert classify_table("Vector table", INTERRUPT_HEADERS, INTERRUPT_ROWS)[0] == "interrupt_vector"


def test_interrupt_vector_entries_all_have_address():
    result = extract_interrupt_vector(INTERRUPT_HEADERS, INTERRUPT_ROWS)
    entries = result["entries"]
    assert len(entries) == 3
    assert all(e["address"] for e in entries)
    assert entries[1] == {
        "position": "-", "priority": "-3", "acronym": "Reset",
        "description": "Reset", "address": "0x0000_0004",
    }


# --------------------------------------------------------------- memory_map
# RM0490 Table 7 "STM32C0 series peripheral register boundary addresses".

MEMORY_MAP_HEADERS = ["Bus", "Boundary address", "Size", "Peripheral", "Peripheral register map"]
MEMORY_MAP_ROWS = [
    ["IOPORT", "0x5000 1400 - 0x5000 17FF", "1 KB", "GPIOF", "Section 8.5.12 on page 195"],
    ["IOPORT", "0x5000 1000 - 0x5000 13FF", "1 KB", "Reserved", "-"],
]


def test_classify_memory_map_from_headers():
    assert classify_table("STM32C0 series peripheral register boundary addresses",
                           MEMORY_MAP_HEADERS, MEMORY_MAP_ROWS)[0] == "memory_map"


def test_memory_map_regions_resolved_by_column_role():
    result = extract_memory_map(MEMORY_MAP_HEADERS, MEMORY_MAP_ROWS)
    assert result["regions"][0] == {
        "bus": "IOPORT", "boundary": "0x5000 1400 - 0x5000 17FF", "size": "1 KB",
        "area": "GPIOF", "register_desc": "Section 8.5.12 on page 195",
    }


def test_memory_map_type_column_synonym_for_bus():
    # RM0490's per-device boundary tables (2-6) use "Type" instead of "Bus".
    headers = ["Type", "Boundary address", "Size", "Memory Area", "Register description"]
    rows = [["FLASH", "0x1FFF 7800 - 0x1FFF 787F", "128 B", "Option bytes", "Section 4.4"]]
    result = extract_memory_map(headers, rows)
    assert result["regions"][0]["bus"] == "FLASH"
    assert result["regions"][0]["area"] == "Option bytes"


# ---------------------------------------------------------------- parameter
# RM0490 Table 116 "SMBus timeout specifications".

PARAMETER_HEADERS = ["Symbol", "Parameter", "Limits", "Limits", "Unit"]
PARAMETER_ROWS = [
    ["Symbol", "Parameter", "Min", "Max", "Unit"],
    ["t\nTIMEOUT", "Detect clock low timeout", "25", "35", "ms"],
]


def test_classify_parameter_from_symbol_and_unit_headers():
    assert classify_table("SMBus timeout specifications", PARAMETER_HEADERS, PARAMETER_ROWS)[0] == "parameter"


def test_parameter_resolves_min_max_from_subheader_row_when_headers_are_ambiguous():
    # Real artifact: the merged-cell "Limits"/"Limits" super-header hides
    # Min/Max in the physical first *data* row (row 0 == the table's own
    # assembly always treats the true first physical row as the header).
    result = extract_parameter(PARAMETER_HEADERS, PARAMETER_ROWS)
    parameters = result["parameters"]
    real = parameters[1]
    assert real["symbol"] == "t\nTIMEOUT"
    assert real["min"] == "25"
    assert real["max"] == "35"
    assert real["unit"] == "ms"


def test_parameter_rows_all_have_unit():
    result = extract_parameter(PARAMETER_HEADERS, PARAMETER_ROWS)
    assert all(p["unit"] for p in result["parameters"])


# ------------------------------------------------------------ feature_matrix
# RM0490 Table 1 "Peripherals or functions versus products".

FEATURE_MATRIX_HEADERS = ["Peripheral or function", "STM32C011xx", "STM32C031xx", "STM32C071xx"]
FEATURE_MATRIX_ROWS = [
    ["CRS", "-", "-", "X"],
    ["USB", "-", "-", "X"],
]


def test_classify_feature_matrix_from_header_shape():
    assert classify_table("Peripherals or functions versus products",
                           FEATURE_MATRIX_HEADERS, FEATURE_MATRIX_ROWS)[0] == "feature_matrix"


def test_feature_matrix_values_keys_equal_variants():
    result = extract_feature_matrix(FEATURE_MATRIX_HEADERS, FEATURE_MATRIX_ROWS)
    variants = set(result["variants"])
    assert variants == {"STM32C011xx", "STM32C031xx", "STM32C071xx"}
    for feature in result["features"]:
        assert set(feature["values"].keys()) == variants


def test_feature_matrix_single_variant_column_is_not_a_matrix():
    # Verified real near-miss: "Feature"/"CRS" (RM0490 Table 32) has only
    # one comparison column -- not a real *matrix* -- and must stay generic.
    headers = ["Feature", "CRS"]
    rows = [["TRIM width", "7 bits"]]
    assert classify_table("CRS features", headers, rows)[0] == "generic"


# -------------------------------------------------------------------- generic

def test_generic_fallback_when_no_signal_matches():
    headers = ["SEC_PROT", "PCROP", "WRP", "PCROP_RDP", "Comment", "WRPERR", "CPU bus error"]
    signal_type, signal = classify_table("Mass erase overview", headers, [])
    assert signal_type == "generic"
    assert signal == "no signal matched"


def test_extract_semantic_downgrades_to_generic_when_extractor_returns_none():
    from rmtables.semantic import extract_semantic

    # classify_table would call this register_map-shaped, but with no
    # resolvable offset/register columns the extractor can't build anything.
    headers = ["A", "B"] + [str(n) for n in range(31, -1, -1)]
    semantic_type, semantic = extract_semantic("register_map", headers, [["x", "y"] + ["Res."] * 32])
    assert semantic_type == "generic"
    assert semantic == {}
