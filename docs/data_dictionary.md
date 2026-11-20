# Data dictionary

## customers  (source: CRM API)
| Column | Notes |
|---|---|
| customer_id | `C-001`… |
| name, email | from CRM `profile` / `contact` |
| phone | E.164; backfilled from jobs.csv when the CRM is blank |
| city, state | normalised city name |
| tier | Gold / Silver / Bronze → SLA 2h / 4h / 6h |
| crm_updated_at | last CRM edit |

## technicians  (source: service_logs.xlsx › Technician Roster)
technician_id (`T-001`…`T-120`), name, home_city, phone, skill (Driver / Installer / Repair Tech), hire_date.

## jobs  (source: jobs.csv)
| Column | Notes |
|---|---|
| job_id | `J-1001`… |
| customer_id, technician_id | FK; rows with unknown values are dropped |
| city | normalised (`CLT` → Charlotte) |
| status | Scheduled / En Route / In Progress / Completed / Cancelled |
| created_at, scheduled_at, completed_at | parsed from 6 formats |
| window_end_at | scheduled_at + 2h (promised arrival window) |
| sla_due_at | scheduled_at + tier SLA |

## tickets  (source: service_logs.xlsx › Service Logs)
ticket_id (`TK-5001`…), job_id (FK), technician_id, logged_at, category (Traffic delay, Weather, Parts missing, …), notes, resolved.

## etl_runs
One row per ETL run with the full data-quality stats as JSONB (shown in the dashboard).

## App tables (backend/app_schema.sql — never dropped by the ETL)

### users
username (`manager`, `t-001`…), name, role (`manager` | `technician`), technician_id (technicians only).

### proposed_actions
action_id, tool (`reassign_technician` | `create_ticket` | `draft_customer_update`), job_id, args (JSON),
rationale, citations (SOP sections), status (`pending` → `executed` | `rejected` | `failed`),
proposed_by (`copilot` or a username), proposed_at, decided_by, decided_at, result (JSON).

### audit_log
log_id, ts (**when**), actor + role (**who**), event (`proposed` / `approved` / `rejected` / `executed` /
`failed` / `denied`), tool, job_id, action_id, details (**what**, JSON). Append-only.

### customer_messages
Approved customer updates, `ready_to_send`: job_id, customer_id, channel (sms/email), body, approved_by, created_at.
