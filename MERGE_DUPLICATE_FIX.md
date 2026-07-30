# Task — fix multi-page tables being emitted as separate objects (duplicate table_number)

## Symptom

The splitter writes two files for one table, e.g. `RM0490_table_038.json` and
`RM0490_table_038_p180.json`. The splitter is behaving correctly — its `_p{page}`
collision suffix is a safety net. The real defect is upstream: the combined JSON contains
**two table objects with the same `table_number`**.

## Verified evidence (all three manuals — same pattern)

| manual | table | segments | pages | column counts |
|---|---|---|---|---|
| RM0490 | 38 `Port bit configuration table` | 2 | 179, 180 | 7, 8 |
| RM0008 | 8 `XL-density Flash module organization` | 2 | 57, 58 | 5, 4 |
| RM0477 | 85 `Port bit configuration` | 2 | 574, 575 | 7, 8 |
| RM0477 | 507 `RTC pin PC13 configuration` | **3** | 2140, 2141, 2142 | 10, 11, 10 |

In every case: same caption, same section, **consecutive pages** — i.e. one logical table
continued across a page break. The distinguishing feature is that the segments have
**different detected column counts**, so the continuation merge refuses to combine them.

This is also a regression: the earlier RM0490 output had 178 tables and zero duplicate
numbers. The tolerant caption matcher now detects the continuation captions on later
pages, and the merge step is not absorbing them.

## Root cause

The continuation merge requires the segments' header rows / column counts to match (and/or
relies on an explicit `(continued)` marker). Real ST tables often re-render the header on
the continuation page with a different number of ruled columns (a spanned header cell
splitting differently), so the equality check fails and a second logical table is created.

## Required fix — merge by identity, not by header equality

In the continuation-merge step:

1. **Merge condition** (any segment after the first):
   merge into the previous logical table when the caption `table_number` is the same AND
   the segment starts on the same page or the next page after the previous segment's last
   page. Do NOT require: identical headers, identical column counts, or the presence of
   `(continued)`. The `(continued)` marker, when present, is confirming evidence only.
2. **Chained continuations:** apply iteratively so 3+ segments merge into one (RM0477
   Table 507 spans three pages).
3. **Header handling on merge:**
   - Keep the FIRST segment's header row as `table_content.headers`.
   - If a continuation's first row is a repeated header (equal to the logical header, or
     equal after whitespace normalization, or ≥80% of its non-empty cells match the
     header), drop that row before appending.
   - **Column-count reconciliation:** let `W = max(len(header), max(len(row)) )` across all
     merged segments. Right-pad every row (and the header) with `""` to width `W`. Never
     truncate — no cell data may be lost.
   - Record `page_start` (first segment) and `page_end` (last segment); `metadata.page`
     stays the START page, as today.
4. **Merge the auxiliary fields:** concatenate `notes` and `legend` from all segments,
   de-duplicated, order preserved.

## Guarantee — table_number must be unique per document

After merging, assert that `table_number` is unique across the document. If any duplicate
remains:
- log an ERROR naming the table number and the pages involved (this indicates a genuine
  parse problem, not a normal continuation);
- keep both objects (never drop data) so the splitter's `_p{page}` fallback still
  disambiguates the filenames.

## Splitter

- Keep the `_p{page}` collision suffix as a last-resort safety net, but raise its log level
  to WARNING/ERROR, since after this fix it should never trigger on a normal manual.
- No other splitter changes.

## Constraints

- Do NOT change caption detection/matching, parsing, merged-cell fill, symbol remap,
  classification, or the semantic extractors.
- The `{text, table_content, metadata}` structure is unchanged.
- `text` and `tags` are regenerated from the MERGED table (so row counts and column lists
  in `text` describe the whole table, not just the first segment).

## Validation

Re-run RM0490, RM0008, RM0477, RM0503 and assert:
- **Zero duplicate `table_number` values** in every combined JSON.
- RM0490 Table 38 is ONE object: 7→W columns, rows = 16 + 8 (minus any repeated header
  row), page 179.
- RM0008 Table 8 is one object (pages 57–58); RM0477 Table 85 one object (574–575);
  RM0477 Table 507 ONE object merged from three segments (2140–2142).
- Every row in a merged table has the same length as `table_content.headers`
  (padding applied, nothing truncated).
- `metadata.columns == table_content.headers` still holds.
- Split output: exactly one file per table; **no `_p{page}` filenames** on any of the four
  manuals; file count == table count; `_index.json` consistent.
- Unchanged: no null cells, notes/legend populated, semantic types and register maps
  unaffected, no OOM on the full runs.
- Table counts should DROP by the number of merged duplicates (e.g. RM0490 179 → 178).

Add tests: two-segment merge with differing column counts; three-segment chained merge;
repeated-header-row drop (exact and whitespace-normalized); row padding to max width with
no truncation; notes/legend concatenation and de-duplication; duplicate-number assertion.
