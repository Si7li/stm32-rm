# Task — rotated text: separate side-by-side runs instead of interleaving them

`cells.py::cell_text` reads rotated (90°) text by sorting **every** rotated char in the cell
by descending `top` and concatenating. That is correct for one rotated run. When a cell
contains **two side-by-side rotated runs**, it interleaves them character by character.

`CELL_TEXT_ASSEMBLY_FIX.md` deliberately left this path untouched, so it is still open.

## Evidence

**1,179 cells across 67 tables** are interleaved. Signature: a bracket containing both
letters and digits (a genuine bit range holds only digits and a colon).

```
RM0490 T168  'THRE[E1_:E0]RR_RX'   should be  'THREE_ERR_RX' + '[1:0]'
RM0490 T168  'U[T1:Y0]PE'          should be  'UTYPE'        + '[1:0]'
RM0522 T592  'NUM[E2X:T0I]NSEL'    should be  'NUMEXTINSEL'  + '[2:0]'
RM0486 T77   'IC[111:0S]EL'        should be  'ICSEL'        + '[11:0]'
RM0486 T891  'N[S1:N0I]D'          should be  'NSID'         + '[1:0]'
```

| Manual | cells | tables |
|---|---|---|
| RM0486 | 815 | 40 |
| RM0522 | 281 | 24 |
| RM0490 | 83 | 3 |

### The same bug produces two other symptoms

**Collapsed headers.** RM0490 T100 and RM0486 T585 emit
`(ALAROM SoEutL[p1u:t 0]enable)`. Probed: that cell holds **30 rotated chars in exactly two
x0 clusters (269.8 and 280.8)** — `OSEL[1:0]` and `(ALARM Output enable)` side by side.
RM0486 T585's mangled header row is this, not a separate defect.

**Corrupted register bit headers → wrong bit coverage.** RM0486 T902 (DBGMCU register map,
page 4657) emits headers

```
[..., '26', '25', '2254', '23', '22', '21', '20', '2109', '18', ..., '15', '1154', '13', ...]
```

where columns `24`, `19` and `14` should be. That cell (bbox x 240.7–254.7) holds four
rotated chars from **two** columns:

```
'2' x0=235.84 top=661.96      '2' x0=247.24 top=661.96
'5' x0=235.84 top=656.98      '4' x0=247.24 top=656.98
```

x0=235.84 is the neighbouring bit-25 column. Membership is decided by the char's **center**,
and a rotated glyph's bbox is wide enough that the neighbour's center falls inside this cell.
Sorting all four by `-top` yields `2254`.

Those three corrupted headers are the direct cause of T902's six registers with wrong bit
coverage (`DBGMCU_CR` dup 12, `DBGMCU_APB2FZR` dup 16, `DBGMCU_APB1LFZR` missing 14–17).
**Fixing the header fixes the register data.**

## Fix

In `cell_text`, replace the single global sort of rotated chars with per-run handling:

```python
def _rotated_lines(rotated, bbox, tol=2.0):
    """Rotated text reads bottom-to-top within one run. All chars of a run share
    an x0; distinct x0 clusters are separate side-by-side runs, which must NOT be
    interleaved. Emitted left-to-right, each run as its own line."""
    if not rotated:
        return []
    xs = sorted({round(c["x0"], 1) for c in rotated})
    clusters = []
    for x in xs:
        if clusters and x - clusters[-1][-1] <= tol:
            clusters[-1].append(x)
        else:
            clusters.append([x])
    groups = [{"x": min(cl), "chars": [c for c in rotated if round(c["x0"], 1) in set(cl)]}
              for cl in clusters]

    # A run anchored outside the cell belongs to the neighbouring column: the
    # center-point membership above admits it because a rotated glyph's bbox is
    # wider than the ruled column (RM0486 T902's '2254'). Drop it ONLY when a run
    # genuinely inside exists -- a lone run whose x0 sits just outside the ruled
    # edge is this cell's own text and must be kept.
    inside = [g for g in groups if bbox[0] <= g["x"] <= bbox[2]]
    if inside and len(inside) < len(groups):
        groups = inside

    return ["".join(_char_text(c["text"], c.get("fontname", ""))
                    for c in sorted(g["chars"], key=lambda c: -c["top"]))
            for g in sorted(groups, key=lambda g: g["x"])]
```

Then `parts.extend(_rotated_lines(rotated, bbox))` in place of the current single append.

Both halves are load-bearing and were arrived at by elimination:

- **Anchor-based membership alone regresses**: it turned `ASYNCWAIT` into
  `ASEYXNTCMOWADIT` and dropped `EXTMOD` entirely on RM0486 p1283.
- **Discarding the non-nearest cluster alone loses data**: it turned
  `THRE[E1_:E0]RR_RX` into `[1:0]`.

The version above does neither: it keeps every run, orders them, and drops a run only when
it is anchored outside the cell *and* another run is inside.

Everything else in `cell_text` — center-point membership, the upright line clustering and
gap-space insertion from `CELL_TEXT_ASSEMBLY_FIX.md`, `fix_symbols` — is unchanged.

## Measured blast radius

Prototyped against the real PDFs over a random page sample:

| Manual | cells sampled | changed |
|---|---|---|
| RM0490 | 6,790 | 12 (0.18%) |
| RM0486 | 6,490 | 6 (0.09%) |

Every observed change is a repair, none loses content:

```
'THRE[E1_:E0]RR_RX'   -> 'THREE_ERR_RX\n[1:0]'
'MCAWINS[D[B1:1:0]0]' -> 'CAS[1:0]\nNB\nMWID[1:0]'
'WSAYRNNCC'           -> 'SYNC\nWARNC'
'CHI[N3S:T0]ATUS'     -> 'CHINSTATUS\n[3:0]'
T902 header           -> ['31','30',...,'25','24','23',...,'1','0']   (clean)
```

## Validation

1. RM0486 T902's header is exactly `Offset, Register name, 31, 30, … 1, 0` — no `2254`,
   `2109` or `1154`.
2. T902's six registers cover bits 31..0 with no duplicates. Use the correct key,
   `semantic.registers[].name` — **not** `register`, which does not exist and makes the
   check pass vacuously.
3. Zero cells contain a bracket holding both a letter and a digit
   (`\[[^\]]*[A-Za-z][^\]]*\]` with a digit inside) — was 1,179 cells / 67 tables.
4. RM0486 T585 and RM0490 T100 headers read `OSEL[1:0]` and `(ALARM Output enable)` as
   separate lines, not interleaved.
5. RM0486 T704's `510` header resolves.
6. Rotated un-reversal still works: RM0490 T26's register-map headers read `31..0`, and no
   cell anywhere contains `.seR` or a reversed field name.
7. `columns == table_content.headers`; table counts 178 / 598 / 902; no null cells;
   `--validate` missing/extra sets unchanged.
8. Report tables whose `table_content` changed, per manual. Expect roughly 67 plus a few.
   Far more means the cluster tolerance is splitting single runs — inspect before accepting.
9. Per-table split files match; combined-vs-split deep-equality passes.

## Do NOT

- Do not change rotated **membership** (keep center-point). The comment in `cell_text`
  documents why: full containment silently drops rotated glyphs that overflow a narrow
  ruled column.
- Do not change the upright path.
- Do not touch parsing, merged-cell fill, `fix_symbols`, caption detection, the figure
  boundary/cut logic, classification, `text_helper` templating, or the Sidekick shape.

## Tests

- Two rotated runs side by side → two lines, left-to-right, not interleaved.
- A single rotated run whose x0 sits just outside the ruled edge → kept (the `EXTMOD` case).
- A foreign run outside the cell plus a genuine run inside → foreign dropped (T902).
- Three runs in one cell → three lines in x order (the `MCAWINS[D[B1:1:0]0]` case).
- A normal single-run rotated cell → byte-identical to today (golden test).
- `Res.` un-reversal unchanged.
