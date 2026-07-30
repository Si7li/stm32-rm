"""Footnote capture below a table.

Collects ALL footnote lines directly below a table's bbox -- no fixed
limit, since some tables have 10+ notes -- joining wrapped continuation
lines into their footnote. Stops at the next Table/Figure caption, a
numbered section heading, a blank gap, or an ST page footer; otherwise
footers ("47/1023", "RM0490 Rev 6") leak into the notes.
"""

from __future__ import annotations

import re

from .captions import CAPTION_RE, FIGURE_CAPTION_RE
from .headings import HEADING_RE

NOTE_RE = re.compile(r"^\(?(\d+)\)?[.)]\s+(\S.*)$")  # "1. ..." or "(1) ..."
FOOTER_RE = re.compile(
    r"(^\d{1,4}/\d{2,5}\b|\b(?:RM|DS|PM|AN|UM)\d{3,4}\s+Rev\s+\d+)"
)  # ST page footer, e.g. "77/1023" or "RM0490 Rev 6"

# pdfplumber's extract_text_lines() doesn't emit a literally-empty line for
# an ordinary paragraph break -- there is no blank *line*, just extra
# vertical space before the next one. Measured as a multiple of the
# previous line's own height (not a fixed point value, so it stays
# portable across manuals/fonts), a same-item line wrap runs ~1.0x that
# height and a new footnote in the same list ~1.6x, while a real paragraph
# break resuming after the last footnote runs ~3.2x. This ratio sits safely
# between "next footnote" and "paragraph break".
GAP_RATIO_THRESHOLD = 2.0


def notes_below(lines, bottom: float) -> list[str]:
    below = sorted((l for l in lines if l["top"] > bottom - 1), key=lambda l: l["top"])
    notes: list[str] = []
    current: str | None = None
    started = False
    prev_top: float | None = None
    prev_height: float | None = None
    for line in below:
        text = line["text"].strip()
        if not text:
            if started:
                break
            continue
        if (
            started
            and prev_top is not None
            and prev_height
            and (line["top"] - prev_top) > prev_height * GAP_RATIO_THRESHOLD
        ):
            break  # vertical gap wide enough to be a paragraph break, not a wrap
        if CAPTION_RE.match(text) or FIGURE_CAPTION_RE.match(text) or HEADING_RE.match(text) or FOOTER_RE.search(text):
            break
        if NOTE_RE.match(text):
            if current is not None:
                notes.append(current)
            current = text
            started = True
        elif current is not None:
            current += " " + text  # wrapped continuation of a footnote
        else:
            break  # first line isn't a footnote -> none
        prev_top, prev_height = line["top"], line["bottom"] - line["top"]
    if current is not None:
        notes.append(current)
    return notes
