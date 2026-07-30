# Task — fix `text_helper` template selection and register-name sourcing

`text_helper` is the field Sidekick embeds and retrieves on, and it is wrong on **289 of
1678 records (17%)** across RM0490 Rev6, RM0522 Rev1 and RM0486 Rev4. Two defects, both in
`exporter.py`, both verified against the emitted JSON and the source PDFs.

## Bug 1 — the register-map template is chosen by column geometry, not by type

`_build_text` (exporter.py:201) picks its shape with `_is_register_map(headers)`
(exporter.py:136), which is pure geometry:

```python
if len(headers) > WIDE_TABLE_COLS:      # >12 columns
    return True
numeric = sum(1 for h in headers if re.fullmatch(r"\d+", (h or "").strip()))
return numeric / len(headers) > 0.5     # or majority-numeric headers
```

It never consults `semantic_type`, even though `table_to_schema` computes the authoritative
value four lines earlier (exporter.py:271). So any wide or bit-numbered table is described
as a register map. **57 tables** assert something false:

| Manual | Table | cols | type | title | emitted text claims |
|---|---|---|---|---|---|
| RM0490 | T142 | 8 (7 numeric) | generic | DLC coding in FDCAN | "is a register map: offsets, 32-bit layout (bits 31..0) and reset values" |
| RM0486 | T20 | 32 | generic | RISUP indexes | same |
| RM0486 | T83 | 16 | generic | Connectivity matrix | same |
| RM0486 | T155 | 29 | generic | SDRAM address mapping with 8-bit data bus | same |
| RM0522 | T28 | — | generic | — | same |

"DLC coding in FDCAN" is a data-length-code lookup table. Describing it as a 32-bit register
layout with reset values is a factual error in the text being embedded.

## Bug 2 — register names are read from the wrong place, producing broken English

`_register_names` (exporter.py:148) locates the register column by an exact header match:

```python
idx = next((i for i, h in enumerate(headers) if (h or "").strip().lower() == "register"), None)
if idx is None:
    return []
```

Real headers are not that literal. RM0486's register maps carry the merged header
`"Register name Reset value"`; others vary. When the lookup fails, `reg_text` falls back to
the placeholder `"the registers listed"`, which the template splices after the word
"registers":

> ...and reset values for **registers the registers listed**.

**232 tables** emit that sentence — 176 genuine `register_map` tables plus the 56 tables
already mis-templated by Bug 1. Per manual: RM0486 **99 of 99** register maps broken,
RM0522 75, RM0490 25. Only ~10 of 186 register-map tables currently produce real names.

### The names are already available

`table_content.semantic.registers[].name` is populated for **4,892 of 4,892 registers
(zero unnamed)** across the three manuals — e.g. RM0486 T16 yields `BSEC_FVRw`, and the
field is verified free of pseudo "Reset value" entries. This is the authoritative source and
requires no header scanning at all.

Note the key is `name`, **not** `register`.

## Fix

Pass `semantic_type` and `semantic` into `_build_text`, and select among three shapes.

### Shape A — `semantic_type == "register_map"`

Take names from `semantic["registers"][*]["name"]`, in first-seen order, de-duplicated,
skipping blanks. Cap the enumeration at 12 and append `and {k} more` beyond that, so a
40-register map does not produce a 400-character sentence.

```
Table {n}, "{name}", in section {section} ({section_title}) on page {page}.
Register map: offsets, 32-bit field layout and reset values for {count} registers:
{A, B, C, ...}[ and {k} more].
```

If no names survive (should not occur — currently 0 of 4,892), **omit the entire
":{names}" clause** and end at "reset values." The string `the registers listed` must never
appear in any output again.

### Shape B — wide table that is NOT a register map

Keep the existing geometry test, renamed `_is_wide_table`, for this branch only. Do not
enumerate 32 bit-number columns, and do not claim a register map:

```
Table {n}, "{name}", in section {section} ({section_title}) on page {page}.
{C} columns: {first up to 8 header names}[, +{C-8} more]. {R} data row(s).
```

### Shape C — everything else

Unchanged from today's generic template.

### Consistency changes that apply to all three shapes

- `section_title` is included in all three. Today Shape A omits it while Shape C includes
  it — an inconsistency with no rationale.
- The `notes` suffix (`Notes: ...`, truncated at `NOTES_TRUNCATE`) is appended in all three.
  Today only Shape C gets it, so 186 register-map tables silently drop their footnotes from
  the embedded text. **This is a deliberate change that will alter those records' text —
  flag it in your report.**
- `_collapse_trailing_punctuation` still runs last on every shape.

`_register_names` and its header-scanning logic are deleted. `_is_register_map` is renamed
`_is_wide_table` and used only by Shape B.

## Validation

Re-run RM0490, RM0522 and RM0486 and assert, across every emitted record:

1. The substring `the registers listed` appears **zero** times (was 232).
2. No record whose `semantic_type != "register_map"` contains the phrase `register map:`
   in `text_helper` (was 57).
3. Every one of the 186 `register_map` tables names at least one real register, and the
   names it lists are a subset of that table's own `semantic.registers[].name` values.
4. No `text_helper` enumerates more than 12 register names or more than 8 column names.
5. The `{R} data row(s)` count still equals `len(table_content.rows)` for every record
   using Shapes B/C (currently 0 mismatches — do not regress it).
6. No `text_helper` ends with `..`.
7. **Only `text_helper` changes.** Every other field on every record is byte-identical:
   `table_id, document, rev, table_number, title, page, section, section_title,
   semantic_type, features, url, url_pdf, columns`, and all of `table_content`. Run this as
   an explicit diff, not an assertion by inspection.
8. Per-table split files reflect the same text, and the combined-vs-split deep-equality
   test still passes.
9. Table counts unchanged: 178 / 598 / 902.

## Expected outcome

- 232 records lose the broken sentence.
- 57 records stop falsely claiming to be register maps.
- 186 register-map records gain real register names (e.g. RM0486 T16 →
  `BSEC_FVRw, ...`) and their footnotes.
- Net: 289 distinct records improved; no record's structured data touched.

## Tests

- Shape A with names present → enumerates them, capped at 12 with `and N more`.
- Shape A with an empty `registers` list → no `:` clause, no placeholder text.
- Shape B on a 32-column generic table (RM0486 T20) → no "register map" phrasing, at most
  8 column names listed.
- Shape B on RM0490 T142 (8 columns, 7 numeric, generic) → same.
- Shape C unchanged on a plain 3-column table — golden-string test against a current record.
- Notes appended in all three shapes; truncation at `NOTES_TRUNCATE` preserved.
- A record whose notes already end in "." does not produce "..".

## Out of scope

Do not touch parsing, merged-cell fill, symbol remap, caption detection, the figure
boundary/cut logic, classification, the semantic extractors, `features`, or the Sidekick
record shape. This task changes one field.
