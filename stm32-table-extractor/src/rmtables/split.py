"""Per-table JSON output (SPLIT_TABLES_TASK.md, reshaped by
SIDEKICK_FORMAT_TASK.md, renamed by FILENAME_SCHEME_TASK.md) -- a purely
additional export next to the combined `-o` document.

Called once, after the combined document is fully assembled, with that
exact in-memory object -- there is no separate extraction path, so the two
outputs cannot drift apart. Nothing here alters `doc`; every value written
into a per-table file is the same object (or an unmodified copy of it)
already present in the combined document.

Every per-table file uses the SAME envelope shape as the combined file
(`{document, rev, url_pdf, ..., tables: [...]}`, `tables` holding exactly
one record) -- so Sidekick's `rootTagPath: tables` works unchanged whether
the operator uploads the combined file or a single-table one.

The per-manual folder and every per-table filename are built from
`exporter.doc_stem()` -- the SAME `{RM}_{Rev}` stem the CLI uses for the
combined file's own name, so a revision bump can never leave the two
naming schemes out of sync with each other.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path

from .exporter import doc_stem

logger = logging.getLogger("rmtables.split")

# Filesystem-safe filename characters (SPLIT_TABLES_TASK.md §3).
_FILENAME_UNSAFE_RE = re.compile(r"[^A-Za-z0-9._-]")
_SLUG_UNSAFE_RE = re.compile(r"[^a-z0-9]+")
# FILENAME_SCHEME_TASK.md: "keep total filename length under ~120 chars;
# truncate the slug, never the stem."
_MAX_FILENAME_LEN = 120
_MAX_SLUG_LEN = 40


def _sanitize_component(text: str) -> str:
    """Strips anything outside `[A-Za-z0-9._-]` -- path separators, quotes,
    everything -- from a single filename/foldername component."""
    return _FILENAME_UNSAFE_RE.sub("", text or "")


def _slugify(table_name: str) -> str:
    """Lowercased, non-alphanumerics collapsed to `-`, trimmed, truncated
    to 40 chars (SPLIT_TABLES_TASK.md §3) -- used only when
    `--filename-slug` is passed."""
    s = _SLUG_UNSAFE_RE.sub("-", (table_name or "").lower()).strip("-")
    return s[:_MAX_SLUG_LEN].rstrip("-")


def _parse_table_number(table_number) -> int | None:
    try:
        return int(table_number)
    except (TypeError, ValueError):
        return None


def _reserve_stem(base_stem: str, page, used: set[str]) -> str:
    """Returns `base_stem` if free, else disambiguates: `_p{page}` first,
    then `_2`, `_3`, ... (SPLIT_TABLES_TASK.md §3), logging every
    collision. Mutates `used`."""
    if base_stem not in used:
        used.add(base_stem)
        return base_stem
    logger.warning("filename collision for %r (page %s); disambiguating", base_stem, page)
    candidate = f"{base_stem}_p{page}"
    if candidate not in used:
        used.add(candidate)
        return candidate
    i = 2
    while True:
        candidate2 = f"{candidate}_{i}"
        if candidate2 not in used:
            used.add(candidate2)
            return candidate2
        i += 1


def _table_sort_key(table: dict) -> tuple:
    n = _parse_table_number(table.get("table_number"))
    if n is not None:
        return (0, n)
    return (1, table.get("page") or 0)


def _build_filenames(stem: str, tables: list[dict], filename_slug: bool) -> list[str]:
    """One filename (without `.json`) per table in `tables`, same order,
    unique within the manual -- `{stem}_table_{NNN}[_{slug}]`. Only the
    optional slug is ever truncated to respect `_MAX_FILENAME_LEN`; the
    `{stem}_table_{NNN}` part (and its `_p{page}`/`_2` collision
    disambiguation) is never cut short (FILENAME_SCHEME_TASK.md)."""
    numbers = [_parse_table_number(t.get("table_number")) for t in tables]
    width = 4 if any(n is not None and n >= 1000 for n in numbers) else 3

    used: set[str] = set()
    names = []
    for table, n in zip(tables, numbers):
        page = table.get("page")
        if n is None:
            base = f"{stem}_table_unnumbered_p{page}"
        else:
            base = f"{stem}_table_{n:0{width}d}"
        base = _reserve_stem(base, page, used)

        name = base
        if filename_slug:
            slug = _slugify(table.get("title"))
            if slug:
                # Reserve room for ".json" (5 chars) and the "_" joiner;
                # truncate the SLUG to fit, never the base.
                budget = _MAX_FILENAME_LEN - 5 - len(base) - 1
                slug = slug[:budget].rstrip("-") if budget > 0 else ""
                if slug:
                    name = f"{base}_{slug}"

        names.append(_sanitize_component(name))
    return names


def _write_json_atomic(path: Path, obj) -> None:
    tmp = Path(str(path) + ".tmp")
    tmp.write_text(json.dumps(obj, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def write_figure_fragments(
    document: str,
    rev: str,
    fragments: list[dict],
    tables_dir: str | Path,
    *,
    pdf_path: str | None = None,
) -> Path:
    """FIGURE_CAPTION_BOUNDARY_FIX.md: writes every grid `classify_page`
    rejected (a ruled region correctly detected but wrongly adjacent to
    another table's caption, per the Figure-caption-boundary check) to
    `<tables_dir>/{stem}/_figure_fragments.json`.

    Deliberately OUTSIDE the Sidekick `{"tables": [...]}` envelope and
    every per-table file -- never uploaded, zero schema risk -- but every
    removed row stays recoverable and every rejection auditable. Uses the
    SAME `doc_stem()` the combined file/per-table folder use, so it always
    lands in the identical per-manual folder regardless of whether
    `--split-tables` also ran this document through `write_split_tables`.
    Called only when `fragments` is non-empty -- nothing to write, nothing
    written, no empty file left behind.
    """
    stem = doc_stem(document, rev, pdf_path)
    manual_dir = Path(tables_dir) / stem
    manual_dir.mkdir(parents=True, exist_ok=True)
    payload = {"document": document, "rev": rev, "fragments": fragments}
    _write_json_atomic(manual_dir / "_figure_fragments.json", payload)
    return manual_dir


def write_split_tables(
    doc: dict,
    tables_dir: str | Path,
    *,
    pdf_path: str | None = None,
    filename_slug: bool = False,
    prune: bool = True,
) -> Path:
    """Writes one self-contained JSON file per record in `doc["tables"]`,
    plus a `_index.json` manifest, into `<tables_dir>/{RM}_{Rev}/`. `doc` is
    the already-fully-assembled combined document (`exporter.build_document`'s
    return value) -- read-only here, never mutated. Returns the manual's
    output directory.

    The folder (and every per-table filename inside it) is keyed by
    `exporter.doc_stem()`, which folds in the revision -- so re-running
    against a NEW revision of the same manual writes to a sibling folder
    rather than overwriting the previous one; pruning below stays scoped to
    exactly that folder and never touches another revision's files
    (FILENAME_SCHEME_TASK.md).
    """
    stem = doc_stem(doc.get("document"), doc.get("rev"), pdf_path)
    manual_dir = Path(tables_dir) / stem
    manual_dir.mkdir(parents=True, exist_ok=True)

    # Everything except the record array -- repeated at the top of every
    # per-table file for human readability (SIDEKICK_FORMAT_TASK.md §1);
    # the record itself is already self-sufficient regardless.
    envelope_fields = {k: v for k, v in doc.items() if k != "tables"}
    records = sorted(doc.get("tables") or [], key=_table_sort_key)
    filenames = [name + ".json" for name in _build_filenames(stem, records, filename_slug)]

    index_entries = []
    written_names = {"_index.json"}
    for record, filename in zip(records, filenames):
        per_table = {**envelope_fields, "tables": [record]}
        _write_json_atomic(manual_dir / filename, per_table)
        written_names.add(filename)

        tc = record["table_content"]
        index_entries.append({
            "file": filename,
            "table_id": record.get("table_id"),
            "table_number": record.get("table_number"),
            "title": record.get("title"),
            "page": record.get("page"),
            "section": record.get("section"),
            "semantic_type": record.get("semantic_type"),
            "features": record.get("features"),
            "n_rows": len(tc.get("rows") or []),
            "n_cols": len(tc.get("headers") or []),
        })

    index = {
        **envelope_fields,
        "table_count": len(records),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "tables": index_entries,
    }
    _write_json_atomic(manual_dir / "_index.json", index)

    if prune:
        for existing in manual_dir.glob("*.json"):
            if existing.name not in written_names:
                logger.info("pruning stale per-table file %s", existing)
                existing.unlink()

    logger.info("wrote %d per-table files to %s", len(records), manual_dir)
    return manual_dir
