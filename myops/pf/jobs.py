from __future__ import annotations

"""
In-memory refresh-job store.

Stands in for dbo.pf_refresh_jobs from the VM handoff (Section 2.4) until that
SQL table exists. Job state lives in this process's memory only — it does not
survive a restart, and it is not shared across multiple API worker processes.
That is a real gap versus the handoff's design, not a permanent choice; it
exists so /appointments/{row_id}/refresh and /refresh-jobs/{job_id} can match
the target request/response shape now.
"""

import threading
import uuid
from datetime import datetime, timezone     

_jobs: dict[str, dict] = {}
_guard = threading.Lock()

ACTIVE_STATUSES = {"queued", "running"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def find_active_job_for_appointment(appointment_pk: str) -> dict | None:
    with _guard:
        for job in _jobs.values():
            if job["appointment_pk"] == appointment_pk and job["status"] in ACTIVE_STATUSES:
                return dict(job)
    return None


def create_job(appointment_pk: str, requested_by: str = "") -> dict:
    job_id = str(uuid.uuid4())
    job = {
        "job_id": job_id,
        "appointment_pk": appointment_pk,
        "status": "queued",
        "requested_by": requested_by,
        "requested_at": _now(),
        "started_at": None,
        "completed_at": None,
        "message": None,
        "pdf_uri": None,
        "error_detail": None,
    }
    with _guard:
        _jobs[job_id] = job
    return dict(job)


def mark_running(job_id: str) -> None:
    with _guard:
        job = _jobs.get(job_id)
        if job:
            job["status"] = "running"
            job["started_at"] = _now()


def mark_done(job_id: str, status: str, message: str = "", pdf_uri: str | None = None) -> None:
    with _guard:
        job = _jobs.get(job_id)
        if job:
            job["status"] = status
            job["message"] = message
            job["pdf_uri"] = pdf_uri
            job["completed_at"] = _now()


def mark_failed(job_id: str, error_detail: str) -> None:
    with _guard:
        job = _jobs.get(job_id)
        if job:
            job["status"] = "failed"
            job["error_detail"] = error_detail
            job["completed_at"] = _now()


def get_job(job_id: str) -> dict | None:
    with _guard:
        job = _jobs.get(job_id)
        return dict(job) if job else None
