# ==========================================================
# utils/logger_utils.py
# ==========================================================
import datetime, pytz, traceback
from utils import config, db_utils
import uuid

TEST_MODE = getattr(config, "TEST_MODE", False)
start_times = {}
CST = pytz.timezone("America/Chicago")
import json

# ==========================================================
# Helpers
# ==========================================================
def _tbl():
    return "wpo.ops_rpa_script_logs_test" if TEST_MODE else "wpo.ops_rpa_script_logs"

def _now():
    utc = datetime.datetime.now(datetime.timezone.utc)
    return utc.astimezone(CST).strftime("%Y-%m-%d %H:%M:%S")

def _trunc(v, n):
    if v is None:
        return None
    s = str(v)
    return s if len(s) <= n else s[:n - 8] + " [...]"

# ==========================================================
# Setup & Logging
# ==========================================================
def setup_logger(script_name: str):
    """Initialize logger cleanly (prevents double ACC_RPA_ prefixes)."""
    clean_name = script_name.replace("ACC_RPA_ACC_RPA_", "ACC_RPA_")
    ts = _now()
    start_times[clean_name] = ts
    start_times[script_name] = ts
    print(f"🟢 [{clean_name}] Logging started at {ts} CST")

def init_log_entry(
    script_name: str,
    run_id: str = None,
    carrier_id: str = None,
    company_id: str = None,
    sub_entity_id: str = None
):
    """Create a new log row when a handler or cycle starts."""
    conn = None
    clean_name = script_name.replace("ACC_RPA_ACC_RPA_", "ACC_RPA_")
    ts = _now()

    try:
        conn = db_utils.get_postgres_connection()
        cur = conn.cursor()

        cur.execute(f"""
            INSERT INTO {_tbl()} (
                script_name,
                start_datetime,
                end_datetime,
                error,
                success,
                process_type,
                run_id,
                carrier_id,
                company_id,
                sub_entity_id
            )
            VALUES (%s, %s, NULL, NULL, NULL, %s, %s, %s, %s, %s)
        """, (
            _trunc(clean_name, 50),
            ts,
            "ACC",                      # process_type
            _trunc(run_id, 50),
            carrier_id,
            company_id,
            sub_entity_id
        ))

        conn.commit()
        print(f"🪵 Log entry created for run_id={run_id} carrier_id={carrier_id}")

    except Exception as e:
        print(f"❌ Failed to insert log entry: {e}")

    finally:
        if conn:
            conn.close()


def log_phase_start(phase: str, carrier_name: str, run_id: str):
    print(f"▶️ [{carrier_name}] [{phase}] started @ {_now()} | run_id={run_id}")

def log_phase_success(phase: str, carrier_name: str, run_id: str):
    print(f"✅ [{carrier_name}] [{phase}] completed successfully @ {_now()} | run_id={run_id}")

# ==========================================================
# utils/logger_utils.py  (Fix for SQL literal error)
# ==========================================================

CST = pytz.timezone("America/Chicago")

def _now_cst():
    utc = datetime.datetime.now(datetime.timezone.utc)
    return utc.astimezone(CST).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]

def safe_log(script_name, message, code="GEN"):
    """
    Fully parameterized, safe logging to ops_rpa_script_logs or *_test depending on TEST_MODE.
    Never crashes the main process.
    """
    import json
    from utils import config
    from utils.db_utils import get_postgres_connection

    # Select correct table based on TEST_MODE
    table_name = "wpo.ops_rpa_script_logs_test" if getattr(config, "TEST_MODE", False) else "wpo.ops_rpa_script_logs"

    try:
        conn = get_postgres_connection()
        cur = conn.cursor()

        # normalize values
        script_name = str(script_name or "")
        code = str(code or "")

        if isinstance(message, (dict, list)):
            message = json.dumps(message, default=str)
        else:
            message = str(message or "")

        # Parameterized insert (Postgres-friendly)
        cur.execute(
            f"""
            INSERT INTO {table_name}
                (script_name, message, code, created_on)
            VALUES (%s, %s, %s, now() at time zone 'utc')
            """,
            (script_name, message, code),
        )

        conn.commit()
        conn.close()

    except Exception as e:
        print(f"[safe_log] ⚠️ Logging failed: {e}")
        print(f"[safe_log] ❌ Failed to log message: {message}")




def log_final_entry(script_name: str, success: str = None, run_id: str = None, error: str = None):
    """Finalize run with CST timestamps."""
    clean_name = script_name.replace("ACC_RPA_ACC_RPA_", "ACC_RPA_")
    ts = _now()
    try:
        conn = db_utils.get_postgres_connection()
        cur = conn.cursor()
        cur.execute(f"""
            UPDATE {_tbl()}
               SET end_datetime = %s,
                   success = %s,
                   error = %s
             WHERE script_name = %s AND run_id = %s
        """, (
            ts,
            1 if success else 0,
            _trunc(error, 255),
            _trunc(clean_name, 50),
            _trunc(run_id, 50),
        ))
        conn.commit()
        conn.close()
        print(f"🧾 Log finalized for {clean_name} → {'✅ Success' if success else '❌ Failed'}")
    except Exception as e:
        print(f"❌ log_final_entry failed for {clean_name}: {e}")
