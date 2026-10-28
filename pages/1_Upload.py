from __future__ import annotations

import streamlit as st
import pandas as pd

from core import config
from core.categorization_jobs import enqueue_categorization
from core.db import get_session
from core.importer import import_file, preview_file
from core.queries import batches_df
from core.ui import remember_categorization_job, render_categorization_jobs

st.title("Upload bank statements")

uploaded_files = st.file_uploader("Bank statement (.xlsx)", type=["xlsx"], accept_multiple_files=True)
previews = []
valid_files = True
if uploaded_files:
    st.subheader("Statement preview")
    with get_session() as preview_session:
        for file in uploaded_files:
            try:
                preview = preview_file(preview_session, file.getvalue())
            except ValueError:
                valid_files = False
                st.error(f"{file.name}: this workbook is invalid or its format is unsupported.")
                continue
            previews.append((file, preview))
            with st.container(border=True):
                st.markdown(f"**{file.name}**")
                if preview.already_imported:
                    st.info("This exact statement was already imported.")
                details = st.columns(4)
                details[0].metric(
                    "Bank", preview.bank_code or "Previously imported"
                )
                details[1].metric("Transactions", preview.total_rows)
                details[2].metric("Existing duplicates", preview.duplicate_rows)
                details[3].metric(
                    "Expected new rows",
                    max(0, preview.total_rows - preview.duplicate_rows),
                )
                if preview.detected_columns:
                    st.caption(
                        "Detected columns: " + " · ".join(preview.detected_columns)
                    )
                if preview.sample_rows:
                    st.dataframe(
                        pd.DataFrame(preview.sample_rows),
                        use_container_width=True,
                        hide_index=True,
                    )
                if preview.period_from or preview.period_to:
                    st.caption(
                        f"Statement period: {preview.period_from or 'unknown'} — "
                        f"{preview.period_to or 'unknown'}"
                    )

ai_consent = st.checkbox(
    "I agree to run local Ollama categorization using merchant names and redacted "
    "transaction descriptions, direction, and transaction type.",
    value=False,
)
st.caption(
    f"AI categorization runs locally through Ollama using {config.OLLAMA_MODEL}. "
    "Transaction data is not sent to a cloud AI provider."
)
if ai_consent:
    st.info(
        "Imports complete immediately. Eligible transactions are categorized in the "
        "background; suggestions remain unconfirmed until you review them."
    )

if st.button("Import", disabled=not uploaded_files or not valid_files) and uploaded_files:
    session = get_session()
    try:
        for file, _preview in previews:
            job_id: str | None = None
            try:
                result = import_file(
                    session,
                    file.getvalue(),
                    file.name,
                    allow_ai=False,
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
                if ai_consent and result.pending_review and result.batch_id is not None:
                    job_id = enqueue_categorization(result.batch_id)
                    remember_categorization_job(job_id)
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
                    f"AI status: {('queued' if job_id else 'not needed' if ai_consent else result.ai_status)}; import status: "
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

job_ids = list(st.session_state.get("categorization_job_ids", []))
if job_ids:
    st.subheader("Background categorization")
    render_categorization_jobs(job_ids)

st.subheader("Import history")
with get_session() as session:
    history = batches_df(session)
if history.empty:
    st.info("No statements have been imported yet.")
else:
    st.dataframe(history, use_container_width=True, hide_index=True)
