"""
OPS EMR RPA API
===============
FastAPI server on port 8010. Thin layer over ehr.pipeline.run — every Tebra
endpoint now builds a WorkSelector and calls the one pipeline.

- /run-tebra                    — ad-hoc Tebra RPA (single practice, date window)
- /run-tebra-daily              — daily trigger for ALL Tebra practices
- /run-patient-insurance-daily  — daily patient insurance scrape
- /run-combined-daily           — patients, then Tebra daily (scheduled task)
"""

import os
import logging
import threading
import time
import uuid
from datetime import datetime
from zoneinfo import ZoneInfo

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from ehr.pipeline import run
from ehr.selector import WorkSelector
from ehr.db import log_run_event, ensure_appointments_schema
from ehr.config import ENTITY, SUB_ENTITY, EHR_NAME
from ehr.patients import run_patient_insurance_rpa
from ehr.session import normalize_practice_compare

log = logging.getLogger(__name__)


def _slog(message: str):
    print(f"[SERVER] [{datetime.now(CST).strftime('%Y-%m-%d %H:%M:%S %Z')}] {message}", flush=True)


def _env_name() -> str:
    return os.environ.get("MYOPS_API_ENV", "development").strip().lower()


def _docs_enabled() -> bool:
    return _env_name() in {"dev", "development", "local"}


app = FastAPI(
    title="OPS EMR RPA API",
    docs_url="/docs" if _docs_enabled() else None,
    redoc_url="/redoc" if _docs_enabled() else None,
    openapi_url="/openapi.json" if _docs_enabled() else None,
)

CST = ZoneInfo("America/Chicago")


@app.on_event("startup")
def _run_migrations():
    # Keep startup schema checks local to the new ehr package.
    try:
        ensure_appointments_schema()
        print("[STARTUP] ensure_appointments_schema OK", flush=True)
    except Exception as e:
        print(f"[STARTUP] schema migration skipped: {e!r}", flush=True)


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


# ENTITY / SUB_ENTITY / EHR_NAME come from ehr.config (single source of truth).
MAX_DATE_RANGE_DAYS = 6  # 7 days inclusive
_locks = {}
_locks_guard = threading.Lock()


class TebraRequest(BaseModel):
    start_date: str
    end_date: str
    practice_name: str
    folder_structure: str | None = None
    wait_for_completion: bool = True
    entity: str | None = None
    sub_entity: str | None = None
    ehr_name: str | None = None


class DailyRequest(BaseModel):
    entity: str | None = None
    sub_entity: str | None = None
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
    entity = (request.entity or ENTITY).strip()
    sub_entity = (request.sub_entity or SUB_ENTITY).strip()
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
            practice=practice_name,
            folder_structure=request.folder_structure,
        )
        sel.entity, sel.sub_entity, sel.ehr_name = entity, sub_entity, ehr_name
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


@app.post("/run-tebra-daily")
def run_tebra_daily(request: DailyRequest):
    entity = (request.entity or ENTITY).strip()
    sub_entity = (request.sub_entity or SUB_ENTITY).strip()
    ehr_name = (request.ehr_name or EHR_NAME).strip()

    lock = _acquire_key_lock("__tebra_daily__")

    def _daily_runner():
        run_start = datetime.now(CST)
        summary, has_error, err = None, False, None
        try:
            sel = WorkSelector.daily()
            sel.entity, sel.sub_entity, sel.ehr_name = entity, sub_entity, ehr_name
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
    entity = (request.entity or ENTITY).strip()
    sub_entity = (request.sub_entity or SUB_ENTITY).strip()
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
    entity = (request.entity or ENTITY).strip()
    sub_entity = (request.sub_entity or SUB_ENTITY).strip()
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
                sel = WorkSelector.daily()
                sel.entity, sel.sub_entity, sel.ehr_name = entity, sub_entity, ehr_name
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
