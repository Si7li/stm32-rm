# Fix task — register_map reset values: read bit-by-bit, per field + clean value

## Problem (evidence, RM0490)

`reset_value` is unreliable: distribution is 302×`"0"`, 60×`""`, 37×`"0x00000000"`,
21×`"X"`, plus raw bit-strings like `"111111111111000000000000"`. The reset row is
per-bit but gets collapsed to a single char, or naively concatenated, or (sometimes)
read correctly. It must be read bit-by-bit and assembled.

Example — DMAMUX_C0CR (Table 54). Headers are `[Offset, Register, 31..0]`. The reset row
is one cell per bit:
`['0x000','Reset value','','','', '0','0','0','0','0', ...per bit..., '0']`
(`''` = reserved bit; `0/1/X` = that bit's reset). The correct register reset is
`0x00000000`, and each field's reset is the slice of those bits.

## Two reset-row styles (handle both)

1. **Per-bit** (RM0490): reset cells are single `0`/`1`/`X`, `''` for reserved. Map each
   bit-number header column → its reset cell.
2. **Hex constant** (RM0008 CRC etc.): a reset cell contains `0x....` spanning the row —
   that hex IS the register reset value; slice it per field.

Detect by inspecting the reset row cells (mostly single 0/1/X → per-bit; contains a
`0x[0-9A-Fa-f ]+` token → hex).

## What to produce

Per field, add `reset` (the field's reset bits, MSB→LSB):
```json
{"bits":"28:24","name":"SYNC_ID[4:0]","reset":"00000"}
{"bits":"16","name":"SE","reset":"0"}
```
- per-bit style: concatenate the reset cells over the field's bit range (high→low).
- hex style: slice the corresponding bits out of the hex value.
- reserved/absent bits → omit `reset` or `""`.

Per register, set `reset_value` to a clean value:
- per-bit style: assemble all 32 bits (reserved `''` counts as `0`); if every defined
  bit ∈ {0,1} → `reset_value = "0x%08X"`; if any bit is `X` (undefined) → keep the
  32-char bit-string with `X` preserved (do NOT fake a hex).
- hex style: normalize the hex (strip spaces) → `"0x........"`.
- no reset row → `reset_value = ""` (not `"0"`).

## Grouped headers (RM0008)

If bit columns are grouped (e.g. `31-24`), expand each group to its bit indices when
mapping the reset row, same as the field bit-range logic already does.

## Acceptance

- DMAMUX_C0CR: each field has a `reset` substring; `reset_value = "0x00000000"` (not
  `"0"`). No lone `"0"`/`"X"` and no raw multi-char bit-strings as `reset_value`.
- A register whose reset row has 1s yields the correct NON-ZERO hex (e.g. bits 23:12 set
  → `"0x00FFF000"`), not `"111111111111000000000000"`.
- CRC (hex style) → `reset_value` normalized to `"0xFFFFFFFF"`; fields sliced from it.
- Registers with genuinely undefined bits keep `X` in a bit-string rather than a fake hex.
- RM0490 + RM0008 reset_value distribution is now dominated by proper `0x........` values
  (and legitimate `""`/X-bitstrings), NOT lone `"0"`/`"X"`/raw bitstrings.

## Scope / rules

- Touch only the `register_map` semantic extractor. Keep the `{text, table_content,
  metadata}` shape and everything else unchanged (additive rule holds).
- Add tests: per-bit assembly (all-zero → 0x00000000; mixed → correct hex), per-field
  `reset` slicing, hex-style slicing, X-preservation, grouped-header expansion.
