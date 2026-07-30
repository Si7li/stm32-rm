# Task — Add selective-RAG enrichment fields to the rmtables table output

Extend the existing `stm32-rm-tables` extractor. For every table, add four new fields
**without changing any existing field or any parsing/notes/merge logic**. The current
per-table fields stay exactly as they are: `table_name, table_number, page, filters,
url_to_table, table_content{headers, rows, units, notes}`. Append: `section`,
`section_title`, `text`, `tags`. Everything below is computed with plain string/geometry
rules from the PDF — no external services.

## 1. section / section_title — POSITION-AWARE (critical)

Maintain a heading stack while scanning pages. A table gets the **deepest numbered
heading whose top is ABOVE the table's top** — NOT the last heading on the page.
Why it matters (verified): Table 16 sits at the top of page 62 with heading `4.3.6 ...`
starting *below* it; "last heading on page" wrongly assigns 4.3.6, but the table belongs
to `4.3.5` (carried from the previous page). Correct: Table 16 → `4.3.5`,
Table 26 → `4.7.14`.

Build a reusable `headings.py`:
- Heading regex: `^\s*(\d+(?:\.\d+){0,3})\s+([A-Z].{2,70})$`, excluding `Table`/`Figure`
  caption lines.
- Keep `cur = {depth: (number, title)}`. Entering a page, `cur` = state from prior pages.
- For a table at top `T`: `section_at(T)` = `cur` overridden by same-page headings with
  `top < T` (a deeper depth replaces and clears deeper levels); take the deepest entry.
- After a page's tables are handled, commit that page's headings into `cur` for later
  pages.
- Guard against false headings: require a real title (starts with a letter, length ≥ 3,
  not itself a caption). A lone number must not be treated as a heading (a weak regex
  mis-assigned some tables to section "2" in testing; the title requirement fixes it).

## 2. text — templated, adapts to table shape

- Normal tables (≤ ~8 columns): `Table {n}, "{name}", in section {section}
  ({section_title}) on page {page}. Columns: {comma-joined headers}. {N} data row(s).`
  + if notes: ` Notes: {joined notes, truncated ~200 chars}`.
- Wide / register-map tables (headers are mostly bit numbers, or > ~12 columns): do NOT
  list all bit columns. Use: `Table {n} "{name}" (section {section}, page {page}) is a
  register map: offsets, 32-bit layout (bits 31..0) and reset values for registers
  {register names harvested from the "Register" column}.`
- Keep `text` self-contained (name + section + page) so a retrieved record carries
  context on its own.

## 3. tags — keyword-derived (verified: 100% coverage, ~6 tags/table on RM0490)

Combine these four sources, then dedup + sort:
1. Curated keyword→tag map (keep it in an editable data file) matched against
   `table_name + section_title + headers`. Seeds: `mass erase→mass-erase`,
   `pcrop→pcrop,flash-protection`, `rdp→readout-protection,security`,
   `wrp→write-protection`, `option byte→option-byte`, `boot→boot`, `dma→dma`,
   `interrupt→interrupt`, `clock→clock`, plus peripherals
   `timer/uart/usart/i2c/spi/adc/gpio/rcc/power`.
2. Slugified REGNAME tokens (`^[A-Z][A-Z0-9_]{2,}$`) found in `table_name`/headers.
3. Register names harvested from the `Register` column rows (register-map tables),
   e.g. `flash-acr`, `flash-keyr`.
4. Fallback significant words from `table_name`: slugify words > 3 chars that aren't
   stopwords, so no table is tag-less (e.g. `organization`, `latency`, `stm32c011xx`).

Hygiene:
- Strip `continued` / `(continued)` before tagging (caption artifact, not a tag).
- Apply a stopword list to the fallback (`and, or, the, of, for, to, with, when, bit,
  bits, values, map, overview, setting, function, ...`).
- `tags` may be empty for a pure numeric table — acceptable.
Keep the keyword map and stopword list as data files so coverage is tunable.

## Integration

- Add `headings.py` (§1). Enrich in `exporter.py` after each table is assembled, appending
  `section`, `section_title`, `text`, `tags`. Additive only — do not reorder or remove
  existing fields, and do not touch parsing/notes/merge. Keep the existing `filters`
  block intact.

## Validation

- Every table has non-empty `section`, non-empty `text`, and a `tags` list (possibly
  empty for numeric tables).
- Position-aware spot-checks: Table 16 → `4.3.5`, Table 26 → `4.7.14`.
- Register-map `text` does NOT enumerate all bit columns; its `tags` include harvested
  register names.
- Sections resolve sensibly across the whole manual (no stray "section 2" artifacts).
- All pre-existing invariants unchanged: `table_number` str, `page` int, no null cells,
  `filters.columns == table_content.headers`, notes intact, ~175/178 tables, full
  1023-page run completes without OOM.
- Add tests: position-aware section assignment, shape-adaptive text, tag recipe
  (keyword hit, harvested register name, stopword/`continued` exclusion).
