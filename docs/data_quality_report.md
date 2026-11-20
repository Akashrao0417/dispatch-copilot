# Data quality report

_Generated 2026-11-19 21:59 by `python -m backend.etl`._

## Summary

| Source | Rows in | Rows loaded | Dropped | Duplicates removed |
|---|---:|---:|---:|---:|
| jobs.csv | 205 | 196 | 4 | 5 |
| service_logs.xlsx (Service Logs) | 120 | 100 | 2 | 18 |
| service_logs.xlsx (Technician Roster) | 124 | 120 | 0 | 4 |
| CRM API | 80 | 80 | 0 | 0 |

## Details

### jobs.csv
- Dropped **1** row(s): missing customer id
- Dropped **1** row(s): customer not in CRM
- Dropped **1** row(s): unknown technician
- Dropped **1** row(s): unparseable scheduled date
- Cities normalized: **144**
- Statuses normalized: **132**
- Phones missing or invalid: **46**
- Phones reformatted: **154**
- Dates fixed: **461**
- Dates unparseable: **1**

### service_logs.xlsx (Service Logs)
- Dropped **2** row(s): job was dropped / unknown
- Exact duplicate rows: **12**
- Same ticket reentered: **6**

### service_logs.xlsx (Technician Roster)

### CRM API
- Phones backfilled from jobs: **5**
- Phones still missing: **9**

## Loaded tables

| Table | Rows |
|---|---:|
| customers | 80 |
| technicians | 120 |
| jobs | 196 |
| tickets | 100 |
