# ==========================================================
# runner_acc.py — Refactored to match new ACC RPA structure
# ==========================================================
"""
ACC RPA Orchestrator (Refactored)
--------------------------------
- Loads active carriers from matrix
- Evaluates run eligibility via rules_engine
- Executes carrier handler workflows
- Runs post-processing (template/email/EOD)
- Uploads final templates to Blob
- Cleans up local download paths
- Graceful daily shutdown
"""

import uuid
import time
import os
import pandas as pd
from datetime import datetime as dt
from utils import (
    config,
    db_utils,
    logger_utils,
    rules_engine,
    runner_tasks,
    runner_utils,
    file_utils,
)
from utils.handle_ops_fail import handle_ops_fail
from utils.carrier_handler import run_caresource, run_priority_health,run_bcbsne,run_solis
print("Done loading libraries")
# ==========================================================
# GLOBALS
# ==========================================================
RUN_ID = str(uuid.uuid4())

# ==========================================================
# 1️⃣ Load Active Carriers
# ==========================================================
def load_active_carriers() -> pd.DataFrame:
    """Fetch active carriers from process matrix."""
    try:
        conn = db_utils.get_postgres_connection()
        df = pd.read_sql("""
            SELECT
                carrier_id,
                carrier_name,
                company_id,
                process_type,
                origin,
                mode,
                email_cadence,
                frequency,
                mode_details,
                link_agent,
                portal_url,
                carrier_template,
                base_gdrive_url,
                download_path,
                email_to,
                active_flag,
                last_run_time,
                last_error,
                crm_filter,
                folder_pattern,
                crm_module,
                crm_update_mode,
                crm_success_status,
                crm_fail_status,
                crm_notify_email,
                pa_trigger_url,
                requires_template_update,
                template_field_map,
                base_blob_url,
                notes,
                eod_time,
                in_development,
                CAST(last_eod_sent AS timestamp) AS last_eod_sent,
                current_template_path,
                email_cc,
                sub_entity_id
            FROM wpo.ops_acc_process_matrix
            WHERE active_flag = '1'
        """, conn)

        conn.close()

        if df.empty:
            print("⚠️ No active carriers found.")
        else:
            print(f"📋 Loaded {len(df)} active carrier(s).")
        return df
    except Exception as e:
        handle_ops_fail(None, "ACC_RPA_MASTER", f"Failed to load process matrix: {e}", "DB_001")
        return pd.DataFrame()

# ==========================================================
# 2️⃣ Update Matrix Timestamps
# ==========================================================
def mark_last_run(carrier_id: str):
    """Mark carrier’s last run in matrix."""
    try:
        conn = db_utils.get_postgres_connection()
        cur = conn.cursor()
        cur.execute("""
            UPDATE wpo.ops_acc_process_matrix
               SET last_run_time = now() at time zone 'utc'
             WHERE carrier_id = %s
        """, (carrier_id,))
        conn.commit()
        conn.close()
        print(f"🕒 last_run_time updated → {carrier_id}")
    except Exception as e:
        print(f"⚠️ Failed to update last_run_time for {carrier_id}: {e}")

# ==========================================================
# 2B  Update EOD Timestamp
# ==========================================================
def mark_eod_sent(carrier_id: str):
    try:
        conn = db_utils.get_postgres_connection()
        cur = conn.cursor()
        cur.execute("""
            UPDATE wpo.ops_acc_process_matrix
            SET last_eod_sent = 
                (now() at time zone 'cst')
            WHERE carrier_id = %s
        """, (carrier_id,))
        conn.commit()
        conn.close()
        print(f"📨 last_eod_sent updated (CST) → {carrier_id}")
    except Exception as e:
        print(f"⚠️ Failed to update last_eod_sent for {carrier_id}: {e}")



# ==========================================================
# 3️⃣ Carrier Dispatcher
# ==========================================================
def dispatch_carrier(carrier_row: pd.Series) -> dict:
    """Route execution to correct carrier handler."""
    name = carrier_row.get("carrier_name", "").lower()
    try:
        if "caresource" in name:
            return run_caresource(carrier_row)
        elif "priority" in name:
            return run_priority_health(carrier_row)
        elif "bcbsne" in name:
            return run_bcbsne(carrier_row)
        elif "solis" in name:
            return run_solis(carrier_row)
        else:
            print(f"⚠️ No handler mapped for {name}, skipping.")
            return {"carrier": name, "success": False, "error": "No handler mapped"}
    except Exception as e:
        return handle_ops_fail(carrier_row.get("carrier_id"), name, f"Handler crash: {e}", "GEN_001")

# ==========================================================
# 4️⃣ Main Cycle
# ==========================================================
def run_cycle():
    """Runs one orchestrator cycle across eligible carriers, with full logging per carrier."""
    summaries = []
    matrix = load_active_carriers()

    if matrix.empty:
        print("ℹ️ No active carriers found in process matrix.")
        return summaries

    # 🧪 TEST MODE filter
    if config.TEST_MODE and not config.TEST_MODE_RUN_ALL:
        filt = str(config.TEST_CARRIER_NAME).strip().lower()
        matrix = matrix[matrix["carrier_name"].str.lower().str.contains(filt, na=False)]
        print(f"🧪 TEST_MODE → restricting to carrier(s): {matrix['carrier_name'].tolist()}")

    # ======================================================
    # 🔁 Loop through carriers sequentially
    # ======================================================
    for _, carrier_row in matrix.iterrows():

        carrier_id   = carrier_row["carrier_id"]
        carrier_name = carrier_row["carrier_name"]
        company_id   = carrier_row.get("company_id")
        sub_entity_id = carrier_row.get("sub_entity_id")
        script       = f"ACC_RPA_{carrier_name.upper()}"
        run_id       = f"ACC_{carrier_id}"

        if carrier_id == '2931751000147793570' or carrier_id == '2931751000020024159':
            print(f"⏸️ Skipping {carrier_name} → Mirror for ACR entry, not runnable")
            continue

        decision = rules_engine.evaluate_run(carrier_row)

        if not decision.run_now:
            print(f"⏸️ Skipping {carrier_name} → {decision.reason}")
            continue

        print(f"\n▶️ Running {carrier_name} ({decision.mode_key}) — reason: {decision.reason}")

        # ======================================================
        # 1️⃣ START LOG ENTRY
        # ======================================================
        try:
            logger_utils.init_log_entry(
                script_name=script,
                run_id=run_id,
                carrier_id=carrier_id,
                company_id=company_id,
                sub_entity_id=sub_entity_id
            )
        except Exception as e:
            print(f"⚠️ Failed to init log entry: {e}")

        try:
            # ======================================================
            # 2️⃣ Run handler
            # ======================================================
            summary = dispatch_carrier(carrier_row)

            # ======================================================
            # 3️⃣ Update last run time
            # ======================================================
            mark_last_run(carrier_id)

            # ======================================================
            # 4️⃣ Post-processing (template, emails, CRM, uploads)
            # ======================================================
            runner_tasks.handle_post_processing(carrier_row, summary, decision)
            # ------------------------------------------------------
            # 4B — Mark EOD as sent (if applicable)
            # ------------------------------------------------------
            if decision.mode_key == "EOD_EOD" and decision.eod_due:
                # EOD_EOD → always mark EOD as sent after post-processing
                mark_eod_sent(carrier_id)

            elif decision.mode_key == "BATCH_EOD" and decision.eod_due:
                # BATCH_EOD → mark only if template was created AND email batch sent
                if summary.get("success") and summary.get("template_updated"):
                    mark_eod_sent(carrier_id)

            # ======================================================
            # 5️⃣ Clean-up downloads (preserve templates)
            # ======================================================
            dl_path = carrier_row.get("download_path")
            if dl_path and os.path.isdir(dl_path):

                active_templates = []
                template_name = carrier_row.get("carrier_template")
                if template_name:
                    active_templates.extend([
                        f"{template_name}.xlsx",
                        f"{template_name}.csv",
                    ])

                file_utils.cleanup_download_path(
                    dl_path,
                    active_templates=active_templates
                )

            # ======================================================
            # 6️⃣ SUCCESS LOG ENTRY
            # ======================================================
            logger_utils.log_final_entry(
                script_name=script,
                success="Process completed successfully",
                run_id=run_id
            )

            summaries.append(summary)

        except Exception as e:

            # ======================================================
            # 7️⃣ FAILURE LOG ENTRY
            # ======================================================
            try:
                logger_utils.log_final_entry(
                    script_name=script,
                    error=str(e),
                    run_id=run_id
                )
            except Exception as err2:
                print(f"⚠️ Failed to write failure log: {err2}")

            handle_ops_fail(
                carrier_id=carrier_id,
                carrier_name=carrier_name,
                error_message=f"Cycle-level error: {e}",
                error_code="GEN_LOOP"
            )

            continue

    return summaries


# ==========================================================
# 5️⃣ Orchestrator Loop (with summary + graceful shutdown)
# ==========================================================
if __name__ == "__main__":
    print(f"🔁 ACC Orchestrator loop started @ {runner_utils.now_cst():%I:%M %p %Z}")

    cutoff = getattr(config, "BUSINESS_END", None)
    loop_min = getattr(config, "MIN_LOOP_GRANULARITY_MIN", 5)
    loop_sec = loop_min * 60

    try:
        while True:
            print(f"\n🕒 {runner_utils.now_cst():%I:%M %p %Z} → Executing orchestrator cycle...")

            try:
                # 🚀 Run all eligible carriers
                summaries = run_cycle()

                # ======================================================
                # 🧾 Summary Email
                # ======================================================
                if summaries:
                    recipients = (
                        ", ".join(config.SUMMARY_EMAILS)
                        if isinstance(config.SUMMARY_EMAILS, (list, tuple))
                        else config.SUMMARY_EMAILS
                    )

                    print(f"📧 Preparing summary email for {len(summaries)} carrier(s)...")
                    runner_utils.summarize_run(
                        run_id=RUN_ID,
                        run_results=summaries,
                        email_to=recipients
                    )
                else:
                    print("ℹ️ No carriers processed — skipping summary email.")

            except Exception as e:
                # Carrier-specific logging still happens inside handle_ops_fail()
                handle_ops_fail(None, None, f"Run cycle exception: {e}", "GEN_LOOP")

            # ======================================================
            # 🕓 EOD SHUTDOWN CHECK
            # ======================================================
            now = runner_utils.now_cst()
            if cutoff and now.time() > cutoff:
                print(f"⏹️ Business cutoff reached ({cutoff}), performing EOD reset...")
                runner_utils.reset_daily_run_state()
                print("✅ ACC Orchestrator shut down cleanly after business hours.")
                break

            # ======================================================
            # 💤 Sleep between cycles
            # ======================================================
            print(f"💤 Sleeping {loop_min} minute(s)...")
            time.sleep(loop_sec)

    except KeyboardInterrupt:
        print("🟥 Manual stop received — performing graceful shutdown.")
        runner_utils.reset_daily_run_state()

    except Exception as e:
        handle_ops_fail(None, None, f"Fatal orchestrator error: {e}", "GEN_FATAL", killswitch=True)


