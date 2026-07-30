import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from rmtables.notes import notes_below


def _line(text, top, height=9.0):
    return {"text": text, "top": top, "bottom": top + height}


def test_notes_below_collects_numbered_footnotes():
    lines = [
        _line("1. First note.", 100),
        _line("2. Second note.", 118),
    ]
    assert notes_below(lines, bottom=90) == ["1. First note.", "2. Second note."]


def test_notes_below_joins_wrapped_continuation_line():
    lines = [
        _line("1. This is a long footnote that wraps onto", 100),
        _line("a second line.", 109),
    ]
    assert notes_below(lines, bottom=90) == [
        "1. This is a long footnote that wraps onto a second line."
    ]


def test_notes_below_stops_at_next_caption():
    lines = [
        _line("1. A real note.", 100),
        _line("Table 27. Something else", 118),
    ]
    assert notes_below(lines, bottom=90) == ["1. A real note."]


def test_notes_below_stops_at_heading():
    lines = [
        _line("1. A real note.", 100),
        _line("4.3.6 FLASH main memory programming sequences", 118),
    ]
    assert notes_below(lines, bottom=90) == ["1. A real note."]


def test_notes_below_stops_at_page_footer():
    lines = [
        _line("1. A real note.", 100),
        _line("62/1023 RM0490 Rev 6", 118),
    ]
    assert notes_below(lines, bottom=90) == ["1. A real note."]


def test_notes_below_stops_at_wide_vertical_gap():
    # A gap much wider than the previous line's own height signals a
    # paragraph break, not a footnote wrap or a new list item.
    lines = [
        _line("1. A real note.", 100),
        _line("Unrelated body prose resuming after the note.", 400),
    ]
    assert notes_below(lines, bottom=90) == ["1. A real note."]


def test_notes_below_returns_empty_when_first_line_is_not_a_footnote():
    lines = [_line("Some ordinary body text.", 100)]
    assert notes_below(lines, bottom=90) == []
