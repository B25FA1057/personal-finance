from __future__ import annotations

from datetime import date

import pandas as pd
import streamlit as st

from core.db import get_session
from core.queries import (
    expense_categories_df,
    monthly_cashflow_df,
    monthly_uncategorized_outflows_df,
)


st.title("Reports")
st.caption("Monthly cash flow and category totals from your imported transactions.")
st.caption(
    "Internal transfers are excluded. Income includes uncategorized inflows; "
    "uncategorized outflows are included in net cash flow."
)

today = date.today()
start_default = date(today.year, 1, 1)
selected_range = st.date_input(
    "Reporting period",
    value=(start_default, today),
    key="reports_date_range",
)
if not isinstance(selected_range, tuple) or len(selected_range) != 2:
    st.info("Choose both a start and end date.")
    st.stop()
date_from, date_to = selected_range
if date_from > date_to:
    st.error("The start date must be on or before the end date.")
    st.stop()

with get_session() as session:
    monthly = monthly_cashflow_df(session, date_from, date_to)
    uncategorized_outflows = monthly_uncategorized_outflows_df(
        session, date_from, date_to
    )
    categories = expense_categories_df(session, date_from, date_to)

if monthly.empty:
    st.info("No categorized income or expenses were found in this period.")
else:
    report = monthly.copy()
    report["income"] = report["income"] + report["uncategorized_income"]
    outflow_by_month = dict(
        zip(uncategorized_outflows["month"], uncategorized_outflows["amount"])
    )
    report["uncategorized_outflows"] = [
        outflow_by_month.get(month, 0) for month in report["month"]
    ]
    report["net_cash_flow"] = (
        report["net_cash_flow"] - report["uncategorized_outflows"]
    )
    report = report.drop(columns=["uncategorized_income"])
    report = report[
        [
            "month",
            "income",
            "expenses",
            "savings",
            "uncategorized_outflows",
            "net_cash_flow",
        ]
    ]
    report.columns = [
        "Month",
        "Income",
        "Expenses (net of refunds)",
        "Savings moved",
        "Uncategorized outflows",
        "Net cash flow",
    ]
    st.subheader("Monthly cash flow")
    st.dataframe(
        report,
        use_container_width=True,
        hide_index=True,
        column_config={
            column: st.column_config.NumberColumn(column, format="%,.2f ₮")
            for column in report.columns
            if column != "Month"
        },
    )
    st.download_button(
        "Download monthly report",
        data=report.to_csv(index=False).encode("utf-8-sig"),
        file_name=f"monthly_report_{date_from:%Y%m%d}_{date_to:%Y%m%d}.csv",
        mime="text/csv",
    )

st.subheader("Expense categories")
if categories.empty:
    st.info("No categorized expense activity was found in this period.")
else:
    st.dataframe(
        categories.rename(
            columns={"category": "Category", "amount": "Net expenses"}
        ).drop(columns=["category_id"]),
        use_container_width=True,
        hide_index=True,
        column_config={
            "Net expenses": st.column_config.NumberColumn(
                "Net expenses", format="%,.2f ₮"
            )
        },
    )
