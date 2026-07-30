# Task — Fix Symbol-font special characters + capture table legends

Two additions to the working extractor. Verified against RM0008 (STM32F1). Keep all
existing behavior; these are additive/corrective. No external services.

## Problem 1 — Symbol-font special characters come out as PUA garbage

Root cause: ST uses the `SymbolMT` font for symbols; the PDF has no ToUnicode for it, so
pdfplumber emits each glyph as a Private-Use codepoint `U+F0xx = 0xF000 + (Symbol byte)`.
In RM0008 output: `U+F0B7` ×361, `U+F0E0` ×56, `U+F0A3` ×7, `U+F020` ×2 — all garbage.

Confirmed meanings (Adobe Symbol encoding): `F0B7→"•"`, `F0E0→"→"`, `F0A3→"≤"`,
`F020→" "`.

Fix — remap at the lowest level (in the cell/text char-join, `cells.py`) so it flows to
headers, rows, notes, legend, caption/`table_name`, `section_title`, and the derived
`text`/`tags`:

```python
# Adobe Symbol encoding: low byte (Symbolfont code) -> Unicode. Use the FULL table;
# this is the high-value/confirmed subset.
SYMBOL = {0x20:' ',0x2D:'\u2212',0xA3:'\u2264',0xB0:'\u00b0',0xB1:'\u00b1',
 0xB3:'\u2265',0xB4:'\u00d7',0xB7:'\u2022',0xB8:'\u00f7',0xB9:'\u2260',0xBB:'\u2248',
 0xAB:'\u2194',0xAC:'\u2190',0xAD:'\u2191',0xAE:'\u2192',0xAF:'\u2193',
 0xDA:'\u21d4',0xDB:'\u21d0',0xDC:'\u21d1',0xDD:'\u21d2',0xDE:'\u21d3',0xE0:'\u2192'}
def fix_symbols(s):
    out=[]
    for ch in s:
        o=ord(ch)
        if 0xF000<=o<=0xF0FF: out.append(SYMBOL.get(o-0xF000,''))  # drop unmapped PUA
        else: out.append(ch)
    return ''.join(out)
```

- Prefer using the **full Adobe Symbol encoding table** (it's a fixed, published map) so
  every Symbol glyph is covered, not just the subset above. Belt-and-suspenders: also
  trigger the remap when a char's `fontname` contains `Symbol`.
- Unmapped `U+F0xx` → drop, and log at DEBUG (so new glyphs get noticed).
- **Keep** legitimate non-ASCII: `µ ® ™ × – ' " °`. (Optional `--ascii-punct` flag, OFF
  by default, to fold smart quotes/dashes to ASCII for cleaner retrieval — do not force.)

## Problem 2 — Table legends are not captured

Legends decode a table's symbols/abbreviations and are important content. Format
(verified in RM0008): lines like `Legend:` / `Legend for Table N:` / `legend:`, sometimes
`N. Legends: ...`. They sit **below** the table and are often **multi-line** (I2C legends
wrap). The current `notes` logic misses them (they aren't numbered footnotes, so
`notes_below` stops before reaching them).

Fix — add a `legend` field to `table_content` (`list[str]`, default `[]`) via a
`legends.py` scan (mirrors `notes.py`):
- Match `^\s*legends?\b.*?[:\-]\s*(.*)$` (case-insensitive); join wrapped continuation
  lines until a blank line / next caption / heading / page footer / next legend.
- Association:
  - `Legend[s]?\s+for\s+Table\s*(\d+)` → attach to THAT `table_number` explicitly
    (position-independent).
  - bare `Legend:` → attach to the nearest table whose bottom is above the legend line on
    the same page (same "below-the-table" rule as notes).
  - If a captured footnote begins with `Legend`/`Legends`, also mirror it into `legend`.
- Apply `fix_symbols` to legend text (e.g. Table 1's legend contains `•`).
- Keep `notes` (numbered footnotes) separate from `legend`.

## Integration

- `fix_symbols` in `cells.py`, applied in the char-join used everywhere (so captions,
  headers, rows, notes, legends, section titles, and derived text/tags are all clean).
- `legends.py` runs per page during extraction (needs table bboxes + page lines);
  `exporter.py` writes `table_content.legend`.

## Validation

- **Zero** codepoints in `U+F000..U+F0FF` anywhere in `output.json` (assert 0).
- Symbol chars render: bullets `•`, arrows `→`, and `≤` in the ADC VREF rows (Table ~65).
- Legends captured (spot-checks): Table 1 → `Legend for Table 1: ... marked with "•"`;
  the I2C transfer tables (~pages 756–764) → `Legend: S = Start, Sr = Repeated Start,
  P = Stop, A = Acknowledge, NA = Non-acknowledge, ...`; USART legend (~p798).
- `legend` is `[]` for tables without one; multi-line legends are joined into one string.
- All existing invariants unchanged: `rag_selective` schema + `section/text/tags`,
  notes intact, merged cells filled (no nulls), ~correct table count, full run no OOM.
- Add tests: `fix_symbols` (`F0B7→•`, `F0A3→≤`, drop unmapped); legend capture
  (below-table association, `for Table N` association, multi-line join, symbol remap).
