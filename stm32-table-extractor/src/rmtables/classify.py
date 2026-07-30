"""Per-page classification: caption_table vs register_layout vs figure_fragment.

The "lines" ruled-grid strategy catches every ruled box on a page, and an
STM32 reference manual draws three very different things with ruled boxes:
real captioned tables, per-register bit-field diagrams (no caption at all),
and figure/schematic boxes (clock trees, block diagrams, power domains).
Only the first two are real data; figure fragments are always dropped, but
the reason is logged at DEBUG so the filtering stays auditable.
"""

from __future__ import annotations

import logging
from collections import defaultdict

from .captions import (
    assign_caption,
    figure_caption_tops,
    find_embedded_figure_row,
    nearest_figure_caption,
)
from .legends import NOTE_LEGEND_RE, assign_legend_table, find_legends
from .notes import notes_below

logger = logging.getLogger(__name__)

MIN_FIGURE_WIDTH = 150  # points; real tables span most of the text column
MIN_NON_EMPTY_CELLS = 4


def _trim_trailing_empty_columns(rows: list) -> list:
    """FIGURE_BLEED_FIX.md §C: drop trailing columns that are empty across
    EVERY surviving row (including the header, i.e. row 0) -- undoes the
    figure grid's width padding once its rows are gone (verified: RM0522
    T210's 29 columns, padded wide solely by the now-removed figure block,
    collapse back to its real 3). Called ONLY on a `raw_table.rows` that
    §A actually truncated -- an uncut wide table's own genuine trailing
    empty columns (11 verified elsewhere in the corpus, none of them a
    figure-bleed case) are deliberately left untouched by this function
    never being called on them."""
    if not rows:
        return rows
    width = max(len(r) for r in rows)
    last_non_empty = -1
    for col in range(width):
        if any(col < len(r) and r[col] not in (None, "") for r in rows):
            last_non_empty = col
    return [r[: last_non_empty + 1] for r in rows]


def _non_empty_cell_count(raw_table) -> int:
    return sum(1 for row in raw_table.rows for cell in row if cell not in (None, ""))


def _is_nested(raw_table, all_raw_tables) -> bool:
    ax0, atop, ax1, abottom = raw_table.bbox
    area_a = (ax1 - ax0) * (abottom - atop)
    for other in all_raw_tables:
        if other is raw_table:
            continue
        bx0, btop, bx1, bbottom = other.bbox
        if bx0 <= ax0 and btop <= atop and bx1 >= ax1 and bbottom >= abottom:
            area_b = (bx1 - bx0) * (bbottom - btop)
            if area_b > area_a:
                return True
    return False


def _figure_drop_reason(raw_table, all_raw_tables, lines) -> str:
    if _is_nested(raw_table, all_raw_tables):
        return "nested_in_another_grid"
    x0, _, x1, _ = raw_table.bbox
    width = x1 - x0
    if width < MIN_FIGURE_WIDTH:
        return f"narrow_bbox(width={width:.1f})"
    non_empty = _non_empty_cell_count(raw_table)
    if non_empty < MIN_NON_EMPTY_CELLS:
        return f"too_few_cells({non_empty})"
    fig_caption = nearest_figure_caption(lines, raw_table.bbox[1])
    if fig_caption:
        return f"under_figure_caption({fig_caption!r})"
    return "no_caption_no_register_signal"


def _caption_boundary_rejections(candidates, fig_tops: list, page_number: int) -> set:
    """FIGURE_CAPTION_BOUNDARY_FIX.md: `id(raw_table)` for every grid in
    `candidates` (`[(raw_table, caption), ...]`, already filtered to those
    with a Table caption assigned) whose assigned caption is separated from
    it by a `Figure N.` caption line -- i.e. NOT actually a grid of that
    table, just the nearest Table caption `assign_caption` could find
    because it's blind to the Figure caption sitting between them.

    Grouped by `caption.number` first so the vanish guard can see, for a
    given table number, whether EVERY grid assigned to it on this page
    would be rejected -- losing a table entirely is far worse than leaving
    one contaminated, so in that case none of them are rejected and a
    WARNING is logged instead.
    """
    by_number = defaultdict(list)
    for raw_table, caption in candidates:
        by_number[caption.number].append((raw_table, caption))

    rejected_ids: set = set()
    for number, entries in by_number.items():
        rejects = [
            (raw_table, caption) for raw_table, caption in entries
            if any(caption.top < f < raw_table.bbox[1] for f in fig_tops)
        ]
        if rejects and len(rejects) == len(entries):
            logger.warning(
                "refusing to reject every grid for Table %s on page %d", number, page_number,
            )
            continue
        rejected_ids.update(id(raw_table) for raw_table, _ in rejects)
    return rejected_ids


def classify_page(
    page_number: int,
    raw_tables,
    lines,
    table_captions,
    heading_tracker,
    table_merger,
    register_merger,
) -> tuple[int, list, list]:
    """Route each raw grid on this page to the right merger.

    Returns `(dropped, explicit_legend_attachments, figure_fragments)`:
    `explicit_legend_attachments` is a list of `(table_number, text)` pairs
    from `Legend[s] for Table N:` lines -- position-independent, so the
    caller must apply them against `table_merger` (retrying across pages,
    since the target table may not exist yet or may already be closed)
    rather than this function attaching them directly. `figure_fragments`
    is a list of dicts (FIGURE_CAPTION_BOUNDARY_FIX.md) for every grid
    rejected by the caption-boundary check below -- the caller is
    responsible for writing them out (nothing is discarded), since this
    function has no notion of the manual's output path.
    """
    consumed_by_register = register_merger.process_page(
        page_number, raw_tables, lines, heading_tracker
    )

    page_legends = find_legends(lines)
    bare_legends = [l for l in page_legends if l["table_number"] is None]
    explicit_legends = [
        (l["table_number"], l["text"]) for l in page_legends if l["table_number"] is not None
    ]

    # FIGURE_CAPTION_BOUNDARY_FIX.md: positional evidence ST prints on the
    # page itself -- a genuine continuation of Table N never has a figure
    # caption printed between Table N's own caption and the continuation
    # grid (ST reprints "Table N. ... (continued)" below the figure
    # instead, which `assign_caption` then picks as the nearer caption).
    # Computed once per page, alongside the existing `find_captions` call
    # the caller already made.
    fig_tops = figure_caption_tops(lines)
    candidates = [
        (raw_table, assign_caption(raw_table.bbox, table_captions))
        for raw_table in raw_tables
        if id(raw_table) not in consumed_by_register
    ]
    candidates = [(rt, cap) for rt, cap in candidates if cap is not None]
    rejected_ids = _caption_boundary_rejections(candidates, fig_tops, page_number)

    captioned_pairs = []
    figure_fragments = []
    dropped = 0
    # FIGURE_BLEED_FIX.md §A: table_number -> True once an embedded `Figure
    # N.` caption has been cut for it on THIS page. A dense figure (e.g.
    # RM0522 Table 210's 128-bit bit-cell diagram) is very often detected as
    # MANY separate small ruled regions rather than one, each independently
    # assigned this same caption (nothing else intervenes) -- only the
    # FIRST such region prints the literal "Figure N." caption text, but
    # every region after it is still figure debris. Once the boundary is
    # crossed for a table_number, every later same-page raw_table sharing
    # it is skipped outright, with no need to re-detect a caption row that
    # was never repeated in each fragment.
    figure_bleed_seen: set[int] = set()
    for raw_table in raw_tables:
        if id(raw_table) in consumed_by_register:
            continue
        caption = assign_caption(raw_table.bbox, table_captions)
        if caption is not None:
            if id(raw_table) in rejected_ids:
                # FIGURE_CAPTION_BOUNDARY_FIX.md: a Figure caption sits
                # between this grid and the Table caption `assign_caption`
                # picked for it -- this is one of Figure N.'s own ruled
                # boxes, not a grid of that table at all. Route it to the
                # figure_fragment path (never merged, so the width padding
                # this whole fix exists to prevent never occurs) and keep
                # it recoverable rather than silently dropping it.
                dropped += 1
                fig_caption = nearest_figure_caption(lines, raw_table.bbox[1])
                logger.info(
                    "grid on page %d (top=%.1f) below 'Figure' caption -- not Table %s",
                    page_number, raw_table.bbox[1], caption.number,
                )
                figure_fragments.append({
                    "page": page_number,
                    "bbox": list(raw_table.bbox),
                    "rows": raw_table.rows,
                    "figure_caption": fig_caption,
                    "would_have_joined": str(caption.number),
                })
                continue

            if caption.number in figure_bleed_seen:
                continue

            section = heading_tracker.heading_before(raw_table.bbox[1])
            # notes_below/legend matching key off the table's true bbox
            # bottom, computed BEFORE any §A cut below -- RM0522 Table
            # 160's real footnote sits below the embedded figure, so
            # trimming the bbox first would silently drop it. Only
            # `raw_table.rows` is ever cut, never `raw_table.bbox`.
            notes = notes_below(lines, raw_table.bbox[3])
            # A numbered footnote that is itself a legend (e.g. "3. Legends:
            # ...") is kept in `notes` AND mirrored into `legend`.
            legend = [n for n in notes if NOTE_LEGEND_RE.match(n)]

            # FIGURE_BLEED_FIX.md §A: a figure printed directly beneath a
            # captioned table with little/no gap ends up fused into the
            # same ruled grid, or (same effect) assigned this same caption
            # as if it were a same-page continuation -- either way its
            # `Figure N.` caption line lands as a data row. Cut here,
            # BEFORE table_merger.process_page below, so a wide figure
            # grid's column count never pads into a genuine continuation's
            # rows on another page (verified necessary for RM0490 Table 38
            # -- MERGE_DUPLICATE_FIX.md's own concern, still true here).
            #
            # §B (a structural-break heuristic for a figure with no visible
            # caption row) and §D (preventing the fusion during detection)
            # are deliberately NOT implemented: §A alone (plus the
            # same-page suppression above) catches every verified case
            # (RM0490 T43, RM0522 T160/T210), and §B's "sudden width
            # change" signal is exactly what would risk truncating a
            # genuine wide table (register maps, multi-column parameter
            # tables) for no benefit on the cases actually seen.
            #
            # FIGURE_CAPTION_BOUNDARY_FIX.md: this handles the rarer,
            # complementary mechanism -- a figure genuinely fused into the
            # SAME ruled grid as the table, so its caption lands inside a
            # cell (RM0486 T187, RM0522 T210's own fused rows). The
            # caption-boundary rejection above stops a SEPARATE figure grid
            # from being adopted in the first place; this cuts one that
            # was actually fused.
            cut = find_embedded_figure_row(raw_table.rows)
            if cut is not None:
                cut_index, figure_number = cut
                dropped_rows = len(raw_table.rows) - cut_index
                raw_table.rows = _trim_trailing_empty_columns(raw_table.rows[:cut_index])
                logger.info(
                    "cut table %s at embedded 'Figure %s' (page %d), dropped %d rows",
                    caption.number, figure_number, page_number, dropped_rows,
                )
                figure_bleed_seen.add(caption.number)
                if not raw_table.rows:
                    # Nothing but figure content survived (e.g. RM0490 T43's
                    # second, separately-detected ruled region, which is
                    # ENTIRELY the figure). Merging zero rows into an
                    # existing same-number LogicalTable is a harmless no-op
                    # in merge.py (and lets this segment's own notes/legend,
                    # already captured above from its true bbox, still
                    # reach the real table) -- safe here only because an
                    # earlier same-page, same-number entry already exists to
                    # merge into. Otherwise there is nothing to merge into,
                    # so skip rather than risk emitting a phantom empty
                    # table.
                    merges_into_earlier_segment = any(
                        pair[1].number == caption.number for pair in captioned_pairs
                    )
                    if not merges_into_earlier_segment:
                        continue

            captioned_pairs.append((raw_table, caption, section, notes, legend))
            continue
        dropped += 1
        reason = _figure_drop_reason(raw_table, raw_tables, lines)
        logger.debug(
            "dropped figure_fragment on page %d (bbox=%s): %s",
            page_number,
            raw_table.bbox,
            reason,
        )

    # A bare "Legend:" attaches to the nearest captioned table above it on
    # this same page (same below-the-table rule notes use, in reverse).
    for legend_block in bare_legends:
        target_number = assign_legend_table(legend_block["top"], captioned_pairs)
        if target_number is None:
            continue
        for i, pair in enumerate(captioned_pairs):
            if pair[1].number == target_number:
                pair[4].append(legend_block["text"])
                break

    table_merger.process_page(page_number, captioned_pairs)
    return dropped, explicit_legends, figure_fragments
