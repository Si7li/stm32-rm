"""
rag_utils.py

Consumer-side helpers for the errata RAG JSON files (output/esXXXX_errata_rag.json).

The internal RAG is a black box: it is only expected to embed embed_text and
return top-k chunks. All the structured behaviour of the selective RAG —
filtering by metadata, errata lookup, parent/group expansion, citation — is
implemented here, on the JSON alone, so the guarantees do not depend on the RAG.

Linkage recap (see errata_extractor.py docstring):
  - parent_document_id (sha1): child chunk -> its errata full_entry
  - filters.group_id (string): errata chunks -> group overview chunk
  - document_summary chunk: top-level meta entry point

Usage:
    from rag_utils import RAGIndex
    idx = RAGIndex.load("output/es0568_errata_rag.json")
    idx.lookup_errata("2.8.1")        # -> full_entry chunk
    idx.filter(peripheral="I2C")      # -> matching chunks (pre-embedding filter)
    idx.expand(chunk)                 # -> chunk + full_entry + group chunk
    idx.is_affected("2.8.1", "A")     # -> True/False
    idx.cite(chunk)                   # -> "ES0568 Rev 4 (2023-06), p.4: 2.8.1 Title <url>"
"""

import json
import re
from pathlib import Path

ERRATA_ID_RE = re.compile(r"^\d+\.\d+\.\d+$")
GROUP_ID_RE = re.compile(r"^\d+\.\d+$")
CHILD_TYPES = {"description", "workaround", "applicability"}

# Query analysis patterns (step 18 hybrid retrieval: structured tokens first)
_ERRATA_ID_IN_QUERY_RE = re.compile(r"\b\d+\.\d+\.\d+\b")
_DOC_ID_IN_QUERY_RE = re.compile(r"\bES\d{4}\b", re.IGNORECASE)
_REV_IN_QUERY_RE = re.compile(r"\bRev\.?\s*([A-Z])\b")
_QUERY_STOPWORDS = frozenset("""
a affect affected and are at does errata for has have how in is of on rev revision
revisions silicon the this that to what when where which with workaround
""".split())


class RAGIndex:
    def __init__(self, doc: dict):
        self.doc = doc
        self.chunks = doc["documents"]
        self._by_id = {c["document_id"]: c for c in self.chunks}
        self._sections = {}
        self._groups = {}
        for c in self.chunks:
            st = c["filters"]["section_type"]
            if st == "group":
                self._groups[c["filters"]["group_id"]] = c
            elif st == "document_summary":
                self.document_summary_chunk = c
            else:
                self._sections.setdefault(c["filters"]["errata_id"], []).append(c)

    # ── constructors ────────────────────────────────────────────────────────

    @classmethod
    def load(cls, path: str | Path) -> "RAGIndex":
        with open(path, encoding="utf-8") as f:
            return cls(json.load(f))

    # ── identity ────────────────────────────────────────────────────────────

    @property
    def doc_id(self) -> str:
        return self.doc["doc_id"]

    def __repr__(self) -> str:
        return (f"RAGIndex({self.doc_id} {self.doc['doc_version']}: "
                f"{self.doc['total_errata']} errata, "
                f"{self.doc['total_groups']} groups, {len(self.chunks)} chunks)")

    # ── lookups ─────────────────────────────────────────────────────────────

    def by_id(self, document_id: str) -> dict | None:
        return self._by_id.get(document_id)

    def sections(self) -> dict[str, list[dict]]:
        return self._sections

    def groups(self) -> dict[str, dict]:
        return self._groups

    def document_summary(self) -> dict:
        return self.document_summary_chunk

    def lookup_errata(self, errata_id: str) -> dict | None:
        """Returns the full_entry chunk for errata_id (e.g. "2.8.1")."""
        errata_id = errata_id.strip()
        if not ERRATA_ID_RE.match(errata_id):
            return None
        group = self._sections.get(errata_id)
        if not group:
            return None
        return next(c for c in group if c["filters"]["section_type"] == "full_entry")

    def group_of(self, errata_id: str) -> dict | None:
        """Returns the group overview chunk containing errata_id."""
        fe = self.lookup_errata(errata_id)
        if fe is None:
            return None
        return self._groups.get(fe["filters"]["group_id"])

    # ── filtering (pre-embedding) ───────────────────────────────────────────

    def filter(self, section_type: str | None = None, **filters) -> list[dict]:
        """Returns chunks whose filters match all constraints.

        Scalars are matched by equality; list values by containment
        (e.g. affected_revisions=["A"] keeps chunks whose list contains "A").
        """
        out = []
        for c in self.chunks:
            if section_type is not None and c["filters"]["section_type"] != section_type:
                continue
            if not all(self._matches(c["filters"].get(k), v) for k, v in filters.items()):
                continue
            out.append(c)
        return out

    @staticmethod
    def _matches(actual, expected) -> bool:
        if isinstance(expected, (list, tuple, set)):
            return isinstance(actual, list) and all(e in actual for e in expected)
        if isinstance(expected, str):
            if isinstance(actual, list):
                # substring containment over list values (keywords, aliases...)
                return any(isinstance(a, str) and expected in a for a in actual)
            return actual == expected
        return actual == expected

    # ── expansion (parent / group) ──────────────────────────────────────────

    def expand(self, chunk: dict) -> list[dict]:
        """Returns an ordered, de-duplicated context chain for a chunk:
        itself, its errata siblings, the full_entry parent, the group chunk.
        """
        out = [chunk]
        st = chunk["filters"]["section_type"]
        if st == "group":
            return out
        if st == "document_summary":
            return out
        parent = self._by_id.get(chunk["parent_document_id"]) if chunk.get("parent_document_id") else None
        if parent and parent not in out:
            out.append(parent)
        eid = chunk["filters"].get("errata_id")
        if eid:
            for c in self._sections.get(eid, []):
                if c not in out:
                    out.append(c)
        g = self._groups.get(chunk["filters"].get("group_id"))
        if g and g not in out:
            out.append(g)
        return out

    def expand_errata(self, errata_id: str) -> list[dict]:
        """Returns the 4 errata chunks + its group chunk, ordered."""
        fe = self.lookup_errata(errata_id)
        if fe is None:
            return []
        return self.expand(fe)

    def workarounds(self, **filters) -> list[dict]:
        """Workaround chunks matching filters, expanded with their full_entry."""
        return [self.expand(c)[0] for c in self.filter(section_type="workaround", **filters)]

    def overview(self, group_id: str) -> list[dict]:
        """Group chunk + full_entry chunks of its members."""
        g = self._groups.get(group_id)
        if g is None:
            return []
        members = [self.lookup_errata(eid) for eid in g["filters"]["errata_ids"]]
        return [g] + [m for m in members if m]

    # ── revision status (type D questions) ──────────────────────────────────

    def status_by_revision(self, errata_id: str) -> dict | None:
        fe = self.lookup_errata(errata_id)
        return fe["filters"]["status_by_revision"] if fe else None

    def is_affected(self, errata_id: str, revision: str) -> bool | None:
        statuses = self.status_by_revision(errata_id)
        if statuses is None or revision not in statuses:
            return None
        return statuses[revision] != "-"

    def affected_revisions(self, errata_id: str) -> list[str] | None:
        fe = self.lookup_errata(errata_id)
        return fe["filters"]["affected_revisions"] if fe else None

    def fixed_revisions(self, errata_id: str) -> list[str] | None:
        fe = self.lookup_errata(errata_id)
        return fe["filters"]["fixed_in_revision"] if fe else None

    # ── citation ────────────────────────────────────────────────────────────

    def cite(self, chunk: dict) -> str:
        cit = chunk["citation"]
        return (f"{cit['doc_id']} {cit['doc_version']} ({cit['doc_date']}), p.{cit['page']}: "
                f"{cit['section_title']} <{cit['url']}>")


# ── hybrid retrieval (step 18: structured path before any embedding) ────────

_SECTION_TYPE_ORDER = {"full_entry": 0, "description": 1, "workaround": 2,
                       "applicability": 3}


def _numeric_key(text: str | None) -> tuple:
    parts = re.findall(r"\d+", text or "")
    return tuple(int(p) for p in parts[:3])


def _document_key(idx: "RAGIndex", chunk: dict) -> tuple:
    """Deterministic document-order key for a chunk (presentation order)."""
    st = chunk["filters"]["section_type"]
    if st == "document_summary":
        return (-1,)
    if st == "group":
        return _numeric_key(chunk["filters"]["group_id"]) + (0,)
    return _numeric_key(chunk["filters"]["errata_id"]) + (_SECTION_TYPE_ORDER[st],)

def analyze_query(query: str, idx: RAGIndex | None = None) -> dict:
    """Structured analysis of a user query.

    Returns {"errata_ids": [...], "doc_ids": [...], "revisions": [...],
    "peripherals": [...], "keywords": [...]}. The RAG black box only does
    vector top-k on embed_text; this plan drives the deterministic
    filter/lookup path first, so exact-id and metadata questions never need
    an embedding.
    """
    plan = {
        "errata_ids": sorted({m for m in _ERRATA_ID_IN_QUERY_RE.findall(query)}),
        "doc_ids": sorted({m.lower() for m in _DOC_ID_IN_QUERY_RE.findall(query)}),
        "revisions": sorted({m for m in _REV_IN_QUERY_RE.findall(query)}),
        "peripherals": [],
        "keywords": [],
    }
    ql = query.lower()
    if idx is not None:
        known = sorted({(g["filters"]["peripheral"] or "").lower()
                        for g in idx.groups().values()})
        for name in known:
            if name and name in ql:
                plan["peripherals"].append(name)
    leftover = ql
    for m in _ERRATA_ID_IN_QUERY_RE.findall(query):
        leftover = leftover.replace(m, " ")
    for m in _DOC_ID_IN_QUERY_RE.findall(query):
        leftover = leftover.replace(m.lower(), " ")
    for m in _REV_IN_QUERY_RE.findall(query):
        leftover = leftover.replace(m, " ")
    tokens = [t for t in re.findall(r"[a-z]{4,}", leftover)
              if t not in _QUERY_STOPWORDS and t not in plan["peripherals"]]
    plan["keywords"] = tokens[:5]
    return plan


def search(idx: RAGIndex, query: str, top_k: int = 5) -> list[dict]:
    """Hybrid retrieval entry point (deterministic path, backward compatible).

    Returns the final LLM context chain (expanded, de-duplicated, document
    order). Delegates to the full Phase 4 pipeline (search_multi).
    """
    return search_multi(idx, query, top_errata=top_k)["context"]


# ── Phase 4 pipeline: intent -> filters -> group -> score -> order -> expand ─

_WORKAROUND_TERMS = ("workaround", "work-around", "fix", "mitigation",
                     "solution", "solve")
_REVISION_TERMS = ("affected", "fixed", "applicab", "revision")


def classify_intent(query: str, idx: RAGIndex | None = None) -> dict:
    """Detects the question intent (Phase 4 step 1), priority order:
    exact (errata ids in query) > workaround > revision > category > general."""
    plan = analyze_query(query, idx)
    ql = query.lower()
    has_workaround = any(t in ql for t in _WORKAROUND_TERMS)
    has_revision = bool(plan["revisions"]) or any(t in ql for t in _REVISION_TERMS)
    if plan["errata_ids"]:
        itype = "exact"
    elif has_workaround:
        itype = "workaround"
    elif has_revision:
        itype = "revision"
    elif plan["peripherals"]:
        itype = "category"
    else:
        itype = "general"
    return {
        "type": itype,
        "errata_ids": plan["errata_ids"],
        "revision": plan["revisions"][0] if plan["revisions"] else None,
        "peripheral": plan["peripherals"][0] if plan["peripherals"] else None,
        "keywords": plan["keywords"],
        "plan": plan,
    }


def group_by_errata(chunks: list[dict]) -> dict[str, list[dict]]:
    """Groups chunks by errata_id (group/document_summary chunks excluded).
    Within a group: canonical chunk order (full_entry, description,
    workaround, applicability). Deterministic."""
    groups: dict[str, list[dict]] = {}
    for c in chunks:
        st = c["filters"]["section_type"]
        if st in ("group", "document_summary"):
            continue
        groups.setdefault(c["filters"]["errata_id"], []).append(c)
    for eid in groups:
        groups[eid].sort(key=lambda c: _SECTION_TYPE_ORDER[c["filters"]["section_type"]])
    return groups


def search_multi(idx: RAGIndex, query: str, top_k: int = 20,
                 top_errata: int = 10) -> dict:
    """Phase 4 retrieval pipeline (deterministic; the RAG only does the final
    vector re-ranking as a black box):

    1. classify_intent
    2. exact errata ids -> lookup + full expansion (no embedding needed)
    3. candidate space: doc / peripheral / revision filters
    4. keyword scoring (aliases + keywords + embed_text, substring)
    5. group_by_errata, errata_score = best rank of its chunks (lower = better)
    6. selection: top_errata by score, weak elimination (no keyword match)
    7. presentation order: document order (never the score order)
    8. expansion: full chain per selected errata (4 chunks + group chunk)
    9. final context for the LLM (de-duplicated)

    Returns {"intent", "errata" (document order), "context"}.
    """
    intent = classify_intent(query, idx)
    if intent["type"] == "exact":
        context, ids, seen = [], [], set()
        for eid in intent["errata_ids"]:
            fe = idx.lookup_errata(eid)
            if fe is None:
                continue
            ids.append(eid)
            for c in idx.expand(fe):
                if c["document_id"] not in seen:
                    seen.add(c["document_id"])
                    context.append(c)
        return {"intent": intent, "errata": ids, "context": context}

    cands = [c for c in idx.chunks
             if c["filters"]["section_type"] not in ("group", "document_summary")]
    if intent["plan"]["doc_ids"]:
        cands = [c for c in cands
                 if c["doc_id"].lower() in intent["plan"]["doc_ids"]]
    if intent["peripheral"]:
        cands = [c for c in cands
                 if (c["filters"].get("peripheral") or "").lower() == intent["peripheral"]]
    if intent["revision"]:
        cands = [c for c in cands
                 if intent["revision"] in c["filters"].get("affected_revisions", [])]
    kws = intent["keywords"]

    def kw_score(c: dict) -> int:
        text = c.get("title", "").lower() + " " \
             + " ".join(c["filters"].get("aliases", [])) + " " \
             + " ".join(c["filters"].get("keywords", [])) + " " + c["embed_text"].lower()
        return sum(1 for kw in kws if kw in text)

    if kws:
        cands = [c for c in cands if kw_score(c) > 0]
        cands = sorted(cands, key=lambda c: (-kw_score(c), _document_key(idx, c)))
    else:
        cands = sorted(cands, key=lambda c: _document_key(idx, c))
    cands = cands[:top_k]

    errata_score: dict[str, int] = {}
    for i, c in enumerate(cands):
        eid = c["filters"]["errata_id"]
        if eid not in errata_score:
            errata_score[eid] = i
    selected = sorted(errata_score, key=lambda e: (errata_score[e],
                                                   _numeric_key(e)))[:top_errata]
    selected.sort(key=_numeric_key)

    context, seen = [], set()
    for eid in selected:
        for c in idx.expand_errata(eid):
            if c["document_id"] not in seen:
                seen.add(c["document_id"])
                context.append(c)
    return {"intent": intent, "errata": selected, "context": context}


def coverage_tree(idx: RAGIndex, errata_ids: list[str] | None = None) -> list[dict]:
    """Phase 2 audit view: per errata, which section_type chunks exist and
    whether the group chunk is reachable. 'complete' = all 4 chunks present
    (the generic rule; a real extraction may legitimately have 3/4 when a
    section exists without Description or Workaround in the PDF)."""
    eids = errata_ids or sorted(idx.sections())
    rows = []
    for eid in eids:
        chunks = idx.sections().get(eid, [])
        types = sorted({c["filters"]["section_type"] for c in chunks},
                       key=_SECTION_TYPE_ORDER.get)
        rows.append({
            "errata_id": eid,
            "chunks": types,
            "count": len(chunks),
            "group": idx.group_of(eid) is not None,
            "complete": len(chunks) == 4,
        })
    return rows


def smoke_test(doc: dict) -> list[str]:
    """Consumer-level checks: every errata must be reachable through the
    helpers and expand to a coherent full_entry + group chain."""
    problems = []
    idx = RAGIndex(doc)
    for eid in idx.sections():
        fe = idx.lookup_errata(eid)
        if fe is None:
            problems.append(f"{idx.doc_id}: lookup_errata({eid}) returned None")
            continue
        chain = idx.expand(fe)
        types = [c["filters"]["section_type"] for c in chain]
        if "full_entry" not in types or "group" not in types:
            problems.append(f"{idx.doc_id}: {eid} expand misses parent/group: {types}")
        if idx.group_of(eid) is None:
            problems.append(f"{idx.doc_id}: {eid} group_of None")
    for gid, g in idx.groups().items():
        if any(idx.lookup_errata(e) is None for e in g["filters"]["errata_ids"]):
            problems.append(f"{idx.doc_id}: group {gid} has unreachable errata")
    ds = idx.document_summary()
    if ds is None or ds["citation"]["page"] != 1:
        problems.append(f"{idx.doc_id}: document_summary missing or wrong page")
    first = sorted(idx.sections())[0]
    plan = analyze_query(f"{idx.doc_id} {first}")
    if plan["errata_ids"] != [first]:
        problems.append(f"{idx.doc_id}: analyze_query failed on {first}: {plan}")
    if not search(idx, f"{idx.doc_id} {first} workaround"):
        problems.append(f"{idx.doc_id}: search returned nothing")

    # Phase 4 smoke: intent detection + exact retrieval + group/document order
    intent = classify_intent(f"{idx.doc_id} {first} workaround", idx)
    if intent["type"] != "exact":
        problems.append(f"{idx.doc_id}: classify_intent expected exact, got {intent['type']}")
    res = search_multi(idx, f"errata {first}", top_errata=1)
    if res["errata"] != [first]:
        problems.append(f"{idx.doc_id}: search_multi exact id failed: {res['errata']}")
    if not any(c["filters"]["section_type"] == "full_entry" for c in res["context"]):
        problems.append(f"{idx.doc_id}: search_multi context misses full_entry")
    int_w = classify_intent("which revision is affected", idx)
    if int_w["type"] != "revision":
        problems.append(f"{idx.doc_id}: classify_intent expected revision, "
                        f"got {int_w['type']}")
    int_g = classify_intent("what does this errata do", idx)
    if int_g["type"] != "general":
        problems.append(f"{idx.doc_id}: classify_intent expected general, "
                        f"got {int_g['type']}")
    tree = coverage_tree(idx, [first])
    if not tree or not tree[0]["complete"] or not tree[0]["group"]:
        problems.append(f"{idx.doc_id}: coverage_tree incomplete for {first}: {tree}")
    return problems


def main() -> int:
    from rmerrata import extractor as ex
    output_dir = ex.OUTPUT_DIR
    problems = []
    for path in sorted(output_dir.rglob("*_errata_rag.json")):
        idx = RAGIndex.load(path)
        print(idx)
        print(f"  document: {idx.document_summary()['raw_text'].splitlines()[0]}")
        eid = sorted(idx.sections())[0]
        fe = idx.lookup_errata(eid)
        print(f"  lookup_errata({eid}) -> {fe['filters']['section_type']}: {fe['citation']['section_title']}")
        print(f"  expand({eid}) -> {[c['filters']['section_type'] for c in idx.expand_errata(eid)]}")
        print(f"  is_affected({eid}, first rev) -> "
              f"{idx.is_affected(eid, next(iter(fe['filters']['status_by_revision'])))}")
        g = idx.group_of(eid)
        print(f"  group_of({eid}) -> {g['filters']['group_id']} {g['filters']['peripheral']}")
        print(f"  cite -> {idx.cite(fe)}")
        plan = analyze_query(f"{idx.doc_id} {eid} workaround")
        print(f"  analyze_query -> {plan}")
        print(f"  search -> {[c['filters']['section_type'] for c in search(idx, f'{idx.doc_id} {eid} workaround')]}")
        res = search_multi(idx, f"{idx.doc_id} {eid} workaround", top_errata=3)
        print(f"  search_multi -> intent={res['intent']['type']} "
              f"errata={res['errata']} chunks={len(res['context'])}")
        cov = coverage_tree(idx, [eid])[0]
        print(f"  coverage -> {cov['chunks']} complete={cov['complete']} "
              f"group={cov['group']}")
        problems.extend(smoke_test(idx.doc))
    if problems:
        print("\nFAILED:")
        for p in problems:
            print(f"  - {p}")
        return 1
    print("\nSmoke test OK on all documents.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
