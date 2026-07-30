import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from rmtables.captions import (
    CAPTION_RE,
    CONTINUED_RE,
    FIGURE_CAPTION_RE,
    LIST_ENTRY_RE,
    _parse_number,
    assign_caption,
    figure_caption_tops,
    find_captions,
    find_embedded_figure_row,
)
from rmtables.model import Caption


def test_caption_regex_matches_basic():
    m = CAPTION_RE.match("Table 26. FLASH register map and reset values")
    assert m.group(1) == "26"
    assert m.group(2) == "FLASH register map and reset values"


def test_caption_regex_rejects_non_caption_lines():
    assert CAPTION_RE.match("This table lists the registers") is None


def test_caption_regex_tolerates_stray_leading_punctuation():
    m = CAPTION_RE.search(". Table 179. DBG register map and reset values")
    assert m.start() <= 12
    assert m.group(1) == "179"
    assert m.group(2) == "DBG register map and reset values"

    m = CAPTION_RE.search(": Table 142. DLC coding in FDCAN")
    assert m.start() <= 12
    assert m.group(1) == "142"
    assert m.group(2) == "DLC coding in FDCAN"


def test_caption_regex_tolerates_split_table_word():
    m = CAPTION_RE.search(
        "Tabl e 45. Programmable data width and endian behavior (when PINC=MINC=1)"
    )
    assert m.group(1) == "45"
    assert m.group(2) == "Programmable data width and endian behavior (when PINC=MINC=1)"


def test_caption_regex_tolerates_heavily_split_table_word():
    # "T able 688." and "Ta b le 346." -- multiple simultaneous intra-word splits.
    m = CAPTION_RE.search("T able 688. Coding for locked EXTI lines")
    assert m.group(1) == "688"
    assert m.group(2) == "Coding for locked EXTI lines"

    m = CAPTION_RE.search("Ta b le 346. Key endianness support")
    assert m.group(1) == "346"
    assert m.group(2) == "Key endianness support"


def test_caption_regex_tolerates_leading_fragment():
    m = CAPTION_RE.search("7 Table 29. FLASH recommended read/write cycles")
    assert m.start() <= 12
    assert m.group(1) == "29"
    assert m.group(2) == "FLASH recommended read/write cycles"

    m = CAPTION_RE.search("t Table 202. 8-bit NAND flash pin table")
    assert m.start() <= 12
    assert m.group(1) == "202"
    assert m.group(2) == "8-bit NAND flash pin table"


def test_caption_regex_tolerates_split_table_number():
    # "Table 7 6 . Programmable data width..." -- a split digit within the
    # number itself, not just the word "Table" (RM0008's table 76).
    m = CAPTION_RE.search(
        "Table 7 6 . Programmable data width and endian behavior (when bits PINC = MINC = 1)"
    )
    assert _parse_number(m.group(1)) == 76
    assert m.group(2) == "Programmable data width and endian behavior (when bits PINC = MINC = 1)"


def test_caption_regex_tolerates_missing_space_before_number():
    m = CAPTION_RE.search("Table332. Some register description")
    assert m.group(1) == "332"
    assert m.group(2) == "Some register description"


def test_caption_regex_rejects_bare_cross_reference_via_find_captions():
    # "Refer to Table 332." has no title after the period -- must not match
    # as a caption even though "Table 332." itself is well-formed.
    lines = [{"text": "Refer to Table 332.", "top": 100}]
    assert find_captions(lines, 1) == []


def test_continued_marker_stripped():
    text = "FLASH register map and reset values (continued)"
    assert CONTINUED_RE.search(text)
    stripped = CONTINUED_RE.sub("", text).strip()
    assert stripped == "FLASH register map and reset values"


def test_continued_marker_absent():
    text = "FLASH register map and reset values"
    assert not CONTINUED_RE.search(text)


def test_list_entry_regex_parses_dot_leader_line():
    line = "Table 1. Peripherals or functions versus products . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . 42"
    m = LIST_ENTRY_RE.match(line)
    assert m is not None
    assert int(m.group(1)) == 1
    assert m.group(2) == "Peripherals or functions versus products"
    assert int(m.group(3)) == 42


def test_assign_caption_picks_nearest_above():
    captions = [
        Caption(number=10, text="First", continued=False, top=100, page=1),
        Caption(number=11, text="Second", continued=False, top=300, page=1),
    ]
    # table sits just below the second caption
    assert assign_caption((0, 320, 100, 400), captions).number == 11
    # table sits between the two captions
    assert assign_caption((0, 200, 100, 250), captions).number == 10


def test_assign_caption_falls_back_to_nearby_caption_just_below():
    captions = [Caption(number=5, text="Only", continued=False, top=55, page=1)]
    # caption sits a few points below the table's top -- within tolerance
    assert assign_caption((0, 40, 100, 90), captions).number == 5


def test_find_captions_rejects_bare_cross_reference():
    # A sentence wrapping mid-line can leave "Table 24." alone on its own
    # line (from "...see\nTable 24.\n") with no caption text following --
    # this must not be treated as a real caption.
    lines = [
        {"text": "PCROP subpages not overlapping with the securable memory. See", "top": 175},
        {"text": "Table 24.", "top": 187},
        {"text": "Table 24. Securable memory erase at RDP level 1 to level 0 change", "top": 211},
    ]
    caps = find_captions(lines, 75)
    assert len(caps) == 1
    assert caps[0].text == "Securable memory erase at RDP level 1 to level 0 change"
    assert caps[0].top == 211


def test_assign_caption_ignores_distant_caption_below():
    # A caption far below an uncaptioned grid (e.g. a Figure's stray ruled
    # box) must not be attached to it -- it almost certainly belongs to
    # unrelated content further down the page.
    captions = [Caption(number=5, text="Far away", continued=False, top=500, page=1)]
    assert assign_caption((0, 10, 100, 50), captions) is None


# --------------------------------------------------------- FIGURE_BLEED_FIX.md

def test_figure_caption_regex_matches_basic():
    m = FIGURE_CAPTION_RE.match("Figure 192. 128-bit block construction")
    assert m.group(1) == "192"
    assert m.group(2) == "128-bit block construction"


def test_figure_caption_regex_tolerates_split_figure_word():
    # Mirrors CAPTION_RE's tolerance for a split "Table" (RM0477) -- a split
    # "F igure" must match just as readily.
    m = FIGURE_CAPTION_RE.match("F igure 21. DMA block diagram")
    assert m.group(1) == "21"
    assert m.group(2) == "DMA block diagram"

    m = FIGURE_CAPTION_RE.match("Figu re 145. Timing diagram")
    assert m.group(1) == "145"


def test_figure_caption_regex_tolerates_spaced_number():
    m = FIGURE_CAPTION_RE.match("Figure 1 45. Timing diagram for conversion")
    assert _parse_number(m.group(1)) == 145


def test_figure_caption_regex_accepts_dot_leader_and_literal_period():
    assert FIGURE_CAPTION_RE.match("Figure 21. DMA block diagram") is not None
    assert FIGURE_CAPTION_RE.match("Figure 21․ DMA block diagram") is not None  # ․


def test_find_embedded_figure_row_cuts_at_embedded_caption():
    # RM0522 T210 shape: real 3-column data rows, then the figure caption
    # alone in column 0 with the rest of that row empty.
    rows = [
        ["DATATYPE[1:0]", "Swapping performed", "..."],
        ["0x0", "No swapping", "..."],
        ["Figure 192. 128-bit block construction of a 4x32-bit AES block", "", ""],
        ["Word 3 D127D96", "D95", "..."],
    ]
    result = find_embedded_figure_row(rows)
    assert result == (2, "192")


def test_find_embedded_figure_row_requires_match_at_first_non_empty_cell():
    # A prose cross-reference to "Figure 21." sitting inside a real data
    # cell mid-table must NOT be mistaken for an embedded figure -- the
    # match must START the row's first non-empty cell, not merely appear
    # somewhere in it.
    rows = [
        ["A", "B"],
        ["see Figure 21. for details", "some value"],
    ]
    assert find_embedded_figure_row(rows) is None


def test_find_embedded_figure_row_ignores_leading_empty_cells():
    # The figure caption is still the row's first NON-EMPTY cell even if
    # earlier columns in that row happen to be blank.
    rows = [["", "Figure 5. Some diagram", ""]]
    assert find_embedded_figure_row(rows) == (0, "5")


def test_find_embedded_figure_row_returns_none_when_no_figure_row():
    rows = [["A", "B"], ["1", "2"]]
    assert find_embedded_figure_row(rows) is None


def test_find_embedded_figure_row_matches_multiline_cell_text():
    # Verified: RM0477 Table 31/782 -- a ruled TABLE CELL (unlike a single
    # extract_text_lines() line) can hold an entire embedded figure
    # rendered as one multi-line blob with the caption on its first
    # physical line. An anchored trailing `$` (requiring `.*` to reach the
    # literal end of the whole multi-line string) would fail here since
    # `.` never matches `\n` -- this must still match against just the
    # first line's content.
    rows = [
        [
            "Figure 17. FLASH stateful initialization\nPower-on reset\n"
            "nvopennvclose\nno unique boot entry, unique boot entry\nMSv55714V1.",
            "Figure 17. FLASH stateful initialization\nPower-on reset\n"
            "nvopennvclose\nno unique boot entry, unique boot entry\nMSv55714V1.",
            "",
        ],
    ]
    assert find_embedded_figure_row(rows) == (0, "17")


# ------------------------------------------------------------- FIGURE_REMNANT_FIX.md

def test_find_embedded_figure_row_matches_caption_after_a_prose_prefix_line():
    # RM0486 T187: the row's only populated cell holds a paragraph fragment,
    # a newline, then the caption -- `.match` at the cell's own start sees
    # "that Attribute..." and used to fail. Splitting on "\n" and testing
    # each line catches it.
    rows = [
        [
            "that Attribute memory space access timings are similar.\n"
            "Figure 179. NAND flash controller waveforms for common memory access",
            "",
            "",
        ],
    ]
    assert find_embedded_figure_row(rows) == (0, "179")


def test_find_embedded_figure_row_still_cuts_when_caption_starts_the_cell():
    # No regression: a cell that starts directly with the caption (no prose
    # prefix line) must still be cut, exactly as before this fix.
    rows = [["Figure 21. DMA block diagram", "", ""]]
    assert find_embedded_figure_row(rows) == (0, "21")


def test_find_embedded_figure_row_does_not_cut_when_reference_is_in_a_populated_data_cell():
    # A prose cross-reference to "Figure 21." sitting in a data cell
    # elsewhere in the row, while other cells (including the first) are
    # genuinely populated with real data, must NOT be mistaken for an
    # embedded figure row -- only the row's first non-empty cell is ever
    # examined, and here that cell is real data, not the reference.
    rows = [
        ["Real header", "Another header"],
        ["0x1", "see Figure 21. for waveform details"],
    ]
    assert find_embedded_figure_row(rows) is None


def test_find_embedded_figure_row_ignores_multiline_cell_with_no_figure_caption():
    rows = [["Just some\nmulti-line\nprose, no caption here", "", ""]]
    assert find_embedded_figure_row(rows) is None


def test_find_embedded_figure_row_tolerant_matching_still_works_on_split_word_and_number():
    # The tolerant FIGURE_WORD_RE/NUMBER_RE matching (split "F igure", a
    # spaced-out number) must still work when the caption is found via the
    # new per-line split, not just at the cell's literal start.
    rows = [
        [
            "some lead-in prose.\n"
            "F igure 1 7. FLASH stateful initialization",
            "",
        ],
    ]
    assert find_embedded_figure_row(rows) == (0, "17")


# ---------------------------------------------------- FIGURE_CAPTION_BOUNDARY_FIX.md

def test_figure_caption_tops_returns_top_of_every_figure_caption_line():
    lines = [
        {"text": "Table 24. Some real table", "top": 211.3},
        {"text": "Figure 4. Example of disabling core debug access", "top": 428.3},
        {"text": "just some prose, not a caption", "top": 440.0},
        {"text": "Figure 5. Another figure", "top": 900.0},
    ]
    assert figure_caption_tops(lines) == [428.3, 900.0]


def test_figure_caption_tops_empty_when_no_figure_captions():
    lines = [{"text": "Table 1. Nothing figure-related here", "top": 100.0}]
    assert figure_caption_tops(lines) == []
