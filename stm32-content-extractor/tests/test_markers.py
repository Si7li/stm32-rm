"""Inline table/figure markers."""

from __future__ import annotations

from rmtables.model import Caption, RawTable

from rmcontent.markers import (
    MARKER_RE,
    figure_marker,
    note_line_tops,
    region_markers,
    table_marker,
)


def caption(number, text, top=100.0):
    return Caption(number=number, text=text, continued=False, top=top, page=1)


def line(text, top):
    return {"text": text, "top": top, "bottom": top + 10, "x0": 67.0, "x1": 528.0}


def test_table_marker_carries_number_and_title():
    marker = table_marker(caption(26, "FLASH register map and reset values"))
    assert marker == "[Table 26. FLASH register map and reset values]"
    assert MARKER_RE.match(marker)


def test_table_marker_strips_a_continued_suffix():
    assert table_marker(caption(26, "FLASH register map (continued)")) == (
        "[Table 26. FLASH register map]"
    )


def test_no_caption_means_no_marker():
    assert table_marker(None) is None


def test_figure_marker():
    marker = figure_marker("Figure 21. DMA block diagram")
    assert marker == "[Figure 21. DMA block diagram]"
    assert MARKER_RE.match(marker)


def test_figure_marker_tolerates_a_split_word_or_number():
    """Reuses `rmtables.captions.FIGURE_CAPTION_RE`, which absorbs ST's
    intra-word space splitting."""
    assert figure_marker("F igure 21. DMA block diagram") == "[Figure 21. DMA block diagram]"
    assert figure_marker("Figure 2 1. DMA block diagram") == "[Figure 21. DMA block diagram]"


def test_figure_cross_reference_is_not_a_caption():
    assert figure_marker("As shown in Figure 21.") is None
    assert figure_marker("see the DMA block diagram") is None


def test_table_markers_are_placed_at_their_region_top():
    raw = RawTable(page=1, bbox=(67.0, 210.0, 528.0, 244.0), rows=[["a"]])
    captions = [caption(26, "FLASH register map and reset values", top=195.0)]
    markers, _, _ = region_markers([raw], captions, [])
    assert markers == [(210.0, 26, "[Table 26. FLASH register map and reset values]")]


def test_uncaptioned_regions_are_counted_not_marked():
    """Every register description prints two uncaptioned half-grids; a
    marker for each would be noise on 40% of all sections."""
    raw = RawTable(page=1, bbox=(67.0, 210.0, 528.0, 244.0), rows=[["Res."]])
    markers, suppressed, uncaptioned = region_markers([raw], [], [])
    assert markers == []
    assert uncaptioned == 1
    assert suppressed == set()


def test_the_regions_own_caption_line_is_suppressed():
    """ST prints the caption ABOVE the grid, so it is outside the bbox
    and would otherwise sit right before a marker restating it."""
    raw = RawTable(page=1, bbox=(67.0, 210.0, 528.0, 244.0), rows=[["a"]])
    captions = [caption(33, "Internal SRAM features", top=195.0)]
    lines = [line("Table 33. Internal SRAM features", 195.0)]
    _, suppressed, _ = region_markers([raw], captions, lines)
    assert 195.0 in suppressed


def test_a_prose_cross_reference_is_not_suppressed():
    """RM0486 10.3.1 prints "Table33 summarizes the features supported by
    each internal SRAM." above the real caption. It differs only by
    position and a missing space, so suppression must be by identity."""
    raw = RawTable(page=1, bbox=(67.0, 210.0, 528.0, 244.0), rows=[["a"]])
    captions = [caption(33, "Internal SRAM features", top=195.0)]
    lines = [
        line("Table33 summarizes the features supported by each internal SRAM.", 160.0),
        line("Table 33. Internal SRAM features", 195.0),
    ]
    _, suppressed, _ = region_markers([raw], captions, lines)
    assert suppressed == {195.0}
    assert 160.0 not in suppressed


def test_table_footnotes_below_the_region_are_suppressed():
    raw = RawTable(page=1, bbox=(67.0, 210.0, 528.0, 244.0), rows=[["a"]])
    captions = [caption(37, "RAMCFG interrupt requests", top=195.0)]
    lines = [
        line("Table 37. RAMCFG interrupt requests", 195.0),
        line("1. All these bits are in RAMCFG_BKPSRAMISR.", 250.0),
    ]
    _, suppressed, _ = region_markers([raw], captions, lines)
    assert suppressed == {195.0, 250.0}


def test_a_wrapped_footnote_suppresses_every_line_it_spans():
    raw = RawTable(page=1, bbox=(67.0, 210.0, 528.0, 244.0), rows=[["a"]])
    captions = [caption(37, "RAMCFG interrupt requests", top=195.0)]
    lines = [
        line("Table 37. RAMCFG interrupt requests", 195.0),
        line("1. All these bits are in the RAMCFG backup SRAM interrupt", 250.0),
        line("status register.", 260.0),
    ]
    _, suppressed, _ = region_markers([raw], captions, lines)
    assert suppressed == {195.0, 250.0, 260.0}


def test_note_line_tops_returns_nothing_without_notes():
    assert note_line_tops([line("Ordinary prose.", 250.0)], 244.0, []) == set()


def test_prose_below_a_region_that_is_not_a_footnote_survives():
    raw = RawTable(page=1, bbox=(67.0, 210.0, 528.0, 244.0), rows=[["a"]])
    captions = [caption(37, "RAMCFG interrupt requests", top=195.0)]
    lines = [
        line("Table 37. RAMCFG interrupt requests", 195.0),
        line("The interrupt requests are described below.", 250.0),
    ]
    _, suppressed, _ = region_markers([raw], captions, lines)
    assert suppressed == {195.0}


# -- one marker per LOGICAL table (MULTIPAGE_TABLE_MARKER_FIX) ---------------

from rmcontent.markers import LogicalTableTracker  # noqa: E402


def test_a_table_continued_on_the_next_page_emits_one_marker():
    """RM0486 4.3.2: p202 'Table 9. BSEC internal input/output signals',
    p203 the same caption with '(continued)'."""
    t = LogicalTableTracker()
    assert t.should_emit(9, 202)
    assert not t.should_emit(9, 203)


def test_two_grids_of_one_table_on_the_same_page_emit_one_marker():
    t = LogicalTableTracker()
    assert t.should_emit(26, 77)
    assert not t.should_emit(26, 77)


def test_a_three_page_table_emits_one_marker():
    """page_end advances on every continuation, exactly as TableMerger."""
    t = LogicalTableTracker()
    assert t.should_emit(9, 202)
    assert not t.should_emit(9, 203)
    assert not t.should_emit(9, 204)


def test_different_numbers_on_one_page_each_emit():
    t = LogicalTableTracker()
    assert t.should_emit(26, 77)
    assert t.should_emit(27, 77)
    assert t.should_emit(28, 77)


def test_the_same_number_far_apart_emits_twice():
    """Dedupe is on page adjacency, never "seen anywhere in the section"."""
    t = LogicalTableTracker()
    assert t.should_emit(9, 202)
    assert t.should_emit(9, 240)


def test_an_interleaved_number_breaks_the_run():
    """Only the PREVIOUS emitted marker is compared."""
    t = LogicalTableTracker()
    assert t.should_emit(9, 202)
    assert t.should_emit(10, 202)
    assert t.should_emit(9, 203)


def test_a_new_section_resets_the_tracker():
    t = LogicalTableTracker()
    assert t.should_emit(9, 202)
    t.reset()
    assert t.should_emit(9, 203)
