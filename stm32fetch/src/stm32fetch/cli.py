"""stm32fetch: catalog, list, download, run, pipeline (STM32FETCH_FINAL_SPEC.md §6)."""

from __future__ import annotations

import argparse
import logging
import sys

from .batch import run_batch
from .catalog import DEFAULT_CATALOG_PATH, DEFAULT_CLASS_ID, DEFAULT_LOCALE, DEFAULT_RESOURCE_TYPE
from .catalog import build_catalog, load_catalog, verify_catalog
from .download import DEFAULT_RATE_SECONDS, download_many
from .net import DEFAULT_IMPERSONATE, make_session
from .series import manuals_matching_series, suggest_series

logger = logging.getLogger("stm32fetch")


def _common_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(add_help=False)
    p.add_argument("--manuals-dir", default="manuals", help="downloaded-PDF folder (default: manuals/)")
    p.add_argument("--json-dir", default="json", help="rmtables JSON output folder (default: json/)")
    p.add_argument(
        "--catalog-path", default=str(DEFAULT_CATALOG_PATH),
        help="catalog cache path (default: catalog.json)",
    )
    p.add_argument("--rate", type=float, default=DEFAULT_RATE_SECONDS, help="min seconds between HTTP requests")
    p.add_argument(
        "--jobs", type=int, default=1,
        help="concurrency (downloads: network I/O; run: separate rmtables processes)",
    )
    p.add_argument("--force", action="store_true", help="re-download / re-run even if already up to date")
    p.add_argument(
        "--impersonate", default=DEFAULT_IMPERSONATE,
        help="curl_cffi browser profile (default: chrome; bump if ST tightens its TLS check)",
    )
    p.add_argument("--locale", default=DEFAULT_LOCALE, help="cxst API locale (default: en)")
    p.add_argument("--class-id", default=DEFAULT_CLASS_ID, help="cxst API class id (default: CL1734)")
    p.add_argument(
        "--resource-type", default=DEFAULT_RESOURCE_TYPE,
        help="cxst API resource type (default: reference_manual)",
    )
    p.add_argument("--log-level", default="INFO")
    return p


def _selection_parser(required: bool) -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(add_help=False)
    g = p.add_mutually_exclusive_group(required=required)
    g.add_argument("--all", action="store_true", help="every manual in the catalog")
    g.add_argument("--series", help="e.g. STM32F4, STM32H7, C0 (matches every manual in that family)")
    g.add_argument("--rm", help="exactly one manual by RM number, e.g. RM0490")
    return p


def _configure_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,
    )


def _load_or_build_catalog(args, session) -> dict:
    return load_catalog(args.catalog_path) or build_catalog(
        args.catalog_path, session=session, locale=args.locale,
        class_id=args.class_id, resource_type=args.resource_type,
    )


def _select_manuals(catalog: dict, args) -> list[dict]:
    """Resolves --all/--series/--rm against `catalog`, printing what was
    selected (or a helpful hint on no match, per STM32FETCH_FINAL_SPEC.md
    §6: never silently do nothing). Empty list means "nothing selected" --
    callers treat that as a failure exit code."""
    manuals = catalog["manuals"]
    if getattr(args, "rm", None):
        rm = args.rm.upper()
        matches = [m for m in manuals if m["rm_number"] == rm]
        if not matches:
            print(f"no manual with RM number {rm!r} in the catalog", file=sys.stderr)
        return matches
    if getattr(args, "series", None):
        matches = manuals_matching_series(manuals, args.series)
        if not matches:
            hint = suggest_series(manuals, args.series)
            print(f"no manual matches series {args.series!r}; closest known series: {hint}", file=sys.stderr)
        return matches
    return manuals  # --all, or (for `list`) no filter given at all


def cmd_catalog(args) -> int:
    _configure_logging(args.log_level)
    session = make_session(args.impersonate)
    catalog = build_catalog(
        args.catalog_path, refresh=args.refresh, session=session, locale=args.locale,
        class_id=args.class_id, resource_type=args.resource_type,
    )
    print(f"catalog: {len(catalog['manuals'])} manuals (scraped_at={catalog['scraped_at']})")

    if args.verify:
        problems = verify_catalog(catalog, session=session)
        if problems:
            print(f"verify: {len(problems)} pdf_url(s) not reachable:")
            for rm_number, url, status in problems:
                print(f"  {rm_number}: HTTP {status or 'error'} -- {url}")
            return 1
        print("verify: all pdf_urls reachable (200)")
    return 0


def cmd_list(args) -> int:
    _configure_logging(args.log_level)
    catalog = _load_or_build_catalog(args, make_session(args.impersonate))
    matches = _select_manuals(catalog, args)
    for m in matches:
        print(f"{m['rm_number']}\t{','.join(m['series'])}\t{m['title']}")
    return 0 if matches else 1


def cmd_download(args) -> int:
    _configure_logging(args.log_level)
    session = make_session(args.impersonate)
    catalog = _load_or_build_catalog(args, session)
    matches = _select_manuals(catalog, args)
    if not matches:
        return 1
    print(f"downloading {len(matches)} manual(s): {[m['rm_number'] for m in matches]}")
    results = download_many(
        matches, args.manuals_dir, rate=args.rate, force=args.force, jobs=args.jobs, session=session,
    )
    for r in results:
        suffix = f" -- {r.error}" if r.error else ""
        print(f"  {r.status}: {r.filename} ({r.bytes} bytes){suffix}")
    return 1 if any(r.status == "failed" for r in results) else 0


def cmd_run(args) -> int:
    _configure_logging(args.log_level)
    # SPLIT_TABLES_TASK.md §6: off by default here (mirrors rmtables' own
    # default) -- only `pipeline` turns it on by default.
    summary = run_batch(
        args.manuals_dir, args.json_dir, force=args.force, jobs=args.jobs,
        mode=args.mode, rmtables_src=args.rmtables_src,
        split_tables=args.split_tables, tables_dir=args.tables_dir,
    )
    print(summary.report())
    return 1 if summary.failed else 0


def cmd_pipeline(args) -> int:
    _configure_logging(args.log_level)
    session = make_session(args.impersonate)
    catalog = _load_or_build_catalog(args, session)
    matches = _select_manuals(catalog, args)
    if not matches:
        return 1
    print(f"pipeline: {len(matches)} manual(s): {[m['rm_number'] for m in matches]}")

    dl_results = download_many(
        matches, args.manuals_dir, rate=args.rate, force=args.force, jobs=args.jobs, session=session,
    )
    for r in dl_results:
        suffix = f" -- {r.error}" if r.error else ""
        print(f"  download {r.status}: {r.filename}{suffix}")

    # SPLIT_TABLES_TASK.md §6: --split-tables defaults ON in `pipeline`
    # (unlike `run`/rmtables itself); `--no-split-tables` overrides it off.
    split_tables = True if args.split_tables is None else args.split_tables
    summary = run_batch(
        args.manuals_dir, args.json_dir, force=args.force, jobs=args.jobs,
        mode=args.mode, rmtables_src=args.rmtables_src,
        split_tables=split_tables, tables_dir=args.tables_dir,
    )
    print(summary.report())
    return 1 if (summary.failed or any(r.status == "failed" for r in dl_results)) else 0


def build_arg_parser() -> argparse.ArgumentParser:
    common = _common_parser()
    p = argparse.ArgumentParser(prog="stm32fetch", description=__doc__)
    sub = p.add_subparsers(dest="command", required=True)

    p_catalog = sub.add_parser("catalog", parents=[common], help="fetch/update catalog.json from the cxst API")
    p_catalog.add_argument("--refresh", action="store_true", help="re-fetch even if a cache exists")
    p_catalog.add_argument(
        "--verify", action="store_true", help="HEAD every pdf_url and flag any that don't return 200"
    )
    p_catalog.set_defaults(func=cmd_catalog)

    p_list = sub.add_parser("list", parents=[common, _selection_parser(required=False)], help="print matching manuals")
    p_list.set_defaults(func=cmd_list)

    p_download = sub.add_parser(
        "download", parents=[common, _selection_parser(required=True)], help="download manual PDFs"
    )
    p_download.set_defaults(func=cmd_download)

    p_run = sub.add_parser("run", parents=[common], help="run rmtables over downloaded PDFs")
    p_run.add_argument("--mode", choices=["subprocess", "import"], default="subprocess")
    p_run.add_argument("--rmtables-src", help="path to rmtables' src/ (override auto-detection)")
    p_run.add_argument(
        "--split-tables", action="store_true",
        help="also write one JSON file per table, forwarded to rmtables (default: off)",
    )
    p_run.add_argument(
        "--tables-dir", default="tables", help="per-table output root (default: tables/)"
    )
    p_run.set_defaults(func=cmd_run)

    p_pipeline = sub.add_parser(
        "pipeline", parents=[common, _selection_parser(required=True)],
        help="catalog (if needed) + download + run, end-to-end",
    )
    p_pipeline.add_argument("--mode", choices=["subprocess", "import"], default="subprocess")
    p_pipeline.add_argument("--rmtables-src", help="path to rmtables' src/ (override auto-detection)")
    p_pipeline.add_argument(
        "--split-tables", action=argparse.BooleanOptionalAction, default=None,
        help="also write one JSON file per table, forwarded to rmtables (default: ON in pipeline; "
             "pass --no-split-tables to disable)",
    )
    p_pipeline.add_argument(
        "--tables-dir", default="tables", help="per-table output root (default: tables/)"
    )
    p_pipeline.set_defaults(func=cmd_pipeline)

    return p


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
