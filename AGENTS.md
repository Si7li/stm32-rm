# STM-UserManuel-Project

Three related tools that turn ST (STMicroelectronics) documentation into structured
data. Built by Khalil Sahli for an internship at ST.

Detailed background lives in [`docs/context/`](docs/context/). Read
[`docs/context/README.md`](docs/context/README.md) first — those notes record root
causes and measured evidence that are expensive to rediscover.

## The three sub-projects

| directory | package | input | output |
|---|---|---|---|
| `stm32-table-extractor/` | `rmtables` | reference manual PDFs | every table, as JSON |
| `stm32-content-extractor/` | `rmcontent` | reference manual PDFs | every section's prose, as JSON |
| `stm32-product-selector/` | `stproducts` | ST selector API + datasheet PDFs | product-selector spreadsheets |

`rmcontent` **imports** `rmtables`. It must not fork or modify it. If a shared helper
genuinely needs to change, raise it with Khalil before changing it.

## Standing constraints

These are decisions already made. Do not relitigate them without asking.

**No LLM anywhere in the pipeline.** The instructor explicitly *allowed* a vision model.
It was evaluated and rejected: ST tables are fully ruled grids that pdfplumber's lattice
mode recovers exactly, and a vision model could silently hallucinate bit values in
register maps. "We evaluated AI and chose not to use it" is itself part of what is being
graded, and the reasoning belongs in the READMEs. Everything is deterministic and
offline-capable; no API keys.

**Never lose information from tables.** Contamination is recoverable; silent row loss is
not. This is why row-level heuristics were rejected — a nameless-column heuristic
destroyed 20 real rows of RM0486 T585. Prefer positional evidence ST actually printed
over inference about row shape, and route anything removed to an audit sidecar outside
the payload.

**Never assert a value the document does not support.** Applies most sharply to
`stproducts`, where every cell carries a provenance token. A `DATASHEET` reading must
name a source row whose label supports the field being filled. Absence of evidence is
not evidence of absence: do not write a negative because a row is missing.

**The datasheet outranks the API.** Established by evidence, not preference. For
`STM32F207IE`, `Table 3. STM32F207xx features and peripheral counts` gives `I2C = 3`;
ST's own selector API and the shipped workbook both say `2`.

## Environment

- `.venv/` at the repo root, Python 3.14. Use `.venv/bin/python`, not system Python.
- **All st.com traffic must go through `curl_cffi` with `impersonate="chrome"`.** ST
  fronts everything with Akamai TLS fingerprinting; plain `requests` and `httpx` are
  refused. See `stproducts/net.py` and `stm32fetch`.
- Large inputs (`datasheets/`, `product_selector/`) and generated outputs
  (`product_selector_out/`, `cache/`, `datasheets_cache/`) are gitignored. Only code is
  tracked.

## Working method

Non-trivial changes are written as a spec — `*_TASK.md`, `*_FIX.md`, `*_DESIGN.md` in
the repo root — then implemented, then the resulting JSON or XLSX is **independently
verified against the source PDF**. Do not treat a passing test suite as verification;
the bugs that matter here are agreement-with-reality bugs, and the tests cannot see the
PDF. Those spec files are deliberately untracked (see `.gitignore`); they are working
notes, not deliverables.

## Verification habits that have paid off

- Re-derive a claim from the PDF rather than trusting a report. The report is generated
  by the same code that might be wrong.
- Check counts reconcile. If classes are meant to partition a total, assert they sum to
  it — a class that is silently not counted is how 26 fabricated cells stayed invisible.
- Cross-check the same part across workbooks. A part appearing in three selectors must
  get identical values and identical provenance in all three.
- Distinguish notation from disagreement. `LQFP64` vs `LQFP 64 10x10x1.4 mm` and
  `Arm Cortex-M33` vs `Arm Cortex-M33 with TrustZone` are the same fact.
