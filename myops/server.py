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

import logging
import threading
import uuid
from datetime import datetime
from zoneinfo import ZoneInfo

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from ehr.pipeline import run
from ehr.selector import WorkSelector
from ehr.db import log_run_to_pch
from ehr.config import ENTITY, SUB_ENTITY, EHR_NAME
from ehr.patients import run_patient_insurance_rpa

log = logging.getLogger(__name__)
app = FastAPI(title="OPS EMR RPA API")

CST = ZoneInfo("America/Chicago")


@app.on_event("startup")
def _run_migrations():
    # Charge columns and prior schema are managed via manual ALTERs now. Run a
    # legacy ensure_appointments_schema if it's still present; else skip.
    try:
        from tebra_rpa import ensure_appointments_schema
        ensure_appointments_schema()
        print("[STARTUP] ensure_appointments_schema OK", flush=True)
    except Exception as e:
        print(f"[STARTUP] schema migration skipped: {e!r}", flush=True)


def _log_rpa_run(product_name, entity, sub_entity, start_dt, end_dt,
                 has_error, error_message=None, success_message=None):
    log_run_to_pch(
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


@app.get("/healthz")
def healthz():
    return {"status": "ok"}


@app.post("/run-tebra")
def run_tebra(request: TebraRequest):
    start_dt = datetime.strptime(request.start_date, "%Y-%m-%d")
    end_dt = datetime.strptime(request.end_date, "%Y-%m-%d")
    validate_dates(start_dt, end_dt)

    practice_name = request.practice_name.strip()
    entity = (request.entity or ENTITY).strip()
    sub_entity = (request.sub_entity or SUB_ENTITY).strip()
    ehr_name = (request.ehr_name or EHR_NAME).strip()

    lock = _acquire_key_lock(f"tebra::{entity}::{practice_name}")

    def _runner():
        try:
            sel = WorkSelector.backfill(
                start_date=start_dt.date(), end_date=end_dt.date(),
                practice=practice_name,
            )
            sel.entity, sel.sub_entity, sel.ehr_name = entity, sub_entity, ehr_name
            run(sel)
        finally:
            try:
                lock.release()
            except Exception:
                pass

    threading.Thread(target=_runner, daemon=True).start()
    return {"status": "started", "practice": practice_name,
            "start_date": request.start_date, "end_date": request.end_date}


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
