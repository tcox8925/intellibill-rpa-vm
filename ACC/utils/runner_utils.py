# ==========================================================
# utils/runner_utils.py (lean version post-rules_engine)
# ==========================================================
from datetime import datetime
import pytz
from utils import db_utils, config

CST = pytz.timezone(config.TIMEZONE)

def now_cst() -> datetime:
    """Return current time in CST."""
    return datetime.now(CST)

def set_last_eod_sent(carrier_id: str):
    """Mark EOD email as sent for carrier."""
    try:
        conn = db_utils.get_postgres_connection()
        cur = conn.cursor()
        cur.execute("""
            UPDATE wpo.ops_acc_process_matrix
               SET last_eod_sent = now() at time zone 'utc'
             WHERE carrier_id = %s
        """, (carrier_id,))
        conn.commit()
        conn.close()
        print(f"📝 Marked last_eod_sent for carrier {carrier_id}")
    except Exception as e:
        print(f"⚠️ Failed to set last_eod_sent: {e}")

def reset_daily_run_state():
    """Reset run & EOD states at end of day."""
    try:
        conn = db_utils.get_postgres_connection()
        cur = conn.cursor()
        cur.execute("""
            UPDATE wpo.ops_acc_process_matrix
               SET last_run_time = NULL,
                   last_eod_sent = NULL,
                   current_template_path = NULL
             WHERE active_flag = '1'
        """)
        conn.commit()
        conn.close()
        print("🔄 Daily run state reset for all active carriers.")
    except Exception as e:
        print(f"⚠️ Failed to reset daily run state: {e}")

def reset_eod_state(carrier_id: str):
    """
    Marks last_eod_sent = NULL (or resets flag) in matrix after batch email.
    """
    from utils.db_utils import get_postgres_connection
    try:
        conn = get_postgres_connection()
        cur = conn.cursor()
        cur.execute("""
            UPDATE wpo.ops_acc_process_matrix
               SET last_eod_sent = NULL
             WHERE carrier_id = %s
        """, (carrier_id,))
        conn.commit()
        conn.close()
        print(f"🔄 Reset EOD state for carrier_id={carrier_id}")
    except Exception as e:
        print(f"⚠️ Failed to reset EOD state: {e}")

# ==========================================================
# utils/runner_utils.py
# ==========================================================
def summarize_run(run_id: str, run_results: list, email_to):
    """
    Build and send the ACC RPA roll-up summary for this orchestrator cycle.
    Dynamically computes:
      - success_count (based on crm_success_status)
      - needs_attention (based on crm_fail_status list)
      - fail_count (everything else)
    """
    from utils.runner_utils import now_cst
    from utils.email_utils import send_email

    if not run_results:
        body = f"Summary for Run ID: {run_id}\n\n(No carriers processed)"
        send_email(to=email_to, subject=f"ACC RPA Summary — Run ID {run_id}", body=body)
        print(f"📧 Summary email sent → {email_to}")
        return

    lines = [
        f"Summary for Run ID: {run_id}",
        f"Run Time (CST): {now_cst():%Y-%m-%d %I:%M %p}",
        ""
    ]

    all_total = 0

    for res in run_results:
        if not res:
            continue

        carrier = res.get("carrier") or res.get("carrier_name") or "Unknown Carrier"

        # --------------------------------------------
        # df_all REQUIRED for correct counting
        # --------------------------------------------
        df_all = res.get("df_all")
        if df_all is None:
            # fallback to df_queue or handler summary
            df_all = res.get("df_queue")

        if df_all is None:
            total = succ = needs = fail = 0
        else:
            df = df_all.copy()

            crm_success = (res.get("crm_success_status") or "Sent to Agent").lower()

            # crm_fail_status may be str "Needs Attention, Pending - Need W-9"
            fail_raw = res.get("crm_fail_status") or ["Needs Attention"]

            if isinstance(fail_raw, str):
                crm_fail_list = [f.strip().lower()
                                 for f in fail_raw.split(",")
                                 if f.strip()]
            else:
                crm_fail_list = [str(f).strip().lower()
                                 for f in fail_raw
                                 if str(f).strip()]

            total = len(df)

            # bucket 1: success
            succ = len(df[
                (df["status"].astype(str).str.lower() == "success") &
                (df["contract_status"].astype(str).str.lower() == crm_success)
            ])

            # bucket 2: needs attention (fail_status labels)
            needs = len(df[
                df["contract_status"].astype(str).str.lower().isin(crm_fail_list)
            ])

            # bucket 3: errors (everything else)
            fail = total - succ - needs

        label = res.get("crm_success_status") or "Sent to Agent"

        lines.append(f"Carrier: {carrier}")
        lines.append(f"  Total Contracts: {total}")
        lines.append(f"  {label}: {succ}")
        lines.append(f"  Needs Attention: {needs}")
        lines.append(f"  Errors: {fail}")
        lines.append("")
        all_total += total

    if all_total > 0:
        body = "\n".join(lines)
        subject = f"ACC RPA Summary — Run ID {run_id}"
        send_email(to=email_to, subject=subject, body=body)
    else:
        print(f"ℹ️ No carriers processed in this run '{run_id}', aborting summary email process.")


    print(f"📧 Summary email sent → {email_to}")



