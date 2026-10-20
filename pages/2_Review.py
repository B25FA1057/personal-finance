from __future__ import annotations

import pandas as pd
import streamlit as st

from core.classify import set_counterparty_category
from core.db import get_session
from core.queries import category_options, unreviewed_counterparties_df

st.title("Classify merchants & people")
show_reviewed = st.checkbox("Show already reviewed", value=False)

session = get_session()
rows = unreviewed_counterparties_df(session, include_reviewed=show_reviewed)
if rows.empty:
    st.success("Nothing to review")
else:
    options = category_options(session) + [""]
    table = rows.copy()
    table["category"] = ""
    category_names = category_options(session)
    category_by_name = {name: idx for idx, name in enumerate(category_names)}
    for idx, row in table.iterrows():
        table.at[idx, "category"] = row.get("default_category") or row.get("current_category") or ""
    edited = st.data_editor(
        table[["id", "display_name", "kind", "txn_count", "total_out", "total_in", "sample_description", "current_category", "category"]],
        use_container_width=True,
        hide_index=True,
        disabled=["id", "display_name", "kind", "txn_count", "total_out", "total_in", "sample_description", "current_category"],
        column_config={
            "category": st.column_config.SelectboxColumn("Category", options=options),
        },
        key="review_editor",
    )
    if st.button("Save classifications"):
        saved = 0
        for _, row in edited.iterrows():
            category_name = str(row["category"]).strip()
            if not category_name:
                continue
            category_map = {name: i + 1 for i, name in enumerate(category_names)}
            if category_name in category_map:
                set_counterparty_category(session, int(row["id"]), category_map[category_name], apply_existing=True)
                saved += 1
        st.success(f"Saved {saved} classifications")
        st.rerun()

if rows.empty:
    st.stop()

names = rows["display_name"].dropna().tolist()
if names:
    selected = st.selectbox("Show transactions for…", names)
    st.write(f"Transactions for {selected}")

session.close()
