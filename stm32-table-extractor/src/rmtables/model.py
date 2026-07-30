"""Dataclasses for the extracted-table JSON schema."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class Caption:
    number: Optional[int]
    text: str
    continued: bool
    top: float
    page: int


@dataclass
class RawTable:
    """One physically-detected grid on a single page, before caption/merge."""

    page: int
    bbox: tuple
    rows: list  # list[list[Optional[str]]]

    @property
    def n_rows(self) -> int:
        return len(self.rows)

    @property
    def n_cols(self) -> int:
        return len(self.rows[0]) if self.rows else 0


@dataclass
class LogicalTable:
    """One logical table, possibly merged across pages."""

    table_number: Optional[int]
    caption: Optional[str]
    page_start: int
    page_end: int
    spans_pages: bool
    rows: list = field(default_factory=list)
    section_number: Optional[str] = None
    section_title: Optional[str] = None
    notes: list = field(default_factory=list)
    legend: list = field(default_factory=list)

    def to_json(self) -> dict:
        header = self.rows[0] if self.rows else []
        return {
            "type": "caption_table",
            "table_number": self.table_number,
            "caption": self.caption,
            "section_number": self.section_number,
            "section_title": self.section_title,
            "page_start": self.page_start,
            "page_end": self.page_end,
            "spans_pages": self.spans_pages,
            "n_rows": len(self.rows),
            "n_cols": len(self.rows[0]) if self.rows else 0,
            "header": header,
            "rows": self.rows,
        }


@dataclass
class RegisterLayout:
    """One register's full 32-bit map, merged from its hi/lo half-grids."""

    register: Optional[str]
    section_number: Optional[str]
    section_title: Optional[str]
    address_offset: Optional[str]
    reset_value: Optional[str]
    page_start: int
    page_end: int
    spans_pages: bool
    bits: list = field(default_factory=list)  # [{bit, field, access}], bit 31..0
    field_descriptions: list = field(default_factory=list)  # [{bits, field, text}]

    def to_json(self) -> dict:
        return {
            "type": "register_layout",
            "register": self.register,
            "section_number": self.section_number,
            "section_title": self.section_title,
            "address_offset": self.address_offset,
            "reset_value": self.reset_value,
            "page_start": self.page_start,
            "page_end": self.page_end,
            "spans_pages": self.spans_pages,
            "bits": sorted(self.bits, key=lambda b: -b["bit"]),
            "field_descriptions": self.field_descriptions,
        }
