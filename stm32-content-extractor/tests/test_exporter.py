"""The §6 record schema and the §7 filename scheme."""

from __future__ import annotations

from rmcontent.exporter import build_document, build_record, section_sort_key
from rmcontent.sections import Section
from rmcontent.split import section_filename

META = {
    "name_datasheet": "RM0490",
    "rev": "Rev 6",
    "url_pdf": "https://www.st.com/resource/en/reference_manual/rm0490.pdf",
    "references": "STM32C0",
    "package": "",
    "family": "C0",
    "core": "Arm 32-bit Cortex-M0+ CPU",
    "frequency": "",
}

CHAPTERS = {"4": ("Embedded flash memory (FLASH)", 56)}


def flash_acr_section():
    return Section(
        number="4.7.1",
        title="FLASH access control register (FLASH_ACR)",
        page=77,
        page_end=78,
        lines=[
            "Address offset: 0x000",
            "Reset value: 0x0000 0000",
            "Bits 31:1 Reserved, must be kept at reset value.",
            "Bit 0 CEN: Counter enable",
        ],
    )


def test_record_shape(fake_contents):
    record = build_record(flash_acr_section(), META, fake_contents(CHAPTERS))
    assert record["section_id"] == "RM0490-S4.7.1"
    assert record["chapter"] == "4"
    assert record["chapter_title"] == "Embedded flash memory (FLASH)"
    assert record["level"] == 3
    assert record["parent_section"] == "4.7"
    assert record["page"] == 77
    assert record["page_end"] == 78
    assert record["semantic_type"] == "register_description"
    assert record["chars"] == len(record["section_content"])
    assert record["url"] == META["url_pdf"] + "#page=77"


def test_document_rev_and_url_are_duplicated_onto_the_record(fake_contents):
    """`rootTagPath: sections` means the processor never sees the
    envelope, so these have to be on the record itself."""
    record = build_record(flash_acr_section(), META, fake_contents(CHAPTERS))
    assert record["document"] == "RM0490"
    assert record["rev"] == "Rev 6"
    assert record["url_pdf"] == META["url_pdf"]


def test_text_helper(fake_contents):
    record = build_record(flash_acr_section(), META, fake_contents(CHAPTERS))
    assert record["text_helper"] == (
        'Section 4.7.1 "FLASH access control register (FLASH_ACR)" in chapter 4 '
        "(Embedded flash memory (FLASH)), RM0490 Rev 6, page 77."
    )


def test_level_2_section_has_no_parent(fake_contents):
    section = Section(number="4.7", title="FLASH registers", page=77, page_end=77)
    record = build_record(section, META, fake_contents(CHAPTERS))
    assert record["parent_section"] is None
    assert record["level"] == 2


def test_generic_section_has_empty_semantic(fake_contents):
    section = Section(
        number="11.2", title="DMA main features", page=223, page_end=223,
        lines=["The DMA offers 5 independently configurable channels."],
    )
    record = build_record(section, META, fake_contents({"11": ("DMA", 223)}))
    assert record["semantic_type"] == "generic"
    assert record["semantic"] == {}


def test_empty_section_is_emitted_with_zero_chars(fake_contents):
    section = Section(number="4.7", title="FLASH registers", page=77, page_end=77)
    record = build_record(section, META, fake_contents(CHAPTERS))
    assert record["section_content"] == ""
    assert record["chars"] == 0


def test_features_include_register_field_names(fake_contents):
    record = build_record(flash_acr_section(), META, fake_contents(CHAPTERS))
    assert "cen" in record["features"]
    assert "flash-acr" in record["features"]


def test_document_envelope(fake_contents):
    doc = build_document([flash_acr_section()], META, fake_contents(CHAPTERS))
    assert doc["section_count"] == 1
    assert doc["document"] == "RM0490"
    assert doc["family"] == "C0"
    assert list(doc)[-1] == "sections"


def test_sections_sort_numerically_not_lexically():
    numbers = ["4.10", "4.7", "4.7.1", "4.2"]
    assert sorted(numbers, key=section_sort_key) == ["4.2", "4.7", "4.7.1", "4.10"]


def test_section_filename_zero_pads_each_component():
    assert section_filename("RM0490_Rev6", "4.7.1") == "RM0490_Rev6_section_004_007_001"
    assert section_filename("RM0490_Rev6", "12.6") == "RM0490_Rev6_section_012_006"
    assert section_filename("RM0490_Rev6", "31.18") == "RM0490_Rev6_section_031_018"


def test_section_filenames_sort_in_reading_order():
    """Raw "4.7.1" strings do not; the zero-padded components do."""
    stems = [section_filename("S", n) for n in ("4.10", "4.2", "4.7.1", "4.7")]
    assert sorted(stems) == [
        section_filename("S", n) for n in ("4.2", "4.7", "4.7.1", "4.10")
    ]
