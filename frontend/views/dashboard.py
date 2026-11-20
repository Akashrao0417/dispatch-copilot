"""Dashboard: total jobs, delayed jobs, SLA breaches, jobs per technician — scoped by role.

Definitions (same as README and backend/queries.py):
  Delayed      = not cancelled, and not finished by the end of its 2h arrival window
  SLA breach   = not cancelled, and not finished by its tier deadline
                 (Gold 2h, Silver 4h, Bronze 6h after scheduled time)
"""
from __future__ import annotations

import altair as alt
import pandas as pd
import streamlit as st

from common import conn, role_badge, sidebar  # noqa: E402

from backend import queries  # noqa: E402

ACCENT = "#2a6fdb"

user, as_of = sidebar()
day = as_of.date()

with conn() as c:
    rows = queries.jobs_for_day(c, user, day, as_of)
    with c.cursor() as cur:
        cur.execute("SELECT ran_at, stats FROM etl_runs ORDER BY run_id DESC LIMIT 1")
        run = cur.fetchone()
        cur.execute("SELECT count(*) AS n FROM proposed_actions WHERE status = 'pending'")
        pending = cur.fetchone()["n"]

st.title("NorthStar Dispatch")
role_badge(user)

if not rows:
    st.info(f"No jobs scheduled on {day:%b %d}. Turn off **Live** in the sidebar and pick a day that has data "
            "(or regenerate data with `python data/generate_data.py`).")
    st.stop()

jobs_all = pd.DataFrame(rows)
cities = sorted(jobs_all["city"].unique())
pick = st.multiselect("City", cities, default=cities) if user.is_manager else cities
jobs = jobs_all[jobs_all["city"].isin(pick)]
st.caption(f"{day:%A, %B %d, %Y} · open jobs measured as of {as_of:%H:%M}")

active = jobs[jobs["status"] != "Cancelled"]
k1, k2, k3, k4 = st.columns(4)
k1.metric("Total jobs", len(jobs), help="All jobs scheduled for this day, including cancelled")
k2.metric("Completed", int((jobs["status"] == "Completed").sum()))
k3.metric("⚠️ Delayed jobs", int(active["is_delayed"].sum()),
          help="Not finished by the end of the 2-hour arrival window")
k4.metric("⛔ SLA breaches", int(active["is_breach"].sum()),
          help="Not finished by the tier deadline: Gold 2h, Silver 4h, Bronze 6h")
if user.is_manager and pending:
    st.page_link("views/actions.py", label=f"{pending} action(s) waiting for approval →", icon="✅")

st.subheader("Delayed jobs")
late = jobs[jobs["is_delayed"]].sort_values(["is_breach", "minutes_late"], ascending=False)
if late.empty:
    st.success("No delayed jobs for this selection.")
else:
    view = late.assign(
        SLA=late["is_breach"].map({True: "⛔ Breached", False: "⚠️ At risk"}),
        Late=late["minutes_late"].map(lambda m: f"{m // 60}h {m % 60:02d}m"),
        Scheduled=pd.to_datetime(late["scheduled_at"]).dt.strftime("%H:%M"),
        Reason=late["reason"].fillna("— no ticket logged —"),
        Note=late["reason_note"].fillna(""),
    )[["job_id", "SLA", "Late", "status", "city", "customer", "tier", "technician", "Scheduled", "Reason", "Note"]]
    st.dataframe(view.rename(columns={"job_id": "Job", "status": "Status", "city": "City", "customer": "Customer",
                                      "tier": "Tier", "technician": "Technician"}),
                 hide_index=True, use_container_width=True)

if user.is_manager:
    st.subheader("Jobs per technician")
    per_tech = (jobs.groupby(["technician_id", "technician"])
                .agg(jobs=("job_id", "count"), delayed=("is_delayed", "sum"))
                .reset_index().sort_values(["jobs", "technician"], ascending=[False, True]))
    per_tech["label"] = per_tech["technician"] + " (" + per_tech["technician_id"] + ")"
    st.caption(f"{len(per_tech)} technicians have work on this day for the selected cities.")
    height = max(160, 22 * len(per_tech))
    chart = (alt.Chart(per_tech)
             .mark_bar(color=ACCENT, cornerRadiusEnd=4, size=14)
             .encode(x=alt.X("jobs:Q", title="Jobs", axis=alt.Axis(tickMinStep=1, gridOpacity=0.3)),
                     y=alt.Y("label:N", sort=None, title=None, axis=alt.Axis(labelLimit=220)),
                     tooltip=[alt.Tooltip("technician:N", title="Technician"),
                              alt.Tooltip("technician_id:N", title="ID"),
                              alt.Tooltip("jobs:Q", title="Jobs"), alt.Tooltip("delayed:Q", title="Delayed")])
             .properties(height=height))
    c1, c2 = st.columns([3, 2])
    c1.altair_chart(chart, use_container_width=True)
    c2.dataframe(per_tech[["technician", "technician_id", "jobs", "delayed"]]
                 .rename(columns={"technician": "Technician", "technician_id": "ID", "jobs": "Jobs", "delayed": "Delayed"}),
                 hide_index=True, use_container_width=True, height=min(height, 420))

with st.expander("All jobs for this day" if user.is_manager else "All my jobs today"):
    st.dataframe(jobs.drop(columns=["reason_note", "customer_email"]), hide_index=True, use_container_width=True)

if run and user.is_manager:
    stats = run["stats"]
    with st.expander(f"Data quality — last ETL run {run['ran_at']:%b %d %H:%M}"):
        st.dataframe(pd.DataFrame([{"Source": k, "Rows in": v["rows_in"], "Loaded": v["rows_out"],
                                    "Dropped": v["rows_dropped"], "Duplicates removed": v["duplicates_removed"]}
                                   for k, v in stats.items()]), hide_index=True, use_container_width=True)
        st.caption("Full breakdown: docs/data_quality_report.md")
