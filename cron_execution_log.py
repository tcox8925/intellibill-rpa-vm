"""
Cron job execution logging into Postgres, replacing the earlier
job_status.json file-based tracker.

Every fire-and-forget trigger (Tebra /run-tebra per practice, Practice
Fusion /sync-schedules-by-date, /patient-sync/tebra/sync) now writes one row
per invocation into "EDI_Tebra".cron_job_executions -- not just a single
"last run" slot per key -- so every run is kept, queryable straight from
Postgres, rather than living in a flat JSON file on the VM's disk. The
matching "EDI_Tebra".cron_jobs row for each job is already seeded manually
(job_setting values below match what's actually in that table); this module
only ever reads cron_jobs (to resolve job_setting -> cron_job_id) and writes
cron_job_executions -- it never creates or edits cron_jobs rows itself.

Usage, mirroring the old mark_started/mark_finished shape:
    execution_id = start_execution("TEBRA_FACESHEET_PULL", response={...})
    ...
    finish_execution(execution_id, success=True, response={...})
    # or
    finish_execution(execution_id, success=False, error_description=repr(exc))
"""

import json
import os
import threading

import psycopg2
import psycopg2.extras


def _json_or_none(value: dict | None):
    """Wrap a response dict for the jsonb column, tolerating values json.dumps
    can't natively handle (datetime, Decimal, etc. commonly show up in
    pipeline summaries) by falling back to str() for those instead of raising
    and losing the whole execution write over a formatting detail."""
    if value is None:
        return None
    return psycopg2.extras.Json(value, dumps=lambda v: json.dumps(v, default=str))


def _db_config() -> dict:
    return {
        "host": os.environ.get("RCM_DB_HOST", "").strip(),
        "dbname": os.environ.get("RCM_DB_NAME", "").strip(),
        "user": os.environ.get("RCM_DB_USER", "").strip(),
        "password": os.environ.get("RCM_DB_PASSWORD", "").strip(),
    }


def _get_connection():
    cfg = _db_config()
    return psycopg2.connect(
        host=cfg["host"],
        dbname=cfg["dbname"],
        user=cfg["user"],
        password=cfg["password"],
        sslmode="require",
        # Without this, an unreachable/slow DB host (e.g. this module running
        # somewhere without a network path to it) hangs on TCP connect for
        # however long the OS's own retry/timeout policy takes - which can be
        # minutes, not seconds. Every call site here already runs off the
        # request-handling thread specifically so a DB hiccup can't block an
        # HTTP response; a fast, explicit failure here just makes that hiccup
        # show up immediately in the caller's own try/except logging instead
        # of leaving a thread hung indefinitely with nothing ever logged.
        connect_timeout=5,
    )


# job_setting -> cron_jobs.id, cached in-process. cron_jobs rows are
# hand-managed (seeded manually via SQL), not written by this module, so a
# process-lifetime cache is safe -- nothing here ever changes a job_setting's
# cron_job_id out from under a running process.
_CRON_JOB_ID_CACHE: dict[str, str] = {}
_CACHE_LOCK = threading.Lock()


def _get_cron_job_id(job_setting: str) -> str:
    with _CACHE_LOCK:
        cached = _CRON_JOB_ID_CACHE.get(job_setting)
    if cached is not None:
        return cached

    conn = _get_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            'SELECT id FROM "EDI_Tebra".cron_jobs WHERE job_setting = %s AND active = true',
            (job_setting,),
        )
        row = cur.fetchone()
        cur.close()
    finally:
        conn.close()

    if row is None:
        raise RuntimeError(
            f"No active EDI_Tebra.cron_jobs row found for job_setting={job_setting!r} "
            "-- insert one before triggering this job."
        )

    with _CACHE_LOCK:
        _CRON_JOB_ID_CACHE[job_setting] = row[0]
    return row[0]


def start_execution(job_setting: str, response: dict | None = None) -> str:
    """Insert a new cron_job_executions row (status='started') and return its id."""
    cron_job_id = _get_cron_job_id(job_setting)
    conn = _get_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO "EDI_Tebra".cron_job_executions (cron_job_id, status, response)
            VALUES (%s, 'started', %s)
            RETURNING id
            """,
            (cron_job_id, _json_or_none(response)),
        )
        execution_id = cur.fetchone()[0]
        conn.commit()
        cur.close()
    finally:
        conn.close()
    return str(execution_id)


def mark_processing(execution_id: str, response: dict | None = None) -> None:
    """Optional midpoint update - not required, but available for a long-running
    job that wants to record progress before it actually finishes."""
    conn = _get_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            UPDATE "EDI_Tebra".cron_job_executions
            SET status = 'processing', response = COALESCE(%s, response)
            WHERE id = %s
            """,
            (_json_or_none(response), execution_id),
        )
        conn.commit()
        cur.close()
    finally:
        conn.close()


def finish_execution(
    execution_id: str,
    success: bool,
    error_description: str | None = None,
    response: dict | None = None,
) -> None:
    """Update an existing execution row to its final status.

    error_description is required by the table's own CHECK constraint when
    success=False; a caller passing success=False with no message would
    otherwise fail the insert with a confusing constraint-violation instead
    of a clear one here.
    """
    if not success and not error_description:
        error_description = "Unknown error (no error_description provided)"

    status = "success" if success else "failed"
    conn = _get_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            UPDATE "EDI_Tebra".cron_job_executions
            SET status = %s,
                error_description = %s,
                response = COALESCE(%s, response)
            WHERE id = %s
            """,
            (
                status,
                error_description,
                _json_or_none(response),
                execution_id,
            ),
        )
        conn.commit()
        cur.close()
    finally:
        conn.close()
