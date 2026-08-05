"""Figure-caption ground truth, and the validation that guards the band rule.

A `[Figure N. ...]` marker is normally harmless if it is wrong -- one
spurious line. It stops being harmless once a caption also *opens an
artwork band*, because a false caption then starts deleting real content.
So the caption that opens a band has to be checked first.

RM0486 12.4.3 is the case that proves it. It emits two markers for the
same number::

    [Figure 14. shows the functional view of TAG and data memories, ...]
    [Figure 14. CACHEAXI TAG and data memories functional view]

The first is a prose cross-reference (`Figure 14. shows the functional
view of...`) read as a caption. Three independent tests reject it, any
one of them sufficient:

1. the word after `Figure N.` is a lowercase verb;
2. the title does not match the manual's own List of figures entry;
3. the number is not in the List of figures at all.

The List of figures is the same kind of ground truth the List of tables
provides for `rmtables`, and it is parsed with the same tolerant entry
shape -- `rmtables.captions.FIGURE_WORD_RE` and `NUMBER_RE`, which absorb
ST's intra-word space splitting, plus the dot-leader-then-page tail of
`LIST_ENTRY_RE`. It is used to VALIDATE a caption found in the body,
never to enumerate figures.
"""

from __future__ import annotations

import logging
import re

from rmtables.captions import (
    CAPTION_SEARCH_BOUND,
    FIGURE_WORD_RE,
    NUMBER_RE,
    _parse_number,
)
from rmtables.cells import fix_symbols

from .lines import DEFAULT_Y_TOLERANCE, page_lines

logger = logging.getLogger("rmcontent.figures")

# "Figure 1. System architecture . . . . . . . . 42" (RM0522 p69). The
# same shape as `rmtables.captions.LIST_ENTRY_RE`, with FIGURE_WORD_RE in
# place of TABLE_WORD_RE -- but WITHOUT its mandatory trailing page
# number, because that pattern has the wrapped-entry defect
# `contents.py` documents at length: a title too long for one line puts
# the dot leaders and the page number on the NEXT line.
#
# Measured: requiring the tail parses 316 of RM0490's figures and 1,039
# of RM0486's, leaving 12 and 48 numbers "absent" from a list that in
# fact contains them -- every one of them a real caption
# ("Counter timing diagram, update event when ARPE=0", "HPDMA channel
# execution and linked-list programming"). Rejecting those would have
# deleted real markers. Only the number and title are needed here, so
# the tail is optional and a continuation line extends the title.
FIGURE_LIST_ENTRY_RE = re.compile(
    FIGURE_WORD_RE + r"\s*(" + NUMBER_RE + r")\s*[.․]\s*(.*)$"
)

# The trailing "<dot leaders><page>" stripped off a title, when present.
_TRAILING_PAGE_RE = re.compile(r"\s*(?:\.\s*)+\d{1,5}\s*$")

# How far into the PDF the List of figures can start. ST front matter
# never runs past this, even on a 4,669-page manual.
MAX_FRONT_MATTER_PAGES = 250

# Below this many entries the parse is not trusted, and membership is not
# used to reject anything -- a failed parse must never suppress captions.
MIN_TRUSTED_ENTRIES = 20

# `Figure 14. shows the functional view of ...` -- a cross-reference
# written as a sentence. ST captions are noun phrases and never open with
# a verb like this.
CROSS_REFERENCE_VERBS = frozenset({
    "shows", "show", "lists", "list", "gives", "give", "describes",
    "describe", "illustrates", "illustrate", "presents", "present",
    "details", "detail", "provides", "provide", "summarizes",
    "summarize", "summarises", "summarise", "displays", "display",
    "depicts", "depict", "represents", "represent", "indicates",
    "indicate", "explains", "explain",
})

# Titles are compared on a prefix of their letters and digits, for the
# same reason chapter titles are: ST wraps a long title in the List of
# figures, and a superscript can be lifted out of the body caption.
TITLE_MATCH_CHARS = 24
TITLE_MIN_CHARS = 8


def _title_key(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (text or "").casefold())


def parse_list_of_figures(
    pdf, y_tolerance: float = DEFAULT_Y_TOLERANCE
) -> dict[int, str]:
    """`{figure_number: title}` from the front-matter List of figures."""
    entries: dict[int, str] = {}
    started = False
    pending: int | None = None
    limit = min(MAX_FRONT_MATTER_PAGES, len(pdf.pages))
    for i in range(limit):
        page = pdf.pages[i]
        try:
            text = page.extract_text() or ""
            is_list_page = "List of figures" in text
            if is_list_page:
                started = True
                for line in page_lines(page, y_tolerance):
                    raw = fix_symbols(line["text"]).strip()
                    m = FIGURE_LIST_ENTRY_RE.search(raw)
                    if m and m.start() <= CAPTION_SEARCH_BOUND:
                        pending = _parse_number(m.group(1))
                        entries.setdefault(
                            pending, _TRAILING_PAGE_RE.sub("", m.group(2)).strip()
                        )
                        continue
                    # A wrapped entry's continuation: the rest of its
                    # title, and the dot leaders it was missing.
                    if pending is not None and raw:
                        rest = _TRAILING_PAGE_RE.sub("", raw).strip()
                        if rest:
                            entries[pending] = f"{entries[pending]} {rest}".strip()
                        pending = None
            elif started:
                break
        except Exception:
            logger.debug("List-of-figures scan failed on page %d", i + 1, exc_info=True)
        finally:
            page.flush_cache()

    if len(entries) < MIN_TRUSTED_ENTRIES:
        logger.warning(
            "List of figures yielded only %d entries; caption validation will "
            "not use it to reject anything", len(entries),
        )
    logger.info("List of figures: %d entries", len(entries))
    return entries


class FigureCaptions:
    """Validates a body figure caption against the List of figures.

    Validation is deliberately TWO-TIER, because the three tests differ
    sharply in precision when measured against the corpus:

    * **`is_caption` -- the verb test -- decides whether a marker is
      emitted at all.** Measured across 1,383 markers in RM0486 and
      RM0490 it fires 3 times, all 3 genuine cross-references: RM0486
      12.4.3's `Figure 14. shows the functional view of...` and
      53.3.25's `shows how the 'gated on A & B' mode is handled` and
      `presents waveforms and corresponding values`. Zero false
      positives. Precise enough to destroy a marker with.

    * **`may_open_band` -- the List-of-figures tests -- decides whether
      a band opens.** These are weaker. After the wrapped-entry fix they
      still reject 25 RM0486 captions and 2 RM0490 ones on a title
      mismatch, and every one is a REAL caption whose body text differs
      from the listing only because a subscript was lifted out (`Device
      startup (V supplied directly from SMPS...` against the listing's
      `V_DD`). Rejecting those would delete real markers, so they do not
      veto the marker -- they only withhold the authority to bound a
      deletion.

    The asymmetry follows the rule the whole band design is built on:
    uncertainty costs a leak, never a deletion.
    """

    def __init__(self, listed: dict | None = None):
        self.listed = listed or {}
        self.trusted = len(self.listed) >= MIN_TRUSTED_ENTRIES
        self.rejected: list[tuple[int, str, str]] = []  # (number, title, reason)
        self.unbanded: list[tuple[int, str, str]] = []  # marker kept, no band

    def is_caption(self, number: int, title: str) -> bool:
        """Whether `Figure {number}. {title}` is a caption at all."""
        first = re.split(r"[\s,;:]+", title.strip(), maxsplit=1)[0].strip(".")
        if first[:1].islower() and first.casefold() in CROSS_REFERENCE_VERBS:
            logger.debug("rejecting figure caption %d %r: verb", number, title[:70])
            self.rejected.append((number, title, f"opens with the verb {first!r}"))
            return False
        return True

    def may_open_band(self, number: int, title: str) -> bool:
        """Whether this caption is trustworthy enough to bound a deletion."""
        if not self.trusted:
            return False

        expected = self.listed.get(number)
        if expected is None:
            self.unbanded.append((number, title, "number absent from the List of figures"))
            return False

        got, want = _title_key(title), _title_key(expected)
        n = min(TITLE_MATCH_CHARS, len(got), len(want))
        if n < min(TITLE_MIN_CHARS, len(want)) or got[:n] != want[:n]:
            self.unbanded.append((
                number, title,
                f"title does not match the List of figures entry {expected[:50]!r}",
            ))
            return False
        return True
