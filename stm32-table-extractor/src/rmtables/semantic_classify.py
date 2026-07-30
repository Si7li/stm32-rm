"""Semantic table-type classification (SEMANTIC_BUILD_TASK.md).

Second-stage classification of an already-assembled table (headers/rows
already merged-cell-filled) into one of the seven selective-RAG semantic
types. Distinct from `classify.py`, which is the *first-stage* raw-grid
routing (caption_table/register_layout/figure_fragment) that runs during
extraction -- this module only runs afterward, in `exporter.py`, on the
finished table.

Deterministic, header-signature-first classification (caption/section are
only tiebreakers), most-specific type checked first, falling through to
`generic`. Conservative by design: a wrong type is worse than `generic`, so
every check here requires a fairly specific signature before committing.
"""

from __future__ import annotations

import logging
import re

logger = logging.getLogger(__name__)

FEATURE_FIRST_COL = {"feature", "peripheral or function", "peripheral"}
INTERRUPT_REQUIRED = {"position", "priority", "address"}
INTERRUPT_ONE_OF = {"acronym", "description"}


def _norm(h) -> str:
    # De-hyphenate a line-wrapped word first (e.g. a narrow "Offset" column
    # header prints as "Off-\nset") -- a real hyphenated compound like
    # "Fast-mode" is untouched since its hyphen isn't followed by whitespace.
    h = re.sub(r"-\s+", "", (h or ""))
    return re.sub(r"\s+", " ", h.strip()).lower()


def _norm_all(headers) -> list[str]:
    return [_norm(h) for h in headers]


def _is_register_map_headers(headers) -> bool:
    """>=8 numeric header columns, including both 31 and 0 -- tolerant of a
    few missing intermediate numbers (a handful of register-map tables have
    narrow bit-header cells that read empty; a known, documented
    limitation), so this checks set-membership + a non-increasing sequence
    rather than requiring a perfectly unbroken 31..0 run."""
    nums = []
    for h in headers:
        hn = (h or "").strip()
        if re.fullmatch(r"\d+", hn):
            nums.append(int(hn))
    if len(nums) < 8 or 31 not in nums or 0 not in nums:
        return False
    return all(a >= b for a, b in zip(nums, nums[1:]))


def _is_alt_function_headers(headers) -> bool:
    norm = _norm_all(headers)
    if any(re.fullmatch(r"af\d+", h) for h in norm):
        return True
    return any("alternate function" in h for h in norm)


def _is_interrupt_headers(headers) -> bool:
    norm = set(_norm_all(headers))
    if not INTERRUPT_REQUIRED <= norm:
        return False
    return bool(INTERRUPT_ONE_OF & norm)


def _is_memory_map_headers(headers) -> bool:
    norm = set(_norm_all(headers))
    has_boundary = bool({"boundary address", "base address"} & norm)
    has_size_or_bus = bool({"size", "bus"} & norm)
    if has_boundary and has_size_or_bus:
        return True
    return {"area", "size"} <= norm


def _is_parameter_headers(headers) -> bool:
    norm = set(_norm_all(headers))
    if norm & {"min", "max", "typ"}:
        return True
    return {"symbol", "unit"} <= norm


def _is_feature_matrix(headers, rows) -> bool:
    """First column is a row label (either a recognized keyword, or every
    remaining header references an STM32/device variant); remaining columns
    (>=2, a real *matrix* needs multiple variants to compare) hold mostly
    short values -- tolerant of the occasional descriptive value ("12
    bits", "2.5 Msps"), but rejects a table where most cells are long,
    sentence-like text (a sign this isn't really a comparison grid)."""
    if len(headers) < 3:
        return False
    first, rest = headers[0], headers[1:]
    keyword_match = _norm(first) in FEATURE_FIRST_COL
    stm32_match = all(re.search(r"stm32", h or "", re.IGNORECASE) for h in rest)
    if not (keyword_match or stm32_match):
        return False
    values = [c for row in rows for c in row[1:] if row and c]
    if not values:
        return False
    long_values = sum(1 for v in values if len(v) > 40)
    return (long_values / len(values)) < 0.2


def classify_table(caption: str, headers: list, rows: list) -> tuple[str, str]:
    """Returns `(semantic_type, signal)` -- `signal` is a short description
    of which rule fired, for logging/debugging only (not part of the
    schema)."""
    caption_norm = (caption or "").lower()

    if _is_register_map_headers(headers):
        return "register_map", "header: descending bit-number run (31..0)"
    if "register map" in caption_norm:
        return "register_map", "caption: 'register map'"

    if _is_alt_function_headers(headers):
        return "alternate_function", "header: AFx column or 'alternate function'"
    if any(k in caption_norm for k in ("alternate function", "remap")) or re.search(
        r"pin (definition|assignment)", caption_norm
    ):
        return "alternate_function", "caption: alternate function/remap/pin keyword"

    if _is_interrupt_headers(headers):
        return "interrupt_vector", "header: position+priority+address+(acronym|description)"
    if "vector table" in caption_norm:
        return "interrupt_vector", "caption: 'vector table'"

    if _is_memory_map_headers(headers):
        return "memory_map", "header: boundary/base address + size|bus, or area+size"
    if any(k in caption_norm for k in ("boundary addresses", "memory map", "register boundary")):
        return "memory_map", "caption: boundary addresses/memory map/register boundary"

    if _is_parameter_headers(headers):
        return "parameter", "header: min|max|typ, or symbol+unit"

    if _is_feature_matrix(headers, rows):
        return "feature_matrix", "header: feature/peripheral row-label + variant columns"

    return "generic", "no signal matched"
