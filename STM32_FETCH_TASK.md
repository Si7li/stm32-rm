# Task — STM32 reference-manual fetcher + batch rmtables runner

Build a NEW orchestrator tool (separate package, e.g. `stm32fetch`) that:
(A) scrapes ST's documentation page for all reference manuals, (B) downloads selected
manual PDFs to a folder, and (C) runs the existing `rmtables` on them, writing JSON to
another folder — either for ALL manuals or for a chosen STM32 series. Do NOT modify
`rmtables`; call it as a library or subprocess.

## Confirmed facts (verified — build to these)

- Catalog page: `https://www.st.com/en/microcontrollers-microprocessors/stm32-32-bit-arm-cortex-mcus/documentation.html`
  is **JavaScript-rendered**. A plain HTTP GET returns only ~5 of 39 reference manuals;
  the full list requires a headless browser. → use **Playwright (chromium)**.
- Reference-manual PDF URL pattern:
  `https://www.st.com/resource/en/reference_manual/<slug>.pdf`
  e.g. `.../rm0008-stm32f101xx-stm32f102xx-stm32f103xx-stm32f105xx-and-stm32f107xx-advanced-armbased-32bit-mcus-stmicroelectronics.pdf`
- Each RM link text = `RM<NNNN> <device list> advanced Arm-based 32-bit MCUs`, so the
  device/series list is scrapable per manual.

## Components

### 1. `catalog.py` — build the manual catalog (Playwright)
- Launch headless chromium, load the documentation page, wait for the resource list to
  render, and if needed expand the "Reference Manual (N)" section.
- Extract every reference-manual entry: `rm_number` (RM\d+), `title`, `devices`
  (parsed from the title text), `pdf_url`, `slug`, `filename`.
- Write `catalog.json` (cached). Provide `--refresh` to re-scrape; otherwise reuse the
  cache so normal runs don't hit the site.
- **Isolate the page selectors in one place** (the site markup changes) and make the
  scraper resilient (retry, explicit waits).
- Fallback if rendering fails: (a) also parse any RM links present in the static HTML;
  (b) allow a user-supplied URL/RM list file. Never hard-crash the whole tool because
  the page changed — log and use what you have.

`catalog.json` schema:
```json
{ "scraped_at": "ISO8601",
  "manuals": [
    { "rm_number":"RM0008", "title":"STM32F101xx, ... MCUs",
      "devices":["STM32F101xx","STM32F102xx","STM32F103xx","STM32F105xx","STM32F107xx"],
      "series":["STM32F1"], "slug":"rm0008-...-stmicroelectronics",
      "filename":"rm0008-...-stmicroelectronics.pdf",
      "pdf_url":"https://www.st.com/resource/en/reference_manual/rm0008-...pdf" } ] }
```
Derive `series` from devices (e.g. `STM32F103xx` → `STM32F1`; `STM32H7Rxx` → `STM32H7`).

### 2. `download.py` — polite downloader
- Input: a list of catalog entries (all, or filtered). Download each `pdf_url` to
  `--manuals-dir` (default `manuals/`) as its `filename`.
- **Politeness / robustness (required):** realistic User-Agent; honor `robots.txt`;
  rate-limit (e.g. ≥1 s between requests, configurable); stream to disk; retries with
  exponential backoff on 429/5xx/timeouts; per-file timeout. RM PDFs are LARGE (some
  50 MB+, and there are ~39) — expect >1 GB total.
- **Idempotent:** skip a file that already exists with a plausible size (HEAD
  Content-Length check); `--force` to re-download. Write a `.partial` then rename on
  success so interrupted downloads don't leave corrupt files.

### 3. `batch.py` — run rmtables over a folder
- For each PDF in `--manuals-dir`, run `rmtables` → write `<rm_number|stem>.json` to
  `--json-dir` (default `json/`).
- Prefer importing rmtables' pipeline; else `subprocess` the CLI
  (`PYTHONPATH=... python -m rmtables.cli <pdf> -o <out> --validate`).
- **Sequential by default** (huge manuals peak ~2–3 GB RAM; RM0477 is 3764 pages).
  Optional `--jobs N` but document the memory risk; cap concurrency low.
- **Idempotent:** skip a PDF whose JSON already exists and is newer than the PDF;
  `--force` to re-run. Per-file try/except: one failure logs and continues.
- Emit a run summary: processed / skipped / failed, table counts per manual, elapsed.

### 4. `cli.py` — commands
```
stm32fetch catalog [--refresh]                 # build/update catalog.json
stm32fetch list [--series STM32F4]             # print matching manuals
stm32fetch download --all                      # download every RM
stm32fetch download --series STM32H7           # download matches for a series
stm32fetch download --rm RM0490                # download one manual
stm32fetch run [--manuals-dir .. --json-dir ..]# rmtables over downloaded PDFs
stm32fetch pipeline --all                      # catalog(if needed)+download+run
stm32fetch pipeline --series STM32C0           # end-to-end for one series
```
Global: `--manuals-dir`, `--json-dir`, `--rate`, `--jobs`, `--force`, `--log-level`.

## Series selection logic

- Normalize the query: `stm32c0` / `STM32C0` / `C0` → family key `STM32C0`.
- Match against each manual's `devices`/`series`. A query may match MULTIPLE manuals
  (e.g. `STM32H7` → RM0477 and others) — `list`/`download --series` act on ALL matches;
  print them so the user sees what will be fetched. `--rm RMxxxx` picks exactly one.
- If no match, print the closest available series from the catalog (don't silently do
  nothing).

## Robustness & correctness

- The catalog is cached; only `--refresh` or `pipeline` (when stale/missing) re-scrapes.
- All network ops: timeout + retry/backoff; clear per-item logging; a failed manual
  never aborts the batch.
- Downloads and JSON runs are both **resumable/idempotent** so re-running continues
  where it stopped.
- Keep ST politeness: rate-limit, User-Agent, robots.txt. Add a short README note that
  the PDFs are ST copyrighted material for personal/research use, not redistribution,
  and that scraping should respect ST's terms of use.

## Project layout

```
stm32fetch/
├── README.md            # setup (playwright install chromium), usage, legal note
├── requirements.txt     # playwright, requests, (reuse rmtables)
├── src/stm32fetch/{cli,catalog,download,batch,series}.py
├── catalog.json         # cached (gitignored)
└── tests/               # series-matching, filename/slug parsing, idempotency (mock net)
```

## Validation / acceptance

- `stm32fetch catalog --refresh` produces `catalog.json` with ~39 reference manuals,
  each with a valid `pdf_url`, `rm_number`, `devices`, `series`.
- `stm32fetch list --series STM32F1` shows RM0008 (and any other F1 manuals).
- `stm32fetch pipeline --series STM32C0` downloads RM0490 to `manuals/` and writes
  `RM0490.json` to `json/`, skipping both steps on a second run.
- `download --all` fetches every RM with rate-limiting and resume; `run` produces one
  JSON per PDF with a summary and no crash on an individual failure.
- Tests: series normalization/matching, slug→filename parsing, catalog-cache reuse,
  idempotent skip logic (network mocked).

## Notes for the implementer

- Playwright needs a one-time `playwright install chromium`; put it in README/setup.
- Optional optimization (don't rely on it): ST serves the resource list from a backend
  JSON API; if you can identify a stable endpoint, you may fetch the catalog without a
  browser — but keep Playwright as the robust default and the API as a fast path only.
- Because RM PDF URLs are deterministic, once the catalog exists, downloads need no
  browser — plain HTTP with the polite client is enough.
