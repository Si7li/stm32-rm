"""Page scan -> one `Section` per numbered heading.

A section runs from its heading line to **the next heading of any level**.
A parent therefore keeps only its own preamble and never repeats a child:
`4.7 FLASH registers` ends where `4.7.1 FLASH access control register
(FLASH_ACR)` begins. Verified on RM0490 Rev 6: 903 headings, all unique,
nothing double-counted.

`rmtables.headings.HEADING_RE` requires at least one dot, so it sees
depths 2-3 and is blind to level-1 chapter headings like `4 Embedded
flash memory (FLASH)`. That is deliberate and is left alone: chapters are
not emitted as records, and `chapter_title` comes from the Contents parse
instead (which yields all 33 of RM0490's cleanly). The consequence for
boundaries is that a chapter heading does not close the preceding
section -- but the preceding section is always the last subsection of the
previous chapter, and a chapter heading is immediately followed by its
own first numbered subsection, so at most the chapter's title line lands
at the end of the previous section. `_CHAPTER_HEADING_RE` below closes
that gap explicitly, using the Contents as the authority on which
`<digits> <Title>` lines are real chapters.

Memory: `rmtables.extract.flush_page` is called for every page. Without
it a full 1023-page run OOMs around page 800, because pdfplumber caches
each page's char/textmap objects for the lifetime of the PDF object.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

from rmtables.captions import assign_caption, find_captions
from rmtables.cells import fix_symbols
from rmtables.extract import extract_page_tables, flush_page
from rmtables.headings import (
    BARE_PAREN_RE,
    CONTENTS_PAGE_HEADER_RE,
    _line_inside_any_bbox,
    _looks_like_toc_line,
    extract_register_name,
    parse_heading,
)

from .figures import FigureCaptions
from .lines import DEFAULT_Y_TOLERANCE, page_lines
from .markers import (
    ArtworkBand,
    LogicalTableTracker,
    continued_note_tops,
    parse_figure_caption,
    region_markers,
)
from .noise import (
    DEFAULT_BODY_FONT,
    NoiseCounts,
    PageFurniture,
    artwork_threshold,
    derive_body_font_size,
    document_header_re,
    contains_asset_id,
    is_artwork_line,
    is_asset_id,
    is_bit_layout_row,
    is_rotated_line,
    is_stray_glyph,
    strip_trailing_asset_id,
)

logger = logging.getLogger("rmcontent.sections")

# A level-1 chapter heading: a bare integer then a title. Far too loose on
# its own -- "16 Bit timer" in a figure label fits it -- so a candidate
# only counts when the Contents lists that exact number AND that exact
# title (see `SectionScanner._is_chapter_heading`). A false positive here
# would silently orphan every line up to the next real heading, so the
# check is deliberately strict; a chapter title that wrapped across two
# lines simply fails it, degrading to the pre-existing behaviour of not
# closing the previous section early.
_CHAPTER_HEADING_RE = re.compile(r"^(\d{1,3})\s+(\S.*)$")


# A numbered heading whose title `rmtables.headings.parse_heading` will
# not accept. That function requires the title to start with an uppercase
# letter, which `TITLE_FIDELITY_FIX.md` added to stop RM0486 body prose
# ("2.0 specification, July 16, 2007") being read as a heading. Two real
# RM0490 headings start with a digit instead -- `17.3.19 6-step PWM
# generation` and `20.4.15 6-step PWM generation` -- and are invisible to
# it. They are recovered here, but only on ST's own authority: the number
# AND the title must both match a Contents entry exactly. Nothing looser
# is accepted, so the guard `parse_heading` provides is not weakened.
_NUMBERED_LINE_RE = re.compile(r"^(\d+(?:\.\d+)+)\s+(\S.*)$")

# A heading continuation line made only of parenthetical groups:
# "(TIMx_CCER)(x = 2 to 3)" under RM0490 18.4.11, or "(x = A, B, C, D, F)"
# under 8.5.1. `rmtables.headings.BARE_PAREN_RE` handles the single-bare-
# identifier form; this covers the rest, which would otherwise truncate
# the title and leak the parenthetical into the section body.
_PAREN_ONLY_LINE_RE = re.compile(r"^\s*(?:\([^()]*\)\s*)+$")


# ST's Contents wraps a long chapter title, so the body heading and the
# Contents entry can differ by a trailing fragment. Titles are compared
# on a prefix rather than in full, capped at this many characters.
CHAPTER_TITLE_MATCH_CHARS = 30
# ...but never on fewer than this, or a body line like "5 OTP" would
# match chapter 5's "OTP mapping (OTP)" on three characters.
CHAPTER_TITLE_MIN_CHARS = 10


def _normalize_title(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().casefold()


def _title_key(text: str) -> str:
    """A chapter title reduced to its letters and digits.

    Punctuation and spacing are dropped because a superscript is lifted
    onto a line of its own by pdfplumber, leaving a gap behind: RM0486
    chapter 75 is `USB Type-C®/USB Power Delivery interface (UCPD)` in
    the Contents but `75 USB Type-C /USB Power Delivery interface (UCPD)`
    in the body, differing at the tenth character. Comparing letters and
    digits only makes the two identical, and the comparison stays
    specific -- it is still 30 characters of title, on a candidate whose
    number must already be a known, not-yet-seen chapter.
    """
    return re.sub(r"[^a-z0-9]+", "", text.casefold())


def _titles_match(candidate: str, expected: str) -> bool:
    """Whether a body heading's title is the Contents title for it.

    Compared as a prefix on the first `CHAPTER_TITLE_MATCH_CHARS`, since
    ST wraps a long chapter title across two printed lines and only the
    first reaches this check.
    """
    a, b = _title_key(candidate), _title_key(expected)
    n = min(CHAPTER_TITLE_MATCH_CHARS, len(a), len(b))
    if n < min(CHAPTER_TITLE_MIN_CHARS, len(b)):
        return False
    return a[:n] == b[:n]


@dataclass
class Section:
    """One numbered section's heading, position and body lines."""

    number: str
    title: str
    page: int
    page_end: int
    lines: list = field(default_factory=list)

    @property
    def level(self) -> int:
        return self.number.count(".") + 1

    @property
    def chapter(self) -> str:
        return self.number.split(".")[0]

    @property
    def parent(self) -> str | None:
        """`None` for a level-2 section: its parent is a chapter, and
        chapters are not records."""
        parts = self.number.split(".")
        if len(parts) <= 2:
            return None
        return ".".join(parts[:-1])

    @property
    def content(self) -> str:
        return "\n".join(self.lines)


class SectionScanner:
    """Accumulates sections across pages, in reading order."""

    def __init__(
        self,
        document: str,
        chapter_titles: dict | None = None,
        section_titles: dict | None = None,
        page_count: int = 0,
        body_font_size: float = DEFAULT_BODY_FONT,
        listed_figures: dict | None = None,
        y_tolerance: float = DEFAULT_Y_TOLERANCE,
    ):
        self.sections: list[Section] = []
        self._page_count = page_count
        self.y_tolerance = y_tolerance
        self.body_font_size = body_font_size
        self.artwork_max = artwork_threshold(body_font_size)
        self.noise = NoiseCounts()
        self.uncaptioned_regions = 0
        self.recovered_headings: list[str] = []
        self.rejected_headings: list[str] = []
        self.rejected_chapters: list[str] = []
        self.duplicate_markers = 0
        # Collapses a table split across pages to a single marker.
        self._tables = LogicalTableTracker()
        # Figure caption -> artwork asset id: the span between them is
        # the drawing's own label text.
        self.figures = FigureCaptions(listed_figures)
        self.band = ArtworkBand()
        self._seen_chapters: set[str] = set()
        self._last_chapter = 0
        self._header_re = document_header_re(document)
        # As printed by ST, and reduced for comparison. A chapter record
        # takes its title from the Contents verbatim: that is the
        # complete, correctly-cased text, whereas the body heading may be
        # only the first line of a wrapped title.
        self._chapter_titles = dict(chapter_titles or {})
        self._chapters = {
            number: _normalize_title(title)
            for number, title in self._chapter_titles.items()
        }
        numbers = [int(n) for n in self._chapters if n.isdigit()]
        self._max_chapter = max(numbers) if numbers else 0
        self._section_titles = {
            number: _normalize_title(title)
            for number, title in (section_titles or {}).items()
        }
        self._current: Section | None = None
        self._max_seen_chapter = 0
        # Set when a page ends with a table whose footnotes may spill
        # onto the next page (see `markers.continued_note_tops`).
        self._notes_may_continue = False
        # A heading whose "(REGNAME)" continuation may be the first line of
        # the next page (`rmtables.headings.HeadingTracker` handles the
        # same case for its own purposes).
        self._awaiting_paren = False

    # -- heading helpers -------------------------------------------------

    def _chapter_heading_at(self, text: str) -> tuple[str, str] | None:
        """`(number, title)` if this line is a level-1 chapter heading.

        `HEADING_RE` requires a dot, so a chapter heading is invisible to
        `parse_heading` and has to be recognized here -- and `5 Some
        numbered item` is a plausible false positive, so three
        independent guards apply, all resolved against the Contents:

        - the number must be a chapter the Contents lists;
        - chapters are encountered once each, in ascending order, so a
          line claiming a chapter already passed is not one;
        - the title must match the Contents title for that chapter.

        A false chapter record is worse than a missing one: it would both
        invent a record and truncate the section it interrupted. Every
        rejected candidate is logged.
        """
        m = _CHAPTER_HEADING_RE.match(text)
        if not m:
            return None
        number, title = m.group(1), m.group(2).strip()
        expected = self._chapters.get(number)
        if expected is None:
            return None
        if not _titles_match(title, expected):
            return None
        if number in self._seen_chapters or int(number) <= self._last_chapter:
            logger.debug(
                "rejecting chapter heading %r (%r): already seen, or out of order "
                "after chapter %d", number, title[:60], self._last_chapter,
            )
            self.rejected_chapters.append(number)
            return None
        # Recorded here rather than in `_open`, because every line on a
        # page is classified before any of them is opened -- so a second
        # candidate on the SAME page would otherwise pass the guard.
        self._seen_chapters.add(number)
        self._last_chapter = max(self._last_chapter, int(number))
        return number, title

    def _chapter_plausible(self, number: str) -> bool:
        """Reject a heading whose chapter cannot be right at this point.

        Two independent bounds, both on ST's own authority and both
        needed -- each catches a shape the other misses.
        """
        return self._chapter_in_range(number) and self._chapter_in_order(number)

    def _chapter_in_order(self, number: str) -> bool:
        """Chapters only ever move forwards through the document.

        RM0486 page 1029 contains the body prose fragment "1.6 GBps",
        which satisfies `HEADING_RE` -- a dotted number and a
        capitalized, letter-bearing title -- and became a phantom section
        1.6 nine hundred pages after the real chapter 1 ended. Comparing
        only the CHAPTER component (never the full number) keeps this
        blind to ST's occasional unlisted subsection, such as RM0490's
        real 29.6.8 following 29.6.7.
        """
        try:
            chapter = int(number.split(".")[0])
        except ValueError:
            return True
        return chapter >= self._max_seen_chapter

    def _chapter_in_range(self, number: str) -> bool:
        """Reject a heading whose chapter is past the end of the manual.

        RM0522 body prose reads "61.44 MHz from the clock controller of
        the circuit. In the example above we ...", which satisfies
        `HEADING_RE` completely -- a dotted number, a capitalized,
        letter-bearing title, no dot leaders -- and became a phantom
        section 61.44 in a manual whose last chapter is 52.

        The test is deliberately an upper BOUND rather than exact
        membership. A chapter the Contents parser happened to miss would,
        under an exact test, silently delete every section in it; under
        this one it survives, and only a number ST's own numbering cannot
        reach is refused. Skipped entirely when no chapters parsed.
        """
        if not self._max_chapter:
            return True
        try:
            return int(number.split(".")[0]) <= self._max_chapter
        except ValueError:
            return True

    def _heading_at(self, raw: str, text: str, inside_table: bool) -> tuple[str, str] | None:
        """The heading this line is, or `None`.

        Inside a detected table's bounding box, ONLY a Contents-vouched
        heading is accepted. That exclusion exists because a ruled
        table's own row text comes back as ordinary lines and reads like
        a heading (`rmtables.headings._line_inside_any_bbox`), and it
        must stay -- but it also swallowed two real headings on RM0486,
        where a figure's ruled box was detected as a table region
        covering the top of the page: `33.3.4 DTS serial data adapter
        (SDA)` sits at top 91.0 inside a region spanning 75.2..448.7.
        Demanding an exact Contents match lets those through without
        readmitting a single table row.
        """
        if inside_table:
            parsed = self._contents_backed_heading(text)
            if parsed is not None:
                self.recovered_headings.append(parsed[0])
            return parsed

        parsed = parse_heading(raw)
        if parsed is not None:
            if self._chapter_plausible(parsed[0]):
                return parsed
            self.rejected_headings.append(parsed[0])
            return None

        parsed = self._contents_backed_heading(text)
        if parsed is not None:
            self.recovered_headings.append(parsed[0])
        return parsed

    def _split_heading_at(self, lines: list[dict], i: int, text: str) -> tuple[str, str] | None:
        """A heading whose first title word was split onto the line ABOVE
        its own number, or `None`.

        The mirror image of the `(REGNAME)`-on-the-next-line wrap. RM0486
        page 1456 typesets `31.4.1 DLYB diagram` such that pdfplumber
        returns two lines, `DLYB` and then `31.4.1 diagram` -- the number
        line's title now starts lowercase, so `parse_heading` refuses it
        and a plain Contents lookup misses too ("diagram" is not "DLYB
        diagram").

        Recognized only when the two fragments joined reproduce the
        Contents title exactly, so this cannot merge two unrelated lines.
        """
        if i + 1 >= len(lines):
            return None
        m = _NUMBERED_LINE_RE.match(lines[i + 1]["text"].strip())
        if not m or _looks_like_toc_line(lines[i + 1]["text"]):
            return None
        expected = self._section_titles.get(m.group(1))
        if expected is None:
            return None
        title = f"{text} {m.group(2)}".strip()
        if _normalize_title(title) != expected:
            return None
        self.recovered_headings.append(m.group(1))
        return m.group(1), title

    def _completes_chapter_title(self, number: str, title: str, next_text: str) -> bool:
        """Whether `next_text` is the rest of a wrapped chapter title.

        True only when the heading's own line falls short of the
        Contents title and joining this line gets closer to it, so an
        ordinary first body line is never swallowed.
        """
        expected = self._chapters.get(number)
        if expected is None or not next_text:
            return False
        head, whole = _title_key(title), _title_key(expected)
        if len(head) >= len(whole):
            return False
        return whole.startswith(_title_key(f"{title} {next_text}")[: len(whole)])

    def _contents_backed_heading(self, text: str) -> tuple[str, str] | None:
        """A heading `parse_heading` rejected, vouched for by the Contents.

        Both the number and the full title must match ST's own listing,
        so this can only ever recover a heading the manual itself
        declares -- never invent one.
        """
        m = _NUMBERED_LINE_RE.match(text)
        if not m:
            return None
        if _looks_like_toc_line(text):
            return None
        expected = self._section_titles.get(m.group(1))
        if expected is None or _normalize_title(m.group(2)) != expected:
            return None
        return m.group(1), m.group(2).strip()

    def _open(self, number: str, title: str, page: int) -> None:
        self._tables.reset()
        self._current = Section(number=number, title=title, page=page, page_end=page)
        self.sections.append(self._current)
        self._awaiting_paren = True
        try:
            chapter = int(number.split(".")[0])
        except ValueError:
            return
        self._max_seen_chapter = max(self._max_seen_chapter, chapter)

    def _append(self, text: str, page: int) -> None:
        if self._current is None:
            return  # front matter before the first numbered heading
        self._current.lines.append(text)
        self._current.page_end = page

    @staticmethod
    def _page_ends_with_a_table(raw_tables, captions, body_lines, redundant_tops) -> bool:
        """Whether this page's last captioned region runs to the end of
        the page, so its footnotes may continue onto the next one.

        True only when nothing survives below that region -- anything
        printed after it means the table is finished and its notes, if
        any, were already taken here.
        """
        captioned = [
            (rt.bbox, assign_caption(rt.bbox, captions)) for rt in raw_tables
        ]
        captioned = [(bbox, c) for bbox, c in captioned if c is not None]
        if not captioned:
            return False
        bottom = max(bbox[3] for bbox, _ in captioned)
        return not any(
            line["top"] > bottom - 1 and line["top"] not in redundant_tops
            for line in body_lines
        )

    # -- the page scan ---------------------------------------------------

    def scan_page(self, page, page_number: int) -> None:
        """Consume one page. `page_number` is 1-indexed (PDF order)."""
        try:
            lines = [
                dict(line, text=fix_symbols(line["text"]))
                for line in page_lines(page, self.y_tolerance)
            ]
        except Exception:
            logger.warning("soft failure reading page %d", page_number, exc_info=True)
            return

        if not lines:
            return

        # Contents / List of tables / List of figures: skipped wholesale.
        # These pages are full of lines that read exactly like headings.
        if CONTENTS_PAGE_HEADER_RE.search(lines[0]["text"]):
            self.noise.contents_pages += 1
            self.noise.contents_page_lines += len(lines)
            return

        raw_tables = extract_page_tables(page, page_number)
        captions = find_captions(lines, page_number)
        table_bboxes = [rt.bbox for rt in raw_tables]

        furniture = PageFurniture(
            page.height, self._header_re, self._page_count
        ).furniture_tops(lines)
        self.noise.headers_footers += len(furniture)

        # Artwork is removed before note capture, not just before the
        # prose: `notes_below` stops at the first line under a grid that
        # is not a numbered note, so a single asset id printed there
        # blocks the whole footnote run (RM0486 67.9.10's `MSv40491V1`
        # sat between Table 665 and its two footnotes, and both leaked).
        readable = [
            line for line in lines
            if line["text"].strip()
            and not is_artwork_line(line, self.artwork_max)
            and not contains_asset_id(line["text"])
        ]

        table_markers, redundant_tops, uncaptioned = region_markers(
            raw_tables, captions, readable
        )
        self.uncaptioned_regions += uncaptioned

        body_lines = [
            line for i, line in enumerate(lines)
            if i not in furniture
            and line["text"].strip()
            and not is_artwork_line(line, self.artwork_max)
            and not contains_asset_id(line["text"])
        ]
        if self._notes_may_continue:
            redundant_tops |= continued_note_tops(body_lines)
        self._notes_may_continue = self._page_ends_with_a_table(
            raw_tables, captions, body_lines, redundant_tops
        )

        # Everything that will land in a section, as (top, rank, kind,
        # payload) tuples so table markers interleave with prose in
        # reading order. `rank` breaks a tie at the same `top`: a
        # region's marker comes before a heading, which comes before
        # body text.
        items: list[tuple[float, int, str, object]] = []
        for top, number, marker in table_markers:
            items.append((top, 0, "table_marker", (number, marker)))

        skip: set[int] = set()

        for i, line in enumerate(lines):
            if i in furniture or i in skip:
                continue
            text = line["text"].strip()
            if not text:
                continue

            if line["top"] in redundant_tops:
                # This region's own caption line, or one of its
                # footnotes: the marker already stands for it.
                self.noise.table_furniture += 1
                continue

            if is_artwork_line(line, self.artwork_max) and not contains_asset_id(text):
                # A figure's internal label text. Dropped before any
                # other classification, so a diagram label can never be
                # read as a heading either. The caption is body-sized and
                # is therefore untouched, and still emits its marker.
                #
                # An asset id is exempt even when it is small enough for
                # the backstop -- and it usually is, since it is printed
                # inside the drawing. It has to reach the item loop to
                # CLOSE the band; letting the backstop eat it first left
                # 263 of RM0490's 318 bands with nothing to close on.
                self.noise.figure_artwork += 1
                continue

            inside_table = bool(table_bboxes) and _line_inside_any_bbox(line, table_bboxes)

            parsed = self._heading_at(line["text"], text, inside_table)
            if parsed:
                number, title = parsed
                # A long heading wraps its tail onto the next line; fold
                # it back into the title rather than let it truncate the
                # title AND leak into the body.
                if i + 1 < len(lines):
                    continuation = self._title_continuation(lines[i + 1]["text"])
                    if continuation:
                        title = f"{title} {continuation}"
                        skip.add(i + 1)
                items.append((line["top"], 1, "heading", (number, title)))
                continue

            if inside_table:
                # Represented by its region's marker, if captioned. Not
                # counted as noise: it is table content, and the noise
                # tallies are meant to describe what was removed from the
                # PROSE, not what the region already accounts for.
                continue

            if is_rotated_line(line):
                # A running head or footer rotated 90 degrees on a
                # landscape figure page. Checked after bbox exclusion, so
                # a register map's rotated `Res.` names never reach here,
                # and before the band, so the counters stay meaningful.
                self.noise.rotated_lines += 1
                continue

            chapter = self._chapter_heading_at(text)
            if chapter:
                number, title = chapter
                # A wrapped chapter title continues on the next line
                # ("7 Resource isolation slave unit for address space" /
                # "protection (full version) (RISAF)"). Take the whole
                # title from the Contents and drop the remainder line,
                # which would otherwise open the chapter's own body.
                if i + 1 < len(lines) and self._completes_chapter_title(
                    number, title, lines[i + 1]["text"].strip()
                ):
                    skip.add(i + 1)
                items.append((
                    line["top"], 1, "heading",
                    (number, self._chapter_titles.get(number, title)),
                ))
                continue
            if is_bit_layout_row(text):
                self.noise.bit_layout_rows += 1
                continue
            if is_stray_glyph(text):
                self.noise.stray_glyphs += 1
                continue

            split = self._split_heading_at(lines, i, text)
            if split:
                items.append((line["top"], 1, "heading", split))
                skip.add(i + 1)
                continue

            caption = parse_figure_caption(text)
            if caption and self.figures.is_caption(caption[0], caption[1]):
                items.append((line["top"], 2, "figure_marker", caption))
                continue
            # A rejected candidate is prose, not a deletion: RM0486
            # 12.4.3's "Figure 14. shows the functional view of..." is a
            # cross-reference and stays as the sentence it is.
            items.append((line["top"], 2, "text", text))

        items.sort(key=lambda it: (it[0], it[1]))

        for _, _, kind, payload in items:
            # Hard bound, checked before anything else on the page: a
            # band may not run more than MAX_BAND_PAGES past its caption.
            if self.band.is_open and not self.band.within_bound(page_number):
                self._abandon_band("past the 2-page bound")

            if kind == "heading":
                # A band may not cross a section boundary either.
                self._abandon_band("section boundary")
                number, title = payload
                self._open(number, title, page_number)
            elif kind == "table_marker":
                # A table inside a figure's band means the band is wrong.
                self._abandon_band("a table marker intervened")
                number, marker = payload
                if self._tables.should_emit(number, page_number):
                    self._append(marker, page_number)
                else:
                    self.duplicate_markers += 1
            elif kind == "figure_marker":
                # One caption, one marker, one band.
                self._abandon_band("the next figure caption arrived")
                number, title, marker = payload
                self._append(marker, page_number)
                if self.figures.may_open_band(number, title):
                    self.band.open(marker, page_number)
            elif self.band.is_open:
                if contains_asset_id(payload):
                    self.band.close(payload)  # inclusive: the id line goes too
                else:
                    self.band.hold(payload, page_number)
            elif is_asset_id(payload):
                # No band open -- the backstop for a figure whose caption
                # was never recognized, so its id has nothing to close.
                self.noise.figure_artwork += 1
            elif not self._consume_paren_continuation(payload):
                self._append(strip_trailing_asset_id(payload), page_number)

    def _abandon_band(self, reason: str = "hard bound") -> None:
        """Give every line an open band was holding back to its section.

        This is the fail-safe: reaching the hard bound costs an artwork
        leak, never a deletion.
        """
        for text, page in self.band.abandon(reason):
            if not self._consume_paren_continuation(text):
                self._append(strip_trailing_asset_id(text), page)

    @staticmethod
    def _title_continuation(text: str) -> str | None:
        """The tail of a heading that wrapped onto this line, or `None`.

        Two shapes occur. `rmtables.headings.BARE_PAREN_RE` recognizes a
        lone register identifier (`(RCC_AHBSMENR)`), which is by far the
        common one. RM0490 18.4.11 shows the other: `(TIMx_CCER)(x = 2 to
        3)` -- two parenthetical groups on one line, which that pattern
        rejects, leaving the title truncated to "TIMx capture/compare
        enable register" and the identifier stranded in the body.
        """
        stripped = text.strip()
        m = BARE_PAREN_RE.match(text)
        if m:
            return f"({m.group(1)})"
        if _PAREN_ONLY_LINE_RE.match(text) and re.search(r"[A-Za-z]", stripped):
            return stripped
        return None

    def _consume_paren_continuation(self, payload: str) -> bool:
        """Fold a standalone `(REGNAME)` back into the heading it wrapped
        off, instead of emitting it as the section's first body line.

        Within a page this is already handled while the heading is read
        (the next line is looked at directly); this covers the case a
        PAGE BREAK creates, where the heading is the last line of one page
        and `(REGNAME)` the first line of the next.
        """
        if not self._awaiting_paren:
            return False
        self._awaiting_paren = False
        if self._current is None:
            return False
        if extract_register_name(self._current.title) is not None:
            return False
        continuation = self._title_continuation(payload)
        if continuation is None:
            return False
        self._current.title = f"{self._current.title} {continuation}"
        return True

    def finalize(self) -> list[Section]:
        # A band still open at the end of the document never found its
        # asset id, so it drops nothing.
        self._abandon_band("end of document")
        return self.sections


def scan_pdf(
    pdf,
    document: str,
    chapter_titles: dict | None = None,
    section_titles: dict | None = None,
    start: int = 1,
    end: int | None = None,
    progress_every: int = 100,
    on_progress=None,
    body_font_size: float | None = None,
    listed_figures: dict | None = None,
    y_tolerance: float = DEFAULT_Y_TOLERANCE,
) -> SectionScanner:
    """Scan `pdf` page by page, releasing each page's caches as it goes."""
    if body_font_size is None:
        body_font_size = derive_body_font_size(pdf, y_tolerance=y_tolerance)
    scanner = SectionScanner(
        document, chapter_titles, section_titles,
        page_count=len(pdf.pages), body_font_size=body_font_size,
        listed_figures=listed_figures, y_tolerance=y_tolerance,
    )
    logger.info(
        "body font size %.1f pt; figure artwork is anything below %.1f pt",
        scanner.body_font_size, scanner.artwork_max,
    )
    last = end or len(pdf.pages)
    for page_number in range(start, last + 1):
        page = pdf.pages[page_number - 1]
        try:
            scanner.scan_page(page, page_number)
        except Exception:
            logger.warning("soft failure on page %d", page_number, exc_info=True)
        finally:
            flush_page(page)
        if on_progress and progress_every and page_number % progress_every == 0:
            on_progress(page_number, last)
    return scanner
