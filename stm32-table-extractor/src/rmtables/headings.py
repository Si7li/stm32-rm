"""Numbered-section heading tracking, shared by register identification and
RAG section tagging.

ST numbers headings like "4.7.1 FLASH access control register (FLASH_ACR)".
A naive `^(\\d+(?:\\.\\d+)*)\\s+(.+)$` also matches non-heading lines that
happen to start with a run of numbers -- most importantly the bit-number
header lines above register layouts ("31 30 29 28 ... 16"), and, verified
against RM0490, a table's own data row once its text has been merged into
one pdfplumber "line" (e.g. Table 16's bottom row reads "1 x Erase aborted
(no erase started) No Yes", which starts with a digit and a letter-bearing
"title" just like a real heading). Requiring at least one letter in the
title rejects the bit-number lines; requiring a real `N.N` number (at least
one dot -- a bare top-level digit is never a real ST heading in this
manual) rejects a stray digit-led table cell; and `HeadingTracker` itself
additionally excludes any line positioned inside a detected table's own
bounding box, which is what actually keeps a table's row text out of the
heading stack in the first place.
"""

from __future__ import annotations

import re

HEADING_RE = re.compile(r"^(\d+(?:\.\d+)+)\s+(.+)$")

# A "Contents"/"List of tables"/"List of figures" entry can read exactly
# like a real heading ("31.18 DBG register map . . . . . . . . 1110" fully
# matches HEADING_RE: number "31.18", a letter-bearing "title"). Two
# independent signals catch these (METADATA_FIXES.md):
# 1. a dot-leader run anywhere in the line -- three or more dots, each
#    optionally followed by whitespace before the next.
# 2. the line ending in dot-leaders then a page number, even with fewer
#    than 3 total dots.
# Neither matches a real title like "SDIO response 1..4 register
# (SDIO_RESPx)": "1..4" is only two dots with no space, and more text
# follows it (the line doesn't END in dots+digits).
DOT_LEADER_RUN_RE = re.compile(r"\.\s*\.\s*\.")
TRAILING_DOT_LEADER_PAGE_RE = re.compile(r"(\.\s*){2,}\d{1,4}\s*$")

# The running page-header on every Contents/List-of-tables/List-of-figures
# page literally says so (e.g. "Contents RM0008", "RM0008 List of tables") --
# skip heading detection on the whole page rather than rely solely on the
# per-line dot-leader check.
CONTENTS_PAGE_HEADER_RE = re.compile(r"\b(Contents|List of tables|List of figures)\b")


def _looks_like_toc_line(text: str) -> bool:
    return bool(DOT_LEADER_RUN_RE.search(text) or TRAILING_DOT_LEADER_PAGE_RE.search(text))


def _sanitize_title(title: str) -> str:
    """Defense-in-depth: strip a trailing dot-leader-run-plus-page-number
    that slipped through the line-level ToC rejection above. Anchored to
    the END of the string, so a genuine "1..4" in the middle of a real
    title (T167's "SDIO response 1..4 register (SDIO_RESPx)") is untouched."""
    return TRAILING_DOT_LEADER_PAGE_RE.sub("", title).rstrip()


# A register name in a heading is a bare identifier in parens, e.g.
# "(FLASH_ACR)". Some headings have a second parenthetical qualifier like
# "(x = 2 to 3)" -- that one has spaces/digits-with-symbols and is skipped.
_PAREN_GROUP_RE = re.compile(r"\(([^()]+)\)")
_IDENTIFIER_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")

# Long register headings wrap, leaving "(REGNAME)" alone on the very next
# line -- sometimes across a page break (heading is the last line of one
# page, "(REGNAME)" the first line of the next). Detected as a standalone
# line containing nothing but one identifier in parens.
BARE_PAREN_RE = re.compile(r"^\s*\(([A-Za-z][A-Za-z0-9_]*)\)\s*$")


def parse_heading(line_text: str) -> tuple[str, str] | None:
    """Return (section_number, section_title) if `line_text` is a real heading."""
    if _looks_like_toc_line(line_text):
        return None
    m = HEADING_RE.match(line_text)
    if not m:
        return None
    number = m.group(1)
    title = _sanitize_title(m.group(2).strip())
    if not re.search(r"[A-Za-z]", title):
        return None
    # TITLE_FIDELITY_FIX.md Part 3: RM0486 T735 matched body prose "USB 2.0
    # specification, July 16, 2007" as a heading -- it satisfies HEADING_RE
    # (number "2.0", letter-bearing "title") and isn't a ToC line, so nothing
    # above rejects it. Two independent, cheap guards, either alone enough:
    # a real ST heading title always starts with an uppercase letter
    # ("specification" doesn't), and ST numbers sections from .1, never .0
    # ("2.0"/"10.0" can't be a real section number).
    if not title[:1].isupper():
        return None
    if number.split(".")[-1] == "0":
        return None
    return number, title


def extract_register_name(heading_title: str) -> str | None:
    """Pull a register identifier like FLASH_ACR out of a heading title."""
    for group in _PAREN_GROUP_RE.findall(heading_title):
        if _IDENTIFIER_RE.match(group):
            return group
    return None


def _line_inside_any_bbox(line: dict, bboxes: list) -> bool:
    """True if `line`'s center point falls inside any of `bboxes`.

    A ruled table's own row text still comes back from
    `page.extract_text_lines()` as ordinary "lines" -- pdfplumber's line
    builder has no notion of table cells. Verified against RM0490: Table
    16's bottom row collapses into the single line "1 x Erase aborted (no
    erase started) No Yes", which otherwise satisfies `HEADING_RE` and gets
    mistaken for a real heading ("1"). Excluding any line positioned inside
    a detected table's bounding box is what actually prevents that -- the
    tightened `HEADING_RE` alone only catches the subset of such rows that
    happen to lack a "N.N" dotted number.
    """
    lx0, ltop, lx1, lbottom = line["x0"], line["top"], line["x1"], line["bottom"]
    lcx, lcy = (lx0 + lx1) / 2, (ltop + lbottom) / 2
    for bx0, btop, bx1, bbottom in bboxes:
        if bx0 <= lcx <= bx1 and btop <= lcy <= bbottom:
            return True
    return False


class HeadingTracker:
    """Tracks the nearest preceding heading as pages are scanned in order."""

    def __init__(self):
        self._last_before_page: tuple[str, str] | None = None
        self._page_headings: list[tuple[float, str, str]] = []

    def start_page(self, lines, raw_tables=()) -> None:
        """`raw_tables` (optional): this page's detected `RawTable`s, so a
        heading candidate positioned inside any of their bounding boxes can
        be excluded (see `_line_inside_any_bbox`)."""
        self._page_headings = []
        # Belt-and-suspenders alongside the per-line ToC rejection: every
        # Contents/List-of-tables/List-of-figures page carries that phrase
        # in its own running page-header line, so the whole page is skipped
        # for heading purposes rather than relying only on individual lines
        # happening to have a dot-leader run.
        if lines and CONTENTS_PAGE_HEADER_RE.search(lines[0]["text"]):
            return
        table_bboxes = [rt.bbox for rt in raw_tables]
        for i, line in enumerate(lines):
            if table_bboxes and _line_inside_any_bbox(line, table_bboxes):
                continue
            parsed = parse_heading(line["text"])
            if not parsed:
                continue
            number, title = parsed
            if i + 1 < len(lines):
                m = BARE_PAREN_RE.match(lines[i + 1]["text"])
                if m:
                    title = f"{title} ({m.group(1)})"
            self._page_headings.append((line["top"], number, title))
        self._page_headings.sort(key=lambda h: h[0])

        # A heading's "(REGNAME)" continuation can be pushed onto the very
        # first line of the next page by a page break. If nothing on this
        # page already looks like a heading there, and the last heading
        # carried over from the previous page has no paren identifier yet,
        # treat that first line as its missing continuation.
        if lines and self._last_before_page is not None:
            number, title = self._last_before_page
            if extract_register_name(title) is None:
                m = BARE_PAREN_RE.match(lines[0]["text"])
                if m:
                    self._last_before_page = (number, f"{title} ({m.group(1)})")

    def heading_before(self, top: float) -> tuple[str, str] | None:
        candidates = [h for h in self._page_headings if h[0] <= top]
        if candidates:
            h = max(candidates, key=lambda h: h[0])
            return (h[1], h[2])
        return self._last_before_page

    def finish_page(self) -> None:
        if self._page_headings:
            _, number, title = self._page_headings[-1]
            self._last_before_page = (number, title)
