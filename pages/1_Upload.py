from __future__ import annotations

import streamlit as st

from core import config
from core.db import get_session
from core.importer import import_file
from core.queries import batches_df

st.title("Upload bank statements")

uploaded_files = st.file_uploader("Bank statement (.xlsx)", type=["xlsx"], accept_multiple_files=True)
ai_consent = st.checkbox(
    "I agree to send merchant names and redacted transaction descriptions, direction, "
    "and transaction type to Google Gemini for categorization. Account numbers, amounts, "
    "dates, and counterparty account numbers are not sent.",
    value=False,
)
st.caption(
    "Google's Gemini API free-tier terms may allow submitted content to be used to improve "
    "Google products. Check the current terms before enabling AI categorization."
)
if not config.GEMINI_API_KEY:
    st.info(
        "Gemini categorization is not configured. Imports will still use existing rules "
        "and confirmed mappings; remaining transactions stay in Review."
    )
elif ai_consent:
    st.warning(
        "With consent enabled, selected transaction details are sent to Google Gemini using the "
        "configured model. Suggestions remain unconfirmed until you review them."
    )

if st.button("Import") and uploaded_files:
    session = get_session()
    try:
        for file in uploaded_files:
            try:
                result = import_file(
                    session,
                    file.getvalue(),
                    file.name,
                    allow_ai=ai_consent,
                )
            except Exception as exc:
                st.error(f"{file.name}: {exc}")
                st.metric("Import errors", 1)
                continue

            if result.already_imported:
                st.info(
                    f"{file.name}: already imported; no new transactions or AI requests "
                    f"were made. Duplicate rows skipped: {result.duplicate_rows}."
                )
            else:
                st.success(f"{file.name}: import completed.")
            metrics = st.columns(7)
            metrics[0].metric("New transactions", result.new_rows)
            metrics[1].metric("Duplicate rows skipped", result.duplicate_rows)
            metrics[2].metric(
                "Existing rules", 0 if result.already_imported else result.rules_classified
            )
            metrics[3].metric(
                "Confirmed mappings",
                0 if result.already_imported else result.counterparties_classified,
            )
            metrics[4].metric(
                "AI suggestions", 0 if result.already_imported else result.ai_suggestions
            )
            metrics[5].metric("Pending review", result.pending_review)
            metrics[6].metric(
                "Import errors", 0 if result.already_imported else len(result.errors)
            )
            if result.already_imported:
                st.caption("AI categorization was not run again for this repeated file.")
            else:
                st.caption(
                    f"AI status: {result.ai_status}; import status: "
                    f"{'completed with errors' if result.errors else 'completed'}."
                )
                for error in result.errors:
                    st.error(f"{file.name}: {error}")
            if result.new_counterparty_ids:
                st.info(f"{len(result.new_counterparty_ids)} new merchants/people to review")
            if result.pending_review or result.new_counterparty_ids:
                st.page_link("pages/2_Review.py", label="Go to Review")
            for warning in result.warnings:
                st.warning(f"{file.name}: {warning}")
    finally:
        session.close()

st.subheader("Import history")
with get_session() as session:
    history = batches_df(session)
if history.empty:
    st.info("No statements have been imported yet.")
else:
    st.dataframe(history, use_container_width=True, hide_index=True)
