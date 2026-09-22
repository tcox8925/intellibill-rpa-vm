"""
Tebra-only patient sync API.

Same Tebra endpoints as app.py's combined (Tebra + Practice Fusion) app, but
isolated so Tebra sync can run/restart independently of Practice Fusion.

Standalone: `python -m uvicorn app_tebra:app --reload --port 8010`, docs at
http://localhost:8010/docs.

Production: mounted at /patient-sync by the repo-root server.py (the process
myops.service actually runs on port 8010 alongside the Tebra RPA and
Practice Fusion sync APIs) -- there its docs resolve at
http://<host>:8010/patient-sync/docs, same pattern as pf-sync's
/pf-sync/docs.
"""
import threading
import uuid
from datetime import datetime
from zoneinfo import ZoneInfo

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from tebra.load_patient_coverages import run_load_patient_coverages
from tebra.load_patient_header import run_load_patient_header
from tebra.tebra_api import pull_patient_demographics

try:
    # Lives at the repo root, two levels up -- only importable when this
    # module is loaded as part of the combined server.py process (repo root
    # is on sys.path there). Fall back to no-ops so this file still runs
    # standalone (`python -m uvicorn app_tebra:app` from inside
    # tebra_patient_sync/), which has no "EDI_Tebra".cron_jobs row to look
    # up / DB creds configured for anyway in a bare standalone run.
    from cron_execution_log import start_execution, finish_execution, mark_processing
except ImportError:
    def start_execution(job_setting: str, response: dict | None = None) -> str:
        return ""

    def finish_execution(execution_id: str, success: bool, error_description: str | None = None, response: dict | None = None) -> None:
        pass

    def mark_processing(execution_id: str, response: dict | None = None) -> None:
        pass

# job_setting value for this job's row in "EDI_Tebra".cron_jobs (already
# seeded manually).
_TEBRA_PATIENT_SYNC_JOB_SETTING = "TEBRA_PATIENT_SYNC"

CST = ZoneInfo("America/Chicago")


def _slog(message: str) -> None:
    print(f"[TEBRA-PATIENT-SYNC] [{datetime.now(CST).strftime('%Y-%m-%d %H:%M:%S %Z')}] {message}", flush=True)


app = FastAPI(title="Tebra Patient Sync")

# Only one sync should ever run at a time -- SOAP calls to the same Tebra
# account, then sequential DB loads. Non-blocking acquire: a caller that
# lands while one is already running gets told so immediately instead of
# queueing up behind it.
_sync_lock = threading.Lock()


@app.post("/tebra/pull-demographics")
def trigger_pull():
    patients = pull_patient_demographics()
    patient_records = patients.get("PatientData") or []
    return {"patients_pulled": len(patient_records)}


class TebraSyncRequest(BaseModel):
    # Was previously a fully synchronous call with no request body at all --
    # this is the single new field. Defaults to True so any existing caller
    # that still POSTs with no body (or an old client that predates this
    # field) keeps today's exact blocking behavior; a caller opting into
    # fire-and-forget has to say so explicitly.
    wait_for_completion: bool = True


def _run_sync() -> dict:
    """Pulls demographics, then loads patient_header, then patient_coverages -
    the full end-to-end Tebra sync."""
    patients = pull_patient_demographics()
    patient_records = patients.get("PatientData") or []

    headers_processed = run_load_patient_header()
    coverages_processed = run_load_patient_coverages()

    return {
        "patients_pulled": len(patient_records),
        "headers_processed": headers_processed,
        "coverage_records_processed": coverages_processed,
    }


@app.post("/tebra/sync")
def trigger_sync(request: TebraSyncRequest = TebraSyncRequest()):
    """Runs the full end-to-end Tebra sync.

    Historically this always blocked the HTTP connection open for the whole
    sync (demographics pull -> patient_header load -> patient_coverages
    load), which is exactly what made TebraPatientSyncJob.js's scheduled 2 AM
    CST run intermittently exceed its 10-minute client-side axios timeout --
    a cold VM / slow SOAP response ate into the same budget a manual same-day
    retrigger (warm VM) didn't have to pay. wait_for_completion=False now
    fires the sync in a background thread and returns 202 immediately;
    logged to "EDI_Tebra".cron_job_executions for the real outcome afterward.
    """
    if not _sync_lock.acquire(blocking=False):
        _slog("tebra/sync rejected - a sync is already running")
        return JSONResponse(
            status_code=409,
            content={"status": "already_running"},
        )

    if request.wait_for_completion:
        _slog("tebra/sync executing (wait_for_completion=true)")
        execution_id = None
        try:
            execution_id = start_execution(_TEBRA_PATIENT_SYNC_JOB_SETTING)
            if execution_id:
                mark_processing(execution_id)
        except Exception as e:
            _slog(f"tebra/sync failed to start execution logging: {e!r}")
        try:
            result = _run_sync()
            if execution_id:
                finish_execution(execution_id, success=True, response=result)
            _slog(f"tebra/sync done: {result}")
            return {"status": "completed", **result}
        except Exception as e:
            if execution_id:
                finish_execution(execution_id, success=False, error_description=repr(e))
            _slog(f"tebra/sync failed: {e!r}")
            raise
        finally:
            _sync_lock.release()

    req_id = str(uuid.uuid4())

    def _runner():
        # start_execution() (a Postgres round-trip) deliberately happens in
        # here, not before threading.Thread(...).start() below -- this whole
        # function only exists so the HTTP response can return the instant
        # the sync is accepted. A DB write on the request thread would put
        # the exact same kind of open-ended wait back in front of that
        # response that wait_for_completion=False was built to remove (and
        # did, on 2026-09-17: a slow/unreachable DB from this run's
        # environment hung start_execution() past TebraPatientSyncJob.js's
        # 30s trigger timeout, so it looked identical to the original
        # 10-minute sync timeout bug -- and logged nothing, since the INSERT
        # never got a chance to run).
        execution_id = None
        try:
            execution_id = start_execution(_TEBRA_PATIENT_SYNC_JOB_SETTING, response={"request_id": req_id})
            if execution_id:
                mark_processing(execution_id)
        except Exception as e:
            _slog(f"tebra/sync background req_id={req_id} failed to start execution logging: {e!r}")

        _slog(f"tebra/sync background req_id={req_id} starting")
        try:
            result = _run_sync()
            if execution_id:
                finish_execution(execution_id, success=True, response={**result, "request_id": req_id})
            _slog(f"tebra/sync background req_id={req_id} done: {result}")
        except Exception as e:
            if execution_id:
                finish_execution(execution_id, success=False, error_description=repr(e), response={"request_id": req_id})
            _slog(f"tebra/sync background req_id={req_id} failed: {e!r}")
        finally:
            _sync_lock.release()

    threading.Thread(target=_runner, daemon=True).start()
    _slog(f"tebra/sync accepted background req_id={req_id}")
    return JSONResponse(
        status_code=202,
        content={
            "status": "started",
            "request_id": req_id,
            "message": "Run accepted and executing in background. Logged to EDI_Tebra.cron_job_executions.",
        },
    )


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8010)
