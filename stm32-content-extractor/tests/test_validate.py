"""Reconciliation against the Contents pages."""

from __future__ import annotations

from rmcontent.exporter import build_document
from rmcontent.noise import NoiseCounts
from rmcontent.sections import Section
from rmcontent.validate import validate

META = {
    "name_datasheet": "RM0490", "rev": "Rev 6",
    "url_pdf": "https://example.invalid/rm0490.pdf",
    "references": "STM32C0", "package": "", "family": "C0",
    "core": "Arm 32-bit Cortex-M0+ CPU", "frequency": "",
}


class FakeScanner:
    def __init__(self):
        self.noise = NoiseCounts(headers_footers=2451, bit_layout_rows=746, stray_glyphs=330)
        self.uncaptioned_regions = 1089
        self.recovered_headings = ["17.3.19"]
        self.rejected_headings = ["61.44"]
        self.rejected_chapters = []


def section(number, title="T", lines=None):
    return Section(number=number, title=title, page=1, page_end=1, lines=lines or [])


def run(sections, contents_cls, chapters, listed):
    contents = contents_cls(chapters, {n: (t, 1) for n, t in listed.items()})
    doc = build_document(sections, META, contents)
    return validate(doc, contents, FakeScanner())


def test_missing_and_extra_are_both_reported(fake_contents):
    report = run(
        [section("4.1"), section("4.3")],
        fake_contents,
        {"4": ("Embedded flash memory (FLASH)", 56)},
        {"4.1": "A", "4.2": "B", "4.3": "C"},
    )
    assert report.missing_sections == ["4.2"]
    assert report.extra_sections == []
    assert report.extracted_count == 2
    assert report.contents_section_count == 3
    assert not report.is_clean()


def test_extra_section_not_listed_by_st(fake_contents):
    """ST really does omit sections from its own Contents (RM0490
    29.6.8, RM0522 47.6.8)."""
    report = run(
        [section("4.1"), section("4.2")],
        fake_contents, {"4": ("FLASH", 56)}, {"4.1": "A"},
    )
    assert report.extra_sections == ["4.2"]


def test_a_contents_chapter_with_no_record_at_all_is_flagged(fake_contents):
    """Every chapter must now yield at least its own chapter record; one
    with none means its level-1 heading was never found and all of its
    content is missing from the output."""
    report = run(
        [section("4"), section("4.1")],
        fake_contents,
        {"4": ("FLASH", 56), "21": ("Infrared interface (IRTIM)", 631)},
        {"4.1": "A"},
    )
    assert report.chapters_without_sections == ["21"]
    assert report.chapters_without_own_record == ["21"]
    assert report.chapter_records == ["4"]
    assert not report.is_clean()


def test_register_and_field_counts(fake_contents):
    body = [
        "Address offset: 0x000",
        "Reset value: 0x0000 0000",
        "Bits 31:1 Reserved, must be kept at reset value.",
        "Bit 0 CEN: Counter enable",
    ]
    report = run(
        [section("4.7.1", "FLASH access control register (FLASH_ACR)", body), section("4.7")],
        fake_contents, {"4": ("FLASH", 56)}, {"4.7": "R", "4.7.1": "A"},
    )
    assert report.register_description_count == 1
    assert report.field_count == 2
    assert report.named_field_count == 1
    assert report.coverage_errors == []


def test_narrow_register_is_separated_from_a_real_defect(fake_contents):
    narrow = [
        "Address offset: 0x00", "Reset value: 0x0000",
        "Bits 15:1 Reserved, must be kept at reset value.",
        "Bit 0 CEN: Counter enable",
    ]
    broken = [
        "Address offset: 0x04", "Reset value: 0x0000 0000",
        "Bits 31:16 A[15:0]: desc",
    ]
    report = run(
        [section("17.4.1", "TIM1 control register 1 (TIM1_CR1)", narrow),
         section("17.4.2", "TIM1 control register 2 (TIM1_CR2)", broken)],
        fake_contents, {"17": ("TIM1", 400)}, {"17.4.1": "A", "17.4.2": "B"},
    )
    assert len(report.coverage_errors) == 2
    assert [s for s, _, _, _ in report.narrow_registers] == ["17.4.1"]
    assert report.narrow_registers[0][2] == 16
    assert "16-bit register" in report.narrow_registers[0][3]
    assert [s for s, _, _ in report.real_coverage_errors] == ["17.4.2"]


def test_multi_register_sections_are_counted(fake_contents):
    body = [
        "Operating mode configuration register (ETH_MACCR)",
        "Address offset: 0x0000",
        "Bits 31:0 A[31:0]: desc",
        "Extended operating mode register (ETH_MACECR)",
        "Address offset: 0x0004",
        "Bits 31:0 B[31:0]: desc",
    ]
    report = run(
        [section("48.11.4", "Ethernet MAC and MMC registers", body)],
        fake_contents, {"48": ("Ethernet", 2311)}, {"48.11.4": "E"},
    )
    assert report.register_description_count == 0
    assert report.multi_register_sections == ["48.11.4"]


def test_oversized_and_empty_sections(fake_contents):
    report = run(
        [section("4.1", "Big", ["x" * 9000]), section("4.2", "Empty")],
        fake_contents, {"4": ("FLASH", 56)}, {"4.1": "A", "4.2": "B"},
    )
    assert report.oversized == [("4.1", 9000)]
    assert report.empty_sections == 1


def test_scanner_counters_reach_the_report(fake_contents):
    report = run([section("4.1")], fake_contents, {"4": ("FLASH", 56)}, {"4.1": "A"})
    assert report.recovered_headings == ["17.3.19"]
    assert report.rejected_headings == ["61.44"]
    assert report.uncaptioned_regions == 1089
    assert any("bit-layout diagram rows: 746" in s for s in report.noise_summary)
    assert "sections: 1 extracted" in report.summary()


def test_clean_report(fake_contents):
    report = run([section("4"), section("4.1")], fake_contents,
                 {"4": ("FLASH", 56)}, {"4.1": "A"})
    assert report.is_clean()
    assert report.chapter_records == ["4"]
