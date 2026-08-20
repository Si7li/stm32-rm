"""rmerrata — deterministic ST STM32 errata-sheet extractor.

Extracts every errata entry in an ESxxxx device-errata PDF into a selective-RAG
schema (per-entry chunks with metadata filters, citations, and a group overview).
Deterministic and offline: pdfplumber lattice/word extraction plus positional
evidence from the PDF — no LLM anywhere in the pipeline.
"""

from rmerrata.extractor import EXTRACTOR_VERSION as __version__

__all__ = ["extractor", "rag_utils", "validate", "regression", "report"]