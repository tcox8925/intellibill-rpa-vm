import os
from datetime import datetime, timedelta
from rpa_matrix_reader import read_rpa_matrix
from azure_blob_utils import authenticate_blob_storage, upload_blob
from date_utils import get_current_date_info
from logger import setup_logger, log_error, log_success, log_final_entry, ERROR_CODES, init_log_entry,update_log_extra_fields
from file_utils import unzip_file, build_renamed_file_path, build_blob_path, build_gdrive_path
from email_utils import send_email_notification, get_email_recipients, send_acu_bob_summary_email
from chrome_utils import get_chrome_driver
from carrier_handlers import handler_map
from rpa_matrix_upload import update_flag_completion
import shutil

today = datetime.today().date()
date_info = get_current_date_info()
rpa_matrix = read_rpa_matrix().astype(str).apply(lambda x: x.str.strip())
today_str = datetime.now().strftime("%m%d%Y")

def get_target_dates(target_date_str, cadence):
    if cadence.lower() == "daily":
        return [today]
    elif cadence.lower() == "weekly":
        return [today - timedelta(days=today.weekday())]
    elif target_date_str:
        days = [int(d) for d in target_date_str.split(",") if d.strip().isdigit()]
        return [today.replace(day=d) for d in days]
    return []


found_matching_rows = False

for _, row in rpa_matrix.iterrows():
    script_name_logged = row.get("script_name", "main_rpa")
    try:
        process_name = row["process_name"]
        company_id = row["company_id"]
        carrier_id = row["carrier_id"]
        script_name = row["script_name"]
        cadence = row.get("cadence", "").lower()
        flag_completion = row.get("flag_completion", "0")

        target_dates = get_target_dates(row["target_dates"], cadence)
        if today not in target_dates:
            continue

        found_matching_rows = True
        is_last_date = target_dates and today == max(target_dates)

        if cadence == "monthly" and flag_completion == "1":
            if is_last_date:
                update_flag_completion(process_name, company_id, carrier_id, flag_value="0")
            else:
                continue

        script_name_logged = setup_logger(script_name)
        init_log_entry(script_name_logged)

        blob_client = authenticate_blob_storage(process_name, company_id, carrier_id)
        if not blob_client:
            log_error(ERROR_CODES["auth_error"], "Blob authentication failed", script_name_logged)
            log_final_entry(script_name_logged)
            continue

        print("use_profile_path value:", row.get("use_profile_path", ""))
        profile_path = row["profile_path"] if str(row.get("use_profile_path", "")) == "YES" else None
        download_folder = os.path.normpath(row["download_path"])
        os.makedirs(download_folder, exist_ok=True)
        driver = get_chrome_driver(profile_path=profile_path, download_folder=download_folder)

        handler = handler_map.get(script_name)
        #print(f"Looking for Check Date: {date_info['prev_month_year_full']}")
        downloaded_file = handler(driver, row, date_info)
        driver.quit()

        if not downloaded_file or not os.path.exists(downloaded_file):
            log_error(ERROR_CODES["download_error"], "Download failed or file missing.", script_name_logged)
            log_final_entry(script_name_logged)
            continue

        requires_extraction = str(row.get("requires_extraction", "")).upper() == "YES"
        if requires_extraction:
            print("requires_extraction set to YES, Extracting file")
            unzip_file(downloaded_file, download_folder)
            print(downloaded_file)
            print(download_folder)
            prefix = row["file_prefix"].strip()
            extension = row["expected_extension"].strip().lower()
        else:
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
        upload_blob(blob_client, "834analytics-dev", renamed_file, blob_path)

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

        # Expecting ['', '042025', '032025']
        if len(parts) >= 3:
            report_month_raw = parts[1]  # '042025'
            process_month_raw = parts[2]  # '032025'

            # Parse dates
            file_report_month = datetime.strptime(report_month_raw, "%m%Y").strftime("%Y-%m-%d")
            file_com_month = datetime.strptime(process_month_raw, "%m%Y").strftime("%Y-%m-%d")

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
            product_name=row.get("product_name", "")
        )

        log_final_entry(script_name_logged)
        # Email notification
        flow_url = row.get("pautomate_url")
        if flow_url and row.get("notification_process") and process_name =="COM":
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
        log_error(ERROR_CODES["general_error"], str(e), script_name_logged)

if found_matching_rows:
    send_acu_bob_summary_email(rpa_matrix, today_str)
else:
    print("No matching target dates found. No processes were run today.")
