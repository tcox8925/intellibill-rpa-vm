import traceback
from datetime import datetime
import pytz
from bob_training import run_training
from bob_predictions import run_forecast
from utils.db_utils import get_postgres_connection


# ==========================================================
# CONFIG
# ==========================================================

SUB_ENTITY_ID = "270681372001"


# ==========================================================
# LOGGING
# ==========================================================

def log_to_db(pg_conn, script_name, start_dt, end_dt, success, error_msg=None, message=None):
    """Write a row to wpo.ops_rpa_script_logs."""

    cursor = pg_conn.cursor()
    cursor.execute("""
        INSERT INTO wpo.ops_rpa_script_logs
        (script_name, start_datetime, end_datetime,
         success, error, message,
         sub_entity_id, process_type, created_on)
        VALUES (%s, %s, %s,
                %s, %s, %s,
                %s, %s, %s)
    """,
    (
        script_name,
        start_dt,
        end_dt,
        success,
        error_msg,
        message,
        SUB_ENTITY_ID,
        "BOB_TIME_SERIES",
        datetime.utcnow(),
    ))
    pg_conn.commit()
    cursor.close()


def run_step(pg_conn, script_name, func):
    """Run a function and log success/failure."""

    start_dt = datetime.utcnow()
    print(f"▶ Starting {script_name} …")

    try:
        func()
        end_dt = datetime.utcnow()
        elapsed = (end_dt - start_dt).total_seconds()
        msg = f"Completed in {elapsed:.1f}s"
        log_to_db(pg_conn, script_name, start_dt, end_dt, success=True, message=msg)
        print(f"  ✅ {msg}\n")
        return True

    except Exception as e:
        end_dt = datetime.utcnow()
        error_msg = traceback.format_exc()
        log_to_db(pg_conn, script_name, start_dt, end_dt, success=False,
                  error_msg=error_msg, message=str(e))
        print(f"  ❌ {script_name} failed: {e}\n")
        return False


# ==========================================================
# MAIN
# ==========================================================

def main():

    ct = pytz.timezone("US/Central")
    today = datetime.now(ct)

    print(f"{'='*60}")
    print(f"BOB Daily Run — {today:%Y-%m-%d %I:%M %p CT}")
    print(f"{'='*60}\n")

    pg_conn = get_postgres_connection()

    # --- 1st of the month: retrain ---
    if today.day == 1:
        print("📅 1st of the month — retraining params …\n")
        success = run_step(pg_conn, "bob_training", run_training)
        if not success:
            print("⚠️  Training failed — running predictions with previous params.\n")

    # --- every day: forecast ---
    print("📈 Running daily forecast …\n")
    run_step(pg_conn, "bob_predictions", run_forecast)

    pg_conn.close()

    print(f"\n{'='*60}")
    print("Done.")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()