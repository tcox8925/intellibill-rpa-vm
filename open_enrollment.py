import os
import shutil
import re
from datetime import datetime
from azure.identity import ClientSecretCredential
from azure.storage.blob import BlobServiceClient
from logger import log_success, log_error, log_final_entry, ERROR_CODES

# Define folders
current_month_name = datetime.now().strftime("%B")
current_month_short = datetime.now().strftime("%b")
current_month_number = datetime.now().strftime("%m")
current_year = datetime.now().strftime("%Y")

# Configurations
gdrive_source = r"G:\My Drive\Matrix - Email"
open_enrollment_folder = r"G:\Shared drives\Data Analytics\Data Analytics Projects\Open Enrollment Report"
blob_folder = f"raw/open_enrollment/{current_year} {current_month_number} {current_month_short}/"

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
        print("Azure Blob Storage authentication successful.")
        return blob_service_client
    except Exception as e:
        log_error(ERROR_CODES["auth_error"], f"Error authenticating to Azure Blob Storage: {e}", script_name)
        print(f"Error authenticating to Azure Blob Storage: {e}")
        exit(1)

# Upload File to Azure Blob Storage
def upload_to_azure(file_path, blob_folder, blob_service_client):
    blob_name = os.path.join(blob_folder, os.path.basename(file_path))
    print(f"Uploading file: {file_path} -> Blob: {blob_name}")
    try:
        blob_client = blob_service_client.get_blob_client(container="834analytics-dev", blob=blob_name)
        with open(file_path, "rb") as data:
            blob_client.upload_blob(data, overwrite=True)
        log_success(f"File uploaded to Azure Blob Storage: {blob_name}")
        print(f"File successfully uploaded: {blob_name}")
    except Exception as e:
        log_error(ERROR_CODES["upload_error"], f"Failed to upload {file_path} to Azure Blob Storage: {e}", script_name)
        print(f"Upload error: {e}")

# Normalize file name for comparison
def normalize_name(name):
    """Remove spaces, special characters, and normalize to lowercase."""
    return re.sub(r'[^a-zA-Z0-9]', '', name).lower().strip()

# Process Files
def process_files():
    blob_service_client = authenticate_azure()
    today_date = datetime.now().strftime("%m%d%Y")

    # List all files in the source folder
    all_files = [f for f in os.listdir(gdrive_source) if os.path.isfile(os.path.join(gdrive_source, f))]

    # Step 1: Process "Enrollments by Agent" file
    for file_name in all_files:
        normalized_file_name = normalize_name(file_name)
        if "enrollmentsbyagentagility" in normalized_file_name and "asof" in normalized_file_name:
            source_path = os.path.join(gdrive_source, file_name)
            renamed_file = f"raw_bob_uhc_aca_{today_date}.xlsx"
            target_path = os.path.join(open_enrollment_folder, renamed_file)

            # Ensure target directory exists
            if not os.path.exists(open_enrollment_folder):
                os.makedirs(open_enrollment_folder)

            # Move and rename the file
            shutil.move(source_path, target_path)
            log_success(f"Moved and renamed 'Enrollments by Agent' to {target_path}")
            print(f"File moved: {source_path} -> {target_path}")

            # Upload to Azure Blob Storage
            upload_to_azure(target_path, blob_folder, blob_service_client)
            continue  # Skip further processing for this file

    # Step 2: Delete "Enrollments by County" file
    for file_name in all_files:
        normalized_file_name = normalize_name(file_name)
        if "enrollmentsbycountyagility" in normalized_file_name and "asof" in normalized_file_name:
            file_path = os.path.join(gdrive_source, file_name)
            try:
                os.remove(file_path)
                log_success(f"Deleted 'Enrollments by County' file: {file_path}")
                print(f"Deleted file: {file_path}")
            except Exception as e:
                log_error(ERROR_CODES["file_error"], f"Failed to delete {file_path}: {e}", script_name)
                print(f"Error deleting file: {e}")

    log_final_entry("open_enrollment")

if __name__ == "__main__":
    process_files()
