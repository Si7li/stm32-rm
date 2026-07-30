# stm32fetch — FINAL consolidated spec (supersedes all earlier fetch/catalog plans)

Build/refactor `stm32fetch` to exactly this design. Everything here is verified working.
**Delete the legacy workaround code listed in §7** — it exists only because the catalog
API had not been found yet, and keeping it now causes stale data and dead dependencies.

## 1. Purpose

Pick an STM32 series (or all) → fetch the reference-manual list from ST → download the
PDFs → run the existing `rmtables` on them → write one JSON per manual. Fully automatic,
no manual links, no browser.

## 2. HTTP layer (`net.py`) — the ONLY way to talk to st.com

ST is behind Akamai TLS-fingerprint bot protection. Verified facts:
- plain `requests`, system `curl` (HTTP/2 **and** HTTP/1.1), and bundled-chromium
  Playwright all FAIL (stream reset / 0 bytes).
- `curl_cffi` with `impersonate="chrome"` WORKS (RM0490 PDF → 200, 11,536,384 bytes).

Therefore: one shared session, used for every st.com request.
```python
from curl_cffi import requests as cffi
session = cffi.Session(impersonate="chrome")   # profile configurable via --impersonate
```
Timeouts `(connect=10, read=120)`; retries with exponential backoff on timeout/5xx/429.
No other HTTP client anywhere in the package.

## 3. Catalog (`catalog.py`) — ST's cxst JSON API is the ONLY live source

Endpoint (verified: 200, 28,089 bytes, 39 records):
```
https://www.st.com/bin/st/selectors/cxst/en.cxst-rs-grid.html/CL1734.technical_literature.reference_manual.json
```
Build it from parameters, don't hardcode:
```
/bin/st/selectors/cxst/{locale}.cxst-rs-grid.html/{class_id}.{category}.{resource_type}.json
  locale="en"  class_id="CL1734"  category="technical_literature"  resource_type="reference_manual"
```
CLI: `--locale`, `--class-id`, `--resource-type` (so datasheets/app-notes/other families
work with the same code).

### Confirmed payload
```json
{ "title": "Reference Manual",
  "rows": [
    { "title": "RM0386", "version": "6.0", "latestUpdate": 1716156000000,
      "physicalResourceType": "reference_manual",
      "localizedDescriptions": { "en": "STM32F469xx and STM32F479xx advanced Arm-based 32-bit MCUs" },
      "localizedLinks":        { "en": "/resource/en/reference_manual/rm0386-...-stmicroelectronics.pdf" },
      "resourcePath": "/content/ccc/resource/technical/document/reference_manual/.../DM00127514.pdf" } ] }
```
`rows` = 39 entries.

### Field mapping (rows[i] → catalog entry)
| field | source |
|---|---|
| `rm_number` | `title` (validate `^RM\d{3,4}$`) |
| `rev` | `version` |
| `updated` | `latestUpdate` (epoch **ms**) → ISO-8601 |
| `pdf_url` | `localizedLinks[locale]`, else `["en"]`, else any → prefix `https://www.st.com` if relative |
| `title` | `localizedDescriptions[locale]` with same fallback chain |
| `filename` | basename of `pdf_url`; `slug` = filename minus `.pdf` |
| `devices` | regex `STM32[A-Z0-9x/]+` over the description, deduped in order |
| `series` | per device `STM32([A-Z]\d)` → e.g. `STM32F4`; sorted unique |
| `resource_path` | `resourcePath` |

Filter rows to `physicalResourceType == "reference_manual"`. Skip+log rows with no usable
link; one bad row must never abort the parse.

### Source precedence (only two)
1. `catalog.json` on-disk cache — used unless `--refresh`.
2. cxst API — fetched on `--refresh`, or automatically when no cache exists.

**The cache is the offline story.** No bundled seed, no third-party catalog, no browser.
If the API fails and a cache exists → warn and use the cache. If it fails with no cache →
one clear error (exit non-zero), but `run --manuals-dir` must still work.

## 4. Download (`download.py`)

- Only `pdf_url`s from the catalog, via the shared session.
- Stream to `<file>.partial` → rename on success; resume with `Range: bytes=<have>-`.
- Verify final size against `Content-Length`; delete + retry on mismatch.
- Idempotent: skip if final file exists with plausible size; `--force` re-downloads.
- Sequential, with a configurable polite delay (`--rate`, default ≥1 s).

## 5. Batch runner (`batch.py`)

- For each PDF in `--manuals-dir`, run `rmtables` → `<rm_number>.json` in `--json-dir`.
- Import rmtables' pipeline if practical, else subprocess its CLI. **Do not modify rmtables.**
- Sequential by default (RM0477 is 3764 pages, ~2–3 GB peak RAM). `--jobs N` allowed but
  documented as memory-risky.
- Idempotent: skip when JSON exists and is newer than the PDF; `--force` re-runs.
- Per-file try/except: a failure logs and the batch continues.
- Print a summary: processed / skipped / failed, tables per manual, elapsed.

## 6. CLI (`cli.py`)

```
stm32fetch catalog [--refresh]              # fetch/update catalog.json from the API
stm32fetch catalog --verify                 # HEAD every pdf_url, flag 404s (ST renames slugs)
stm32fetch list [--series STM32F4]          # show matching manuals
stm32fetch download --all | --series X | --rm RM0490
stm32fetch run [--manuals-dir D --json-dir D]
stm32fetch pipeline --all | --series X      # catalog(if needed) → download → run
```
Global: `--manuals-dir` (default `manuals/`), `--json-dir` (default `json/`), `--rate`,
`--jobs`, `--force`, `--impersonate`, `--locale`, `--class-id`, `--resource-type`,
`--log-level`.

**Series matching:** normalize `stm32f4` / `STM32F4` / `F4` → `STM32F4`; match against each
entry's `series`/`devices`. One series may match several manuals — act on all and print
them. `--rm` selects exactly one. No match → list the available series, don't no-op.

## 7. DELETE these (legacy workarounds — now harmful)

- `seed_catalog.json` and all seed-fallback logic (stale slugs would override fresh API data).
- The GitHub `stm32-rs/stm32_part_table.yaml` source and its parser.
- All Playwright / headless-chromium / real-Chrome tier code, and the `playwright`
  dependency + `playwright install` docs.
- The IPv4-forcing / `host-resolver-rules` / `--disable-http2` code (the problem was TLS
  fingerprinting, solved by curl_cffi).
- Any `requests`-based st.com path, the sitemap/HTML-scraping attempts, and
  `--rm-list-file`.
- Any multi-tier escalation logic — there is one transport and one catalog source now.

## 8. Dependencies

`curl_cffi` only (plus the existing `rmtables` deps). Remove `playwright`, `requests`,
`beautifulsoup4`, `scrapy` if they were added for this tool.

## 9. Politeness / compliance (README)

- robots.txt does NOT disallow `/resource/`; it DOES disallow `/search.html` and
  `/content/st_com/search-sitemaps/*` — never request those.
- Hit the catalog endpoint only on `--refresh`; cache the result; rate-limit downloads.
- PDFs are ST copyright, for personal/research use — not redistribution.
- Note that curl_cffi impersonation is required and that `--impersonate` can be bumped to
  a newer Chrome profile if ST tightens.

## 10. Tests (network mocked)

Catalog URL construction from params; `rows` fixture → correct rm_number / pdf_url /
devices / series; epoch-ms → ISO; locale fallback chain; malformed payload is non-fatal;
cache-vs-refresh precedence; series normalization/matching; resumable `Range` logic;
idempotent skips in both download and batch.

## 11. Acceptance

- `stm32fetch catalog --refresh` → `catalog.json` with **39** manuals, each with absolute
  `pdf_url`, `rm_number`, `rev`, `devices`, `series`.
- `stm32fetch list --series STM32F4` → includes RM0386.
- `stm32fetch pipeline --series STM32C0` → downloads RM0490 (~11.5 MB) and writes
  `RM0490.json`; re-running skips both steps.
- `stm32fetch catalog --verify` → reports any 404 `pdf_url`s.
- `stm32fetch run --manuals-dir <dir>` → works with no network.
- No `playwright`/`requests`/seed/GitHub code remains; `grep` for them comes back clean.
