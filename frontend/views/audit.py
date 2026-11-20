"""Audit log: who did what, when. Managers see everything; technicians see their own entries."""
from __future__ import annotations

import pandas as pd
import streamlit as st

from common import conn, role_badge, sidebar  # noqa: E402

from backend import agent  # noqa: E402

user, as_of = sidebar()
st.title("Audit log")
role_badge(user)
st.caption("Append-only record of every proposal, approval, rejection, execution and refused attempt.")

with conn() as c:
    rows = agent.audit_entries(c, user, limit=500)
if not rows:
    st.info("No activity yet.")
    st.stop()

df = pd.DataFrame(rows)
events = sorted(df["event"].unique())
pick = st.multiselect("Events", events, default=events)
df = df[df["event"].isin(pick)]
st.dataframe(df[["ts", "actor", "role", "event", "tool", "job_id", "action_id", "details"]]
             .rename(columns={"ts": "When", "actor": "Who", "role": "Role", "event": "What", "tool": "Tool",
                              "job_id": "Job", "action_id": "Action #", "details": "Details"}),
             hide_index=True, use_container_width=True)
st.download_button("Download CSV", df.to_csv(index=False), "audit_log.csv", "text/csv")
