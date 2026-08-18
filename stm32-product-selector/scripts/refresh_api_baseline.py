#!/usr/bin/env python
"""Re-record the ``--source api`` reproducibility baseline.

Run this **only** after confirming the output moved for a legitimate reason.
``test_7_api_source_reproduces_the_pre_inversion_output`` prints which columns
carried the change; if they are all volatile commercial columns (``Buy On
Line``, ``Marketing Status``) the drift is ST's data, not the code, and
re-recording is correct. If a specification column moved, find out why first
-- re-recording would enshrine the regression as the new expectation.

    python scripts/refresh_api_baseline.py [--check]

``--check`` reports what would change and writes nothing.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT.parent
sys.path.insert(0, str(ROOT / "src"))

from stproducts.cli import main  # noqa: E402

BASELINE = ROOT / "tests" / "data" / "api_baseline_sha256.json"


def build_hashes(out: Path) -> dict[str, str]:
    code = main([
        "build", "--source", "api",
        "--input", str(REPO / "product_selector"),
        "--out", str(out),
        "--cache", str(REPO / "cache"),
        "--offline",
    ])
    if code != 0:
        raise SystemExit(f"build failed with exit code {code}")
    return {
        p.name: hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(out.glob("*.xlsx"))
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check", action="store_true", help="report differences, write nothing"
    )
    return parser.parse_args()


def main_cli() -> int:
    args = parse_args()
    old = json.loads(BASELINE.read_text()) if BASELINE.exists() else {}

    with tempfile.TemporaryDirectory() as tmp:
        new = build_hashes(Path(tmp))

    moved = sorted(k for k in set(old) | set(new) if old.get(k) != new.get(k))
    if not moved:
        print(f"baseline already current ({len(new)} files)")
        return 0

    print(f"{len(moved)} of {len(new)} files differ:")
    for name in moved:
        state = "new" if name not in old else "removed" if name not in new else "changed"
        print(f"   {state:8} {name}")

    if args.check:
        print("\n--check: nothing written")
        return 1

    BASELINE.write_text(json.dumps(new, indent=2, sort_keys=True) + "\n")
    print(f"\nwrote {BASELINE.relative_to(REPO)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main_cli())
