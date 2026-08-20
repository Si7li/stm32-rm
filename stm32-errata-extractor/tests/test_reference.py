"""Tests against the frozen legacy-schema reference. These are offline:
they never open a PDF, so they cannot see the source documents — the
agreement-with-reality checks live in the pipeline ('rmerrata validate' and
'rmerrata regression'), which re-extract the PDFs."""

import json
import hashlib

import pytest

from rmerrata import rag_utils, regression

REF = regression.REF_ES0676


def sha1(s: str) -> str:
    return hashlib.sha1(s.encode("utf-8")).hexdigest()


@pytest.fixture(scope="module")
def ref_doc():
    with open(REF, encoding="utf-8") as f:
        return json.load(f)


@pytest.fixture(scope="module")
def ref_idx():
    return rag_utils.RAGIndex.load(REF)


def test_reference_exists():
    assert REF.exists(), f"missing reference fixture {REF}"


def test_canonical_count(ref_doc):
    assert ref_doc["total_chunks"] == 4 * ref_doc["total_errata"]


def test_reference_passes_legacy_check():
    problems = []
    regression.check_es0676_reference(problems)
    assert problems == []


def test_four_chunks_per_errata(ref_idx):
    sections = ref_idx.sections()
    assert len(sections) == ref_idx.doc["total_errata"]
    for eid, chunks in sections.items():
        assert len(chunks) == 4
        types = {c["filters"]["section_type"] for c in chunks}
        assert types == {"full_entry", "description", "workaround", "applicability"}


def test_deterministic_ids(ref_idx):
    doc_id = ref_idx.doc_id
    for chunk in ref_idx.chunks:
        expected = sha1(f"{doc_id}:{chunk['filters']['errata_id']}:"
                        f"{chunk['filters']['section_type']}")
        assert chunk["document_id"] == expected


def test_parent_linkage(ref_idx):
    for chunk in ref_idx.chunks:
        if chunk["filters"]["section_type"] == "full_entry":
            assert chunk["parent_document_id"] is None
        else:
            parent = chunk["parent_document_id"]
            assert parent is not None
            assert parent in ref_idx._by_id  # no dangling parents


def test_lookup_and_expand(ref_idx):
    eid = sorted(ref_idx.sections())[0]
    fe = ref_idx.lookup_errata(eid)
    assert fe is not None
    assert fe["filters"]["section_type"] == "full_entry"
    exp = ref_idx.expand(fe)
    assert len(exp) == 4
    assert exp[0]["filters"]["section_type"] == "full_entry"


def test_status_matrix(ref_idx):
    eid = sorted(ref_idx.sections())[0]
    status = ref_idx.status_by_revision(eid)
    assert set(status.values()) <= {"A", "N", "P", "-"}
    fe = ref_idx.lookup_errata(eid)
    assert set(status.keys()) == set(fe["filters"]["status_by_revision"])


def test_search_pipeline(ref_idx):
    eid = sorted(ref_idx.sections())[0]
    res = rag_utils.search_multi(ref_idx, f"{ref_idx.doc_id} {eid} workaround")
    assert res["intent"]["type"] == "exact"
    assert eid in res["errata"]
    assert len(res["context"]) == 4


def test_analyze_query():
    plan = rag_utils.analyze_query("what is the workaround for es0568 2.4.2?")
    assert plan["doc_ids"] == ["es0568"] or "es0568" in plan["doc_ids"]
    assert "2.4.2" in plan["errata_ids"]
    plan2 = rag_utils.analyze_query("is the bug fixed on Rev A?")
    assert plan2["revisions"] == ["A"]  # revision letters detected (case-sensitive "Rev")