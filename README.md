# Self-Healing Supply Chain — Complete Runnable MVP

This project implements the brief's **MVP scope**: seeded multi-tier supply data (5 products, 4 tiers, 15 countries, 100+ edges), directed graph engine, upstream traversal, disruption impact, tariff/sanction/war/disaster simulations, candidate ranking and partial allocation, supplier registration and search, manual events, and a bounded HTML scraper fallback. It also includes a small browser dashboard and tests.

> Scope honesty: this is a runnable MVP, not a finished enterprise implementation of every aspirational Phase 0–12 item. Full authentication/roles, business-document verification, map tiles/geocoding, live external event feeds, LLM extraction, synonym/full-text search, RFQ messaging, notifications, globally optimal min-cost flow, live trade-law validation, and fully integrated DynamoDB/Neptune repositories remain follow-up work. The AWS notes explain the necessary persistence adapters.

## Requirements
- Python 3.11 or newer
- Internet only if you want the scraper to fetch an external website. The included local HTML fixture can be inspected but the URL fetcher intentionally rejects local/private destinations.

## Run on Windows / VS Code
Open this folder in VS Code. In Terminal → New Terminal run:

```powershell
py -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
uvicorn app.main:app --reload
```

If PowerShell blocks activation, run `Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass` in that terminal, then activate again. Or use `\.venv\Scripts\python.exe -m pip install -r requirements.txt` without activation.

Open:
- API docs: http://127.0.0.1:8000/docs
- Health: http://127.0.0.1:8000/health
- Dashboard: open `frontend/index.html` in your browser (or use VS Code Live Server) and leave API base URL as `http://127.0.0.1:8000`.

## Demo flow
1. `GET /seed/summary` confirms 5 products, 4 tiers, 15 countries and 100+ edges.
2. In dashboard, simulate `war`, country `China`, demand `500`; show impacted links and healing allocations/unmet quantity.
3. Register a backup supplier from the onboarding tab.
4. Search by product name `Fuel Pump`, category `Energy`, or country `India`.
5. Create a manual event via `POST /events` in `/docs` and refresh the Events tab.
6. For a reliable scraper demo without DNS errors, click **Import bundled scraper demo data** in the Events tab or call `POST /scrape/sample`. For external scraping, use a real permitted public directory URL in `POST /scrape`; do not use `.example` placeholders. Check the source's terms and robots.txt first. The bundled HTML is synthetic test data, not a real supplier source.

## Key API endpoints
- `GET /`, `GET /health`, `GET /seed/summary`
- `POST /supplier`, `GET /search`, `GET /suppliers`, `PATCH /suppliers/{supplier_id}/verification`
- `GET /graph/edges`, `GET /graph/dependencies?item_id=MED-KIT`
- `GET /impact?country=China&event_type=war`
- `POST /events`, `GET /events`
- `POST /heal?demand=500`, `POST /simulate?demand=500`
- `POST /scrape`, `POST /scrape/sample`, `GET /products`, `GET /countries`

Example `POST /supplier` body:

```json
{
  "company_name": "Demo Backup Components Ltd",
  "country": "India",
  "region": "Pune",
  "contact_email": "sales@example.org",
  "certifications": [],
  "export_countries": ["India", "Singapore"],
  "items": [{
    "product_name": "Fuel Pump", "category": "Energy", "hs_code": "8413",
    "capacity": 1000, "spare_capacity": 500, "unit_price": 24,
    "currency": "USD", "lead_time_days": 7, "priority": "P2"
  }]
}
```

Example `POST /simulate?demand=500` body:

```json
{"type":"war","country":"China","severity":1,"source":"manual","approved":true}
```

## Optional Docker run

```powershell
docker build -t self-healing-supply-chain .
docker run --rm -p 8000:8000 self-healing-supply-chain
```

## Run tests

```powershell
python -m pytest -q
```

## Run command-line demo

```powershell
python -m scripts.demo
```

## Data and model notes
- Facility graph is a NetworkX `MultiDiGraph` so more than one supply lane can exist between facilities.
- Seed data is synthetic and intended only for demonstration. It is not a source of real-world supplier availability or legal trade advice.
- The current healing scorer is a transparent heuristic, not a global min-cost-flow solver. It reports partial and unmet demand and does not execute real orders.
- Scraped supplier rows are marked `Unverified - public data`, retain their source URL in the result, and must be reviewed before operational use.
- `app/database.py` uses local SQLite. See `aws/README.md` before deploying to Lambda with durable data.
