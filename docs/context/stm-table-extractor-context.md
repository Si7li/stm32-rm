# stm table extractor context

> Goal, constraints and deployment target of the STM32 reference-manual table extractor project

Khalil is building this for an internship/course at ST. The instructor's ask: parse every
table out of ST reference manuals (RM####) into JSON, to be ingested by **ST Sidekick**,
an ST-internal RAG tool (JSON processor, `rootTagPath = tables`, link templates like
`{{url_pdf}}#page={{page}}`). The instructor is often unreachable, so design decisions get
made locally with safe/reversible defaults rather than blocking.

Key standing decision (as of 2026-07): the instructor **allowed** using an LLM/vision model,
but the pipeline is deliberately **100% deterministic, offline, no API key** — ST tables are
fully ruled grids that pdfplumber lattice mode recovers exactly, and a vision model could
silently hallucinate bit values in register maps. "We evaluated AI and chose not to use it"
is itself part of what's being graded, and the reasoning lives in the README.

Working method: every change is written as a `*_TASK.md` / `*_FIX.md` spec in the project
root, handed to Claude Code with a kickoff prompt, then the resulting JSON is independently
verified. See [stm-figure-bleed-frontier](stm-figure-bleed-frontier.md).
