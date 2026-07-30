import os
import sys
import time
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from stm32fetch import batch as batch_mod
from stm32fetch.batch import (
    FileResult,
    is_split_up_to_date,
    is_up_to_date,
    output_name_for,
    resolve_rmtables_src,
    run_batch,
    stem_for,
)

HERE = os.path.dirname(__file__)
REAL_PDF = Path(HERE, "..", "..", "usermanuel", "rm0490-stm32c0-series-advanced-armbased-32bit-mcus-stmicroelectronics.pdf")


def test_is_up_to_date_true_when_json_newer(tmp_path):
    pdf = tmp_path / "rm0490.pdf"
    pdf.write_bytes(b"pdf")
    js = tmp_path / "RM0490.json"
    time.sleep(0.01)
    js.write_text("{}")
    assert is_up_to_date(pdf, js) is True


def test_is_up_to_date_false_when_json_missing():
    from pathlib import Path

    assert is_up_to_date(Path("/nonexistent/x.pdf"), Path("/nonexistent/x.json")) is False


def test_is_up_to_date_false_when_json_older_than_pdf(tmp_path):
    js = tmp_path / "RM0490.json"
    js.write_text("{}")
    time.sleep(0.01)
    pdf = tmp_path / "rm0490.pdf"
    pdf.write_bytes(b"pdf")
    assert is_up_to_date(pdf, js) is False


def test_output_name_for_uses_rm_number_when_present(tmp_path):
    from pathlib import Path

    # These paths don't exist on disk -> pdfplumber can't open them -> stem
    # pre-derivation falls back to the filename-derived RM number, with no
    # revision segment (FILENAME_SCHEME_TASK.md's safe-fallback behavior).
    assert output_name_for(Path("rm0490-stm32c0-series-....pdf")) == "RM0490.json"
    assert output_name_for(Path("some_weird_file.pdf")) == "some_weird_file.json"


def test_stem_for_derives_rm_and_rev_from_a_real_pdf():
    # FILENAME_SCHEME_TASK.md: stem_for pre-derives {RM}_{Rev} by calling
    # rmtables' OWN derive_metadata/doc_stem against the real cover pages,
    # via the sibling-checkout auto-detection (resolve_rmtables_src).
    assert REAL_PDF.exists(), "expected usermanuel/ fixture PDF to be present"
    assert stem_for(REAL_PDF) == "RM0490_Rev6"
    assert output_name_for(REAL_PDF) == "RM0490_Rev6.json"


def test_resolve_rmtables_src_explicit_wins_over_everything(monkeypatch, tmp_path):
    monkeypatch.setattr(batch_mod, "_importable", lambda m: True)
    monkeypatch.delenv("RMTABLES_SRC", raising=False)
    result = resolve_rmtables_src(explicit=tmp_path / "custom-src")
    assert result == tmp_path / "custom-src"


def test_resolve_rmtables_src_env_var_used_when_no_explicit(monkeypatch, tmp_path):
    monkeypatch.setattr(batch_mod, "_importable", lambda m: True)
    monkeypatch.setenv("RMTABLES_SRC", str(tmp_path / "env-src"))
    result = resolve_rmtables_src()
    assert result == tmp_path / "env-src"


def test_resolve_rmtables_src_none_when_already_importable(monkeypatch):
    monkeypatch.delenv("RMTABLES_SRC", raising=False)
    monkeypatch.setattr(batch_mod, "_importable", lambda m: True)
    assert resolve_rmtables_src() is None


def test_resolve_rmtables_src_falls_back_to_sibling_checkout(monkeypatch, tmp_path):
    sibling = tmp_path / "stm32-table-extractor" / "src"
    (sibling / "rmtables").mkdir(parents=True)
    (sibling / "rmtables" / "__init__.py").write_text("")

    monkeypatch.delenv("RMTABLES_SRC", raising=False)
    monkeypatch.setattr(batch_mod, "_importable", lambda m: False)
    monkeypatch.setattr(batch_mod, "_DEFAULT_SIBLING_RMTABLES_SRC", sibling)

    assert resolve_rmtables_src() == sibling


def test_run_batch_skips_up_to_date_files(tmp_path, monkeypatch):
    manuals_dir, json_dir = tmp_path / "manuals", tmp_path / "json"
    manuals_dir.mkdir()
    json_dir.mkdir()

    (manuals_dir / "rm0001.pdf").write_bytes(b"a")
    (manuals_dir / "rm0002.pdf").write_bytes(b"b")
    time.sleep(0.01)
    (json_dir / "RM0001.json").write_text("{}")  # up to date for rm0001 only

    calls = []

    def fake_run_rmtables_on_pdf(pdf_path, json_dir_arg, **kwargs):
        calls.append(str(pdf_path))
        return FileResult(str(pdf_path), str(json_dir_arg), "processed", table_count=5)

    monkeypatch.setattr(batch_mod, "run_rmtables_on_pdf", fake_run_rmtables_on_pdf)

    summary = run_batch(manuals_dir, json_dir, force=False)

    assert len(summary.skipped) == 1
    assert len(summary.processed) == 1
    assert any("rm0002" in c for c in calls)
    assert not any("rm0001" in c for c in calls)


def test_run_batch_force_reruns_everything(tmp_path, monkeypatch):
    manuals_dir, json_dir = tmp_path / "manuals", tmp_path / "json"
    manuals_dir.mkdir()
    json_dir.mkdir()
    (manuals_dir / "rm0001.pdf").write_bytes(b"a")
    time.sleep(0.01)
    (json_dir / "RM0001.json").write_text("{}")

    calls = []
    monkeypatch.setattr(
        batch_mod, "run_rmtables_on_pdf",
        lambda pdf_path, json_dir_arg, **kw: (calls.append(str(pdf_path)) or FileResult(str(pdf_path), "", "processed")),
    )

    summary = run_batch(manuals_dir, json_dir, force=True)

    assert len(summary.skipped) == 0
    assert len(calls) == 1


def test_run_batch_continues_after_one_failure(tmp_path, monkeypatch):
    manuals_dir, json_dir = tmp_path / "manuals", tmp_path / "json"
    manuals_dir.mkdir()
    json_dir.mkdir()
    (manuals_dir / "rm_good.pdf").write_bytes(b"a")
    (manuals_dir / "rm_bad.pdf").write_bytes(b"b")

    def fake_run(pdf_path, json_dir_arg, **kwargs):
        if "bad" in str(pdf_path):
            return FileResult(str(pdf_path), "", "failed", error="boom")
        return FileResult(str(pdf_path), "", "processed", table_count=3)

    monkeypatch.setattr(batch_mod, "run_rmtables_on_pdf", fake_run)

    summary = run_batch(manuals_dir, json_dir, force=True)

    assert len(summary.processed) == 1
    assert len(summary.failed) == 1
    assert summary.failed[0].error == "boom"


# ------------------------------------------------ SPLIT_TABLES_TASK.md §6

def test_is_split_up_to_date_true_when_index_newer_than_pdf(tmp_path):
    pdf = tmp_path / "rm0490.pdf"
    pdf.write_bytes(b"pdf")
    index_dir = tmp_path / "tables" / "RM0490"
    index_dir.mkdir(parents=True)
    time.sleep(0.01)
    (index_dir / "_index.json").write_text("{}")
    assert is_split_up_to_date(pdf, tmp_path / "tables", "RM0490") is True


def test_is_split_up_to_date_false_when_index_missing(tmp_path):
    pdf = tmp_path / "rm0490.pdf"
    pdf.write_bytes(b"pdf")
    assert is_split_up_to_date(pdf, tmp_path / "tables", "RM0490") is False


def test_is_split_up_to_date_false_when_index_older_than_pdf(tmp_path):
    index_dir = tmp_path / "tables" / "RM0490"
    index_dir.mkdir(parents=True)
    (index_dir / "_index.json").write_text("{}")
    time.sleep(0.01)
    pdf = tmp_path / "rm0490.pdf"
    pdf.write_bytes(b"pdf")
    assert is_split_up_to_date(pdf, tmp_path / "tables", "RM0490") is False


def test_run_rmtables_on_pdf_forwards_split_tables_flags(tmp_path, monkeypatch):
    captured = {}

    def fake_run_subprocess(argv, rmtables_src, timeout):
        captured["argv"] = argv
        out_path = tmp_path / "json" / "RM0490.json"
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text('{"tables": []}')
        return 0, ""

    monkeypatch.setattr(batch_mod, "_run_subprocess", fake_run_subprocess)
    pdf = tmp_path / "rm0490.pdf"
    pdf.write_bytes(b"pdf")

    batch_mod.run_rmtables_on_pdf(
        pdf, tmp_path / "json", split_tables=True, tables_dir=tmp_path / "tables",
        filename_slug=True, no_prune=True,
    )

    argv = captured["argv"]
    assert "--split-tables" in argv
    assert "--tables-dir" in argv
    assert argv[argv.index("--tables-dir") + 1] == str(tmp_path / "tables")
    assert "--filename-slug" in argv
    assert "--no-prune" in argv


def test_run_rmtables_on_pdf_omits_split_flags_when_disabled(tmp_path, monkeypatch):
    captured = {}

    def fake_run_subprocess(argv, rmtables_src, timeout):
        captured["argv"] = argv
        out_path = tmp_path / "json" / "RM0490.json"
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text('{"tables": []}')
        return 0, ""

    monkeypatch.setattr(batch_mod, "_run_subprocess", fake_run_subprocess)
    pdf = tmp_path / "rm0490.pdf"
    pdf.write_bytes(b"pdf")

    batch_mod.run_rmtables_on_pdf(pdf, tmp_path / "json")

    argv = captured["argv"]
    assert "--split-tables" not in argv
    assert "--tables-dir" not in argv


def test_run_batch_reruns_when_combined_current_but_split_stale(tmp_path, monkeypatch):
    manuals_dir, json_dir, tables_dir = tmp_path / "manuals", tmp_path / "json", tmp_path / "tables"
    manuals_dir.mkdir()
    json_dir.mkdir()
    pdf = manuals_dir / "rm0490.pdf"
    pdf.write_bytes(b"a")
    time.sleep(0.01)
    (json_dir / "RM0490.json").write_text("{}")  # combined output IS current
    # no tables/RM0490/_index.json at all -- split output has never been produced

    calls = []
    monkeypatch.setattr(
        batch_mod, "run_rmtables_on_pdf",
        lambda pdf_path, json_dir_arg, **kw: (calls.append(kw) or FileResult(str(pdf_path), "", "processed")),
    )

    summary = run_batch(manuals_dir, json_dir, force=False, split_tables=True, tables_dir=tables_dir)

    assert len(summary.processed) == 1
    assert len(summary.skipped) == 0
    assert calls[0]["split_tables"] is True


def test_run_batch_skips_when_both_combined_and_split_current(tmp_path, monkeypatch):
    manuals_dir, json_dir, tables_dir = tmp_path / "manuals", tmp_path / "json", tmp_path / "tables"
    manuals_dir.mkdir()
    json_dir.mkdir()
    pdf = manuals_dir / "rm0490.pdf"
    pdf.write_bytes(b"a")
    index_dir = tables_dir / "RM0490"
    index_dir.mkdir(parents=True)
    time.sleep(0.01)
    (json_dir / "RM0490.json").write_text("{}")
    (index_dir / "_index.json").write_text("{}")

    calls = []
    monkeypatch.setattr(
        batch_mod, "run_rmtables_on_pdf",
        lambda pdf_path, json_dir_arg, **kw: (calls.append(kw) or FileResult(str(pdf_path), "", "processed")),
    )

    summary = run_batch(manuals_dir, json_dir, force=False, split_tables=True, tables_dir=tables_dir)

    assert len(summary.skipped) == 1
    assert calls == []


def test_run_batch_skip_check_keys_off_the_real_rm_and_rev_stem(tmp_path, monkeypatch):
    # FILENAME_SCHEME_TASK.md: idempotency must key off {stem}.json, not the
    # old <rm_number>.json -- exercised here against a REAL PDF (so the stem
    # is genuinely pre-derived via rmtables' own metadata/doc_stem) rather
    # than a fake one that would just fall back to the RM-number-only form.
    manuals_dir, json_dir = tmp_path / "manuals", tmp_path / "json"
    manuals_dir.mkdir()
    json_dir.mkdir()
    pdf = manuals_dir / REAL_PDF.name
    pdf.write_bytes(REAL_PDF.read_bytes())
    time.sleep(0.01)
    (json_dir / "RM0490_Rev6.json").write_text('{"tables": []}')  # up to date under the NEW name

    calls = []
    monkeypatch.setattr(
        batch_mod, "run_rmtables_on_pdf",
        lambda pdf_path, json_dir_arg, **kw: (calls.append(str(pdf_path)) or FileResult(str(pdf_path), "", "processed")),
    )

    summary = run_batch(manuals_dir, json_dir, force=False)

    assert len(summary.skipped) == 1
    assert calls == []


def test_run_rmtables_on_pdf_import_failure_becomes_failed_result(tmp_path, monkeypatch):
    # No rmtables checkout anywhere findable -> import mode fails cleanly,
    # not with an unhandled exception.
    #
    # FILENAME_SCHEME_TASK.md's stem pre-derivation may have already
    # (genuinely) imported the real sibling rmtables checkout as a side
    # effect of an earlier test in this same process -- and Python caches
    # successful imports for the life of the interpreter -- so this test
    # has to undo that: evict any cached rmtables modules AND strip any
    # sibling-checkout path already sitting in `sys.path`, or a bare
    # `import rmtables` would succeed here regardless of the mocks below.
    for name in list(sys.modules):
        if name == "rmtables" or name.startswith("rmtables."):
            monkeypatch.delitem(sys.modules, name)
    monkeypatch.setattr(sys, "path", [p for p in sys.path if "stm32-table-extractor" not in p])

    monkeypatch.setattr(batch_mod, "_importable", lambda m: False)
    monkeypatch.setattr(batch_mod, "_DEFAULT_SIBLING_RMTABLES_SRC", tmp_path / "nowhere")
    # Isolates this test from the stem pre-derivation's OWN rmtables import
    # attempt: it's independent of, and would otherwise mask, the
    # `_run_in_process` import-failure path this test actually targets.
    monkeypatch.setattr(batch_mod, "_derive_stem", lambda pdf_path, rmtables_src: None)

    pdf = tmp_path / "rm9999.pdf"
    pdf.write_bytes(b"not a real pdf")
    result = batch_mod.run_rmtables_on_pdf(pdf, tmp_path / "json", mode="import")

    assert result.status == "failed"
    assert "rmtables" in result.error.lower() or result.error
