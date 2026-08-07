"""The one place `rmcontent` takes text lines off a page.

Subscripts sit below the baseline, so at pdfplumber's default tolerance
`extract_text_lines()` returns them as lines of their own and every
signal name breaks apart. RM0486 13.4 reads::

    '• V : optional external power supply for backup domain when V is not present'
    'BAT DD'
    '(V mode)'
    'BAT'

`V_BAT` and `V_DD` become a bare `V` plus an orphan `BAT DD`. Measured:
717 lone-subscript lines across 332 sections in RM0486, 304 across 60 in
RM0522, 229 across 47 in RM0490 -- `V_CORE` (269x), `t_SAMPLING`
(132-144x), `V_BAT` (61x), `V_DDIO2/3/4/5`, `V_REF+`, `f_PCLK`.

Raising `y_tolerance` to 5 merges each subscript back into its baseline
line and orders the result by x, which is exactly the required
behaviour::

    y_tolerance=5 -> '• VBAT: optional external power supply for backup domain when VDD is not present'

This does NOT need the char-clustering algorithm `cells.py` uses for cell
text: the subscript offset is ~3.7 pt against ~12 pt body line spacing,
so one tolerance separates them with room to spare. Verified on the test
pages, 5 and 7 give byte-identical output -- a plateau, not a
knife-edge.

**Every caller must use the same value.** Heading tracking, caption
detection, note capture, the figure band and section assembly all key off
line positions in the same list; a mismatch between them would misalign
those positions against each other. That is why this function exists
rather than the parameter being passed at each call site.
"""

from __future__ import annotations

import logging

logger = logging.getLogger("rmcontent.lines")

#: Merges a subscript into its baseline line without reaching the next
#: body line. Overridable per run via `--y-tolerance`.
DEFAULT_Y_TOLERANCE = 5


def _position(line) -> tuple:
    return (line["top"], line.get("x0", 0.0))


def is_top_ordered(lines) -> bool:
    """Whether `lines` are already in reading order, top to bottom."""
    return all(a["top"] <= b["top"] for a, b in zip(lines, lines[1:]))


def read_page_lines(page, y_tolerance: float = DEFAULT_Y_TOLERANCE) -> tuple[list, bool]:
    """`(lines_in_reading_order, was_reordered)` for one page.

    `extract_text_lines()` returns lines in PDF **content-stream order**,
    which is not reading order. ST draws a figure in more than one content
    pass, so a page's tops ascend, jump back and ascend again. RM0490 page
    290 (Figure 30, the ADC block diagram) returns::

        idx 3      top 138.2  Figure 30. ADC block diagram
        idx 4-39   top 164 -> 563.8   artwork, first pass
        idx 40     top 575.9  1. TRGi are mapped at product level...
        idx 41     top 744.5  page footer
        idx 42     top 235.6  BHA          <- second pass starts again
        idx 43-74  top 171.6 -> 503.5     VREF+, CHSEL[22:0], TRG0..TRG7

    Anything that walks the list as a sequence -- an open/closed band, a
    caption's "lines below it" -- sees the figure interrupted by its own
    footnote and footer, then resumed. Measured on 60 random pages per
    manual, 12-22% of pages are affected: RM0486 22% (median 13 content
    runs, max 38), RM0008 17%, RM0522 15%, RM0490 12% (median 14, max 33).

    Sorting is safe because the anomaly is confined to small-font runs:
    no page was found where BODY-sized lines are out of order, so a page
    that was already sorted must produce byte-identical output. That is a
    validation gate, not an assumption.

    Line dicts are returned unchanged otherwise -- `text`, `top`,
    `bottom`, `x0`, `x1` and `chars` are all still present.
    """
    raw = page.extract_text_lines(y_tolerance=y_tolerance)
    return sorted(raw, key=_position), not is_top_ordered(raw)


def page_lines(page, y_tolerance: float = DEFAULT_Y_TOLERANCE) -> list[dict]:
    """This page's text lines in reading order, subscripts merged."""
    return read_page_lines(page, y_tolerance)[0]
