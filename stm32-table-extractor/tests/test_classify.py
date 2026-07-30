import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from rmtables.classify import classify_page
from rmtables.headings import HeadingTracker
from rmtables.merge import TableMerger
from rmtables.model import RawTable
from rmtables.registers import RegisterMerger


def _line(text, top, height=10.0):
    return {"text": text, "top": top, "bottom": top + height}


def test_captioned_table_goes_to_table_merger():
    lines = [_line("Table 10. Flash memory organization", 90)]
    raw_table = RawTable(page=57, bbox=(123, 100, 500, 200), rows=[["a", "b"], ["c", "d"]])
    table_merger = TableMerger()
    register_merger = RegisterMerger()
    tracker = HeadingTracker()
    tracker.start_page(lines)

    dropped, explicit_legends, _ = classify_page(
        57, [raw_table], lines, [_caption(10, "Flash memory organization", 90)],
        tracker, table_merger, register_merger,
    )

    assert dropped == 0
    assert explicit_legends == []
    tables = table_merger.finalize()
    assert len(tables) == 1
    assert tables[0].table_number == 10


def test_register_grid_takes_priority_and_is_not_dropped():
    lines = [
        _line("4.7.1 FLASH access control register (FLASH_ACR)", 90),
        _line("31 30 29 28 27 26 25 24 23 22 21 20 19 18 17 16", 150),
    ]
    hi_half = RawTable(page=77, bbox=(67.29, 160, 527.97, 190), rows=[["Res."] * 16, [""] * 16])
    table_merger = TableMerger()
    register_merger = RegisterMerger()
    tracker = HeadingTracker()
    tracker.start_page(lines)

    dropped, explicit_legends, _ = classify_page(
        77, [hi_half], lines, [], tracker, table_merger, register_merger,
    )

    assert dropped == 0
    assert explicit_legends == []
    assert table_merger.finalize() == []
    assert register_merger.current is not None  # consumed as the open hi-half


def test_narrow_figure_box_is_dropped_as_figure_fragment():
    lines = [_line("Figure 10. Some clock diagram", 90)]
    narrow_box = RawTable(page=123, bbox=(200, 100, 220, 150), rows=[["OSC_IN"], ["OSC_OUT"]])
    table_merger = TableMerger()
    register_merger = RegisterMerger()
    tracker = HeadingTracker()
    tracker.start_page(lines)

    dropped, explicit_legends, _ = classify_page(
        123, [narrow_box], lines, [], tracker, table_merger, register_merger,
    )

    assert dropped == 1
    assert explicit_legends == []
    assert table_merger.finalize() == []


def test_bare_legend_attaches_to_nearest_table_above_on_same_page():
    lines = [
        _line("Table 10. Flash memory organization", 90),
        _line("Legend: A = Alpha, B = Beta", 250),
    ]
    raw_table = RawTable(page=57, bbox=(123, 100, 500, 200), rows=[["a", "b"], ["c", "d"]])
    table_merger = TableMerger()
    register_merger = RegisterMerger()
    tracker = HeadingTracker()
    tracker.start_page(lines)

    classify_page(
        57, [raw_table], lines, [_caption(10, "Flash memory organization", 90)],
        tracker, table_merger, register_merger,
    )

    tables = table_merger.finalize()
    assert tables[0].legend == ["A = Alpha, B = Beta"]


def test_explicit_legend_for_table_n_is_returned_not_attached_directly():
    lines = [_line("Legend for Table 5: A = Alpha", 90)]
    table_merger = TableMerger()
    register_merger = RegisterMerger()
    tracker = HeadingTracker()
    tracker.start_page(lines)

    dropped, explicit_legends, _ = classify_page(
        57, [], lines, [], tracker, table_merger, register_merger,
    )
    assert explicit_legends == [(5, "A = Alpha")]


def _caption(number, text, top):
    from rmtables.model import Caption

    return Caption(number=number, text=text, continued=False, top=top, page=1)


# --------------------------------------------------------- FIGURE_BLEED_FIX.md

def test_embedded_figure_caption_truncates_table_and_logs(caplog):
    import logging

    lines = [_line("Table 43. DMA implementation", 90)]
    raw_table = RawTable(
        page=224,
        bbox=(66, 100, 528, 400),
        rows=[
            ["Number of channels", "STM32C011xx", "STM32C051xx", "STM32C091xx"],
            ["DMA1", "3", "5", "7"],
            ["Figure 21. DMA block diagram", "", "", ""],
            ["DMA\nCh 1\nCh 2...", "", "", ""],
        ],
    )
    table_merger = TableMerger()
    register_merger = RegisterMerger()
    tracker = HeadingTracker()
    tracker.start_page(lines)

    with caplog.at_level(logging.INFO, logger="rmtables.classify"):
        classify_page(
            224, [raw_table], lines, [_caption(43, "DMA implementation", 90)],
            tracker, table_merger, register_merger,
        )

    tables = table_merger.finalize()
    assert len(tables) == 1
    assert tables[0].rows == [
        ["Number of channels", "STM32C011xx", "STM32C051xx", "STM32C091xx"],
        ["DMA1", "3", "5", "7"],
    ]
    assert "cut table 43 at embedded 'Figure 21'" in caplog.text
    assert "dropped 2 rows" in caplog.text


def test_figure_bleed_across_two_separate_raw_tables_on_same_page():
    # Mirrors the real RM0490 Table 43 mechanism: the real table and the
    # figure below it are TWO SEPARATE detected ruled regions, both
    # assigned the same caption (nothing else sits between them), and
    # normally fused by the continuation merge. The figure segment must
    # collapse to nothing rather than surviving as a phantom second table.
    lines = [_line("Table 43. DMA implementation", 90)]
    real_table = RawTable(
        page=224, bbox=(66, 100, 528, 200),
        rows=[["Number of channels", "A", "B", "C"], ["DMA1", "3", "5", "7"]],
    )
    figure_table = RawTable(
        page=224, bbox=(66, 300, 528, 600),
        rows=[["Figure 21. DMA block diagram"], ["big figure blob"]],
    )
    table_merger = TableMerger()
    register_merger = RegisterMerger()
    tracker = HeadingTracker()
    tracker.start_page(lines)

    dropped, _, _ = classify_page(
        224, [real_table, figure_table], lines,
        [_caption(43, "DMA implementation", 90)],
        tracker, table_merger, register_merger,
    )

    assert dropped == 0
    tables = table_merger.finalize()
    assert len(tables) == 1  # no phantom second table for the figure
    assert tables[0].rows == [["Number of channels", "A", "B", "C"], ["DMA1", "3", "5", "7"]]


def test_cross_reference_to_figure_inside_mid_table_cell_does_not_cut():
    # "see Figure 21." inside a genuine populated data cell (not alone in
    # column 0) must never be mistaken for an embedded figure boundary.
    lines = [_line("Table 7. Some feature", 90)]
    raw_table = RawTable(
        page=10, bbox=(66, 100, 528, 300),
        rows=[
            ["Col A", "Col B"],
            ["see Figure 21. for details", "some value"],
            ["more data", "another value"],
        ],
    )
    table_merger = TableMerger()
    register_merger = RegisterMerger()
    tracker = HeadingTracker()
    tracker.start_page(lines)

    classify_page(
        10, [raw_table], lines, [_caption(7, "Some feature", 90)],
        tracker, table_merger, register_merger,
    )

    tables = table_merger.finalize()
    assert len(tables) == 1
    assert len(tables[0].rows) == 3  # nothing truncated


def test_trailing_empty_columns_trimmed_after_cut_29_to_3():
    # RM0522 Table 210 shape: a 3-column real table padded to 29 columns
    # solely by the wide figure grid below it.
    header = ["DATATYPE[1:0]", "Swapping performed", "Description"] + [""] * 26
    data_row = ["0x0", "No swapping", "..."] + [""] * 26
    figure_row = ["Figure 192. 128-bit block construction"] + [""] * 28
    figure_grid_row = ["Word 3 D127D96"] + ["D95"] * 20 + [""] * 8

    raw_table = RawTable(
        page=818, bbox=(66, 100, 528, 700),
        rows=[header, data_row, figure_row, figure_grid_row],
    )
    lines = [_line("Table 210. AES data swapping example", 90)]
    table_merger = TableMerger()
    register_merger = RegisterMerger()
    tracker = HeadingTracker()
    tracker.start_page(lines)

    classify_page(
        818, [raw_table], lines, [_caption(210, "AES data swapping example", 90)],
        tracker, table_merger, register_merger,
    )

    tables = table_merger.finalize()
    assert len(tables) == 1
    assert len(tables[0].rows[0]) == 3
    assert len(tables[0].rows[1]) == 3
    assert tables[0].rows == [
        ["DATATYPE[1:0]", "Swapping performed", "Description"],
        ["0x0", "No swapping", "..."],
    ]


def test_uncut_wide_table_keeps_all_trailing_empty_columns():
    # A genuinely wide table with its own trailing empty columns (no
    # embedded figure caption anywhere) must be left completely alone --
    # verified: 11 such tables exist in the corpus and must not be trimmed.
    header = ["Offset", "Register"] + [""] * 9
    data_row = ["0x00", "FOO_CR"] + [""] * 9
    raw_table = RawTable(page=5, bbox=(66, 100, 528, 200), rows=[header, data_row])
    lines = [_line("Table 3. Some register summary", 90)]
    table_merger = TableMerger()
    register_merger = RegisterMerger()
    tracker = HeadingTracker()
    tracker.start_page(lines)

    classify_page(
        5, [raw_table], lines, [_caption(3, "Some register summary", 90)],
        tracker, table_merger, register_merger,
    )

    tables = table_merger.finalize()
    assert len(tables) == 1
    assert len(tables[0].rows[0]) == 11
    assert len(tables[0].rows[1]) == 11


# ---------------------------------------------------- FIGURE_CAPTION_BOUNDARY_FIX.md

def test_page_75_fixture_rejects_figures_grids_adopted_via_caption_bleed():
    # Verified real RM0490 page 75 shape: find_tables correctly returns
    # FOUR separate grids -- the real Table 24 and three of Figure 4's own
    # ruled boxes -- but assign_caption is blind to the Figure 4. caption
    # sitting between them and labels all four "Table 24". Exactly one
    # grid (the real one) must be accepted; the other three must be
    # rejected and routed to fragments, not merged in.
    lines = [
        _line("Table 24. Some real table caption", 211.3),
        _line("Figure 4. Example of disabling core debug access", 428.3),
    ]
    real_table = RawTable(
        page=75, bbox=(66, 223.4, 528, 300),
        rows=[["A", "B"], ["1", "2"], ["3", "4"], ["5", "6"], ["7", "8"]],
    )
    fig_grid_1 = RawTable(page=75, bbox=(66, 475.5, 528, 520), rows=[["f1"], ["f2"], ["f3"]])
    fig_grid_2 = RawTable(page=75, bbox=(66, 566.3, 528, 580), rows=[["f4"]])
    fig_grid_3 = RawTable(page=75, bbox=(66, 588.4, 528, 620), rows=[["f5"], ["f6"]])

    table_merger = TableMerger()
    register_merger = RegisterMerger()
    tracker = HeadingTracker()
    tracker.start_page(lines)

    dropped, _, fragments = classify_page(
        75, [real_table, fig_grid_1, fig_grid_2, fig_grid_3], lines,
        [_caption(24, "Some real table caption", 211.3)],
        tracker, table_merger, register_merger,
    )

    assert dropped == 3
    assert len(fragments) == 3
    assert all(f["would_have_joined"] == "24" for f in fragments)
    assert all(f["figure_caption"] == "Example of disabling core debug access" for f in fragments)

    tables = table_merger.finalize()
    assert len(tables) == 1
    assert tables[0].rows == [["A", "B"], ["1", "2"], ["3", "4"], ["5", "6"], ["7", "8"]]

    # round-trip: fragment rows + emitted rows == original total across all four grids
    original_total = 5 + 3 + 1 + 2
    emitted_total = len(tables[0].rows)
    fragment_total = sum(len(f["rows"]) for f in fragments)
    assert emitted_total + fragment_total == original_total


def test_figure_caption_above_table_caption_rejects_nothing():
    # Ordering matters: a Figure caption sitting ABOVE both the table's own
    # caption and the grid must never reject it -- only a Figure caption
    # strictly BETWEEN the caption and the grid counts as a boundary.
    lines = [
        _line("Figure 1. Some earlier figure", 50.0),
        _line("Table 9. A real table", 100.0),
    ]
    raw_table = RawTable(page=1, bbox=(66, 120, 528, 200), rows=[["A"], ["1"]])
    table_merger = TableMerger()
    register_merger = RegisterMerger()
    tracker = HeadingTracker()
    tracker.start_page(lines)

    dropped, _, fragments = classify_page(
        1, [raw_table], lines, [_caption(9, "A real table", 100.0)],
        tracker, table_merger, register_merger,
    )

    assert dropped == 0
    assert fragments == []
    tables = table_merger.finalize()
    assert len(tables) == 1
    assert tables[0].rows == [["A"], ["1"]]


def test_continued_caption_below_figure_is_picked_as_nearer_and_accepted():
    # A genuine multi-page/same-page continuation reprints "Table N. ...
    # (continued)" below the figure -- assign_caption picks THAT nearer
    # caption for the continuation grid, so no Figure caption sits between
    # ITS caption and itself, and it must be accepted normally.
    lines = [
        _line("Table 12. Some table", 90.0),
        _line("Figure 8. An unrelated figure", 200.0),
        _line("Table 12. Some table (continued)", 300.0),
    ]
    continuation_grid = RawTable(page=1, bbox=(66, 320, 528, 400), rows=[["x"], ["y"]])
    table_merger = TableMerger()
    register_merger = RegisterMerger()
    tracker = HeadingTracker()
    tracker.start_page(lines)

    captions = [
        _caption(12, "Some table", 90.0),
        _caption(12, "Some table (continued)", 300.0),
    ]
    dropped, _, fragments = classify_page(
        1, [continuation_grid], lines, captions,
        tracker, table_merger, register_merger,
    )

    assert dropped == 0
    assert fragments == []
    tables = table_merger.finalize()
    assert len(tables) == 1
    assert tables[0].rows == [["x"], ["y"]]


def test_vanish_guard_keeps_all_grids_when_every_one_would_be_rejected(caplog):
    import logging

    # Synthetic worst case: BOTH grids assigned to Table 6 sit below a
    # Figure caption relative to Table 6's own caption -- rejecting both
    # would leave the table with NO grid at all. The vanish guard must
    # keep them all rather than lose the table entirely.
    lines = [
        _line("Table 6. Some table", 90.0),
        _line("Figure 3. A figure between caption and both grids", 150.0),
    ]
    grid_a = RawTable(page=1, bbox=(66, 200, 528, 250), rows=[["a"], ["b"]])
    grid_b = RawTable(page=1, bbox=(66, 260, 528, 300), rows=[["c"]])
    table_merger = TableMerger()
    register_merger = RegisterMerger()
    tracker = HeadingTracker()
    tracker.start_page(lines)

    with caplog.at_level(logging.WARNING, logger="rmtables.classify"):
        dropped, _, fragments = classify_page(
            1, [grid_a, grid_b], lines, [_caption(6, "Some table", 90.0)],
            tracker, table_merger, register_merger,
        )

    assert dropped == 0
    assert fragments == []
    assert "refusing to reject every grid for Table 6 on page 1" in caplog.text
    tables = table_merger.finalize()
    assert len(tables) == 1
    assert tables[0].rows == [["a"], ["b"], ["c"]]
