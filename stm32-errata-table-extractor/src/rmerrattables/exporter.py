"""Sidekick JSON document builder (mirrors rmtables.exporter exactly).

Envelope: {document, rev, url_pdf, references, package, family, core,
frequency, table_count, tables}. Records are flat and repeat document/rev/
url_pdf on every record (rootTagPath: tables means the processor never sees
anything outside the array). Conventions are the sibling's: table_number is
a string; page is an int; headers/columns/title/section_title are trimmed
and single-spaced; rows are DATA ONLY with "" (never null) for missing or
merged cells; notes are de-duplicated and order-preserving; table_id is
{document}-T{n:03}; `url` deep-links to the table's start page.
"""

from __future__ import annotations

import re

from .tags import build_tags

_NOTES_TRUNCATE = 200


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip())


def _text_helper(number, title, page, section, section_title, rev, notes) -> str:
    section_part = f" in section {section} ({section_title})" if section else ""
    text = (
        f'Table {number}, "{title}", on page {page} of {rev}{section_part}.'
    )
    if notes:
        joined = "; ".join(notes)
        if len(joined) > _NOTES_TRUNCATE:
            joined = joined[:_NOTES_TRUNCATE].rstrip() + "..."
        text += f" Notes: {joined}."
    return text


def table_to_schema(table, document: str, rev: str, url_pdf: str) -> dict:
    n = table.number
    title = _normalize(table.title)
    section = str(table.section)
    section_title = _normalize(table.section_title)
    headers = [_normalize(h) for h in table.headers]
    rows = [[_normalize(c) for c in r] for r in table.rows]
    notes = [ _normalize(n) for n in table.notes]
    legend = _normalize(table.legend)
    page = table.page

    features = build_tags(title, section_title, headers)
    text_helper = _text_helper(
        n, title, page, section, section_title, rev, notes)

    return {
        "table_id": f"{document}-T{n:03d}",
        "document": document,
        "rev": rev,
        "table_number": str(n),
        "title": title,
        "page": page,
        "section": section,
        "section_title": section_title,
        "semantic_type": "generic",
        "features": features,
        "url": f"{url_pdf}#page={page}",
        "url_pdf": url_pdf,
        "columns": headers,
        "text_helper": text_helper,
        "table_content": {
            "headers": headers,
            "rows": rows,
            "notes": notes,
            "legend": legend,
            "semantic_type": "generic",
            "semantic": {},
        },
    }


def build_document(tables: list, meta: dict) -> dict:
    document = meta["name_datasheet"]
    rev = meta["rev"]
    url_pdf = meta["url_pdf"]
    records = [table_to_schema(t, document, rev, url_pdf) for t in tables]
    _ensure_unique_ids(records)
    return {
        "document": document,
        "rev": rev,
        "url_pdf": url_pdf,
        "references": meta.get("references", ""),
        "package": meta.get("package", ""),
        "family": meta.get("family", ""),
        "core": meta.get("core", ""),
        "frequency": meta.get("frequency", ""),
        "table_count": len(records),
        "tables": records,
    }


def _ensure_unique_ids(records: list) -> None:
    seen: dict[str, int] = {}
    for rec in records:
        key = rec["table_id"]
        if key in seen:
            new = f"{key}-{seen[key]}"
            seen[key] += 1
            rec["table_id"] = new
        else:
            seen[key] = 1