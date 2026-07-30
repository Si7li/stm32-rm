# stm32fetch

Fetches ST's reference-manual catalog, downloads selected manual PDFs, and runs
[`rmtables`](../stm32-table-extractor) over them — either for every manual or for one
STM32 series. A separate package: it never modifies `rmtables`, only calls it (as a
library import or a subprocess).

## Setup

```
pip install -r requirements.txt
```

`rmtables` itself is located automatically if this checkout sits next to
`stm32-table-extractor/` (the layout in this repo). Otherwise point at it explicitly:

```
export RMTABLES_SRC=/path/to/stm32-table-extractor/src
```

or pass `--rmtables-src` to `run`/`pipeline`.

## Usage

```
stm32fetch catalog [--refresh]                  # fetch/update catalog.json from the API
stm32fetch catalog --verify                     # HEAD every pdf_url, flag any 404s
stm32fetch list [--series STM32F4]               # print matching manuals
stm32fetch download --all                        # download every RM
stm32fetch download --series STM32H7             # download matches for a series
stm32fetch download --rm RM0490                  # download one manual
stm32fetch run [--manuals-dir .. --json-dir ..]  # rmtables over downloaded PDFs
stm32fetch pipeline --all                        # catalog (if needed) + download + run
stm32fetch pipeline --series STM32C0             # end-to-end for one series
```

`run` and `pipeline` also take `--split-tables`/`--tables-dir DIR` (forwarded to
rmtables unmodified), writing one self-contained JSON file per table into
`<tables-dir>/{RM}_{Rev}/` alongside the combined `json/{RM}_{Rev}.json` (e.g.
`json/RM0490_Rev6.json` -- the revision-aware naming rmtables itself uses, see
its README). **`pipeline` turns `--split-tables` on by default**
(`--no-split-tables` opts back out); `run` keeps it off by default, matching
rmtables' own default. The split step has its own idempotency check
independent of the combined JSON's: it's skipped only when that manual's
`<tables-dir>/{RM}_{Rev}/_index.json` is already newer than the PDF (`--force`
overrides both). Both checks key off `{RM}_{Rev}` -- pre-derived from each
PDF's own cover pages the same way rmtables derives it, via a lightweight
metadata-only scan -- so a new manual revision is never mistaken for an
up-to-date one, and older revisions' JSON/folders are left alone rather than
overwritten (delete them by hand if you don't need them).

Global flags on every subcommand: `--manuals-dir` (default `manuals/`), `--json-dir`
(default `json/`), `--catalog-path` (default `catalog.json`), `--rate` (min seconds
between HTTP requests, default 1.0), `--jobs`, `--force`, `--impersonate` (curl_cffi
browser profile, default `chrome`), `--locale`, `--class-id`, `--resource-type`,
`--log-level`.

`download`/`pipeline` require exactly one of `--all` / `--series NAME` / `--rm RMxxxx`.
A series query is normalized (`stm32f4`, `F4`, `STM32F4` all match) and can match more
than one manual — every match is printed and acted on. `list` defaults to `--all` when
no filter is given. A query or `--rm` that matches nothing prints the available series
instead of silently doing nothing.

### The HTTP layer: why curl_cffi

ST's site sits behind Akamai TLS-fingerprint bot protection. Verified: plain `requests`,
system `curl` (both HTTP/2 and HTTP/1.1), and even a real bundled-Chromium Playwright
browser all fail against it (connection reset, or the request just never completes) --
none of their TLS handshakes reproduce a real browser's fingerprint closely enough.
`curl_cffi` with `impersonate="chrome"` does, and is the *only* HTTP client anywhere in
this package (`net.py`) -- every request, catalog fetch, `--verify` HEAD, and PDF
download alike, goes through one shared session. If ST tightens its check further,
bump `--impersonate` to a newer Chrome profile as curl_cffi adds support for one.

### Catalog: one source, cached

The catalog comes from exactly one place: ST's own `cxst` JSON API (the same endpoint
its documentation page's resource grid calls), built from `--locale`/`--class-id`/
`--resource-type` rather than hardcoded, so the same code lists other resource families
(datasheets, application notes, ...) by pointing `--resource-type` elsewhere. There is
no bundled seed data and no fallback scraping tier -- the on-disk `catalog.json` cache
*is* the offline story: `catalog` (no `--refresh`) never touches the network at all if
a cache exists, and `pipeline`/`download`/`list` build one automatically the first time
there isn't one. `--refresh` always re-fetches; if that fails and a cache already
exists, it's a logged warning and the existing cache is kept (never silently emptied).
If it fails with no cache at all, `catalog` exits non-zero with a clear error -- but
`run --manuals-dir <dir>` always works offline against whatever's already downloaded,
regardless of catalog state.

`catalog --verify` HEADs every cached `pdf_url` and reports any that don't come back
200 -- ST periodically renames a manual's slug, which silently breaks a cached URL
until you notice downloads failing; `--verify` catches that proactively.

### Downloads are resumable and idempotent

Re-running `download` (or `pipeline`) picks up where a previous run left off: an
already-downloaded PDF with a plausible size is skipped outright, and an interrupted
download resumes via HTTP `Range` from a `.partial` sibling rather than restarting --
*when the server actually honors the range request*. In practice, ST's CDN serves PDFs
gzip-compressed and doesn't reliably support `Range` against that compressed stream (or
consistently at all for this resource type), so a `Range` attempt that isn't honored
falls back to a clean full restart rather than risking a corrupted file; either way, the
download completes correctly, just not always with the bandwidth savings a working
`Range` would give. A `.partial` file is only ever renamed into place once a download
actually completes, so an interrupted run never leaves something that looks done but
isn't. Pass `--force` to redo a download (or an `rmtables` run) that's already up to
date.

`run`'s default `--mode subprocess` gives each manual its own process (some reference
manuals are 3000+ pages and peak at several GB of RAM); `--mode import` calls
`rmtables.cli.main()` directly in-process instead, at the cost of sharing memory across
every manual processed that way.

## Politeness / compliance

- robots.txt does **not** disallow `/resource/` (where the PDFs live); it **does**
  disallow `/search.html` and `/content/st_com/search-sitemaps/*` -- this package never
  requests either.
- The catalog API is only ever hit on `--refresh` (or the first run with no cache);
  every other run works from the cached `catalog.json`.
- Downloads are rate-limited (`--rate`, default ≥1s between requests) and retried with
  backoff rather than hammered.
- The downloaded PDFs are ST Microelectronics copyrighted material, provided here for
  personal/research use only — not for redistribution.
