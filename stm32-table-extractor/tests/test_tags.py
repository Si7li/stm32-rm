import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from rmtables.tags import build_tags, slugify


def test_build_tags_matches_expected_table16_exactly():
    # Verified ground truth (expected_table16.json): keyword hits + REGNAME
    # header matches alone already produce 13 tags, so the table_name
    # fallback scan never runs (it would otherwise add a redundant "mass",
    # already covered by the "mass-erase" keyword phrase) and the "CPU" in
    # the compound header "CPU bus error" must NOT surface as a tag.
    headers = ["SEC_PROT", "PCROP", "WRP", "PCROP_RDP", "Comment", "WRPERR", "CPU bus error"]
    tags = build_tags("Mass erase overview", "FLASH main memory erase sequences", headers, [])
    assert tags == [
        "erase", "flash", "flash-protection", "mass-erase", "memory",
        "pcrop", "pcrop-rdp", "readout-protection", "sec-prot", "security",
        "write-protection", "wrp", "wrperr",
    ]
    assert "cpu" not in tags
    assert "mass" not in tags


def test_build_tags_harvests_register_column_names():
    headers = ["Offset", "Register", "31", "0"]
    rows = [["0x000", "FLASH_ACR", "Res.", "LATENCY"], ["0x004", "FLASH_KEYR", "", ""]]
    tags = build_tags("FLASH register map and reset values", "FLASH registers", headers, rows)
    assert "flash-acr" in tags
    assert "flash-keyr" in tags


def test_build_tags_strips_continued_marker_and_stopwords():
    tags = build_tags("FLASH register map and reset values (continued)", "", ["Offset"], [])
    assert "continued" not in tags
    for stop in ("register", "map", "values", "reset", "continued"):
        assert stop not in tags


def test_build_tags_fallback_only_fires_when_otherwise_tagless():
    # No keyword/REGNAME/harvested signal at all here -> fallback kicks in.
    tags = build_tags("Device dimensions overview", "", ["A", "B"], [])
    assert "dimensions" in tags


def test_build_tags_fallback_skipped_when_other_sources_already_hit():
    # "Flash memory organization" hits the "flash"/"memory" keyword entries
    # (added to reproduce expected_table16.json), so the table_name fallback
    # scan must not also run -- "organization" would otherwise appear too.
    tags = build_tags("Flash memory organization", "", ["A", "B"], [])
    assert tags == ["flash", "memory"]
    assert "organization" not in tags


def test_build_tags_may_be_empty_for_pure_numeric_table():
    tags = build_tags("123", "", ["31", "0"], [])
    assert tags == []


def test_slugify():
    assert slugify("FLASH_ACR") == "flash-acr"
    assert slugify("Mass Erase!!") == "mass-erase"
