"""
regression_check.py

Non-regression harness on top of the canonical schema (see AGENTS.md):

  1. Baselines: for every PDF in input/, re-extract with
     errata_extractor.process_pdf and byte-compare against the saved JSON in
     output/ (catches any silent regression in the extractor).
  2. ES0676 reference: validate references/es0676_errata_rag.json (old
      canonical schema from the demo errata_extractor_es0676.py) — 4 chunks per
      errata, deterministic document_id, parent linkage, citation coherence.
  3. Human sampling checklist: deterministic sample of errata per document
     (section id, title, page, url) for manual comparison against the source
     PDFs — focus on workaround bodies, the most fragile extraction output.

Usage:
    python regression_check.py [--seed N]   # default seed 42
"""

import argparse
import hashlib
import json
import random
import sys
from pathlib import Path

from rmerrata import extractor as ex
from rmerrata import rag_utils, validate

BASE = Path(__file__).resolve().parent
PROJECT_ROOT = BASE.parents[1] if BASE.name == "rmerrata" else BASE
INPUT_DIR = ex.INPUT_DIR
OUTPUT_DIR = Path(__import__("os").environ.get("ERRATA_OUTPUT_DIR", ex.OUTPUT_DIR))
REF_ES0676 = PROJECT_ROOT / "references" / "es0676_errata_rag.json"

SECTION_TYPES = {"full_entry", "description", "workaround", "applicability"}


def sha1(s: str) -> str:
    return hashlib.sha1(s.encode("utf-8")).hexdigest()


def check_baselines(problems: list) -> int:
    docs = sorted(OUTPUT_DIR.rglob("*_errata_rag.json"))
    checked = 0
    for doc_path in docs:
        pdf_name = doc_path.stem.split("_")[0]
        pdf_path = ex.find_pdf(pdf_name)
        if pdf_path is None:
            problems.append(f"{doc_path.name}: no source PDF in input/ for baseline check")
            continue
        before = len(problems)
        validate.validate_reproducibility(pdf_path, doc_path, problems)
        if len(problems) == before:
            print(f"  baseline OK  {doc_path.name}")
        else:
            print(f"  baseline FAIL {doc_path.name}")
        checked += 1
    return checked


def check_es0676_reference(problems: list):
    if not REF_ES0676.exists():
        problems.append(f"{REF_ES0676.name}: missing reference (run the demo path patch to regenerate)")
        return
    with open(REF_ES0676, encoding="utf-8") as f:
        doc = json.load(f)
    doc_id = doc["doc_id"]
    if doc["total_chunks"] != 4 * doc["total_errata"]:
        problems.append(f"{doc_id}: total_chunks {doc['total_chunks']} != 4*{doc['total_errata']}")
    by_section = {}
    for c in doc["documents"]:
        by_section.setdefault(c["filters"]["errata_id"], []).append(c)
    if len(by_section) != doc["total_errata"]:
        problems.append(f"{doc_id}: {len(by_section)} sections != {doc['total_errata']} errata")
    for sec, group in by_section.items():
        types = {c["filters"]["section_type"] for c in group}
        if types != SECTION_TYPES:
            problems.append(f"{doc_id}: {sec} types {types}")
        parent = next((c for c in group if c["filters"]["section_type"] == "full_entry"), None)
        if parent is None:
            problems.append(f"{doc_id}: {sec} missing full_entry")
            continue
        if parent["parent_document_id"] is not None or parent["document_id"] != sha1(f"{doc_id}:{sec}:full_entry"):
            problems.append(f"{doc_id}: {sec} full_entry linkage/document_id mismatch")
        for c in group:
            if c["filters"]["section_type"] == "full_entry":
                continue
            if c["parent_document_id"] != parent["document_id"] or \
               c["document_id"] != sha1(f"{doc_id}:{sec}:{c['filters']['section_type']}"):
                problems.append(f"{doc_id}: {sec} {c['filters']['section_type']} linkage/document_id mismatch")
            if c["citation"]["doc_id"] != doc_id or c["citation"]["doc_version"] != doc["doc_version"]:
                problems.append(f"{doc_id}: {sec} citation doc mismatch")
    if problems and all(p.startswith(doc_id) for p in problems):
        pass
    print("  ES0676 reference: OK" if not problems else "  ES0676 reference: FAIL")


def sampling_checklist(seed: int):
    print("\nHuman sampling checklist (compare against the source PDFs, "
          "workarounds in priority):")
    rng = random.Random(seed)
    for doc_path in sorted(OUTPUT_DIR.glob("*_errata_rag.json")):
        idx = rag_utils.RAGIndex.load(doc_path)
        eids = sorted(idx.sections())
        sample = [eids[i] for i in rng.sample(range(len(eids)), min(4, len(eids)))]
        print(f"\n  {doc_path.name} (seed {seed}):")
        for eid in sample:
            fe = idx.lookup_errata(eid)
            wa = next(c for c in idx.sections()[eid]
                      if c["filters"]["section_type"] == "workaround")
            print(f"    - {eid:8s} {fe['citation']['section_title'][:60]}")
            print(f"      p.{fe['citation']['page']:>3d} workaround: {wa['raw_text'][:70]!r}")
            print(f"      {fe['citation']['url']}")


def coverage_summary(problems: list) -> int:
    """Phase 2/4 audit view: per document, chunk coverage (coverage_tree) and
    exact-retrieval spot check on the first errata. Non-blocking (report only)."""
    total_errata = 0
    for doc_path in sorted(OUTPUT_DIR.glob("*_errata_rag.json")):
        idx = rag_utils.RAGIndex.load(doc_path)
        tree = rag_utils.coverage_tree(idx)
        incomplete = [row["errata_id"] for row in tree if not row["complete"]]
        no_group = [row["errata_id"] for row in tree if not row["group"]]
        total_errata += len(tree)
        print(f"  coverage {doc_path.name}: {len(tree)} errata, "
              f"{len(tree) - len(incomplete)} complete, "
              f"{len(incomplete)} incomplete, {len(no_group)} without group")
        for row in tree:
            if not row["complete"] or not row["group"]:
                problems.append(f"{idx.doc_id}: coverage {row['errata_id']} "
                                f"{row['chunks']} group={row['group']}")
        first = sorted(idx.sections())[0]
        res = rag_utils.search_multi(idx, first, top_errata=1)
        if res["errata"] != [first]:
            problems.append(f"{idx.doc_id}: exact retrieval spot check failed")
    return total_errata


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", type=int, default=42, help="seed for the sampling checklist")
    args = parser.parse_args(argv)

    problems = []
    print("== Baselines (re-extraction == saved output/)")
    checked = check_baselines(problems)
    print(f"== ES0676 reference")
    check_es0676_reference(problems)
    print("== Coverage summary (per errata: 4 chunks + group)")
    total_errata = coverage_summary(problems)
    sampling_checklist(args.seed)

    if problems:
        print(f"\nFAILED ({len(problems)}):")
        for p in problems:
            print(f"  - {p}")
        return 1
    print(f"\nRegression OK: {checked} baselines + ES0676 reference valid "
          f"+ {total_errata} errata fully covered.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
