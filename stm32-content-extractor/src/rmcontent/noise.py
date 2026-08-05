"""The four classes of non-prose line that must not reach `section_content`.

Measured over RM0490 Rev 6's 40,440 body lines:

| class | share | example |
|---|---|---|
| page headers/footers | 2.5% | `77/1023 RM0490 Rev 6`, `RM0490 Contents` |
| bit-layout diagram rows | 1.8% | `31 30 29 28 27 26 25 24 23 22 21 20 19 18 17 16` |
| Contents / List-of pages | (whole pages) | the front matter |
| stray single glyphs | small | a lone `s` under RM0490 4.7.1 |

The bit-layout rows deserve their own filter rather than falling out of
table-bbox exclusion: verified on RM0490 page 77, the two `31 30 ... 16`
/ `15 14 ... 0` header rows are printed *above* the ruled grid (tops
200.6 and 247.7 against grid bboxes starting at 210.5 and 257.5), so they
sit outside every detected bbox and survive that exclusion untouched.

The stray glyph is the tail of a rotated "Bits" label that pdfplumber
resolves to a one-character line of its own; it recurs above every
register layout in the manual.

Every count is reported, not silently applied -- a filter that starts
eating prose should be visible in the run's own numbers.
"""

from __future__ import annotations

import logging
import re
import statistics
from collections import Counter
from dataclasses import dataclass

from rmtables.notes import FOOTER_RE

from .lines import DEFAULT_Y_TOLERANCE, page_lines

logger = logging.getLogger("rmcontent.noise")

# "31 30 29 28 ... 16" -- a run of short numbers and nothing else.
#
# Eight (`{7,}`) is the smallest run a real register bit-layout strip
# ever has, and at that threshold zero of them reach the prose on either
# manual. The threshold is `{5,}` because a shorter run of the same shape
# still occurs, printed INSIDE a figure rather than above a register: a
# bit-position ruler (`19 15 11 7 3 0`, RM0490 16.9 Figure 53), timing
# sequence indices (`0 1 2 3 0 1 2`, RM0486 32.4.30) and memory-bank
# labels (`00 10 20 01 11 21`, RM0486 10.3.2). Those three lines are
# the complete set the loosening removes across RM0486 Rev 4 and RM0490
# Rev 6 -- measured, not assumed -- and none is prose.
#
# Five is the floor, not a preference: a run of four would start to
# reach genuine numeric prose, and real content of this shape is anyway
# protected twice over, since a ruled table's rows are excluded by bbox
# and a value enumeration carries a colon.
BIT_LAYOUT_ROW_RE = re.compile(r"^(\d{1,2}\s+){5,}\d{1,2}$")

# A page's running header is its topmost line and carries the document
# number at one end or the other ("RM0490 Embedded flash memory (FLASH)"
# on odd pages, "Embedded flash memory (FLASH) RM0490" on even ones).
# Requiring BOTH the position and the document token keeps a body line
# that merely cites the manual from being mistaken for furniture.
HEADER_BAND = 0.09  # fraction of page height; content starts below this
FOOTER_BAND = 0.85  # fraction of page height; footers sit below this

# A stray rotated-label remnant: one or two characters, no digit. The
# digit exclusion protects real content like a lone "0" or "1" that is a
# value enumeration's leftover, and short numeric fragments.
STRAY_GLYPH_MAX_LEN = 2

# ST's page fraction, alone on its line: "160/4669". A few of these
# escape the bottom band entirely -- on a LANDSCAPE figure page the
# footer is printed along the side, so its `top` lands mid-page and
# survives into the prose (RM0486 2.1.2, 64.7.3).
#
# The denominator is what makes this safe to apply anywhere on the page.
# Matching a bare `\d{1,4}/\d{2,5}` line by shape would destroy real
# content: RM0486 alone prints `16/32-bit`, `18/24-bit mode (RGB888)`,
# `64/26 = 2.5x = 1.25x * 2, ...` and, inside a bit-timing figure, the
# bare lines `6/16` and `7/16 7/16`. Requiring the denominator to equal
# the manual's own page count -- and the numerator not to exceed it --
# separates a real footer from every one of those.
PAGE_FRACTION_RE = re.compile(r"^(\d{1,5})/(\d{1,5})$")

# The other footer form on its own line, e.g. "RM0486 Rev 4". Neither
# manual leaks one today; it is covered so the filter does not depend on
# which half of the footer a given page happens to print.
DOC_REV_LINE_RE = re.compile(r"^(?:RM|DS|PM|AN|UM)\d{3,4}\s+Rev\s+\d+$")


# -- figure artwork ---------------------------------------------------------
#
# A figure's internal label text lands in the prose with nothing to stop
# it. Tables are excluded by their detected bbox; figures have none, and
# one cannot be built: RM0486 page 159 reports ZERO grids from
# `find_tables`, 0 images, 3 curves and 4 rects, yet carries 1,401
# characters of artwork -- the drawing lives in a form XObject that
# pdfplumber does not decompose, so there is no region to exclude.
#
# Font size separates the two absolutely. Measured across RM0486 pages
# 150-599 (21,109 lines): 2-5 pt is figure artwork (1,561 lines), 6 pt is
# ambiguous (884), 7-8 pt is footnotes/table cells/subscripts (3,466),
# 9-10 pt is body prose (14,559). On the figure pages of RM0486 2.1.2 the
# split is total -- page 160 is 1,552 chars at 2 pt plus 1,266 at 3 pt,
# with only the 51-char caption at 10 pt.
#
# The threshold is a fraction of the document's OWN body size, never a
# hardcoded point value, so a manual set in different typography still
# works. At 0.6 the 6 pt ambiguous band falls on the keep side, which is
# the conservative direction: prose is never sacrificed to remove labels.
ARTWORK_RATIO = 0.6
FONT_SAMPLE_PAGES = 120  # pages sampled, spread evenly, to derive body size
DEFAULT_BODY_FONT = 10.0  # fallback when sampling finds nothing usable
MIN_PLAUSIBLE_BODY_FONT = 6.0  # below this the sample is not believable

# ST's artwork asset ID -- "MSv66119V2", "MS70497V3", "MSc12345b2". It
# is printed as the LAST element of every drawing, which is what gives a
# figure an explicit end to pair with its caption's explicit start; the
# band rule in `sections.py` is built on it.
#
# Generalised, not hardcoded to "MSv": the v/c is optional and either
# case, and internal spaces are tolerated because the id renders rotated
# and kerned inside the artwork, so pdfplumber can split the token
# ("MS v 66119 V2"). Four or more digits, then an optional trailing
# letter and digits. Verified against MSv66119V2, MS70497V3, MSv45319V2,
# MS56979V1.
#
# The optional space sits between EVERY element, not only after the "S":
# the kerning example "MS v 66119 V2" splits after the "v" and after the
# digits too, which a single `\s?` in one position cannot absorb. Each is
# `\s?` rather than `\s*`, so the pattern can never span an arbitrary run
# of words -- "The ROM S 1234 area" stays unmatched.
ASSET_ID = r"\bM\s?S\s?[vcVC]?\s?\d{4,}\s?[A-Za-z]?\s?\d*\b"
ASSET_ID_RE = re.compile(ASSET_ID)

# The line is nothing BUT asset ids -- ST prints two side by side where
# one figure abuts another ("MS54051V1 MS54052V1", RM0486 39.9.2), and
# sometimes gives one a stray trailing period ("MSv53041V1.").
ASSET_ID_LINE_RE = re.compile(rf"^(?:{ASSET_ID}[.,]?\s*)+$")

# The same id concatenated onto the END of a real line -- on a wide
# figure it lands on the legend's own line ("RRA = round-robin
# arbitration, FPA = fixed-priority arbitration MSv66927V1"). Dropping
# the line would take the legend with it, so only the id is stripped.
TRAILING_ASSET_ID_RE = re.compile(rf"(?:\s+{ASSET_ID})+[.,]?\s*$")


# -- rotated running heads --------------------------------------------------
#
# On a landscape figure page ST rotates the running head and footer 90
# degrees, and pdfplumber returns them as word-fragments that land in the
# prose: RM0490 2.1 carried standalone `Memory`, `and`, `bus`,
# `architecture`, `RM0490`. Neither the header rule (it is not the page's
# topmost line) nor the footer band (it runs down the side, not along the
# bottom) reaches them, and at 5.5-6.1 pt they sit above the artwork size
# floor.
#
# `upright` on the char objects separates them cleanly. Verified on
# RM0486 p160, a landscape figure page, every running-head fragment is
# `upright={False: n}` while the figure caption on the same page is
# `upright={True: 44}`; on the portrait body page before it, every single
# line is `upright={True: n}`.
#
# Running prose in an ST manual is never rotated. The two places rotated
# text is legitimate -- a register map's `Res.` field names and figure
# artwork -- are already kept out of the prose by table-bbox exclusion
# and the artwork band, so nothing legitimate is at risk here.
#
# The test is a MAJORITY, not "any": a single rotated glyph inside an
# otherwise upright line must not discard the line.
def is_rotated_line(line: dict) -> bool:
    """Whether this line's characters are predominantly rotated."""
    chars = line.get("chars")
    if not chars:
        return False
    rotated = sum(1 for c in chars if not c.get("upright", True))
    return rotated * 2 > len(chars)


def line_font_size(line: dict) -> float | None:
    """Median char size of a line, or `None` when it carries no chars."""
    chars = line.get("chars")
    if not chars:
        return None
    sizes = [c["size"] for c in chars if c.get("size")]
    return statistics.median(sizes) if sizes else None


def derive_body_font_size(
    pdf,
    sample_pages: int = FONT_SAMPLE_PAGES,
    y_tolerance: float = DEFAULT_Y_TOLERANCE,
) -> float:
    """The document's body text size: the mode of its lines' median char
    sizes, over pages sampled evenly through the whole manual.

    Body prose outnumbers artwork several times over in every ST manual
    (14,559 lines against 1,561 in the RM0486 sample), so the mode lands
    on the body size even when a sampled page is nothing but a diagram.
    """
    total = len(pdf.pages)
    if not total:
        return DEFAULT_BODY_FONT
    step = max(1, total // max(1, sample_pages))
    counts: Counter = Counter()
    for i in range(0, total, step):
        page = pdf.pages[i]
        try:
            for line in page_lines(page, y_tolerance):
                size = line_font_size(line)
                if size:
                    counts[round(size)] += 1
        except Exception:
            logger.debug("font sampling failed on page %d", i + 1, exc_info=True)
        finally:
            page.flush_cache()

    if not counts:
        logger.warning(
            "could not sample any font size; assuming a %.1f pt body",
            DEFAULT_BODY_FONT,
        )
        return DEFAULT_BODY_FONT
    body = float(counts.most_common(1)[0][0])
    if body < MIN_PLAUSIBLE_BODY_FONT:
        logger.warning(
            "derived body font size %.1f pt is implausibly small (sample: %s); "
            "falling back to %.1f pt so artwork filtering cannot eat the prose",
            body, dict(counts.most_common(5)), DEFAULT_BODY_FONT,
        )
        return DEFAULT_BODY_FONT
    return body


def artwork_threshold(body_font_size: float) -> float:
    return body_font_size * ARTWORK_RATIO


def is_asset_id(text: str) -> bool:
    """The whole line is one or more artwork asset ids."""
    return bool(ASSET_ID_LINE_RE.match(text.strip()))


def contains_asset_id(text: str) -> bool:
    """An asset id appears anywhere in the line -- this is what CLOSES an
    artwork band, and the id's own line is dropped with it."""
    return bool(ASSET_ID_RE.search(text))


def strip_trailing_asset_id(text: str) -> str:
    """Remove an artwork asset id tacked onto the end of a real line."""
    return TRAILING_ASSET_ID_RE.sub("", text).rstrip()


def is_artwork_line(line: dict, artwork_max: float) -> bool:
    """Whether this line is too small to be prose -- the size BACKSTOP.

    No longer the primary rule (`markers.ArtworkBand` is), because
    artwork and body text overlap in size and do so differently per
    manual. It is retained because it cannot reach 9.0 pt register prose
    at `0.6 x body`, it is proven on RM0486's 0.83-3.0 pt labels, and it
    catches artwork whose caption was missed entirely -- a case the band
    rule structurally cannot reach.

    Asset ids are deliberately NOT tested here: an id line has to survive
    this filter to reach the band and close it.

    A line with no measurable size is kept: absence of evidence is not
    evidence that it is a diagram label.
    """
    size = line_font_size(line)
    return size is not None and size < artwork_max


@dataclass
class NoiseCounts:
    """Per-class tallies for the run, reported by `--validate`."""

    headers_footers: int = 0
    bit_layout_rows: int = 0
    contents_pages: int = 0  # pages skipped wholesale
    contents_page_lines: int = 0  # lines on those pages
    stray_glyphs: int = 0
    # A captioned region's own caption line and its footnotes: printed
    # outside the bbox, but already represented by the region's marker.
    table_furniture: int = 0
    # Figure label text, identified by font size (see ARTWORK_RATIO).
    figure_artwork: int = 0
    # Running head/footer rotated 90 degrees on a landscape figure page.
    rotated_lines: int = 0

    def total_lines(self) -> int:
        return (
            self.headers_footers
            + self.bit_layout_rows
            + self.contents_page_lines
            + self.stray_glyphs
            + self.table_furniture
            + self.figure_artwork
            + self.rotated_lines
        )

    def summary_lines(self) -> list[str]:
        return [
            f"noise -- page headers/footers: {self.headers_footers}",
            f"noise -- bit-layout diagram rows: {self.bit_layout_rows}",
            f"noise -- Contents/List-of pages: {self.contents_pages} "
            f"({self.contents_page_lines} lines)",
            f"noise -- stray 1-2 char glyphs: {self.stray_glyphs}",
            f"noise -- table captions/footnotes replaced by a marker: "
            f"{self.table_furniture}",
            f"noise -- figure artwork label lines: {self.figure_artwork}",
            f"noise -- rotated running-head fragments: {self.rotated_lines}",
            f"noise -- total lines filtered: {self.total_lines()}",
        ]


def is_bit_layout_row(text: str) -> bool:
    return bool(BIT_LAYOUT_ROW_RE.match(text.strip()))


def is_stray_glyph(text: str) -> bool:
    stripped = text.strip()
    return 0 < len(stripped) <= STRAY_GLYPH_MAX_LEN and not any(c.isdigit() for c in stripped)


def document_header_re(document: str) -> re.Pattern | None:
    """Pattern for this manual's running page header, or `None` when the
    document number is unknown (then only footers are filtered).

    The third alternative -- the line being nothing but the document
    number -- covers a page whose running head carries no chapter title
    beside it, which is how RM0490 p45 leaves a standalone `RM0490` in
    section 2.1. It is only ever tested against the page's topmost line
    inside the header band, so a body line that merely says `RM0490`
    somewhere down the page is untouched.
    """
    if not document:
        return None
    token = re.escape(document)
    return re.compile(rf"^{token}\s+\S|\S\s+{token}\s*$|^{token}\s*$")


class PageFurniture:
    """Decides which of a page's lines are header/footer rather than body.

    Both signals are positional *and* textual. The footer is whatever
    matches `rmtables.notes.FOOTER_RE` in the bottom band; everything at
    or below it goes too, which is what removes ST's bare marginal
    chapter-tab number (`91` under page 77's footer) without a rule of its
    own. The header is the topmost line in the top band when it carries
    the document number.
    """

    def __init__(
        self,
        page_height: float,
        header_re: re.Pattern | None,
        page_count: int = 0,
    ):
        self.header_limit = page_height * HEADER_BAND
        self.footer_limit = page_height * FOOTER_BAND
        self.header_re = header_re
        self.page_count = page_count

    def _is_stray_footer(self, text: str) -> bool:
        """A footer that escaped the bottom band -- see PAGE_FRACTION_RE."""
        if DOC_REV_LINE_RE.match(text):
            return True
        m = PAGE_FRACTION_RE.match(text)
        if not m or not self.page_count:
            return False
        return int(m.group(2)) == self.page_count and int(m.group(1)) <= self.page_count

    def furniture_tops(self, lines: list[dict]) -> set[int]:
        """Indexes of `lines` that are page furniture."""
        drop: set[int] = set()
        if not lines:
            return drop

        first = lines[0]
        if (
            self.header_re is not None
            and first["top"] <= self.header_limit
            and self.header_re.search(first["text"].strip())
        ):
            drop.add(0)

        footer_top: float | None = None
        for line in lines:
            if line["top"] < self.footer_limit:
                continue
            if FOOTER_RE.search(line["text"].strip()):
                footer_top = line["top"] if footer_top is None else min(footer_top, line["top"])
        if footer_top is not None:
            for i, line in enumerate(lines):
                if line["top"] >= footer_top - 0.5:
                    drop.add(i)

        for i, line in enumerate(lines):
            if i not in drop and self._is_stray_footer(line["text"].strip()):
                drop.add(i)
        return drop
