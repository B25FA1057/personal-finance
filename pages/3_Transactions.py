from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import pandas as pd
import streamlit as st
from sqlalchemy import select

from core.classify import classify_transactions, reapply_all
from core.db import get_session
from core.models import Account, Category, Transaction
from core.queries import (
    category_options,
    merchant_options,
    transaction_count,
    transactions_df,
)

TRANSACTION_TYPES = [
    "card",
    "fee",
    "p2p_in",
    "p2p_out",
    "qpay",
    "topup",
    "own_transfer",
    "other",
]
PAGE_SIZE = 100


@dataclass(frozen=True)
class TransactionEdit:
    id: int
    description: str
    txn_type: str
    category_id: int | None
    note: str | None


st.title("Transactions")
today = date.today()
default_from = date(today.year, today.month, 1)
for _ in range(2):
    default_from = (
        date(default_from.year - 1, 12, 1)
        if default_from.month == 1
        else date(default_from.year, default_from.month - 1, 1)
    )

with get_session() as session:
    accounts = session.execute(
        select(Account.id, Account.account_no).order_by(Account.account_no)
    ).all()
    categories = session.execute(
        select(Category.id, Category.name).order_by(
            Category.kind, Category.sort_order, Category.name
        )
    ).all()
    merchants = merchant_options(session)

account_names = {int(row.id): row.account_no for row in accounts}
category_names_by_id = {int(row.id): row.name for row in categories}
category_ids_by_name = {row.name: int(row.id) for row in categories}

with st.sidebar:
    st.header("Filters")
    start_date = st.date_input("From", value=default_from, key="transactions_from")
    end_date = st.date_input("To", value=today, key="transactions_to")
    selected_accounts = st.multiselect(
        "Accounts",
        options=list(account_names),
        default=list(account_names),
        format_func=account_names.__getitem__,
    )
    selected_categories = st.multiselect(
        "Categories",
        options=list(category_names_by_id),
        default=list(category_names_by_id),
        format_func=category_names_by_id.__getitem__,
    )
    selected_merchants = st.multiselect(
        "Merchant / payee", options=merchants, default=merchants
    )
    include_uncategorized = st.checkbox("Include uncategorized", value=True)
    include_fees = st.checkbox("Include bank fees", value=True)
    txn_types = st.multiselect(
        "Transaction types",
        options=TRANSACTION_TYPES,
        default=TRANSACTION_TYPES,
    )
    search = st.text_input("Search descriptions, merchants, or accounts")

if start_date > end_date:
    st.error("The start date must be on or before the end date.")
    st.stop()

filters = {
    "date_from": start_date,
    "date_to": end_date,
    "account_ids": selected_accounts,
    "category_ids": selected_categories,
    "include_uncategorized": include_uncategorized,
    "txn_types": txn_types,
    "search": search or None,
    "hide_fees": not include_fees,
    "merchant_names": selected_merchants,
}
with get_session() as session:
    matching_count = transaction_count(session, **filters)
    page_count = max(1, (matching_count + PAGE_SIZE - 1) // PAGE_SIZE)
    current_page = min(max(1, int(st.session_state.get("transaction_page", 1))), page_count)
    st.session_state.transaction_page = current_page

    previous_col, page_col, next_col, summary_col = st.columns([1, 1, 1, 5])
    if previous_col.button("Previous", disabled=current_page <= 1):
        st.session_state.transaction_page = current_page - 1
        st.rerun()
    page_col.caption(f"Page {current_page} of {page_count}")
    if next_col.button("Next", disabled=current_page >= page_count):
        st.session_state.transaction_page = current_page + 1
        st.rerun()
    summary_col.caption(
        f"{matching_count:,} matching transaction(s) · {PAGE_SIZE} per page"
    )
    original = transactions_df(
        session,
        **filters,
        limit=PAGE_SIZE,
        offset=(int(current_page) - 1) * PAGE_SIZE,
    )
    category_names = category_options(session)

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
kpi_income.metric("Money in (this page)", f"{income_total:,.2f} ₮")
kpi_expense.metric("Money out (this page)", f"{expense_total:,.2f} ₮")
kpi_count.metric("Transactions", f"{matching_count:,}")
kpi_uncategorized.metric("Uncategorized (this page)", uncategorized_count)

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
        "counterparty",
        "merchant",
        "category_kind",
        "category_source",
    ],
    column_config={
        "id": None,
        "category_id": None,
        "txn_at": st.column_config.DatetimeColumn(
            "Transaction date", format="YYYY-MM-DD HH:mm"
        ),
        "posted_at": st.column_config.DatetimeColumn(
            "Posted date", format="YYYY-MM-DD HH:mm"
        ),
        "amount": st.column_config.NumberColumn("Amount", format="%,.2f ₮"),
        "signed_amount": st.column_config.NumberColumn("Net amount", format="%,.2f ₮"),
        "description": st.column_config.TextColumn("Description", required=True),
        "category": st.column_config.SelectboxColumn(
            "Category", options=["", *category_names]
        ),
        "txn_type": st.column_config.SelectboxColumn(
            "Transaction type", options=TRANSACTION_TYPES, required=True
        ),
        "note": st.column_config.TextColumn("Note"),
    },
    key="transactions_editor",
)

if st.button("Save changes", type="primary"):
    original_by_id = original.set_index("id")
    edited_rows = edited.to_dict("records")
    validation_errors: list[str] = []
    updates: list[TransactionEdit] = []
    seen_ids: set[int] = set()
    for row_number, row in enumerate(edited_rows, start=1):
        try:
            txn_id = int(row["id"])
        except (TypeError, ValueError, KeyError):
            validation_errors.append(f"Row {row_number}: transaction id is invalid.")
            continue
        if txn_id not in original_by_id.index:
            validation_errors.append(f"Row {row_number}: transaction is outside the current page.")
            continue
        if txn_id in seen_ids:
            validation_errors.append(f"Row {row_number}: transaction appears more than once.")
            continue
        seen_ids.add(txn_id)
        description = str(row.get("description") or "").strip()
        txn_type = str(row.get("txn_type") or "").strip()
        raw_category = row.get("category")
        category_name = (
            ""
            if raw_category is None or pd.isna(raw_category)
            else str(raw_category).strip()
        )
        if not description:
            validation_errors.append(f"Row {row_number}: description cannot be empty.")
        if txn_type not in TRANSACTION_TYPES:
            validation_errors.append(f"Row {row_number}: choose a valid transaction type.")
        if category_name and category_name not in category_ids_by_name:
            validation_errors.append(f"Row {row_number}: choose an existing category.")
        raw_note = row.get("note")
        note = None if raw_note is None or pd.isna(raw_note) else str(raw_note).strip() or None
        updates.append(
            TransactionEdit(
                id=txn_id,
                description=description,
                txn_type=txn_type,
                category_id=category_ids_by_name.get(category_name),
                note=note,
            )
        )

    if validation_errors:
        for error in validation_errors:
            st.error(error)
    else:
        changed_count = 0
        reclassify_ids: list[int] = []
        with get_session() as session:
            current_transactions = {
                txn.id: txn
                for txn in session.scalars(
                    select(Transaction).where(
                        Transaction.id.in_([update.id for update in updates])
                    )
                ).all()
            }
            for update in updates:
                txn_id = update.id
                txn = current_transactions.get(txn_id)
                previous = original_by_id.loc[txn_id]
                if txn is None:
                    validation_errors.append(
                        f"Transaction {txn_id} was deleted by another change; refresh before saving."
                    )
                    break
                if (
                    txn.description != previous["description"]
                    or txn.txn_type != previous["txn_type"]
                    or txn.category_id != previous["category_id"]
                    or txn.note != previous["note"]
                ):
                    validation_errors.append(
                        f"Transaction {txn_id} changed since this page loaded; refresh before saving."
                    )
                    break

            if validation_errors:
                session.rollback()
                for error in validation_errors:
                    st.error(error)
            else:
                for update in updates:
                    txn_id = update.id
                    txn = current_transactions.get(txn_id)
                    if txn is None:
                        continue
                    new_category_id = update.category_id
                    previous_category = txn.category_id
                    if txn.description != update.description:
                        txn.description = update.description
                        reclassify_ids.append(txn_id)
                        changed_count += 1
                    if txn.txn_type != update.txn_type:
                        txn.txn_type = update.txn_type
                        reclassify_ids.append(txn_id)
                        changed_count += 1
                    if previous_category != new_category_id:
                        txn.category_id = (
                            int(new_category_id) if new_category_id is not None else None
                        )
                        txn.category_source = "manual" if new_category_id is not None else "none"
                        if new_category_id is None:
                            reclassify_ids.append(txn_id)
                        changed_count += 1
                    if txn.note != update.note:
                        txn.note = update.note
                        changed_count += 1
                if reclassify_ids:
                    classify_transactions(session, list(dict.fromkeys(reclassify_ids)))
                session.commit()
        if not validation_errors:
            st.success(f"Saved {changed_count} change(s).")
            st.rerun()

csv = original.to_csv(index=False).encode("utf-8-sig")
st.download_button(
    "Download this page as CSV",
    data=csv,
    file_name=f"transactions_{start_date:%Y%m%d}_{end_date:%Y%m%d}_page{int(current_page)}.csv",
    mime="text/csv",
)

if st.button("Re-apply rules to all (keeps manual)"):
    with get_session() as session:
        reclassified = reapply_all(session)
    st.success(f"Reclassified {reclassified} transaction(s).")
    st.rerun()
