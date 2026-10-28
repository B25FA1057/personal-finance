from __future__ import annotations

import pandas as pd
import streamlit as st
from sqlalchemy import select

from core import config
from core.ai_categorization import (
    eligible_category_options,
    eligible_transaction_count,
    resolve_suggestion,
)
from core.categorization_jobs import enqueue_categorization
from core.classify import classify_transactions
from core.db import get_session
from core.models import (
    CategorizationSuggestion,
    Category,
    Counterparty,
    Transaction,
)
from core.queries import (
    category_id_by_name,
    category_options,
    unreviewed_counterparties_df,
)
from core.ui import remember_categorization_job, render_categorization_jobs


st.title("Review categories")

st.subheader("AI transaction suggestions")
st.caption(
    "AI results are suggestions only. A transaction is not categorized until you accept "
    "or edit it. Verified own-account transfers are never sent for AI categorization."
)

st.info(
    f"AI suggestions run locally through Ollama ({config.OLLAMA_MODEL}); "
    "transaction data is not sent to a cloud AI provider."
)

with get_session() as session:
    eligible_count = eligible_transaction_count(session)
    categories = list(session.scalars(select(Category).order_by(Category.kind, Category.name)))
    pending = session.execute(
        select(
            CategorizationSuggestion.transaction_id,
            Transaction.txn_at,
            Transaction.direction,
            Transaction.amount,
            Transaction.merchant,
            Transaction.description,
            Transaction.txn_type,
            Category.name.label("suggested_category"),
            CategorizationSuggestion.confidence,
            CategorizationSuggestion.rationale,
        )
        .join(
            Transaction,
            Transaction.id == CategorizationSuggestion.transaction_id,
        )
        .join(Category, Category.id == CategorizationSuggestion.category_id)
        .where(
            CategorizationSuggestion.status == "pending",
            Transaction.category_id.is_(None),
            Transaction.category_source == "none",
            Transaction.txn_type != "own_transfer",
        )
        .order_by(Transaction.txn_at.desc(), Transaction.id.desc())
    ).all()

st.caption(f"{eligible_count} uncategorized transaction(s) can be categorized.")
ai_consent = st.checkbox(
    "I agree to run local Ollama categorization using redacted transaction details.",
    value=False,
    key="review_ai_consent",
)
if st.button(
    "Categorize eligible transactions in the background",
    disabled=not ai_consent or eligible_count == 0,
    key="run_ai_review",
):
    job_id = enqueue_categorization(None, retry=True)
    remember_categorization_job(job_id)
    st.rerun()

job_ids = list(st.session_state.get("categorization_job_ids", []))
if job_ids:
    render_categorization_jobs(job_ids)

if pending:
    pending_frame = pd.DataFrame(
        [
            {
                "id": row.transaction_id,
                "date": row.txn_at,
                "merchant": row.merchant or "",
                "description": row.description,
                "direction": row.direction,
                "amount": row.amount,
                "suggested category": row.suggested_category,
                "confidence": float(row.confidence),
                "reason": row.rationale,
            }
            for row in pending
        ]
    )
    st.dataframe(
        pending_frame.drop(columns=["id"]),
        use_container_width=True,
        hide_index=True,
        column_config={
            "amount": st.column_config.NumberColumn("Amount", format="%,.2f"),
            "confidence": st.column_config.NumberColumn("Confidence", format="%.0%%"),
        },
    )
    suggestion_ids = [int(row.transaction_id) for row in pending]
    label_by_id = {
        int(row.transaction_id): (
            f"{row.txn_at:%Y-%m-%d} — {row.merchant or row.description[:60]} "
            f"({row.suggested_category})"
        )
        for row in pending
    }
    selected_id = st.selectbox(
        "Choose a suggestion to review",
        suggestion_ids,
        format_func=lambda transaction_id: label_by_id[transaction_id],
        key="ai_suggestion_selection",
    )
    selected = next(row for row in pending if row.transaction_id == selected_id)
    with get_session() as session:
        transaction = session.get(Transaction, selected_id)
        allowed_categories = (
            eligible_category_options(transaction, categories) if transaction else []
        )
    if transaction is None:
        st.error("The selected transaction no longer exists. Refresh and retry.")
    elif not allowed_categories:
        st.error("No compatible existing category is available for this transaction.")
    else:
        st.write(f"**Transaction:** {selected.description}")
        st.write(
            f"**Amount:** {selected.direction} {selected.amount} · "
            f"**AI suggestion:** {selected.suggested_category} · "
            f"**Confidence:** {float(selected.confidence):.0%}"
        )
        st.caption(selected.rationale)
        category_names = [category.name for category in allowed_categories]
        suggestion_category = st.selectbox(
            "Accept or edit category",
            category_names,
            index=(
                category_names.index(selected.suggested_category)
                if selected.suggested_category in category_names
                else 0
            ),
            key=f"ai_category_{selected_id}",
        )
        merchant_available = (
            transaction.txn_type == "card" and bool(transaction.merchant)
        )
        create_rule = st.checkbox(
            "Create or update a reusable exact-match rule for future transactions",
            value=False,
            key=f"ai_create_rule_{selected_id}",
        )
        rule_field = st.selectbox(
            "Rule matches",
            ["merchant", "description"] if merchant_available else ["description"],
            key=f"ai_rule_field_{selected_id}",
        )
        accept_col, reject_col = st.columns(2)
        if accept_col.button("Confirm category", type="primary", key=f"ai_accept_{selected_id}"):
            try:
                with get_session() as session:
                    resolve_suggestion(
                        session,
                        selected_id,
                        action="accept",
                        category_id=category_id_by_name(session).get(suggestion_category),
                        create_rule=create_rule,
                        rule_field=rule_field,
                    )
                    session.commit()
            except ValueError as exc:
                st.error(str(exc))
            else:
                st.success("Category confirmed.")
                st.rerun()
        if reject_col.button("Reject suggestion", key=f"ai_reject_{selected_id}"):
            with get_session() as session:
                resolve_suggestion(session, selected_id, action="reject")
                session.commit()
            st.info("Suggestion rejected; it will not be requested again.")
            st.rerun()
else:
    st.info("No AI suggestions are waiting for review.")

st.divider()
st.subheader("Merchant and counterparty mappings")
show_reviewed = st.checkbox("Show already reviewed", value=False)

with get_session() as session:
    rows = unreviewed_counterparties_df(session, include_reviewed=show_reviewed)
    options = category_options(session)
    category_ids = category_id_by_name(session)

if rows.empty:
    st.success("No merchants or people need review.")
else:
    table = rows.copy()
    table["suggestion"] = table["current_category"]
    table["category"] = [
        row["default_category"] or row["current_category"] or ""
        for _, row in table.iterrows()
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
                    st.error(
                        f"Counterparty {counterparty_id} no longer exists. Refresh and retry."
                    )
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
    labels = labels.where(
        ~duplicate_labels, labels + " (" + rows["key"].astype(str) + ")"
    )
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
