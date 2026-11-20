"""Ask the copilot in plain English. Answers come from Postgres (jobs) or the SOP PDFs (with citations)."""
from __future__ import annotations

import streamlit as st

from common import conn, role_badge, sidebar  # noqa: E402

from backend import copilot, llm  # noqa: E402
from backend.rag import SOP_DIR  # noqa: E402

user, as_of = sidebar()
st.title("Ask Copilot")
role_badge(user)
st.caption("Answers come only from NorthStar's own data and SOPs. "
           + ("Claude writes SOP answers." if llm.client() else "No API key set: SOP answers quote the matching section.")
           + " If the SOPs don't cover it, the copilot says **I don't know**.")

examples = (["Which jobs are late in Charlotte today, and why?", "Which jobs are in SLA breach today?",
             "How much credit does a Gold customer get for an SLA breach?", "Who can reassign a job?",
             "What is the vacation policy?"] if user.is_manager else
            ["Which of my jobs are late today?", "What should I do in a lightning storm?",
             "My van broke down on the highway, what do I do?", "Can I promise a customer a refund?"])
cols = st.columns(len(examples))
for col, ex in zip(cols, examples):
    if col.button(ex, use_container_width=True):
        st.session_state["question"] = ex

question = st.chat_input("Ask about jobs, delays or procedures…")
if question:
    st.session_state["question"] = question
q = st.session_state.get("question")

if q:
    with st.chat_message("user"):
        st.write(q)
    with conn() as c:
        ans = copilot.ask(c, user, q, as_of)
    with st.chat_message("assistant"):
        if ans.kind == "idk":
            st.markdown("**I don't know.** The SOPs and job data don't cover that. Try rephrasing, or ask your shift manager.")
        else:
            st.markdown(ans.text)
        if ans.kind == "jobs" and ans.rows:
            st.caption("Proof — straight from the jobs and tickets tables:")
            st.dataframe([{"Job": r["job_id"], "Customer": r["customer"], "Tier": r["tier"], "Technician": r["technician"],
                           "Min late": r["minutes_late"], "SLA breached": r["is_breach"], "Status": r["status"],
                           "Ticket": r["reason_ticket"] or "—", "Reason": r["reason"] or "—"} for r in ans.rows],
                         hide_index=True, use_container_width=True)
        if ans.citations:
            st.caption("What the SOP says:" if ans.kind == "jobs" else "Sources:")
            for cit in ans.citations:
                with st.expander(f"📄 {cit.label} — {cit.sop_title}: {cit.heading}  ({cit.file}, page {cit.page})",
                                 expanded=ans.kind == "jobs"):
                    st.markdown(f"> {cit.quote}")
                    pdf = SOP_DIR / cit.file
                    if pdf.exists():
                        st.download_button("Open PDF", pdf.read_bytes(), file_name=cit.file, mime="application/pdf",
                                           key=f"dl-{cit.label}")
        if ans.kind == "jobs" and ans.rows and user.is_manager:
            st.page_link("views/actions.py", label="Get suggested actions for these jobs →", icon="⚡")
