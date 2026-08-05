"""The register-description grammar and its self-validating bit coverage."""

from __future__ import annotations

from rmcontent.registers import (
    check_bit_coverage,
    declared_width,
    parse_register,
)

# RM0490 Rev 6 section 4.7.1, verbatim from pages 77-78 (page furniture,
# the bit-layout rows and the rotated-label glyph already filtered out).
FLASH_ACR = """Address offset: 0x000
Reset value: 0b0000 0000 0000 010X 0000 0110 0000 0000 (the EMPTY bit is updated only
by OBL. It is not affected by the system reset.)
Bits 31:19 Reserved, must be kept at reset value.
Bit 18 DBG_SWEN: Debug access software enable
Software may use this bit to enable/disable the debugger read access.
0: Debugger disabled
1: Debugger enabled
Bit 17 Reserved, must be kept at reset value.
Bit 16 EMPTY: Main flash memory area empty
This bit indicates whether the first location of the main flash memory area was read as
erased or as programmed during OBL. It is not affected by the system reset. Software may
need to change this bit value after a flash memory program or erase operation.
0: Main flash memory area programmed
1: Main flash memory area empty
The bit can be set and reset by software.
Bits 15:12 Reserved, must be kept at reset value.
Bit 11 ICRST: CPU Instruction cache reset
0: CPU Instruction cache is not reset
1: CPU Instruction cache is reset
This bit can be written only when the instruction cache is disabled.
Bit 10 Reserved, must be kept at reset value.
Bit 9 ICEN: CPU Instruction cache enable
0: CPU Instruction cache is disabled
1: CPU Instruction cache is enabled
Bit 8 PRFTEN: CPU Prefetch enable
0: CPU Prefetch disabled
1: CPU Prefetch enabled
Bits 7:3 Reserved, must be kept at reset value.
Bits 2:0 LATENCY[2:0]: Flash memory access latency
The value in this bitfield represents the number of CPU wait states when accessing the flash
memory.
000: Zero wait states
001: One wait state
Other: Reserved
A new write into the bitfield becomes effective when it returns the same value upon read."""

FLASH_ACR_TITLE = "FLASH access control register (FLASH_ACR)"


def field(semantic, name):
    return next(f for f in semantic["fields"] if f["name"] == name)


def test_flash_acr_header_values():
    s = parse_register(FLASH_ACR_TITLE, FLASH_ACR)
    assert s["register"] == "FLASH_ACR"
    assert s["address_offset"] == "0x000"
    assert s["reset_value"] == "0b0000 0000 0000 010X 0000 0110 0000 0000"


def test_flash_acr_reset_note_survives_the_line_wrap():
    """The parenthetical wraps onto a second printed line; it carries
    real information about when the reset value applies."""
    s = parse_register(FLASH_ACR_TITLE, FLASH_ACR)
    assert s["reset_note"] == (
        "the EMPTY bit is updated only by OBL. "
        "It is not affected by the system reset."
    )


def test_flash_acr_dbg_swen_at_bit_18_with_both_meanings():
    s = parse_register(FLASH_ACR_TITLE, FLASH_ACR)
    f = field(s, "DBG_SWEN")
    assert f["bits"] == "18"
    assert f["description"].startswith("Debug access software enable")
    assert f["values"] == [
        {"value": "0", "meaning": "Debugger disabled"},
        {"value": "1", "meaning": "Debugger enabled"},
    ]


def test_flash_acr_latency_at_bits_2_0():
    s = parse_register(FLASH_ACR_TITLE, FLASH_ACR)
    f = field(s, "LATENCY[2:0]")
    assert f["bits"] == "2:0"
    assert [v["value"] for v in f["values"]] == ["000", "001", "Other"]


def test_flash_acr_reserved_runs_are_fields():
    s = parse_register(FLASH_ACR_TITLE, FLASH_ACR)
    reserved = [f["bits"] for f in s["fields"] if f["name"] == "Res."]
    assert reserved == ["31:19", "17", "15:12", "10", "7:3"]


def test_flash_acr_fields_cover_31_to_0():
    s = parse_register(FLASH_ACR_TITLE, FLASH_ACR)
    assert check_bit_coverage(s) == ""


def test_a_trailing_caveat_after_the_enumeration_is_kept():
    s = parse_register(FLASH_ACR_TITLE, FLASH_ACR)
    assert field(s, "ICRST")["description"].endswith(
        "This bit can be written only when the instruction cache is disabled."
    )


def test_non_register_section_is_not_classified():
    """Conservative by design: a wrong type is worse than generic."""
    assert parse_register("DMA main features", "The DMA offers 5 channels.") is None


def test_address_offset_without_fields_is_not_a_register():
    assert parse_register("X (Y)", "Address offset: 0x000\nSome prose only.") is None


def test_fields_without_address_offset_are_not_a_register():
    assert parse_register("X (Y)", "Bit 3 CEN: Counter enable") is None


def test_register_name_is_empty_when_the_title_has_no_parenthetical():
    s = parse_register("Some register", "Address offset: 0x0\nBit 0 CEN: Counter enable")
    assert s["register"] == ""


def test_name_containing_a_colon_is_parsed_whole():
    """"Bits 31:0 KEY[31:0]: FLASH key" -- the name has its own colon."""
    s = parse_register("FLASH key register (FLASH_KEYR)",
                       "Address offset: 0x008\nBits 31:0 KEY[31:0]: FLASH key")
    assert s["fields"][0]["name"] == "KEY[31:0]"
    assert s["fields"][0]["description"] == "FLASH key"
    assert check_bit_coverage(s) == ""


def test_discontiguous_bit_list():
    """RM0490 18.4.8: a bitfield widened into space elsewhere in the word."""
    s = parse_register("TIMx capture/compare mode register 1 (TIMx_CCMR1)", """Address offset: 0x18
Reset value: 0x00000000
Bits 31:25 Reserved, must be kept at reset value.
Bits 24, 14:12 OC2M[3:0]: Output compare 2 mode
Bits 23:15 Reserved, must be kept at reset value.
Bits 11:0 Reserved, must be kept at reset value.""")
    assert s["fields"][1]["bits"] == "24, 14:12"
    assert s["fields"][1]["name"] == "OC2M[3:0]"
    assert check_bit_coverage(s) == ""


def test_field_with_the_description_on_the_next_line():
    """RM0490 29.6.7 prints "Bit 23 NAK:" with nothing after the colon."""
    s = parse_register("USB endpoint register (USB_CHEPnR)", """Address offset: 0x0
Bit 23 NAK:
This bit is set by hardware.
Bits 22:0 Reserved, must be kept at reset value.
Bits 31:24 Reserved, must be kept at reset value.""")
    assert s["fields"][0]["name"] == "NAK"
    assert s["fields"][0]["description"] == "This bit is set by hardware."


def test_templated_field_name():
    """RM0490 14.5.11's field name carries a per-instance index template."""
    s = parse_register("EXTI external interrupt selection register (EXTI_EXTICRx)", """Address offset: 0x060 + 0x4 * (x - 1), (x = 1 to 4)
Reset value: 0x0000 0000
Bits 31:24 EXTI{4*(x-1)+3}[7:0]: GPIO port selection
Bits 23:0 Reserved, must be kept at reset value.""")
    assert s["fields"][0]["name"] == "EXTI{4*(x-1)+3}[7:0]"


def test_prose_that_merely_starts_with_bit_is_not_a_field():
    """RM0490 18.4.12's opening line satisfies the "Bit N ..." shape but
    is a sentence; it once produced a phantom field named "of" at bit 31,
    which the coverage check caught as an overlap."""
    s = parse_register("TIMx counter (TIMx_CNT)", """Bit 31 of this register has two possible definitions depending on the value of UIFREMAP in
TIMx_CR1 register:
Address offset: 0x24
Reset value: 0x00000000
Bits 31:16 CNT[31:16]: Most significant part counter value
Bits 15:0 CNT[15:0]: Least significant part of counter value""")
    assert [f["name"] for f in s["fields"]] == ["CNT[31:16]", "CNT[15:0]"]
    assert check_bit_coverage(s) == ""


def test_markers_do_not_pollute_a_field_description():
    s = parse_register("X (Y)", """Address offset: 0x0
Bit 0 CEN: Counter enable
[Table 26. FLASH register map and reset values]
Bits 31:1 Reserved, must be kept at reset value.""")
    assert "Table 26" not in s["fields"][0]["description"]


def test_a_section_documenting_several_registers_stays_generic():
    """RM0522 48.11.4 "Ethernet MAC and MMC registers" holds about fifty
    registers under one heading, each with an unnumbered sub-heading. The
    single-register shape cannot represent that, so it is left generic
    rather than emitted as one merged block with overlapping bits."""
    assert parse_register("Ethernet MAC and MMC registers", """Operating mode configuration register (ETH_MACCR)
Address offset: 0x0000
Reset value: 0x00008000
Bits 31:0 ARPEN[31:0]: ARP Offload Enable
Extended operating mode register (ETH_MACECR)
Address offset: 0x0004
Reset value: 0x00000000
Bits 31:0 EIPGCNT[31:0]: Extended Inter-Packet Gap""") is None


def test_one_address_offset_still_classifies():
    s = parse_register("X (Y)", "Address offset: 0x0\nBits 31:0 A[31:0]: desc")
    assert s is not None and s["address_offset"] == "0x0"


def test_coverage_reports_gaps_and_overlaps():
    s = {"fields": [{"bits": "31:16"}, {"bits": "15:8"}]}
    assert check_bit_coverage(s) == "missing bits 7:0"
    s = {"fields": [{"bits": "31:0"}, {"bits": "3:0"}]}
    assert check_bit_coverage(s) == "overlapping bits 3:0"


def test_declared_width_from_the_reset_value():
    assert declared_width({"reset_value": "0x0000"}) == 16
    assert declared_width({"reset_value": "0x0000 0000"}) == 32
    assert declared_width({"reset_value": "0x00000000"}) == 32
    assert declared_width({"reset_value": "0b" + "0" * 32}) == 32
    # ST appends prose to a factory-programmed value.
    assert declared_width({"reset_value": "0xXXXX where X is factory-programmed"}) == 16
    # No reset value at all: assume a full word rather than guess narrow.
    assert declared_width({"reset_value": ""}) == 32


def test_a_16_bit_register_covers_its_own_width():
    """Every STM32 timer control register is 16 bits; the fields cover
    15..0 and nothing is missing at that width."""
    s = parse_register("TIM1 control register 1 (TIM1_CR1)", """Address offset: 0x00
Reset value: 0x0000
Bits 15:1 Reserved, must be kept at reset value.
Bit 0 CEN: Counter enable""")
    assert check_bit_coverage(s) == "missing bits 31:16"
    assert check_bit_coverage(s, declared_width(s)) == ""


def test_bit_range_alone_on_its_line_with_the_description_wrapped():
    """RM0486 73.14.47 prints a bare "Bits 14:11" with "Reserved, must be
    kept at reset value." on the next line, leaving four bits uncovered
    until the range was accepted without a same-line remainder."""
    s = parse_register("OTG device IN endpoint x control register (OTG_DIEPCTLx)", """Address offset: 0x900
Reset value: 0x00000000
Bits 31:15 Reserved, must be kept at reset value.
Bits 14:11
Reserved, must be kept at reset value.
Bits 10:0 MPSIZ[10:0]: Maximum packet size""")
    assert [f["bits"] for f in s["fields"]] == ["31:15", "14:11", "10:0"]
    assert s["fields"][1]["name"] == "Res."
    assert check_bit_coverage(s) == ""


# -- coverage against the register's own width (SECTION_REGISTER_COVERAGE_FIX) --

# RM0486 53.6.1 TIMx_CR1, verbatim. The PDF prints ONE bit strip for it,
# "15 14 13 ... 0" with no "31 ... 16" row above, and the page states the
# registers "can be accessed by half-words (16-bit) or words (32-bit)".
# Bits 16-20 do not exist.
TIMX_CR1 = """Address offset: 0x000
Reset value: 0x0000
Bits 15:13 Reserved, must be kept at reset value.
Bit 12 DITHEN: Dithering enable
0: Dithering disabled
1: Dithering enabled
Bit 11 UIFREMAP: UIF status bit remapping
Bit 10 Reserved, must be kept at reset value.
Bits 9:8 CKD[1:0]: Clock division
Bit 7 ARPE: Auto-reload preload enable
Bits 6:5 CMS[1:0]: Center-aligned mode selection
Bit 4 DIR: Direction
Bit 3 OPM: One-pulse mode
Bit 2 URS: Update request source
Bit 1 UDIS: Update disable
Bit 0 CEN: Counter enable"""


def test_a_16_bit_register_passes_at_its_own_width_without_inventing_bits():
    s = parse_register("TIMx control register 1 (TIMx_CR1)(x = 1, 8)", TIMX_CR1)
    assert declared_width(s) == 16
    assert check_bit_coverage(s, declared_width(s)) == ""
    # The full-word check still reports the shortfall, as context.
    assert check_bit_coverage(s) == "missing bits 31:16"
    # Nothing above bit 15 is invented.
    assert max(int(f["bits"].split(":")[0]) for f in s["fields"]) == 15
    assert len(s["fields"]) == 12


def test_the_two_ccmr_modes_are_separate_records_each_covering_the_word():
    """RM0486 splits TIMx_CCMR1 into 53.6.7 (input capture) and 53.6.8
    (output compare), each with its own Address offset -- so no section
    describes the same bits twice and neither description is dropped."""
    output_compare = """Output compare mode:
Address offset: 0x018
Reset value: 0x00000000
Bits 31:25 Reserved, must be kept at reset value.
Bits 24, 14:12 OC2M[3:0]: Output compare 2 mode
Bits 23:17 Reserved, must be kept at reset value.
Bit 16 Reserved, must be kept at reset value.
Bit 15 OC2CE: Output compare 2 clear enable
Bits 11:0 Reserved, must be kept at reset value."""
    input_capture = """Input capture mode:
Address offset: 0x018
Reset value: 0x00000000
Bits 31:16 Reserved, must be kept at reset value.
Bits 15:12 IC2F[3:0]: Input capture 2 filter
Bits 11:0 Reserved, must be kept at reset value."""
    a = parse_register("TIMx capture/compare mode register 1 [alternate] (TIMx_CCMR1)",
                       output_compare)
    b = parse_register("TIMx capture/compare mode register 1 (TIMx_CCMR1)", input_capture)
    assert check_bit_coverage(a) == ""
    assert check_bit_coverage(b) == ""
    assert any(f["name"] == "OC2M[3:0]" for f in a["fields"])
    assert any(f["name"] == "IC2F[3:0]" for f in b["fields"])


def test_a_nested_sub_field_breakdown_is_not_parsed_as_a_field():
    """RM0486 73.14.17 decomposes the VALUE of NPTXQTOP[6:0] with lines
    like "Bits 30:27: Channel/endpoint number". Those are not register
    fields -- parsing them would overlap bits 30:24 and break a register
    that covers 31..0 correctly today."""
    s = parse_register("OTG non-periodic transmit FIFO/queue status register (OTG_HNPTXSTS)",
                       """Address offset: 0x02C
Reset value: 0x0008 0400
Bit 31 Reserved, must be kept at reset value.
Bits 30:24 NPTXQTOP[6:0]: Top of the non-periodic transmit request queue
Bits 30:27: Channel/endpoint number
Bits 26:25:
Bit 24: Terminate (last entry for selected channel/endpoint)
Bits 23:16 NPTQXSAV[7:0]: Non-periodic transmit request queue space available
Bits 15:0 NPTXFSAV[15:0]: Non-periodic Tx FIFO space available""")
    assert [f["bits"] for f in s["fields"]] == ["31", "30:24", "23:16", "15:0"]
    assert check_bit_coverage(s) == ""


def test_prose_about_bits_is_not_parsed_as_a_field():
    """"Bits 18:17 are the mirror of ATOSEL4[1:0]..." is a sentence."""
    s = parse_register("TAMP configuration register (TAMP_ATCR2)", """Address offset: 0x04
Reset value: 0x0000 0000
Bits 31:0 ATOSEL[31:0]: Output selection
Bits 18:17 are the mirror of ATOSEL4[1:0] in the TAMP_ATCR1, and so can also be read or
written through that register.""")
    assert [f["bits"] for f in s["fields"]] == ["31:0"]
    assert check_bit_coverage(s) == ""


def test_a_full_width_register_is_unaffected():
    """A register that passes today must be byte-identical."""
    s = parse_register(FLASH_ACR_TITLE, FLASH_ACR)
    assert declared_width(s) == 32
    assert check_bit_coverage(s) == ""
    assert check_bit_coverage(s, declared_width(s)) == ""
