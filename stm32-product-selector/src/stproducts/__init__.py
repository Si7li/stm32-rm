"""stproducts -- rebuild ST product-selector spreadsheets from ST's own data.

The nine workbooks in ``product_selector/`` are ST product-selector exports
that were edited by hand. This package refetches the authoritative rows from
ST's public selector API, writes corrected workbooks *alongside* the
originals, and emits a diff report saying exactly which hand-entered cells
were wrong.

Deterministic. No LLM anywhere in the pipeline.
"""

__version__ = "0.1.0"
