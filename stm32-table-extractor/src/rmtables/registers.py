"""Register bit-layout detection and hi/lo half merging.

Every STM32 register is rendered as two 16-column ruled grids -- bits
31..16, then bits 15..0 -- each preceded by a plain-text (non-ruled) line
of descending bit numbers and followed by an access-type row (rw/r/w).
These have no "Table N." caption; they're identified by that bit-number
line sitting just above the grid, verified against the real PDF: 677 such
header lines in RM0490, all exactly 16 numbers long.
"""

from __future__ import annotations

import logging
import re

from .headings import extract_register_name
from .model import RegisterLayout

logger = logging.getLogger(__name__)

BIT_HEADER_RE = re.compile(r"^((?:3[01]|[12]?\d)(?:\s+(?:3[01]|[12]?\d))*)$")
OFFSET_RE = re.compile(r"^Address offset:\s*(.+)$")
RESET_RE = re.compile(r"^Reset value:\s*(.+)$")
BIT_DESC_RE = re.compile(r"^Bits?\s+(\d+)(?::(\d+))?\s+(.*)$")
FIELD_PREFIX_RE = re.compile(r"^([A-Za-z0-9_]+(?:\[\d+(?::\d+)?\])?):\s*(.*)$")
_BRACKET_SUFFIX_RE = re.compile(r"\[\d+(?::\d+)?\]$")

HEADER_GAP_TOLERANCE = 15  # points between a bit-header line and its grid


def bit_header_kind(text: str) -> str | None:
    """Return 'hi' / 'lo' if `text` is a descending 16-number bit header."""
    m = BIT_HEADER_RE.match(text.strip())
    if not m:
        return None
    nums = [int(x) for x in text.split()]
    if len(nums) != 16 or not all(a == b + 1 for a, b in zip(nums, nums[1:])):
        return None
    if nums[0] == 31:
        return "hi"
    if nums[0] == 15:
        return "lo"
    return None


def find_bit_headers(lines) -> list[tuple[float, str]]:
    headers = []
    for line in lines:
        kind = bit_header_kind(line["text"])
        if kind:
            headers.append((line["top"], kind))
    return headers


def match_bit_header(raw_table, bit_headers) -> str | None:
    """Return the bit-header kind whose line sits just above this grid."""
    top = raw_table.bbox[1]
    best_kind, best_gap = None, None
    for h_top, kind in bit_headers:
        gap = top - h_top
        if 0 <= gap <= HEADER_GAP_TOLERANCE and (best_gap is None or gap < best_gap):
            best_kind, best_gap = kind, gap
    return best_kind


def _cols_to_bits(raw_table, start_bit: int) -> list[dict]:
    field_row = raw_table.rows[0] if raw_table.rows else []
    access_row = raw_table.rows[1] if len(raw_table.rows) > 1 else []
    bits = []
    current_field = ""
    for i, cell in enumerate(field_row):
        if cell is not None:
            current_field = cell
        access = access_row[i] if i < len(access_row) and access_row[i] is not None else ""
        bits.append({"bit": start_bit - i, "field": current_field, "access": access})
    return bits


def parse_bit_description(line_text: str) -> dict | None:
    m = BIT_DESC_RE.match(line_text)
    if not m:
        return None
    hi = int(m.group(1))
    lo = int(m.group(2)) if m.group(2) else None
    rest = m.group(3)
    field = None
    fm = FIELD_PREFIX_RE.match(rest)
    if fm:
        field = _BRACKET_SUFFIX_RE.sub("", fm.group(1))
        text = fm.group(2)
    else:
        text = rest
    bits_str = f"{hi}:{lo}" if lo is not None else str(hi)
    return {"bits": bits_str, "field": field, "text": text}


def _nearest_line_above(lines, pattern, before_top) -> str | None:
    best_top, best_val = None, None
    for line in lines:
        if line["top"] >= before_top:
            continue
        m = pattern.match(line["text"])
        if m and (best_top is None or line["top"] > best_top):
            best_top, best_val = line["top"], m.group(1)
    return best_val


def _last_match(lines, pattern) -> str | None:
    """Last match of `pattern` in `lines`, in document order."""
    val = None
    for line in lines:
        m = pattern.match(line["text"])
        if m:
            val = m.group(1)
    return val


class RegisterMerger:
    def __init__(self):
        self.current: RegisterLayout | None = None
        self.active_target: RegisterLayout | None = None
        self.finalized: list[RegisterLayout] = []
        self._prev_page_lines: list = []

    def process_page(self, page_number: int, raw_tables, lines, heading_tracker) -> set:
        """Consume register-grid raw tables and bit-description lines on this page.

        Returns the set of `id(raw_table)` consumed, so the caller can treat
        the rest as plain caption/figure candidates.
        """
        bit_headers = find_bit_headers(lines)
        events = []
        consumed = set()
        for raw_table in raw_tables:
            kind = match_bit_header(raw_table, bit_headers)
            if kind:
                consumed.add(id(raw_table))
                events.append((raw_table.bbox[1], "grid", kind, raw_table))
        for line in lines:
            desc = parse_bit_description(line["text"])
            if desc:
                events.append((line["top"], "desc", desc, None))
        events.sort(key=lambda e: e[0])

        for top, kind, payload, raw_table in events:
            if kind == "grid":
                self._handle_grid(page_number, top, payload, raw_table, lines, heading_tracker)
            else:
                target = self.current or self.active_target
                if target is not None:
                    target.field_descriptions.append(payload)

        self._prev_page_lines = lines
        return consumed

    def _handle_grid(self, page_number, top, half_kind, raw_table, lines, heading_tracker):
        if half_kind == "hi":
            if self.current is not None:
                logger.warning(
                    "orphan register hi-half with no lo-half (register=%s, page %d)",
                    self.current.register,
                    self.current.page_start,
                )
                self.finalized.append(self.current)
                self.active_target = self.current

            heading = heading_tracker.heading_before(top)
            section_number, section_title = heading if heading else (None, None)
            register_name = extract_register_name(section_title) if section_title else None
            # A register's "Address offset:"/"Reset value:" lines (and its
            # heading) can land on the tail of the *previous* page when the
            # bit-field grid itself starts fresh at the top of this one.
            address_offset = _nearest_line_above(lines, OFFSET_RE, top) or _last_match(
                self._prev_page_lines, OFFSET_RE
            )
            reset_value = _nearest_line_above(lines, RESET_RE, top) or _last_match(
                self._prev_page_lines, RESET_RE
            )

            self.current = RegisterLayout(
                register=register_name,
                section_number=section_number,
                section_title=section_title,
                address_offset=address_offset,
                reset_value=reset_value,
                page_start=page_number,
                page_end=page_number,
                spans_pages=False,
                bits=_cols_to_bits(raw_table, 31),
                field_descriptions=[],
            )
        else:  # "lo"
            if self.current is None:
                logger.warning(
                    "orphan register lo-half with no hi-half (page %d)", page_number
                )
                return
            self.current.bits.extend(_cols_to_bits(raw_table, 15))
            self.current.page_end = page_number
            self.current.spans_pages = page_number != self.current.page_start
            self.finalized.append(self.current)
            self.active_target = self.current
            self.current = None

    def finalize(self) -> list[RegisterLayout]:
        if self.current is not None:
            logger.warning(
                "register %s left open at end of document", self.current.register
            )
            self.finalized.append(self.current)
            self.current = None
        return self.finalized
