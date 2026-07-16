# ==========================================================
#  job_tracking.py
# ==========================================================
"""Per-file job tracking into ops_srv.ops_process_history.

One job row per Ready RPA file (or per carrier/file in scan-all mode).
Lifecycle: processing -> Success | Failure

In prod the rows are written to the DB (INSERT on start, UPDATE on finish).
In test mode they are appended to a local CSV instead.

job_id format:  {process_type}-{carrier_id}-{MMDDYYYYHHMMSS}
"""

import os
import csv
import uuid
import threading
from datetime import datetime

from config import FEATURES

PROCESS_HISTORY_TABLE = "ops_srv.ops_process_history"
RPA_SCRIPT_LOGS_TABLE = "wpo.ops_rpa_script_logs"
INBOUND_FILE_LOG_TABLE = "wpo.ops_inbound_file_log"

HISTORY_COLUMNS = [
    "job_id", "company_id", "process_type", "carrier_id", "product_id",
    "report_month", "com_month", "file_name", "job_status", "commission_status",
    "job_start_datetime", "job_update_datetime", "job_end_datetime",
    "product_name", "job_owner_name", "job_owner_email",
    "entity_affiliation", "job_type",
]

_CSV_LOCK = threading.Lock()

def clone_inbound_ready_row(conn, source_pk_id, process_type=None, test_mode=False):
    """
    Clone a Ready inbound row into a new 'processing' row.
    Returns the new pk_id.
    """
    if not source_pk_id or test_mode or FEATURES.get("test_mode"):
        return source_pk_id

    try:
        cur = conn.cursor()
        now = _now()

        cur.execute(
            f"""
            INSERT INTO {INBOUND_FILE_LOG_TABLE} (
                file_name,
                destination_schema,
                destination_table,
                process_type,
                process_date_start,
                process_date_end,
                load_status,
                txn_tot_cnt,
                txn_process_cnt,
                txn_error_cnt,
                file_report_month,
                file_com_month,
                product_name,
                carrier_id,
                company_id,
                sub_entity_id,
                validation_details,
                validation_status
            )
            SELECT
                file_name,
                destination_schema,
                destination_table,
                %s,
                %s,
                NULL,
                'Processing',
                NULL,
                NULL,
                NULL,
                file_report_month,
                file_com_month,
                product_name,
                carrier_id,
                company_id,
                sub_entity_id,
                validation_details,
                validation_status
            FROM {INBOUND_FILE_LOG_TABLE}
            WHERE pk_id = %s
              AND LOWER(TRIM(load_status)) = 'ready'
            RETURNING pk_id
            """,
            (process_type, now, source_pk_id),
        )

        row = cur.fetchone()
        conn.commit()
        cur.close()

        if not row:
            raise Exception(f"No Ready inbound row found for pk_id={source_pk_id}")

        return row[0]

    except Exception as e:
        try:
            conn.rollback()
        except Exception:
            pass
        print(f"    ⚠️  inbound row clone failed: {e}")
        return None


def archive_inbound_ready_row(conn, source_pk_id, test_mode=False, status_message=None):
    """
    Mark the original Ready row as Archive after processing finishes.
    """
    if not source_pk_id or test_mode or FEATURES.get("test_mode"):
        return

    try:
        cur = conn.cursor()
        cur.execute(
            f"""
            UPDATE {INBOUND_FILE_LOG_TABLE}
               SET load_status = 'Archive',
                   status_message = COALESCE(%s, status_message)
             WHERE pk_id = %s
            """,
            (status_message, source_pk_id),
        )
        conn.commit()
        cur.close()
    except Exception as e:
        try:
            conn.rollback()
        except Exception:
            pass
        print(f"    ⚠️  inbound source archive failed: {e}")

def generate_job_id(process_type, carrier_id):
    """{process_type}-{carrier_id}-{MMDDYYYYHHMMSS%f}-{rand} (process timestamp).

    The microsecond component plus a short random suffix guarantees uniqueness
    even when (a) two carriers legitimately share a carrier_id — e.g. Humana /
    SMA-Humana, Devoted / SMA-Devoted — or (b) many carriers start within the
    same second in the parallel submit loop. Without it their job_ids collided
    and job_finish's UPDATE-by-job_id updated the wrong row (or hit a duplicate),
    leaving carriers stuck in 'processing'.
    """
    ts = datetime.now().strftime("%m%d%Y%H%M%S%f")
    return f"{str(process_type).strip().lower()}-{carrier_id}-{ts}-{uuid.uuid4().hex[:6]}"


def _now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _blank_row(**kw):
    row = {c: "" for c in HISTORY_COLUMNS}
    for k, v in kw.items():
        if k in HISTORY_COLUMNS:
            row[k] = v
    return row


def _final_status(status):
    s = str(status).strip().upper()
    if s == "SUCCESS":
        return "Success"
    if s in ("FAILED", "FAILURE", "FAIL"):
        return "Failure"
    return status


def fetch_ready_inbound_jobs(conn, process_type):
    """Return Ready rows from wpo.ops_inbound_file_log for ACU or BOB."""
    pt = f"%{str(process_type).strip().upper()}%"

    cur = conn.cursor()
    cur.execute(
        f"""
        SELECT pk_id, file_name, load_status, process_type, file_report_month,
               file_com_month, company_id, carrier_id, product_name
        FROM {INBOUND_FILE_LOG_TABLE}
        WHERE process_type ILIKE %s
          AND LOWER(TRIM(load_status)) = 'ready'
          AND pk_id = %s
        ORDER BY pk_id DESC
        """,
        (pt, '64068f31-a6c6-4cca-9d8b-25a7ac6ba199'),
    )
    cols = [d[0] for d in cur.description]
    rows = [dict(zip(cols, r)) for r in cur.fetchall()]
    cur.close()
    return rows


def fetch_ready_rpa_jobs(conn, process_type):
    """Legacy: Ready rows from ops_rpa_script_logs (unused by --ready)."""
    pt = str(process_type).strip().upper()
    cur = conn.cursor()
    cur.execute(
        f"""SELECT pk_id, file_path, file_status, process_type, file_report_month,
                   file_com_month, company_id, carrier_id, product_name
            FROM {RPA_SCRIPT_LOGS_TABLE}
            WHERE process_type = %s AND LOWER(TRIM(file_status)) = 'ready'
            ORDER BY start_datetime DESC NULLS LAST""",
        (pt,),
    )
    cols = [d[0] for d in cur.description]
    rows = [dict(zip(cols, r)) for r in cur.fetchall()]
    cur.close()
    return rows


def aggregate_inbound_txn_counts(metrics_or_result):
    """Sum txn counts from ACU pipeline result dict or BOB results list."""
    if isinstance(metrics_or_result, dict):
        metrics = metrics_or_result.get("metrics") or []
    else:
        metrics = metrics_or_result or []
    if not metrics:
        return None, None, None
    tot = sum((m.get("results_count") or 0) + (m.get("exceptions_count") or 0) for m in metrics)
    proc = sum(m.get("results_count") or 0 for m in metrics)
    err = sum(m.get("exceptions_count") or 0 for m in metrics)
    return tot, proc, err


def mark_inbound_load_status(conn, pk_id, load_status, test_mode=False, process_date_end=None,
                             txn_tot_cnt=None, txn_process_cnt=None, txn_error_cnt=None,
                             status_message=None):
    """Update ops_inbound_file_log after --ready processing."""
    if not pk_id or test_mode or FEATURES.get("test_mode"):
        return
    try:
        cur = conn.cursor()
        cur.execute(
            f"""
            UPDATE {INBOUND_FILE_LOG_TABLE}
            SET load_status = %s,
                process_date_end = COALESCE(%s, process_date_end),
                txn_tot_cnt = COALESCE(%s::text, txn_tot_cnt),
                txn_process_cnt = COALESCE(%s::text, txn_process_cnt),
                txn_error_cnt = COALESCE(%s::text, txn_error_cnt),
                status_message = COALESCE(%s, status_message)
            WHERE pk_id = %s
            """,
            (
                load_status,
                process_date_end,
                txn_tot_cnt,
                txn_process_cnt,
                txn_error_cnt,
                status_message,
                pk_id,
            ),
        )
        conn.commit()
        cur.close()
    except Exception as e:
        try:
            conn.rollback()
        except Exception:
            pass
        print(f"    ⚠️  Inbound log status update failed: {e}")


def mark_rpa_file_status(conn, pk_id, file_status, test_mode=False):
    """Update ops_rpa_script_logs.file_status after --ready processing."""
    if not pk_id or test_mode or FEATURES.get("test_mode"):
        return
    try:
        cur = conn.cursor()
        cur.execute(
            f"UPDATE {RPA_SCRIPT_LOGS_TABLE} SET file_status = %s WHERE pk_id = %s",
            (file_status, pk_id),
        )
        conn.commit()
        cur.close()
    except Exception as e:
        try:
            conn.rollback()
        except Exception:
            pass
        print(f"    ⚠️  RPA log status update failed: {e}")

def start_inbound_job(conn, inbound_row, process_type, test_mode=False, local_csv_path=None, job_type=None):
    """
    Creates:
      1) job row in ops_process_history
      2) cloned 'processing' row in ops_inbound_file_log
    Returns a dict with both ids.
    """
    source_pk_id = inbound_row.get("pk_id")
    carrier_id = inbound_row.get("carrier_id")
    company_id = inbound_row.get("company_id")
    product_name = inbound_row.get("product_name")
    file_name = inbound_row.get("file_name")
    report_month = inbound_row.get("file_report_month")
    com_month = inbound_row.get("file_com_month")

    job_id = job_start(
        conn=conn,
        process_type=process_type,
        carrier_id=carrier_id,
        file_name=file_name,
        report_month=report_month,
        job_type=job_type,
        test_mode=test_mode,
        local_csv_path=local_csv_path,
        company_id=company_id,
        product_name=product_name,
        com_month=com_month,
    )

    processing_pk_id = clone_inbound_ready_row(
        conn=conn,
        source_pk_id=source_pk_id,
        process_type=process_type,
        test_mode=test_mode,
    )

    return {
        "job_id": job_id,
        "source_inbound_pk_id": source_pk_id,
        "processing_inbound_pk_id": processing_pk_id,
    }

def job_start(conn, process_type, carrier_id, file_name, report_month="",
              job_type=None, test_mode=False, local_csv_path=None,
              company_id=None, product_name=None, com_month=None):
    """Open a job (status processing). Returns the generated job_id."""
    if not FEATURES.get("job_tracking", True):
        return None
    pt = str(process_type).strip().upper()
    job_id = generate_job_id(pt, carrier_id)
    now = _now()
    row = _blank_row(
        job_id=job_id, process_type=pt, carrier_id=str(carrier_id),
        file_name=file_name, report_month=report_month, job_status="processing",
        job_type=(job_type or pt), job_start_datetime=now, job_update_datetime=now,
        company_id=company_id, product_name=product_name, com_month=com_month,
    )
    if test_mode:
        _append_csv(local_csv_path, row)
    else:
        _db_insert(conn, row)
    return job_id


def job_finish(conn, process_type, carrier_id, job_id, status, file_name="",
               report_month="", note=None, test_mode=False, local_csv_path=None,
               rpa_pk_id=None, inbound_pk_id=None, inbound_metrics=None,
               inbound_source_pk_id=None, inbound_processing_pk_id=None):
    """Close a job with Success or Failure."""
    if not FEATURES.get("job_tracking", True):
        return

    pt = str(process_type).strip().upper()
    final_status = _final_status(status)
    now = _now()

    if test_mode:
        row = _blank_row(
            job_id=job_id,
            process_type=pt,
            carrier_id=str(carrier_id),
            file_name=file_name,
            report_month=report_month,
            job_status=final_status,
            job_type=pt,
            job_update_datetime=now,
            job_end_datetime=now,
        )
        _append_csv(local_csv_path, row)
    else:
        _db_update(conn, job_id, final_status, now)

    target_inbound_pk_id = inbound_processing_pk_id or inbound_pk_id

    if target_inbound_pk_id:
        load_status = "succeeded" if final_status == "Success" else "failed"
        tot, proc, err = aggregate_inbound_txn_counts(inbound_metrics)

        mark_inbound_load_status(
            conn,
            target_inbound_pk_id,
            load_status,
            test_mode=test_mode,
            process_date_end=now,
            txn_tot_cnt=tot,
            txn_process_cnt=proc,
            txn_error_cnt=err,
            status_message=note,
        )

        if inbound_source_pk_id:
            archive_inbound_ready_row(
                conn,
                inbound_source_pk_id,
                test_mode=test_mode,
                status_message=f"Arcived Successfully",
            )

    elif rpa_pk_id:
        if final_status == "Success":
            mark_rpa_file_status(conn, rpa_pk_id, "Archive", test_mode=test_mode)
        elif final_status == "Failure":
            mark_rpa_file_status(conn, rpa_pk_id, "Failed", test_mode=test_mode)


# ── persistence ──

def _append_csv(path, row):
    if not path:
        return
    with _CSV_LOCK:
        try:
            is_new = not os.path.exists(path)
            with open(path, "a", newline="") as f:
                w = csv.DictWriter(f, fieldnames=HISTORY_COLUMNS)
                if is_new:
                    w.writeheader()
                w.writerow(row)
        except Exception as e:
            print(f"    ⚠️  job-tracking CSV append failed: {e}")


def _db_insert(conn, row):
    cols = HISTORY_COLUMNS
    placeholders = ", ".join(["%s"] * len(cols))
    vals = [(row.get(c) if (row.get(c) not in ("", None)) else None) for c in cols]
    sql = f"INSERT INTO {PROCESS_HISTORY_TABLE} ({', '.join(cols)}) VALUES ({placeholders})"
    last_err = None
    # Two attempts: if the connection was left in an aborted-transaction state by
    # prior work on it, the first execute fails, we roll back to clear it, and the
    # retry succeeds. Otherwise attempt 1 succeeds and we return immediately.
    for _ in range(2):
        try:
            cur = conn.cursor()
            cur.execute(sql, vals)
            conn.commit()
            cur.close()
            return
        except Exception as e:
            last_err = e
            try:
                conn.rollback()
            except Exception:
                pass
    print(f"    ⚠️  job-tracking insert failed: {last_err}")


def _db_update(conn, job_id, status, now):
    sql = (f"UPDATE {PROCESS_HISTORY_TABLE} "
           f"SET job_status=%s, job_update_datetime=%s, job_end_datetime=%s "
           f"WHERE job_id=%s")
    last_err = None
    for _ in range(2):
        try:
            cur = conn.cursor()
            cur.execute(sql, (status, now, now, job_id))
            conn.commit()
            cur.close()
            return
        except Exception as e:
            last_err = e
            try:
                conn.rollback()
            except Exception:
                pass
    print(f"    ⚠️  job-tracking update failed: {last_err}")
