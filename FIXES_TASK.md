# Fix task — rmtables (post-validation)

The tool works and is schema-clean (175 tables, 24 tests pass). These fixes are
**localized** — do NOT rewrite the extraction/merge/notes logic that already works,
except exactly where specified. Add tests. After EACH fix, re-run the full RM0490
extraction + `--validate` and confirm no regressions (checklist at bottom).

Root causes below are **verified against RM0490** — don't re-investigate from scratch.

## Fix 1 (PRIORITY) — Robust caption text + matching

One root cause underlies four symptoms: caption detection trusts raw
`extract_text_lines()` text, which mis-renders some captions.

Verified cases:
- **Table 179** — body caption is `". Table 179. DBG register map and reset values"`;
  a leading `". "` defeats a `^\s*Table` anchor.
- **Table 45** — caption word is split at char level; **no** line matches `Table\s*45`.
- **Table 142** — only the inline cross-ref `"...according to Table 142."` appears as a
  line; the real caption is broken and/or must not be confused with a cross-reference.
- **Tables 92/94** — `table_name` comes out `"O utput control bits..."` (stray
  intra-word space) from raw line text.

Do:
1. **Reconstruct caption text from `page.chars` using x-gap-based spacing** (same
   principle as `cells.py`), not raw line text. This heals split/garbled words
   (`"O utput"→"Output"`, `"Tab le"→"Table"`) for BOTH the number match and the stored
   `table_name`.
2. **Tolerant match:** allow leading punctuation/whitespace before `Table`; accept the
   caption form `Table\s+(\d+)\s*\.\s+<capitalized title>`. Requiring a title after the
   number naturally rejects inline cross-refs like `"...according to Table 142."`.
3. Keep the existing position rule (nearest caption above the grid, closer than any
   `Figure`) as a second guard.
4. Diagnose 45/142/179 by dumping their reconstructed caption chars first, then confirm
   the new matcher catches all three.

Verify: `--validate` "missing" → shrinks to ~0 (45, 142, 179 recovered); table count
rises toward 178; `table_name` for 92/94 reads `"Output..."`; schema invariants still
hold; no NEW misses/extras.

## Fix 2 (PRIORITY) — Validator "List of tables" parser tolerance

Root cause: entries 92 and 94 are listed (p31) **without** trailing dot-leaders/page
number, so the List-of-tables parser skips them → falsely reported as "extra".

Do: make the List-of-tables parser capture entries even when the trailing
dot-leader + page number is absent or wraps to the next line. Reuse the tolerant /
char-reconstructed matcher from Fix 1 so front-matter and body parsing agree.

Verify: `--validate` "extra" no longer lists 92/94; listed-set count ≈ 178.

## Fix 3 (OPTIONAL, guarded) — Tables 30/89 narrow header cells

A few single-digit **bit-number header labels** read empty in 2 of 26 register-map
tables (row data is intact; this is cosmetic). If attempted: use **center-point**
membership (glyph center inside the cell bbox) for header cells instead of strict
containment. **Mandatory guard:** re-extract ALL 26 register-map tables; confirm 30/89
improve AND none of the other 24 regress; if any regression, **revert** and keep it as
the documented limitation. (A quick test suggests center-point alone may not fully fix
page 542 — leaving it documented is an acceptable outcome. Do not over-engineer.)

## Fix 4 (OPTIONAL) — Memory ceiling

Peak ~2.6 GB on the 1023-page run (pdfplumber permanently retains `Page` objects; not a
leak). Only if constrained-machine runs are needed: add `--chunk-pages N` that reopens
the PDF per N-page window and streams results, carrying any in-progress continuation
table across the window boundary. Otherwise leave as documented.

## DO NOT implement yet (pending instructor decision)
- Typed `table_content` categories (`parameters`/`pins`/etc.).
- Register bit-layout diagram inclusion.

## Verification checklist — run after EACH fix
- Full RM0490 run completes, no OOM.
- Schema: every `table_number` is str, `page` is int, no null cells,
  `filters.columns == table_content.headers`, no duplicate numbers, no empty tables.
- `--validate`: target `missing → []`, `extra → []`, count ≈ 178.
- Notes unchanged & clean: Table 9 = 1 note, Table 27 = 13 notes, no footer leakage.
- Tests green; add `test_captions.py`: leading-punctuation caption, split-word caption,
  cross-ref rejection, garbled-name healing.
