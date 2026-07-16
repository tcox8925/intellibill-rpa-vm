"""
ACU/BOB orchestrator API.

Run:  uvicorn api:app --host 0.0.0.0 --port 8020
"""

import os
import threading
from datetime import datetime

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from acu_runner import (
    run_acu_pipeline, _pipeline_succeeded, carrier_id_for_filename,
)
from bob_runner import run_bob_pipeline, _bob_pipeline_succeeded
from job_tracking import job_start, job_finish, PROCESS_HISTORY_TABLE
from utils.db_utils import get_postgres_connection

app = FastAPI(title="ACU/BOB API")

_busy = set()
_busy_lock = threading.Lock()


class RunRequest(BaseModel):
    process_type: str
    filename: str
    report_date: str
    product: str | None = None


@app.post("/run-file-processing")
def run_file_processing(req: RunRequest):
    process_type = req.process_type.strip().upper()
    if process_type not in ("ACU", "BOB"):
        raise HTTPException(400, "process_type must be ACU or BOB")

    try:
        report_dt = datetime.strptime(req.report_date.strip(), "%Y-%m-%d")
    except ValueError:
        raise HTTPException(400, "report_date must be YYYY-MM-DD")

    filename = os.path.basename(req.filename.strip())
    if not filename:
        raise HTTPException(400, "filename is required")

    conn = get_postgres_connection()
    carrier_id = carrier_id_for_filename(conn, process_type, filename)
    if not carrier_id:
        conn.close()
        raise HTTPException(404, f"No active rule found for file: {filename}")
    conn.close()

    job_key = f"{process_type}:{filename}"
    with _busy_lock:
        if job_key in _busy:
            raise HTTPException(409, "This file is already processing")
        _busy.add(job_key)

    conn = get_postgres_connection()
    job_id = job_start(
        conn, process_type, carrier_id, filename,
        report_month=req.report_date, product_name=req.product,
    )
    conn.close()

    def run_job():
        try:
            if process_type == "ACU":
                result = run_acu_pipeline(
                    scan_date=report_dt.date(), file_filter=filename, track_jobs=False,
                )
                ok = _pipeline_succeeded(result)
            else:
                results = run_bob_pipeline(
                    run_date=req.report_date, file_filter=filename, track_jobs=False,
                )
                ok = _bob_pipeline_succeeded(results)
            finish_conn = get_postgres_connection()
            job_finish(
                finish_conn, process_type, carrier_id, job_id,
                "SUCCESS" if ok else "FAILED",
                file_name=filename, report_month=req.report_date,
            )
            finish_conn.close()
        except Exception:
            try:
                finish_conn = get_postgres_connection()
                job_finish(
                    finish_conn, process_type, carrier_id, job_id, "FAILED",
                    file_name=filename, report_month=req.report_date,
                )
                finish_conn.close()
            except Exception:
                pass
        finally:
            with _busy_lock:
                _busy.discard(job_key)

    threading.Thread(target=run_job, daemon=True).start()

    return {
        "status": "processing",
        "job_id": job_id,
        "carrier_id": carrier_id,
        "process_type": process_type,
        "filename": filename,
        "report_date": req.report_date,
    }


@app.get("/job-status/{job_id}")
def get_job_status(job_id: str):
    conn = get_postgres_connection()
    cur = conn.cursor()
    cur.execute(
        f"""SELECT job_id, job_status, process_type, carrier_id, file_name, report_month,
                   job_start_datetime, job_end_datetime
            FROM {PROCESS_HISTORY_TABLE}
            WHERE job_id = %s""",
        (job_id,),
    )
    row = cur.fetchone()
    cur.close()
    conn.close()
    if not row:
        raise HTTPException(404, "Job not found")
    return {
        "job_id": row[0],
        "status": row[1],
        "process_type": row[2],
        "carrier_id": row[3],
        "filename": row[4],
        "report_date": row[5],
        "job_start_datetime": str(row[6]) if row[6] else None,
        "job_end_datetime": str(row[7]) if row[7] else None,
    }
