import os
import shutil
import pandas as pd
from datetime import datetime
from azure.identity import ClientSecretCredential
from azure.storage.blob import BlobServiceClient
from logger import log_success, log_error, log_final_entry, ERROR_CODES
import difflib

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
    elif file_type == "EXC":
        g_drive_folder = os.path.join(
            r"G:\Shared drives\Data Analytics\Data Ops Production Files\834labs raw files\exclusion files (exc)",
            f"{current_month_number} {current_month_name} {current_year}"
        )
        blob_folder = f"raw/exclusion_candidates/{current_year} {current_month_number} {current_month_short}"
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
        log_success()
    except Exception as e:
        log_error(ERROR_CODES["upload_error"], f"Failed to upload {file_path} to Azure: {e}", script_name)

# Find Best Match
def find_best_match(download_file, all_files_no_ext):
    match_threshold = 0.8
    best_matches = difflib.get_close_matches(download_file, all_files_no_ext, n=1, cutoff=match_threshold)
    return best_matches[0] if best_matches else None

# Process Files
def process_files():
    df = pd.read_excel(matrix_file)
    required_columns = ["Downloaded_File", "RENAME", "Type", "CARRIER", "Log_In_Required?"]
    if not all(col in df.columns for col in required_columns):
        log_error(ERROR_CODES["file_error"], f"Matrix file must contain columns: {required_columns}", script_name)
        return

    df = df[df["Log_In_Required?"].str.strip().str.lower().isin(["", "no"])]

    if df.empty:
        log_error(ERROR_CODES["file_error"], "No rows to process after filtering Log_In_Required? = No", script_name)
        return

    blob_service_client = authenticate_azure()
    today_date = datetime.now().strftime("%m%d%Y")

    all_files = [f for f in os.listdir(gdrive_source) if os.path.isfile(os.path.join(gdrive_source, f))]
    all_files_no_ext = [os.path.splitext(f)[0] for f in all_files]

    processed_files = set()

    for _, row in df.iterrows():
        try:
            download_file = row["Downloaded_File"]
            if pd.isna(download_file) or not isinstance(download_file, str):
                continue

            download_file = download_file.strip()
            if "rpa_automation" in download_file.lower():
                continue

            rename_to = row["RENAME"]
            file_type = row["Type"]
            carrier_script = row["CARRIER"]

            best_match = find_best_match(download_file, all_files_no_ext)
            if not best_match:
                continue  # Skip files not found in the source directory

            matching_file = next(f for f in all_files if os.path.splitext(f)[0] == best_match)
            processed_files.add(matching_file)

            source_file_path = os.path.join(gdrive_source, matching_file)
            g_drive_folder, blob_folder = get_drive_and_blob_paths(file_type)

            base_name, ext = os.path.splitext(rename_to)
            rename_with_date = f"{base_name.strip('_')}_{today_date}{ext if ext else os.path.splitext(matching_file)[1]}"
            renamed_file_path = os.path.join(g_drive_folder, rename_with_date)

            if not os.path.exists(g_drive_folder):
                os.makedirs(g_drive_folder)

            shutil.move(source_file_path, renamed_file_path)
            log_success()

            upload_to_azure(renamed_file_path, blob_folder, blob_service_client)

        except Exception as e:
            log_error(ERROR_CODES["general_error"], f"Error processing row for carrier script {carrier_script}: {e}", script_name)

    # Delete files not mentioned in the Downloaded_File column
    for file in all_files:
        if file not in processed_files and "rpa_automation" not in file.lower():
            os.remove(os.path.join(gdrive_source, file))
            log_success()  # Log file deletion

    log_final_entry("RPA_File_Processing")

if __name__ == "__main__":
    process_files()
