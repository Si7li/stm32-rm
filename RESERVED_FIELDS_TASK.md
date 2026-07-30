# Task — include reserved bits (Res.) as entries in register_map `fields`

## Goal

Currently `fields` lists only named fields, so reserved bit positions are absent and the
list doesn't cover all 32 bits. Add reserved bits as explicit field entries so `fields`
is complete and self-describing. Register-level `reset_value` stays unchanged.

## What to add

For every run of reserved bit columns in a register's field row, emit a field entry:

```json
{ "bits": "<hi:lo or single>", "name": "Res.", "reset": "" }
```

- `name` is the canonical `"Res."` for every reserved run (normalize `"Reserved"`,
  `"res."`, etc. → `"Res."`).
- `reset` is `""` (empty) — reflecting the blank reset cell for reserved bits in the
  table. Do NOT put `0` here. (The register-level `reset_value` still 0-fills reserved
  positions by convention — that is intentional and unchanged; per-field reserved shows
  the literal blank.)
- `bits` is the range of the reserved run, same format as named fields (`"31:12"`,
  `"10"`).

## How to identify reserved runs

Work from the already-filled field row (post merged-cell fill) mapped to bit columns:
- A bit column is **reserved** if its field-row cell matches `^\s*res\.?\s*$` or
  `^\s*reserved\s*$` (case-insensitive), OR the cell is empty/blank (no field label).
- Group **consecutive** reserved columns into one entry spanning `hi:lo` (single bit →
  just the number). Named fields break the runs.

## Ordering & coverage

- Emit ALL fields (named + reserved) ordered by bit position, MSB→LSB, interleaved by
  position (reserved entries slot in at their bit ranges).
- After the change, the union of all `fields` bit-ranges for a register must cover
  `31..0` with **no gaps and no overlaps**. This is a strong new validation.

## Acceptance (concrete — TIM15_CR1)

Named bits are 11, 9:8, 7, 3, 2, 1, 0; the rest are reserved. `fields` must become:

```json
[
  { "bits": "31:12", "name": "Res.",     "reset": "" },
  { "bits": "11",    "name": "UIFREMA",  "reset": "0" },
  { "bits": "10",    "name": "Res.",     "reset": "" },
  { "bits": "9:8",   "name": "CKD [1:0]","reset": "00" },
  { "bits": "7",     "name": "ARPE",     "reset": "0" },
  { "bits": "6:4",   "name": "Res.",     "reset": "" },
  { "bits": "3",     "name": "OPM",      "reset": "0" },
  { "bits": "2",     "name": "URS",      "reset": "0" },
  { "bits": "1",     "name": "UDIS",     "reset": "0" },
  { "bits": "0",     "name": "CEN",      "reset": "0" }
]
```

`reset_value` stays `"0x00000000"`. Named fields' existing `bits`/`name`/`reset` are
unchanged — reserved entries are only ADDED and interleaved.

## Scope / rules

- Change ONLY the register_map semantic extractor's field-building. Keep the
  `{text, table_content, metadata}` shape, `reset_value` logic, classification, and all
  other semantic types unchanged (additive rule holds).
- Do not alter `table_content.rows`/`headers`; this only enriches
  `semantic.registers[].fields`.

## Validation

- Re-run RM0490, RM0008, RM0477.
- For every register in every register_map: `fields` bit-ranges cover `31..0` exactly —
  no gaps, no overlaps (add this as a validator check; report any register that fails,
  which would indicate a parse issue rather than a real gap).
- Every reserved entry has `name == "Res."` and `reset == ""`.
- Named fields unchanged vs previous output (value-preservation on the named subset).
- Spot-check TIM15_CR1 equals the acceptance block above.
- Add tests: reserved-run grouping (single + multi-bit), Reserved/res. normalization,
  full-coverage assertion, and that reserved `reset` is `""` while `reset_value` is
  still 0-filled.

## Note

Wider registers with a 32-bit field (e.g. `CNT[15:0]` plus a reserved top half) already
show the pattern — the reserved top half becomes `{ "bits": "31:16", "name": "Res.",
"reset": "" }` (or the actual reserved span). If a bit is a special case like
`"UIFCPY or Res."`, keep that literal name (it is partially named) rather than forcing
`"Res."`.
