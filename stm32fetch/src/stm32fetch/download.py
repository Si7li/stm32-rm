"""Polite, resumable PDF downloader (STM32FETCH_FINAL_SPEC.md §4).

Every request goes through the shared curl_cffi session from `net.py` --
no other HTTP client. Downloads are idempotent (an existing file with a
plausible size is skipped unless `--force`), streamed to a `.partial`
sibling that's only renamed into place on success, and resumed via an
HTTP Range request when a `.partial` file already exists from a previous
interrupted attempt.

A hard-won caveat, empirically verified against the real server (not a
guess): ST's CDN serves PDFs gzip-compressed by default, and `Content-
Length` reflects the WIRE (compressed) size while the body curl_cffi hands
back is already decompressed -- so the two are never byte-comparable, and
the server doesn't advertise `Accept-Ranges` at all. A `Range` request
against the default (compressed) encoding corrupts the gzip stream outright
(curl raises `RequestException: ... incorrect header check`); the CDN
doesn't honor `Range` under `Accept-Encoding: identity` either (it just
serves 200 with an empty body). So resume is attempted opportunistically --
a `Range` request is sent, but the response is trusted only if it actually
comes back `206` with a `Content-Range` matching what was asked; anything
else (an exception, a plain `200`, a mismatched range) discards the
attempt and restarts the download from scratch rather than risking a
corrupted file. Given that, `Content-Length` is never used as a hard
pass/fail signal either -- only whether the stream completed without error
and produced a plausible number of bytes.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from .net import CONNECT_TIMEOUT, DEFAULT_MAX_RETRIES, READ_TIMEOUT, make_session, request_with_retry

logger = logging.getLogger("stm32fetch.download")

DEFAULT_RATE_SECONDS = 1.0
MIN_PLAUSIBLE_BYTES = 1024  # guards against saving an HTML error page as a ".pdf"

# STM32FETCH_FINAL_SPEC.md §9 (verified, static -- robots.txt itself isn't
# fetched over the network: robots.txt fetching is exactly the kind of
# extra plain-HTTP st.com request this package no longer makes, and these
# two prefixes are the only things it disallows).
DISALLOWED_PATH_PREFIXES = ("/search.html", "/content/st_com/search-sitemaps/")


def _path_allowed(url: str) -> bool:
    path = urlsplit(url).path
    return not any(path.startswith(p) for p in DISALLOWED_PATH_PREFIXES)


class RateLimiter:
    """Enforces a minimum gap between successive `wait()` calls, across
    threads if `--jobs` > 1 -- politeness must hold regardless of
    concurrency."""

    def __init__(self, min_interval: float = DEFAULT_RATE_SECONDS):
        self.min_interval = min_interval
        self._lock = threading.Lock()
        self._last = 0.0

    def wait(self) -> None:
        with self._lock:
            now = time.monotonic()
            delta = now - self._last
            if delta < self.min_interval:
                time.sleep(self.min_interval - delta)
            self._last = time.monotonic()


@dataclass
class DownloadResult:
    rm_number: str
    filename: str
    status: str  # "downloaded" | "skipped" | "disallowed" | "failed"
    bytes: int = 0
    error: str | None = None


def _is_plausible(path: Path) -> bool:
    return path.exists() and path.stat().st_size >= MIN_PLAUSIBLE_BYTES


def _stream_download(session, url: str, partial: Path) -> int:
    """Streams `url` into `partial`, resuming from its current size via
    `Range` if it already exists -- trusting the resume only if the server
    actually honors it (see module docstring). Returns the total bytes now
    in `partial`."""
    have = partial.stat().st_size if partial.exists() else 0
    mode = "wb"
    resp = None

    if have:
        try:
            candidate = session.get(
                url, headers={"Range": f"bytes={have}-"}, stream=True,
                timeout=(CONNECT_TIMEOUT, READ_TIMEOUT),
            )
        except Exception:  # noqa: BLE001 -- Range + compression can raise; just restart
            logger.info("range resume not honored for %s; restarting from scratch", url, exc_info=True)
            candidate = None
        if (
            candidate is not None
            and candidate.status_code == 206
            and candidate.headers.get("content-range", "").startswith(f"bytes {have}-")
        ):
            resp, mode = candidate, "ab"
        else:
            have = 0  # server didn't honor the range -- start over, don't corrupt the file

    if resp is None:
        resp = request_with_retry(session, "GET", url, max_retries=1, stream=True)
    resp.raise_for_status()

    written = have
    with open(partial, mode) as f:
        for chunk in resp.iter_content(chunk_size=1 << 16):
            if chunk:
                f.write(chunk)
                written += len(chunk)
    return written


def download_one(
    entry: dict,
    manuals_dir: str | Path,
    *,
    session=None,
    rate_limiter: RateLimiter | None = None,
    force: bool = False,
    max_retries: int = DEFAULT_MAX_RETRIES,
) -> DownloadResult:
    manuals_dir = Path(manuals_dir)
    manuals_dir.mkdir(parents=True, exist_ok=True)
    dest = manuals_dir / entry["filename"]
    url = entry["pdf_url"]
    rm_number = entry.get("rm_number", entry["filename"])
    session = session or make_session()
    rate_limiter = rate_limiter or RateLimiter()

    if not _path_allowed(url):
        logger.warning("robots.txt disallows %s; skipping", url)
        return DownloadResult(rm_number, entry["filename"], "disallowed")

    if not force and _is_plausible(dest):
        logger.info("%s already downloaded (%d bytes); skipping", entry["filename"], dest.stat().st_size)
        return DownloadResult(rm_number, entry["filename"], "skipped", dest.stat().st_size)

    partial = dest.with_suffix(dest.suffix + ".partial")
    if force:
        partial.unlink(missing_ok=True)

    last_error: Exception | None = None
    for attempt in range(1, max_retries + 1):
        rate_limiter.wait()
        try:
            written = _stream_download(session, url, partial)
            if written < MIN_PLAUSIBLE_BYTES:
                raise ValueError(f"downloaded only {written} bytes -- likely an error page")
        except Exception as exc:  # noqa: BLE001 -- one bad manual must not abort the batch
            last_error = exc
            partial.unlink(missing_ok=True)
            if attempt < max_retries:
                backoff = min(2**attempt, 60)
                logger.warning(
                    "download %s attempt %d/%d failed (%s); retrying in %ds",
                    entry["filename"], attempt, max_retries, exc, backoff,
                )
                time.sleep(backoff)
            else:
                logger.error("download %s failed after %d attempts: %s", entry["filename"], max_retries, exc)
            continue

        partial.replace(dest)  # atomic on the same filesystem
        logger.info("downloaded %s (%d bytes)", entry["filename"], written)
        return DownloadResult(rm_number, entry["filename"], "downloaded", written)

    return DownloadResult(rm_number, entry["filename"], "failed", 0, str(last_error))


def download_many(
    entries: list[dict],
    manuals_dir: str | Path,
    *,
    rate: float = DEFAULT_RATE_SECONDS,
    force: bool = False,
    jobs: int = 1,
    max_retries: int = DEFAULT_MAX_RETRIES,
    session=None,
) -> list[DownloadResult]:
    """Downloads every entry, sequential by default; one failure never
    aborts the rest. `session` is injectable so tests can supply a fake
    HTTP client."""
    rate_limiter = RateLimiter(rate)
    session = session or make_session()

    def _one(entry: dict) -> DownloadResult:
        try:
            return download_one(
                entry, manuals_dir, session=session, rate_limiter=rate_limiter,
                force=force, max_retries=max_retries,
            )
        except Exception as exc:  # noqa: BLE001 -- never let one entry kill the batch
            logger.error("unexpected error downloading %s: %s", entry.get("filename"), exc, exc_info=True)
            return DownloadResult(entry.get("rm_number", "?"), entry.get("filename", "?"), "failed", 0, str(exc))

    if jobs <= 1:
        return [_one(e) for e in entries]

    from concurrent.futures import ThreadPoolExecutor

    results: list[DownloadResult | None] = [None] * len(entries)
    with ThreadPoolExecutor(max_workers=jobs) as pool:
        futures = {pool.submit(_one, e): i for i, e in enumerate(entries)}
        for future in futures:
            results[futures[future]] = future.result()
    return results  # type: ignore[return-value]
