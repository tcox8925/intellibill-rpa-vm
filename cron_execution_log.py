"""
Cron job execution logging into Postgres, replacing the earlier
job_status.json file-based tracker.

Every fire-and-forget trigger (Tebra /run-tebra per practice, Practice
Fusion /sync-schedules-by-date and /appointments-by-date,
/patient-sync/tebra/sync) writes one row per invocation into
"EDI_Tebra".cron_job_executions. The matching "EDI_Tebra".cron_jobs row for
each job is seeded manually (pf_sync_v5_6/rcm_schema/cron_jobs.sql); this
module only ever reads cron_jobs (to resolve job_setting -> cron_job_id) and
writes cron_job_executions -- it never creates, edits or deletes rows in
either table.

Row lifecycle (status column):
    started     row inserted, run accepted -- may still be waiting for a lock
    processing  run has actually begun (processing_started_at set)
    success     run finished and its outcome check found no failures
    failed      run raised, its outcome check found failures, it never got
                its lock, or its process died mid-run (see
                abandon_stale_executions) -- error_description says which
success/failed are final: finish_execution only ever moves a row out of
started/processing, so a later call can never overwrite a recorded outcome.

Usage:
    execution_id = start_execution("PF_SC_appointments_pull", response={...},
                                   execution_id=job_id)
    result = run_tracked(execution_id, lambda: do_work(), outcome=check_fn)
"""

import json
import os
import socket
import threading
import time
import uuid

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
        # Without this, an unreachable/slow DB host hangs on TCP connect for
        # however long the OS's own retry policy takes - minutes, not seconds.
        # With it, the worst case for a start_execution() on a request thread
        # is bounded (two connects: cron_job_id lookup + insert), so callers
        # can afford to insert synchronously before returning a job id.
        connect_timeout=5,
    )


# Identifies which server process owns a row, so abandon_stale_executions can
# tell "still running in another process" apart from "its process is gone".
_WORKER = {"host": socket.gethostname(), "pid": os.getpid()}


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


def new_execution_id() -> str:
    """A fresh id for start_execution(execution_id=...), so the id a caller
    hands back to its client is the row's own primary key."""
    return str(uuid.uuid4())


def describe_exception(exc: BaseException) -> str:
    """error_description text for an exception -- HTTPException-style errors
    carry their message in .detail, which repr() alone buries."""
    detail = getattr(exc, "detail", None)
    if detail:
        return f"{type(exc).__name__}: {detail}"
    return repr(exc)


def start_execution(job_setting: str, response: dict | None = None,
                    execution_id: str | None = None) -> str:
    """Insert a new cron_job_executions row (status='started') and return its id.

    execution_id, if given, becomes the row's primary key (see
    new_execution_id); otherwise the DB generates one. The owning process
    (host/pid) is recorded under response["worker"]."""
    cron_job_id = _get_cron_job_id(job_setting)
    conn = _get_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO "EDI_Tebra".cron_job_executions (id, cron_job_id, status, response)
            VALUES (COALESCE(%s::uuid, gen_random_uuid()), %s, 'started', %s)
            RETURNING id
            """,
            (execution_id, cron_job_id, _json_or_none({**(response or {}), "worker": _WORKER})),
        )
        new_id = cur.fetchone()[0]
        conn.commit()
        cur.close()
    finally:
        conn.close()
    return str(new_id)


def mark_processing(execution_id: str, response: dict | None = None) -> bool:
    """Move a 'started' row to 'processing' (the run has actually begun).
    response, if given, is merged into the existing response's top-level keys.
    Returns False if the row wasn't in 'started' (nothing changed)."""
    conn = _get_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            UPDATE "EDI_Tebra".cron_job_executions
            SET status = 'processing',
                processing_started_at = CURRENT_TIMESTAMP,
                response = COALESCE(response, '{}'::jsonb) || COALESCE(%s::jsonb, '{}'::jsonb)
            WHERE id = %s AND status = 'started'
            """,
            (_json_or_none(response), execution_id),
        )
        changed = cur.rowcount == 1
        conn.commit()
        cur.close()
    finally:
        conn.close()
    return changed


def finish_execution(
    execution_id: str,
    success: bool,
    error_description: str | None = None,
    response: dict | None = None,
) -> bool:
    """Move a started/processing row to its final status and set finished_at.

    response, if given, is merged into the existing response's top-level keys,
    so what start_execution recorded (request params, worker) is kept.

    Only non-final rows are touched: returns False (and changes nothing) if
    the row is already success/failed, so an outcome is recorded exactly once
    and a late safety-net call can never overwrite it.

    error_description is required by the table's CHECK constraint when
    success=False; a placeholder is used rather than letting the update fail.
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
                finished_at = CURRENT_TIMESTAMP,
                response = COALESCE(response, '{}'::jsonb) || COALESCE(%s::jsonb, '{}'::jsonb)
            WHERE id = %s AND status IN ('started', 'processing')
            """,
            (status, error_description, _json_or_none(response), execution_id),
        )
        changed = cur.rowcount == 1
        conn.commit()
        cur.close()
    finally:
        conn.close()
    return changed


def _safely(log, what: str, fn, *args, attempts: int = 3, **kwargs):
    """Run a logging write without ever letting a DB failure change the job's
    own outcome. Retried a few times (a transient blip here would otherwise
    leave the row open until the next restart's abandon sweep); if every
    attempt fails, the failure is printed and None returned."""
    for attempt in range(1, attempts + 1):
        try:
            return fn(*args, **kwargs)
        except Exception as exc:
            log(f"cron_execution_log: {what} attempt {attempt}/{attempts} failed: {exc!r}")
            if attempt < attempts:
                time.sleep(2 * attempt)
    return None


def run_tracked(execution_id: str, fn, outcome=None, summarize=None, log=print):
    """Run fn() and record its outcome on an already-started execution row.

    - marks the row 'processing' first
    - fn() raises (anything, including SystemExit) -> 'failed' with the
      exception, then re-raises it
    - fn() returns -> outcome(result) -> (success, error_description) decides
      success/failed (default: success); summarize(result) -> dict is merged
      into response (default: {"result": result})

    A failure of the logging writes themselves is printed via log() and never
    turns a successful run into a failed one, or masks the job's own error.
    """
    if not execution_id:
        return fn()

    _safely(log, "mark_processing", mark_processing, execution_id)
    try:
        result = fn()
    except BaseException as exc:
        _safely(log, "finish_execution", finish_execution, execution_id,
                success=False, error_description=describe_exception(exc))
        raise

    try:
        success, error_description = outcome(result) if outcome else (True, None)
    except Exception as exc:
        success, error_description = False, f"Outcome check itself failed: {exc!r}"
    try:
        response = summarize(result) if summarize else {"result": result}
    except Exception as exc:
        response = {"summarize_error": repr(exc)}
    _safely(log, "finish_execution", finish_execution, execution_id,
            success=success, error_description=error_description, response=response)
    return result


def abandon_stale_executions(job_settings: list[str], log=print) -> int:
    """Fail every started/processing row for these job_settings that was
    written by an earlier process on this same host -- i.e. its server was
    restarted or crashed mid-run, so nothing will ever finish it.

    Scoped to this host so a server started elsewhere against the same DB
    (e.g. a dev laptop) never touches the VM's in-flight rows. Assumes one
    server process per host for these jobs, which is how they're deployed.
    Returns the number of rows failed."""
    conn = _get_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            UPDATE "EDI_Tebra".cron_job_executions e
            SET status = 'failed',
                finished_at = CURRENT_TIMESTAMP,
                error_description = 'Abandoned: server process ' || COALESCE(e.response->'worker'->>'pid', '?')
                    || ' on ' || COALESCE(e.response->'worker'->>'host', '?')
                    || ' exited before this run finished (restart or crash).'
            FROM "EDI_Tebra".cron_jobs j
            WHERE j.id = e.cron_job_id
              AND j.job_setting = ANY(%s)
              AND e.status IN ('started', 'processing')
              AND e.response->'worker'->>'host' = %s
              AND e.response->'worker'->>'pid' IS DISTINCT FROM %s
            """,
            (list(job_settings), _WORKER["host"], str(_WORKER["pid"])),
        )
        count = cur.rowcount
        conn.commit()
        cur.close()
    finally:
        conn.close()
    if count:
        log(f"cron_execution_log: marked {count} abandoned execution row(s) failed for {job_settings}")
    return count
