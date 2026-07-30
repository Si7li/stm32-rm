"""The ONLY way this package talks to st.com (STM32FETCH_FINAL_SPEC.md §2).

ST is behind Akamai TLS-fingerprint bot protection. Verified: plain
`requests`, system `curl` (HTTP/2 and HTTP/1.1), and bundled-chromium
Playwright all fail (stream reset / 0 bytes / stalls indefinitely) --
their TLS handshakes don't match a real browser's fingerprint closely
enough. `curl_cffi` with `impersonate="chrome"` reproduces a real Chrome
TLS fingerprint and works. Every st.com request in this package -- catalog
API, HEAD verification, PDF downloads -- goes through the one shared
session this module builds; there is no other HTTP client anywhere in the
package.
"""

from __future__ import annotations

import logging
import time

from curl_cffi import requests as cffi

logger = logging.getLogger("stm32fetch.net")

DEFAULT_IMPERSONATE = "chrome"
CONNECT_TIMEOUT = 10
READ_TIMEOUT = 120
RETRYABLE_STATUS = {429, 500, 502, 503, 504}
DEFAULT_MAX_RETRIES = 5


def make_session(impersonate: str = DEFAULT_IMPERSONATE) -> cffi.Session:
    return cffi.Session(impersonate=impersonate)


def request_with_retry(
    session: cffi.Session,
    method: str,
    url: str,
    *,
    max_retries: int = DEFAULT_MAX_RETRIES,
    timeout: tuple[float, float] = (CONNECT_TIMEOUT, READ_TIMEOUT),
    **kwargs,
):
    """`session.request(method, url, ...)` with exponential backoff on a
    retryable status (429/5xx) or a transport-level exception (timeout,
    connection reset). Never retries a genuine 4xx other than 429 --
    that's a real answer (e.g. 404), not a transient failure."""
    last_exc: Exception | None = None
    for attempt in range(1, max_retries + 1):
        try:
            resp = session.request(method, url, timeout=timeout, **kwargs)
        except Exception as exc:  # noqa: BLE001 -- network errors are all retryable here
            last_exc = exc
            logger.warning("%s %s attempt %d/%d raised: %s", method, url, attempt, max_retries, exc)
        else:
            if resp.status_code not in RETRYABLE_STATUS:
                return resp
            logger.warning(
                "%s %s attempt %d/%d got HTTP %d", method, url, attempt, max_retries, resp.status_code
            )
            last_exc = None
        if attempt < max_retries:
            time.sleep(min(2**attempt, 60))
    if last_exc is not None:
        raise last_exc
    return resp  # last (retryable-status) response, exhausted retries
