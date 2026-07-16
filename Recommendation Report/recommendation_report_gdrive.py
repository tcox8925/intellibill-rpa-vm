import os
from azure_blob_utils import authenticate_blob_storage, download_blob, upload_blob
from logger import setup_logger, log_error, log_success, log_final_entry, ERROR_CODES, init_log_entry, \
    update_log_extra_fields
from file_utils import unzip_file, build_renamed_file_path, build_blob_path, build_gdrive_path
import shutil
from datetime import datetime

script_name_logged = setup_logger("Recommendation Report - Desktop")
init_log_entry(script_name_logged)
update_log_extra_fields(
    script_name_logged,
    flow_id="9fc8e33e-b134-4b6f-9e21-2325f0875544"
)

now = datetime.now()
today_date = now.strftime("%m%d%Y")  # e.g., "02162025"
filename = f"recommendation_report_{today_date}.csv"
blob_target_path = f"results/recommendation_report/{filename}"
blob_archive_target_path = f"results/recommendation_report/archive/{filename}"
gdrive_target_path = f"C:/Users/myopsadmin/Agility Insurance Services/834 Labs - Documents/Data Ops Production Files/834labs raw files/recommendation reports/{filename}"
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
