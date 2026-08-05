"""Build the ST Sidekick JSON-processor document from scanned sections.

Envelope (identical shape for the combined file and every per-section
split file): `{document, rev, url_pdf, references, package, family, core,
frequency, section_count, sections}` -- `sections` is the record array
Sidekick's `rootTagPath` points at. It mirrors the sibling project's
table document exactly, with `section_count` in place of `table_count`.

Records are **flat**, and `document`/`rev`/`url_pdf` are repeated on each
one. That is not redundancy for its own sake: `rootTagPath: sections`
means the processor never sees anything outside the array, so any field a
Link URL or Label Template might reference has to be on the record
itself.

**Sections are not chunked.** RM0490's median section is 937 characters
and its p90 is 4,158; only 23 of 903 exceed 8,000 (~2k tokens). One
record per section mirrors one record per table and the "rows not chunks"
preference. `chars` is on every record and the oversized ones are logged
at the end of a run so they stay visible; there is no `--max-chars`.

The 31 sections whose body is entirely a table or a figure are emitted
anyway -- completeness is provable that way, and the §4 markers mean they
are rarely truly empty.
"""

from __future__ import annotations

import logging
import re

from rmtables.tags import build_tags

from .registers import parse_register

logger = logging.getLogger("rmcontent.exporter")

# Sections longer than this are reported at the end of a run. Nothing is
# split or truncated -- this is a visibility threshold, not a limit.
OVERSIZED_CHARS = 8000

# A bitfield name's index suffix, dropped before the name is offered to
# `build_tags` (whose REGNAME pattern is `^[A-Z][A-Z0-9_]{2,}$` and so
# never matches "LATENCY[2:0]").
_INDEX_SUFFIX_RE = re.compile(r"\[[^\]]*\]$")


def _collapse(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def _field_tag_sources(semantic: dict) -> list[str]:
    """Bitfield names from a register description, offered to
    `build_tags` as whole-value REGNAME candidates -- `DBG_SWEN` and
    `LATENCY` become tags, `Res.` does not."""
    names = []
    for f in semantic.get("fields") or []:
        name = _INDEX_SUFFIX_RE.sub("", f.get("name") or "")
        if name and name != "Res.":
            names.append(name)
    return names


def build_record(section, meta: dict, contents) -> dict:
    """One flat section record (§6)."""
    document = meta["name_datasheet"]
    rev = meta["rev"]
    url_pdf = meta["url_pdf"]
    chapter = section.chapter
    chapter_title = contents.chapter_title(chapter)
    title = _collapse(section.title)
    content = section.content

    semantic = parse_register(title, content)
    semantic_type = "register_description" if semantic else "generic"
    if semantic is None:
        semantic = {}

    features = build_tags(title, chapter_title, _field_tag_sources(semantic), [])

    chapter_phrase = f" ({chapter_title})" if chapter_title else ""
    text_helper = (
        f'Section {section.number} "{title}" in chapter {chapter}{chapter_phrase}, '
        f"{document} {rev}, page {section.page}."
    )

    return {
        "section_id": f"{document}-S{section.number}",
        "document": document,
        "rev": rev,
        "chapter": chapter,
        "chapter_title": chapter_title,
        "section": section.number,
        "section_title": title,
        "level": section.level,
        "parent_section": section.parent,
        "page": section.page,
        "page_end": section.page_end,
        "semantic_type": semantic_type,
        "features": features,
        "chars": len(content),
        "url": f"{url_pdf}#page={section.page}",
        "url_pdf": url_pdf,
        "text_helper": text_helper,
        "section_content": content,
        "semantic": semantic,
    }


def section_sort_key(number: str) -> tuple:
    """Numeric, component-wise: `4.7.1` sorts after `4.7` and before
    `4.10`, which string ordering gets wrong."""
    parts = []
    for part in number.split("."):
        try:
            parts.append(int(part))
        except ValueError:
            parts.append(0)
    return tuple(parts)


def build_document(sections: list, meta: dict, contents) -> dict:
    """Assemble the full Sidekick document."""
    records = [build_record(s, meta, contents) for s in sections]
    records.sort(key=lambda r: section_sort_key(r["section"]))
    return {
        "document": meta["name_datasheet"],
        "rev": meta["rev"],
        "url_pdf": meta["url_pdf"],
        "references": meta["references"],
        "package": meta["package"],
        "family": meta["family"],
        "core": meta["core"],
        "frequency": meta["frequency"],
        "section_count": len(records),
        "sections": records,
    }


def oversized_sections(doc: dict) -> list[dict]:
    return sorted(
        (r for r in doc["sections"] if r["chars"] > OVERSIZED_CHARS),
        key=lambda r: -r["chars"],
    )
