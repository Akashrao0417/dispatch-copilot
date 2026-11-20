# CLAUDE.md — working on dispatch-copilot

Dispatcher copilot for the (fictional) NorthStar Logistics: 120 technicians, ~200 jobs,
three messy sources → Postgres → Streamlit. See README.md for the story and definitions.

## Commands
- Install: `pip install -r requirements.txt`
- Postgres: `docker compose up -d` (DATABASE_URL defaults to postgresql://postgres:postgres@localhost:5432/dispatch)
- Regenerate raw data: `python data/generate_data.py [--as-of "YYYY-MM-DD HH:MM"]`
- CRM API: `uvicorn backend.crm_api:app --port 8001`
- ETL: `python -m backend.etl` (needs Postgres + CRM API running)
- Dashboard: `streamlit run frontend/app.py`
- Tests: `pytest` (no DB required) — run after every change to backend/ or data/

## Conventions
- Raw files in data/raw/ are *supposed* to be messy. Never clean them by hand; fix things in backend/etl.py.
- Every cleaning rule must update `SourceStats` so it shows up in docs/data_quality_report.md.
- If you change the generator's injected mess, update the expected counts in tests/test_etl.py.
- Schema lives in backend/schema.sql; the ETL drops and recreates tables on each run.
- Timestamps are naive local time (America/New_York). Pass "now" from Python, not Postgres `now()`.
- Keep the definitions of "delayed" and "SLA breach" identical in README.md and frontend/app.py.

## Next up (roadmap)
Phase 2: natural-language Q&A over the DB + SOP docs with citations.
Phase 3: suggested actions (reassign, customer message) behind an Approve button, with an audit log table.
