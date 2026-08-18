# stm content extractor

> The rmcontent sibling project: what it extracts, and the ground-truth trick both extractors depend on

`stm32-content-extractor` (package `rmcontent`) was built 2026-08-03 as a sibling to
`stm32-table-extractor`: same manuals, same ST Sidekick target, same deterministic
pdfplumber-only constraint, but it extracts **sections** (prose body + a typed `semantic`
block for register descriptions) rather than tables. It **imports** `rmtables` and must not
fork or modify it — if a shared helper genuinely needs changing, raise it with Khalil first.

The load-bearing technique, worth reusing anywhere else in this project: **the manual's own
front matter is the ground truth**, and it is used for far more than reporting. The Contents
parse supplies chapter titles, vouches for headings `parse_heading` rejects, and bounds which
chapter numbers are plausible — which is what kills phantom sections from body prose like
RM0522's "61.44 MHz from the clock controller" and RM0486's "1.6 GBps". Same role the List of
Tables plays for [stm-table-extractor-context](stm-table-extractor-context.md).

Every ST front-matter parser hits the same three rendering defects, so write them in from the
start: a wrapped entry has no trailing page number on its first line; the dot-leader run can
be one dot or none (resolve the resulting ambiguity by measuring the right-aligned
page-number column, not by guessing); and once a number grows a digit, ST's field overflows
and the separating space vanishes (`14.10.100RCC APB1H...`, mirroring `Table332.`).

The figure-bleed frontier ([stm-figure-bleed-frontier](stm-figure-bleed-frontier.md)) is open here too: vector figure
labels are not inside any table bbox and are not noise by any rule, so they land in
`section_content`.
