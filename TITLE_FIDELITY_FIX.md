# Task — title fidelity and residual defects

The remaining defects after `FIGURE_CAPTION_BOUNDARY_FIX.md`, `TEXT_HELPER_FIX.md` and
`CELL_TEXT_ASSEMBLY_FIX.md`. All measured against the three PDFs (RM0490 Rev6, RM0522 Rev1,
RM0486 Rev4) and their `List of Tables`.

Ordered by value. Parts 3 and 4 are deliberately conservative — read them before touching
anything.

---

## Part 1 — titles carry footnote markers (68 tables)

`title` is taken from the printed body caption, which includes the superscript footnote
reference ST prints on it. ST's own List of Tables does not:

| Manual | affected | example |
|---|---|---|
| RM0486 | 39 | `SDRAM address mapping with 8-bit data bus width(1)(2)` |
| RM0522 | 24 | `Peripherals interconnect matrix(1)(2)` |
| RM0490 | 5 | `Port bit configuration table(1)` |

The marker is a reference to a footnote already captured in `notes`. It is not part of the
table's name, and it pollutes the title used in Sidekick's link label
(`{{document}}#Table {{table_number}}: {{title}}`).

### Fix
Strip standalone numeric footnote markers from `title`: remove every `\(\d{1,2}\)` group,
then collapse whitespace via the existing `_normalize_ws`.

**Only pure-digit groups.** `Reset source identification (RCC_RSR)(1)` must become
`Reset source identification (RCC_RSR)` — the alphanumeric parenthetical is part of the
name. Verified: stripping pure-digit groups alone reconciles 58 of the 68 against ST's LoT
exactly.

---

## Part 2 — repair captions damaged by rendering (≈5 tables)

A few body captions are damaged by the artifacts this project has fought throughout:

```
T82  (RM0490)  body: 'O utput control bits for complementary OCx and OCxN channels...'
               LoT : 'Output control bits for complementary OCx and OCxN channels...'
T72  (RM0490)  body: 't timings depending on resolution'        <- subscript lost
               LoT : 'tSAR timings depending on resolution'
T142 (RM0522)  body: 'T timings depending on resolution'
               LoT : 'TSAR timings depending on resolution'
```

ST's List of Tables is a **clean, independent rendering of the same title** — verified: zero
LoT entries across all three manuals contain dot-leader remnants, page-number leakage, or
empty text.

### Fix
When a table is present in the LoT, compare the Part-1-stripped body title with the LoT
title:

- identical → keep the body title;
- differ **only by letter case** → keep the **body** title (it is the printed caption; ST's
  index inconsistently title-cases some ETH tables);
- differ in any other way → use the **LoT** title, and log
  `INFO: table {n} title taken from List of Tables (body caption damaged): {body!r} -> {lot!r}`.

Tables absent from the LoT (18 across the corpus — the genuine extras ST omitted from its
index) always keep the body caption.

### Verified expected outcome
After Parts 1 and 2, differences against the LoT drop to **zero** except the case-only ones
deliberately preserved:

| Manual | differences now | after | case-only kept |
|---|---|---|---|
| RM0490 | 5 | 0 | 0 |
| RM0522 | 26 | 0 | 2 |
| RM0486 | 47 | 0 | 5 |

Do **not** rewrite `table_number`, `page`, or anything else from the LoT — the body is
authoritative for those, and the LoT is known to be wrong at least once (it lists
RM0522 T423 "I3C instantiation", which does not exist in the manual: page 1690 has
section 42.3.1 and a sentence, no table).

---

## Part 3 — false heading match (1 table)

RM0486 T735 (page 3760, "OTG speeds supported") resolves to
`section: "2.0"`, `section_title: "specification, July 16, 2007"`. The heading tracker
matched the body text *"USB 2.0 specification, July 16, 2007"* as a section heading.

This is the same class `METADATA_FIXES.md` addressed for ToC lines, and it survives because
the guard only rejects dot-leader/page-number patterns.

### Fix
Add two cheap guards to the heading candidate test in `headings.py`:

1. The title following the number must start with an **uppercase letter**. ST headings
   always do; `specification` does not.
2. Reject a section number whose final component is `0` (`2.0`, `10.0`). ST numbers sections
   from `.1`.

Either guard alone rejects this case; implement both, they are independent and cheap.

Guard: re-run all three manuals and confirm **no other table's `section`/`section_title`
changes**. One table should move; if more do, the guards are too strong — report and stop.

---

## Part 4 — register bit-coverage (15 registers of 4892) — INVESTIGATE, FIX ONLY IF SAFE

Two clusters, and they are different problems:

**RM0490 T139 (SPI/I2S register map), 9 registers** — every one missing exactly bits 16–21:

```
SPIx_CR1  fields=14  missing [16,17,18,19,20,21]  dup 0
SPIx_CR2  fields=13  missing [16,17,18,19,20,21]  dup 0
SPIx_DR   fields=1   missing [16,17,18,19,20,21]  dup 0
```

The identical missing run across every register in the table points at the table's header
geometry, not at individual registers. Note `validate.py` already documents T139 as a
genuinely narrower register map.

**RM0486 T902 (DBGMCU register map), 6 registers** — overlapping fields:

```
DBGMCU_CR        fields=10  missing -                dup 12
DBGMCU_APB2FZR   fields=9   missing -                dup 16
DBGMCU_APB1LFZR  fields=21  missing [14..19]         dup 10
```

`dup` means the same bit position is claimed by more than one field — a spanning field label
assigned an over-wide range.

### Approach
Diagnose both against the PDF pages first and report the root cause. **Do not apply a fix
that changes any register outside these two tables.** 15 of 4,892 registers (0.3%) is not
worth destabilising a register extractor that is otherwise clean — 0 unnamed, 0 pseudo
"Reset value" entries, 0 malformed bit-range strings, 3,876 proper `0x` hex resets.

If the fix cannot be contained to T139 and T902, leave both documented as known limitations
and say so.

---

## Part 5 — classifier under-coverage: LEAVE ALONE, DOCUMENT ONLY

About 7 tables look like `memory_map` or `interrupt_vector` but sit in `generic` (4 + 1 in
RM0522, 4 + 1 in RM0486). This is the conservative classifier behaving as designed, and the
project's standing decision is that a wrong type is worse than `generic` for retrieval.

**Make no classifier change.** Record the list in the README's known-limitations section so
it is a documented choice rather than an unnoticed gap.

---

## What must NOT change

- Table counts: 178 / 598 / 902. `--validate` still reports `missing: []` for RM0490.
- `table_number`, `page`, `section` (except the single T735 correction), `url`, `url_pdf`,
  `columns`, `table_content`, `features`, `semantic_type` — all unchanged.
- `text_helper` will change on the affected tables because it embeds the title. That is
  expected; assert the change is confined to the title substring.
- Parsing, merged-cell fill, symbol remap, figure boundary/cut logic, the semantic
  extractors and the Sidekick record shape are untouched.

## Validation

1. Zero titles match `\(\d{1,2}\)` (was 68).
2. Titles differing from the LoT: zero except the 7 case-only cases listed above.
3. Exactly one table's `section`/`section_title` changes (RM0486 T735).
4. The 18 tables absent from the LoT keep their body captions unchanged.
5. Table counts unchanged; `--validate` missing/extra sets unchanged on all three manuals.
6. `text_helper` differs only where the title differs.
7. Per-table split files match, and the combined-vs-split deep-equality test passes.

## Tests

- `(1)` / `(1)(2)` markers stripped; `(RCC_RSR)` preserved.
- LoT repair fires for `O utput...` and not for a case-only difference.
- A table absent from the LoT keeps its body caption.
- Heading guard rejects `2.0 specification, July 16, 2007` and accepts `42.3.1 I3C
  instantiation`.
- A title that is legitimately all lowercase in ST's caption is not rejected by the heading
  guards (the guards apply to headings, not captions).
