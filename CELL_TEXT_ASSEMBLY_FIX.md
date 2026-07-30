# Task — fix sub/superscript placement and lost gap-spaces in cell text assembly

`cells.py::cell_text` assembles a cell's text from raw chars. Two flaws in that assembly
corrupt the text of **~270 tables** across RM0490 Rev6, RM0522 Rev1 and RM0486 Rev4. Both
are verified char-by-char against the source PDFs, and a prototype of the fix is included
below with measured results.

## The mechanism

```python
upright.sort(key=lambda c: (round(c["top"] / 2), c["x0"]))   # cells.py:161
...
line.append(text)                                             # joined with "".join(line)
```

Sorting primarily by a **2-point band of `top`** means any char not sitting exactly on the
baseline lands in its own band and is ordered *after* the whole baseline run. Joining with
`"".join` means a horizontal **layout gap** with no space glyph produces no space.

### Defect A — subscripts are torn out of position

RM0490 page 306, Table 72's second header cell. Actual chars:

```
'(' x0=173.82 top=173.32 size=9.0      't' x0=192.00 top=159.34 size=9.0
'f' x0=176.82 top=173.32 size=9.0      'S' x0=195.00 top=163.04 size=7.2
'A' x0=179.82 top=177.02 size=7.2      'A' x0=199.80 top=163.04 size=7.2
'D' x0=184.98 top=177.02 size=7.2      'R' x0=204.96 top=163.04 size=7.2
'C' x0=190.20 top=177.02 size=7.2      ' c y c l e s )' ... top=173.32 size=9.0
```

The cell is two visual lines — `t`+`SAR` and `(f`+`ADC`+` cycles)`. The 2-point banding
splits them into four (tops 159.34 / 163.04 / 173.32 / 177.02), producing
`"t \nSAR\n(f cycles)\nADC"`, which `_normalize_ws` then flattens into the emitted header
`t SAR (f cycles) ADC` instead of `tSAR (fADC cycles)`.

Affects ~18 tables, concentrated in timing/electrical tables (`t_SAR`, `f_ADC`, `t_CONV`,
`TD_RVU`/`TD_PVU`).

### Defect B — superscript footnote markers are moved to the FRONT

Same cause, opposite direction: a superscript sits *above* the baseline, so its band sorts
*first*. `I2C features` with a trailing `(1)` marker is emitted as `(1)I2C features`.

This is the largest impact, and it was not in the original bug list:

| Manual | tables with leading-`(N)` cells | cells | tables with leading-`(N)` headers |
|---|---|---|---|
| RM0486 | 123 | 730 | 34 |
| RM0522 | 98 | 302 | 28 |
| RM0490 | 29 | 103 | 12 |
| **total** | **250** | **1135** | **74** |

Examples: `(1)LSE monitoring`, `(1)NIST SP800-90B`, `(2)Software and hardware modes`,
`(1)SYSCFG(ITLINE)`, `(1)TPIU`, `(3)AES-256`. Every one should carry the marker as a
suffix. A retrieval hit on `(1)X` is meaningless; `X(1)` is correct.

### Defect C — layout gaps lose their space

RM0486 page 3671, Table 708's header cell:

```
'3' x0=148.68 x1=153.68    '1' x0=153.66 x1=158.66
'2' x0=261.18 x1=266.18    '4' x0=266.16 x1=271.16
gaps: [-0.03, 102.51, -0.03]
```

A **102.5-point** gap with no space char. `"".join` yields `3124`; the PDF reads `31 24`
(bits 31 down to 24). Affects the FDCAN message-RAM element tables — RM0490 T144–T152,
RM0522 T475–T483, RM0486 T708–T718 (16 tables) — plus scattered cells elsewhere.

## Fix — cluster on baselines, attach scripts, insert gap-spaces

Replace the sort-and-scan in `cell_text` with explicit line clustering. Prototype, already
run against the real PDFs:

```python
sizes = collections.Counter(round(c["size"], 1) for c in upright)
dom = max(sizes.items(), key=lambda kv: (kv[1], kv[0]))[0]      # dominant font size
main  = [c for c in upright if round(c["size"], 1) >= SMALL_RATIO * dom]
small = [c for c in upright if round(c["size"], 1) <  SMALL_RATIO * dom]
if not main:                       # an all-small cell is its own baseline
    main, small = upright, []

lines = []                         # cluster the baseline chars
for c in sorted(main, key=lambda c: (c["top"], c["x0"])):
    for L in lines:
        if abs(c["top"] - L["top"]) <= LINE_TOLERANCE:
            L["chars"].append(c); break
    else:
        lines.append({"top": c["top"], "chars": [c]})

for c in small:                    # attach each script to its nearest baseline
    if lines:
        L = min(lines, key=lambda L: abs(c["top"] - L["top"]))
        if abs(c["top"] - L["top"]) <= dom:
            L["chars"].append(c); continue
    lines.append({"top": c["top"], "chars": [c]})

for L in sorted(lines, key=lambda L: L["top"]):      # order by x, insert gap-spaces
    out, prev = [], None
    for c in sorted(L["chars"], key=lambda c: c["x0"]):
        if prev is not None and c["x0"] - prev["x1"] > GAP_RATIO * dom \
           and not out[-1].isspace() and c["text"] != " ":
            out.append(" ")
        out.append(_char_text(c["text"], c.get("fontname", "")))
        prev = c
    parts.append("".join(out))
```

`SMALL_RATIO = 0.85`, `GAP_RATIO = 0.28`. Both as named module constants with the
reasoning in a comment. `LINE_TOLERANCE` and `BBOX_PAD` keep their current values.

The rotated-char path is **unchanged** — vertical text still sorts by descending `top`, and
the un-reversal that makes register-map field names readable must not be touched.

### Verified prototype results

```
RM0490 p306 T72 header : 't \nSAR\n(f cycles)\nADC'  ->  'tSAR \n(fADC cycles)'
RM0486 p3671 T708 hdr  : '3124'                      ->  '31 24'
RM0490 p679            : '(1)I2C features'           ->  'I2C features(1)'
RM0490 p918            : '–00: Reserved'             ->  '– 00: Reserved'
```

Blast radius measured over a 120-page random sample of RM0490: **70 of 8,212 cells change
(0.85%)**. This is a targeted fix, not a rewrite of the text.

## Downstream effects — expected and to be reported, not suppressed

Cell text feeds `columns`/`headers`, `table_content.rows`, `features` and `text_helper`, so
those legitimately change on affected tables. What must NOT change:

- Rotated text handling. Register-map bit headers must still read `31..0` and field names
  must still un-reverse (`Res.`, not `.seR`).
- Symbol remapping (`fix_symbols`) still applied, still last.
- `columns == table_content.headers` still holds exactly.
- Table counts: 178 / 598 / 902. `--validate` still reports `missing: []` for RM0490.
- No new null cells; merged-cell fill unaffected.

### The one real risk: register-map bit parsing

The `register_map` semantic extractor parses bit-range headers. Today one RM0486 register
map has a fused numeric header; after the fix it becomes space-separated (`31 24`). Make the
grouped-header expansion accept `31 24` alongside `31-24`, and re-verify the register audit
holds — using the correct key, `semantic.registers[].name`, **not** `register`:

| metric | current | after |
|---|---|---|
| registers total | 4892 | 4892 |
| unnamed | 0 | 0 |
| pseudo "Reset value" entries | 0 | 0 |
| bad bit-range strings | 0 | 0 |
| registers not covering 31..0 | 15 | ≤ 15 |
| clean `0x` hex reset values | 3876 | ≥ 3876 |

## Validation

1. The four prototype cases above produce exactly the shown output.
2. Zero cells match `^\(\d{1,2}\)\S` in any emitted record (was 1,135 cells / 250 tables).
3. Zero headers match `^\d{3,4}$` on the 16 FDCAN element tables; they read `31 24`,
   `23 16`, `15 8`, `7 0`.
4. The register-map table above holds on all three manuals.
5. `columns == headers` on every record; table counts unchanged; no null cells.
6. Rotated text intact — spot-check RM0490 T26 (FLASH register map) headers read `31..0`
   and no cell contains `.seR` or a reversed field name.
7. Per-table split files match, and the combined-vs-split deep-equality test passes.
8. Report the total number of tables whose `table_content` changed, per manual. Expect
   roughly 270; a number far above that means `GAP_RATIO` is injecting spurious spaces —
   inspect before accepting.

## Tests

- Two-line cell with a subscript on each line → `tSAR` / `(fADC cycles)`.
- Superscript marker → suffix, not prefix.
- 102-point gap with no space glyph → one space inserted.
- Normal single-line cell with real space chars → byte-identical to today (golden test).
- A cell whose chars are *all* small → still emitted, not dropped.
- Rotated field-name cell → unchanged un-reversal.
- Kerning-sized gaps (< `GAP_RATIO * dom`) → no space inserted.

## Part 2 — captions lose their subscript (OPTIONAL, guarded)

`find_captions` reads `page.extract_text_lines()`, which has the same defect one level up:
on RM0490 page 306 the caption is line `top=137.22 "Table 72. t timings depending on
resolution"` with `SAR` as a **separate line** at `top=141.30`, size 7.98 vs 9.96. So the
title is emitted as `t timings depending on resolution` — ST's List of Tables says
`tSAR timings depending on resolution`. About 11 titles across the three manuals.

The fix is a `merge_script_lines(lines)` helper applied to the caption path: a line whose
chars are all smaller than `SMALL_RATIO * dominant size of the preceding line`, and within
one line-height of it, is spliced into that line at its x positions.

**Only do Part 2 if the guard passes**: caption detection is the most heavily patched area
of this codebase (CAPTION_ROBUSTNESS_FIX.md), and a change to the line list feeds caption
matching, notes, legends and heading tracking. Required guard — `--validate` on all three
manuals shows the same `missing`/`extra` sets as before, and table counts stay 178/598/902.
If anything shifts, revert Part 2 and leave it documented; the 11 titles are not worth
destabilising caption matching.

Also report, without changing them, how many `notes`, `legend` and `section_title` strings
*would* change if the same helper were applied to those paths. That is a separate decision.

## Out of scope

Parsing strategy, merged-cell fill, the figure boundary/cut logic, classification, the
semantic extractors beyond the bit-range tolerance above, `text_helper` templating
(TEXT_HELPER_FIX.md), and the Sidekick record shape.
