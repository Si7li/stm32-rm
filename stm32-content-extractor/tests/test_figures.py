"""Figure-caption validation against the manual's List of figures."""

from __future__ import annotations

from rmcontent.figures import (
    MIN_TRUSTED_ENTRIES,
    FigureCaptions,
    parse_list_of_figures,
)


class FakePage:
    def __init__(self, text, lines=None):
        self._text = text
        self._lines = lines or []

    def extract_text(self):
        return self._text

    def extract_text_lines(self, **kwargs):
        # `top` matters: `lines.read_page_lines` sorts by it, so a
        # fixture without one cannot express the order it is testing.
        return [
            {"text": t, "top": 100.0 + 12.0 * i, "x0": 67.0}
            for i, t in enumerate(self._lines)
        ]

    def flush_cache(self):
        pass


class FakePDF:
    def __init__(self, pages):
        self.pages = pages


def list_pdf(entries):
    return FakePDF([
        FakePage("List of figures", entries),
        FakePage("1 Documentation conventions", []),
    ])


def listed(n):
    """Enough entries for the parse to be trusted."""
    return {i: f"Filler figure {i}" for i in range(100, 100 + n)}


def test_list_of_figures_parses_entries():
    index = parse_list_of_figures(list_pdf([
        "List of figures",
        "Figure 1. System architecture . . . . . . . . . . . 42",
        "Figure 2. Memory map . . . . . . . . . . . . . . . . 45",
    ]))
    assert index[1] == "System architecture"
    assert index[2] == "Memory map"


def test_a_wrapped_list_entry_is_recovered():
    """The same defect `contents.py` documents: a long title puts the dot
    leaders and the page number on the next line, so requiring that tail
    loses the entry -- and with it 48 real RM0486 captions."""
    index = parse_list_of_figures(list_pdf([
        "List of figures",
        "Figure 80. HPDMA channel execution and linked-list",
        "programming . . . . . . . . . . . . . . . . . . 612",
    ]))
    assert index[80] == "HPDMA channel execution and linked-list programming"


def test_a_split_figure_word_is_tolerated():
    """Reuses rmtables' FIGURE_WORD_RE, which absorbs ST's rendering."""
    index = parse_list_of_figures(list_pdf([
        "List of figures",
        "F igure 3. Clock tree . . . . . . . . . . . . . . . 90",
    ]))
    assert index[3] == "Clock tree"


def test_a_cross_reference_opening_with_a_verb_is_not_a_caption():
    """RM0486 12.4.3's live bug, and 53.3.25's two."""
    fc = FigureCaptions(listed(MIN_TRUSTED_ENTRIES))
    assert not fc.is_caption(
        14, "shows the functional view of TAG and data memories, for an n-way set")
    assert not fc.is_caption(571, "shows how the 'gated on A & B' mode is handled")
    assert not fc.is_caption(570, "presents waveforms and corresponding values")
    assert len(fc.rejected) == 3


def test_a_real_caption_survives_the_verb_test():
    fc = FigureCaptions(listed(MIN_TRUSTED_ENTRIES))
    assert fc.is_caption(14, "CACHEAXI TAG and data memories functional view")
    assert fc.is_caption(1, "System architecture")
    # A title that merely CONTAINS a verb is fine; only the first word counts.
    assert fc.is_caption(2, "Block diagram that shows the clock tree")
    assert fc.rejected == []


def test_a_capitalised_first_word_is_never_a_verb_rejection():
    """"Details" can open a real title; the test requires lowercase."""
    fc = FigureCaptions(listed(MIN_TRUSTED_ENTRIES))
    assert fc.is_caption(5, "Details of the interconnect")


def test_a_band_opens_only_for_a_caption_the_list_confirms():
    fc = FigureCaptions({**listed(MIN_TRUSTED_ENTRIES), 1: "System architecture"})
    assert fc.may_open_band(1, "System architecture")
    assert not fc.may_open_band(999, "Some unlisted figure")


def test_a_title_mismatch_withholds_the_band_but_keeps_the_marker():
    """RM0486 13.4.1's body caption reads "Device startup (V supplied
    directly from SMPS..." where the listing has the subscripted form.
    That is a rendering artifact, not a pseudo-caption, so the marker
    stays and only the authority to delete is withheld."""
    fc = FigureCaptions({**listed(MIN_TRUSTED_ENTRIES),
                         18: "Device startup (VDD supplied directly from SMPS step-down)"})
    assert fc.is_caption(18, "Device startup (V supplied directly from SMPS step-down)")
    assert not fc.may_open_band(18, "Device startup (V supplied directly from SMPS step-down)")
    assert len(fc.unbanded) == 1


def test_an_untrusted_list_never_rejects_or_bands():
    """A failed parse must not suppress captions -- but it also cannot
    authorise a deletion."""
    fc = FigureCaptions({1: "System architecture"})
    assert not fc.trusted
    assert fc.is_caption(1, "System architecture")
    assert fc.is_caption(999, "Anything at all")
    assert not fc.may_open_band(1, "System architecture")


def test_the_verb_test_applies_even_without_a_trusted_list():
    fc = FigureCaptions({})
    assert not fc.is_caption(14, "shows the functional view of TAG memories")
