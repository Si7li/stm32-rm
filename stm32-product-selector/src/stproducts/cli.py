"""Command line: ``build``, ``resolve``, ``diff``.

The inputs in ``product_selector/`` are read-only. Every run hashes them
before doing anything and hashes them again at the end, and fails loudly if
a single byte moved. That makes "alongside, never overwriting" a structural
property of the tool rather than a promise in the README -- if a "fix in
place" path is ever added by accident, this check is what catches it.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import sys
from dataclasses import asdict
from pathlib import Path

from .api import fetch_datasheet_url, fetch_grid
from .compose import api_only_sheet, compose_sheet
from .datasheets import LocalIndex, acquire, st_datasheet_url
from .diffing import REPORTED_CLASSES, SOURCE_CLASSES, UNCHANGED, compare, plan_columns
from .extract import set_parse_cache_dir
from .net import FetchError, Fetcher
from .discover import SEED_CATALOGUES, discover
from .resolve import Resolution, resolve_sheet, save_map
from .sheetio import read_original, synthesize, unreproducible_headers
from .writer import write_corrected, write_diff

logger = logging.getLogger("stproducts")

DEFAULT_INPUT = Path("product_selector")
DEFAULT_OUTPUT = Path("product_selector_out")
DEFAULT_CACHE = Path("cache")


class InputsModified(RuntimeError):
    """An input workbook changed during the run. Should never happen."""


def digest_inputs(paths: list[Path]) -> dict[str, str]:
    return {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(paths)}


def input_files(directory: Path, only: str | None) -> list[Path]:
    files = sorted(p for p in directory.glob("*.xlsx") if not p.name.startswith("~$"))
    if only:
        needle = only.casefold()
        files = [p for p in files if needle in p.stem.casefold()]
    return files


def _resolutions_from_map(payload: dict) -> dict[str, Resolution]:
    out = {}
    for stem, entry in payload.items():
        out[stem] = Resolution(
            stem=stem,
            resolved=entry.get("resolved", False),
            level_id=entry.get("level_id"),
            level_title=entry.get("level_title"),
            rows=entry.get("rows"),
            expected_parts=entry.get("expected_parts"),
            pages_tried=entry.get("pages_tried", []),
            candidates=entry.get("candidates", []),
            reason=entry.get("reason"),
        )
    return out


def do_resolve(args, fetcher: Fetcher, sheets: dict[str, object], map_path: Path) -> dict[str, Resolution]:
    cached = _resolutions_from_map(json.loads(map_path.read_text())) if map_path.exists() else {}
    resolutions: dict[str, Resolution] = {}
    for stem, sheet in sheets.items():
        known = cached.get(stem)
        if known and known.resolved and not args.refresh:
            # A cached hit still has to agree with the workbook in front of us.
            if known.expected_parts == len(sheet.parts):
                resolutions[stem] = known
                logger.info("%-30s %s (cached)", stem, known.level_id)
                continue
            logger.info("%-30s cached map is stale, re-resolving", stem)
        result = resolve_sheet(fetcher, sheet)
        resolutions[stem] = result
        if result.resolved:
            logger.info(
                "%-30s %s  %r  %d rows", stem, result.level_id, result.level_title, result.rows
            )
        else:
            logger.warning("%-30s UNRESOLVED: %s", stem, result.reason)
    save_map(map_path, resolutions)
    return resolutions


def do_build(args) -> int:
    input_dir = Path(args.input)
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    cache_dir = Path(args.cache)

    files = input_files(input_dir, args.only)
    if not files:
        logger.error("no .xlsx inputs found in %s", input_dir)
        return 2

    before = digest_inputs(files)
    fetcher = Fetcher(cache_dir=cache_dir, use_cache=not args.no_cache, offline=args.offline)

    sheets = {}
    schema_failures: dict[str, list[str]] = {}
    for path in files:
        sheet = read_original(path)
        sheets[sheet.stem] = sheet
        if sheet.duplicate_parts:
            logger.warning(
                "%-30s %d duplicate part rows ignored: %s",
                sheet.stem,
                len(sheet.duplicate_parts),
                ", ".join(sheet.duplicate_parts[:5]),
            )

    # ST's banner logo lives in the workbook, not in any API response, so a
    # discovered selector borrows one from a file that has it.
    logo, logo_size = next(
        ((s.logo_png, s.logo_size) for s in sheets.values() if s.logo_png), (None, None)
    )

    map_path = out_dir / "series_map.json"
    resolutions = do_resolve(args, fetcher, sheets, map_path)

    # stem -> level id, for everything that will be built.
    targets: dict[str, str] = {
        stem: r.level_id for stem, r in resolutions.items() if r.resolved
    }
    catalog = None
    if getattr(args, "selectors", "local") != "local":
        local_stems = {level_id: stem for stem, level_id in targets.items()}
        catalog = discover(fetcher, local_stems=local_stems)
        catalog.write(out_dir / "catalog.json")
        logger.info(
            "discovered %d selectors (%s); %d already have a local workbook",
            len(catalog.selectors),
            ", ".join(f"{len(catalog.by_level(f))} {f}" for f in ("SC", "SS", "LN")),
            sum(1 for s in catalog.selectors if s.local_workbook),
        )
        if catalog.failed:
            logger.warning("  %d level ids unreachable: %s",
                           len(catalog.failed), ", ".join(sorted(catalog.failed)[:8]))
        if catalog.seed_mismatches:
            for level_id, detail in catalog.seed_mismatches.items():
                logger.warning("  seed %s: %s", level_id, detail)

        if args.selectors == "discovered":
            # Only selectors with no local workbook; the local ones are
            # already covered and keep their diff under 'all'.
            targets = {}
        for selector in catalog.selectors:
            if selector.local_workbook:
                continue  # prefer the real workbook, so the diff survives
            targets[selector.stem] = selector.level_id

    datasheet_first = getattr(args, "source", "datasheet") == "datasheet"
    local_index = None
    if datasheet_first:
        set_parse_cache_dir(
            None if args.no_cache else Path(args.datasheet_cache) / "parsed"
        )
        local_index = LocalIndex(
            Path(args.datasheets), Path(args.datasheet_cache) / "cover-index.json"
        )

    classes = (*REPORTED_CLASSES, UNCHANGED)
    if datasheet_first:
        classes = (*REPORTED_CLASSES, *SOURCE_CLASSES, UNCHANGED)
    report = {
        "inputs": str(input_dir),
        "output": str(out_dir),
        "source": "datasheet" if datasheet_first else "api",
        "files": {},
        "unresolved": [],
        "totals": {k: 0 for k in classes},
    }
    totals = report["totals"]

    for stem, sheet in sheets.items():
        if stem in targets:
            continue  # built below, from `targets`
        resolution = resolutions.get(stem)
        report["unresolved"].append(
            {
                "file": stem,
                "expected_parts": len(sheet.parts),
                "reason": resolution.reason if resolution else "not attempted",
                "pages_tried": resolution.pages_tried if resolution else [],
                "candidates": [
                    c if isinstance(c, dict) else asdict(c)
                    for c in (resolution.candidates if resolution else [])
                ],
            }
        )
        logger.warning("%-30s skipped, unresolved", stem)

    for stem, level_id in targets.items():
        grid = fetch_grid(fetcher, level_id)
        sheet = sheets.get(stem)
        if sheet is None:
            # Discovered, with no workbook to copy: the schema comes from the
            # API's own column metadata. There is nothing to diff against.
            sheet = synthesize(grid, stem=stem, logo_png=logo, logo_size=logo_size)
        else:
            unreproducible = unreproducible_headers(sheet, grid)
            if unreproducible:
                # The rule that lets discovered selectors be built just failed
                # against a workbook ST actually exported. Say so.
                logger.warning(
                    "%-30s %d shipped header(s) not reproducible from the API: %s",
                    stem, len(unreproducible), unreproducible[:4],
                )
                schema_failures[stem] = unreproducible

        original_cols, appended_cols = plan_columns(sheet, grid)
        layout_keys = [k for k, _ in original_cols] + [k for k, _ in appended_cols]

        datasheet_urls: dict[str, str] = {}
        acquisition = None
        if datasheet_first:
            acquisition = acquire(
                fetcher,
                grid.rows,
                local=local_index,
                cache_dir=Path(args.datasheet_cache),
                allow_download=not args.offline,
            )
            logger.info(
                "%-30s datasheets: %d resolved (%d local, %d cached, %d downloaded), "
                "%d unresolved",
                stem,
                len(acquisition.resolved),
                acquisition.local_hits,
                acquisition.cache_hits,
                acquisition.downloads,
                len(acquisition.unresolved),
            )
            composed = compose_sheet(grid, layout_keys, acquisition.resolved)
            # ST's own URL where we have one; otherwise the local PDF that
            # answered for this part, so the cell always says what was read.
            for part, path in acquisition.resolved.items():
                datasheet_urls[part] = acquisition.urls.get(part) or st_datasheet_url(path)
        else:
            composed = api_only_sheet(grid, layout_keys)
            if args.datasheet_urls:
                for row in grid.rows:
                    if not row.get("product_id"):
                        continue
                    try:
                        url = fetch_datasheet_url(fetcher, row["product_id"])
                    except FetchError as exc:
                        logger.debug("rpn-info %s: %s", row["part_number"], exc)
                        continue
                    if url:
                        datasheet_urls[row["part_number"]] = url

        corrected = out_dir / f"{stem}.xlsx"
        write_corrected(
            corrected,
            sheet,
            grid,
            composed,
            datasheet_urls=datasheet_urls,
            provenance=datasheet_first,
        )

        entry = {
            "level_id": grid.level_id,
            "level_title": grid.level_title,
            "api_rows": len(grid.rows),
            "api_columns": len(grid.columns),
            "corrected": corrected.name,
        }

        result = None
        if sheet.is_synthetic:
            # No original means nothing to diff against. Saying "1352 added
            # columns" against an empty sheet would be arithmetic, not a
            # finding, so the diff is omitted rather than faked.
            entry["source_workbook"] = None
            entry["diff"] = None
            entry["note"] = "discovered selector: no original workbook to diff against"
        else:
            result = compare(sheet, grid, composed, with_source_classes=datasheet_first)
            diff_path = out_dir / f"{stem} - diff.xlsx"
            write_diff(
                diff_path, result, level_id=grid.level_id, level_title=grid.level_title
            )
            entry["source_workbook"] = sheet.path.name if sheet.path else None
            entry["diff"] = diff_path.name
            entry.update(result.summary())
        if datasheet_first:
            entry["provenance"] = composed.token_counts()
            entry["datasheets"] = acquisition.summary()
            entry["parts_without_datasheet"] = len(composed.without_datasheet)
            entry["datasheets_with_summary_table"] = composed.families_with_summary
            entry["datasheets_without_summary_table"] = composed.families_without_summary
            entry["extraction_notes"] = composed.extraction_notes
            # The source classes are already in result.summary()["classes"] in
            # datasheet mode, and that is what the aggregate loop below adds up.

        report["files"][stem] = entry
        if result is None:
            logger.info(
                "%-30s %-8s %3d parts, %2d columns, discovered (no diff)",
                stem, grid.level_id, len(grid.rows), len(grid.columns),
            )
            continue

        for name, count in result.summary()["classes"].items():
            totals[name] = totals.get(name, 0) + count

        logger.info(
            "%-30s %-8s %3d parts, %5d cells, %4d CHANGED, %3d BLANK_FILLED, "
            "%3d MISSING_FROM_ST, %4d ADDED_COLUMN, %2d NEW_PART, %2d NOT_IN_ST_DATA",
            stem,
            grid.level_id,
            result.parts_compared,
            result.cells_compared,
            result.counts["CHANGED"],
            result.counts["BLANK_FILLED"],
            result.counts["MISSING_FROM_ST"],
            result.counts["ADDED_COLUMN"],
            result.counts["NEW_PART"],
            result.counts["NOT_IN_ST_DATA"],
        )

    report["network_calls"] = fetcher.calls
    report["cache_hits"] = fetcher.hits

    provenance_totals: dict[str, int] = {}
    if datasheet_first:
        for entry in report["files"].values():
            for token, count in entry.get("provenance", {}).items():
                provenance_totals[token] = provenance_totals.get(token, 0) + count
        report["provenance_totals"] = provenance_totals

    after = digest_inputs(files)
    changed = [name for name, digest in before.items() if after.get(name) != digest]
    report["inputs_unchanged"] = not changed
    (out_dir / "run_report.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n"
    )

    logger.info("")
    logger.info(
        "TOTAL  %d CHANGED, %d BLANK_FILLED, %d MISSING_FROM_ST, %d ADDED_COLUMN, "
        "%d NEW_PART, %d NOT_IN_ST_DATA, %d unchanged",
        totals["CHANGED"],
        totals["BLANK_FILLED"],
        totals["MISSING_FROM_ST"],
        totals["ADDED_COLUMN"],
        totals["NEW_PART"],
        totals["NOT_IN_ST_DATA"],
        totals[UNCHANGED],
    )
    if datasheet_first:
        logger.info(
            "SOURCE %d DATASHEET_OVERRIDES_API, %d ORIGINAL_MATCHED_API_NOT_DATASHEET",
            totals.get("DATASHEET_OVERRIDES_API", 0),
            totals.get("ORIGINAL_MATCHED_API_NOT_DATASHEET", 0),
        )
        logger.info(
            "CELLS  " + ", ".join(f"{count} {token}" for token, count in provenance_totals.items())
        )
    logger.info("network calls: %d, cache hits: %d", fetcher.calls, fetcher.hits)
    if report["unresolved"]:
        logger.warning(
            "unresolved: %s", ", ".join(u["file"] for u in report["unresolved"])
        )

    if changed:
        raise InputsModified(
            "input workbooks changed during the run: " + ", ".join(changed)
        )
    logger.info("inputs verified byte-identical (%d files)", len(files))
    return 1 if report["unresolved"] else 0


def do_resolve_only(args) -> int:
    input_dir = Path(args.input)
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    files = input_files(input_dir, args.only)
    if not files:
        logger.error("no .xlsx inputs found in %s", input_dir)
        return 2
    before = digest_inputs(files)
    fetcher = Fetcher(cache_dir=Path(args.cache), use_cache=not args.no_cache, offline=args.offline)
    sheets = {read_original(p).stem: read_original(p) for p in files}
    resolutions = do_resolve(args, fetcher, sheets, out_dir / "series_map.json")
    unresolved = [s for s, r in resolutions.items() if not r.resolved]
    logger.info("network calls: %d, cache hits: %d", fetcher.calls, fetcher.hits)
    if digest_inputs(files) != before:
        raise InputsModified("input workbooks changed during resolve")
    return 1 if unresolved else 0


def do_discover(args) -> int:
    """Enumerate ST's selector tree and write catalog.json. Builds nothing."""
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    fetcher = Fetcher(
        cache_dir=Path(args.cache), use_cache=not args.no_cache, offline=args.offline
    )

    # Tie a discovered level id back to a local workbook where one exists.
    local_stems: dict[str, str] = {}
    files = input_files(Path(args.input), args.only)
    if files:
        sheets = {}
        for path in files:
            sheet = read_original(path)
            sheets[sheet.stem] = sheet
        for stem, resolution in do_resolve(
            args, fetcher, sheets, out_dir / "series_map.json"
        ).items():
            if resolution.resolved:
                local_stems[resolution.level_id] = stem

    levels = tuple(p.strip().upper() for p in args.levels.split(",") if p.strip())
    catalog = discover(fetcher, include=levels, local_stems=local_stems)
    path = out_dir / "catalog.json"
    catalog.write(path)

    covered = sum(1 for s in catalog.selectors if s.local_workbook)
    logger.info("")
    for family, label in (("SC", "catalogues"), ("SS", "series"), ("LN", "product lines")):
        found = catalog.by_level(family)
        if found:
            logger.info("%-14s %3d %s", family, len(found), label)
    logger.info("%-14s %3d total", "", len(catalog.selectors))
    logger.info(
        "%-14s %3d already have a local workbook, %d would be new",
        "", covered, len(catalog.selectors) - covered,
    )
    if catalog.failed:
        logger.warning("%d unreachable: %s", len(catalog.failed),
                       ", ".join(sorted(catalog.failed)))
    for level_id, detail in catalog.seed_mismatches.items():
        logger.warning("seed %s: %s", level_id, detail)
    logger.info("wrote %s", path)
    logger.info("nothing was built; run `build --selectors all` to generate them")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="stproducts",
        description="Rebuild ST product-selector spreadsheets from ST's selector API.",
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="debug logging")
    sub = parser.add_subparsers(dest="command", required=True)

    def common(p):
        p.add_argument("--input", default=str(DEFAULT_INPUT), help="directory of original .xlsx")
        p.add_argument("--out", default=str(DEFAULT_OUTPUT), help="output directory")
        p.add_argument("--cache", default=str(DEFAULT_CACHE), help="HTTP cache directory")
        p.add_argument("--only", metavar="STEM", help="restrict to workbooks matching STEM")
        p.add_argument("--refresh", action="store_true", help="rebuild series_map.json")
        p.add_argument("--no-cache", action="store_true", help="ignore the HTTP cache")
        p.add_argument(
            "--offline", action="store_true", help="fail rather than make a network call"
        )

    build = sub.add_parser("build", help="fetch, correct, diff, and write everything")
    common(build)
    build.add_argument(
        "--source", choices=("datasheet", "api"), default="datasheet",
        help="where values come from; 'datasheet' (default) reads the PDFs and "
             "falls back to the API per column, 'api' is the pre-inversion behaviour",
    )
    build.add_argument(
        "--datasheets", default="datasheets",
        help="directory of local datasheet PDFs, searched first",
    )
    build.add_argument(
        "--datasheet-cache", default="datasheets_cache",
        help="where datasheets fetched from ST are stored",
    )
    build.add_argument(
        "--datasheet-urls", action="store_true",
        help="populate the Datasheet URL column (one rpn-info call per part)",
    )
    build.add_argument(
        "--selectors", choices=("local", "discovered", "all"), default="local",
        help="which selectors to build: 'local' (default) only the workbooks "
             "in --input, 'discovered' only the ones enumerated from ST, "
             "'all' both, preferring the local workbook where one exists so "
             "its diff is kept",
    )
    build.set_defaults(func=do_build)

    resolve = sub.add_parser("resolve", help="map local workbooks to level ids")
    common(resolve)
    resolve.set_defaults(func=do_resolve_only)

    disco = sub.add_parser(
        "discover",
        help="enumerate every selector ST publishes; writes catalog.json",
    )
    common(disco)
    disco.add_argument(
        "--levels", default="SC,SS,LN",
        help="which level families to include (default SC,SS,LN)",
    )
    disco.set_defaults(func=do_discover)

    diff = sub.add_parser("diff", help="re-diff from cache without refetching")
    common(diff)
    diff.set_defaults(
        func=do_build,
        datasheet_urls=False,
        offline=True,
        source="datasheet",
        datasheets="datasheets",
        datasheet_cache="datasheets_cache",
    )

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(message)s",
        stream=sys.stdout,
    )
    try:
        return args.func(args)
    except InputsModified as exc:
        logger.error("FATAL: %s", exc)
        return 3
    except FetchError as exc:
        logger.error("FATAL: %s", exc)
        return 4


if __name__ == "__main__":
    raise SystemExit(main())
