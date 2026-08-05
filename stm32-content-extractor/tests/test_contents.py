"""Contents-page parsing, including the wrapped-entry case."""

from __future__ import annotations

import pytest

from rmcontent.contents import (
    match_entry,
    MIN_PAGE_COLUMN_SAMPLES,
    _page_column_x1,
    _strip_trailing_page,
    parse_contents,
)


class FakePage:
    def __init__(self, lines, height=842.0):
        self._lines = lines
        self.height = height

    def extract_text_lines(self, **kwargs):
        return [{"text": t, "x1": x1, "top": 100.0} for t, x1 in self._lines]

    def flush_cache(self):
        pass


class FakePDF:
    def __init__(self, pages):
        self.pages = pages


PAGE_COLUMN = 527.9


def _dot_entries(n: int) -> list[tuple[str, float]]:
    """Enough ordinary entries to calibrate the page-number column."""
    return [(f"2.{i} Filler section . . . . . . . . {40 + i}", PAGE_COLUMN) for i in range(n)]


def _contents_pdf(extra: list[tuple[str, float]]) -> FakePDF:
    lines = [("Contents RM0490", 400.0)] + _dot_entries(MIN_PAGE_COLUMN_SAMPLES) + extra
    return FakePDF([FakePage(lines), FakePage([("RM0490 List of tables", 400.0)])])


def test_strip_trailing_page_plain():
    assert _strip_trailing_page("General information . . . . . 41") == ("General information", 41)


def test_strip_trailing_page_single_dot():
    # "11.6.4 ... (DMA_CNDTRx) . 238" -- one leader dot is all that fits.
    title, page = _strip_trailing_page("Register (DMA_CNDTRx) . 238")
    assert (title, page) == ("Register (DMA_CNDTRx)", 238)


def test_strip_trailing_page_absent():
    assert _strip_trailing_page("FLASH PCROP area A start address register") == (
        "FLASH PCROP area A start address register",
        None,
    )


def test_bare_page_number_needs_the_column():
    """A dot-less trailing number is a page number only when the line is
    right-aligned in the page-number column."""
    assert _strip_trailing_page("Register 1 [alternate] (TIM14_CCMR1) 537", True) == (
        "Register 1 [alternate] (TIM14_CCMR1)",
        537,
    )
    assert _strip_trailing_page("Clock enable in Sleep/Stop mode register 1", False) == (
        "Clock enable in Sleep/Stop mode register 1",
        None,
    )


def test_page_column_needs_enough_samples():
    assert _page_column_x1([("1.1 A . . . 4", 527.9)]) is None
    assert _page_column_x1(_dot_entries(MIN_PAGE_COLUMN_SAMPLES)) == pytest.approx(527.9)


def test_wrapped_entry_page_number_on_next_line():
    """The 61-entry shortfall on RM0490: the title fills the line, so the
    dot leaders and page number land on the following one."""
    pdf = _contents_pdf([
        ("8.5.1 GPIO port mode register (GPIOx_MODER)", 392.9),
        ("(x = A, B, C, D, F) . . . . . . . . . . . . 188", PAGE_COLUMN),
    ])
    index = parse_contents(pdf)
    assert index.sections["8.5.1"] == (
        "GPIO port mode register (GPIOx_MODER) (x = A, B, C, D, F)",
        188,
    )


def test_wrapped_entry_with_dotless_page_number():
    """RM0490 19.4.6: the title fills its line so completely that ST
    prints no leader dots at all, only a space before the page number."""
    pdf = _contents_pdf([
        ("19.4.6 TIM14 capture/compare mode register 1 [alternate] (TIM14_CCMR1) 537", PAGE_COLUMN),
    ])
    index = parse_contents(pdf)
    assert index.sections["19.4.6"] == (
        "TIM14 capture/compare mode register 1 [alternate] (TIM14_CCMR1)",
        537,
    )


def test_wrapped_title_ending_in_a_digit_is_not_a_page_number():
    """The ambiguity the column measurement resolves: this title really
    does end in "1", and its page number is on the next line."""
    pdf = _contents_pdf([
        ("6.4.18 RCC APB peripheral clock enable in Sleep/Stop mode register 1", 485.8),
        (". . . . . . . . . . . . . . . . 154", PAGE_COLUMN),
    ])
    index = parse_contents(pdf)
    title, page = index.sections["6.4.18"]
    assert title == "RCC APB peripheral clock enable in Sleep/Stop mode register 1"
    assert page == 154


def test_entry_with_no_space_before_the_title():
    """RM0486: once the last component reaches three digits, ST's
    Contents field overflows and the separating space disappears. This
    cost 199 of that manual's 3,585 sections from the ground truth."""
    pdf = _contents_pdf([
        ("14.10.100RCC APB1H sleep enable register (RCC_APB1HLPENR) . . . . 611", PAGE_COLUMN),
    ])
    index = parse_contents(pdf)
    assert index.sections["14.10.100"] == (
        "RCC APB1H sleep enable register (RCC_APB1HLPENR)",
        611,
    )


def test_the_missing_space_never_splits_the_number_itself():
    """Without a space the title must start with a letter or bracket, so
    "14.10.100" can never be read as "14.10.10" plus a title of "0...".
    """
    pdf = _contents_pdf([("14.10.100 . . . . . . . . . . . . 611", PAGE_COLUMN)])
    index = parse_contents(pdf)
    assert "14.10.10" not in index.sections
    assert "14.10.100" not in index.sections  # no letter-bearing title at all


def test_chapters_and_sections_are_separated():
    pdf = _contents_pdf([
        ("4 Embedded flash memory (FLASH) . . . . . . 56", PAGE_COLUMN),
        ("4.1 FLASH main features . . . . . . 56", PAGE_COLUMN),
    ])
    index = parse_contents(pdf)
    assert index.chapters["4"] == ("Embedded flash memory (FLASH)", 56)
    assert index.chapter_title("4") == "Embedded flash memory (FLASH)"
    assert "4" not in index.sections
    assert index.sections["4.1"] == ("FLASH main features", 56)


def test_page_furniture_is_ignored():
    pdf = _contents_pdf([
        ("3/1023 RM0490 Rev 6", 300.0),
        ("29", 550.0),
        ("7.1 CRS introduction . . . . . . 166", PAGE_COLUMN),
    ])
    index = parse_contents(pdf)
    assert index.sections["7.1"] == ("CRS introduction", 166)
    assert "3" not in index.chapters


def test_no_contents_pages_yields_empty_index():
    pdf = FakePDF([FakePage([("Some body page", 400.0)])])
    index = parse_contents(pdf)
    assert len(index) == 0
    assert index.chapter_title("4") == ""


def test_an_entry_whose_title_opens_with_an_ordinal_is_not_a_chapter():
    """RM0486 wraps a VENC register entry onto a line of its own,
    "1st DCT partition register (VENC_SWREG58) . . . 2180". Under a
    pattern allowing no space it became chapter 1, overwriting
    "Documentation conventions"; "2nd ..." overwrote chapter 2."""
    assert match_entry("1st DCT partition register (VENC_SWREG58) . . . 2180") is None
    assert match_entry("2nd DCT partition register (VENC_SWREG59) . . . 2181") is None
    assert match_entry("3rd stage filter . . . 100") is None
    assert match_entry("4th order response . . . 100") is None


def test_a_real_chapter_line_still_matches():
    assert match_entry("1 Documentation conventions . . . 41") == (
        "1", "Documentation conventions . . . 41",
    )


def test_a_subsection_keeps_its_missing_space_tolerance():
    """The mandatory space applies to chapters only; a dot makes the
    number unambiguous, so RM0486's overflowed subsections still parse."""
    assert match_entry("14.10.100RCC APB1H sleep enable register . . . 611") == (
        "14.10.100", "RCC APB1H sleep enable register . . . 611",
    )


def test_chapters_must_be_listed_in_ascending_order():
    """A second guard, independent of the pattern: a line claiming
    chapter 1 after chapter 40 has been listed is not a chapter."""
    pdf = _contents_pdf([
        ("40 Some later chapter . . . . . . 2000", PAGE_COLUMN),
        ("1 Documentation conventions . . . . . . 41", PAGE_COLUMN),
    ])
    index = parse_contents(pdf)
    assert index.chapters["40"] == ("Some later chapter", 2000)
    assert "1" not in index.chapters


def test_a_duplicate_chapter_keeps_the_first_occurrence():
    pdf = _contents_pdf([
        ("4 Embedded flash memory (FLASH) . . . . . . 56", PAGE_COLUMN),
        ("4 Something else entirely . . . . . . 900", PAGE_COLUMN),
    ])
    index = parse_contents(pdf)
    assert index.chapters["4"] == ("Embedded flash memory (FLASH)", 56)
