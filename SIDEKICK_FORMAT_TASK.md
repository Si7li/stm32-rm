# Task — restructure output for ST Sidekick KB ingestion (JSON processor)

Target consumer: ST Sidekick, `processor: "JSON"`. Its datasource is registered with
`processorParams` containing `rootTagPath` (where the record array lives) plus a
**Link URL Template** and **Link Label Template** that are evaluated **per record** with
`{{field}}` placeholders (e.g. `{{document}}#Table{{table_number}}: {{title}}`).

Two consequences drive this restructure:
1. Template fields must be **top-level on each record** — do not require nested paths like
   `{{metadata.table_number}}`; the ST operator filling the UI should use plain names.
2. With `rootTagPath` pointing at the array, the processor sees **only the array
   elements**. Any parent/document block outside the array is invisible, so manual
   identity must be repeated on every record.

`processorParams` is API-registration config — it must NEVER appear inside the JSON data
files.

---

## 1. Envelope — identical for BOTH output modes

Use one envelope so a single `rootTagPath` value (`tables`) works whether the operator
uploads the combined file or a single-table file.

**Combined** (`RM0490.json`):
```json
{
  "document": "RM0490",
  "rev": "Rev 6",
  "url_pdf": "https://www.st.com/resource/en/reference_manual/rm0490-....pdf",
  "family": "C0",
  "core": "Arm 32-bit Cortex-M0+ CPU",
  "references": "STM32C0",
  "table_count": 178,
  "tables": [ <record>, ... ]
}
```

**Per-table** (`tables/RM0490/RM0490_table_038.json`) — same key, array of one:
```json
{ "tables": [ <the identical record> ] }
```

Document-level keys may also be repeated at the top of the per-table file for human
readability, but the record itself must be self-sufficient (§2).

## 2. Record shape (flattened — this replaces `metadata`)

```json
{
  "id": "RM0490-T038",
  "document": "RM0490",
  "rev": "Rev 6",
  "table_number": "38",
  "title": "Port bit configuration table",
  "page": 179,
  "section": "8.3",
  "section_title": "GPIO functional description",
  "semantic_type": "generic",
  "tags": ["gpio", "configuration"],
  "url": "https://www.st.com/resource/en/reference_manual/rm0490-....pdf#page=179",
  "url_pdf": "https://www.st.com/resource/en/reference_manual/rm0490-....pdf",
  "columns": ["MODE(i) [1:0]", "OTYPE(i)", "..."],
  "text": "Table 38, Port bit configuration table, in section 8.3 ...",
  "table_content": {
    "headers": [...],
    "rows": [[...]],
    "notes": [...],
    "legend": [...],
    "semantic_type": "generic",
    "semantic": {}
  }
}
```

Mapping from the current shape — **no values lost**:

| current | new |
|---|---|
| `metadata.table_name` | `title` (renamed — matches the operator's `{{title}}` template) |
| `metadata.table_number` | `table_number` |
| `metadata.page` | `page` |
| `metadata.section` | `section` |
| `metadata.section_title` | `section_title` |
| `metadata.tags` | `tags` |
| `metadata.semantic_type` | `semantic_type` |
| `metadata.url_to_table` | `url` (renamed — complete deep link, ready to use) |
| doc `url_pdf` | `url_pdf` (on every record — base PDF URL, no `#fragment`) |
| `metadata.columns` | `columns` |
| `text` | `text` (unchanged) |
| `table_content.*` | `table_content.*` (unchanged) |
| doc `name_datasheet` | `document` (on every record) |
| doc `rev` | `rev` (on every record) |
| (new) | `id` = `{document}-T{table_number zero-padded 3}`; unnumbered → `-Tp{page}` |

- **Delete the `metadata` object entirely.** Every field moves up; nothing is dropped.
- `table_content.semantic_type` stays (next to `semantic`); the top-level copy is the
  template/selector field. Both are written from one computed value — never derived twice.
- `id` must be stable and unique per document+table (used for KB dedupe/update).
- Keep `units` removed, per the previous cleanup task.

## 3. Field-naming rules (operator-facing)

Top-level record keys must be lowercase `snake_case`, contain no spaces or dots, and be
stable across releases — the templates in ST's UI reference them literally. Do not
introduce nested structures for anything a template might need.

Expected operator templates (document them in the README so ST can paste them in):
- Root Tag Path: `tables`
- Link Label Template: `{{document}}#Table{{table_number}}: {{title}}`
- Link URL Template — **all three styles must work**, because the operator may build the
  URL rather than use the ready-made one:

  | style | template | datasources needed |
  |---|---|---|
  | A (recommended) | `{{url}}` | ONE for all manuals |
  | B | `{{url_pdf}}#page={{page}}` | ONE for all manuals |
  | C | `https://www.st.com/resource/en/reference_manual/rm0490-...pdf#page={{page}}` | one PER manual (base URL hardcoded) |

  This is why `url`, `url_pdf` and `page` are ALL top-level per-record fields: style A uses
  `url`, style B composes from `url_pdf` + `page`, style C needs only `page`. Every record
  must carry all three so any style renders correctly.

  Recommend style A or B in the README: with `url`/`url_pdf` on every record, a single
  datasource can serve every manual. Style C hardcodes one manual's base URL and therefore
  requires a separate datasource per book — note this trade-off explicitly.

## 4. README additions

- The four UI fields (Processor = JSON, Root Tag Path, Link URL Template, Link Label
  Template) and the exact values above.
- A worked example record.
- A statement that both the combined and per-table files use `rootTagPath: tables`, so the
  same datasource config works for single-table uploads now and one-file-for-all-tables
  later.
- Note that `processorParams` belongs to the datasource registration call, not the data.

## 5. Constraints

- Do NOT change parsing, notes/legend capture, merged-cell fill, symbol remap, caption
  detection, continuation merge, classification, or the semantic extractors.
- This is a serialization/shape change only, in the exporter + splitter.
- The per-table files must contain records byte-identical to the corresponding entries in
  the combined file's `tables` array (the existing deep-equality test must still pass,
  updated for the new shape).

## 6. Validation

Re-run RM0490, RM0008, RM0477, RM0503 and assert:
- Top-level `tables` array present in BOTH the combined and per-table files; per-table
  files contain exactly one record.
- No record contains a `metadata` key; no record contains `processorParams`.
- Every record has non-empty `id`, `document`, `table_number`, `title`, `page`, `url`,
  `text`, `table_content`; `id` is unique within a document.
- `columns == table_content.headers` still holds.
- Every record is self-sufficient: `document` and `rev` present on each one (assert no
  record relies on the parent block).
- Rendering `{{document}}#Table{{table_number}}: {{title}}` against every record produces a
  non-empty string with no unresolved `{{` placeholders (add this as a test).
- URL styles all render: for every record assert `url`, `url_pdf` and `page` are present
  and non-empty; that `url_pdf` contains no `#`; and that `url == f"{url_pdf}#page={page}"`
  (so style A and style B produce the identical link).
- Unchanged: table counts, zero duplicate `table_number`s, no null cells, notes/legend
  populated, semantic types and register maps unaffected, no `_p{page}` filenames.

Update tests: flattened-record shape, `metadata` absence, `id` generation and uniqueness,
template rendering, combined-vs-split record equality, envelope in both modes.
