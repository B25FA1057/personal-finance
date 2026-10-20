from __future__ import annotations

import pandas as pd
import plotly.express as px
import streamlit as st

from core.db import get_session
from core.queries import transactions_df

st.title("Dashboard")

session = get_session()
df = transactions_df(session, None, None, hide_fees=False)
if df.empty:
    st.info("No data to chart yet.")
    session.close()
    st.stop()

st.write(df.head())
session.close()
