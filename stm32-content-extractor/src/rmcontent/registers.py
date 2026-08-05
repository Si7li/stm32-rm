"""The `semantic` block for register-description sections.

Roughly 40% of an ST reference manual's sections describe one register,
in a grammar rigid enough to parse deterministically. Counts across
RM0490 Rev 6:

| pattern | matches |
|---|---|
| `Address offset: 0x000` | 371 |
| `Reset value: 0x... / 0b...` | 357 |
| `Bit 18 DBG_SWEN: <description>` | 1,225 |
| `Bits 2:0 LATENCY[2:0]: <description>` | 473 |
| `Bits 31:19 Reserved, must be kept at reset value.` | 579 |
| `0: Debugger disabled` | 2,333 |

**Classification is conservative.** A section is
`semantic_type: "register_description"` only when it has exactly one
`Address offset:` line *and* at least one `Bit`/`Bits` field line;
everything else is `"generic"` with `semantic: {}`. This mirrors the
sibling project's classifier, for the same reason: for retrieval, a
wrong type is worse than a generic one.

**Reserved runs are fields.** They appear as `Res.` entries so `fields`
covers all 32 bits -- the decision `RESERVED_FIELDS_TASK.md` made for
register-map tables. That is what makes each register self-validating:
`check_bit_coverage` can then demand an exact partition of 31..0, and a
gap or overlap is a real parse bug rather than a missing reserved row.
"""

from __future__ import annotations

import logging
import re

from rmtables.headings import extract_register_name

from .markers import MARKER_RE

logger = logging.getLogger("rmcontent.registers")

ADDRESS_OFFSET_RE = re.compile(r"^Address offset:\s*(.*)$")
RESET_VALUE_RE = re.compile(r"^Reset value:\s*(.*)$")

# "Bit 18 ..." / "Bits 31:19 ...", and also the DISCONTIGUOUS form ST
# uses when a bitfield was widened into space left elsewhere in the word:
# "Bits 24, 14:12 OC2M[3:0]: Output compare 2 mode" and "Bits 21, 20, 6,
# 5, 4 TS[4:0]: Trigger selection" (RM0490 18.4.3/18.4.8). Reading only
# the first range there loses the field entirely -- the remainder no
# longer looks like a name -- which is what the bit-coverage check
# surfaced as "missing bits 21:20, 16, 6:4, 2:0" on every TIM SMCR/CCMR.
#
# The remainder is parsed separately because a bitfield NAME can itself
# contain a colon: "Bits 31:0 KEY[31:0]: FLASH key" defeats any single
# greedy pattern.
#
# The remainder may be empty: RM0486 73.14.47/48 print a bare `Bits
# 14:11` with `Reserved, must be kept at reset value.` wrapped onto the
# following line, which left those four bits uncovered.
# A bit number may be split by a space. Merging a superscript into its
# baseline line (see `lines.py`) perturbs pdfplumber's word splitting on
# that line, so RM0486 64.16.20 and RM0522 42.16.21 -- both I3C_TIMINGR0,
# whose description mentions "I2C" with a superscript 2 -- render their
# first field as `Bits 3 1 :24 SCLH_I2C[7:0]: ...`. This is the same
# rendering artifact `rmtables.captions.NUMBER_RE` already absorbs for a
# split table number ("Table 7 6."), and the spaces come back out in
# `_parse_field_head`.
_NUM = r"\d(?:\s?\d)?"
_RANGE = rf"{_NUM}(?:\s?:\s?{_NUM})?"
FIELD_RE = re.compile(rf"^Bits?\s+({_RANGE}(?:\s*,\s*{_RANGE})*)(?:\s+(\S.*))?$")

# "DBG_SWEN: Debug access software enable" or "KEY[31:0]: FLASH key".
# The identifier must carry an uppercase letter, and the colon after it
# is mandatory. Both are load-bearing: RM0490 18.4.12 opens with the
# prose line "Bit 31 of this register has two possible definitions
# depending on the value of UIFREMAP in TIMx_CR1 register:", which
# satisfies `FIELD_RE` and, under a looser name pattern, produced a
# phantom field named "of" at bit 31 -- caught by the bit-coverage check
# as an overlap. Only the two shapes ST actually prints are accepted:
# `NAME: description` and `Reserved, ...`.
#
# The description after the colon may be empty: RM0490 29.6.7 prints
# "Bit 23 NAK:" and "Bits 22:16 DEVADDR[6:0]:" with the text starting on
# the following line. What rejects the prose case is the colon coming
# immediately after the identifier, not the presence of a description.
#
# A name may also carry a templated index for a register that repeats
# across instances: RM0490 14.5.11 prints "Bits 31:24
# EXTI{4*(x-1)+3}[7:0]: EXTI{4*(x-1)+3} GPIO port selection".
_NAME = r"[A-Za-z_][A-Za-z0-9_]*(?:\{[^{}]*\}[A-Za-z0-9_]*)*(?:\[[^\]]*\])?"
NAME_DESC_RE = re.compile(rf"^((?=[^:]*[A-Z]){_NAME})\s*:\s*(.*)$")

RESERVED_RE = re.compile(r"^Res(?:erved|\.)?\b", re.IGNORECASE)
RESERVED_NAME = "Res."

# A value enumeration: "0: Debugger disabled", "000: Zero wait states",
# "0x3: ...", "Other: Reserved". The alternation is deliberately narrow --
# a bare identifier followed by a colon is a field heading, not a value,
# and prose like "KEY1: 0x4567 0123" must not be mistaken for one.
VALUE_RE = re.compile(r"^(\d+|0[xX][0-9A-Fa-f]+|[Oo]thers?)\s*:\s*(\S.*)$")

REGISTER_BITS = 32


def _strip_note(reset: str) -> tuple[str, str]:
    """Split `Reset value:`'s trailing parenthetical off as `reset_note`.

    RM0490 4.7.1 reads `Reset value: 0b0000 ... 0000 (the EMPTY bit is
    updated only by OBL. It is not affected by the system reset.)`, the
    parenthetical wrapping across two printed lines. It carries real
    information about when the reset value applies and is kept rather
    than discarded.
    """
    start = reset.find("(")
    if start == -1:
        return reset.strip(), ""
    note = reset[start + 1 :]
    if note.endswith(")"):
        note = note[:-1]
    return reset[:start].strip(), note.strip()


class _FieldBuilder:
    def __init__(self, bits: str, name: str, description: str):
        self.bits = bits
        self.name = name
        self.description_parts = [description] if description else []
        self.values: list[dict] = []
        self.seen_values = False

    def add_text(self, text: str) -> None:
        # A field whose bit range filled its printed line on its own gets
        # its name from the first continuation line, so a reserved run
        # written that way is still named `Res.` like every other.
        if not self.name and not self.description_parts and RESERVED_RE.match(text):
            self.name = RESERVED_NAME
        self.description_parts.append(text)

    def add_value(self, value: str, meaning: str) -> None:
        self.values.append({"value": value, "meaning": meaning})
        self.seen_values = True

    def build(self) -> dict:
        return {
            "bits": self.bits,
            "name": self.name,
            "description": " ".join(p.strip() for p in self.description_parts).strip(),
            "values": self.values,
        }


def _parse_field_head(raw_bits: str, rest: str | None) -> tuple[str, str, str] | None:
    """`("31:19", "Res.", "Reserved, must be kept at reset value.")`, or
    `None` when the line only looked like a field heading.

    A discontiguous field keeps its printed range list as `bits`
    (`"24, 14:12"`), normalized to one space after each comma.

    `rest` is `None` when the bit range fills the printed line on its own
    and its description wrapped. The field is still real -- the range is
    unambiguous -- so it is opened with an empty name and description,
    both of which the following lines then supply.
    """
    bits = ", ".join(re.sub(r"\s+", "", part) for part in raw_bits.split(","))
    if rest is None:
        return bits, "", ""
    if RESERVED_RE.match(rest):
        return bits, RESERVED_NAME, rest.strip()
    m = NAME_DESC_RE.match(rest)
    if m:
        return bits, m.group(1), m.group(2).strip()
    return None


def parse_register(section_title: str, content: str) -> dict | None:
    """Return the `semantic` block, or `None` if this is not a register
    description (which makes the section `generic` with `semantic: {}`)."""
    lines = content.split("\n")

    address_offset = ""
    offset_count = 0
    reset_value = ""
    reset_note = ""
    fields: list[_FieldBuilder] = []
    current: _FieldBuilder | None = None
    # A `Reset value:` parenthetical that wrapped: keep consuming lines
    # until its closing bracket arrives.
    pending_note = False

    for raw in lines:
        text = raw.strip()
        if not text or MARKER_RE.match(text):
            pending_note = False
            continue

        offset_match = ADDRESS_OFFSET_RE.match(text)
        head = FIELD_RE.match(text)
        parsed_head = _parse_field_head(head.group(1), head.group(2)) if head else None
        field_match = head if parsed_head else None

        if pending_note:
            # Bounded: a parenthetical that never closes gives up as soon
            # as the register's real grammar resumes, rather than
            # swallowing the rest of the section.
            if field_match or offset_match:
                pending_note = False
            else:
                reset_note = f"{reset_note} {text}".strip()
                if ")" in text:
                    reset_note = reset_note.rstrip(")").strip()
                    pending_note = False
                continue

        if parsed_head:
            bits, name, description = parsed_head
            current = _FieldBuilder(bits, name, description)
            fields.append(current)
            continue

        if offset_match:
            offset_count += 1
            if not address_offset:
                address_offset = offset_match.group(1).strip()
            continue

        m = RESET_VALUE_RE.match(text)
        if m:
            reset_value, reset_note = _strip_note(m.group(1))
            pending_note = m.group(1).count("(") > m.group(1).count(")")
            continue

        if current is not None:
            m = VALUE_RE.match(text)
            if m:
                current.add_value(m.group(1), m.group(2).strip())
                continue
            # Prose. Before the first enumeration it is the field
            # description continuing; after one it is a trailing caveat
            # ("This bit can be written only when the instruction cache is
            # disabled."), which is appended to the same description
            # rather than dropped -- `section_content` keeps it either
            # way, but a field missing its caveat is misleading.
            current.add_text(text)

    if not address_offset or not fields:
        return None

    # More than one `Address offset:` means the section documents several
    # registers under one heading, each with an unnumbered sub-heading of
    # its own -- RM0522 48.11.4 "Ethernet MAC and MMC registers" holds
    # about fifty in 167k characters, and chapter 49's ROM-table sections
    # hold a handful each. The single-register `semantic` shape cannot
    # represent that: it would merge every register's fields into one
    # list whose bits overlap several times over. Conservative by design,
    # exactly as for an unrecognized section: `generic` with `semantic:
    # {}` is better for retrieval than a confidently wrong block, and the
    # full prose is in `section_content` either way.
    if offset_count > 1:
        logger.debug(
            "section %r has %d Address offset lines; leaving it generic",
            section_title, offset_count,
        )
        return None

    return {
        "register": extract_register_name(section_title) or "",
        "address_offset": address_offset,
        "reset_value": reset_value,
        "reset_note": reset_note,
        "fields": [f.build() for f in fields],
    }


def _bit_ranges(bits: str) -> list[tuple[int, int]] | None:
    """`"24, 14:12"` -> `[(24, 24), (14, 12)]`; `None` if unparsable."""
    ranges: list[tuple[int, int]] = []
    for chunk in bits.split(","):
        parts = chunk.strip().split(":")
        try:
            if len(parts) == 1:
                n = int(parts[0])
                ranges.append((n, n))
            elif len(parts) == 2:
                ranges.append((int(parts[0]), int(parts[1])))
            else:
                return None
        except ValueError:
            return None
    return ranges or None


def declared_width(semantic: dict) -> int:
    """How many bits ST's own `Reset value:` says this register has.

    `Reset value: 0x0000` is a 16-bit register -- every STM32 timer
    control register is one -- while `0x0000 0000` and the 32-digit
    `0b...` form are 32-bit. Falls back to 32 when there is no parseable
    reset value.

    This does not replace the 31..0 check the spec asks for; that still
    runs. It separates "ST describes 16 bits because the register has
    16" from "the parse lost half a register", which is the difference
    between a report an operator can act on and 80 lines of noise.
    """
    reset = (semantic.get("reset_value") or "").strip()
    # The digit run may be split by spaces (`0x0000 0000`) and may be
    # followed by prose (`0xXXXX where X is factory-programmed`), so the
    # match is a prefix and the whitespace comes out of the capture.
    m = re.match(r"^0[xX]((?:[0-9A-Fa-fXx]+\s*)+)", reset)
    if m:
        return len(re.sub(r"\s+", "", m.group(1))) * 4
    m = re.match(r"^0[bB]((?:[01Xx]+\s*)+)", reset)
    if m:
        return len(re.sub(r"\s+", "", m.group(1)))
    return REGISTER_BITS


def check_bit_coverage(semantic: dict, width: int = REGISTER_BITS) -> str:
    """`""` when `fields` exactly partitions bits `width-1..0`, else a
    description of the gaps and/or overlaps.

    Reported, never silently emitted: this is the self-validating
    property that surfaced real parse bugs in the table project.
    """
    seen: dict[int, int] = {}
    overlaps: list[int] = []
    for f in semantic.get("fields") or []:
        ranges = _bit_ranges(f.get("bits", ""))
        if ranges is None:
            return f"unparsable bit range {f.get('bits')!r}"
        for hi, lo in ranges:
            if hi < lo:
                hi, lo = lo, hi
            for b in range(lo, hi + 1):
                seen[b] = seen.get(b, 0) + 1
                if seen[b] == 2:
                    overlaps.append(b)

    missing = [b for b in range(width) if b not in seen]
    above = sorted(b for b in seen if b >= width)

    problems = []
    if missing:
        problems.append(f"missing bits {_compact(missing)}")
    if overlaps:
        problems.append(f"overlapping bits {_compact(sorted(set(overlaps)))}")
    if above:
        problems.append(f"bits above {width - 1}: {_compact(above)}")
    return "; ".join(problems)


def _compact(bits: list[int]) -> str:
    """`[31, 30, 29, 19]` -> `"31:29, 19"`."""
    if not bits:
        return ""
    ordered = sorted(bits, reverse=True)
    runs: list[tuple[int, int]] = []
    start = prev = ordered[0]
    for b in ordered[1:]:
        if b == prev - 1:
            prev = b
            continue
        runs.append((start, prev))
        start = prev = b
    runs.append((start, prev))
    return ", ".join(str(hi) if hi == lo else f"{hi}:{lo}" for hi, lo in runs)
