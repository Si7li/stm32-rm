"""Per-section JSON output, next to the combined `-o` document.

Called once, after the combined document is fully assembled, with that
exact in-memory object -- there is no separate build path, so the two
outputs cannot drift apart. Nothing here alters `doc`; every value
written into a per-section file is the same object already present in the
combined document.

**Filenames zero-pad each component of the section number to three:**
`4.7.1` -> `RM0490_Rev6_section_004_007_001.json`. A sequence index would
be smaller and prettier and is wrong for the same reason a caption slug
was wrong for tables: insert one section in Rev 7 and every file after it
renumbers, so a diff between revisions becomes noise. Zero-padded
components are stable across revisions *and* sort naturally, which raw
`4.7.1` does not. The readable number lives in `_index.json`.

Atomic writes, stale pruning, and the `{RM}_{Rev}` stem all come from the
sibling project (`rmtables.split._write_json_atomic`,
`rmtables.exporter.doc_stem`) so the two datasets land in matching,
consistently-named folders.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path

from rmtables.exporter import doc_stem
from rmtables.split import _sanitize_component, _write_json_atomic

from .exporter import section_sort_key

logger = logging.getLogger("rmcontent.split")

# Width each component of the section number is padded to. Three digits
# covers every ST manual seen (RM0486's deepest chapter has 41 sections);
# a component that somehow exceeds it is written in full rather than
# truncated, since truncating would collide two different sections.
COMPONENT_WIDTH = 3


def section_filename(stem: str, number: str) -> str:
    """`("RM0490_Rev6", "4.7.1")` -> `"RM0490_Rev6_section_004_007_001"`."""
    parts = []
    for part in number.split("."):
        parts.append(f"{int(part):0{COMPONENT_WIDTH}d}" if part.isdigit() else part)
    return _sanitize_component(f"{stem}_section_" + "_".join(parts))


def write_split_sections(
    doc: dict,
    sections_dir: str | Path,
    *,
    pdf_path: str | None = None,
    prune: bool = True,
) -> Path:
    """Write one self-contained JSON file per record in `doc["sections"]`,
    plus an `_index.json` manifest, into `<sections_dir>/{RM}_{Rev}/`.

    Each file carries the SAME envelope as the combined document with
    `sections` holding exactly one record, so a single Sidekick Root Tag
    Path works whether the operator uploads the combined file or one
    section.
    """
    stem = doc_stem(doc.get("document"), doc.get("rev"), pdf_path)
    manual_dir = Path(sections_dir) / stem
    manual_dir.mkdir(parents=True, exist_ok=True)

    envelope_fields = {k: v for k, v in doc.items() if k != "sections"}
    records = sorted(doc.get("sections") or [], key=lambda r: section_sort_key(r["section"]))

    index_entries = []
    written_names = {"_index.json"}
    used: set[str] = set()
    for record in records:
        base = section_filename(stem, record["section"])
        if base in used:
            # Two records with the same section number should be
            # impossible (headings are unique); log loudly rather than
            # let one silently overwrite the other.
            logger.warning("duplicate section number %r; disambiguating filename", record["section"])
            i = 2
            while f"{base}_{i}" in used:
                i += 1
            base = f"{base}_{i}"
        used.add(base)
        filename = base + ".json"

        _write_json_atomic(manual_dir / filename, {**envelope_fields, "sections": [record]})
        written_names.add(filename)

        index_entries.append({
            "file": filename,
            "section_id": record.get("section_id"),
            "section": record.get("section"),
            "section_title": record.get("section_title"),
            "chapter": record.get("chapter"),
            "chapter_title": record.get("chapter_title"),
            "level": record.get("level"),
            "page": record.get("page"),
            "page_end": record.get("page_end"),
            "semantic_type": record.get("semantic_type"),
            "chars": record.get("chars"),
        })

    index = {
        **envelope_fields,
        "section_count": len(records),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "sections": index_entries,
    }
    _write_json_atomic(manual_dir / "_index.json", index)

    if prune:
        for existing in manual_dir.glob("*.json"):
            if existing.name not in written_names:
                logger.info("pruning stale per-section file %s", existing)
                existing.unlink()

    logger.info("wrote %d per-section files to %s", len(records), manual_dir)
    return manual_dir
