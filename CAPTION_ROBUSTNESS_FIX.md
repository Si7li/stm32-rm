# Fix task — robust caption recognition (tables missed on dense manuals)

## Problem (verified on RM0477, STM32H7, 3764 pages)

12 of 787 tables were missed. The tables and their data extract fine — only the
CAPTION line fails to match, because in dense ST layouts the caption renders with:
1. **Split "Table" word:** `"T able 688. Coding for ..."`, `"Ta b le 346. Key endianness ..."`,
   `"T able 437. Counting direction ..."` (spurious spaces inside the word).
2. **Leading stray fragment:** `"7 Table 29. FLASH recommended ..."`, `"t Table 202. 8-bit NAND ..."`.
3. **No space before the number:** `"Table332. ..."`.

The current matcher expects `^\s*Table\s+\d+\.` and misses all three. A tolerant matcher
recovers 10/12 in testing; the last 2 are rarer and are acceptably left for `--validate`
to report.

## Fix — tolerant caption matcher (use for BOTH body captions and the List-of-tables parser)

Match with intra-word spaces in "Table", an optional short leading fragment, an optional
missing space before the number, and a REQUIRED period + capitalized title:

```python
CAP = re.compile(r'T\s?a\s?b\s?l\s?e\s*(\d+)\s*[.\u2024]\s+([A-Z0-9\u00b5(].*)')
def match_caption(line):
    m = CAP.search(line)
    if not m or m.start() > 12:          # only a short leading fragment allowed
        return None
    return int(m.group(1)), m.group(2).strip()
```

Rules:
- The `T\s?a\s?b\s?l\s?e` pattern absorbs `T able`, `Ta b le`, `Tab le`, `Table`.
- `Table\s*(\d+)` (`\s*`) absorbs the no-space `Table332` case.
- Requiring `. <Capitalized title>` after the number rejects bare prose cross-references
  like `"Refer to Table 332."` (no title follows).
- **Position association still decides:** keep assigning each detected table the nearest
  matched caption ABOVE it. This is what disambiguates a real caption from an in-prose
  cross-reference that happens to be followed by a capitalized word (e.g. T419/T437,
  where the cross-ref sentence starts with "The ..." but the real caption "Counting
  direction ..." sits directly above the table).
- Apply the SAME tolerant matcher in the List-of-tables parser (front matter), together
  with the existing spaced-dot-leader handling, so `missing`/`extra` reconciliation and
  the caption set both improve. (Several `extra` reports are LoT entries the strict
  parser skipped — split "Table" in the LoT — not truly unlisted tables.)

## Do NOT over-match

- Keep the title requirement and the `m.start() <= 12` bound so ordinary sentences
  containing "table" or a cross-ref don't become captions.
- Keep the Figure-vs-Table position guard already in place.

## Acceptance

- RM0477: previously-missing set shrinks from 12 to ≤ 2 (T541, T689 may remain — report
  them, don't fabricate). No NEW missing or extra beyond what's genuinely unlisted.
- RM0490: still `missing: []` (no regression).
- RM0008: no new misses; the split-word tolerance may also recover its earlier stragglers.
- No new false captions: table count doesn't balloon; `caption mismatches` stays 0;
  spot-check that no prose line became a table.
- Add tests: split-word caption (`"T able 688. ..."`), leading-fragment
  (`"7 Table 29. ..."`), no-space (`"Table332. ..."`), and cross-ref rejection
  (`"Refer to Table 332."` → not a caption).

## Note on residual

On very large/dense manuals a handful of captions may still resist (heavier splitting,
caption far from its table, or page-offset). `--validate` already lists them, so they are
visible and can be accepted or hand-added — never silently lost. This is expected and
acceptable; the goal is to minimize, not to guarantee zero on every 3000+ page document.
