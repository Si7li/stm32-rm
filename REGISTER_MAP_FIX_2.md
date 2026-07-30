# Follow-up fix — register_map reset-row detection (final cases)

The earlier register_map fix worked for the simple case and fully fixed bit-ranges
(CRC_DR → 31:0, reset_value populated). But reset rows whose name cell is
`"<REGISTER> Reset value"` still leak as pseudo-registers: 19 in RM0008, 3 in RM0490.
Examples (all contain "reset value" as a SUBSTRING, not as the whole cell):
- `"GPIOx _CRL Reset value"`, `"GPIOx _BSRR Reset value"` (Table 59)
- `"TIMx_CR1 Reset value"` (Table 91)
- `"Reset value (ports other than A)"` (RM0490 Table 40)

Root cause: reset-row detection matches the name too strictly (equals / startswith),
so combined names and parenthetical qualifiers are missed.

## Fix (register_map extractor only; keep everything else)

1. Detect a reset row with a **substring**, whitespace-collapsed, case-insensitive test:
   `re.search(r'reset\s*value', re.sub(r'\s+', ' ', name), re.I)`.
2. When it matches: do NOT emit a register. Fold it into the correct target register —
   the register whose name equals `name` with the `reset value` phrase and any trailing
   `(...)` qualifier stripped; if that doesn't resolve, fall back to the immediately
   preceding emitted register. Set that register's `reset_value`.
3. `reset_value` string: use the register's actual reset constant — the distinct
   hex/binary value from the reset row (e.g. `0x44444444`, `0`). Do NOT concatenate
   field labels (the TIMx_CR1 case currently yields garbage like
   `"TS[2:0]\n000SMS[2:0]..."`). If the reset row has no single clean value, prefer the
   most frequent hex-looking token; if none, leave `reset_value` `""` rather than
   emitting label soup.

## Acceptance

- 0 registers whose collapsed name matches `reset value` across ALL register_map tables
  in BOTH RM0008 and RM0490.
- GPIO (Table 59) registers carry their reset (e.g. `GPIOx_CRL.reset_value` =
  `0x44444444`) with no pseudo-register.
- No `reset_value` contains field-label text (`[`, newline-joined labels).
- Unchanged: bit-ranges (CRC_DR 31:0), all other semantic types, all pre-existing
  fields, no PUA/null regressions on either manual.
- Add a test for a combined-name reset row (`"FOO_CR Reset value"`) and a
  parenthetical one (`"Reset value (ports other than A)"`).
