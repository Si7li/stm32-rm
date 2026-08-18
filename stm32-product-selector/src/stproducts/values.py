"""Value normalisation (for comparing) and rendering (for writing).

Two different jobs, deliberately separated:

``canon``  -- collapse a value to a comparison key. Two values with the same
              key are the *same value written differently*, and must not be
              reported as an error. This is what keeps the diff honest: with
              no normalisation the F2 file alone shows 306 differing cells,
              nearly all of them ``120`` vs ``120.0``, ``No`` vs ``false``,
              ``-`` vs empty, or ``A, B`` vs ``A||B``.

``render`` -- turn an API value into the text ST's own export would put in
              the cell, so the corrected workbook reads like the original.

The rendering conventions below were derived from the shipped workbooks, not
assumed:

* ``||`` is the API's repeat separator; the export writes ``, ``.
* booleans arrive as ``true``/``false``; the export writes ``Yes``/``No``.
* an absent value is written ``-`` (all nine workbooks; 1471 such cells, no
  empty cells at all).
* a numeric column qualified ``min``/``max`` that carries several values is
  collapsed to that extreme -- e.g. ``Operating Temperature (°C) max`` of
  ``105||85`` renders ``105``. Verified on all 19 occurrences in the F2 file.
"""

from __future__ import annotations

import html
import re

from .api import MULTI_SEP, Column

#: Renderings of "no value". The workbooks use "-"; the API uses "".
BLANK_TOKENS = {"", "-", "--", "—", "–", "n/a", "na"}

BLANK_RENDERING = "-"

_TRUE = {"true", "yes"}
_FALSE = {"false", "no"}

_WS = re.compile(r"\s+")
_NUMBER = re.compile(r"^[+-]?(\d+\.?\d*|\.\d+)$")


def _clean(text: object) -> str:
    """Unescape entities, normalise every kind of space, collapse runs."""
    if text is None:
        return ""
    s = str(text)
    if "&" in s:
        s = html.unescape(s)
    s = s.replace(" ", " ").replace("‑", "-")
    return _WS.sub(" ", s).strip()


def is_blank(text: object) -> bool:
    return _clean(text).casefold() in BLANK_TOKENS


def _canon_number(token: str) -> str | None:
    """``120``, ``120.0`` and ``+120`` all collapse to the same key."""
    if not _NUMBER.match(token):
        return None
    value = float(token)
    return str(int(value)) if value == int(value) else repr(value)


def _split(text: str, column: Column | None) -> list[str]:
    """Split a repeated-value cell into its parts.

    ``||`` always separates. A comma only separates for a column that
    actually holds a list -- otherwise every ``General Description``
    ("...128 Kbytes of Flash memory, 120 MHz CPU, ART Accelerator") would be
    shredded into fragments.
    """
    if MULTI_SEP in text:
        parts = text.split(MULTI_SEP)
    elif column is not None and column.is_list and "," in text:
        parts = text.split(",")
    else:
        return [text]
    return [p.strip() for p in parts if p.strip()]


def _looks_like_list(text: str, column: Column | None) -> bool:
    return MULTI_SEP in text or (column is not None and column.is_list and "," in text)


def _absent_boolean(column: Column | None) -> bool:
    """A boolean column with no value means "No", not "unknown".

    The workbooks never write ``-`` in a boolean column: across the three
    columns where ST omits values entirely (``Dual-bank Flash``,
    ``Touch sensing FW library``, ``Secure Boot spec``) all 800 cells read
    ``Yes`` for ``true`` and ``No`` for both ``false`` and absent. Treating
    absent as blank instead would report 239 cells as wrong when the export
    is simply rendering "not present" as "No".
    """
    return column is not None and column.type == "boolean"


def reduce_boolean(text: str, column: Column | None) -> str | None:
    """Collapse a boolean cell, which may carry one value per variant.

    ``Buy On Line`` arrives as ``false||true`` for a part sold in some
    packages but not others, and all three STM8 workbooks render that ``No``
    -- 84 occurrences, no counterexample. Reported as a change it would be 84
    false positives, so the rule is applied rather than the difference.

    The data cannot separate "logical AND" from "first token wins": every
    mixed value present is ``false||true``, and both readings give ``No``.
    AND is implemented as the conservative one -- ``Yes`` only when every
    variant agrees. Returns None when the cell is not boolean-shaped.
    """
    if column is None or column.type != "boolean":
        return None
    tokens = [t.strip().casefold() for t in re.split(r"\|\||,", text) if t.strip()]
    if not tokens or not all(t in _TRUE or t in _FALSE for t in tokens):
        return None
    return "true" if all(t in _TRUE for t in tokens) else "false"


def collapse_numeric_extreme(text: str, column: Column | None) -> str | None:
    """``105||85`` on a ``max`` column is ``105``; on a ``min`` column, ``85``.

    Returns None when the rule does not apply, so callers can fall back.
    """
    if column is None or not column.is_numeric or column.qualifier not in ("min", "max"):
        return None
    parts = _split(text, column)
    if len(parts) < 2:
        return None
    numbers = [_canon_number(p) for p in parts]
    if any(n is None for n in numbers):
        return None
    pick = (max if column.qualifier == "max" else min)(float(p) for p in parts)
    return str(int(pick)) if pick == int(pick) else str(pick)


def canon(value: object, column: Column | None = None) -> str:
    """Comparison key for a cell. Equal keys mean "not a real change"."""
    text = _clean(value)
    if text.casefold() in BLANK_TOKENS:
        return "false" if _absent_boolean(column) else ""

    boolean = reduce_boolean(text, column)
    if boolean is not None:
        return boolean

    low = text.casefold()
    if low in _TRUE:
        return "true"
    if low in _FALSE:
        return "false"

    extreme = collapse_numeric_extreme(text, column)
    if extreme is not None:
        text = extreme

    if _looks_like_list(text, column):
        # Repeated values are a set: order is a rendering choice, not data.
        parts = {canon(p, None) for p in _split(text, column)}
        parts.discard("")
        return "||".join(sorted(parts))

    number = _canon_number(text)
    return number if number is not None else text


def _canon_package(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", text.casefold())


#: Matched before punctuation is stripped, because the "+" is the point.
_BALL_SPLIT = re.compile(r"^([A-Za-z]+)\s*(\d+)\s*\+\s*(\d+)\b")


def _package_forms(token: str) -> set[str]:
    """The ways one package gets written.

    A datasheet writes ``WLCSP64+2`` -- 64 balls plus 2 -- where ST writes
    ``WLCSP 66``. Both name one package, so the summed form counts as the
    same token.
    """
    forms = {_canon_package(token)}
    match = _BALL_SPLIT.match(token.strip())
    if match:
        total = int(match.group(2)) + int(match.group(3))
        forms.add(_canon_package(f"{match.group(1)}{total}"))
    return forms


def package_equivalent(api_text: str, datasheet_text: str) -> bool:
    """Do two package lists name the same packages, in different notation?

    A datasheet writes ``LQFP64``; ST writes ``LQFP 64 10x10x1.4 mm``. The
    short name is a prefix of the fuller one, so they agree about *which*
    package -- and reporting that as the sources disagreeing would bury the
    real findings under one row per part.
    """
    api = {_canon_package(t) for t in re.split(r"[,|]+", api_text) if t.strip()}
    datasheet = [t for t in re.split(r"[,|]+", datasheet_text) if t.strip()]
    if not api or not datasheet:
        return False
    return all(
        any(form and a.startswith(form) for form in _package_forms(token) for a in api)
        for token in datasheet
    )


def core_equivalent(api_text: str, datasheet_text: str) -> bool:
    """Is ST's core name the datasheet's, with more detail attached?

    A cover says ``Cortex-M33``; ST writes ``Arm Cortex-M33 with TrustZone``.
    ST's label is the datasheet's plus a qualifier, so the two name the same
    core and reporting a disagreement would be noise.
    """
    api = _WS.sub(" ", _clean(api_text)).casefold()
    datasheet = _WS.sub(" ", _clean(datasheet_text)).casefold()
    if not api or not datasheet:
        return False
    return api.startswith(datasheet) or datasheet.startswith(api)


#: Field-specific "these agree" tests, referenced by name from the field map.
EQUIVALENCE = {"package": package_equivalent, "core": core_equivalent}


def render(value: object, column: Column | None = None, *, blank: str = BLANK_RENDERING) -> str:
    """The text ST's own export would write for this API value."""
    text = _clean(value)
    if text.casefold() in BLANK_TOKENS:
        return "No" if _absent_boolean(column) else blank

    boolean = reduce_boolean(text, column)
    if boolean is not None:
        return "Yes" if boolean == "true" else "No"

    extreme = collapse_numeric_extreme(text, column)
    if extreme is not None:
        return extreme

    if MULTI_SEP in text:
        return ", ".join(p.strip() for p in text.split(MULTI_SEP) if p.strip())
    return text
