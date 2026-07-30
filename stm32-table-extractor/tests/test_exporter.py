import json
import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from rmtables.exporter import (
    MAX_HEADER_NAMES,
    MAX_REGISTER_NAMES,
    _build_text,
    _clean_legend_entry,
    _collapse_trailing_punctuation,
    _is_wide_table,
    _make_id,
    _normalize_ws,
    _register_names_from_semantic,
    build_document,
    doc_stem,
    table_to_schema,
)
from rmtables.model import LogicalTable

HERE = os.path.dirname(__file__)
TARGET_TABLE16 = os.path.join(HERE, "..", "..", "target_table16_shape.json")

DOCUMENT = "RM0490"
REV = "Rev 6"
URL_PDF = "https://www.st.com/resource/en/reference_manual/rm0490.pdf"

# SIDEKICK_FORMAT_TASK.md §2: flat record, no `metadata` object.
# RENAME_FIELDS_TASK.md: id -> table_id, text -> text_helper, tags -> features.
EXPECTED_RECORD_KEYS = {
    "table_id", "document", "rev", "table_number", "title", "page", "section",
    "section_title", "semantic_type", "features", "url", "url_pdf", "columns",
    "text_helper", "table_content",
}
EXPECTED_CONTENT_KEYS = {"headers", "rows", "notes", "legend", "semantic_type", "semantic"}


def _table16_logical_table():
    header = ["SEC_PROT", "PCROP", "WRP", "PCROP_RDP", "Comment", "WRPERR", "CPU bus error"]
    rows = [
        header,
        ["0", "No", "No", "x", "Memory is erased", "No", "No"],
        ["0", "No", "Yes", "x", "Erase aborted (no erase started)", "Yes", "No"],
        ["0", "Yes", "No", "x", "Erase aborted (no erase started)", "Yes", "No"],
        ["0", "Yes", "Yes", "x", "Erase aborted (no erase started)", "Yes", "No"],
        ["1", "x", "x", "x", "Erase aborted (no erase started)", "No", "Yes"],
    ]
    return LogicalTable(
        table_number=16,
        caption="Mass erase overview",
        page_start=62,
        page_end=62,
        spans_pages=False,
        rows=rows,
        section_number="4.3.5",
        section_title="FLASH main memory erase sequences",
        notes=[],
    )


def _schema(lt, document=DOCUMENT, rev=REV, url_pdf=URL_PDF):
    return table_to_schema(lt, document, rev, url_pdf)


def test_table_to_schema_key_structure_and_types():
    lt = _table16_logical_table()
    t = _schema(lt)

    # Flat record (SIDEKICK_FORMAT_TASK.md §2): no `metadata` object.
    assert set(t.keys()) == EXPECTED_RECORD_KEYS
    assert set(t["table_content"].keys()) == EXPECTED_CONTENT_KEYS
    assert "metadata" not in t
    assert "filters" not in t
    # CLEANUP_TASK.md §1: no `units` anywhere in the record.
    assert "units" not in t
    assert "units" not in t["table_content"]

    assert isinstance(t["table_number"], str)
    assert isinstance(t["page"], int)
    assert t["columns"] == t["table_content"]["headers"]
    assert t["semantic_type"] == t["table_content"]["semantic_type"]
    # header must NOT be duplicated into table_content.rows
    assert t["table_content"]["headers"] not in t["table_content"]["rows"]
    assert all(cell is not None for row in t["table_content"]["rows"] for cell in row)


def test_table_to_schema_matches_target_table16_shape_exactly():
    lt = _table16_logical_table()
    with open(TARGET_TABLE16) as f:
        expected = json.load(f)
    t = table_to_schema(lt, expected["document"], expected["rev"], expected["url_pdf"])
    assert t == expected


def test_table_to_schema_replaces_none_cells_with_empty_string():
    # A ragged same-page merge can pad with None (merge.py's _pad_row);
    # the schema requires "" for every missing cell, never null.
    lt = LogicalTable(
        table_number=24, caption="Note table", page_start=75, page_end=75,
        spans_pages=False, rows=[["a", "b"], ["c", None]], notes=[],
    )
    t = _schema(lt)
    assert t["table_content"]["rows"] == [["c", ""]]


def test_is_wide_table_detects_bit_number_headers():
    # TEXT_HELPER_FIX.md: renamed from _is_register_map -- pure geometry,
    # used ONLY to pick Shape B's template, never to assert register-map-ness.
    assert _is_wide_table(["Offset", "Register", "31", "30", "29", "0"]) is True
    assert _is_wide_table(["SEC_PROT", "PCROP", "WRP", "Comment"]) is False


def test_register_names_from_semantic_dedupes_and_skips_blanks():
    # TEXT_HELPER_FIX.md: sourced directly from semantic["registers"][*]["name"]
    # -- no header scanning, no "Reset value"/"Reserved" filtering needed
    # since those never become named entries in extract_register_map's output.
    semantic = {
        "registers": [
            {"name": "FLASH_ACR"},
            {"name": ""},
            {"name": "FLASH_ACR"},
            {"name": "FLASH_KEYR"},
        ]
    }
    assert _register_names_from_semantic(semantic) == ["FLASH_ACR", "FLASH_KEYR"]


def test_register_names_from_semantic_empty_when_no_registers():
    assert _register_names_from_semantic({}) == []
    assert _register_names_from_semantic({"registers": []}) == []


# ------------------------------------------------------- RESTRUCTURE_TASK.md
# Value-preservation: every field the OLD shapes had must still be present,
# unaltered, somewhere in the current flat record.

def test_restructure_preserves_every_old_field_value():
    from rmtables.semantic import extract_semantic
    from rmtables.semantic_classify import classify_table
    from rmtables.tags import build_tags

    lt = _table16_logical_table()

    headers = lt.rows[0]
    body = lt.rows[1:]
    name, section, section_title, page, n = (
        lt.caption, lt.section_number, lt.section_title, lt.page_start, lt.table_number,
    )
    signal_type, _ = classify_table(name, headers, body)
    semantic_type, semantic = extract_semantic(signal_type, headers, body)

    old = {
        "table_name": name,
        "table_number": str(n),
        "page": page,
        "section": section,
        "section_title": section_title,
        "tags": build_tags(name, section_title, headers, body),
        "text": _build_text(n, name, section, section_title, page, headers, body, [], semantic_type, semantic),
        "columns": headers,
        "url_to_table": f"{URL_PDF}#page={page}",
        "table_content": {
            "headers": headers,
            "rows": body,
            "notes": [],
            "legend": [],
            "semantic_type": semantic_type,
            "semantic": semantic,
        },
    }

    t = _schema(lt)

    # text (top) -> text_helper (top), value unchanged (RENAME_FIELDS_TASK.md)
    assert t["text_helper"] == old["text"]
    for key in ("headers", "rows", "notes", "legend", "semantic_type", "semantic"):
        assert t["table_content"][key] == old["table_content"][key]
    # metadata.* -> top-level, table_name renamed to title, url_to_table to url
    assert t["title"] == old["table_name"]
    assert t["table_number"] == old["table_number"]
    assert t["page"] == old["page"]
    assert t["section"] == old["section"]
    assert t["section_title"] == old["section_title"]
    assert t["features"] == old["tags"]
    assert t["url"] == old["url_to_table"]
    assert t["columns"] == old["columns"]
    assert t["semantic_type"] == old["table_content"]["semantic_type"]

    assert set(t.keys()) == EXPECTED_RECORD_KEYS
    assert "units" not in t and "units" not in t["table_content"]
    assert "metadata" not in t
    assert "filters" not in t


# ----------------------------------------------------------- CLEANUP_TASK.md §2
# No doubled sentence-final punctuation in `text_helper`.

def test_collapse_trailing_punctuation_helper():
    assert _collapse_trailing_punctuation("abc..") == "abc."
    assert _collapse_trailing_punctuation("abc. .") == "abc."
    assert _collapse_trailing_punctuation("abc...") == "abc."
    assert _collapse_trailing_punctuation("abc.") == "abc."  # untouched: not doubled
    assert _collapse_trailing_punctuation("abc") == "abc"
    assert _collapse_trailing_punctuation("abc.  ") == "abc."  # trailing whitespace stripped too


def test_text_never_doubles_period_when_appended_note_already_ends_with_one():
    lt = LogicalTable(
        table_number=5, caption="Foo", page_start=10, page_end=10,
        spans_pages=False, rows=[["A", "B"], ["1", "2"]],
        notes=["Depending on the M bit value."],
    )
    t = _schema(lt)
    assert not t["text_helper"].endswith("..")
    assert t["text_helper"].endswith("value.")


def test_register_map_text_also_never_doubles_period():
    # A wide (>12 column) header triggers the register-map template branch.
    headers = ["Offset", "Register"] + [str(i) for i in range(31, -1, -1)]
    lt = LogicalTable(
        table_number=7, caption="Some regs", page_start=1, page_end=1,
        spans_pages=False, rows=[headers, ["0x00", "FOO_CR"] + ["0"] * 32],
    )
    t = _schema(lt)
    assert not t["text_helper"].endswith("..")


# ----------------------------------------------------------- TEXT_HELPER_FIX.md
# Shape A (semantic_type == "register_map"), Shape B (wide but not a register
# map), Shape C (everything else) -- selected by semantic_type, not geometry.

def test_shape_a_enumerates_register_names_capped_at_12_with_and_n_more():
    names = [f"REG{i:02d}" for i in range(15)]
    semantic = {"registers": [{"name": n} for n in names]}
    text = _build_text(
        26, "FLASH register map", "4.7.14", "FLASH register map", 90,
        ["Offset", "Register"] + [str(i) for i in range(31, -1, -1)], [],
        [], "register_map", semantic,
    )
    assert "Register map: offsets, 32-bit field layout and reset values" in text
    assert "for 15 registers:" in text
    for n in names[:12]:
        assert n in text
    assert "and 3 more" in text
    for n in names[12:]:
        assert n not in text
    assert "the registers listed" not in text


def test_shape_a_empty_registers_list_has_no_placeholder_and_no_colon_clause():
    text = _build_text(
        7, "Some regs", "9.1", "Some section", 50,
        ["Offset", "Register"], [], [], "register_map", {"registers": []},
    )
    assert text == (
        'Table 7, "Some regs", in section 9.1 (Some section) on page 50. '
        "Register map: offsets, 32-bit field layout and reset values."
    )
    assert "the registers listed" not in text


def test_shape_b_wide_generic_table_has_no_register_map_phrasing():
    # RM0486 T20 "RISUP indexes" shape: 32 columns, not a register map.
    headers = [f"Col{i}" for i in range(32)]
    text = _build_text(
        20, "RISUP indexes", "3.1", "Some section", 40,
        headers, [["x"] * 32], [], "generic", {},
    )
    assert "register map" not in text.lower()
    assert "32 columns:" in text
    for h in headers[:8]:
        assert h in text
    assert ", +24 more" in text


def test_shape_b_majority_numeric_headers_generic_table_has_no_register_map_phrasing():
    # RM0490 T142 "DLC coding in FDCAN" shape: 8 columns, 7 numeric, generic.
    headers = ["DLC", "0", "1", "2", "3", "4", "5", "6"]
    rows = [["0", "0", "0", "0", "0", "0", "0", "0"]]
    text = _build_text(
        142, "DLC coding in FDCAN", "1.1", "Some section", 100,
        headers, rows, [], "generic", {},
    )
    assert "register map" not in text.lower()
    assert "8 columns:" in text
    for h in headers:
        assert h in text
    assert "1 data row(s)" in text


def test_shape_c_unchanged_golden_string():
    lt = LogicalTable(
        table_number=99, caption="Simple lookup", page_start=12, page_end=12,
        spans_pages=False,
        rows=[["Col A", "Col B", "Col C"], ["1", "2", "3"], ["4", "5", "6"]],
        section_number="2.1", section_title="Some section",
    )
    t = _schema(lt)
    assert t["text_helper"] == (
        'Table 99, "Simple lookup", in section 2.1 (Some section) on page 12. '
        "Columns: Col A, Col B, Col C. 2 data row(s)."
    )


def test_notes_appended_and_truncated_in_all_three_shapes():
    long_note = "N" * 300
    text_a = _build_text(
        1, "T", "1.1", "S", 1, ["Offset", "Register"], [],
        [long_note], "register_map", {"registers": [{"name": "REGX"}]},
    )
    text_b = _build_text(
        1, "T", "1.1", "S", 1, [f"C{i}" for i in range(20)], [["x"] * 20],
        [long_note], "generic", {},
    )
    text_c = _build_text(
        1, "T", "1.1", "S", 1, ["A", "B"], [["1", "2"]], [long_note], "generic", {},
    )
    for text in (text_a, text_b, text_c):
        assert " Notes: " in text
        assert not text.endswith("..")
        assert ("N" * 200) in text  # truncated at NOTES_TRUNCATE
        assert ("N" * 201) not in text


def test_shape_a_notes_ending_in_period_does_not_double():
    text = _build_text(
        1, "T", "1.1", "S", 1, ["Offset", "Register"], [],
        ["Some note already ending in a period."], "register_map",
        {"registers": [{"name": "REGX"}]},
    )
    assert not text.endswith("..")
    assert text.endswith("period.")


def test_shape_a_and_shape_b_cap_constants():
    assert MAX_REGISTER_NAMES == 12
    assert MAX_HEADER_NAMES == 8


# ----------------------------------------------------------- CLEANUP_TASK.md §3
# Whitespace trimming in headers/columns/title/section_title only.

def test_normalize_ws_helper():
    assert _normalize_ws("  PCE bit  ") == "PCE bit"
    assert _normalize_ws("a   b\t c") == "a b c"
    assert _normalize_ws(None) == ""
    assert _normalize_ws("") == ""


def test_headers_columns_and_titles_are_trimmed_and_collapsed():
    lt = LogicalTable(
        table_number=6, caption="  Mass   erase  overview ", page_start=1, page_end=1,
        spans_pages=False,
        rows=[["PCE bit ", " B  ", "C"], ["1", "2", "3"]],
        section_title=" Some   Title ",
    )
    t = _schema(lt)

    assert t["table_content"]["headers"] == ["PCE bit", "B", "C"]
    assert t["columns"] == ["PCE bit", "B", "C"]
    assert t["title"] == "Mass erase overview"
    assert t["section_title"] == "Some Title"
    assert t["columns"] == t["table_content"]["headers"]


def test_row_data_is_never_trimmed():
    lt = LogicalTable(
        table_number=8, caption="X", page_start=1, page_end=1,
        spans_pages=False,
        rows=[["A", "B"], ["  padded value  ", " x "]],
    )
    t = _schema(lt)
    assert t["table_content"]["rows"] == [["  padded value  ", " x "]]


# ----------------------------------------------------------- CLEANUP_TASK.md §4
# notes/legend never hold the identical string.

def test_clean_legend_entry_strips_numbered_footnote_prefix():
    assert (
        _clean_legend_entry("3. Legends: SB: start bit, STB: stop bit, PB: parity bit.")
        == "SB: start bit, STB: stop bit, PB: parity bit."
    )
    assert _clean_legend_entry("(2) Legend: A = alpha, B = beta") == "A = alpha, B = beta"


def test_clean_legend_entry_leaves_standalone_legend_line_unchanged():
    assert _clean_legend_entry("A = alpha, B = beta") == "A = alpha, B = beta"


def test_footnote_legend_not_duplicated_between_notes_and_legend():
    footnote = "3. Legends: SB: start bit, STB: stop bit, PB: parity bit."
    lt = LogicalTable(
        table_number=9, caption="X", page_start=1, page_end=1,
        spans_pages=False, rows=[["A"], ["1"]],
        notes=[footnote], legend=[footnote],
    )
    t = _schema(lt)

    assert t["table_content"]["notes"] == [footnote]
    assert t["table_content"]["legend"] == ["SB: start bit, STB: stop bit, PB: parity bit."]
    assert t["table_content"]["notes"][0] != t["table_content"]["legend"][0]


def test_standalone_legend_table_behaves_as_before():
    lt = LogicalTable(
        table_number=10, caption="X", page_start=1, page_end=1,
        spans_pages=False, rows=[["A"], ["1"]],
        notes=[], legend=["A = alpha, B = beta"],
    )
    t = _schema(lt)
    assert t["table_content"]["legend"] == ["A = alpha, B = beta"]


# --------------------------------------------------------- SIDEKICK_FORMAT_TASK.md

def test_make_id_zero_pads_to_3_and_never_truncates():
    assert _make_id("RM0490", 38, 179) == "RM0490-T038"
    assert _make_id("RM0490", 6, 1) == "RM0490-T006"
    assert _make_id("RM0490", 1200, 1) == "RM0490-T1200"  # never truncated


def test_make_id_unnumbered_uses_page():
    assert _make_id("RM0490", None, 42) == "RM0490-Tp42"


def test_record_has_document_rev_url_pdf_for_self_sufficiency():
    # rootTagPath: tables means the processor sees nothing outside the
    # array -- every record must carry its own document identity.
    lt = _table16_logical_table()
    t = _schema(lt)
    assert t["document"] == DOCUMENT
    assert t["rev"] == REV
    assert t["url_pdf"] == URL_PDF
    assert t["table_id"] == "RM0490-T016"


def test_url_equals_url_pdf_plus_page_fragment():
    lt = _table16_logical_table()
    t = _schema(lt)
    assert t["url"] == f"{URL_PDF}#page={t['page']}"
    assert "#" not in t["url_pdf"]


LABEL_TEMPLATE = "{{document}}#Table{{table_number}}: {{title}}"
PLACEHOLDER_RE = re.compile(r"\{\{(\w+)\}\}")


def _render(template: str, record: dict) -> str:
    return PLACEHOLDER_RE.sub(lambda m: str(record.get(m.group(1), "")), template)


def test_label_template_renders_with_no_unresolved_placeholders():
    lt = _table16_logical_table()
    t = _schema(lt)
    rendered = _render(LABEL_TEMPLATE, t)
    assert "{{" not in rendered and "}}" not in rendered
    assert rendered != ""
    assert rendered == "RM0490#Table16: Mass erase overview"


def test_build_document_envelope_shape():
    lt = _table16_logical_table()
    meta = {
        "name_datasheet": DOCUMENT, "rev": REV, "url_pdf": URL_PDF,
        "references": "STM32C0", "package": "", "family": "C0",
        "core": "Arm 32-bit Cortex-M0+ CPU", "frequency": "",
    }
    doc = build_document([lt], meta)

    assert doc["document"] == DOCUMENT
    assert doc["rev"] == REV
    assert doc["url_pdf"] == URL_PDF
    assert doc["references"] == "STM32C0"
    assert doc["family"] == "C0"
    assert doc["core"] == "Arm 32-bit Cortex-M0+ CPU"
    assert doc["table_count"] == 1
    assert len(doc["tables"]) == 1
    assert "metadata" not in doc["tables"][0]
    assert "name_datasheet" not in doc
    assert "processorParams" not in doc


def test_build_document_assigns_unique_ids_even_on_duplicate_table_number():
    lt1 = LogicalTable(table_number=5, caption="A", page_start=1, page_end=1, spans_pages=False, rows=[["a"]])
    lt2 = LogicalTable(table_number=5, caption="B", page_start=2, page_end=2, spans_pages=False, rows=[["b"]])
    meta = {"name_datasheet": DOCUMENT, "rev": REV, "url_pdf": URL_PDF}
    doc = build_document([lt1, lt2], meta)

    ids = [t["table_id"] for t in doc["tables"]]
    assert len(ids) == len(set(ids))  # unique despite the shared table_number
    assert ids[0] == "RM0490-T005"
    assert ids[1] == "RM0490-T005_2"


# ------------------------------------------------------------- doc_stem
# FILENAME_SCHEME_TASK.md: the {RM}_{Rev} stem shared by the combined
# output file, the split-tables folder, and every per-table filename.

def test_doc_stem_strips_internal_space_from_rev():
    assert doc_stem("RM0490", "Rev 6") == "RM0490_Rev6"
    assert doc_stem("RM0008", "Rev 21") == "RM0008_Rev21"
    assert doc_stem("RM0477", "Rev 10") == "RM0477_Rev10"


def test_doc_stem_prefixes_rev_when_bare_number_given():
    assert doc_stem("RM0490", "6") == "RM0490_Rev6"
    assert doc_stem("RM0490", "6.0") == "RM0490_Rev6.0"


def test_doc_stem_keeps_dot_in_version_but_replaces_other_unsafe_chars():
    assert doc_stem("RM0490", "Rev 6.0 (draft)") == "RM0490_Rev6.0-draft-"


def test_doc_stem_omits_revision_segment_when_rev_missing(caplog):
    import logging

    with caplog.at_level(logging.WARNING, logger="rmtables.exporter"):
        stem = doc_stem("RM0490", "")
    assert stem == "RM0490"
    assert "rev is missing" in caplog.text.lower()


def test_doc_stem_falls_back_to_pdf_stem_when_document_missing(caplog):
    import logging

    with caplog.at_level(logging.WARNING, logger="rmtables.exporter"):
        stem = doc_stem("", "Rev 6", pdf_path="/x/y/rm0008-foo.pdf")
    assert stem == "rm0008-foo_Rev6"
    assert "document" in caplog.text.lower()


def test_doc_stem_falls_back_to_unknown_when_document_and_pdf_path_missing():
    assert doc_stem("", "Rev 6") == "UNKNOWN_Rev6"


def test_doc_stem_sanitizes_document_part():
    assert doc_stem("RM0490/../evil", "Rev 6") == "RM0490..evil_Rev6"


# ----------------------------------------------------- TITLE_FIDELITY_FIX.md

def _lt_with_caption(caption, table_number=1):
    lt = _table16_logical_table()
    lt.table_number = table_number
    lt.caption = caption
    return lt


def test_strip_footnote_markers_removes_pure_digit_groups():
    from rmtables.exporter import _strip_footnote_markers

    assert _strip_footnote_markers(
        "SDRAM address mapping with 8-bit data bus width(1)(2)"
    ) == "SDRAM address mapping with 8-bit data bus width"


def test_strip_footnote_markers_keeps_alphanumeric_parenthetical():
    from rmtables.exporter import _strip_footnote_markers

    assert _strip_footnote_markers(
        "Reset source identification (RCC_RSR)(1)"
    ) == "Reset source identification (RCC_RSR)"


def test_footnote_marker_stripped_from_title_end_to_end():
    lt = _lt_with_caption("Port bit configuration table(1)")
    t = _schema(lt)
    assert t["title"] == "Port bit configuration table"


def test_lot_repair_fires_for_damaged_body_caption():
    lt = _lt_with_caption("O utput control bits for complementary channels", table_number=82)
    list_of_tables = {82: ("Output control bits for complementary channels", 123)}
    t = table_to_schema(lt, DOCUMENT, REV, URL_PDF, list_of_tables)
    assert t["title"] == "Output control bits for complementary channels"


def test_lot_repair_does_not_fire_for_case_only_difference():
    # ST's own index inconsistently title-cases some ETH tables -- the body
    # caption (the actual printed text) wins on a case-only difference.
    lt = _lt_with_caption("ethernet mac register map", table_number=5)
    list_of_tables = {5: ("Ethernet MAC register map", 200)}
    t = table_to_schema(lt, DOCUMENT, REV, URL_PDF, list_of_tables)
    assert t["title"] == "ethernet mac register map"


def test_lot_repair_keeps_body_caption_when_identical():
    lt = _lt_with_caption("Memory map", table_number=9)
    list_of_tables = {9: ("Memory map", 50)}
    t = table_to_schema(lt, DOCUMENT, REV, URL_PDF, list_of_tables)
    assert t["title"] == "Memory map"


def test_table_absent_from_lot_keeps_body_caption_unchanged():
    lt = _lt_with_caption("A genuine extra ST left out of its own index", table_number=999)
    list_of_tables = {5: ("Some other table", 50)}
    t = table_to_schema(lt, DOCUMENT, REV, URL_PDF, list_of_tables)
    assert t["title"] == "A genuine extra ST left out of its own index"


def test_lot_repair_logs_info_on_body_caption_damage(caplog):
    import logging

    lt = _lt_with_caption("t timings depending on resolution", table_number=72)
    list_of_tables = {72: ("tSAR timings depending on resolution", 306)}
    with caplog.at_level(logging.INFO, logger="rmtables.exporter"):
        t = table_to_schema(lt, DOCUMENT, REV, URL_PDF, list_of_tables)
    assert t["title"] == "tSAR timings depending on resolution"
    assert "table 72 title taken from List of Tables" in caplog.text
    assert "body caption damaged" in caplog.text


def test_lot_repair_does_not_touch_table_number_or_page():
    # The LoT is known to be wrong at least once about which tables exist
    # (RM0522's phantom "T423 I3C instantiation") -- only the title text may
    # ever be taken from it.
    lt = _lt_with_caption("Damaged caption text here", table_number=423)
    list_of_tables = {423: ("I3C instantiation", 1690)}
    t = table_to_schema(lt, DOCUMENT, REV, URL_PDF, list_of_tables)
    assert t["title"] == "I3C instantiation"
    assert t["table_number"] == "423"
    assert t["page"] == lt.page_start
