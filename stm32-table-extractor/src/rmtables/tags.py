"""Keyword-derived tags for each table.

Combines four sources -- a curated keyword->tag map, slugified REGNAME
tokens found in the table name/headers, register names harvested from a
register-map table's "Register" column, and significant fallback words
from the table name -- then dedups and sorts. The keyword map and stopword
list are kept as editable data files (`data/keyword_tags.json`,
`data/stopwords.txt`) so coverage is tunable without touching this logic.
"""

from __future__ import annotations

import json
import os
import re

_DATA_DIR = os.path.join(os.path.dirname(__file__), "data")

with open(os.path.join(_DATA_DIR, "keyword_tags.json")) as _f:
    _KEYWORD_TAGS: dict = json.load(_f)

with open(os.path.join(_DATA_DIR, "stopwords.txt")) as _f:
    _STOPWORDS = {line.strip().lower() for line in _f if line.strip()}

# Caption artifact, not a tag -- stripped before any tagging is done.
CONTINUED_RE = re.compile(r"\(continued\)\s*$", re.IGNORECASE)

# An all-caps register/field token, e.g. "FLASH_ACR", "PCROP1_ASR".
REGNAME_RE = re.compile(r"^[A-Z][A-Z0-9_]{2,}$")

WORD_RE = re.compile(r"[A-Za-z0-9_]+")

FALLBACK_MIN_LEN = 3  # words strictly longer than this are eligible


def slugify(text: str) -> str:
    text = text.strip().lower().replace("_", "-")
    text = re.sub(r"[^a-z0-9]+", "-", text)
    return text.strip("-")


def _strip_continued(text: str) -> str:
    return CONTINUED_RE.sub("", text).strip()


def normalize_regname(value: str) -> str:
    """Collapses whitespace so a register name that wrapped across two lines
    in its cell (e.g. "FLASH_ PCROP1ASR", a `cells.py` multi-line-join
    artifact) is treated as one identifier rather than dropped."""
    return re.sub(r"\s+", "", value.strip())


def _keyword_hits(*texts: str) -> set[str]:
    haystack = " ".join(_strip_continued(t) for t in texts).lower()
    hits: set[str] = set()
    for phrase, mapped_tags in _KEYWORD_TAGS.items():
        if phrase in haystack:
            hits.update(mapped_tags)
    return hits


def _regname_words(text: str) -> set[str]:
    """Word-tokenized REGNAME scan -- catches a bare acronym embedded in an
    otherwise natural-language sentence (e.g. "DBG" in "DBG register map and
    reset values")."""
    found: set[str] = set()
    for word in WORD_RE.findall(_strip_continued(text)):
        if REGNAME_RE.match(word):
            found.add(slugify(word))
    return found


def _regnames_in_headers(headers: list) -> set[str]:
    """Whole-value REGNAME match against each header, not a sub-word
    tokenization of it. Verified against RM0490 (Table 16): a compound,
    natural-language header like "CPU bus error" must NOT surface "CPU" as
    a tag just because that one word happens to be all-caps -- each header
    is normally either wholly a field/register identifier (`WRP`,
    `SEC_PROT`) or wholly a descriptive phrase, so testing the header value
    as a whole is what correctly keeps the two apart."""
    found: set[str] = set()
    for h in headers:
        val = normalize_regname(h)
        if REGNAME_RE.match(val):
            found.add(slugify(val))
    return found


def _harvested_register_names(headers: list, rows: list) -> set[str]:
    idx = next((i for i, h in enumerate(headers) if h.strip().lower() == "register"), None)
    if idx is None:
        return set()
    found: set[str] = set()
    for row in rows:
        if idx < len(row) and row[idx]:
            val = normalize_regname(row[idx])
            if val and REGNAME_RE.match(val):
                found.add(slugify(val))
    return found


def _fallback_words(table_name: str) -> set[str]:
    words: set[str] = set()
    for word in WORD_RE.findall(_strip_continued(table_name)):
        if len(word) > FALLBACK_MIN_LEN and word.lower() not in _STOPWORDS:
            words.add(slugify(word))
    return words


def build_tags(table_name: str, section_title: str, headers: list, rows: list) -> list[str]:
    """tags may be empty for a pure numeric table -- acceptable.

    Fallback words are a last resort, only added when the curated/REGNAME/
    harvested sources produced nothing -- verified against RM0490 (Table
    16): "Mass erase overview" already gets 13 tags from those three
    sources, so the fallback scan of the caption itself (which would
    otherwise also emit a bare "mass", redundant with the already-matched
    "mass-erase" keyword phrase) never runs. This matches the fallback's
    stated purpose ("so no table is tag-less"), not an unconditional fourth
    source.
    """
    tags: set[str] = set()
    tags |= _keyword_hits(table_name, section_title or "", " ".join(headers))
    tags |= _regname_words(table_name)
    tags |= _regnames_in_headers(headers)
    tags |= _harvested_register_names(headers, rows)
    if not tags:
        tags |= _fallback_words(table_name)
    tags.discard("")
    return sorted(tags)
