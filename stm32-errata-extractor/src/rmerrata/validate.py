"""
validate_json.py

Validates the errata RAG JSON files produced by errata_extractor.py against the
canonical schema invariants (see AGENTS.md), and verifies reproducibility by
re-extracting each PDF and comparing byte-for-byte.

Invariants checked per document:
  1. total_chunks == 4 * total_errata + total_groups + 1 (document_summary)
  2. exactly 4 chunks per errata id, one of each section_type (full_entry parent,
     description / workaround / applicability children)
  3. document_id unique; parent linkage coherent
  4. document_id == sha1(f"{doc_id}:{section_id}:{type}")  (deterministic)
  5. filters consistent with the Table 3 status matrix:
       affected_revisions == [rev where status != "-"]
       fixed_in_revision   == [rev where status == "-"]
       has_workaround      == any status == "A"
       partial_workaround_only == any status == "P"
       status_by_revision keys == tracked revisions, values in {A, N, P, -}
  6. citation: doc_id/doc_version/doc_date match the wrapper, page >= 1,
     section_title starts with the section id
  7. non-empty embed_text / raw_text
  8. group chunks: one per section 2.x with errata, document_id ==
     sha1(f"{doc_id}:{group_id}:group"), errata_ids all exist and point back
     to the same group_id, filters.peripheral identical group <-> members
  9. document_summary: exactly one, document_id deterministic, page 1, meta tokens
     present, group ids count == total_groups
  10. reproducibility: re-extraction yields byte-identical JSON
"""

import hashlib
import json
import re
import sys
from pathlib import Path

from rmerrata import extractor as ex
from rmerrata import rag_utils

OUTPUT_DIR = Path(__import__("os").environ.get("ERRATA_OUTPUT_DIR", ex.OUTPUT_DIR))

VALID_STATUS = {"A", "N", "P", "-"}
SECTION_TYPES = {"full_entry", "description", "workaround", "applicability"}


def sha1(s: str) -> str:
    return hashlib.sha1(s.encode("utf-8")).hexdigest()


def check(cond: bool, problems: list, msg: str):
    if not cond:
        problems.append(msg)


def validate_document(doc_path: Path) -> list:
    problems = []
    with open(doc_path, encoding="utf-8") as f:
        doc = json.load(f)

    doc_id = doc["doc_id"]
    chunks = doc["documents"]
    check("total_groups" in doc, problems, f"{doc_id}: missing total_groups")
    check(doc["total_chunks"] == 4 * doc["total_errata"] + doc.get("total_groups", -1) + 1, problems,
          f"{doc_id}: total_chunks={doc['total_chunks']} != "
          f"4*{doc['total_errata']}+{doc.get('total_groups')} groups+1 doc")

    ids = [c["document_id"] for c in chunks]
    check(len(ids) == len(set(ids)), problems, f"{doc_id}: duplicate document_id")

    # Group by errata section id (the document_id itself differs per section_type,
    # so the section key comes from filters.errata_id)
    by_section = {}
    for c in chunks:
        if c["filters"]["section_type"] in ("group", "document_summary"):
            continue
        by_section.setdefault(c["filters"]["errata_id"], []).append(c)
    check(len(by_section) == doc["total_errata"], problems,
          f"{doc_id}: {len(by_section)} distinct sections != {doc['total_errata']} errata")

    for base, group in by_section.items():
        types = {c["filters"]["section_type"] for c in group}
        check(types == SECTION_TYPES, problems,
              f"{doc_id}: section {base} has types {types}")

        sec_id = group[0]["filters"]["errata_id"]
        parent = next((c for c in group if c["filters"]["section_type"] == "full_entry"), None)
        check(parent is not None, problems, f"{doc_id}: {sec_id} missing full_entry")
        if parent:
            check(parent["parent_document_id"] is None, problems,
                  f"{doc_id}: {sec_id} full_entry has parent_document_id")
            check(parent["document_id"] == sha1(f"{doc_id}:{sec_id}:full_entry"), problems,
                  f"{doc_id}: {sec_id} full_entry document_id mismatch")
            for child in group:
                if child is parent:
                    continue
                check(child["parent_document_id"] == parent["document_id"], problems,
                      f"{doc_id}: {sec_id} {child['filters']['section_type']} bad parent link")
                check(child["document_id"] == sha1(f"{doc_id}:{sec_id}:{child['filters']['section_type']}"),
                      problems, f"{doc_id}: {sec_id} {child['filters']['section_type']} document_id mismatch")

        # filters consistency
        flt = group[0]["filters"]
        revs = list(flt["status_by_revision"].keys())
        statuses = flt["status_by_revision"]
        check(all(v in VALID_STATUS for v in statuses.values()), problems,
              f"{doc_id}: {sec_id} invalid status values {statuses}")
        expected_affected = [r for r in revs if statuses[r] != "-"]
        expected_fixed = [r for r in revs if statuses[r] == "-"]
        check(flt["affected_revisions"] == expected_affected, problems,
              f"{doc_id}: {sec_id} affected_revisions {flt['affected_revisions']} != {expected_affected}")
        check(flt["fixed_in_revision"] == expected_fixed, problems,
              f"{doc_id}: {sec_id} fixed_in_revision {flt['fixed_in_revision']} != {expected_fixed}")
        check(flt["has_workaround"] == any(statuses[r] == "A" for r in revs), problems,
              f"{doc_id}: {sec_id} has_workaround mismatch")
        check(flt["partial_workaround_only"] == any(statuses[r] == "P" for r in revs), problems,
              f"{doc_id}: {sec_id} partial_workaround_only mismatch")

        # enrichment fields (schema v2, deterministic rules)
        check(flt.get("severity") == ex.derive_severity(statuses, revs), problems,
              f"{doc_id}: {sec_id} severity mismatch")
        check(flt.get("severity") in {"high", "medium", "unknown"}, problems,
              f"{doc_id}: {sec_id} invalid severity {flt.get('severity')!r}")
        is_doc = flt.get("is_documentation_errata", False)
        check(isinstance(is_doc, bool), problems,
              f"{doc_id}: {sec_id} is_documentation_errata must be a bool")
        if is_doc:
            check(flt["status_by_revision"] == {} and flt["affected_revisions"] == []
                  and flt["fixed_in_revision"] == [] and not flt["has_workaround"]
                  and not flt["partial_workaround_only"] and flt["severity"] == "unknown",
                  problems, f"{doc_id}: {sec_id} documentation errata must have an empty "
                            f"status matrix (status_by_revision/affected/fixed/severity)")
        conds = flt.get("conditions")
        check(isinstance(conds, list)
              and all(isinstance(c, str) and c.strip() for c in conds),
              problems, f"{doc_id}: {sec_id} conditions malformed")
        impact = flt.get("impact_category")
        check(isinstance(impact, list)
              and all(name in {n for n, _ in ex.IMPACT_LEXICON} for name in impact),
              problems, f"{doc_id}: {sec_id} unknown impact category {impact}")
        kws = flt.get("keywords")
        check(kws and all(isinstance(k, str) and k for k in kws), problems,
              f"{doc_id}: {sec_id} keywords malformed")
        aliases = flt.get("aliases")
        check(aliases and sec_id in aliases, problems,
              f"{doc_id}: {sec_id} aliases missing the section id")
        for mkey in ex.MENTIONS_FLAGS:
            check(isinstance(flt.get(mkey), bool), problems,
                  f"{doc_id}: {sec_id} missing boolean flag {mkey}")
        # keywords/conditions must be traceable to the entry text
        fe_raw = (parent or {}).get("raw_text", "").lower()
        for k in kws:
            check(k in fe_raw, problems,
                  f"{doc_id}: {sec_id} keyword {k!r} not in full_entry text")
        for c in conds:
            check(c.lower() in fe_raw, problems,
                  f"{doc_id}: {sec_id} condition {c!r} not in full_entry text")

        # citation consistency
        cit = group[0]["citation"]
        check(cit["doc_id"] == doc_id, problems, f"{doc_id}: {sec_id} citation doc_id")
        check(cit["doc_version"] == doc["doc_version"], problems, f"{doc_id}: {sec_id} citation doc_version")
        check(bool(cit["doc_date"]) and len(cit["doc_date"]) >= 7, problems,
              f"{doc_id}: {sec_id} citation doc_date")
        check(isinstance(cit["page"], int) and cit["page"] >= 1, problems,
              f"{doc_id}: {sec_id} page {cit['page']}")
        check(cit["url"] == f"{doc['url_pdf']}#page={cit['page']}", problems,
              f"{doc_id}: {sec_id} citation url mismatch")
        check(cit["section_title"].startswith(sec_id), problems,
              f"{doc_id}: {sec_id} section_title mismatch")

        # title: plain errata title, sibling of citation, inherited by the 4 chunks
        titles = {c.get("title") for c in group}
        check(len(titles) == 1, problems,
              f"{doc_id}: {sec_id} title differs across the 4 chunks: {titles}")
        for c in group:
            t = c.get("title")
            check(isinstance(t, str) and t.strip(), problems,
                  f"{doc_id}: {sec_id} {c['filters']['section_type']} missing title")
            check(c["citation"]["section_title"] == f"{sec_id} {t}", problems,
                  f"{doc_id}: {sec_id} {c['filters']['section_type']} title "
                  f"!= suffix of citation.section_title")

        # content non-empty
        for c in group:
            check(c["embed_text"].strip(), problems, f"{doc_id}: {sec_id} empty embed_text")
            check(c["raw_text"].strip(), problems, f"{doc_id}: {sec_id} empty raw_text")
            check(re.search(r"\(t\s*\)", c["raw_text"]) is None
                  and re.search(r"(?<![\w(])SU;DAT", c["raw_text"]) is None,
                  problems, f"{doc_id}: {sec_id} subscript artifact tSU;DAT not normalized")
        check(flt.get("group_id") and flt.get("group_title"), problems,
              f"{doc_id}: {sec_id} missing group_id/group_title")

    # group chunks (one per section 2.x with at least one errata)
    groups = [c for c in chunks if c["filters"]["section_type"] == "group"]
    check(doc.get("total_groups") == len(groups), problems,
          f"{doc_id}: total_groups {doc.get('total_groups')} != {len(groups)} group chunks")
    errata_group_ids = {c["filters"]["group_id"] for group in by_section.values()
                        for c in group if c["filters"].get("group_id")}
    check(len(errata_group_ids) == len(groups), problems,
          f"{doc_id}: {len(errata_group_ids)} distinct group_id in errata != {len(groups)} group chunks")
    for g in groups:
        gid = g["filters"]["group_id"]
        check(g["document_id"] == sha1(f"{doc_id}:{gid}:group"), problems,
              f"{doc_id}: group {gid} document_id mismatch")
        check(g["parent_document_id"] is None, problems, f"{doc_id}: group {gid} has parent_document_id")
        check(g["filters"]["peripheral"], problems, f"{doc_id}: group {gid} missing peripheral")
        errata_ids = g["filters"]["errata_ids"]
        check(len(errata_ids) == len(set(errata_ids)), problems,
              f"{doc_id}: group {gid} duplicate errata_ids")
        check(all(eid in by_section for eid in errata_ids), problems,
              f"{doc_id}: group {gid} references unknown errata")
        for eid in errata_ids:
            for c in by_section[eid]:
                check(c["filters"]["group_id"] == gid, problems,
                      f"{doc_id}: errata {eid} group mismatch ({c['filters']['group_id']} != {gid})")
                check(c["filters"]["peripheral"] == g["filters"]["peripheral"], problems,
                      f"{doc_id}: errata {eid} peripheral {c['filters']['peripheral']!r} "
                      f"!= group {gid} peripheral {g['filters']['peripheral']!r}")
        gcit = g["citation"]
        check(gcit["doc_id"] == doc_id and gcit["doc_version"] == doc["doc_version"], problems,
              f"{doc_id}: group {gid} citation doc mismatch")
        check(bool(gcit["doc_date"]) and len(gcit["doc_date"]) >= 7, problems,
              f"{doc_id}: group {gid} citation doc_date")
        check(isinstance(gcit["page"], int) and gcit["page"] >= 1, problems,
              f"{doc_id}: group {gid} page {gcit['page']}")
        check(gcit["url"] == f"{doc['url_pdf']}#page={gcit['page']}", problems,
              f"{doc_id}: group {gid} citation url mismatch")
        check(gcit["section_title"].startswith(gid), problems,
              f"{doc_id}: group {gid} section_title mismatch")
        check(g["embed_text"].strip() and g["raw_text"].strip(), problems,
              f"{doc_id}: group {gid} empty content")

    # document_summary chunk (meta + group list, page 1)
    doc_chunks = [c for c in chunks if c["filters"]["section_type"] == "document_summary"]
    check(len(doc_chunks) == 1, problems, f"{doc_id}: expected 1 document_summary chunk")
    for dc in doc_chunks:
        check(dc["document_id"] == sha1(f"{doc_id}:document:document_summary"), problems,
              f"{doc_id}: document_summary document_id mismatch")
        check(dc["parent_document_id"] is None, problems, f"{doc_id}: document_summary has parent_document_id")
        check(dc["citation"]["page"] == 1, problems, f"{doc_id}: document_summary page != 1")
        check(dc["citation"]["url"] == f"{doc['url_pdf']}#page=1", problems,
              f"{doc_id}: document_summary url mismatch")
        check(dc["embed_text"].strip() and dc["raw_text"].strip(), problems,
              f"{doc_id}: document_summary empty content")
        for token in (doc_id, doc["family"], doc["reference_manual"]):
            check(token and token in dc["raw_text"], problems,
                  f"{doc_id}: document_summary missing {token!r}")

    # wrapper-level
    check(all(c["doc_id"] == doc_id for c in chunks), problems, f"{doc_id}: chunk doc_id mismatch")
    check(doc["family"] and doc["reference_manual"], problems, f"{doc_id}: missing family/rm")
    check(doc.get("extractor_version"), problems, f"{doc_id}: missing extractor_version")
    check(bool(doc.get("url_pdf"))
          and doc["url_pdf"].startswith("https://www.st.com/resource/en/")
          and doc["url_pdf"].endswith(".pdf"),
          problems, f"{doc_id}: invalid url_pdf {doc.get('url_pdf')!r}")
    check(doc["url_pdf"].rsplit("/", 1)[-1][:-4].startswith(doc_id.lower()),
          problems, f"{doc_id}: url_pdf filename does not match doc_id")
    check(len([c for c in chunks
               if c["filters"]["section_type"] not in ("group", "document_summary")])
          == 4 * doc["total_errata"], problems, f"{doc_id}: total_errata mismatch")

    # Phase 4 hard tests: exact retrieval returns ONLY the asked errata, with
    # the full entry in the context (no unrelated chunks, no vector dependency)
    idx = rag_utils.RAGIndex(doc)
    eids = sorted(idx.sections())
    if eids:
        e1, e2 = eids[0], (eids[1] if len(eids) > 1 else eids[0])
        for q in (e1, f"errata {e1}", f"{doc_id} {e1}"):
            res = rag_utils.search_multi(idx, q, top_errata=10)
            check(res["errata"] == [e1], problems,
                  f"{doc_id}: exact retrieval {q!r} -> {res['errata']} (expected [{e1}])")
            check(any(c["filters"]["section_type"] == "full_entry"
                      and c["filters"]["errata_id"] == e1 for c in res["context"]),
                  problems, f"{doc_id}: exact retrieval {q!r} misses full_entry")
            check(all(c["filters"].get("errata_id") in (e1,) for c in res["context"]
                      if c["filters"]["section_type"] not in ("group", "document_summary")),
                  problems, f"{doc_id}: exact retrieval {q!r} leaks other errata")
        if len(eids) > 1:
            res = rag_utils.search_multi(idx, f"{e1} and {e2}", top_errata=10)
            check(res["errata"] == [e1, e2], problems,
                  f"{doc_id}: exact retrieval two ids -> {res['errata']} "
                  f"(expected [{e1}, {e2}])")
    return problems


def validate_reproducibility(pdf_path: Path, doc_json_path: Path, problems: list):
    r = ex.process_pdf(pdf_path)
    with open(doc_json_path, encoding="utf-8") as f:
        existing = json.load(f)
    fresh = r["out"]
    if fresh != existing:
        problems.append(f"{pdf_path.stem}: re-extraction differs from saved JSON")
        # help debugging: show first differing key path
        for k in ("doc_id", "doc_version", "family", "reference_manual",
                  "total_errata", "total_groups", "total_chunks"):
            if fresh.get(k) != existing.get(k):
                problems.append(f"  diff at wrapper.{k}: {existing.get(k)!r} vs {fresh.get(k)!r}")
        if fresh.get("total_errata") == existing.get("total_errata"):
            for i, (a, b) in enumerate(zip(existing["documents"], fresh["documents"])):
                if a != b:
                    for k in a:
                        if a[k] != b[k]:
                            problems.append(f"  diff chunk {i}.{k}:\n    saved: {a[k]!r}\n    fresh: {b[k]!r}")
                    break


def main() -> int:
    docs = sorted(OUTPUT_DIR.rglob("*_errata_rag.json"))
    if not docs:
        print("No JSON in output/")
        return 1

    problems = []
    for doc_path in docs:
        print(f"== {doc_path.name}")
        problems.extend(validate_document(doc_path))
        with open(doc_path, encoding="utf-8") as f:
            problems.extend(rag_utils.smoke_test(json.load(f)))
        pdf_name = doc_path.stem.split("_")[0]
        pdf_path = ex.find_pdf(pdf_name)
        if pdf_path is None:
            problems.append(f"{pdf_name}: source PDF not found for reproducibility check")
            continue
        validate_reproducibility(pdf_path, doc_path, problems)

    if problems:
        print("\nFAILED:")
        for p in problems:
            print(f"  - {p}")
        return 1
    print("\nAll documents valid and reproducible.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
