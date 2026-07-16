# ==========================================================
# utils/runner_tasks.py
# ==========================================================
"""
Runner Tasks for ACC RPA Orchestration
--------------------------------------
Responsible for all post-handler actions:
    • Template append & upload
    • Email dispatch (single or batch)
    • CRM bulk upload (Zoho)
    • EOD state resets

All email operations use utils/email_utils.py
All file operations use utils/file_utils.py
"""

import os
from utils import file_utils, runner_utils, config, db_utils, zoho_utils
from utils.email_utils import send_email, send_email_with_attachment, send_email_with_attachments
from utils.logger_utils import safe_log
from utils.db_utils import get_postgres_connection
from utils.runner_utils import now_cst
import pandas as pd
import re
from concurrent.futures import ThreadPoolExecutor, as_completed


# ==========================================================
# 1️⃣ CRM UPLOAD HANDLER
# ==========================================================
def upload_to_crm(carrier_row, summary, decision):
    """
    Handles post-run CRM synchronization via Zoho Bulk Write.
    Applies filters and field mappings defined in decision and handler summary.
    Always runs independently of template/email requirements.
    """
    import pandas as pd
    import os

    carrier_name = carrier_row.get("carrier_name")
    carrier_id = carrier_row.get("carrier_id")
    download_path = carrier_row.get("download_path") or os.getenv("TEMP", "/tmp")

    # ----------------------------------------------------------
    # Select the working DataFrame
    # ----------------------------------------------------------
    df = None
    if isinstance(summary.get("ordered_df"), pd.DataFrame) and not summary["ordered_df"].empty:
        df = summary["ordered_df"]
    elif isinstance(summary.get("df_queue"), pd.DataFrame) and not summary["df_queue"].empty:
        df = summary["df_queue"]

    if df is None or df.empty:
        print(f"ℹ️ No queue data for CRM upload → {carrier_name}")
        return

    print(f"[DEBUG][CRM] Starting upload_to_crm() for {carrier_name} → initial rows={len(df)}")

    # ----------------------------------------------------------
    # CRM FILTER (decision → handler → default)
    # ----------------------------------------------------------
    crm_filter = (
        getattr(decision, "crm_filter", {})
        or summary.get("crm_filter", {})
        or {"status": ["Success"], "agent_type": ["Agent", "Agency"]}
    )
    print(f"[DEBUG][CRM] crm_filter={crm_filter}")

    # ----------------------------------------------------------
    # DYNAMIC FILTERING:
    #   AND all filters together...
    #   OR include special_incl=1
    # ----------------------------------------------------------
    df_all = df.copy()

    # pull special_incl out of the normal filter bucket
    special_vals = crm_filter.pop("special_incl", None)

    # PART A → AND logic for all normal filters
    df_main = df_all.copy()
    for col, allowed_vals in crm_filter.items():

        if col not in df_main.columns:
            print(f"[DEBUG][CRM] Skipping filter: '{col}' not in DataFrame")
            continue

        allowed_vals_lower = [str(v).lower() for v in allowed_vals]
        before = len(df_main)
        df_main = df_main[df_main[col].astype(str).str.lower().isin(allowed_vals_lower)]
        print(f"[DEBUG][CRM] Filtered {col}: {before} → {len(df_main)} rows")

    # PART B → special_incl OR logic
    df_special = pd.DataFrame()

    if special_vals and "special_incl" in df_all.columns:
        try:
            special_vals_int = [int(v) for v in special_vals]
            before = len(df_all)
            df_special = df_all[df_all["special_incl"].astype(int).isin(special_vals_int)]
            print(f"[DEBUG][CRM] special_incl filter: {before} → {len(df_special)} rows")
        except Exception as e:
            print(f"[DEBUG][CRM] special_incl filter error: {e}")

    # UNION → OR logic (drop duplicate NPNs)
    df = pd.concat([df_main, df_special], ignore_index=True).drop_duplicates()

    print(f"[DEBUG][CRM] After OR-filtering → {len(df)} rows remain")

    if df.empty:
        print(f"ℹ️ All records filtered out by crm_filter for {carrier_name}")
        return

    # ----------------------------------------------------------
    # Postgres CRM Upload (Bulk Write)
    # ----------------------------------------------------------
    print(f"🚀 Preparing Postgres CRM upload for {carrier_name} ({len(df)} record(s))")
    
    crm_mappings = summary.get("crm_mapping", [])
    if not crm_mappings:
        print(f"ℹ️ No Postgres CRM mapping defined for {carrier_name}")
    else:
        for mapping in crm_mappings:
            module = mapping.get("module")
            field_map = mapping.get("field_mapping", {})

            if not module or not field_map:
                print(f"⚠️ Skipping invalid mapping for {carrier_name}: {mapping}")
                continue

            records = df.to_dict(orient="records")
            payload = db_utils.build_crm_payload(records, mapping)

            if not payload:
                print(f"ℹ️ No payload records for module {module} ({carrier_name})")
                continue

            try:
                result = db_utils.bulk_update_crm(
                    module,
                    payload,
                    carrier_id=carrier_id,
                    download_path=download_path,
                )
                print(f"✅ CRM upload completed → {module} ({carrier_name}) → {result}")

            except Exception as e:
                safe_log(
                    "ACC_RPA_TASKS",
                    f"CRM upload failed for {carrier_name}: {e}",
                    code="CRM_UPDATE_ERROR",
                )

        df_notes = df.loc[df['crm_note'].notna()]
        print(f"🚀 Preparing CRM Note Upload for {carrier_name} ({len(df_notes)} record(s))")
        db_utils.upload_crm_notes(df_notes)

    # ----------------------------------------------------------
    # Zoho CRM Upload (Bulk Write)
    # ----------------------------------------------------------
    zoho_mappings = summary.get("zoho_mapping", [])
    if not zoho_mappings:
        print(f"ℹ️ No Zoho CRM mapping defined for {carrier_name}")
        return

    print(f"🚀 Preparing Zoho CRM upload for {carrier_name} ({len(df)} record(s))")

    for mapping in zoho_mappings:
        module = mapping.get("module")
        field_map = mapping.get("field_mapping", {})

        if not module or not field_map:
            print(f"⚠️ Skipping invalid mapping for {carrier_name}: {mapping}")
            continue

        records = df.to_dict(orient="records")
        payload = zoho_utils.build_crm_payload(records, mapping)

        if not payload:
            print(f"ℹ️ No payload records for module {module} ({carrier_name})")
            continue

        try:
            result = zoho_utils.bulk_update_crm(
                module,
                payload,
                carrier_id=carrier_id,
                download_path=download_path,
            )
            print(f"✅ CRM upload completed → {module} ({carrier_name}) → {result}")

        except Exception as e:
            safe_log(
                "ACC_RPA_TASKS",
                f"CRM upload failed for {carrier_name}: {e}",
                code="CRM_UPDATE_ERROR",
            )

    df_notes = df.loc[df['crm_note'].notna()]
    print(f"🚀 Preparing CRM Note Upload for {carrier_name} ({len(df_notes)} record(s))")
    zoho_utils.upload_crm_notes(df_notes)



# ==========================================================
# 2️⃣ MAIN POST-PROCESSING HANDLER (FINAL)
# ==========================================================
def handle_post_processing(carrier_row, summary, decision):
    """
    Executes final actions after each carrier handler completes.
    Includes debug tracing for template, email, CRM, and output file generation.
    """
    carrier_name = carrier_row.get("carrier_name")
    carrier_id = carrier_row.get("carrier_id")
    template_required = decision.template_required
    email_mode = decision.email_mode
    eod_due = decision.eod_due

    print(f"\n[DEBUG] ===== POST-PROCESSING START ({carrier_name}) =====")
    print(f"[DEBUG] template_required={template_required}, email_mode={email_mode}, eod_due={eod_due}")

    try:
        # ------------------------------------------------------
        # 1️⃣ TEMPLATE HANDLING
        # ------------------------------------------------------
        local_template_path = None
        if template_required:
            print("[DEBUG] Template phase triggered...")

            base_template_path = file_utils.download_base_template_from_blob(carrier_row)
            if not base_template_path or not os.path.exists(base_template_path):
                print(f"[DEBUG] ❌ Base template not found → {base_template_path}")
            else:
                df_success = None
                if isinstance(summary.get("ordered_df"), pd.DataFrame) and not summary["ordered_df"].empty:
                    df_success = summary["ordered_df"]
                elif isinstance(summary.get("df_success"), pd.DataFrame) and not summary["df_success"].empty:
                    df_success = summary["df_success"]

                if df_success is None or df_success.empty:
                    print(f"[DEBUG] ⚠️ No DataFrame found for {carrier_name}")
                else:
                    # Apply filter: Success + crm_success_status
                    crm_status = carrier_row.get("crm_success_status")
                    template_filter = summary.get("template_filter")
                    for col, allowed_vals in template_filter.items():
                        if col in df_success.columns:
                            before = len(df_success)
                            allowed_vals = [str(v).lower() for v in allowed_vals]
                            df_success = df_success[
                                df_success[col].astype(str).str.lower().isin(allowed_vals)
                            ]
                            print(f"[DEBUG] Filtered {col}: {before} → {len(df_success)} rows remain")
                        else:
                            print(f"[DEBUG] Column '{col}' not found in dataframe → skipping filter")


                    if not df_success.empty:
                        print(f"[DEBUG] Appending {len(df_success)} rows to {base_template_path}")
                        local_template_path = file_utils.append_to_template(
                            df_success, carrier_row,
                            base_template_path=base_template_path,
                            mapping=summary.get("template_mapping"),
                            header_row=summary.get("header_row", 1),
                            header_column=summary.get("header_column", 0)
                        )
                        if local_template_path:
                            print(f"🧾 Template updated → {os.path.basename(local_template_path)}")
                    else:
                        print(f"[DEBUG] ℹ️ No eligible rows after filtering for {carrier_name}")

        # ------------------------------------------------------
        # 2️⃣ EMAIL HANDLING
        # ------------------------------------------------------
        print(f"[DEBUG] Checking email mode: {email_mode}")
        if email_mode == "single":
            send_per_record_emails(carrier_row, summary, decision)
        elif email_mode == "batch" and (eod_due or getattr(config, "TEST_MODE", False)):
            if template_required and local_template_path:
                print(f"[DEBUG] Sending batch email → {carrier_name}")
                send_batch_email_with_template(carrier_row, local_template_path, summary)
                file_utils.upload_template_to_success(local_template_path, carrier_row)
                if hasattr(runner_utils, "reset_eod_state"):
                    runner_utils.reset_eod_state(carrier_id)
            else:
                print(f"[DEBUG] Skipping batch email (template_required={template_required}, path={local_template_path})")
        else:
            print(f"[DEBUG] ℹ️ No email dispatch required yet for {carrier_name}")

        # ------------------------------------------------------
        # 3️⃣ CRM UPLOAD (always runs)
        # ------------------------------------------------------
        print("[DEBUG] Initiating CRM upload...")
        upload_to_crm(carrier_row, summary, decision)
        print("[DEBUG] CRM upload complete")

        # ------------------------------------------------------
        # 4️⃣ OUTPUT FILE GENERATION (Success + Error TXT)
        # ------------------------------------------------------
        # ------------------------------------------------------
        # 4️⃣ OUTPUT FILE GENERATION (Success + Error TXT)
        # ------------------------------------------------------
        try:
            df_all = None
            if isinstance(summary.get("ordered_df"), pd.DataFrame):
                df_all = summary["ordered_df"]
            elif isinstance(summary.get("df_success"), pd.DataFrame):
                df_all = summary["df_success"]
            elif isinstance(summary.get("df_queue"), pd.DataFrame):
                df_all = summary["df_queue"]

            if df_all is not None and not df_all.empty:
                crm_success_status = carrier_row.get("crm_success_status") or "Sent to Agent"

                # Split success vs error
                df_success = df_all[
                    (df_all["status"].astype(str).str.lower() == "success") &
                    (df_all["contract_status"].astype(str).str.lower() == crm_success_status.lower())
                    ].copy()

                df_error = df_all[
                    ~((df_all["status"].astype(str).str.lower() == "success") &
                      (df_all["contract_status"].astype(str).str.lower() == crm_success_status.lower()))
                ].copy()

                print(f"[DEBUG] Preparing TXT files → Success={len(df_success)}, Error={len(df_error)}")

                # ⭐ FIXED — GET FILE PATHS FROM DICT, NOT TUPLE
                paths = file_utils.generate_output_files(df_success, df_error, carrier_row, carrier_id)

                success_path = paths.get("success_file")
                error_path = paths.get("error_file")

                print(f"[DEBUG] Paths returned → success={success_path}, error={error_path}")

                # Upload using correct paths
                uploaded_txt = file_utils.upload_output_files_to_blob({
                    "success": success_path,
                    "error": error_path,
                })

                print(f"☁️ Uploaded TXT files → {uploaded_txt}")

            else:
                print(f"ℹ️ No data available for success/error TXT generation ({carrier_name}).")

        except Exception as e:
            safe_log("ACC_RPA_TASKS",
                     f"TXT generation failed for {carrier_name}: {e}",
                     code="TXT_GEN_ERROR")

    except Exception as e:
        safe_log("ACC_RPA_TASKS", f"Post-processing failed for {carrier_name}: {e}", code="TASK_POSTPROC_ERROR")

    # ------------------------------------------------------
    # 5️⃣ QUEUE CLEANUP (Post-run purge)
    # ------------------------------------------------------
    try:
        conn = get_postgres_connection()
        cur = conn.cursor()
        cur.execute("""
            DELETE FROM wpo.ops_acc_process_queue
             WHERE carrier_id = %s
        """, (carrier_row.get("carrier_id"),))
        affected = cur.rowcount or 0
        conn.commit()
        conn.close()
        print(f"🧹 Cleared {affected} queue record(s) for {carrier_name}")
    except Exception as e:
        safe_log(
            "ACC_RPA_TASKS",
            f"Queue cleanup failed for {carrier_name}: {e}",
            code="QUEUE_CLEANUP_ERROR",
        )

    print(f"[DEBUG] ===== POST-PROCESSING END ({carrier_name}) =====\n")



# ==========================================================
# 3️⃣ SINGLE EMAIL HANDLER
# ==========================================================
def send_per_record_emails(carrier_row, summary, decision):
    """
    Sends individual emails per successful record.
    Multi-threaded (3 workers max).

    Uses handler-defined email_template and supports:
    - dynamic placeholders
    - greeting
    - attachments_mapping
    """
    tpl = summary.get("email_template") or {}
    if not tpl:
        print(f"ℹ️ No email template defined for {carrier_row.get('carrier_name')}.")
        return

    df_queue = summary.get("df_queue")
    if df_queue is None or df_queue.empty:
        print(f"ℹ️ No eligible records for {carrier_row.get('carrier_name')}.")
        return

    # ------------------------------------------------------
    # APPLY OPTIONAL FILTER
    # ------------------------------------------------------
    email_filter = getattr(decision, "single_email_filter", {}) or {}
    for col, allowed_vals in email_filter.items():
        allowed_vals = [v.lower() for v in allowed_vals]
        df_queue = df_queue[df_queue[col].astype(str).str.lower().isin(allowed_vals)]

    if df_queue.empty:
        print(f"ℹ️ No records match email filter for {carrier_row.get('carrier_name')}.")
        return

    # ------------------------------------------------------
    # HELPERS
    # ------------------------------------------------------
    def greeting():
        h = now_cst().hour
        return "morning" if h < 12 else "afternoon" if h < 17 else "evening"

    def extract_placeholders(template: str) -> list:
        return re.findall(r"{(\w+)}", template or "")

    # ------------------------------------------------------
    # TEMPLATE CONFIG
    # ------------------------------------------------------
    email_to = tpl.get("email_to") or carrier_row.get("email_to")
    email_cc = tpl.get("email_cc") or getattr(config, "EMAIL_CC", None)
    subj_fmt = tpl.get("subject", "")
    body_fmt = tpl.get("body", "")
    attachment_source = tpl.get("attachments_source", "queue")
    carrier_name = carrier_row.get("carrier_name")

    all_placeholders = set(extract_placeholders(subj_fmt) + extract_placeholders(body_fmt))

    # ------------------------------------------------------
    # WORKER FUNCTION (runs inside threads)
    # ------------------------------------------------------
    def send_one_email(row):
        """Send email for a single DataFrame row."""
        try:
            # Build substitution dict
            subs = {}
            for key in all_placeholders:
                if key == "greeting":
                    subs[key] = greeting()
                elif key == "carrier":
                    subs[key] = carrier_name
                else:
                    subs[key] = (
                        row.get(key)
                        or row.get(key.lower())
                        or row.get(key.upper())
                        or ""
                    )

            subject = subj_fmt.format(**subs)
            body = body_fmt.format(**subs)

            # Attachments
            attachments = []
            if attachment_source == "queue":
                attachments = [
                    p for p in [row.get("contract_path"), row.get("w9_path"), row.get("eo_path")]
                    if p and os.path.exists(p)
                ]
            elif attachment_source == "template" and summary.get("local_template_path"):
                attachments = [summary["local_template_path"]]
            elif attachment_source == "custom" and tpl.get("custom_attachments"):
                attachments = [p for p in tpl["custom_attachments"] if p and os.path.exists(p)]

            # Send email
            if attachments:
                send_email_with_attachments(
                    to=email_to,
                    cc=email_cc,
                    subject=subject,
                    body=body,
                    attachment_paths=attachments
                )
            else:
                send_email(
                    to=email_to,
                    cc=email_cc,
                    subject=subject,
                    body=body
                )

            return True

        except Exception as e:
            safe_log(
                "ACC_RPA_TASKS",
                f"Email send failed for {row.get('npn') or 'UNKNOWN'}: {e}",
                code="EMAIL_SEND_ERROR"
            )
            return False

    # ------------------------------------------------------
    # MULTITHREADED EXECUTION (max_workers=3)
    # ------------------------------------------------------
    futures = []
    sent = failed = 0

    with ThreadPoolExecutor(max_workers=3) as executor:
        for _, row in df_queue.iterrows():
            futures.append(executor.submit(send_one_email, row))

        for f in as_completed(futures):
            if f.result():
                sent += 1
            else:
                failed += 1

    print(f"📧 Sent {sent} email(s) (failed: {failed}) for {carrier_name}")


# ==========================================================
# 4️⃣ BATCH EMAIL HANDLER
# ==========================================================
def send_batch_email_with_template(carrier_row, local_template_path, summary=None):
    """
    Sends one batch email to carrier with filled template attached.
    Uses handler-defined email_template if provided.
    """
    tpl = (summary or {}).get("email_template") or {}
    carrier_name = carrier_row.get("carrier_name")

    email_to = tpl.get("email_to") or carrier_row.get("email_to")
    email_cc = tpl.get("email_cc") or getattr(config, "EMAIL_CC", None)
    subject = tpl.get("subject")
    body = tpl.get("body")

    try:
        send_email_with_attachment(
            to=email_to,
            cc=email_cc,
            subject=subject,
            body=body,
            attachment_path=local_template_path,
            mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
        print(f"📨 Batch email with template sent for {carrier_name}")
    except Exception as e:
        safe_log(
            "ACC_RPA_TASKS",
            f"Batch email failed for {carrier_name}: {e}",
            code="EMAIL_BATCH_FAIL",
        )