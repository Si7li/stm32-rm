"""Run rmtables over a folder of downloaded PDFs (STM32_FETCH_TASK.md).

rmtables is never modified -- it's called either by importing its CLI
`main()` directly (library mode) or via `subprocess` (`python -m
rmtables.cli ...`, matching how it's invoked everywhere else in this
project). Subprocess is the default: each huge manual (RM0477 alone is
3764 pages, ~2-3 GB peak RSS) gets its own process, so memory is fully
released between manuals and a crash in one file can't take the whole
batch down with it -- import mode is available for callers who'd rather
avoid the subprocess overhead (e.g. tests) and accept sharing one process's
memory across every manual.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

from .catalog import RM_NUMBER_SEARCH_RE

logger = logging.getLogger("stm32fetch.batch")

# stm32fetch/src/stm32fetch/batch.py -> repo root -> sibling checkout's src/.
_DEFAULT_SIBLING_RMTABLES_SRC = (
    Path(__file__).resolve().parents[3] / "stm32-table-extractor" / "src"
)


def _importable(module: str) -> bool:
    import importlib

    try:
        importlib.import_module(module)
        return True
    except ImportError:
        return False


def resolve_rmtables_src(explicit: str | Path | None = None) -> Path | None:
    """Where rmtables' `src/` lives, for `sys.path`/`PYTHONPATH` injection --
    `None` means "don't inject anything," either because an explicit path
    wasn't needed (rmtables is already importable as-is) or because nothing
    usable was found (the caller will get a clear ImportError/subprocess
    failure rather than a silent no-op)."""
    if explicit is not None:
        return Path(explicit)
    env = os.environ.get("RMTABLES_SRC")
    if env:
        return Path(env)
    if _importable("rmtables"):
        return None
    if (_DEFAULT_SIBLING_RMTABLES_SRC / "rmtables" / "__init__.py").exists():
        return _DEFAULT_SIBLING_RMTABLES_SRC
    return None


def _run_in_process(argv: list[str], rmtables_src: Path | None) -> tuple[int, str]:
    if rmtables_src is not None and str(rmtables_src) not in sys.path:
        sys.path.insert(0, str(rmtables_src))
    try:
        from rmtables.cli import main as rmtables_main
    except ImportError as exc:
        return 1, f"could not import rmtables (looked in {rmtables_src}): {exc}"
    try:
        rc = rmtables_main(argv)
        return rc, ""
    except Exception as exc:  # noqa: BLE001 -- one bad PDF must not kill the batch
        return 1, str(exc)


def _run_subprocess(argv: list[str], rmtables_src: Path | None, timeout: float | None) -> tuple[int, str]:
    env = os.environ.copy()
    if rmtables_src is not None:
        existing = env.get("PYTHONPATH", "")
        env["PYTHONPATH"] = str(rmtables_src) + (os.pathsep + existing if existing else "")
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "rmtables.cli", *argv],
            capture_output=True, text=True, env=env, timeout=timeout,
        )
        return proc.returncode, proc.stderr
    except subprocess.TimeoutExpired as exc:
        return 1, f"timed out after {exc.timeout}s"
    except Exception as exc:  # noqa: BLE001
        return 1, str(exc)


def _derive_stem(pdf_path: Path, rmtables_src: Path | None) -> str | None:
    """Independently computes the `{RM}_{Rev}` stem rmtables will use for
    this PDF (FILENAME_SCHEME_TASK.md), by calling rmtables' OWN
    `derive_metadata` + `doc_stem` -- never duplicated here -- so the
    idempotency skip-check (and the explicit `-o` path passed to rmtables)
    can be decided WITHOUT running the full, expensive extraction first.

    Returns `None` if rmtables/pdfplumber can't be imported, or if metadata
    derivation fails for any reason (corrupt/unreadable PDF); the caller
    then falls back to the filename-derived RM number with no revision
    segment, same as before this task -- safe because it can only cause an
    avoidable re-run, never a wrong skip of stale output."""
    if rmtables_src is not None and str(rmtables_src) not in sys.path:
        sys.path.insert(0, str(rmtables_src))
    try:
        import pdfplumber
        from rmtables.exporter import doc_stem
        from rmtables.metadata import derive_metadata
    except ImportError:
        return None
    try:
        with pdfplumber.open(pdf_path) as pdf:
            meta = derive_metadata(pdf, str(pdf_path), {})
        return doc_stem(meta["name_datasheet"], meta["rev"], pdf_path=str(pdf_path))
    except Exception:
        logger.warning(
            "could not pre-derive the output stem for %s; falling back to "
            "the filename-derived RM number with no revision segment",
            pdf_path.name, exc_info=True,
        )
        return None


def stem_for(pdf_path: Path, rmtables_src: Path | None = None) -> str:
    """`{RM}_{Rev}` (FILENAME_SCHEME_TASK.md), pre-derived from the PDF
    itself when possible; falls back to the RM number parsed from the
    filename (no revision segment) otherwise.

    `rmtables_src`, like everywhere else in this module, is an explicit
    override -- `None` (the default) means "auto-detect," resolved here via
    `resolve_rmtables_src` (env var / already-importable / sibling
    checkout), NOT "don't bother looking." A caller that already resolved
    it (`run_batch`, `run_rmtables_on_pdf`) can pass that result straight
    through; `resolve_rmtables_src` treats an already-resolved `Path` as an
    explicit override and returns it unchanged, so re-resolving here is
    cheap and never redoes real work."""
    resolved = resolve_rmtables_src(rmtables_src)
    stem = _derive_stem(pdf_path, resolved)
    if stem:
        return stem
    m = RM_NUMBER_SEARCH_RE.search(pdf_path.stem)
    return m.group(0).upper() if m else pdf_path.stem


def output_name_for(pdf_path: Path, rmtables_src: Path | None = None) -> str:
    """`{RM}_{Rev}.json` (FILENAME_SCHEME_TASK.md; previously `<rm_number>.json`)."""
    return stem_for(pdf_path, rmtables_src) + ".json"


def is_up_to_date(pdf_path: Path, json_path: Path) -> bool:
    return json_path.exists() and json_path.stat().st_mtime >= pdf_path.stat().st_mtime


def is_split_up_to_date(pdf_path: Path, tables_dir: str | Path, manual: str) -> bool:
    """SPLIT_TABLES_TASK.md §6: the split step (independent of the combined
    JSON's own idempotency check below) is up to date when that manual's
    `_index.json` already exists and is newer than the PDF."""
    index_path = Path(tables_dir) / manual / "_index.json"
    return index_path.exists() and index_path.stat().st_mtime >= pdf_path.stat().st_mtime


@dataclass
class FileResult:
    pdf: str
    json_path: str
    status: str  # "processed" | "skipped" | "failed"
    table_count: int | None = None
    error: str | None = None
    elapsed: float = 0.0


@dataclass
class BatchSummary:
    results: list[FileResult] = field(default_factory=list)
    elapsed: float = 0.0

    @property
    def processed(self) -> list[FileResult]:
        return [r for r in self.results if r.status == "processed"]

    @property
    def skipped(self) -> list[FileResult]:
        return [r for r in self.results if r.status == "skipped"]

    @property
    def failed(self) -> list[FileResult]:
        return [r for r in self.results if r.status == "failed"]

    def report(self) -> str:
        lines = [
            f"processed={len(self.processed)} skipped={len(self.skipped)} "
            f"failed={len(self.failed)} elapsed={self.elapsed:.1f}s"
        ]
        for r in self.processed:
            lines.append(f"  {r.pdf}: {r.table_count} tables ({r.elapsed:.1f}s)")
        for r in self.failed:
            lines.append(f"  FAILED {r.pdf}: {r.error}")
        return "\n".join(lines)


def run_rmtables_on_pdf(
    pdf_path: str | Path,
    json_dir: str | Path,
    *,
    mode: str = "subprocess",
    rmtables_src: str | Path | None = None,
    validate: bool = True,
    extra_args: list[str] | None = None,
    timeout: float | None = None,
    split_tables: bool = False,
    tables_dir: str | Path | None = None,
    filename_slug: bool = False,
    no_prune: bool = False,
    out_path: str | Path | None = None,
) -> FileResult:
    """Runs rmtables on one PDF; never raises -- any failure (import,
    subprocess, bad output) comes back as a `FileResult(status="failed")`
    so a caller looping over many manuals can just keep going.

    `split_tables`/`tables_dir`/`filename_slug`/`no_prune` are forwarded
    to rmtables' own `--split-tables`/`--tables-dir`/`--filename-slug`/
    `--no-prune` flags unmodified (SPLIT_TABLES_TASK.md §6) -- rmtables'
    own manual-folder naming (`{RM}_{Rev}`, FILENAME_SCHEME_TASK.md) already
    produces the `<tables-dir>/{RM}_{Rev}/` layout, so `tables_dir` here is
    the shared root, not a per-manual path.

    `out_path` lets `run_batch` pass down the `{stem}.json` path it already
    computed for its idempotency check, so the (cheap but non-free) stem
    derivation isn't repeated; standalone callers can omit it and one is
    computed here."""
    pdf_path = Path(pdf_path)
    json_dir = Path(json_dir)
    json_dir.mkdir(parents=True, exist_ok=True)
    src = resolve_rmtables_src(rmtables_src)
    out_path = Path(out_path) if out_path is not None else json_dir / output_name_for(pdf_path, src)
    argv = [str(pdf_path), "-o", str(out_path)]
    if validate:
        argv.append("--validate")
    if split_tables:
        argv.append("--split-tables")
        if tables_dir is not None:
            argv.extend(["--tables-dir", str(tables_dir)])
        if filename_slug:
            argv.append("--filename-slug")
        if no_prune:
            argv.append("--no-prune")
    argv.extend(extra_args or [])

    start = time.monotonic()
    try:
        if mode == "import":
            rc, err = _run_in_process(argv, src)
        else:
            rc, err = _run_subprocess(argv, src, timeout)
    except Exception as exc:  # noqa: BLE001 -- belt and suspenders
        rc, err = 1, str(exc)
    elapsed = time.monotonic() - start

    if rc != 0:
        logger.error("rmtables failed on %s: %s", pdf_path.name, err.strip()[-2000:])
        return FileResult(str(pdf_path), str(out_path), "failed", error=err.strip()[-500:], elapsed=elapsed)

    table_count = None
    try:
        table_count = len(json.loads(out_path.read_text())["tables"])
    except Exception:
        logger.warning("rmtables succeeded on %s but output couldn't be read back", pdf_path.name, exc_info=True)

    return FileResult(str(pdf_path), str(out_path), "processed", table_count=table_count, elapsed=elapsed)


def run_batch(
    manuals_dir: str | Path,
    json_dir: str | Path,
    *,
    force: bool = False,
    jobs: int = 1,
    mode: str = "subprocess",
    rmtables_src: str | Path | None = None,
    validate: bool = True,
    extra_args: list[str] | None = None,
    timeout: float | None = None,
    split_tables: bool = False,
    tables_dir: str | Path | None = None,
    filename_slug: bool = False,
    no_prune: bool = False,
) -> BatchSummary:
    manuals_dir, json_dir = Path(manuals_dir), Path(json_dir)
    pdfs = sorted(manuals_dir.glob("*.pdf"))
    if jobs > 1 and mode == "import":
        logger.warning(
            "jobs=%d has no effect in import mode (Python's GIL serializes CPU-bound "
            "work in one process); use --mode subprocess for real parallelism", jobs,
        )

    src = resolve_rmtables_src(rmtables_src)

    def _one(pdf_path: Path) -> FileResult:
        # {RM}_{Rev} is computed ONCE here (FILENAME_SCHEME_TASK.md) and
        # reused for both idempotency checks and the -o path passed down,
        # so the skip-check and the actual run key off the identical name.
        stem = stem_for(pdf_path, src)
        out_path = json_dir / f"{stem}.json"
        # The split step's idempotency is checked independently of the
        # combined JSON's (SPLIT_TABLES_TASK.md §6): a manual whose
        # combined output is already current but whose per-table files
        # aren't (e.g. --split-tables was just turned on) must still run.
        combined_current = is_up_to_date(pdf_path, out_path)
        split_current = (
            not split_tables
            or (tables_dir is not None and is_split_up_to_date(pdf_path, tables_dir, stem))
        )
        if not force and combined_current and split_current:
            logger.info("%s is up to date; skipping", pdf_path.name)
            return FileResult(str(pdf_path), str(out_path), "skipped")
        return run_rmtables_on_pdf(
            pdf_path, json_dir, mode=mode, rmtables_src=rmtables_src,
            validate=validate, extra_args=extra_args, timeout=timeout,
            split_tables=split_tables, tables_dir=tables_dir,
            filename_slug=filename_slug, no_prune=no_prune,
            out_path=out_path,
        )

    start = time.monotonic()
    if jobs <= 1 or mode != "subprocess":
        results = [_one(p) for p in pdfs]
    else:
        from concurrent.futures import ThreadPoolExecutor

        results = [None] * len(pdfs)
        with ThreadPoolExecutor(max_workers=jobs) as pool:
            futures = {pool.submit(_one, p): i for i, p in enumerate(pdfs)}
            for future in futures:
                results[futures[future]] = future.result()

    return BatchSummary(results=results, elapsed=time.monotonic() - start)
