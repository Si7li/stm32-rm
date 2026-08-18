"""The only way this package talks to st.com.

ST sits behind Akamai TLS-fingerprint bot protection: plain ``requests``,
system ``curl`` and bundled-chromium Playwright all fail. ``curl_cffi`` with
``impersonate="chrome"`` reproduces a real Chrome TLS fingerprint and works.
This is the same transport ``stm32fetch`` proved out; every st.com request in
this package goes through the one session built here.

Every response is written to an on-disk cache keyed by URL, so a second run
is fully offline (validation item 10). ``Fetcher.calls`` counts the requests
that actually crossed the network, which is what the run report asserts on.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path

from curl_cffi import requests as cffi

logger = logging.getLogger("stproducts.net")

IMPERSONATE = "chrome"
CONNECT_TIMEOUT = 10
READ_TIMEOUT = 120
RETRYABLE_STATUS = {429, 500, 502, 503, 504}
MAX_RETRIES = 5
MIN_INTERVAL = 1.0  # seconds between network requests (~1 req/s)

ST_ROOT = "https://www.st.com"


class FetchError(RuntimeError):
    """A URL could not be retrieved, from cache or from the network."""


def _slot(url: str) -> str:
    """Cache filename for a URL: readable tail plus a hash of the whole thing."""
    digest = hashlib.sha256(url.encode("utf-8")).hexdigest()[:16]
    tail = url.rstrip("/").rsplit("/", 1)[-1].replace(".json", "").replace(".html", "")
    safe = "".join(ch if ch.isalnum() or ch in "-_." else "_" for ch in tail)[:60]
    return f"{safe}-{digest}"


@dataclass
class Fetcher:
    """Rate-limited, retrying, caching HTTP GET against st.com."""

    cache_dir: Path
    use_cache: bool = True
    offline: bool = False
    calls: int = 0  # requests that actually hit the network
    hits: int = 0  # requests served from cache
    _session: cffi.Session | None = field(default=None, repr=False)
    _last_request: float = field(default=0.0, repr=False)

    def __post_init__(self) -> None:
        self.cache_dir = Path(self.cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    @property
    def session(self) -> cffi.Session:
        if self._session is None:
            self._session = cffi.Session(impersonate=IMPERSONATE)
        return self._session

    def _throttle(self) -> None:
        wait = MIN_INTERVAL - (time.monotonic() - self._last_request)
        if wait > 0:
            time.sleep(wait)
        self._last_request = time.monotonic()

    def get_bytes(self, url: str, *, referer: str | None = None, xhr: bool = False) -> bytes:
        """GET ``url``, from cache when possible. Raises FetchError on failure.

        Definitive failures (a non-retryable 4xx, which is what probing a
        non-grid level id returns) are cached too, so rediscovery with a warm
        cache stays offline.
        """
        path = self.cache_dir / _slot(url)
        fail = path.with_suffix(".err")
        if self.use_cache and path.exists():
            self.hits += 1
            logger.debug("cache hit %s", url)
            return path.read_bytes()
        if self.use_cache and fail.exists():
            self.hits += 1
            raise FetchError(f"GET {url} -> {fail.read_text().strip()} (cached)")
        if self.offline:
            raise FetchError(f"offline and not cached: {url}")

        headers = {}
        if xhr:
            headers["X-Requested-With"] = "XMLHttpRequest"
        if referer:
            headers["Referer"] = referer

        last: Exception | None = None
        resp = None
        for attempt in range(1, MAX_RETRIES + 1):
            self._throttle()
            self.calls += 1
            try:
                resp = self.session.get(
                    url, headers=headers, timeout=(CONNECT_TIMEOUT, READ_TIMEOUT)
                )
            except Exception as exc:  # noqa: BLE001 -- transport errors are all retryable
                last = exc
                logger.warning("GET %s attempt %d/%d raised: %s", url, attempt, MAX_RETRIES, exc)
            else:
                if resp.status_code == 200:
                    path.write_bytes(resp.content)
                    return resp.content
                if resp.status_code not in RETRYABLE_STATUS:
                    fail.write_text(f"HTTP {resp.status_code}")
                    raise FetchError(f"GET {url} -> HTTP {resp.status_code}")
                logger.warning(
                    "GET %s attempt %d/%d -> HTTP %d", url, attempt, MAX_RETRIES, resp.status_code
                )
            if attempt < MAX_RETRIES:
                time.sleep(min(2**attempt, 60))
        if last is not None:
            raise FetchError(f"GET {url} failed: {last}") from last
        raise FetchError(f"GET {url} -> HTTP {resp.status_code if resp else '?'} after retries")

    def get_text(self, url: str, **kw) -> str:
        return self.get_bytes(url, **kw).decode("utf-8", errors="replace")

    def get_json(self, url: str, **kw) -> dict:
        raw = self.get_bytes(url, **kw)
        try:
            return json.loads(raw)
        except json.JSONDecodeError as exc:
            # A cached error page is worse than no cache -- drop it so a
            # later run can retry cleanly.
            slot = self.cache_dir / _slot(url)
            slot.unlink(missing_ok=True)
            raise FetchError(f"GET {url} returned non-JSON ({exc})") from exc
