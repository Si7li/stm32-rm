# Task — remove the dead `units` field + three output cleanups

Four changes found in real output (RM0503 Table 206). Do NOT touch parsing, notes/legend
capture, merged-cell fill, symbol remap, classification, or the semantic extractors.
The `text` template itself stays as it is, apart from the punctuation fix in §2.

---

## 1. Remove `units` entirely

`units` is always `{}` for reference manuals — it was inherited from the datasheet schema
and has never been populated across RM0490, RM0008, RM0477 or RM0503.

- Delete `metadata.units` from every emitted table.
- Delete `table_content.units` if it is still written anywhere.
- Remove the units-derivation code and any tests that assert its presence.
- Do NOT reintroduce a global `units` key later. If a `parameter`-typed table ever needs
  units, they belong inside that type's `semantic` block as a per-parameter `unit` field.

## 2. No doubled sentence punctuation in `text`

Observed: `... depending on the M bit value)..` — the appended note already ends with `.`
and the template adds another.

- After building `text` (and after each appended segment), strip trailing whitespace and
  collapse repeated sentence-final punctuation at the end (`..` → `.`, `. .` → `.`).
- Do not otherwise change the template wording or structure.

## 3. Trim whitespace in headers, columns, and titles

Observed header `"PCE bit "` (trailing space), which propagates into `metadata.columns`
and into `text`.

- `.strip()` every header cell and every `metadata.columns` entry.
- Collapse internal runs of whitespace to a single space.
- Apply the same normalization to `table_name` and `section_title`.
- Do NOT alter `table_content.rows` cell values — row data stays verbatim.

## 4. Don't duplicate a footnote into `legend` verbatim

Observed: the identical string appears in BOTH `notes` and `legend`, because the footnote
begins with `Legends:`.

- Keep the numbered footnote in `notes` unchanged.
- In `legend`, store the legend CONTENT with the leading numbering and `Legend(s):` prefix
  stripped — e.g. `SB: start bit, STB: stop bit, PB: parity bit. ...`
- Never store the exact same string in both fields; if stripping yields the same text,
  keep the stripped form in `legend`.
- Tables with a standalone `Legend:` line (not a footnote) behave exactly as today.

---

## Constraints

- These are field-content changes only. The `{text, table_content, metadata}` structure is
  unchanged apart from the removal of `units`.
- The per-table split files copy the same objects, so they must reflect these changes and
  the combined-vs-split deep-equality test must still pass.

## Validation

Re-run RM0490, RM0008, RM0477, RM0503 and assert:
- `'units' not in metadata` and `'units' not in table_content` for every table.
- No `text` ends with `..` (or any repeated sentence-final punctuation).
- Every header and every `metadata.columns` entry equals its own `.strip()`, and contains
  no double spaces; same for `table_name` and `section_title`.
- No table has an identical string in both `notes` and `legend`.
- `metadata.columns == table_content.headers` still holds after trimming.
- Unchanged: no null cells, notes populated where footnotes exist, semantic types and
  register maps unaffected, table counts unchanged, full runs complete without OOM.

Update tests: units removal, doubled-punctuation collapse, header/column trimming,
legend-vs-note de-duplication.

## Acceptance (RM0503 Table 206)

- `metadata.units` absent; no `units` anywhere in the record.
- `text` ends with a single `.` (no `..`).
- Header reads `PCE bit` (trimmed), and `metadata.columns` matches.
- `legend` holds the stripped legend content, not the raw numbered footnote, and differs
  from the entry in `notes`.
