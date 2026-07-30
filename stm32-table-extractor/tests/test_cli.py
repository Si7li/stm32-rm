import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from rmtables.cli import main

HERE = os.path.dirname(__file__)
PDF = os.path.join(
    HERE, "..", "..", "usermanuel",
    "rm0490-stm32c0-series-advanced-armbased-32bit-mcus-stmicroelectronics.pdf",
)
STEM = "RM0490_Rev6"


# ------------------------------------------------------- -o resolution
# FILENAME_SCHEME_TASK.md "CLI behaviour": an explicit FILE path wins
# verbatim; an existing DIRECTORY (or omitting -o) auto-names {stem}.json.

def test_explicit_file_path_is_honored_verbatim(tmp_path):
    out_path = tmp_path / "custom.json"
    rc = main([PDF, "-o", str(out_path), "--pages", "89-95", "--log-level", "ERROR"])
    assert rc == 0
    assert out_path.exists()
    # no auto-named file was ALSO created alongside it
    assert sorted(p.name for p in tmp_path.glob("*.json")) == ["custom.json"]


def test_existing_directory_auto_names_stem_json_inside_it(tmp_path):
    rc = main([PDF, "-o", str(tmp_path), "--pages", "89-95", "--log-level", "ERROR"])
    assert rc == 0
    assert (tmp_path / f"{STEM}.json").exists()


def test_omitted_output_auto_names_stem_json_in_cwd(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    rc = main([PDF, "--pages", "89-95", "--log-level", "ERROR"])
    assert rc == 0
    assert (tmp_path / f"{STEM}.json").exists()


def test_auto_named_output_content_matches_explicit_file_path(tmp_path):
    explicit = tmp_path / "explicit.json"
    main([PDF, "-o", str(explicit), "--pages", "89-95", "--log-level", "ERROR"])

    auto_dir = tmp_path / "auto"
    auto_dir.mkdir()
    main([PDF, "-o", str(auto_dir), "--pages", "89-95", "--log-level", "ERROR"])

    assert explicit.read_bytes() == (auto_dir / f"{STEM}.json").read_bytes()


def test_tables_dir_defaults_next_to_auto_named_output(tmp_path):
    rc = main([
        PDF, "-o", str(tmp_path), "--pages", "89-95", "--log-level", "ERROR",
        "--split-tables",
    ])
    assert rc == 0
    assert (tmp_path / "tables" / STEM / "_index.json").exists()
