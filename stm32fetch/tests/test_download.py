import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from stm32fetch import download as download_mod
from stm32fetch.download import RateLimiter, download_many, download_one


class FakeResponse:
    def __init__(self, status_code=200, headers=None, chunks=None):
        self.status_code = status_code
        self.headers = headers or {}
        self._chunks = chunks or []

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def iter_content(self, chunk_size):
        yield from self._chunks


class FakeSession:
    """Duck-types just enough of curl_cffi's Session for `download_one` --
    `.get`/`.head` both funnel through `.request` (mirroring how curl_cffi
    itself is a thin wrapper), so one queue drives everything. Each item is
    a response, or an exception instance to raise."""

    def __init__(self, responses=None):
        self._responses = list(responses or [])
        self.calls = []  # (method, url, headers) per call

    def request(self, method, url, *, headers=None, stream=None, timeout=None, **kwargs):
        self.calls.append((method, url, dict(headers or {})))
        item = self._responses.pop(0) if self._responses else FakeResponse(200, {}, [b"x" * 2000])
        if isinstance(item, Exception):
            raise item
        return item

    def get(self, url, **kwargs):
        return self.request("GET", url, **kwargs)

    def head(self, url, **kwargs):
        return self.request("HEAD", url, **kwargs)


ENTRY = {
    "rm_number": "RM0490",
    "filename": "rm0490.pdf",
    "pdf_url": "https://www.st.com/resource/en/reference_manual/rm0490.pdf",
}


def _no_sleep(monkeypatch):
    monkeypatch.setattr(download_mod.time, "sleep", lambda s: None)


def test_idempotent_skip_when_file_exists_and_plausible(tmp_path):
    dest = tmp_path / ENTRY["filename"]
    dest.write_bytes(b"x" * 2000)
    session = FakeSession()

    result = download_one(ENTRY, tmp_path, session=session, rate_limiter=RateLimiter(0), force=False)

    assert result.status == "skipped"
    assert session.calls == []  # no network at all for a plausible existing file


def test_force_redownloads_even_if_file_exists(tmp_path, monkeypatch):
    _no_sleep(monkeypatch)
    dest = tmp_path / ENTRY["filename"]
    dest.write_bytes(b"x" * 2000)
    session = FakeSession(responses=[FakeResponse(200, {}, [b"y" * 3000])])

    result = download_one(ENTRY, tmp_path, session=session, rate_limiter=RateLimiter(0), force=True)

    assert result.status == "downloaded"
    assert dest.read_bytes() == b"y" * 3000


def test_retries_on_5xx_then_succeeds(tmp_path, monkeypatch):
    _no_sleep(monkeypatch)
    session = FakeSession(responses=[FakeResponse(503), FakeResponse(200, {}, [b"z" * 2000])])

    result = download_one(ENTRY, tmp_path, session=session, rate_limiter=RateLimiter(0), max_retries=3)

    assert result.status == "downloaded"
    assert (tmp_path / ENTRY["filename"]).exists()


def test_failure_never_leaves_a_partial_file(tmp_path, monkeypatch):
    _no_sleep(monkeypatch)
    session = FakeSession(responses=[ConnectionError("refused"), ConnectionError("refused")])

    result = download_one(ENTRY, tmp_path, session=session, rate_limiter=RateLimiter(0), max_retries=2)

    assert result.status == "failed"
    assert not (tmp_path / ENTRY["filename"]).exists()
    assert not (tmp_path / (ENTRY["filename"] + ".partial")).exists()


def test_atomic_rename_leaves_no_partial_sibling_on_success(tmp_path, monkeypatch):
    _no_sleep(monkeypatch)
    session = FakeSession(responses=[FakeResponse(200, {}, [b"a" * 5000])])

    download_one(ENTRY, tmp_path, session=session, rate_limiter=RateLimiter(0))

    assert (tmp_path / ENTRY["filename"]).exists()
    assert not (tmp_path / (ENTRY["filename"] + ".partial")).exists()


def test_disallowed_path_skips_download_entirely(tmp_path):
    # Static check (STM32FETCH_FINAL_SPEC.md §9) -- no network involved.
    entry = {**ENTRY, "pdf_url": "https://www.st.com/search.html?q=rm0490"}
    session = FakeSession()

    result = download_one(entry, tmp_path, session=session, rate_limiter=RateLimiter(0))

    assert result.status == "disallowed"
    assert session.calls == []
    assert not (tmp_path / ENTRY["filename"]).exists()


def test_resource_path_is_allowed():
    assert download_mod._path_allowed("https://www.st.com/resource/en/reference_manual/rm0490.pdf")


def test_search_and_sitemap_paths_are_disallowed():
    assert not download_mod._path_allowed("https://www.st.com/search.html?q=x")
    assert not download_mod._path_allowed("https://www.st.com/content/st_com/search-sitemaps/foo")


def test_download_many_continues_after_one_failure(tmp_path, monkeypatch):
    _no_sleep(monkeypatch)
    ok_entry = {**ENTRY, "rm_number": "RM0001", "filename": "ok.pdf"}
    bad_entry = {**ENTRY, "rm_number": "RM0002", "filename": "bad.pdf"}

    def fake_download_one(entry, manuals_dir, **kwargs):
        if entry["filename"] == "bad.pdf":
            return download_mod.DownloadResult(entry["rm_number"], entry["filename"], "failed", 0, "boom")
        return download_mod.DownloadResult(entry["rm_number"], entry["filename"], "downloaded", 123)

    monkeypatch.setattr(download_mod, "download_one", fake_download_one)

    results = download_many([ok_entry, bad_entry], tmp_path, rate=0)

    statuses = {r.rm_number: r.status for r in results}
    assert statuses == {"RM0001": "downloaded", "RM0002": "failed"}


# ------------------------------------------------------- Range-based resume

def test_resume_appends_when_server_honors_range(tmp_path, monkeypatch):
    _no_sleep(monkeypatch)
    partial = tmp_path / (ENTRY["filename"] + ".partial")
    partial.write_bytes(b"A" * 100)  # bytes already on disk from a prior interrupted attempt

    session = FakeSession(responses=[
        FakeResponse(206, {"content-range": "bytes 100-2099/2100"}, [b"B" * 2000]),
    ])

    result = download_one(ENTRY, tmp_path, session=session, rate_limiter=RateLimiter(0))

    assert result.status == "downloaded"
    dest = tmp_path / ENTRY["filename"]
    assert dest.read_bytes() == b"A" * 100 + b"B" * 2000
    method, url, headers = session.calls[0]
    assert headers.get("Range") == "bytes=100-"


def test_resume_falls_back_to_full_restart_when_server_returns_200(tmp_path, monkeypatch):
    _no_sleep(monkeypatch)
    partial = tmp_path / (ENTRY["filename"] + ".partial")
    partial.write_bytes(b"A" * 100)

    # First call (the Range attempt) comes back 200, not 206 -- not honored.
    # Second call (request_with_retry's fresh GET) succeeds fully.
    session = FakeSession(responses=[
        FakeResponse(200, {}, [b"C" * 3000]),
        FakeResponse(200, {}, [b"C" * 3000]),
    ])

    result = download_one(ENTRY, tmp_path, session=session, rate_limiter=RateLimiter(0))

    assert result.status == "downloaded"
    dest = tmp_path / ENTRY["filename"]
    # NOT "A"*100 + "C"*3000 -- the unhonored range response is discarded
    # and the file restarts clean.
    assert dest.read_bytes() == b"C" * 3000


def test_resume_falls_back_to_full_restart_when_range_request_raises(tmp_path, monkeypatch):
    _no_sleep(monkeypatch)
    partial = tmp_path / (ENTRY["filename"] + ".partial")
    partial.write_bytes(b"A" * 100)

    session = FakeSession(responses=[
        RuntimeError("incorrect header check"),  # the gzip+Range failure mode seen against the real CDN
        FakeResponse(200, {}, [b"D" * 3000]),
    ])

    result = download_one(ENTRY, tmp_path, session=session, rate_limiter=RateLimiter(0))

    assert result.status == "downloaded"
    assert (tmp_path / ENTRY["filename"]).read_bytes() == b"D" * 3000


def test_resume_fresh_download_sends_no_range_header(tmp_path, monkeypatch):
    _no_sleep(monkeypatch)
    session = FakeSession(responses=[FakeResponse(200, {}, [b"E" * 2000])])

    download_one(ENTRY, tmp_path, session=session, rate_limiter=RateLimiter(0))

    assert len(session.calls) == 1
    _, _, headers = session.calls[0]
    assert "Range" not in headers
