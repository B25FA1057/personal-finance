from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from threading import Event, RLock
from uuid import uuid4

from sqlalchemy import select

from .ai_categorization import (
    eligible_transaction_count,
    refresh_import_summary,
    suggest_categories,
)
from .db import get_session
from .models import ImportCategorizationSummary, Transaction


@dataclass
class CategorizationJob:
    job_id: str
    batch_id: int | None
    status: str = "queued"
    completed: int = 0
    total: int = 0
    error: str | None = None
    _cancel: Event = field(default_factory=Event, repr=False)


_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="finance-ollama")
_lock = RLock()
_jobs: dict[str, CategorizationJob] = {}
_active_scopes: dict[tuple[int | None, bool], str] = {}
_futures: dict[str, Future[None]] = {}


def _update_progress(job_id: str, completed: int, total: int) -> None:
    with _lock:
        job = _jobs[job_id]
        job.completed = completed
        job.total = total


def _persist_batch_status(
    batch_id: int,
    ai_status: str,
    errors: list[str],
) -> None:
    with get_session() as session:
        summary = session.get(ImportCategorizationSummary, batch_id)
        if summary is None:
            return
        summary.ai_status = ai_status
        summary.errors = errors
        refresh_import_summary(session, batch_id)
        session.commit()


def _run_job(job_id: str, include_pending: bool) -> None:
    with _lock:
        job = _jobs[job_id]
        job.status = "running"

    try:
        with get_session() as session:
            statement = select(Transaction.id).order_by(Transaction.id)
            if job.batch_id is not None:
                statement = statement.where(Transaction.batch_id == job.batch_id)
            transaction_ids = list(session.scalars(statement).all())
            total = eligible_transaction_count(
                session,
                transaction_ids,
                include_pending=include_pending,
            )
            _update_progress(job_id, 0, total)
            result = suggest_categories(
                session,
                transaction_ids,
                include_pending=include_pending,
                progress_callback=lambda completed, total: _update_progress(
                    job_id, completed, total
                ),
                cancel_check=job._cancel.is_set,
            )

            if job.batch_id is not None:
                summary = session.get(ImportCategorizationSummary, job.batch_id)
                if summary is not None:
                    summary.ai_status = (
                        "cancelled"
                        if result.cancelled
                        else "partial"
                        if result.error and result.suggestions
                        else "failed"
                        if result.error
                        else "completed"
                    )
                    summary.errors = (
                        result.batch_errors
                        if result.batch_errors
                        else [result.error]
                        if result.error
                        else []
                    )
                    refresh_import_summary(session, job.batch_id)
            session.commit()

        with _lock:
            job.total = result.eligible
            job.error = result.error
            job.status = (
                "cancelled"
                if result.cancelled
                else "partial_failure"
                if result.error and result.suggestions
                else "failed"
                if result.error
                else "completed"
            )
    except Exception:
        error = (
            "Background categorization failed. Transactions remain available for review."
        )
        if job.batch_id is not None:
            try:
                _persist_batch_status(job.batch_id, "failed", [error])
            except Exception:
                error = (
                    "Background categorization failed, and the import status could not "
                    "be updated. Retry categorization from Review."
                )
        with _lock:
            job.error = error
            job.status = "failed"


def enqueue_categorization(
    batch_id: int | None,
    *,
    include_pending: bool = False,
    retry: bool = False,
) -> str:
    scope = (batch_id, include_pending)
    with _lock:
        existing_id = _active_scopes.get(scope)
        if existing_id is not None:
            existing = _jobs[existing_id]
            if existing.status in {"queued", "running"} or not retry:
                return existing_id

        job_id = uuid4().hex
        job = CategorizationJob(job_id=job_id, batch_id=batch_id)
        _jobs[job_id] = job
        _active_scopes[scope] = job_id
        _futures[job_id] = _executor.submit(_run_job, job_id, include_pending)
        return job_id


def get_categorization_job(job_id: str) -> dict[str, int | str | None] | None:
    with _lock:
        job = _jobs.get(job_id)
        if job is None:
            return None
        return {
            "job_id": job.job_id,
            "batch_id": job.batch_id,
            "status": job.status,
            "completed": job.completed,
            "total": job.total,
            "error": job.error,
        }


def cancel_categorization_job(job_id: str) -> bool:
    cancelled_before_start = False
    batch_id: int | None = None
    with _lock:
        job = _jobs.get(job_id)
        if job is None or job.status not in {"queued", "running"}:
            return False
        job._cancel.set()
        batch_id = job.batch_id
        future = _futures.get(job_id)
        if future is not None and future.cancel():
            job.status = "cancelled"
            job.error = "Categorization cancelled before processing started."
            cancelled_before_start = True
    if cancelled_before_start and batch_id is not None:
        try:
            _persist_batch_status(batch_id, "cancelled", [])
        except Exception:
            with _lock:
                job = _jobs[job_id]
                job.error = (
                    "Categorization was cancelled before processing started, but the "
                    "import status could not be updated."
                )
    return True
