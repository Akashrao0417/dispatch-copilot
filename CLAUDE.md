# CLAUDE.md — working on dispatch-copilot

Dispatcher copilot for the (fictional) NorthStar Logistics: 120 technicians, ~200 jobs,
three messy sources → Postgres → Streamlit. See README.md for the story and definitions.

## Commands
- Install: `pip install -r requirements.txt`
- Postgres: `docker compose up -d` (DATABASE_URL defaults to postgresql://postgres:postgres@localhost:5432/dispatch)
- Regenerate raw data: `python data/generate_data.py [--as-of "YYYY-MM-DD HH:MM"]`
- CRM API: `uvicorn backend.crm_api:app --port 8001`
- ETL: `python -m backend.etl` (needs Postgres + CRM API running)
- SOP PDFs: `python docs/sops/build_sops.py` (edit docs/sops/sops.json, never the PDFs; each must stay 1 page)
- App: `streamlit run frontend/app.py` (pages in frontend/views/)
- Tests: `pytest` — run after every change. DB tests create/drop a `dispatch_test` database; they skip if Postgres is down.

## Conventions
- Raw files in data/raw/ are *supposed* to be messy. Never clean them by hand; fix things in backend/etl.py.
- Every cleaning rule must update `SourceStats` so it shows up in docs/data_quality_report.md.
- If you change the generator's injected mess, update the expected counts in tests/test_etl.py.
- Schema lives in backend/schema.sql; the ETL drops and recreates tables on each run.
- Timestamps are naive local time (America/New_York). Pass "now" from Python, not Postgres `now()`.
- Keep the definitions of "delayed" and "SLA breach" identical in README.md and backend/queries.py.
- Roles are enforced in the backend: every job query goes through `auth.job_scope_sql`, every action through
  `agent.validate`. Never filter by role only in the UI.
- The agent has exactly 3 tools (reassign_technician, create_ticket, draft_customer_update). Adding a tool means
  updating auth.PERMISSIONS, agent.validate, agent.EXECUTORS, the app_schema CHECK, and tests.
- Nothing executes without `agent.approve()` by a person; every path (including refusals) writes to audit_log.
- The copilot never lets the model invent job facts: job answers come from SQL; only SOP answers use the LLM,
  and they must cite a retrieved `[SOP-XX §N]` or become "I don't know".
- App tables (users, proposed_actions, audit_log, customer_messages) live in backend/app_schema.sql and are never
  dropped by the ETL. Reassignments/tickets in jobs/tickets ARE reset by an ETL reload; the audit log keeps them.
- Works without ANTHROPIC_API_KEY (rule planner + extractive answers). Test the LLM path with fake clients.
