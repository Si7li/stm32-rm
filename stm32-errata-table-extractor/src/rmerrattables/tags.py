"""Keyword-derived tags for each errata table (mirrors rmtables.tags).

Sources: a curated keyword->tag map scoped to errata content, slugified
REGNAME tokens in the caption/headers (e.g. "DIE_ID"), and significant
fallback words from the caption when the other sources produced nothing.
Deduped and sorted; an empty tag list is acceptable (pure-numeric tables).
"""

from __future__ import annotations

import re

_KEYWORD_TAGS: dict[str, list[str]] = {
    "summary of device limitations": ["errata-summary", "limitation"],
    "summary of device errata": ["errata-summary"],
    "device summary": ["device-summary"],
    "device variants": ["device-variants", "silicon-revision"],
    "document revision history": ["revision-history", "document-history"],
    "revision history": ["revision-history"],
    "documentation erratum": ["documentation-erratum"],
    "silicon revision": ["silicon-revision"],
    "applicability": ["applicability"],
    "limitation": ["limitation"],
    "workaround": ["workaround"],
    "status": ["status"],
    "errata": ["errata"],
    "silicon": ["silicon"],
    "part number": ["part-number"],
    "reference": ["reference"],
    "date": ["date"],
    "version": ["version"],
    "changes": ["changes"],
    "memory": ["memory"],
    "flash": ["flash"],
    "clock": ["clock"],
    "interrupt": ["interrupt"],
    "reset": ["reset"],
    "usb": ["usb"],
    "uart": ["uart"],
    "i2c": ["i2c"],
    "spi": ["spi"],
    "adc": ["adc"],
    "dac": ["dac"],
    "rtc": ["rtc"],
    "dma": ["dma"],
    "rng": ["rng"],
    "debug": ["debug"],
    "power": ["power"],
    "differential": ["differential"],
}

_STOPWORDS = frozenset("""
a an and are as at be by for from in is it of on or that the this to was with
""".split())

# An all-caps register/identifier token, e.g. "DIE_ID", "DBGMCU".
REGNAME_RE = re.compile(r"^[A-Z][A-Z0-9_]{2,}$")
WORD_RE = re.compile(r"[A-Za-z0-9_]+")
FALLBACK_MIN_LEN = 3


def slugify(text: str) -> str:
    text = text.strip().lower().replace("_", "-")
    text = re.sub(r"[^a-z0-9]+", "-", text)
    return text.strip("-")


def _keyword_hits(*texts: str) -> set[str]:
    haystack = " ".join(texts).lower()
    hits: set[str] = set()
    for phrase, mapped in _KEYWORD_TAGS.items():
        if phrase in haystack:
            hits.update(mapped)
    return hits


def _regname_tokens(*texts: str) -> set[str]:
    found: set[str] = set()
    for text in texts:
        for word in WORD_RE.findall(text or ""):
            if REGNAME_RE.match(word):
                found.add(slugify(word))
    return found


def _fallback_words(caption: str) -> set[str]:
    words: set[str] = set()
    for word in WORD_RE.findall(caption or ""):
        if len(word) > FALLBACK_MIN_LEN and word.lower() not in _STOPWORDS:
            words.add(slugify(word))
    return words


def build_tags(caption: str, section_title: str, headers: list) -> list[str]:
    """Sorted, de-duplicated tags. Empty is allowed and meaningful."""
    tags: set[str] = set()
    tags |= _keyword_hits(caption, section_title or "", " ".join(headers))
    tags |= _regname_tokens(caption, " ".join(headers))
    if not tags:
        tags |= _fallback_words(caption)
    tags.discard("")
    return sorted(tags)