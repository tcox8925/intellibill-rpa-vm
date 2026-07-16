"""
OPS EMR RPA API
===============
FastAPI server on port 8010.
- /run-tebra              — ad-hoc Tebra RPA (single practice)
- /run-tebra-daily        — daily trigger for ALL Tebra practices
- /run-patient-insurance-daily — daily patient insurance scrape
"""

import logging
import threading
import uuid
from datetime import datetime

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from tebra_rpa import run_tebra_rpa, ensure_appointments_schema
from ehr_patients import run_patient_insurance_rpa

log = logging.getLogger(__name__)

app = FastAPI(title="OPS EMR RPA API")


# ═════════════════════════════════════════════
# STARTUP — SCHEMA MIGRATIONS
# ═════════════════════════════════════════════

@app.on_event("startup")
def _run_migrations():
    try:
        ensure_appointments_schema()
        print("[STARTUP] ensure_appointments_schema OK", flush=True)
    except Exception as e:
        print(f"[STARTUP] ensure_appointments_schema FAILED: {e!r}", flush=True)


# ═════════════════════════════════════════════
# LOG HELPERS
# ═════════════════════════════════════════════

def _log_rpa_run(product_name, entity, sub_entity, start_dt, end_dt,
                 has_error, error_message=None, success_message=None):
    """
    Write one row to wpo.ops_pch_logs on pch. Safe on both success and
    failure paths. Never raises (log_run_to_pch swallows its own errors).
    Signature unchanged so existing callers are unaffected; sub_entity is
    no longer stored (ops_pch_logs has no sub_entity column).
    """
    from tebra_rpa import log_run_to_pch
    log_run_to_pch(
        script_name="OPS_EMR_RPA",
        process_type=product_name,
        status="Error" if has_error else "Success",
        error=error_message if has_error else None,
        company_id=entity,
        started_at=start_dt,
        ended_at=end_dt,
    )

# ═════════════════════════════════════════════
# SHARED CONSTANTS
# ═════════════════════════════════════════════

ENTITY = "270681372"
SUB_ENTITY = "270681372001"
EHR_NAME = "Tebra"

MAX_DATE_RANGE_DAYS = 6  # 7 days inclusive

# ═════════════════════════════════════════════
# EXISTING — TEBRA RPA (AD-HOC)
# ═════════════════════════════════════════════

_locks = {}
_locks_guard = threading.Lock()


class TebraRequest(BaseModel):
    start_date: str       # YYYY-MM-DD
    end_date: str         # YYYY-MM-DD
    practice_name: str
    entity: str
    sub_entity: str
    ehr_name: str


class DailyRequest(BaseModel):
    """Required payload for cron-triggered daily endpoints."""
    entity: str
    sub_entity: str
    ehr_name: str


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
        if key not in _locks:
            _locks[key] = threading.Lock()
        lock = _locks[key]
    if not lock.acquire(blocking=False):
        raise HTTPException(status_code=409, detail="A job is already running for this practice/entity")
    return lock


@app.get("/healthz")
def healthz():
    return {"ok": True}


@app.post("/run-tebra")
def run_tebra(request: TebraRequest):
    try:
        start_date = datetime.strptime(request.start_date, "%Y-%m-%d")
        end_date = datetime.strptime(request.end_date, "%Y-%m-%d")
    except ValueError:
        raise HTTPException(status_code=400, detail="Dates must be in YYYY-MM-DD format")

    validate_dates(start_date, end_date)

    practice = (request.practice_name or "").strip()
    if not practice:
        raise HTTPException(status_code=400, detail="practice_name is required")

    ehr_name = (request.ehr_name or EHR_NAME).strip()
    entity = (request.entity or ENTITY).strip()
    sub_entity = (request.sub_entity or SUB_ENTITY).strip()

    job_id = str(uuid.uuid4())
    lock_key = f"{entity}:{sub_entity}:{practice.lower()}"
    lock = _acquire_key_lock(lock_key)

    def _runner():
        try:
            print(f"[RUN-TEBRA] job_id={job_id} start={request.start_date} end={request.end_date} practice={practice} ehr={ehr_name}", flush=True)
            run_tebra_rpa(
                start_date=start_date, end_date=end_date,
                practice_name=practice, entity=entity,
                sub_entity=sub_entity, ehr_name=ehr_name,
            )
            print(f"[RUN-TEBRA] job_id={job_id} completed", flush=True)
        except Exception as e:
            print(f"[RUN-TEBRA][ERROR] job_id={job_id} err={repr(e)}", flush=True)
        finally:
            try:
                lock.release()
            except Exception:
                pass

    threading.Thread(target=_runner, daemon=True).start()
    return {"status": "started", "job_id": job_id, "ehr_name": ehr_name, "message": "EHR RPA job started"}


# ═════════════════════════════════════════════
# DAILY RUN — ALL TEBRA PRACTICES
# ═════════════════════════════════════════════

@app.post("/run-tebra-daily")
def run_tebra_daily(request: DailyRequest):
    """
    Daily trigger: discovers ALL practices from Tebra UI,
    runs each one for today's date (CST).
    """
    from zoneinfo import ZoneInfo
    from tebra_rpa import discover_tebra_practices

    entity = request.entity.strip()
    sub_entity = request.sub_entity.strip()
    ehr_name = request.ehr_name.strip()

    today_cst = datetime.now(ZoneInfo("America/Chicago")).date()
    start_date = datetime(today_cst.year, today_cst.month, today_cst.day)
    end_date = start_date

    # Discover practices from Tebra UI (quick browser login)
    practices = discover_tebra_practices()

    if not practices:
        return {"status": "skipped", "message": "No practices found in Tebra UI"}

    job_id = str(uuid.uuid4())
    lock_key = "__daily_tebra__"
    lock = _acquire_key_lock(lock_key)

    def _daily_runner():
        completed, failed = [], []
        try:
            for practice_name in practices:
                print(f"[DAILY] Running: {practice_name}", flush=True)
                try:
                    run_tebra_rpa(
                        start_date=start_date,
                        end_date=end_date,
                        practice_name=practice_name,
                        entity=entity,
                        sub_entity=sub_entity,
                        ehr_name=ehr_name,
                    )
                    completed.append(practice_name)
                    print(f"[DAILY] Completed: {practice_name}", flush=True)
                except Exception as e:
                    import traceback
                    failed.append(practice_name)
                    print(f"[DAILY][ERROR] {practice_name}:", flush=True)
                    traceback.print_exc()

            print(f"[DAILY] Done. job_id={job_id} completed={completed} failed={failed}", flush=True)
        finally:
            try:
                lock.release()
            except Exception:
                pass

    threading.Thread(target=_daily_runner, daemon=True).start()

    return {
        "status": "started",
        "job_id": job_id,
        "date": str(today_cst),
        "entity": entity,
        "sub_entity": sub_entity,
        "practices": practices,
        "message": f"Daily run started for {len(practices)} practice(s)",
    }


# ═════════════════════════════════════════════
# DAILY RUN — PATIENT INSURANCE RPA
# ═════════════════════════════════════════════

@app.post("/run-patient-insurance-daily")
def run_patient_insurance_daily(request: DailyRequest):

    entity = request.entity.strip()
    sub_entity = request.sub_entity.strip()
    ehr_name = request.ehr_name.strip()

    job_id = str(uuid.uuid4())
    lock_key = "__daily_patient_insurance__"
    lock = _acquire_key_lock(lock_key)

    def _runner():
        try:
            print(f"[PATIENT-INS] Starting. job_id={job_id}", flush=True)
            run_patient_insurance_rpa(
                entity=entity,
                sub_entity=sub_entity,
                ehr_name=ehr_name,
            )
            print(f"[PATIENT-INS] Completed. job_id={job_id}", flush=True)
        except Exception as e:
            import traceback
            print(f"[PATIENT-INS][ERROR] job_id={job_id}:", flush=True)
            traceback.print_exc()
        finally:
            try:
                lock.release()
            except Exception:
                pass

    threading.Thread(target=_runner, daemon=True).start()

    return {
        "status": "started",
        "job_id": job_id,
        "entity": entity,
        "sub_entity": sub_entity,
        "ehr_name": ehr_name,
        "message": "Patient Insurance RPA started",
    }


# ═════════════════════════════════════════════
# DAILY RUN — COMBINED (patients first, then tebra)
# ═════════════════════════════════════════════

@app.post("/run-combined-daily")
def run_combined_daily(request: DailyRequest):
    """
    Single-trigger daily job (called by Windows Scheduled Task):
      1. Patient Insurance RPA (full roster upsert, all practices)
      2. Tebra RPA for today (CST), all practices discovered from UI

    Runs sequentially in a background thread; holds one lock to prevent
    overlapping invocations. Writes a parent COMBINED_DAILY row to
    wpo.ops_pch_logs capturing overall success/failure.
    """
    from zoneinfo import ZoneInfo
    from tebra_rpa import discover_tebra_practices

    entity = request.entity.strip()
    sub_entity = request.sub_entity.strip()
    ehr_name = request.ehr_name.strip()

    today_cst = datetime.now(ZoneInfo("America/Chicago")).date()
    start_date = datetime(today_cst.year, today_cst.month, today_cst.day)
    end_date = start_date

    job_id = str(uuid.uuid4())
    lock_key = "__daily_combined__"
    lock = _acquire_key_lock(lock_key)

    def _combined_runner():
        run_start = datetime.now(ZoneInfo("America/Chicago"))
        step_patients_error = None
        step_tebra_failed = []
        step_tebra_completed = []
        practices = []

        try:
            # ---- Step 1: Patients ----
            print(f"[COMBINED] job_id={job_id} step=patients starting", flush=True)
            try:
                run_patient_insurance_rpa(
                    entity=entity,
                    sub_entity=sub_entity,
                    ehr_name=ehr_name,
                )
                print(f"[COMBINED] job_id={job_id} step=patients done", flush=True)
            except Exception as e:
                import traceback
                step_patients_error = repr(e)
                print(f"[COMBINED][ERROR] job_id={job_id} step=patients", flush=True)
                traceback.print_exc()

            # ---- Step 2: Tebra per practice ----
            print(f"[COMBINED] job_id={job_id} step=tebra discovering practices", flush=True)
            try:
                practices = discover_tebra_practices()
            except Exception:
                import traceback
                print(f"[COMBINED][ERROR] job_id={job_id} step=tebra-discover", flush=True)
                traceback.print_exc()
                practices = []

            for practice_name in practices:
                print(f"[COMBINED] job_id={job_id} tebra practice={practice_name}", flush=True)
                try:
                    run_tebra_rpa(
                        start_date=start_date,
                        end_date=end_date,
                        practice_name=practice_name,
                        entity=entity,
                        sub_entity=sub_entity,
                        ehr_name=ehr_name,
                    )
                    step_tebra_completed.append(practice_name)
                except Exception:
                    import traceback
                    step_tebra_failed.append(practice_name)
                    print(f"[COMBINED][ERROR] job_id={job_id} practice={practice_name}", flush=True)
                    traceback.print_exc()

            print(
                f"[COMBINED] job_id={job_id} done "
                f"completed={step_tebra_completed} failed={step_tebra_failed}",
                flush=True,
            )
        finally:
            # ---- Parent log row ----
            run_end = datetime.now(ZoneInfo("America/Chicago"))
            has_error = bool(step_patients_error) or bool(step_tebra_failed)

            summary_bits = []
            if step_patients_error:
                summary_bits.append(f"patients: {step_patients_error}")
            if step_tebra_failed:
                summary_bits.append(f"tebra failed: {step_tebra_failed}")
            error_message = " | ".join(summary_bits) if summary_bits else None

            success_message = (
                f"completed={len(step_tebra_completed)}/{len(practices)} practice(s)"
                if not has_error else None
            )

            _log_rpa_run(
                product_name="COMBINED_DAILY",
                entity=entity,
                sub_entity=sub_entity,
                start_dt=run_start,
                end_dt=run_end,
                has_error=has_error,
                error_message=error_message,
                success_message=success_message,
            )

            try:
                lock.release()
            except Exception:
                pass

    threading.Thread(target=_combined_runner, daemon=True).start()

    return {
        "status": "started",
        "job_id": job_id,
        "date": str(today_cst),
        "entity": entity,
        "sub_entity": sub_entity,
        "ehr_name": ehr_name,
        "message": "Combined daily run started (patients first, then Tebra)",
    }