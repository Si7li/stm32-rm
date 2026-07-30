# Task — fix truncated `core` detection (Cortex-M55 → "Cortex-M5") and harden metadata regexes

## Bug

`metadata.py` derives the core with a regex like:
```python
re.search(r'Cortex[\u00ae\-\s]*M(\d\+?)', txt)   # \d = EXACTLY ONE digit
```
`\d` matches a single digit, so every **two-digit** Arm core is truncated.

Verified against real manuals:

| manual | PDF says | current output | correct |
|---|---|---|---|
| RM0486 (STM32N6) | `Cortex®-M55` | `Arm 32-bit Cortex-M5 CPU` | Cortex-M55 |
| RM0456 (STM32U5) | `Cortex®-M33` | `Arm 32-bit Cortex-M3 CPU` | Cortex-M33 |
| (future M85) | `Cortex-M85` | `Cortex-M8` | Cortex-M85 |
| RM0490 (STM32C0) | `Cortex®-M0+` | `Cortex-M0+` ✔ | — |
| RM0008 (STM32F1) | `Cortex-M3` | `Cortex-M3` ✔ | — |

The U5 case is the most dangerous: `Cortex-M3` is a real core, so the wrong value looks
plausible and passes review. This is a **silent data-corruption bug**, not a crash.

## Fix 1 — core regex

Replace with a pattern that accepts 1–3 digits, an optional `+`, the ® / ™ symbols, and
whitespace around the separator (ST's PDFs render `Cortex®-M55` and insert stray spaces):

```python
CORE_RE = re.compile(
    r'Cortex\s*[\u00ae\u2122]?\s*[-\u2011\u2013\u2014]?\s*M\s*(\d{1,3})\s*(\+?)',
    re.I)
m = CORE_RE.search(txt)
core = f"Arm 32-bit Cortex-M{m.group(1)}{m.group(2)} CPU" if m else ""
```
Verified to yield: M55, M33, M85, M0+, M3, M7 — and to still match when spaces are
injected (`Cortex ® - M 55`).

**Longest-match rule:** if several distinct core numbers appear (e.g. an H7 manual
mentioning both M7 and M4), prefer the FIRST match on the cover/title pages, and log all
distinct matches found at DEBUG so a multi-core manual is visible rather than silently
reduced to one.

**Keep "Arm 32-bit"** — all Cortex-M cores are 32-bit, so the prefix stays correct.

## Fix 2 — audit ALL metadata regexes for the same single-character class mistake

The same bug class (`\d` where `\d+` is needed) may exist elsewhere in metadata
derivation. Review and fix each:

- `family`: `STM32([A-Z]\d)` → fine for C0/F1/H7, but verify against series with two
  digits or letters (e.g. `STM32WBA`, `STM32H7R`). Use `STM32([A-Z]\d[A-Z]?)` and confirm
  it still yields `C0`, `F1`, `H7`, `U5`, `N6`, and sensible values for WB/WL/WBA.
- `name_datasheet`: `RM(\d{3,4})` — confirm 4-digit RMs (RM0486, RM0522) match and that a
  5-digit future number would not truncate.
- `rev`: `Rev\s+(\d+)` — must accept multi-digit revisions (e.g. `Rev 21` on RM0008).
  Verify RM0008 → `Rev 21`, not `Rev 2`.
- `frequency`: `up to (\d+)\s*MHz` — must accept 3-digit speeds (e.g. `up to 600 MHz` on
  RM0477/N6-class parts), not just 2.
- `references`: device tokens `STM32[A-Z0-9x/]+` — confirm multi-device lists are complete.

For every one of these, add a test with a multi-digit example. The general rule: **no
metadata regex may capture a fixed single character where a variable-length token is
possible.**

## Fix 3 — sanity validation on derived metadata

Add cheap assertions/warnings so a wrong value surfaces instead of shipping silently:
- `core`: if a Cortex number is found, it must be one of the known Arm Cortex-M cores
  (0, 0+, 1, 3, 4, 7, 23, 33, 35, 52, 55, 85). If not, log a WARNING with the raw matched
  text. Do NOT hard-fail — a new core could legitimately appear.
- `rev`: warn if empty or > 3 digits.
- `frequency`: warn if the number is outside ~1–2000 MHz.
- Log the full derived metadata block once per run at INFO so it can be eyeballed.

## Constraints

- Metadata derivation only. Do NOT touch parsing, caption detection, continuation merge,
  notes/legend, merged-cell fill, symbol remap, classification, or the semantic
  extractors.
- Output structure unchanged — only the VALUES of derived metadata fields change.
- The per-record `document`/`rev`/`url_pdf` copies must reflect the corrected values.

## Validation

Re-run RM0486, RM0490, RM0008, RM0477, RM0503, RM0522 and assert:
- RM0486 → `core == "Arm 32-bit Cortex-M55 CPU"`.
- RM0490 → `Cortex-M0+`; RM0008 → `Cortex-M3` **and** `rev == "Rev 21"`.
- Any U5/H5/N6-class manual processed yields the correct two-digit core (M33/M55).
- No `core` value ends in a digit that is a truncation of a longer number present in the
  source text (add a test: re-scan the cover text; the captured core number must be a
  MAXIMAL digit run, i.e. not followed immediately by another digit).
- `family`, `name_datasheet`, `rev`, `frequency` all correct on every manual above.
- Everything else unchanged: table counts, no duplicate table numbers, records still
  flat and template-renderable.

Add tests: `Cortex®-M55`, `Cortex®-M33`, `Cortex-M85`, `Cortex®-M0+`, `Cortex-M3`,
spaced/®-variant forms, `Rev 21`, `up to 600 MHz`, and a maximal-digit-run assertion.
