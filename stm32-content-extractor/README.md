# stm32-content-extractor (`rmcontent`)

Deterministic **section** extractor for ST STM32 reference manuals — the sibling of
`stm32-table-extractor` (`rmtables`). Where that project extracts the manual's *tables*, this
one extracts its *sections*: the prose body of every numbered section, plus a typed
`semantic` block for register descriptions.

**pdfplumber only. No LLM, no network, no API keys.** Every field is derived from the PDF
itself; two runs over the same file produce byte-identical output.

```
pip install -e ../stm32-table-extractor    # rmtables first -- it is a path dependency
pip install -e . --no-deps

rmcontent usermanuel/rm0490-....pdf -o out/ --split-sections --validate
```

## It depends on `rmtables`; it does not fork it

About 70% of what this needs already exists in the sibling project, verified against three
manuals. It is imported, never copied, and `rmtables` is not modified:

| module | what is reused |
|---|---|
| `metadata.py` | `derive_metadata`, `OVERRIDE_FIELDS` — document/rev/family/core/references/url_pdf |
| `headings.py` | `HEADING_RE` via `parse_heading`, ToC-line rejection, `CONTENTS_PAGE_HEADER_RE`, `BARE_PAREN_RE`, `extract_register_name`, bbox exclusion |
| `cells.py` | `fix_symbols` — section prose carries the same Symbol-font PUA bullets and arrows |
| `captions.py` | `FIGURE_CAPTION_RE`, `find_captions`, `assign_caption`, `CONTINUED_RE` |
| `tags.py` | `build_tags` → `features` |
| `split.py` | `_write_json_atomic`, `_sanitize_component` |
| `exporter.py` | `doc_stem` — the shared `{RM}_{Rev}` naming stem |
| `extract.py` | `TABLE_SETTINGS`, `extract_page_tables`, `flush_page` |
| `notes.py` | `FOOTER_RE` — page-footer detection |

Using the sibling's `TABLE_SETTINGS` and `assign_caption` is what makes the two datasets
agree on what a table is and which caption belongs to it, so `table_number` is a real join
key between them.

## What it produces

One record per section, flat, in a `{"sections": [...]}` envelope:

```json
{
  "section_id": "RM0490-S4.7.1",
  "document": "RM0490", "rev": "Rev 6",
  "chapter": "4", "chapter_title": "Embedded flash memory (FLASH)",
  "section": "4.7.1",
  "section_title": "FLASH access control register (FLASH_ACR)",
  "level": 3, "parent_section": "4.7",
  "page": 77, "page_end": 78,
  "semantic_type": "register_description",
  "features": ["cache", "dbg-swen", "flash", "flash-acr", "latency"],
  "chars": 1643,
  "url": "https://www.st.com/.../rm0490-....pdf#page=77",
  "url_pdf": "https://www.st.com/.../rm0490-....pdf",
  "text_helper": "Section 4.7.1 \"FLASH access control register (FLASH_ACR)\" in chapter 4 (Embedded flash memory (FLASH)), RM0490 Rev 6, page 77.",
  "section_content": "Address offset: 0x000\nReset value: ...",
  "semantic": { "register": "FLASH_ACR", "address_offset": "0x000", "...": "..." }
}
```

`document`, `rev` and `url_pdf` are repeated on every record because Sidekick's
`rootTagPath: sections` means the processor never sees the envelope.

### Sections are not chunked

RM0490's median section is 937 characters, p90 4,158; only 23 of 897 exceed 8,000 (~2k
tokens). One record per section mirrors one record per table and the "rows not chunks"
preference. `chars` is on every record and the oversized ones are printed at the end of every
run, with or without `--validate`. There is no `--max-chars`.

The 32 sections whose body is entirely a table or a figure are emitted anyway —
completeness is provable that way, and the inline markers mean they are rarely truly empty.

## Section boundaries

A section runs from its heading to **the next heading of any level**, so a parent keeps only
its own preamble and never repeats a child.

`HEADING_RE` requires at least one dot, so it matches depths 2–3 only and is blind to level-1
chapter headings (`4 Embedded flash memory (FLASH)`). Those are recognized separately and
**emitted as records of their own**, with `section` = the chapter number (`"5"`), `level` 1
and `parent_section` null. Without them a chapter that has no `N.M` subsections produced
nothing at all and its whole body was silently dropped — RM0486 chapter 5 (`OTP mapping
(OTP)`, 2,342 chars) and RM0490 chapter 21 (`Infrared interface (IRTIM)`, 1,581 chars).

A chapter record introduces no duplication: it runs to the next heading of any level, so a
chapter *with* subsections keeps only the text before its first one, exactly as `2.2` keeps
only its preamble.

Detecting a level-1 heading is the risky part — `5 Some numbered item` fits the shape — so
three independent guards apply, all resolved against the Contents: the number must be a
chapter it lists, chapters must be encountered once each in ascending order, and the title
must match. A false chapter record is worse than a missing one, because it would both invent
a record and truncate the section it interrupted; every rejected candidate is logged and
counted. The title is then taken from the Contents verbatim, which is what completes a
heading ST wrapped across two lines (`7 Resource isolation slave unit for address space` /
`protection (full version) (RISAF)`) instead of letting the remainder open the chapter's body.

Titles are compared on a 30-character prefix of their letters and digits only. The prefix
handles ST's wrapping; dropping punctuation handles a superscript, which pdfplumber lifts
onto a line of its own and leaves a gap behind — RM0486 chapter 75 is `USB Type-C®/USB Power
Delivery interface (UCPD)` in the Contents but `75 USB Type-C /USB Power Delivery interface
(UCPD)` in the body, differing at the tenth character. A minimum comparison length keeps a
short fragment like `5 OTP` from matching `OTP mapping (OTP)` on three characters.

## Subscripts are merged back into their line (`lines.py`)

Subscripts sit below the baseline, so at pdfplumber's default tolerance every signal name
breaks apart. RM0486 §13.4 read:

```
• V : optional external power supply for backup domain when V is not present
BAT DD
```

`V_BAT` and `V_DD` became a bare `V` plus an orphan `BAT DD`. Measured: **717** lone-subscript
lines across 332 sections in RM0486, 304 across 60 in RM0522, 229 across 47 in RM0490 —
`V_CORE` 269×, `t_SAMPLING` 132×, `V_BAT` 61×.

Raising `y_tolerance` to **5** merges each subscript into its baseline line and orders the
result by x. The subscript offset is ~3.7 pt against ~12 pt body line spacing, so one
tolerance separates them with room to spare — and 5 and 7 give byte-identical output, so it
is a plateau, not a knife-edge. Overridable with `--y-tolerance`, and the value is logged.

**Every caller goes through `page_lines`.** Heading tracking, caption detection, note capture,
the figure band and section assembly all key off positions in the same list, so a mismatch
between them would misalign those positions. That is why the parameter lives in one function
rather than at each call site.

Result across the three manuals: lone-subscript lines **717 / 304 / 229 → 0**, with the
section-number set, `chapter_title` values, marker counts, `register_description` counts and
total `semantic.fields` all unchanged. The merge is provably lossless — over 474 sampled
pages the character multiset is preserved exactly, asserted page-by-page in the tests.

Three things improved as a side effect, all verified rather than assumed:

- **Register field descriptions read correctly.** `Monitored V level above high threshold
  DDCORE DDCORE DDCORE` became `Monitored VDDCORE level above high threshold`; 102 RM0486
  `semantic` blocks are repaired this way.
- **Four headings were repaired**, e.g. `SYSCFG V compensation cell control register` →
  `SYSCFG VDDIO4 compensation cell control register (SYSCFG_VDDIO4CCCR)`, and RM0522's
  `COMPregisters` → `COMP registers`. No title lost a character.
- **More figure bands close.** Captions carry subscripts too, so band closures rose 822 → 835
  on RM0486 and 583 → 590 on RM0522, removing a further 14k and 9k characters of artwork.

One cost, found and fixed: merging a *superscript* perturbs pdfplumber's word splitting on
that line, so RM0486 §64.16.20 and RM0522 §42.16.21 — both `I3C_TIMINGR0`, whose text
mentions I²C — render their first field head as `Bits 3 1 :24`. The register grammar now
absorbs a split bit number, exactly as `rmtables.captions.NUMBER_RE` absorbs a split table
number (`Table 7 6.`). Without it those two registers each lost a field.

## Noise filtering (`noise.py`)

Four classes, each counted and reported:

| class | RM0490 Rev 6 |
|---|---|
| page headers/footers | 2,451 lines |
| bit-layout diagram rows (`^(\d{1,2}\s+){7,}\d{1,2}$`) | 746 lines |
| Contents / List-of pages, skipped wholesale | 39 pages / 1,613 lines |
| stray 1–2 character glyphs with no digit | 330 lines |

The bit-layout rows need their own filter rather than falling out of bbox exclusion: on page
77 the `31 30 ... 16` and `15 14 ... 0` rows are printed *above* the ruled grid (tops 200.6
and 247.7 against bboxes starting at 210.5 and 257.5), so no bbox contains them. The
threshold is five numbers rather than eight because a shorter run of the same shape occurs
inside figures — a bit-position ruler, timing indices, memory-bank labels. Those three lines
are the complete set the loosening removes across both manuals, measured rather than assumed.

Header/footer detection is positional *and* textual — a body line citing "RM0490 Rev 6"
mid-page is prose. Everything at or below the matched footer goes too, which is what removes
ST's bare marginal chapter-tab number without a rule of its own.

A footer can escape the bottom band entirely: on a **landscape** figure page ST prints it
along the side, so its `top` lands mid-page. Those are caught anywhere on the page, but only
when the line is exactly `<n>/<total>` and `<total>` equals the manual's own page count.
That denominator is the whole safeguard — RM0486 prints `16/32-bit`, `18/24-bit mode
(RGB888)`, `64/26 = 2.5x = 1.25x * 2, ...` and, inside a bit-timing figure, the bare lines
`6/16` and `7/16 7/16`, every one of which a plain `NNNN/NNNN` match would destroy.

### Rotated running heads

On the same landscape pages ST rotates the running head 90°, and pdfplumber returns it as
word-fragments — RM0490 §2.1 carried standalone `Memory`, `and`, `bus`, `architecture`.
Neither the header rule (they are not the page's topmost line) nor the footer band (they run
down the side) reaches them, and at 5.5–6.1 pt they sit above the artwork size floor.

The `upright` flag on the char objects separates them absolutely. On RM0486 p160 every
running-head fragment is `upright={False: n}` while the figure caption on the same page is
`upright={True: 44}`; on the portrait page before it, every line is upright. Running prose in
an ST manual is never rotated, and the two places rotated text *is* legitimate — a register
map's `Res.` names and figure artwork — are already excluded by table-bbox exclusion and the
artwork band, so the check runs after the former and before the latter.

The test is a **majority**, not "any", so a single rotated glyph inside an otherwise upright
line cannot discard it. Dropped: 82 fragments on RM0486, 17 on RM0522, 0 on RM0490 — where
they had already been absorbed by the artwork band. Only **18 sections across the three
manuals** change, every change removal-only, every other section byte-identical.

One straggler was upright and needed a different fix: RM0490 p45 prints its running head as
just `RM0490` with no chapter title beside it, and the header pattern required text on one
side. It now also matches the bare document number — tested only against the page's topmost
line inside the header band, so a body line that merely says `RM0490` is untouched.

## Tables and figures become markers (`markers.py`)

Table text is 14.5% of in-section characters. Inlining it would duplicate the sibling
project; dropping it silently would break sentences like *"as shown in Table 26"*. Each
detected region is replaced, in reading order, by one line:

```
[Table 26. FLASH register map and reset values]
[Figure 21. DMA block diagram]
```

**A marker replaces the region *and* its furniture.** The bbox covers only the ruled grid,
but ST prints the caption above it and any footnotes below it, both outside the bbox. Left
alone the caption lands immediately before a marker restating it word for word, and the
footnotes immediately after — content the sibling project's `notes` field already holds.
`region_markers` returns those line positions alongside the markers so the caller drops
exactly them.

Both are identified by **position**, never by shape, using the same `assign_caption` and
`notes_below` calls the table extractor makes. That distinction is load-bearing: RM0486
10.3.1 prints the prose cross-reference `Table33 summarizes the features supported by each
internal SRAM.` three lines above the real caption `Table 33. Internal SRAM features`, and
the two differ only by position and a missing space. Likewise a section's own numbered list
is never below a table bbox, so `notes_below` never reports it.

When a grid fills its page, ST pushes the notes to the top of the next one, where there is no
region for `notes_below` to work from (RM0490 Table 104: grid ends at bbox bottom 710.5 on
page 660, notes printed at tops 97.1 and 110.8 on page 661). That run is picked up only when
the previous page ended with a captioned region and *nothing* survived below it, and even
then the decision stays with `notes_below`, which returns nothing unless the very first line
of the page is a numbered note. Measured on RM0490: fires on 3 page boundaries, all 3 real
continuations, no prose list among them.

Figure footnotes are **not** suppressed. A figure has no ruled bbox, so `notes_below` has
nothing to work from, and — unlike a table's — they are not captured anywhere in the sibling
project either. Dropping them would be silent loss rather than de-duplication, so they stay
in the prose: 25 lines on RM0486, 5 on RM0490.

**One marker per logical table, not per detected grid.** ST splits a long table across pages
and each page's grid is detected separately, so one table emitted its marker twice — 543
redundant markers across 248 sections on RM0486, where §4.3.2 held nothing but a
cross-reference and the same marker twice (p202's `Table 9. BSEC internal input/output
signals`, p203's `… (continued)`).

The merge rule is `rmtables.merge.TableMerger`'s, unchanged: the same `table_number` on the
same page as, or the page after, the previous segment — no matching headers required, and
`(continued)` treated as corroborating evidence only. Advancing the end page on every
continuation carries a three-page table. Matching is on the parsed **number and page**, never
on the marker text, since a caption can render differently on the continuation page; and only
markers are considered, so a prose cross-reference is never a candidate. The tracker resets at
each section, so a table whose continuation lands in the next section is still marked there.

RM0486 drops 1,490 → 947 markers and RM0490 263 → 190, with the *set* of table numbers
identical before and after — every table still appears at least once, and no non-marker line
changes anywhere.

Multi-page **figures** show almost none of this pattern: one consecutive repeat in RM0486
(§30.5.4, Figure 239) and none in RM0490, against 1,063 and 320 figure markers. Left alone.

**Uncaptioned regions emit no marker.** Every register description prints its 32-bit layout
as two ruled half-grids with no caption — 1,089 regions on RM0490. They carry no number,
nothing cross-references them, and their content is restated field by field directly below. A
marker for each would be `[Table . ]` noise on 40% of all sections. Their lines are still
excluded from the prose, and the suppressed count is reported by `--validate`.

### Figure artwork is text outside the body column

A figure's *internal label text* has nothing to stop it reaching the prose, and **no bbox can
be built for it**: RM0486 page 159 reports zero grids from `find_tables`, 0 images, 3 curves
and 4 rects, yet carries 1,401 characters of artwork. The drawing lives in a form XObject
pdfplumber does not decompose, so there is no region to exclude.

**Font size cannot separate them either**, because the two overlap and do so *differently per
manual*:

| | body / caption | artwork | register prose |
|---|---|---|---|
| RM0486 p160 | 9.96 | 0.83 – 3.0 | 9.0 |
| RM0490 p43 | 9.96 | **8.0 and 6.5** | 9.0 |

Any threshold catching RM0490's 8 pt artwork also destroys 9 pt register-field prose
(`Bits 15:0 BSy: Port x set I/O y`) — the highest-value content in the corpus.

Size alone is therefore not the discriminator — **geometry combined with size** is. Body
prose is set at one size and starts at one of a handful of left margins; artwork labels are
scattered across the page at whatever x the drawing puts them. A line inside a page's figure
zone is artwork when **both** hold:

1. its median char size is below the document's body size (by more than 0.4 pt), **and**
2. its `x0` is not within 1 pt of any body left margin.

Either condition alone is wrong. Condition 1 alone destroys RM0490's 8 pt register prose;
condition 2 alone destroys every indented body line. Together they are precise: a figure
*footnote* is small but sits exactly on a margin, so it survives — measured across RM0008 and
RM0490, **every** numbered footnote sits within **0.3 pt** of a margin.

Both quantities are derived from the document, never hardcoded. Body size and the margin set
come from a whole-document pass over lines outside every table bbox (memoised per file);
a margin qualifies at a 2% share of lines. RM0008 measures 9.96 pt with margins
`{67, 124, 145, 161, 162, 163}`.

**The 1 pt tolerance is measured, not assumed.** The obvious 2 pt fails on RM0008 Figure 201,
whose artwork column sits at x0 159.3–159.8 against the manual's 161 pt indent: at 2 pt the
labels `A[25:0]`, `NEx`, `NWE` read as body flow and survive. Sub-body-size lines cluster at
0.0–0.5 pt from a margin and then gap; nothing legitimate is near 1.0. So 1 pt keeps every
footnote with better than 3× headroom while putting that column on the correct side.
Body-*size* lines are unaffected either way, since the conditions are ANDed.

Chasing ST's id conventions is what made the earliest rule fragile — the corpus turned out to
use at least two families (`MSv66119V2` and `ai15797c`, the latter with `-m` and `V3`
variants). The column rule drops both as ordinary artwork without knowing either. The pattern
survives only to remove a standalone id whose figure opened no zone, and to report the health
metric that says how many zones contained an id at all.

### Structural grammar always wins over size and margin

The size+margin rule is statistical, and the corpus defeats it in both directions: RM0522
prints a register's `31 24 15 7 0` bit header *inside* a figure at 9.94 pt, and a figure can
push its own footnote off the margin. Rather than tune the rule until no such line exists,
the highest-value content is identified by its own grammar and exempted outright. A line
matching any of these is body, whatever its typography says:

- a section heading (`rmtables.headings.HEADING_RE`)
- `^Bits?\s+\d` — a register field line
- `^(0b[01]+|0x[0-9A-Fa-f]+|\d{1,3}):\s+\S` — a value enumeration
- `^Note:` / `^Caution:`
- `^\d+\.\s+\S` **at a body margin** — a numbered or figure footnote
- a `Table N.` or `Figure N.` caption

`Bits 15:0 BSy: Port x set I/O y` is the single most valuable line shape in the corpus and
the one a size threshold has historically destroyed. It is now unreachable by any of this.

The margin condition on the footnote rule is what keeps it honest: a drawing's interior `1.`
callouts are scattered across the figure, while ST prints real footnotes at a body margin.

### Reading order: `extract_text_lines()` does not provide it

It returns lines in PDF **content-stream order**. RM0490 page 290 (§16.4, Figure 30):

| idx | top | size | x0 | line |
|---|---|---|---|---|
| 3 | 138.2 | 9.96 | 226.5 | `Figure 30. ADC block diagram` |
| 4–39 | 164 → 563.8 | 6.00 | scattered | artwork, first drawing pass |
| 40 | 575.9 | 7.98 | 67.3 | `1. TRGi are mapped at product level…` |
| 41 | 744.5 | 9.00 | 67.3 | page footer |
| **42** | **235.6** | 4.00 | 501.8 | `BHA` — second pass starts again |
| 43–74 | 171.6 → 503.5 | 6.00 | scattered | `VREF+`, `CHSEL[22:0]`, `TRG0`…`TRG7` |

Tops ascend to index 41, then jump back and ascend again: ST drew the artwork in two content
passes. **Measured on 60 random pages per manual, 12–22% of pages are affected** — RM0486 22%
(median 13 content runs, max 38), RM0008 17%, RM0522 15%, RM0490 12% (median 14, max 33).

Anything that walks the list as a *sequence* breaks on this. The band rule that preceded this
one opened at the caption, ran through the first drawing pass correctly, closed on the
figure's own footnote — and then met the entire second pass with nothing left to stop it.

So `lines.read_page_lines` sorts every page by `top` (tie-break `x0`) immediately after
extraction, before anything else looks at it, and reports whether it had to. Sorting is safe
because the anomaly is confined to small-font runs: no page was found where *body*-sized
lines are out of order.

### There is no band any more — classification is per line

Sorting alone is not enough, because a body-size line can sit inside a figure and any rule
that *ends* filtering on a body-flow line ends it there. So the state machine is gone. On a
page containing a validated figure caption, every line below the first such caption is
classified on its own merits: structural grammar first, then size-and-margin. A body line in
the middle of a drawing is simply kept, and the artwork after it is still dropped.

**The zone is per page, and needs no hard bound.** It cannot outlive the page that opened it,
so a mis-validated caption costs the artwork test on the remainder of one page rather than
pages of real prose. The old two-page fail-safe protected against something that can no
longer happen.

#### Caption validation is two-tier, and the tiers are not equally trusted

A wrong marker costs one line; a wrong *zone* deletes prose. So the two decisions are
separated, calibrated against the corpus:

- **The verb test decides whether a marker is emitted.** Measured across 1,383 markers it
  fires 3 times, all 3 genuine cross-references — RM0486 §12.4.3's `Figure 14. shows the
  functional view of…` (which previously emitted a *second*, bogus marker for Figure 14) and
  §53.3.25's two. Zero false positives; precise enough to destroy a marker with.
- **The List-of-figures tests decide only whether a zone may open.** These are weaker: they
  reject 26 RM0486 captions and 2 RM0490 ones on a title mismatch, and every one is a *real*
  caption whose body text differs from the listing only because a subscript was lifted out
  (`Device startup (V supplied…` against the listing's `V_DD`). Rejecting those would have
  deleted real markers, so they withhold only the authority to bound a deletion.

The List of figures is parsed with `rmtables.captions.FIGURE_WORD_RE`/`NUMBER_RE`, but
**without** `LIST_ENTRY_RE`'s mandatory trailing page number — that has the same wrapped-entry
defect `contents.py` documents, and requiring it left 12 RM0490 and 48 RM0486 numbers
"absent" from a list that in fact contains them.

#### The size floor is retained as a backstop

`< 0.6 × body_size` still runs **outside** bands, derived per document as the mode of its
lines' median char sizes over 120 evenly sampled pages, never hardcoded. It cannot reach 9 pt
register prose, it is proven on RM0486's 0.83–3.0 pt labels, and it catches artwork whose
caption was missed entirely — which the band rule structurally cannot reach.

Measured over the four manuals: **2,078 figure zones, 372,756 characters of artwork removed**
— 54% more than the band rule that preceded it.

| | pages reordered | zones | chars removed | zones containing an id | pages ending mid-artwork |
|---|---|---|---|---|---|
| RM0008 Rev 21 | 191 (17%) | 282 | 47,071 | 234 (**83%**) | 77 |
| RM0486 Rev 4 | 807 (17%) | 919 | 161,671 | 887 (**97%**) | 196 |
| RM0490 Rev 6 | 110 (11%) | 261 | 54,819 | 251 (**96%**) | 87 |
| RM0522 Rev 1 | 399 (15%) | 616 | 109,195 | 606 (**98%**) | 168 |

Not one asset-id line survives into `section_content` in any of the four.

**The id-coverage metric is what confirms the zone boundaries are right.** It rose from
41–58% under the band rule to 96–98% on three manuals: a zone now nearly always reaches the
id ST printed as the last element of its drawing. RM0008's 83% is not a defect but its
ceiling — it prints 306 id lines for ~367 real figures, so ~17% of its figures carry no id at
all, and 83% is exactly that bound.

**Figure footnotes are kept** — `1. The high-performance domain is shown in pink…` is 8 pt
and stays. It is readable prose explaining the figure, and it is exactly what the margin half
of the rule and the footnote grammar exist to protect.

#### Known limits, accepted and measured

- **The zone is per page.** A figure whose artwork spills onto the next page leaks there,
  because no caption opens a zone on that page: 77 / 196 / 87 / 168 pages end mid-artwork.
- **Artwork rendered at body size is unreachable.** The two conditions are ANDed, so a label
  at 9.72–9.88 pt against a 9.96 pt body is never artwork whatever its position. That is the
  whole of the residue: sections with ≥3 label-like lines after a `[Figure]` marker fall from
  93 / 54 / 28 / 15 to **53 / 32 / 7 / 8**, and every RM0008 survivor measured
  (`Analog voltage` 9.88 pt, `External` 9.72 pt) is this case.
- **A label can coincidentally sit on a margin.** RM0490 §16.4's `CHSEL[22:0]` is at x0 144.0
  against the manual's 145 pt margin — exactly 1.0 pt, at the tolerance. Tightening below
  1.0 pt is not available: the 0.4–1.0 pt band is dense with real prose
  (`This bit-field defines the direction…`, `Indicates the amount of free space`), so
  hundreds of genuine lines would be at risk to remove one label.

Each of these is a **leak, not a loss** — the failure direction the whole design chooses.

Regression-checked against the previous build on all four manuals: numbered-footnote counts,
`[Table]`/`[Figure]` marker counts, record counts, section counts, `register_description`
counts, `semantic.fields` totals and value-enumeration counts are all **identical**. The one
movement is `^Bits` lines, which *rose* by 4 / 1 / 1 — register prose the structural-grammar
rule rescued from deletion (`Bit 17 SDINIT: SDRAM device initialization`, 9.0 pt at x0 97.9,
below body size and off every margin). Content recovered, not lost.

## Register descriptions (`registers.py`)

A section is `semantic_type: "register_description"` only when it has **exactly one**
`Address offset:` line **and** at least one `Bit`/`Bits` field line. Everything else is
`"generic"` with `semantic: {}` — conservative for the same reason as the sibling's
classifier: a wrong type is worse than generic for retrieval.

The "exactly one" is what handles ST's occasional mega-section: RM0522 48.11.4 "Ethernet MAC
and MMC registers" documents about fifty registers under a single numbered heading, each with
an unnumbered sub-heading, in 167k characters. The single-register `semantic` shape cannot
represent that — it would merge every register's fields into one list whose bits overlap
several times over — so those sections stay generic and are counted in the report.

Reserved runs are included as fields named `Res.` so `fields` covers all 32 bits (the
`RESERVED_FIELDS_TASK.md` decision). That is what makes each register self-validating:
`check_bit_coverage` demands an exact partition, so a gap or overlap is a real parse bug. It
found four during development:

- **`Bits 24, 14:12 OC2M[3:0]`** — a discontiguous bitfield, appearing on every TIM
  `SMCR`/`CCMR`. Reading only the first range dropped the field entirely.
- **`Bit 23 NAK:`** with the description on the *next* line (RM0490 29.6.7) — 8 bits lost.
- **`Bit 31 of this register has two possible definitions...`** — prose that satisfies the
  field shape, producing a phantom field named `of` at bit 31.
- **`EXTI{4*(x-1)+3}[7:0]`** — a templated field name for a per-instance register.

- **`Bits 14:11`** with the whole description wrapped onto the next line (RM0486 73.14.47).

**Coverage is checked against the register's own width, not a hard-coded 32.** A fixed 32 is
simply the wrong invariant for the registers ST documents as 16-bit — 42 in RM0486, 66 in
RM0490, mostly timer control registers. Each prints a single `15 14 13 … 0` strip in the PDF
with no `31 … 16` row above it, carries `Reset value: 0x0000`, and its fields partition 15..0
exactly with the highest bit claimed anywhere being 15. Width comes from ST's own reset-value
string. The full-word result is still computed and reported as context, but it is not a
failure on its own, and each narrow register is listed with its reason.

`declared_width` is used only by the validator; nothing in the emit path calls it, so no
field is ever altered by it.

Two shapes deliberately do **not** become fields, both verified against RM0486:

- prose about bits — `Bits 18:17 are the mirror of ATOSEL4[1:0]…`, `Bit MON can be set only
  by software…`;
- a nested breakdown of a field's *value* — §73.14.17 follows `Bits 30:24 NPTXQTOP[6:0]` with
  `Bits 30:27: Channel/endpoint number`, `Bits 26:25:`, `Bit 24: Terminate`. Parsing those
  would overlap bits 30:24 and break two registers that cover 31..0 correctly today.

Requiring an identifier immediately before the colon is what keeps both out.

The two modes of a `TIMx_CCMRx` register are not a dual-description problem here: ST splits
them into separate numbered sections — §53.6.7 `(TIMx_CCMR1)` for input capture, §53.6.8
`[alternate] (TIMx_CCMR1)` for output compare — each with its own `Address offset:` and each
covering the full word. The section-boundary rule keeps them apart, so both are represented
and neither is dropped.

## Validation — the Contents pages are ground truth (`contents.py`)

The manual's own Contents is the exact analog of the List of Tables. Two defects the
List-of-Tables parser learned the hard way are handled from the start:

1. **A wrapped entry has no trailing page number on its first line.** A strict parser gets
   835 sections on RM0490; 61 more wrap so the leaders and page land on the next line.
2. **The leader run can be one dot, or none at all.** `19.4.6 TIM14 capture/compare mode
   register 1 [alternate] (TIM14_CCMR1) 537` fills its line so completely that ST prints no
   leaders. That is ambiguous against a wrapped title ending in a digit (`... mode register
   1`), so it is resolved by *position*: Contents page numbers are right-aligned in their own
   column, measured from this manual's own Contents rather than assumed. On RM0490, 946
   dot-leader entries end at x1 527.9 ± 0.3 while every wrapped title line ends at x1 ≤ 494.

A third defect showed up only on RM0486, and only because that manual is big enough to reach
it: once a section number's last component hits three digits, ST's Contents field overflows
and the space before the title disappears — `14.10.100RCC APB1H sleep enable register
(RCC_APB1HLPENR) . . . 611`. Requiring that space lost 199 of RM0486's 3,585 sections from
the ground truth, which then surfaced as "extra" in validation; the extractor had had them
all along. The separator is now optional, and without it the title must start with a letter
or bracket so the number itself can never be split.

**That tolerance is for subsections only** — a dot in the number is mandatory for it. A
chapter line requires its space, because RM0486's Contents wraps a VENC register entry onto a
line of its own, `1st DCT partition register (VENC_SWREG58) . . . 2180`. Read tolerantly that
splits into number `1` and title `st DCT partition register (VENC_SWREG58)`, which then
overwrote chapter 1's real title on 5 records; `2nd ...` did the same to chapter 2 on 10. The
`s` follows the `1` directly, so mandatory whitespace rejects it outright. A second,
independent guard requires chapters to be listed once each in ascending order, so a line
claiming chapter 1 after chapter 40 is refused whatever it looks like.

### What the Contents is used for beyond validation

- **Chapter titles**, since chapters are not records.
- **The chapter-heading test** — a `<digits> <Title>` line is a chapter only when both its
  number and its title match the Contents.
- **Recovering headings `parse_heading` rejects.** RM0490's `17.3.19 6-step PWM generation`
  starts with a digit, which `TITLE_FIDELITY_FIX.md`'s uppercase-initial guard refuses.
  Recovery requires the number *and* the full title to match ST's own listing, so it can
  never invent a heading. This is also what lets a real heading through the table-bbox
  exclusion: on RM0486, a figure's ruled box was detected as a table region covering the top
  of page 1580, swallowing `33.3.4 DTS serial data adapter (SDA)`. Inside a bbox, only a
  Contents-vouched heading is accepted, so no table row is readmitted.
- **Rejecting phantom headings from body prose.** Two independent bounds, each catching what
  the other misses: a chapter beyond the last one ST numbers (RM0522's `"61.44 MHz from the
  clock controller of the circuit. In the example above we"` in a 52-chapter manual), and a
  chapter that moves backwards through the document (RM0486's `"1.6 GBps"` on page 1029). The
  first is an upper *bound*, not exact membership — an exact test would silently delete every
  section of a chapter the Contents parse happened to lose. Both comparisons use the chapter
  component only, so ST's occasional unlisted subsection (RM0490's real 29.6.8, right after a
  listed 29.6.7) still comes through.

Recoveries and rejections are both reported.

`--validate` reports: sections listed vs extracted (`missing`/`extra`), chapters resolved,
sections with an empty `chapter_title`, headings recovered, register-description and field
counts, bit-coverage violations (split into expected-narrow and genuine), sections over 8,000
characters, empty sections, suppressed uncaptioned regions, and each noise-class count.

## Output files

- Combined: `{RM}_{Rev}.json`, envelope `{"sections": [...]}`.
- Per-section: `<sections-dir>/{RM}_{Rev}/{RM}_{Rev}_section_{NNN_NNN_NNN}.json`, each
  holding the same envelope with `sections` containing exactly one record — so one Root Tag
  Path works in both upload modes.
- `_index.json` per manual, carrying the readable `section` number, title, chapter, page,
  level, `semantic_type` and `chars`.

Each component of the section number is zero-padded to three: `4.7.1` →
`004_007_001`. A sequence index would renumber every file after an inserted section in the
next revision; raw `4.7.1` does not sort. The readable number lives in `_index.json`.

Per-section files are written from the already-assembled combined document, so the two
outputs cannot drift — a test asserts deep equality between them.

## CLI

```
rmcontent <pdf> [-o out.json|outdir] [--split-sections] [--sections-dir DIR]
                [--validate] [--no-prune] [--pages 76-80]
                [--document RM0490] [--rev "Rev 6"] [--family C0]
                [--core "..."] [--references "..."] [--url-pdf URL]
                [--package ...] [--frequency ...] [--log-level LEVEL]
```

An explicit `-o file.json` wins verbatim; `-o <directory>` or no `-o` auto-names
`{RM}_{Rev}.json`. Metadata flags override the auto-derived values.

Memory: `rmtables.extract.flush_page` runs on every page. Without it a full 1023-page run
OOMs around page 800 because pdfplumber caches each page's char and textmap objects for the
lifetime of the PDF object. With it, RM0490 peaks at 431 MiB.

## Verified on three manuals

Nothing in the code references a specific manual; these are three runs of the same binary.

| | RM0490 Rev 6 | RM0522 Rev 1 | RM0486 Rev 4 |
|---|---|---|---|
| pages | 1,023 | 2,572 | 4,669 |
| **records** | **930** | 1,826† | **3,666** |
| — numbered sections | 897 | 1,826 | 3,585 |
| — chapter records | 33 | † | 81 |
| listed in Contents | 896 | 1,825 | 3,585 |
| **missing** | **0** | **0** | **0** |
| **extra** | 1 (`29.6.8`) | 1 (`47.6.8`) | **0** |
| chapters resolved | 33/33 | 50/52† | 81/81 |
| register descriptions | 368 | 636 | 1,748 |
| fields (named / reserved) | 2,298 (1,718 / 580) | 4,474 (3,249 / 1,225) | 10,543 (7,827 / 2,716) |
| **coverage failures at own width** | **0** | 2† | **0** |
| — 16-bit registers (expected) | 66 | 24† | 42 |
| sections > 8,000 chars | 23 | 73 | 107 |
| headings recovered via Contents | 2 | 2 | 4 |
| phantom headings rejected | 0 | 1 | 9 |
| multi-register sections | 1 | 17 | 36 |
| runtime / peak RSS | 99 s / 431 MiB | 246 s / 547 MiB | 480 s / 657 MiB |

† RM0522 has not been re-run since chapter records were added; its figures are from the
previous revision of this table.

The two "extra" sections are real: ST omits `29.6.8 USB register map` and RM0522's `47.6.8`
from its own Contents. Every rejected phantom heading was verified to be prose — `6.4`,
`14.4` and `3.3` on RM0486 are also *real* sections, correctly extracted hundreds of pages
earlier at their proper places, and what was rejected was a later "3.3 V" / "6.4 Gbps"
fragment.

RM0522's two remaining bit-coverage errors are both device-electronic-signature registers
(`50.2`, `50.3`) that ST documents with `Read only = 0xXXXX where X is factory-programmed`
instead of a `Reset value:` line. They are genuinely 16-bit, but nothing in the section states
a width, so they are reported rather than silently assumed narrow.

## Tests

```
pytest                  # includes the end-to-end runs against usermanuel/rm0490-*.pdf
pytest -m "not slow"    # skips the full-manual run
```

The end-to-end tests skip cleanly when the PDFs are absent.

## Out of scope

Table *content* (the sibling owns it — this emits markers only), chunking, network access,
`stm32fetch` integration, and any LLM.

Figure *bodies* are not filtered: a vector figure's embedded labels come back from pdfplumber
as ordinary text lines, are not inside any table bbox, and are not noise by any of the four
rules, so they land in `section_content` of the section that holds the figure. Only the
caption becomes a marker. This is the same figure-bleed frontier the sibling project has open.
