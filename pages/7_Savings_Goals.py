from __future__ import annotations

from datetime import date
from decimal import Decimal

import pandas as pd
import streamlit as st
from sqlalchemy import select

from core.db import get_session
from core.models import SavingsGoal
from core.planning import savings_goal_metrics, validate_goal_values
from core.queries import currency_options

st.title("Savings goals")
st.caption(
    "Goal progress is manually maintained and is separate from imported bank transactions. "
    "Transfers are never assumed to be savings contributions."
)
st.info(
    "Currency is a label for goal amounts; values from different currencies are not converted."
)

with get_session() as session:
    currencies = currency_options(session)

with st.form("create_savings_goal"):
    st.subheader("Create a goal")
    goal_name = st.text_input("Name")
    goal_target_text = st.text_input("Target amount")
    goal_saved_text = st.text_input("Current saved amount", value="0.00")
    goal_currency = st.selectbox("Currency", options=currencies)
    goal_target_date = st.date_input(
        "Target date", value=date(date.today().year + 1, 12, 31)
    )
    goal_description = st.text_area("Description (optional)")
    create_goal = st.form_submit_button("Create goal")

if create_goal:
    try:
        normalized_name, target, saved = validate_goal_values(
            goal_name, goal_target_text, goal_saved_text, goal_target_date
        )
    except ValueError as exc:
        st.error(str(exc))
    else:
        with get_session() as session:
            session.add(
                SavingsGoal(
                    name=normalized_name,
                    target_amount=target,
                    current_saved=saved,
                    currency=goal_currency,
                    target_date=goal_target_date,
                    description=goal_description.strip() or None,
                )
            )
            session.commit()
        st.rerun()

show_archived = st.checkbox("Show archived goals", value=False)
with get_session() as session:
    goals = session.execute(
        select(SavingsGoal)
        .where(SavingsGoal.archived.is_(show_archived))
        .order_by(SavingsGoal.target_date, SavingsGoal.id)
    ).scalars().all()

if not goals:
    st.info("No savings goals to display.")
else:
    st.subheader("Archived goals" if show_archived else "Active goals")
    cards = st.columns(2)
    table_rows = []
    for index, goal in enumerate(goals):
        remaining, progress_percent = savings_goal_metrics(
            goal.target_amount, goal.current_saved
        )
        with cards[index % len(cards)].container(border=True):
            st.markdown(f"### {goal.name}")
            st.caption(f"Target date: {goal.target_date:%Y-%m-%d}")
            st.metric(
                "Saved",
                f"{goal.currency} {goal.current_saved:,.2f}",
                f"of {goal.currency} {goal.target_amount:,.2f}",
            )
            st.progress(
                float(
                    min(max(progress_percent, Decimal("0")), Decimal("100"))
                    / Decimal("100")
                ),
                text=f"{progress_percent:.1f}% complete",
            )
            st.caption(f"Remaining: {goal.currency} {remaining:,.2f}")
            if goal.description:
                st.write(goal.description)
        table_rows.append(
            {
                "Goal": goal.name,
                "Currency": goal.currency,
                "Target": goal.target_amount,
                "Saved": goal.current_saved,
                "Remaining": remaining,
                "Progress": f"{progress_percent:.1f}%",
                "Target date": goal.target_date,
                "Archived": goal.archived,
            }
        )

    st.dataframe(
        pd.DataFrame(table_rows),
        width="stretch",
        hide_index=True,
        column_config={
            "Target": st.column_config.NumberColumn("Target", format="%,.2f"),
            "Saved": st.column_config.NumberColumn("Saved", format="%,.2f"),
            "Remaining": st.column_config.NumberColumn("Remaining", format="%,.2f"),
            "Target date": st.column_config.DateColumn("Target date", format="YYYY-MM-DD"),
        },
    )

    st.subheader("Edit, archive, or delete a goal")
    goals_by_id = {goal.id: goal for goal in goals}
    selected_goal_id = st.selectbox(
        "Goal",
        options=list(goals_by_id),
        format_func=lambda goal_id: goals_by_id[goal_id].name,
    )
    selected_goal = goals_by_id[selected_goal_id]
    with st.form("edit_savings_goal"):
        edited_name = st.text_input("Name", value=selected_goal.name)
        edited_target_text = st.text_input(
            "Target amount", value=f"{selected_goal.target_amount:.2f}"
        )
        edited_saved_text = st.text_input(
            "Current saved amount", value=f"{selected_goal.current_saved:.2f}"
        )
        edited_currency = st.selectbox(
            "Currency",
            options=currencies,
            index=currencies.index(selected_goal.currency),
        )
        edited_target_date = st.date_input(
            "Target date", value=selected_goal.target_date
        )
        edited_description = st.text_area(
            "Description (optional)", value=selected_goal.description or ""
        )
        save_goal = st.form_submit_button("Save changes")

    if save_goal:
        try:
            normalized_name, target, saved = validate_goal_values(
                edited_name,
                edited_target_text,
                edited_saved_text,
                edited_target_date,
            )
        except ValueError as exc:
            st.error(str(exc))
        else:
            with get_session() as session:
                goal = session.get(SavingsGoal, selected_goal_id)
                if goal is None:
                    st.error("This goal no longer exists. Refresh and try again.")
                else:
                    goal.name = normalized_name
                    goal.target_amount = target
                    goal.current_saved = saved
                    goal.currency = edited_currency
                    goal.target_date = edited_target_date
                    goal.description = edited_description.strip() or None
                    session.commit()
                    st.rerun()

    action_col, delete_col = st.columns(2)
    action_label = "Restore goal" if selected_goal.archived else "Archive goal"
    if action_col.button(action_label):
        with get_session() as session:
            goal = session.get(SavingsGoal, selected_goal_id)
            if goal is None:
                st.error("This goal no longer exists. Refresh and try again.")
            else:
                goal.archived = not goal.archived
                session.commit()
                st.rerun()

    confirm_delete = delete_col.checkbox(
        "Confirm permanent deletion",
        key=f"confirm_goal_delete_{selected_goal_id}",
    )
    if delete_col.button("Delete goal", disabled=not confirm_delete):
        with get_session() as session:
            goal = session.get(SavingsGoal, selected_goal_id)
            if goal is None:
                st.error("This goal no longer exists. Refresh and try again.")
            else:
                session.delete(goal)
                session.commit()
                st.rerun()
