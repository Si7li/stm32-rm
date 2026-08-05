"""CLI entry point: rmcontent INPUT.pdf [-o out.json|outdir] [options].

Mirrors `rmtables`' CLI: an explicit `-o file.json` wins verbatim, an
`-o <directory>` (or no `-o` at all) auto-names `{RM}_{Rev}.json`, and
every auto-derived metadata field has a matching override flag.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
import logging
import sys
from pathlib import Path

import pdfplumber

from rmtables.exporter import doc_stem
from rmtables.metadata import OVERRIDE_FIELDS, derive_metadata

from .contents import parse_contents
from .figures import parse_list_of_figures
from .exporter import OVERSIZED_CHARS, build_document, oversized_sections
from .lines import DEFAULT_Y_TOLERANCE
from .noise import artwork_threshold, derive_body_font_size
from .sections import scan_pdf
from .split import write_split_sections
from .validate import validate

logger = logging.getLogger("rmcontent")


def parse_page_range(spec: str | None, n_pages: int) -> tuple[int, int]:
    if not spec:
        return 1, n_pages
    start, end = spec.split("-")
    return int(start), int(end)


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="rmcontent", description=__doc__)
    p.add_argument("input", help="input PDF path")
    p.add_argument(
        "-o", "--output", default=None,
        help="output path: an explicit file path is used verbatim; an existing "
             "directory (or omitting -o entirely) auto-names the file "
             "{RM}_{Rev}.json inside it (default: current directory)",
    )
    p.add_argument("--pages", help="subset for dev/debug, e.g. 76-80")
    p.add_argument(
        "--split-sections", action="store_true",
        help="also write one self-contained JSON file per section (default: off)",
    )
    p.add_argument(
        "--sections-dir", default=None,
        help="per-section output directory (default: <output's dir>/sections)",
    )
    p.add_argument(
        "--validate", action="store_true",
        help="reconcile against the manual's own Contents pages",
    )
    p.add_argument(
        "--no-prune", action="store_true",
        help="keep stale per-section files from a previous run instead of deleting them",
    )
    for f in OVERRIDE_FIELDS:
        p.add_argument("--" + f.replace("_", "-"), dest=f, default=None, help=f"override {f}")
    # The spec names this flag `--document`; `rmtables` calls the same
    # metadata field `name_datasheet`. Both spellings set it.
    p.add_argument("--document", dest="name_datasheet", default=None,
                   help="override the document number, e.g. RM0490")
    p.add_argument(
        "--body-font-size", type=float, default=None,
        help="override the auto-derived body text size in points; figure "
             "artwork is anything below 0.6x this (default: derived per document)",
    )
    p.add_argument(
        "--y-tolerance", type=float, default=DEFAULT_Y_TOLERANCE,
        help="vertical tolerance (points) when grouping chars into text "
             "lines; the default merges a subscript into its baseline line "
             f"(default: {DEFAULT_Y_TOLERANCE})",
    )
    p.add_argument("--log-level", default="INFO")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.INFO),
        format="%(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,
    )

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
        if args.output is None:
            output_path = f"{stem}.json"
        elif Path(args.output).is_dir():
            output_path = str(Path(args.output) / f"{stem}.json")
        else:
            output_path = args.output

        start, end = parse_page_range(args.pages, n_pages)

        # Needed unconditionally, not just under --validate: chapters are
        # not emitted as records, so `chapter_title` on every section
        # comes from here, and the scanner uses the chapter list to
        # recognize a level-1 heading (which HEADING_RE cannot see).
        print(
            f"line y-tolerance {args.y_tolerance:g} pt "
            f"(subscripts merge into their baseline line)",
            file=sys.stderr,
        )

        contents = parse_contents(pdf, y_tolerance=args.y_tolerance)
        chapter_titles = {number: title for number, (title, _) in contents.chapters.items()}
        section_titles = {number: title for number, (title, _) in contents.sections.items()}

        def progress(page_number: int, last: int) -> None:
            print(f"...processed page {page_number}/{last}", file=sys.stderr)

        body_font_size = args.body_font_size or derive_body_font_size(
            pdf, y_tolerance=args.y_tolerance)
        print(
            f"body font size {body_font_size:.1f} pt; figure artwork is "
            f"anything below {artwork_threshold(body_font_size):.1f} pt",
            file=sys.stderr,
        )

        listed_figures = parse_list_of_figures(pdf, y_tolerance=args.y_tolerance)
        print(
            f"List of figures: {len(listed_figures)} entries",
            file=sys.stderr,
        )

        scanner = scan_pdf(
            pdf, meta["name_datasheet"], chapter_titles, section_titles,
            start=start, end=end, on_progress=progress,
            body_font_size=body_font_size, listed_figures=listed_figures,
            y_tolerance=args.y_tolerance,
        )
        sections = scanner.finalize()

    doc = build_document(sections, meta, contents)

    with open(output_path, "w") as f:
        json.dump(doc, f, indent=2, ensure_ascii=False)
    print(f"wrote {len(doc['sections'])} sections to {output_path}", file=sys.stderr)

    sections_dir = args.sections_dir or str(Path(output_path).parent / "sections")
    if args.split_sections:
        manual_dir = write_split_sections(
            doc, sections_dir, pdf_path=args.input, prune=not args.no_prune,
        )
        print(
            f"wrote {len(doc['sections'])} per-section files to {manual_dir}",
            file=sys.stderr,
        )

    # Always visible, with or without --validate: no chunking happens, so
    # an oversized section is the one thing an operator has to know about.
    big = oversized_sections(doc)
    print(
        f"sections over {OVERSIZED_CHARS} characters: {len(big)}",
        file=sys.stderr,
    )
    for record in big:
        print(
            f"  {record['section']} {record['section_title']!r}: {record['chars']} chars",
            file=sys.stderr,
        )

    band = scanner.band
    print(
        f"artwork bands: {band.opened} opened, {band.closed} closed by an asset id, "
        f"{len(band.abandoned)} abandoned at the hard bound "
        f"({band.lines_dropped} lines / {band.chars_dropped} chars removed)",
        file=sys.stderr,
    )
    reasons = Counter(reason for _, reason in band.abandoned)
    for reason, n in reasons.most_common():
        print(f"  abandoned -- {reason}: {n}", file=sys.stderr)
    for caption, reason in band.abandoned[:15]:
        print(f"    {caption}  [{reason}]", file=sys.stderr)
    if len(band.abandoned) > 15:
        print(f"    ... and {len(band.abandoned) - 15} more", file=sys.stderr)
    print(
        f"figure captions rejected as cross-references: "
        f"{len(scanner.figures.rejected)}; kept but not trusted to bound a band: "
        f"{len(scanner.figures.unbanded)}",
        file=sys.stderr,
    )
    for number, title, reason in scanner.figures.rejected[:10]:
        print(f"  rejected Figure {number}: {title[:60]!r} -- {reason}", file=sys.stderr)

    if args.validate:
        report = validate(doc, contents, scanner)
        print(report.summary(), file=sys.stderr)
        if not report.is_clean():
            print("validation found discrepancies (see above)", file=sys.stderr)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
