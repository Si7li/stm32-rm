# Task — RESTORE the rag_selective output (it regressed to an old schema)

The current `output.json` reverted to an early raw schema and dropped work that was
already correct. The extraction core is fine (179 tables, `missing: []`), but the
**output assembly is wrong**. Fix the output to the correct target below. Do NOT
re-architect extraction; only fix how records are built and enriched.

## Required per-table schema (this is the target — match it exactly)

Each table object must be:

```json
{
  "table_name": "...",              // NOT "caption"
  "table_number": "16",             // STRING
  "page": 62,                       // INT = start page
  "section": "4.3.5",
  "section_title": "FLASH main memory erase sequences",
  "tags": ["..."],
  "text": "Table 16, \"...\", in section 4.3.5 (...) on page 62. Columns: ... N data row(s).",
  "filters": { "columns": [<headers>], "units": {} },
  "url_to_table": "<url_pdf>#page=62",
  "table_content": { "headers": [...], "rows": [[...]], "units": {}, "notes": [...] }
}
```

Top-level document keeps the `rag_selective` shape (`name_datasheet, rev, url_pdf,
references, package, family, core, frequency, tables[]`). The reference is the golden
sample `rm0490_rag_selective_sample.json` plus `expected_table16.json` (attached).

## Regressions to fix (all verified in the current output.json)

1. **Wrong schema.** Emit the `rag_selective` wrapper above. Restore `table_name`
   (currently `caption`), `filters`, `url_to_table`, and the `table_content` wrapper.
   Drop the raw-only fields (`n_rows`, `n_cols`, `spans_pages`, top-level `header`/`rows`)
   from the emitted records — that info lives inside `table_content` now.
2. **Notes gone (0/179).** Restore footnote capture into `table_content.notes` (the
   working footnote logic: collect `^\(?\d+\)?[.)]\s+...` lines below the table, join
   wraps, stop at next caption/heading/blank/ST page footer). Notes is a required field.
3. **Merged cells reverted to `null` (93/179).** Re-apply merged-cell fill so every
   spanned position carries the value that visually covers it, and genuinely empty cells
   are `""` — NEVER `null`. (Explode each drawn cell's text across every grid row/col its
   rectangle covers.) Acceptance: Table 16 rows have no nulls.
4. **Header duplicated (179/179).** `table_content.rows` must be DATA ONLY;
   `table_content.headers` is row 0. Currently `rows[0] == headers`. Remove the duplicate.
5. **Sections broken (21/179).** `section`/`section_title` must come from real document
   headings, resolved **position-aware** (nearest numbered heading whose top is ABOVE the
   table). Currently table CELL text is being read as a heading (Table 16 →
   `section_number:"1", section_title:"x Protected pages only No Yes"`). Fixes required:
   - Only consider heading candidates OUTSIDE any detected table's bounding box.
   - Require a valid section number `^\d+(?:\.\d+)+$` and a real title (starts with a
     letter, len ≥ 3, not a caption/table line).
   - Acceptance: Table 16 → `4.3.5` ("FLASH main memory erase sequences"),
     Table 26 → `4.7.14`, Tables 11/12/13 get proper `4.x.y`, not `"1"`/`"2"`.
6. **Enrichment missing.** Add `text` (templated; shape-adaptive for wide register-map
   tables) and `tags` (keyword map + slugified REGNAME tokens + register names harvested
   from the `Register` column + significant-word fallback; strip `continued`; apply
   stopwords). Coverage target ~100% like before.

## Acceptance test

`output.json` for Table 16 must deep-equal `expected_table16.json` (attached): correct
schema, filled merged cells (no nulls), no duplicated header row, `section` 4.3.5,
populated `text` and `tags`, `notes` present as `[]` here (Table 16 has no footnotes),
and `filters.columns == table_content.headers`.

Also re-confirm the whole run:
- 179 tables, `missing: []`, `extra: [92, 94]` (both real tables — OK).
- 0 tables with `null` cells; 0 tables where `rows[0] == headers`.
- Every table has `table_name, filters, url_to_table, table_content{headers,rows,units,notes}, section, section_title, text, tags`.
- Notes populated where footnotes exist (e.g. Table 21, Table 27).
- Full 1023-page run completes without OOM.

## Note on the register pipeline

The `rmtables.registers` "orphan lo-half" warnings are from the separate register-field
work and `register_layout_count` is 0 — that track is incomplete and is NOT part of this
table output. **Get the table output correct first;** handle the register pipeline
separately afterward. Do not let it alter the `rag_selective` table schema.
