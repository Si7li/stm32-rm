# Task — generalize the figure-boundary cut (supersedes FIGURE_BLEED_FIX.md §A/§B)

`FIGURE_BLEED_FIX.md` landed and works for the case it was written against: a figure whose
`Figure N.` caption is printed alone in the first cell of a row. It fixed RM0490 T43,
RM0522 T160 and T210. But a corpus-wide scan of all 1678 tables across RM0490 Rev6,
RM0522 Rev1 and RM0486 Rev4 shows figure bleed in **21 tables**, and the current
`find_embedded_figure_row` catches only 3 of them.

The caption row is the *rarest* form of the problem, not the typical one.

## Why the current detector misses the rest

`find_embedded_figure_row` requires the `Figure N.` caption to **start** the row's first
non-empty cell. Three things defeat that:

1. **Prose in front of the caption, same cell.** RM0486 T187 (p1227) row 16 is literally
   `"that Attribute memory space access timings are similar.\nFigure 179. NAND flash
   controller waveforms for common memory access"` — a trailing paragraph fragment from
   above the figure, a newline, then the caption. `.match()` on the cell fails.
2. **No caption in the grid at all** (the common case). The figure's caption rendered
   outside the fused region, so the table just runs on into artwork. 9 such tables carry
   an ST figure watermark ID (`MSv45382V2`, `MSc12345`) in the bled rows.
3. **No caption and no watermark.** 11 tables end in a blank row followed by sparse
   diagram labels or nothing at all.

## Fix — three ordered signals, earliest boundary wins

Replace `find_embedded_figure_row` with `find_figure_boundary(rows, width)` returning the
row index to cut at, or `None`. Compute all three candidates, take the **minimum**.

### Signal 1 — `Figure N.` caption on any LINE of the row's only populated cell
Same as today, with one change: split the first non-empty cell on `\n` and test each line
with `FIGURE_CAPTION_RE.match`, rather than testing the cell as a whole. Keep the existing
requirement that **every other cell in the row is empty** — that is what stops a prose
cross-reference inside a real data cell from cutting a good table, and it holds in all four
caption cases. Keep the tolerant `FIGURE_WORD_RE`/`NUMBER_RE` matching and the no-`$`
behaviour already documented in `captions.py`.

### Signal 2 — ST artwork identifier
ST stamps every figure's artwork with an asset ID: `MSv48187V1`, `MSv45319V2`,
`MSv45382V2`, `MSc…`. Regex: `\bMS[vc]\d{4,}[A-Za-z]?\d*\b`. It appears in figure
artwork and never in table data — 10 occurrences across 1678 tables, all verified figure
content. Cut at the first row containing one.

Guard: require that row's populated-cell count to be **strictly less than the table's
column width** (all 10 verified rows populate 1–2 cells of a 3–16 column table). A full-width
row containing such a token would be data, not artwork.

### Signal 3 — blank row followed ONLY by sparse rows
Cut at the first all-empty row at index > 0 where **every** subsequent row populates at most
one cell. This catches diagram labels and trailing grid remnants that carry neither a caption
nor a watermark (RM0490 T78/T80/T84, RM0486 T873).

The `every` is deliberate and must not be loosened to `any`. Requiring all following rows to
be sparse is what makes it impossible for this signal to strand real, dense data below the
cut. A looser variant catches ~3 more tables (RM0522 T2) at the cost of that guarantee —
not a trade this project accepts.

### Signal 4 — REJECTED (documented so it is not re-proposed)

A sustained run of rows populating a nameless ("ghost") column would roughly double coverage
(21 → 37 tables) and is the only way to catch bleed with no caption, no watermark and no
blank separator (RM0490 T24 p75). **It is deliberately not implemented.**

Signals 1–3 rest on direct evidence that a figure is present: ST printed `Figure N.`,
stamped an artwork ID, or left a blank row followed by nothing but sparse debris. Signal 4
rests on an inference — "a column with no header name is not a real column" — which holds
only when the header row itself was extracted correctly. Headers suffer the same extraction
damage as everything else, and when they do, the signal deletes real data.

RM0486 T585 p3006 ("RTC pin PC13 configuration") is the proof: stacked/rotated header cells
collapsed into each other (`'(ALAROM SoEu'` is several labels interleaved), leaving the 11th
column nameless, while rows 3–8 legitimately populate position 10 with a pull-configuration
value that is merged away in rows 0–2. Signal 4 cuts this table 23 → 3 and destroys 20 rows
of real configuration data.

The failure modes are asymmetric, which decides it. A missed bleed leaves visible junk that
can be found and fixed later. An over-cut is silent and permanent: nothing downstream can
tell rows were removed, and Sidekick would serve a table that looks clean and is two-thirds
missing. That is the same failure mode this project rejected a vision model to avoid.

If Signal 4 is ever revisited, it needs both guards (trailing-only; reset on rows populating
ALL named columns) and RM0486 T585 (must not cut) plus RM0490 T24 (must cut at row 4) as
permanent fixtures.

<details>
<summary>Original Signal 4 design, retained for reference only</summary>

### Signal 4 — sustained use of a nameless ("ghost") column  ⚠ the risky one
A column whose header is `""` is not part of the table: nothing in the caption's own grid
populates it. So a sustained run of rows populating ghost columns is bled content. This
signal roughly doubles coverage (21 → 37 tables) and is the only one that catches bleed
with no caption, no watermark and no blank separator (RM0490 T24 p75, whose rows 4–9 are a
debug/RDP state diagram).

It is also the only signal that can destroy real data, so it carries two mandatory guards:

**Guard 1 — trailing only.** The ghost-column run must continue unbroken to the last row.
Figure debris always trails; it never stops and resumes into real data. Reset the candidate
whenever a later row populates **all** named columns — that row proves the table is still
going. Do NOT reset on merely "dense" rows.

**Guard 2 — at least two ghost-using rows.** A single one is noise.

The canonical false positive both guards exist for is **RM0486 T585 p3006** ("RTC pin PC13
configuration"): a genuine 11-column table whose header text is mangled into neighbouring
cells, so its last column looks nameless while rows 3–8 populate it with real data. Rows
9–22 populate all named columns, which is what must reset the candidate. Without Guard 1
this table is cut 23 → 3 and 20 rows of real configuration data are destroyed.

The canonical true positive Guard 1 must NOT lose is **RM0490 T24 p75**: its debris run is
interrupted at row 7 by a 2-populated-cell row, which is why the reset condition is "all
named columns" and not "dense".

Both belong in the test suite as fixtures.

</details>

### Then: extend the cut backwards over sparse rows
From the chosen index, while the preceding row populates ≤ 1 cell, move the cut up one.
This is what turns RM0486 T873's cut from row 15 into row 12, dropping the three
`Offset: 0x1000` / `Top of table` diagram labels that sit above the first blank row.

### Refuse to cut at row 0
If the backward walk reaches index 0, return `None` — a table whose every row is sparse is
not a figure-bleed case, and cutting it would empty the table. This correctly skips
RM0522 T297/T472 and RM0486 T321/T493, which the naive rule would have destroyed.

## Integration — unchanged from FIGURE_BLEED_FIX.md

Same call site in `classify.py::classify_page`, inside the `if caption is not None:` branch,
before the tuple is appended to `captioned_pairs`. Still **before** `table_merger.process_page`
so a wide figure block never pads the continuation rows, and still **without** recomputing
`raw_table.bbox`, so `notes_below` and legend position-matching are untouched
(RM0522 T160's footnote is captured from below the figure and must stay).

`§C`'s trailing-empty-column trim stays scoped to tables that were actually cut. Do not
generalize it: 11 tables have trailing empty columns and are not figure-bleed cases.

`§B` (the width-based structural-break heuristic) stays **unimplemented**. Signals 2 and 3
cover the no-caption cases it was meant for, deterministically and without risking genuine
wide tables.

## Nothing is destroyed — the audit sidecar

Cutting must not mean discarding. Alongside each manual's output, write
`<tables-dir>/<RM>_<Rev>/_figure_cuts.json`:

```json
{"document": "RM0486", "rev": "Rev 4", "cuts": [
  {"table_number": "46", "page": 328, "cut_at_row": 7, "signal": "artwork_id",
   "rows_before": 31, "rows_after": 7, "cols_before": 16, "cols_after": 3,
   "dropped_rows": [[...], [...]]}
]}
```

This lives **outside** the Sidekick payload — it is not part of the `{"tables": [...]}`
envelope, not in any per-table file, and never uploaded — so it carries zero schema risk
while guaranteeing every removed row is recoverable. It also makes any wrong cut auditable
instead of silent, and it is what turns the RM0486 T436 footnote fragment from a loss into
a recoverable artifact.

## Acceptance — every affected table, hand-verified against the current output

Each of the 21 below was read row by row and the dropped content confirmed to be figure
artwork, diagram labels or blanks. Signals 1–3 produce exactly these.

| Manual | Table | Page | rows | cols | dropped cells |
|---|---|---|---|---|---|
| RM0490 | T78 | 388 | 9 → 3 | 5 → 5 | 2 |
| RM0490 | T80 | 396 | 11 → 7 | 6 → 6 | 0 |
| RM0490 | T84 | 478 | 11 → 7 | 6 → 6 | 0 |
| RM0522 | T53 | 225 | 13 → 1 | 3 → 3 | 7 |
| RM0522 | T125 | 498 | 43 → 37 | 5 → 5 | 0 |
| RM0522 | T143 | 592 | 13 → 5 | 5 → 5 | 14 |
| RM0522 | T170 | 713 | 4 → 2 | 3 → 3 | 0 |
| RM0522 | T187 | 748 | 18 → 7 | 4 → 3 | 28 |
| RM0522 | T231 | 894 | 15 → 5 | 5 → 4 | 29 |
| RM0522 | T294 | 996 | 22 → 20 | 17 → 17 | 0 |
| RM0486 | T46 | 328 | 31 → 7 | 16 → 3 | 101 |
| RM0486 | T135 | 1126 | 9 → 4 | 2 → 2 | 1 |
| RM0486 | T165 | 1194 | 19 → 8 | 8 → 3 | 3 |
| RM0486 | T174 | 1203 | 17 → 7 | 9 → 3 | 3 |
| RM0486 | T187 | 1227 | 26 → 16 | 9 → 7 | 21 |
| RM0486 | T245 | 1497 | 13 → 5 | 5 → 5 | 14 |
| RM0486 | T436 | 2435 | 16 → 7 | 8 → 6 | 1 |
| RM0486 | T724 | 3749 | 6 → 3 | 3 → 3 | 1 |
| RM0486 | T728 | 3752 | 6 → 3 | 3 → 3 | 1 |
| RM0486 | T873 | 4416 | 68 → 12 | 6 → 6 | 38 |
| RM0486 | T892 | 4560 | 25 → 6 | 4 → 3 | 30 |

Spot-verified as genuine figure content, not data: T873 rows 12+ are a ROM-map diagram's
`Offset: 0x…` labels; T165/T174 rows 7+ are an FMC `Memory transaction / A[25:0]` waveform;
T53 rows 1+ are a PWR power-domain diagram; T187 (RM0486) rows 16+ are the NAND waveform
behind its caption; T78 rows 3+ are `Deadtime` labels from a timing diagram.

## Column-only trims

Four tables have nameless trailing columns but no row bleed: RM0522 T297 (6→5) and RM0486
T321 (16→5), T493 (6→5), T888 (5→4). They are NOT cut. Whether to trim their columns is a
separate decision — leave them alone in this task, since the trim is scoped to cut tables.

RM0486 T90 is the illustrative case of why the column padding matters beyond cosmetics: its
`columns` array carries five `""` entries, and RM0486 T46's padding pushed `text_helper`
into the register-map template ("is a register map: offsets, 32-bit layout (bits 31..0)…")
on a table that is nothing of the kind. Both are repaired automatically once the cut brings
the width back down and `text_helper` is regenerated.

## What must NOT change

- Exactly the tables listed change. Every other table byte-identical across all three manuals.
- **RM0486 T585 p3006 is unchanged: 23 rows, 11 columns.** This is the single most important
  regression check in the task.
- Table counts unchanged (178 / 598 / 902). No table becomes empty.
- No register_map / alternate_function / interrupt_vector / memory_map / parameter table
  changes row or column count.
- `notes` and `legend` unchanged on every table, including the 21.
- `columns == table_content.headers` holds after the column trim.
- Parsing, merged-cell fill, symbol remap, caption detection, continuation merge,
  classification and the semantic extractors are untouched.

Note that RM0522 T187 is `semantic_type: feature_matrix` and RM0486 T187/T165 are `generic`;
because the cut runs before the merger, the semantic block is re-derived from the corrected
rows. Confirm T187's feature_matrix `variants`/`values` still validate after the cut.

## Global assertions (all manuals)

- No row's only populated cell contains a line matching a `Figure N.` caption.
- No cell matches `\bMS[vc]\d{4,}`.
- No table ends in an all-empty row.
- `text_helper` regenerated: no `Columns: ..., , .` empty-column artifacts, and the
  `N data row(s)` count matches the trimmed row count.

## Tests

Caption behind prose on a second line inside one cell (RM0486 T187 row 16); artwork-ID cut
(T165); blank-row-then-sparse cut with the backward walk (T873 → 12); refusal to cut at
row 0 (RM0522 T297); the guard that a full-width row containing an artwork-like token is NOT
cut; trailing-empty-column trim 16 → 3 (T46); and a negative case proving an uncut wide table
keeps its trailing empty columns.

## The one content-bearing row removed

RM0486 T436 row 9 holds `"(1)Block cipher encr…"` — a footnote fragment swept into the grid,
not present in that table's `notes` (which correctly hold notes 1–3 from below the table).
It is the only removed row across all 21 tables that carries real prose rather than figure
artwork or blanks. The audit sidecar preserves it, so this is a relocation, not a loss.
Re-associating it into `notes` would mean fixing stray-text capture, which is a different
task from cutting figures — leave it out of scope here.
