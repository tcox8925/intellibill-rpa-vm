import os
from azure_blob_utils import authenticate_blob_storage, download_blob, upload_blob
from logger import setup_logger, log_error, log_success, log_final_entry, ERROR_CODES, init_log_entry, \
    update_log_extra_fields
from file_utils import unzip_file, build_renamed_file_path, build_blob_path, build_gdrive_path
import shutil
from datetime import datetime

script_name_logged = setup_logger("High Policy Count Report - Desktop")
init_log_entry(script_name_logged)
update_log_extra_fields(
    script_name_logged,
    flow_id="C4F9AA42-5CBC-49EA-A63A-37F976E85F11",
    sub_entity_id="270681372001"
)

now = datetime.now()
today_date = now.strftime("%m%d%Y")  # e.g., "02062025"
filename = f"high_policy_report_{today_date}.csv"
blob_target_path = f"results/High Policy Count Reports/{filename}"
blob_archive_target_path = f"results/High Policy Count Reports/archive/{filename}"
gdrive_target_path = f"G:/My Drive/High Policy Count Report/{filename}"
container_name = "834analytics-dev"

# Connect to blob
blob_client = authenticate_blob_storage()
if not blob_client:
    print("Blob authentication failed.")
    log_error(ERROR_CODES["auth_error"], "Blob authentication failed", script_name_logged)
    log_final_entry(script_name_logged)
    
# Download file from blob into gdrive
download_blob(blob_client, container_name, blob_path=blob_target_path, local_file_path=gdrive_target_path)
print("Download process finished.")

log_success()
log_final_entry(script_name_logged)
