"""Enumerate every product selector ST publishes, not just the downloaded ones.

The problem this solves
-----------------------
Every other entry point starts from ``--input``, a directory of workbooks
someone downloaded by hand. Nine in, nine out. There was no path that produced
a selector nobody had already fetched, which meant the tool could only ever
re-issue corrected copies of files that already existed.

How the tree is walked
----------------------
Each grid row carries its full position in ST's product tree::

    /etc/prmis/products/FM141/CL1734/SC2154/SS1577/LN1938/PF262719
                        family class  catal.  series line   product

So fetching one catalogue-level grid names every series (``SS``) and product
line (``LN``) underneath it. Discovery is a tree walk over that one field.

This is deliberately **not** page scraping. A sub-family page such as
``stm32f2x5.html`` embeds its *parent's* ``SS`` id throughout the markup, so
scraping ids off HTML resolves STM32F2x5 to the 38-row STM32F2 series grid --
wrong, and wrong silently. ``resolve.py`` carries a careful workaround for
that. Reading ``path`` off the rows sidesteps it: the id is data, not markup.

Why the catalogue list is seeded rather than discovered
-------------------------------------------------------
The walk goes downward only. ``CL`` and ``FM`` are the levels above ``SC``,
and the grid endpoint answers HTTP 400 for both, so there is no root to start
from. :data:`SEED_CATALOGUES` is therefore an explicit list -- but every entry
is verified against the grid on each run, and a title mismatch is reported
rather than assumed away. A new ST catalogue needs one line adding here; the
alternative was scraping ST's landing page, which reintroduces exactly the
HTML fragility the ``path`` walk avoids.
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .api import GRID_LEVELS, Grid, fetch_grid
from .net import FetchError, Fetcher

logger = logging.getLogger("stproducts.discover")

#: Catalogue-level ids, the roots of the walk. ``expect`` is checked against
#: the grid's own ``levelTitle`` every run, so a wrong or retired id is
#: reported rather than silently producing someone else's parts.
SEED_CATALOGUES: dict[str, str] = {
    # Confirmed against the grid endpoint from the shipped workbooks.
    "SC2154": "STM32 high performance MCUs",
    "SC2230": "STM32 Arm Cortex MPUs",
    "SC1244": "STM8 8-bit MCUs",
    # The remaining STM32 catalogues. Titles are checked, not trusted -- this
    # list was written from ST's site naming and the check immediately caught
    # SC2157, where ST's grid says "ultra low power" and the site says
    # "ultra-low-power". Verified 2026-08-12: all six resolve, 0 unreachable.
    "SC2155": "STM32 mainstream MCUs",
    "SC2157": "STM32 ultra low power MCUs",
    "SC2156": "STM32 wireless MCUs",
}


@dataclass
class Selector:
    """One discovered product selector."""

    level_id: str
    level_title: str
    family: str  # SC / SS / LN
    parts: int
    columns: int
    parent: str | None = None
    breadcrumb: str = ""
    #: Stem of the local workbook covering this selector, when there is one.
    local_workbook: str | None = None

    @property
    def stem(self) -> str:
        return self.local_workbook or self.level_title.strip()


@dataclass
class Catalog:
    """Everything discovery found, plus what it could not reach."""

    selectors: list[Selector] = field(default_factory=list)
    failed: dict[str, str] = field(default_factory=dict)
    seed_mismatches: dict[str, str] = field(default_factory=dict)

    def by_level(self, family: str) -> list[Selector]:
        return [s for s in self.selectors if s.family == family]

    def to_json(self) -> dict:
        return {
            "counts": {f: len(self.by_level(f)) for f in GRID_LEVELS},
            "total": len(self.selectors),
            "selectors": [asdict(s) for s in sorted(
                self.selectors, key=lambda s: (s.family, s.level_id)
            )],
            "failed": self.failed,
            "seed_mismatches": self.seed_mismatches,
        }

    def write(self, path: Path) -> None:
        path.write_text(json.dumps(self.to_json(), indent=2) + "\n")


def _record(grid: Grid, family: str, parent: str | None) -> Selector:
    return Selector(
        level_id=grid.level_id,
        level_title=grid.level_title.strip(),
        family=family,
        parts=len(grid.rows),
        columns=len(grid.columns),
        parent=parent,
        breadcrumb=grid.breadcrumb,
    )


def discover(
    fetcher: Fetcher,
    seeds: dict[str, str] | None = None,
    *,
    include: tuple[str, ...] = GRID_LEVELS,
    local_stems: dict[str, str] | None = None,
) -> Catalog:
    """Walk ST's selector tree from the seed catalogues downward.

    ``local_stems`` maps a level id to the stem of the workbook that already
    covers it, so ``build`` can prefer the real file and keep its diff.
    """
    seeds = SEED_CATALOGUES if seeds is None else seeds
    local_stems = local_stems or {}
    catalog = Catalog()
    seen: set[str] = set()
    # level id -> the catalogue it was found under, for the report
    pending: list[tuple[str, str | None]] = [(sid, None) for sid in seeds]

    while pending:
        level_id, parent = pending.pop(0)
        if level_id in seen:
            continue
        seen.add(level_id)
        family = level_id[:2]
        if family not in include:
            continue

        try:
            grid = fetch_grid(fetcher, level_id)
        except FetchError as exc:
            catalog.failed[level_id] = str(exc)
            logger.warning("discover: %s unreachable (%s)", level_id, exc)
            continue

        if not grid.rows:
            catalog.failed[level_id] = "grid returned no rows"
            continue

        expected = seeds.get(level_id)
        if expected and grid.level_title.strip().casefold() != expected.casefold():
            # Reported, not fatal: ST renames things, and the id is what
            # matters. But an unnoticed rename can mean a wrong id entirely.
            catalog.seed_mismatches[level_id] = (
                f"seed says {expected!r}, grid says {grid.level_title.strip()!r}"
            )

        selector = _record(grid, family, parent)
        selector.local_workbook = local_stems.get(level_id)
        catalog.selectors.append(selector)

        # Descend: every SS and LN named in this grid's rows.
        below = grid.level_ids()
        for child_family in ("SS", "LN"):
            if child_family not in include:
                continue
            for child in sorted(below.get(child_family, ())):
                if child not in seen:
                    pending.append((child, level_id))

        logger.info(
            "%-10s %-38s %4d parts, %2d columns, %d children",
            level_id, grid.level_title.strip()[:38], len(grid.rows), len(grid.columns),
            sum(len(below.get(f, ())) for f in ("SS", "LN")),
        )

    return catalog
