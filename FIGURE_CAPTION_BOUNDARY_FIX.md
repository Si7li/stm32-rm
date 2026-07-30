# Task — stop figure grids inheriting a table's caption (supersedes FIGURE_BLEED_FIX_2.md)

## The root cause is not what we thought

`FIGURE_BLEED_FIX.md` assumed the lattice detector fuses a figure into a captioned table's
grid. Measured against RM0490, that is **not** the dominant mechanism. On page 75:

```
Table captions on page : [(24, top=211.3)]
Figure captions on page: [(Figure 4., top=428.3)]

find_tables returns FOUR SEPARATE grids:
  top=223.4  rows=5  -> assign_caption -> Table 24      <- the real table
  top=475.5  rows=3  -> assign_caption -> Table 24      <- Figure 4's boxes
  top=566.3  rows=1  -> assign_caption -> Table 24      <- Figure 4's boxes
  top=588.4  rows=2  -> assign_caption -> Table 24      <- Figure 4's boxes
```

`find_tables` fused nothing. The figure's ruled boxes are correctly returned as separate
grids. The bug is in **caption assignment**: `assign_caption` picks the nearest *Table*
caption above a grid and is blind to the `Figure 4.` caption sitting between them, so all
three figure grids are labelled "Table 24". `TableMerger.process_page` then merges them
(same number, same page) into one logical table.

This single mechanism explains both symptoms:

- **Row bleed** — the figure grids' rows are appended to the real table's rows.
- **Column padding** — `merge.py::_pad_row` widens every row to the widest merged grid.
  RM0486 T46's 16 columns and RM0522 T210's 29 columns come from here, not from
  `build_grid`. That is why the padding always exceeds the real table's width by exactly
  the figure's grid width.

## The fix — positional evidence, applied upstream

**A grid whose assigned Table caption is separated from it by a `Figure N.` caption line
does not belong to that table.**

This is direct evidence printed on the page by ST, not an inference about row shape or
header names. A genuine continuation of Table N never has a figure caption between Table N's
caption and itself — when ST interrupts a table with a figure, it reprints
`Table N. ... (continued)` below the figure, which `assign_caption` then picks as the nearer
caption.

### Implementation

1. In `captions.py`, add `figure_caption_tops(lines) -> list[float]` returning the `top` of
   every line matching the existing `FIGURE_CAPTION_RE`. Reuse the regex as-is — it already
   carries the tolerant `FIGURE_WORD_RE`/`NUMBER_RE` matching.

2. In `classify.py::classify_page`, after `caption = assign_caption(raw_table.bbox, table_captions)`
   and before the grid is accepted, reject the assignment when any figure-caption top lies
   strictly between the caption's top and the grid's top:

   ```
   if caption is not None and any(caption.top < f < raw_table.bbox[1] for f in fig_tops):
       -> not a caption_table; route to the figure_fragment path
       -> logger.info("grid on page %d (top=%.1f) below 'Figure' caption -- not Table %s",
                      page_number, raw_table.bbox[1], caption.number)
   ```

3. Compute `fig_tops` once per page, alongside the existing `find_captions(lines, page_number)`
   call, and pass it in.

That is the whole fix. It runs before the merger, so the figure grid is never merged and the
width padding never occurs.

### Safety guard — a table must never vanish

Rejecting grids must not leave a table number with no grid at all on a page where it was
the only one. Verified across all 1023 pages of RM0490: this never happens (every affected
table keeps at least one grid). Implement it as an assertion anyway — if every grid carrying
caption N on a page would be rejected, keep them all and log
`WARNING: refusing to reject every grid for Table N on page P`. Losing a table entirely is
far worse than leaving one contaminated.

## Nothing is discarded

Rejected grids currently follow the existing `figure_fragment` drop path, which is silent.
Write them instead to `<tables-dir>/<RM>_<Rev>/_figure_fragments.json`:

```json
{"document": "RM0490", "rev": "Rev 6", "fragments": [
  {"page": 75, "bbox": [...], "rows": [[...]],
   "figure_caption": "Figure 4. Example of disabling core debug access",
   "would_have_joined": "24"}
]}
```

This file lives **outside** the Sidekick payload — not in the `{"tables": [...]}` envelope,
not in any per-table file, never uploaded — so it carries zero schema risk while making
every removed row recoverable and every rejection auditable.

## Verified expected results — RM0490

Exactly six tables change. Each keeps at least one grid.

| Table | Page | grids kept / rejected | rows | cols | figure |
|---|---|---|---|---|---|
| T24 | 75 | 1 / 3 (6 rows) | 10 → 4 | 7 → 3 | Figure 4. Example of disabling core debug access |
| T43 | 224 | 1 / 1 (2 rows) | 1 → 1 (already cut by §A) | 4 → 4 | Figure 21. DMA block diagram |
| T78 | 388 | 1 / 2 (6 rows) | 9 → 3 | 5 → 5 | Figure 102. PWM output state following BRK |
| T80 | 396 | 1 / 1 (4 rows) | 11 → 7 | 6 → 6 | Figure 109. Example of counter operation |
| T84 | 478 | 1 / 1 (4 rows) | 11 → 7 | 6 → 6 | Figure 154. Example of counter operation |
| T155 | 954 | 2 / 5 (9 rows) | 16 → 7 | 5 → 2 | Figure 326. USB peripheral block diagram |

Cross-check: T78/T80/T84/T155's row counts are identical to what an independent row-level
text heuristic predicted, and T24 is a case no safe text heuristic could reach.

RM0522 and RM0486 could not be measured locally (PDFs not on disk). Fetch them via
`stm32fetch` and report the affected tables. Expect the RM0486 T46 / RM0522 T210 column
padding to disappear, since it originates in the merge this fix prevents.

## Relationship to the existing §A row-cut — KEEP IT

The landed `find_embedded_figure_row` cut handles the *other*, rarer mechanism: a figure
whose ruled boxes really are fused into the table's own grid, so its caption lands inside a
cell (RM0486 T187 p1227, RM0522 T210). That is genuine lattice fusion and this fix does not
address it. Keep §A exactly as implemented, including the line-level caption matching if
that has landed.

The two are complementary: this fix stops separate figure grids being *adopted*; §A cuts
figures that were *fused*.

## Explicitly NOT to be implemented

- `FIGURE_BLEED_FIX_2.md` Signals 2 (artwork ID), 3 (blank-row + sparse) and 4 (nameless
  column). All were row-level heuristics compensating for a root cause we had misdiagnosed.
  Signal 4 in particular destroys 20 real rows of RM0486 T585.
- `FIGURE_BLEED_FIX.md` §B, the width-based structural-break heuristic.

Leave `FIGURE_BLEED_FIX_2.md` on disk with its rejection rationale intact.

## What must NOT change

- Only the six tables above change in RM0490. Every other table byte-identical.
- Table count stays 178; `--validate` still reports `missing: []`.
- No table loses every grid; no table disappears.
- notes, legend, merged-cell fill, symbol remap, continuation merge across pages,
  classification and the semantic extractors are untouched.
- `columns == table_content.headers` still holds.
- Multi-page continuation still works — verify a known multi-page table (RM0490 T26,
  the FLASH register map) is unchanged.

## Tests

- Page-75 fixture: four grids, one caption, one figure caption between → exactly one grid
  accepted, three routed to fragments.
- A figure caption ABOVE the table caption must not reject anything (ordering matters).
- A `(continued)` caption below a figure must be picked as the nearer caption, so the
  continuation grid is accepted.
- The vanish-guard: a synthetic page where every grid would be rejected keeps them all
  and logs the warning.
- `_figure_fragments.json` round-trip: fragment rows + emitted rows equal the original
  pre-fix row count for each affected table.
