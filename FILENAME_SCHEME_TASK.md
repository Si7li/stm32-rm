# Task — rename outputs to include the manual revision

## Target naming

| output | new name | example |
|---|---|---|
| combined JSON | `{RM}_{Rev}.json` | `RM0490_Rev6.json` |
| per-table JSON | `{RM}_{Rev}_table_{NNN}.json` | `RM0490_Rev6_table_038.json` |
| per-manual folder | `{RM}_{Rev}/` | `tables/RM0490_Rev6/` |
| manifest | `_index.json` (unchanged, inside the folder) | `tables/RM0490_Rev6/_index.json` |

## The `{RM}_{Rev}` stem — build it once, reuse everywhere

`rev` is stored with a space (verified: `"Rev 6"`, `"Rev 21"`, `"Rev 10"`). Add ONE helper
(e.g. `doc_stem(document, rev)`) used by both the exporter and the splitter so the combined
file, the folder, and every per-table file are guaranteed consistent.

Rules:
1. `rm_part` = `name_datasheet` / `document` (e.g. `RM0490`). If missing/empty, fall back to
   the sanitized PDF filename stem and log a WARNING.
2. `rev_part` = `rev` with all whitespace removed → `Rev 6` → `Rev6`, `Rev 21` → `Rev21`.
   - If `rev` does not already start with `Rev` (case-insensitive), prefix it:
     `6` → `Rev6`, `6.0` → `Rev6.0`.
   - Replace any remaining unsafe characters with `-`; a `.` inside a version is allowed
     (e.g. `Rev6.0`).
3. If `rev` is empty/unknown: **omit the revision segment entirely** (`RM0490.json`,
   `RM0490/`, `RM0490_table_038.json`) and log a WARNING. Do NOT invent `RevNA`/`Rev0`.
4. Sanitize the final stem to `[A-Za-z0-9._-]` only; no spaces, path separators or quotes.

Verified expected stems: `RM0490_Rev6`, `RM0008_Rev21`, `RM0477_Rev10`.

## Per-table filenames

- Pattern: `{stem}_table_{NNN}.json`, `NNN` = table number **zero-padded to 3 digits**
  (4 if any table number in that manual is ≥ 1000, applied consistently across the manual).
  Zero-padding is required so files sort naturally — keep it.
- Unnumbered tables: `{stem}_table_unnumbered_p{page}.json`.
- Keep the existing `_p{page}` / `_2` collision fallback and its WARNING log level.
- Keep `--filename-slug` working: `{stem}_table_{NNN}_{slug}.json`, still off by default.
- Keep total filename length under ~120 chars; truncate the slug, never the stem.

## CLI behaviour

`rmtables`:
- If `-o/--output` is given as a **file path** → use it verbatim (explicit wins; do not
  rename it). If given as an **existing directory** → write `{stem}.json` inside it.
  If `-o` is omitted → write `{stem}.json` into the current output directory.
- `--tables-dir DIR` default stays `<combined output's dir>/tables`; the per-manual
  subfolder inside it is now `{stem}/`.

`stm32fetch` (`batch.py`):
- Combined JSON per manual becomes `{stem}.json` in `--json-dir` (previously
  `<rm_number>.json`).
- Per-table output goes to `<tables-dir>/{stem}/`.
- **Idempotency must key off the new names:** skip a manual when `{stem}.json` exists and
  is newer than the PDF (and, for the split step, when `<tables-dir>/{stem}/_index.json`
  is newer), unless `--force`.

## Revision safety (important)

Because the stem now includes the revision, a NEW revision of a manual produces new
filenames rather than overwriting the old ones. Consequences to handle:
- Stale-file pruning in the splitter must stay scoped to `<tables-dir>/{stem}/` only —
  it must never delete another revision's folder.
- Note in the README that old revisions are retained side-by-side and can be deleted
  manually; optionally add `--replace-revisions` (default OFF) that removes other
  `{RM}_Rev*` folders/files for the same RM after a successful run.

## Constraints

- Naming/paths only. Do NOT change JSON content, record shape, parsing, caption detection,
  continuation merge, notes/legend, merged-cell fill, symbol remap, classification, or the
  semantic extractors.
- `_index.json` keeps its name but its `file` entries must reference the new filenames.
- Atomic writes (`.tmp` + rename) and determinism are unchanged.

## Validation

Re-run RM0490, RM0008, RM0477, RM0522 and assert:
- Combined files: `RM0490_Rev6.json`, `RM0008_Rev21.json`, `RM0477_Rev10.json`.
- Folders: `tables/RM0490_Rev6/` etc.; per-table example
  `tables/RM0490_Rev6/RM0490_Rev6_table_038.json`.
- Every filename matches `^[A-Za-z0-9._-]+$` (no spaces) and is unique within its folder.
- `_index.json` `file` values match the actual files on disk 1:1 (count and names).
- Per-table file count == table count; no `_p{page}` names on these manuals.
- Explicit `-o custom.json` is still honoured verbatim.
- Missing-`rev` case (synthetic test): stem degrades to `RM0490` with a logged WARNING.
- Re-running is idempotent under the new names; `--force` re-writes.
- JSON content byte-identical to a run before the rename (content must not change).

Add tests: stem builder (`Rev 6`→`Rev6`, `Rev 21`→`Rev21`, `6.0`→`Rev6.0`, empty→omitted),
sanitization, zero-padding incl. the ≥1000 case, `-o` file vs directory vs omitted,
`_index.json` ↔ disk consistency, prune scoped to one revision folder.
