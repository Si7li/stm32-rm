# Claude Code Task — Generic STM32 Reference-Manual Table Extractor → `rag_selective` JSON

## 0. What you're building

A Python CLI that parses **every table** from **any** ST STM32 **reference-manual**
PDF and writes **one document-level JSON per manual** in the internal `rag_selective`
schema. It must be **general** — no logic, paths, or metadata tied to a specific
manual. The user will run it on RM0490 today and other RMxxxx manuals later.

Reference files provided:
- `export_rag_selective.py` — a **working, general prototype**. Productionize it;
  keep its extraction logic. It already auto-derives document metadata, filters to
  captioned tables, un-reverses vertical text, merges continuations, captures
  footnotes, and manages memory.
- `stm32c011d6_rag_selective.json` — an instructor-provided example of the target
  schema (it's a *datasheet*; use it as the **shape/field-name reference** only, not
  as a fixture to reproduce).

Turn the prototype into a clean, tested package. Do not tie it to one document.

## 1. Hard rules

- **Deterministic, offline, free.** `pdfplumber` lattice (ruled-line) extraction.
  **No LLM, no API keys, no network.** ST RM tables are fully ruled grids, so
  structure is recovered exactly; a model would be slower, non-deterministic, and
  could hallucinate register bit values.
- **General, not manual-specific.** Derive everything possible from the PDF itself.
- **Match the schema exactly** (field names, types, conventions in §2).

## 2. Output schema

Top-level — one JSON object per manual:

```json
{
  "name_datasheet": "RM0490", "rev": "Rev 6",
  "url_pdf": "https://www.st.com/resource/en/reference_manual/<filename>.pdf",
  "references": "...", "package": "",
  "family": "C0", "core": "Arm 32-bit Cortex-M0+ CPU", "frequency": "up to 48 MHz",
  "tables": [ <table>, ... ]
}
```

Each table:

```json
{
  "table_name": "Flash memory organization ...",
  "table_number": "10",
  "page": 57,
  "filters": { "columns": [<headers>], "units": {} },
  "url_to_table": "<url_pdf>#page=57",
  "table_content": { "headers": [...], "rows": [[...],...], "units": {}, "notes": [...] }
}
```

**Conventions (fixed):** `table_number` is a **string**; `page` is an **int** (start
page for multi-page tables); merged/spanned cells are the **empty string `""`**, never
null; `url_to_table` = `url_pdf + "#page=" + page`; `filters.columns` mirrors
`table_content.headers`; `headers` = first row, `rows` = remaining rows; `units` =
`{}` and `notes` = list of footnote strings (see §3.6). Emit **only captioned
"Table N." tables** in this generic shape.

## 3. Pipeline — validated details (in the prototype; keep them)

**3.1 Lattice settings**
```python
{"vertical_strategy":"lines","horizontal_strategy":"lines","snap_tolerance":3,
 "join_tolerance":3,"edge_min_length":3,"intersection_tolerance":3}
```

**3.2 Cell text with rotated-text un-reversal — CRITICAL.** ST prints bit-field
names, "Res.", and bit numbers vertically; naive extraction reverses them. Collect
chars in the cell bbox: upright by `(top,x0)` grouped into lines; rotated
(`char["upright"] is False`) sorted by `-top` (bottom-to-top). Without this, every
register table is corrupt.

**3.3 Keep only captioned tables; drop figure/register noise.** For each detected
grid, keep it only if the nearest caption-like line above is a `Table N.` caption
(not `Figure N.`, not a bare section heading).

**3.4 Continuation merge.** Merge a grid into the previous logical table when its
caption number matches AND (`(continued)` OR same/next page); drop a duplicate header
row on the continuation. Use the start page for `page`.

**3.5 Memory — mandatory.** `pdfplumber` caches pages; a naive full run OOM-kills
around page 800 of a 1023-page manual. Call `page.flush_cache()` after each page;
process one page at a time.

**3.6 Footnote capture (`notes`).** Collect **all** footnote lines directly below the
table bbox (no fixed limit — some tables have 10+), each matching
`^\(?\d+\)?[.)]\s+...`, joining wrapped continuation lines into their footnote.
**Stop** on the next `Table`/`Figure` caption, a numbered section heading, a blank
gap, or an ST **page footer** (`\d+/\d+` or `(RM|DS|PM|AN|UM)\d+ Rev \d+`) — otherwise
footers leak in. Attach notes to the (merged) logical table; de-duplicate.

**3.7 Metadata auto-derivation (general).** Derive from the PDF cover page (first 1–2
pages), the PDF `Title`, and the filename — never hardcode:
- `name_datasheet`: `\bRM\d{3,4}\b` from cover (fallback: filename stem).
- `rev`: first `Rev \d+` found (in footers), scanning early pages.
- `url_pdf`: `https://www.st.com/resource/en/reference_manual/<pdf-filename>` (ST
  files are named as the resource slug); overridable.
- `family`: `STM32([A-Z]\d)` → e.g. `C0`.
- `core`: `Cortex-M<n>` → `Arm 32-bit Cortex-M<n> CPU` (best effort).
- `frequency`, `references`, `package`: best effort / often empty for an RM.
Every field is overridable via a matching CLI flag.

## 4. Project structure

```
stm32-rm-tables/
├── README.md            # what it does + why deterministic/no-LLM + usage
├── requirements.txt     # pdfplumber, pypdf
├── pyproject.toml       # console entry point `rmtables`
├── src/rmtables/
│   ├── cli.py           # argparse
│   ├── metadata.py      # §3.7 auto-derivation + overrides
│   ├── extract.py       # §3.1 page -> ruled tables
│   ├── cells.py         # §3.2 cell_text + rotated-text un-reversal
│   ├── captions.py      # §3.3 caption detection + figure filter
│   ├── notes.py         # §3.6 footnote capture
│   ├── merge.py         # §3.4 continuation merge
│   ├── exporter.py      # §2 build the rag_selective document
│   └── validate.py      # §6
└── tests/
    ├── test_cells.py    # un-reversal: "Res.", "DBG_SWEN", header 31..0
    ├── test_notes.py    # footnote join + footer/caption stop
    ├── test_merge.py    # duplicate-header drop on continuation
    └── test_schema.py   # output matches the example's key structure/types
```

## 5. CLI

```
rmtables INPUT.pdf -o out.json
         [--pages 55-95]
         [--name-datasheet ..] [--rev ..] [--url-pdf ..] [--references ..]
         [--package ..] [--family ..] [--core ..] [--frequency ..]   # all optional overrides
         [--log-level INFO]
```

Metadata flags override auto-derived values; with none given it runs fully
automatically on any RM. Progress every 100 pages to stderr; continue on per-page
errors.

## 6. Validation

- Parse the manual's **"List of tables"** front matter and assert emitted
  `table_number`s ≈ that list; report missing/extra. (Do this generically — the
  section exists in every RM.)
- `filters.columns == table_content.headers` for every table.
- No `null` cells (merged cells must be `""`).
- Register-map tables must have a header containing the descending `31..0` run
  (proves the rotation fix works).
- `test_schema.py`: output has the same key structure/types as the provided example.

## 7. Confirm with instructor (defaults match the example)

1. Generic `{headers, rows, units, notes}` acceptable for all RM tables? (The tool is
   category-aware; do **not** invent a `table_content` category it can't consume.)
2. Per-register bit-layout diagrams (not in the "List of tables") are currently
   **excluded**; include only if a representation is agreed first.

## 8. Definition of done

- `rmtables <any-RM>.pdf -o out.json` runs on the full manual without OOM, auto-derives
  metadata, and emits the captioned tables in the exact schema with footnotes populated.
- Validation passes; tests green; README explains the no-LLM rationale.
- Nothing in the code references a specific manual.
