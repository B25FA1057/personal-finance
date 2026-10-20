from __future__ import annotations

from datetime import date
from decimal import Decimal

import pandas as pd
import plotly.express as px
import streamlit as st
from sqlalchemy import func, or_, select

from core.db import get_session
from core.models import Category, Transaction
from core.queries import (
    expense_categories_df,
    expense_trend_df,
    monthly_cashflow_df,
    top_expenses_df,
    top_merchant_expenses_df,
)


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
    monthly = monthly_cashflow_df(session, date_from, date_to)
    category_spending = expense_categories_df(session, date_from, date_to)
    top_merchants = top_merchant_expenses_df(session, date_from, date_to)
    largest_expenses = top_expenses_df(session, date_from, date_to)
    end_exclusive = date.fromordinal(date_to.toordinal() + 1)
    fees_total = session.scalar(
        select(func.coalesce(func.sum(Transaction.amount), 0)).where(
            Transaction.txn_at >= date_from,
            Transaction.txn_at < end_exclusive,
            Transaction.txn_type == "fee",
        )
    ) or Decimal("0.00")
    uncategorized_out = session.scalar(
        select(func.coalesce(func.sum(Transaction.amount), 0))
        .select_from(Transaction)
        .outerjoin(Category, Category.id == Transaction.category_id)
        .where(
            Transaction.txn_at >= date_from,
            Transaction.txn_at < date.fromordinal(date_to.toordinal() + 1),
            Transaction.direction == "out",
            Transaction.category_id.is_(None),
            Transaction.txn_type != "own_transfer",
            or_(Category.kind != "transfer", Category.kind.is_(None)),
        )
    ) or Decimal("0.00")

if monthly.empty:
    st.info("No categorized income or expenses in this date range.")
    income_total = expense_total = savings_total = net_total = Decimal("0.00")
else:
    income_total = sum(monthly["income"], Decimal("0.00"))
    expense_total = sum(monthly["expenses"], Decimal("0.00"))
    savings_total = sum(monthly["savings"], Decimal("0.00"))
    net_total = sum(monthly["net_cash_flow"], Decimal("0.00"))

kpis = st.columns(6)
kpis[0].metric("Income", _money(income_total))
kpis[1].metric("Expenses", _money(expense_total))
kpis[2].metric("Savings", _money(savings_total))
kpis[3].metric("Net cash flow", _money(net_total))
kpis[4].metric("Bank fees", _money(Decimal(str(fees_total))))
kpis[5].metric("Uncategorized out", _money(Decimal(str(uncategorized_out))))

st.subheader("Monthly cash flow")
if monthly.empty:
    st.info("Classify transactions as income or expense to see monthly cash flow.")
else:
    monthly_chart = monthly.melt(
        id_vars="month",
        value_vars=["income", "expenses", "savings", "net_cash_flow"],
        var_name="measure",
        value_name="amount",
    )
    monthly_chart["amount"] = monthly_chart["amount"].astype(float)
    monthly_chart["measure"] = monthly_chart["measure"].map(
        {
            "income": "Income",
            "expenses": "Expenses",
            "savings": "Savings",
            "net_cash_flow": "Net cash flow",
        }
    )
    st.plotly_chart(
        px.line(monthly_chart, x="month", y="amount", color="measure", markers=True),
        use_container_width=True,
    )

left, right = st.columns(2)
with left:
    st.subheader("Expenses by category")
    if category_spending.empty:
        st.info("No categorized expenses in this range.")
    else:
        chart_data = category_spending.copy()
        chart_data["amount"] = chart_data["amount"].astype(float)
        st.plotly_chart(
            px.bar(
                chart_data,
                x="amount",
                y="category",
                orientation="h",
                labels={"amount": "Amount", "category": "Category"},
            ).update_layout(yaxis={"categoryorder": "total ascending"}),
            use_container_width=True,
        )

with right:
    st.subheader("Top merchants & people")
    if top_merchants.empty:
        st.info("No categorized merchant spend in this range.")
    else:
        chart_data = top_merchants.copy()
        chart_data["amount"] = chart_data["amount"].astype(float)
        st.plotly_chart(
            px.bar(chart_data, x="amount", y="payee", orientation="h").update_layout(
                yaxis={"categoryorder": "total ascending"}
            ),
            use_container_width=True,
        )

st.subheader("Monthly expense trend")
if category_spending.empty:
    st.info("No expense categories are available for a trend.")
else:
    category_by_name = dict(
        zip(category_spending["category"], category_spending["category_id"])
    )
    selected_category = st.selectbox(
        "Expense category",
        options=category_spending["category"].tolist(),
    )
    with get_session() as session:
        trend = expense_trend_df(
            session=session,
            category_id=int(category_by_name[selected_category]),
            date_from=date_from,
            date_to=date_to,
        )
    if trend.empty:
        st.info("No spending recorded for this category in the selected dates.")
    else:
        trend["amount"] = trend["amount"].astype(float)
        st.plotly_chart(
            px.line(trend, x="month", y="amount", markers=True),
            use_container_width=True,
        )

st.subheader("Largest expenses")
if largest_expenses.empty:
    st.info("No categorized expenses in this range.")
else:
    st.dataframe(
        largest_expenses,
        use_container_width=True,
        hide_index=True,
        column_config={
            "txn_at": st.column_config.DatetimeColumn(
                "Date", format="YYYY-MM-DD HH:mm"
            ),
            "amount": st.column_config.NumberColumn("Amount", format="%,.2f ₮"),
        },
    )
