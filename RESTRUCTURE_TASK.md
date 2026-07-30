# Task — Restructure each table into { text, table_content, metadata }

Reshape every table object into the target below. This MOVES fields — it is a
restructure, not additive — but **no value may be lost**: every field that exists today
must appear at its new location. Provide the exact mapping; a value-preservation test is
mandatory.

The golden example is `target_table16_shape.json` (RM0490 Table 16) — your output for
that table must deep-equal it.

## Target per-table shape

```json
{
  "text": "<unchanged embeddable description>",
  "table_content": {
    "headers": [...],
    "rows": [[...]],
    "notes": [...],
    "legend": [...],
    "semantic_type": "register_map | ... | generic",
    "semantic": { ... }
  },
  "metadata": {
    "table_name": "...",
    "table_number": "16",
    "page": 62,
    "section": "4.3.5",
    "section_title": "...",
    "tags": [...],
    "semantic_type": "register_map | ... | generic",
    "url_to_table": "...#page=62",
    "columns": [...],
    "units": {}
  }
}
```

## Exact field mapping (old → new). Nothing may be dropped.

| current location | new location |
|---|---|
| `text` (top) | `text` (top) — unchanged |
| `table_content.headers` | `table_content.headers` |
| `table_content.rows` | `table_content.rows` |
| `table_content.notes` | `table_content.notes` |
| `table_content.legend` | `table_content.legend` |
| `table_content.semantic_type` | `table_content.semantic_type` AND `metadata.semantic_type` (same value) |
| `table_content.semantic` | `table_content.semantic` |
| `table_content.units` | **`metadata.units`** (moves out of table_content) |
| `table_name` (top) | `metadata.table_name` |
| `table_number` (top) | `metadata.table_number` |
| `page` (top) | `metadata.page` |
| `section` (top) | `metadata.section` |
| `section_title` (top) | `metadata.section_title` |
| `tags` (top) | `metadata.tags` |
| `url_to_table` (top) | `metadata.url_to_table` |
| `filters.columns` | `metadata.columns` |
| `filters.units` | `metadata.units` (same as table_content.units — one value) |

- **Remove** the old top-level keys (`table_name, table_number, page, section,
  section_title, tags, url_to_table, filters`) and `table_content.units` after moving
  them. The only top-level per-table keys become: `text`, `table_content`, `metadata`.
- `metadata.columns` equals `table_content.headers` (the within-table column list;
  formerly `filters.columns`). `filters` as a key is gone.
- `semantic_type` is intentionally written in BOTH `table_content` (next to `semantic`)
  and `metadata` (as a selector). It is computed once and written to both — never derive
  them independently.
- `table_name` and `url_to_table` MUST land in `metadata` (they are essential and were
  not in the informal sketch).

## Document-level (top of file) — UNCHANGED

`name_datasheet, rev, url_pdf, references, package, family, core, frequency, tables[]`
stay exactly as they are. Only each element of `tables[]` is reshaped.

## Do NOT change

Parsing, notes, legend, merged-cell fill, symbol remap, classification, and the semantic
extractors are untouched. Only the ASSEMBLY/serialization in `exporter.py` changes shape.

## Validation (must pass)

1. **Value preservation:** for every table, assert each old field's value is present at
   its mapped new location (build the old-shape record in the test and check equality of
   every value). No value dropped or altered.
2. **Golden:** RM0490 Table 16 deep-equals `target_table16_shape.json`.
3. Per-table top-level keys are exactly `{text, table_content, metadata}`; no leftover
   `filters`, no `table_content.units`.
4. `metadata.semantic_type == table_content.semantic_type` for every table.
5. `metadata.columns == table_content.headers` for every table.
6. Run on RM0490 and RM0008; document-level block unchanged; full runs complete.

## Note

This new shape is now the canonical output (your decision). It diverges from the ST
tool's original flat schema; if that tool ever needs the old flat form, it's a trivial
inverse mapping — keep this restructure as one function in `exporter.py` so it stays
easy to adjust.
