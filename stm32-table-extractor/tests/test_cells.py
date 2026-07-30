import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from rmtables.cells import cell_text


def _char(text, x0, top, upright=True, width=6.0, height=8.0, size=9.0, fontname=""):
    return {
        "text": text,
        "x0": x0,
        "x1": x0 + width,
        "top": top,
        "bottom": top + height,
        "upright": upright,
        "size": size,
        "fontname": fontname,
    }


def test_upright_single_line():
    chars = [_char(ch, i * 6, 10) for i, ch in enumerate("Offset")]
    assert cell_text(chars, (0, 9, 100, 20)) == "Offset"


def test_rotated_text_unreverses_res():
    # Rotated glyphs read bottom-to-top on the page: the first letter of
    # "Res." sits at the largest `top` (lowest on the page), the last
    # letter at the smallest `top`. Sorting by descending top restores it.
    label = "Res."
    chars = [
        _char(ch, x0=5, top=40 - i * 8, upright=False)
        for i, ch in enumerate(label)
    ]
    assert cell_text(chars, (0, 0, 20, 50)) == "Res."


def test_rotated_text_unreverses_dbg_swen():
    label = "DBG_SWEN"
    chars = [
        _char(ch, x0=5, top=(len(label) - i) * 8, upright=False)
        for i, ch in enumerate(label)
    ]
    assert cell_text(chars, (0, 0, 20, 200)) == label


def test_bit_header_reads_descending():
    header_cells = []
    for i, bit in enumerate(range(31, -1, -1)):
        header_cells.append(str(bit))
    # Simulate 32 upright cells side by side, each its own bbox.
    for i, bit in enumerate(range(31, -1, -1)):
        chars = [_char(c, x0=0, top=10) for c in str(bit)]
        assert cell_text(chars, (0, 9, 20, 20)) == str(bit)


def test_multiline_upright_cell_joins_with_newline():
    line1 = [_char(ch, i * 6, 10) for i, ch in enumerate("LATENCY")]
    line2 = [_char(ch, i * 6, 20) for i, ch in enumerate("[2:0]")]
    chars = line1 + line2
    assert cell_text(chars, (0, 9, 100, 30)) == "LATENCY\n[2:0]"


def test_empty_cell_returns_empty_string():
    assert cell_text([], (0, 0, 10, 10)) == ""


# --------------------------------------------------------- CELL_TEXT_ASSEMBLY_FIX.md

def test_two_line_cell_with_subscript_on_each_line():
    # Mirrors RM0490 p306 T72's header cell: two visual baseline lines
    # ("t" and "(f cycles)"), each with its own subscript sitting just
    # below its baseline ("SAR" under "t"; "ADC" under "f").
    chars = [
        # line 1 baseline: "t"
        _char("t", x0=0, top=0, width=5, size=9.0),
        # subscript "SAR" glued to line 1
        _char("S", x0=5, top=4, width=5, size=7.2),
        _char("A", x0=10, top=4, width=5, size=7.2),
        _char("R", x0=15, top=4, width=5, size=7.2),
        # line 2 baseline: "(" "f" ... " cycles)"
        _char("(", x0=0, top=14, width=5, size=9.0),
        _char("f", x0=5, top=14, width=5, size=9.0),
        _char(" ", x0=25, top=14, width=5, size=9.0),
        _char("c", x0=30, top=14, width=5, size=9.0),
        _char("y", x0=35, top=14, width=5, size=9.0),
        _char("c", x0=40, top=14, width=5, size=9.0),
        _char("l", x0=45, top=14, width=5, size=9.0),
        _char("e", x0=50, top=14, width=5, size=9.0),
        _char("s", x0=55, top=14, width=5, size=9.0),
        _char(")", x0=60, top=14, width=5, size=9.0),
        # subscript "ADC" glued to line 2, sitting between "f" and " cycles)"
        _char("A", x0=10, top=18, width=5, size=7.2),
        _char("D", x0=15, top=18, width=5, size=7.2),
        _char("C", x0=20, top=18, width=5, size=7.2),
    ]
    assert cell_text(chars, (0, 0, 70, 25)) == "tSAR\n(fADC cycles)"


def test_superscript_marker_is_a_suffix_not_a_prefix():
    # A superscript sits ABOVE the baseline (smaller top) -- it must still
    # end up as a trailing suffix, not sort in front of the text.
    baseline = "I2C features"
    chars = [_char(ch, x0=i * 5, top=10, width=5, size=9.0) for i, ch in enumerate(baseline)]
    marker_x0 = len(baseline) * 5
    chars += [
        _char("(", x0=marker_x0, top=6, width=3, size=6.0),
        _char("1", x0=marker_x0 + 3, top=6, width=3, size=6.0),
        _char(")", x0=marker_x0 + 6, top=6, width=3, size=6.0),
    ]
    assert cell_text(chars, (0, 0, 100, 20)) == "I2C features(1)"


def test_wide_gap_with_no_space_glyph_inserts_one_space():
    # RM0486 p3671 T708 header cell: "31" then a 102.5pt gap then "24",
    # with no space glyph -- must render "31 24", not "3124".
    chars = [
        _char("3", x0=148.68, top=10, width=5.0, size=9.0),
        _char("1", x0=153.66, top=10, width=5.0, size=9.0),
        _char("2", x0=261.18, top=10, width=5.0, size=9.0),
        _char("4", x0=266.16, top=10, width=5.0, size=9.0),
    ]
    assert cell_text(chars, (140, 0, 280, 20)) == "31 24"


def test_normal_single_line_cell_with_real_spaces_is_byte_identical():
    # Golden test: a genuine space glyph must be preserved exactly (not
    # doubled, not dropped), and no spurious gap-space inserted around it.
    text = "Offset Register"
    chars = [_char(ch, x0=i * 6.0, top=10, width=6.0, size=9.0) for i, ch in enumerate(text)]
    assert cell_text(chars, (0, 9, 200, 20)) == text


def test_all_small_cell_is_still_emitted_not_dropped():
    # A cell whose every char is below SMALL_RATIO * dom relative to...
    # itself has no larger baseline to compare against -- it must become
    # its own baseline rather than being silently dropped.
    chars = [_char(ch, x0=i * 4.0, top=10, width=4.0, size=6.0) for i, ch in enumerate("(1)")]
    assert cell_text(chars, (0, 9, 20, 20)) == "(1)"


def test_kerning_sized_gap_inserts_no_space():
    # A gap smaller than GAP_RATIO * dom is ordinary kerning, not a real
    # word/column gap -- it must NOT trigger a space insertion.
    chars = [
        _char("A", x0=0, top=10, width=5.0, size=9.0),
        _char("B", x0=5.5, top=10, width=5.0, size=9.0),  # 0.5pt gap, well under 0.28*9.0=2.52
    ]
    assert cell_text(chars, (0, 9, 20, 20)) == "AB"


def test_rotated_field_name_cell_unchanged_by_the_fix():
    label = "Res."
    chars = [
        _char(ch, x0=5, top=40 - i * 8, upright=False)
        for i, ch in enumerate(label)
    ]
    assert cell_text(chars, (0, 0, 20, 50)) == "Res."


def test_hex_literal_kerning_gap_inside_prefix_inserts_no_space():
    # Post-review refinement: a gap WHILE STILL INSIDE an assembled
    # "0x..." hex literal needs DIGIT_GAP_RATIO's much wider clearance,
    # not GAP_RATIO's, since ordinary kerning inside the literal (e.g.
    # "0x41000000") lands at the same ratio as genuine letter-adjacent
    # gaps like "0MHz"->"0 MHz". This 2.7pt gap at size 9.0 (ratio 0.30)
    # is ABOVE GAP_RATIO (0.28) -- proving the hex-run rule, not just a
    # small gap, is what suppresses it -- but well below DIGIT_GAP_RATIO (1.0).
    chars = [
        _char("0", x0=0, top=10, width=5.0, size=9.0),
        _char("x", x0=5.0, top=10, width=5.0, size=9.0),
        _char("4", x0=10.0, top=10, width=5.0, size=9.0),
        _char("1", x0=15.0, top=10, width=5.0, size=9.0),
        _char("0", x0=20.0, top=10, width=5.0, size=9.0),
        _char("0", x0=25.0, top=10, width=5.0, size=9.0),
        _char("0", x0=32.7, top=10, width=5.0, size=9.0),  # 2.7pt gap from prior x1=30.0
        _char("0", x0=37.7, top=10, width=5.0, size=9.0),
        _char("0", x0=42.7, top=10, width=5.0, size=9.0),
        _char("0", x0=47.7, top=10, width=5.0, size=9.0),
    ]
    assert cell_text(chars, (0, 9, 60, 20)) == "0x41000000"


def test_hex_literal_kerning_gap_between_hex_letters_inserts_no_space():
    # The residual case a pure digit-digit check misses: the gap sits
    # between two HEX LETTERS ("F"|"F" inside "0xE00FF00C"), not decimal
    # digits. `.isdigit()` alone would not catch this; the hex-run check
    # must, since 'F' is a valid hex digit character.
    chars = [
        _char("0", x0=0, top=10, width=5.0, size=9.0),
        _char("x", x0=5.0, top=10, width=5.0, size=9.0),
        _char("E", x0=10.0, top=10, width=5.0, size=9.0),
        _char("0", x0=15.0, top=10, width=5.0, size=9.0),
        _char("0", x0=20.0, top=10, width=5.0, size=9.0),
        _char("F", x0=25.0, top=10, width=5.0, size=9.0),
        _char("F", x0=27.54, top=10, width=5.0, size=9.0),  # 2.54pt gap, ratio 0.28 (real RM0522 case)
        _char("0", x0=32.54, top=10, width=5.0, size=9.0),
        _char("0", x0=37.54, top=10, width=5.0, size=9.0),
        _char("C", x0=42.54, top=10, width=5.0, size=9.0),
    ]
    assert cell_text(chars, (0, 9, 60, 20)) == "0xE00FF00C"


def test_word_built_only_from_hex_letters_still_gets_a_real_gap_space():
    # "Table"'s trailing "e" is itself a valid hex-digit character, but the
    # word "Table" is NOT an assembled "0x..." run -- the gap before "10"
    # must still use the normal GAP_RATIO and get a space, matching the
    # real RM0522 "Table10"->"Table 10" case (ratio ~0.284).
    chars = [_char(ch, x0=i * 5.0, top=10, width=5.0, size=9.0) for i, ch in enumerate("Tabl")]
    chars.append(_char("e", x0=4 * 5.0, top=10, width=5.0, size=9.0))
    chars.append(_char("1", x0=5 * 5.0 + 2.56, top=10, width=5.0, size=9.0))  # 2.56pt gap
    chars.append(_char("0", x0=6 * 5.0 + 2.56, top=10, width=5.0, size=9.0))
    assert cell_text(chars, (0, 9, 60, 20)) == "Table 10"


# ----------------------------------------------------------- ROTATED_TEXT_FIX.md

def test_two_side_by_side_rotated_runs_produce_separate_lines_not_interleaved():
    # RM0490 T168: a field name ("THREE_ERR_RX") and its bit-range suffix
    # ("[1:0]") are printed as two separate rotated runs side by side. The
    # old single `-top` sort across the whole cell interleaved them
    # character by character into "THRE[E1_:E0]RR_RX".
    name = "THREE_ERR_RX"
    suffix = "[1:0]"
    chars = [
        _char(ch, x0=10.0, top=(len(name) - i) * 8, upright=False)
        for i, ch in enumerate(name)
    ]
    chars += [
        _char(ch, x0=20.0, top=(len(suffix) - i) * 8, upright=False)
        for i, ch in enumerate(suffix)
    ]
    assert cell_text(chars, (0, 0, 30, 200)) == "THREE_ERR_RX\n[1:0]"


def test_lone_rotated_run_anchored_just_outside_ruled_edge_is_kept():
    # RM0486 p1283's "EXTMOD": a single run whose x0 sits just outside the
    # ruled column edge is this cell's own text, not a neighbour's bleed-in
    # -- it must be kept. Anchor-based membership alone regressed this case
    # (dropped it entirely); dropping only happens when ANOTHER run is
    # genuinely inside, which isn't true here (only one run exists at all).
    label = "EXTMOD"
    chars = [
        _char(ch, x0=8.0, top=(len(label) - i) * 8, upright=False, width=9.0)
        for i, ch in enumerate(label)
    ]
    # bbox's left edge (10.0) sits to the right of the run's x0 (8.0) -- the
    # glyph's wide rotated bbox still passes center-point membership into
    # `chars` (center 12.5 falls within [9.0, 31.0]), but the run itself is
    # anchored outside [bbox[0], bbox[2]].
    assert cell_text(chars, (10.0, 0, 30.0, 200)) == label


def test_foreign_rotated_run_outside_cell_is_dropped_when_a_genuine_run_is_inside():
    # RM0486 T902 (DBGMCU register map): the bit-24 column's cell also
    # admits rotated chars from the neighbouring bit-25 column because a
    # rotated glyph's bbox is wider than the ruled column. Sorting all four
    # chars by `-top` as one run produced "2254" instead of "24". The
    # foreign cluster (anchored outside the cell) must be dropped now that
    # a genuine cluster (anchored inside) exists.
    bbox = (240.7, 650.0, 254.7, 668.0)
    genuine = [
        _char("2", x0=247.24, top=661.96, upright=False, width=9.0),
        _char("4", x0=247.24, top=656.98, upright=False, width=9.0),
    ]
    foreign = [
        _char("2", x0=235.84, top=661.96, upright=False, width=9.0),
        _char("5", x0=235.84, top=656.98, upright=False, width=9.0),
    ]
    assert cell_text(genuine + foreign, bbox) == "24"


def test_glyph_width_jitter_within_one_run_does_not_split_it():
    # Post-implementation review: RM0486 T055's "VBAT mode" is ONE genuine
    # rotated run, but "BAT"'s glyphs are narrower (width 7.2) than "V" and
    # " mode"'s (width 9.0), so pdfplumber reports "BAT" at a slightly
    # different x0 (513.38 vs 509.74, a 3.64pt gap) purely from the width
    # difference -- tol=2.0 mis-split this into two runs and reordered them
    # ("V mode\nBAT"); tol=5.0 absorbs the jitter and keeps the one true
    # reading order intact.
    chars = [
        _char("V", x0=509.74, top=182.82, upright=False, width=9.0),
        _char("B", x0=513.38, top=177.62, upright=False, width=7.2),
        _char("A", x0=513.38, top=172.40, upright=False, width=7.2),
        _char("T", x0=513.38, top=168.58, upright=False, width=7.2),
        _char(" ", x0=509.74, top=166.04, upright=False, width=9.0),
        _char("m", x0=509.74, top=158.08, upright=False, width=9.0),
        _char("o", x0=509.74, top=152.60, upright=False, width=9.0),
        _char("d", x0=509.74, top=147.13, upright=False, width=9.0),
        _char("e", x0=509.74, top=142.15, upright=False, width=9.0),
    ]
    assert cell_text(chars, (505.0, 140.0, 530.0, 190.0)) == "VBAT mode"


def test_glyph_width_jitter_does_not_flip_a_footnote_marker_to_a_prefix():
    # RM0486 T290's "CIC order (1)": the parenthesised marker's glyphs are
    # narrower than "CIC order"'s, landing at x0=165.20 vs 167.38 (a 2.18pt
    # gap, real measured PDF coordinates) -- just enough to clear tol=2.0
    # and get reordered in front of the text it annotates, the exact class
    # of bug CELL_TEXT_ASSEMBLY_FIX.md fixed for upright text. tol=5.0 keeps
    # it a single run read bottom-to-top, marker last (a suffix), not first.
    chars = [
        _char("C", x0=167.38, top=201.58, upright=False, width=9.0),
        _char("I", x0=167.38, top=199.09, upright=False, width=9.0),
        _char("C", x0=167.38, top=192.61, upright=False, width=9.0),
        _char("o", x0=167.38, top=184.60, upright=False, width=9.0),
        _char("r", x0=167.38, top=181.12, upright=False, width=9.0),
        _char("d", x0=167.38, top=175.63, upright=False, width=9.0),
        _char("e", x0=167.38, top=170.64, upright=False, width=9.0),
        _char("r", x0=167.38, top=167.16, upright=False, width=9.0),
        _char("(", x0=165.20, top=162.24, upright=False, width=7.2),
        _char("1", x0=165.20, top=158.23, upright=False, width=7.2),
        _char(")", x0=165.20, top=155.82, upright=False, width=7.2),
    ]
    result = cell_text(chars, (160.0, 140.0, 180.0, 210.0))
    assert result.endswith("(1)")
    assert not result.startswith("(1)")
    assert result == "CICorder(1)"


def test_three_rotated_runs_in_one_cell_produce_three_lines_in_x_order():
    # RM0486's "MCAWINS[D[B1:1:0]0]" case: three side-by-side runs
    # ("CAS[1:0]", "NB", "MWID[1:0]") must each become their own line,
    # ordered left-to-right by x, not interleaved two (or three) at a time.
    runs = [(10.0, "CAS[1:0]"), (20.0, "NB"), (30.0, "MWID[1:0]")]
    chars = []
    for x0, label in runs:
        chars += [
            _char(ch, x0=x0, top=(len(label) - i) * 8, upright=False)
            for i, ch in enumerate(label)
        ]
    assert cell_text(chars, (0, 0, 40, 200)) == "CAS[1:0]\nNB\nMWID[1:0]"


def test_normal_single_run_rotated_cell_is_byte_identical_to_today():
    # Golden test: a cell with exactly one rotated run must be unaffected by
    # the multi-run clustering -- same result as the pre-fix single sort.
    label = "DBG_SWEN"
    chars = [
        _char(ch, x0=5, top=(len(label) - i) * 8, upright=False)
        for i, ch in enumerate(label)
    ]
    assert cell_text(chars, (0, 0, 20, 200)) == label


def test_res_unreversal_still_works_after_rotated_run_clustering():
    label = "Res."
    chars = [
        _char(ch, x0=5, top=40 - i * 8, upright=False)
        for i, ch in enumerate(label)
    ]
    assert cell_text(chars, (0, 0, 20, 50)) == "Res."


def test_hex_literal_wide_digit_gap_still_inserts_space():
    # A genuinely wide gap inside a hex/decimal run (the FDCAN "31"..."24"
    # bit-range case) must still get a space -- DIGIT_GAP_RATIO only
    # raises the bar, it doesn't disable gap detection entirely. This one
    # isn't even inside a "0x" run, so it uses plain GAP_RATIO -- and at
    # ratio ~11 it clears either threshold easily.
    chars = [
        _char("3", x0=148.68, top=10, width=5.0, size=9.0),
        _char("1", x0=153.66, top=10, width=5.0, size=9.0),
        _char("2", x0=261.18, top=10, width=5.0, size=9.0),
        _char("4", x0=266.16, top=10, width=5.0, size=9.0),
    ]
    assert cell_text(chars, (140, 0, 280, 20)) == "31 24"
