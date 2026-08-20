"""
generate_report.py

Aggregates the per-document errata RAG JSON files (canonical schema, see
AGENTS.md) into a single deterministic report JSON (output/report.json by
default).

Content:
  - global totals (documents / families / errata / groups / chunks)
  - per-family summary (documents, errata, groups, chunks, reference manuals)
  - per-document rows: wrapper meta + file path (sorted by doc_id)
  - skipped files: JSONs under the scan dir that do not follow the canonical
    schema (e.g. the legacy flat output/esXXXX_*.json with the old "chunks"
    key) — listed, not counted.
  - duplicate doc_ids (same document copied in several folders) are resolved
    deterministically: the alphabetically-first path wins, the other
    occurrences are listed under "duplicates" (never deleted).

No timestamps: two runs on the same inputs produce byte-identical reports.

Usage:
    python generate_report.py [--scan-dir output] [--report output/report.json]
"""

import argparse
import json
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent
DEFAULT_SCAN_DIR = Path(__import__("os").environ.get(
    "ERRATA_OUTPUT_DIR", BASE.parents[1] / "output"))
DEFAULT_REPORT = DEFAULT_SCAN_DIR / "report.json"

WRAPPER_KEYS = ("doc_id", "doc_version", "doc_date", "family",
                "reference_manual", "url_pdf", "total_errata",
                "total_groups", "total_chunks")


def scan(dir_path: Path) -> tuple[list[dict], list[dict]]:
    """Returns (documents, skipped) from the canonical JSONs under dir_path.

    Documents are de-duplicated by doc_id: the occurrence with the fewest
    path components wins (the canonical family folders live at depth 1), the
    other occurrences are listed in the report as "duplicates" (never
    deleted).
    """
    documents = []
    skipped = []
    for p in sorted(dir_path.rglob("*_errata_rag.json")):
        with open(p, encoding="utf-8") as f:
            doc = json.load(f)
        if "documents" not in doc:
            skipped.append({
                "file": str(p.relative_to(dir_path)),
                "reason": "missing 'documents' key (legacy schema?)",
                "keys": sorted(doc.keys()),
            })
            continue
        row = {k: doc[k] for k in WRAPPER_KEYS if k in doc}
        row["file"] = str(p.relative_to(dir_path))
        documents.append(row)
    documents.sort(key=lambda r: (r["doc_id"], len(Path(r["file"]).parts), r["file"]))

    seen: set[str] = set()
    unique: list[dict] = []
    duplicates: list[dict] = []
    for row in documents:
        if row["doc_id"] in seen:
            duplicates.append(row)
        else:
            seen.add(row["doc_id"])
            unique.append(row)
    return unique, duplicates, skipped


def family_of(doc: dict) -> str:
    return Path(doc["file"]).parts[0].upper() if len(Path(doc["file"]).parts) > 1 else "ROOT"


def build_report(documents: list[dict], duplicates: list[dict],
                 skipped: list[dict]) -> dict:
    by_family = {}
    for doc in documents:
        by_family.setdefault(family_of(doc), []).append(doc)

    families = {}
    for name, docs in sorted(by_family.items()):
        families[name] = {
            "documents": len(docs),
            "total_errata": sum(d["total_errata"] for d in docs),
            "total_groups": sum(d["total_groups"] for d in docs),
            "total_chunks": sum(d["total_chunks"] for d in docs),
            "reference_manuals": sorted({d["reference_manual"] for d in docs if d.get("reference_manual")}),
        }

    return {
        "generated_by": "generate_report.py",
        "total_families": len(families),
        "total_documents": len(documents),
        "total_errata": sum(d["total_errata"] for d in documents),
        "total_groups": sum(d["total_groups"] for d in documents),
        "total_chunks": sum(d["total_chunks"] for d in documents),
        "families": families,
        "documents": documents,
        "duplicates": duplicates,
        "skipped": skipped,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Aggregate errata RAG JSONs into a report.")
    parser.add_argument("--scan-dir", type=Path, default=DEFAULT_SCAN_DIR,
                        help="directory scanned recursively (default: output/)")
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT,
                        help="output report path (default: output/report.json)")
    args = parser.parse_args(argv)

    if not args.scan_dir.exists():
        print(f"No such directory: {args.scan_dir}")
        return 1
    documents, duplicates, skipped = scan(args.scan_dir)
    if not documents:
        print(f"No canonical errata JSON found under {args.scan_dir}")
        return 1
    report = build_report(documents, duplicates, skipped)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    with open(args.report, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print(f"Report written: {args.report}")
    print(f"  {len(documents)} documents, {report['total_families']} families, "
          f"{report['total_errata']} errata, {report['total_chunks']} chunks")
    for name, fam in report["families"].items():
        print(f"  {name}: {fam['documents']} docs, {fam['total_errata']} errata, "
              f"{fam['total_chunks']} chunks")
    if duplicates:
        print(f"  duplicates (same doc_id, kept first path only): {len(duplicates)}")
        for d in duplicates:
            print(f"    - {d['doc_id']} {d['file']}")
    if skipped:
        print(f"  skipped (non-canonical): {len(skipped)}")
        for s in skipped:
            print(f"    - {s['file']}: {s['reason']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
