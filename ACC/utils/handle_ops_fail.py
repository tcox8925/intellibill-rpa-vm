# ==========================================================
# utils/handle_ops_fail.py
# ==========================================================
"""
Centralized handler for operational errors.
Responsibilities:
    • Log operational failures to SQL
    • Send email alerts (to ALERT_EMAILS)
    • Deactivate carrier (active_flag=0, last_error=message) if systemic failure
"""

from datetime import datetime
from utils import config, logger_utils, email_utils, error_codes
from utils.db_utils import get_postgres_connection


# ==========================================================
# Helper: deactivate failing carrier
# ==========================================================
def deactivate_carrier(carrier_id, error_code, error_message):
    """Set active_flag = '0' and record last_error for a failing carrier (ops-level)."""
    try:
        conn = get_postgres_connection()
        cur = conn.cursor()
        cur.execute("""
            UPDATE wpo.ops_acc_process_matrix
               SET active_flag = '0',
                   last_error = %s
             WHERE carrier_id = %s
        """, (f"{error_code} – {error_message[:200]}", carrier_id))
        conn.commit()
        conn.close()
        print(f"🚫 Deactivated carrier_id={carrier_id} due to operational failure.")
    except Exception as e:
        print(f"⚠️ Failed to deactivate carrier {carrier_id}: {e}")


# ==========================================================
# Core failure handler
# ==========================================================

def handle_ops_fail(carrier_id, carrier_name, error_message, error_code="GEN_001", killswitch=False):
    """
    Safe failure handler — never breaks even if inputs are None.
    """

    try:
        # --------------------------------------------------
        # 1️⃣ Normalize carrier_name / carrier_id
        # --------------------------------------------------
        name_safe = (carrier_name or "UNKNOWN").strip()
        id_safe = (str(carrier_id) if carrier_id else "UNKNOWN").strip()

        script_name = f"ACC_RPA_{name_safe.upper().replace(' ', '_')}"
        full_msg = f"[{name_safe}] {error_message}"

        # --------------------------------------------------
        # 2️⃣ Error code mapping
        # --------------------------------------------------
        mapped_code = error_codes.ERROR_CODES.get(error_code, error_code)

        # --------------------------------------------------
        # 3️⃣ Log to SQL
        # --------------------------------------------------
        logger_utils.safe_log(script_name, f"{mapped_code} ({error_code}) → {error_message}")

        # --------------------------------------------------
        # 4️⃣ Send failure email
        # --------------------------------------------------
        if getattr(config, "ENABLE_EMAIL_ALERTS", False):
            subj = f"❌ [ACC] Failure in {name_safe}"
            body = f"""
            Carrier: {name_safe}
            Carrier ID: {id_safe}
            Error Code: {mapped_code} ({error_code})
            Error: {error_message}
            Timestamp (CST): {datetime.now().strftime('%Y-%m-%d %I:%M:%S %p')}
                        """.strip()

            recipients = getattr(config, "ALERT_EMAILS", ["dataops@834labs.com"])
            email_utils.send_email(
                to=", ".join(recipients),
                subject=subj,
                body=body
            )
            print(f"📨 Failure alert sent for {name_safe} → {recipients}")

        # --------------------------------------------------
        # 5️⃣ Auto deactivate carriers for severe errors
        # --------------------------------------------------
        ops_error_codes = {
            "GEN_001", "AZURE_UPLOAD_ERROR", "AZURE_DOWNLOAD_ERROR",
            "CRM_UPDATE_ERROR", "DB_INSERT_ERROR", "DB_004",
            "TEMPLATE_WRITE_ERROR", "EMAIL_SEND_ERROR"
        }

        if error_code in ops_error_codes and not killswitch:
            deactivate_carrier(id_safe, error_code, error_message)

        # --------------------------------------------------
        # 6️⃣ Killswitch
        # --------------------------------------------------
        if killswitch:
            print(f"⛔ Killswitch activated for {name_safe} — halting orchestrator.")
            raise SystemExit(1)

        # --------------------------------------------------
        # 7️⃣ Return structured fail summary
        # --------------------------------------------------
        return {
            "carrier": name_safe,
            "carrier_id": id_safe,
            "success": False,
            "error": error_message,
            "error_code": f"{mapped_code} ({error_code})"
        }

    except Exception as e:
        print(f"❌ handle_ops_fail() internal error: {e}")
        return {
            "carrier": carrier_name or "UNKNOWN",
            "carrier_id": carrier_id or "UNKNOWN",
            "success": False,
            "error": str(e),
            "error_code": "FAIL_HANDLER"
        }
