"""Cell text extraction with rotated-text un-reversal, and Symbol-font
special-character remapping (SPECIALCHARS_LEGENDS_TASK.md).

pdfplumber's cell.extract_text() reverses vertical (rotated 90 deg) text
because it walks chars in PDF content-stream order, not reading order. ST's
register-map field names are printed rotated, so this module re-derives
reading order directly from char geometry instead.

Separately: ST uses the `SymbolMT` font for bullets/arrows/comparison
operators, and the PDF carries no ToUnicode CMap for it, so pdfplumber
decodes each glyph as a Private-Use-Area codepoint `U+F0xx = 0xF000 +
<Symbol-font byte>` instead of the real character. `fix_symbols` remaps
those back via the published Adobe Symbol font encoding (verified against
RM0008: U+F0B7 -> "*" (bullet), U+F0E0 -> arrow, U+F0A3 -> "<=", U+F020 ->
space, among others).

CELL_TEXT_ASSEMBLY_FIX.md: upright text is assembled by clustering chars
onto baseline lines by actual vertical proximity and attaching
sub/superscript-sized chars to their nearest line, rather than the old
fixed 2-point `top` band -- a subscript no longer tears its line in two,
and a superscript footnote marker no longer sorts in front of the text it
annotates. See `SMALL_RATIO`/`GAP_RATIO` below.

ROTATED_TEXT_FIX.md: rotated text is assembled per side-by-side run,
clustered by x0 (`_rotated_lines`), rather than sorting every rotated char
in the cell by `-top` as one run -- two side-by-side rotated runs (e.g. a
field name next to its own bit-range suffix) no longer interleave into a
single garbled string.
"""

from __future__ import annotations

import collections
import logging
import re

logger = logging.getLogger(__name__)

LINE_TOLERANCE = 3  # points; chars within this vertical band are one line
BBOX_PAD = 1.0

# CELL_TEXT_ASSEMBLY_FIX.md: the old upright path sorted by a 2-point band of
# `top`, so any char off the baseline (a sub/superscript) landed in its own
# band and was ordered AFTER the whole baseline run -- tearing 't'+'SAR' into
# separate lines, and moving a trailing footnote marker like "(1)" in front
# of the text it annotates. Fixed by explicitly clustering baseline chars
# into lines, then attaching each smaller char to its nearest baseline line
# by vertical distance rather than by sort order.
#
# SMALL_RATIO: a char whose rounded size is below this fraction of the
# cell's dominant font size is treated as a sub/superscript rather than
# baseline text. 0.85 was verified against the real size pairs in the
# corpus (9.0/7.2 = 0.8, 9.96/7.98 = 0.80) while staying well above 1.0 for
# same-size baseline runs, so it never mis-classifies ordinary text as a
# script.
SMALL_RATIO = 0.85
# GAP_RATIO: a horizontal gap between adjacent chars on the SAME line wider
# than this fraction of the dominant font size, with no space glyph between
# them, is a real word/column gap (e.g. "31" ... "24" 102pt apart) rather
# than normal kerning. 0.28 was verified to catch the real corpus gaps
# (all >> 1x the font size) while a same-word kerning gap (a small fraction
# of the font size) never crosses it.
GAP_RATIO = 0.28

# CELL_TEXT_ASSEMBLY_FIX.md, post-implementation review: a gap INSIDE an
# assembled "0x..." hex literal needs a much wider clearance than GAP_RATIO
# before it counts as a real separator. Measured against the actual
# corpus: repeated hex reset-value cells (e.g. "0x41000000", ~275 cells)
# have ordinary inter-glyph kerning that lands at ratio ~0.28-0.29 --
# statistically indistinguishable from genuine letter-adjacent gaps like
# "0MHz"->"0 MHz" (ratio ~0.283) or "Table10"->"Table 10" (ratio ~0.284),
# so GAP_RATIO alone cannot separate them; but the genuine digit-digit gap
# this fix must still catch (the FDCAN "31"..."24" bit-range header, ratio
# ~11) is nearly two orders of magnitude wider. Requiring a full character
# width of clearance keeps that huge, real gap while suppressing the
# hex-literal kerning noise.
#
# Scoped to "inside a 0x-prefixed run" specifically (not "both chars are
# hex digits" in general): a hex LETTER a-f is also an ordinary English
# letter, so "Table10"'s "e"|"1" gap must NOT be widened just because 'e'
# happens to be hex-digit-shaped -- only text that is *itself* accumulating
# as "0x[hexdigits]" so far gets the wider bar.
DIGIT_GAP_RATIO = 1.0
# Matches when the text assembled so far, from the last whitespace, is
# itself a (possibly partial) "0x"-prefixed hex literal.
_HEX_RUN_RE = re.compile(r"0[xX][0-9a-fA-F]*$")

# Adobe Symbol font encoding: low byte (the Symbol-font code, i.e. the PUA
# codepoint minus 0xF000) -> Unicode. Covers the confirmed/high-value subset
# verified against real RM0008 output (bullets, arrows, comparison/math
# operators, degree, plus-minus, the ASCII-identical punctuation block) plus
# a broader set of well-documented entries from the same published table
# (set/logic notation, additional arrows, typographic marks). Deliberately
# does NOT include the Symbol font's Greek-letter block (0x41-0x7A-ish):
# STM32 reference manuals have no confirmed use of it, and guessing at the
# exact byte-to-letter ordering risks silently emitting the *wrong* Greek
# letter, which is worse than leaving it unmapped (dropped, and logged).
SYMBOL: dict[int, str] = {
    # ASCII-identical low block (Symbol font renders these unchanged),
    # except 0x2D (minus) and 0x40 (congruent) which are genuinely special.
    **{b: chr(b) for b in range(0x20, 0x40) if b != 0x2D},
    0x2D: "−",  # minus
    0x40: "≅",  # congruent
    # Confirmed against RM0008 (task spec):
    0xA3: "≤",  # <=
    0xB0: "°",  # degree
    0xB1: "±",  # +/-
    0xB3: "≥",  # >=
    0xB4: "×",  # multiply
    0xB7: "•",  # bullet
    0xB8: "÷",  # divide
    0xB9: "≠",  # not equal
    0xBB: "≈",  # approx equal
    0xAB: "↔",  # left-right arrow
    0xAC: "←",  # left arrow
    0xAD: "↑",  # up arrow
    0xAE: "→",  # right arrow
    0xAF: "↓",  # down arrow
    0xDA: "⇔",  # left-right double arrow
    0xDB: "⇐",  # left double arrow
    0xDC: "⇑",  # up double arrow
    0xDD: "⇒",  # right double arrow
    0xDE: "⇓",  # down double arrow
    0xE0: "→",  # right arrow (observed variant slot, verified in RM0008)
    # Additional published Adobe Symbol entries (math/set notation, marks).
    0xA1: "ϒ",  # Upsilon with hook (rare technical variant)
    0xA2: "′",  # prime (minute)
    0xA4: "⁄",  # fraction slash
    0xA5: "∞",  # infinity
    0xA6: "ƒ",  # florin
    0xA7: "♣",  # club suit
    0xA8: "♦",  # diamond suit
    0xA9: "♥",  # heart suit
    0xAA: "♠",  # spade suit
    0xB2: "″",  # double prime (second)
    0xB5: "∝",  # proportional to
    0xB6: "∂",  # partial differential
    0xBA: "≡",  # identical to / equivalence
    0xBC: "…",  # ellipsis
    0xBF: "↵",  # carriage return arrow
    0xC0: "ℵ",  # aleph
    0xCA: "⊗",  # circled times
    0xCB: "⊕",  # circled plus
    0xCC: "∅",  # empty set
    0xCD: "∩",  # intersection
    0xCE: "∪",  # union
    0xCF: "⊃",  # proper superset
    0xD0: "⊇",  # superset or equal
    0xD1: "⊄",  # not a subset
    0xD2: "⊂",  # proper subset
    0xD3: "⊆",  # subset or equal
    0xD4: "∈",  # element of
    0xD5: "∉",  # not an element of
    0xD6: "∠",  # angle
    0xD7: "∇",  # nabla / gradient
}


def fix_symbols(s: str) -> str:
    """Remap Symbol-font PUA codepoints (`U+F000..U+F0FF`) to their real
    Unicode character via `SYMBOL`. An unmapped PUA codepoint is dropped
    (and logged at DEBUG, so a newly-encountered glyph gets noticed) rather
    than left as garbage. Legitimate Unicode outside that PUA range --
    including real non-ASCII like micro/registered/trademark/multiply/en
    dash/curly quotes/degree -- passes through unchanged."""
    out = []
    for ch in s:
        o = ord(ch)
        if 0xF000 <= o <= 0xF0FF:
            mapped = SYMBOL.get(o - 0xF000)
            if mapped is None:
                logger.debug("unmapped Symbol-font codepoint U+%04X", o)
                continue
            out.append(mapped)
        else:
            out.append(ch)
    return "".join(out)


def _char_text(text: str, fontname: str) -> str:
    """Belt-and-suspenders: if this glyph's font is a Symbol variant and its
    own raw codepoint (not already PUA-shifted) directly matches a Symbol-
    encoding slot, remap it here -- covers a font/subsetting variant where
    the byte isn't relocated into the U+F0xx PUA range. The main path is
    still `fix_symbols` on the joined text, which handles the confirmed
    U+F0xx case."""
    if fontname and len(text) == 1 and "symbol" in fontname.lower():
        o = ord(text)
        if o in SYMBOL and not (0xF000 <= o <= 0xF0FF):
            return SYMBOL[o]
    return text


def _rotated_lines(rotated, bbox, tol=5.0):
    """ROTATED_TEXT_FIX.md: rotated text reads bottom-to-top within one run.
    All chars of a run share an x0; distinct x0 clusters are separate
    side-by-side runs, which must NOT be interleaved -- the old single
    global `-top` sort across the whole cell turned two side-by-side runs
    like 'THREE_ERR_RX' + '[1:0]' into a character-by-character shuffle
    ('THRE[E1_:E0]RR_RX'). Emitted left-to-right, each run as its own line.

    Post-implementation review: the spec's own tol=2.0 turned out too tight.
    A single genuine run that mixes a narrower-glyph footnote marker (e.g.
    "(2)") or letter with the wider main text gets a slightly different x0
    from pdfplumber for the narrower glyphs alone -- measured real cases at
    2.18pt ("CIC order (1)") and 3.64pt ("VBAT mode", "FRS (kHz)") -- close
    enough to false-split a single run into two and reorder it wrong (a
    trailing marker flips to a leading one, or a plain word splits in half).
    Genuine side-by-side runs are never that close: every verified case
    (T100/T585's two header halves, T902's neighbouring bit column) is 10pt+
    apart. tol=5.0 absorbs the measured glyph-width jitter with a wide
    margin while staying well under any real separate-run gap.
    """
    if not rotated:
        return []
    xs = sorted({round(c["x0"], 1) for c in rotated})
    clusters = []
    for x in xs:
        if clusters and x - clusters[-1][-1] <= tol:
            clusters[-1].append(x)
        else:
            clusters.append([x])
    groups = [{"x": min(cl), "chars": [c for c in rotated if round(c["x0"], 1) in set(cl)]}
              for cl in clusters]

    # A run anchored outside the cell belongs to the neighbouring column: the
    # center-point membership above admits it because a rotated glyph's bbox
    # is wider than the ruled column (RM0486 T902's '2254'). Drop it ONLY
    # when a run genuinely inside exists -- a lone run whose x0 sits just
    # outside the ruled edge is this cell's own text and must be kept
    # (RM0486 p1283's 'EXTMOD').
    inside = [g for g in groups if bbox[0] <= g["x"] <= bbox[2]]
    if inside and len(inside) < len(groups):
        groups = inside

    return ["".join(_char_text(c["text"], c.get("fontname", ""))
                    for c in sorted(g["chars"], key=lambda c: -c["top"]))
            for g in sorted(groups, key=lambda g: g["x"])]


def cell_text(page_chars, bbox) -> str:
    """Return the text inside `bbox`, handling upright and rotated chars.

    Upright chars are clustered into baseline lines by actual vertical
    proximity (CELL_TEXT_ASSEMBLY_FIX.md), not by rounding `top` into a
    fixed band: each smaller (sub/superscript-sized) char is then attached
    to its nearest baseline line, so a subscript stays glued to the line it
    visually belongs to and a superscript footnote marker stays a suffix
    instead of sorting before the text it annotates. Within a line, chars
    are joined left-to-right with a space inserted wherever a horizontal
    gap is wide enough to be a real word/column gap rather than kerning.
    Rotated chars (upright is False) are read bottom-to-top on the page --
    sorting by descending `top` restores the correct character order (e.g.
    ".seR" -> "Res."). ROTATED_TEXT_FIX.md: a cell can hold more than one
    such run side by side (e.g. a bit-range suffix next to a field name) --
    `_rotated_lines` clusters rotated chars by x0 first and orders each
    cluster's own bottom-to-top run independently, rather than sorting
    every rotated char in the cell as one interleaved run.

    Membership is decided by the char's *center* point, not full bbox
    containment: pdfplumber's bbox for a rotated glyph in a narrow
    single-digit column (e.g. a "31..0" bit header) can overflow the ruled
    cell width by a point or so, and full containment silently drops it.
    """
    x0, top, x1, bottom = bbox
    chars = [
        c
        for c in page_chars
        if x0 - BBOX_PAD <= (c["x0"] + c["x1"]) / 2 <= x1 + BBOX_PAD
        and top - BBOX_PAD <= (c["top"] + c["bottom"]) / 2 <= bottom + BBOX_PAD
    ]
    if not chars:
        return ""

    upright = [c for c in chars if c.get("upright", True)]
    rotated = [c for c in chars if not c.get("upright", True)]

    parts = []
    if upright:
        sizes = collections.Counter(round(c["size"], 1) for c in upright)
        dom = max(sizes.items(), key=lambda kv: (kv[1], kv[0]))[0]  # dominant font size
        main = [c for c in upright if round(c["size"], 1) >= SMALL_RATIO * dom]
        small = [c for c in upright if round(c["size"], 1) < SMALL_RATIO * dom]
        if not main:  # an all-small cell is its own baseline
            main, small = upright, []

        lines = []  # cluster the baseline chars into lines
        for c in sorted(main, key=lambda c: (c["top"], c["x0"])):
            for L in lines:
                if abs(c["top"] - L["top"]) <= LINE_TOLERANCE:
                    L["chars"].append(c)
                    break
            else:
                lines.append({"top": c["top"], "chars": [c]})

        for c in small:  # attach each script to its nearest baseline line
            if lines:
                L = min(lines, key=lambda L: abs(c["top"] - L["top"]))
                if abs(c["top"] - L["top"]) <= dom:
                    L["chars"].append(c)
                    continue
            lines.append({"top": c["top"], "chars": [c]})

        for L in sorted(lines, key=lambda L: L["top"]):  # order by x, insert gap-spaces
            out, prev = [], None
            for c in sorted(L["chars"], key=lambda c: c["x0"]):
                if prev is not None and not out[-1].isspace() and c["text"] != " ":
                    # A gap while still inside an assembled "0x..." hex
                    # literal needs DIGIT_GAP_RATIO's much wider clearance
                    # (kerning noise inside the literal vs. the genuine, far
                    # wider FDCAN bit-range gap) -- every other adjacency,
                    # including ordinary words that happen to be built only
                    # from hex-digit-shaped letters (e.g. "Table"'s "e"),
                    # uses GAP_RATIO as before.
                    last_word = re.split(r"\s", "".join(out))[-1]
                    in_hex_run = bool(_HEX_RUN_RE.match(last_word))
                    ratio = DIGIT_GAP_RATIO if in_hex_run else GAP_RATIO
                    if c["x0"] - prev["x1"] > ratio * dom:
                        out.append(" ")
                out.append(_char_text(c["text"], c.get("fontname", "")))
                prev = c
            parts.append("".join(out))

    if rotated:
        parts.extend(_rotated_lines(rotated, bbox))

    return fix_symbols("\n".join(p for p in parts if p.strip()))
