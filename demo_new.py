#!/usr/bin/env python3
"""
Self-Healing Supply Chain - LOCAL DEMO (single file)
====================================================

Put this file in the PROJECT ROOT (the folder that contains `app/`, `frontend/`,
`data/` and `requirements.txt`), then run:

    python -m pip install -r requirements.txt
    python demo.py

It opens http://127.0.0.1:8000/ui in your browser.

What it does
------------
* Runs your REAL FastAPI app (`app.main:app`) with your real engine, seed data,
  SQLite layer and scraper. Nothing in the codebase is edited.
* The preview dashboard at /ui talks only to your real endpoints:
    /seed/summary  /products  /countries  /graph/edges  /graph/dependencies
    /simulate      /events    /search     /supplier     /suppliers
    /suppliers/{id}/verification          /scrape/sample  /scrape
* Also serves your original, unmodified `frontend/` at /frontend/ so you can see
  exactly what will be uploaded to S3. (Its "API base URL" box defaults to
  http://127.0.0.1:8000 - keep the demo on port 8000 for it to connect.)
* Uses an ISOLATED throw-away SQLite file in your OS temp folder (via the
  DATABASE_PATH variable your app already supports), so `data/supply_chain.db`
  is never touched. It is re-seeded with demo suppliers on every start.
* Adds only three small routes (/ui, /demo/status, /demo/reseed) and a static
  mount to the app object in memory.

Options
-------
    python demo.py --port 8080      choose a port
    python demo.py --no-browser     do not auto-open the browser
    python demo.py --keep-data      keep the demo DB from the previous run
"""
from __future__ import annotations

import argparse
import contextlib
import importlib
import os
import socket
import sys
import tempfile
import threading
import webbrowser
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
if not (HERE / "app" / "main.py").exists():
    sys.exit(
        "demo.py must be placed in the project root (the folder that contains app/, "
        "frontend/ and requirements.txt)."
    )
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

# Must be set BEFORE the app touches the database, so your real DB is never used.
DEMO_DB = Path(tempfile.gettempdir()) / "self_healing_supply_chain_demo.db"
os.environ["DATABASE_PATH"] = str(DEMO_DB)

# ---------------------------------------------------------------------------
# Demo supplier catalog (loaded through your real upsert_supplier / set_verified)
# ---------------------------------------------------------------------------

PUBLIC = "Unverified - public data"
SELF_REG = "Self-registered - unverified"

# (company, country, region, product_id, product name, category, hs, capacity,
#  spare, unit price, lead days, certifications, state)
# state: "verified" | "self" | "public"
_SUPPLIERS: list[tuple[Any, ...]] = [
    ("Rhein MedTech GmbH", "Germany", "Stuttgart", "MED-KIT", "Emergency Medical Kit", "Medical", "3006", 1500, 800, 38.0, 9, ["ISO 13485", "CE"], "verified"),
    ("Pune LifeCare Pvt Ltd", "India", "Pune", "MED-KIT", "Emergency Medical Kit", "Medical", "3006", 1200, 600, 33.0, 7, ["ISO 13485"], "verified"),
    ("Shenzhen Rescue Gear Co", "China", "Shenzhen", "MED-KIT", "Emergency Medical Kit", "Medical", "3006", 3000, 2000, 22.0, 14, [], "self"),
    ("Hanoi MedPack JSC", "Vietnam", "Hanoi", "MED-KIT", "Emergency Medical Kit", "Medical", "3006", 900, 400, 27.0, 12, [], "public"),
    ("Chennai Pump Works", "India", "Chennai", "FUEL-PUMP", "Fuel Pump", "Energy", "8413", 2000, 1000, 24.0, 7, ["ISO 9001"], "verified"),
    ("Aichi Fluid Systems", "Japan", "Nagoya", "FUEL-PUMP", "Fuel Pump", "Energy", "8413", 800, 300, 41.0, 10, ["ISO 9001"], "verified"),
    ("Guangzhou Pump Co", "China", "Guangzhou", "FUEL-PUMP", "Fuel Pump", "Energy", "8413", 2500, 1500, 18.0, 15, [], "self"),
    ("Monterrey Motores SA", "Mexico", "Monterrey", "FUEL-PUMP", "Fuel Pump", "Energy", "8413", 1100, 500, 29.0, 8, [], "self"),
    ("Penang AquaTech", "Malaysia", "Penang", "WATER-PUR", "Water Purifier", "Water", "8421", 1000, 450, 52.0, 11, ["NSF 42"], "verified"),
    ("Eindhoven Filtration BV", "Netherlands", "Eindhoven", "WATER-PUR", "Water Purifier", "Water", "8421", 700, 300, 61.0, 8, ["NSF 42"], "verified"),
    ("Sao Paulo Aqua Ltda", "Brazil", "Sao Paulo", "WATER-PUR", "Water Purifier", "Water", "8421", 1300, 600, 47.0, 16, [], "self"),
    ("Seoul PowerConv", "South Korea", "Seoul", "SOLAR-INV", "Solar Inverter", "Energy", "8504", 900, 400, 118.0, 12, ["UL 1741"], "verified"),
    ("Hsinchu Inverters Ltd", "Taiwan", "Hsinchu", "SOLAR-INV", "Solar Inverter", "Energy", "8504", 1500, 700, 96.0, 13, [], "self"),
    ("Bavaria Solar Electronics", "Germany", "Munich", "SOLAR-INV", "Solar Inverter", "Energy", "8504", 600, 250, 125.0, 9, [], "public"),
    ("Bangkok Staples Co", "Thailand", "Bangkok", "FOOD-PACK", "Staple Food Pack", "Food", "2106", 5000, 3000, 6.5, 10, ["HACCP"], "verified"),
    ("Jakarta Grain Trading", "Indonesia", "Jakarta", "FOOD-PACK", "Staple Food Pack", "Food", "2106", 4000, 2000, 5.9, 14, [], "self"),
    ("Rotterdam Provisions BV", "Netherlands", "Rotterdam", "FOOD-PACK", "Staple Food Pack", "Food", "2106", 2500, 1200, 8.2, 6, ["HACCP"], "verified"),
]


def _db():
    return importlib.import_module("app.database")


def seed_database(wipe: bool = True) -> None:
    """(Re)create the demo DB and load demo suppliers via your real DB helpers."""
    database = _db()
    # Safety: only ever wipe the isolated demo database.
    if Path(database.db_path()).resolve() != DEMO_DB.resolve():
        raise RuntimeError("Refusing to modify a database that is not the demo DB.")
    database.init_db()
    if wipe:
        with contextlib.closing(database.connect()) as con:
            with con:
                for table in ("catalog_items", "suppliers", "events", "audit_log"):
                    con.execute(f"DELETE FROM {table}")
    for (company, country, region, pid, pname, cat, hs, cap, spare, price, lead, certs, state) in _SUPPLIERS:
        payload = {
            "company_name": company,
            "country": country,
            "region": region,
            "certifications": certs,
            "export_countries": [country],
        }
        item = {
            "item_id": pid,
            "product_name": pname,
            "category": cat,
            "hs_code": hs,
            "capacity": cap,
            "spare_capacity": spare,
            "unit_price": price,
            "currency": "USD",
            "lead_time_days": lead,
            "certifications": certs,
            "priority": "P2",
        }
        label = PUBLIC if state == "public" else SELF_REG
        supplier_id, _ = database.upsert_supplier(payload, [item], source_label=label)
        if state == "verified":
            database.set_verified(supplier_id, True)  # same call PATCH /suppliers/{id}/verification makes


def db_status() -> dict[str, Any]:
    database = _db()
    database.init_db()
    with contextlib.closing(database.connect()) as con:
        suppliers = con.execute("SELECT COUNT(*) AS n FROM suppliers").fetchone()["n"]
        items = con.execute("SELECT COUNT(*) AS n FROM catalog_items").fetchone()["n"]
        events = con.execute("SELECT COUNT(*) AS n FROM events").fetchone()["n"]
        by_label = {
            r["label"]: r["n"]
            for r in con.execute(
                "SELECT CASE WHEN verified=1 THEN 'Verified' ELSE source_label END AS label, "
                "COUNT(*) AS n FROM suppliers GROUP BY 1"
            )
        }
    return {
        "suppliers": suppliers,
        "catalog_items": items,
        "events": events,
        "by_label": by_label,
        "db_path": str(DEMO_DB),
    }


# ---------------------------------------------------------------------------
# Web app: your real app.main:app + demo routes
# ---------------------------------------------------------------------------

def build_app():
    from fastapi.responses import HTMLResponse
    from fastapi.staticfiles import StaticFiles

    from app.main import app  # your real FastAPI app with all real endpoints

    @app.get("/ui", response_class=HTMLResponse, include_in_schema=False)
    def ui() -> str:
        return DASHBOARD_HTML

    @app.get("/demo/status", tags=["demo"])
    def demo_status() -> dict[str, Any]:
        return db_status()

    @app.post("/demo/reseed", tags=["demo"])
    def demo_reseed() -> dict[str, Any]:
        seed_database(wipe=True)
        return db_status()

    frontend_dir = HERE / "frontend"
    if frontend_dir.is_dir():
        # Your original dashboard, served unchanged.
        app.mount("/frontend", StaticFiles(directory=str(frontend_dir), html=True), name="frontend")
    return app


# ---------------------------------------------------------------------------
# Dashboard (single inline page, no external CDN - works offline)
# ---------------------------------------------------------------------------

DASHBOARD_HTML = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Self-Healing Supply Chain - Local Demo</title>
<style>
:root{
  --bg:#0e131f; --panel:#161d2e; --panel2:#1c2538; --line:#2a3550; --text:#e7ecf6; --muted:#8c9ab6;
  --ok:#3ecf8e; --bad:#ff5d6c; --warn:#ffb020; --info:#4c9aff; --accent:#8f7cff; --tar:#e879f9;
}
*{box-sizing:border-box}
html,body{margin:0;background:var(--bg);color:var(--text);font:14px/1.45 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}
a{color:#a99bff}
header{display:flex;justify-content:space-between;align-items:center;gap:16px;flex-wrap:wrap;padding:14px 22px;border-bottom:1px solid var(--line);background:#111827}
.brand{display:flex;gap:12px;align-items:center}
.logo{font-size:26px}
h1{font-size:17px;margin:0}
.brand p{margin:0;color:var(--muted);font-size:12px}
h2{font-size:14px;margin:0}
h3{font-size:12px;margin:14px 0 6px;color:var(--muted);text-transform:uppercase;letter-spacing:.06em}
code{background:#0f1626;padding:1px 5px;border-radius:5px;font-size:12px}
main{display:grid;grid-template-columns:minmax(0,1.55fr) minmax(400px,1fr);gap:16px;padding:16px 22px;align-items:start}
@media (max-width:1150px){main{grid-template-columns:1fr}.sticky{position:static!important}}
.sticky{position:sticky;top:12px}
.card{background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:14px}
.card + .card{margin-top:12px}
.card-head{display:flex;justify-content:space-between;align-items:center;margin-bottom:8px;gap:8px;flex-wrap:wrap}
.muted{color:var(--muted)} .small{font-size:12px}
.chips{display:flex;gap:6px;flex-wrap:wrap;align-items:center}
.chip{display:inline-block;padding:2px 9px;border-radius:999px;background:var(--panel2);border:1px solid var(--line);font-size:12px}
.chip.bad{border-color:var(--bad);color:#ffb3bb;background:#2b1620}
.chip.warn{border-color:var(--warn);color:#ffd98a;background:#2b2210}
.chip.ok{border-color:var(--ok);color:#a6f0cf;background:#10281f}
.badge{display:inline-block;font-size:11px;padding:1px 7px;border-radius:6px;border:1px solid;white-space:nowrap}
.b-ok{color:#a6f0cf;border-color:#2c7a58;background:#10281f}
.b-self{color:#ffd98a;border-color:#8a6a1c;background:#2b2210}
.b-pub{color:#c9d2e6;border-color:#4a5675;background:#1f2638}
.b-bad{color:#ffb3bb;border-color:#8a2c3a;background:#2b1620}
.b-p1{color:#ffb3bb;border-color:#8a2c3a;background:#2b1620}
.b-p2{color:#ffd98a;border-color:#8a6a1c;background:#2b2210}
.b-p3{color:#c9d2e6;border-color:#4a5675;background:#1f2638}
.tog{font-size:12px;color:var(--muted);display:flex;gap:6px;align-items:center;cursor:pointer}
.tog input{flex:0 0 auto;width:15px;height:15px;min-width:0;padding:0}
.ptabs{display:flex;gap:6px;flex-wrap:wrap;margin-bottom:10px}
.ptab{padding:6px 10px;border-radius:9px;border:1px solid var(--line);background:var(--panel2);color:var(--muted);cursor:pointer;font:inherit;font-size:12.5px;font-weight:600}
.ptab.on{background:var(--accent);border-color:var(--accent);color:#0b1020}
.ptab .n{margin-left:6px;padding:0 6px;border-radius:999px;background:#2b1620;color:#ffb3bb;font-size:11px}
.ptab.on .n{background:#0b1020;color:#ffb3bb}
#graphwrap{overflow:auto;border:1px solid var(--line);border-radius:10px;background:#0f1626}
svg{display:block;width:100%;height:auto;min-width:820px}
.legend{display:flex;gap:14px;flex-wrap:wrap;margin-top:10px;font-size:12px;color:var(--muted)}
.legend i{display:inline-block;width:14px;height:10px;border-radius:3px;margin-right:5px;vertical-align:-1px;border:2px solid}
.edgeinfo{margin-top:10px;padding:10px;border-radius:8px;background:var(--panel2);border:1px solid var(--line);font-size:12.5px;min-height:40px}
.node rect{fill:var(--panel2);stroke:#3a4870;stroke-width:1.3}
.node.hot rect{fill:#3a1820;stroke:var(--bad);stroke-width:2}
.node.alt rect{fill:#12302a;stroke:var(--ok);stroke-width:2}
.node text{fill:var(--text);pointer-events:none}
.node .t1{font-size:11px;font-weight:600}
.node .t2{font-size:9.5px;fill:var(--muted)}
.lh{font-size:11px;fill:#8c9ab6;font-weight:700;text-anchor:middle}
.edge{fill:none;stroke-linecap:round}
.e-ok{stroke:#5a6b92;stroke-width:1;opacity:.28}
.e-dis{stroke:var(--bad);stroke-width:2.2;stroke-dasharray:6 4}
.e-deg{stroke:var(--warn);stroke-width:2.2;stroke-dasharray:6 4}
.e-tar{stroke:var(--tar);stroke-width:2.2}
.e-alloc{stroke:var(--ok);stroke-width:2.4}
.e-trace{stroke:var(--info);stroke-width:1.8;opacity:.9}
.e-sel{stroke-width:4!important;opacity:1!important}
.edge.dim{opacity:.07!important}
.hit{fill:none;stroke:transparent;stroke-width:9;cursor:pointer}
.elabel{font-size:10px;font-weight:700;fill:#a6f0cf;paint-order:stroke;stroke:#0f1626;stroke-width:3px;pointer-events:none}
.tabs{display:flex;gap:4px;margin-bottom:12px}
.tab{flex:1;padding:8px 6px;border-radius:8px;border:1px solid var(--line);background:var(--panel);color:var(--muted);cursor:pointer;font:inherit;font-weight:600;font-size:13px}
.tab.on{background:var(--accent);border-color:var(--accent);color:#0b1020}
.pane{display:none} .pane.on{display:block}
.row{display:flex;gap:8px;flex-wrap:wrap;align-items:flex-end;margin-bottom:8px}
.row > label{display:flex;flex-direction:column;gap:3px;font-size:11.5px;color:var(--muted);flex:1 1 120px;min-width:0}
select,input{background:#0f1626;color:var(--text);border:1px solid var(--line);border-radius:8px;padding:7px 9px;font:inherit;min-width:0;width:100%}
.btn{background:var(--accent);color:#0b1020;border:0;border-radius:8px;padding:7px 12px;cursor:pointer;font:inherit;font-weight:700}
.btn.sec{background:var(--panel2);color:var(--text);border:1px solid var(--line)}
.btn.ok{background:var(--ok)} .btn.bad{background:var(--bad)}
.btn.sm{padding:4px 9px;font-size:12px}
.btn:disabled{opacity:.5;cursor:default}
.scen{display:flex;gap:6px;flex-wrap:wrap;margin:8px 0}
.kpis{display:grid;grid-template-columns:repeat(3,1fr);gap:8px;margin:10px 0}
.kpi{background:var(--panel2);border:1px solid var(--line);border-radius:10px;padding:9px}
.kpi b{font-size:20px;display:block}
.kpi span{font-size:11.5px;color:var(--muted)}
.bar{height:8px;border-radius:6px;background:#2b1620;overflow:hidden;margin-top:6px}
.bar i{display:block;height:100%;background:var(--ok)}
.reclist{max-height:300px;overflow:auto;border:1px solid var(--line);border-radius:10px}
.rec{display:flex;justify-content:space-between;gap:8px;align-items:center;padding:7px 10px;border-bottom:1px solid var(--line);cursor:pointer;font-size:12.5px}
.rec:last-child{border-bottom:0}
.rec:hover{background:var(--panel2)}
.rec.on{background:#232d4a}
.detail{margin-top:10px;padding:10px;border:1px solid var(--line);border-radius:10px;background:var(--panel2)}
.warnbox{margin-top:8px;padding:8px;border-radius:8px;background:#2b2210;border:1px solid #8a6a1c;color:#ffd98a;font-size:12.5px}
.infobox{margin-top:8px;padding:8px;border-radius:8px;background:#14233d;border:1px solid #2c4f86;color:#bcd6ff;font-size:12.5px}
table{width:100%;border-collapse:collapse;font-size:12.5px}
th,td{text-align:left;padding:5px 6px;border-bottom:1px solid var(--line);vertical-align:top}
th{color:var(--muted);font-weight:600}
pre{background:#0f1626;border:1px solid var(--line);border-radius:8px;padding:10px;overflow:auto;font-size:12px;max-height:220px}
#toast{position:fixed;right:18px;bottom:18px;max-width:380px;background:#1d2a45;border:1px solid var(--accent);color:var(--text);padding:10px 14px;border-radius:10px;opacity:0;transform:translateY(8px);transition:.2s;pointer-events:none;z-index:9}
#toast.show{opacity:1;transform:none}
#toast.err{border-color:var(--bad);background:#2b1620}
.hide{display:none!important}
</style>
</head>
<body>
<header>
  <div class="brand"><span class="logo">&#9939;</span>
    <div><h1>Self-Healing Supply Chain</h1>
    <p>Local preview &middot; your real app, engine and API &middot; isolated demo database</p></div>
  </div>
  <div class="chips" id="hdr"></div>
</header>

<main>
  <section class="sticky">
    <div class="card">
      <div class="card-head"><h2>Supply network <span class="muted small" id="gsub"></span></h2>
        <label class="tog"><input type="checkbox" id="lbl" checked> allocation labels</label></div>
      <div class="ptabs" id="ptabs"></div>
      <div id="graphwrap"><svg id="svg" role="img" aria-label="Supply network graph"></svg></div>
      <div class="legend">
        <span><i style="border-color:#3a4870;background:#1c2538"></i>facility</span>
        <span><i style="border-color:var(--bad);background:#3a1820"></i>in disrupted country</span>
        <span><i style="border-color:var(--ok);background:#12302a"></i>recommended alternate supplier</span>
        <span style="color:var(--bad)">&#9473;&#9473; disrupted lane</span>
        <span style="color:var(--warn)">&#9473;&#9473; degraded</span>
        <span style="color:var(--tar)">&#9473;&#9473; tariffed</span>
        <span style="color:var(--ok)">&#9473;&#9473; allocated alternate</span>
        <span style="color:var(--info)">&#9473;&#9473; traced upstream</span>
      </div>
      <div id="edgeinfo" class="edgeinfo muted">Click any lane to inspect it. Pick a scenario on the right to simulate a disruption.</div>
    </div>
  </section>

  <aside>
    <div class="tabs">
      <button class="tab on" data-tab="sim">Disrupt &amp; heal</button>
      <button class="tab" data-tab="trace">Upstream trace</button>
      <button class="tab" data-tab="cat">Suppliers</button>
      <button class="tab" data-tab="evt">Events</button>
    </div>

    <!-- ===== Disrupt & heal ===== -->
    <div id="pane-sim" class="pane on">
      <div class="card">
        <h2>1 &middot; Simulate a disruption <span class="muted small">POST /simulate</span></h2>
        <p class="muted small" style="margin:4px 0 6px">Pure what-if: it never changes the live graph. Pick a scenario or build your own.</p>
        <div class="scen" id="scen"></div>
        <div class="row">
          <label>Event type<select id="evType">
            <option value="war">War / route blocked</option>
            <option value="country_block">Country block</option>
            <option value="tariff">Tariff</option>
            <option value="sanction">Sanction</option>
            <option value="embargo">Embargo</option>
            <option value="disaster">Natural disaster</option>
            <option value="chokepoint_closure">Chokepoint closure</option>
          </select></label>
          <label data-for="war country_block tariff sanction embargo disaster">Country<select id="evCountry"></select></label>
          <label data-for="tariff sanction embargo">Trade partner<select id="evTarget"></select></label>
        </div>
        <div class="row">
          <label data-for="chokepoint_closure">Chokepoint<select id="evChoke"></select></label>
          <label data-for="tariff">Tariff rate<input id="evTariff" type="number" min="0" max="10" step="0.05" value="0.5"></label>
          <label data-for="disaster">Severity (0-1)<input id="evSeverity" type="number" min="0" max="1" step="0.1" value="0.7"></label>
          <label>Demand per lane<input id="demand" type="number" min="0" value="500"></label>
          <button class="btn bad" id="runBtn">Run simulation</button>
          <button class="btn sec" id="clearBtn">Clear</button>
        </div>
        <div id="kpis"></div>
      </div>

      <div class="card">
        <div class="card-head"><h2>2 &middot; Healing recommendations <span class="muted small">for <span id="recProd"></span></span></h2>
          <select id="recFilter" style="width:auto"><option value="all">all lanes</option><option value="unmet">needs attention</option><option value="healed">healed only</option></select></div>
        <p class="muted small" style="margin:0 0 8px">Ranked by cost, lead time, capacity fit and risk. Partial and unmet quantity is reported explicitly; recommendations require human approval.</p>
        <div id="recs"><span class="muted">Run a simulation to see recommendations.</span></div>
        <div id="recDetail"></div>
        <div class="row" style="margin-top:10px"><button class="btn sec sm" id="saveEvt">Save scenario as event (POST /events)</button></div>
      </div>
    </div>

    <!-- ===== Upstream trace ===== -->
    <div id="pane-trace" class="pane">
      <div class="card">
        <h2>Upstream dependency tracer <span class="muted small">GET /graph/dependencies</span></h2>
        <p class="muted small" style="margin:4px 0 10px">Traces every lane that supplies an item and everything feeding those facilities, for the product selected above.</p>
        <div class="row"><label>Item<select id="traceItem"></select></label>
          <button class="btn" id="traceBtn">Trace</button>
          <button class="btn sec" id="clearTraceBtn">Clear</button></div>
        <div id="traceOut"></div>
      </div>
    </div>

    <!-- ===== Suppliers ===== -->
    <div id="pane-cat" class="pane">
      <div class="card">
        <h2>Search the supplier catalog <span class="muted small">GET /search</span></h2>
        <div class="row" style="margin-top:8px">
          <label>Product<input id="sp" placeholder="Fuel Pump"></label>
          <label>HS code<input id="sh" placeholder="8413"></label>
          <label>Country<input id="sc" placeholder="India"></label>
          <label>Category<input id="sk" placeholder="Energy"></label>
        </div>
        <div class="row">
          <label>Min spare capacity<input id="smin" type="number" min="0" placeholder="500"></label>
          <label>Max lead time (d)<input id="slt" type="number" min="0" placeholder="10"></label>
          <label>Certification<input id="scert" placeholder="ISO"></label>
          <label class="tog" style="flex:0 0 auto;flex-direction:row;align-items:center"><input type="checkbox" id="sver"> verified only</label>
          <button class="btn" id="searchBtn">Search</button>
        </div>
        <div id="searchOut" class="muted small">Run a search to see results.</div>
      </div>
      <div class="card">
        <h2>Register a supplier <span class="muted small">POST /supplier</span></h2>
        <div class="row" style="margin-top:8px">
          <label>Company<input id="rc" value="Demo Backup Components Ltd"></label>
          <label>Country<input id="rk" value="India"></label>
          <label>Region<input id="rr" value="Pune"></label>
        </div>
        <div class="row">
          <label>Product<input id="rp" value="Fuel Pump"></label>
          <label>Category<input id="rcat" value="Energy"></label>
          <label>HS<input id="rh" value="8413"></label>
        </div>
        <div class="row">
          <label>Capacity<input id="rcap" type="number" value="1000"></label>
          <label>Spare<input id="rsp" type="number" value="500"></label>
          <label>Price<input id="rpr" type="number" value="24"></label>
          <label>Lead days<input id="rlt" type="number" value="7"></label>
          <button class="btn" id="regBtn">Register</button>
        </div>
        <pre id="regOut" class="hide"></pre>
      </div>
      <div class="card">
        <h2>Public-directory scraper</h2>
        <p class="muted small" style="margin:4px 0 10px">Imported records are labeled <code>Unverified - public data</code>.</p>
        <div class="row"><button class="btn" id="sampleBtn">Import bundled sample (POST /scrape/sample)</button>
          <button class="btn sec" id="reseedBtn">Reset demo database</button></div>
        <div class="row"><label>Live URL (needs internet; real, permitted site)<input id="url" placeholder="https://a-real-public-directory.com/suppliers"></label>
          <button class="btn sec" id="urlBtn">Scrape URL</button></div>
        <pre id="scrapeOut" class="hide"></pre>
      </div>
    </div>

    <!-- ===== Events ===== -->
    <div id="pane-evt" class="pane">
      <div class="card">
        <div class="card-head"><h2>Saved events <span class="muted small">GET /events</span></h2>
          <button class="btn sec sm" id="evtBtn">Refresh</button></div>
        <div id="evtOut" class="muted small">No events loaded.</div>
      </div>
      <div class="card">
        <h2>Original frontend &amp; API docs</h2>
        <p class="muted small" style="margin:6px 0 0">Your unmodified dashboard (the one you'd upload to S3) is served at <a href="/frontend/" target="_blank">/frontend/</a>,
        and the interactive API docs are at <a href="/docs" target="_blank">/docs</a>.</p>
      </div>
    </div>
  </aside>
</main>
<div id="toast"></div>

<script>
const $ = s => document.querySelector(s);
const esc = s => String(s == null ? '' : s).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const fmt = n => (n == null || n === '' ? '-' : Number(n).toLocaleString(undefined, {maximumFractionDigits: 2}));
const TIER_NAMES = ['Raw materials (T3)', 'Sub-parts (T2)', 'Components (T1)', 'Finished-goods facilities (T0)', 'Buyer'];
const CHOKEPOINTS = ['Suez Canal', 'Strait of Malacca', 'Panama Canal', 'Strait of Hormuz'];
const COUNTRY_EVENTS = ['war', 'country_block', 'disaster'];       // nodes in the country are highlighted
const EXCLUDE_EVENTS = ['war', 'country_block', 'disaster', 'sanction', 'embargo']; // catalog fallbacks skip that country
const SCENARIOS = [
  {label: 'War: China', ev: {type: 'war', country: 'China'}},
  {label: 'Tariff: China \u2192 India +50%', ev: {type: 'tariff', country: 'China', target_country: 'India', tariff_rate: 0.5}},
  {label: 'Disaster: Taiwan 70%', ev: {type: 'disaster', country: 'Taiwan', severity: 0.7}},
  {label: 'Malacca closed', ev: {type: 'chokepoint_closure', country: 'Global', chokepoint: 'Strait of Malacca'}},
  {label: 'Sanction: China \u2192 India', ev: {type: 'sanction', country: 'China', target_country: 'India'}},
  {label: 'Block: India', ev: {type: 'country_block', country: 'India'}},
];

let EDGES = [], EDGE_BY_ID = {}, PRODUCTS = [], COUNTRIES = [];
let product = 'MED-KIT', SIM = null, EVENT = null, TRACE = new Set(), SEL = null, D = null;
const LAYOUT = {};

const badge = (l, v) => {
  const label = v ? 'Verified' : l;
  const cls = label === 'Verified' ? 'b-ok' : (String(label).startsWith('Unverified') ? 'b-pub' : 'b-self');
  return '<span class="badge ' + cls + '">' + esc(label) + '</span>';
};
const prio = p => '<span class="badge b-' + String(p || 'P3').toLowerCase() + '">' + esc(p || 'P3') + '</span>';
const statusBadge = s => {
  const m = {healed: ['b-ok', 'healed'], partially_healed: ['b-self', 'partial'], no_alternative: ['b-bad', 'no alternative']}[s] || ['b-pub', s];
  return '<span class="badge ' + m[0] + '">' + m[1] + '</span>';
};
const short = n => {
  if (/Market-Buyer$/.test(n)) return 'Market buyer';
  const m = /-P\d-(T\d)-(F\d)$/.exec(n);
  return m ? m[1] + ' \u00b7 ' + m[2] : (n.length > 16 ? n.slice(0, 15) + '\u2026' : n);
};

let toastTimer = null;
function toast(msg, isErr) {
  const t = $('#toast'); t.textContent = msg; t.className = 'show' + (isErr ? ' err' : '');
  clearTimeout(toastTimer); toastTimer = setTimeout(() => { t.className = ''; }, 4200);
}
async function api(path, opts) {
  const r = await fetch(path, opts);
  let data = null; try { data = await r.json(); } catch (e) { /* ignore */ }
  if (!r.ok) {
    let msg = r.statusText;
    if (data && data.detail) msg = typeof data.detail === 'string' ? data.detail : JSON.stringify(data.detail);
    throw new Error(msg);
  }
  return data;
}
const send = (method, p, b) => api(p, {method, headers: {'Content-Type': 'application/json'}, body: JSON.stringify(b || {})});
const guard = fn => async (...a) => { try { await fn(...a); } catch (e) { toast(e.message || String(e), true); } };

/* ================= layout & graph ================= */
function layoutFor(pid) {
  if (LAYOUT[pid]) return LAYOUT[pid];
  const es = EDGES.filter(e => e.product_id === pid);
  const layer = {}, country = {};
  es.forEach(e => {
    layer[e.supplier] = 3 - e.item_tier; layer[e.buyer] = 4 - e.item_tier;
    country[e.supplier] = e.supplier_country; country[e.buyer] = e.buyer_country;
  });
  const cols = [[], [], [], [], []];
  Object.keys(layer).forEach(n => cols[layer[n]].push(n));
  const key = n => { const m = /-F(\d+)$/.exec(n); return m ? Number(m[1]) : 100; };
  cols.forEach(c => c.sort((a, b) => key(a) - key(b) || a.localeCompare(b)));
  const W = 118, H = 40, GX = 92, GY = 14, PX = 14, PY = 34;
  const maxN = Math.max(...cols.map(c => c.length));
  const totalH = maxN * (H + GY) - GY;
  const pos = {};
  cols.forEach((c, ci) => {
    const colH = c.length * (H + GY) - GY, top = PY + (totalH - colH) / 2;
    c.forEach((n, i) => { pos[n] = {x: PX + ci * (W + GX), y: top + i * (H + GY)}; });
  });
  return (LAYOUT[pid] = {es, pos, country, cols, W, H, width: PX * 2 + 5 * W + 4 * GX, height: PY + totalH + 14});
}

function renderGraph() {
  const L = layoutFor(product), {pos, W, H} = L;
  const tracing = TRACE.size > 0, selRec = SEL && D ? D.recById[SEL] : null;
  const selIds = new Set();
  if (selRec) { selIds.add(selRec.disrupted_edge_id); selRec.allocations.forEach(a => selIds.add(a.edge_id)); }
  const focus = tracing || selIds.size > 0;
  const rank = e => {
    if (selIds.has(e.edge_id)) return 5;
    if (D && D.alloc.has(e.edge_id)) return 4;
    if (D && D.aff.has(e.edge_id)) return 3;
    if (TRACE.has(e.edge_id)) return 2;
    return 0;
  };
  const es = L.es.slice().sort((a, b) => rank(a) - rank(b));
  const hotCountry = EVENT && COUNTRY_EVENTS.includes(EVENT.type) ? EVENT.country : null;
  const altSuppliers = new Set();
  if (D) L.es.forEach(e => { if (D.alloc.has(e.edge_id)) altSuppliers.add(e.supplier); });

  const mk = (id, c) => '<marker id="' + id + '" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="5" markerHeight="5" orient="auto"><path d="M0,0 L10,5 L0,10 z" fill="' + c + '"/></marker>';
  let out = '<defs>' + mk('m-ok', '#5a6b92') + mk('m-bad', '#ff5d6c') + mk('m-warn', '#ffb020') + mk('m-tar', '#e879f9') + mk('m-good', '#3ecf8e') + mk('m-info', '#4c9aff') + '</defs>';
  TIER_NAMES.forEach((t, i) => { out += '<text class="lh" x="' + (14 + i * (W + 92) + W / 2) + '" y="18">' + esc(t) + '</text>'; });

  es.forEach(e => {
    let cls = 'e-ok', m = 'm-ok';
    const a = D && D.aff.get(e.edge_id);
    if (TRACE.has(e.edge_id)) { cls = 'e-trace'; m = 'm-info'; }
    if (a) {
      if (a.status === 'disrupted') { cls = 'e-dis'; m = 'm-bad'; }
      else if (a.status === 'degraded') { cls = 'e-deg'; m = 'm-warn'; }
      else { cls = 'e-tar'; m = 'm-tar'; }
    }
    if (D && D.alloc.has(e.edge_id)) { cls = 'e-alloc'; m = 'm-good'; }
    const sel = selIds.has(e.edge_id) ? ' e-sel' : '';
    const dim = focus && !selIds.has(e.edge_id) && !TRACE.has(e.edge_id) ? ' dim' : '';
    const x1 = pos[e.supplier].x + W, y1 = pos[e.supplier].y + H / 2, x2 = pos[e.buyer].x, y2 = pos[e.buyer].y + H / 2;
    const dx = Math.max(30, (x2 - x1) * 0.5);
    const d = 'M' + x1 + ',' + y1 + ' C' + (x1 + dx) + ',' + y1 + ' ' + (x2 - dx) + ',' + y2 + ' ' + x2 + ',' + y2;
    out += '<g><path class="edge ' + cls + sel + dim + '" d="' + d + '" marker-end="url(#' + m + ')"/>' +
      '<path class="hit" d="' + d + '" data-id="' + esc(e.edge_id) + '"><title>' + esc(e.edge_id + ': ' + e.item_id + ' ' + e.supplier + ' \u2192 ' + e.buyer) + '</title></path>';
    if ($('#lbl').checked && selRec && D.alloc.has(e.edge_id) && selIds.has(e.edge_id)) {
      const q = selRec.allocations.filter(x => x.edge_id === e.edge_id).reduce((s, x) => s + x.quantity, 0);
      out += '<text class="elabel" x="' + ((x1 + x2) / 2) + '" y="' + ((y1 + y2) / 2 - 4) + '" text-anchor="middle">+' + fmt(q) + '</text>';
    }
    out += '</g>';
  });

  L.cols.forEach(c => c.forEach(n => {
    const p = pos[n], cn = L.country[n];
    let cls = '';
    if (hotCountry && cn === hotCountry) cls = 'hot'; else if (altSuppliers.has(n)) cls = 'alt';
    out += '<g class="node ' + cls + '" transform="translate(' + p.x + ',' + p.y + ')"><title>' + esc(n + ' - ' + cn) + '</title>' +
      '<rect width="' + W + '" height="' + H + '" rx="8"/>' +
      '<text class="t1" x="' + (W / 2) + '" y="17" text-anchor="middle">' + esc(cn) + '</text>' +
      '<text class="t2" x="' + (W / 2) + '" y="31" text-anchor="middle">' + esc(short(n)) + '</text></g>';
  }));
  const svg = $('#svg');
  svg.setAttribute('viewBox', '0 0 ' + L.width + ' ' + L.height);
  svg.innerHTML = out;
  svg.querySelectorAll('.hit').forEach(h => h.addEventListener('click', () => showEdge(EDGE_BY_ID[h.dataset.id])));
  const P = PRODUCTS.find(x => x.item_id === product);
  $('#gsub').textContent = P ? '\u00b7 ' + P.name + ' (' + P.priority + ')' : '';
}

function showEdge(e) {
  if (!e) return;
  const a = D && D.aff.get(e.edge_id), q = D && D.alloc.get(e.edge_id);
  let status = '<span class="chip">healthy</span>';
  if (a && a.status === 'disrupted') status = '<span class="chip bad">disrupted</span>';
  else if (a && a.status === 'degraded') status = '<span class="chip warn">degraded</span>';
  else if (a) status = '<span class="chip" style="border-color:var(--tar);color:#f5b8fb">tariffed</span>';
  if (q) status += ' <span class="chip ok">+' + fmt(q) + ' allocated as alternate</span>';
  const eff = a && a.effective_cost != null && a.effective_cost !== a.unit_cost ? ' \u2192 <b>' + fmt(a.effective_cost) + '</b> effective' : '';
  const avail = a && a.available_capacity != null && a.available_capacity !== a.spare_capacity ? ' (<b>' + fmt(a.available_capacity) + '</b> available)' : '';
  $('#edgeinfo').classList.remove('muted');
  $('#edgeinfo').innerHTML = '<b>' + esc(e.edge_id) + '</b> &middot; ' + esc(e.item_id) + ' ' + prio(e.priority) + ' ' + status + '<br>' +
    esc(e.supplier) + ' (' + esc(e.supplier_country) + ') &rarr; ' + esc(e.buyer) + ' (' + esc(e.buyer_country) + ')<br>' +
    '<span class="muted">route ' + esc((e.route_countries || []).join(' \u203a ')) + ' &middot; chokepoint ' + esc((e.route_chokepoints || []).join(', ') || '-') +
    ' &middot; ' + esc(e.transport_mode) + '<br>unit cost ' + fmt(e.unit_cost) + eff + ' &middot; lead ' + fmt(e.lead_time) + ' d &middot; spare capacity ' + fmt(e.spare_capacity) + avail +
    ' &middot; risk ' + fmt(e.risk) + ' &middot; ' + (e.qualified ? 'qualified' : '<span style="color:var(--bad)">not qualified</span>') + '</span>' +
    (a && a.disruption_reason ? '<br><span style="color:#ffd98a">' + esc(a.disruption_reason) + '</span>' : '');
}

/* ================= simulation ================= */
function derive() {
  if (!SIM) { D = null; return; }
  const aff = new Map(SIM.impact.affected_edges.map(e => [e.edge_id, e]));
  const alloc = new Map(), recById = {};
  const recs = SIM.healing.recommendations;
  recs.forEach(r => { recById[r.disrupted_edge_id] = r; r.allocations.forEach(a => alloc.set(a.edge_id, (alloc.get(a.edge_id) || 0) + a.quantity)); });
  const perProd = {};
  PRODUCTS.forEach(p => { perProd[p.item_id] = {aff: 0, req: 0, ful: 0, unmet: 0}; });
  SIM.impact.affected_edges.forEach(e => { if (perProd[e.product_id]) perProd[e.product_id].aff++; });
  recs.forEach(r => {
    const p = (EDGE_BY_ID[r.disrupted_edge_id] || {}).product_id;
    if (perProd[p]) { perProd[p].req += r.requested_quantity; perProd[p].ful += r.fulfilled_quantity; perProd[p].unmet += r.unmet_quantity; }
  });
  D = {aff, alloc, recById, perProd};
}

function renderProductTabs() {
  $('#ptabs').innerHTML = PRODUCTS.map(p => {
    const n = D ? D.perProd[p.item_id].aff : 0;
    return '<button class="ptab' + (p.item_id === product ? ' on' : '') + '" data-p="' + esc(p.item_id) + '">' + esc(p.name) + (n ? '<span class="n">' + n + '</span>' : '') + '</button>';
  }).join('');
  document.querySelectorAll('.ptab').forEach(b => b.addEventListener('click', () => { product = b.dataset.p; SEL = null; TRACE = new Set(); $('#traceOut').innerHTML = ''; renderAll(); }));
}

function renderKpis() {
  const box = $('#kpis');
  if (!SIM) { box.innerHTML = ''; return; }
  const h = SIM.healing, ev = SIM.impact.event;
  const recs = h.recommendations, cnt = {healed: 0, partially_healed: 0, no_alternative: 0};
  recs.forEach(r => { cnt[r.status] = (cnt[r.status] || 0) + 1; });
  const pct = h.total_requested ? Math.round(100 * h.total_fulfilled / h.total_requested) : 100;
  box.innerHTML = '<div class="kpis">' +
    '<div class="kpi"><b>' + ev.affected_edges + '</b><span>lanes affected &middot; ' + SIM.impact.products_at_risk.length + '/' + PRODUCTS.length + ' products</span></div>' +
    '<div class="kpi"><b>' + fmt(SIM.impact.disrupted_capacity) + '</b><span>disrupted capacity</span></div>' +
    '<div class="kpi"><b>' + fmt(h.total_requested) + '</b><span>quantity to re-source</span></div>' +
    '<div class="kpi"><b style="color:var(--ok)">' + fmt(h.total_fulfilled) + '</b><span>fulfilled by alternates (' + pct + '%)</span><div class="bar"><i style="width:' + pct + '%"></i></div></div>' +
    '<div class="kpi"><b style="color:' + (h.total_unmet ? 'var(--bad)' : 'var(--ok)') + '">' + fmt(h.total_unmet) + '</b><span>unmet quantity</span></div>' +
    '<div class="kpi"><b style="font-size:14px;line-height:1.7"><span class="chip ok">' + cnt.healed + ' healed</span> <span class="chip warn">' + cnt.partially_healed + ' partial</span> <span class="chip bad">' + cnt.no_alternative + ' none</span></b><span>lane outcomes</span></div></div>' +
    '<div class="muted small">' + esc(SIM.healing.note) + '</div>';
}

function renderRecs() {
  const P = PRODUCTS.find(x => x.item_id === product);
  $('#recProd').textContent = P ? P.name : '';
  const box = $('#recs'), det = $('#recDetail');
  if (!SIM) { box.innerHTML = '<span class="muted">Run a simulation to see recommendations.</span>'; det.innerHTML = ''; return; }
  const f = $('#recFilter').value;
  let recs = SIM.healing.recommendations.filter(r => (EDGE_BY_ID[r.disrupted_edge_id] || {}).product_id === product);
  const total = recs.length;
  if (f === 'unmet') recs = recs.filter(r => r.status !== 'healed');
  if (f === 'healed') recs = recs.filter(r => r.status === 'healed');
  if (!total) { box.innerHTML = '<div class="infobox">This scenario does not touch ' + esc(P ? P.name : product) + '. Pick another product tab with a red badge.</div>'; det.innerHTML = ''; return; }
  box.innerHTML = '<div class="reclist">' + (recs.map(r => {
    const e = EDGE_BY_ID[r.disrupted_edge_id];
    return '<div class="rec' + (SEL === r.disrupted_edge_id ? ' on' : '') + '" data-id="' + esc(r.disrupted_edge_id) + '"><div><b>' + esc(r.disrupted_edge_id) + '</b> ' + prio(r.priority) +
      '<br><span class="muted">' + esc(r.item_id) + ' &middot; ' + esc(e ? e.supplier_country : '') + ' &rarr; ' + esc(short(r.buyer)) + '</span></div>' +
      '<div style="text-align:right">' + statusBadge(r.status) + '<br><span class="muted">' + fmt(r.fulfilled_quantity) + ' / ' + fmt(r.requested_quantity) + '</span></div></div>';
  }).join('') || '<div class="rec muted">No lanes match this filter.</div>') + '</div>';
  box.querySelectorAll('.rec[data-id]').forEach(el => el.addEventListener('click', () => { SEL = el.dataset.id; showEdge(EDGE_BY_ID[SEL]); renderGraph(); renderRecs(); }));
  renderDetail();
}

function renderDetail() {
  const det = $('#recDetail');
  const r = SEL && D ? D.recById[SEL] : null;
  if (!r) { det.innerHTML = ''; return; }
  const pct = r.requested_quantity ? Math.round(100 * r.fulfilled_quantity / r.requested_quantity) : 100;
  det.innerHTML = '<div class="detail"><b>' + esc(r.disrupted_edge_id) + '</b> &middot; ' + esc(r.item_id) + ' &rarr; ' + esc(r.buyer) + ' ' + prio(r.priority) + ' ' + statusBadge(r.status) +
    '<div class="bar"><i style="width:' + pct + '%"></i></div><div class="muted small">fulfilled ' + fmt(r.fulfilled_quantity) + ' of ' + fmt(r.requested_quantity) + ' &middot; unmet ' + fmt(r.unmet_quantity) + '</div>' +
    (r.allocations.length ? '<table style="margin-top:8px"><tr><th>Alternate supplier</th><th>Qty</th><th>Cost</th><th>Lead</th><th>Score</th></tr>' +
      r.allocations.map(a => '<tr><td>' + esc(a.supplier) + '<br><span class="muted">' + esc(a.supplier_country) + ' &middot; ' + esc(a.edge_id) + '</span></td><td>' + fmt(a.quantity) + '</td><td>' + fmt(a.unit_cost) + '</td><td>' + fmt(a.lead_time) + ' d</td><td>' + fmt(a.score) + '</td></tr>').join('') + '</table>' +
      '<div class="muted small" style="margin-top:6px">' + esc(r.allocations[0].reason) + '</div>' : '<div class="warnbox">No qualified, healthy alternate lane exists in the supply graph for this lane.</div>') +
    (r.unmet_quantity > 0 ? '<div class="row" style="margin-top:8px"><button class="btn sm" id="fbBtn">Search supplier catalog for fallbacks</button></div><div id="fbOut"></div>' : '') + '</div>';
  const b = $('#fbBtn'); if (b) b.addEventListener('click', guard(() => catalogFallback(r)));
}

async function catalogFallback(r) {
  const pid = (EDGE_BY_ID[r.disrupted_edge_id] || {}).product_id;
  const P = PRODUCTS.find(x => x.item_id === pid);
  const d = await api('/search?product_name=' + encodeURIComponent(P.name) + '&limit=100');
  const skip = EVENT && EXCLUDE_EVENTS.includes(EVENT.type) ? String(EVENT.country).toLowerCase() : null;
  const rows = d.results.filter(x => !skip || String(x.country).toLowerCase() !== skip);
  const hidden = d.results.length - rows.length;
  $('#fbOut').innerHTML = '<div class="muted small" style="margin:8px 0 4px">GET /search?product_name=' + esc(P.name) + ' &middot; ' + rows.length + ' candidate(s)' + (hidden ? ', ' + hidden + ' hidden (supplier in ' + esc(EVENT.country) + ')' : '') + '</div>' +
    (rows.length ? '<table><tr><th>Supplier</th><th>Price</th><th>Lead</th><th>Spare cap.</th></tr>' + rows.map(x => {
      const cov = r.unmet_quantity ? Math.min(100, Math.round(100 * (x.spare_capacity || 0) / r.unmet_quantity)) : 100;
      return '<tr><td><b>' + esc(x.company_name) + '</b><br><span class="muted">' + esc(x.country) + '</span> ' + badge(x.source_label, x.verified) + '</td><td>' + esc(x.currency) + ' ' + fmt(x.unit_price) + '</td><td>' + fmt(x.lead_time_days) + ' d</td><td>' + fmt(x.spare_capacity) + '<br><span class="muted">' + cov + '% of unmet</span></td></tr>';
    }).join('') + '</table>' : '<div class="warnbox">No catalog suppliers found. Register one or import a directory in the Suppliers tab.</div>') +
    '<div class="infobox">Catalog suppliers are discovery candidates. They are not yet part of the healing engine, so they must be qualified before becoming lanes.</div>';
}

async function runSim(ev) {
  const demand = Number($('#demand').value || 0);
  const d = await send('POST', '/simulate?demand=' + demand, ev);
  SIM = d; EVENT = ev; SEL = null; TRACE = new Set(); $('#traceOut').innerHTML = '';
  derive();
  if (!D.perProd[product] || !D.perProd[product].aff) {
    const best = PRODUCTS.map(p => [p.item_id, D.perProd[p.item_id].aff]).sort((a, b) => b[1] - a[1])[0];
    if (best && best[1]) product = best[0];
  }
  renderAll();
  toast('Simulated: ' + ev.type.replace('_', ' ') + ' \u2014 ' + d.impact.event.affected_edges + ' lane(s) affected, ' + fmt(d.healing.total_unmet) + ' unmet.');
}

function readEvent() {
  const type = $('#evType').value;
  const ev = {type, country: $('#evCountry').value, source: 'demo-dashboard', approved: true};
  if (['tariff', 'sanction', 'embargo'].includes(type)) { const t = $('#evTarget').value; if (t) ev.target_country = t; }
  if (type === 'tariff') ev.tariff_rate = Number($('#evTariff').value);
  if (type === 'disaster') ev.severity = Number($('#evSeverity').value);
  if (type === 'chokepoint_closure') { ev.chokepoint = $('#evChoke').value; ev.country = 'Global'; }
  return ev;
}
function fillForm(ev) {
  $('#evType').value = ev.type; syncForm();
  if (ev.type !== 'chokepoint_closure') $('#evCountry').value = ev.country;
  $('#evTarget').value = ev.target_country || '';
  if (ev.tariff_rate != null) $('#evTariff').value = ev.tariff_rate;
  if (ev.severity != null) $('#evSeverity').value = ev.severity;
  if (ev.chokepoint) $('#evChoke').value = ev.chokepoint;
}
function syncForm() {
  const t = $('#evType').value;
  document.querySelectorAll('[data-for]').forEach(el => el.classList.toggle('hide', !el.dataset.for.split(' ').includes(t)));
}

/* ================= trace ================= */
function renderTraceItems() {
  const items = [...new Set(EDGES.filter(e => e.product_id === product).map(e => e.item_id))].sort((a, b) => a.length - b.length || a.localeCompare(b));
  $('#traceItem').innerHTML = items.map(i => '<option>' + esc(i) + '</option>').join('');
}
async function runTrace() {
  const item = $('#traceItem').value;
  const d = await api('/graph/dependencies?item_id=' + encodeURIComponent(item));
  TRACE = new Set(d.dependencies.map(e => e.edge_id));
  const byTier = {}, countries = new Set();
  d.dependencies.forEach(e => { byTier[e.item_tier] = (byTier[e.item_tier] || 0) + 1; countries.add(e.supplier_country); });
  $('#traceOut').innerHTML = '<h3>' + d.count + ' lanes upstream of ' + esc(item) + '</h3>' +
    '<table><tr><th>Tier</th><th>Lanes</th></tr>' + Object.keys(byTier).sort().reverse().map(t => '<tr><td>T' + t + '</td><td>' + byTier[t] + '</td></tr>').join('') + '</table>' +
    '<h3>Supplier countries involved</h3><div class="chips">' + [...countries].sort().map(c => '<span class="chip">' + esc(c) + '</span>').join('') + '</div>';
  renderGraph();
}

/* ================= suppliers / events ================= */
async function loadHeader() {
  const [s, st] = await Promise.all([api('/seed/summary'), api('/demo/status')]);
  $('#hdr').innerHTML = '<span class="chip">' + s.edge_count + ' lanes</span><span class="chip">' + s.node_count + ' facilities</span><span class="chip">' + s.products.length + ' products</span><span class="chip">' + s.countries.length + ' countries</span>' +
    '<span class="chip">' + st.suppliers + ' suppliers</span>' + Object.entries(st.by_label).map(([k, v]) => badge(k, k === 'Verified') + '<span class="muted small">' + v + '</span>').join(' ') +
    ' <a class="small" href="/frontend/" target="_blank">original frontend</a> <a class="small" href="/docs" target="_blank">API docs</a>';
}
async function doSearch() {
  const p = new URLSearchParams();
  [['product_name', '#sp'], ['hs_code', '#sh'], ['country', '#sc'], ['category', '#sk'], ['min_capacity', '#smin'], ['max_lead_time_days', '#slt'], ['certification', '#scert']]
    .forEach(([k, id]) => { const v = $(id).value.trim(); if (v) p.set(k, v); });
  if ($('#sver').checked) p.set('verified_only', 'true');
  const url = '/search?' + p.toString();
  const d = await api(url);
  $('#searchOut').innerHTML = '<div class="muted small" style="margin:6px 0">GET ' + esc(url) + ' &rarr; ' + d.count + ' result(s)</div>' +
    (d.count ? '<table><tr><th>Supplier</th><th>Product</th><th>Price / lead</th><th></th></tr>' + d.results.map(r =>
      '<tr><td><b>' + esc(r.company_name) + '</b><br><span class="muted">' + esc(r.country) + (r.region ? ', ' + esc(r.region) : '') + '</span><br>' + badge(r.source_label, r.verified) + '</td>' +
      '<td>' + esc(r.product_name) + '<br><span class="muted">HS ' + esc(r.hs_code || '-') + ' &middot; ' + esc(r.category || '-') + '<br>cap ' + fmt(r.capacity) + ' / spare ' + fmt(r.spare_capacity) + '</span></td>' +
      '<td>' + esc(r.currency) + ' ' + fmt(r.unit_price) + '<br><span class="muted">' + fmt(r.lead_time_days) + ' d</span></td>' +
      '<td><button class="btn sec sm" data-sid="' + esc(r.supplier_id) + '" data-v="' + (r.verified ? 0 : 1) + '">' + (r.verified ? 'Unverify' : 'Verify') + '</button></td></tr>').join('') + '</table>' : '');
  document.querySelectorAll('button[data-sid]').forEach(b => b.addEventListener('click', guard(async () => {
    await send('PATCH', '/suppliers/' + encodeURIComponent(b.dataset.sid) + '/verification', {verified: b.dataset.v === '1'});
    toast(b.dataset.v === '1' ? 'Supplier marked Verified.' : 'Supplier marked unverified.'); await doSearch(); loadHeader();
  })));
}
async function register() {
  const n = id => Number($(id).value || 0);
  const body = {company_name: $('#rc').value, country: $('#rk').value, region: $('#rr').value || null, certifications: [], export_countries: [],
    items: [{product_name: $('#rp').value, category: $('#rcat').value || null, hs_code: $('#rh').value || null, capacity: n('#rcap'), spare_capacity: n('#rsp'), unit_price: n('#rpr'), lead_time_days: Math.round(n('#rlt')), priority: 'P3'}]};
  const out = $('#regOut');
  try {
    const d = await send('POST', '/supplier', body);
    out.classList.remove('hide'); out.textContent = JSON.stringify(d, null, 2);
    toast('Supplier saved (unverified). Search for it to see it appear.'); loadHeader();
  } catch (e) { out.classList.remove('hide'); out.textContent = 'Error: ' + e.message; toast(e.message, true); }
}
async function scrape(path, body) {
  const out = $('#scrapeOut');
  try {
    const d = await send('POST', path, body);
    out.classList.remove('hide'); out.textContent = JSON.stringify(d, null, 2);
    toast('Scrape finished: ' + d.records_imported + ' record(s) imported.'); loadHeader();
  } catch (e) { out.classList.remove('hide'); out.textContent = 'Error: ' + e.message; toast(e.message, true); }
}
async function loadEvents() {
  const d = await api('/events');
  $('#evtOut').innerHTML = d.results.length ? '<table><tr><th>ID</th><th>Event</th><th>Sev.</th><th>Source</th></tr>' + d.results.map(e =>
    '<tr><td>' + esc(e.id) + '<br><span class="muted">' + esc(e.created_at) + '</span></td><td><b>' + esc(e.event_type) + '</b> ' + esc(e.country) + (e.target_country ? ' \u2192 ' + esc(e.target_country) : '') + '</td><td>' + fmt(e.severity) + '</td><td>' + esc(e.source) + '</td></tr>').join('') + '</table>'
    : 'No events saved yet. Run a simulation and click "Save scenario as event".';
}

/* ================= wiring ================= */
function renderAll() { renderProductTabs(); renderGraph(); renderKpis(); renderRecs(); renderTraceItems(); }

document.querySelectorAll('.tab').forEach(t => t.addEventListener('click', () => {
  document.querySelectorAll('.tab').forEach(x => x.classList.toggle('on', x === t));
  document.querySelectorAll('.pane').forEach(p => p.classList.toggle('on', p.id === 'pane-' + t.dataset.tab));
  if (t.dataset.tab === 'evt') guard(loadEvents)();
}));
$('#lbl').addEventListener('change', renderGraph);
$('#evType').addEventListener('change', syncForm);
$('#runBtn').addEventListener('click', guard(() => runSim(readEvent())));
$('#clearBtn').addEventListener('click', () => { SIM = null; EVENT = null; D = null; SEL = null; TRACE = new Set(); $('#traceOut').innerHTML = ''; $('#edgeinfo').innerHTML = 'Cleared.'; renderAll(); });
$('#recFilter').addEventListener('change', renderRecs);
$('#traceBtn').addEventListener('click', guard(runTrace));
$('#clearTraceBtn').addEventListener('click', () => { TRACE = new Set(); $('#traceOut').innerHTML = ''; renderGraph(); });
$('#searchBtn').addEventListener('click', guard(doSearch));
['#sp', '#sh', '#sc', '#sk', '#smin', '#slt', '#scert'].forEach(id => $(id).addEventListener('keydown', ev => { if (ev.key === 'Enter') guard(doSearch)(); }));
$('#regBtn').addEventListener('click', register);
$('#sampleBtn').addEventListener('click', () => scrape('/scrape/sample'));
$('#urlBtn').addEventListener('click', () => scrape('/scrape', {url: $('#url').value.trim(), timeout: 5}));
$('#reseedBtn').addEventListener('click', guard(async () => { await send('POST', '/demo/reseed'); await loadHeader(); $('#searchOut').innerHTML = 'Demo database reset.'; toast('Demo database reset to the seed suppliers.'); }));
$('#evtBtn').addEventListener('click', guard(loadEvents));
$('#saveEvt').addEventListener('click', guard(async () => {
  if (!EVENT) { toast('Run a simulation first.', true); return; }
  const d = await send('POST', '/events', EVENT); toast('Saved as ' + d.event_id + '. See the Events tab.'); loadHeader();
}));

(guard(async () => {
  const [edges, prods, ctry] = await Promise.all([api('/graph/edges'), api('/products'), api('/countries')]);
  EDGES = edges.results; EDGES.forEach(e => { EDGE_BY_ID[e.edge_id] = e; });
  PRODUCTS = prods.results; COUNTRIES = ctry.results;
  $('#evCountry').innerHTML = COUNTRIES.map(c => '<option>' + esc(c) + '</option>').join('');
  $('#evTarget').innerHTML = '<option value="">(any partner)</option>' + COUNTRIES.map(c => '<option>' + esc(c) + '</option>').join('');
  $('#evChoke').innerHTML = CHOKEPOINTS.map(c => '<option>' + esc(c) + '</option>').join('');
  $('#evCountry').value = 'China'; $('#evTarget').value = 'India';
  $('#scen').innerHTML = SCENARIOS.map((s, i) => '<button class="btn sec sm" data-i="' + i + '">' + esc(s.label) + '</button>').join('');
  document.querySelectorAll('#scen button').forEach(b => b.addEventListener('click', guard(async () => { const s = SCENARIOS[+b.dataset.i]; fillForm(s.ev); await runSim(s.ev); })));
  syncForm(); renderAll(); await loadHeader();
}))();
</script>
</body>
</html>
"""


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def _check_dependencies() -> None:
    needed = {
        "networkx": "networkx",
        "fastapi": "fastapi",
        "uvicorn": "uvicorn[standard]",
        "bs4": "beautifulsoup4",
        "pydantic": "pydantic",
    }
    missing = []
    for module, package in needed.items():
        try:
            importlib.import_module(module)
        except ImportError:
            missing.append(package)
    if missing:
        sys.exit(
            "Missing packages: " + ", ".join(missing) + "\n"
            "Install everything with:  python -m pip install -r requirements.txt"
        )


def _free_port(host: str, start: int) -> int:
    for port in range(start, start + 20):
        with socket.socket() as sock:
            if sock.connect_ex((host, port)) != 0:
                return port
    return start


def main() -> None:
    parser = argparse.ArgumentParser(description="Local demo for the Self-Healing Supply Chain project.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--keep-data", action="store_true", help="keep the demo DB from the previous run")
    args = parser.parse_args()

    _check_dependencies()
    import uvicorn

    seed_database(wipe=not args.keep_data)
    if args.keep_data and db_status()["suppliers"] == 0:
        seed_database(wipe=False)
    app = build_app()

    port = _free_port(args.host, args.port)
    base = f"http://{args.host}:{port}"
    print("\n  Self-Healing Supply Chain - local demo")
    print("  --------------------------------------")
    print(f"  Dashboard         : {base}/ui")
    print(f"  Original frontend : {base}/frontend/")
    print(f"  API docs          : {base}/docs")
    print(f"  Demo DB           : {DEMO_DB}  (isolated; data/supply_chain.db is untouched)")
    if port != 8000:
        print("  Note: port 8000 was busy/changed - set the 'API base URL' box in /frontend/ to " + base)
    print("  Stop with Ctrl+C\n")
    if not args.no_browser:
        threading.Timer(1.2, lambda: webbrowser.open(f"{base}/ui")).start()
    uvicorn.run(app, host=args.host, port=port, log_level="info")


if __name__ == "__main__":
    main()
