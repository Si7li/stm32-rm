"""The four noise classes of §3, plus figure-artwork detection."""

from __future__ import annotations

import pytest

from rmcontent.noise import (
    NoiseCounts,
    PageFurniture,
    document_header_re,
    is_bit_layout_row,
    is_stray_glyph,
)

PAGE_HEIGHT = 842.0


def line(text, top):
    return {"text": text, "top": top, "bottom": top + 10, "x0": 67.0, "x1": 528.0}


def test_bit_layout_rows():
    assert is_bit_layout_row("31 30 29 28 27 26 25 24 23 22 21 20 19 18 17 16")
    assert is_bit_layout_row("15 14 13 12 11 10 9 8 7 6 5 4 3 2 1 0")
    # Eight numbers is the shortest run a real register layout has.
    assert is_bit_layout_row("7 6 5 4 3 2 1 0")


def test_shorter_runs_printed_inside_figures():
    """The three lines the `{5,}` threshold adds over `{7,}`, measured
    across RM0486 Rev 4 and RM0490 Rev 6 -- all figure-interior labels,
    no prose among them."""
    assert is_bit_layout_row("19 15 11 7 3 0")  # RM0490 16.9, Figure 53 bit ruler
    assert is_bit_layout_row("0 1 2 3 0 1 2")  # RM0486 32.4.30, timing indices
    assert is_bit_layout_row("00 10 20 01 11 21")  # RM0486 10.3.2, bank labels


def test_bit_layout_row_does_not_eat_prose_or_short_runs():
    assert not is_bit_layout_row("1 2 3 4 5")  # five numbers, below the floor
    assert not is_bit_layout_row("0: Debugger disabled")
    assert not is_bit_layout_row("Bits 31:19 Reserved, must be kept at reset value.")
    assert not is_bit_layout_row("1 2 3 4 5 6 7 8 wait states")
    assert not is_bit_layout_row("100 200 300 400 500 600 700 800")  # 3-digit, not bits
    assert not is_bit_layout_row("0x00 0x01 0x02 0x03 0x04 0x05 0x06")


def test_stray_glyphs():
    # The lone rotated-label remnant printed above every register layout.
    assert is_stray_glyph("s")
    assert is_stray_glyph("ts")
    assert not is_stray_glyph("0")  # a digit could be real content
    assert not is_stray_glyph("1.")
    assert not is_stray_glyph("The")


def test_document_header_matches_both_page_parities():
    header_re = document_header_re("RM0490")
    assert header_re.search("RM0490 Embedded flash memory (FLASH)")
    assert header_re.search("Embedded flash memory (FLASH) RM0490")
    assert not header_re.search("as described in RM0490 section 4")


def test_document_header_absent_when_document_unknown():
    assert document_header_re("") is None


def test_page_furniture_drops_header_footer_and_margin_number():
    lines = [
        line("RM0490 Embedded flash memory (FLASH)", 59.76),
        line("4.7.1 FLASH access control register (FLASH_ACR)", 120.97),
        line("Address offset: 0x000", 142.26),
        line("RM0490 Rev 6 77/1023", 744.46),
        line("91", 760.89),  # ST's bare marginal chapter-tab number
    ]
    drop = PageFurniture(PAGE_HEIGHT, document_header_re("RM0490")).furniture_tops(lines)
    assert drop == {0, 3, 4}


def test_page_furniture_leaves_body_alone_when_there_is_no_footer():
    lines = [
        line("4.7.1 FLASH access control register (FLASH_ACR)", 120.97),
        line("Address offset: 0x000", 142.26),
    ]
    drop = PageFurniture(PAGE_HEIGHT, document_header_re("RM0490")).furniture_tops(lines)
    assert drop == set()


def test_footer_pattern_only_applies_in_the_bottom_band():
    """A body line citing "RM0490 Rev 6" mid-page is prose, not a footer."""
    lines = [
        line("see RM0490 Rev 6 for the full description", 300.0),
        line("78/1023 RM0490 Rev 6", 744.46),
    ]
    drop = PageFurniture(PAGE_HEIGHT, document_header_re("RM0490")).furniture_tops(lines)
    assert drop == {1}


def test_stray_footer_outside_the_bottom_band_is_dropped():
    """On a landscape figure page ST prints the footer along the side, so
    its `top` lands mid-page (RM0486 2.1.2, 64.7.3)."""
    lines = [
        line("[Figure 1. Interconnect top view]", 200.0),
        line("160/4669", 300.0),
        line("Memory", 320.0),
    ]
    drop = PageFurniture(PAGE_HEIGHT, document_header_re("RM0486"), 4669).furniture_tops(lines)
    assert drop == {1}


def test_stray_footer_needs_the_manuals_own_page_count():
    """The denominator is the whole safeguard: RM0486 prints `16/32-bit`,
    `18/24-bit mode (RGB888)` and the bare figure labels `6/16` and
    `7/16 7/16`, none of which is a footer."""
    lines = [
        line("6/16", 300.0),
        line("16/32", 320.0),
        line("160/4669", 340.0),
    ]
    drop = PageFurniture(PAGE_HEIGHT, document_header_re("RM0486"), 4669).furniture_tops(lines)
    assert drop == {2}


def test_stray_footer_filter_is_inert_without_a_page_count():
    lines = [line("160/4669", 300.0)]
    assert PageFurniture(PAGE_HEIGHT, None, 0).furniture_tops(lines) == set()


def test_doc_rev_line_is_dropped_anywhere():
    lines = [line("Some prose.", 200.0), line("RM0486 Rev 4", 300.0)]
    drop = PageFurniture(PAGE_HEIGHT, document_header_re("RM0486"), 4669).furniture_tops(lines)
    assert drop == {1}


def test_prose_mentioning_the_revision_is_not_a_footer():
    lines = [line("as described in RM0486 Rev 4 section 4", 300.0)]
    drop = PageFurniture(PAGE_HEIGHT, document_header_re("RM0486"), 4669).furniture_tops(lines)
    assert drop == set()


def test_noise_counts_total_and_summary():
    counts = NoiseCounts(
        headers_footers=2046, bit_layout_rows=742,
        contents_pages=40, contents_page_lines=1200, stray_glyphs=371,
    )
    assert counts.total_lines() == 2046 + 742 + 1200 + 371
    assert any("bit-layout" in s for s in counts.summary_lines())


# -- figure artwork ---------------------------------------------------------

from rmcontent.noise import (  # noqa: E402
    ARTWORK_RATIO,
    DEFAULT_BODY_FONT,
    MIN_PLAUSIBLE_BODY_FONT,
    artwork_threshold,
    derive_body_font_size,
    is_artwork_line,
    is_asset_id,
    line_font_size,
)


def sized(text, size):
    return {"text": text, "top": 100.0, "bottom": 110.0, "x0": 67.0, "x1": 528.0,
            "chars": [{"size": size} for _ in text]}


class FontPage:
    def __init__(self, lines):
        self._lines = lines

    def extract_text_lines(self, **kwargs):
        return self._lines

    def flush_cache(self):
        pass


class FontPDF:
    def __init__(self, pages):
        self.pages = pages


def test_line_font_size_is_the_median():
    line = {"chars": [{"size": 2.0}, {"size": 2.0}, {"size": 10.0}]}
    assert line_font_size(line) == 2.0
    assert line_font_size({"chars": []}) is None
    assert line_font_size({}) is None


def test_artwork_threshold_is_a_fraction_of_the_body_size():
    assert artwork_threshold(10.0) == 6.0
    assert artwork_threshold(9.0) == pytest.approx(5.4)
    assert ARTWORK_RATIO == 0.6


def test_artwork_classification_by_size():
    assert is_artwork_line(sized("CK_ICN_M_NPU", 2.0), 6.0)
    assert is_artwork_line(sized("elabaehcaC UPN", 3.0), 6.0)
    assert not is_artwork_line(sized("Body prose here.", 10.0), 6.0)
    assert not is_artwork_line(sized("1. The domain is pink.", 8.0), 6.0)
    # 6 pt is the ambiguous band and stays on the keep side.
    assert not is_artwork_line(sized("Ambiguous.", 6.0), 6.0)


def test_asset_ids():
    assert is_asset_id("MS70497V3")
    assert is_asset_id("MSv40234")
    assert is_asset_id("MSc12345b2")
    assert is_asset_id("  MS56979V1  ")
    # Only when it is the whole line -- never inside a sentence.
    assert not is_asset_id("The drawing MS70497V3 is referenced here.")
    assert not is_asset_id("MS123")  # too few digits
    assert is_asset_id("MSv53041V1.")  # a stray trailing period
    assert not is_asset_id("Memory")


def test_body_font_size_is_the_mode_over_sampled_lines():
    pdf = FontPDF([
        FontPage([sized("body", 10.0) for _ in range(8)] + [sized("art", 2.0)]),
        FontPage([sized("body", 10.0) for _ in range(6)] + [sized("note", 8.0)]),
    ])
    assert derive_body_font_size(pdf) == 10.0


def test_body_font_size_survives_a_page_that_is_all_artwork():
    """RM0486 page 160 is 1,552 chars at 2 pt and 1,266 at 3 pt, with
    only the caption at body size."""
    artwork = FontPage([sized("label", 2.0) for _ in range(60)])
    body = [FontPage([sized("body", 9.0) for _ in range(40)]) for _ in range(5)]
    assert derive_body_font_size(FontPDF([artwork] + body)) == 9.0


def test_an_implausibly_small_derived_size_falls_back():
    """Rather than let artwork filtering eat the prose."""
    pdf = FontPDF([FontPage([sized("tiny", 3.0) for _ in range(20)])])
    assert derive_body_font_size(pdf) == DEFAULT_BODY_FONT
    assert DEFAULT_BODY_FONT >= MIN_PLAUSIBLE_BODY_FONT


def test_no_sampleable_text_falls_back():
    assert derive_body_font_size(FontPDF([FontPage([])])) == DEFAULT_BODY_FONT
    assert derive_body_font_size(FontPDF([])) == DEFAULT_BODY_FONT


def test_a_trailing_asset_id_is_stripped_not_the_line():
    """On a wide figure the id lands on the legend's own line."""
    from rmcontent.noise import strip_trailing_asset_id
    assert strip_trailing_asset_id(
        "RRA = round-robin arbitration, FPA = fixed-priority arbitration MSv66927V1"
    ) == "RRA = round-robin arbitration, FPA = fixed-priority arbitration"
    assert strip_trailing_asset_id("Bus interface clocks Kernel clocks MSv70484V3") == (
        "Bus interface clocks Kernel clocks"
    )
    # Untouched when there is no id, or when the id is mid-sentence.
    assert strip_trailing_asset_id("Ordinary prose.") == "Ordinary prose."
    assert strip_trailing_asset_id("The drawing MS70497V3 is referenced here.") == (
        "The drawing MS70497V3 is referenced here."
    )


def test_a_line_of_several_asset_ids_is_recognised():
    """RM0486 39.9.2 prints two side by side at 5.99 pt, above the size
    threshold -- so the id rule, not the size rule, has to catch them."""
    assert is_asset_id("MS54051V1 MS54052V1")


def test_the_size_rule_no_longer_tests_asset_ids():
    """An id line must survive the size backstop to reach the band and
    close it; the band's caller drops it instead."""
    assert not is_artwork_line(sized("MSv66119V2", 9.96), 6.0)
    assert is_artwork_line(sized("CK_ICN_M_NPU", 2.0), 6.0)


def test_the_identifier_matches_every_rendered_form():
    from rmcontent.noise import contains_asset_id
    for form in ("MSv66119V2", "MS70497V3", "MSv45319V2", "MS56979V1",
                 "MSc12345b2", "MS v 66119 V2", "M S v 66119 V2"):
        assert contains_asset_id(form), form
    # An id anywhere in the line closes a band, so `search` not `match`.
    assert contains_asset_id("RRA = round-robin arbitration MSv66927V1")


def test_the_identifier_does_not_reach_register_prose():
    """The gate that matters: 9.0 pt register prose must be untouchable."""
    from rmcontent.noise import contains_asset_id
    for prose in ("Bits 15:0 BSy: Port x set I/O y",
                  "Bits 31:19 Reserved, must be kept at reset value.",
                  "0: Debugger disabled", "MS123", "The ROM S 1234 area"):
        assert not contains_asset_id(prose), prose


# -- rotated running heads (ROTATED_RUNNING_HEAD_FIX) -----------------------

from rmcontent.noise import is_rotated_line  # noqa: E402


def oriented(text, uprights):
    """A line whose chars carry the given `upright` flags."""
    return {"text": text, "top": 100.0, "bottom": 110.0, "x0": 67.0, "x1": 528.0,
            "chars": [{"size": 6.0, "upright": u} for u in uprights]}


def test_a_rotated_running_head_fragment_is_dropped():
    """RM0486 p160, a landscape figure page."""
    assert is_rotated_line(oriented("Memory", [False] * 6))
    assert is_rotated_line(oriented("and bus", [False] * 6))
    assert is_rotated_line(oriented("architecture", [False] * 12))
    assert is_rotated_line(oriented("RM0486", [False] * 6))
    assert is_rotated_line(oriented("160/4669", [False] * 8))


def test_a_body_line_on_a_portrait_page_is_kept():
    """Every line on RM0486 p158 is upright."""
    assert not is_rotated_line(oriented("Memory and bus architecture RM0486", [True] * 30))
    assert not is_rotated_line(oriented("2.1 System architecture", [True] * 21))
    assert not is_rotated_line(
        oriented("Bits 15:0 BSy: Port x set I/O y", [True] * 31))


def test_a_figure_caption_on_a_landscape_page_is_kept():
    """Captions stay upright, so markers are unaffected."""
    assert not is_rotated_line(oriented(
        "Figure 1. Interconnect top view - STM32N6x7 devices", [True] * 44))


def test_one_stray_rotated_glyph_does_not_discard_the_line():
    """The rule is a MAJORITY, not "any"."""
    assert not is_rotated_line(oriented("mostly upright text", [True] * 19 + [False]))
    assert not is_rotated_line(oriented("half and half", [True] * 6 + [False] * 6))
    # A clear majority does trigger it.
    assert is_rotated_line(oriented("rotated artwork", [False] * 30 + [True] * 7))


def test_a_line_with_no_chars_is_kept():
    assert not is_rotated_line({"text": "no chars", "chars": []})
    assert not is_rotated_line({"text": "no key"})


def test_a_char_missing_the_flag_counts_as_upright():
    assert not is_rotated_line(
        {"text": "x", "chars": [{"size": 9.0}, {"size": 9.0}]})


def test_a_header_that_is_only_the_document_number_is_dropped():
    """RM0490 p45 prints the running head with no chapter title beside
    it, leaving a standalone 'RM0490' in section 2.1."""
    header_re = document_header_re("RM0490")
    assert header_re.search("RM0490")
    lines = [line("RM0490", 59.8), line("Body prose.", 120.0)]
    drop = PageFurniture(PAGE_HEIGHT, header_re, 1023).furniture_tops(lines)
    assert drop == {0}


def test_a_body_line_that_is_only_the_document_number_survives():
    """The bare form is only ever tested against the topmost line in the
    header band."""
    header_re = document_header_re("RM0490")
    lines = [line("4.7.1 FLASH access control register", 120.0),
             line("RM0490", 300.0)]
    drop = PageFurniture(PAGE_HEIGHT, header_re, 1023).furniture_tops(lines)
    assert drop == set()


# -- the body text column (FIGURE_COLUMN_FIX) -------------------------------

from rmcontent.noise import (  # noqa: E402
    MARGIN_MIN_SHARE,
    BodyMetrics,
    derive_body_metrics,
)


RM0008_MARGINS = (67, 124, 145, 161, 162, 163, 164, 176)


def test_rm0008_figure_187_lines_classify_as_measured():
    """The measurement the whole rule rests on."""
    m = BodyMetrics(9.96, RM0008_MARGINS)
    assert not m.is_artwork(9.96, 246.8)   # caption
    assert m.is_artwork(7.50, 296.7)       # 'Memory transaction'
    assert m.is_artwork(7.50, 171.1)       # 'A[25:0]'
    assert m.is_artwork(6.00, 489.6)       # 'ai14720c'
    assert not m.is_artwork(7.98, 124.0)   # figure footnote, at the margin
    assert not m.is_artwork(9.96, 124.0)   # body prose


def test_either_condition_alone_makes_a_line_body_flow():
    m = BodyMetrics(9.96, RM0008_MARGINS)
    assert not m.is_artwork(9.96, 400.0)   # body size, off margin
    assert not m.is_artwork(6.00, 124.0)   # small, at the margin
    assert m.is_artwork(6.00, 400.0)       # neither


def test_the_margin_test_has_a_one_point_tolerance():
    m = BodyMetrics(9.96, (124.0,))
    assert not m.is_artwork(7.0, 123.2)
    assert not m.is_artwork(7.0, 124.9)
    assert m.is_artwork(7.0, 125.5)


def test_a_line_just_below_body_size_is_still_body():
    """The 0.4 pt allowance absorbs rendering jitter."""
    m = BodyMetrics(9.96, (124.0,))
    assert not m.is_artwork(9.6, 400.0)
    assert m.is_artwork(9.5, 400.0)


def test_an_unmeasurable_line_is_never_artwork():
    m = BodyMetrics(9.96, (124.0,))
    assert not m.is_artwork(None, 400.0)
    assert not m.is_artwork(6.0, None)


def test_metrics_are_derived_from_the_document_not_hardcoded():
    body = [sized("body prose", 9.96) for _ in range(60)]
    for line in body:
        line["x0"] = 124.0
    art = [sized("label", 7.5) for _ in range(10)]
    for i, line in enumerate(art):
        line["x0"] = 300.0 + i  # scattered, none reaches the 2% floor

    class P(FontPage):
        def find_tables(self, table_settings=None):
            return []

    m = derive_body_metrics(FontPDF([P(body + art)]))
    assert m.size == 9.96
    assert 124 in m.margins
    assert not any(300 <= x <= 310 for x in m.margins)


def test_a_different_typography_yields_different_metrics():
    lines = [sized("body prose", 12.0) for _ in range(50)]
    for line in lines:
        line["x0"] = 90.0

    class P(FontPage):
        def find_tables(self, table_settings=None):
            return []

    m = derive_body_metrics(FontPDF([P(lines)]))
    assert m.size == 12.0
    assert m.margins == (90,)
    assert MARGIN_MIN_SHARE == 0.02


def test_the_margin_tolerance_clears_every_measured_footnote():
    """Measured over RM0008 and RM0490: every numbered figure footnote
    sits within 0.3 pt of a body margin, so 1.0 leaves 3x headroom."""
    from rmcontent.noise import MARGIN_TOLERANCE
    m = BodyMetrics(9.96, (124.0,))
    for offset in (0.0, 0.1, 0.2, 0.3):
        assert not m.is_artwork(7.98, 124.0 + offset), offset
    assert MARGIN_TOLERANCE == 1.0


def test_an_artwork_column_just_off_a_margin_is_artwork():
    """RM0008 Figure 201's labels sit at x0 159.3-159.8 against the
    manual's 161 indent; at a 2 pt tolerance they closed the band."""
    m = BodyMetrics(9.96, (67, 124, 145, 161, 162, 163))
    for x0 in (159.3, 159.7, 159.8):
        assert m.is_artwork(8.0, x0), x0


# -- structural grammar (LINE_ORDER_FIGURE_FIX) ------------------------------

from rmcontent.noise import is_structural_body  # noqa: E402


def test_structural_grammar_wins_over_size_and_margin():
    """Each of these is body whatever its typography, so a figure zone
    can never delete one."""
    m = BodyMetrics(9.96, (67.0, 124.0))
    off_margin = 400.0
    for text in (
        "Bits 31:19 Reserved, must be kept at reset value.",
        "Bit 0 CEN: Counter enable",
        "0: Debugger disabled",
        "0b101: divide by 5",
        "0x00: no wait state",
        "Note: The value is undefined after reset.",
        "Caution: Do not write while busy.",
        "Table 26. FLASH register map and reset values",
        "Figure 30. ADC block diagram",
        "4.7.1 FLASH access control register (FLASH_ACR)",
    ):
        assert is_structural_body(text, off_margin, m), text


def test_a_numbered_item_counts_only_at_a_body_margin():
    """A figure's interior `1.` callouts are scattered across the
    drawing; ST prints real footnotes at the body margin."""
    m = BodyMetrics(9.96, (67.0, 124.0))
    assert is_structural_body("1. NBL[1:0] are driven low during read access.", 124.0, m)
    assert not is_structural_body("1. interior callout", 401.3, m)


def test_artwork_labels_do_not_satisfy_the_grammar():
    m = BodyMetrics(9.96, (67.0, 124.0))
    for text in ("CHSEL[22:0]", "VREF+", "TRG0", "BHA", "MSv68740V5",
                 "GPIO Ports Flash memory", "31 24 15 7 0", "AUTOFF"):
        assert not is_structural_body(text, 400.0, m), text


def test_is_figure_artwork_checks_grammar_before_size_and_margin():
    m = BodyMetrics(9.96, (67.0, 124.0))
    # Small and off-margin, but register grammar.
    assert not m.is_figure_artwork("Bits 15:0 BSy: Port x set I/O y", 6.0, 400.0)
    # Small and off-margin, no grammar.
    assert m.is_figure_artwork("CHSEL[22:0]", 6.0, 400.0)
    # Body size, off-margin: not artwork under the two-condition rule.
    assert not m.is_figure_artwork("A body-size label", 9.96, 400.0)
