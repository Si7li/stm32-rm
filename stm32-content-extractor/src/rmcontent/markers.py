"""Inline `[Table N. ...]` / `[Figure N. ...]` markers.

Table text is 14.5% of RM0490's in-section characters. Inlining it would
duplicate the sibling project, which owns table *content*; dropping it
silently would break the prose, because ST writes "as shown in Table 26"
and the reader (or the retrieval model) then finds nothing. A marker line
keeps the reference intact at its real position in reading order and
gives the two datasets a join key -- `table_number` here is the same
`table_number` `rmtables` emits.

Regions are found with `rmtables.extract.TABLE_SETTINGS` (identical
detection to the sibling, so the two agree on what a table is) and
captioned with `rmtables.captions.assign_caption` (identical caption
assignment, so they agree on which caption belongs to it).

**A marker replaces the region AND its furniture.** The bbox covers only
the ruled grid, but ST prints the caption above it and any footnotes
below it, both outside the bbox. Left alone they survive into the prose:
the caption immediately before a marker restating it word for word
(1,278 duplicate pairs across 546 sections on RM0486), and the footnotes
immediately after -- 207 lines that the sibling project's `notes` field
already holds. `region_markers` returns those line positions along with
the markers, so the caller drops exactly them. Both are identified by
POSITION, from the same `assign_caption`/`notes_below` calls the table
extractor makes; neither is matched by shape.

**Uncaptioned regions emit no marker.** Every register description in the
manual prints its 32-bit layout as two ruled half-grids with no caption
of their own -- 371 registers on RM0490, so ~742 regions. They carry no
table number, nothing cross-references them, and their content is
restated field by field in the `Bit`/`Bits` lines directly below. A
marker for each would be `[Table . ]` noise on 40% of all sections. Their
lines are still excluded from the prose (they are inside a detected
bbox), and the count of suppressed regions is reported by `--validate`
rather than being invisible.
"""

from __future__ import annotations

import re

from rmtables.captions import CONTINUED_RE, FIGURE_CAPTION_RE, assign_caption
from rmtables.notes import notes_below

# Marker text is deliberately literal and stable: an opening bracket, the
# word, the number, a period, the caption, a closing bracket.
TABLE_MARKER = "[Table {number}. {title}]"
FIGURE_MARKER = "[Figure {number}. {title}]"

# Matches a marker this module produced -- used by the register parser to
# step over one, and by the tests.
MARKER_RE = re.compile(r"^\[(?:Table|Figure) \d+\..*\]$")


def _clean_caption(raw: str) -> str:
    """Same normalization `rmtables.captions.find_captions` applies to a
    table caption: drop a trailing `(continued)`, trailing period and
    surrounding whitespace."""
    return CONTINUED_RE.sub("", raw).strip().rstrip(".").strip()


def table_marker(caption) -> str | None:
    """`[Table 26. FLASH register map and reset values]` for an assigned
    `rmtables.model.Caption`, or `None` when there isn't one."""
    if caption is None or caption.number is None:
        return None
    title = _clean_caption(caption.text)
    return TABLE_MARKER.format(number=caption.number, title=title)


def parse_figure_caption(text: str) -> tuple[int, str, str] | None:
    """`(number, title, marker)` for a figure-caption line, else `None`.

    The number and title are handed back alongside the marker so the
    caller can validate the caption before trusting it to open an
    artwork band.
    """
    m = FIGURE_CAPTION_RE.match(text)
    if not m:
        return None
    number = int(re.sub(r"\s", "", m.group(1)))
    title = _clean_caption(m.group(2))
    if not title:
        # A bare cross-reference ("...see Figure 21.") with nothing after
        # the period is not a caption -- same rejection `find_captions`
        # makes for tables.
        return None
    return number, title, FIGURE_MARKER.format(number=number, title=title)


def figure_marker(text: str) -> str | None:
    """`[Figure 21. DMA block diagram]` for a figure-caption line, else
    `None`. Reuses `FIGURE_CAPTION_RE`, so a caption whose word or number
    was split by ST's renderer (`F igure`, `Figure 2 1.`) is still
    recognized."""
    m = FIGURE_CAPTION_RE.match(text)
    if not m:
        return None
    number = int(re.sub(r"\s", "", m.group(1)))
    title = _clean_caption(m.group(2))
    if not title:
        # A bare cross-reference ("...see Figure 21.") with nothing after
        # the period is not a caption -- same rejection `find_captions`
        # makes for tables.
        return None
    return FIGURE_MARKER.format(number=number, title=title)


def note_line_tops(lines, bottom: float, notes: list[str]) -> set[float]:
    """The `top` of every line `rmtables.notes.notes_below` consumed.

    A table's numbered footnotes belong to the table -- the sibling
    project already captures them in its `notes` field -- but they are
    printed BELOW the ruled grid, so they fall outside the region's bbox
    and would otherwise land in the prose right after the marker
    (`[Table 37. RAMCFG interrupt requests]` then `1. All these bits are
    in RAMCFG_BKPSRAMISR.`).

    `notes_below` returns joined strings, not line positions -- a wrapped
    footnote comes back as one note spanning several printed lines. Rather
    than reimplement its break conditions (a second, divergent copy of
    logic verified against three manuals), this walks the same lines it
    walked and consumes them against its own output: a line is one of its
    notes exactly while the accumulated text remains a prefix of them.
    Nothing is matched by shape, so a section's genuine numbered list --
    which is not below a table bbox and never appears in `notes_below`'s
    return value -- is untouched.
    """
    if not notes:
        return set()
    expected = " ".join(notes)
    below = sorted((l for l in lines if l["top"] > bottom - 1), key=lambda l: l["top"])
    tops: set[float] = set()
    accumulated = ""
    for line in below:
        text = line["text"].strip()
        if not text:
            continue
        candidate = f"{accumulated} {text}".strip()
        if not expected.startswith(candidate):
            break
        accumulated = candidate
        tops.add(line["top"])
        if accumulated == expected:
            break
    return tops


def continued_note_tops(body_lines) -> set[float]:
    """Footnotes of a table whose grid ended on the PREVIOUS page.

    ST prints a table's notes directly under its grid, so when the grid
    fills a page the notes are pushed to the top of the next one -- where
    there is no region for `notes_below` to work from, and they land in
    the prose (RM0490 Table 104: grid ends at bbox bottom 710.5 on page
    660, notes printed at tops 97.1 and 110.8 on page 661).

    `body_lines` must already have page furniture removed, and the caller
    must only ask when the previous page genuinely ended with a table and
    nothing after it. The decision itself still belongs entirely to
    `notes_below`: it is handed the top of the page and returns nothing
    unless the very first line there is a numbered note, so a page
    opening with ordinary prose suppresses nothing. Verified on RM0490:
    fires on 3 page boundaries, all 3 real table-note continuations, no
    prose list among them.
    """
    if not body_lines:
        return set()
    first_top = min(line["top"] for line in body_lines)
    return note_line_tops(body_lines, first_top - 1, notes_below(body_lines, first_top - 1))


class FigureZone:
    """Counters for the region of a page below its first figure caption.

    This replaces the sequential band that used to run from a caption to
    the figure's artwork id. Two independent facts killed that design:

    - `extract_text_lines()` is in content-stream order, not reading
      order, so ST's second drawing pass arrives AFTER the figure's own
      footnote and the page footer. A band closes on the footnote and
      everything in the second pass leaks (`lines.read_page_lines`).
    - Even sorted, a body-size line can sit INSIDE a figure -- RM0522
      prints a `31 24 15 7 0` bit header at 9.94 pt within one -- and any
      rule that ENDS filtering on a body-flow line ends it there.

    So there is no state machine left. Sorting makes the figure region
    contiguous, and every line below the first validated caption on the
    page is classified **on its own merits** by
    `noise.BodyMetrics.is_figure_artwork`: structural grammar first, then
    size-and-margin. A body line inside a figure is simply kept, and the
    artwork after it is still dropped.

    The zone is per PAGE and needs no hard bound: it cannot outlive the
    page that opened it, so a mis-validated caption costs the artwork
    test on the remainder of one page rather than pages of real prose.
    The old `MAX_BAND_PAGES` fail-safe is gone because what it protected
    against can no longer happen.

    Artwork ids are no longer terminators -- they drop as ordinary
    artwork, small and off-margin, whichever of ST's two conventions
    (`MSv66119V2`, `ai15797c`) a manual uses. `with_asset_id` keeps
    matching them purely as a health metric.
    """

    def __init__(self):
        self.opened = 0
        self.lines_dropped = 0
        self.chars_dropped = 0
        # Health metric: zones that contained one of ST's artwork ids at
        # all, which says whether zone boundaries agree with where ST
        # itself ends its drawings.
        self.with_asset_id = 0
        # A page whose last line below the caption was still artwork:
        # the figure spills onto the next page, where no zone covers it.
        self.pages_ending_mid_artwork = 0

    def open(self) -> None:
        self.opened += 1

    def drop(self, text: str) -> None:
        self.lines_dropped += 1
        self.chars_dropped += len(text)


class LogicalTableTracker:
    """One marker per LOGICAL table, not per detected grid.

    ST splits a long table across pages and each page's grid is detected
    separately, so one table emitted its marker twice (543 redundant
    markers across 248 sections on RM0486). RM0486 4.3.2 held nothing but
    a cross-reference and the same marker twice, from p202's `Table 9.
    BSEC internal input/output signals` and p203's `... (continued)`.

    The merge rule is `rmtables.merge.TableMerger`'s, unchanged: the same
    `table_number` on the same page as, or the page after, the previous
    segment. Nothing else is required -- not matching headers, not a
    `(continued)` marker, which that module treats as corroborating
    evidence only. Advancing `page_end` on every continuation is what
    carries a three-page table.

    Matching is on the parsed NUMBER and the page, never on the marker
    text: a caption can render slightly differently on the continuation
    page. And only markers are ever considered -- a prose cross-reference
    like `Table9 describes the user relevant internal signals...` is not
    a marker and never reaches this class.
    """

    def __init__(self):
        self._last: tuple[int, int] | None = None

    def reset(self) -> None:
        """Called when a new section opens: continuation is a property of
        one section's reading order, and a table whose continuation lands
        in the next section must still be marked there."""
        self._last = None

    def should_emit(self, number: int, page: int) -> bool:
        if self._last is not None:
            last_number, page_end = self._last
            if number == last_number and page in (page_end, page_end + 1):
                self._last = (number, page)
                return False
        self._last = (number, page)
        return True


def region_markers(raw_tables, captions, lines) -> tuple[list, set, int]:
    """Everything a page's table regions contribute to a section.

    Returns `(markers, suppressed_tops, uncaptioned)`:

    - `markers` -- `(top, table_number, text)` per captioned region, so
      the caller can slot each into reading order by its vertical
      position and collapse a multi-page table to one marker;
    - `suppressed_tops` -- body lines the markers make redundant: each
      region's own caption line, and each region's footnotes;
    - `uncaptioned` -- regions that produced no marker (counted, not
      marked; see the module docstring).

    **Caption suppression is by identity, never by pattern.** ST prints
    the caption ABOVE the ruled grid, so it sits outside the bbox and
    survives into the prose immediately before a marker restating it --
    1,278 duplicate pairs across 546 sections on RM0486. The line that is
    dropped is the one at the `top` `rmtables.captions.assign_caption`
    itself matched for THAT region, which is the same association the
    table extractor makes. A pattern would be wrong here: RM0486 10.3.1
    prints the prose cross-reference `Table33 summarizes the features
    supported by each internal SRAM.` three lines above the real caption
    `Table 33. Internal SRAM features`, and the two differ only by
    position and a missing space.
    """
    markers: list[tuple[float, int, str]] = []
    suppressed: set[float] = set()
    uncaptioned = 0
    for raw in raw_tables:
        caption = assign_caption(raw.bbox, captions)
        marker = table_marker(caption)
        if marker is None:
            uncaptioned += 1
            continue
        markers.append((raw.bbox[1], caption.number, marker))
        suppressed.add(caption.top)
        suppressed |= note_line_tops(lines, raw.bbox[3], notes_below(lines, raw.bbox[3]))
    return markers, suppressed, uncaptioned
