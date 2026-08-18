# st export rendering rules

> How ST's Export-to-Excel renders API values — the rules that must be applied before any diff is believable.

ST's "Export to Excel" does not write API values verbatim. Comparing a
selector workbook against the grid JSON without these rules produces a flood
of false positives (the STM32F2 file alone: 306 differing cells, 36 real).

Derived from the nine shipped workbooks, not assumed:

- `||` is the API's repeat separator; the export writes `, `. Compare repeated
  values as a **set** — order is a rendering choice.
- Whether a column holds a list is **learned from the data**, not its declared
  type: `Package` is typed `string` yet holds `LQFP 64 ...||WLCSP 66 ...`.
- Booleans arrive `true`/`false`, are written `Yes`/`No`.
- **An absent boolean means `No`, not blank.** 800 cells checked; a boolean
  column never renders `-`. Ignoring this reports 239 correct cells as wrong.
- A multi-valued numeric collapses to its qualifier: `105||85` on a `max`
  column renders `105` (19/19 in the F2 file).
- A multi-valued boolean collapses to one answer: `false||true` renders `No`
  (84/84). The data cannot separate "logical AND" from "first token wins" —
  every mixed value present is `false||true`. AND is implemented.
- Absent values render `-`; numbers compare numerically (`120` == `120.0`).

Header text is composed from column metadata, not returned:
`name [" (symbol)"] [" (conditional)"] [" qualifier"]`, with an `aggregation`
key becoming a merged group header. This reproduces every shipped header
exactly across all three workbook shapes.

Related: [st-selector-api](st-selector-api.md)
