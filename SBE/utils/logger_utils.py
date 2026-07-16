# ==========================================================
# utils/logger_utils.py  (SBE — SINGLE ROW PER STATE)
# ==========================================================
"""
Centralized logging for all SBE / RPA processes.

Writes to:
    wpo.ops_rpa_script_logs

New pattern (recommended for SBE):
    ctx = log_start(script_name, run_id, company_id=...)
    ...
    log_end(ctx, success="Process Completed Successfully")
    or
    log_end(ctx, phase="SCRAPE", error_message="timeout on page 12")

Legacy helpers (still available):
    log_success(...)
    log_error(...)
"""

import datetime
from utils import db_utils


# ----------------------------------------------------------
# INTERNAL WRITER
# ----------------------------------------------------------
def _write_log(
    script_name: str,
    start_datetime: datetime.datetime,
    end_datetime: datetime.datetime,
    success: str = None,
    error: str = None,
    file_status: str = None,
    run_id: str = None,
    process_type: str = "SBE",
    company_id: int = None,
    carrier_id: str = None,
    product_name: str = None,
    flow_id: str = None,
    sub_entity_id: str = None
):
    """
    Inserts ONE row into wpo.ops_rpa_script_logs.
    """

    # Safe truncation to column limits
    script_name  = (script_name or "")[:200]
    success      = (success or "")[:2000]
    error        = (error or "")[:4000]
    file_status  = (file_status or "")[:20]
    process_type = (process_type or "")[:3]   # 'SBE'
    flow_id      = (flow_id or "")[:100]
    run_id       = (run_id or "")[:50]
    product_name = (product_name or "")[:20]
    carrier_id   = (carrier_id or "")[:20]
    # company_id is INT, no truncation

    conn = db_utils.get_postgres_connection()
    cur = conn.cursor()

    sql = """
        INSERT INTO wpo.ops_rpa_script_logs (
            script_name,
            start_datetime,
            end_datetime,
            error,
            success,
            file_status,
            process_type,
            company_id,
            carrier_id,
            product_name,
            flow_id,
            run_id,
            sub_entity_id
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
    """

    cur.execute(
        sql,
        (
            script_name,
            start_datetime,
            end_datetime,
            error,
            success,
            file_status,
            process_type,
            company_id,
            carrier_id,
            product_name,
            flow_id,
            run_id,
        ),
    )

    conn.commit()
    conn.close()


# ----------------------------------------------------------
# NEW API — SINGLE ROW PER STATE
# ----------------------------------------------------------
def log_start(
    script_name: str,
    run_id: str,
    company_id: int = None,
    carrier_id: str = None,
    product_name: str = None,
    flow_id: str = None,
    sub_entity_id: str = None
):
    """
    Start a log context (does NOT write to DB).
    Returns a dict you pass back into log_end().
    """
    return {
        "script_name": script_name,
        "start_datetime": datetime.datetime.utcnow(),
        "run_id": run_id,
        "company_id": company_id,
        "carrier_id": carrier_id,
        "product_name": product_name,
        "flow_id": flow_id,
        "sub_entity_id": sub_entity_id
    }


def log_end(
    log_ctx: dict,
    success: str = None,
    phase: str = None,
    error_message: str = None,
    file_status: str = None,
):
    """
    Finalize and write ONE row for the state.

    - If error_message is provided → error = "{PHASE}_{error_message}"
    - Else → success = "Process Completed Successfully" (if success not provided)
    - file_status defaults:
        - "Failed" if error_message is present
        - "Success" otherwise
    """

    script_name    = log_ctx["script_name"]
    start_datetime = log_ctx["start_datetime"]
    run_id         = log_ctx.get("run_id")
    company_id     = log_ctx.get("company_id")
    carrier_id     = log_ctx.get("carrier_id")
    product_name   = log_ctx.get("product_name")
    flow_id        = log_ctx.get("flow_id")
    sub_entity_id  = log_ctx.get("sub_entity_id")

    end_datetime = datetime.datetime.utcnow()

    error = None
    if error_message:
        phase_prefix = (phase or "GEN").upper()
        error = f"{phase_prefix}_{error_message}"

    if not file_status:
        file_status = "Failed" if error else "Success"

    if not success and not error:
        success = "Process Completed Successfully"

    _write_log(
        script_name=script_name,
        start_datetime=start_datetime,
        end_datetime=end_datetime,
        success=success,
        error=error,
        file_status=file_status,
        run_id=run_id,
        process_type="SBE",
        company_id=company_id,
        carrier_id=carrier_id,
        product_name=product_name,
        flow_id=flow_id,
        sub_entity_id=sub_entity_id
    )


# ----------------------------------------------------------
# LEGACY HELPERS (STILL USED IN OTHER MODULES)
# ----------------------------------------------------------
def log_success(script_name: str, message: str = "Process Completed Successfully", **kwargs):
    """
    Backwards-compatible helper.
    Writes a one-off success row (start=end=now).
    Avoid using this for SBE state-level logging.
    """
    now = datetime.datetime.utcnow()

    _write_log(
        script_name=script_name,
        start_datetime=now,
        end_datetime=now,
        success=message,
        error=None,
        file_status="Success",
        run_id=kwargs.get("run_id"),
        process_type="SBE",
        company_id=kwargs.get("company_id"),
        carrier_id=kwargs.get("carrier_id"),
        product_name=kwargs.get("product_name"),
        flow_id=kwargs.get("flow_id"),
        sub_entity_id=kwargs.get("sub_entity_id")
    )


def log_error(script_name: str, error_code: str, message: str, **kwargs):
    """
    Backwards-compatible helper.
    Writes a one-off failure row (start=end=now).
    error stored as "{error_code} | message"
    """
    now = datetime.datetime.utcnow()
    full_error = f"{error_code} | {message}"

    _write_log(
        script_name=script_name,
        start_datetime=now,
        end_datetime=now,
        success=None,
        error=full_error,
        file_status="Failed",
        run_id=kwargs.get("run_id"),
        process_type="SBE",
        company_id=kwargs.get("company_id"),
        carrier_id=kwargs.get("carrier_id"),
        product_name=kwargs.get("product_name"),
        flow_id=kwargs.get("flow_id"),
        sub_entity_id=kwargs.get("sub_entity_id")
    )
