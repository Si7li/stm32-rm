# Task — rename three record fields (project-wide sweep)

Rename these top-level per-record fields everywhere they appear:

| old key | new key |
|---|---|
| `id`   | `table_id`   |
| `text` | `text_helper`|
| `tags` | `features`   |

Values are unchanged — keys only. This is a mechanical but **complete** sweep: a missed
reference will silently break the splitter, the manifest, or the Sidekick templates.

## Where each must change (find ALL of these)

1. **Record construction** in the exporter (the flattened record built for each table).
2. **Per-table split files** — they copy the same records; they inherit the rename
   automatically ONLY if built from the shared record object. Verify.
3. **`_index.json` manifest** — if it surfaces `id`/`text`/`tags` (e.g. `table_id`,
   summary/`features`), rename those keys there too.
4. **Any internal code that READS these keys by name**, not just writes them:
   - `text`/`text_helper`: the splitter, index builder, `text`-template/punctuation
     cleanup logic, any length/`""` assertions, any place that sets or trims it.
   - `tags`/`features`: the tag-derivation code, the index summary, any dedupe/sort step.
   - `id`/`table_id`: id generation (`{document}-T{nnn}`), uniqueness checks, any
     KB-dedupe reference.
5. **Tests** — update every fixture and assertion referencing the old keys.
6. **README / docs** — the record-shape example, the field table, and the Sidekick
   operator section:
   - Note `text_helper` is the human-readable helper/embedding text (rename in any
     "which field to embed" note).
   - If the Link Label/URL templates reference `{{...}}` of a renamed field, update them
     (they currently use `document`/`table_number`/`title`/`url`/`url_pdf`/`page`, none of
     which are being renamed — but grep to be sure no template uses `{{tags}}`/`{{text}}`/
     `{{id}}`).

## Method (do it safely)

- Grep the whole codebase (both `stm32-table-extractor` and `stm32fetch`) for the
  whole-word tokens `"id"`, `"text"`, `"tags"`, and the bare identifiers used as dict keys
  / attribute names for these fields. **Beware false positives** — do NOT rename:
  - Python builtins/vars like `id(...)`, unrelated local `text=` used for other strings,
    `tags` that refer to something else (e.g. HTML/`cqTagNames` from the ST API).
  - Only the RECORD's fields change. `cqTagNames` in the catalog code, `physicalResourceType`,
    etc. are unrelated — leave them.
- Prefer renaming at the single point where the record dict is assembled, then follow
  every reader. Confirm with a final grep that no record still emits `id`/`text`/`tags`.

## Constraints

- Keys only — do not change values, record structure, ordering (keep a sensible key order:
  `table_id` first, `text_helper` near where `text` was, `features` where `tags` was),
  parsing, or any extraction logic.
- `semantic_type` duplication (top-level + inside `table_content`) is unaffected.
- Combined and per-table records must stay byte-identical to each other (deep-equality
  test updated for the new key names).

## Validation

Re-run RM0490, RM0008, RM0477, RM0522 and assert, for every record:
- `table_id`, `text_helper`, `features` are PRESENT.
- `id`, `text`, `tags` are ABSENT (assert none of the old keys exist anywhere in the
  combined file, the per-table files, or `_index.json`).
- `text_helper` is non-empty; `features` is a list; `table_id` is unique within a document
  and still matches `{document}-T{nnn}`.
- Everything else unchanged: table counts, no duplicate table numbers, `columns ==
  table_content.headers`, records flat and template-renderable, per-table == combined.
- README examples/templates use only the new names.

Add/adjust tests to cover the three renames and a grep-style assertion that the old keys
appear nowhere in emitted JSON.
