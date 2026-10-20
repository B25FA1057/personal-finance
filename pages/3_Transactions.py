from __future__ import annotations

from datetime import date

import pandas as pd
import streamlit as st
from sqlalchemy import select

from core.classify import classify_transactions
from core.db import get_session
from core.models import Account, Category, Transaction
from core.queries import category_options, transactions_df

st.title("Transactions")

today = date.today()
default_from = date(today.year, today.month, 1)
for _ in range(2):
    default_from = (
        date(default_from.year - 1, 12, 1)
        if default_from.month == 1
        else date(default_from.year, default_from.month - 1, 1)
    )

with st.sidebar:
    st.header("Filters")
    start_date = st.date_input("From", value=default_from, key="transactions_from")
    end_date = st.date_input("To", value=today, key="transactions_to")
    with get_session() as session:
        accounts = session.execute(
            select(Account.id, Account.account_no).order_by(Account.account_no)
        ).all()
        categories = session.execute(
            select(Category.id, Category.name).order_by(
                Category.kind, Category.sort_order, Category.name
            )
        ).all()
    selected_accounts = st.multiselect(
        "Accounts",
        options=[int(row.id) for row in accounts],
        default=[int(row.id) for row in accounts],
        format_func=lambda account_id: next(
            row.account_no for row in accounts if row.id == account_id
        ),
    )
    selected_categories = st.multiselect(
        "Categories",
        options=[int(row.id) for row in categories],
        default=[int(row.id) for row in categories],
        format_func=lambda category_id: next(
            row.name for row in categories if row.id == category_id
        ),
    )
    include_uncategorized = st.checkbox("Include uncategorized", value=True)
    include_fees = st.checkbox("Include bank fees", value=True)
    txn_types = st.multiselect(
        "Transaction types",
        options=["card", "fee", "p2p_in", "p2p_out", "qpay", "topup", "own_transfer", "other"],
        default=["card", "fee", "p2p_in", "p2p_out", "qpay", "topup", "own_transfer", "other"],
    )
    search = st.text_input("Search description, merchant, or account")

if start_date > end_date:
    st.error("The start date must be on or before the end date.")
    st.stop()

with get_session() as session:
    original = transactions_df(
        session,
        start_date,
        end_date,
        account_ids=selected_accounts,
        category_ids=selected_categories,
        include_uncategorized=include_uncategorized,
        txn_types=txn_types,
        search=search or None,
        hide_fees=not include_fees,
    )
    category_names = category_options(session)
    category_ids = {
        name: int(category_id)
        for category_id, name in session.execute(select(Category.id, Category.name)).all()
    }

if original.empty:
    st.info("No transactions match these filters.")
    st.stop()

income_total = sum(
    (row.signed_amount for row in original.itertuples() if row.signed_amount > 0),
    start=0,
)
expense_total = -sum(
    (row.signed_amount for row in original.itertuples() if row.signed_amount < 0),
    start=0,
)
uncategorized_count = int(original["category"].isna().sum())
kpi_income, kpi_expense, kpi_count, kpi_uncategorized = st.columns(4)
kpi_income.metric("Money in", f"{income_total:,.2f} ₮")
kpi_expense.metric("Money out", f"{expense_total:,.2f} ₮")
kpi_count.metric("Transactions", len(original))
kpi_uncategorized.metric("Uncategorized", uncategorized_count)

editor = original.copy()
editor["category"] = editor["category"].fillna("")
edited = st.data_editor(
    editor,
    use_container_width=True,
    hide_index=True,
    disabled=[
        "id",
        "txn_at",
        "posted_at",
        "account_no",
        "direction",
        "amount",
        "signed_amount",
        "description",
        "merchant",
        "counterparty",
        "txn_type",
        "category_kind",
        "category_source",
    ],
    column_config={
        "id": None,
        "txn_at": st.column_config.DatetimeColumn("Transaction date", format="YYYY-MM-DD HH:mm"),
        "posted_at": st.column_config.DatetimeColumn("Posted date", format="YYYY-MM-DD HH:mm"),
        "amount": st.column_config.NumberColumn("Amount", format="%,.2f ₮"),
        "signed_amount": st.column_config.NumberColumn("Net amount", format="%,.2f ₮"),
        "category": st.column_config.SelectboxColumn(
            "Category", options=["", *category_names]
        ),
        "note": st.column_config.TextColumn("Note"),
    },
    key="transactions_editor",
)

if st.button("Save changes", type="primary"):
    original_by_id = original.set_index("id")
    changed_category_ids: list[int] = []
    changed_count = 0
    with get_session() as session:
        for row in edited.to_dict("records"):
            txn_id = int(row["id"])
            previous = original_by_id.loc[txn_id]
            txn = session.get(Transaction, txn_id)
            if txn is None:
                continue
            raw_category = row.get("category")
            category_name = (
                ""
                if raw_category is None or pd.isna(raw_category)
                else str(raw_category).strip()
            )
            category_id = category_ids.get(category_name)
            if category_name and category_id is None:
                st.error(f"Unknown category {category_name!r}; no changes were saved.")
                session.rollback()
                st.stop()

            previous_category = previous["category"]
            previous_category_name = (
                ""
                if previous_category is None or pd.isna(previous_category)
                else str(previous_category).strip()
            )
            if category_name != previous_category_name:
                if category_id is None:
                    txn.category_id = None
                    txn.category_source = "none"
                    changed_category_ids.append(txn_id)
                else:
                    txn.category_id = category_id
                    txn.category_source = "manual"
                changed_count += 1
            note = row.get("note")
            note = None if pd.isna(note) or not str(note).strip() else str(note).strip()
            previous_note = previous["note"]
            previous_note = (
                None
                if pd.isna(previous_note) or not str(previous_note).strip()
                else str(previous_note).strip()
            )
            if note != previous_note:
                txn.note = note
                changed_count += 1

        if changed_category_ids:
            classify_transactions(session, changed_category_ids)
        session.commit()
    st.success(f"Saved {changed_count} change(s).")
    st.rerun()

csv = original.to_csv(index=False).encode("utf-8-sig")
st.download_button(
    "Download filtered transactions (CSV)",
    data=csv,
    file_name=f"transactions_{start_date:%Y%m%d}_{end_date:%Y%m%d}.csv",
    mime="text/csv",
)

if st.button("Re-apply rules to all (keeps manual)"):
    from core.classify import reapply_all

    with get_session() as session:
        reclassified = reapply_all(session)
    st.success(f"Reclassified {reclassified} transaction(s).")
    st.rerun()
