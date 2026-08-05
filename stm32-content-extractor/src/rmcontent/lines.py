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


def page_lines(page, y_tolerance: float = DEFAULT_Y_TOLERANCE) -> list[dict]:
    """This page's text lines, with subscripts merged into their baseline.

    Returns pdfplumber's own line dicts unchanged otherwise -- `text`,
    `top`, `bottom`, `x0`, `x1` and `chars` are all still present, so
    callers that classify by position or font size are unaffected.
    """
    return page.extract_text_lines(y_tolerance=y_tolerance)
