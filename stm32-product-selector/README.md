# stproducts

Rebuilds the nine product-selector spreadsheets in `product_selector/` from
**the datasheets**, writes corrected copies **alongside** the originals, and
emits a report saying which values were wrong and which source was wrong.

Deterministic. No LLM anywhere in the pipeline.

## The source of truth is the datasheet

The datasheet supplies every value it can. ST's selector API keeps only the
two jobs where trust does not apply:

- the **row list** — which parts belong in which selector. An index, not a
  claim about a part.
- the **datasheet URL** per part, via `cxst-rpn-info`, which is also what
  closes the gap for families with no local PDF.

Where the datasheet cannot supply a value, the API fills in **and the cell is
marked**. Every cell carries exactly one provenance token, in a parallel
`Provenance` sheet.

This inversion is justified by evidence, not preference. For **STM32F207IE**,
`Table 3. STM32F207xx features and peripheral counts` gives **I2C = 3**; ST's
API and the hand-edited workbook both say **2**. Under the old
API-first architecture that cell was reported "unchanged" and shipped wrong.

## Result

Across the nine workbooks, 48 386 cells compared:

| | cells |
|---|---|
| `DATASHEET` — read from a PDF | 3197 |
| `DERIVED` — computed from datasheet values | 394 |
| `AMBIGUOUS` — datasheet has evidence but does not settle it | 2400 |
| `API` — the datasheet makes no such assertion | 24 538 |
| `UNAVAILABLE` — neither source has a value | 17 857 |

**743 cells in the shipped workbooks were wrong.** Of those, **124** are cases
where ST's own API agreed with the workbook and the datasheet disagreed with
both — errors propagated out of ST's database rather than introduced by hand.
**82** of them are the stricter `ORIGINAL_MATCHED_API_NOT_DATASHEET` case.

The six override groups, all confirmed by hand against the PDFs:

| cells | column | ST | datasheet |
|---|---|---|---|
| 50 | Additional Interfaces | `Ethernet, SD/MMC` | `…, Parallel camera interface, …` |
| 24 | I/Os (High Current) | `106` | `131` |
| 24 | I2C | `2` | `3` |
| 14 | Package | `LQFP 144 20x20x1.4 mm` | `LQPF144` |
| 7 | USART | `3` | `4` |
| 5 | UART | `4` | `3` |

Two are worth singling out. The **Additional Interfaces** group is ST omitting
`Parallel camera interface` for parts whose datasheet says the camera
interface is present — and ST uses that exact token for other parts, so it is
a gap in their data, not a vocabulary difference. The **Package** group is the
opposite: `LQPF144` is a transposition typo in ST's own datasheet (page 13 of
`stm32f469ae.pdf` reads `LQFP100 LQPF144 LQFP208 TFBGA216`). The datasheet is
the declared source of truth, so the typo is written **and flagged** rather
than silently corrected — the report surfaces errors on both sides, which is
the point.

## Usage

```
stproducts build                     # datasheet-first (the default)
stproducts build --source api        # the pre-inversion behaviour
stproducts resolve                   # discovery only; writes series_map.json
stproducts diff                      # re-diff from cache, no network
```

Useful flags:

| flag | effect |
|---|---|
| `--source datasheet\|api` | where values come from; `datasheet` is the default |
| `--only STEM` | restrict to workbooks matching `STEM` |
| `--out DIR` | output directory (default `product_selector_out/`) |
| `--datasheets DIR` | local PDFs, searched first (default `datasheets/`) |
| `--datasheet-cache DIR` | where PDFs fetched from ST are stored |
| `--refresh` | rebuild `series_map.json` from scratch |
| `--no-cache` / `--offline` | ignore the cache / refuse to touch the network |
| `--download-originals` | for discovered selectors, fetch ST's own Excel export and diff against it (see below) |

`--source api` reproduces the pre-inversion workbooks byte for byte; a hash
manifest of them is committed under `tests/data/` and checked by the suite,
so the old path cannot rot silently.

Output lands in `product_selector_out/`:

- `<stem>.xlsx` — the corrected spreadsheet
- `<stem> - diff.xlsx` — the change report
- `series_map.json`, `run_report.json`

## Your originals are safe

`product_selector/` is a read-only input. Every run hashes all nine files
before doing anything and again at the end, and aborts with exit code 3 if a
single byte moved. That makes "never overwriting" a property the tool checks,
not a promise in a README — if a "fix in place" path is ever added by
accident, this is what catches it.

## Where the data comes from

```
/bin/st/selectors/cxst/en.cxst-ps-grid.html/{levelId}.json    the grid
/bin/st/selectors/cxst/en.cxst-rpn-info.html/{productId}.json per-part detail
/bin/st/selectors/cxst/products-excel-download                the Export-to-Excel POST
```

This is ST's public product selector — the same data the site's own **Export
to Excel** button produces. No authentication is involved; every endpoint is
fetched anonymously, exactly as a browser loading the page would. Requests are
rate-limited to about one per second and every response is cached under
`cache/`, so a second run is fully offline and makes zero network calls.
`robots.txt` is honoured: none of the paths used here is disallowed.

The Export-to-Excel endpoint is what `--download-originals` uses. ST's site
POSTs a base64 `downloadInfo` payload to it and gets back the `ProductsList.xlsx`
its button downloads — a workbook in exactly the shape this tool rebuilds.
A discovered selector without a shipped workbook therefore need not be built
against a synthesised schema: it can be diffed against **ST's own original**.
The workbook is stored under `cache/originals/`, so the diff survives offline
and later `stproducts diff` runs.

Transport is `curl_cffi` with `impersonate="chrome"`. ST sits behind Akamai
TLS-fingerprint checks, and plain `requests`, system `curl` and Playwright all
fail against it; this is the same transport `stm32fetch` proved out.

A full cold run of the API side is 18 requests — nine product pages and nine
grids. Datasheet-first adds one `rpn-info` call per part whose PDF is not
already local or cached.

Parsed datasheets are cached under `datasheets_cache/parsed/`, keyed by file
size and mtime. The first datasheet-first build parses a few hundred PDFs and
takes about 40 minutes; later runs reuse the cache and take seconds.

### Resolving the level ids

Three id families are live against the grid endpoint:

- `SS####` — series (`SS1575` = STM32F2 series)
- `SC####` — catalogue/class (`SC1244` = STM8 8-bit MCUs)
- `LN####` — product line, which is what the sub-family workbooks need

The third one matters. A sub-family page like `stm32f2x5.html` embeds its
*parent's* series id all over the markup, so "grab the SS id off the page"
resolves STM32F2x5 to the 38-row STM32F2 series grid — wrong, and wrong
silently. What the page does carry unambiguously is its hierarchy:

```
stm32f2x5.html      window.productHierarchy = "LN1433-SS1575-SC2154-CL1734-FM141"
stm32f2-series.html window.productHierarchy = "SS1575-SC2154-CL1734-FM141"
```

Most-specific first, so the leading component is the page's own id. (`CL` and
`FM` are hierarchy bookkeeping; the grid answers HTTP 400 for them.)

Candidates are gathered from that hierarchy plus a plain id scrape, and each
is tried against the grid. A candidate is accepted only if it agrees with the
workbook on **both** the level title and the part count. Nothing is guessed:
a file that matches nothing is reported unresolved, with every candidate tried
and what it returned. All nine currently resolve on the first candidate:

| workbook | parts | id | levelTitle | rows |
|---|---|---|---|---|
| STM32F2 series | 38 | `SS1575` | STM32F2 series | 38 |
| STM32F2x5 | 20 | `LN1433` | STM32F2x5 | 20 |
| STM8AF series | 30 | `SS1583` | `STM8AF series ` | 30 |
| STM8AF52 | 10 | `LN1543` | STM8AF52 | 10 |
| STM32MP1 series | 24 | `SS2003` | STM32MP1 series | 24 |
| STM32MP131 | 4 | `LN2413` | STM32MP131 | 4 |
| STM32 high performance MCUs | 515 | `SC2154` | STM32 high performance MCUs | 515 |
| STM8 8-bit MCUs | 135 | `SC1244` | STM8 8-bit MCUs | 135 |
| STM32 Arm Cortex MPUs | 64 | `SC2230` | STM32 Arm Cortex MPUs | 64 |

The map is cached in `series_map.json`; `--refresh` rebuilds it. A cached
entry is re-checked against the workbook's part count before being trusted.

## The corrected spreadsheet

Reproduces the original's skeleton — ST banner and logo, breadcrumb, header
row, and the sub-header row that grouped files use (`Number of Channels typ`
under `A/D Converters 12-bit`) — and keeps every original column in its
original position, so consumers reading by position still work.

Then it appends the columns the API carries that the original lacked, in the
API's own `order`: `FPU`, `Co-Processor type`, `L1 Cache`, `Data E2PROM`,
`CCM RAM (I/D)`, `ITCM/DTCM RAM`, `I3C`, `Display controller`,
`Graphic accelerator`, `Integrated op-amps` and the rest. A `Datasheet URL`
column goes last.

Rows are matched on Part Number. A part ST lists that the original lacks is
added and marked `NEW_PART`. A part the original lists that ST does not is
**kept**, highlighted, and flagged `NOT_IN_ST_DATA` — never silently dropped.

### How the header text is derived

The grid returns column metadata, not rendered headers. ST composes them as:

```
name [ " (<symbol>)" ] [ " (<conditional>)" ] [ " <qualifier>" ]
```

and a column with an `aggregation` key becomes a merged group header with the
composed label beneath it. Checked against all three distinct workbook shapes,
this reproduces every shipped header exactly, with no misses in either
direction.

## The diff report

One row per `(part, column)` worth reporting, classed as `CHANGED`,
`BLANK_FILLED`, `MISSING_FROM_ST`, `ADDED_COLUMN`, `NEW_PART` or
`NOT_IN_ST_DATA`. `UNCHANGED` is counted in the summary, not written out.
Each file's report opens with its own totals; `run_report.json` carries the
per-file and aggregate figures.

### Why the number is trustworthy

The report is only meaningful if formatting noise is excluded, and there is a
lot of it: rendering alone changes the text of 4864 cells across the nine
workbooks — `||` becomes `, `, `true`/`false` become `Yes`/`No`, `-`, `—`
and `–` are all blankness. Every one of those would be a false `CHANGED` if
text were compared literally. Values are normalised before comparison:

- whitespace trimmed and internal runs collapsed; HTML entities unescaped
- `-`, `—`, `–` and empty all treated as the same thing
- numbers compared numerically, so `120` == `120.0`
- `||` is the API's repeat separator and the export writes `, ` — repeated
  values are compared as a **set**, since their order is a rendering choice
- booleans arrive as `true`/`false` and are written `Yes`/`No`

Three further conventions were derived from the shipped files rather than
assumed, because each would otherwise have produced a large block of false
positives:

- **An absent boolean means No, not unknown.** Across every boolean column in
  the nine files, all 955 cells read `Yes` for `true` and `No` for both
  `false` and absent; no boolean column ever shows `-`. Without this, 249
  correct cells get reported as wrong.
- **A multi-valued numeric collapses to its qualifier.**
  `Operating Temperature (°C) max` of `105||85` renders `105`. Verified on all
  19 occurrences in the F2 file.
- **A multi-valued boolean collapses to a single answer.** `Buy On Line`
  arrives as `false||true` for a part sold in some packages but not others,
  and all three STM8 workbooks render that `No` — 82 occurrences, no
  counterexample. Worth flagging that the data cannot distinguish "logical
  AND" from "first token wins": every mixed value present is `false||true`,
  and both readings give `No`. The conservative AND is implemented.

Whether a column holds a list is learned from the data, not from its declared
type — `Package` is typed `string` yet holds `LQFP 64 ...||WLCSP 66 ...`.
Prose columns are never tokenised on commas, so a reordered
`General Description` would still be caught.

## Provenance

Every cell carries exactly one token, in a `Provenance` sheet with the same
dimensions as the data sheet, so the two line up cell for cell:

| token | meaning |
|---|---|
| `DATASHEET` | read from the device-summary table or the cover page |
| `DERIVED` | computed from datasheet values by a rule stated below |
| `AMBIGUOUS` | the datasheet has related information but does not say which value ST publishes; the API value is written and the evidence goes in a `Conditions` sheet |
| `API` | the datasheet makes no such assertion |
| `UNAVAILABLE` | neither source has a value |

`DATASHEET` and `DERIVED` readings are **required to name the table they came
from** — the `Reading` type refuses to be constructed otherwise. That is the
mechanism behind "no cell is marked `DATASHEET` unless it was actually read
from a PDF"; it is enforced at construction, not audited afterwards.

### Field tiering

Configuration, not code — `fieldmap.py`, keyed by the API column key, which
is the same string across all nine sheets. The reference is the 36-column
STM32F2 series file.

- **`DATASHEET`** — Flash Size, RAM Size, I2C, SPI, CAN, I/Os, USART, UART,
  Package, I2S (from `SPI/(I2S)`: `3/(2)` → 2), Core and Operating Frequency
  (cover page), Supply Voltage min/max (`Table N. General operating
  conditions`, `V DD` row), Operating Temperature min.
- **`DERIVED`** — `Timers (16-bit)` / `Timers (32-bit)`. The summary table
  gives general-purpose / advanced-control / basic counts; `Table N. Timer
  feature comparison` gives each named timer's width. The two are joined, and
  **only** when the per-class counts agree — a variant with fewer timers than
  the family does not say *which* ones it drops, so a mismatch falls back to
  `AMBIGUOUS` rather than guessing. For the F2 family the join gives 12
  16-bit and 2 32-bit, matching ST.
- **`AMBIGUOUS`** — both Supply Current columns, the A/D and D/A converter
  columns, and Operating Temperature max (see below).
- **`API`** — Part Number, General Description, Marketing Status.
- **`API` via absence** — Dual-bank Flash, Comparator, Cryptography, Security
  Functions. A datasheet not mentioning a feature is not evidence the part
  lacks it.
- Any column with no rule defined defaults to `API`, with the reason recorded.

### Three judgement calls worth knowing about

**Operating Temperature max is `AMBIGUOUS`, not `DATASHEET`.** The row reads
`Ambient temperatures: –40 to +85 °C / –40 to +105 °C` — two grades, selected
by an ordering-code suffix that is not part of the base part number. The
minimum is the same in both and is settled; the maximum is not, so asserting
one would be a guess.

**Three set-valued columns fall short of `DATASHEET` on most families.** ST's
vocabulary for `USB Type`, `Additional Interfaces` and `Other timer
functions` is wider than the summary table can speak to: the table has
`SDIO` / `Camera interface` / `Ethernet` / `FSMC` rows, while ST also writes
`SAI`, `DFSDM`, `S/PDIF`, `HDMI CEC`, `MIPI CSI-2`; it has `IWDG` / `WWDG` /
`RTC` rows, while ST also writes `SysTick`, `AWU`, `Beeper`, `LP timer`.
Composing a value from only the rows that exist would write something
*incomplete* and then report it as an override against ST on nearly every
part. So one stated rule applies: the datasheet supplies the value when every
token in play is one the table can decide, and otherwise steps back to
`AMBIGUOUS` with the rows it did read recorded as evidence. On the F2 sheet
that gives `DATASHEET` for `Additional Interfaces` and `AMBIGUOUS` for the
other two.

**Package notation is not a disagreement.** A datasheet writes `LQFP64`
where ST writes `LQFP 64 10x10x1.4 mm`, and `WLCSP64+2` where ST writes
`WLCSP 66` (64 balls plus 2). The two are not treated as disagreeing, and the
workbook is not reported as having been wrong. When the equivalence test says
they name the same package, the **fuller** form is kept — usually ST's, which
carries the dimensions the workbook already shipped — because writing the
short form would make the replacement file poorer than the one it replaces
for no gain in accuracy. Provenance stays `DATASHEET` either way: the
datasheet is still what established the fact, and both renderings are
recorded in the reading. A package the datasheet names *differently* (the
`LQPF144` transposition) is a genuine disagreement and is written and
flagged as such. Same principle as the rest of the normalisation: report a
genuine value change, not a rendering difference.

### Reading the right column, twice over

The summary table has one column per variant, headed with a wildcard family
(`STM32F205Rx`) and told apart by its Flash-size row. Two things make reading
it correct rather than lucky:

1. **The variant column is chosen from the part number, never from the API.**
   The flash size is decoded with the map the datasheet itself prints
   (`B = 128 Kbytes of Flash memory`), so `STM32F205RB` → 128 kB → the column
   whose Flash row reads 128. Selecting it with the API's Flash Size would
   make every reading an echo of the API instead of evidence.

2. **Continuation fragments are aligned by geometry.** The table runs over
   several pages and each piece re-heads itself with a different number of
   columns — page 14 of the F2 datasheet has five `STM32F205Rx` columns, page
   15 has four, because a merged cell spans two variants. Fragments of one
   table share an x-grid, so a continuation cell is matched to the variant
   whose x-span it covers. `STM32F205RB` occupies x=303–338 on page 14; the
   Package cell spanning 303–374 covers it, giving `LQFP64`, while the 512 kB
   variant at 374–418 correctly gets `LQFP64/WLCSP64+2`. Aligning by column
   *index* instead would map the 512 kB part to the wrong cell.

## Datasheet acquisition

Local first, ST second:

1. a local file named after the part (`datasheets/F2/stm32f205rb.pdf`);
2. a local file that *claims* the part. One ST datasheet serves a whole
   family, and the cover names families with a wildcard (`STM32F205xx`), so
   the concrete list is read from `Table 1. Device summary` on page 2 —
   `stm32f205rb.pdf` names 26 F205xx/F207xx parts there. That index is built
   once and cached;
3. otherwise `downloadURL` from `cxst-rpn-info`, cached under
   `datasheets_cache/`.

Each PDF is parsed once and every part of its family takes a cheap view over
the result; parsing per part instead made a single file take minutes.

The run reports, per file: parts resolved, parts unresolved, local hits,
cache hits, downloads, and how many datasheets yielded a usable summary table.

**Known limitation, reported rather than hidden:** STM8 and STM32MP
datasheets do not use the `Table N. <family> features and peripheral counts`
layout the extractor expects. Those families yield no datasheet-sourced
values at all — every cell falls to `API`, marked as such, with the reason
recorded in `extraction_notes`. That is a visible, correct outcome, not a
silent failure.

## The disagreement report

Two families of class, answering different questions about the same cell.

*Did this cell change?* — `CHANGED`, `BLANK_FILLED`, `MISSING_FROM_ST`,
`ADDED_COLUMN`, `NEW_PART`, `NOT_IN_ST_DATA`.

*Which source was wrong?* —

- `DATASHEET_OVERRIDES_API` — both sources have a value and they differ; the
  datasheet's is written.
- `ORIGINAL_MATCHED_API_NOT_DATASHEET` — the workbook agreed with the API and
  both disagree with the datasheet: an error propagated out of ST's own
  database rather than introduced by hand.

The second is by construction a subset of the first, and a cell can appear in
both plus `CHANGED`. STM32F207IE's I2C is exactly that: `CHANGED` (2 → 3),
`DATASHEET_OVERRIDES_API` (the API said 2) and
`ORIGINAL_MATCHED_API_NOT_DATASHEET` (the workbook had copied ST's 2). Counts
are reported separately per file and in aggregate.

## What is deliberately not read from the datasheet

`Supply Current (@ Lowest Power)` and `(Run Mode (per MHz))` exist in the
datasheet only as condition-laden tables across temperature, voltage and
mode, with no rule in the document identifying which row ST publishes. They
stay `AMBIGUOUS`: the API's value is written and the candidate table captions
are recorded in the `Conditions` sheet. Picking a row would mean inventing a
convention and calling the result evidence.

The same restraint applies wherever a cell states alternatives rather than an
answer — a Flash row reading `512 1024 2048` for a column covering three
variants, a `V DD` figure carrying a footnote, a cover naming two cores. In
each case the field steps back to `AMBIGUOUS` or `API` rather than guess, and
the evidence is recorded. Datasheet table extraction reuses `rmtables`, which
is hardened for this shape.

## Tests

```
python -m pytest stm32-product-selector/tests
```

109 tests. `tests/test_validation.py` runs the acceptance checks from both
build specs against the real workbooks — including that `--source api` still
reproduces the pre-inversion output byte for byte, and that every cell in
every sheet carries exactly one provenance token. It uses the warm caches
offline and skips cleanly if they are absent.

## Out of scope

Rebuilding values from datasheets (the API is authoritative), reference
manuals (no per-part parametric data), and any LLM. The
`products-excel-download` endpoint was previously out of scope on the grounds
that the workbook is generated here anyway; since the format is reproduced
under test, it is now optional scope — `--download-originals` — because it
gives a discovered selector a real original to diff against instead of a
synthesised stand-in.
