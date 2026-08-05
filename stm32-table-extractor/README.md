# rmtables

Parses every table in an ST STM32 reference-manual PDF into structured JSON
(and, optionally, RAG-ready JSONL chunks), preserving row/column structure,
merged cells, captions, and page spans — including register-map tables
where field names are printed rotated 90°, and the per-register bit-field
diagrams that accompany each register's prose description.

## Why deterministic parsing, not an LLM

No paid or online dependency is used anywhere in this tool: no API key, no
network calls, no model. This was a deliberate choice, not a shortcut:

- ST's tables are **fully ruled** — every cell has drawn border lines.
  `pdfplumber`'s `"lines"` strategy recovers the exact row/column grid from
  those lines, deterministically. Verified against this manual: page 90's
  FLASH register map extracts as a clean 28×34 grid; page 57's memory maps
  extract with correct merged-cell handling.
- The only defect in naive extraction is that vertical (rotated) text comes
  out character-reversed (`.seR` instead of `Res.`). This is a **fixed 90°
  rotation artifact**, corrected deterministically by reordering characters
  using their `top` coordinate (see `cells.py`). No inference involved.
- A vision LLM over ~180 dense bit-field tables (plus ~300 more per-register
  bit diagrams) would be slower, non-deterministic, and prone to silently
  hallucinating bit values — the worst possible failure mode for register
  documentation, which is exactly the kind of ground-truth reference this
  tool exists to produce.
- The rare "hard page" fallback (`fallback.py`, `--text-fallback`) is also
  deterministic: it retries `pdfplumber`'s text-alignment strategy, then
  falls back to clustering word positions. No model involved at any tier.

## Install

```bash
pip install -r requirements.txt
# or: pip install -e .
```

Requires Python 3.10+. The only dependency is `pdfplumber`.

## Usage

```bash
rmtables INPUT.pdf -o tables.json
rmtables INPUT.pdf -o tables.json --pages 90-95         # subset for dev/debug
rmtables INPUT.pdf -o tables.json --validate            # reconcile vs "List of tables"
rmtables INPUT.pdf -o tables.json --include-registers   # also emit per-register bit maps
rmtables INPUT.pdf -o tables.json --flatten-merges       # forward-fill merged col-0 cells
rmtables INPUT.pdf -o tables.json --text-fallback        # enable deterministic fallback

# RAG chunk output (JSONL), one line per record:
rmtables INPUT.pdf -o chunks.jsonl --emit rag
rmtables INPUT.pdf -o tables.json --emit both --chunks-output chunks.jsonl

# also write one self-contained JSON file per table (off by default):
rmtables INPUT.pdf -o tables.json --split-tables
rmtables INPUT.pdf -o tables.json --split-tables --tables-dir out/tables --filename-slug

# -o is optional -- an existing directory (or omitting it) auto-names the
# combined file {RM}_{Rev}.json, e.g. RM0490_Rev6.json:
rmtables INPUT.pdf -o out/
rmtables INPUT.pdf
```

`-o`: an explicit *file* path is always used verbatim (never renamed); an
existing *directory*, or omitting `-o` entirely, auto-names the file
`{RM}_{Rev}.json` inside it (current directory if omitted) -- see "Output
filenames" below.

Progress is printed to stderr every 100 pages. Per-page/per-table failures
are logged as warnings and skipped rather than aborting a 1000+ page run;
only a hard failure (e.g. the PDF won't open) exits non-zero.

Per-table output (`--split-tables`) is documented under "Output schema" below,
alongside the full record shape it writes.

## Three kinds of ruled grid, classified before merging

A ruled-line grid detector catches every box with drawn borders on a page,
and an STM32 reference manual draws three very different things that way:

1. **`caption_table`** — a real `Table N. <caption>` table (memory maps,
   feature-comparison tables, register *maps* like "FLASH register map and
   reset values"). Handled by `captions.py` + `merge.py`, same as before.
2. **`register_layout`** — the per-register bit-field diagram under each
   `N.N.N <name> (REGNAME)` subsection heading: no `Table N.` caption at
   all, just a plain bit-number line ("`31 30 29 ... 16`") above each of two
   16-column ruled halves. Handled by `registers.py` (below).
3. **`figure_fragment`** — ruled boxes from schematics (clock trees, block
   diagrams, power domains). Always dropped from the output; the reason
   (narrow bbox, too few cells, nested inside another grid, sits under a
   `Figure N.` caption, ...) is logged at `DEBUG` so filtering stays
   auditable (`classify.py`).

Classification order is caption match, then register bit-header signal,
then (if neither) figure fragment. `--include-registers` controls whether
bucket 2 is included in the output; bucket 3 is never included.

## Register bit-map merging (`registers.py`)

Every register is rendered as two 16-column grids (bits 31..16, then
15..0), each preceded by a plain bit-number line and followed by an
access-type row (`rw`/`r`/`w`) — verified against 677 such header lines in
RM0490, all exactly 16 numbers long, descending. `registers.py`:

- Detects each half via the bit-number line immediately above it (not the
  heading text alone — headings like "FLASH read protection (RDP)" also
  match the `N.N.N ... (NAME)` pattern but aren't registers; only a real
  bit-header line confirms it).
- Merges hi + lo into one 32-bit map: `{"bit": n, "field": ..., "access":
  ...}` for every bit 31..0. Multi-bit fields (`LATENCY[2:0]`) repeat across
  their bits, matching the ruled grid's merged-cell spans.
- Resolves register identity from the nearest preceding
  `N.N.N <name> (REGNAME)` heading, plus its `Address offset:` / `Reset
  value:` lines. Handles both being wrapped onto their own line and,
  rarely, split across a page break (verified against `RCC_APBSMENR1` and
  `TIM3_AF1`, the only two such cases in RM0490).
- Captures a short form of each field's prose description (`Bit 18
  DBG_SWEN: Debug access software enable`) as `field_descriptions`, used
  by RAG serialization.
- `--validate` checks every `register_layout` covers all 32 bits; RM0490
  reports 305/305 complete.

## RAG output (`serialize.py`, `chunk.py`)

`--emit rag` (or `both`) renders each `caption_table`/`register_layout`
entry into embeddable text and writes one JSON object per line:

- **Narrow tables** (≤6 columns) render as a GitHub-flavored markdown table
  plus a linearized `<rowkey>: col=val, ...` line per row.
- **Wide/bit-field tables** (register-map summaries, register layouts)
  render as one linearized line per register: `offset 0x000 -- FLASH_ACR:
  bit 18 DBG_SWEN, bits 2:0 LATENCY[2:0], reset 1X010000` — a 34-column
  markdown grid embeds badly, but this retrieves well. A register map's
  field row and its following `Reset value` row are paired into one line.
- Each chunk carries a `[section_number section_title]` context header
  resolved from the nearest preceding numbered heading (`headings.py`,
  shared with register identity resolution), plus full metadata
  (`table_number`/`register`, `page_start`/`page_end`, `address_offset`,
  `chunk_index`/`n_chunks`) for filtered/selective retrieval.
- One chunk per table/register by default; only split (by row group,
  repeating the header) when serialized text exceeds `--chunk-tokens`
  (default 600, approximated as `len(text)//4`). `register_layout` entries
  are always small and never split.
- IDs are deterministic (`<source>-table<N>-chunk<i>` /
  `<source>-register-<NAME>-s<section>-<offset>-chunk<i>`) so re-runs
  upsert cleanly into a vector store instead of duplicating. Section number
  *and* address offset are both needed to disambiguate: peripheral
  instances share generic register names (`TIMx_CCMR1` at the same offset
  for TIM1/TIM3/TIM14/...), and a single heading can cover several
  same-named words of one wide register (`UID` at 0x00/0x04/0x08 under one
  "Unique device ID register (96 bits)" heading).

## Output schema

`-o`'s combined JSON (whether `--emit raw` or `both`) is a flat, ST-Sidekick-ready
document: one envelope, one array of per-table records, no nested `metadata`
object -- every field a KB template might reference is top-level on the record
itself.

```json
{
  "document": "RM0490",
  "rev": "Rev 6",
  "url_pdf": "https://www.st.com/resource/en/reference_manual/rm0490-....pdf",
  "references": "STM32C0",
  "package": "",
  "family": "C0",
  "core": "Arm 32-bit Cortex-M0+ CPU",
  "frequency": "",
  "table_count": 178,
  "tables": [
    {
      "table_id": "RM0490-T038",
      "document": "RM0490",
      "rev": "Rev 6",
      "table_number": "38",
      "title": "Port bit configuration table",
      "page": 179,
      "section": "8.3",
      "section_title": "GPIO functional description",
      "semantic_type": "generic",
      "features": ["gpio", "configuration"],
      "url": "https://www.st.com/resource/en/reference_manual/rm0490-....pdf#page=179",
      "url_pdf": "https://www.st.com/resource/en/reference_manual/rm0490-....pdf",
      "columns": ["MODE(i) [1:0]", "OTYPE(i)", "..."],
      "text_helper": "Table 38, \"Port bit configuration table\", in section 8.3 ...",
      "table_content": {
        "headers": ["MODE(i) [1:0]", "OTYPE(i)", "..."],
        "rows": [["01", "0", "..."]],
        "notes": [],
        "legend": [],
        "semantic_type": "generic",
        "semantic": {}
      }
    }
  ]
}
```

- `document`, `rev`, and `url_pdf` are repeated on every record (not just the
  envelope) -- see the Sidekick section below for why.
- `columns` mirrors `table_content.headers`; both are trimmed (leading/trailing
  whitespace stripped, internal runs collapsed to one space), same for `title`
  and `section_title`. `table_content.rows` cell values are left verbatim.
- A merged/spanned or otherwise missing cell is `""`, never `null`.
- `table_content.notes` is the de-duplicated, order-preserving footnote list;
  `table_content.legend` never repeats a footnote's exact string -- a numbered
  footnote that is itself a legend keeps its full text (with numbering) in
  `notes` and only the stripped legend content in `legend`.
- `table_id` is `{document}-T{table_number, zero-padded to 3}` (or
  `{document}-Tp{page}` when a table has no number), guaranteed unique within
  the document even in the rare case a duplicate `table_number` survives
  merging (see `merge.py` below).
- `text_helper` is the human-readable, self-contained summary of the table
  (name + section + page, plus any notes) -- the field to embed if you're
  wiring this up to your own retrieval pipeline rather than Sidekick.
  `features` is the keyword-derived tag list used for filtering/search.
- `--split-tables` additionally writes one self-contained file per table (see
  below) using this exact same record shape; `--flatten-merges` forward-fills
  `""` first-column cells for CSV-friendly consumption.
- Register layouts and figure fragments are never part of this schema (see
  above); `--emit rag`/`both` renders a separate, different JSONL chunk format
  (see "RAG output" above) from an internal representation, not from this file.

### Output filenames: `{RM}_{Rev}`

Every output name (the combined file, the per-table split folder, and every
per-table file) is built from one shared stem, `{RM}_{Rev}` -- e.g.
`RM0490_Rev6` -- so a revision bump can never leave the combined file, the
folder, and the per-table filenames out of sync with each other:

| output | name | example |
|---|---|---|
| combined JSON | `{stem}.json` | `RM0490_Rev6.json` |
| per-manual folder | `<tables-dir>/{stem}/` | `tables/RM0490_Rev6/` |
| per-table JSON | `{stem}_table_{NNN}.json` | `RM0490_Rev6_table_038.json` |
| manifest | `<tables-dir>/{stem}/_index.json` | (unchanged name) |

`Rev` comes from the manual's own `rev` field (`"Rev 6"` -> `Rev6`, internal
space stripped); if `rev` can't be derived at all, the revision segment is
simply omitted (`RM0490.json`, `RM0490/`) rather than inventing a placeholder
-- a WARNING is logged either way (missing `document`, or missing `rev`).

**Because the stem includes the revision, re-running against a NEW revision
of the same manual writes to new files/folders rather than overwriting the
old ones.** Older revisions are retained side-by-side and are not deleted
automatically -- prune them by hand once you no longer need them (there is
no `--replace-revisions` flag; deleting the old `{RM}_Rev*` combined file
and `tables/{RM}_Rev*/` folder is enough).

### Per-table split output (`--split-tables`)

Off by default; writes one JSON file per table into
`<tables-dir>/{stem}/{stem}_table_<NNN>.json` (plus a `_index.json`
manifest), using the *same* `{..., "tables": [...]}` envelope as the combined
file, with exactly one record in the array. This is deliberate: whether an
operator uploads the combined file or a single-table file to the same KB
datasource, `rootTagPath: tables` resolves identically either way. The
combined `-o` output is written exactly as it always is, whether or not
`--split-tables` is used -- this is a purely additional export, never a
replacement. Pruning stale per-table files (see `--no-prune` below) only
ever touches `<tables-dir>/{stem}/` -- a different revision's folder is
never pruned or overwritten.

```
rmtables INPUT.pdf -o tables.json --split-tables
rmtables INPUT.pdf -o tables.json --split-tables --tables-dir out/tables --filename-slug
```

Other flags: `--tables-dir DIR` (default `<output's dir>/tables`), `--filename-slug`
(adds a caption-derived slug to filenames, truncated to fit rather than ever
truncating the `{stem}_table_{NNN}` part itself; off by default -- captions
can change between manual revisions, so the number-only filename is the
stable one), `--no-prune` (keep stale per-table files from a previous run
instead of deleting them).

### Rejected figure grids (`_figure_fragments.json`)

A `Figure N.` box printed directly under a table sometimes gets misassigned
that table's caption (`assign_caption` has no way to know a figure caption
sits between them) -- `classify.py` rejects those grids before they ever
reach the merger, so they can't inflate the real table's row/column count.
Nothing is thrown away: every rejected grid (its page, bbox, rows, the
figure caption it actually sits under, and the table number it would have
wrongly joined) is written to `<tables-dir>/{stem}/_figure_fragments.json`
-- written whenever any grid is rejected, independent of `--split-tables`.
This file sits **outside** the Sidekick `{"tables": [...]}` envelope and
every per-table file -- never uploaded, zero schema risk -- purely a
recovery/audit log.

### ST Sidekick KB ingestion

This output is designed to be uploaded directly to an ST Sidekick JSON-processor
datasource. Registering one:

| UI field | value |
|---|---|
| Processor | `JSON` |
| Root Tag Path | `tables` |
| Link Label Template | `{{document}}#Table{{table_number}}: {{title}}` |
| Link URL Template | see below -- three styles all work |

Three Link URL Template styles are all supported, because every record carries
`url`, `url_pdf`, and `page`:

| style | template | datasources needed |
|---|---|---|
| A (recommended) | `{{url}}` | ONE for all manuals |
| B (recommended) | `{{url_pdf}}#page={{page}}` | ONE for all manuals |
| C | `https://www.st.com/resource/en/.../rm0490-....pdf#page={{page}}` | one PER manual |

Style A uses the ready-made deep link; style B composes the same link from
`url_pdf` + `page` (useful if you'd rather not store a precomposed URL); either
lets a single datasource serve every manual you upload, since `url`/`url_pdf`
are already correct per record. Style C hardcodes one manual's base PDF URL
into the template itself, so it only ever resolves for that one book -- you'd
need a separate datasource per manual. Prefer A or B unless you have a
specific reason to pin a datasource to one manual.

Both the combined file and every per-table split file use `rootTagPath: tables`,
so the same datasource configuration works whether you upload one big file now
or switch to one-file-per-table later.

`processorParams` (the `rootTagPath`/template values above) belongs to the
datasource *registration* call in Sidekick's API -- it is configuration about
how to read the data, and never appears inside these JSON files themselves.

## How it works

1. **`extract.py`** — `page.find_tables()` with a lines/lines ruled-table
   strategy recovers the grid for each page. `flush_page()` releases
   pdfplumber's per-page caches afterward so a 1000+ page run stays flat in
   memory (verified: naive iteration OOMs around page ~800 without this).
2. **`cells.py`** — reconstructs cell text from raw chars instead of
   trusting `cell.extract_text()`, because that reverses rotated glyphs.
   Rotated chars (`upright: False`) are sorted by descending `top` to
   restore correct reading order. Membership in a cell is decided by a
   char's *center* point, not full bbox containment -- pdfplumber's bbox
   for a rotated glyph in a narrow single-digit column can overflow the
   ruled cell width by a point or so.
3. **`captions.py`** — matches `Table N. <caption>` lines above each grid
   (tolerating a couple of rare rendering artifacts: a stray leading
   punctuation glyph, and "Tabl e" with a spurious internal space), detects
   `(continued)`, and separately parses the manual's own front-matter "List
   of tables" section as ground truth for validation.
4. **`headings.py`** — tracks the nearest preceding numbered heading
   (`N.N.N <title>`) as pages are scanned, shared by register identity
   resolution and RAG section tagging. Rejects bit-number header lines
   ("`31 30 29 ...`") that would otherwise also match the pattern by
   requiring at least one letter in the title. Also merges a heading's
   `(REGNAME)` when it wraps onto its own line, including across a page
   break.
5. **`registers.py`** — see above.
6. **`classify.py`** — routes each page's raw grids to caption/register/
   figure buckets (see above). Also rejects a grid whose assigned Table
   caption is separated from it by a `Figure N.` caption line -- a real
   ruled grid the lattice detector correctly separated, but wrongly
   adopted by `assign_caption` picking the nearest Table caption without
   noticing a Figure caption sits between them. This is the dominant
   figure-bleed mechanism; the rarer case of a figure genuinely fused into
   the SAME grid (so its caption lands inside a cell) is still handled by
   `captions.find_embedded_figure_row`'s row-level cut, applied after
   caption assignment. Neither rejects/truncates a genuine continuation:
   ST reprints `Table N. ... (continued)` below a figure, which
   `assign_caption` picks as the nearer caption, so no Figure caption ever
   sits between IT and the continuation grid.
7. **`merge.py`** — stitches `caption_table` continuation segments back
   into one logical table by identity alone: same table number, starting
   on the same page or the very next one. Matching headers, a matching
   column count, and a `(continued)` marker are NOT required (real ST
   tables often re-render the header with a different ruled-column split
   between segments) -- a mismatched width is reconciled by right-padding
   every row to the widest segment's width, never by refusing to merge or
   truncating data. A continuation's repeated header row is dropped if
   it's equal to the first segment's header (exactly, after whitespace
   normalization, or by ≥80% cell membership, position-independent, so a
   shifted grouped column is still recognized). A `table_number` that
   still ends up duplicated after merging (e.g. another table's caption
   interrupted the continuation) is logged as an error but both objects
   are kept, never silently dropped.
8. **`serialize.py` / `chunk.py`** — RAG text rendering and row-group
   chunking (see above).
9. **`validate.py`** — reconciles table numbers/captions against the
   manual's "List of tables", sanity-checks register-map headers and
   per-register 32-bit coverage, and (`validate_chunks`) checks chunk text
   is non-empty, within budget, has a resolved section, and has a unique id.
10. **`fallback.py`** (opt-in via `--text-fallback`) — only used when a
    page's caption implies a table but the ruled-line strategy found
    nothing. Retries `pdfplumber`'s text-alignment strategy, then falls
    back to clustering word positions. Still no model.

## Known limitations (explained, not bugs)

Running `--validate --include-registers` against RM0490 in full (1023
pages) reports these remaining, understood discrepancies — everything else
reconciles cleanly (178/178 caption tables, 305/305 registers with full
32-bit coverage):

- **Table numbers 92 and 94 are "extra"** (present in the extracted output,
  absent from the manual's own front-matter "List of tables"). Both
  captions ("Table 92."/"Table 94.", pages 598 and 619) genuinely exist in
  the body text — ST's own table-of-contents has a gap here, not an
  extraction error.
- **Two RAG chunks (`table180`, "Document revision history") exceed the
  token budget.** A revision-history row can legitimately contain over
  3000 characters of change-log prose in one cell; splitting *within* a
  single oversized cell wasn't worth the complexity for 2 chunks out of 569.

### TITLE_FIDELITY_FIX.md

- **RM0490 Table 139 (SPI/I2S register map), 9 registers "not covering
  31..0".** Verified directly against the PDF (page 887): the table's own
  printed bit-number header reads `15 14 13 ... 0` -- these registers are
  genuinely 16-bit-wide on this family, not a parsing gap. `validate.py`'s
  `_header_bit_span`/`_register_map_field_coverage_errors` already scope
  "expected coverage" to the table's *own* printed header span rather than
  a hardcoded 31..0, so this table correctly reports **zero** field-coverage
  errors under the authoritative check. Not a bug; no fix applicable.
- **RM0486 Table 902 (DBGMCU register map), 6 registers with
  overlapping/gapped fields (17 field-coverage errors, header values
  "2254"/"2109"/"1154" instead of "24"/"19"/"14").** Root cause, verified
  char-by-char against the PDF (pages 4657-4659): this is the one register
  map long enough to span a page break, and ST reprints the 32-bit column
  header (rotated, 2-stacked-digit glyphs per label) at the top of the
  `(continued)` page. For exactly the 3 bit-columns whose glyph pairs sit at
  that page-continuation boundary, the assembled header text comes out as a
  corrupted 4-digit run instead of the correct 2-digit label -- and because
  the same header text is also used to resolve which bits a field row spans,
  the corruption propagates into those registers' field bit-ranges (the
  reported overlaps/gaps). Verified to be the *only* register_map table in
  the 4,892-register corpus with a header value outside 0..31, i.e. a
  genuinely isolated, page-continuation-specific artifact, not a widespread
  defect. A contained fix would need the multi-page table merge (`merge.py`/
  `extract.py`) to keep each physical page's repeated header row separate
  before the rotated-glyph assembly in `cells.py` ever sees them -- exactly
  the kind of cross-cutting change `CELL_TEXT_ASSEMBLY_FIX.md` and this task
  both kept out of scope. Left as a known limitation for 6 registers out of
  4,892 (0.12%) rather than risking every other multi-page table in the
  corpus.
- **About 12 tables classify as `generic` despite a caption that says
  "memory map"/"vector table"** (`semantic_classify.classify_table`'s
  caption fallback recognizes the words, but `semantic.extract_semantic`'s
  structural extractor then requires an exact column-role match --
  `position`+`address` for `interrupt_vector`, `boundary address`/`base
  address`+`size`/`bus` or `area`+`size` for `memory_map` -- and downgrades
  to `generic` when the real headers don't literally match, e.g. "IRQ
  number"/"Address offset" instead of "position"/"address"). This is the
  conservative classifier working as designed (`extract_semantic`: "a wrong
  type is worse than generic for retrieval"), not a bug -- **no classifier
  change made.** The list:
  - RM0522: `T016` SRAM with ECC memory map, `T024`/`T025`/`T026` Memory map
    and the swapping option (per device variant), `T100` STM32C5 vector
    table.
  - RM0486: `T001` Memory map based on IDAU mapping, `T002` Memory map and
    peripheral register boundary addresses, `T003` Peripheral register
    boundary addresses, `T152` NAND access memory map, `T266` DTS register
    memory map, `T898` STM extended stimulus port memory map, `T134`
    STM32N6x5/x7xx vector table.
  - RM0490: none.

## Tests

```bash
python -m pytest tests/
```

Covers rotation un-reversal, caption parsing (including the rendering
artifacts above), continuation merging, heading tracking (same-page and
cross-page `(REGNAME)` wrapping), register bit-map merging (including the
cross-page-split case), grid classification, RAG serialization, and
chunking — plus a golden end-to-end test over pages 89–95 of `rm0490`
(the FLASH register map and the `FLASH_SECR` register,
`examples/rm0490_pages_89-95.json`).

## Command example
cd /home/khalils/Desktop/Projects/STM-UserManuel-Project/stm32-table-extractor
PYTHONPATH=src python -m rmtables.cli \
  /home/khalils/Desktop/Projects/STM-UserManuel-Project/usermanuel/rm0490-stm32c0-series-advanced-armbased-32bit-mcus-stmicroelectronics.pdf \
  -o /home/khalils/Desktop/Projects/STM-UserManuel-Project/RefMan \
  --validate --split-tables
