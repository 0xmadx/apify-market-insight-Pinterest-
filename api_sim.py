"""A local stand-in for the Apify API — so you can see what a customer sees.

    .venv/Scripts/python.exe api_sim.py            # http://localhost:8080

Two things it gives you:

  * a **browser playground** at `/` — pick an operation, fill the same fields
    the Apify form shows, press Run, read the records
  * the **real Apify endpoint shape** at
    `POST /v2/acts/pinterest-trends/run-sync-get-dataset-items`, which is the
    call an integrator actually writes. Point their code at localhost, then at
    Apify later, and only the host changes.

It runs the REAL traversals — `src/scraper.py`, the same code the actor runs.
Nothing here re-implements the product, because a simulation that drifts from
the thing it simulates is worse than no simulation.

TWO MODES, and the page always says which one you are in:

  LIVE   a Pinterest session is in the vault → real requests, live data
  DEMO   no session → the committed fixtures answer instead, so the shapes,
         fields and record counts are real even though the numbers are from
         a captured run. Labelled `"_demo": true` on every record, because a
         customer must never mistake replayed data for fresh data.

Stdlib only — no new dependencies to install or explain.
"""
import json
import sys
import threading
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

sys.path.insert(0, ".")
sys.path.insert(0, "tests")

from src import scraper  # noqa: E402
from src.config import Config  # noqa: E402
from src.session import leased_session  # noqa: E402
from src.transport import TrendsClient  # noqa: E402
from src.vault import SessionVault, VaultEmpty  # noqa: E402

PORT = 8080
ACTOR = "pinterest-trends"


# --------------------------------------------------------------------- modes

def vault_is_live():
    """A cheap read-only check. Never leases — that would steal the profile
    from a real run for no reason."""
    try:
        vault = SessionVault(Config())
        return any(r["cookie_count"] and (r["age_seconds"] or 1e9) < 900
                   for r in vault.describe())
    except Exception:
        return False


class DemoCtx:
    """Replays committed fixtures through the real traversals."""

    def __init__(self, task):
        self.task = task
        self.cache = None
        self.force_refresh = False
        self.session = None


def run_demo(task):
    from tests.test_dispatch import EveryFixture
    client = EveryFixture()
    original = scraper.TrendsClient
    scraper.TrendsClient = lambda *a, **k: client
    try:
        records = [r.data for r in scraper.run(DemoCtx(task), task)]
    finally:
        scraper.TrendsClient = original
    for record in records:
        record["_demo"] = True
    return records, len(client.all_calls)


def run_live(task):
    class Ctx:
        def __init__(self, session, task):
            self.session = session
            self.task = task
            self.cache = None
            self.force_refresh = bool(task.get("forceRefresh"))

    with leased_session() as (session, identity):
        client = TrendsClient(session)
        original = scraper.TrendsClient
        scraper.TrendsClient = lambda *a, **k: client
        try:
            records = [r.data for r in scraper.run(Ctx(session, task), task)]
        finally:
            scraper.TrendsClient = original
        return records, client.request_count


def execute(task):
    """Live when a session exists, fixtures otherwise. Always reports which."""
    if vault_is_live():
        try:
            records, requests = run_live(task)
            return {"mode": "LIVE", "records": records, "requests": requests}
        except VaultEmpty:
            pass          # went stale between the check and the lease
    records, requests = run_demo(task)
    return {"mode": "DEMO", "records": records, "requests": requests}


# ---------------------------------------------------------------------- page

PAGE = """<!doctype html><meta charset=utf-8>
<title>Pinterest Trends — API playground</title>
<style>
 :root{--bg:#fff;--fg:#1a1a1a;--mut:#666;--line:#e3e3e3;--accent:#b8003f;--card:#fafafa}
 @media(prefers-color-scheme:dark){:root{--bg:#161618;--fg:#eee;--mut:#999;--line:#2e2e32;--accent:#ff5c8a;--card:#1e1e21}}
 *{box-sizing:border-box}
 body{margin:0;background:var(--bg);color:var(--fg);font:14px/1.55 ui-sans-serif,system-ui,sans-serif}
 header{padding:18px 22px;border-bottom:1px solid var(--line)}
 h1{margin:0;font-size:17px} .sub{color:var(--mut);font-size:13px;margin-top:3px}
 .wrap{display:grid;grid-template-columns:340px 1fr;min-height:calc(100vh - 66px)}
 .panel{padding:18px 22px;border-right:1px solid var(--line);overflow:auto}
 .out{padding:18px 22px;overflow:auto;background:var(--card)}
 label{display:block;margin:13px 0 4px;font-size:12px;font-weight:600;letter-spacing:.02em}
 .hint{font-weight:400;color:var(--mut);font-size:11px;margin-top:2px}
 select,input,textarea{width:100%;padding:7px 9px;border:1px solid var(--line);
   border-radius:6px;background:var(--bg);color:var(--fg);font:13px ui-monospace,monospace}
 button{margin-top:16px;width:100%;padding:10px;border:0;border-radius:6px;
   background:var(--accent);color:#fff;font-weight:600;font-size:14px;cursor:pointer}
 button:disabled{opacity:.55;cursor:wait}
 pre{white-space:pre-wrap;word-break:break-word;font:12px/1.5 ui-monospace,monospace;margin:0}
 .badge{display:inline-block;padding:2px 8px;border-radius:99px;font-size:11px;font-weight:700}
 .live{background:#0a7c2f;color:#fff}.demo{background:#8a6d00;color:#fff}
 .stat{color:var(--mut);font-size:12px;margin:10px 0 14px}
 .grp{margin-top:18px;padding-top:14px;border-top:1px solid var(--line)}
 .grp>span{font-size:11px;color:var(--mut);text-transform:uppercase;letter-spacing:.08em}
 code{background:var(--card);padding:1px 5px;border-radius:4px;font-size:12px}
</style>
<header>
  <h1>Pinterest Trends — API playground</h1>
  <div class=sub>The same code the Apify actor runs. This is what your customer sees.</div>
</header>
<div class=wrap>
<div class=panel>
  <label>Operation
    <div class=hint>Each one is a different question. All work with no other input.</div>
  </label>
  <select id=op onchange=fields()>
    <option value=radar>radar — what Pinterest is featuring (2 requests)</option>
    <option value=keywords>keywords — trending search terms + forecast</option>
    <option value=shopping selected>shopping — trending product categories</option>
    <option value=moments>moments — seasonal timing</option>
  </select>

  <label>Region</label>
  <select id=region>
    <option>US</option><option>CA</option><option>GB+IE</option><option>DE</option>
    <option>FR</option><option>BR</option><option>AU+NZ</option><option>JP</option>
  </select>

  <label>Max records <div class=hint>0 = no limit</div></label>
  <input id=maxRecords type=number value=8 min=0>

  <div id=extra></div>
  <button id=go onclick=run()>Run</button>
  <div class=grp><span>the same call, in code</span>
    <pre id=curl style="margin-top:8px;color:var(--mut)"></pre>
  </div>
</div>
<div class=out>
  <div id=stat class=stat>Press Run.</div>
  <pre id=res></pre>
</div>
</div>
<script>
const EXTRA = {
 shopping:`<label>Verticals <div class=hint>empty = the 3 Pinterest's UI shows. 1148/1016/1500/1315 are hidden from their interface but served by the API.</div></label>
  <input id=verticals placeholder="1042  (Beauty)">
  <label>Drill top N <div class=hint>how many categories get audience + products</div></label><input id=drillTopN type=number value=1 min=0>
  <label>Fetch price + merchant link <div class=hint>per drilled category. 1 extra request PER product — start small.</div></label><input id=enrichTopN type=number value=2 min=0>`,
 keywords:`<label>Mode</label><select id=mode><option value=discover>discover — Pinterest's trending set</option><option value=exact>exact — my own terms</option><option value=seed>seed — expand a stem</option></select>
  <label>Terms / stem <div class=hint>comma separated. Case is fixed for you.</div></label><input id=queries placeholder="boho wall art, macrame">
  <label>Must contain <div class=hint>scopes discovery to your niche</div></label><input id=keywordsToInclude placeholder="wall art">
  <label>Max terms to enrich</label><input id=maxTerms type=number value=5 min=0>`,
 moments:`<label>Granularity <div class=hint>daily is unique to moments — nothing else resolves below weekly</div></label>
  <select id=aggregation><option>weekly</option><option>daily</option><option>monthly</option></select>
  <label>Interest breakdown <div class=hint>ids, comma separated. 918530398158 = Food and Drinks — an audience Pinterest's own dropdown will not show you for most moments.</div></label>
  <input id=interestIds placeholder="918530398158">`,
 radar:`<label>Interest filter <div class=hint>one id, or empty for all</div></label><input id=interest placeholder="">`,
};
function fields(){document.getElementById('extra').innerHTML=EXTRA[op.value]||'';curl();}
function body(){
  const t={operation:op.value,region:region.value,maxRecords:+maxRecords.value};
  const get=id=>{const e=document.getElementById(id);return e&&e.value.trim()?e.value.trim():null};
  const list=id=>{const v=get(id);return v?v.split(',').map(s=>s.trim()).filter(Boolean):null};
  for(const [k,f] of Object.entries({verticals:list,queries:list,keywordsToInclude:list,interestIds:list})){
    const v=f(k); if(v) t[k]=v;
  }
  for(const k of ['mode','aggregation','interest']){const v=get(k); if(v) t[k]=v;}
  for(const k of ['drillTopN','enrichTopN','maxTerms']){const v=get(k); if(v!==null) t[k]=+v;}
  return t;
}
function curl(){document.getElementById('curl').textContent=
 `curl -X POST localhost:8080/v2/acts/${'{{ACTOR}}'}/run-sync-get-dataset-items \\\\\n  -H 'content-type: application/json' \\\\\n  -d '`+JSON.stringify(body())+`'`;}
['op','region','maxRecords'].forEach(i=>document.getElementById(i).addEventListener('input',curl));
document.addEventListener('input',curl);
async function run(){
  go.disabled=true; stat.textContent='running…'; res.textContent='';
  try{
    const r=await fetch(`/v2/acts/${'{{ACTOR}}'}/run-sync-get-dataset-items`,
      {method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify(body())});
    const j=await r.json();
    if(j.error){stat.textContent='';res.textContent='ERROR: '+j.error+'\\n\\n'+(j.detail||'');}
    else{
      const b=j.mode==='LIVE'?'<span class="badge live">LIVE</span>':'<span class="badge demo">DEMO — replayed fixtures, real shapes</span>';
      stat.innerHTML=`${b} &nbsp; ${j.items.length} records · ${j.requests} requests to Pinterest`;
      res.textContent=JSON.stringify(j.items,null,2);
    }
  }catch(e){stat.textContent='';res.textContent=String(e);}
  go.disabled=false;
}
fields();
</script>
"""


# ------------------------------------------------------------------- server

class Handler(BaseHTTPRequestHandler):
    def _send(self, code, payload, ctype="application/json"):
        raw = payload if isinstance(payload, bytes) else \
            (json.dumps(payload, indent=2).encode() if ctype.startswith("application/json")
             else payload.encode())
        self.send_response(code)
        self.send_header("content-type", ctype + "; charset=utf-8")
        self.send_header("content-length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        path = urlparse(self.path).path
        if path in ("/", "/index.html"):
            return self._send(200, PAGE.replace("{{ACTOR}}", ACTOR), "text/html")
        if path == "/health":
            return self._send(200, {"ok": True,
                                    "mode": "LIVE" if vault_is_live() else "DEMO"})
        self._send(404, {"error": "not found"})

    def do_POST(self):
        path = urlparse(self.path).path
        # The real Apify one-shot endpoint. An integrator writes this exact
        # call; only the host changes when they move to the cloud.
        if not path.startswith(f"/v2/acts/{ACTOR}/"):
            return self._send(404, {"error": "unknown actor",
                                    "hint": f"use /v2/acts/{ACTOR}/run-sync-get-dataset-items"})
        try:
            length = int(self.headers.get("content-length") or 0)
            task = json.loads(self.rfile.read(length) or b"{}")
        except Exception as exc:
            return self._send(400, {"error": "input is not valid JSON",
                                    "detail": str(exc)})
        try:
            out = execute(task)
        except Exception as exc:
            # Surface the real reason — a customer-facing API that says
            # "something went wrong" teaches nobody anything.
            return self._send(400, {"error": f"{type(exc).__name__}: {exc}",
                                    "detail": traceback.format_exc()[-900:]})
        self._send(200, {"mode": out["mode"], "requests": out["requests"],
                         "items": out["records"]})

    def log_message(self, fmt, *args):
        sys.stderr.write("  %s\n" % (fmt % args))


def main():
    mode = "LIVE (a Pinterest session is in the vault)" if vault_is_live() \
        else "DEMO (no session — committed fixtures will answer)"
    print(f"\n  Pinterest Trends API playground")
    print(f"  mode: {mode}")
    print(f"\n  open  http://localhost:{PORT}")
    print(f"  api   POST http://localhost:{PORT}/v2/acts/{ACTOR}/run-sync-get-dataset-items\n")
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
