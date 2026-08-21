"""Find the datasheet for a part: local copies first, ST second.

One ST datasheet covers a whole family -- ``stm32f205rb.pdf`` is the
datasheet for every ``STM32F205xx``/``STM32F207xx`` part and names 14 of them
on its cover -- so the local index is built by asking each PDF which parts it
claims, not by guessing from filenames alone.

Resolution order for a part:

1. a local file named after the part (``datasheets/F2/stm32f205rb.pdf``);
2. a local file whose *cover* names the part, or whose name shares the part's
   family stem (``stm32f205rb.pdf`` for ``STM32F207IE``);
3. ``downloadURL`` from ``cxst-rpn-info``, cached under ``datasheets_cache/``.

Everything is cached, so a warm run makes no network calls.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

from .api import fetch_datasheet_url
from .net import ST_ROOT, FetchError, Fetcher

#: Where ST serves datasheets. Every ``downloadURL`` returned by rpn-info has
#: this shape, so the public URL for a PDF already on disk is recoverable from
#: its filename -- no rpn-info call, and it works offline.
DATASHEET_URL = ST_ROOT + "/resource/en/datasheet/{stem}.pdf"


def st_datasheet_url(path: Path) -> str:
    """Public ST URL for a datasheet held locally.

    The workbook's ``Datasheet URL`` column used to receive a local path like
    ``datasheets/F2/stm32f205rb.pdf`` whenever the PDF was found on disk
    rather than downloaded, which is most of the time. That is not a URL: it
    cannot be clicked, it does not resolve on another machine, and it leaks
    the layout of whoever ran the build.
    """
    return DATASHEET_URL.format(stem=Path(path).stem.lower())

logger = logging.getLogger("stproducts.datasheets")

#: ``STM32F205RB -> STM32F205 ; STM8AF5268 -> STM8AF52``
#:
#: Single-digit families need the trailing package letter in the stem:
#: ``STM32U3B5JI -> STM32U3B``, so that ``stm32u3b5cg.pdf`` buckets with its
#: own family instead of ``STM32U3`` -- where ``candidates[0]`` could hand
#: back a U375 datasheet. Two-digit-and-up families keep the bare number
#: (``F205RB`` and ``F207IG`` must land in different buckets).
FAMILY_STEM = re.compile(
    r"^(STM32[A-Z]\d{1,3}[A-Z]|STM32[A-Z]\d{2,3}|STM8[A-Z]{1,3}\d{2})",
    re.I,
)

#: Concrete part numbers only -- "STM32F205xx" is a family, not a part, and
#: indexing it would make one datasheet claim every part in the series.
PART_ON_COVER = re.compile(
    r"\bSTM32[A-Z]\d{2,3}[A-Z][A-Z0-9]\b|\bSTM8[A-Z]{1,3}\d{2,6}\b"
)

#: The device-summary table naming a datasheet's parts sits on page 2.
PAGES_SCANNED = 4


def family_stem(part: str) -> str:
    match = FAMILY_STEM.match(part)
    return (match.group(1) if match else part).upper()


@dataclass
class Acquisition:
    """Per-family tally of how datasheets were obtained."""

    resolved: dict[str, Path] = field(default_factory=dict)
    #: part -> ST's downloadURL, for parts whose datasheet came from ST.
    urls: dict[str, str] = field(default_factory=dict)
    unresolved: list[str] = field(default_factory=list)
    local_hits: int = 0
    cache_hits: int = 0
    downloads: int = 0
    notes: list[str] = field(default_factory=list)

    def summary(self) -> dict:
        return {
            "parts_resolved": len(self.resolved),
            "parts_unresolved": len(self.unresolved),
            "unresolved_examples": self.unresolved[:8],
            "local_hits": self.local_hits,
            "cache_hits": self.cache_hits,
            "downloads": self.downloads,
            "notes": self.notes[:8],
        }


class LocalIndex:
    """Index of `datasheets/**/*.pdf`, by filename stem and by cover mention."""

    def __init__(self, root: Path, cache_file: Path | None = None):
        self.root = Path(root)
        self.by_name: dict[str, Path] = {}
        self.by_stem: dict[str, list[Path]] = {}
        self.by_cover: dict[str, Path] = {}
        self._cache_file = cache_file
        self._build()

    def _build(self) -> None:
        if not self.root.exists():
            return
        for path in sorted(self.root.rglob("*.pdf")):
            name = path.stem.upper()
            self.by_name.setdefault(name, path)
            self.by_stem.setdefault(family_stem(name), []).append(path)

        covers = self._load_cover_index()
        for part, filename in covers.items():
            candidate = self.by_name.get(Path(filename).stem.upper())
            if candidate is not None:
                self.by_cover.setdefault(part, candidate)

    def _load_cover_index(self) -> dict[str, str]:
        """Which concrete parts each local PDF claims.

        The cover names families with a wildcard (``STM32F205xx``); the
        concrete list is in ``Table 1. Device summary`` on page 2, which is
        where ``stm32f205rb.pdf`` names the 26 F205xx/F207xx parts it covers.
        Scanning the first few pages of 20-odd PDFs costs a second or two and
        is cached, and it is what lets one family datasheet answer for parts
        whose filename shares nothing with it.
        """
        if self._cache_file and self._cache_file.exists():
            try:
                return json.loads(self._cache_file.read_text())
            except json.JSONDecodeError:
                pass

        import pdfplumber

        index: dict[str, str] = {}
        for path in sorted(self.root.rglob("*.pdf")):
            try:
                with pdfplumber.open(path) as pdf:
                    text = "\n".join(
                        (page.extract_text() or "") for page in pdf.pages[:PAGES_SCANNED]
                    )
            except Exception as exc:  # noqa: BLE001
                logger.debug("cover read failed for %s: %s", path, exc)
                continue
            for part in PART_ON_COVER.findall(text):
                index.setdefault(part.upper(), path.name)
        if self._cache_file:
            self._cache_file.parent.mkdir(parents=True, exist_ok=True)
            self._cache_file.write_text(json.dumps(index, indent=1, sort_keys=True) + "\n")
        return index

    def find(self, part: str) -> Path | None:
        upper = part.upper()
        if upper in self.by_name:
            return self.by_name[upper]
        if upper in self.by_cover:
            return self.by_cover[upper]
        candidates = self.by_stem.get(family_stem(upper))
        return candidates[0] if candidates else None


def acquire(
    fetcher: Fetcher,
    grid_rows: list[dict],
    *,
    local: LocalIndex,
    cache_dir: Path,
    allow_download: bool = True,
) -> Acquisition:
    """Resolve a datasheet for every part in a grid."""
    result = Acquisition()
    cache_dir = Path(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    url_by_part: dict[str, str] = {}

    # Datasheets fetched on an earlier run are indexed the same way as the
    # local ones, so an offline run can still use them. Without this, finding
    # a cached PDF would require the rpn-info call that names it, and
    # --offline would report every STM8 part as unresolved despite the file
    # sitting in the cache.
    cached = LocalIndex(cache_dir, cache_dir / "cache-cover-index.json")

    for row in grid_rows:
        part = row["part_number"]

        found = local.find(part)
        if found is not None:
            result.resolved[part] = found
            result.urls[part] = st_datasheet_url(found)
            result.local_hits += 1
            continue

        found = cached.find(part)
        if found is not None:
            result.resolved[part] = found
            result.urls[part] = st_datasheet_url(found)
            result.cache_hits += 1
            continue

        if not allow_download or not row.get("product_id"):
            result.unresolved.append(part)
            continue

        try:
            url = fetch_datasheet_url(fetcher, row["product_id"])
        except FetchError as exc:
            result.unresolved.append(part)
            result.notes.append(f"{part}: rpn-info failed ({exc})")
            continue
        if not url:
            result.unresolved.append(part)
            result.notes.append(f"{part}: no downloadURL")
            continue

        url_by_part[part] = url
        result.urls[part] = url
        target = cache_dir / url.rsplit("/", 1)[-1]
        if target.exists():
            result.resolved[part] = target
            result.cache_hits += 1
            continue
        try:
            target.write_bytes(fetcher.get_bytes(url))
        except FetchError as exc:
            result.unresolved.append(part)
            result.notes.append(f"{part}: download failed ({exc})")
            continue
        result.resolved[part] = target
        result.downloads += 1

    return result
