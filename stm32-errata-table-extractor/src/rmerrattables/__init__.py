"""rmerrattables — deterministic table extractor for ST STM32 errata sheets.

Every table in an ESxxxx device-errata PDF (device summary, device variants,
the "Summary of device limitations" status matrix, document revision history)
becomes one record in a sidekick JSON document whose schema mirrors
`rmtables` exactly. Deterministic and offline: pdfplumber ruled-grid
extraction, captions above the grid, multi-page continuation merging, and
footnotes below — no LLM anywhere.
"""

__version__ = "0.1.0"

__all__ = ["metadata", "tables", "tags", "exporter", "validate"]