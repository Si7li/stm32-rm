"""
errata_extractor.py

Extracts errata from STM32 errata sheets (ESxxxx PDFs in input/) and produces a
selective-RAG JSON per document, using the same canonical schema as the demo
errata_extractor_es0676.py:

  - 1 "full_entry" chunk per errata (parent)
  - 1 "description" chunk per errata (child)
  - 1 "workaround" chunk per errata (child)
  - 1 "applicability" chunk per errata (child), built from the Table 3 status matrix
  - 1 "group" chunk per section 2.x with >= 1 errata (top-level)
  - 1 "document_summary" chunk (top-level)

Linkage mechanisms (two independent keys, no ambiguity):
  - parent_document_id (sha1 hash): links each child chunk to its errata full_entry.
    document_id = sha1(f"{doc_id}:{section_id}:{section_type}") is deterministic.
  - filters.group_id (string, e.g. "2.8"): links every errata chunk to its
    group overview chunk (filters.section_type == "group").
  - The document_summary chunk is a top-level entry point (parent_document_id = null).

Status letters from Table 3 (Summary of device limitations):
  A = Limitation applicable, workaround available
  N = Limitation applicable, no workaround available
  P = Limitation applicable, partial workaround available
  - = Limitation absent in this silicon revision (=> fixed_in_revision)

Sources of truth:
  - Table 3 pages: errata ids, function (peripheral), limitation title, status matrix,
    and the tracked silicon revisions (revision column headers).
  - Section 2.x.y pages: authoritative title, Description body, Workaround body,
    and the physical page where the entry starts.

Chunk enrichment (schema v2, deterministic rules — no LLM):
  - filters.conditions: bullet lists preceded by trigger phrases (TRIGGER_RE)
  - filters.impact_category: keyword-lexicon categories (IMPACT_LEXICON)
  - filters.keywords / filters.aliases: deterministic token / n-gram extraction
  - filters.severity: derived from the Table 3 matrix (N -> high, P/A -> medium)
  - filters.mentions_*: boolean keyword flags on the entry text
"""

import argparse
import hashlib
import json
import os
import re
import sys
from collections import Counter, deque
from difflib import SequenceMatcher
from pathlib import Path

import pdfplumber

BASE = Path(__file__).resolve().parent
PROJECT_ROOT = BASE.parents[1] if BASE.name == "rmerrata" else BASE
INPUT_DIR = Path(os.environ.get("ERRATA_INPUT_DIR", PROJECT_ROOT.parent / "Erratasheet"))
OUTPUT_DIR = Path(os.environ.get("ERRATA_OUTPUT_DIR", PROJECT_ROOT / "output"))


def list_pdfs(directory: Path = INPUT_DIR) -> list[Path]:
    """All PDFs under directory, recursively (subfolders are archives, kept as-is)."""
    return sorted(directory.rglob("*.pdf"))


def find_pdf(doc_id: str, directory: Path = INPUT_DIR) -> Path | None:
    """Locate the source PDF for a doc id (ex. 'es0676') anywhere under input/."""
    for p in list_pdfs(directory):
        if p.stem.lower().startswith(doc_id.lower()):
            return p
    return None

# ── Patterns ────────────────────────────────────────────────────────────────

SECTION_RE = re.compile(r"^(\d+\.\d+\.\d+)\s+(.+)$")
FUNCTION_RE = re.compile(r"^(\d+\.\d+)\s+([A-Z][A-Za-z0-9/_-]*\s*[A-Za-z0-9/_-]*(?:\s[A-Za-z0-9/_-]+)*)$")
FOOTER_RE = re.compile(r"(ES\d+)\s*-\s*Rev\s+(\d+)\s*-\s*([A-Za-z]+\s+\d{4})", re.MULTILINE)
PAGE_FOOTER_RE = re.compile(r"^ES\d+\s*-\s*Rev\s+\d+\s+page\s+\d+/\d+\s*$")
RM_RE = re.compile(r"\bRM\d{4}\b")
SECTION_CELL_RE = re.compile(r"\s*(\d+\.\d+\.\d+)\b")
STATUS_RE = re.compile(r"[ANP-]")

FAMILY_HINT_RE = re.compile(r"^STM32")

STOP_MARKERS = ("Important security notice", "IMPORTANT NOTICE", "Revision history", "Contents")

MONTHS = {
    "January": "01", "February": "02", "March": "03", "April": "04",
    "May": "05", "June": "06", "July": "07", "August": "08",
    "September": "09", "October": "10", "November": "11", "December": "12",
}

STATUS_MEANING = {
    "A": "Limitation applicable, workaround available",
    "N": "Limitation applicable, no workaround available",
    "P": "Limitation applicable, partial workaround available",
    "-": "Limitation absent",
}

# ── Layout evidence (font sizes / bold, used to confirm regex headings) ──────

BOLD_FONT_TAGS = ("Bold", "Bd", "Heavy", "Black")

# Subheadings recognized inside a section 2.x.y, beyond Description/Workaround.
SUBHEADINGS = ("Description", "Workaround", "Limitation", "Conditions")

# ── Bullet blocks (conditions extraction) ────────────────────────────────────

BULLET_CHARS = ("\u2022", "\u2023", "\u25cf", "\u25aa")

# A bullet block whose preceding lines match one of these phrases is extracted
# as filters.conditions; any other bullet list stays inline (faithful to PDF).
TRIGGER_RE = re.compile(
    r"(occurs when|the failure occurs|this occurs|triggered when|fails when|"
    r"operat\w+ in|one of the following|in the following|as follows|"
    r"setup time|when the following)", re.IGNORECASE)

# ── Computed enrichment (deterministic rules, no LLM) ────────────────────────

EXTRACTOR_VERSION = "2.0.0"

STOPWORDS = frozenset("""
a about after all also an and any are as at be been before between but by can case
during each for from has have having its may more most must not of on only or other
our over shall should so some such than that the their them then there these they
this those through to under up upon us use used using was we were when where which
while will with would
bit bits data device devices errata errata1 figure flag flags given limitation
limitations means mode note notes occurs output page register registers reset
section signal state status table time value values workaround workarounds
""".split())

# Ordered impact lexicon: all matching categories are returned, in this order.
IMPACT_LEXICON = [
    ("power", re.compile(r"\b(power|vdd|vdda|supply|voltage|lvd|brown[- ]?out|"
                          r"undervoltage|power[- ]?down|power[- ]?up)\b", re.I)),
    ("clock", re.compile(r"\b(clock|clk|hsi|hse|lsi|lse|pll|msi|hsidiv|oscillator|"
                          r"crystal|kernel clock|timer clock|rtcclk)\b", re.I)),
    ("reset", re.compile(r"\b(reset|boot|bootstrap)\b", re.I)),
    ("dma", re.compile(r"\b(dma|dmamux)\b", re.I)),
    ("interrupt", re.compile(r"\b(interrupt|irq|nmi|exception|wake[- ]?up event|"
                              r"wakeup)\b", re.I)),
    ("timer", re.compile(r"\b(tim[0-9x]|pwm|compare|capture|break signal|bkin|"
                          r"dead[- ]?time|update event|counter)\b", re.I)),
    ("adc", re.compile(r"\b(adc|analog[- ]?to[- ]?digital|conversion|awd)\b", re.I)),
    ("usart", re.compile(r"\b(usart|uart|lin|irda)\b", re.I)),
    ("i2c", re.compile(r"\b(i2c|smbus|sda|scl|i2cclk)\b", re.I)),
    ("spi", re.compile(r"\bspi\b", re.I)),
    ("rtc", re.compile(r"\b(rtc|calendar|alarm|tamper)\b", re.I)),
    ("flash", re.compile(r"\b(flash|eeprom|program|erase|bank)\b", re.I)),
    ("usb", re.compile(r"\busb\b", re.I)),
    ("debug", re.compile(r"\b(debug|swd|jtag|trace)\b", re.I)),
    ("data_corruption", re.compile(r"\b(corrupt|data loss|misread|wrong data|lost)\b",
                                   re.I)),
]

# Boolean flags on the entry text (title + description + workaround + limitation).
MENTIONS_FLAGS = {
    "mentions_workaround": re.compile(r"\bworkaround\b", re.I),
    "mentions_condition": re.compile(r"\bcondition\b", re.I),
    "mentions_note": re.compile(r"\bnote\b", re.I),
    "mentions_register": re.compile(r"\bregisters?\b", re.I),
    "mentions_clock": re.compile(r"\bclock\b", re.I),
    "mentions_dma": re.compile(r"\bdma\b", re.I),
    "mentions_interrupt": re.compile(r"\binterrupt\b", re.I),
    "mentions_power": re.compile(r"\bpower\b", re.I),
    "mentions_reset": re.compile(r"\breset\b", re.I),
}

# pdfplumber extracts subscript glyph runs (e.g. tSU;DAT) out of order:
#   "(t ) is shorter than one I2C kernel clock period SU;DAT"   -> "(tSU;DAT) is shorter ..."
#   "when t is smaller than one I2C kernel clock SU;DAT (I2C-bus ..." -> "when tSU;DAT is smaller ..."
_SUBSCRIPT_RE = re.compile(r"\(t\s*\)")
_STRAY_SUB_RUN_RE = re.compile(r"(?<!\(t)\bSU;DAT\b")


def fix_tsu_dat(s: str) -> str:
    if "SU;DAT" not in s:
        return s
    if _SUBSCRIPT_RE.search(s):
        s = s.replace("SU;DAT", "", 1)
        s = _SUBSCRIPT_RE.sub("(tSU;DAT)", s, count=1)
    m = _STRAY_SUB_RUN_RE.search(s)
    if m:
        tm = None
        for t in re.finditer(r"\bt\b", s[:m.start()]):
            tm = t
        if tm is not None:
            s = s[:tm.end()] + m.group(0) + s[tm.end():m.start()] + s[m.end():]
    return re.sub(r"\s+", " ", s).strip()


def sha1(s: str) -> str:
    return hashlib.sha1(s.encode("utf-8")).hexdigest()


def norm(s: str) -> str:
    """Collapse whitespace/newlines in a cell or line into single spaces."""
    return " ".join((s or "").split())


def page_text(pdf, page_idx: int) -> str:
    return pdf.pages[page_idx].extract_text() or ""


# ── Layout layer (font evidence) ─────────────────────────────────────────────

def page_records(pdf, page_idx: int) -> list[dict]:
    """Line-level layout evidence for one page: {text, size, bold}.

    Extracted from pdfplumber's layout-aware text lines (font sizes + font
    names). This is *evidence only*: it never replaces extract_text() as the
    extraction source (extract_text keeps subscript runs like "SU;DAT"
    attachable to their phrase, extract_text_lines splits them apart).
    """
    records = []
    for ln in pdf.pages[page_idx].extract_text_lines(extra_attrs=["fontname", "size"]) or []:
        text = norm(ln.get("text", ""))
        if not text:
            continue
        chars = ln.get("chars") or []
        sizes = [c.get("size") for c in chars if c.get("size")]
        fonts = [c.get("fontname") for c in chars if c.get("fontname")]
        records.append({
            "text": text,
            "size": round(max(sizes), 1) if sizes else None,
            "bold": any(tag in f for f in fonts for tag in BOLD_FONT_TAGS),
        })
    return records


def layout_evidence(pdf, detail_pages: list[int]) -> dict:
    """Per-document evidence map: norm(text) -> {size, bold} + body_size.

    body_size = dominant font size on the detail pages (the paragraph body).
    On the ST template: 2.x group headings are larger+bold, Description/
    Workaround subheadings are bold, 2.x.y section headings are smaller.
    """
    ev = {}
    sizes = Counter()
    for pno in detail_pages:
        for rec in page_records(pdf, pno):
            ev.setdefault(rec["text"], rec)
            if rec["size"]:
                sizes[rec["size"]] += 1
    body = sizes.most_common(1)[0][0] if sizes else None
    return {"lines": ev, "body_size": body}


# ── Metadata ────────────────────────────────────────────────────────────────

def parse_metadata(pdf) -> dict:
    page0 = page_text(pdf, 0)
    m = FOOTER_RE.search(page0)
    if not m:
        for p in range(1, min(len(pdf.pages), 6)):
            m = FOOTER_RE.search(page_text(pdf, p))
            if m:
                break
    if not m:
        raise ValueError("footer ESxxxx - Rev n - Month year not found")

    doc_id = m.group(1)
    revision = f"Rev {m.group(2)}"
    month, year = m.group(3).split()
    doc_date = f"{year}-{MONTHS.get(month, 'XX')}" if month in MONTHS else m.group(3)

    family = next((ln.strip() for ln in page0.splitlines() if FAMILY_HINT_RE.match(ln.strip())), None)
    rm = RM_RE.search(page0)
    reference_manual = rm.group(0) if rm else None

    return {
        "doc_id": doc_id,
        "doc_version": revision,
        "doc_date": doc_date,
        "family": family,
        "reference_manual": reference_manual,
    }


# ── Table 3 (status matrix) ─────────────────────────────────────────────────

def extract_table3(pdf, summary_pages: list[int]) -> list[dict]:
    """Returns rows: {section_id, function, title, status_by_revision: {rev: status}}.

    Handles both table shapes:
      - [Function | Section | Limitation | Rev. A | Rev. Z]   (2 revision columns)
      - [Function | Section | Limitation | Rev. Z]            (1 revision column)
    Header may be split over two rows (first row "Status", second "Rev. A" / "Rev. Z").

    Also handles the separate "Documentation erratum" table
    ([Function | Section | Documentation erratum], no status column): rows get
    status_by_revision = {} and documentation_errata = True.
    """
    revisions: list[str] = []
    rows = []
    last_function = None
    last_doc_function = None

    def _collect_header(table):
        nonlocal revisions
        for hdr_row in table[:2]:
            for cell in hdr_row:
                if not cell:
                    continue
                for m in re.finditer(r"Rev\.?\s*([A-Z])", cell):
                    rev = m.group(1)
                    if rev not in revisions:
                        revisions.append(rev)

    def _parse_row(row, function_ff):
        """Shared row parsing: returns (section_id, function, title) or None."""
        nonlocal last_function, last_doc_function
        sec = None
        sec_extra = ""
        sec_idx = None
        for idx, cell in enumerate(row):
            if not cell:
                continue
            m = SECTION_CELL_RE.match(cell)
            if m:
                sec = m.group(1)
                sec_idx = idx
                sec_extra = cell[m.end():].strip()
                break
        if sec is None:
            return None
        func_raw = row[0]
        if func_raw and norm(func_raw):
            if function_ff is None:
                last_function = norm(func_raw)
            else:
                last_doc_function = norm(func_raw)
        function = (last_doc_function if function_ff is not None else last_function) or ""
        lim_parts = [sec_extra] if sec_extra else []
        if sec_idx is not None and sec_idx + 1 < len(row) and (row[sec_idx + 1] or "").strip():
            lim_parts.append(norm(row[sec_idx + 1]))
        return sec, function, " ".join(p for p in lim_parts if p)

    for pno in summary_pages:
        for table in pdf.pages[pno].extract_tables():
            if not table:
                continue
            flat = " ".join((c or "") for row in table[:2] for c in row)
            if not any(kw in flat for kw in ("Function", "Section")):
                continue
            is_doc = "documentation erratum" in flat.lower()
            if is_doc:
                # Single-row header (Function | Section | Documentation erratum),
                # data starts at row 1; continuations may repeat the header.
                for row in table[1:]:
                    if not row or not any(row):
                        continue
                    if any((c or "").strip() in ("Function", "Section", "Documentation erratum")
                           for c in row):
                        continue
                    parsed = _parse_row(row, "")
                    if parsed is None:
                        continue
                    sec, function, title = parsed
                    rows.append({
                        "section_id": sec,
                        "function": function,
                        "title": title,
                        "status_by_revision": {},
                        "documentation_errata": True,
                    })
                continue
            if "Limitation" not in flat:
                continue
            _collect_header(table)
            n_rev = len(revisions)
            if n_rev == 0:
                continue

            for row in table[2:]:
                if not row or not any(row):
                    continue
                if any((c or "") in ("Function", "Section", "Limitation") for c in row):
                    continue
                parsed = _parse_row(row, None)
                if parsed is None:
                    continue
                sec, function, title = parsed

                # Statuses: columns after the fixed Function|Section|Limitation prefix
                status_cells = []
                for cell in row[3:3 + n_rev]:
                    if cell is not None:
                        status_cells.append(cell)
                while len(status_cells) < n_rev and status_cells:
                    last = status_cells.pop()
                    status_cells.extend(re.findall(r"[ANP-]", last))
                while len(status_cells) < n_rev:
                    status_cells.append("")

                status_by_revision = {}
                for rev, cell in zip(revisions, status_cells):
                    m = STATUS_RE.search(cell)
                    status_by_revision[rev] = m.group(0) if m else "?"

                rows.append({
                    "section_id": sec,
                    "function": function,
                    "title": title,
                    "status_by_revision": status_by_revision,
                })
    return rows, revisions


# ── Detail sections (2.x.y) ─────────────────────────────────────────────────

def clean_corpus(pdf, detail_pages: list[int], function_names: set[str]) -> str:
    """Lowercased, whitespace-normalized PDF text without page headers/footers.

    Used by the content-fidelity audit: a sentence cut by a page break is
    contiguous here (the header/footer lines that pdfplumber emits in between
    are dropped), so false negatives are avoided.
    """
    parts = []
    for pno in detail_pages:
        body = []
        for i, ln in enumerate(page_text(pdf, pno).splitlines()):
            s = ln.strip()
            if not s:
                continue
            if i <= 1 and (FAMILY_HINT_RE.match(s) or s in function_names
                           or s in ("Description of device errata", "Summary of device errata",
                                    "Contents", "Revision history")):
                continue
            if PAGE_FOOTER_RE.match(s):
                continue
            body.append(s)
        # Apply the same subscript normalization as the extracted entries so the
        # fidelity check compares like with like.
        parts.append(fix_tsu_dat(" ".join(body)))
    return norm(" ".join(parts)).lower()

def extract_details(pdf, detail_pages: list[int], function_names: set[str],
                    evidence: dict | None = None) -> tuple[dict, dict]:
    """Returns (entries, stats).

    entries: {section_id: entry} with title, description, workaround,
    limitation, conditions, page (1-indexed) and 2.x group links.
    stats: {"subheadings": {name: count}, "conditions_blocks": int,
            "layout_warns": [str]}
    """
    ev_lines = (evidence or {}).get("lines", {})
    body_size = (evidence or {}).get("body_size")

    entries = {}
    section = None
    mode = None  # "title" | "desc" | "work" | "limitation" | "conditions"
    group = None  # {"id": "2.2", "title": "System", "page": n}
    cond_block = []  # pending bullet block (normalized bullet texts)
    cond_prev = []  # last stored body lines before the block (trigger context)
    prev_window = deque(maxlen=2)  # trailing stored lines, for cond_prev
    stats = {"subheadings": {}, "conditions_blocks": 0, "layout_warns": []}

    def flush():
        nonlocal section, mode, cond_block, prev_window
        section = None
        mode = None
        cond_block = []
        prev_window.clear()

    def close_cond_block():
        nonlocal cond_block
        if not cond_block or section is None:
            cond_block = []
            return
        if TRIGGER_RE.search(" ".join(cond_prev)):
            entries[section]["conditions"].extend(cond_block)
            stats["conditions_blocks"] += 1
        cond_block = []

    def store(key, text):
        if section is not None and text:
            entries[section][key].append(text)

    def heading_evidence(s: str):
        """None = no evidence; True/False = font suggests heading / prose."""
        rec = ev_lines.get(s)
        if rec is None:
            return None
        if rec["bold"]:
            return True
        if body_size and rec["size"] and rec["size"] > body_size:
            return True
        return False

    for pno in detail_pages:
        lines = page_text(pdf, pno).splitlines()

        # Drop running header (line 0: family; line 1: section banner / group name)
        body = []
        for i, ln in enumerate(lines):
            s = ln.strip()
            if not s:
                continue
            if i <= 1 and (FAMILY_HINT_RE.match(s) or s in function_names
                           or s in ("Description of device errata", "Summary of device errata",
                                    "Contents", "Revision history")):
                continue
            body.append(s)

        for s in body:
            if PAGE_FOOTER_RE.match(s):
                continue

            m = SECTION_RE.match(s)
            if m:
                close_cond_block()
                flush()
                section = m.group(1)
                mode = "title"
                entries[section] = {"title": [norm(m.group(2))], "desc": [], "work": [],
                                    "limitation": [], "conditions": [],
                                    "desc_seen": False, "work_seen": False,
                                    "limitation_seen": False, "conditions_seen": False,
                                    "page": pno + 1,
                                    "group_id": group["id"] if group else None,
                                    "group_title": group["title"] if group else None,
                                    "group_page": group["page"] if group else None}
                continue

            m = FUNCTION_RE.match(s)
            if m:
                close_cond_block()
                group = {"id": m.group(1), "title": norm(m.group(2)), "page": pno + 1}
                if section is not None:
                    flush()
                continue

            if section is None:
                continue

            if s in SUBHEADINGS:
                if s == "Description":
                    mode = "desc"
                    entries[section]["desc_seen"] = True
                elif s == "Workaround":
                    mode = "work"
                    entries[section]["work_seen"] = True
                else:
                    # "Limitation" / "Conditions": heading only when the font
                    # evidence confirms it (bold or larger than body); otherwise
                    # the line is prose and flows into the current mode.
                    if heading_evidence(s) is False:
                        store(mode, norm(s))
                        if mode != "work":
                            prev_window.append(norm(s))
                        continue
                    mode = s.lower()
                    entries[section][f"{mode}_seen"] = True
                    stats["subheadings"][s] = stats["subheadings"].get(s, 0) + 1
                close_cond_block()
                prev_window.clear()
                continue

            is_bullet = s.startswith(BULLET_CHARS)
            if is_bullet:
                store(mode, s)
                if mode in ("desc", "limitation", "conditions"):
                    if not cond_block:
                        cond_prev = list(prev_window)
                    cond_block.append(norm(s[1:]))
                continue

            close_cond_block()
            if mode == "title":
                entries[section]["title"].append(norm(s))
            elif mode in ("desc", "limitation", "conditions"):
                store(mode, norm(s))
                prev_window.append(norm(s))
            elif mode == "work":
                store(mode, norm(s))

    return entries, stats


# ── Computed enrichment (deterministic rules, no LLM) ───────────────────────

def extract_keywords(text: str, top_n: int = 8) -> list[str]:
    """Lowercased token frequency ranking of title + description text."""
    counts = Counter()
    for m in re.finditer(r"[a-z0-9]{4,}", text.lower()):
        tok = m.group(0)
        if tok not in STOPWORDS and not tok.isdigit():
            counts[tok] += 1
    return [tok for tok, _ in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[:top_n]]


def extract_aliases(doc_id: str, section_id: str, peripheral: str, title: str,
                    top_n: int = 6) -> list[str]:
    """Deterministic alias candidates: ids, peripheral, title n-grams."""
    aliases = []

    def add(a: str):
        a = norm(a)
        if a and a not in aliases and a.lower() not in STOPWORDS:
            aliases.append(a)

    add(section_id)
    add(f"{doc_id} {section_id}")
    add(peripheral)
    words = title.split()
    for n in (2, 3, 1):
        for i in range(0, len(words) - n + 1):
            add(" ".join(words[i:i + n]))
    return aliases[:top_n]


def classify_impact(text: str) -> list[str]:
    """All impact categories whose lexicon matches the text, in lexicon order."""
    t = text.lower()
    return [name for name, pattern in IMPACT_LEXICON if pattern.search(t)]


def mentions_flags(text: str) -> dict:
    t = text.lower()
    return {key: bool(pattern.search(t)) for key, pattern in MENTIONS_FLAGS.items()}


def derive_severity(statuses: dict, revisions: list[str]) -> str:
    """High when any revision has no workaround (N), medium for P/A, else unknown."""
    letters = [statuses.get(r, "?") for r in revisions]
    if "N" in letters:
        return "high"
    if "P" in letters or "A" in letters:
        return "medium"
    return "unknown"


# ── RAG chunks (canonical schema, see errata_extractor_es0676.py) ────────────

def build_chunks(meta: dict, errata: list[dict], revisions: list[str],
                 groups: dict[str, dict]) -> list[dict]:
    chunks = []
    for e in errata:
        base_key = f"{meta['doc_id']}:{e['section_id']}"
        parent_document_id = sha1(base_key + ":full_entry")

        statuses = e["status_by_revision"]
        is_doc = e.get("documentation_errata", False)
        if is_doc:
            affected = []
            fixed = []
            has_workaround = False
            partial = False
        else:
            affected = [r for r in revisions if statuses.get(r, "?") != "-"]
            fixed = [r for r in revisions if statuses.get(r, "?") == "-"]
            has_workaround = any(statuses.get(r, "?") == "A" for r in revisions)
            partial = any(statuses.get(r, "?") == "P" for r in revisions)

        full_text = f"{e['title']}\n\nDescription\n{e['description']}"
        if e.get("limitation"):
            full_text += f"\n\nLimitation\n{e['limitation']}"
        full_text += f"\n\nWorkaround\n{e['workaround']}"

        filters_common = {
            "family": meta["family"],
            "reference_manual": meta["reference_manual"],
            "peripheral": e["function"],
            "group_id": e.get("group_id"),
            "group_title": e.get("group_title"),
            "errata_id": e["section_id"],
            "affected_revisions": affected,
            "fixed_in_revision": fixed,
            "status_by_revision": {} if is_doc else {r: statuses.get(r, "?") for r in revisions},
            "is_documentation_errata": is_doc,
            "has_workaround": has_workaround,
            "partial_workaround_only": partial,
            "conditions": e.get("conditions") or [],
            "impact_category": e["impact_category"],
            "keywords": e["keywords"],
            "aliases": e["aliases"],
            "severity": e["severity"],
            **e["mentions"],
        }

        citation_common = {
            "doc_id": meta["doc_id"],
            "doc_version": meta["doc_version"],
            "doc_date": meta["doc_date"],
            "page": e["page"],
            "url": f"{meta['url_pdf']}#page={e['page']}",
            "section_title": f"{e['section_id']} {e['title']}",
        }
        # title: plain errata title, sibling of citation, inherited by the 4 chunks
        title_common = e["title"]

        chunks.append({
            "document_id": sha1(base_key + ":full_entry"),
            "parent_document_id": None,
            "doc_id": meta["doc_id"],
            "title": title_common,
            "embed_text": f"{e['function']} errata {e['section_id']}: {e['title']}. "
                         f"{e['description']} Workaround: {e['workaround']}",
            "raw_text": full_text,
            "filters": {**filters_common, "section_type": "full_entry"},
            "citation": citation_common,
        })

        chunks.append({
            "document_id": sha1(base_key + ":description"),
            "parent_document_id": parent_document_id,
            "doc_id": meta["doc_id"],
            "title": title_common,
            "embed_text": f"{e['function']} bug description - {e['title']}: {e['description']}",
            "raw_text": e["description"],
            "filters": {**filters_common, "section_type": "description"},
            "citation": citation_common,
        })

        chunks.append({
            "document_id": sha1(base_key + ":workaround"),
            "parent_document_id": parent_document_id,
            "doc_id": meta["doc_id"],
            "title": title_common,
            "embed_text": f"Workaround for {e['function']} issue - {e['title']}: {e['workaround']}",
            "raw_text": e["workaround"],
            "filters": {**filters_common, "section_type": "workaround"},
            "citation": citation_common,
        })

        if is_doc:
            status_sentence = "Documentation erratum: no silicon revision applicability (not listed in Table 3)"
            applicability_embed = f"Documentation erratum {e['function']} {e['section_id']} " \
                                  f"({e['title']}): no silicon revision applicability"
        else:
            status_sentence = "; ".join(
                f"Rev {r}: {STATUS_MEANING.get(statuses.get(r, '?'), statuses.get(r, '?'))}"
                for r in revisions
            )
            applicability_embed = f"Silicon revision applicability for {e['function']} errata " \
                                  f"{e['section_id']} ({e['title']}): {status_sentence}"
        chunks.append({
            "document_id": sha1(base_key + ":applicability"),
            "parent_document_id": parent_document_id,
            "doc_id": meta["doc_id"],
            "title": title_common,
            "embed_text": applicability_embed,
            "raw_text": status_sentence,
            "filters": {**filters_common, "section_type": "applicability"},
            "citation": citation_common,
        })

    # Group overview chunks (one per section 2.x with at least one errata)
    group_chunks = []
    for gid in sorted(groups):
        g = groups[gid]
        members = [e for e in errata if e.get("group_id") == gid]
        if not members:
            continue
        list_text = "; ".join(f"{e['section_id']} {e['title']}" for e in members)
        group_chunks.append({
            "document_id": sha1(f"{meta['doc_id']}:{gid}:group"),
            "parent_document_id": None,
            "doc_id": meta["doc_id"],
            "embed_text": f"Errata overview for {g['title']} (section {gid}): {list_text}",
            "raw_text": f"Section {gid} {g['title']}\n\n{list_text}",
            "filters": {
                "family": meta["family"],
                "reference_manual": meta["reference_manual"],
                "peripheral": g["title"],
                "group_id": gid,
                "errata_ids": [e["section_id"] for e in members],
                "section_type": "group",
            },
            "citation": {
                "doc_id": meta["doc_id"],
                "doc_version": meta["doc_version"],
                "doc_date": meta["doc_date"],
                "page": g["page"],
                "url": f"{meta['url_pdf']}#page={g['page']}",
                "section_title": f"{gid} {g['title']}",
            },
        })

    # Document-level summary chunk (meta + group list + top keywords; answers
    # counting/meta questions without relying on vector retrieval)
    group_lines = "; ".join(
        f"{gid} {groups[gid]['title']} "
        f"({sum(1 for e in errata if e.get('group_id') == gid)} errata)"
        for gid in sorted(groups) if any(e.get("group_id") == gid for e in errata))
    kw_counter = Counter()
    for e in errata:
        kw_counter.update(e["keywords"])
    top_kw = ", ".join(t for t, _ in kw_counter.most_common(10))
    doc_chunk = {
        "document_id": sha1(f"{meta['doc_id']}:document:document_summary"),
        "parent_document_id": None,
        "doc_id": meta["doc_id"],
        "embed_text": f"Errata sheet {meta['doc_id']} {meta['doc_version']} ({meta['doc_date']}) "
                      f"for {meta['family']} (reference manual {meta['reference_manual']}): "
                      f"{len(errata)} errata across {len([g for g in groups if any(e.get('group_id') == g for e in errata)])} "
                      f"groups. Groups: {group_lines}",
        "raw_text": f"Errata sheet {meta['doc_id']} {meta['doc_version']} ({meta['doc_date']})\n"
                    f"Family: {meta['family']}\n"
                    f"Reference manual: {meta['reference_manual']}\n"
                    f"Total errata: {len(errata)}\n"
                    f"Groups: {group_lines}"
                    + (f"\nTop keywords: {top_kw}" if top_kw else ""),
        "filters": {
            "family": meta["family"],
            "reference_manual": meta["reference_manual"],
            "section_type": "document_summary",
        },
        "citation": {
            "doc_id": meta["doc_id"],
            "doc_version": meta["doc_version"],
            "doc_date": meta["doc_date"],
            "page": 1,
            "url": f"{meta['url_pdf']}#page=1",
            "section_title": "Document summary",
        },
    }

    return [doc_chunk] + group_chunks + chunks


# ── Per-document pipeline ───────────────────────────────────────────────────

# ── PHASE 2: structural verification gate (blocking) ────────────────────────
#
# Runs on the raw assembly (errata/groups/details, optionally the chunks) BEFORE
# the JSON is written. FAIL means: no JSON for this document. Content audits
# (title ratio, corpus fidelity, unexpected status) stay non-blocking warnings.

def verify_extraction(meta: dict, errata: list[dict], groups: dict,
                      details: dict) -> list[str]:
    """Structural gate 2.1-2.3/2.5: groups, errata, chunk presence, coverage.

    Rules (generic, presence-based):
      - every detail section must be covered by an errata (zero missing)
      - errata section ids unique (zero duplicate)
      - every errata has a group (zero orphan) and a title (full_entry base)
      - every group referenced by an errata exists; every group has a member
    """
    problems = []
    doc_id = meta["doc_id"]
    eids = [e["section_id"] for e in errata]
    dupes = sorted({x for x in eids if eids.count(x) > 1})
    if dupes:
        problems.append(f"{doc_id}: duplicate errata section ids {dupes}")
    missing = sorted(set(details) - set(eids))
    if missing:
        problems.append(f"{doc_id}: detail sections without errata: {missing}")
    for e in errata:
        if not e.get("title", "").strip():
            problems.append(f"{doc_id}: {e['section_id']} missing title (full_entry mandatory)")
        if not e.get("group_id"):
            problems.append(f"{doc_id}: {e['section_id']} missing group_id (orphan errata)")
        elif e["group_id"] not in groups:
            problems.append(f"{doc_id}: {e['section_id']} group {e['group_id']} "
                            f"missing from groups")
        if e.get("documentation_errata") and e.get("status_by_revision"):
            problems.append(f"{doc_id}: {e['section_id']} documentation errata "
                            f"must have an empty status matrix")
    for gid in groups:
        if not any(e.get("group_id") == gid for e in errata):
            problems.append(f"{doc_id}: group {gid} has no errata member")
    return problems


def verify_chunk_integrity(meta: dict, chunks: list[dict],
                           errata: list[dict]) -> list[str]:
    """Structural gate 2.4: parent/group linkage on the built chunks.

    - every child parent_document_id resolves to an existing chunk
    - every errata has exactly one full_entry chunk
    - every group chunk references only existing errata ids
    """
    problems = []
    doc_id = meta["doc_id"]
    ids = {c["document_id"] for c in chunks}
    errata_ids = set()
    for c in chunks:
        if c["filters"]["section_type"] in ("group", "document_summary"):
            continue
        errata_ids.add(c["filters"]["errata_id"])
        if c["parent_document_id"] is not None and c["parent_document_id"] not in ids:
            problems.append(f"{doc_id}: document {c['document_id']} invalid parent_document_id")
    for e in errata:
        count = sum(1 for c in chunks
                    if c["filters"].get("errata_id") == e["section_id"]
                    and c["filters"]["section_type"] == "full_entry")
        if count != 1:
            problems.append(f"{doc_id}: {e['section_id']} has {count} full_entry chunks "
                            f"(exactly 1 mandatory)")
    for c in chunks:
        if c["filters"]["section_type"] != "group":
            continue
        for eid in c["filters"].get("errata_ids", []):
            if eid not in errata_ids:
                problems.append(f"{doc_id}: group {c['filters']['group_id']} references "
                                f"unknown errata {eid}")
    return problems

def process_pdf(pdf_path: Path, doc_id_override: str | None = None) -> dict:
    pdf = pdfplumber.open(pdf_path)
    try:
        meta = parse_metadata(pdf)
        if doc_id_override:
            meta["doc_id"] = doc_id_override
        meta["url_pdf"] = f"https://www.st.com/resource/en/errata_sheet/{pdf_path.stem}.pdf"

        # Locate page ranges
        summary_start = detail_start = None
        stop = len(pdf.pages)
        for pno in range(len(pdf.pages)):
            text = page_text(pdf, pno)
            if summary_start is None and "Table 3. Summary of device limitations" in text:
                summary_start = pno
            elif summary_start is None and re.search(r"^1\s+Summary of device errata", text, re.M):
                # Old sheets (ex. es0468/es0547 G0) have no "Table 3." caption:
                # fall back to the "1 Summary of device errata" section heading.
                summary_start = pno
            if detail_start is None and re.search(r"^2\s+Description of device errata", text, re.M):
                detail_start = pno
            if detail_start is not None and stop == len(pdf.pages) and pno >= detail_start:
                if any(mk in text for mk in STOP_MARKERS):
                    stop = pno
                    break
        if summary_start is None or detail_start is None:
            raise ValueError(f"page ranges not found (summary={summary_start}, detail={detail_start})")

        summary_pages = list(range(summary_start, detail_start))
        detail_pages = list(range(detail_start, stop))

        # First pass over details to collect function group names (running headers)
        function_names = set()
        for pno in detail_pages:
            for ln in page_text(pdf, pno).splitlines():
                m = FUNCTION_RE.match(ln.strip())
                if m:
                    function_names.add(norm(m.group(2)))

        table_rows, revisions = extract_table3(pdf, summary_pages)
        evidence = layout_evidence(pdf, detail_pages)
        details, dstats = extract_details(pdf, detail_pages, function_names, evidence)
        corpus_clean = clean_corpus(pdf, detail_pages, function_names)
        notes = [
            f"layout: body size {evidence['body_size']}pt, "
            f"{len(evidence['lines'])} evidence lines",
            f"subheadings detected: "
            f"{dstats['subheadings'] or 'none (Description/Workaround only)'}",
            f"conditions blocks extracted: {dstats['conditions_blocks']}",
        ]
        notes.extend(f"layout: {w}" for w in dstats["layout_warns"])

        # Audit: Table 3 vs detail sections
        problems = []
        for row in table_rows:
            sec = row["section_id"]
            if sec not in details:
                problems.append(f"Table 3 section {sec} has no detail section")
                continue
            for rev, status in row["status_by_revision"].items():
                if status == "?":
                    problems.append(f"Section {sec}: unexpected status '?' for Rev {rev}")
            detail_title = fix_tsu_dat(" ".join(details[sec]["title"]))
            t3_title = fix_tsu_dat(row["title"])
            if t3_title and detail_title:
                ratio = SequenceMatcher(None, t3_title.lower(), detail_title.lower()).ratio()
                if ratio < 0.75:
                    problems.append(f"Section {sec}: Table 3 title diverges from detail title "
                                    f"(ratio {ratio:.2f}) -> Table3: {t3_title[:60]!r} / detail: {detail_title[:60]!r}")
        for sec, d in details.items():
            if not any(r["section_id"] == sec for r in table_rows):
                problems.append(f"Detail section {sec} absent from Table 3 "
                                f"(and not listed as documentation erratum)")
            if not d["desc_seen"]:
                problems.append(f"Section {sec} has no Description heading")
            if not d["work_seen"]:
                problems.append(f"Section {sec} has no Workaround heading")
            if not d["desc"]:
                problems.append(f"Section {sec} has empty Description")
            if not d["work"]:
                problems.append(f"Section {sec} has empty Workaround body")
            if not d.get("group_id"):
                problems.append(f"Section {sec} has no 2.x group heading")

        # Audit: content fidelity vs cleaned PDF corpus
        for row in table_rows:
            d = details[row["section_id"]]
            title = fix_tsu_dat(" ".join(d["title"]))
            desc = fix_tsu_dat(" ".join(d["desc"]))
            work = fix_tsu_dat(" ".join(d["work"]))
            limitation = fix_tsu_dat(" ".join(d["limitation"]))
            if not title or title.lower()[:70] not in corpus_clean:
                problems.append(f"Section {row['section_id']}: title not found in PDF corpus")
            if desc and desc.lower()[:50] not in corpus_clean:
                problems.append(f"Section {row['section_id']}: Description not found in PDF corpus")
            if limitation and limitation.lower()[:50] not in corpus_clean:
                problems.append(f"Section {row['section_id']}: Limitation not found in PDF corpus")
            if work and work.lower()[:50] not in corpus_clean:
                problems.append(f"Section {row['section_id']}: Workaround not found in PDF corpus")
            for cond in d["conditions"]:
                if not cond or fix_tsu_dat(cond).lower()[:40] not in corpus_clean:
                    problems.append(f"Section {row['section_id']}: condition not found in "
                                    f"PDF corpus: {cond[:50]!r}")

        # Join Table 3 rows with detail bodies
        errata = []
        for row in table_rows:
            d = details[row["section_id"]]
            title = fix_tsu_dat(" ".join(d["title"]))
            desc = fix_tsu_dat(" ".join(d["desc"]))
            work = fix_tsu_dat(" ".join(d["work"])) or "None."
            limitation = fix_tsu_dat(" ".join(d["limitation"]))
            conditions = [fix_tsu_dat(norm(c)) for c in d["conditions"]]
            entry_text = f"{title} {desc} {work} {limitation}"
            errata.append({
                "section_id": row["section_id"],
                "function": row["function"],
                "group_id": d.get("group_id"),
                "group_title": d.get("group_title"),
                "title": title,
                "description": desc,
                "workaround": work,
                "limitation": limitation,
                "conditions": conditions,
                "status_by_revision": row["status_by_revision"],
                "documentation_errata": bool(row.get("documentation_errata")),
                "page": d["page"],
                "impact_category": classify_impact(entry_text),
                "keywords": extract_keywords(f"{title} {desc}"),
                "aliases": extract_aliases(meta["doc_id"], row["section_id"],
                                           row["function"], title),
                "severity": derive_severity(row["status_by_revision"], revisions),
                "mentions": mentions_flags(entry_text),
            })

        # Detail sections absent from every table (ST omission, ex. ES0661 2.6.2):
        # kept for RAG coverage with an empty status matrix, flagged for review.
        covered = {row["section_id"] for row in table_rows}
        for sec, d in sorted(details.items()):
            if sec in covered or not d.get("group_id"):
                continue
            title = fix_tsu_dat(" ".join(d["title"]))
            desc = fix_tsu_dat(" ".join(d["desc"]))
            work = fix_tsu_dat(" ".join(d["work"])) or "None."
            limitation = fix_tsu_dat(" ".join(d["limitation"]))
            conditions = [fix_tsu_dat(norm(c)) for c in d["conditions"]]
            entry_text = f"{title} {desc} {work} {limitation}"
            errata.append({
                "section_id": sec,
                "function": d.get("group_title") or "",
                "group_id": d.get("group_id"),
                "group_title": d.get("group_title"),
                "title": title,
                "description": desc,
                "workaround": work,
                "limitation": limitation,
                "conditions": conditions,
                "status_by_revision": {},
                "documentation_errata": True,
                "page": d["page"],
                "impact_category": classify_impact(entry_text),
                "keywords": extract_keywords(f"{title} {desc}"),
                "aliases": extract_aliases(meta["doc_id"], sec,
                                           d.get("group_title") or "", title),
                "severity": derive_severity({}, revisions),
                "mentions": mentions_flags(entry_text),
            })

        # Group sections 2.x (heading id/title/page of the first entry that saw it)
        groups = {}
        for d in details.values():
            gid = d.get("group_id")
            if gid and gid not in groups:
                groups[gid] = {"id": gid, "title": d.get("group_title"), "page": d.get("group_page")}

        # PHASE 2: structural gate (blocking) on the raw assembly
        structural = verify_extraction(meta, errata, groups, details)

        # PHASE 3: build the enriched chunks (title, filters, citations)
        chunks = build_chunks(meta, errata, revisions, groups)

        # PHASE 2.4: chunk-level parent/group linkage gate (before writing)
        structural.extend(verify_chunk_integrity(meta, chunks, errata))

        out = {
            "doc_id": meta["doc_id"],
            "doc_version": meta["doc_version"],
            "family": meta["family"],
            "reference_manual": meta["reference_manual"],
            "url_pdf": f"https://www.st.com/resource/en/errata_sheet/{pdf_path.stem}.pdf",
            "extractor_version": EXTRACTOR_VERSION,
            "total_errata": len(errata),
            "total_groups": len([g for g in groups.values()
                                 if any(e.get("group_id") == g["id"] for e in errata)]),
            "total_chunks": len(chunks),
            "documents": chunks,
        }
        return {"meta": meta, "revisions": revisions, "out": out,
                "problems": problems, "structural": structural,
                "notes": notes, "pdf": pdf_path.stem}
    finally:
        pdf.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Extract STM32 errata sheets (ESxxxx PDFs) into selective-RAG JSON.")
    parser.add_argument("--pdf-path", type=Path, default=None,
                        help="process a single PDF (default: batch over --input-dir)")
    parser.add_argument("--input-dir", type=Path, default=INPUT_DIR,
                        help="directory of PDFs to process (default: input/)")
    parser.add_argument("--output", dest="output_dir", type=Path, default=OUTPUT_DIR,
                        help="output directory for esXXXX_errata_rag.json (default: output/)")
    parser.add_argument("--doc-id", default=None, metavar="AUTO|ES0xxx",
                        help="override the doc id extracted from the PDF footer")
    parser.add_argument("--validate", action="store_true",
                        help="run validate_json invariants + rag_utils smoke test on generated files")
    args = parser.parse_args(argv)

    files = [args.pdf_path] if args.pdf_path else list_pdfs(args.input_dir)
    files = [f for f in files if f]
    if not files:
        print(f"No PDFs in {args.input_dir}")
        return 1
    args.output_dir.mkdir(parents=True, exist_ok=True)

    doc_id_override = None
    if args.doc_id and args.doc_id != "AUTO":
        doc_id_override = args.doc_id

    problems_all = []
    ok_docs = 0
    for f in files:
        print("=" * 90)
        print(f.name)
        try:
            r = process_pdf(f, doc_id_override=doc_id_override)
        except Exception as exc:
            print(f"  ERROR: {exc}")
            problems_all.append(f"{f.name}: {exc}")
            continue

        out = r["out"]
        out_path = args.output_dir / f"{r['meta']['doc_id'].lower()}_errata_rag.json"

        # PHASE 2 gate: structural FAIL -> no JSON for this document
        if r["structural"]:
            print(f"  PHASE 2 GATE FAIL ({len(r['structural'])} structural problem(s)):")
            for p in r["structural"]:
                print(f"    - {p}")
            problems_all.extend(f"{f.name}: [structural] {p}" for p in r["structural"])
            continue

        with open(out_path, "w", encoding="utf-8") as fh:
            json.dump(out, fh, ensure_ascii=False, indent=2)

        print(f"  {r['meta']['doc_id']} {r['meta']['doc_version']} ({r['meta']['doc_date']}) "
              f"family={r['meta']['family']} rm={r['meta']['reference_manual']}")
        print(f"  revisions tracked: {r['revisions']}")
        print(f"  errata: {out['total_errata']}  groups: {out['total_groups']}  "
              f"chunks: {out['total_chunks']}  "
              f"(expected {4 * out['total_errata'] + out['total_groups'] + 1})")
        print(f"  -> {out_path}")
        print(f"  PHASE 2 gate: PASS ({len(r['structural'])} structural checks)")
        for p in r["problems"]:
            print(f"  AUDIT: {p}")
            problems_all.append(f"{f.name}: {p}")
        for n in r["notes"]:
            print(f"  NOTE: {n}")
        if not r["problems"]:
            print("  AUDIT: clean")

        if args.validate:
            from rmerrata import rag_utils, validate
            vproblems = validate.validate_document(out_path)
            with open(out_path, encoding="utf-8") as fh:
                vproblems += rag_utils.smoke_test(json.load(fh))
            if vproblems:
                print(f"  VALIDATE FAILED ({len(vproblems)}):")
                for p in vproblems:
                    print(f"    - {p}")
                problems_all.extend(f"{f.name}: {p}" for p in vproblems)
            else:
                print("  VALIDATE: ok")
        ok_docs += 1

    print("=" * 90)
    print(f"Done. {ok_docs}/{len(files)} documents, {len(problems_all)} problem(s).")
    if problems_all:
        print("\n".join(f"  - {p}" for p in problems_all))
    return 1 if problems_all else 0


if __name__ == "__main__":
    sys.exit(main())
