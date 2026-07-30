import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from stm32fetch import net as net_mod
from stm32fetch.net import request_with_retry


class FakeResponse:
    def __init__(self, status_code):
        self.status_code = status_code


class FakeSession:
    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = 0

    def request(self, method, url, **kwargs):
        self.calls += 1
        item = self._responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def _no_sleep(monkeypatch):
    monkeypatch.setattr(net_mod.time, "sleep", lambda s: None)


def test_request_with_retry_returns_immediately_on_success():
    session = FakeSession([FakeResponse(200)])
    resp = request_with_retry(session, "GET", "https://x/y")
    assert resp.status_code == 200
    assert session.calls == 1


def test_request_with_retry_does_not_retry_plain_404():
    session = FakeSession([FakeResponse(404)])
    resp = request_with_retry(session, "HEAD", "https://x/y")
    assert resp.status_code == 404
    assert session.calls == 1


def test_request_with_retry_retries_on_5xx_then_succeeds(monkeypatch):
    _no_sleep(monkeypatch)
    session = FakeSession([FakeResponse(503), FakeResponse(200)])
    resp = request_with_retry(session, "GET", "https://x/y", max_retries=3)
    assert resp.status_code == 200
    assert session.calls == 2


def test_request_with_retry_retries_on_429_then_succeeds(monkeypatch):
    _no_sleep(monkeypatch)
    session = FakeSession([FakeResponse(429), FakeResponse(200)])
    resp = request_with_retry(session, "GET", "https://x/y", max_retries=3)
    assert resp.status_code == 200


def test_request_with_retry_retries_on_exception_then_succeeds(monkeypatch):
    _no_sleep(monkeypatch)
    session = FakeSession([ConnectionError("reset"), FakeResponse(200)])
    resp = request_with_retry(session, "GET", "https://x/y", max_retries=3)
    assert resp.status_code == 200


def test_request_with_retry_raises_last_exception_after_exhausting_retries(monkeypatch):
    _no_sleep(monkeypatch)
    session = FakeSession([ConnectionError("a"), ConnectionError("b"), ConnectionError("c")])
    with pytest.raises(ConnectionError):
        request_with_retry(session, "GET", "https://x/y", max_retries=3)
    assert session.calls == 3


def test_request_with_retry_backs_off_exponentially(monkeypatch):
    sleeps = []
    monkeypatch.setattr(net_mod.time, "sleep", lambda s: sleeps.append(s))
    session = FakeSession([FakeResponse(503), FakeResponse(503), FakeResponse(200)])
    request_with_retry(session, "GET", "https://x/y", max_retries=3)
    assert sleeps == [2, 4]
