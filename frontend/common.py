"""Shared Streamlit helpers: DB access, demo sign-in, and the 'as of' clock.

Sign-in here is a DEMO picker (no passwords) so you can switch between Manager and
Technician views. Real access control still happens in the backend: every query and
every action is checked against the signed-in user's role.
"""
from __future__ import annotations

import sys
from datetime import datetime, time
from pathlib import Path

import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # make `backend` importable

from backend import db  # noqa: E402
from backend.auth import User, get_user, list_users  # noqa: E402


def conn():
    """Context-managed connection: `with conn() as c: ...` (commits on success)."""
    return db.connect()


@st.cache_data(ttl=60)
def _user_options() -> list[tuple[str, str]]:
    with conn() as c:
        db.init_app_tables(c)
    with conn() as c:
        users = list_users(c)
    return [(u.username, f"Manager · {u.name}" if u.is_manager else f"Technician · {u.technician_id} {u.name}")
            for u in users]


def sidebar() -> tuple[User, datetime]:
    """Render the sign-in + clock in the sidebar; returns (user, as_of)."""
    try:
        options = _user_options()
    except Exception as exc:
        st.error(f"Can't reach Postgres at `{db.DATABASE_URL}`. Start it and run `python -m backend.etl`.\n\n{exc}")
        st.stop()
    labels = dict(options)
    names = [u for u, _ in options]
    with st.sidebar:
        st.markdown("### Signed in as")
        now = datetime.now().replace(second=0, microsecond=0)
        # Streamlit forgets widget state on page switch, so keep the real values under
        # "_" keys and copy them into the widgets on every run.
        ss = st.session_state
        ss.setdefault("_username", "manager")
        ss.setdefault("_live", True)
        ss.setdefault("_as_of_date", now.date())
        ss.setdefault("_as_of_time", time(14, 0))
        if ss["_username"] not in names:
            ss["_username"] = names[0]
        for k in ("username", "live", "as_of_date", "as_of_time"):
            ss[f"w_{k}"] = ss[f"_{k}"]

        def keep(k):
            return lambda: ss.update({f"_{k}": ss[f"w_{k}"]})

        username = st.selectbox("User", names, format_func=labels.get, label_visibility="collapsed",
                                key="w_username", on_change=keep("username"))
        st.caption("Demo sign-in. Permissions are enforced in the backend.")

        st.markdown("### Clock")
        if st.toggle("Live (now)", key="w_live", on_change=keep("live")):
            as_of = now
        else:
            d = st.date_input("Date", key="w_as_of_date", on_change=keep("as_of_date"))
            t = st.time_input("Time", key="w_as_of_time", step=900, on_change=keep("as_of_time"))
            as_of = datetime.combine(d, t)
        st.caption(f"As of **{as_of:%a %b %d, %H:%M}**")
    with conn() as c:
        user = get_user(c, username)
    return user, as_of


def role_badge(user: User) -> None:
    if user.is_manager:
        st.caption(f"👤 **{user.name}** · Manager — you see every job.")
    else:
        st.caption(f"👤 **{user.name}** ({user.technician_id}) · Technician — you see only your own jobs.")
