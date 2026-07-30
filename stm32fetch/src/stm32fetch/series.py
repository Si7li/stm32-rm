"""STM32 series/family normalization and matching (STM32FETCH_FINAL_SPEC.md §3/§6).

A "series" like STM32F1 or STM32H7 is the STM32 prefix plus exactly one
letter plus exactly one digit -- `STM32([A-Z]\\d)` -- e.g. "STM32F103xx" ->
"STM32F1", "STM32H7Rxx" -> "STM32H7", discarding the specific-device
digits/suffix that follow. The same derivation normalizes a user-typed
query ("stm32c0", "c0", "C0", "STM32C0" all -> "STM32C0"), so a CLI query
and catalog data compare on the same canonical form.
"""

from __future__ import annotations

import difflib
import re

_SERIES_RE = re.compile(r"^STM32([A-Z]\d)")


def normalize_series(text: str) -> str | None:
    """Canonical family key ("STM32C0", "STM32H7", ...) from a device name
    or a user query, or `None` if `text` doesn't look like an STM32
    part/family at all."""
    t = (text or "").strip().upper()
    if not t:
        return None
    if not t.startswith("STM32"):
        t = "STM32" + t
    m = _SERIES_RE.match(t)
    if not m:
        return None
    return f"STM32{m.group(1)}"


def derive_series_from_devices(devices: list[str]) -> list[str]:
    """Sorted, de-duplicated series derived from a manual's device list
    (STM32FETCH_FINAL_SPEC.md §3: "sorted unique") -- a manual can straddle
    more than one series (e.g. one RM covering both STM32F103 and
    STM32F105 devices, both STM32F1, contributes the series only once)."""
    return sorted({s for d in devices if (s := normalize_series(d))})


def manuals_matching_series(manuals: list[dict], query: str) -> list[dict]:
    """Every catalog entry whose `series` list contains the normalized
    query family key. A query can legitimately match multiple manuals
    (e.g. STM32H7 spans more than one RM) -- callers should act on every
    match, not just the first."""
    key = normalize_series(query)
    if key is None:
        return []
    return [m for m in manuals if key in (m.get("series") or [])]


def suggest_series(manuals: list[dict], query: str, n: int = 5) -> list[str]:
    """Closest known series keys to an unmatched query, so a typo or an
    unknown family prints a helpful hint instead of silently doing
    nothing."""
    known = sorted({s for m in manuals for s in (m.get("series") or [])})
    key = normalize_series(query) or (query or "").strip().upper()
    matches = difflib.get_close_matches(key, known, n=n, cutoff=0.4)
    return matches or known[:n]
