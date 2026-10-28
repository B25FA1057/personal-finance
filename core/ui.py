from __future__ import annotations

import streamlit as st


def remember_categorization_job(job_id: str) -> None:
    job_ids = list(st.session_state.get("categorization_job_ids", []))
    if job_id not in job_ids:
        job_ids.append(job_id)
    st.session_state["categorization_job_ids"] = job_ids[-8:]


@st.fragment(run_every="2s")
def render_categorization_jobs(job_ids: list[str]) -> None:
    from .categorization_jobs import (
        cancel_categorization_job,
        get_categorization_job,
    )

    for job_id in job_ids:
        job = get_categorization_job(job_id)
        if job is None:
            st.info(
                "A previous background task is no longer available. "
                "Pending transactions can be retried from Review."
            )
            continue

        status = str(job["status"])
        completed = int(job["completed"])
        total = int(job["total"])
        if status == "queued":
            st.caption("Local Ollama categorization is queued.")
            if st.button("Cancel queued job", key=f"cancel_job_{job_id}"):
                cancel_categorization_job(job_id)
        elif status == "running":
            if total:
                st.caption("Local Ollama categorization is running in the background.")
                st.progress(
                    min(completed / total, 1.0),
                    text=f"{completed} of {total} transaction(s) processed",
                )
            else:
                st.caption("Preparing eligible transactions for local categorization.")
            if st.button("Cancel after current batch", key=f"cancel_job_{job_id}"):
                cancel_categorization_job(job_id)
        elif status == "completed":
            st.success(f"Categorization complete. {completed} suggestion(s) are ready for review.")
        elif status == "partial_failure":
            st.warning(
                f"Categorization finished with batch errors. {completed} transaction(s) "
                f"were processed; successful suggestions remain available for review. "
                f"{job['error'] or ''}"
            )
        elif status == "cancelled":
            st.warning(str(job["error"] or "Categorization was cancelled."))
        else:
            st.error(str(job["error"] or "Local categorization failed."))


def apply_app_style() -> None:
    st.markdown(
        """
        <style>
        .stApp {
            background: #f5f7f4;
        }
        .stAppViewContainer .main .block-container {
            max-width: 1440px;
            padding-top: 2rem;
            padding-bottom: 3rem;
        }
        section[data-testid="stSidebar"] {
            background: #ffffff;
            border-right: 1px solid #e4e9e5;
        }
        h1 {
            color: #183c2e;
            font-size: 1.85rem !important;
            font-weight: 680 !important;
            letter-spacing: -0.035em;
        }
        h2, h3 {
            color: #263b31;
            letter-spacing: -0.02em;
        }
        div[data-testid="stMetric"] {
            background: #ffffff;
            border: 1px solid #e4e9e5;
            border-radius: 14px;
            padding: 1rem 1.15rem;
            box-shadow: 0 2px 8px rgba(27, 55, 42, 0.035);
        }
        div[data-testid="stMetricLabel"] {
            color: #65756c;
            font-size: 0.82rem;
        }
        div[data-testid="stMetricValue"] {
            color: #203b2e;
            font-size: 1.42rem;
            font-weight: 650;
        }
        div[data-testid="stVerticalBlockBorderWrapper"] {
            border-color: #e4e9e5;
            border-radius: 14px;
            background: #ffffff;
        }
        .stButton > button, .stDownloadButton > button {
            border-radius: 9px;
            font-weight: 560;
            min-height: 2.45rem;
        }
        .stButton > button[kind="primary"] {
            background: #1f684f;
            border-color: #1f684f;
        }
        div[data-testid="stDataFrame"] {
            border: 1px solid #e4e9e5;
            border-radius: 12px;
            overflow: hidden;
        }
        .finance-muted {
            color: #65756c;
            font-size: 0.9rem;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )
