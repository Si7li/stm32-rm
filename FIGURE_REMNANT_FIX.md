# Task — the last figure remnant: match a Figure caption on any line of a cell

One table in the corpus still carries figure content: **RM0486 T187** (page 1227,
"Supported memories and transactions"). Its 16 real rows and 7 columns are correct; rows
16–17 are figure content.

This is the last known instance of figure bleed. Everything else was resolved by
`FIGURE_CAPTION_BOUNDARY_FIX.md` (21 tables) and the §A row-cut.

## Why it survives

`captions.py::find_embedded_figure_row` requires the `Figure N.` caption to **start** the
row's first non-empty cell:

```python
first_cell = next((cell for cell in row if cell and cell.strip()), None)
m = FIGURE_CAPTION_RE.match(first_cell)          # .match -> must start the cell
```

T187's row 16 is a single cell holding a paragraph fragment, a newline, then the caption:

```
"that Attribute memory space access timings are similar.\nFigure 179. NAND flash controller
 waveforms for common memory access"
```

`.match` anchors at the cell start, sees `that Attribute...`, and fails. Row 17 is the
waveform blob (`fmc_ker_ck\nNCEx\nMEMxSETM...`).

`FIGURE_BLEED_FIX_2.md` specified this as Signal 1 ("caption on ANY LINE of the row's only
populated cell"), but that document was superseded before the change landed, and
`FIGURE_CAPTION_BOUNDARY_FIX.md` said to keep §A "including the line-level caption matching
**if that has landed**" — it had not. That is the gap.

## Fix

In `find_embedded_figure_row`, split the first non-empty cell on `\n` and test each line:

```python
for i, row in enumerate(rows):
    first_cell = next((cell for cell in row if cell and cell.strip()), None)
    if first_cell is None:
        continue
    for line in str(first_cell).split("\n"):
        m = FIGURE_CAPTION_RE.match(line)
        if m:
            return i, str(_parse_number(m.group(1)))
```

**Keep both existing guards unchanged**:

- Only the row's **first non-empty cell** is examined.
- Every other cell in the row must be empty. This is what stops a prose cross-reference
  ("see Figure 21.") inside a real data cell from truncating a good table, and it holds in
  every verified case.

Keep `FIGURE_CAPTION_RE` as it is — it already carries the tolerant `FIGURE_WORD_RE` /
`NUMBER_RE` matching and the deliberate absence of a trailing `$` (documented in
`captions.py`: a figure embedded in a cell renders as one multi-line blob, and an anchored
`(.*)$` fails on every such row).

## Validation

1. RM0486 T187 → exactly **16 rows**, 7 columns. Last row is
   `['NAND 16-bit ', 'Asynchronous ', 'W ', '64 ', '16 ', 'Y ', 'Split into 4 FMC accesses ']`.
   No `Figure 179` text and no `fmc_ker_ck` blob anywhere in the record.
2. Its two dropped rows appear in `tables/RM0486_Rev4/_figure_fragments.json` (or the §A
   equivalent), so nothing is destroyed.
3. Corpus-wide: no row whose only populated cell contains a line matching a `Figure N.`
   caption — currently 1, must become 0.
4. No cell matches `\bMS[vc]\d{4,}` — currently 1 table (T187), must become 0.
5. **Only T187 changes.** Every other table byte-identical across all three manuals. Table
   counts stay 178 / 598 / 902.
6. `--validate` missing/extra sets unchanged; `columns == table_content.headers`; no null
   cells; no empty tables.
7. Per-table split files match; combined-vs-split deep-equality passes.

## Tests

- A row whose only populated cell is `"<prose>.\nFigure 179. <title>"` → cut at that row.
- A row whose cell starts directly with `Figure 21. <title>` → still cut (no regression).
- A row where a **data** cell elsewhere contains `"see Figure 21."` while other cells are
  populated → NOT cut.
- A multi-line cell with no figure caption → NOT cut.
- Tolerant matching still works on a split `F igure` and a spaced number.

## Out of scope

Do not revisit the caption-boundary rule, the fragment sidecar, `FIGURE_BLEED_FIX_2.md`'s
rejected Signals 2–4, or `FIGURE_BLEED_FIX.md` §B. This is a one-line matching change to an
existing, working function.
