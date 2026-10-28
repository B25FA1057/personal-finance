from __future__ import annotations

import streamlit as st

from core.db import init_db
from core.ui import apply_app_style

st.set_page_config(
    page_title="Personal Finance",
    page_icon=":material/account_balance_wallet:",
    layout="wide",
    initial_sidebar_state="expanded",
)

init_db()
apply_app_style()

pages = {
    "Overview": [
        st.Page(
            "pages/4_Dashboard.py",
            title="Dashboard",
            icon=":material/dashboard:",
            default=True,
        ),
        st.Page(
            "pages/8_Reports.py",
            title="Reports",
            icon=":material/assessment:",
        ),
    ],
    "Activity": [
        st.Page("pages/3_Transactions.py", title="Transactions", icon=":material/swap_vert:"),
        st.Page("pages/1_Upload.py", title="Import statements", icon=":material/upload_file:"),
        st.Page("pages/2_Review.py", title="Review categories", icon=":material/rule:"),
    ],
    "Planning": [
        st.Page("pages/6_Budgets.py", title="Budgets", icon=":material/savings:"),
        st.Page("pages/7_Savings_Goals.py", title="Savings goals", icon=":material/flag:"),
    ],
    "Preferences": [
        st.Page("pages/5_Settings.py", title="Settings", icon=":material/settings:"),
    ],
}
st.navigation(pages).run()
