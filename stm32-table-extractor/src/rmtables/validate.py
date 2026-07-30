"""Reconcile extracted tables against the manual's own "List of tables"."""

from __future__ import annotations

import difflib
import re
from dataclasses import dataclass, field

BIT_HEADER_RE = re.compile(r"^\d+$")
CAPTION_MATCH_THRESHOLD = 0.6


@dataclass
class ValidationReport:
    missing_numbers: list = field(default_factory=list)  # in list-of-tables, not extracted
    extra_numbers: list = field(default_factory=list)  # extracted, not in list-of-tables
    caption_mismatches: list = field(default_factory=list)  # (number, expected, got, ratio)
    bad_register_headers: list = field(default_factory=list)  # table_number
    empty_tables: list = field(default_factory=list)  # table_number
    register_count: int = 0
    incomplete_registers: list = field(default_factory=list)  # (register, missing_bits)
    # RESERVED_FIELDS_TASK.md: (table_number, register_name, problem) for any
    # register_map register whose semantic `fields` don't exactly cover
    # 31..0 -- a real gap/overlap here means a parse issue in that specific
    # table, not a genuinely incomplete register (every field, including
    # reserved runs, is expected to be present after the fix).
    field_coverage_errors: list = field(default_factory=list)

    def is_clean(self) -> bool:
        return not (
            self.missing_numbers
            or self.extra_numbers
            or self.caption_mismatches
            or self.bad_register_headers
            or self.empty_tables
            or self.incomplete_registers
            or self.field_coverage_errors
        )

    def summary(self) -> str:
        lines = []
        lines.append(f"missing (in list-of-tables, not extracted): {self.missing_numbers}")
        lines.append(f"extra (extracted, not in list-of-tables): {self.extra_numbers}")
        lines.append(f"caption mismatches: {len(self.caption_mismatches)}")
        for num, expected, got, ratio in self.caption_mismatches:
            lines.append(f"  Table {num}: expected {expected!r} got {got!r} (ratio {ratio:.2f})")
        lines.append(f"register tables with bad bit headers: {self.bad_register_headers}")
        lines.append(f"empty tables: {self.empty_tables}")
        if self.register_count:
            lines.append(f"register_layout count: {self.register_count}")
            lines.append(f"registers with incomplete 32-bit coverage: {len(self.incomplete_registers)}")
            for reg, missing in self.incomplete_registers:
                lines.append(f"  {reg}: missing bits {missing}")
        lines.append(
            f"register_map fields with 31..0 coverage errors: {len(self.field_coverage_errors)}"
        )
        for table_number, register, problem in self.field_coverage_errors:
            lines.append(f"  Table {table_number} {register}: {problem}")
        return "\n".join(lines)


def _looks_like_register_map(caption: str | None) -> bool:
    # ST's recurring exact phrase for a real bit-field register map. Looser
    # matches like "register map" alone also catch index/summary tables
    # (e.g. "EXTI register map sections") that are plain 2-column tables,
    # not a 31..0 bit-field grid.
    return bool(caption) and "register map and reset values" in caption.lower()


def _has_descending_bit_header(header: list) -> bool:
    nums = [int(h) for h in header if isinstance(h, str) and BIT_HEADER_RE.match(h)]
    if len(nums) < 2:
        return False
    return all(a == b + 1 for a, b in zip(nums, nums[1:]))


def _field_bit_range(bits: str) -> tuple[int, int]:
    if ":" in bits:
        hi, lo = bits.split(":")
        return int(hi), int(lo)
    n = int(bits)
    return n, n


def _header_bit_span(headers: list) -> tuple[int, int] | None:
    """The `(max_bit, min_bit)` a register_map table's own bit-number
    headers actually span -- NOT hardcoded to 31..0, since some real
    register maps are genuinely narrower (e.g. RM0490 Table 139's SPI/I2S
    registers print only bits 15..0, a real 16-bit-wide register on that
    family, not a parsing gap). Uses `_parse_bit_header` so a grouped header
    like "31-24" counts too. `None` if the table has no bit-number headers
    at all (shouldn't happen for a register_map, but never guessed at)."""
    from .semantic import _parse_bit_header

    spans = [r for h in headers if (r := _parse_bit_header(h)) is not None]
    if not spans:
        return None
    return max(hi for hi, _ in spans), min(lo for _, lo in spans)


def _register_map_field_coverage_errors(rag_doc: dict) -> list[tuple[str, str, str]]:
    """(table_number, register_name, problem) for any register_map register
    in the actual exported rag_selective document whose `fields` don't cover
    that table's own full bit-header span exactly -- gaps or overlaps both
    reported, since either one means the field row didn't parse the way
    `_build_fields` expects."""
    errors: list[tuple[str, str, str]] = []
    for t in rag_doc.get("tables", []):
        table_content = t.get("table_content", {})
        if table_content.get("semantic_type") != "register_map":
            continue
        span = _header_bit_span(table_content.get("headers", []))
        if span is None:
            continue
        max_bit, min_bit = span
        expected = set(range(min_bit, max_bit + 1))
        table_number = t.get("table_number", "")
        for reg in table_content.get("semantic", {}).get("registers", []):
            covered: set[int] = set()
            overlap_bits: set[int] = set()
            for f in reg.get("fields", []):
                hi, lo = _field_bit_range(f["bits"])
                span_bits = set(range(lo, hi + 1))
                overlap_bits |= covered & span_bits
                covered |= span_bits
            if overlap_bits:
                errors.append((table_number, reg["name"], f"overlapping bits {sorted(overlap_bits, reverse=True)}"))
            missing = sorted(expected - covered, reverse=True)
            if missing:
                errors.append((table_number, reg["name"], f"gap at bits {missing}"))
    return errors


def validate(tables_json: dict, list_of_tables: dict, rag_doc: dict | None = None) -> ValidationReport:
    report = ValidationReport()
    if rag_doc is not None:
        report.field_coverage_errors = _register_map_field_coverage_errors(rag_doc)

    caption_tables = [t for t in tables_json["tables"] if t["type"] == "caption_table"]
    registers = [t for t in tables_json["tables"] if t["type"] == "register_layout"]

    extracted_numbers = {
        t["table_number"] for t in caption_tables if t["table_number"] is not None
    }
    list_numbers = set(list_of_tables.keys())

    report.missing_numbers = sorted(list_numbers - extracted_numbers)
    report.extra_numbers = sorted(extracted_numbers - list_numbers)

    for t in caption_tables:
        num = t["table_number"]
        if num is None or num not in list_of_tables:
            continue
        expected_caption, _expected_page = list_of_tables[num]
        got_caption = t["caption"] or ""
        ratio = difflib.SequenceMatcher(None, expected_caption.lower(), got_caption.lower()).ratio()
        if ratio < CAPTION_MATCH_THRESHOLD:
            report.caption_mismatches.append((num, expected_caption, got_caption, ratio))

        if _looks_like_register_map(t["caption"]) and not _has_descending_bit_header(t["header"]):
            report.bad_register_headers.append(num)

        if t["n_rows"] == 0 or all(
            all(cell in (None, "") for cell in row) for row in t["rows"]
        ):
            report.empty_tables.append(num)

    report.register_count = len(registers)
    for reg in registers:
        bits_present = {b["bit"] for b in reg["bits"]}
        missing = sorted(set(range(32)) - bits_present)
        if missing:
            report.incomplete_registers.append((reg["register"], missing))

    return report


def validate_chunks(chunk_records: list, chunk_tokens: int) -> list[str]:
    """Sanity-check RAG chunk records; returns a list of warning strings."""
    from .chunk import approx_tokens

    warnings = []
    empty = [r["id"] for r in chunk_records if not r["text"].strip()]
    if empty:
        warnings.append(f"{len(empty)} chunk(s) have empty text: {empty[:10]}")

    over_budget = [
        r["id"] for r in chunk_records if approx_tokens(r["text"]) > chunk_tokens * 1.1
    ]
    if over_budget:
        warnings.append(
            f"{len(over_budget)} chunk(s) exceed the {chunk_tokens}-token budget: {over_budget[:10]}"
        )

    no_section = [
        r["id"]
        for r in chunk_records
        if not r["metadata"].get("section_number") and not r["metadata"].get("section_title")
    ]
    if no_section:
        warnings.append(f"{len(no_section)} chunk(s) have no resolved section: {no_section[:10]}")

    ids = [r["id"] for r in chunk_records]
    dupes = sorted({i for i in ids if ids.count(i) > 1})
    if dupes:
        warnings.append(f"{len(dupes)} duplicate chunk id(s): {dupes[:10]}")

    return warnings
