# Task — stop figure content bleeding into a captioned table

## Symptom (verified: RM0522 Table 210 "AES data swapping example", p818)

A captioned table absorbs the FIGURE printed directly beneath it. In the emitted record:
- real table = rows 0–9 (3 columns: `DATATYPE[1:0]`, `Swapping performed`, data);
- row 10 onward = **Figure 192** — row 10 literally contains
  `"Figure 192. 128-bit block construction..."`, and rows 11–23 are the figure's wide
  bit-cell grid (`Word 3 D127D96`, `D95`, ...);
- headers padded to **29 columns** solely because the figure grid below is that wide.

Root cause: the figure's ruled cells sit directly below the table with little/no gap, so
the lattice detector fuses the two into one table region (and/or the continuation merge
appends the figure block). The `Figure N.` caption ends up as a data row.

## Fix — cut a detected table at a figure boundary, keep only the real table

Apply these in the table-assembly path (after grid build + merged-cell fill, before the
record is finalized). All are deterministic.

### A. Split on an in-table `Figure` caption (primary, catches this case)
- Scan the assembled rows for any row whose concatenated text matches
  `^\s*Figure\s+\d+\s*[.\u2024]` (reuse the caption regexes; tolerate split "Figure",
  leading fragments, spaced dots — same tolerance as table captions).
- **Truncate the table at the first such row**: keep rows above it, DROP that row and
  everything below. Log `INFO: cut table {n} at embedded 'Figure {m}' (page {p})`.
- The dropped block is figure content — it is NOT a table and must not be emitted as one.

### B. Structural-break heuristic (secondary, for figures with no visible caption row)
After the figure caption is gone, also cut when the row structure clearly changes from the
table's body to a figure grid. Detect the table's "real width" = the number of columns
populated in the header + first few data rows (here 3). Then cut at the first row index `k`
(beyond a small header zone) where a run of rows abruptly widens — e.g. rows that populate
many columns to the right of the real width while the left/key columns go empty, sustained
for ≥2 rows. Be conservative: require a clear, sustained change so genuine wide tables
(register maps, parameter tables) are NOT truncated.
- Guard: NEVER apply B to `semantic_type in {register_map, ...}` or to tables whose header
  legitimately has many columns (bit-numbered headers). B is only for a narrow table that
  suddenly sprouts a wide block.

### C. Trim trailing all-empty / figure-padding columns
After truncation, recompute width from the SURVIVING rows and drop trailing columns that
are empty across all of them (the 29→3 padding disappears). Keep `columns ==
table_content.headers` consistent after trimming.

### D. Prevent the fusion earlier (preferred if feasible)
In table detection, treat a `Figure N.` caption line between two ruled regions as a hard
boundary so the figure's grid is never merged into the table in the first place, and so
figure grids are classified as `figure_fragment` (already dropped) rather than joined to
the table. If this is done well, A/B become safety nets.

## What must NOT change
- Genuine multi-page table continuation (the earlier merge fix) must still work — a
  `(continued)` table on the next page is NOT a figure boundary.
- Wide legitimate tables (register maps, alternate-function, parameter) must be untouched:
  verify their column counts and row counts are identical before/after.
- Parsing, notes/legend, symbol remap, classification, semantic extractors: unchanged.

## Validation

- RM0522 Table 210 → exactly the real rows (DATATYPE 0x0..0x3 with their swapping
  descriptions), headers = 3 columns, NO `Figure 192` text anywhere in the record, no
  29-wide padding. (If RM0522 isn't in the test corpus, add a fixture built from this
  known case.)
- Add a global assertion across RM0490, RM0008, RM0477, RM0522: **no `table_content.rows`
  cell matches `^\s*Figure\s+\d+\s*\.`** — i.e. no figure caption survives inside any table.
- No register_map / parameter / alternate_function table changes row or column count
  (before/after diff on those types = empty).
- Table counts unchanged except where a figure was correctly cut off (which removes rows,
  not tables). No new empty tables.
- `columns == headers` holds; no all-empty trailing columns remain on any table.

Add tests: embedded-Figure-caption truncation; trailing-empty-column trim (29→3);
structural-break cut with a register-map negative case (must NOT cut); tolerant Figure
caption match (split "Figure", spaced dots).

## Note
This is the figure-side twin of the earlier caption/merge work: tables and figures both
render as ruled grids in ST PDFs, and when a figure abuts a table the lattice detector
can't tell them apart geometrically — the `Figure N.` caption is the reliable separator.