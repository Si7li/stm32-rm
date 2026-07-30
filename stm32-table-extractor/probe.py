# probe_api.py
from curl_cffi import requests as r
import json

URL = ("https://www.st.com/bin/st/selectors/cxst/en.cxst-rs-grid.html/"
       "CL1734.technical_literature.reference_manual.json")

S = r.Session(impersonate="chrome")
x = S.get(URL, timeout=60)
print("status:", x.status_code, "| bytes:", len(x.content))

try:
    d = x.json()
except Exception:
    print(x.text[:800]); raise SystemExit

def walk(o, path="", depth=0):
    if depth > 3: return
    if isinstance(o, dict):
        print("  "*depth + f"{path} dict keys={list(o.keys())[:12]}")
        for k, v in list(o.items())[:6]:
            walk(v, k, depth+1)
    elif isinstance(o, list):
        print("  "*depth + f"{path} list len={len(o)}")
        if o: walk(o[0], path+"[0]", depth+1)
    else:
        s = str(o)
        print("  "*depth + f"{path} = {s[:70]!r}")

walk(d)
print("\n--- first record, full ---")
# find the list of records
def find_list(o):
    if isinstance(o, list) and o and isinstance(o[0], dict): return o
    if isinstance(o, dict):
        for v in o.values():
            f = find_list(v)
            if f: return f
    return None
recs = find_list(d)
print("record count:", len(recs) if recs else 0)
if recs: print(json.dumps(recs[0], indent=2)[:1200])