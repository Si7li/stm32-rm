"""Table legend capture ("Legend:" / "Legend for Table N:" / "Legends: ...").

Legends decode a table's symbols/abbreviations and are important content,
but they are NOT numbered footnotes -- `notes_below` (which requires a
leading "1."/"(1)" marker) never reaches them. This mirrors that same
below-the-table line-walking (join wrapped continuation lines, stop at a
blank line / next caption / heading / page footer / next legend), starting
from a `Legend[s]` line instead of a numbered one.

Association is two-tiered:
- `Legend[s] for Table N:` names its target explicitly and
  position-independently -- the table it decodes need not be the nearest
  one on the page (verified in RM0008: Table 1's legend prints *above* its
  own caption).
- A bare `Legend:` is attached by the caller to the nearest captioned table
  above it on the same page (the same "below the table" rule notes use, in
  reverse -- see `assign_legend_table`).
"""

from __future__ import annotations

import re

from .captions import CAPTION_RE, FIGURE_CAPTION_RE
from .cells import fix_symbols
from .headings import HEADING_RE
from .notes import FOOTER_RE, GAP_RATIO_THRESHOLD

LEGEND_START_RE = re.compile(r"^\s*legends?\b.*?[:\-]\s*(.*)$", re.IGNORECASE)
LEGEND_FOR_TABLE_RE = re.compile(r"legends?\s+for\s+table\s*(\d+)", re.IGNORECASE)

# A numbered footnote that is itself a legend, e.g. "3. Legends: S = Start, ...".
NOTE_LEGEND_RE = re.compile(r"^\(?\d+\)?[.)]\s*legends?\b", re.IGNORECASE)


def find_legends(lines) -> list[dict]:
    """Every `Legend[s][ for Table N]: ...` block on a page, in document
    order, with wrapped continuation lines joined into one `fix_symbols`-ed
    string. Each entry: `{"top": float, "table_number": int | None, "text": str}`
    -- `table_number` is set only for an explicit "...for Table N:" line.
    """
    ordered = sorted(lines, key=lambda l: l["top"])
    legends: list[dict] = []
    current: dict | None = None
    prev_top: float | None = None
    prev_height: float | None = None

    def flush():
        nonlocal current
        if current is not None:
            current["text"] = fix_symbols(current["text"].strip())
            legends.append(current)
            current = None

    for line in ordered:
        text = line["text"].strip()
        if not text:
            flush()
            prev_top = prev_height = None
            continue

        gap_break = (
            current is not None
            and prev_top is not None
            and prev_height
            and (line["top"] - prev_top) > prev_height * GAP_RATIO_THRESHOLD
        )
        m_start = LEGEND_START_RE.match(text)
        is_stop_line = bool(
            not m_start
            and (
                CAPTION_RE.match(text)
                or FIGURE_CAPTION_RE.match(text)
                or HEADING_RE.match(text)
                or FOOTER_RE.search(text)
            )
        )

        if gap_break or is_stop_line:
            flush()

        if m_start:
            flush()
            table_m = LEGEND_FOR_TABLE_RE.search(text)
            current = {
                "top": line["top"],
                "table_number": int(table_m.group(1)) if table_m else None,
                "text": m_start.group(1),
            }
        elif current is not None and not is_stop_line:
            current["text"] += " " + text

        prev_top, prev_height = line["top"], line["bottom"] - line["top"]

    flush()
    return legends


def assign_legend_table(legend_top: float, captioned_pairs) -> int | None:
    """For a bare (position-dependent) legend, the number of the nearest
    captioned table above it on the same page. `captioned_pairs` is the
    (raw_table, caption, ...) sequence already assembled by `classify_page`
    for this page -- mirrors `captions.assign_caption`'s "nearest above"
    rule, keyed on a table's own bottom instead of a caption's top."""
    above = [
        (raw_table.bbox[3], caption.number)
        for raw_table, caption, *_ in captioned_pairs
        if raw_table.bbox[3] <= legend_top
    ]
    if not above:
        return None
    return max(above, key=lambda pair: pair[0])[1]
