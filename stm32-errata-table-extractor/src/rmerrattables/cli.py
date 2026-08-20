"""CLI entry point: rmerrattables [PDF.pdf | --input-dir DIR] [-o PATH] [options].

Mirrors rmtables' CLI: an explicit `-o file.json` wins verbatim, an
`-o <directory>` (or no `-o`) auto-names `{ESxxxx}_{RevN}.json` inside it.
`--input-dir <dir>` enables batch mode: every PDF under the directory is
processed into `--output-dir` (default `<project>/output`), one file per
sheet, mirroring how the errata RAG project is driven.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

import pdfplumber

from .exporter import build_document
from .metadata import derive_metadata, doc_stem
from .tables import extract_tables
from .validate import validate_document

logger = logging.getLogger("rmerrattables")

OVERRIDE_FIELDS = ["name_datasheet", "rev", "url_pdf", "references", "family"]


def parse_page_range(spec: str | None, n_pages: int) -> tuple[int, int]:
    if not spec:
        return 1, n_pages
    start, end = spec.split("-")
    return int(start), int(end)


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="rmerrattables", description=__doc__)
    p.add_argument("input", nargs="?", default=None, help="input errata-sheet PDF path")
    p.add_argument("-o", "--output", default=None,
                   help="output path for a single PDF: explicit file, or a "
                        "directory to auto-name {ESxxxx}_{RevN}.json inside")
    p.add_argument("--input-dir", default=None,
                   help="batch mode: recursively process every PDF here into "
                        "--output-dir (ignores positional `input`)")
    p.add_argument("--output-dir", default=None,
                   help="batch output directory (default: <this project>/output)")
    p.add_argument("--pages", help="subset for dev/debug, e.g. 1-5")
    p.add_argument("--validate", action="store_true",
                   help="walk the built JSON against the schema invariants")
    for f in OVERRIDE_FIELDS:
        p.add_argument("--" + f.replace("_", "-"), dest=f, default=None,
                       help=f"override {f}")
    p.add_argument("--log-level", default="INFO")
    return p


def process_pdf(pdf_path: str, overrides: dict | None = None,
                pages: str | None = None, validate: bool = False):
    with pdfplumber.open(pdf_path) as pdf:
        meta = derive_metadata(pdf, pdf_path, overrides)
        start, end = parse_page_range(pages, len(pdf.pages))
        tables = extract_tables(pdf, start, end)
    doc = build_document(tables, meta)
    problems = validate_document(doc) if validate else []
    return doc, problems


def _stem(pdf_path: str, meta_override: dict) -> str:
    with pdfplumber.open(pdf_path) as pdf:
        meta = derive_metadata(pdf, pdf_path, meta_override)
    return doc_stem(meta["name_datasheet"], meta["rev"], pdf_path=pdf_path)


def _resolve_output(args, pdf_path: str, meta_override: dict) -> str:
    """Single-PDF mode naming (mirrors rmtables): an explicit FILE path is used
    verbatim; an existing DIRECTORY, or omitting -o entirely, auto-names
    {ESxxxx}_{RevN}.json inside it (default: current directory)."""
    if args.output and Path(args.output).is_dir():
        return str(Path(args.output) / f"{_stem(pdf_path, meta_override)}.json")
    if args.output:
        return args.output
    return f"{_stem(pdf_path, meta_override)}.json"


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.INFO),
        format="%(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,
    )
    overrides = {f: getattr(args, f) for f in OVERRIDE_FIELDS if getattr(args, f)}

    if args.input_dir:
        pdfs = sorted(Path(args.input_dir).rglob("*.pdf"))
        if not pdfs:
            print(f"No PDFs in {args.input_dir}", file=sys.stderr)
            return 1
        out_dir = Path(args.output_dir or _default_output_dir())
        out_dir.mkdir(parents=True, exist_ok=True)
        # one folder per family (input subfolder name, lowercased) so outputs
        # match the sibling projects' layout and report.json can reconcile.
        jobs = []
        for pdf in pdfs:
            parts = Path(pdf).relative_to(Path(args.input_dir)).parts
            fam = parts[0].lower() if len(parts) > 1 else ""
            jobs.append((str(pdf), str(out_dir / fam)))
    elif args.input:
        jobs = [(args.input, None)]
    else:
        print("no input: pass a PDF path or --input-dir", file=sys.stderr)
        return 2

    problems = []
    for pdf_path, out_dir in jobs:
        name = Path(pdf_path).name
        if out_dir is not None:
            Path(out_dir).mkdir(parents=True, exist_ok=True)
            out_path = str(Path(out_dir) / f"{_stem(pdf_path, overrides)}.json")
        else:
            out_path = _resolve_output(args, pdf_path, overrides)
        try:
            doc, vp = process_pdf(pdf_path, overrides, pages=args.pages,
                                  validate=args.validate)
        except Exception as exc:
            print(f"ERROR {name}: {exc}", file=sys.stderr)
            problems.append(f"{name}: {exc}")
            continue
        Path(out_path).parent.mkdir(parents=True, exist_ok=True)
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(doc, f, indent=2, ensure_ascii=False)
        print(f"wrote {doc['table_count']} tables to {out_path}", file=sys.stderr)
        for p in vp:
            print(f"  VALIDATE: {p}", file=sys.stderr)
            problems.append(f"{name}: {p}")
        if not vp and args.validate:
            print("  VALIDATE ok", file=sys.stderr)

    print(f"Done. {len(jobs) - len(problems)}/{len(jobs)} documents, {len(problems)} problem(s).")
    if problems:
        return 1
    return 0


def _default_output_dir() -> Path:
    return Path(__file__).resolve().parents[2] / "output"


if __name__ == "__main__":
    raise SystemExit(main())