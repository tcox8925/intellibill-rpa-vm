import os
from datetime import datetime, timedelta
from rpa_matrix_reader import read_rpa_matrix
from azure_blob_utils import authenticate_blob_storage, upload_blob
from date_utils import get_current_date_info
from logger import setup_logger, log_error, log_success, log_final_entry, ERROR_CODES, init_log_entry, \
    update_log_extra_fields
from file_utils import unzip_file, build_renamed_file_path, build_blob_path, build_gdrive_path
from email_utils import send_email_notification, get_email_recipients, send_acu_bob_summary_email
from chrome_utils import get_chrome_driver
from carrier_handlers import handler_map
from rpa_matrix_upload import update_flag_completion
import shutil
from psycopg2.extras import Json
from db_connection import connect_to_db

today = datetime.today().date()
date_info = get_current_date_info()
rpa_matrix = read_rpa_matrix().astype(str).apply(lambda x: x.str.strip())
today_str = datetime.now().strftime("%m%d%Y")


def get_target_dates(target_date_str, cadence):
    if cadence.lower() == "daily":
        return [today]
    elif cadence.lower() == "weekly":
        weekdays = [int(d) for d in target_date_str.split(",") if d.strip().isdigit()]
        current_week_number = today.isocalendar()[1]
        target_dates = []
        # Construct list of target dates for this week from given weekdays
        # 1 = Monday, 7 = Sunday
        for d in weekdays:
            target_dates += [datetime.fromisocalendar(2025, current_week_number, d).date()]
        return target_dates
    elif target_date_str:
        days = [int(d) for d in target_date_str.split(",") if d.strip().isdigit()]
        return [today.replace(day=d) for d in days]
    return []

def insert_ops_inbound_file_log(
    *,
    file_name,
    destination_schema=None,
    destination_table=None,
    process_type=None,
    process_date_start=None,
    process_date_end=None,
    load_status="Ready",
    txn_tot_cnt=None,
    txn_process_cnt=None,
    txn_error_cnt=None,
    status_message=None,
    file_report_month=None,
    file_com_month=None,
    product_name=None,
    carrier_id=None,
    company_id=None,
    sub_entity_id=None,
    validation_details=None,
    validation_status=None,
):
    conn = None
    cur = None
    try:
        conn = connect_to_db()
        cur = conn.cursor()

        insert_sql = """
            INSERT INTO wpo.ops_inbound_file_log (
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
                status_message,
                file_report_month,
                file_com_month,
                product_name,
                carrier_id,
                company_id,
                sub_entity_id,
                validation_details,
                validation_status
            )
            VALUES (
                %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                %s, %s, %s, %s, %s, %s, %s, %s, %s
            )
        """

        cur.execute(
            insert_sql,
            (
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
                status_message,
                file_report_month,
                file_com_month,
                product_name,
                carrier_id,
                company_id,
                sub_entity_id,
                Json(validation_details) if validation_details is not None else None,
                validation_status,
            ),
        )
        conn.commit()

    except Exception as e:
        if conn:
            conn.rollback()
        print(f"Failed to insert ops_inbound_file_log entry: {e}")

    finally:
        if cur:
            cur.close()
        if conn:
            conn.close()

def run_all_carriers():
    found_matching_rows = False
    for _, row in rpa_matrix.iterrows():
        script_name_logged = row.get("script_name", "main_rpa")
        try:
            process_name = row["process_name"]
            company_id = row["company_id"]
            carrier_id = row["carrier_id"]
            carrier_name = row["carrier_name"]
            script_name = row["script_name"]
            cadence = row.get("cadence", "").lower()
            flag_completion = row.get("flag_completion", "0")

            # Skip row if it is marked Yes for disabled
            if row["disabled"].lower() == 'yes':
                print(f"Skipping disabled carrier - {carrier_name} {process_name}")
                continue

            # Skip row if it is marked for sandbox use only
            #if row["run_sandbox_only"].lower() == 'yes':
            #    print(f"Skipping sandbox-only carrier - {carrier_name} {process_name}")
            #    continue
            
            #Process only carriers marked for sandbox
            if row["run_sandbox_only"].lower() != 'yes':
                print(f"Skipping non-sandbox carrier - {carrier_name} {process_name}")
                continue

            #SINGLE CARRIER DEBUG FILTER
            #if carrier_name != 'Molina Healthcare - ACA' or process_name != 'ACU':
                #print(f"[DEBUG] Skipping carrier {carrier_name}; currently targeting another.")
                #continue

            target_dates = get_target_dates(row["target_dates"], cadence)
            if today not in target_dates:
                print(f"No target dates matching today. Skipping {process_name} {carrier_name}.")
                continue

            found_matching_rows = True
            is_last_date = target_dates and today == max(target_dates)

            if cadence == "monthly" and flag_completion == "1":
                if is_last_date:
                    update_flag_completion(process_name, company_id, carrier_id, flag_value="0")
                else:
                    continue

            # Skip row if it contains a parent entry that does not match its own data (Row is a child of parent)
            parent_process_name = row["parent_process_name"]
            parent_carrier_id = row["parent_carrier_id"]
            parent_carrier_name = row["parent_carrier_name"]
            if ((parent_process_name != "") and (parent_carrier_id != "") and (parent_carrier_name != "") and
                    ((process_name != parent_process_name) or (carrier_id != parent_carrier_id) or (
                            carrier_name != parent_carrier_name))
            ):
                print(f"Skipping child process {process_name} {carrier_name} of parent {parent_carrier_name}...")
                continue

            process_started_at = datetime.now()

            script_name_logged = setup_logger(script_name)
            init_log_entry(script_name_logged)
            update_log_extra_fields(
                script_name_logged,
                process_type=row["process_name"],
                product_name=row.get("product_name", ""),
                flow_id="6496A5D2-FF34-4074-81C2-C44C9F4CDD04",
                sub_entity_id="270681372001"
            )

            blob_client = authenticate_blob_storage(process_name, company_id, carrier_id)
            if not blob_client:
                log_error(ERROR_CODES["auth_error"], "Blob authentication failed", script_name_logged)
                log_final_entry(script_name_logged)
                continue

            print("use_profile_path value:", row.get("use_profile_path", ""))
            profile_path = row["profile_path"] if str(row.get("use_profile_path", "")) == "YES" else None

            for attempt in range(4):  # If handler returns None or nothing, retry up to 3 more times.
                print(f"== Attempt #{attempt + 1} to download file...")
                download_folder = os.path.normpath(row["download_path"])
                os.makedirs(download_folder, exist_ok=True)
                driver = get_chrome_driver(profile_path=profile_path, download_folder=download_folder)
                handler = handler_map.get(script_name)
                # print(f"Looking for Check Date: {date_info['prev_month_year_full']}")
                download_folder = None  # Reset download_folder value. Will correctly be [None] if handler crashes and cannot return [None].
                print("== Attempting handler()")
                download_folder = handler(driver, row, date_info)
                print("== handler() finished")
                if download_folder is not None:
                    print(f"Download attempt succeeded! [{download_folder}]")
                    break
                print(f"Download attempt failed. [{download_folder}]")

                log_final_entry(script_name_logged)  # Publish this log's error

                script_name_logged = setup_logger(script_name)  # Reset log
                init_log_entry(script_name_logged)
                update_log_extra_fields(
                    script_name_logged,
                    process_type=row["process_name"],
                    product_name=row.get("product_name", ""),
                    flow_id="6496A5D2-FF34-4074-81C2-C44C9F4CDD04",
                    sub_entity_id="270681372001"
                )

            print(f"== Download folder value returned: [{download_folder}]")
            if download_folder is None:
                print(f"No download folder returned. Logging error for {carrier_name} {process_name}.")
                log_final_entry(script_name_logged)
                driver.quit()
                continue
            else:
                print(f"handler() completed successfully. Download folder received. [{download_folder}]")

            driver.quit()

            downloaded_file = next((f for f in os.listdir(download_folder) if
                                    f.lower().startswith(row["file_prefix"]) and f.upper().endswith(row["file_type"])),
                                None)
            if not downloaded_file:
                log_error(ERROR_CODES["download_error"], "Downloaded file could not be found.", script_name_logged)
                log_final_entry(script_name_logged)
                print(f"Downloaded file could not be found: File prefix: {row['file_prefix']}")
                continue

            downloaded_file_path = os.path.join(download_folder, downloaded_file)

            if not os.path.exists(downloaded_file_path):
                log_error(ERROR_CODES["download_error"], "Path to downloaded file could not be resolved.",
                        script_name_logged)
                log_final_entry(script_name_logged)
                continue

            requires_extraction = str(row.get("requires_extraction", "")).upper() == "YES"
            if requires_extraction:
                unzip_file(downloaded_file_path, download_folder)
                # prefix = row["file_prefix"].strip()
                # extension = row["expected_extension"].strip().lower()
            # else:
            # prefix = row["extracted_file_prefix"].strip()
            # extension = row["extracted_file_extension"].strip().lower()

            prefix = row["extracted_file_prefix"].strip()
            extension = row["extracted_file_extension"].strip().lower()

            print(f"Looking for files with prefix: {prefix}")
            print(f"Looking for files with extension: {extension}")

            matching_files = [
                os.path.join(download_folder, f)
                for f in os.listdir(download_folder)
                if prefix in f and f.lower().endswith(f".{extension}")
            ]

            if not matching_files:
                log_error(ERROR_CODES["general_error"],
                        f"No file found containing '{prefix}' and ending with '.{extension}'.", script_name_logged)
                log_final_entry(script_name_logged)
                continue

            downloaded_file = max(matching_files, key=os.path.getmtime)  # Pick most recent

            file_extension = row["extracted_file_extension"].strip().lower()
            renamed_file = build_renamed_file_path(
                download_folder,
                row["rename_base"].strip(),
                row["process_name"],
                cadence,
                row.get("schedule", "prev_month").lower(),
                date_info,
                file_extension
            )

            os.rename(downloaded_file, renamed_file)

            blob_path = build_blob_path(row, date_info, os.path.basename(renamed_file))

            try:
                upload_blob(blob_client, row["container_name"], renamed_file, blob_path)
            except Exception as e:
                log_error(
                    ERROR_CODES["general_error"],
                    f"Blob upload failed: {str(e)}",
                    script_name_logged
                )
                log_final_entry(script_name_logged)
                print(f"Blob upload failed for file {renamed_file}: {e}")
                continue
            else:
                process_ended_at = datetime.now()
                raw_filename = os.path.basename(renamed_file)

                trimmed = raw_filename.replace(row["rename_base"], "").replace(".csv", "")
                parts = trimmed.split("_")

                file_report_month = None
                file_com_month = None

                if len(parts) >= 2:
                    report_month_raw = parts[0]
                    process_month_raw = parts[1]
                    try:
                        file_report_month = datetime.strptime(report_month_raw, "%m%d%Y").strftime("%Y-%m-%d")
                        file_com_month = datetime.strptime(process_month_raw, "%m%d%Y").strftime("%Y-%m-%d")
                    except Exception as e:
                        print(f"Could not parse file dates from {raw_filename}: {e}")

                insert_ops_inbound_file_log(
                    file_name=raw_filename,
                    # destination_schema=row.get("destination_schema") or None,
                    # destination_table=row.get("destination_table") or None,
                    process_type=f"RPA/{row['process_name']}",
                    process_date_start=process_started_at.strftime("%Y-%m-%d %H:%M:%S"),
                    process_date_end=process_ended_at.strftime("%Y-%m-%d %H:%M:%S"),
                    load_status="Ready",
                    status_message="Uploaded successfully",
                    file_report_month=file_report_month,
                    file_com_month=file_com_month,
                    product_name=row.get("product_name") or None,
                    carrier_id=carrier_id,
                    company_id=company_id,
                    sub_entity_id=row.get("sub_entity_id") or "270681372001",
                    # validation_details={
                    #     "blob_path": blob_path,
                    #     "container_name": row.get("container_name"),
                    #     "source_download_folder": download_folder,
                    #     "process_started_at": process_started_at.strftime("%Y-%m-%d %H:%M:%S"),
                    #     "process_ended_at": process_ended_at.strftime("%Y-%m-%d %H:%M:%S"),
                    #     "uploaded_at": process_ended_at.strftime("%Y-%m-%d %H:%M:%S"),
                    # },
                    # validation_status="Success",
                )

            # Handle G-Drive move if g_drive_base_path is provided.
            print("process_name value:", row["process_name"])
            process_name = row["process_name"].upper()
            gdrive_base = row.get("g_drive_base_path", "").strip()

            # GDrive move if applicable
            if process_name != "COM" and gdrive_base:
                gdrive_folder = build_gdrive_path(gdrive_base, process_name, date_info)
                os.makedirs(gdrive_folder, exist_ok=True)
                destination = os.path.join(gdrive_folder, os.path.basename(renamed_file))
                shutil.move(renamed_file, destination)
                print(f"File moved to G-Drive: {destination}")
            else:
                print("G-Drive move skipped (either COM or no gdrive_base_path).")

            # Cleanup section
            # Delete renamed file (if still in original location)
            if os.path.exists(renamed_file):
                os.remove(renamed_file)
                print(f"Deleted renamed file: {renamed_file}")

            # Delete ZIP file if it exists (only if extraction was done)
            print("requires extraction:", row["requires_extraction"])
            if str(row.get("requires_extraction", "")).upper() == "YES":
                zip_path = os.path.join(download_folder, f"{row['file_prefix']}.zip")
                if os.path.exists(zip_path):
                    os.remove(zip_path)
                    print(f"Deleted ZIP file: {zip_path}")

            log_success()
            log_final_entry(script_name_logged)

            raw_filename = os.path.basename(renamed_file)
            # Remove the rename base
            trimmed = raw_filename.replace(row["rename_base"], "").replace(".csv", "")

            # Split parts
            parts = trimmed.split("_")

            # Expecting ['', '05012025', '04012025']
            if len(parts) >= 2:
                report_month_raw = parts[0]  # '05012025'
                process_month_raw = parts[1]  # '04012025'

                # Parse dates using MMDDYYYY
                file_report_month = datetime.strptime(report_month_raw, "%m%d%Y").strftime("%Y-%m-%d")
                file_com_month = datetime.strptime(process_month_raw, "%m%d%Y").strftime("%Y-%m-%d")

                print(f"Report Month extracted: {file_report_month}")
                print(f"Process Month extracted: {file_com_month}")

            else:
                # Fallback if filename is not as expected
                print(f"Filename not in expected format: {raw_filename}")
                file_report_month = None
                file_com_month = None

            update_log_extra_fields(
                script_name_logged,
                file_status="Ready",
                file_path=blob_path,
                process_type=row["process_name"],
                file_report_month=datetime.now().strftime("%Y-%m-%d"),
                file_com_month=file_com_month,
                company_id=company_id,
                carrier_id=carrier_id,
                product_name=row.get("product_name", ""),
                flow_id="6496A5D2-FF34-4074-81C2-C44C9F4CDD04",
                sub_entity_id="270681372001"
            )

            log_final_entry(script_name_logged)
            # Email notification
            flow_url = row.get("pautomate_url")
            if flow_url and row.get("notification_process") and process_name == "COM":
                recipients = get_email_recipients(row["notification_process"])
                send_email_notification(
                    flow_url=flow_url,
                    process_name=script_name,
                    notification_process=row["notification_process"],
                    to=recipients["to"],
                    cc=recipients["cc"],
                    file_name=os.path.basename(renamed_file),
                    folder_path=os.path.dirname(blob_path),
                    process_type=process_name
                )

            if cadence == "monthly":
                update_flag_completion(process_name, company_id, carrier_id, flag_value="0" if is_last_date else "1")
        except Exception as e:
            print("General exception caught, applying general error to log.")
            log_error(ERROR_CODES["general_error"], str(e), script_name_logged)
            log_final_entry(script_name_logged)

    if found_matching_rows:
        send_acu_bob_summary_email(rpa_matrix, today_str)
    else:
        print("No matching target dates found. No processes were run today.")

run_all_carriers()

import requests
try:
    print("Triggering Service Interruption Engine...")
    resp = requests.post(
        "http://57.154.234.15:8000/service_interruptions/run",
        timeout=30
    )
    print("Service Interruption response:", resp.status_code, resp.text)
except Exception as e:
    print("Failed to trigger Service Interruption Engine:", e)
