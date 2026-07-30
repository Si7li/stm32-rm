# Fix task — register_map semantic extractor (two localized bugs)

The semantic typing is otherwise correct and fully additive — no regressions, and
`alternate_function` / `interrupt_vector` / `memory_map` / `feature_matrix` are good.
Only the `register_map` extractor needs fixing. Do NOT touch anything else. Keep the
change additive (existing fields and other semantic types unchanged).

## Bug 1 — reset-value rows leak as pseudo-registers (42 cases in RM0008)

A register-map row whose Register-name cell is "Reset value" is the reset row for the
register above it. Currently ~42 of these are emitted as bogus registers (e.g. Table 10
CRC: a junk register named `"Reset  value"`), and the real register's `reset_value` is
left `""`. Root cause: the name cell is often `"Reset \nvalue"` (embedded newline /
double space), so a naive equality check misses it.

Fix: when detecting a reset row, **collapse whitespace/newlines** in the name cell and
match `re.search(r'reset\s*value', name, re.I)`. If it matches:
- do NOT emit it as a register;
- set the preceding register's `reset_value` to the row's reset value (the distinct
  hex string across the data cells, e.g. `0xFFFF FFFF`).

Acceptance: 0 registers whose `name` matches `reset value`; Table 10 `CRC_DR` has
`reset_value: "0xFFFF FFFF"`.

## Bug 2 — bit ranges wrong with grouped bit-column headers

Register-map headers are often grouped, e.g. `["Offset","Register","31-24","23-16",
"15-8","7","6",...,"0"]`. The current extractor derived `CRC_DR` "Data register" as
`bits:"7:0"` when it spans the whole register (`31:0`) — it ignored the grouped columns
`31-24/23-16/15-8`.

Fix: parse each bit-column header into the bit indices it covers — a single number
`"7"` → {7}; a range `"31-24"` (also `"31:24"`) → {31,...,24}. For a field that spans a
set of columns, `bits = f"{max_bit}:{min_bit}"` (or just `str(bit)` for a single bit).
Compute the span from the columns the field's cell actually covers (post merged-cell
fill, a field label repeats across all its columns — group consecutive identical field
labels in the register's row to find its column span).

Acceptance: Table 10 `CRC_DR` "Data register" → `bits:"31:0"`; reserved/other fields get
correct ranges; a single-bit field yields a single number (e.g. `"8"`).

## Validation

- Re-run on RM0490 and RM0008.
- 0 pseudo "Reset value" registers across all register_map tables.
- Every register in a register_map has plausible `fields` bit-ranges (hi ≥ lo, within
  0..31) and a `reset_value` when the table has a reset row.
- No change to any non-`register_map` table or any pre-existing field (additive rule
  still holds).
- Add tests: reset-row folding (with embedded newline), grouped-header bit-range
  (`31-24` etc.), single-bit range.
