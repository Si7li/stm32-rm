# stproducts datasheet first

> stproducts inverted to datasheet-as-truth — the provenance contract and the extraction traps found doing it.

As of 2026-08-10, `stm32-product-selector/` treats the **datasheet** as the
source of truth; ST's selector API only supplies the row list and the
datasheet URL. `--source api` reproduces the pre-inversion output byte for
byte (hashes locked in `tests/data/api_baseline_sha256.json`).

Every cell carries one of `DATASHEET / DERIVED / AMBIGUOUS / API /
UNAVAILABLE` in a parallel `Provenance` sheet. `DATASHEET`/`DERIVED` readings
**cannot be constructed without naming their source table** — the invariant
lives in `Reading.__post_init__`, not in an after-the-fact audit.

Traps found while extracting, each of which produced hundreds of false
"ST is wrong" rows before being fixed:

- A summary column can cover several variants: a Flash cell reading
  `512 1024 2048` is alternatives, not an answer. But `64 (48+16)` and
  `3/(2)(2)` are one value plus a breakdown. Told apart by punctuation.
- Table fragments on different pages have different column *counts* but a
  shared x-grid, so continuation cells must be aligned by **x-overlap**, not
  by index. Index alignment silently reads the wrong variant.
- A footnote marker on an electrical figure (`1.8(1)`) means the document is
  qualifying it — ST often publishes the footnote's value (1.7). Footnoted
  figures are AMBIGUOUS.
- ST's set-valued vocabularies (USB Type, Additional Interfaces, Other timer
  functions) are wider than the summary table can speak to. Composing from
  only the rows that exist writes an *incomplete* value.
- Notation differences are not disagreements: `LQFP64` vs
  `LQFP 64 10x10x1.4 mm`, `WLCSP64+2` vs `WLCSP 66`, `Arm Cortex-M33` vs
  `Arm Cortex-M33 with TrustZone`.

STM8 and STM32MP datasheets mostly lack the `features and peripheral counts`
layout, so those families fall to `API` — reported per family, not hidden.

Parsing is the slow half: first build ~40 min for a few hundred PDFs, cached
under `datasheets_cache/parsed/` keyed by size+mtime.

Related: [st-selector-api](st-selector-api.md), [st-export-rendering-rules](st-export-rendering-rules.md)
