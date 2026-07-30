#!/usr/bin/env python3
"""Export tables from ANY ST STM32 reference-manual PDF into the internal ST
`rag_selective` schema. Deterministic (pdfplumber lattice), offline, no LLM.

- Document metadata is AUTO-DERIVED from the PDF (cover page + filename); every
  field can be overridden on the CLI. Nothing here is specific to one manual.
- Only captioned "Table N." tables are emitted, in the generic
  {headers, rows, units, notes} content shape. Merged cells -> "".
- Table footnotes below each table are captured into `notes`.
"""
import argparse, json, re, sys
import pdfplumber

CAP  = re.compile(r'^\s*Table\s+(\d+)\s*\.\s*(.*)$')
FIG  = re.compile(r'^\s*Figure\s+\d+\s*\.')
CONT = re.compile(r'\(continued\)\s*$', re.IGNORECASE)
NOTE = re.compile(r'^\(?(\d+)\)?[.)]\s+(\S.*)$')     # "1. ..."  or "(1) ..."
HEAD = re.compile(r'^\d+(?:\.\d+)+\s+\S')            # "4.7.1 Title"
FOOT = re.compile(r'(^\d{1,4}/\d{2,5}\b|\b(?:RM|DS|PM|AN|UM)\d{3,4}\s+Rev\s+\d+)')  # ST footer
TS = {"vertical_strategy":"lines","horizontal_strategy":"lines","snap_tolerance":3,
      "join_tolerance":3,"edge_min_length":3,"intersection_tolerance":3}


def cell_text(chars, bbox):
    x0,top,x1,bottom = bbox; pad=0.5
    cs=[c for c in chars if c['x0']>=x0-pad and c['x1']<=x1+pad
        and c['top']>=top-pad and c['bottom']<=bottom+pad]
    if not cs: return ""
    up=[c for c in cs if c.get('upright',True)]; rot=[c for c in cs if not c.get('upright',True)]
    parts=[]
    if up:
        up.sort(key=lambda c:(round(c['top']/2),c['x0'])); line=[]; last=None
        for c in up:
            if last is None or abs(c['top']-last)<=3: line.append(c['text'])
            else: parts.append(''.join(line)); line=[c['text']]
            last=c['top']
        if line: parts.append(''.join(line))
    if rot:
        rot.sort(key=lambda c:-c['top']); parts.append(''.join(c['text'] for c in rot))
    return ' '.join(p.strip() for p in parts if p.strip())


def notes_below(lines, bottom):
    """Collect footnote lines directly below a table, joining wrapped lines."""
    below=sorted([l for l in lines if l['top']>bottom-1], key=lambda l:l['top'])
    notes=[]; cur=None; started=False
    for l in below:
        txt=l['text'].strip()
        if not txt:
            if started: break
            continue
        if CAP.match(txt) or FIG.match(txt) or HEAD.match(txt) or FOOT.search(txt): break
        if NOTE.match(txt):
            if cur is not None: notes.append(cur)
            cur=txt; started=True
        elif cur is not None:
            cur+=' '+txt                      # wrapped continuation of a footnote
        else:
            break                              # first line isn't a footnote -> none
    if cur is not None: notes.append(cur)
    return notes


def derive_metadata(pdf, filename, overrides):
    cover=""
    for i in range(min(2,len(pdf.pages))):
        cover+="\n"+(pdf.pages[i].extract_text() or ""); pdf.pages[i].flush_cache()
    title=(pdf.metadata or {}).get('Title','') or ""
    txt=cover+"\n"+title
    def find(pat, default=""):
        m=re.search(pat, txt); return m.group(1).strip() if m else default
    # scan more pages for a clean "Rev N" (usually in footers)
    rev=""
    for i in range(min(120,len(pdf.pages))):
        m=re.search(r'Rev\s+(\d+)', pdf.pages[i].extract_text() or "")
        pdf.pages[i].flush_cache()
        if m: rev="Rev "+m.group(1); break
    fam=find(r'STM32([A-Z]\d)')
    core_m=re.search(r'Cortex[\u00ae\-\s]*M(\d\+?)', txt)
    core=f"Arm 32-bit Cortex-M{core_m.group(1)} CPU" if core_m else ""
    meta={
        "name_datasheet": find(r'\b(RM\d{3,4})\b') or re.sub(r'[-_].*','',filename).upper(),
        "rev": rev,
        "url_pdf": f"https://www.st.com/resource/en/reference_manual/{filename}",
        "references": find(r'(STM32[A-Z]\d[0-9A-Za-z]*)'),  # best-effort; often overridden
        "package": "",
        "family": fam,
        "core": core,
        "frequency": find(r'(up to \d+\s*MHz)'),
    }
    meta.update({k:v for k,v in overrides.items() if v is not None})
    return meta


def build(pdf, page_range):
    lo,hi=page_range; logical=[]
    for i in range(lo-1,min(hi,len(pdf.pages))):
        page=pdf.pages[i]; chars=page.chars; lines=page.extract_text_lines()
        caps=[{'n':int(m.group(1)),'txt':CONT.sub('',m.group(2)).strip().rstrip('.').strip(),
               'cont':bool(CONT.search(m.group(2))),'top':l['top']}
              for l in lines for m in [CAP.match(l['text'])] if m]
        figs=[l['top'] for l in lines if FIG.match(l['text'])]
        for t in page.find_tables(table_settings=TS):
            top=t.bbox[1]; bottom=t.bbox[3]
            above=[c for c in caps if c['top']<=top+2]
            nfig=max([f for f in figs if f<=top+2], default=-1)
            ncap=max(above,key=lambda c:c['top']) if above else None
            if not ncap or ncap['top']<nfig: continue        # keep captioned tables only
            rows=[[ (cell_text(chars,c) if c else "") for c in r.cells] for r in t.rows]
            if not rows: continue
            notes=notes_below(lines, bottom)
            if (logical and ncap['n']==logical[-1]['n'] and
                (ncap['cont'] or i+1-logical[-1]['page']<=1)):
                prev=logical[-1]
                if rows and prev['rows'] and rows[0]==prev['rows'][0]: rows=rows[1:]
                prev['rows'].extend(rows); prev['notes'].extend(notes)
            else:
                logical.append({'n':ncap['n'],'name':ncap['txt'],'page':i+1,
                                'rows':rows,'notes':notes})
        page.flush_cache()
    return logical


def to_schema(logical, meta):
    tables=[]
    for lt in logical:
        rows=lt['rows']; headers=rows[0] if rows else []; body=rows[1:] if len(rows)>1 else []
        # de-dup notes preserving order
        seen=set(); notes=[n for n in lt['notes'] if not (n in seen or seen.add(n))]
        tables.append({
            "table_name": lt['name'], "table_number": str(lt['n']), "page": lt['page'],
            "filters": {"columns": headers, "units": {}},
            "url_to_table": f"{meta['url_pdf']}#page={lt['page']}",
            "table_content": {"headers": headers, "rows": body, "units": {}, "notes": notes},
        })
    doc=dict(meta); doc["tables"]=tables; return doc


def main():
    ap=argparse.ArgumentParser(description="Export ST RM tables to rag_selective JSON")
    ap.add_argument("pdf"); ap.add_argument("-o","--out",default="rag_selective.json")
    ap.add_argument("--pages")
    for f in ["name_datasheet","rev","url_pdf","references","package","family","core","frequency"]:
        ap.add_argument("--"+f)
    a=ap.parse_args()
    import os
    pdf=pdfplumber.open(a.pdf); filename=os.path.basename(a.pdf)
    overrides={f:getattr(a,f) for f in
               ["name_datasheet","rev","url_pdf","references","package","family","core","frequency"]}
    meta=derive_metadata(pdf, filename, overrides)
    pr=tuple(int(x) for x in a.pages.split('-')) if a.pages else (1,len(pdf.pages))
    logical=build(pdf, pr)
    doc=to_schema(logical, meta)
    json.dump(doc, open(a.out,'w'), indent=2, ensure_ascii=False)
    print(f"metadata: {json.dumps({k:doc[k] for k in doc if k!='tables'}, ensure_ascii=False)}", file=sys.stderr)
    print(f"wrote {len(doc['tables'])} tables -> {a.out}", file=sys.stderr)

if __name__=="__main__":
    main()
