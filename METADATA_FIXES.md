# Fix task — document metadata (references) + ToC-leak in section detection

Three small, targeted fixes. Additive rule still holds: don't change table parsing or
the semantic layer; these touch metadata derivation and heading detection only.

## Fix 1 — `references` is truncated to the first device

Current: `references: "STM32F101xx"` (RM0008). Should be the full list:
`STM32F101xx, STM32F102xx, STM32F103xx, STM32F105xx, STM32F107xx`.

Root cause: the derivation takes the FIRST `STM32...` match instead of all of them.

Fix: collect ALL distinct device tokens `STM32[A-Z]\d[0-9A-Za-z]*` from **both** the
cover title lines (first ~3 lines of page 1) and the **filename slug** (e.g.
`...stm32f101xx-stm32f102xx-stm32f103xx-stm32f105xx-and-stm32f107xx-...`), dedupe
preserving order, join with `", "`. If the only token is a series (e.g. RM0490 →
`STM32C0`), keep the series string (optionally enrich from the first applicability table,
but the series name is acceptable). Still overridable via `--references`.

Acceptance: RM0008 `references` lists all five devices; RM0490 stays `STM32C0` (or
series). 

## Fix 2 — Table-of-Contents lines leak into section detection

Symptom: Tables 1 & 2 (front matter) got `section: "31.18"`,
`section_title: "DBG register map . . . . . . 1110"` — a List-of-tables/ToC line
(`<number> <title> ....dot-leaders.... <page>`) was matched as a heading.

Fixes in the heading detector (`headings.py`):
1. **Reject ToC/list lines** as heading candidates when the line contains a dot-leader
   run (`\.\s*\.\s*\.` — three or more spaced/again dots) OR ends in dot-leaders followed
   by a page number (`(\.\s*){2,}\d{1,4}\s*$`). These are contents/list-of-tables/
   list-of-figures entries, never real headings.
2. **Skip the front-matter** contents pages for heading tracking: do not accept headings
   until after the first real body heading (e.g. the page where numbered body sections
   begin), or explicitly skip pages whose text is dominated by dot-leader lines
   ("Contents", "List of tables", "List of figures").
3. **Sanitize any title** that still slips through: strip a trailing
   `(\s*\.){2,}\s*\d{1,4}$` (dot-leaders + page number) from `section_title`.
4. Do NOT strip legitimate titles: `"SDIO response 1..4 register (SDIO_RESPx)"` (T167)
   is valid — only dot-LEADER runs (3+ spaced dots) and trailing "....page" count as ToC,
   not "1..4".

Acceptance: 0 tables with a dot-leader run or trailing "....<page>" in `section_title`
on RM0008 and RM0490; Tables 1/2 get a sensible early section (or empty) instead of
`31.18`; T167 keeps its real title.

## Fix 3 — empty `frequency` / `package` / `units` (keep keys; populate where cheap)

These schema keys are inherited from the datasheet format and are legitimately often
empty for reference manuals. **Keep the keys** (schema compatibility). Optional low-cost
population:
- `frequency`: search the intro for `up to \d+\s*MHz` and use it if found; else `""`.
- `units` (table_content, for `parameter`-typed tables): fill from a `Unit` column or a
  unit in a header parenthesis (e.g. `Voltage (V)` → `V`); else `{}`.
- `package`: leave `""` for reference manuals (it is a datasheet-only field). Keep the key.

Acceptance: keys always present; `frequency`/`units` populated where derivable, empty
otherwise; no schema change.

## Validation

- Re-run RM0008 + RM0490. `references` complete; 0 ToC-garbage section_titles; Tables
  1/2 sane; T167 unchanged; all pre-existing fields and the semantic layer unchanged
  (additive check still passes). Add tests: multi-device references from filename;
  ToC-line rejection; title sanitize; the `1..4` non-strip case.
