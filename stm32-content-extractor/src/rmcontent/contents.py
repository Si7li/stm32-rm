"""The manual's own Contents pages, parsed into ground truth.

This is the exact analog of `rmtables.captions.parse_list_of_tables`: the
front matter lists every numbered heading ST believes the document has, so
reconciling the extracted sections against it is a real correctness check
rather than a self-consistency one.

Three rendering defects break a naive entry pattern. The first two are
what the List-of-tables parser learned the hard way, and are built in
here from the start rather than after the fact; the third only shows up
on a manual large enough to reach it.

1. **A wrapped entry has no trailing page number on its first line.**
   Measured on RM0490 Rev 6: a parser that requires `<dots><page>` at the
   end of the entry line finds 835 sections against 903 real headings. The
   61-entry shortfall is entirely entries whose title is too long for one
   Contents line, so the dot leaders and the page number land on the
   *next* line::

       8.5.1 GPIO port mode register (GPIOx_MODER)
       (x = A, B, C, D, F) . . . . . . . . . . . . . . . . . . . . 188

   So the page number is optional on the entry line, and a following line
   that is *not* itself an entry is folded into the pending entry's title
   (and can supply its page number). This is `FIXES_TASK.md` Fix 2's
   lesson, applied up front instead of after the fact.

2. **The dot-leader run can be one dot, or none at all.** `11.6.4 DMA
   channel x number of data to transfer register (DMA_CNDTRx) . 238` fits
   its line with room for exactly one leader dot, which a
   `(?:\\.\\s*){2,}` tail rejects; and `19.4.6 TIM14 capture/compare mode
   register 1 [alternate] (TIM14_CCMR1) 537` fills its line so completely
   that ST prints no leaders whatsoever.

   A dot-less trailing number is genuinely ambiguous against a wrapped
   title that simply ends in a digit (`6.4.18 RCC APB peripheral clock
   enable in Sleep/Stop mode register 1`, whose page number is on the
   next line). ST resolves it on the page: Contents page numbers are
   right-aligned in their own column. That column is measured from this
   manual's own Contents rather than assumed -- see `_page_column_x1` --
   so a bare trailing number is read as a page number only when it ends
   in that column. Verified on RM0490: 946 dot-leader entries end at
   x1 527.9 +/- 0.3, while every wrapped title line ends at x1 <= 494.

3. **The space before the title can be missing.** Once a section
   number's last component reaches three digits, ST's Contents field
   overflows and the separator disappears entirely: RM0486 prints
   `14.10.100RCC APB1H sleep enable register (RCC_APB1HLPENR) . . . 611`
   for every entry from 14.10.100 onwards. Requiring that space lost 199
   of that manual's 3,585 sections from the ground truth, which then
   surfaced as "extra" in validation even though the extractor had had
   them all along. Same defect class as `Table332.` in
   `CAPTION_ROBUSTNESS_FIX.md`.

Nothing in this module names a specific manual: the Contents pages are
found by `rmtables.headings.CONTENTS_PAGE_HEADER_RE` matching the running
page-header ST prints on them ("Contents RM0490" / "RM0490 Contents").
"""

from __future__ import annotations

import logging
import re
import statistics

from rmtables.cells import fix_symbols
from rmtables.headings import CONTENTS_PAGE_HEADER_RE
from rmtables.notes import FOOTER_RE

from .lines import DEFAULT_Y_TOLERANCE, page_lines

logger = logging.getLogger("rmcontent.contents")

# "4.7.1 FLASH access control register (FLASH_ACR) . . . . . 77", with
# BOTH the dot-leader run and the page number optional (see the module
# docstring). A bare number with no title never matches: `\S` is required.
#
# For a SUBSECTION the space between the number and the title is optional
# too. Once the last component reaches three digits, ST's Contents field
# overflows and the separator disappears: RM0486 prints "14.10.100RCC
# APB1H sleep enable register (RCC_APB1HLPENR) . . . 611" for every entry
# from 14.10.100 onwards. Requiring the space lost 199 of that manual's
# sections from the ground truth, which then showed up as "extra" in
# validation -- the extractor had them all along. This is the same defect
# `CAPTION_ROBUSTNESS_FIX.md` absorbed for "Table332.". Without a space
# the title must start with a letter or an opening bracket, so the
# number's own digits can never be split. A dot is mandatory here, which
# is what keeps this tolerant pattern away from chapter lines.
SECTION_ENTRY_RE = re.compile(r"^(\d+(?:\.\d+)+)(?:\s+|(?=[A-Za-z(]))(\S.*)$")

# A CHAPTER line, where the whitespace is MANDATORY. That single
# difference is what stops an entry whose title opens with an ordinal
# from being read as a chapter: RM0486's Contents wraps a VENC register
# entry onto a line of its own, "1st DCT partition register
# (VENC_SWREG58) . . . 2180", which under the tolerant pattern above
# split into number "1" and title "st DCT partition register
# (VENC_SWREG58)" and overwrote chapter 1 -- and "2nd ..." chapter 2. The
# `s` follows the `1` directly, so `\s+` rejects it outright.
CHAPTER_ENTRY_RE = re.compile(r"^(\d{1,2})\s+(\S.*)$")

# Trailing "<dot leaders><page number>", stripped off an entry's title.
# One or more leader dots (a title that nearly fills its line leaves room
# for only one), each optionally followed by whitespace.
TRAILING_PAGE_RE = re.compile(r"\s*(?:\.\s*)+(\d{1,5})\s*$")

# The same trailing page number with NO dot leaders at all -- only trusted
# when the line ends in the right-aligned page-number column (see the
# module docstring).
TRAILING_BARE_PAGE_RE = re.compile(r"\s+(\d{1,5})\s*$")

# Half-width of the page-number column: a line whose x1 is within this
# many points of the measured column edge is right-aligned in it.
PAGE_COLUMN_TOLERANCE = 2.0
# Below this many dot-leader entries the column measurement isn't
# trustworthy, so dot-less page numbers are simply not inferred.
MIN_PAGE_COLUMN_SAMPLES = 20

# A continuation line ends the same way but carries no leading number --
# e.g. "(x = A, B, C, D, F) . . . . . . 188" or a bare
# "(FLASH_PCROP1ASR) . . . . . . 85".
_BARE_PAGE_RE = re.compile(r"^\s*(\d{1,5})\s*$")

# Page furniture on a Contents page: the running header, the ST footer
# ("3/1023 RM0490 Rev 6"), and the bare marginal chapter-tab number ST
# prints in the outer margin ("29").
_MARGIN_NUMBER_RE = re.compile(r"^\d{1,4}$")

# The Contents section itself is preceded by "Contents" as a page header;
# "List of tables"/"List of figures" pages share CONTENTS_PAGE_HEADER_RE
# but are a different list and must not be parsed as section entries.
_CONTENTS_HEADER_RE = re.compile(r"\bContents\b")

# How far into the PDF to look for the Contents section before giving up.
# ST front matter never runs past this even on a 4600-page manual.
MAX_FRONT_MATTER_PAGES = 200


def match_entry(text: str) -> tuple[str, str] | None:
    """`(number, title)` for a Contents entry line, else `None`.

    A subsection is tried first: it is the more specific of the two (a
    dot is mandatory), so the tolerant missing-space form can never be
    offered a chapter line.
    """
    m = SECTION_ENTRY_RE.match(text)
    if m:
        return m.group(1), m.group(2)
    m = CHAPTER_ENTRY_RE.match(text)
    if m:
        return m.group(1), m.group(2)
    return None


def _strip_trailing_page(title: str, in_page_column: bool = False) -> tuple[str, int | None]:
    """`("General information . . . 41")` -> `("General information", 41)`;
    returns `(title, None)` when the entry wrapped and its page number is
    on the following line.

    `in_page_column` says this line ends in the right-aligned page-number
    column, which is the only circumstance under which a trailing number
    with no dot leaders in front of it is read as a page number.
    """
    m = TRAILING_PAGE_RE.search(title)
    if m:
        return title[: m.start()].strip(), int(m.group(1))
    if in_page_column:
        m = TRAILING_BARE_PAGE_RE.search(title)
        if m:
            return title[: m.start()].strip(), int(m.group(1))
    return title.strip(), None


def _page_column_x1(lines: list[tuple[str, float]]) -> float | None:
    """The right edge of this manual's Contents page-number column,
    measured as the median x1 of every entry that *does* carry dot
    leaders. Returns `None` when there are too few samples to trust."""
    x1s = sorted(x1 for text, x1 in lines if TRAILING_PAGE_RE.search(text))
    if len(x1s) < MIN_PAGE_COLUMN_SAMPLES:
        logger.warning(
            "only %d dot-leader Contents entries found; not inferring "
            "page numbers for dot-less entries", len(x1s),
        )
        return None
    return statistics.median(x1s)


def _is_page_furniture(text: str) -> bool:
    return bool(
        not text
        or FOOTER_RE.search(text)
        or _MARGIN_NUMBER_RE.match(text)
        or (CONTENTS_PAGE_HEADER_RE.search(text) and len(text) < 40)
    )


def _contents_page_indexes(pdf, y_tolerance: float = DEFAULT_Y_TOLERANCE) -> list[int]:
    """0-based PDF page indexes of the Contents section.

    Detected by the running page header, so the exact page range can move
    between revisions without breaking anything. Scanning stops at the
    first page after the section ends (the "List of tables" front matter
    immediately follows it), so body pages are never scanned.
    """
    indexes: list[int] = []
    started = False
    limit = min(MAX_FRONT_MATTER_PAGES, len(pdf.pages))
    for i in range(limit):
        page = pdf.pages[i]
        try:
            lines = page_lines(page, y_tolerance)
            first = lines[0]["text"] if lines else ""
            is_contents = bool(_CONTENTS_HEADER_RE.search(first))
        finally:
            page.flush_cache()
        if is_contents:
            started = True
            indexes.append(i)
        elif started:
            break
    return indexes


class ContentsIndex:
    """Parsed Contents: `sections` and `chapters`, each `{number: (title, page)}`.

    `page` is the printed page number ST lists, or `None` for the rare
    entry whose wrapped continuation carried no number either.
    """

    def __init__(self, sections: dict, chapters: dict):
        self.sections = sections
        self.chapters = chapters

    def chapter_title(self, chapter: str) -> str:
        entry = self.chapters.get(chapter)
        return entry[0] if entry else ""

    def __len__(self) -> int:
        return len(self.sections)


def parse_contents(pdf, y_tolerance: float = DEFAULT_Y_TOLERANCE) -> ContentsIndex:
    """Parse the front-matter Contents into a `ContentsIndex`."""
    sections: dict[str, tuple[str, int | None]] = {}
    chapters: dict[str, tuple[str, int | None]] = {}

    pending_number: str | None = None
    pending_title: str = ""
    pending_page: int | None = None

    # Second, independent guard on chapters (the `\s+` in
    # CHAPTER_ENTRY_RE is the first): ST numbers chapters 1, 2, 3, ...
    # once each, in order, so a line claiming chapter 1 after chapter 40
    # has been listed is not a chapter whatever it looks like. The first
    # occurrence wins and every reject is logged.
    highest_chapter = 0

    def commit() -> None:
        nonlocal pending_number, pending_title, pending_page, highest_chapter
        if pending_number is None:
            return
        title = pending_title.strip()
        if re.search(r"[A-Za-z]", title):
            if "." in pending_number:
                sections[pending_number] = (title, pending_page)
            else:
                number = int(pending_number)
                if pending_number in chapters or number <= highest_chapter:
                    logger.warning(
                        "Contents: ignoring out-of-order or duplicate chapter %r (%r); "
                        "highest chapter seen so far is %d",
                        pending_number, title[:60], highest_chapter,
                    )
                else:
                    chapters[pending_number] = (title, pending_page)
                    highest_chapter = number
        pending_number, pending_title, pending_page = None, "", None

    indexes = _contents_page_indexes(pdf, y_tolerance)
    if not indexes:
        logger.warning("no Contents pages found -- validation has no ground truth")
        return ContentsIndex(sections, chapters)

    # Pass 1: collect every Contents line with the x-coordinate of its
    # right edge, so the page-number column can be measured before any
    # entry is interpreted.
    collected: list[tuple[str, float]] = []
    for i in indexes:
        page = pdf.pages[i]
        try:
            for line in page_lines(page, y_tolerance):
                text = fix_symbols(line["text"]).strip()
                if not _is_page_furniture(text):
                    collected.append((text, line["x1"]))
        except Exception:
            logger.warning("failed to read Contents page %d", i + 1, exc_info=True)
        finally:
            page.flush_cache()

    column_x1 = _page_column_x1(collected)

    # Pass 2: interpret entries and their wrapped continuations.
    for text, x1 in collected:
        in_column = column_x1 is not None and abs(x1 - column_x1) <= PAGE_COLUMN_TOLERANCE
        entry = match_entry(text)
        if entry:
            commit()
            number, raw_title = entry
            title, target_page = _strip_trailing_page(raw_title, in_column)
            pending_number, pending_title, pending_page = number, title, target_page
            if pending_page is not None:
                commit()
            continue
        if pending_number is None:
            continue
        # A wrapped entry's continuation: the rest of the title, then the
        # dot leaders and the page number this entry was missing.
        bare = _BARE_PAGE_RE.match(text)
        if bare:
            pending_page = int(bare.group(1))
            commit()
            continue
        rest, target_page = _strip_trailing_page(text, in_column)
        pending_title = f"{pending_title} {rest}".strip()
        pending_page = target_page
        if pending_page is not None:
            commit()

    commit()
    logger.info("Contents: %d chapters, %d sections", len(chapters), len(sections))
    return ContentsIndex(sections, chapters)
