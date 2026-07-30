"""CLI entry point: rmtables INPUT.pdf -o tables.json [options]."""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

import pdfplumber

from .captions import find_captions, parse_list_of_tables
from .cells import fix_symbols
from .chunk import DEFAULT_CHUNK_TOKENS, build_chunks
from .classify import classify_page
from .exporter import build_document, doc_stem
from .extract import extract_page_tables, flush_page
from .fallback import extract_via_text_strategy, page_looks_like_it_has_a_table
from .headings import HeadingTracker
from .merge import TableMerger
from .metadata import OVERRIDE_FIELDS, derive_metadata
from .registers import RegisterMerger
from .split import write_figure_fragments, write_split_tables
from .validate import validate, validate_chunks

logger = logging.getLogger("rmtables")


def parse_page_range(spec: str | None, n_pages: int) -> tuple[int, int]:
    if not spec:
        return 1, n_pages
    start, end = spec.split("-")
    return int(start), int(end)


def flatten_merges(rows: list) -> list:
    """Forward-fill None cells in column 0 (nice for CSV-style consumption)."""
    flat = []
    last_col0 = None
    for row in rows:
        new_row = list(row)
        if new_row and new_row[0] is None:
            new_row[0] = last_col0
        elif new_row:
            last_col0 = new_row[0]
        flat.append(new_row)
    return flat


def derive_chunks_path(output_path: str) -> str:
    if output_path.endswith(".json"):
        return output_path[:-5] + ".jsonl"
    return output_path + ".jsonl"


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="rmtables", description=__doc__)
    p.add_argument("input", help="input PDF path")
    p.add_argument(
        "-o", "--output", default=None,
        help="output path: an explicit file path is used verbatim; an existing "
             "directory (or omitting -o entirely) auto-names the file "
             "{RM}_{Rev}.json inside it (default: current directory)",
    )
    p.add_argument("--pages", help="subset for dev/debug, e.g. 90-95")
    p.add_argument(
        "--text-fallback",
        action="store_true",
        help="deterministic word-clustering fallback for unruled pages",
    )
    p.add_argument("--flatten-merges", action="store_true")
    p.add_argument("--validate", action="store_true", help="reconcile against List of tables")
    p.add_argument(
        "--include-registers",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="include per-register bit-field layouts (default: on for --emit rag/both, off otherwise)",
    )
    p.add_argument(
        "--emit",
        choices=["raw", "rag", "both"],
        default="raw",
        help="raw = structured tables.json; rag = JSONL chunks; both = write each",
    )
    p.add_argument("--chunk-tokens", type=int, default=DEFAULT_CHUNK_TOKENS)
    p.add_argument("--chunks-output", help="JSONL path when --emit both (default: derived from -o)")
    p.add_argument(
        "--split-tables", action="store_true",
        help="also write one self-contained JSON file per table (default: off)",
    )
    p.add_argument(
        "--tables-dir", default=None,
        help="per-table output directory (default: <output's dir>/tables)",
    )
    p.add_argument(
        "--filename-slug", action="store_true",
        help="include a caption-derived slug in per-table filenames (default: off)",
    )
    p.add_argument(
        "--no-prune", action="store_true",
        help="keep stale per-table files from a previous run instead of deleting them",
    )
    for f in OVERRIDE_FIELDS:
        p.add_argument("--" + f.replace("_", "-"), dest=f, default=None, help=f"override {f}")
    p.add_argument("--log-level", default="INFO")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.INFO),
        format="%(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,
    )

    include_registers = args.include_registers
    if include_registers is None:
        include_registers = args.emit in ("rag", "both")

    try:
        pdf = pdfplumber.open(args.input)
    except Exception:
        logger.error("failed to open %s", args.input, exc_info=True)
        return 1

    with pdf:
        n_pages = len(pdf.pages)
        overrides = {f: getattr(args, f) for f in OVERRIDE_FIELDS}
        meta = derive_metadata(pdf, args.input, overrides)
        stem = doc_stem(meta["name_datasheet"], meta["rev"], pdf_path=args.input)
        # FILENAME_SCHEME_TASK.md: an explicit FILE path wins verbatim; an
        # existing DIRECTORY (or omitting -o) auto-names {stem}.json inside it.
        if args.output is None:
            output_path = f"{stem}.json"
        elif Path(args.output).is_dir():
            output_path = str(Path(args.output) / f"{stem}.json")
        else:
            output_path = args.output
        start, end = parse_page_range(args.pages, n_pages)
        # TITLE_FIDELITY_FIX.md Part 2: needed unconditionally now, not just
        # under --validate -- build_document uses it to repair a body caption
        # damaged by rendering, not merely to report a discrepancy.
        list_of_tables = parse_list_of_tables(pdf)

        table_merger = TableMerger()
        register_merger = RegisterMerger()
        heading_tracker = HeadingTracker()
        dropped_total = 0
        # FIGURE_CAPTION_BOUNDARY_FIX.md: every grid rejected because a
        # Figure caption sits between it and the Table caption it would
        # otherwise have adopted -- nothing is discarded, so these are
        # written out to _figure_fragments.json after the run completes.
        all_figure_fragments: list[dict] = []
        # (table_number, text) pairs from an explicit "Legend for Table N:"
        # line whose target table doesn't exist in table_merger yet --
        # position-independent, so retried on every later page.
        pending_legends: list[tuple[int, str]] = []

        def _flush_pending_legends():
            nonlocal pending_legends
            still_pending = []
            for table_number, text in pending_legends:
                if not table_merger.attach_legend(table_number, text):
                    still_pending.append((table_number, text))
            pending_legends = still_pending

        for page_number in range(start, end + 1):
            page = pdf.pages[page_number - 1]
            try:
                # Symbol-font glyphs (bullets, arrows, <=/>=, ...) come back
                # from pdfplumber as U+F0xx PUA garbage with no ToUnicode;
                # cleaning it here -- the single shared source for every
                # line-based consumer below (captions, headings, notes,
                # legends) -- means all of them see the real character.
                lines = [dict(l, text=fix_symbols(l["text"])) for l in page.extract_text_lines()]
                raw_tables = extract_page_tables(page, page_number)
                captions = find_captions(lines, page_number)

                if args.text_fallback and not raw_tables and page_looks_like_it_has_a_table(page):
                    logger.warning(
                        "page %d: no ruled table found but a caption is present; "
                        "trying text-strategy fallback",
                        page_number,
                    )
                    raw_tables = extract_via_text_strategy(page, page_number)

                heading_tracker.start_page(lines, raw_tables)
                dropped, explicit_legends, figure_fragments = classify_page(
                    page_number,
                    raw_tables,
                    lines,
                    captions,
                    heading_tracker,
                    table_merger,
                    register_merger,
                )
                dropped_total += dropped
                all_figure_fragments.extend(figure_fragments)
                pending_legends.extend(explicit_legends)
                _flush_pending_legends()
                heading_tracker.finish_page()
            except Exception:
                logger.warning("soft failure on page %d", page_number, exc_info=True)
            finally:
                flush_page(page)

            if page_number % 100 == 0:
                print(f"...processed page {page_number}/{end}", file=sys.stderr)

        logical_tables = table_merger.finalize()
        _flush_pending_legends()  # one more pass now every table is finalized
        register_layouts = register_merger.finalize()

    tables_out = []
    for t in logical_tables:
        d = t.to_json()
        if args.flatten_merges:
            d["rows"] = flatten_merges(d["rows"])
            d["header"] = d["rows"][0] if d["rows"] else []
        tables_out.append(d)

    if include_registers:
        tables_out.extend(r.to_json() for r in register_layouts)

    # Internal raw representation -- kept exactly as before, still the
    # source `--emit rag` chunking and `--validate` both work from. It is
    # NOT what gets written to `-o` anymore (see `rag_doc` below): the
    # register pipeline is deliberately kept out of the rag_selective table
    # schema (see RECOVERY_TASK.md), so this dict (which can include
    # register_layout entries) stays purely an internal/diagnostic view.
    result = {
        "source_file": args.input.split("/")[-1],
        "table_count": len(tables_out),
        "caption_table_count": sum(1 for t in tables_out if t["type"] == "caption_table"),
        "register_layout_count": sum(1 for t in tables_out if t["type"] == "register_layout"),
        "figure_fragments_dropped": dropped_total,
        "tables": tables_out,
    }

    # The actual deliverable: the flat Sidekick-ready document (table_id,
    # document, rev, table_number, title, page, section, section_title,
    # semantic_type, features, url, url_pdf, columns, text_helper,
    # table_content{headers,rows,notes,legend,semantic_type,semantic}),
    # built only from caption_table LogicalTables -- never register layouts.
    rag_doc = build_document(logical_tables, meta, list_of_tables)

    if args.emit in ("raw", "both"):
        with open(output_path, "w") as f:
            json.dump(rag_doc, f, indent=2, ensure_ascii=False)
        print(f"wrote {len(rag_doc['tables'])} tables to {output_path}", file=sys.stderr)

    tables_dir = args.tables_dir or str(Path(output_path).parent / "tables")

    if args.split_tables:
        # Consumes the already-finished rag_doc -- same object as what was
        # (or would have been) written to -o, so the two outputs can't drift.
        manual_dir = write_split_tables(
            rag_doc, tables_dir, pdf_path=args.input,
            filename_slug=args.filename_slug, prune=not args.no_prune,
        )
        print(f"wrote {len(rag_doc['tables'])} per-table files to {manual_dir}", file=sys.stderr)

    if all_figure_fragments:
        # FIGURE_CAPTION_BOUNDARY_FIX.md: written unconditionally (even
        # without --split-tables) since "nothing is discarded" -- lands in
        # the identical per-manual folder write_split_tables would use.
        fragments_dir = write_figure_fragments(
            rag_doc["document"], rag_doc["rev"], all_figure_fragments,
            tables_dir, pdf_path=args.input,
        )
        print(
            f"wrote {len(all_figure_fragments)} rejected figure grid(s) to "
            f"{fragments_dir / '_figure_fragments.json'}",
            file=sys.stderr,
        )

    chunk_records = []
    if args.emit in ("rag", "both"):
        for entry in tables_out:
            chunk_records.extend(build_chunks(entry, result["source_file"], args.chunk_tokens))
        chunks_path = output_path if args.emit == "rag" else (
            args.chunks_output or derive_chunks_path(output_path)
        )
        with open(chunks_path, "w") as f:
            for record in chunk_records:
                f.write(json.dumps(record) + "\n")
        print(f"wrote {len(chunk_records)} chunks to {chunks_path}", file=sys.stderr)

    print(
        f"figure fragments dropped: {dropped_total}; "
        f"caption tables: {result['caption_table_count']}; "
        f"register layouts: {result['register_layout_count']}",
        file=sys.stderr,
    )

    if args.validate:
        report = validate(result, list_of_tables, rag_doc)
        print(report.summary(), file=sys.stderr)
        if not report.is_clean():
            print("validation found discrepancies (see above)", file=sys.stderr)
        if chunk_records:
            chunk_warnings = validate_chunks(chunk_records, args.chunk_tokens)
            for w in chunk_warnings:
                print(f"chunk validation: {w}", file=sys.stderr)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
