"""Document metadata auto-derivation for the rag_selective wrapper.

Everything is derived from the PDF itself -- the cover page, the PDF
`Title` metadata, and the filename -- never hardcoded to one manual. Every
field is overridable by the caller (wired to a matching CLI flag).
"""

from __future__ import annotations

import logging
import os
import re

logger = logging.getLogger("rmtables.metadata")

COVER_PAGES = 2  # cover-page text scan range for name/family/core
COVER_TITLE_LINES = 8  # first-page lines scanned for device tokens (references)
REV_SCAN_PAGES = 120  # early pages scanned for a "Rev N" footer
FREQUENCY_SCAN_PAGES = 120  # early pages scanned for "up to N MHz" (not always on the cover)

# RM numbers have grown from 3 to 4 digits (RM0008 -> RM0486) -- unbounded so a
# future 5-digit number isn't truncated either (CORE_REGEX_FIX.md).
NAME_RE = re.compile(r"\b(RM\d{3,})\b")
REV_RE = re.compile(r"Rev\s+(\d+)")
# Optional trailing letter distinguishes sub-lines that share a digit, e.g.
# STM32H7Rx/7Sx -> "H7R", not just "H7" (CORE_REGEX_FIX.md).
FAMILY_RE = re.compile(r"STM32([A-Z]\d[A-Z]?)")
# CORE_REGEX_FIX.md: the old `M(\d\+?)` used a single-digit class, so every
# two-digit Arm core (M33, M55, M85, ...) was truncated to its first digit
# (e.g. "Cortex-M55" -> "M5"). 1-3 digits, optional trailing "+", optional
# (R)/(TM) marks, and stray whitespace around the separator all accounted for
# -- ST's PDFs render "Cortex®-M55" and sometimes inject extra spaces.
CORE_RE = re.compile(
    r"Cortex\s*[®™]?\s*[-‑–—]?\s*M\s*(\d{1,3})\s*(\+?)",
    re.IGNORECASE,
)
FREQUENCY_RE = re.compile(r"(up to \d+\s*MHz)", re.IGNORECASE)
DEVICE_TOKEN_RE = re.compile(r"stm32[a-z]\d[0-9a-z]*", re.IGNORECASE)

# Every Arm Cortex-M core number ST has shipped in an STM32 to date -- used
# only to WARN (never hard-fail) when a match doesn't look like a real core,
# since a new core can legitimately appear (CORE_REGEX_FIX.md §3).
KNOWN_CORE_TOKENS = {"0", "0+", "1", "3", "4", "7", "23", "33", "35", "52", "55", "85"}

OVERRIDE_FIELDS = [
    "name_datasheet",
    "rev",
    "url_pdf",
    "references",
    "package",
    "family",
    "core",
    "frequency",
]


def _pdf_title(pdf_path: str) -> str:
    try:
        import pypdf

        reader = pypdf.PdfReader(pdf_path)
        title = (reader.metadata or {}).get("/Title", "") or ""
        return title
    except Exception:
        return ""


def _normalize_device_token(raw: str) -> str:
    """Normalizes a device token to ST's standard casing (STM32Fxxx, with
    the trailing wildcard suffix like "xx" kept exactly as printed) --
    needed because the filename slug is all-lowercase
    ("stm32f101xx-stm32f102xx-...") while the cover title is usually
    already correctly cased; both must dedupe as the same token."""
    m = re.match(r"stm32([a-z])(\d[0-9a-z]*)", raw, re.IGNORECASE)
    if not m:
        return raw
    return f"STM32{m.group(1).upper()}{m.group(2)}"


def _distinct_core_tokens(matches: list) -> list[str]:
    """Order-preserving distinct `{number}{plus}` tokens across every CORE_RE
    match (e.g. an H7 manual mentioning both M7 and M4 cores) -- used only
    for the multi-core DEBUG log, never to pick the value itself."""
    seen: list[str] = []
    for m in matches:
        token = m.group(1) + m.group(2)
        if token not in seen:
            seen.append(token)
    return seen


def _derive_core(text: str) -> str:
    """First Cortex-M match in the cover/title text (CORE_REGEX_FIX.md:
    "prefer the FIRST match on the cover/title pages"). Every distinct core
    number found is logged at DEBUG so a multi-core manual stays visible
    instead of silently collapsing to one; an unrecognized or apparently
    truncated core number is logged at WARNING (never a hard failure --
    a new core could legitimately appear)."""
    matches = list(CORE_RE.finditer(text))
    if not matches:
        return ""

    tokens = _distinct_core_tokens(matches)
    if len(tokens) > 1:
        logger.debug("multiple distinct Cortex-M cores found in cover text: %s", tokens)

    m = matches[0]
    number, plus = m.group(1), m.group(2)
    token = number + plus
    if token not in KNOWN_CORE_TOKENS:
        logger.warning(
            "unrecognized Cortex-M core number %r (matched text: %r) -- "
            "double-check this isn't a truncated or mis-parsed value",
            token, m.group(0),
        )
    # Maximal-digit-run check: if another digit sits immediately after the
    # captured run, the regex's 1-3 digit cap (not a real ST core exceeds 2
    # digits) truncated a longer number rather than a real separator.
    end = m.end(1)
    if end < len(text) and text[end].isdigit():
        logger.warning(
            "Cortex-M core number %r may be truncated -- immediately "
            "followed by another digit in source text", number,
        )
    return f"Arm 32-bit Cortex-M{number}{plus} CPU"


def _validate_rev(rev: str) -> None:
    if not rev:
        logger.warning("rev could not be derived from the scanned pages")
        return
    digits = re.sub(r"\D", "", rev)
    if len(digits) > 3:
        logger.warning("rev %r has more than 3 digits -- looks suspicious", rev)


def _validate_frequency(frequency: str) -> None:
    if not frequency:
        return
    m = re.search(r"\d+", frequency)
    if m and not (1 <= int(m.group(0)) <= 2000):
        logger.warning("frequency %r is outside the expected ~1-2000 MHz range", frequency)


def _derive_references(cover_title: str, filename: str) -> str:
    """Collects ALL distinct device tokens from the cover title lines and
    the filename slug (a manual applying to 5 devices used to only surface
    the first one found) -- deduped preserving first-seen order, joined
    with ", ". A series-only manual (e.g. RM0490 -> "STM32C0") naturally
    yields just that one token, which is an acceptable reference on its
    own."""
    tokens: list[str] = []
    seen: set[str] = set()
    for source in (cover_title, filename):
        for m in DEVICE_TOKEN_RE.finditer(source):
            token = _normalize_device_token(m.group(0))
            key = token.upper()
            if key not in seen:
                seen.add(key)
                tokens.append(token)
    return ", ".join(tokens)


def derive_metadata(pdf, pdf_path: str, overrides: dict) -> dict:
    """`pdf` is an open pdfplumber.PDF; `pdf_path` its file path on disk."""
    filename = os.path.basename(pdf_path)

    cover = ""
    for i in range(min(COVER_PAGES, len(pdf.pages))):
        cover += "\n" + (pdf.pages[i].extract_text() or "")
        pdf.pages[i].flush_cache()

    title = _pdf_title(pdf_path)
    text = cover + "\n" + title

    def find(pattern, default=""):
        m = pattern.search(text)
        return m.group(1).strip() if m else default

    cover_title_lines = "\n".join(cover.splitlines()[:COVER_TITLE_LINES])

    rev = ""
    frequency = ""
    scan_pages = max(REV_SCAN_PAGES, FREQUENCY_SCAN_PAGES)
    for i in range(min(scan_pages, len(pdf.pages))):
        page_text = pdf.pages[i].extract_text() or ""
        if not rev and i < REV_SCAN_PAGES:
            m = REV_RE.search(page_text)
            if m:
                rev = "Rev " + m.group(1)
        if not frequency and i < FREQUENCY_SCAN_PAGES:
            m = FREQUENCY_RE.search(page_text)
            if m:
                frequency = m.group(1).strip()
        pdf.pages[i].flush_cache()
        if rev and frequency:
            break

    core = _derive_core(text)

    _validate_rev(rev)
    _validate_frequency(frequency)

    meta = {
        "name_datasheet": find(NAME_RE) or re.sub(r"[-_].*", "", filename).upper(),
        "rev": rev,
        "url_pdf": f"https://www.st.com/resource/en/reference_manual/{filename}",
        "references": _derive_references(cover_title_lines, filename),
        "package": "",
        "family": find(FAMILY_RE),
        "core": core,
        "frequency": frequency,
    }
    meta.update({k: v for k, v in overrides.items() if v is not None})
    logger.info("derived metadata: %s", meta)
    return meta
