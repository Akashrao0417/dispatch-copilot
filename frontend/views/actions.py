"""Suggested actions: the copilot proposes, a person approves, then it runs. Everything is logged."""
from __future__ import annotations

import streamlit as st

from common import conn, role_badge, sidebar  # noqa: E402

from backend import agent, llm, queries  # noqa: E402
from backend.auth import PermissionDenied  # noqa: E402

TOOL_LABEL = {"reassign_technician": "🔁 Reassign technician", "create_ticket": "🎫 Create ticket",
              "draft_customer_update": "✉️ Customer update"}

user, as_of = sidebar()
st.title("Actions")
role_badge(user)
st.caption("Three tools only: reassign technician, create ticket, draft customer update. "
           "**Nothing runs until a person clicks Approve.** Every proposal, approval, rejection and refusal is logged.")

# ---------------------------------------------------------------- suggest
if user.is_manager:
    with st.container(border=True):
        c1, c2, c3 = st.columns([2, 1, 1])
        with conn() as c:
            cities = sorted({j["city"] for j in queries.jobs_for_day(c, user, as_of.date(), as_of)})
        city = c1.selectbox("City", ["All cities"] + cities)
        limit = c2.number_input("Jobs to plan", 1, 20, 5)
        c3.write("")
        if c3.button("⚡ Suggest actions", type="primary", use_container_width=True):
            with conn() as c:
                res = agent.suggest(c, user, as_of, limit=int(limit), city=None if city == "All cities" else city)
            msg = f"{len(res['created'])} new suggestion(s) from the {res['planner']} planner."
            if res["skipped"]:
                msg += f" {len(res['skipped'])} skipped by validation."
            st.toast(msg)
        st.caption("Planner: " + ("Claude (tool use)" if llm.client() else "SOP rules (set ANTHROPIC_API_KEY to use Claude)"))

# ---------------------------------------------------------------- technicians: create a ticket on own job
else:
    with st.container(border=True):
        st.markdown("**Log a ticket on one of your jobs**")
        with conn() as c:
            mine = queries.jobs_for_day(c, user, as_of.date(), as_of)
        if not mine:
            st.caption("You have no jobs today.")
        else:
            with st.form("ticket"):
                job_id = st.selectbox("Job", [j["job_id"] for j in mine],
                                      format_func=lambda j: next(f"{x['job_id']} · {x['customer']} · {x['status']}"
                                                                 for x in mine if x["job_id"] == j))
                category = st.selectbox("Category", agent.TICKET_CATEGORIES)
                notes = st.text_area("Notes", placeholder="e.g. I-77 closed at exit 10, ETA +40 min")
                if st.form_submit_button("Propose ticket"):
                    try:
                        with conn() as c:
                            agent.propose(c, user, "create_ticket",
                                          {"job_id": job_id, "category": category, "notes": notes}, as_of=as_of)
                        st.toast("Proposed — approve it below to log it.")
                    except (PermissionDenied, agent.ActionError) as e:
                        st.error(str(e))
        st.caption("Technicians can't reassign jobs (SOP-RA §4) or message customers (SOP-CU §6).")

# ---------------------------------------------------------------- pending queue
with conn() as c:
    pending = sorted(agent.list_actions(c, user, "pending"), key=lambda a: a["action_id"])
st.subheader(f"Waiting for approval ({len(pending)})")
if not pending:
    st.caption("Nothing waiting." + (" Click **Suggest actions** to plan the delayed jobs." if user.is_manager else ""))

for a in pending:
    args = a["args"]
    with st.container(border=True):
        top = st.columns([3, 1])
        top[0].markdown(f"**{TOOL_LABEL[a['tool']]} · {a['job_id']}**  \n"
                        f"<small>#{a['action_id']} · proposed by {a['proposed_by']} at {a['proposed_at']:%H:%M}</small>",
                        unsafe_allow_html=True)
        if a["citations"]:
            top[1].caption("📄 " + ", ".join(a["citations"]))
        if a["rationale"]:
            st.caption(a["rationale"])

        edited = {}
        if a["tool"] == "reassign_technician":
            st.markdown(f"Move **{a['job_id']}** to **{args['technician_id']}** — {args['reason']}")
        elif a["tool"] == "create_ticket":
            st.markdown(f"Category **{args['category']}** — {args['notes']}")
        else:
            st.caption(f"Channel: {args['channel'].upper()} — edit before approving if needed")
            new_msg = st.text_area("Message", args["message"], key=f"msg-{a['action_id']}", height=110,
                                   label_visibility="collapsed")
            if new_msg != args["message"]:
                edited = {"message": new_msg}

        b1, b2, _ = st.columns([1, 1, 4])
        if b1.button("Approve", key=f"ok-{a['action_id']}", type="primary"):
            try:
                with conn() as c:
                    res = agent.approve(c, user, a["action_id"], as_of=as_of, edited_args=edited or None)
                st.toast(f"Done: {res}")
                st.rerun()
            except (PermissionDenied, agent.ActionError) as e:
                st.error(f"Not executed: {e}")
        if b2.button("Reject", key=f"no-{a['action_id']}"):
            try:
                with conn() as c:
                    agent.reject(c, user, a["action_id"], as_of=as_of)
                st.rerun()
            except (PermissionDenied, agent.ActionError) as e:
                st.error(str(e))

# ---------------------------------------------------------------- history
with conn() as c:
    done = [x for x in agent.list_actions(c, user, limit=50) if x["status"] != "pending"]
if done:
    st.subheader("Recent decisions")
    st.dataframe([{"#": x["action_id"], "Tool": x["tool"], "Job": x["job_id"], "Status": x["status"],
                   "Decided by": x["decided_by"], "When": x["decided_at"], "Result": x["result"]} for x in done],
                 hide_index=True, use_container_width=True)
