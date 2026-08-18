"""Map each workbook to the selector level id that produced it.

Why this needs verifying rather than scraping
---------------------------------------------
A sub-family page such as ``stm32f2x5.html`` embeds its *parent's* series id
(``SS1575``) all over the markup, so "grab the SS id off the page" resolves
STM32F2x5 to the 38-row STM32F2 series grid -- wrong, and wrong silently.

What the pages do carry unambiguously is the hierarchy::

    window.productHierarchy = "LN1433-SS1575-SC2154-CL1734-FM141"   (stm32f2x5)
    window.productHierarchy = "SS1575-SC2154-CL1734-FM141"          (stm32f2-series)

Read left to right that is most-specific-first, and the extra leading
component on the sub-family page is its own id. Three id families are live
against the grid endpoint -- ``SS`` (series), ``SC`` (catalogue/class) and
``LN`` (product line, which is what the sub-family workbooks need). ``CL``
and ``FM`` are hierarchy bookkeeping and answer HTTP 400.

So: gather candidates from the hierarchy plus a plain id scrape, try each
against the grid, and accept only a candidate that agrees with the workbook
on *both* facts -- the level title and the part count. Anything else is
reported unresolved with everything that was tried.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .api import fetch_grid
from .net import ST_ROOT, FetchError, Fetcher
from .sheetio import OriginalSheet
from .values import _clean

logger = logging.getLogger("stproducts.resolve")

MCU_ROOT = ST_ROOT + "/en/microcontrollers-microprocessors/{slug}.html"

#: Level-id families that the grid endpoint actually serves.
GRID_ID = re.compile(r"\b((?:SS|SC|LN)\d{3,5})\b")
HIERARCHY = re.compile(r'window\.productHierarchy\s*=\s*"([^"]+)"')


@dataclass
class Candidate:
    """One id that was tried, and what the grid said about it."""

    level_id: str
    source: str
    status: str  # "ok" | "error"
    level_title: str | None = None
    rows: int | None = None
    columns: int | None = None
    detail: str | None = None


@dataclass
class Resolution:
    stem: str
    resolved: bool
    level_id: str | None = None
    level_title: str | None = None
    rows: int | None = None
    expected_parts: int | None = None
    pages_tried: list[str] = field(default_factory=list)
    candidates: list[Candidate] = field(default_factory=list)
    reason: str | None = None

    def to_json(self) -> dict:
        payload = asdict(self)
        payload["candidates"] = [asdict(c) if not isinstance(c, dict) else c for c in self.candidates]
        return payload


def slugify(text: str) -> str:
    """``"STM32F2 series"`` -> ``stm32f2-series``, ST's own page-slug rule."""
    slug = _clean(text).casefold()
    slug = re.sub(r"[^a-z0-9]+", "-", slug)
    return slug.strip("-")


def candidate_pages(sheet: OriginalSheet) -> list[str]:
    """The workbook's own page first, then its breadcrumb ancestors.

    Ancestors matter because a sub-family page is where the parent ids come
    from, and because a stem whose slug does not resolve can still be reached
    from the catalogue page above it.
    """
    names = [sheet.level_title, sheet.stem]
    if sheet.breadcrumb:
        # Most specific ancestor first.
        names += [seg for seg in reversed(sheet.breadcrumb.split("/"))]
    pages: list[str] = []
    for name in names:
        slug = slugify(name)
        if not slug:
            continue
        url = MCU_ROOT.format(slug=slug)
        if url not in pages:
            pages.append(url)
    return pages


def ids_from_page(text: str) -> list[tuple[str, str]]:
    """Candidate ids in priority order, tagged with where they came from.

    The hierarchy string is most-specific-first, so its leading component is
    the best guess for the page's own grid and is tried first.
    """
    found: list[tuple[str, str]] = []
    seen: set[str] = set()

    for match in HIERARCHY.finditer(text):
        for level_id in match.group(1).split("-"):
            if GRID_ID.fullmatch(level_id) and level_id not in seen:
                seen.add(level_id)
                found.append((level_id, "productHierarchy"))

    # Fall back to a plain scrape, most-mentioned first: the page's own grid
    # id is repeated far more than incidental cross-links.
    counts: dict[str, int] = {}
    for level_id in GRID_ID.findall(text):
        counts[level_id] = counts.get(level_id, 0) + 1
    for level_id, _ in sorted(counts.items(), key=lambda kv: -kv[1]):
        if level_id not in seen:
            seen.add(level_id)
            found.append((level_id, "page-scrape"))
    return found


def resolve_sheet(fetcher: Fetcher, sheet: OriginalSheet, *, max_candidates: int = 24) -> Resolution:
    """Find the level id whose grid matches this workbook on title and count."""
    expected = len(sheet.parts)
    result = Resolution(stem=sheet.stem, resolved=False, expected_parts=expected)

    wanted_titles = {_clean(sheet.level_title).casefold(), _clean(sheet.stem).casefold()}

    tried: set[str] = set()
    for page in candidate_pages(sheet):
        try:
            markup = fetcher.get_text(page)
        except FetchError as exc:
            logger.debug("candidate page %s unavailable: %s", page, exc)
            result.pages_tried.append(f"{page} (unavailable)")
            continue
        result.pages_tried.append(page)

        for level_id, source in ids_from_page(markup):
            if level_id in tried or len(tried) >= max_candidates:
                continue
            tried.add(level_id)
            try:
                grid = fetch_grid(fetcher, level_id, referer=page)
            except FetchError as exc:
                result.candidates.append(
                    Candidate(level_id, source, "error", detail=str(exc)[:160])
                )
                continue

            result.candidates.append(
                Candidate(
                    level_id,
                    source,
                    "ok",
                    level_title=grid.level_title,
                    rows=len(grid.rows),
                    columns=len(grid.columns),
                )
            )
            title_ok = _clean(grid.level_title).casefold() in wanted_titles
            count_ok = len(grid.rows) == expected
            if title_ok and count_ok:
                result.resolved = True
                result.level_id = level_id
                result.level_title = grid.level_title
                result.rows = len(grid.rows)
                return result

        if result.resolved:
            break

    near = [
        c
        for c in result.candidates
        if c.status == "ok" and _clean(c.level_title or "").casefold() in wanted_titles
    ]
    if near:
        result.reason = (
            f"level title matched but row count did not: "
            + ", ".join(f"{c.level_id} has {c.rows} rows, workbook has {expected}" for c in near)
        )
    else:
        result.reason = (
            f"no candidate returned levelTitle {sheet.level_title!r} with {expected} rows"
        )
    return result


def load_map(path: Path) -> dict[str, dict]:
    if Path(path).exists():
        return json.loads(Path(path).read_text())
    return {}


def save_map(path: Path, resolutions: dict[str, Resolution]) -> None:
    payload = {stem: res.to_json() for stem, res in sorted(resolutions.items())}
    Path(path).write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n")
