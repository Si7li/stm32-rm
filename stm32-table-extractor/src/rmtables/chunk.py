"""Chunk caption_table / register_layout entries into RAG-ready JSONL records.

One chunk per logical table/register by default. A caption_table is only
split when its serialized text exceeds the token budget, splitting by row
groups and repeating the context header (and column-header row, for narrow
tables) in every sub-chunk. register_layout entries are small and never
split. Continuation-merge (merge.py) already ran before this point, so a
multi-page table is one logical unit here, not duplicate chunks.
"""

from __future__ import annotations

from .serialize import (
    context_header,
    group_rows,
    is_narrow,
    render_rows_block,
    serialize_register_layout,
)

DEFAULT_CHUNK_TOKENS = 600


def approx_tokens(text: str) -> int:
    return max(1, len(text) // 4)


def _caption_table_texts(table: dict, chunk_tokens: int) -> list[str]:
    header, rows = table["header"], table["rows"]
    body_rows = rows[1:] if rows and rows[0] == header else rows
    header_line = context_header(
        f"Table {table['table_number']}. {table['caption']}"
        if table.get("table_number") is not None
        else table.get("caption") or "",
        table.get("section_number"),
        table.get("section_title"),
    )
    separator = "\n\n" if is_narrow(header) else "\n"
    full_text = f"{header_line}{separator}{render_rows_block(header, body_rows)}"
    if approx_tokens(full_text) <= chunk_tokens or not body_rows:
        return [full_text]

    groups = group_rows(header, body_rows)
    chunks_rows: list[list] = []
    current: list = []
    for g in groups:
        candidate = current + g
        candidate_text = f"{header_line}{separator}{render_rows_block(header, candidate)}"
        if current and approx_tokens(candidate_text) > chunk_tokens:
            chunks_rows.append(current)
            current = list(g)
        else:
            current = candidate
    if current:
        chunks_rows.append(current)

    return [
        f"{header_line}{separator}{render_rows_block(header, rows_subset)}"
        for rows_subset in chunks_rows
    ]


def _slugify(text: str) -> str:
    return "".join(c if c.isalnum() else "_" for c in text).strip("_")


def _base_id(entry: dict, source_stem: str) -> str:
    if entry["type"] == "register_layout":
        # Register name alone collides across peripheral instances that
        # share a generic name (TIMx_CCMR1 at 0x18 for TIM1/TIM3/TIM14/...),
        # and section number alone can still collide when one heading covers
        # several same-named words of a wide register (UID at 0x00/0x04/0x08
        # under one "Unique device ID register" heading). Section number and
        # address offset together are unique in every case observed.
        parts = [source_stem, "register", entry.get("register") or "unknown"]
        if entry.get("section_number"):
            parts.append("s" + _slugify(str(entry["section_number"])))
        if entry.get("address_offset"):
            parts.append(_slugify(str(entry["address_offset"])))
        return "-".join(parts)
    return f"{source_stem}-table{entry.get('table_number')}"


def build_chunks(entry: dict, source_file: str, chunk_tokens: int = DEFAULT_CHUNK_TOKENS) -> list[dict]:
    stem = source_file.rsplit(".", 1)[0] if "." in source_file else source_file
    base_id = _base_id(entry, stem)

    if entry["type"] == "register_layout":
        texts = [serialize_register_layout(entry)]
    else:
        texts = _caption_table_texts(entry, chunk_tokens)

    n_chunks = len(texts)
    records = []
    for i, text in enumerate(texts):
        records.append(
            {
                "id": f"{base_id}-chunk{i}",
                "text": text,
                "metadata": {
                    "source_file": source_file,
                    "type": entry["type"],
                    "table_number": entry.get("table_number"),
                    "register": entry.get("register"),
                    "caption": entry.get("caption"),
                    "section_number": entry.get("section_number"),
                    "section_title": entry.get("section_title"),
                    "page_start": entry.get("page_start"),
                    "page_end": entry.get("page_end"),
                    "address_offset": entry.get("address_offset"),
                    "chunk_index": i,
                    "n_chunks": n_chunks,
                },
            }
        )
    return records
