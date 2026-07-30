# Fix task — stm32fetch catalog must never be fatal + Playwright HTTP/2 fix

## What happened

`pipeline --series STM32C0` crashed: Playwright failed with
`net::ERR_HTTP2_PROTOCOL_ERROR`, the static-HTML fallback timed out (15 s), there was no
cache, and `build_catalog` raised `RuntimeError` — a hard crash. ST is up and reachable;
these are client-side issues, and the tool should degrade, not die.

## Root causes

1. **ERR_HTTP2_PROTOCOL_ERROR**: headless Chromium negotiating HTTP/2 with ST's CDN
   (Akamai) commonly fails. Fix: force HTTP/1.1.
2. **Static fetch timeout**: 15 s is too short for st.com and the request looks like a
   bot (minimal headers). Fix: longer timeout + realistic browser headers + retries.
3. **Fatal on no-catalog**: the tool depends on live scraping. But RM PDF URLs are
   deterministic, so it shouldn't. Fix: ship a bundled **seed catalog** and make catalog
   building degrade gracefully.

## Fix 1 — bundled seed catalog (primary; makes the tool work offline)

- Ship `seed_catalog.json` (provided) inside the package (`src/stm32fetch/data/`).
- New catalog precedence in `build_catalog` (never raise if ANY source yields manuals):
  1. on-disk cache (`catalog.json`) if present and not `--refresh`;
  2. live scrape (Playwright, then static HTML) — **best-effort**, wrapped so failure is
     a logged warning, not an exception;
  3. bundled `seed_catalog.json`;
  4. user `rm_list_file` (merged in if given).
- **Merge** sources by `rm_number` (live/refreshed entries override seed). Write the
  merged result to `catalog.json` as the cache.
- Only raise if ALL sources yield zero manuals AND no seed shipped — which shouldn't
  happen since the seed is bundled. On live-scrape failure with seed present: log
  `"using bundled seed catalog (N manuals); run 'catalog --refresh' when ST is reachable"`
  and continue.

## Fix 2 — Playwright robustness

- Launch chromium with:
  `args=["--disable-http2", "--disable-blink-features=AutomationControlled",
         "--no-sandbox"]`, a realistic `user_agent`, and a normal `viewport`.
- `page.goto(url, wait_until="domcontentloaded", timeout=60000)` (not "load").
- After nav, wait explicitly for the resource list container selector (bounded timeout);
  if it doesn't appear, fall through to static/seed rather than hanging.
- Keep 2–3 retries with backoff; treat all Playwright errors as recoverable warnings.

## Fix 3 — static HTML fallback robustness

- Timeout 45–60 s; send full browser-like headers (`User-Agent`, `Accept`,
  `Accept-Language`, `Referer`); retry with backoff. Parse whatever RM links exist
  (the static page exposes ~5) and merge; never fatal.

## Fix 4 — rm_list_file + refresh UX

- `rm_list_file`: accept one entry per line as either a full RM PDF URL, or `RMxxxx`,
  or `RMxxxx <pdf_url>`. Build catalog entries from these (derive slug/filename/series
  from the URL/number).
- `catalog --refresh` attempts live scrape and, on success, overwrites the cache with the
  full ~39-manual list; on failure keeps the seed/cache and warns.
- Document in README: if scraping stays blocked, the seed covers common series and users
  can extend via `rm_list_file` (copy RM PDF links from the ST page).

## Acceptance

- `pipeline --series STM32C0` with NO network and NO cache: uses the seed, downloads
  RM0490, writes `RM0490.json` — no crash.
- `catalog` with Playwright failing: logs a warning, falls back to seed, exits 0 with a
  usable `catalog.json`.
- `list --series STM32G0` shows RM0444 and RM0454 from the seed.
- When ST is reachable, `catalog --refresh` replaces the seed with the full rendered
  list (HTTP/2 disabled so it actually loads).
- Tests: catalog precedence/merge, seed fallback on scrape failure (mocked),
  rm_list_file parsing, HTTP/2-disabled launch args present.

## Note

The provided `seed_catalog.json` has 13 verified manuals across C0/F1/F2/F4/G0/G4/H7/
L1/L4 with exact `pdf_url`s. Treat it as the reliable floor; live scraping only extends
it. Because downloads are deterministic from `pdf_url`, the seed alone makes the whole
download+rmtables pipeline fully functional without ever rendering the JS page.
