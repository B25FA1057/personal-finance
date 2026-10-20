from __future__ import annotations

import streamlit as st
from sqlalchemy import func, select

from core.db import get_session, init_db
from core.models import ImportBatch, Transaction
from core.queries import batches_df, unreviewed_counterparties_df

st.set_page_config(page_title="My Finance", layout="wide")

init_db()

st.title("My Finance")
st.caption("Your local personal finance overview")

with get_session() as session:
    transactions_count = session.scalar(select(func.count(Transaction.id))) or 0
    start_date = session.scalar(select(func.min(Transaction.txn_at)))
    end_date = session.scalar(select(func.max(Transaction.txn_at)))
    review_count = len(unreviewed_counterparties_df(session))
    last_import = session.scalar(select(func.max(ImportBatch.imported_at)))
    recent_imports = batches_df(session).head(10)

c1, c2, c3, c4 = st.columns(4)
c1.metric("Transactions", transactions_count)
c2.metric(
    "Date range",
    f"{start_date.date() if start_date else 'n/a'} → {end_date.date() if end_date else 'n/a'}",
)
c3.metric("Needs review", review_count)
c4.metric("Last import", last_import.strftime("%Y-%m-%d %H:%M") if last_import else "n/a")

nav_upload, nav_review, nav_transactions, nav_dashboard = st.columns(4)
nav_upload.page_link("pages/1_Upload.py", label="Upload statements")
nav_review.page_link("pages/2_Review.py", label="Review merchants & people")
nav_transactions.page_link("pages/3_Transactions.py", label="Browse transactions")
nav_dashboard.page_link("pages/4_Dashboard.py", label="Open dashboard")

st.subheader("Recent imports")
if recent_imports.empty:
    st.info("No imports yet.")
else:
    st.dataframe(recent_imports, use_container_width=True, hide_index=True)
