# Project context

Working notes carried over from the Claude Code sessions that built this repo
(Jul–Aug 2026). Each one records a **root cause or a measured result**, not a
summary — the kind of thing that is expensive to rediscover because finding it
originally took a day of digging through PDFs.

Read these before changing extraction behaviour.

| note | what it settles |
|---|---|
| [stm-table-extractor-context](stm-table-extractor-context.md) | Why the project exists, ST Sidekick as the target, and the deliberate no-LLM decision. |
| [stm-figure-bleed-frontier](stm-figure-bleed-frontier.md) | Why figure content bleeds into tables — the root cause is caption assignment, not the lattice detector. Also the standing no-data-loss constraint. |
| [stm-content-extractor](stm-content-extractor.md) | The `rmcontent` sibling, and the front-matter-as-ground-truth technique both extractors depend on. |
| [st-selector-api](st-selector-api.md) | The grid endpoint, the three level-id families, and why sub-family pages cannot be scraped naively. |
| [st-export-rendering-rules](st-export-rendering-rules.md) | How ST's Export-to-Excel reshapes API values. Required before any diff is believable. |
| [stproducts-datasheet-first](stproducts-datasheet-first.md) | The inversion to datasheet-as-truth, the provenance contract, and the PDF extraction traps. |

## Session transcript

[`claude-session-transcript.md`](claude-session-transcript.md) is the full
conversation that produced the above (541 messages, 2026-07-28 → 2026-08-11).
It is the reasoning chain: which alternatives were considered and *why they
were rejected*. The notes record conclusions; the transcript records the
arguments.

## A caution about the numbers

Figures quoted in these notes were true when measured and may drift as the code
changes. Where a note and the code disagree, re-measure — do not assume either
is right. The habit that has caught the most bugs in this project is
re-deriving a claim from the source PDF rather than trusting a generated
report, since the report comes from the same code that might be wrong.
