"""ETL: three messy NorthStar sources -> clean Postgres tables.

Extract:   data/raw/jobs.csv, data/raw/service_logs.xlsx, fake CRM API (paginated JSON)
Transform: parse mixed date formats, normalise phones/cities/IDs/statuses,
           dedupe, enforce referential integrity, compute SLA deadlines
Load:      customers, technicians, jobs, tickets (+ an etl_runs audit row)
Report:    docs/data_quality_report.md — every dropped row, fixed date and removed duplicate

Run:
  uvicorn backend.crm_api:app --port 8001 &      # the CRM must be up
  python -m backend.etl
"""
from __future__ import annotations

import json
import os
import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path

import httpx
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw"
DOCS = ROOT / "docs"
DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/dispatch")
CRM_API_URL = os.getenv("CRM_API_URL", "http://localhost:8001")

ARRIVAL_WINDOW = timedelta(hours=2)
SLA_BY_TIER = {"Gold": timedelta(hours=2), "Silver": timedelta(hours=4), "Bronze": timedelta(hours=6)}

CANONICAL_DATE = "%Y-%m-%d %H:%M"
DATE_FORMATS = [CANONICAL_DATE, "%m/%d/%Y %I:%M %p", "%d-%b-%Y %H:%M", "%Y/%m/%dT%H:%M:%S",
                "%m/%d/%y %H:%M", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S"]
CITY_ALIASES = {"clt": "Charlotte", "gso": "Greensboro", "winston salem": "Winston-Salem"}
STATUSES = {"scheduled": "Scheduled", "en route": "En Route", "in progress": "In Progress",
            "completed": "Completed", "cancelled": "Cancelled"}


# --------------------------------------------------------------------------- stats
@dataclass
class SourceStats:
    rows_in: int = 0
    rows_out: int = 0
    duplicates_removed: int = 0
    dropped: Counter = field(default_factory=Counter)
    fixes: Counter = field(default_factory=Counter)

    def as_dict(self) -> dict:
        return {"rows_in": self.rows_in, "rows_out": self.rows_out,
                "duplicates_removed": self.duplicates_removed,
                "rows_dropped": sum(self.dropped.values()), "dropped_reasons": dict(self.dropped),
                "fixes": dict(self.fixes)}


# --------------------------------------------------------------------------- cleaners
def parse_date(raw, stats: SourceStats | None = None, label: str = "dates") -> datetime | None:
    """Parse any of the formats seen in the wild. Counts non-canonical values as 'fixed'."""
    if raw is None or (isinstance(raw, float) and pd.isna(raw)):
        return None
    if isinstance(raw, (pd.Timestamp, datetime)):
        return pd.Timestamp(raw).to_pydatetime()
    s = str(raw).strip()
    if not s:
        return None
    for fmt in DATE_FORMATS:
        try:
            dt = datetime.strptime(s, fmt)
        except ValueError:
            continue
        if stats is not None and fmt != CANONICAL_DATE:
            stats.fixes[f"{label}_fixed"] += 1
        return dt
    if stats is not None:
        stats.fixes[f"{label}_unparseable"] += 1
    return None


def normalize_phone(raw) -> str | None:
    digits = re.sub(r"\D", "", str(raw or ""))
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    return f"+1{digits}" if len(digits) == 10 else None


def normalize_city(raw) -> str:
    s = re.sub(r",\s*NC$", "", str(raw or "").strip(), flags=re.I).strip()
    return CITY_ALIASES.get(s.lower(), s.title() if s.islower() or s.isupper() else s)


def normalize_tech_id(raw) -> str | None:
    m = re.search(r"(\d+)", str(raw or ""))
    return f"T-{int(m.group(1)):03d}" if m else None


def normalize_status(raw) -> str:
    return STATUSES.get(str(raw or "").strip().lower(), "Unknown")


# --------------------------------------------------------------------------- extract
def extract_jobs(path: Path = RAW / "jobs.csv") -> pd.DataFrame:
    return pd.read_csv(path, dtype=str, keep_default_na=False)


def extract_service_logs(path: Path = RAW / "service_logs.xlsx") -> tuple[pd.DataFrame, pd.DataFrame]:
    sheets = pd.read_excel(path, sheet_name=None)
    return sheets["Service Logs"], sheets["Technician Roster"]


def extract_crm(client: httpx.Client | None = None, base_url: str = CRM_API_URL) -> list[dict]:
    """Walk every page of the CRM API."""
    own = client is None
    client = client or httpx.Client(base_url=base_url, timeout=10)
    rows, page = [], 1
    try:
        while page:
            r = client.get("/api/v1/customers", params={"page": page, "page_size": 25})
            r.raise_for_status()
            body = r.json()
            rows.extend(body["data"])
            page = body["next_page"]
    finally:
        if own:
            client.close()
    return rows


# --------------------------------------------------------------------------- transform
def transform_technicians(roster: pd.DataFrame) -> tuple[pd.DataFrame, SourceStats]:
    st = SourceStats(rows_in=len(roster))
    df = roster.copy()
    df["technician_id"] = df["Tech ID"].map(normalize_tech_id)
    before = len(df)
    df = df.drop_duplicates(subset="technician_id", keep="first")
    st.duplicates_removed = before - len(df)
    out = pd.DataFrame({
        "technician_id": df["technician_id"],
        "name": df["Name"].str.strip(),
        "home_city": df["Home City"].map(normalize_city),
        "phone": df["Mobile"].map(normalize_phone),
        "skill": df["Skill"],
        "hire_date": pd.to_datetime(df["Hire Date"]).dt.date,
    })
    st.rows_out = len(out)
    return out.reset_index(drop=True), st


def transform_customers(crm: list[dict], jobs_raw: pd.DataFrame) -> tuple[pd.DataFrame, SourceStats]:
    st = SourceStats(rows_in=len(crm))
    recs = []
    for c in crm:
        recs.append({"customer_id": c["id"], "name": c["profile"]["company_name"],
                     "phone": normalize_phone(c["contact"].get("phone")),
                     "email": c["contact"].get("email"),
                     "city": normalize_city(c["address"]["city"]), "state": c["address"].get("state"),
                     "tier": c["profile"]["tier"], "crm_updated_at": parse_date(c.get("updated_at", "").replace("T", " ")[:16])})
    df = pd.DataFrame(recs)
    before = len(df)
    df = df.drop_duplicates(subset="customer_id")
    st.duplicates_removed = before - len(df)

    # Backfill phones the CRM is missing from what drivers typed into jobs.csv
    job_phones = (jobs_raw.assign(p=jobs_raw["Customer Phone"].map(normalize_phone))
                  .dropna(subset=["p"]).groupby("Cust ID")["p"].agg(lambda s: s.mode().iat[0]))
    missing = df["phone"].isna()
    df.loc[missing, "phone"] = df.loc[missing, "customer_id"].map(job_phones)
    st.fixes["phones_backfilled_from_jobs"] = int(missing.sum() - df["phone"].isna().sum())
    st.fixes["phones_still_missing"] = int(df["phone"].isna().sum())
    st.rows_out = len(df)
    return df.reset_index(drop=True), st


def transform_jobs(raw: pd.DataFrame, customers: pd.DataFrame, techs: pd.DataFrame) -> tuple[pd.DataFrame, SourceStats]:
    st = SourceStats(rows_in=len(raw))
    df = raw.copy()
    before = len(df)
    df = df.drop_duplicates()
    st.duplicates_removed = before - len(df)

    df["job_id"] = df["Job ID"].str.strip().str.upper()
    dupe_ids = df.duplicated(subset="job_id", keep="first")
    st.duplicates_removed += int(dupe_ids.sum())
    df = df[~dupe_ids]

    df["customer_id"] = df["Cust ID"].str.strip().str.upper().replace("", None)
    df["technician_id"] = df["Tech"].map(normalize_tech_id)
    city_clean = df["City"].map(normalize_city)
    st.fixes["cities_normalized"] = int((city_clean != df["City"]).sum())
    df["city"] = city_clean
    status_clean = df["Status"].map(normalize_status)
    st.fixes["statuses_normalized"] = int((status_clean != df["Status"]).sum())
    df["status"] = status_clean
    phone_clean = df["Customer Phone"].map(normalize_phone)
    st.fixes["phones_missing_or_invalid"] = int(phone_clean.isna().sum())
    st.fixes["phones_reformatted"] = int(((phone_clean.notna()) & (phone_clean != df["Customer Phone"])).sum())
    df["customer_phone"] = phone_clean
    df["created_at"] = [parse_date(v, st) for v in df["Created"]]
    df["scheduled_at"] = [parse_date(v, st) for v in df["Scheduled Date"]]
    df["completed_at"] = [parse_date(v, st) for v in df["Completed"]]

    # Referential integrity / required fields
    checks = [
        ("missing customer id", df["customer_id"].isna()),
        ("customer not in CRM", df["customer_id"].notna() & ~df["customer_id"].isin(customers["customer_id"])),
        ("unknown technician", ~df["technician_id"].isin(techs["technician_id"])),
        ("unparseable scheduled date", df["scheduled_at"].isna()),
    ]
    keep = pd.Series(True, index=df.index)
    for reason, bad in checks:
        bad = bad & keep
        st.dropped[reason] += int(bad.sum())
        keep &= ~bad
    df = df[keep].copy()

    tier = df["customer_id"].map(customers.set_index("customer_id")["tier"])
    df["window_end_at"] = df["scheduled_at"] + ARRIVAL_WINDOW
    df["sla_due_at"] = df["scheduled_at"] + tier.map(SLA_BY_TIER)

    out = df[["job_id", "customer_id", "technician_id", "city", "Job Type", "Priority", "status",
              "created_at", "scheduled_at", "window_end_at", "sla_due_at", "completed_at"]]
    out = out.rename(columns={"Job Type": "job_type", "Priority": "priority"})
    st.rows_out = len(out)
    return out.reset_index(drop=True), st


def transform_tickets(raw: pd.DataFrame, jobs: pd.DataFrame) -> tuple[pd.DataFrame, SourceStats]:
    st = SourceStats(rows_in=len(raw))
    df = raw.copy()
    before = len(df)
    df = df.drop_duplicates()
    exact = before - len(df)

    df["ticket_id"] = df["Ticket #"].astype(str).str.strip().str.upper()
    df["notes"] = df["Notes"].astype(str).str.strip()
    # Prefer the cleanly-keyed copy when the same ticket was entered twice
    df["_clean"] = (df["Notes"] == df["notes"]).astype(int)
    df = df.sort_values("_clean", ascending=False)
    rekeyed = int(df.duplicated(subset="ticket_id").sum())
    df = df.drop_duplicates(subset="ticket_id", keep="first")
    st.duplicates_removed = exact + rekeyed
    st.fixes["exact_duplicate_rows"] = exact
    st.fixes["same_ticket_reentered"] = rekeyed

    df["job_id"] = df["Job Ref"].astype(str).str.strip().str.upper()
    orphan = ~df["job_id"].isin(jobs["job_id"])
    st.dropped["job was dropped / unknown"] = int(orphan.sum())
    df = df[~orphan]

    out = pd.DataFrame({
        "ticket_id": df["ticket_id"], "job_id": df["job_id"],
        "technician_id": df["Tech"].map(normalize_tech_id),
        "logged_at": [parse_date(v, st, "logged_at") for v in df["Logged At"]],
        "category": df["Category"].astype(str).str.strip(),
        "notes": df["notes"].map(lambda s: s[:1].upper() + s[1:].lower() if s.isupper() else s),
        "resolved": df["Resolved"].astype(str).str.strip().str.upper().eq("Y"),
    }).sort_values("ticket_id")
    st.rows_out = len(out)
    return out.reset_index(drop=True), st


def transform(jobs_raw, tickets_raw, roster_raw, crm_raw) -> tuple[dict, dict]:
    techs, s_tech = transform_technicians(roster_raw)
    customers, s_cust = transform_customers(crm_raw, jobs_raw)
    jobs, s_jobs = transform_jobs(jobs_raw, customers, techs)
    tickets, s_tix = transform_tickets(tickets_raw, jobs)
    clean = {"customers": customers, "technicians": techs, "jobs": jobs, "tickets": tickets}
    stats = {"jobs.csv": s_jobs.as_dict(), "service_logs.xlsx (Service Logs)": s_tix.as_dict(),
             "service_logs.xlsx (Technician Roster)": s_tech.as_dict(), "CRM API": s_cust.as_dict()}
    return clean, stats


# --------------------------------------------------------------------------- load
def _py(v):
    if v is None:
        return None
    if isinstance(v, pd.Timestamp):
        return None if pd.isna(v) else v.to_pydatetime()
    if isinstance(v, float) and pd.isna(v):
        return None
    if v is pd.NaT:
        return None
    if hasattr(v, "item") and not isinstance(v, (str, date, datetime)):
        return v.item()
    return v


def load(clean: dict, stats: dict, database_url: str = DATABASE_URL) -> None:
    import psycopg2
    from psycopg2.extras import Json, execute_values

    with psycopg2.connect(database_url) as conn, conn.cursor() as cur:
        cur.execute((Path(__file__).parent / "schema.sql").read_text())
        for table in ["customers", "technicians", "jobs", "tickets"]:  # FK order
            df = clean[table].astype(object).where(clean[table].notna(), None)
            cols = list(df.columns)
            rows = [tuple(_py(v) for v in r) for r in df.itertuples(index=False, name=None)]
            execute_values(cur, f"INSERT INTO {table} ({', '.join(cols)}) VALUES %s", rows)
        cur.execute("INSERT INTO etl_runs (stats) VALUES (%s)", (Json(stats),))


# --------------------------------------------------------------------------- report
def write_report(clean: dict, stats: dict, path: Path = DOCS / "data_quality_report.md") -> str:
    lines = [f"# Data quality report", "",
             f"_Generated {datetime.now():%Y-%m-%d %H:%M} by `python -m backend.etl`._", "",
             "## Summary", "",
             "| Source | Rows in | Rows loaded | Dropped | Duplicates removed |",
             "|---|---:|---:|---:|---:|"]
    for src, s in stats.items():
        lines.append(f"| {src} | {s['rows_in']} | {s['rows_out']} | {s['rows_dropped']} | {s['duplicates_removed']} |")
    lines += ["", "## Details", ""]
    for src, s in stats.items():
        lines.append(f"### {src}")
        for reason, n in s["dropped_reasons"].items():
            if n:
                lines.append(f"- Dropped **{n}** row(s): {reason}")
        for k, n in s["fixes"].items():
            lines.append(f"- {k.replace('_', ' ').capitalize()}: **{n}**")
        lines.append("")
    lines += ["## Loaded tables", "", "| Table | Rows |", "|---|---:|"]
    lines += [f"| {t} | {len(df)} |" for t, df in clean.items()]
    text = "\n".join(lines) + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return text


def main() -> None:
    jobs_raw = extract_jobs()
    tickets_raw, roster_raw = extract_service_logs()
    crm_raw = extract_crm()
    clean, stats = transform(jobs_raw, tickets_raw, roster_raw, crm_raw)
    load(clean, stats)
    print(write_report(clean, stats))


if __name__ == "__main__":
    main()
