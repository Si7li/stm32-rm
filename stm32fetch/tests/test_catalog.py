import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from stm32fetch import catalog as catalog_mod
from stm32fetch.catalog import (
    CXST_URL_TEMPLATE,
    build_catalog,
    cxst_url,
    load_catalog,
    parse_catalog_payload,
)

# A trimmed but real-shaped payload (STM32FETCH_FINAL_SPEC.md §3's own example).
SAMPLE_PAYLOAD = {
    "title": "Reference Manual",
    "rows": [
        {
            "title": "RM0386",
            "version": "6.0",
            "latestUpdate": 1716156000000,
            "physicalResourceType": "reference_manual",
            "localizedDescriptions": {
                "en": "STM32F469xx and STM32F479xx advanced Arm-based 32-bit MCUs"
            },
            "localizedLinks": {
                "en": "/resource/en/reference_manual/rm0386-stm32f469xx-and-stm32f479xx-advanced-armbased-32bit-mcus-stmicroelectronics.pdf"
            },
            "resourcePath": "/content/ccc/resource/technical/document/reference_manual/29/77/09/5a/b1/60/4e/bd/DM00127514.pdf",
        },
        {
            "title": "RM0490",
            "version": "8.0",
            "latestUpdate": 1700000000000,
            "physicalResourceType": "reference_manual",
            "localizedDescriptions": {"en": "STM32C0 series advanced Arm-based 32-bit MCUs"},
            "localizedLinks": {
                "en": "/resource/en/reference_manual/rm0490-stm32c0-series-advanced-armbased-32bit-mcus-stmicroelectronics.pdf"
            },
            "resourcePath": "/content/ccc/resource/technical/document/reference_manual/rm0490.pdf",
        },
        # A datasheet row that must be filtered OUT (wrong physicalResourceType).
        {
            "title": "DS12930",
            "version": "1.0",
            "latestUpdate": 1700000000000,
            "physicalResourceType": "datasheet",
            "localizedDescriptions": {"en": "STM32C011xx datasheet"},
            "localizedLinks": {"en": "/resource/en/datasheet/ds12930-stmicroelectronics.pdf"},
            "resourcePath": "/content/ds12930.pdf",
        },
        # A malformed row (no title at all) -- must be skipped, not fatal.
        {
            "version": "1.0",
            "physicalResourceType": "reference_manual",
            "localizedLinks": {"en": "/resource/en/reference_manual/broken.pdf"},
        },
        # A row with no usable link at all -- must be skipped, not fatal.
        {
            "title": "RM9999",
            "version": "1.0",
            "physicalResourceType": "reference_manual",
            "localizedDescriptions": {"en": "STM32Z9 fake device"},
            "localizedLinks": {},
        },
    ],
}


# ------------------------------------------------------------------- URL building

def test_cxst_url_built_from_params_not_hardcoded():
    url = cxst_url(locale="en", class_id="CL1734", category="technical_literature", resource_type="reference_manual")
    assert url == (
        "https://www.st.com/bin/st/selectors/cxst/en.cxst-rs-grid.html/"
        "CL1734.technical_literature.reference_manual.json"
    )


def test_cxst_url_changes_with_different_resource_type():
    url = cxst_url(resource_type="datasheet")
    assert url.endswith("CL1734.technical_literature.datasheet.json")
    assert url != cxst_url()


def test_cxst_url_template_has_four_placeholders():
    for placeholder in ("{locale}", "{class_id}", "{category}", "{resource_type}"):
        assert placeholder in CXST_URL_TEMPLATE


# ------------------------------------------------------------- payload parsing

def test_parse_catalog_payload_field_mapping():
    entries = parse_catalog_payload(SAMPLE_PAYLOAD)
    by_rm = {e.rm_number: e for e in entries}

    assert set(by_rm) == {"RM0386", "RM0490"}  # datasheet + malformed rows excluded

    rm0386 = by_rm["RM0386"]
    assert rm0386.rev == "6.0"
    assert rm0386.pdf_url == (
        "https://www.st.com/resource/en/reference_manual/"
        "rm0386-stm32f469xx-and-stm32f479xx-advanced-armbased-32bit-mcus-stmicroelectronics.pdf"
    )
    assert rm0386.filename == (
        "rm0386-stm32f469xx-and-stm32f479xx-advanced-armbased-32bit-mcus-stmicroelectronics.pdf"
    )
    assert rm0386.slug == (
        "rm0386-stm32f469xx-and-stm32f479xx-advanced-armbased-32bit-mcus-stmicroelectronics"
    )
    assert rm0386.devices == ["STM32F469xx", "STM32F479xx"]
    assert rm0386.series == ["STM32F4"]
    assert rm0386.title == "STM32F469xx and STM32F479xx advanced Arm-based 32-bit MCUs"
    assert rm0386.resource_path.endswith("DM00127514.pdf")


def test_parse_catalog_payload_filters_non_reference_manual_rows():
    entries = parse_catalog_payload(SAMPLE_PAYLOAD)
    assert all(e.rm_number != "DS12930" for e in entries)


def test_parse_catalog_payload_epoch_ms_to_iso():
    entries = parse_catalog_payload(SAMPLE_PAYLOAD)
    rm0386 = next(e for e in entries if e.rm_number == "RM0386")
    # 1716156000000 ms -> 2024-05-19T00:00:00+00:00 (UTC)
    assert rm0386.updated.startswith("2024-05-19")


def test_parse_catalog_payload_locale_fallback_chain():
    payload = {
        "rows": [
            {
                "title": "RM1234",
                "version": "1.0",
                "physicalResourceType": "reference_manual",
                "localizedDescriptions": {"fr": "Description francaise"},
                "localizedLinks": {"fr": "/resource/fr/reference_manual/rm1234-stmicroelectronics.pdf"},
            }
        ]
    }
    # requested locale "en" isn't present -> falls back through "en" (absent
    # here too) -> first available value ("fr").
    entries = parse_catalog_payload(payload, locale="en")
    assert len(entries) == 1
    assert entries[0].pdf_url.endswith("rm1234-stmicroelectronics.pdf")
    assert entries[0].title == "Description francaise"


def test_parse_catalog_payload_malformed_rows_are_non_fatal():
    payload = {"rows": [
        {"title": "RM0001", "physicalResourceType": "reference_manual"},  # no link at all
        None,  # not even a dict
        {"garbage": True},
        SAMPLE_PAYLOAD["rows"][1],  # one genuinely good row (RM0490)
    ]}
    entries = parse_catalog_payload(payload)
    assert [e.rm_number for e in entries] == ["RM0490"]


def test_parse_catalog_payload_relative_link_gets_absolute_prefix():
    entries = parse_catalog_payload(SAMPLE_PAYLOAD)
    assert all(e.pdf_url.startswith("https://www.st.com/") for e in entries)


# --------------------------------------------------- build_catalog precedence

def test_build_catalog_reuses_cache_without_any_network_call(tmp_path, monkeypatch):
    cache_path = tmp_path / "catalog.json"
    cache_path.write_text(json.dumps({"scraped_at": "x", "manuals": [{"rm_number": "RM0001"}]}))

    def _boom(*a, **kw):
        raise AssertionError("network should not be hit when a cache exists and refresh=False")

    monkeypatch.setattr(catalog_mod, "fetch_catalog_payload", _boom)

    result = build_catalog(cache_path, refresh=False)
    assert result["manuals"] == [{"rm_number": "RM0001"}]


def test_build_catalog_refresh_fetches_and_writes_cache(tmp_path, monkeypatch):
    cache_path = tmp_path / "catalog.json"
    cache_path.write_text(json.dumps({"scraped_at": "x", "manuals": [{"rm_number": "RM0001"}]}))

    monkeypatch.setattr(catalog_mod, "fetch_catalog_payload", lambda *a, **kw: SAMPLE_PAYLOAD)

    result = build_catalog(cache_path, refresh=True)
    assert {m["rm_number"] for m in result["manuals"]} == {"RM0386", "RM0490"}
    assert json.loads(cache_path.read_text())["manuals"] == result["manuals"]


def test_build_catalog_no_refresh_builds_automatically_when_no_cache_exists(tmp_path, monkeypatch):
    cache_path = tmp_path / "catalog.json"
    monkeypatch.setattr(catalog_mod, "fetch_catalog_payload", lambda *a, **kw: SAMPLE_PAYLOAD)

    result = build_catalog(cache_path, refresh=False)  # no cache yet -> fetches anyway
    assert len(result["manuals"]) == 2


def test_build_catalog_api_failure_with_existing_cache_warns_and_keeps_cache(tmp_path, monkeypatch, caplog):
    cache_path = tmp_path / "catalog.json"
    good = {"scraped_at": "x", "manuals": [{"rm_number": "RM0001"}]}
    cache_path.write_text(json.dumps(good))

    def _raises(*a, **kw):
        raise RuntimeError("network down")

    monkeypatch.setattr(catalog_mod, "fetch_catalog_payload", _raises)

    import logging
    with caplog.at_level(logging.WARNING):
        result = build_catalog(cache_path, refresh=True)

    assert result == good
    assert json.loads(cache_path.read_text()) == good  # cache on disk untouched
    assert "using existing cache" in caplog.text


def test_build_catalog_api_failure_with_no_cache_raises(tmp_path, monkeypatch):
    cache_path = tmp_path / "catalog.json"

    def _raises(*a, **kw):
        raise RuntimeError("network down")

    monkeypatch.setattr(catalog_mod, "fetch_catalog_payload", _raises)

    with pytest.raises(RuntimeError):
        build_catalog(cache_path, refresh=True)


def test_build_catalog_empty_payload_with_no_cache_raises(tmp_path, monkeypatch):
    cache_path = tmp_path / "catalog.json"
    monkeypatch.setattr(catalog_mod, "fetch_catalog_payload", lambda *a, **kw: {"rows": []})

    with pytest.raises(RuntimeError):
        build_catalog(cache_path, refresh=True)


def test_load_catalog_returns_none_when_missing(tmp_path):
    assert load_catalog(tmp_path / "nope.json") is None


def test_load_catalog_returns_none_when_corrupt(tmp_path):
    p = tmp_path / "catalog.json"
    p.write_text("{not json")
    assert load_catalog(p) is None


# ---------------------------------------------------------------------- verify

def test_verify_catalog_flags_non_200_responses(monkeypatch):
    from stm32fetch.catalog import verify_catalog

    class FakeResp:
        def __init__(self, status_code):
            self.status_code = status_code
            self.headers = {}

    class FakeSession:
        def request(self, method, url, **kwargs):
            if "0490" in url:
                return FakeResp(200)
            return FakeResp(404)

    catalog = {
        "manuals": [
            {"rm_number": "RM0490", "pdf_url": "https://www.st.com/resource/en/reference_manual/rm0490.pdf"},
            {"rm_number": "RM0001", "pdf_url": "https://www.st.com/resource/en/reference_manual/renamed-slug.pdf"},
        ]
    }
    problems = verify_catalog(catalog, session=FakeSession())
    assert problems == [("RM0001", "https://www.st.com/resource/en/reference_manual/renamed-slug.pdf", 404)]


def test_verify_catalog_all_reachable_reports_nothing():
    from stm32fetch.catalog import verify_catalog

    class FakeResp:
        status_code = 200
        headers = {}

    class FakeSession:
        def request(self, method, url, **kwargs):
            return FakeResp()

    catalog = {"manuals": [{"rm_number": "RM0490", "pdf_url": "https://www.st.com/x.pdf"}]}
    assert verify_catalog(catalog, session=FakeSession()) == []
