"""Serialize caption_table / register_layout JSON objects into embeddable text.

Narrow tables render as a markdown grid (embeds fine). Wide/bit-field tables
(register maps, big parameter tables) render as one linearized line per row
instead -- a 34-column markdown grid embeds badly, but
"offset 0x000 -- FLASH_ACR: bit 18 DBG_SWEN, bits 2:0 LATENCY[2:0], reset ..."
retrieves well.
"""

from __future__ import annotations

NARROW_COL_THRESHOLD = 6


def _flatten(cell) -> str:
    if cell is None:
        return ""
    return " ".join(str(cell).split("\n"))


def context_header(caption: str, section_number, section_title) -> str:
    section = f"{section_number} {section_title}".strip() if section_number else (section_title or "")
    prefix = f"[{section}] " if section else ""
    return f"{prefix}{caption}"


def _markdown_table(header: list, rows: list) -> str:
    cols = [_flatten(c) for c in header]
    lines = ["| " + " | ".join(cols) + " |", "| " + " | ".join(["---"] * len(cols)) + " |"]
    for row in rows:
        lines.append("| " + " | ".join(_flatten(c) for c in row) + " |")
    return "\n".join(lines)


def _linearize_row(header: list, row: list) -> str:
    if not row:
        return ""
    rowkey = _flatten(row[0])
    parts = [
        f"{_flatten(h)}={_flatten(v)}"
        for h, v in zip(header[1:], row[1:])
        if v not in (None, "")
    ]
    body = ", ".join(parts)
    return f"{rowkey}: {body}" if rowkey else body


def _consolidate_spans(cols: list, vals: list) -> list[tuple[str, str]]:
    """Group consecutive None-continuation cells into one (bit_range, value)."""
    groups = []
    i, n = 0, len(vals)
    while i < n:
        v = vals[i]
        if v in (None, ""):
            i += 1
            continue
        j = i
        while j + 1 < n and vals[j + 1] is None:
            j += 1
        hi, lo = cols[i], cols[j]
        bits_str = f"{hi}" if hi == lo else f"{hi}:{lo}"
        groups.append((bits_str, v))
        i = j + 1
    return groups


def _linearize_wide_rows(header: list, rows: list) -> list[str]:
    """Render offset/register wide rows, pairing a field row with its
    following "Reset value" row into a single line per register entry."""
    is_register_map = len(header) >= 2 and header[0] == "Offset" and header[1] == "Register"
    lines = []
    i = 0
    n = len(rows)
    while i < n:
        row = rows[i]
        if not is_register_map:
            groups = _consolidate_spans(header, row)
            lines.append(", ".join(f"{c}={v}" for c, v in groups))
            i += 1
            continue

        offset, register = _flatten(row[0]), _flatten(row[1])
        if register == "Reset value":
            # An orphaned reset row with no preceding field row (shouldn't
            # normally happen); render standalone.
            groups = _consolidate_spans(header[2:], row[2:])
            lines.append(f"reset value: " + ", ".join(f"bit{c}={v}" for c, v in groups))
            i += 1
            continue

        groups = _consolidate_spans(header[2:], row[2:])
        field_text = ", ".join(
            f"bit {c} {v}" for c, v in groups if v not in (None, "", "Res.")
        )
        reset_text = None
        if i + 1 < n and _flatten(rows[i + 1][1]) == "Reset value":
            reset_groups = _consolidate_spans(header[2:], rows[i + 1][2:])
            reset_text = "".join(v for _, v in reset_groups) or None
            i += 1

        line = f"offset {offset} -- {register}"
        if field_text:
            line += f": {field_text}"
        if reset_text:
            line += f", reset {reset_text}"
        lines.append(line)
        i += 1
    return lines


def is_narrow(header: list) -> bool:
    return len(header) <= NARROW_COL_THRESHOLD


def group_rows(header: list, rows: list) -> list[list]:
    """Split `rows` into atomic groups that must never be separated when
    chunking: a register-map field row plus its following "Reset value" row
    stay together; every other row is its own singleton group."""
    is_register_map = len(header) >= 2 and header[0] == "Offset" and header[1] == "Register"
    if not is_register_map:
        return [[r] for r in rows]
    groups, i, n = [], 0, len(rows)
    while i < n:
        if (
            i + 1 < n
            and _flatten(rows[i + 1][1]) == "Reset value"
            and _flatten(rows[i][1]) != "Reset value"
        ):
            groups.append([rows[i], rows[i + 1]])
            i += 2
        else:
            groups.append([rows[i]])
            i += 1
    return groups


def render_rows_block(header: list, rows: list) -> str:
    """Render the table body: a markdown grid for narrow tables, or one
    linearized line per row/pair for wide bit-field-style tables."""
    if is_narrow(header):
        body = _markdown_table(header, rows)
        linearized = "\n".join(_linearize_row(header, r) for r in rows if any(r))
        return f"{body}\n\n{linearized}" if linearized.strip() else body
    return "\n".join(_linearize_wide_rows(header, rows))


def serialize_caption_table(table: dict) -> str:
    header_line = context_header(
        f"Table {table['table_number']}. {table['caption']}"
        if table.get("table_number") is not None
        else table.get("caption") or "",
        table.get("section_number"),
        table.get("section_title"),
    )
    header, rows = table["header"], table["rows"]
    body_rows = rows[1:] if rows and rows[0] == header else rows
    separator = "\n\n" if is_narrow(header) else "\n"
    return f"{header_line}{separator}{render_rows_block(header, body_rows)}"


def _consolidate_bits(bits: list) -> list[tuple[str, str, str]]:
    """Group consecutive same-field bit entries; `bits` is sorted 31..0."""
    groups = []
    i, n = 0, len(bits)
    while i < n:
        field, access = bits[i]["field"], bits[i]["access"]
        j = i
        while j + 1 < n and bits[j + 1]["field"] == field and bits[j + 1]["access"] == access:
            j += 1
        hi, lo = bits[i]["bit"], bits[j]["bit"]
        bits_str = f"{hi}" if hi == lo else f"{hi}:{lo}"
        groups.append((bits_str, field, access))
        i = j + 1
    return groups


def serialize_register_layout(reg: dict) -> str:
    header_line = context_header(
        f"Register {reg.get('register') or '?'} "
        f"(offset {reg.get('address_offset') or '?'}, reset {reg.get('reset_value') or '?'})",
        reg.get("section_number"),
        reg.get("section_title"),
    )
    lines = []
    for bits_str, field, access in _consolidate_bits(reg["bits"]):
        if field in ("Res.", "", None):
            continue
        access_part = f" ({access})" if access else ""
        lines.append(f"bit {bits_str} {field}{access_part}")
    for desc in reg.get("field_descriptions", []):
        field = f"{desc['field']}: " if desc.get("field") else ""
        lines.append(f"bit {desc['bits']} {field}{desc['text']}")
    return header_line + "\n" + "\n".join(lines)


def serialize(entry: dict) -> str:
    if entry["type"] == "register_layout":
        return serialize_register_layout(entry)
    return serialize_caption_table(entry)
