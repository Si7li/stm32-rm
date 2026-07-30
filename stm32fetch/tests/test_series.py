import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from stm32fetch.series import (
    derive_series_from_devices,
    manuals_matching_series,
    normalize_series,
    suggest_series,
)


def test_normalize_series_various_query_forms():
    assert normalize_series("stm32c0") == "STM32C0"
    assert normalize_series("STM32C0") == "STM32C0"
    assert normalize_series("C0") == "STM32C0"
    assert normalize_series("c0") == "STM32C0"
    assert normalize_series("  h7 ") == "STM32H7"


def test_normalize_series_from_device_name_drops_specific_device_suffix():
    # STM32_FETCH_TASK.md's own examples.
    assert normalize_series("STM32F103xx") == "STM32F1"
    assert normalize_series("STM32H7Rxx") == "STM32H7"
    assert normalize_series("STM32F107xx") == "STM32F1"


def test_normalize_series_rejects_text_with_no_letters():
    assert normalize_series("") is None
    assert normalize_series("   ") is None
    assert normalize_series("1234") is None
    assert normalize_series("!!!") is None


def test_normalize_series_requires_exactly_one_letter_and_one_digit():
    # STM32FETCH_FINAL_SPEC.md §3: series is `STM32([A-Z]\d)` exactly --
    # a letter-only family with no digit (no such case in the current
    # catalog) doesn't normalize to anything.
    assert normalize_series("STM32WB") is None
    assert normalize_series("F4") == "STM32F4"


def test_derive_series_from_devices_dedupes_and_sorts():
    # STM32FETCH_FINAL_SPEC.md §3: "sorted unique".
    devices = ["STM32F103xx", "STM32F105xx", "STM32F107xx"]
    assert derive_series_from_devices(devices) == ["STM32F1"]

    devices2 = ["STM32H7Rxx", "STM32H7Sxx", "STM32F103xx"]
    assert derive_series_from_devices(devices2) == ["STM32F1", "STM32H7"]


def test_manuals_matching_series_matches_multiple_manuals():
    manuals = [
        {"rm_number": "RM0008", "series": ["STM32F1"]},
        {"rm_number": "RM0490", "series": ["STM32C0"]},
        {"rm_number": "RM0433", "series": ["STM32F4"]},
        {"rm_number": "RMXXXX", "series": ["STM32F1", "STM32F3"]},
    ]
    matches = manuals_matching_series(manuals, "f1")
    assert {m["rm_number"] for m in matches} == {"RM0008", "RMXXXX"}


def test_manuals_matching_series_no_match_returns_empty():
    manuals = [{"rm_number": "RM0008", "series": ["STM32F1"]}]
    assert manuals_matching_series(manuals, "STM32Z9") == []


def test_manuals_matching_series_unrecognized_query_returns_empty():
    manuals = [{"rm_number": "RM0008", "series": ["STM32F1"]}]
    assert manuals_matching_series(manuals, "") == []


def test_suggest_series_returns_close_matches_from_catalog():
    manuals = [{"series": ["STM32F1"]}, {"series": ["STM32F4"]}, {"series": ["STM32H7"]}]
    suggestions = suggest_series(manuals, "STM32F0")
    assert suggestions
    assert all(s in {"STM32F1", "STM32F4", "STM32H7"} for s in suggestions)
