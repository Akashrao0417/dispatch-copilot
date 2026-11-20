"""NorthStar Dispatch — Streamlit entry point.

Run:  streamlit run frontend/app.py
Pages live in frontend/views/: Dashboard · Ask Copilot · Actions · Audit Log.
"""
import streamlit as st

st.set_page_config(page_title="NorthStar Dispatch", page_icon="🚚", layout="wide")

nav = st.navigation([
    st.Page("views/dashboard.py", title="Dashboard", icon="📊", default=True),
    st.Page("views/ask.py", title="Ask Copilot", icon="💬"),
    st.Page("views/actions.py", title="Actions", icon="✅"),
    st.Page("views/audit.py", title="Audit Log", icon="📜"),
])
nav.run()
