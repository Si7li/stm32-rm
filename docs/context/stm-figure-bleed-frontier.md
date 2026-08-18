# stm figure bleed frontier

> Root cause of figure content bleeding into STM32 tables, and the standing no-data-loss constraint

Figure content bleeding into captioned tables had a misdiagnosed root cause. It is NOT the
lattice detector fusing regions (what `FIGURE_BLEED_FIX.md` assumed). Measured on RM0490
p75: `find_tables` returns the figure's boxes as separate grids, but `assign_caption` labels
them with the nearest *Table* caption above, ignoring the `Figure N.` caption in between, and
`TableMerger` then merges them. `merge.py::_pad_row` widening rows to the widest merged grid
is where the 16- and 29-column padding comes from — not `build_grid`.

Fix is in `FIGURE_CAPTION_BOUNDARY_FIX.md`: reject a caption assignment when a `Figure N.`
caption line sits between the caption and the grid. Verified on all 1023 RM0490 pages —
fires on exactly 6 tables, zero false positives, no table loses every grid.

**Standing constraint from Khalil (2026-07-29): never lose information from tables.**
Contamination is recoverable; silent row loss is not. This is why row-level heuristics
(artwork IDs, blank-row runs, nameless-column runs) were rejected — the nameless-column one
destroyed 20 real rows of RM0486 T585, whose header was itself mis-extracted. Prefer
positional evidence ST actually printed over inference about row shape, and route anything
removed to an audit sidecar outside the Sidekick payload.

Context in [stm-table-extractor-context](stm-table-extractor-context.md).
