import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from rmtables.headings import HeadingTracker, extract_register_name, parse_heading
from rmtables.model import RawTable


def test_parse_heading_matches_real_heading():
    assert parse_heading("4.7.1 FLASH access control register (FLASH_ACR)") == (
        "4.7.1",
        "FLASH access control register (FLASH_ACR)",
    )


def test_parse_heading_rejects_bit_number_lines():
    # "31 30 29 ... 16" would otherwise match \d+(\.\d+)* \s+ .+ ; the lack
    # of any letter in the "title" is what saves us.
    assert parse_heading("31 30 29 28 27 26 25 24 23 22 21 20 19 18 17 16") is None


def test_extract_register_name_skips_qualifier_parens():
    title = "TIMx DMA address for full transfer (TIMx_DMAR)(x = 2 to 3)"
    assert extract_register_name(title) == "TIMx_DMAR"


def test_extract_register_name_matches_any_bare_identifier_in_parens():
    # This heading ("FLASH read protection (RDP)") isn't actually a register
    # section -- extract_register_name doesn't know that on its own. Callers
    # only invoke it after independently confirming a bit-header grid
    # follows (see registers.py), which this non-register heading never has.
    assert extract_register_name("FLASH read protection (RDP)") == "RDP"


def test_extract_register_name_returns_none_without_parens():
    assert extract_register_name("FLASH registers") is None


def test_heading_tracker_same_page_lookup():
    tracker = HeadingTracker()
    lines = [
        {"text": "4.7 FLASH registers", "top": 90},
        {"text": "4.7.1 FLASH access control register (FLASH_ACR)", "top": 120},
        {"text": "Address offset: 0x000", "top": 142},
    ]
    tracker.start_page(lines)
    assert tracker.heading_before(142) == ("4.7.1", "FLASH access control register (FLASH_ACR)")
    assert tracker.heading_before(100) == ("4.7", "FLASH registers")


def test_heading_tracker_carries_over_across_pages():
    tracker = HeadingTracker()
    tracker.start_page([{"text": "4.7.1 FLASH access control register (FLASH_ACR)", "top": 90}])
    tracker.finish_page()
    tracker.start_page([{"text": "Bits 31:19 Reserved.", "top": 50}])
    assert tracker.heading_before(50) == ("4.7.1", "FLASH access control register (FLASH_ACR)")


def test_heading_tracker_merges_same_page_wrapped_paren_continuation():
    tracker = HeadingTracker()
    lines = [
        {"text": "4.7.7 FLASH PCROP area A start address register", "top": 366},
        {"text": "(FLASH_PCROP1ASR)", "top": 380},
        {"text": "Address offset: 0x024", "top": 400},
    ]
    tracker.start_page(lines)
    assert tracker.heading_before(400) == (
        "4.7.7",
        "FLASH PCROP area A start address register (FLASH_PCROP1ASR)",
    )


def test_heading_tracker_merges_cross_page_wrapped_paren_continuation():
    tracker = HeadingTracker()
    tracker.start_page([
        {"text": "6.4.18 RCC APB peripheral clock enable in Sleep/Stop mode register 1", "top": 636},
    ])
    tracker.finish_page()
    tracker.start_page([
        {"text": "(RCC_APBSMENR1)", "top": 60},
        {"text": "Address offset: 0x4C", "top": 80},
    ])
    assert tracker.heading_before(80) == (
        "6.4.18",
        "RCC APB peripheral clock enable in Sleep/Stop mode register 1 (RCC_APBSMENR1)",
    )


def test_parse_heading_rejects_bare_top_level_number():
    # RECOVERY_TASK.md fix: a bare digit is never a real ST heading in this
    # manual -- only a real "N.N" (at least one dot) counts. Without this, a
    # table cell whose merged line happens to start with a lone digit (e.g.
    # "1 x Erase aborted (no erase started) No Yes", Table 16's bottom row)
    # gets mistaken for section "1".
    assert parse_heading("1 x Erase aborted (no erase started) No Yes") is None
    assert parse_heading("4 FLASH memory") is None
    assert parse_heading("4.3 FLASH memory") == ("4.3", "FLASH memory")


def test_heading_tracker_excludes_lines_inside_a_detected_table_bbox():
    # Reproduces the verified RM0490 Table 16 regression: this table-row
    # line falls inside the table's own bounding box and would otherwise
    # satisfy HEADING_RE, mis-assigning section "1".
    raw_table = RawTable(page=62, bbox=(66.8, 105.4, 528.4, 211.4), rows=[["x"]])
    lines = [
        {"text": "1 x Erase aborted (no erase started) No Yes",
         "top": 198.3, "bottom": 207.3, "x0": 93.1, "x1": 498.5},
        {"text": "4.3.6 FLASH main memory programming sequences",
         "top": 411.9, "bottom": 424.0, "x0": 67.3, "x1": 391.3},
    ]
    tracker = HeadingTracker()
    tracker.start_page(lines, [raw_table])
    # The in-table line must not become a heading at all.
    assert tracker.heading_before(200) is None
    # The real heading below the table (outside its bbox) still resolves.
    assert tracker.heading_before(420) == (
        "4.3.6", "FLASH main memory programming sequences",
    )


def test_heading_tracker_still_finds_headings_when_no_raw_tables_given():
    # `raw_tables` is optional (backward compatible default `()`); a heading
    # candidate is only excluded when it actually falls inside a bbox.
    tracker = HeadingTracker()
    tracker.start_page([{"text": "4.7.1 FLASH access control register (FLASH_ACR)", "top": 90}])
    assert tracker.heading_before(90) == ("4.7.1", "FLASH access control register (FLASH_ACR)")


# --------------------------------------------------------- METADATA_FIXES.md

def test_parse_heading_rejects_contents_toc_line():
    # Verified RM0008 artifact: this Contents-page line fully matches
    # HEADING_RE on its own ("31.18" + a letter-bearing "title"), but it's a
    # dot-leader ToC entry, not a real heading.
    line = "31.18 DBG register map . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . .1110"
    assert parse_heading(line) is None


def test_parse_heading_rejects_trailing_dot_leader_page_with_few_dots():
    # Fewer than 3 total dots, but still a trailing dot-leader + page number.
    assert parse_heading("4.2 Some heading . .42") is None


def test_parse_heading_does_not_strip_legitimate_1_dot_dot_4_title():
    # T167: "1..4" is two dots with no space and more text follows -- not a
    # dot-leader run, and the line doesn't end in dots+digits.
    assert parse_heading("22.9.6 SDIO response 1..4 register (SDIO_RESPx)") == (
        "22.9.6", "SDIO response 1..4 register (SDIO_RESPx)",
    )


def test_heading_tracker_skips_contents_page_entirely():
    tracker = HeadingTracker()
    lines = [
        {"text": "Contents RM0008", "top": 40, "bottom": 50, "x0": 60, "x1": 200},
        {"text": "31.18 DBG register map . . . . . . . . . . . . 1110",
         "top": 90, "bottom": 100, "x0": 60, "x1": 400},
    ]
    tracker.start_page(lines)
    assert tracker.heading_before(100) is None


def test_heading_tracker_skips_list_of_tables_page_entirely():
    tracker = HeadingTracker()
    lines = [
        {"text": "RM0008 List of tables", "top": 40, "bottom": 50, "x0": 60, "x1": 200},
        {"text": "Table 235. DBG register map and reset values . . . . 1110",
         "top": 90, "bottom": 100, "x0": 60, "x1": 400},
    ]
    tracker.start_page(lines)
    assert tracker.heading_before(100) is None


def test_sanitize_title_strips_trailing_dot_leader_but_keeps_1_dot_dot_4():
    from rmtables.headings import _sanitize_title

    assert _sanitize_title("DBG register map . . . . . . . .1110") == "DBG register map"
    assert _sanitize_title("SDIO response 1..4 register (SDIO_RESPx)") == (
        "SDIO response 1..4 register (SDIO_RESPx)"
    )


# ----------------------------------------------------- TITLE_FIDELITY_FIX.md

def test_parse_heading_rejects_false_match_from_usb_spec_prose():
    # RM0486 T735: body text "USB 2.0 specification, July 16, 2007" fully
    # satisfies HEADING_RE (number "2.0", letter-bearing "title") and isn't
    # a ToC line -- neither guard existed before this fix.
    assert parse_heading("2.0 specification, July 16, 2007") is None


def test_parse_heading_accepts_real_heading_with_wide_section_number():
    # A real heading whose title starts uppercase and whose section number's
    # final component isn't "0" must still be accepted -- the new guards are
    # not supposed to reject genuine deep headings.
    assert parse_heading("42.3.1 I3C instantiation") == ("42.3.1", "I3C instantiation")


def test_parse_heading_rejects_trailing_dot_zero_section_number():
    # Even with an uppercase title, ST never numbers a section ending in
    # ".0" -- "10.0" fails this guard on its own.
    assert parse_heading("10.0 Some Section") is None


def test_parse_heading_rejects_lowercase_title():
    # A lowercase-starting "title" fails the uppercase guard on its own, even
    # with a section number that doesn't end in ".0".
    assert parse_heading("4.7.1 flash access control register") is None


def test_lowercase_caption_is_not_rejected_by_heading_guards():
    # The new guards live in parse_heading (headings), not in caption
    # detection -- a legitimately lowercase-starting ST caption (e.g.
    # "bxCAN register map and reset values") is a completely separate code
    # path (captions.CAPTION_RE) and must keep matching regardless.
    from rmtables.captions import CAPTION_RE

    assert CAPTION_RE.search("Table 5. bxCAN register map and reset values") is not None
    # Confirms scope: the same lowercase text is correctly rejected as a
    # *heading* (it isn't one), not silently accepted by the wrong code path.
    assert parse_heading("4.5 bxCAN register map and reset values") is None
