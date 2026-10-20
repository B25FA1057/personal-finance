from __future__ import annotations

from datetime import date
from decimal import Decimal

import pandas as pd
import plotly.express as px
import streamlit as st

from core.db import get_session
from core.queries import transactions_df


def _money(value: Decimal) -> str:
    return f"{value:,.0f} ₮"


st.title("Dashboard")
today = date.today()
default_start = date(today.year, today.month, 1)
for _ in range(6):
    default_start = (
        date(default_start.year - 1, 12, 1)
        if default_start.month == 1
        else date(default_start.year, default_start.month - 1, 1)
    )

with st.sidebar:
    st.header("Dashboard filters")
    date_range = st.date_input(
        "Date range",
        value=(default_start, today),
        key="dashboard_date_range",
    )

if not isinstance(date_range, tuple) or len(date_range) != 2:
    st.info("Choose both a start and end date.")
    st.stop()
date_from, date_to = date_range
if date_from > date_to:
    st.error("The start date must be on or before the end date.")
    st.stop()

with get_session() as session:
    transactions = transactions_df(session, date_from, date_to, hide_fees=False)

if transactions.empty:
    st.info("No transactions in this date range.")
    st.stop()

income = transactions[transactions["category_kind"] == "income"]
expenses = transactions[transactions["category_kind"] == "expense"]
savings = transactions[transactions["category_kind"] == "savings"]
fees = transactions[transactions["txn_type"] == "fee"]
uncategorized_out = transactions[
    transactions["category"].isna() & (transactions["direction"] == "out")
]

income_total = sum(income["amount"], Decimal("0.00"))
expense_total = sum(expenses["amount"], Decimal("0.00"))
savings_total = sum(savings["amount"], Decimal("0.00"))
fee_total = sum(fees["amount"], Decimal("0.00"))
uncategorized_total = sum(uncategorized_out["amount"], Decimal("0.00"))

kpis = st.columns(6)
kpis[0].metric("Income", _money(income_total))
kpis[1].metric("Expenses", _money(expense_total))
kpis[2].metric("Savings", _money(savings_total))
kpis[3].metric("Net", _money(income_total - expense_total - savings_total))
kpis[4].metric("Bank fees", _money(fee_total))
kpis[5].metric("Uncategorized out", _money(uncategorized_total))

categorized = transactions[
    transactions["category_kind"].isin(["income", "expense", "savings"])
].copy()
if categorized.empty:
    st.info("Categorize transactions to see income, expense, and savings charts.")
else:
    categorized["month"] = pd.to_datetime(categorized["txn_at"]).dt.to_period("M").astype(str)
    categorized["amount_for_chart"] = categorized["amount"].astype(float)
    monthly = (
        categorized.groupby(["month", "category_kind"], as_index=False)["amount_for_chart"]
        .sum()
        .rename(columns={"category_kind": "Type", "amount_for_chart": "Amount"})
    )
    st.subheader("Monthly income, expenses & savings")
    st.plotly_chart(
        px.bar(monthly, x="month", y="Amount", color="Type", barmode="group"),
        use_container_width=True,
    )

expense_by_category = (
    expenses.groupby("category", as_index=False)["amount"]
    .sum()
    .rename(columns={"amount": "amount_for_chart"})
    .sort_values("amount_for_chart", ascending=False)
)
left, right = st.columns(2)
with left:
    st.subheader("Expenses by category")
    if expense_by_category.empty:
        st.info("No categorized expenses in this range.")
    else:
        st.plotly_chart(
            px.bar(
                expense_by_category,
                x="amount_for_chart",
                y="category",
                orientation="h",
                labels={"amount_for_chart": "Amount", "category": "Category"},
            ).update_layout(yaxis={"categoryorder": "total ascending"}),
            use_container_width=True,
        )

with right:
    st.subheader("Top merchants & people")
    spend = transactions[
        (transactions["direction"] == "out")
        & (~transactions["txn_type"].isin(["fee", "own_transfer"]))
        & transactions["category_kind"].eq("expense")
    ].copy()
    spend["payee"] = spend["merchant"].fillna(transactions["counterparty"]).fillna("Unspecified")
    top_spend = (
        spend.groupby("payee", as_index=False)["amount"]
        .sum()
        .sort_values("amount", ascending=False)
        .head(10)
    )
    if top_spend.empty:
        st.info("No categorized merchant or person spend in this range.")
    else:
        st.plotly_chart(
            px.bar(top_spend, x="amount", y="payee", orientation="h").update_layout(
                yaxis={"categoryorder": "total ascending"}
            ),
            use_container_width=True,
        )

st.subheader("Category spending trend")
expense_categories = sorted(expenses["category"].dropna().unique().tolist())
if not expense_categories:
    st.info("No categorized expenses to trend.")
else:
    selected_category = st.selectbox("Expense category", expense_categories)
    category_spend = expenses[expenses["category"] == selected_category].copy()
    category_spend["month"] = pd.to_datetime(category_spend["txn_at"]).dt.to_period("M").astype(str)
    category_monthly = (
        category_spend.groupby("month", as_index=False)["amount"]
        .sum()
        .rename(columns={"amount": "Amount"})
    )
    st.plotly_chart(
        px.line(category_monthly, x="month", y="Amount", markers=True),
        use_container_width=True,
    )

st.subheader("Largest expenses")
largest = (
    expenses[expenses["direction"] == "out"]
    .sort_values("amount", ascending=False)
    .head(10)
)
if largest.empty:
    st.info("No categorized expense transactions in this range.")
else:
    st.dataframe(
        largest[
            ["txn_at", "amount", "merchant", "counterparty", "description", "category"]
        ].rename(
            columns={
                "txn_at": "Date",
                "amount": "Amount",
                "merchant": "Merchant",
                "counterparty": "Counterparty",
                "description": "Description",
                "category": "Category",
            }
        ),
        use_container_width=True,
        hide_index=True,
    )
