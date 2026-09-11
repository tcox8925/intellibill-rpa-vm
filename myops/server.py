"""
OPS EMR RPA API
===============
FastAPI server on port 8010. Thin layer over ehr.pipeline.run — every Tebra
endpoint now builds a WorkSelector and calls the one pipeline.

- /run-tebra                            — ad-hoc Tebra RPA (single practice, date window)
- /run-tebra-daily                      — daily trigger for ALL Tebra practices
- /run-patient-insurance-daily          — daily patient insurance scrape
- /run-combined-daily                   — patients, then Tebra daily (scheduled task)
- /run-daily-pdf-processor               — Tebra medical-extraction PDF processor
- /run-daily-practice-fusion-pdf-processor — Practice Fusion facesheet PDF processor

The last two are ports of intellibill-rpa's DailyPdfProcessorJob.js /
DailyPracticeFusionPdfProcessorJob.js (see ehr/pdf_processor.py and
ehr/pf_facesheet_processor.py) — that Azure Function App no longer schedules
or runs them, and neither runs on a fixed cron here either. Instead they're
event-triggered: ehr/zipbuild.py (Tebra) and
pf_sync_v5_6/pf_sync_pkg/rcm_upload.py (Practice Fusion) call the two
`_run_*_job` functions below directly, right after a ZIP upload to
`rcm-attachments` actually succeeds. The endpoints below just let them also
be triggered on demand / for testing.
"""

import os
import logging
import threading
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime
from zoneinfo import ZoneInfo

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from ehr.pipeline import run
from ehr.selector import WorkSelector
from ehr.db import log_run_event, ensure_appointments_schema
from ehr.config import EHR_NAME
from ehr.patients import run_patient_insurance_rpa
from ehr.session import normalize_practice_compare
from ehr.pdf_processor import run_daily_pdf_processor
from ehr.pf_facesheet_processor import run_daily_practice_fusion_pdf_processor

log = logging.getLogger(__name__)


def _slog(message: str):
    print(f"[SERVER] [{datetime.now(CST).strftime('%Y-%m-%d %H:%M:%S %Z')}] {message}", flush=True)


def _env_name() -> str:
    return os.environ.get("MYOPS_API_ENV", "development").strip().lower()


def _docs_enabled() -> bool:
    return _env_name() in {"dev", "development", "local"}


CST = ZoneInfo("America/Chicago")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    # Keep startup schema checks local to the new ehr package. Exposed as a
    # module-level `lifespan` (not the deprecated @app.on_event("startup"))
    # so the combined repo-root server.py can compose it directly
    # (`async with lifespan(tebra_app): ...`) instead of duplicating this
    # logic -- mounting this app as a sub-app does not make its own lifespan
    # fire automatically, so whoever hosts it must invoke it explicitly.
    try:
        ensure_appointments_schema()
        print("[STARTUP] ensure_appointments_schema OK", flush=True)
    except Exception as e:
        print(f"[STARTUP] schema migration skipped: {e!r}", flush=True)
    yield


app = FastAPI(
    title="OPS EMR RPA API",
    docs_url="/docs" if _docs_enabled() else None,
    redoc_url="/redoc" if _docs_enabled() else None,
    openapi_url="/openapi.json" if _docs_enabled() else None,
    lifespan=lifespan,
)


def _log_rpa_run(product_name, entity, sub_entity, start_dt, end_dt,
                 has_error, error_message=None, success_message=None):
    log_run_event(
        script_name="OPS_EMR_RPA",
        process_type=product_name,
        status="Error" if has_error else "Success",
        error=error_message if has_error else None,
        company_id=entity,
        started_at=start_dt,
        ended_at=end_dt,
    )


# entity/sub_entity are REQUIRED on every request below, no fallback default.
# Confirmed live 2026-09-11: a silent `request.entity or ENTITY` fallback to
# a hardcoded tenant is exactly how 1710 real ehr_patients rows ended up
# permanently under the wrong, unused entity (270681372) while every
# ehr_appointments row is under the real one (414584128) -- the caller that
# never passed entity in its payload never got an error, it just silently
# wrote to the wrong tenant. A required Pydantic field fails the request with
# a 422 instead. EHR_NAME still defaults -- it's the EHR product, not tenant
# identity, and this package only talks to Tebra today.
MAX_DATE_RANGE_DAYS = 6  # 7 days inclusive
_locks = {}
_locks_guard = threading.Lock()


class TebraRequest(BaseModel):
    start_date: str
    end_date: str
    practice_name: str
    entity: str
    sub_entity: str
    folder_structure: str | None = None
    wait_for_completion: bool = True
    ehr_name: str | None = None


class DailyRequest(BaseModel):
    entity: str
    sub_entity: str
    ehr_name: str | None = None


def validate_dates(start_date: datetime, end_date: datetime):
    delta_days = (end_date.date() - start_date.date()).days
    if delta_days < 0:
        raise HTTPException(status_code=400, detail="end_date cannot be before start_date")
    if delta_days > MAX_DATE_RANGE_DAYS:
        raise HTTPException(
            status_code=400,
            detail=f"Date range cannot exceed {MAX_DATE_RANGE_DAYS} days (inclusive)",
        )


def _acquire_key_lock(key: str) -> threading.Lock:
    with _locks_guard:
        lock = _locks.setdefault(key, threading.Lock())
    if not lock.acquire(blocking=False):
        raise HTTPException(status_code=409, detail="A job is already running for this practice/entity")
    return lock


def _normalize_practice_name(practice_name: str) -> str:
    practice_name = practice_name.strip()
    normalized = normalize_practice_compare(practice_name)
    if not normalized:
        raise HTTPException(status_code=400, detail="practice_name cannot be empty")
    return normalized


@app.get("/healthz")
def healthz():
    return {"status": "ok"}


@app.post("/run-tebra")
def run_tebra(request: TebraRequest):
    request_clock = time.monotonic()
    req_id = str(uuid.uuid4())
    start_dt = datetime.strptime(request.start_date, "%Y-%m-%d")
    end_dt = datetime.strptime(request.end_date, "%Y-%m-%d")
    validate_dates(start_dt, end_dt)

    practice_name = _normalize_practice_name(request.practice_name)
    entity = request.entity.strip()
    sub_entity = request.sub_entity.strip()
    ehr_name = (request.ehr_name or EHR_NAME).strip()

    _slog(
        f"run-tebra received req_id={req_id} practice={practice_name} "
        f"window={request.start_date}..{request.end_date} wait_for_completion={request.wait_for_completion}"
    )

    lock = _acquire_key_lock(f"tebra::{entity}::{practice_name}")

    def _execute_run():
        _slog(f"run-tebra executing req_id={req_id}")
        sel = WorkSelector.backfill(
            start_date=start_dt.date(), end_date=end_dt.date(),
            entity=entity, sub_entity=sub_entity, ehr_name=ehr_name,
            practice=practice_name,
            folder_structure=request.folder_structure,
        )
        summary = run(sel, scrape_patients=False)
        _slog(f"run-tebra done req_id={req_id} summary={summary}")
        return summary

    def _runner():
        try:
            _execute_run()
        except Exception as e:
            _slog(f"run-tebra failed req_id={req_id} error={e!r}")
        finally:
            try:
                lock.release()
            except Exception:
                pass
            _slog(
                f"run-tebra background cleanup req_id={req_id} "
                f"elapsed={time.monotonic() - request_clock:.1f}s"
            )

    if request.wait_for_completion:
        try:
            summary = _execute_run()
            _slog(
                f"run-tebra response completed req_id={req_id} "
                f"elapsed={time.monotonic() - request_clock:.1f}s"
            )
            return {
                "status": "completed",
                "request_id": req_id,
                "practice": practice_name,
                "start_date": request.start_date,
                "end_date": request.end_date,
                "summary": summary,
            }
        finally:
            try:
                lock.release()
            except Exception:
                pass

    threading.Thread(target=_runner, daemon=True).start()
    _slog(f"run-tebra accepted background req_id={req_id}")
    return JSONResponse(
        status_code=202,
        content={
            "status": "started",
            "request_id": req_id,
            "practice": practice_name,
            "start_date": request.start_date,
            "end_date": request.end_date,
            "message": "Run accepted and executing in background. Set wait_for_completion=true to wait for completion.",
        },
    )


# /run-tebra caps start_date..end_date at MAX_DATE_RANGE_DAYS (7 days) because
# that window also drives a LIVE Tebra worklist calendar scrape (pass_appointments),
# which is slow per day. /run-tebra-recheck below skips that scrape entirely --
# it only reruns the DB-only recheck passes (notes/facesheets/charges) for rows
# already in the table, so a much wider window costs nothing extra. This is what
# catches a note signed weeks after its appointment date: backfill mode's
# facesheets gate already ignores process_status (see query.py's
# UNGATED_REPULL), so any signed-but-unprocessed row whose appt_date falls
# inside this window gets its facesheet pulled here, no matter how long ago it
# was signed.
RECHECK_MAX_DATE_RANGE_DAYS = 365


class RecheckRequest(BaseModel):
    start_date: str
    end_date: str
    entity: str
    sub_entity: str
    practice_name: str | None = None  # None = every discovered practice
    wait_for_completion: bool = True
    ehr_name: str | None = None


@app.post("/run-tebra-recheck")
def run_tebra_recheck(request: RecheckRequest):
    request_clock = time.monotonic()
    req_id = str(uuid.uuid4())
    start_dt = datetime.strptime(request.start_date, "%Y-%m-%d")
    end_dt = datetime.strptime(request.end_date, "%Y-%m-%d")
    delta_days = (end_dt.date() - start_dt.date()).days
    if delta_days < 0:
        raise HTTPException(status_code=400, detail="end_date cannot be before start_date")
    if delta_days > RECHECK_MAX_DATE_RANGE_DAYS:
        raise HTTPException(
            status_code=400,
            detail=f"Date range cannot exceed {RECHECK_MAX_DATE_RANGE_DAYS} days (inclusive)",
        )

    practice_name = _normalize_practice_name(request.practice_name) if request.practice_name else None
    entity = request.entity.strip()
    sub_entity = request.sub_entity.strip()
    ehr_name = (request.ehr_name or EHR_NAME).strip()

    _slog(
        f"run-tebra-recheck received req_id={req_id} practice={practice_name or 'ALL'} "
        f"window={request.start_date}..{request.end_date} wait_for_completion={request.wait_for_completion}"
    )

    lock = _acquire_key_lock(f"tebra-recheck::{entity}::{practice_name or 'ALL'}")

    def _execute_run():
        _slog(f"run-tebra-recheck executing req_id={req_id}")
        sel = WorkSelector.backfill(
            start_date=start_dt.date(), end_date=end_dt.date(),
            entity=entity, sub_entity=sub_entity, ehr_name=ehr_name,
            practice=practice_name,
            # True (default): re-pull facesheets for every signed row in this
            # window regardless of prior process_status -- already-Processed
            # rows get collected/re-verified too, not skipped. This is the
            # same contract /run-tebra already has, just over a much wider
            # window since the live appointment scrape is skipped here.
            ungated_repull=True,
        )
        summary = run(sel, scrape_patients=False, skip_appointment_scrape=True)
        _slog(f"run-tebra-recheck done req_id={req_id} summary={summary}")
        return summary

    def _runner():
        try:
            _execute_run()
        except Exception as e:
            _slog(f"run-tebra-recheck failed req_id={req_id} error={e!r}")
        finally:
            try:
                lock.release()
            except Exception:
                pass
            _slog(
                f"run-tebra-recheck background cleanup req_id={req_id} "
                f"elapsed={time.monotonic() - request_clock:.1f}s"
            )

    if request.wait_for_completion:
        try:
            summary = _execute_run()
            _slog(
                f"run-tebra-recheck response completed req_id={req_id} "
                f"elapsed={time.monotonic() - request_clock:.1f}s"
            )
            return {
                "status": "completed",
                "request_id": req_id,
                "practice": practice_name or "ALL",
                "start_date": request.start_date,
                "end_date": request.end_date,
                "summary": summary,
            }
        finally:
            try:
                lock.release()
            except Exception:
                pass

    threading.Thread(target=_runner, daemon=True).start()
    _slog(f"run-tebra-recheck accepted background req_id={req_id}")
    return JSONResponse(
        status_code=202,
        content={
            "status": "started",
            "request_id": req_id,
            "practice": practice_name or "ALL",
            "start_date": request.start_date,
            "end_date": request.end_date,
            "message": "Recheck accepted and executing in background. Set wait_for_completion=true to wait for completion.",
        },
    )


@app.post("/run-tebra-daily")
def run_tebra_daily(request: DailyRequest):
    entity = request.entity.strip()
    sub_entity = request.sub_entity.strip()
    ehr_name = (request.ehr_name or EHR_NAME).strip()

    lock = _acquire_key_lock("__tebra_daily__")

    def _daily_runner():
        run_start = datetime.now(CST)
        summary, has_error, err = None, False, None
        try:
            sel = WorkSelector.daily(entity=entity, sub_entity=sub_entity, ehr_name=ehr_name)
            summary = run(sel)
            has_error = bool(summary and summary.get("failed"))
            if has_error:
                err = f"tebra failed: {summary['failed']}"
        except Exception as e:
            has_error, err = True, repr(e)
        finally:
            _log_rpa_run("TEBRA_DAILY", entity, sub_entity, run_start,
                         datetime.now(CST), has_error, error_message=err)
            try:
                lock.release()
            except Exception:
                pass

    threading.Thread(target=_daily_runner, daemon=True).start()
    return {"status": "started", "date": str(datetime.now(CST).date())}


@app.post("/run-patient-insurance-daily")
def run_patient_insurance_daily(request: DailyRequest):
    entity = request.entity.strip()
    sub_entity = request.sub_entity.strip()
    ehr_name = (request.ehr_name or EHR_NAME).strip()

    lock = _acquire_key_lock("__patient_insurance_daily__")

    def _runner():
        run_start = datetime.now(CST)
        has_error, err = False, None
        try:
            run_patient_insurance_rpa(entity=entity, sub_entity=sub_entity, ehr_name=ehr_name)
        except Exception as e:
            has_error, err = True, repr(e)
        finally:
            _log_rpa_run("PATIENT_INSURANCE_DAILY", entity, sub_entity, run_start,
                         datetime.now(CST), has_error, error_message=err)
            try:
                lock.release()
            except Exception:
                pass

    threading.Thread(target=_runner, daemon=True).start()
    return {"status": "started"}


@app.post("/run-combined-daily")
def run_combined_daily(request: DailyRequest):
    entity = request.entity.strip()
    sub_entity = request.sub_entity.strip()
    ehr_name = (request.ehr_name or EHR_NAME).strip()

    job_id = str(uuid.uuid4())
    lock = _acquire_key_lock("__daily_combined__")

    def _combined_runner():
        run_start = datetime.now(CST)
        step_patients_error = None
        summary = None
        try:
            print(f"[COMBINED] job_id={job_id} step=patients starting", flush=True)
            try:
                run_patient_insurance_rpa(entity=entity, sub_entity=sub_entity, ehr_name=ehr_name)
                print(f"[COMBINED] job_id={job_id} step=patients done", flush=True)
            except Exception as e:
                import traceback
                step_patients_error = repr(e)
                print(f"[COMBINED][ERROR] job_id={job_id} step=patients", flush=True)
                traceback.print_exc()

            print(f"[COMBINED] job_id={job_id} step=tebra starting", flush=True)
            try:
                sel = WorkSelector.daily(entity=entity, sub_entity=sub_entity, ehr_name=ehr_name)
                summary = run(sel, scrape_patients=False)  # patients already done above
                print(f"[COMBINED] job_id={job_id} step=tebra done "
                      f"completed={summary.get('completed')} failed={summary.get('failed')}",
                      flush=True)
            except Exception:
                import traceback
                print(f"[COMBINED][ERROR] job_id={job_id} step=tebra", flush=True)
                traceback.print_exc()
        finally:
            run_end = datetime.now(CST)
            tebra_failed = (summary or {}).get("failed") or []
            has_error = bool(step_patients_error) or bool(tebra_failed) or summary is None

            bits = []
            if step_patients_error:
                bits.append(f"patients: {step_patients_error}")
            if tebra_failed:
                bits.append(f"tebra failed: {tebra_failed}")
            if summary is None:
                bits.append("tebra: run raised")
            error_message = " | ".join(bits) if bits else None

            _log_rpa_run("COMBINED_DAILY", entity, sub_entity, run_start, run_end,
                         has_error, error_message=error_message)
            try:
                lock.release()
            except Exception:
                pass

    threading.Thread(target=_combined_runner, daemon=True).start()
    return {"status": "started", "job_id": job_id,
            "date": str(datetime.now(CST).date()),
            "message": "Combined daily run started (patients first, then Tebra)"}


# --------------------------------------------------------------------- #
#  PDF processors (ported from intellibill-rpa, see module docstring).  #
#  Event-triggered from ehr/zipbuild.py and                             #
#  pf_sync_v5_6/pf_sync_pkg/rcm_upload.py right after a ZIP upload      #
#  succeeds -- see _run_daily_pdf_processor_job/_run_pf_facesheet_      #
#  processor_job's own docstrings for how those callers reach these.    #
#  No fixed cron: nothing here runs on a timer.                         #
# --------------------------------------------------------------------- #

def _job_log(prefix):
    def log(*args):
        message = " ".join(str(a) for a in args)
        print(f"[{prefix}] [{datetime.now(CST).strftime('%Y-%m-%d %H:%M:%S %Z')}] {message}", flush=True)
    return log


def _run_daily_pdf_processor_job():
    """Runs run_daily_pdf_processor with the same locking/logging every
    caller gets, regardless of how it was reached: the /run-daily-pdf-processor
    endpoint below, or ehr/zipbuild.py's post-upload trigger (imported and
    called directly, in a background thread, since zipbuild.py lives in this
    same ehr package -- no HTTP hop needed)."""
    try:
        lock = _acquire_key_lock("__daily_pdf_processor__")
    except HTTPException:
        _slog("daily_pdf_processor already running - skipping this trigger")
        return

    run_start = datetime.now(CST)
    has_error, err = False, None
    try:
        run_daily_pdf_processor(_job_log("PDF-PROCESSOR"))
    except Exception as e:
        has_error, err = True, repr(e)
    finally:
        # Not entity-scoped -- run_daily_pdf_processor walks every tenant's
        # blob folder in one pass, so there's no single entity to attribute
        # this run to. company_id is informational only (log_run_event is a
        # no-op today).
        _log_rpa_run("DAILY_PDF_PROCESSOR", "ALL", "ALL", run_start,
                     datetime.now(CST), has_error, error_message=err)
        try:
            lock.release()
        except Exception:
            pass


def _run_pf_facesheet_processor_job():
    """Same pattern as _run_daily_pdf_processor_job above. Reached from the
    /run-daily-practice-fusion-pdf-processor endpoint below, or from
    pf_sync_v5_6/pf_sync_pkg/rcm_upload.py's post-upload trigger -- that
    caller lives in a different top-level project, so it imports this
    function via `ehr.pdf_processor`'s sibling module rather than reaching
    into server.py, and only when it's actually running inside this combined
    process (see rcm_upload.py's _trigger_pf_facesheet_processor)."""
    try:
        lock = _acquire_key_lock("__daily_pf_facesheet_processor__")
    except HTTPException:
        _slog("daily_pf_facesheet_processor already running - skipping this trigger")
        return

    run_start = datetime.now(CST)
    has_error, err = False, None
    try:
        run_daily_practice_fusion_pdf_processor(_job_log("PF-FACESHEET-PROCESSOR"))
    except Exception as e:
        has_error, err = True, repr(e)
    finally:
        # Same reasoning as _run_daily_pdf_processor_job above -- this job is
        # scoped to one fixed PF blob folder, not one entity.
        _log_rpa_run("DAILY_PF_FACESHEET_PROCESSOR", "ALL", "ALL", run_start,
                     datetime.now(CST), has_error, error_message=err)
        try:
            lock.release()
        except Exception:
            pass


@app.post("/run-daily-pdf-processor")
def run_daily_pdf_processor_endpoint():
    """On-demand / manual-testing trigger -- the real trigger is
    ehr/zipbuild.py, right after a ZIP upload succeeds."""
    threading.Thread(target=_run_daily_pdf_processor_job, daemon=True).start()
    return {"status": "started"}


@app.post("/run-daily-practice-fusion-pdf-processor")
def run_daily_practice_fusion_pdf_processor_endpoint():
    """On-demand / manual-testing trigger -- the real trigger is
    pf_sync_v5_6/pf_sync_pkg/rcm_upload.py, right after a ZIP upload succeeds."""
    threading.Thread(target=_run_pf_facesheet_processor_job, daemon=True).start()
    return {"status": "started"}
