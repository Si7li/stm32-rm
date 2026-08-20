# rmerrata

Parses every STM32 device-errata sheet (ST ESxxxx PDFs) into one
selective-RAG-ready JSON per document. Deterministic and offline — `pdfplumber`
word/line extraction plus positional evidence printed by ST. **No LLM anywhere
in the pipeline** (the instructor explicitly allowed a vision model; it was
evaluated and rejected — a model could silently hallucinate bit values in
register maps, and the errata status matrix is a fully ruled grid that lattice
mode recovers exactly). This was a deliberate choice, and the reasoning
belongs in the README.

A sibling of `rmtables`, `rmcontent`, and `stproducts` in the same repo.

## The pipeline

```
input/ (repo-root Erratasheet/<family>/*.pdf)
  └─ rmerrata extract           esXXXX_errata_rag.json per document
       └─ rmerrata validate     schema invariants + reproducibility (re-extract == save)
            └─ rmerrata regression  byte-identical baselines + ES0676 reference + coverage
                 └─ rmerrata report  aggregates output/** -> output/report.json
```

Each errata becomes **4 chunks** (`full_entry` parent + `description` /
`workaround` / `applicability` children), each section `2.x` with errata becomes
**1 group chunk**, and the document gets **1 `document_summary` chunk**:

```
total_chunks == 4 * total_errata + total_groups + 1
```

Chunks carry structured `filters` (family, peripheral, group, status matrix,
severity, keywords, aliases, conditions) and a `citation` with the exact PDF
page and a deep-link URL — so retrieval can filter before embedding and cite
the source instead of guessing.

## Install

```bash
pip install -r requirements.txt
# or: pip install -e .
```

Requires Python 3.10+. The only dependency is `pdfplumber`.

## Usage

`INPUT_DIR` defaults to the repo-root `Erratasheet/` and `OUTPUT_DIR` to
`output/` in this project; both are overridable with `ERRATA_INPUT_DIR` /
`ERRATA_OUTPUT_DIR` or per-command flags.

```bash
# Extract (batch over all families, validating each file as it is written)
rmerrata extract --validate

# Extract one family / one PDF
rmerrata extract --input-dir ../Erratasheet/C5 --output output/c5 --validate
rmerrata extract --pdf-path ../Erratasheet/H5/es0561-....pdf --output output/h5 --validate

# Validate an output folder (invariants + re-extract byte-compare; scans recursively)
ERRATA_OUTPUT_DIR=output rmerrata validate

# Regression + coverage per family folder (baselines + ES0676 reference +
# deterministic sampling checklist). OUTPUT_DIR is scanned at top level only,
# so run it once per family, as the original HOW_TO_USE instructed.
for f in c0 c5 g0 h5; do
  ERRATA_OUTPUT_DIR=output/$f rmerrata regression --seed 42
done

# Aggregate report (recursive; two runs on the same inputs produce byte-identical JSON)
rmerrata report

# Consumer-side demo / smoke test over every generated JSON
rmerrata smoke
```

Per-document console report (extract):

```
ES0561 Rev 6 (2026-04) family=STM32H503CB/EB/KB/RB rm=RM0492
  revisions tracked: ['A', 'Z', 'Y']
  errata: 60  groups: 15  chunks: 256  (expected 256)
  PHASE 2 gate: PASS (0 structural checks)
  VALIDATE: ok
```

- **PHASE 2 gate** is blocking: a FAIL means **no JSON is written** for that
  document (nothing silently dropped).
- **AUDIT:** lines are non-blocking observations routed to the console/exit
  code, never into the payload.

## Consuming the JSON

`rag_utils.RAGIndex` is the public consumer API — see `CHUNKING_STRUCTURE.md`
for the schema. Quickstart:

```python
from rmerrata.rag_utils import RAGIndex, search

idx = RAGIndex.load("output/h5/es0561_errata_rag.json")
fe = idx.lookup_errata("2.4.2")
print(idx.status_by_revision("2.4.2"))   # {"A": "P", "Z": "A", ...}
print(idx.is_affected("2.4.2", "A"))
print(idx.cite(fe))                      # "ES0561 Rev 6 (2026-04), p.14: 2.4.2 <title> <url#page=14>"
print(search(idx, "what is the workaround for errata 2.4.2?"))
```

## Layout

```
src/rmerrata/
  extractor.py   PDF -> JSON (process_pdf) + `extract` CLI
  rag_utils.py   consumer helpers (RAGIndex, search*) + `smoke` CLI
  validate.py    schema invariants + reproducibility + `validate` CLI
  regression.py  baselines, ES0676 reference, coverage + `regression` CLI
  report.py      aggregate report.json + `report` CLI
  cli.py         command dispatcher
references/      frozen legacy-schema reference (regression ground truth)
tests/           offline tests on the reference JSON
```

## Verification culture

Tests cannot see the PDF — the bugs that matter here are agreement-with-reality
bugs. Beyond the test suite this project ships, verification includes:

- structural gate failures block the write (no silent row loss);
- `validate` re-extracts every PDF and byte-compares with the saved JSON;
- `regression` re-checks the same 4-chunks-per-errata coverage partition and
  reconciles counts against `4 * total_errata + total_groups + 1`;
- inputs (`Erratasheet/`) and regenerable outputs are gitignored; only code is
  tracked.