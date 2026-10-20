from __future__ import annotations

from datetime import datetime

import pandas as pd
import streamlit as st
from sqlalchemy import func, select

from core.config import DATABASE_URL
from core.db import get_session, init_db
from core.models import ImportBatch, Transaction
from core.queries import batches_df, unreviewed_counterparties_df

st.set_page_config(page_title="My Finance", layout="wide")

init_db()

session = get_session()

transactions_count = session.execute(select(func.count(Transaction.id))).scalar() or 0
start_date = session.execute(select(func.min(Transaction.txn_at))).scalar()
end_date = session.execute(select(func.max(Transaction.txn_at))).scalar()
review_count = len(unreviewed_counterparties_df(session))
last_import = session.execute(select(func.max(ImportBatch.imported_at))).scalar()

st.title("My Finance")
st.caption(f"Database: {DATABASE_URL}")

c1, c2, c3, c4 = st.columns(4)
c1.metric("Transactions", transactions_count)
c2.metric("Date range", f"{start_date.date() if start_date else 'n/a'} → {end_date.date() if end_date else 'n/a'}")
c3.metric("Needs review", review_count)
c4.metric("Last import", last_import.strftime("%Y-%m-%d %H:%M") if last_import else "n/a")

st.page_link("pages/2_Review.py", label="Review merchants & people")

st.subheader("Recent imports")
if not session.execute(select(func.count(ImportBatch.id))).scalar():
    st.info("No imports yet.")
else:
    st.dataframe(batches_df(session).head(10), use_container_width=True)

session.close()
