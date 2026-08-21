"""Which source owns which column -- configuration, not code.

Keyed by the API column key (:attr:`stproducts.api.Column.key`), which is the
same string across every selector sheet. That is what makes the 52-column
high-performance file and the 32-column STM8 file configuration: they reuse
the entries below and pick up whatever else they need from the default.

The reference sheet for the tiering is STM32F2 series (36 columns), per §3 of
the build spec.

A note on three fields the spec tiers as ``DATASHEET``
------------------------------------------------------
``USB Type``, ``Additional Interfaces`` and ``Other timer functions`` are
set-valued, and ST's vocabulary for them is wider than the summary table can
speak to: the table has ``SDIO`` / ``Camera interface`` / ``Ethernet`` /
``FSMC`` rows, while ST also writes ``SAI``, ``DFSDM``, ``S/PDIF``,
``HDMI CEC``, ``MIPI CSI-2``, ``ADF``, ``MDF`` and ``PLAY``; it has
``IWDG`` / ``WWDG`` / ``RTC`` rows, while ST also writes ``SysTick``, ``AWU``,
``Beeper``, ``LP timer`` and ``HR timer``.

Composing a value from only the rows the table carries would write a value
that is *incomplete* -- and would then report an override against ST on
essentially every part, burying the real findings.

So these use :func:`stproducts.extract.read_speakable_set`, which applies one
stated rule: the datasheet supplies the value when every token in play is one
the summary table can speak to, and otherwise steps back to ``AMBIGUOUS``
with the rows it did read recorded as evidence. This is the same
"never guess" fallback the spec asks for on the derived timer split. On the
F2 reference sheet that yields ``DATASHEET`` for ``Additional Interfaces``
and ``AMBIGUOUS`` for ``USB Type`` and ``Other timer functions``.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .provenance import AMBIGUOUS, API, DATASHEET, DERIVED


@dataclass(frozen=True)
class FieldSpec:
    """How one column is sourced."""

    tier: str
    #: Name of a reader registered in :mod:`stproducts.extract`.
    reader: str | None = None
    args: tuple = ()
    #: Why this tier -- surfaced in the README and the run report.
    reason: str = ""
    #: Name of an entry in :data:`stproducts.values.EQUIVALENCE`. Used only to
    #: decide whether the two sources *disagree*: the datasheet's ``LQFP64``
    #: and ST's ``LQFP 64 10x10x1.4 mm`` are the same package written two
    #: ways, and calling that an override would add one false row per part.
    equivalence: str | None = None
    #: Tokens that must appear in the matched row's label before the reading
    #: is accepted as datasheet evidence for *this* column.
    requires_row_tokens: tuple[str, ...] = ()
    #: Tokens whose presence in the row label disqualifies it for this column.
    forbids_row_tokens: tuple[str, ...] = ()
    #: Set when this column deliberately shares a source row with another.
    #: Sharing is otherwise rejected at import time -- see :func:`_build`.
    shares_source: bool = False

    def row_supports(self, row_label: str) -> bool:
        """Does the row this value came from actually speak to this column?

        Rows are matched by substring, so ``comm. interfaces | can`` finds a
        ``CAN FD`` row as readily as a classic ``CAN`` one. Without this check
        the value is attributed to whichever column asked first.
        """
        if not (self.requires_row_tokens or self.forbids_row_tokens):
            return True
        label = (row_label or "").casefold()
        if not label:
            # Nothing to check against: only safe when nothing was required.
            return not self.requires_row_tokens
        if any(t.casefold() not in label for t in self.requires_row_tokens):
            return False
        return all(t.casefold() not in label for t in self.forbids_row_tokens)


#: Columns the datasheet can settle outright.
_DATASHEET_FIELDS = {
    "Flash Size (kB) (Prog)": ("summary_number", ("flash memory in kbytes",)),
    "RAM Size (kB)": ("summary_number", ("sram in kbytes | system",)),
    "I2C typ": ("summary_number", ("comm. interfaces | i2c",)),
    # Packed SPI/I2S cells ("5/4") are positional; see read_spi.
    "SPI typ": ("summary_spi", ()),
    # The two CAN columns read the same row and are told apart by its label.
    # See _ROW_TOKENS: a plain "CAN" row is bxCAN 2.0B and says nothing about
    # CAN FD; an "FDCAN" row is the reverse.
    "CAN (2.0)": ("summary_number", ("comm. interfaces | can",)),
    "CAN (FD)": ("summary_number", ("comm. interfaces | can",)),
    # GPIO sub-rows aggregate: type rows sum, supply alternatives union.
    "I/Os (High Current)": ("summary_gpio_total", ()),
    "USART typ": ("summary_usart", ()),
    "UART typ": ("summary_uart", ()),
    # ST's selector carries no LPUART column at all; this one is appended by
    # the tool (see EXTRA_COLUMNS) and filled purely from the datasheet.
    "LPUART typ": ("summary_lpuart", ()),
    "Package": ("summary_package", ()),
    # 3/(2) in the SPI/(I2S) row means three SPIs, two of them I2S-capable.
    "I2S typ": ("summary_i2s", ()),
    "Core": ("cover_core", ()),
    "Operating Frequency (MHz)": ("cover_frequency", ()),
    "Supply Voltage (V) min": ("operating_conditions_voltage", ("min",)),
    "Supply Voltage (V) max": ("operating_conditions_voltage", ("max",)),
    "Operating Temperature (°C) min": ("summary_temperature", ("min",)),
    "Operating Temperature (°C) max": ("summary_temperature", ("max",)),
    # Converters / channels packed into one cell ("2/16"), mapped
    # positionally -- one reader, two columns, told apart by ``which``.
    "A/D Converters 12-bit | Number of A/D Converters typ": (
        "summary_adc_pair", ("12-bit", "converters"),
    ),
    "A/D Converters 12-bit | Number of Channels typ": (
        "summary_adc_pair", ("12-bit", "channels"),
    ),
    "A/D Converters 14-bit | Number of A/D Converters typ": (
        "summary_adc_pair", ("14-bit", "converters"),
    ),
    "A/D Converters 14-bit | Number of Channels typ": (
        "summary_adc_pair", ("14-bit", "channels"),
    ),
    "A/D Converters 16-bit | Number of A/D Converters typ": (
        "summary_adc_pair", ("16-bit", "converters"),
    ),
    "A/D Converters 16-bit | Number of Channels typ": (
        "summary_adc_pair", ("16-bit", "channels"),
    ),
    # ST's column carries the channel count (F105 "Yes\n2" -> 2).
    "D/A Converters (12-bit) typ": ("summary_dac", ()),
}

#: Row-label conditions, as ``column key -> (required tokens, forbidden)``.
#:
#: Substring row matching cannot distinguish ``Comm. interfaces | CAN`` from
#: ``Comm. interfaces | FDCAN``, so both CAN columns used to accept whichever
#: row appeared. On STM32F2 -- classic bxCAN only, no CAN FD anywhere in the
#: family -- that wrote ``CAN (FD) = 2`` for 26 parts and labelled it
#: ``DATASHEET``. A row saying "CAN" is not evidence about CAN FD, and a row
#: saying "FDCAN" is not evidence about how many 2.0B controllers there are;
#: whichever column the row does not speak to falls through to the API.
_ROW_TOKENS: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
    "CAN (2.0)": ((), ("fd",)),
    "CAN (FD)": (("fd",), ()),
}

#: Columns computed from datasheet values by a stated rule.
_DERIVED_FIELDS = {
    "Timers (8-bit) typ": (
        "derived_timer_width",
        (8,),
        "sums the timer counts the summary table annotates (8-bit) across "
        "all its timer rows -- type sub-rows aggregate, the first row alone "
        "undercounts; falls back to the comparison-table join when the "
        "summary carries no width annotations",
    ),
    "Timers (16-bit) typ": (
        "derived_timer_width",
        (16,),
        "sums the timer counts the summary table annotates (16-bit) across "
        "all its timer rows -- advanced-control, general-purpose, basic AND "
        "low-power; the old first-row/inventory read missed the low-power "
        "timers (G431 wrote 9 where ST sums to 10). Falls back to the "
        "comparison-table join when the summary carries no annotations. ST's "
        "selector does not always agree with its own annotated sums (H543: "
        "datasheet 10, selector 5); the document's statement is written and "
        "the diff reports the disagreement",
    ),
    "Timers (32-bit) typ": ("derived_timer_width", (32,), "as Timers (16-bit) typ"),
}

#: Set-valued columns; the datasheet answers only when it knows every token.
_SPEAKABLE_SETS = {
    "Additional Interfaces": ("additional_interfaces",),
    "USB Type": ("usb_type",),
    "Other timer functions": ("other_timer_functions",),
}

#: The datasheet has candidate tables but does not say which row ST publishes.
_AMBIGUOUS_FIELDS = {
    "Supply Current (µA) (@ Lowest Power) typ": (
        "supply_current",
        ("lowest power",),
        "the datasheet gives current consumption only as tables across "
        "temperature, voltage and mode; nothing states which row ST publishes",
    ),
    "Supply Current (µA) (Run Mode (per MHz)) typ": (
        "supply_current",
        ("run mode",),
        "as above",
    ),
}

#: The datasheet makes no such assertion at all.
_API_FIELDS = {
    "Part Number": "an index, not a claim about the part",
    "General Description": "marketing copy; the datasheet has no equivalent field",
    "Marketing Status": "lifecycle state, varies per part within one family",
}

#: Absence of a mention is not evidence of absence -- take the API value.
_API_VIA_ABSENCE = (
    "Dual-bank Flash",
    "Comparator",
    "Cryptography",
    "Security Functions",
)

ABSENCE_REASON = (
    "API via absence: the datasheet not mentioning this is not evidence the "
    "part lacks it"
)

DEFAULT_SPEC = FieldSpec(
    tier=API, reason="no datasheet rule defined for this column"
)


#: Columns the tool appends that ST's selector API does not carry. They have
#: no Column metadata, hold '-' where the datasheet is silent, and are
#: appended after the API's own extras -- plan_columns, the writer and the
#: diff all treat them through the same (key, None) path.
EXTRA_COLUMNS = ("LPUART typ",)


#: Columns whose two sources use different notation for the same fact.
_EQUIVALENCE = {"Package": "package", "Core": "core"}


class AliasedSourceError(RuntimeError):
    """Two columns read the same source without being told apart."""


def _check_no_silent_aliasing(specs: dict[str, FieldSpec]) -> None:
    """Reject two columns reading one source with nothing to separate them.

    This is the structural half of the CAN (FD) fix. Distinguishing the two
    CAN columns by row label repairs the instance; this rejects the *class*,
    at import time, so the next duplicated needle cannot reach a workbook.

    Sharing is legal when the specs are mutually exclusive -- some token one
    requires is forbidden by the other, so no single row label can satisfy
    both -- or when a column opts in explicitly with ``shares_source``.
    """
    by_source: dict[tuple, list[str]] = {}
    for key, spec in specs.items():
        if spec.reader:
            by_source.setdefault((spec.reader, tuple(spec.args)), []).append(key)

    for (reader_name, args), keys in sorted(by_source.items()):
        if len(keys) < 2:
            continue
        for i, left in enumerate(keys):
            for right in keys[i + 1 :]:
                a, b = specs[left], specs[right]
                if a.shares_source or b.shares_source:
                    continue
                exclusive = set(a.requires_row_tokens) & set(b.forbids_row_tokens) or (
                    set(b.requires_row_tokens) & set(a.forbids_row_tokens)
                )
                if exclusive:
                    continue
                raise AliasedSourceError(
                    f"{left!r} and {right!r} both read {reader_name}{args!r} with "
                    "nothing to tell them apart, so the same value would be "
                    "asserted for both at DATASHEET provenance. Give them "
                    "disjoint requires/forbids row tokens in _ROW_TOKENS, or "
                    "set shares_source=True if they genuinely share an answer."
                )


def _build() -> dict[str, FieldSpec]:
    specs: dict[str, FieldSpec] = {}
    for key, (reader, args) in _DATASHEET_FIELDS.items():
        requires, forbids = _ROW_TOKENS.get(key, ((), ()))
        specs[key] = FieldSpec(
            DATASHEET, reader, args,
            equivalence=_EQUIVALENCE.get(key),
            requires_row_tokens=requires,
            forbids_row_tokens=forbids,
        )
    for key, (reader, args, reason) in _DERIVED_FIELDS.items():
        specs[key] = FieldSpec(DERIVED, reader, args, reason)
    for key, (reader,) in _SPEAKABLE_SETS.items():
        specs[key] = FieldSpec(
            DATASHEET, "read_speakable_set", (reader,),
            "datasheet supplies it only when every token in play is one the "
            "summary table can speak to; otherwise AMBIGUOUS with evidence",
        )
    for key, (reader, args, reason) in _AMBIGUOUS_FIELDS.items():
        specs[key] = FieldSpec(AMBIGUOUS, reader, args, reason)
    for key, reason in _API_FIELDS.items():
        specs[key] = FieldSpec(API, None, (), reason)
    for key in _API_VIA_ABSENCE:
        specs[key] = FieldSpec(API, None, (), ABSENCE_REASON)
    _check_no_silent_aliasing(specs)
    return specs


FIELD_MAP: dict[str, FieldSpec] = _build()


def spec_for(column_key: str) -> FieldSpec:
    return FIELD_MAP.get(column_key, DEFAULT_SPEC)


def coverage(column_keys: list[str]) -> dict[str, list[str]]:
    """Group a sheet's columns by the tier they are configured for."""
    grouped: dict[str, list[str]] = {}
    for key in column_keys:
        grouped.setdefault(spec_for(key).tier, []).append(key)
    return grouped
