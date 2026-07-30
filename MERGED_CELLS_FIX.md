# Fix task — rmtables: fill merged (spanned) cells instead of leaving `""`

## Problem

Tables with **merged cells** lose data. A cell that spans multiple rows (rowspan)
or multiple columns (colspan) is reported by pdfplumber **once**, at the span's
top-left "home" position; the covered positions come back empty, and the current
code writes them as `""`. Result: rows that should carry a repeated value are blank.

Verified example — **Table 16 "Mass erase overview" (page 62)**. Its merge geometry
(confirmed via cell bboxes): `SEC_PROT="0"`, `PCROP_RDP="x"`, `CPU bus error="No"`
each span **4 rows**; `Comment="Erase aborted..."` and `WRPERR="Yes"` span **3 rows**;
and the bottom row's `"x"` spans **3 columns** (PCROP+WRP+PCROP_RDP). The current
output blanks all the covered positions; the correct output repeats each merged value
into every cell it visually covers.

## Root cause

Row-building does roughly `[cell_text(c) if c else "" for c in row.cells]`, which only
fills the home position and turns every spanned position into `""`. It never
propagates a merged cell across the grid positions its rectangle covers.

## The fix — geometry-based span explosion (validated, reproduces the expected output)

Replace the per-table row assembly with a routine that assigns each **drawn** cell's
text to **every** grid `(row, col)` its bbox overlaps. Genuinely empty drawn cells
stay `""`. This handles rowspans and colspans uniformly. Keep `cell_text` (the
rotated-text un-reversal) exactly as-is; only change how cells are placed into the grid.

```python
def build_grid(table, chars):
    """Explode merged cells: each drawn cell's text fills every grid position its
    rectangle covers (rowspan + colspan). Empty cells stay "". `chars` = page.chars."""
    rows = table.rows
    xs = sorted({round(e,1) for r in rows for cell in r.cells if cell for e in (cell[0], cell[2])})
    ys = sorted({round(r.bbox[1],1) for r in rows} | {round(rows[-1].bbox[3],1)})
    ncol = len(xs) - 1
    nrow = len(rows)
    def cols(x0, x1): return [i for i in range(ncol) if not (x1 <= xs[i]+0.5 or x0 >= xs[i+1]-0.5)]
    def rws(top, bottom): return [i for i in range(nrow) if not (bottom <= ys[i]+0.5 or top >= ys[i+1]-0.5)]
    grid = [['' for _ in range(ncol)] for _ in range(nrow)]
    seen = set()
    for r in rows:
        for cell in r.cells:
            if not cell:
                continue
            key = tuple(round(v,1) for v in cell)
            if key in seen:
                continue
            seen.add(key)
            txt = cell_text(chars, cell)
            for ri in rws(cell[1], cell[3]):
                for ci in cols(cell[0], cell[2]):
                    if grid[ri][ci] == '':
                        grid[ri][ci] = txt
    return grid
```

## Integration

- Use `build_grid(table, page.chars)` wherever a single table's rows are currently
  built (in `extract.py`), returning the full grid; `headers = grid[0]`,
  `rows = grid[1:]`. Everything downstream (`captions.py` filtering, `merge.py`
  continuation, `notes.py`, `exporter.py`) is unchanged — it still receives a grid.
- Run this at extraction time, **before** continuation merge. The dedup-header check in
  `merge.py` (drop a continuation's first row if it equals the header) still works.
- Merged/spanned positions are now filled; only cells with no drawn text remain `""`.
  Do **not** reintroduce a `None -> ""` path.

## Acceptance test (add to tests)

`build_grid` on **page 62** must equal exactly:

```python
[["SEC_PROT","PCROP","WRP","PCROP_RDP","Comment","WRPERR","CPU bus error"],
 ["0","No","No","x","Memory is erased","No","No"],
 ["0","No","Yes","x","Erase aborted (no erase started)","Yes","No"],
 ["0","Yes","No","x","Erase aborted (no erase started)","Yes","No"],
 ["0","Yes","Yes","x","Erase aborted (no erase started)","Yes","No"],
 ["1","x","x","x","Erase aborted (no erase started)","No","Yes"]]
```

## Intended consequences (these are correct, not regressions — don't "fix" them)

- **Row-span tables**: a group label now repeats down its rows (e.g. Table 10 "Flash
  memory organization": `Information block` / `Main flash memory` repeat on each of
  their rows). This is the desired behavior.
- **Register-map tables**: a field label spanning several bit columns now repeats
  across those columns (e.g. a 3-bit field label appears in all 3 bit columns), and a
  register's `Offset` repeats onto its `Reset value` row. Consistent with the fill
  semantics; good for row-level retrieval.

## Regression guards (must still hold after the change)

- Register-map headers still read the descending `31..0` run (rotation fix intact).
- `filters.columns == table_content.headers` for every table.
- No `null` cells (merged now filled; empty stays `""`).
- Table count unchanged (~175/178 on RM0490); notes unchanged (Table 9 = 1, Table 27 = 13).
- Full 1023-page run still completes without OOM (`page.flush_cache()` retained).

## Verification

Run the full RM0490 extraction + `--validate`; confirm the acceptance test passes,
the guards above hold, and spot-check 2–3 other merged tables (e.g. Table 10) show
filled values rather than `""`.
