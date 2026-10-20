from __future__ import annotations

from datetime import datetime

import pandas as pd
import streamlit as st

from core.classify import reapply_all, set_manual_category
from core.db import get_session
from core.queries import category_options, transactions_df

st.title("Transactions")

session = get_session()

if "date_from" not in st.session_state:
    st.session_state.date_from = datetime(datetime.now().year, datetime.now().month, 1)

# Basic page layout
start_date = st.date_input("From", value=st.session_state.date_from)
end_date = st.date_input("To", value=datetime.now().date())

df = transactions_df(session, start_date, end_date, hide_fees=True)

if not df.empty:
    st.write(df.head())
else:
    st.info("No transactions")

session.close()
