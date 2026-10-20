from __future__ import annotations

import pandas as pd
import streamlit as st
from sqlalchemy import select

from core.classify import classify_transactions
from core.db import get_session
from core.models import Counterparty, Transaction
from core.queries import (
    category_id_by_name,
    category_options,
    unreviewed_counterparties_df,
)

st.title("Classify merchants & people")
show_reviewed = st.checkbox("Show already reviewed", value=False)

with get_session() as session:
    rows = unreviewed_counterparties_df(session, include_reviewed=show_reviewed)
    options = category_options(session)
    category_ids = category_id_by_name(session)

if rows.empty:
    st.success("Nothing to review")
    st.stop()

table = rows.copy()
table["suggestion"] = table["current_category"]
table["category"] = [
    row["default_category"] or row["current_category"] or "" for _, row in table.iterrows()
]
edited = st.data_editor(
    table[
        [
            "id",
            "display_name",
            "kind",
            "txn_count",
            "total_out",
            "total_in",
            "sample_description",
            "suggestion",
            "category",
        ]
    ],
    use_container_width=True,
    hide_index=True,
    disabled=[
        "id",
        "display_name",
        "kind",
        "txn_count",
        "total_out",
        "total_in",
        "sample_description",
        "suggestion",
    ],
    column_config={
        "id": None,
        "total_out": st.column_config.NumberColumn("Spent", format="%,.2f"),
        "total_in": st.column_config.NumberColumn("Received", format="%,.2f"),
        "category": st.column_config.SelectboxColumn(
            "Category", options=["", *options]
        ),
    },
    key="review_editor",
)

save_col, accept_col = st.columns(2)
save_clicked = save_col.button("Save classifications", type="primary")
accept_clicked = accept_col.button("Accept all suggestions")
if save_clicked or accept_clicked:
    saved = 0
    with get_session() as session:
        affected_ids: list[int] = []
        for _, row in edited.iterrows():
            raw_category = row["suggestion"] if accept_clicked else row["category"]
            category_name = "" if pd.isna(raw_category) else str(raw_category).strip()
            category_id = category_ids.get(category_name)
            if category_id is None:
                continue
            counterparty_id = int(row["id"])
            counterparty = session.get(Counterparty, counterparty_id)
            if counterparty is None:
                st.error(f"Counterparty {counterparty_id} no longer exists. Refresh and retry.")
                session.rollback()
                st.stop()
            counterparty.default_category_id = category_id
            counterparty.reviewed = True
            affected_ids.extend(
                session.scalars(
                    select(Transaction.id).where(
                        Transaction.counterparty_id == counterparty_id
                    )
                ).all()
            )
            saved += 1
        if affected_ids:
            classify_transactions(session, affected_ids)
        session.commit()
    st.success(f"Saved {saved} classification(s).")
    st.rerun()

labels = rows["display_name"].fillna(rows["key"]).astype(str)
duplicate_labels = labels.duplicated(keep=False)
labels = labels.where(~duplicate_labels, labels + " (" + rows["key"].astype(str) + ")")
label_by_id = dict(zip(rows["id"].astype(int), labels))
selected_id = st.selectbox(
    "Show transactions for…",
    options=rows["id"].astype(int).tolist(),
    format_func=lambda counterparty_id: label_by_id[counterparty_id],
)
with get_session() as session:
    transactions = session.execute(
        select(
            Transaction.txn_at,
            Transaction.amount,
            Transaction.direction,
            Transaction.description,
            Transaction.category_source,
        )
        .where(Transaction.counterparty_id == selected_id)
        .order_by(Transaction.txn_at.desc(), Transaction.id.desc())
        .limit(100)
    ).all()
    transaction_rows = [
        {
            "Date": row.txn_at,
            "Amount": row.amount,
            "Direction": row.direction,
            "Description": row.description,
            "Classification": row.category_source,
        }
        for row in transactions
    ]
if transaction_rows:
    st.dataframe(
        pd.DataFrame(transaction_rows), use_container_width=True, hide_index=True
    )
else:
    st.info("No transactions found for this counterparty.")
