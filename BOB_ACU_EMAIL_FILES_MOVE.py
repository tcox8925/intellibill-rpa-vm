import os
import shutil
import pandas as pd
from datetime import datetime
from azure.identity import ClientSecretCredential
from azure.storage.blob import BlobServiceClient
from logger import log_success, log_error, log_final_entry, ERROR_CODES

# Configurations
matrix_file = r"G:\My Drive\Matrix - Email\RPA_automation_12032024.xlsx"
gdrive_source = r"G:\My Drive\Matrix - Email"

# Azure Blob Storage Authentication
def authenticate_azure():
    tenant_id = os.getenv("AZURE_TENANT_ID", "")
    client_id = os.getenv("AZURE_CLIENT_ID", "")
    client_secret = os.getenv("AZURE_CLIENT_SECRET", "")

    try:
        azure_credential = ClientSecretCredential(
            tenant_id=tenant_id,
            client_id=client_id,
            client_secret=client_secret
        )
        storage_account_name = "834analyticsdatalake"
        account_url = f"https://{storage_account_name}.blob.core.windows.net"
        blob_service_client = BlobServiceClient(account_url=account_url, credential=azure_credential)
        return blob_service_client
    except Exception as e:
        log_error(ERROR_CODES["auth_error"], f"Error authenticating to Azure Blob Storage: {e}", script_name)
        exit(1)

# Get Folder and Blob Paths
def get_drive_and_blob_paths(file_type):
    current_month_number = datetime.now().strftime("%m")
    current_month_name = datetime.now().strftime("%B")
    current_month_short = datetime.now().strftime("%b")
    current_year = datetime.now().strftime("%Y")

    if file_type == "BOB":
        g_drive_folder = os.path.join(
            r"G:\Shared drives\Data Analytics\Data Ops Production Files\834labs raw files\production reports (BOB)",
            f"{current_month_number} {current_month_name} {current_year}"
        )
        blob_folder = f"raw/agent_contract_update/{current_month_number} {current_month_short} {current_year}"
    elif file_type == "ACU":
        g_drive_folder = os.path.join(
            r"G:\Shared drives\Data Analytics\Data Ops Production Files\834labs raw files\agent contract updates (acu)",
            f"{current_month_number} {current_month_name} {current_year}"
        )
        blob_folder = f"raw/agent_contract_update/{current_month_number} {current_month_short} {current_year}"
    else:
        log_error(ERROR_CODES.get("carrier_error", "Unknown error", script_name), f"Unsupported file type: {file_type}")
        raise ValueError(f"Unsupported file type: {file_type}")

    return g_drive_folder, blob_folder

# Upload File to Azure Blob Storage
def upload_to_azure(file_path, blob_folder, blob_service_client):
    blob_name = os.path.join(blob_folder, os.path.basename(file_path))
    try:
        blob_client = blob_service_client.get_blob_client(container="834analytics-dev", blob=blob_name)
        with open(file_path, "rb") as data:
            blob_client.upload_blob(data, overwrite=True)
        log_success()  # Corrected to call log_success without arguments
    except Exception as e:
        log_error(ERROR_CODES["upload_error"], f"Failed to upload {file_path} to Azure: {e}", script_name)

# Process Files
def process_files():
    df = pd.read_excel(matrix_file)
    required_columns = ["Downloaded_File", "RENAME", "Type", "CARRIER"]
    if not all(col in df.columns for col in required_columns):
        log_error(ERROR_CODES["file_error"], f"Matrix file must contain columns: {required_columns}", script_name)
        return

    blob_service_client = authenticate_azure()
    today_date = datetime.now().strftime("%m%d%Y")

    # List all files in the source folder
    all_files = [f for f in os.listdir(gdrive_source) if os.path.isfile(os.path.join(gdrive_source, f))]
    all_files_no_ext = [os.path.splitext(f)[0] for f in all_files]  # File names without extension

    for _, row in df.iterrows():
        try:
            download_file = row["Downloaded_File"]
            if pd.isna(download_file) or not isinstance(download_file, str):
                continue  # Skip blank rows in Downloaded_File

            download_file = download_file.strip()
            if download_file.lower() == "rpa_automation_12032024":
                continue  # Skip the RPA file

            rename_to = row["RENAME"]
            file_type = row["Type"]
            carrier_script = row["CARRIER"]

            # Check if the file exists in the folder (ignoring extension)
            if download_file not in all_files_no_ext:
                continue  # Skip if file is not found, no log needed

            # Match the file with its full path
            matching_file = next(f for f in all_files if os.path.splitext(f)[0] == download_file)
            source_file_path = os.path.join(gdrive_source, matching_file)

            # Get paths based on file_type
            g_drive_folder, blob_folder = get_drive_and_blob_paths(file_type)

            # Append today's date to the renamed file, removing duplicate underscores
            base_name, ext = os.path.splitext(rename_to)
            rename_with_date = f"{base_name.strip('_')}_{today_date}{ext if ext else os.path.splitext(matching_file)[1]}"
            renamed_file_path = os.path.join(g_drive_folder, rename_with_date)

            # Ensure target folder exists
            if not os.path.exists(g_drive_folder):
                os.makedirs(g_drive_folder)

            # Move the file from My Drive to Shared Drive
            shutil.move(source_file_path, renamed_file_path)
            log_success()  # Corrected to call log_success without arguments

            # Upload the moved file to Azure Blob Storage
            upload_to_azure(renamed_file_path, blob_folder, blob_service_client)

        except Exception as e:
            log_error(ERROR_CODES["general_error"], f"Error processing row for carrier script {carrier_script}: {e}", script_name)

    log_final_entry("RPA_File_Processing")

if __name__ == "__main__":
    process_files()
