"""Metadata derivation for errata sheets.

Everything here is deterministic and read from the PDF itself: the ESxxxx
document number and revision come from the page footer (or the revision
table), the products from the page-1 running header, the STM32 family from
the product tokens, and the url_pdf from the input filename (the official
st.com errata_sheet URL). No field is invented: absent evidence stays empty
or falls back to the sanitized filename stem, logged at WARNING.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

logger = logging.getLogger("rmerrattables.metadata")

# Footer shape ST prints on every page: "ES0676 - Rev 2 - March 2026".
_FOOTER_RE = re.compile(r"\b(ES\d{4})\s*-\s*Rev\s+(\d+)\b")

# Page-1 running header carries the products, e.g.
# "STM32C531xx STM32C532xx STM32C542xx".
_PRODUCT_RE = re.compile(r"^STM32([A-Z0-9][A-Za-z0-9]*[A-Za-z])(?:\s|$)")
_STEM_UNSAFE_RE = re.compile(r"[^A-Za-z0-9._-]")


def _sanitize(text: str) -> str:
    return _STEM_UNSAFE_RE.sub("", (text or "").strip())


def _rev_part(rev: str) -> str:
    compact = re.sub(r"\s+", "", (rev or "").strip())
    if not compact:
        return ""
    if not re.match(r"(?i)^rev", compact):
        compact = "Rev" + compact
    return _sanitize(compact) or ""


def derive_metadata(pdf, input_path: str, overrides: dict | None = None) -> dict:
    """Read document id/rev from the footers, products from page 1."""
    overrides = overrides or {}
    footer_doc, footer_rev = _scan_footer(pdf)
    doc_id = overrides.get("name_datasheet") or footer_doc
    rev = overrides.get("rev") or _revision(pdf, footer_rev)
    products = overrides.get("references") or _products(pdf)
    family = overrides.get("family") or _family(products)
    url_pdf = overrides.get("url_pdf") or _url_pdf(input_path)
    if not doc_id:
        logger.warning("could not derive ESxxxx doc id; %s fallback",
                       "using --name-datasheet" if overrides.get("name_datasheet")
                       else "use --name-datasheet")
    return {
        "name_datasheet": doc_id or "",
        "rev": rev,
        "url_pdf": url_pdf,
        "references": products,
        "package": "",
        "family": family,
        "core": "",
        "frequency": "",
    }


def _scan_footer(pdf) -> tuple[str, str]:
    """doc_id / rev-number from the first footer found over the first pages."""
    for page in pdf.pages[:6]:
        text = page.extract_text() or ""
        for line in text.splitlines():
            m = _FOOTER_RE.search(line)
            if m:
                return m.group(1), f"Rev {int(m.group(2))}"
    return "", ""


def _revision(pdf, footer_rev: str) -> str:
    if footer_rev:
        return footer_rev
    for page in pdf.pages:
        text = page.extract_text() or ""
        for line in text.splitlines():
            m = re.search(r"\bRevision\s+([A-Za-z0-9.]+)\b", line, re.IGNORECASE)
            if m:
                return f"Rev {m.group(1)}"
    return ""


def _products(pdf) -> str:
    for page in pdf.pages[:2]:
        text = page.extract_text() or ""
        for line in text.splitlines():
            line = line.strip()
            if not line.startswith("STM32"):
                continue
            toks = re.findall(r"STM32[A-Z]\d[A-Za-z0-9]*", line)
            if toks:
                return ", ".join(toks)
    return ""


def _family(products: str) -> str:
    m = re.search(r"STM32([A-Z]\d)", products or "")
    return m.group(1) if m else ""


def _url_pdf(input_path: str) -> str:
    name = Path(input_path).name
    return f"https://www.st.com/resource/en/errata_sheet/{name}"


def doc_stem(document: str, rev: str, pdf_path: str | None = None) -> str:
    """`{ESxxxx}_{RevN}` output stem, shared by the combined file and every
    per-table split file (FILENAME_SCHEME convention from the sibling)."""
    doc_part = _sanitize(document or "")
    if not doc_part:
        fallback = Path(pdf_path).stem if pdf_path else ""
        doc_part = _sanitize(fallback) or "UNKNOWN"
        logger.warning("doc_stem: document missing; fell back to %r", doc_part)
    rev_part = _rev_part(rev)
    if not rev_part:
        logger.warning("doc_stem: rev missing for %r; omitting revision segment", doc_part)
        return doc_part
    return f"{doc_part}_{rev_part}"