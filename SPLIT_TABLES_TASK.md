# Task — emit one JSON file per table (in addition to the combined JSON)

Applies to BOTH projects:
- `stm32-table-extractor` (`rmtables`) — produce the per-table files.
- `stm32fetch` — pass the option through in its batch runner.

**The existing combined JSON output must not change in any way.** This is purely an
additional output.

## 1. What to write

For every table in the combined output, write one JSON file. Each file must be
**self-contained** — a consumer reading a single file must know which manual, revision and
URL it came from, so the document-level block is embedded in every file.

```json
{
  "document": {
    "name_datasheet": "RM0490",
    "rev": "Rev 6",
    "url_pdf": "https://www.st.com/resource/en/reference_manual/rm0490-....pdf",
    "references": "STM32C0",
    "package": "",
    "family": "C0",
    "core": "Arm 32-bit Cortex-M0+ CPU",
    "frequency": ""
  },
  "text": "<identical to the combined file>",
  "table_content": { "headers": [...], "rows": [...], "notes": [...], "legend": [...],
                     "semantic_type": "...", "semantic": {...} },
  "metadata": { "table_name": "...", "table_number": "16", "page": 62, "section": "4.3.5",
                "section_title": "...", "tags": [...], "semantic_type": "...",
                "url_to_table": "...", "columns": [...], "units": {} }
}
```

Rules:
- `text`, `table_content`, `metadata` are **byte-identical** to that table's object in the
  combined JSON (same values, same nesting). Only the `document` block is added.
- `document` is a copy of the combined file's top-level fields **excluding** `tables`.

## 2. Folder layout

```
<tables-dir>/
  RM0490/
    _index.json
    RM0490_table_001.json
    RM0490_table_016.json
    ...
  RM0008/
    _index.json
    RM0008_table_001.json
```
One subfolder per manual, named by `name_datasheet` (fallback: PDF stem,Read SPLIT_TABLES_TASK.md and implement per-table JSON output in both stm32-table-extractor and stm32fetch. The combined JSON must stay byte-identical — this is a purely additional output.

In rmtables, add a split.py module that runs after the combined document is assembled and consumes that finished in-memory object (so the two outputs can't drift). For each table write <tables-dir>/<RM>/<RM>_table_<NNN>.json containing that table's exact text/table_content/metadata plus an added document block with the manual's top-level fields, so each file is self-contained. Also write a _index.json manifest per manual. Handle filename sanitization, zero-padded natural sorting, collisions, and unnumbered tables per §3; write atomically via .tmp+rename; prune stale files unless --no-prune. Add the CLI flags in §6 (--split-tables, --tables-dir, --filename-slug, --no-prune), with split output off by default in rmtables.

In stm32fetch, forward the options through batch.py so each manual writes to <tables-dir>/<RM>/, enable --split-tables by default in pipeline, and make the split step idempotent (skip when _index.json is newer than the PDF unless --force).

Don't touch parsing, notes, legend, merged-cell fill, symbol remap, classification, or the semantic extractors. Add the §8 tests — especially the deep-equality check that every per-table file matches its object in the combined JSON, and the byte-compare proving the combined output is unchanged. Then verify §9 on RM0490. sanitized). This
prevents collisions when many manuals are processed into the same tree.

## 3. Filenames — stable, sortable, filesystem-safe

- Pattern: `{RM}_table_{NNN}.json` where `NNN` is the table number zero-padded to 3 digits
  (4 if any table number ≥ 1000, applied consistently within a manual).
- Optional readable slug via `--filename-slug`: `{RM}_table_{NNN}_{slug}.json`, where slug
  is the lowercased `table_name`, non-alphanumerics → `-`, collapsed, trimmed, truncated to
  40 chars. **Off by default** (captions can change between revisions; the number-only form
  is the stable one).
- Sanitize: allow only `[A-Za-z0-9._-]`; strip path separators and quotes; keep the total
  filename under 100 chars.
- **Collisions:** if two tables would produce the same name (missing/duplicate table
  number), append `_p{page}`, then `_2`, `_3`… Log every collision.
- Tables with no `table_number`: use `{RM}_table_unnumbered_p{page}.json`.

## 4. `_index.json` per manual

A lightweight manifest so a consumer can select tables without opening every file:

```json
{
  "document": { ...same document block... },
  "table_count": 179,
  "generated_at": "<ISO-8601>",
  "tables": [
    { "file": "RM0490_table_016.json", "table_number": "16",
      "table_name": "Mass erase overview", "page": 62, "section": "4.3.5",
      "semantic_type": "generic", "tags": ["erase", "flash", "..."],
      "n_rows": 5, "n_cols": 7 }
  ]
}
```
`n_rows` = `len(table_content.rows)`, `n_cols` = `len(table_content.headers)`.

## 5. Writing behaviour

- **Atomic:** write each file to `<name>.tmp` then rename, so an interrupted run never
  leaves a half-written JSON.
- **Deterministic:** same input → identical bytes (`json.dump(..., indent=2,
  ensure_ascii=False, sort_keys=False)`); stable key order matching §1.
- **Prune stale files:** after a successful run, delete files in that manual's folder that
  were not produced by this run (e.g. tables removed by a revision). `--no-prune` disables.
  Never delete outside `<tables-dir>/<RM>/`.
- Create directories as needed; fail loudly if `<tables-dir>` is not writable.

## 6. CLI

`rmtables`:
```
--split-tables            # enable per-table output (off by default)
--tables-dir DIR          # default: <output JSON's dir>/tables
--filename-slug           # include caption slug in filenames (default off)
--no-prune                # keep stale per-table files
```
The combined `-o output.json` is written exactly as today, whether or not `--split-tables`
is used.

`stm32fetch`:
```
--split-tables / --tables-dir DIR      # forwarded to rmtables per manual
```
In `batch.py`, each manual writes to `<tables-dir>/<RM>/`. Enable `--split-tables` by
default in `pipeline` (document this), keeping the combined JSON as well.
Idempotency: skip the split step when the manual's `_index.json` is newer than the PDF,
unless `--force`.

## 7. Constraints

- Do NOT alter parsing, notes, legend, merged-cell fill, symbol remap, classification, the
  semantic extractors, or the combined output's schema.
- Implement as a separate module (e.g. `split.py`) called after the combined document is
  assembled — it consumes the finished in-memory document, so there is no risk of drift
  between the two outputs.

## 8. Validation / tests

- Per-table `text`/`table_content`/`metadata` deep-equal the corresponding object in the
  combined JSON (assert for every table, both manuals).
- File count == table count; `_index.json` lists every file and all files exist.
- Filenames are unique, sanitized, sorted naturally by table number.
- Collision handling: two tables with the same number → distinct files, both logged.
- Prune removes only stale files inside the manual folder; `--no-prune` keeps them.
- Atomic write: no `.tmp` files remain after a successful run.
- Re-running produces byte-identical files (determinism).
- Combined JSON is unchanged vs. a run without `--split-tables` (byte-compare).

## 9. Acceptance

- `rmtables rm0490.pdf -o output1.json --split-tables` → `output1.json` unchanged plus
  `tables/RM0490/` containing 179 files + `_index.json`, and
  `tables/RM0490/RM0490_table_016.json` matches the Table 16 object from `output1.json`
  with the `document` block added.
- `stm32fetch pipeline --series STM32C0` → combined JSON in `json/` and per-table files in
  `tables/RM0490/`.
