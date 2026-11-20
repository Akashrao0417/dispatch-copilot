"""NorthStar Dispatch — Streamlit dashboard.

Run:  streamlit run frontend/app.py
Reads the clean Postgres tables loaded by `python -m backend.etl`.

Definitions (also in docs/README):
  Delayed      = not cancelled, and not finished by the end of its 2h arrival window
  SLA breach   = not cancelled, and not finished by its tier deadline
                 (Gold 2h, Silver 4h, Bronze 6h after scheduled time)
Open jobs are measured against "as of" time (now, for today).
"""
from __future__ import annotations

import json
import os
from datetime import date, datetime, time

import altair as alt
import pandas as pd
import streamlit as st
from sqlalchemy import create_engine, text

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/dispatch")
ACCENT = "#2a6fdb"

st.set_page_config(page_title="NorthStar Dispatch", page_icon="🚚", layout="wide")


@st.cache_resource
def engine():
    url = DATABASE_URL.replace("postgresql://", "postgresql+psycopg2://", 1)
    return create_engine(url, pool_pre_ping=True)


@st.cache_data(ttl=30)
def load_jobs(day: date, as_of: datetime) -> pd.DataFrame:
    sql = text("""
        WITH last_ticket AS (
            SELECT DISTINCT ON (job_id) job_id, category, notes, logged_at
            FROM tickets
            WHERE category <> 'Routine update'
            ORDER BY job_id, logged_at DESC
        )
        SELECT j.job_id, j.city, j.status, j.priority, j.job_type,
               j.scheduled_at, j.window_end_at, j.sla_due_at, j.completed_at,
               c.name AS customer, c.tier, c.phone AS customer_phone,
               t.technician_id, t.name AS technician,
               lt.category AS reason, lt.notes AS reason_note,
               (j.status <> 'Cancelled' AND COALESCE(j.completed_at, :as_of) > j.window_end_at) AS is_delayed,
               (j.status <> 'Cancelled' AND COALESCE(j.completed_at, :as_of) > j.sla_due_at)   AS is_breach,
               GREATEST(0, EXTRACT(EPOCH FROM COALESCE(j.completed_at, :as_of) - j.window_end_at) / 60)::int AS minutes_late
        FROM jobs j
        JOIN customers c   USING (customer_id)
        JOIN technicians t USING (technician_id)
        LEFT JOIN last_ticket lt USING (job_id)
        WHERE j.scheduled_at::date = :day
        ORDER BY j.scheduled_at
    """)
    with engine().connect() as conn:
        df = pd.read_sql(sql, conn, params={"day": day, "as_of": as_of})
    # An open job scheduled after "as of" can't be late yet
    future = df["scheduled_at"] > as_of
    df.loc[future, ["is_delayed", "is_breach"]] = False
    df.loc[future, "minutes_late"] = 0
    return df


@st.cache_data(ttl=30)
def available_days() -> list[date]:
    with engine().connect() as conn:
        rows = conn.execute(text("SELECT DISTINCT scheduled_at::date FROM jobs ORDER BY 1")).all()
    return [r[0] for r in rows]


@st.cache_data(ttl=30)
def last_etl_run() -> tuple[datetime, dict] | None:
    with engine().connect() as conn:
        row = conn.execute(text("SELECT ran_at, stats FROM etl_runs ORDER BY run_id DESC LIMIT 1")).first()
    if not row:
        return None
    stats = row[1] if isinstance(row[1], dict) else json.loads(row[1])
    return row[0], stats


# ----------------------------------------------------------------- sidebar
try:
    days = available_days()
except Exception as exc:  # DB not up / ETL not run
    st.error(f"Can't reach Postgres at `{DATABASE_URL}`. Start it and run `python -m backend.etl`.\n\n{exc}")
    st.stop()
if not days:
    st.warning("No jobs loaded yet — run `python -m backend.etl`.")
    st.stop()

today = date.today()
with st.sidebar:
    st.header("Filters")
    default_day = today if today in days else days[-1]
    day = st.date_input("Day", value=default_day, min_value=days[0], max_value=days[-1])
    as_of = datetime.now().replace(second=0, microsecond=0) if day == today else datetime.combine(day, time(23, 59))
    st.caption(f"Open jobs measured as of **{as_of:%b %d, %H:%M}**")
    jobs_all = load_jobs(day, as_of)
    cities = sorted(jobs_all["city"].unique())
    pick = st.multiselect("City", cities, default=cities)
    if st.button("Refresh data"):
        st.cache_data.clear()
        st.rerun()

jobs = jobs_all[jobs_all["city"].isin(pick)]

# ----------------------------------------------------------------- header + KPIs
st.title("NorthStar Dispatch")
st.caption(f"{day:%A, %B %d, %Y} · {', '.join(pick) if len(pick) < len(cities) else 'All cities'}")

active = jobs[jobs["status"] != "Cancelled"]
k1, k2, k3, k4 = st.columns(4)
k1.metric("Total jobs", len(jobs), help="All jobs scheduled for this day, including cancelled")
k2.metric("Completed", int((jobs["status"] == "Completed").sum()))
k3.metric("⚠️ Delayed jobs", int(active["is_delayed"].sum()),
          help="Not finished by the end of the 2-hour arrival window")
k4.metric("⛔ SLA breaches", int(active["is_breach"].sum()),
          help="Not finished by the tier deadline: Gold 2h, Silver 4h, Bronze 6h")

# ----------------------------------------------------------------- delayed jobs
st.subheader("Delayed jobs")
late = jobs[jobs["is_delayed"]].sort_values(["is_breach", "minutes_late"], ascending=False)
if late.empty:
    st.success("No delayed jobs for this selection.")
else:
    view = late.assign(
        SLA=late["is_breach"].map({True: "⛔ Breached", False: "⚠️ At risk"}),
        Late=late["minutes_late"].map(lambda m: f"{m // 60}h {m % 60:02d}m"),
        Scheduled=late["scheduled_at"].dt.strftime("%H:%M"),
        Reason=late["reason"].fillna("— no ticket logged —"),
        Note=late["reason_note"].fillna(""),
    )[["job_id", "SLA", "Late", "status", "city", "customer", "tier", "technician", "Scheduled", "Reason", "Note"]]
    st.dataframe(view.rename(columns={"job_id": "Job", "status": "Status", "city": "City",
                                      "customer": "Customer", "tier": "Tier", "technician": "Technician"}),
                 hide_index=True, use_container_width=True)

# ----------------------------------------------------------------- jobs per technician
st.subheader("Jobs per technician")
per_tech = (jobs.groupby(["technician_id", "technician"])
            .agg(jobs=("job_id", "count"), delayed=("is_delayed", "sum"))
            .reset_index().sort_values(["jobs", "technician"], ascending=[False, True]))
per_tech["label"] = per_tech["technician"] + " (" + per_tech["technician_id"] + ")"
st.caption(f"{len(per_tech)} technicians have work on this day for the selected cities.")
height = max(160, 22 * len(per_tech))
chart = (alt.Chart(per_tech)
         .mark_bar(color=ACCENT, cornerRadiusEnd=4, size=14)
         .encode(x=alt.X("jobs:Q", title="Jobs", axis=alt.Axis(tickMinStep=1, grid=True, gridOpacity=0.3)),
                 y=alt.Y("label:N", sort=None, title=None, axis=alt.Axis(labelLimit=220)),
                 tooltip=[alt.Tooltip("technician:N", title="Technician"),
                          alt.Tooltip("technician_id:N", title="ID"),
                          alt.Tooltip("jobs:Q", title="Jobs"),
                          alt.Tooltip("delayed:Q", title="Delayed")])
         .properties(height=height))
c1, c2 = st.columns([3, 2])
c1.altair_chart(chart, use_container_width=True)
c2.dataframe(per_tech[["technician", "technician_id", "jobs", "delayed"]]
             .rename(columns={"technician": "Technician", "technician_id": "ID", "jobs": "Jobs", "delayed": "Delayed"}),
             hide_index=True, use_container_width=True, height=min(height, 420))

# ----------------------------------------------------------------- all jobs + data quality
with st.expander("All jobs for this day"):
    st.dataframe(jobs.drop(columns=["reason_note"]), hide_index=True, use_container_width=True)

run = last_etl_run()
if run:
    ran_at, stats = run
    with st.expander(f"Data quality — last ETL run {ran_at:%b %d %H:%M}"):
        st.dataframe(pd.DataFrame([{"Source": k, "Rows in": v["rows_in"], "Loaded": v["rows_out"],
                                    "Dropped": v["rows_dropped"], "Duplicates removed": v["duplicates_removed"]}
                                   for k, v in stats.items()]), hide_index=True, use_container_width=True)
        st.caption("Full breakdown: docs/data_quality_report.md")
