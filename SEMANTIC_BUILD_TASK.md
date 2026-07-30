# Task — Add semantic (context-aware) table typing WITHOUT changing existing output

Decisions are already made (below). The overriding rule: **the current output must be
preserved exactly** — this change is strictly additive.

## Non-negotiable safety rule

The new `output.json` must be a **strict superset** of the current one. For every table,
all existing fields stay byte-for-byte identical: `table_name, table_number, page,
section, section_title, tags, text, filters, url_to_table`, and inside `table_content`:
`headers, rows, units, notes, legend`. **Do not remove, rename, reorder, or alter any of
them.** Only ADD two keys inside `table_content`:
- `semantic_type`: one of `register_map | memory_map | interrupt_vector |
  alternate_function | parameter | feature_matrix | generic`.
- `semantic`: the typed object for that type (see schemas). `{}` when `generic`.

A regression test MUST assert: for every table, the pre-existing keys deep-equal the
previous run's values. If a typed extractor is unsure, emit `generic` — never drop or
mutate raw data to force a type.

## Classifier (deterministic; header signature first, caption/section as tiebreakers)

Check most specific first; fall through to `generic`. Be conservative. Log the chosen
type and which signal fired. (Merged-cell fill already runs before this — typed
extractors assume complete rows.)

- **register_map**: header row has a descending bit-number run (≥8 numeric cols incl.
  `31` and `0`) OR caption matches `register map`.
- **alternate_function**: header has `AFx` columns or contains `alternate function`;
  caption has `alternate function` / `remap` / `pin (definition|assignment)`.
- **interrupt_vector**: headers ⊇ {`position`,`priority`,(`acronym`|`description`),
  `address`} OR caption `vector table`.
- **memory_map**: headers ⊇ {(`boundary address`|`base address`),(`size`|`bus`)} or
  {`area`,`size`}; caption `boundary addresses` / `memory map` / `register boundary`.
- **parameter**: headers ⊇ {`min`|`max`|`typ`} and/or {`symbol`,`unit`}.
- **feature_matrix**: first column ∈ {`feature`,`peripheral or function`,`peripheral`}
  AND remaining columns are variant/device names (`STM32…`, instance names) with cell
  values in {`•`,`X`,`-`,`yes`,`no`,number}.
- else **generic**.

## Per-type `semantic` schemas (built from the already-filled rows)

- register_map: `{ "registers":[ {"offset":"0x00","name":"CRC_DR",
  "fields":[{"bits":"31:0","name":"..."}], "reset_value":"0x..."} ] }`
- alternate_function: `{ "pins":[ {"pin":"PA0","functions":{"AF0":"...","AF1":"..."}} ] }`
  (remap tables: `{ "functions":[ {"function":"CAN_RX","configs":{"00":"PA11",...}} ] }`)
- interrupt_vector: `{ "entries":[ {"position":0,"priority":"-3","acronym":"NMI",
  "description":"...","address":"0x..."} ] }`
- memory_map: `{ "regions":[ {"bus":"APB1","boundary":"0x4000 0000-0x4000 03FF",
  "size":"1 KB","area":"TIM2","register_desc":"..."} ] }`
- parameter: `{ "parameters":[ {"symbol":"...","parameter":"...","conditions":"...",
  "min":..,"typ":..,"max":..,"unit":".."} ] }`
- feature_matrix: `{ "variants":["STM32C011xx",...], "features":[ {"feature":"ADC",
  "values":{"STM32C011xx":"1",...}} ] }`
- generic: `semantic` = `{}`.

Map each row into the typed object by column role (resolve columns by header name).
Keep raw strings; do light typing only where safe (e.g. numeric min/typ/max may stay
strings if parsing is ambiguous — never guess).

## Integration

- New `classify.py` (returns `semantic_type` + which signal fired) and
  `semantic.py` (per-type row→object extractors). Called in `exporter.py` AFTER the
  table is fully assembled (headers/rows/notes/legend), appending the two keys inside
  `table_content`. Nothing else changes.
- Ship all types with `generic` fallback. If one extractor is risky, it may return
  `generic` for edge cases.

## Validation (must pass before shipping)

1. **Superset/no-regression:** diff against the previous `output.json` — every
   pre-existing key on every table is unchanged; the ONLY additions are
   `table_content.semantic_type` and `table_content.semantic`.
2. Every table has a `semantic_type`; report the per-type counts (expect roughly, on
   RM0490+RM0008: register_map ~58, alternate_function ~23, interrupt_vector ~8,
   memory_map ~7, feature_matrix ≥7, parameter ~5, rest generic).
3. Each typed table validates against its schema (interrupt_vector entries have an
   `address`; parameter rows have a `unit`; feature_matrix `values` keys == `variants`).
4. Run on BOTH RM0490 and RM0008; no schema drift; full runs complete without OOM.
5. Golden set: 1–2 tables of each type with expected `semantic` output, checked in.

## Notes

- Names align with the instructor's datasheet example where they overlap; the typed
  block lives inside `table_content` (where that example put typed content) while the raw
  `headers/rows` remain beside it, so both generic and typed consumers are satisfied.
- Register maps are handled as this table type (not routed elsewhere).
