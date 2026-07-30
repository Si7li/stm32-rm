import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from rmtables.legends import assign_legend_table, find_legends
from rmtables.model import Caption, RawTable


def _line(text, top, height=10.0):
    return {"text": text, "top": top, "bottom": top + height}


def test_find_legends_captures_bare_legend_below_table():
    lines = [
        _line("Table 10. Flash memory organization", 90),
        _line("Legend: A = Alpha, B = Beta", 250),
    ]
    legends = find_legends(lines)
    assert len(legends) == 1
    assert legends[0]["table_number"] is None
    assert legends[0]["text"] == "A = Alpha, B = Beta"


def test_find_legends_captures_legend_for_table_n():
    # Verified RM0008 (page 40): "Legend for Table1: ..." -- no space
    # between "Table" and the number, and it can sit *above* its own
    # table's caption (position-independent).
    lines = [_line("Legend for Table1: the section in each row applies to products", 129.7)]
    legends = find_legends(lines)
    assert legends[0]["table_number"] == 1
    assert legends[0]["text"] == "the section in each row applies to products"


def test_find_legends_joins_wrapped_continuation_lines():
    lines = [
        _line("Legend: S = Start, A = Acknowledge,", 100),
        _line("NA = Non-acknowledge", 109),
    ]
    legends = find_legends(lines)
    assert len(legends) == 1
    assert legends[0]["text"] == "S = Start, A = Acknowledge, NA = Non-acknowledge"


def test_find_legends_stops_at_next_caption():
    lines = [
        _line("Legend: A = Alpha", 100),
        _line("Table 27. Something else", 118),
    ]
    legends = find_legends(lines)
    assert len(legends) == 1
    assert legends[0]["text"] == "A = Alpha"


def test_find_legends_stops_at_heading():
    lines = [
        _line("Legend: A = Alpha", 100),
        _line("4.3.6 FLASH main memory programming sequences", 118),
    ]
    legends = find_legends(lines)
    assert legends[0]["text"] == "A = Alpha"


def test_find_legends_stops_at_wide_vertical_gap():
    lines = [
        _line("Legend: A = Alpha", 100),
        _line("Unrelated body prose resuming much further down.", 400),
    ]
    legends = find_legends(lines)
    assert legends[0]["text"] == "A = Alpha"


def test_find_legends_captures_multiple_blocks_on_one_page():
    lines = [
        _line("Legend: A = Alpha", 100),
        _line("", 130),  # blank gap between the two blocks
        _line("Legend for Table 9: B = Beta", 160),
    ]
    legends = find_legends(lines)
    assert len(legends) == 2
    assert legends[0]["text"] == "A = Alpha"
    assert legends[1] == {"top": 160, "table_number": 9, "text": "B = Beta"}


def test_find_legends_applies_fix_symbols_to_captured_text():
    bullet = chr(0xF0B7)
    lines = [_line("Legend for Table 1: marked with “{0}”".format(bullet), 129.7)]
    legends = find_legends(lines)
    assert legends[0]["text"] == "marked with “•”"


def test_find_legends_case_insensitive_lowercase_legend():
    lines = [_line("legend: f - Input clock to the peripheral", 475.2)]
    legends = find_legends(lines)
    assert legends[0]["text"] == "f - Input clock to the peripheral"


def test_note_legend_re_matches_numbered_legend_footnote():
    from rmtables.legends import NOTE_LEGEND_RE

    assert NOTE_LEGEND_RE.match("3. Legends: S = Start, P = Stop")
    assert NOTE_LEGEND_RE.match("1. This is an ordinary footnote.") is None


def test_assign_legend_table_picks_nearest_table_above():
    cap10 = Caption(number=10, text="First", continued=False, top=90, page=1)
    cap11 = Caption(number=11, text="Second", continued=False, top=300, page=1)
    t10 = RawTable(page=1, bbox=(0, 100, 500, 200), rows=[["a"]])
    t11 = RawTable(page=1, bbox=(0, 310, 500, 400), rows=[["b"]])
    captioned_pairs = [(t10, cap10, None, [], []), (t11, cap11, None, [], [])]

    assert assign_legend_table(250, captioned_pairs) == 10
    assert assign_legend_table(450, captioned_pairs) == 11


def test_assign_legend_table_returns_none_when_nothing_above():
    assert assign_legend_table(50, []) is None
