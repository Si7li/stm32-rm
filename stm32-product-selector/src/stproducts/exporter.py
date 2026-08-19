"""Per-workbook JSON export: ``values``, ``descriptions`` and ``notes``.

One JSON file per output workbook (all 156, local and discovered alike),
matching the workbook's name. ``values`` is always keyed per part so no
per-part fact is lost; ``descriptions`` is a hand-curated plain-English map
keyed by :attr:`stproducts.api.Column.key`, with ST's own rendered label as
the fallback; ``notes`` tells a reader how to interpret the value -- which
source settled it, why it has several values, and what evidence sits behind
an ``AMBIGUOUS`` cell.
"""

from __future__ import annotations

from collections import Counter

from .api import Grid
from .compose import ComposedSheet
from .fieldmap import spec_for
from .provenance import AMBIGUOUS, API, DATASHEET, DERIVED, UNAVAILABLE
from .values import is_blank

#: Plain-English descriptions, keyed by the API column key (the same string
#: used in the workbooks and in ``corrections.json``). Anything not listed
#: falls back to ST's own composed label from the grid metadata.
PARAMETER_DESCRIPTIONS: dict[str, str] = {
    "A/D Converters 12-bit | Number of A/D Converters typ": "Number of 12-bit analogue-to-digital converters (typical)",
    "A/D Converters 12-bit | Number of Channels typ": "Total 12-bit ADC input channels (typical)",
    "A/D Converters 14-bit | Number of A/D Converters typ": "Number of 14-bit analogue-to-digital converters (typical)",
    "A/D Converters 14-bit | Number of Channels typ": "Total 14-bit ADC input channels (typical)",
    "A/D Converters 16-bit | Number of A/D Converters typ": "Number of 16-bit analogue-to-digital converters (typical)",
    "A/D Converters 16-bit | Number of Channels typ": "Total 16-bit ADC input channels (typical)",
    "Additional Interfaces": "Extra communication interfaces beyond the counted USART / UART / I2C / SPI sets",
    "Advanced Motor Control Timers": "Number of timers with advanced motor-control capability (typical)",
    "Buy On Line": "Whether ST sells the part through its online store",
    "CAN (2.0)": "Number of classic CAN 2.0B controllers (bxCAN) (typical)",
    "CAN (FD)": "Number of CAN-FD-capable controllers (FDCAN) (typical)",
    "CCM RAM (I/D) (kByte) typ": "Core-coupled memory (instruction and data) in KiB (typical)",
    "Co-Processor frequency (MHz) max": "Maximum clock frequency of the co-processor in MHz",
    "Co-Processor type": "Type of secondary processor core(s) on the device",
    "Comparator": "Number of built-in analogue comparators (typical)",
    "Connectivity supported": "Which communication standards the device implements",
    "Core": "Processor core family",
    "Cryptography": "Hardware cryptographic acceleration features",
    "D/A Converters (12-bit) typ": "Number of 12-bit digital-to-analogue converters (typical)",
    "DRAM support typ": "External DRAM types the memory interface supports",
    "Data E2PROM (B) nom": "Embedded EEPROM capacity in bytes (nominal)",
    "Display controller": "Which display interfaces (LCD-TFT, MIPI-DSI, ...) are supported",
    "Dual-bank Flash": "Whether the Flash memory is organised in two banks",
    "Ethernet": "Ethernet MAC interfaces supported and their speed",
    "Ethernet ports typ": "Number of Ethernet MAC ports (typical)",
    "External Memory Interfaces": "External memory bus types (FSMC / FMC / Octo-SPI, ...)",
    "FPU": "Floating-point unit(s) present",
    "Flash Size (kB) (Prog)": "Program memory capacity in KiB",
    "Flash Support typ": "External Flash memory types supported",
    "General Description": "ST's one-paragraph marketing summary of the device",
    "Graphic accelerator": "Graphics acceleration hardware present",
    "I/Os (High Current)": "Number of pins rated for high sink/source current (typical)",
    "I2C typ": "Number of I2C interfaces (typical)",
    "I2S typ": "Number of I2S audio interfaces (typical)",
    "I3C typ": "Number of I3C interfaces (typical)",
    "ITCM/DTCM RAM (kB)": "Tightly-coupled instruction/data RAM in KiB",
    "Integrated op-amps": "Number of integrated operational amplifiers (typical)",
    "Junction Temperature (°C) max": "Maximum junction temperature in °C",
    "Junction Temperature (°C) min": "Minimum junction temperature in °C",
    "L1 Cache (kB) typ": "Level-1 cache size in KiB (typical)",
    "L2 Cache (kB) typ": "Level-2 cache size in KiB (typical)",
    "LIN-UART typ": "Number of LIN-capable UART interfaces (typical)",
    "Longevity Commitment (yr) typ": "Years ST commits to keeping the part available (typical)",
    "Longevity Starting Date": "Date the longevity commitment begins to count from",
    "Marketing Status": "Lifecycle state of the part at ST",
    "NPU AI/NN Hardware Accelerator": "Neural-network accelerator present",
    "Number of A/D Converters (10-bit Channels) typ": "ADC channels a 10-bit converter can reach (typical)",
    "Number of A/D Converters (12-bit Channels) typ": "ADC channels a 12-bit converter can reach (typical)",
    "Number of Cores nom": "Number of CPU cores (nominal)",
    "On-chip SRAM (kB) typ": "Embedded static RAM capacity in KiB (typical)",
    "Operating Frequency (MHz)": "Maximum CPU clock frequency in MHz",
    "Operating Temperature (°C) max": "Maximum operating ambient temperature in °C",
    "Operating Temperature (°C) min": "Minimum operating ambient temperature in °C",
    "Other timer functions": "Timer features beyond the counted general-purpose set",
    "Output Power (dBm) (Step) typ": "Number of configurable RF output-power steps (typical)",
    "Output Power (dBm) max": "Maximum RF output power in dBm",
    "Output Power (dBm) min": "Minimum RF output power in dBm",
    "PCIe": "Number of PCIe interfaces",
    "PCIe type": "PCIe generation and lane configuration",
    "Package": "Package type(s) the part is offered in",
    "Part Number": "ST orderable part number",
    "RAM Size (kB)": "Embedded RAM capacity in KiB",
    "RF frequency (MHz) typ": "Radio operating frequency band(s) in MHz",
    "RX current (mA) typ": "Radio receiver current draw in mA (typical)",
    "RX sensitivity (dBm) typ": "Radio receiver sensitivity in dBm (typical)",
    "SMPS": "Switched-mode power-supply features",
    "SPI typ": "Number of SPI interfaces (typical)",
    "Secure Boot spec": "Whether a secure-boot specification is supported",
    "Security Functions": "Security features beyond cryptography (secure storage, tamper, ...)",
    "Standby Current (µA) typ": "Current draw in standby / lowest-power mode in µA (typical)",
    "Supply Current (µA) (@ Lowest Power) typ": "Current draw at the lowest-power setting in µA (typical)",
    "Supply Current (µA) (Run Mode (per MHz)) typ": "Current draw per MHz while running, in µA/MHz (typical)",
    "Supply Voltage (V) max": "Maximum supply voltage in V",
    "Supply Voltage (V) min": "Minimum supply voltage in V",
    "TRNG typ": "Hardware true random-number generator present (typical)",
    "TX current (mA) (@ 0dBm) max": "Radio transmit current at 0 dBm in mA (maximum)",
    "Target Application": "Applications ST positions the part for",
    "Timers (16-bit) typ": "Number of 16-bit timers (typical)",
    "Timers (32-bit) typ": "Number of 32-bit timers (typical)",
    "Timers (8-bit) typ": "Number of 8-bit timers (typical)",
    "Touch sensing FW library": "Whether the touch-sensing firmware library is supported",
    "UART typ": "Number of UART interfaces (typical)",
    "USART typ": "Number of USART interfaces (typical)",
    "USB 2.0 typ": "Number of USB 2.0 interfaces (typical)",
    "USB 3.0": "Number of USB 3.0 interfaces",
    "USB Type": "Which USB roles / interfaces the device supports",
    "Video HW accelerator": "Video-processing hardware present",
}


def _sentence(text: str) -> str:
    """'lowercase text.' -> 'Lowercase text.' -- one merged sentence."""
    text = text.strip().rstrip(".").strip()
    return text[:1].upper() + text[1:] + "."


def _note_for(key: str, grid: Grid, composed: ComposedSheet) -> str:
    cells = [
        cp.cells[key] for cp in composed.parts.values() if key in cp.cells
    ]
    if not cells:
        return "No value in this file."
    n = len(composed.parts)
    tokens = Counter(c.token for c in cells)
    sentences: list[str] = []

    if len(tokens) == 1:
        (token,) = tokens
        if token in (DATASHEET, DERIVED):
            sources = sorted({c.source for c in cells if c.source})
            source_txt = f", read from {', '.join(sources)}" if sources else ""
            sentences.append(f"{token} for all {n} parts{source_txt}.")
        elif token == AMBIGUOUS:
            sentences.append(
                "AMBIGUOUS for all {0} parts: the datasheet carries related "
                "information but does not say which value ST publishes, so the "
                "API value above is written.".format(n)
            )
        elif token == API:
            sentences.append(f"API for all {n} parts.")
        else:  # UNAVAILABLE
            sentences.append(
                f"UNAVAILABLE for all {n} parts: neither the datasheet nor "
                "ST's selector carries a value."
            )
    else:
        distribution = ", ".join(f"{tokens[t]} {t}" for t in sorted(tokens))
        sentences.append(f"Provenance across {n} parts: {distribution}.")
        sources = sorted(
            {c.source for c in cells if c.token in (DATASHEET, DERIVED) and c.source}
        )
        if sources:
            sentences.append(f"Datasheet readings come from {', '.join(sources)}.")

    reason = spec_for(key).reason
    if reason:
        sentences.append(_sentence(reason))

    conditions = sorted(
        {c.conditions for c in cells if c.token == AMBIGUOUS and c.conditions}
    )
    if conditions:
        sentences.append("Datasheet evidence: " + " ".join(conditions))

    column = grid.by_key().get(key)
    if column is not None and column.is_list:
        sentences.append(
            "A single part can carry several such values (shown comma-separated here)."
        )

    values = {c.value for c in cells if not is_blank(c.value)}
    if not values:
        pass
    elif len(values) == 1:
        sentences.append("The value is the same for every populated part.")
    else:
        sentences.append("The value differs across parts and is listed per part.")

    return " ".join(sentences)


def export_sheet_json(
    document: str, grid: Grid, layout_keys: list[str], composed: ComposedSheet
) -> dict:
    """The ``document``/``values``/``descriptions``/``notes`` exports for one
    output workbook.

    ``values`` is always keyed per part (``{part: written value}``), so a
    parameter that differs across parts keeps every per-part fact. Values are
    the cells exactly as written to the xlsx (``ComposedCell.value``).
    """
    descriptions = {
        key: PARAMETER_DESCRIPTIONS.get(key, grid.by_key().get(key).label)
        for key in layout_keys
        if grid.by_key().get(key) is not None
    }
    values: dict[str, dict[str, str]] = {}
    notes: dict[str, str] = {}
    part_order = [p for p in grid.part_numbers if p in composed.parts]
    for key in layout_keys:
        values[key] = {
            part: composed.parts[part].cells[key].value
            for part in part_order
            if key in composed.parts[part].cells
        }
        notes[key] = _note_for(key, grid, composed)
    return {
        "document": document,
        "level_id": grid.level_id,
        "level_title": grid.level_title,
        "values": values,
        "descriptions": descriptions,
        "notes": notes,
    }