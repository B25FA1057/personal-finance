from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import pandas as pd
import plotly.express as px
import streamlit as st

from core.db import get_session
from core.queries import (
    budget_performance_df,
    expense_categories_df,
    expense_trend_df,
    internal_transfer_totals,
    monthly_uncategorized_outflows_df,
    monthly_cashflow_df,
    transactions_df,
    uncategorized_cashflow_totals,
)


_COLORS = {
    "income": "#278060",
    "expenses": "#c76d68",
    "net": "#526779",
    "budget": "#9aa9a0",
}


def _period_dates(period: str, today: date) -> tuple[date, date]:
    if period == "This month":
        return date(today.year, today.month, 1), today
    if period == "Last month":
        end = date(today.year, today.month, 1) - timedelta(days=1)
        return date(end.year, end.month, 1), end
    if period == "Last 3 months":
        month = today.month - 2
        year = today.year
        while month < 1:
            month += 12
            year -= 1
        return date(year, month, 1), today
    return date(today.year, 1, 1), today


def _money(value: Decimal) -> str:
    return f"{value:,.0f} ₮"


st.title("Overview")
st.caption("A clear view of your cash flow, spending, and plans.")

today = date.today()
with st.sidebar:
    st.subheader("Reporting period")
    selected_period = st.selectbox(
        "Period",
        ["This month", "Last month", "Last 3 months", "This year", "Custom"],
        label_visibility="collapsed",
        key="dashboard_period",
    )
    if selected_period == "Custom":
        selected_range = st.date_input(
            "Choose dates",
            value=(date(today.year, 1, 1), today),
            key="dashboard_custom_range",
        )
        if not isinstance(selected_range, tuple) or len(selected_range) != 2:
            st.info("Choose both a start and end date.")
            st.stop()
        date_from, date_to = selected_range
    else:
        date_from, date_to = _period_dates(selected_period, today)
    if date_from > date_to:
        st.error("The start date must be on or before the end date.")
        st.stop()
    st.caption(f"{date_from:%b %d, %Y} — {date_to:%b %d, %Y}")

with get_session() as session:
    monthly = monthly_cashflow_df(session, date_from, date_to)
    uncategorized_outflows = monthly_uncategorized_outflows_df(
        session, date_from, date_to
    )
    category_spending = expense_categories_df(session, date_from, date_to)
    trend = expense_trend_df(session, date_from=date_from, date_to=date_to)
    budgets = budget_performance_df(session, date_from, date_to)
    recent = transactions_df(
        session,
        date_from=date_from,
        date_to=date_to,
        limit=8,
    )
    transfer_in, transfer_out = internal_transfer_totals(
        session, date_from, date_to
    )
    uncategorized_in, uncategorized_out = uncategorized_cashflow_totals(
        session, date_from, date_to
    )

if monthly.empty:
    total_income = total_expenses = total_savings = net_cash_flow = Decimal("0.00")
else:
    total_income = sum(
        (
            income + uncategorized
            for income, uncategorized in zip(
                monthly["income"], monthly["uncategorized_income"]
            )
        ),
        Decimal("0.00"),
    )
    total_expenses = sum(monthly["expenses"], Decimal("0.00"))
    total_savings = sum(monthly["savings"], Decimal("0.00"))
    net_cash_flow = (
        sum(monthly["net_cash_flow"], Decimal("0.00")) - uncategorized_out
    )

st.markdown("#### Period summary")
kpis = st.columns(4)
kpis[0].metric("Total income", _money(total_income))
kpis[1].metric("Expenses · net of refunds", _money(total_expenses))
kpis[2].metric("Net cash flow", _money(net_cash_flow))
kpis[3].metric("Savings moved", _money(total_savings))
uncategorized_columns = st.columns(2)
uncategorized_columns[0].metric("Uncategorized money in", _money(uncategorized_in))
uncategorized_columns[1].metric("Uncategorized money out", _money(uncategorized_out))
st.caption(
    "Income includes uncategorized inflows. Expenses are net of refunds; "
    "uncategorized outflows are shown separately and included in net cash flow. "
    "Savings moved reflects outflows assigned to a savings category."
)

if monthly.empty and category_spending.empty and recent.empty:
    st.info("No transactions were found for this period. Import a statement to get started.")
else:
    st.markdown("#### Monthly cash flow")
    if monthly.empty:
        st.info("No cash-flow totals are available for this period.")
    else:
        chart = pd.DataFrame(
            {
                "Month": monthly["month"],
                "Income": [
                    float(income + uncategorized)
                    for income, uncategorized in zip(
                        monthly["income"], monthly["uncategorized_income"]
                    )
                ],
                "Expenses": [float(value) for value in monthly["expenses"]],
            }
        ).melt(id_vars="Month", var_name="Type", value_name="Amount")
        if not uncategorized_outflows.empty:
            uncat_by_month = dict(
                zip(uncategorized_outflows["month"], uncategorized_outflows["amount"])
            )
            chart = pd.concat(
                [
                    chart,
                    pd.DataFrame(
                        {
                            "Month": list(monthly["month"]),
                            "Type": "Uncategorized outflows",
                            "Amount": [
                                float(uncat_by_month.get(month, Decimal("0.00")))
                                for month in monthly["month"]
                            ],
                        }
                    ),
                ],
                ignore_index=True,
            )
        fig = px.bar(
            chart,
            x="Month",
            y="Amount",
            color="Type",
            barmode="group",
            color_discrete_map={
                "Income": _COLORS["income"],
                "Expenses": _COLORS["expenses"],
                "Uncategorized outflows": _COLORS["net"],
            },
            labels={"Amount": "Amount (₮)"},
        )
        fig.update_layout(
            margin=dict(l=8, r=8, t=18, b=8),
            legend_title_text="",
            paper_bgcolor="rgba(0,0,0,0)",
            plot_bgcolor="rgba(0,0,0,0)",
        )
        st.plotly_chart(fig, use_container_width=True)

    left, right = st.columns(2)
    with left:
        st.markdown("#### Spending by category")
        if category_spending.empty:
            st.info("No categorized expense activity in this period.")
        else:
            category_chart = category_spending.copy()
            category_chart["amount"] = category_chart["amount"].astype(float)
            fig = px.bar(
                category_chart,
                x="amount",
                y="category",
                orientation="h",
                color_discrete_sequence=[_COLORS["expenses"]],
                labels={"amount": "Net expenses (₮)", "category": ""},
            )
            fig.update_layout(
                yaxis={"categoryorder": "total ascending"},
                margin=dict(l=8, r=8, t=12, b=8),
                paper_bgcolor="rgba(0,0,0,0)",
                plot_bgcolor="rgba(0,0,0,0)",
            )
            st.plotly_chart(fig, use_container_width=True)

    with right:
        st.markdown("#### Spending trend")
        if trend.empty:
            st.info("No categorized expense trend is available yet.")
        else:
            trend["amount"] = trend["amount"].astype(float)
            fig = px.line(
                trend,
                x="month",
                y="amount",
                markers=True,
                color_discrete_sequence=[_COLORS["net"]],
                labels={"month": "Month", "amount": "Net expenses (₮)"},
            )
            fig.update_layout(
                margin=dict(l=8, r=8, t=12, b=8),
                paper_bgcolor="rgba(0,0,0,0)",
                plot_bgcolor="rgba(0,0,0,0)",
            )
            st.plotly_chart(fig, use_container_width=True)

    st.markdown("#### Budget performance")
    if budgets.empty:
        st.info("No monthly budgets are set within this period.")
    else:
        st.caption(
            "Actual outflows include net refunds and exclude internal transfers. "
            "Budget currencies are labels; amounts are not converted."
        )
        st.dataframe(
            budgets.rename(
                columns={
                    "month": "Month",
                    "category": "Category",
                    "currency": "Currency",
                    "budget": "Limit",
                    "actual": "Actual",
                }
            ),
            use_container_width=True,
            hide_index=True,
            column_config={
                "Limit": st.column_config.NumberColumn("Limit", format="%,.2f"),
                "Actual": st.column_config.NumberColumn("Actual", format="%,.2f"),
            },
        )
        currencies = budgets["currency"].unique()
        if len(currencies) == 1:
            chart_data = budgets.copy()
            chart_data["label"] = chart_data["month"] + " · " + chart_data["category"]
            chart_data = chart_data.melt(
                id_vars="label",
                value_vars=["budget", "actual"],
                var_name="Series",
                value_name="Amount",
            )
            chart_data["Amount"] = chart_data["Amount"].astype(float)
            fig = px.bar(
                chart_data,
                x="label",
                y="Amount",
                color="Series",
                barmode="group",
                color_discrete_map={
                    "budget": _COLORS["budget"],
                    "actual": _COLORS["expenses"],
                },
                labels={"label": "", "Amount": f"Amount ({currencies[0]})"},
            )
            fig.update_layout(
                margin=dict(l=8, r=8, t=12, b=8),
                paper_bgcolor="rgba(0,0,0,0)",
                plot_bgcolor="rgba(0,0,0,0)",
            )
            st.plotly_chart(fig, use_container_width=True)
        else:
            st.warning(
                "Budget rows use different currency labels. The chart is hidden to avoid "
                "misleading cross-currency comparisons."
            )

    st.markdown("#### Recent transactions")
    if recent.empty:
        st.info("No transactions in this period.")
    else:
        recent_view = recent[
            [
                "txn_at",
                "merchant",
                "description",
                "direction",
                "signed_amount",
                "category",
                "category_source",
                "txn_type",
            ]
        ].copy()
        recent_view["merchant"] = recent_view["merchant"].fillna("")
        st.dataframe(
            recent_view,
            use_container_width=True,
            hide_index=True,
            column_config={
                "txn_at": st.column_config.DatetimeColumn(
                    "Date", format="YYYY-MM-DD HH:mm"
                ),
                "signed_amount": st.column_config.NumberColumn(
                    "Amount", format="%,.2f ₮"
                ),
                "category_source": st.column_config.TextColumn("Classification"),
                "txn_type": st.column_config.TextColumn("Type"),
            },
        )

with st.expander("Internal transfers (excluded from cash flow)"):
    transfer_columns = st.columns(2)
    transfer_columns[0].metric("Transfers in", _money(transfer_in))
    transfer_columns[1].metric("Transfers out", _money(transfer_out))
