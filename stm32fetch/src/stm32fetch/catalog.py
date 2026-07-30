"""Build the STM32 reference-manual catalog (STM32FETCH_FINAL_SPEC.md §3).

ST's cxst JSON API is the ONLY live catalog source -- no browser, no HTML
scraping, no bundled seed, no third-party data. The endpoint is built from
parameters rather than hardcoded, so the same code can list other resource
families (datasheets, application notes, ...) by pointing `--resource-type`
elsewhere:

    /bin/st/selectors/cxst/{locale}.cxst-rs-grid.html/{class_id}.{category}.{resource_type}.json

There are exactly two sources, in this order:
1. the on-disk `catalog.json` cache -- used as-is unless `refresh`;
2. the cxst API -- fetched on `--refresh`, or automatically when no cache
   exists yet. The cache IS the offline story: if the API fails and a
   cache exists, that's a warning and the cache is used; if it fails with
   no cache, that's a clear, actionable error (but never prevents
   `run --manuals-dir` from working against already-downloaded PDFs).
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .net import make_session, request_with_retry
from .series import derive_series_from_devices

logger = logging.getLogger("stm32fetch.catalog")

CXST_URL_TEMPLATE = (
    "https://www.st.com/bin/st/selectors/cxst/{locale}.cxst-rs-grid.html/"
    "{class_id}.{category}.{resource_type}.json"
)
DEFAULT_LOCALE = "en"
DEFAULT_CLASS_ID = "CL1734"
DEFAULT_CATEGORY = "technical_literature"
DEFAULT_RESOURCE_TYPE = "reference_manual"

# ST's own RM numbers are always "RM" + 3-4 digits (STM32FETCH_FINAL_SPEC.md §3).
RM_NUMBER_RE = re.compile(r"^RM\d{3,4}$")
# Same shape, unanchored -- for pulling an RM number out of a longer
# string (a PDF filename stem) rather than validating a catalog row's
# `title` field is nothing else.
RM_NUMBER_SEARCH_RE = re.compile(r"RM\d{3,4}", re.IGNORECASE)
# Matches device tokens like "STM32F469xx", "STM32F405/415" inside a
# description -- uppercase, digits, the literal wildcard "x", and "/" for a
# combined device list, but not ordinary lowercase prose.
DEVICE_TOKEN_RE = re.compile(r"STM32[A-Z0-9x/]+")


def cxst_url(
    locale: str = DEFAULT_LOCALE,
    class_id: str = DEFAULT_CLASS_ID,
    category: str = DEFAULT_CATEGORY,
    resource_type: str = DEFAULT_RESOURCE_TYPE,
) -> str:
    return CXST_URL_TEMPLATE.format(
        locale=locale, class_id=class_id, category=category, resource_type=resource_type
    )


@dataclass
class CatalogEntry:
    rm_number: str
    rev: str
    updated: str
    pdf_url: str
    title: str
    filename: str
    slug: str
    devices: list[str]
    series: list[str]
    resource_path: str

    def to_json(self) -> dict:
        return {
            "rm_number": self.rm_number,
            "rev": self.rev,
            "updated": self.updated,
            "pdf_url": self.pdf_url,
            "title": self.title,
            "filename": self.filename,
            "slug": self.slug,
            "devices": self.devices,
            "series": self.series,
            "resource_path": self.resource_path,
        }


def _locale_value(d: dict | None, locale: str) -> str | None:
    """`d[locale]`, else `d["en"]`, else the first value present, else
    `None` -- the fallback chain STM32FETCH_FINAL_SPEC.md §3 specifies for
    both `localizedLinks` and `localizedDescriptions`."""
    if not d:
        return None
    if locale in d:
        return d[locale]
    if "en" in d:
        return d["en"]
    return next(iter(d.values()), None)


def _absolute_url(url: str) -> str:
    if url.startswith("http"):
        return url
    if not url.startswith("/"):
        url = "/" + url
    return "https://www.st.com" + url


def _epoch_ms_to_iso(value) -> str:
    """`latestUpdate` (epoch milliseconds) -> ISO-8601, or `""` if absent
    or unparseable -- never guessed, never fatal to the row."""
    if value is None:
        return ""
    try:
        return datetime.fromtimestamp(int(value) / 1000, tz=timezone.utc).isoformat()
    except (TypeError, ValueError, OSError):
        return ""


def parse_catalog_payload(payload: dict, locale: str = DEFAULT_LOCALE) -> list[CatalogEntry]:
    """Every usable row -> `CatalogEntry`, per the field mapping in
    STM32FETCH_FINAL_SPEC.md §3. Filters to `physicalResourceType ==
    "reference_manual"`; a row with no usable link, an unparseable RM
    number, or that raises for any other reason is logged and skipped --
    one bad row never aborts the rest of the parse."""
    entries = []
    for row in payload.get("rows") or []:
        try:
            if row.get("physicalResourceType") != "reference_manual":
                continue
            rm_number = (row.get("title") or "").strip().upper()
            if not RM_NUMBER_RE.match(rm_number):
                logger.warning("catalog row skipped: title %r isn't an RM number", row.get("title"))
                continue
            link = _locale_value(row.get("localizedLinks"), locale)
            if not link:
                logger.warning("catalog row %s skipped: no usable pdf link", rm_number)
                continue
            pdf_url = _absolute_url(link)
            filename = pdf_url.rsplit("/", 1)[-1]
            if not filename.lower().endswith(".pdf"):
                logger.warning("catalog row %s skipped: link isn't a PDF: %r", rm_number, pdf_url)
                continue
            slug = filename[: -len(".pdf")]
            description = _locale_value(row.get("localizedDescriptions"), locale) or ""
            devices = list(dict.fromkeys(DEVICE_TOKEN_RE.findall(description)))
            entries.append(
                CatalogEntry(
                    rm_number=rm_number,
                    rev=row.get("version") or "",
                    updated=_epoch_ms_to_iso(row.get("latestUpdate")),
                    pdf_url=pdf_url,
                    title=description,
                    filename=filename,
                    slug=slug,
                    devices=devices,
                    series=derive_series_from_devices(devices),
                    resource_path=row.get("resourcePath") or "",
                )
            )
        except Exception:  # noqa: BLE001 -- one malformed row must never abort the parse
            logger.warning("catalog row raised while parsing; skipping: %r", row, exc_info=True)
    return entries


def fetch_catalog_payload(
    session=None,
    *,
    locale: str = DEFAULT_LOCALE,
    class_id: str = DEFAULT_CLASS_ID,
    category: str = DEFAULT_CATEGORY,
    resource_type: str = DEFAULT_RESOURCE_TYPE,
) -> dict:
    session = session or make_session()
    url = cxst_url(locale, class_id, category, resource_type)
    resp = request_with_retry(session, "GET", url)
    resp.raise_for_status()
    return resp.json()


DEFAULT_CATALOG_PATH = Path("catalog.json")


def load_catalog(cache_path: str | Path = DEFAULT_CATALOG_PATH) -> dict | None:
    path = Path(cache_path)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        logger.warning("catalog cache at %s is unreadable; ignoring", path, exc_info=True)
        return None


def _write_catalog(cache_path: Path, entries: list[CatalogEntry], source_url: str) -> dict:
    doc = {
        "scraped_at": datetime.now(timezone.utc).isoformat(),
        "source": source_url,
        "manuals": [e.to_json() for e in entries],
    }
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps(doc, indent=2))
    return doc


def build_catalog(
    cache_path: str | Path = DEFAULT_CATALOG_PATH,
    *,
    refresh: bool = False,
    session=None,
    locale: str = DEFAULT_LOCALE,
    class_id: str = DEFAULT_CLASS_ID,
    category: str = DEFAULT_CATEGORY,
    resource_type: str = DEFAULT_RESOURCE_TYPE,
) -> dict:
    """The catalog: cache-or-refresh, nothing else (STM32FETCH_FINAL_SPEC.md
    §3). A failed API fetch with an existing cache logs a warning and
    returns the cache unchanged; a failed fetch with no cache raises a
    clear error."""
    cache_path = Path(cache_path)
    cached = load_catalog(cache_path)
    if cached and not refresh:
        logger.info("using cached catalog at %s (%d manuals)", cache_path, len(cached["manuals"]))
        return cached

    url = cxst_url(locale, class_id, category, resource_type)
    session = session or make_session()
    try:
        payload = fetch_catalog_payload(
            session, locale=locale, class_id=class_id, category=category, resource_type=resource_type
        )
        entries = parse_catalog_payload(payload, locale)
    except Exception as exc:  # noqa: BLE001 -- API failure degrades to cache, never crashes
        if cached:
            logger.warning("catalog API fetch failed (%s); using existing cache at %s", exc, cache_path)
            return cached
        raise RuntimeError(
            f"could not fetch the reference-manual catalog from {url} and no cache "
            f"exists ({exc})"
        ) from exc

    if not entries:
        if cached:
            logger.warning("catalog API returned no usable manuals; keeping existing cache at %s", cache_path)
            return cached
        raise RuntimeError(f"catalog API at {url} returned no usable manuals and no cache exists")

    logger.info("fetched %d manuals from the catalog API", len(entries))
    return _write_catalog(cache_path, entries, url)


def verify_catalog(catalog: dict, *, session=None) -> list[tuple[str, str, int]]:
    """HEAD every `pdf_url` in `catalog`; returns `(rm_number, pdf_url,
    status_code)` for anything that didn't come back 200 -- ST
    periodically renames slugs, which silently breaks a cached `pdf_url`
    otherwise. `status_code` is `0` for a request that raised outright."""
    session = session or make_session()
    problems = []
    for m in catalog.get("manuals", []):
        url = m["pdf_url"]
        try:
            resp = request_with_retry(session, "HEAD", url, max_retries=2)
        except Exception as exc:  # noqa: BLE001
            logger.warning("verify: HEAD %s raised: %s", url, exc)
            problems.append((m["rm_number"], url, 0))
            continue
        if resp.status_code != 200:
            problems.append((m["rm_number"], url, resp.status_code))
    return problems
