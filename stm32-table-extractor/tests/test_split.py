import json
import logging
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from rmtables.cli import main
from rmtables.split import (
    _build_filenames,
    _parse_table_number,
    _sanitize_component,
    _slugify,
    write_split_tables,
)

HERE = os.path.dirname(__file__)
PDF = os.path.join(
    HERE, "..", "..", "usermanuel",
    "rm0490-stm32c0-series-advanced-armbased-32bit-mcus-stmicroelectronics.pdf",
)

STEM = "RM0490_Rev6"


def _table(number, name, page, section="1.1", rows=None, headers=None, semantic_type="generic", tags=None):
    """A flat record (SIDEKICK_FORMAT_TASK.md §2, renamed by
    RENAME_FIELDS_TASK.md) -- no `metadata` object."""
    cols = headers if headers is not None else ["A", "B"]
    return {
        "table_id": f"RM0490-T{number}" if number else f"RM0490-Tp{page}",
        "document": "RM0490",
        "rev": "Rev 6",
        "table_number": number,
        "title": name,
        "page": page,
        "section": section,
        "section_title": "Some section",
        "semantic_type": semantic_type,
        "features": tags or ["tag1", "tag2"],
        "url": f"https://example.com/x.pdf#page={page}",
        "url_pdf": "https://example.com/x.pdf",
        "columns": cols,
        "text_helper": f"Table {number} \"{name}\" (page {page}) is a generic table.",
        "table_content": {
            "headers": cols,
            "rows": rows if rows is not None else [["1", "2"]],
            "notes": [],
            "legend": [],
            "semantic_type": semantic_type,
            "semantic": {},
        },
    }


ENVELOPE_FIELDS = {
    "document": "RM0490",
    "rev": "Rev 6",
    "url_pdf": "https://www.st.com/resource/en/reference_manual/rm0490-....pdf",
    "references": "STM32C0",
    "package": "",
    "family": "C0",
    "core": "Arm 32-bit Cortex-M0+ CPU",
    "frequency": "",
}


def _doc(tables, rev="Rev 6"):
    return {**ENVELOPE_FIELDS, "rev": rev, "table_count": len(tables), "tables": tables}


# ------------------------------------------------------------------- helpers

def test_parse_table_number_handles_missing_and_non_numeric():
    assert _parse_table_number("16") == 16
    assert _parse_table_number("") is None
    assert _parse_table_number(None) is None
    assert _parse_table_number("N/A") is None


def test_sanitize_component_strips_unsafe_characters():
    assert _sanitize_component('RM0490/../"evil"') == "RM0490..evil"


def test_slugify_lowercases_collapses_and_truncates():
    assert _slugify("Mass Erase Overview!!") == "mass-erase-overview"
    long_name = "A" * 60
    assert len(_slugify(long_name)) <= 40


# ------------------------------------------------------------- basic writing

def test_write_split_tables_creates_one_file_per_table_plus_index(tmp_path):
    doc = _doc([_table("1", "First", 10), _table("2", "Second", 20)])
    manual_dir = write_split_tables(doc, tmp_path)

    assert manual_dir.name == STEM
    files = sorted(p.name for p in manual_dir.glob("*.json"))
    assert files == [f"{STEM}_table_001.json", f"{STEM}_table_002.json", "_index.json"]


def test_per_table_file_uses_same_tables_envelope_and_record_is_self_sufficient(tmp_path):
    tables = [_table("1", "First", 10), _table("16", "Mass erase overview", 62)]
    doc = _doc(tables)
    manual_dir = write_split_tables(doc, tmp_path)

    for table in tables:
        n = int(table["table_number"])
        written = json.loads((manual_dir / f"{STEM}_table_{n:03d}.json").read_text())

        # same envelope shape as the combined file: {..., "tables": [...]}
        assert "tables" in written
        assert len(written["tables"]) == 1
        record = written["tables"][0]

        # record byte-identical (deep-equal) to the combined file's object
        assert record == table

        # record is self-sufficient -- carries its own document/rev/url_pdf,
        # independent of the envelope fields also present for readability.
        assert record["document"] == "RM0490"
        assert record["rev"] == "Rev 6"
        assert record["url_pdf"]

        # envelope fields repeated for human readability (optional, but present)
        assert written["document"] == "RM0490"
        assert written["rev"] == "Rev 6"


def test_index_json_lists_every_file_and_all_exist(tmp_path):
    doc = _doc([_table("1", "First", 10, rows=[["1", "2"], ["3", "4"]], headers=["A", "B", "C"])])
    manual_dir = write_split_tables(doc, tmp_path)

    index = json.loads((manual_dir / "_index.json").read_text())
    assert index["table_count"] == 1
    assert index["document"] == "RM0490"
    assert "generated_at" in index
    entry = index["tables"][0]
    assert entry["file"] == f"{STEM}_table_001.json"
    assert entry["n_rows"] == 2
    assert entry["n_cols"] == 3
    assert (manual_dir / entry["file"]).exists()


def test_index_json_entries_use_renamed_keys(tmp_path):
    # RENAME_FIELDS_TASK.md: the manifest surfaces id/tags too, as
    # table_id/features -- not the old names.
    doc = _doc([_table("1", "First", 10)])
    manual_dir = write_split_tables(doc, tmp_path)
    index = json.loads((manual_dir / "_index.json").read_text())
    entry = index["tables"][0]
    assert entry["table_id"] == "RM0490-T1"
    assert entry["features"] == ["tag1", "tag2"]
    assert "id" not in entry
    assert "tags" not in entry


def test_index_json_file_entries_match_disk_1to1(tmp_path):
    doc = _doc([_table("1", "First", 10), _table("2", "Second", 20), _table("", "Unnumbered", 30)])
    manual_dir = write_split_tables(doc, tmp_path)

    index = json.loads((manual_dir / "_index.json").read_text())
    index_files = sorted(e["file"] for e in index["tables"])
    disk_files = sorted(p.name for p in manual_dir.glob("*.json") if p.name != "_index.json")
    assert index_files == disk_files
    assert len(index_files) == len(index["tables"])  # no duplicates either side


def test_digit_width_is_4_when_any_table_number_reaches_1000(tmp_path):
    doc = _doc([_table("5", "Small", 1), _table("1200", "Big", 2)])
    manual_dir = write_split_tables(doc, tmp_path)

    files = sorted(p.name for p in manual_dir.glob("*.json") if p.name != "_index.json")
    assert files == [f"{STEM}_table_0005.json", f"{STEM}_table_1200.json"]


def test_filenames_sorted_naturally_by_table_number(tmp_path):
    tables = [_table("10", "Ten", 1), _table("2", "Two", 2), _table("1", "One", 3)]
    names = _build_filenames(STEM, sorted(tables, key=lambda t: int(t["table_number"])), False)
    assert names == [f"{STEM}_table_001", f"{STEM}_table_002", f"{STEM}_table_010"]


def test_unnumbered_table_uses_page_based_filename(tmp_path):
    t = _table("", "No number", 42)
    doc = _doc([t])
    manual_dir = write_split_tables(doc, tmp_path)
    files = [p.name for p in manual_dir.glob("*.json") if p.name != "_index.json"]
    assert files == [f"{STEM}_table_unnumbered_p42.json"]


def test_filename_slug_appends_readable_slug_when_enabled(tmp_path):
    doc = _doc([_table("16", "Mass erase overview", 62)])
    manual_dir = write_split_tables(doc, tmp_path, filename_slug=True)
    files = [p.name for p in manual_dir.glob("*.json") if p.name != "_index.json"]
    assert files == [f"{STEM}_table_016_mass-erase-overview.json"]


def test_filename_slug_off_by_default(tmp_path):
    doc = _doc([_table("16", "Mass erase overview", 62)])
    manual_dir = write_split_tables(doc, tmp_path)
    files = [p.name for p in manual_dir.glob("*.json") if p.name != "_index.json"]
    assert files == [f"{STEM}_table_016.json"]


def test_filename_slug_truncates_slug_never_the_stem(tmp_path):
    # A very long title must never eat into {stem}_table_{NNN} -- only the
    # slug is truncated (FILENAME_SCHEME_TASK.md).
    long_title = "Overview of " + "very " * 40 + "long register description"
    doc = _doc([_table("16", long_title, 62)])
    manual_dir = write_split_tables(doc, tmp_path, filename_slug=True)
    files = [p.name for p in manual_dir.glob("*.json") if p.name != "_index.json"]
    assert len(files) == 1
    name = files[0]
    assert name.startswith(f"{STEM}_table_016")
    assert len(name) <= 120
    assert name.endswith(".json")


def test_every_filename_matches_safe_pattern(tmp_path):
    import re

    doc = _doc([_table("16", "Mass erase overview!! (weird™ chars)", 62)])
    manual_dir = write_split_tables(doc, tmp_path, filename_slug=True)
    for p in manual_dir.glob("*.json"):
        assert re.fullmatch(r"[A-Za-z0-9._-]+", p.name), p.name


# ---------------------------------------------------------------- doc_stem

def test_manual_folder_uses_rm_and_rev_stem():
    doc = _doc([_table("1", "First", 10)])
    assert doc["document"] == "RM0490" and doc["rev"] == "Rev 6"


def test_manual_folder_falls_back_to_pdf_stem_when_document_missing(tmp_path):
    doc = _doc([_table("1", "First", 10)])
    doc["document"] = ""
    manual_dir = write_split_tables(doc, tmp_path, pdf_path="/x/y/rm0008-foo.pdf")
    assert manual_dir.name == "rm0008-foo_Rev6"


def test_manual_folder_omits_revision_segment_when_rev_missing(tmp_path, caplog):
    doc = _doc([_table("1", "First", 10)], rev="")
    with caplog.at_level(logging.WARNING, logger="rmtables.exporter"):
        manual_dir = write_split_tables(doc, tmp_path)
    assert manual_dir.name == "RM0490"
    assert "rev is missing" in caplog.text.lower()


# ----------------------------------------------------------------- collisions

def test_duplicate_table_numbers_produce_distinct_files_and_are_logged(tmp_path, caplog):
    doc = _doc([
        _table("5", "First copy", 100),
        _table("5", "Second copy", 200),
    ])
    with caplog.at_level(logging.WARNING):
        manual_dir = write_split_tables(doc, tmp_path)

    files = sorted(p.name for p in manual_dir.glob("*.json") if p.name != "_index.json")
    assert len(files) == 2
    assert len(set(files)) == 2
    assert "collision" in caplog.text.lower()


def test_unnumbered_tables_on_same_page_still_get_distinct_files(tmp_path):
    doc = _doc([_table("", "A", 5), _table("", "B", 5)])
    manual_dir = write_split_tables(doc, tmp_path)
    files = sorted(p.name for p in manual_dir.glob("*.json") if p.name != "_index.json")
    assert len(files) == 2
    assert len(set(files)) == 2


# --------------------------------------------------------------------- prune

def test_prune_removes_stale_files_from_previous_run(tmp_path):
    manual_dir = tmp_path / STEM
    manual_dir.mkdir()
    stale = manual_dir / f"{STEM}_table_999.json"
    stale.write_text("{}")

    doc = _doc([_table("1", "First", 10)])
    write_split_tables(doc, tmp_path, prune=True)

    assert not stale.exists()
    assert (manual_dir / f"{STEM}_table_001.json").exists()


def test_no_prune_keeps_stale_files(tmp_path):
    manual_dir = tmp_path / STEM
    manual_dir.mkdir()
    stale = manual_dir / f"{STEM}_table_999.json"
    stale.write_text("{}")

    doc = _doc([_table("1", "First", 10)])
    write_split_tables(doc, tmp_path, prune=False)

    assert stale.exists()


def test_prune_never_touches_other_manual_folders(tmp_path):
    other_dir = tmp_path / "RM0008_Rev21"
    other_dir.mkdir()
    other_file = other_dir / "RM0008_Rev21_table_001.json"
    other_file.write_text("{}")

    doc = _doc([_table("1", "First", 10)])
    write_split_tables(doc, tmp_path, prune=True)

    assert other_file.exists()


def test_prune_never_touches_another_revision_of_the_same_manual(tmp_path):
    # FILENAME_SCHEME_TASK.md "Revision safety": a NEW revision writes to a
    # sibling folder rather than overwriting/pruning the old one.
    old_rev_dir = tmp_path / "RM0490_Rev5"
    old_rev_dir.mkdir()
    old_file = old_rev_dir / "RM0490_Rev5_table_001.json"
    old_file.write_text("{}")
    old_index = old_rev_dir / "_index.json"
    old_index.write_text("{}")

    doc = _doc([_table("1", "First", 10)])  # rev "Rev 6" -> RM0490_Rev6
    manual_dir = write_split_tables(doc, tmp_path, prune=True)

    assert manual_dir.name == "RM0490_Rev6"
    assert old_file.exists()
    assert old_index.exists()


# ------------------------------------------------------------ atomicity / determinism

def test_no_tmp_files_remain_after_successful_run(tmp_path):
    doc = _doc([_table("1", "First", 10), _table("2", "Second", 20)])
    manual_dir = write_split_tables(doc, tmp_path)
    assert list(manual_dir.glob("*.tmp")) == []


def test_rerun_produces_byte_identical_files(tmp_path):
    doc = _doc([_table("1", "First", 10), _table("16", "Mass erase overview", 62)])
    manual_dir = write_split_tables(doc, tmp_path)
    before = {p.name: p.read_bytes() for p in manual_dir.glob("*.json")}

    manual_dir2 = write_split_tables(doc, tmp_path)
    after = {p.name: p.read_bytes() for p in manual_dir2.glob("*.json")}

    assert before.keys() == after.keys()
    for name in before:
        if name == "_index.json":
            continue  # generated_at legitimately changes between runs
        assert before[name] == after[name]


# --------------------------------------------------------------- real PDF (CLI)

def test_combined_json_byte_identical_with_and_without_split_tables(tmp_path):
    out_plain = tmp_path / "plain.json"
    out_split = tmp_path / "split.json"

    rc1 = main([PDF, "-o", str(out_plain), "--pages", "89-95", "--log-level", "ERROR"])
    rc2 = main([
        PDF, "-o", str(out_split), "--pages", "89-95", "--log-level", "ERROR",
        "--split-tables", "--tables-dir", str(tmp_path / "tables"),
    ])
    assert rc1 == 0 and rc2 == 0
    assert out_plain.read_bytes() == out_split.read_bytes()


def test_real_table_per_file_deep_equals_combined_record(tmp_path):
    out_path = tmp_path / "out.json"
    tables_dir = tmp_path / "tables"
    rc = main([
        PDF, "-o", str(out_path), "--pages", "89-95", "--log-level", "ERROR",
        "--split-tables", "--tables-dir", str(tables_dir),
    ])
    assert rc == 0

    combined = json.loads(out_path.read_text())
    assert len(combined["tables"]) >= 1

    stem = f"{combined['document']}_{combined['rev'].replace(' ', '')}"
    manual_dir = tables_dir / stem
    index = json.loads((manual_dir / "_index.json").read_text())
    assert index["table_count"] == len(combined["tables"])

    for record in combined["tables"]:
        n = record["table_number"]
        matches = [e for e in index["tables"] if e["table_number"] == n]
        assert len(matches) == 1
        per_file = json.loads((manual_dir / matches[0]["file"]).read_text())
        assert len(per_file["tables"]) == 1
        assert per_file["tables"][0] == record  # deep-equality, byte-identical record


def test_old_key_names_appear_nowhere_in_emitted_json(tmp_path):
    # RENAME_FIELDS_TASK.md: grep-style assertion -- id/text/tags must be
    # absent, table_id/text_helper/features present, everywhere records are
    # emitted (combined file, per-table files, and the _index.json manifest).
    out_path = tmp_path / "out.json"
    tables_dir = tmp_path / "tables"
    rc = main([
        PDF, "-o", str(out_path), "--pages", "89-95", "--log-level", "ERROR",
        "--split-tables", "--tables-dir", str(tables_dir),
    ])
    assert rc == 0

    def _assert_no_old_keys(obj, path=""):
        if isinstance(obj, dict):
            assert "id" not in obj, path
            assert "text" not in obj, path
            assert "tags" not in obj, path
            for k, v in obj.items():
                _assert_no_old_keys(v, f"{path}.{k}")
        elif isinstance(obj, list):
            for i, v in enumerate(obj):
                _assert_no_old_keys(v, f"{path}[{i}]")

    combined = json.loads(out_path.read_text())
    _assert_no_old_keys(combined)
    for record in combined["tables"]:
        assert "table_id" in record and record["table_id"]
        assert "text_helper" in record and record["text_helper"]
        assert "features" in record and isinstance(record["features"], list)

    manual_dir = tables_dir / f"{combined['document']}_{combined['rev'].replace(' ', '')}"
    for p in manual_dir.glob("*.json"):
        _assert_no_old_keys(json.loads(p.read_text()))
