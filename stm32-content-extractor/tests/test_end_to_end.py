"""End-to-end runs against the real RM0490 PDF.

Skipped cleanly when the manual is not present in `usermanuel/`.
"""

from __future__ import annotations

import json

import pytest

from rmcontent.cli import main
from rmcontent.split import section_filename


@pytest.fixture(scope="module")
def flash_run(rm0490_path, tmp_path_factory):
    """One real run over the FLASH register pages, split and combined."""
    out = tmp_path_factory.mktemp("flash")
    rc = main([
        str(rm0490_path),
        "-o", str(out / "doc.json"),
        "--pages", "76-80",
        "--split-sections",
        "--sections-dir", str(out / "sections"),
        "--log-level", "WARNING",
    ])
    assert rc == 0
    doc = json.loads((out / "doc.json").read_text())
    return doc, out / "sections"


def record(doc, number):
    return next(r for r in doc["sections"] if r["section"] == number)


def test_flash_acr_semantic(flash_run):
    """RM0490 4.7.1, the §5 worked example, straight out of the PDF."""
    doc, _ = flash_run
    r = record(doc, "4.7.1")
    assert r["semantic_type"] == "register_description"
    semantic = r["semantic"]
    assert semantic["register"] == "FLASH_ACR"
    assert semantic["address_offset"] == "0x000"

    dbg = next(f for f in semantic["fields"] if f["name"] == "DBG_SWEN")
    assert dbg["bits"] == "18"
    assert [v["meaning"] for v in dbg["values"]] == [
        "Debugger disabled", "Debugger enabled",
    ]

    latency = next(f for f in semantic["fields"] if f["name"] == "LATENCY[2:0]")
    assert latency["bits"] == "2:0"

    assert any(f["name"] == "Res." for f in semantic["fields"])


def test_flash_acr_fields_cover_31_to_0(flash_run):
    from rmcontent.registers import check_bit_coverage

    doc, _ = flash_run
    assert check_bit_coverage(record(doc, "4.7.1")["semantic"]) == ""


def test_flash_acr_chapter_resolved_from_contents(flash_run):
    doc, _ = flash_run
    r = record(doc, "4.7.1")
    assert r["chapter"] == "4"
    assert r["chapter_title"] == "Embedded flash memory (FLASH)"


def test_parent_keeps_only_its_own_preamble(flash_run):
    """4.7 must not repeat 4.7.1's body."""
    doc, _ = flash_run
    parent = record(doc, "4.7")
    assert "Address offset: 0x000" not in parent["section_content"]


def test_noise_is_absent_from_the_body(flash_run):
    doc, _ = flash_run
    content = record(doc, "4.7.1")["section_content"]
    assert "31 30 29 28" not in content  # bit-layout diagram row
    assert "77/1023" not in content  # page footer
    assert "\ns\n" not in content  # rotated-label glyph


def test_chapters_are_not_records(flash_run):
    doc, _ = flash_run
    assert all("." in r["section"] for r in doc["sections"])


def test_combined_and_split_are_deeply_equal(flash_run):
    """Every per-section file's record must be identical to its entry in
    the combined document -- there is one build path, and this proves it."""
    doc, sections_dir = flash_run
    manual_dir = next(p for p in sections_dir.iterdir() if p.is_dir())
    stem = manual_dir.name

    for r in doc["sections"]:
        path = manual_dir / (section_filename(stem, r["section"]) + ".json")
        assert path.exists(), f"missing per-section file for {r['section']}"
        per_section = json.loads(path.read_text())
        assert per_section["sections"] == [r]
        # Same envelope, so one Root Tag Path works in both upload modes.
        for key, value in doc.items():
            if key != "sections":
                assert per_section[key] == value


def test_index_carries_the_readable_section_number(flash_run):
    doc, sections_dir = flash_run
    manual_dir = next(p for p in sections_dir.iterdir() if p.is_dir())
    index = json.loads((manual_dir / "_index.json").read_text())
    entry = next(e for e in index["sections"] if e["section"] == "4.7.1")
    assert entry["file"].endswith("_section_004_007_001.json")
    assert entry["section_title"] == "FLASH access control register (FLASH_ACR)"
    assert entry["semantic_type"] == "register_description"
    assert index["section_count"] == len(doc["sections"])


def test_output_is_auto_named_from_document_and_rev(rm0490_path, tmp_path):
    rc = main([
        str(rm0490_path), "-o", str(tmp_path),
        "--pages", "76-78", "--log-level", "WARNING",
    ])
    assert rc == 0
    assert (tmp_path / "RM0490_Rev6.json").exists()


def test_metadata_overrides_win(rm0490_path, tmp_path):
    rc = main([
        str(rm0490_path), "-o", str(tmp_path / "d.json"),
        "--pages", "76-78", "--document", "RM9999", "--rev", "Rev 1",
        "--family", "ZZ", "--log-level", "WARNING",
    ])
    assert rc == 0
    doc = json.loads((tmp_path / "d.json").read_text())
    assert doc["document"] == "RM9999"
    assert doc["rev"] == "Rev 1"
    assert doc["family"] == "ZZ"
    assert doc["sections"][0]["section_id"].startswith("RM9999-S")


def test_stale_per_section_files_are_pruned(rm0490_path, tmp_path):
    sections_dir = tmp_path / "sections"
    args = [
        str(rm0490_path), "-o", str(tmp_path / "d.json"),
        "--pages", "76-80", "--split-sections",
        "--sections-dir", str(sections_dir), "--log-level", "WARNING",
    ]
    assert main(args) == 0
    manual_dir = next(p for p in sections_dir.iterdir() if p.is_dir())
    stale = manual_dir / "RM0490_Rev6_section_999_999_999.json"
    stale.write_text("{}")
    assert main(args) == 0
    assert not stale.exists()
    assert main(args + ["--no-prune"]) == 0
    stale.write_text("{}")
    assert main(args + ["--no-prune"]) == 0
    assert stale.exists()


@pytest.mark.slow
def test_full_manual_completes_without_oom(rm0490_path, tmp_path):
    """A naive full run OOMs around page 800 because pdfplumber caches
    every page; `rmtables.extract.flush_page` per page is what prevents
    it. Deselect with `-m 'not slow'`."""
    rc = main([
        str(rm0490_path), "-o", str(tmp_path / "full.json"),
        "--validate", "--log-level", "WARNING",
    ])
    assert rc == 0
    doc = json.loads((tmp_path / "full.json").read_text())
    assert doc["section_count"] > 800
    assert doc["section_count"] == len(doc["sections"])


# -- subscript line merging (SUBSCRIPT_LINE_MERGE_FIX) ----------------------


@pytest.fixture(scope="module")
def rm0486_path():
    from conftest import find_manual

    path = find_manual("rm0486")
    if path is None:
        pytest.skip("RM0486 PDF not available in usermanuel/")
    return path


def test_a_subscript_is_merged_into_its_baseline_line(rm0486_path):
    """RM0486 13.4 page 350, the case the fix is measured on. At the
    default tolerance `V_BAT` and `V_DD` split into a bare `V` plus an
    orphan `BAT DD` line."""
    import pdfplumber

    from rmcontent.lines import page_lines

    with pdfplumber.open(rm0486_path) as pdf:
        page = pdf.pages[349]
        default = [l["text"] for l in page.extract_text_lines()]
        merged = [l["text"] for l in page_lines(page)]

    assert "BAT DD" in default
    assert "• V : optional external power supply for backup domain when V is not present" \
        in default

    assert "BAT DD" not in merged
    assert "BAT" not in merged
    assert (
        "• VBAT: optional external power supply for backup domain when VDD is not present"
        in merged
    )


def test_the_tolerance_sits_on_a_plateau(rm0486_path):
    """5 and 7 give byte-identical output -- not a knife-edge."""
    import pdfplumber

    with pdfplumber.open(rm0486_path) as pdf:
        page = pdf.pages[349]
        at5 = [l["text"] for l in page.extract_text_lines(y_tolerance=5)]
        at7 = [l["text"] for l in page.extract_text_lines(y_tolerance=7)]
    assert at5 == at7


def test_body_lines_a_full_line_apart_are_not_merged(rm0486_path):
    """The subscript offset is ~3.7 pt against ~12 pt body line spacing,
    so no two consecutive body lines may be absorbed into one."""
    import pdfplumber

    from rmcontent.lines import DEFAULT_Y_TOLERANCE, page_lines

    with pdfplumber.open(rm0486_path) as pdf:
        page = pdf.pages[349]
        lines = page_lines(page)

    tops = sorted(l["top"] for l in lines)
    gaps = [b - a for a, b in zip(tops, tops[1:])]
    assert min(gaps) > DEFAULT_Y_TOLERANCE
    # The bullet and the line under it stay separate.
    texts = [l["text"] for l in lines]
    assert any(t.startswith("• VBAT:") for t in texts)
    assert "(VBATmode)" in texts


def test_a_heading_is_still_detected_and_not_merged(rm0486_path):
    """Heading detection is the thing that would break if adjacent body
    lines merged, so assert it survives on a real page."""
    import pdfplumber

    from rmtables.headings import parse_heading

    from rmcontent.lines import page_lines

    with pdfplumber.open(rm0486_path) as pdf:
        page = pdf.pages[349]
        texts = [l["text"] for l in page_lines(page)]

    assert "13.4 Power supplies" in texts
    assert parse_heading("13.4 Power supplies") == ("13.4", "Power supplies")


def test_register_field_prose_is_not_merged_with_its_description(rm0490_path):
    """`Bits 31:19 Reserved...` and the description under it must stay
    two lines -- merging them would corrupt every register."""
    import pdfplumber

    from rmcontent.lines import page_lines
    from rmcontent.registers import FIELD_RE, _parse_field_head

    def field_lines(texts):
        return [t for t in texts
                if (m := FIELD_RE.match(t)) and _parse_field_head(m.group(1), m.group(2))]

    with pdfplumber.open(rm0490_path) as pdf:
        page = pdf.pages[76]  # RM0490 4.7.1 FLASH_ACR
        default = [l["text"] for l in page.extract_text_lines()]
        merged = [l["text"] for l in page_lines(page)]

    assert "Bits 31:19 Reserved, must be kept at reset value." in merged
    assert "Bit 18 DBG_SWEN: Debug access software enable" in merged
    assert "Software may use this bit to enable/disable the debugger read access." in merged
    # The merge must leave the register grammar untouched, line for line.
    assert field_lines(merged) == field_lines(default)
    assert len(field_lines(merged)) == 8


def test_the_tolerance_is_overridable(rm0486_path):
    import pdfplumber

    from rmcontent.lines import page_lines

    with pdfplumber.open(rm0486_path) as pdf:
        page = pdf.pages[349]
        loose = [l["text"] for l in page_lines(page)]
        tight = [l["text"] for l in page_lines(page, y_tolerance=0)]
    assert "BAT DD" in tight
    assert "BAT DD" not in loose


def test_the_merge_loses_no_characters(rm0486_path):
    """Criterion 7, isolated from every downstream filter: the merge is a
    MERGE, so the page's character multiset must be preserved exactly.

    Compared as a multiset, not a sequence -- merging reorders a line's
    chars by x, which changes the stream without losing anything.
    """
    import re
    from collections import Counter

    import pdfplumber

    from rmcontent.lines import page_lines

    def bag(lines):
        return Counter(re.sub(r"\s+", "", " ".join(l["text"] for l in lines)))

    with pdfplumber.open(rm0486_path) as pdf:
        for index in (349, 350, 3264, 600, 1200):
            page = pdf.pages[index]
            before = bag(page.extract_text_lines())
            after = bag(page_lines(page))
            assert not (before - after), f"page {index + 1} lost {before - after}"
            page.flush_cache()


def test_a_bit_range_split_by_the_merge_still_parses(rm0486_path):
    """RM0486 64.16.20's description mentions I2C with a superscript 2;
    merging it perturbs word splitting so the field head renders as
    `Bits 3 1 :24`. The register grammar absorbs the split, exactly as
    `rmtables` absorbs a split table number."""
    import pdfplumber

    from rmcontent.lines import page_lines
    from rmcontent.registers import FIELD_RE, _parse_field_head

    with pdfplumber.open(rm0486_path) as pdf:
        page = pdf.pages[3264]
        texts = [l["text"] for l in page_lines(page)]

    line = next(t for t in texts if t.strip().startswith("Bits 3"))
    assert "Bits 3 1 :24" in line
    m = FIELD_RE.match(line.strip())
    assert m and _parse_field_head(m.group(1), m.group(2))[0] == "31:24"
