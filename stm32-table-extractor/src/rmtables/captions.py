"""Caption detection on content pages, and List-of-tables ground truth parsing."""

from __future__ import annotations

import re

from .model import Caption

# CAPTION_ROBUSTNESS_FIX.md -- verified on RM0477 (STM32H7, 3764 pages):
# dense ST layouts sometimes render a caption with intra-word spaces
# splitting "Table" ("T able 688.", "Ta b le 346." -- even several splits
# at once), a short stray leading fragment before it ("7 Table 29.",
# "t Table 202."), or no space before the table number ("Table332."). This
# tolerant "Table" pattern absorbs all three: `\s?` at each letter boundary
# absorbs any single split (or several simultaneously), and `\s*` before
# the number absorbs the missing-space case. `․` (ONE DOT LEADER) is
# an occasional Unicode stand-in for a literal period in dot-leader-heavy
# rendering. This also subsumes the older, narrower tolerance for a single
# leading punctuation glyph (". Table 179.") and one internal space
# ("Tabl e 45.").
TABLE_WORD_RE = r"T\s?a\s?b\s?l\s?e"
CAPTION_SEARCH_BOUND = 12  # only a short leading fragment is tolerated

# The table NUMBER itself can suffer the same intra-token space-splitting as
# "Table" (e.g. "Table 7 6 . Programmable data width..." for table 76) --
# `_parse_number` strips the whitespace back out before int().
NUMBER_RE = r"\d(?:\s?\d)*"


def _parse_number(raw: str) -> int:
    return int(re.sub(r"\s", "", raw))


# Requires a period (or one-dot-leader) + at least one non-space character
# following it (possibly with no space in between, e.g. "Table 106.Programmed
# HPDMA1 trigger" -- the space-before-number artifact's mirror image, missing
# *after* the period instead) -- this is what keeps a bare prose cross-reference
# ("Refer to Table 332.", nothing follows) from being mistaken for the real
# caption: with nothing after the period, `\s*\S` can't match at all. A
# stricter check requiring the title to *start* capitalized was tried, but real
# ST captions occasionally start lowercase (e.g. "bxCAN register map and reset
# values", "table wake-up source selection") and that rejected genuine
# captions. A cross-reference elsewhere on the page that happens to have a real
# title-like continuation can still match here, but `assign_caption` always
# prefers whichever candidate sits nearer above the table, so the real caption
# (printed right above its table) wins.
CAPTION_RE = re.compile(TABLE_WORD_RE + r"\s*(" + NUMBER_RE + r")\s*[.․]\s*(\S.*)")

# Max gap (points) allowed when a caption sits just below its table -- ST
# almost always prints the caption above, but layout jitter can put it a few
# points below. Beyond this, treat the table as uncaptioned rather than
# risk attaching a distant, unrelated caption (e.g. a Figure's stray ruled
# box picking up the next real Table N. caption several inches down the page).
CAPTION_BELOW_TOLERANCE = 20
CONTINUED_RE = re.compile(r"\(continued\)\s*$", re.IGNORECASE)

# "Table 1. Peripherals or functions versus products . . . . . . . . 42"
# -- the same tolerant "Table"/number matching as CAPTION_RE (a split word,
# a stray leading fragment, or a missing space before the number can all
# appear in the List-of-tables front matter too), plus the pre-existing
# spaced-dot-leader-run-then-page-number ending.
LIST_ENTRY_RE = re.compile(
    TABLE_WORD_RE + r"\s*(" + NUMBER_RE + r")\s*[.․]\s*(.*?)\s*(?:\.\s*){2,}\s*(\d+)\s*$"
)


# FIGURE_BLEED_FIX.md: the same intra-word-space / missing-space / dot-leader
# tolerance TABLE_WORD_RE/NUMBER_RE already give "Table" captions (verified
# necessary on RM0477) applies equally to "Figure" -- a split "F igure" or
# "Figu re", or a spaced-out number, would otherwise slip past this pattern
# exactly as the old strict `Table\s+(\d+)` once did. `nearest_figure_caption`
# below shares this regex (debug-only logging), so it benefits for free.
#
# No trailing `$` (matching CAPTION_RE's own lack of one): a ruled TABLE
# CELL's text -- unlike a single `extract_text_lines()` line, which is what
# this regex was originally written against -- can hold an embedded figure
# rendered as one multi-line blob, e.g. "Figure 17. FLASH stateful
# initialization\nPower-on reset\n...". `.` never matches `\n`, so an
# anchored `(.*)$` would demand reaching the literal end of that whole
# blob and fail on every such row (verified: RM0477 Table 31/782 both
# silently kept their embedded figure caption row until this was found).
FIGURE_WORD_RE = r"F\s?i\s?g\s?u\s?r\s?e"
FIGURE_CAPTION_RE = re.compile(
    r"^\s*[.:•]?\s*" + FIGURE_WORD_RE + r"\s*(" + NUMBER_RE + r")\s*[.․]\s*(.*)"
)


def find_embedded_figure_row(rows: list) -> tuple[int, str] | None:
    """FIGURE_BLEED_FIX.md §A: index of (and figure number at) the
    first row whose FIRST NON-EMPTY cell is a `Figure N.` caption -- i.e. a
    figure that bled into a captioned table's ruled grid, printed directly
    beneath it with little/no gap (verified: RM0490 T43, RM0522 T160/T210).

    Only the row's first non-empty cell is checked, and the match must
    start a LINE of it (`.match`, not `.search`), so a prose cross-reference
    like "see Figure 21." sitting inside a real data cell elsewhere in the
    row can never trigger a cut -- all verified cases print the embedded
    caption alone in column 0 with every other cell in that row empty.

    FIGURE_REMNANT_FIX.md: RM0486 T187's row holds a paragraph fragment,
    then a newline, then the caption ("that Attribute memory space access
    timings are similar.\nFigure 179. NAND flash controller waveforms...")
    -- matching only the cell's start missed it. Splitting on `\n` and
    testing each line catches the caption wherever it lands, while still
    requiring it to start that line (a "see Figure 21." reference embedded
    mid-line still fails every line's `.match`).
    """
    for i, row in enumerate(rows):
        first_cell = next((cell for cell in row if cell and cell.strip()), None)
        if first_cell is None:
            continue
        for line in str(first_cell).split("\n"):
            m = FIGURE_CAPTION_RE.match(line)
            if m:
                return i, str(_parse_number(m.group(1)))
    return None


def find_captions(lines, page_number: int) -> list[Caption]:
    """Return every `Table N. <caption>` line found on a content page.

    `lines` is `page.extract_text_lines()`, passed in so callers that also
    need headings/registers from the same page don't re-extract it.
    """
    captions = []
    for line in lines:
        text = line["text"]
        m = CAPTION_RE.search(text)
        if not m or m.start() > CAPTION_SEARCH_BOUND:
            continue
        number = _parse_number(m.group(1))
        raw_caption = m.group(2)
        continued = bool(CONTINUED_RE.search(raw_caption))
        caption_text = CONTINUED_RE.sub("", raw_caption).strip().rstrip(".").strip()
        if not caption_text:
            # A body-text cross-reference like "See Table 24." can land at
            # the start of a wrapped line and match the caption pattern with
            # nothing after the period. A real caption always has a
            # description; reject the match rather than let it hijack
            # caption assignment for a nearby, unrelated grid.
            continue
        captions.append(
            Caption(
                number=number,
                text=caption_text,
                continued=continued,
                top=line["top"],
                page=page_number,
            )
        )
    return captions


def assign_caption(table_bbox, captions: list[Caption]) -> Caption | None:
    """Pick the caption whose top is the greatest value still <= the table's top.

    Falls back to the nearest caption below only if it is within
    `CAPTION_BELOW_TOLERANCE` points (rare: minor layout jitter). Beyond that,
    the table is left uncaptioned -- a caption far below is almost always a
    real Table caption for unrelated content further down the page, not a
    match for this grid.
    """
    if not captions:
        return None
    table_top = table_bbox[1]
    above = [c for c in captions if c.top <= table_top]
    if above:
        return max(above, key=lambda c: c.top)
    nearest_below = min(captions, key=lambda c: c.top - table_top)
    if nearest_below.top - table_top <= CAPTION_BELOW_TOLERANCE:
        return nearest_below
    return None


def figure_caption_tops(lines) -> list[float]:
    """FIGURE_CAPTION_BOUNDARY_FIX.md: the `top` of every `Figure N.` caption
    line on this page -- positional evidence ST prints on the page itself.
    A grid whose assigned Table caption is separated from it by one of
    these tops does not belong to that table: a genuine continuation of
    Table N never has a figure caption printed between Table N's own
    caption and the continuation grid (ST reprints `Table N. ...
    (continued)` below the figure instead, which `assign_caption` then
    picks as the nearer caption). Reuses `FIGURE_CAPTION_RE` unchanged --
    same tolerant `FIGURE_WORD_RE`/`NUMBER_RE` matching `find_embedded_figure_row`
    already relies on."""
    return [line["top"] for line in lines if FIGURE_CAPTION_RE.match(line["text"])]


def nearest_figure_caption(lines, before_top: float) -> str | None:
    """Nearest `Figure N. <caption>` line above `before_top`, if any.

    Used only to explain why an uncaptioned grid was dropped as a figure
    fragment -- not part of the caption_table assignment path.
    """
    best_top, best_text = None, None
    for line in lines:
        if line["top"] >= before_top:
            continue
        m = FIGURE_CAPTION_RE.match(line["text"])
        if m and (best_top is None or line["top"] > best_top):
            best_top, best_text = line["top"], m.group(2).strip()
    return best_text


def parse_list_of_tables(pdf) -> dict[int, tuple[str, int]]:
    """Parse the front-matter "List of tables" section into {number: (caption, page)}.

    Detected by pages whose text contains the heading "List of tables"; this
    is robust to the exact page range varying between manual revisions.
    """
    entries: dict[int, tuple[str, int]] = {}
    in_section = False
    for page in pdf.pages:
        text = page.extract_text() or ""
        is_list_page = "List of tables" in text
        if is_list_page:
            in_section = True
        elif in_section and "List of tables" not in text:
            # First non-list page after the section ends: stop scanning.
            break
        if not is_list_page:
            continue
        for line in page.extract_text_lines():
            m = LIST_ENTRY_RE.search(line["text"])
            if not m or m.start() > CAPTION_SEARCH_BOUND:
                continue
            number = _parse_number(m.group(1))
            caption = m.group(2).strip()
            target_page = int(m.group(3))
            entries[number] = (caption, target_page)
    return entries
