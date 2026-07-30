"""Per-type semantic extractors (SEMANTIC_BUILD_TASK.md).

Each `extract_*` function turns an already-assembled table's `headers` +
`rows` (merged-cell fill already ran; every row is complete) into the typed
object for its schema, resolving columns by header name -- never by
position -- so a reordered or slightly-differently-worded column still
resolves correctly. Returns `None` when the shape doesn't confidently
resolve (e.g. a table whose header matched the classifier's signature but
whose columns can't actually be role-mapped); `exporter.py` treats `None`
as "fall back to generic" so `semantic_type`/`semantic` never end up in a
mismatched half-state (a `register_map` type with an empty/junk `semantic`
body, for instance).

Values are kept as raw strings throughout -- no numeric coercion, no
guessing at what a blank/reserved cell "should" mean.
"""

from __future__ import annotations

import re

FOOTNOTE_MARKER_RE = re.compile(r"^\(\d+\)\s*")


def _norm(h) -> str:
    # De-hyphenate a line-wrapped word first (e.g. a narrow "Offset" column
    # header prints as "Off-\nset") -- a real hyphenated compound like
    # "Fast-mode" is untouched since its hyphen isn't followed by whitespace.
    h = re.sub(r"-\s+", "", (h or ""))
    return re.sub(r"\s+", " ", h.strip()).lower()


def _strip_footnote(s: str) -> str:
    return FOOTNOTE_MARKER_RE.sub("", (s or "").strip())


def _col(headers, *names) -> int | None:
    """First header index whose normalized text equals one of `names`."""
    normed = [_norm(h) for h in headers]
    for name in names:
        if name in normed:
            return normed.index(name)
    return None


def _get(row, idx) -> str:
    if idx is None or idx >= len(row):
        return ""
    return row[idx] or ""


# ---------------------------------------------------------------- register_map

RESET_VALUE_RE = re.compile(r"reset\s*value", re.IGNORECASE)
TRAILING_PAREN_RE = re.compile(r"\s*\([^)]*\)\s*$")
TRAILING_RESET_VALUE_RE = re.compile(r"\s*reset\s*value\s*$", re.IGNORECASE)
HEX_TOKEN_RE = re.compile(r"^0x[0-9A-Fa-f ]+$")
BIT_SINGLE_RE = re.compile(r"^\d+$")
# CELL_TEXT_ASSEMBLY_FIX.md: a bare space is now also a valid separator --
# fixing the cell-text-assembly gap-space bug turns a header that used to
# render fused ("3124") into space-separated ("31 24"), same as the
# already-supported "31-24"/"31:24" forms.
BIT_RANGE_RE = re.compile(r"^(\d+)(?:\s*[-:]\s*|\s+)(\d+)$")


def _collapse_ws(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip())


def _parse_bit_header(h) -> tuple[int, int] | None:
    """Returns the `(hi, lo)` bit positions a header column covers, or
    `None` if it isn't a bit-number header at all. Handles both a single
    bit ("7" -> (7, 7)) and a grouped range ("31-24" or "31:24" -> (31,
    24)) -- ST reference manuals commonly group several bit columns under
    one wide header instead of printing all 32 individually."""
    hs = (h or "").strip()
    if BIT_SINGLE_RE.fullmatch(hs):
        n = int(hs)
        return (n, n)
    m = BIT_RANGE_RE.fullmatch(hs)
    if m:
        a, b = int(m.group(1)), int(m.group(2))
        return (max(a, b), min(a, b))
    return None


# RESERVED_FIELDS_TASK.md: a bit column is reserved if its (merge-filled)
# label is blank, or reads as some spelling of "reserved" -- "Res.", "res.",
# "Reserved", bare "Res" without the period. A partially-named cell like
# "UIFCPY or Res." is NOT reserved -- the whole string must be nothing but
# the reserved marker, so that literal, partially-meaningful name is kept
# as-is rather than forced to "Res.".
RESERVED_RE = re.compile(r"^\s*res(?:erved)?\.?\s*$", re.IGNORECASE)


def _is_reserved(name: str) -> bool:
    n = (name or "").strip()
    return not n or bool(RESERVED_RE.match(n))


def _build_fields(bit_cols: list[tuple[int, int, int]], values: list[str]) -> list[dict]:
    """Group consecutive bit *columns* into `{"bits": "hi:lo", "name": ...}`
    entries covering every bit, MSB->LSB (RESERVED_FIELDS_TASK.md): a run of
    columns sharing the same (non-reserved) field name becomes one named
    entry, e.g. "LATENCY\\n[2:0]" repeated across bits 2,1,0 becomes one
    entry, not three -- and a run of reserved columns (any spelling, or
    blank) becomes one canonical `{"name": "Res."}` entry, so the two kinds
    of runs interleave to cover the full width with no gaps. `bit_cols` is
    `[(col_index, hi, lo), ...]`, aligned position-for-position with
    `values`; a run's overall span is the actual bit range its grouped
    columns cover (`bit_cols[i][1]` down to `bit_cols[j][2]`), not the
    column's own header text -- this is what correctly spans a field across
    a grouped header like "31-24"/"23-16" instead of only counting the
    ungrouped single-bit columns."""
    fields = []
    i, n = 0, len(bit_cols)
    while i < n:
        raw = (values[i] or "").replace("\n", "")
        reserved = _is_reserved(raw)
        j = i
        if reserved:
            while j + 1 < n and _is_reserved((values[j + 1] or "").replace("\n", "")):
                j += 1
            name = "Res."
        else:
            while j + 1 < n and (values[j + 1] or "").replace("\n", "") == raw:
                j += 1
            name = raw
        hi, lo = bit_cols[i][1], bit_cols[j][2]
        bits = str(hi) if hi == lo else f"{hi}:{lo}"
        fields.append({"bits": bits, "name": name})
        i = j + 1
    return fields


SIMPLE_BIT_VALUE_RE = re.compile(r"^[01Xx]$")


def _consolidate_reset_spans(
    bit_cols: list[tuple[int, int, int]], reset_values: list[str]
) -> list[tuple[int, int, str]]:
    """Groups consecutive bit columns sharing the identical (non-blank,
    non-reserved) reset-cell text into one `(hi, lo, value)` span -- the
    exact same grouping `_build_fields` uses for field names, because
    merged-cell fill repeats one drawn cell's text across every position it
    visually covers, whether that's a single bit digit repeated (each
    column genuinely is that bit) or one hex token spanning several
    *ungrouped* single-bit columns (REGISTER_RESET_FIX.md: RM0008's
    CRC_IDR/CRC_CR reset row shows "0x00"/"0" repeated across 8 individual
    1-bit header columns -- that's ONE 8-bit value across bits 7:0, not
    eight independent re-readings of the same token)."""
    spans = []
    i, n = 0, len(bit_cols)
    while i < n:
        v = (reset_values[i] or "").strip()
        if not v or v in ("Reserved", "Res."):
            i += 1
            continue
        j = i
        while j + 1 < n and (reset_values[j + 1] or "").strip() == v:
            j += 1
        hi, lo = bit_cols[i][1], bit_cols[j][2]
        spans.append((hi, lo, v))
        i = j + 1
    return spans


def _reset_bit_map(bit_cols: list[tuple[int, int, int]], reset_values: list[str]) -> dict[int, str]:
    """Per-INDIVIDUAL-bit `{bit_position: '0'/'1'/'X'}` from the reset row --
    the common representation both reset-row styles (per-bit digits, or a
    hex constant spanning a run of columns) reduce to, so field-reset
    slicing and whole-register assembly both work off one thing. A span
    whose value is a hex token is decoded and distributed across its own
    bit width (handles both a genuinely grouped header column like "31-24"
    and a hex token merge-repeated across several single-bit columns); a
    span whose value is a plain 0/1/X digit repeats that digit across its
    width. Anything else (unrecognized/field-label text) contributes
    nothing -- never guessed."""
    bitmap: dict[int, str] = {}
    for hi, lo, v in _consolidate_reset_spans(bit_cols, reset_values):
        if HEX_TOKEN_RE.match(v):
            value = int(re.sub(r"\s+", "", v), 16)
            for bit in range(lo, hi + 1):
                bitmap[bit] = str((value >> (bit - lo)) & 1)
        elif SIMPLE_BIT_VALUE_RE.match(v):
            digit = v.upper()
            for bit in range(lo, hi + 1):
                bitmap[bit] = digit
    return bitmap


def _assemble_register_reset(bit_cols: list[tuple[int, int, int]], reset_values: list[str]) -> tuple[str, dict]:
    """Returns `(reset_value, bitmap)`. `reset_value` is `""` when the reset
    row yields nothing usable at all (no reset row, or nothing but
    field-label garbage); a clean `"0x%0*X"` when every bit resolves to 0/1
    (a reserved/absent bit counts as 0, per REGISTER_RESET_FIX.md -- not a
    guess, since an undocumented bit reading as 0 is the universal HW
    convention already relied on elsewhere in this codebase); or the raw
    MSB->LSB bit-string with 'X' preserved if any bit is genuinely
    undefined -- never faked into a hex."""
    bitmap = _reset_bit_map(bit_cols, reset_values)
    if not bitmap:
        return "", bitmap
    max_bit = max(hi for _, hi, _ in bit_cols)
    min_bit = min(lo for _, _, lo in bit_cols)
    bits_msb_to_lsb = [bitmap.get(b, "0") for b in range(max_bit, min_bit - 1, -1)]
    if any(b == "X" for b in bits_msb_to_lsb):
        return "".join(bits_msb_to_lsb), bitmap
    value = int("".join(bits_msb_to_lsb), 2)
    hex_digits = max(1, (max_bit - min_bit + 1 + 3) // 4)
    return f"0x{value:0{hex_digits}X}", bitmap


def _field_reset(bitmap: dict[int, str], hi: int, lo: int) -> str:
    """A field's own reset bits, MSB->LSB, sliced from the register's
    bitmap. `""` if the field's whole range has no reset info at all
    (reserved/absent); a partially-covered range fills any gap bit as '0',
    matching the register-level convention."""
    bits = [bitmap.get(b) for b in range(hi, lo - 1, -1)]
    if all(b is None for b in bits):
        return ""
    return "".join(b if b is not None else "0" for b in bits)


def _clean_register_name(name: str) -> str:
    """Strips a trailing "(...)" qualifier and a trailing "reset value"
    phrase (in that order -- verified RM0490 artifact: "Reset value (ports
    other than A)" has the qualifier *after* "reset value"), then heals the
    space a line-wrapped underscore leaves behind ("GPIOx _CRL" ->
    "GPIOx_CRL"). Applied to a reset-row's own (often combined) name to
    find which already-emitted register it belongs to; may come back ""
    for a bare "Reset value" with nothing else in the cell."""
    collapsed = TRAILING_PAREN_RE.sub("", _collapse_ws(name))
    collapsed = TRAILING_RESET_VALUE_RE.sub("", collapsed)
    collapsed = re.sub(r"\s+_", "_", collapsed)
    return collapsed.strip()


def _find_offset_col(headers: list) -> int | None:
    for i, h in enumerate(headers):
        if _norm(h) in ("offset", "addr", "addr.", "address"):
            return i
    return None


def _find_register_col(headers: list) -> int | None:
    """Matches "Register", "Register name", and the occasional narrow-
    column artifact where a "Reset value" sub-label gets joined into the
    same header cell as "Register name" (e.g. "Register \\nname\\nreset
    value") -- the row-pairing logic below is unaffected either way, since
    it identifies a reset row by *cell content* ("Reset value"), not by a
    second header column."""
    for i, h in enumerate(headers):
        if _norm(h).startswith("register"):
            return i
    return None


def _name_key(name: str) -> str:
    """Normalizes a register name for `by_name` lookups -- the same
    whitespace-collapse and wrapped-underscore healing `_clean_register_name`
    applies, so a later reset row's cleaned name reliably finds the
    register a plain field row was already emitted under."""
    return re.sub(r"\s+_", "_", _collapse_ws(name))


def _parse_field_bits(bits: str) -> tuple[int, int]:
    if ":" in bits:
        hi, lo = bits.split(":")
        return int(hi), int(lo)
    return int(bits), int(bits)


def _apply_reset_to_fields(fields: list[dict], bitmap: dict[int, str]) -> None:
    """Fills in each field's own `reset` slice (MSB->LSB) from the
    register's bitmap, in place. A "Res." entry always keeps `reset == ""`
    (RESERVED_FIELDS_TASK.md: the blank reset cell for reserved bits is the
    literal fact, never guessed at from the bitmap even if it happened to
    have data there) -- only named fields get a bitmap-derived slice."""
    for field in fields:
        if field["name"] == "Res.":
            field["reset"] = ""
            continue
        hi, lo = _parse_field_bits(field["bits"])
        field["reset"] = _field_reset(bitmap, hi, lo)


def extract_register_map(headers: list, rows: list) -> dict | None:
    offset_idx = _find_offset_col(headers)
    register_idx = _find_register_col(headers)
    bit_cols = []
    for i, h in enumerate(headers):
        parsed = _parse_bit_header(h)
        if parsed is not None:
            bit_cols.append((i, parsed[0], parsed[1]))
    if offset_idx is None or register_idx is None or not bit_cols:
        return None

    registers: list[dict] = []
    by_name: dict[str, dict] = {}

    for row in rows:
        raw_name = _get(row, register_idx)
        collapsed = _collapse_ws(raw_name)
        bit_values = [_get(row, bi) for bi, _, _ in bit_cols]

        is_reset_named = bool(collapsed) and bool(RESET_VALUE_RE.search(collapsed))
        is_blank_continuation = (
            not collapsed and registers and _get(row, offset_idx) == registers[-1]["offset"]
        )

        if is_reset_named or is_blank_continuation:
            # Never emit a register whose name is (or reduces to) a "reset
            # value" label -- fold it into the register it belongs to
            # instead: by cleaned-name lookup first, else the immediately
            # preceding emitted register (handles a bare "Reset value" row,
            # which has no name of its own to look up).
            clean = _clean_register_name(raw_name) if is_reset_named else ""
            target = by_name.get(clean) if clean else None
            if target is None and registers:
                target = registers[-1]
            candidate_rv, bitmap = _assemble_register_reset(bit_cols, bit_values)

            if target is not None and candidate_rv:
                target["reset_value"] = candidate_rv
                _apply_reset_to_fields(target["fields"], bitmap)
                continue
            if clean:
                # Verified RM0008 artifact (TIMx_CR1 and similar narrow
                # timer registers): a SINGLE row carries both the register
                # name AND "reset value" in its own cell, with no separate
                # field row and no clean reset constant among its own bit
                # values (they're field-name/reset-digit pairs stacked in
                # one cell, e.g. "CKD \n[1:0]\n00") -- there is nothing to
                # fold into, so this becomes its own register under its
                # cleaned name instead of leaking "... Reset value" as-is.
                fields = _build_fields(bit_cols, bit_values)
                for field in fields:
                    field["reset"] = ""
                reg = {
                    "offset": _get(row, offset_idx),
                    "name": clean,
                    "fields": fields,
                    "reset_value": candidate_rv,
                }
                registers.append(reg)
                by_name[_name_key(clean)] = reg
            # else: blank name, nothing to fold into, nothing usable -> skip
            continue

        fields = _build_fields(bit_cols, bit_values)
        for field in fields:
            field["reset"] = ""
        reg = {
            "offset": _get(row, offset_idx),
            "name": raw_name.replace("\n", " "),
            "fields": fields,
            "reset_value": "",
        }
        registers.append(reg)
        by_name[_name_key(raw_name)] = reg

    if not registers:
        return None
    return {"registers": registers}


# ----------------------------------------------------------- alternate_function

CONFIG_VALUE_RE = re.compile(r"=\s*[“\"']?([\w:]+)[”\"']?")
ALT_FUNCTION_FIRST_COL = {"alternate function", "alternate functions", "alternate functions mapping"}
AF_COL_RE = re.compile(r"^af\d+$")


def _config_key(header: str, fallback: str) -> str:
    # A footnote marker can land *inside* the header, between "=" and the
    # quoted config value (verified: 'CAN_REMAP[1:0] = \n(2)"10"'), not just
    # at the start -- strip it wherever it appears, not only as a prefix.
    header = re.sub(r"\(\d+\)", "", header or "").replace("\n", " ")
    m = CONFIG_VALUE_RE.search(header)
    return m.group(1) if m else fallback


def extract_alternate_function(headers: list, rows: list) -> dict | None:
    if not headers:
        return None
    first_norm = _norm(_strip_footnote(headers[0]))

    # AFx-column ("pin") shape: header = Pin, AF0, AF1, ...
    af_idx = [i for i, h in enumerate(headers) if AF_COL_RE.match(_norm(h))]
    if af_idx and first_norm not in ("", None):
        pins = []
        for row in rows:
            pin = _strip_footnote(_get(row, 0))
            if not pin:
                continue
            functions = {headers[i]: _get(row, i) for i in af_idx if _get(row, i)}
            pins.append({"pin": pin, "functions": functions})
        if pins:
            return {"pins": pins}
        return None

    # Remap-table ("functions") shape: header = Alternate function, <config
    # A>, <config B>, ... -- e.g. "CAN_REMAP[1:0] = "00"" -> config key "00".
    if first_norm in ALT_FUNCTION_FIRST_COL and len(headers) >= 2:
        functions = []
        for row in rows:
            function = _strip_footnote(_get(row, 0))
            if not function:
                continue
            configs = {}
            for i in range(1, len(headers)):
                key = _config_key(headers[i], f"col{i}")
                configs[key] = _strip_footnote(_get(row, i))
            functions.append({"function": function, "configs": configs})
        if functions:
            return {"functions": functions}
    return None


# ------------------------------------------------------------- interrupt_vector

def extract_interrupt_vector(headers: list, rows: list) -> dict | None:
    idx = {
        "position": _col(headers, "position"),
        "priority": _col(headers, "priority"),
        "acronym": _col(headers, "acronym"),
        "description": _col(headers, "description"),
        "address": _col(headers, "address"),
    }
    if idx["position"] is None or idx["address"] is None:
        return None
    entries = []
    for row in rows:
        entries.append({
            "position": _get(row, idx["position"]),
            "priority": _get(row, idx["priority"]),
            "acronym": _get(row, idx["acronym"]),
            "description": _get(row, idx["description"]),
            "address": _get(row, idx["address"]),
        })
    if not entries:
        return None
    return {"entries": entries}


# ------------------------------------------------------------------ memory_map

_MEMORY_MAP_ROLES = {
    "type": "bus", "bus": "bus",
    "boundary address": "boundary", "base address": "boundary",
    "size": "size",
    "memory area": "area", "peripheral": "area", "area": "area",
    "register description": "register_desc", "peripheral register map": "register_desc",
}


def extract_memory_map(headers: list, rows: list) -> dict | None:
    idx: dict[str, int] = {}
    for i, h in enumerate(headers):
        role = _MEMORY_MAP_ROLES.get(_norm(h))
        if role and role not in idx:
            idx[role] = i
    if "boundary" not in idx:
        return None
    regions = []
    for row in rows:
        regions.append({
            "bus": _get(row, idx.get("bus")),
            "boundary": _get(row, idx.get("boundary")),
            "size": _get(row, idx.get("size")),
            "area": _get(row, idx.get("area")),
            "register_desc": _get(row, idx.get("register_desc")),
        })
    if not regions:
        return None
    return {"regions": regions}


# ------------------------------------------------------------------- parameter

_PARAMETER_ROLES = {
    "symbol": "symbol", "parameter": "parameter",
    "conditions": "conditions", "condition": "conditions", "test conditions": "conditions",
    "min": "min", "typ": "typ", "typical": "typ", "max": "max",
    "unit": "unit", "units": "unit",
}


def _resolve_parameter_roles(headers: list, first_row: list) -> dict:
    """Column roles resolved from `headers` first; a role missing there
    (e.g. a merged 2-row header like "Symbol,Parameter,Limits,Limits,Unit"
    where the real "Min"/"Max" sub-labels are the first *data* row, since
    the shared table-assembly logic always treats physical row 0 as the
    header) is filled in from `first_row` if it's itself clearly a
    sub-header (its own cells are role keywords) -- read-only, `first_row`
    is never altered or removed from the output."""
    roles: dict[str, int] = {}
    for i, h in enumerate(headers):
        role = _PARAMETER_ROLES.get(_norm(h))
        if role and role not in roles:
            roles[role] = i
    for i, cell in enumerate(first_row or []):
        role = _PARAMETER_ROLES.get(_norm(cell))
        if role and role not in roles:
            roles[role] = i
    return roles


def extract_parameter(headers: list, rows: list) -> dict | None:
    if not rows:
        return None
    roles = _resolve_parameter_roles(headers, rows[0])
    if "symbol" not in roles and "parameter" not in roles:
        return None
    parameters = []
    for row in rows:
        parameters.append({
            "symbol": _get(row, roles.get("symbol")),
            "parameter": _get(row, roles.get("parameter")),
            "conditions": _get(row, roles.get("conditions")),
            "min": _get(row, roles.get("min")),
            "typ": _get(row, roles.get("typ")),
            "max": _get(row, roles.get("max")),
            "unit": _get(row, roles.get("unit")),
        })
    if not parameters:
        return None
    return {"parameters": parameters}


# -------------------------------------------------------------- feature_matrix

def extract_feature_matrix(headers: list, rows: list) -> dict | None:
    if len(headers) < 2:
        return None
    variants = list(headers[1:])
    features = []
    for row in rows:
        feature = _get(row, 0)
        if not feature:
            continue
        values = {variants[i - 1]: _get(row, i) for i in range(1, len(headers))}
        features.append({"feature": feature, "values": values})
    if not features:
        return None
    return {"variants": variants, "features": features}


# ---------------------------------------------------------------------- generic

EXTRACTORS = {
    "register_map": extract_register_map,
    "alternate_function": extract_alternate_function,
    "interrupt_vector": extract_interrupt_vector,
    "memory_map": extract_memory_map,
    "parameter": extract_parameter,
    "feature_matrix": extract_feature_matrix,
}


def extract_semantic(semantic_type: str, headers: list, rows: list) -> tuple[str, dict]:
    """Returns the possibly-downgraded `(semantic_type, semantic)` pair.
    If `semantic_type` isn't `generic` but its extractor can't confidently
    build the object, downgrades to `("generic", {})` rather than leaving
    a `semantic_type` with an empty/partial `semantic` body."""
    extractor = EXTRACTORS.get(semantic_type)
    if extractor is None:
        return "generic", {}
    result = extractor(headers, rows)
    if result is None:
        return "generic", {}
    return semantic_type, result
