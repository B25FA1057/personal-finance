from __future__ import annotations

import streamlit as st

from core.db import get_session
from core.importer import import_file
from core.queries import batches_df

st.title("Upload bank statements")

uploaded_files = st.file_uploader("Bank statement (.xlsx)", type=["xlsx"], accept_multiple_files=True)

if st.button("Import") and uploaded_files:
    session = get_session()
    for file in uploaded_files:
        try:
            result = import_file(session, file.getvalue(), file.name)
            if result.already_imported:
                st.info(f"{file.name}: already imported")
                continue
            st.success(f"{file.name}: {result.bank_code} / {result.account_no} — {result.new_rows} new, {result.duplicate_rows} duplicates")
            if result.new_counterparty_ids:
                st.success(f"{len(result.new_counterparty_ids)} new merchants/people to classify")
                st.page_link("pages/2_Review.py", label="Go to Review")
        except Exception as exc:
            st.error(f"{file.name}: {exc}")
    session.close()

st.subheader("Import history")
st.dataframe(batches_df(get_session()), use_container_width=True)
