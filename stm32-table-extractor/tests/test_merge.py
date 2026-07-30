import logging
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from rmtables.merge import TableMerger
from rmtables.model import Caption, RawTable

HEADER = ["Offset", "Register", "31", "30"]


def test_continuation_merges_and_drops_duplicate_header():
    merger = TableMerger()

    page1_table = RawTable(page=90, bbox=(0, 100, 500, 700), rows=[
        list(HEADER),
        ["0x000", "FOO", "Res.", "1"],
    ])
    cap1 = Caption(number=26, text="FLASH register map and reset values", continued=False, top=90, page=90)

    page2_table = RawTable(page=91, bbox=(0, 100, 500, 300), rows=[
        list(HEADER),  # duplicate header repeated at top of continuation page
        ["0x004", "BAR", "Res.", "0"],
    ])
    cap2 = Caption(
        number=26,
        text="FLASH register map and reset values",
        continued=True,
        top=90,
        page=91,
    )

    merger.process_page(90, [(page1_table, cap1, None, ["1. some note"], [])])
    merger.process_page(91, [(page2_table, cap2, None, [], [])])
    tables = merger.finalize()

    assert len(tables) == 1
    t = tables[0]
    assert t.table_number == 26
    assert t.spans_pages is True
    assert t.page_start == 90
    assert t.page_end == 91
    # header should appear exactly once, not duplicated by the continuation page
    assert t.rows.count(HEADER) == 1
    assert t.rows == [
        HEADER,
        ["0x000", "FOO", "Res.", "1"],
        ["0x004", "BAR", "Res.", "0"],
    ]
    assert t.notes == ["1. some note"]


def test_different_table_numbers_are_not_merged():
    merger = TableMerger()
    t1 = RawTable(page=57, bbox=(0, 100, 500, 200), rows=[["a", "b"]])
    t2 = RawTable(page=57, bbox=(0, 300, 500, 400), rows=[["c", "d"]])
    cap1 = Caption(number=10, text="First", continued=False, top=90, page=57)
    cap2 = Caption(number=11, text="Second", continued=False, top=290, page=57)
    merger.process_page(57, [(t1, cap1, None, [], []), (t2, cap2, None, [], [])])
    tables = merger.finalize()
    assert len(tables) == 2
    assert [t.table_number for t in tables] == [10, 11]


# --------------------------------------------------------- MERGE_DUPLICATE_FIX.md
# Identity-based merge: same table_number + same/next page is sufficient --
# mismatched column counts (and no "(continued)" marker) must NOT block the
# merge anymore; they're reconciled by padding instead.

def test_mismatched_width_cross_page_now_merges_with_padding():
    merger = TableMerger()
    t1 = RawTable(page=90, bbox=(0, 100, 500, 700), rows=[
        list(HEADER), ["0x000", "FOO", "Res.", "1"],
    ])
    cap1 = Caption(number=26, text="Reg map", continued=False, top=90, page=90)
    # continuation page has a genuinely different column count (e.g. a
    # spanned header cell splitting differently) and no "(continued)"
    # marker -- must still merge, not be refused.
    t2 = RawTable(page=91, bbox=(0, 100, 500, 200), rows=[["a", "b", "c"]])
    cap2 = Caption(number=26, text="Reg map", continued=False, top=90, page=91)
    merger.process_page(90, [(t1, cap1, None, [], [])])
    merger.process_page(91, [(t2, cap2, None, [], [])])
    tables = merger.finalize()

    assert len(tables) == 1
    t = tables[0]
    assert t.page_start == 90 and t.page_end == 91
    assert t.rows == [
        HEADER,
        ["0x000", "FOO", "Res.", "1"],
        ["a", "b", "c", None],
    ]
    assert all(len(r) == 4 for r in t.rows)


def test_continuation_wider_than_first_segment_pads_earlier_rows_too():
    merger = TableMerger()
    t1 = RawTable(page=179, bbox=(0, 100, 500, 700), rows=[
        ["A", "B", "C"],
        ["1", "2", "3"],
    ])
    cap1 = Caption(number=38, text="Port bit configuration table", continued=False, top=90, page=179)
    t2 = RawTable(page=180, bbox=(0, 100, 500, 300), rows=[["x", "y", "z", "w"]])
    cap2 = Caption(number=38, text="Port bit configuration table", continued=False, top=90, page=180)
    merger.process_page(179, [(t1, cap1, None, [], [])])
    merger.process_page(180, [(t2, cap2, None, [], [])])
    tables = merger.finalize()

    assert len(tables) == 1
    t = tables[0]
    assert t.rows == [
        ["A", "B", "C", None],
        ["1", "2", "3", None],
        ["x", "y", "z", "w"],
    ]
    assert all(len(r) == 4 for r in t.rows)  # never truncated, only padded


def test_three_segment_chain_merges_into_one():
    # RM0477 Table 507 shape: 3 consecutive-page segments, different widths.
    merger = TableMerger()
    t1 = RawTable(page=2140, bbox=(0, 100, 500, 700), rows=[["a", "b", "c"]])
    cap1 = Caption(number=507, text="RTC pin PC13 configuration", continued=False, top=90, page=2140)
    t2 = RawTable(page=2141, bbox=(0, 100, 500, 700), rows=[["d", "e", "f", "g"]])
    cap2 = Caption(number=507, text="RTC pin PC13 configuration", continued=False, top=90, page=2141)
    t3 = RawTable(page=2142, bbox=(0, 100, 500, 700), rows=[["h", "i", "j"]])
    cap3 = Caption(number=507, text="RTC pin PC13 configuration", continued=False, top=90, page=2142)

    merger.process_page(2140, [(t1, cap1, None, [], [])])
    merger.process_page(2141, [(t2, cap2, None, [], [])])
    merger.process_page(2142, [(t3, cap3, None, [], [])])
    tables = merger.finalize()

    assert len(tables) == 1
    t = tables[0]
    assert t.page_start == 2140 and t.page_end == 2142
    assert all(len(r) == 4 for r in t.rows)
    assert t.rows == [
        ["a", "b", "c", None],
        ["d", "e", "f", "g"],
        ["h", "i", "j", None],
    ]


def test_repeated_header_dropped_after_whitespace_normalization():
    header = ["Offset", "Register", "31", "30"]
    merger = TableMerger()
    t1 = RawTable(page=90, bbox=(0, 100, 500, 700), rows=[list(header), ["0x000", "FOO", "Res.", "1"]])
    cap1 = Caption(number=26, text="Reg map", continued=False, top=90, page=90)
    t2 = RawTable(page=91, bbox=(0, 100, 500, 300), rows=[
        ["Offset", " Register ", "31", "30"],  # same header, irregular whitespace
        ["0x004", "BAR", "Res.", "0"],
    ])
    cap2 = Caption(number=26, text="Reg map", continued=False, top=90, page=91)
    merger.process_page(90, [(t1, cap1, None, [], [])])
    merger.process_page(91, [(t2, cap2, None, [], [])])
    tables = merger.finalize()

    assert len(tables) == 1
    assert tables[0].rows == [header, ["0x000", "FOO", "Res.", "1"], ["0x004", "BAR", "Res.", "0"]]


def test_repeated_header_dropped_via_80_percent_cell_match():
    header = ["Offset", "Register", "31", "30", "29"]
    merger = TableMerger()
    t1 = RawTable(page=90, bbox=(0, 100, 500, 700), rows=[list(header), ["0x000", "FOO", "Res.", "1", "0"]])
    cap1 = Caption(number=26, text="Reg map", continued=False, top=90, page=90)
    # 4 of 5 cells match exactly (80%) -- a rendering slip in the 5th cell
    # ("29" -> "2 9") must not prevent recognizing this as a repeated header.
    t2 = RawTable(page=91, bbox=(0, 100, 500, 300), rows=[
        ["Offset", "Register", "31", "30", "2 9"],
        ["0x004", "BAR", "Res.", "0", "1"],
    ])
    cap2 = Caption(number=26, text="Reg map", continued=False, top=90, page=91)
    merger.process_page(90, [(t1, cap1, None, [], [])])
    merger.process_page(91, [(t2, cap2, None, [], [])])
    tables = merger.finalize()

    assert len(tables) == 1
    assert tables[0].rows == [header, ["0x000", "FOO", "Res.", "1", "0"], ["0x004", "BAR", "Res.", "0", "1"]]


def test_repeated_header_with_shifted_grouped_column_is_recognized_by_membership():
    # Real case (RM0490 Table 38): the two segments' ruled grids split a
    # grouped column differently, so the continuation's reprinted header
    # has the SAME cell values but at DIFFERENT positions -- a strict
    # position-for-position comparison scores only 5/8 (62.5%, below the
    # 80% cutoff) and would wrongly keep this as a data row. Membership
    # (each header cell can satisfy one row cell, regardless of position)
    # correctly recognizes it as a repeated header.
    header = [
        "MODE(i) [1:0]", "OTYPE(i)", "OSPEED(i) [1:0]", "PUPD(i) [1:0]",
        "PUPD(i) [1:0]", "I/O configuration", "I/O configuration",
    ]
    merger = TableMerger()
    t1 = RawTable(page=179, bbox=(0, 100, 500, 700), rows=[
        list(header), ["01", "0", "SPEED", "0", "0", "GP output", "PP"],
    ])
    cap1 = Caption(number=38, text="Port bit configuration table", continued=False, top=90, page=179)
    # continuation's header: OSPEED spans 2 columns instead of PUPD, shifting
    # everything after position 2 -- same 5 distinct labels, different slots.
    shifted_header = [
        "MODE(i) [1:0]", "OTYPE(i)", "OSPEED(i) [1:0]", "OSPEED(i) [1:0]",
        "PUPD(i) [1:0]", "PUPD(i) [1:0]", "I/O configuration", "I/O configuration",
    ]
    t2 = RawTable(page=180, bbox=(0, 100, 500, 300), rows=[
        list(shifted_header), ["00", "x", "x", "x", "0", "0", "Input", "Floating"],
    ])
    cap2 = Caption(number=38, text="Port bit configuration table", continued=False, top=90, page=180)
    merger.process_page(179, [(t1, cap1, None, [], [])])
    merger.process_page(180, [(t2, cap2, None, [], [])])
    tables = merger.finalize()

    assert len(tables) == 1
    t = tables[0]
    # the shifted header row was dropped -- header + t1's data row + t2's
    # data row remain (3), not 4 (which would mean the header wasn't dropped).
    assert len(t.rows) == 3
    assert t.rows[0] == header + [None]  # first segment's header kept, padded to 8
    assert t.rows[1] == ["01", "0", "SPEED", "0", "0", "GP output", "PP", None]
    assert t.rows[2] == ["00", "x", "x", "x", "0", "0", "Input", "Floating"]


def test_row_below_80_percent_match_is_not_dropped_as_header():
    header = ["Offset", "Register", "31", "30", "29"]
    merger = TableMerger()
    t1 = RawTable(page=90, bbox=(0, 100, 500, 700), rows=[list(header), ["0x000", "FOO", "Res.", "1", "0"]])
    cap1 = Caption(number=26, text="Reg map", continued=False, top=90, page=90)
    # only 2 of 5 cells match -- a genuine data row, must be kept.
    t2 = RawTable(page=91, bbox=(0, 100, 500, 300), rows=[["Offset", "Register", "X", "Y", "Z"]])
    cap2 = Caption(number=26, text="Reg map", continued=False, top=90, page=91)
    merger.process_page(90, [(t1, cap1, None, [], [])])
    merger.process_page(91, [(t2, cap2, None, [], [])])
    tables = merger.finalize()

    assert len(tables) == 1
    assert len(tables[0].rows) == 3  # header + 2 data rows, nothing dropped


def test_no_continued_marker_required_to_merge():
    merger = TableMerger()
    t1 = RawTable(page=57, bbox=(0, 100, 500, 200), rows=[["a"]])
    t2 = RawTable(page=58, bbox=(0, 100, 500, 200), rows=[["b"]])
    cap1 = Caption(number=8, text="XL-density Flash module organization", continued=False, top=90, page=57)
    cap2 = Caption(number=8, text="XL-density Flash module organization", continued=False, top=90, page=58)
    merger.process_page(57, [(t1, cap1, None, [], [])])
    merger.process_page(58, [(t2, cap2, None, [], [])])
    tables = merger.finalize()
    assert len(tables) == 1


def test_same_page_ragged_fragments_merge_and_pad():
    merger = TableMerger()
    t1 = RawTable(page=75, bbox=(0, 100, 500, 200), rows=[["a", "b", "c"]])
    t2 = RawTable(page=75, bbox=(0, 300, 500, 400), rows=[["d", "e"]])
    cap = Caption(number=24, text="Note table", continued=False, top=90, page=75)
    merger.process_page(75, [(t1, cap, None, [], []), (t2, cap, None, [], [])])
    tables = merger.finalize()
    assert len(tables) == 1
    assert tables[0].rows == [["a", "b", "c"], ["d", "e", None]]


def test_notes_extend_across_continuation():
    merger = TableMerger()
    t1 = RawTable(page=57, bbox=(0, 100, 500, 200), rows=[["a"]])
    t2 = RawTable(page=58, bbox=(0, 100, 500, 200), rows=[["b"]])
    cap1 = Caption(number=5, text="Foo", continued=False, top=90, page=57)
    cap2 = Caption(number=5, text="Foo", continued=False, top=90, page=58)
    merger.process_page(57, [(t1, cap1, None, ["1. first"], [])])
    merger.process_page(58, [(t2, cap2, None, ["2. second"], [])])
    tables = merger.finalize()
    assert len(tables) == 1
    assert tables[0].notes == ["1. first", "2. second"]


def test_legend_extends_across_continuation():
    merger = TableMerger()
    t1 = RawTable(page=57, bbox=(0, 100, 500, 200), rows=[["a"]])
    t2 = RawTable(page=58, bbox=(0, 100, 500, 200), rows=[["b"]])
    cap1 = Caption(number=5, text="Foo", continued=False, top=90, page=57)
    cap2 = Caption(number=5, text="Foo", continued=False, top=90, page=58)
    merger.process_page(57, [(t1, cap1, None, [], ["Legend: A = Alpha"])])
    merger.process_page(58, [(t2, cap2, None, [], [])])
    tables = merger.finalize()
    assert len(tables) == 1
    assert tables[0].legend == ["Legend: A = Alpha"]


def test_attach_legend_reaches_finalized_table():
    merger = TableMerger()
    t1 = RawTable(page=1, bbox=(0, 100, 500, 200), rows=[["a"]])
    cap1 = Caption(number=1, text="Foo", continued=False, top=90, page=1)
    merger.process_page(1, [(t1, cap1, None, [], [])])

    t2 = RawTable(page=2, bbox=(0, 100, 500, 200), rows=[["b"]])
    cap2 = Caption(number=2, text="Bar", continued=False, top=90, page=2)
    merger.process_page(2, [(t2, cap2, None, [], [])])  # closes table 1

    assert merger.attach_legend(1, "Legend for Table1: marked with bullet") is True
    tables = merger.finalize()
    t1_final = next(t for t in tables if t.table_number == 1)
    assert t1_final.legend == ["Legend for Table1: marked with bullet"]


def test_attach_legend_returns_false_when_table_not_seen_yet():
    merger = TableMerger()
    assert merger.attach_legend(99, "Legend for Table 99: ...") is False


# ------------------------------------------------ MERGE_DUPLICATE_FIX.md guarantee

def test_duplicate_table_number_logs_error_but_keeps_both_objects(caplog):
    merger = TableMerger()
    t1 = RawTable(page=90, bbox=(0, 100, 500, 200), rows=[["a"]])
    cap1 = Caption(number=26, text="First seg", continued=False, top=90, page=90)
    # a genuinely different table interrupts the continuation
    t_other = RawTable(page=91, bbox=(0, 100, 500, 200), rows=[["z"]])
    cap_other = Caption(number=27, text="Interrupter", continued=False, top=90, page=91)
    t2 = RawTable(page=92, bbox=(0, 100, 500, 200), rows=[["b"]])
    cap2 = Caption(number=26, text="Second seg (too late)", continued=False, top=90, page=92)

    merger.process_page(90, [(t1, cap1, None, [], [])])
    merger.process_page(91, [(t_other, cap_other, None, [], [])])
    merger.process_page(92, [(t2, cap2, None, [], [])])

    with caplog.at_level(logging.ERROR):
        tables = merger.finalize()

    assert len(tables) == 3  # nothing dropped
    assert [t.table_number for t in tables].count(26) == 2
    assert "26" in caplog.text
    assert "genuine parse problem" in caplog.text.lower()


def test_no_duplicate_number_no_error_logged(caplog):
    merger = TableMerger()
    t1 = RawTable(page=1, bbox=(0, 100, 500, 200), rows=[["a"]])
    cap1 = Caption(number=1, text="Foo", continued=False, top=90, page=1)
    merger.process_page(1, [(t1, cap1, None, [], [])])
    with caplog.at_level(logging.ERROR):
        merger.finalize()
    assert caplog.text == ""
