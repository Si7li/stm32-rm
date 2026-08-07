"""Reading order: `extract_text_lines` returns content-stream order."""

from __future__ import annotations

from rmcontent.lines import is_top_ordered, page_lines, read_page_lines


class OrderPage:
    def __init__(self, rows):
        self._rows = rows

    def extract_text_lines(self, **kwargs):
        return [{"text": t, "top": top, "x0": x0} for t, top, x0 in self._rows]


# RM0490 page 290, Figure 30: ST drew the artwork in two content passes,
# so the tops ascend to the page footer and then jump back to 235.6.
PAGE_290 = [
    ("Figure 30. ADC block diagram", 138.2, 226.5),
    ("CHSEL[22:0]", 164.0, 300.1),
    ("SMP[2:0]", 563.8, 412.7),
    ("1. TRGi are mapped at product level...", 575.9, 67.3),
    ("[page footer]", 744.5, 67.3),
    ("BHA", 235.6, 501.8),
    ("VREF+", 171.6, 380.2),
    ("TRG0", 503.5, 199.4),
]


def test_the_corpus_page_is_not_in_reading_order():
    lines = OrderPage(PAGE_290).extract_text_lines()
    assert not is_top_ordered(lines)


def test_sorting_makes_the_figure_region_contiguous():
    lines = page_lines(OrderPage(PAGE_290))
    assert [l["text"] for l in lines] == [
        "Figure 30. ADC block diagram",
        "CHSEL[22:0]",
        "VREF+",
        "BHA",
        "TRG0",
        "SMP[2:0]",
        "1. TRGi are mapped at product level...",
        "[page footer]",
    ]
    assert is_top_ordered(lines)


def test_read_page_lines_reports_whether_it_reordered():
    _, reordered = read_page_lines(OrderPage(PAGE_290))
    assert reordered

    ordered = [("a", 100.0, 67.0), ("b", 120.0, 67.0), ("c", 140.0, 67.0)]
    lines, reordered = read_page_lines(OrderPage(ordered))
    assert not reordered
    assert [l["text"] for l in lines] == ["a", "b", "c"]


def test_lines_at_the_same_top_break_on_x0():
    page = OrderPage([("right", 100.0, 400.0), ("left", 100.0, 67.0)])
    assert [l["text"] for l in page_lines(page)] == ["left", "right"]
