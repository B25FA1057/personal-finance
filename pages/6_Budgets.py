from __future__ import annotations

import calendar
from datetime import date
from decimal import Decimal

import pandas as pd
import streamlit as st
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from core.db import get_session
from core.models import Category, MonthlyBudget
from core.planning import budget_metrics, parse_amount
from core.queries import budget_actuals_df, currency_options

st.title("Monthly budgets")
st.caption(
    "Actual spending includes saved outflow transactions in expense categories, "
    "excluding own-account transfers. Duplicate imports are prevented by the transaction dedupe key."
)
st.info(
    "Currency is a label for the budget amount. Actual spending combines account amounts "
    "without currency filtering or conversion."
)

today = date.today()
year_col, month_col = st.columns(2)
selected_year = int(
    year_col.number_input(
        "Year", min_value=2000, max_value=9999, value=today.year, step=1
    )
)
selected_month = int(
    month_col.selectbox(
        "Month",
        options=list(range(1, 13)),
        index=today.month - 1,
        format_func=lambda month: calendar.month_name[month],
    )
)
month_start = date(selected_year, selected_month, 1)

with get_session() as session:
    categories = session.execute(
        select(Category.id, Category.name)
        .where(Category.kind == "expense")
        .order_by(Category.sort_order, Category.name, Category.id)
    ).all()
    currencies = currency_options(session)
    budgets = session.execute(
        select(MonthlyBudget, Category.name)
        .join(Category, Category.id == MonthlyBudget.category_id)
        .where(MonthlyBudget.month_start == month_start)
        .order_by(Category.sort_order, Category.name)
    ).all()
    actuals = budget_actuals_df(session, selected_year, selected_month)

category_names = {int(row.id): row.name for row in categories}
actual_by_category = {
    int(row.category_id): Decimal(row.actual_spending)
    for row in actuals.itertuples(index=False)
}
budget_by_id = {budget.id: (budget, category_name) for budget, category_name in budgets}
budgeted_category_ids = {budget.category_id for budget, _ in budgets}

st.subheader(f"{calendar.month_name[selected_month]} {selected_year}")
if budgets:
    table_rows = []
    for budget, category_name in budgets:
        actual = actual_by_category.get(budget.category_id, Decimal("0.00"))
        remaining, used_percent, status = budget_metrics(budget.amount, actual)
        table_rows.append(
            {
                "Category": category_name,
                "Currency": budget.currency,
                "Budget": budget.amount,
                "Actual": actual,
                "Remaining": remaining,
                "Used": f"{used_percent:.1f}%",
                "Status": status,
            }
        )
        st.progress(
            float(min(max(used_percent, Decimal("0")), Decimal("100")) / Decimal("100")),
            text=f"{category_name}: {used_percent:.1f}% used",
        )
        if status == "Over budget":
            st.error(f"{category_name} has exceeded its budget.")
        elif status == "Near limit":
            st.warning(f"{category_name} has reached at least 80% of its budget.")

    st.dataframe(
        pd.DataFrame(table_rows),
        width="stretch",
        hide_index=True,
        column_config={
            "Budget": st.column_config.NumberColumn("Budget", format="%,.2f"),
            "Actual": st.column_config.NumberColumn("Actual", format="%,.2f"),
            "Remaining": st.column_config.NumberColumn("Remaining", format="%,.2f"),
        },
    )
else:
    st.info("No budgets have been set for this month.")

st.subheader("Add a budget")
available_categories = [
    category_id
    for category_id in category_names
    if category_id not in budgeted_category_ids
]
if not available_categories:
    st.caption("Every expense category already has a budget for this month.")
else:
    with st.form("create_monthly_budget"):
        new_category_id = st.selectbox(
            "Expense category",
            options=available_categories,
            format_func=category_names.__getitem__,
        )
        new_amount_text = st.text_input("Budget amount", value="")
        new_currency = st.selectbox("Currency label", options=currencies)
        create_budget = st.form_submit_button("Create budget")

    if create_budget:
        try:
            amount = parse_amount(new_amount_text, "Budget amount")
        except ValueError as exc:
            st.error(str(exc))
        else:
            try:
                with get_session() as session:
                    session.add(
                        MonthlyBudget(
                            category_id=new_category_id,
                            month_start=month_start,
                            amount=amount,
                            currency=new_currency,
                        )
                    )
                    session.commit()
                st.rerun()
            except IntegrityError:
                st.error("A budget already exists for this category and month.")

if budgets:
    st.subheader("Edit or delete a budget")
    selected_budget_id = st.selectbox(
        "Budget",
        options=list(budget_by_id),
        format_func=lambda budget_id: (
            f"{budget_by_id[budget_id][1]} "
            f"({budget_by_id[budget_id][0].currency} "
            f"{budget_by_id[budget_id][0].amount:,.2f})"
        ),
    )
    selected_budget, selected_budget_category = budget_by_id[selected_budget_id]
    editable_category_ids = [
        category_id
        for category_id in category_names
        if category_id not in budgeted_category_ids
        or category_id == selected_budget.category_id
    ]
    with st.form("edit_monthly_budget"):
        edited_category_id = st.selectbox(
            "Expense category",
            options=editable_category_ids,
            index=editable_category_ids.index(selected_budget.category_id),
            format_func=category_names.__getitem__,
        )
        edited_amount_text = st.text_input(
            "Budget amount", value=f"{selected_budget.amount:.2f}"
        )
        edited_currency = st.selectbox(
            "Currency label",
            options=currencies,
            index=currencies.index(selected_budget.currency),
        )
        save_budget = st.form_submit_button("Save changes")

    if save_budget:
        try:
            amount = parse_amount(edited_amount_text, "Budget amount")
        except ValueError as exc:
            st.error(str(exc))
        else:
            try:
                with get_session() as session:
                    budget = session.get(MonthlyBudget, selected_budget_id)
                    if budget is None:
                        st.error("This budget no longer exists. Refresh and try again.")
                    else:
                        budget.category_id = edited_category_id
                        budget.amount = amount
                        budget.currency = edited_currency
                        session.commit()
                        st.rerun()
            except IntegrityError:
                st.error("A budget already exists for that category and month.")

    confirm_delete = st.checkbox(
        "I understand this will permanently delete the selected budget.",
        key=f"confirm_budget_delete_{selected_budget_id}",
    )
    if st.button("Delete budget", disabled=not confirm_delete):
        with get_session() as session:
            budget = session.get(MonthlyBudget, selected_budget_id)
            if budget is None:
                st.error("This budget no longer exists. Refresh and try again.")
            else:
                session.delete(budget)
                session.commit()
                st.rerun()
