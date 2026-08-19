"""The HTTP layer: caching of GET and of the one POST.

The POST custom here is the products-excel-download export. Its cache must be
keyed by the body too, or one selector's workbook answers for another; and a
cached POST must serve offline like a cached GET does.
"""

from __future__ import annotations

import pytest

from stproducts.net import Fetcher, FetchError


class _Response:
    def __init__(self, status_code, content=b"ok"):
        self.status_code = status_code
        self.content = content


class _StubSession:
    def __init__(self):
        self.calls = []

    def post(self, url, data=None, headers=None, timeout=None):
        self.calls.append((url, data))
        return _Response(200, b"xlsx-bytes")


def test_post_form_bytes_reused_offline_when_cached(tmp_path):
    first = Fetcher(cache_dir=tmp_path, offline=False)
    first._session = _StubSession()
    assert first.post_form_bytes("https://x/export", data={"requestData": "AAA"}) == b"xlsx-bytes"

    # Same URL and body: the second run is offline and makes no call.
    second = Fetcher(cache_dir=tmp_path, offline=True)
    second._session = _StubSession()
    assert second.post_form_bytes("https://x/export", data={"requestData": "AAA"}) == b"xlsx-bytes"
    assert second.calls == 0
    assert second._session.calls == []


def test_post_form_bytes_cache_is_keyed_by_body(tmp_path):
    online = Fetcher(cache_dir=tmp_path, offline=False)
    online._session = _StubSession()
    online.post_form_bytes("https://x/export", data={"requestData": "AAA"})

    # A different body must not be answered with another body's cached file.
    offline = Fetcher(cache_dir=tmp_path, offline=True)
    with pytest.raises(FetchError):
        offline.post_form_bytes("https://x/export", data={"requestData": "BBB"})
    assert offline.calls == 0