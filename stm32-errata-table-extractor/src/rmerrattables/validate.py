"""Internal schema validation (mirrors rmtables' --validate spirit).

Walks the built document and asserts the invariants the schema promises
(see exporter docstring). Everything is checkable without the source PDF;
agreement-with-reality checks live in the manual verification habits and
`tests/` PDF-free fixtures.
"""

from __future__ import annotations


def _check(cond: bool, problems: list, msg: str):
    if not cond:
        problems.append(msg)


def validate_document(doc: dict) -> list[str]:
    problems: list[str] = []
    envelope_keys = {"document", "rev", "url_pdf", "references", "package",
                     "family", "core", "frequency", "table_count", "tables"}
    _check(set(doc) == envelope_keys, problems,
           f"envelope keys {sorted(doc)} != {sorted(envelope_keys)}")
    records = doc.get("tables", [])
    _check(isinstance(doc.get("table_count"), int)
           and doc.get("table_count") == len(records), problems,
           "table_count != len(tables)")

    ids = set()
    for rec in records:
        tag = rec.get("table_id", "")
        _check(tag not in ids, problems, f"duplicate table_id {tag}")
        ids.add(tag)
        _check(rec.get("document") == doc["document"], problems,
               f"{tag}: record document != envelope")
        _check(rec.get("rev") == doc["rev"], problems, f"{tag}: record rev != envelope")
        _check(isinstance(rec.get("page"), int) and rec.get("page", 0) >= 1,
               problems, f"{tag}: bad page {rec.get('page')!r}")
        _check(isinstance(rec.get("table_number"), str), problems,
               f"{tag}: table_number must be str")
        tc = rec.get("table_content") or {}
        _check("headers" in tc and "rows" in tc, problems,
               f"{tag}: table_content missing headers/rows")
        headers = tc.get("headers", [])
        _check(rec.get("columns") == headers, problems, f"{tag}: columns != headers")
        _check(rec.get("title") is None or isinstance(rec.get("title"), str),
               problems, f"{tag}: title not a string")
        for row in tc.get("rows", []):
            _check(isinstance(row, list) and len(row) == len(headers),
                   problems, f"{tag}: row width {len(row)} != headers {len(headers)}")
            for cell in row:
                _check(isinstance(cell, str), problems, f"{tag}: non-string cell {cell!r}")
        _check(isinstance(tc.get("notes"), list), problems, f"{tag}: notes not a list")
        _check(isinstance(tc.get("legend"), str), problems, f"{tag}: legend not a string")
        _check(rec.get("url", "").startswith(doc["url_pdf"] + "#page="),
               problems, f"{tag}: url not a deep link of url_pdf")
    _check("url_pdf" not in doc or doc.get("url_pdf"), problems, "empty url_pdf")
    return problems