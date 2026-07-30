import json
import os
import sys

import pdfplumber

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from rmtables.captions import find_captions
from rmtables.classify import classify_page
from rmtables.cli import main
from rmtables.extract import extract_page_tables, flush_page
from rmtables.headings import HeadingTracker
from rmtables.merge import TableMerger
from rmtables.registers import RegisterMerger

HERE = os.path.dirname(__file__)
PDF = os.path.join(
    HERE,
    "..",
    "..",
    "usermanuel",
    "rm0490-stm32c0-series-advanced-armbased-32bit-mcus-stmicroelectronics.pdf",
)
GOLDEN = os.path.join(HERE, "..", "examples", "rm0490_pages_89-95.json")


def test_pages_89_95_matches_golden_fixture(tmp_path):
    out_path = tmp_path / "out.json"
    rc = main([
        PDF, "-o", str(out_path), "--pages", "89-95",
        "--include-registers", "--log-level", "ERROR",
    ])
    assert rc == 0

    with open(out_path) as f:
        got = json.load(f)
    with open(GOLDEN) as f:
        expected = json.load(f)

    # The rag_selective schema never includes register layouts (see
    # RECOVERY_TASK.md); --include-registers only affects the internal
    # --emit rag chunking path, not this file.
    assert len(got["tables"]) == len(expected["tables"])
    assert got["tables"] == expected["tables"]


def test_default_emit_drops_figure_fragments_and_registers(tmp_path):
    out_path = tmp_path / "out.json"
    main([PDF, "-o", str(out_path), "--pages", "89-95", "--log-level", "ERROR"])
    with open(out_path) as f:
        data = json.load(f)
    # Only the one real captioned table (Table 26); figure fragments and
    # register bit-layouts are never part of the rag_selective table schema.
    assert len(data["tables"]) == 1
    assert data["tables"][0]["table_number"] == "26"


def _run_register_pipeline(page_range):
    """Drives the same per-page pipeline as `cli.main()` (extraction,
    heading tracking, classification) over `page_range`, returning the
    finalized register layouts directly -- the register-layout track is
    intentionally excluded from the CLI's rag_selective `-o` output (see
    RECOVERY_TASK.md), so its own correctness is verified at this lower
    level instead."""
    start, end = page_range
    table_merger = TableMerger()
    register_merger = RegisterMerger()
    heading_tracker = HeadingTracker()
    with pdfplumber.open(PDF) as pdf:
        for page_number in range(start, end + 1):
            page = pdf.pages[page_number - 1]
            lines = page.extract_text_lines()
            raw_tables = extract_page_tables(page, page_number)
            captions = find_captions(lines, page_number)
            heading_tracker.start_page(lines, raw_tables)
            classify_page(
                page_number, raw_tables, lines, captions,
                heading_tracker, table_merger, register_merger,
            )
            heading_tracker.finish_page()
            flush_page(page)
    return register_merger.finalize()


def test_register_layout_flash_secr_has_full_32_bit_coverage():
    register_layouts = _run_register_pipeline((89, 95))
    reg = next(r for r in register_layouts if r.register == "FLASH_SECR")
    reg_json = reg.to_json()
    assert reg_json["address_offset"] == "0x080"
    bits_present = {b["bit"] for b in reg_json["bits"]}
    assert bits_present == set(range(32))
    boot_lock = next(b for b in reg_json["bits"] if b["bit"] == 16)
    assert boot_lock["field"] == "BOOT_LOCK"
    assert boot_lock["access"] == "rw"


def test_register_map_table_26_is_correct(tmp_path):
    out_path = tmp_path / "out.json"
    main([PDF, "-o", str(out_path), "--pages", "89-95", "--log-level", "ERROR"])
    with open(out_path) as f:
        data = json.load(f)

    t26 = next(t for t in data["tables"] if t["table_number"] == "26")
    assert t26["page"] == 90
    headers = t26["table_content"]["headers"]
    assert headers[2:6] == ["31", "30", "29", "28"]
    assert headers[-1] == "0"
    # rotated field names must read correctly, not reversed
    flat = [cell for row in t26["table_content"]["rows"] for cell in row if cell]
    assert "Res." in flat
    assert "DBG_SWEN" in flat
    assert any("LATENCY" in cell for cell in flat)
