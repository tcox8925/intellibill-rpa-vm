import os
import shutil
from azure.storage.blob import BlobServiceClient
from azure.identity import ClientSecretCredential
from logger import setup_logger, log_success, log_error, log_final_entry, init_log_entry, update_log_extra_fields  #Logging module
import requests

# **CONFIGURATION**
SCRIPT_NAME = setup_logger("GDrive_to_Azure_Move")  #Set up logging
init_log_entry(SCRIPT_NAME)
update_log_extra_fields(
    SCRIPT_NAME,
    flow_id="901CBA9F-F6FD-4F0A-B0E6-35628C3F26B3",
    sub_entity_id="270681372001"
)

# **Local G-Drive Folder (Static Path)**
G_DRIVE_FOLDER = r"G:\My Drive\kelsey"

# **Azure Blob Storage Configuration**
BLOB_CONTAINER_NAME = "834analytics-sftp-kelsey"
BLOB_PATH = "outbound/"

# **Local Temporary Storage Before Uploading**
LOCAL_TEMP_PATH = "C:\\temp\\gdrive_files"
os.makedirs(LOCAL_TEMP_PATH, exist_ok=True)  # Ensure temp directory exists

# **Azure Credentials**
AZURE_TENANT_ID = os.getenv("AZURE_TENANT_ID", "")
AZURE_CLIENT_ID = os.getenv("AZURE_CLIENT_ID", "")
AZURE_CLIENT_SECRET = os.getenv("AZURE_CLIENT_SECRET", "")
AZURE_STORAGE_ACCOUNT = "834analyticsdatalake"

POWER_AUTOMATE_URL = "https://prod-96.westus.logic.azure.com:443/workflows/3aade458f999406896f3adacc0f2b38a/triggers/manual/paths/invoke?api-version=2016-06-01&sp=%2Ftriggers%2Fmanual%2Frun&sv=1.0&sig=2Qy_eMiIAq0mUNcM3fep-XU6M-sIFxp3L2VvnLFDNFk"

def clean_kelsey_folder(folder_path):
    if os.path.exists(folder_path):
        for file in os.listdir(folder_path):
            file_path = os.path.join(folder_path, file)
            try:
                os.remove(file_path)
                print(f"Deleted file: {file_path}")
            except Exception as e:
                print(f"Error deleting file {file_path}: {e}")
    else:
        print(f"Folder {folder_path} does not exist.")

#**Authenticate Azure Blob Storage**
def authenticate_blob_storage():
    try:
        credential = ClientSecretCredential(
            tenant_id=AZURE_TENANT_ID,
            client_id=AZURE_CLIENT_ID,
            client_secret=AZURE_CLIENT_SECRET
        )
        account_url = f"https://{AZURE_STORAGE_ACCOUNT}.blob.core.windows.net"
        blob_service_client = BlobServiceClient(account_url=account_url, credential=credential)
        print("Successfully authenticated to Azure Blob Storage.")
        return blob_service_client
    except Exception as e:
        log_error("E_BLOB_AUTH", f"Azure authentication failed: {e}", SCRIPT_NAME)
        return None


#**Function to List Files in G-Drive (Excluding System Files)**
def list_gdrive_files(local_folder):
    if not os.path.exists(local_folder):
        log_error("E_FILE_NOT_FOUND", f"G-Drive folder does not exist: {local_folder}", SCRIPT_NAME)
        return []

    allowed_extensions = (".xls", ".xlsx", ".csv")  # Only allow these file types

    return [
        f for f in os.listdir(local_folder)
        if os.path.isfile(os.path.join(local_folder, f))
        and f.lower().endswith(allowed_extensions)  # Filter by extension
        and f.lower() not in ["desktop.ini", "thumbs.db"]
    ]


#**Function to Move Files from G-Drive to Temp Folder**
def move_files_to_temp(gdrive_folder, temp_folder):
    files = list_gdrive_files(gdrive_folder)

    if not files:
        print("No files found in G-Drive folder.")
        return []

    moved_files = []
    for file_name in files:
        src_path = os.path.join(gdrive_folder, file_name)
        dest_path = os.path.join(temp_folder, file_name)

        try:
            shutil.move(src_path, dest_path)  # Copy file to avoid accidental loss
            print(f"Copied {file_name} to {dest_path}")
            moved_files.append(dest_path)
        except Exception as e:
            log_error("E_FILE_MOVE_ERROR", f"Failed to move {file_name}: {e}", SCRIPT_NAME)

    return moved_files

def notify_power_automate(status, message, files=None, paths=None):
    payload = {
        "status": status,
        "message": message,
        "files": files or [],
        "paths": paths or []
    }

    try:
        response = requests.post(POWER_AUTOMATE_URL, json=payload)
        if response.status_code == 200:
            print("Notification sent to Power Automate.")
        else:
            print(f"Failed to notify Power Automate. Status code: {response.status_code}")
    except Exception as e:
        print(f"Notification error: {e}")


#**Function to Upload Files to Azure Blob Storage**
def upload_files_to_blob(file_paths):
    blob_service_client = authenticate_blob_storage()
    if not blob_service_client:
        notify_power_automate("Error", "Files not uploaded!")
        return

    uploaded_files = []
    uploaded_paths = []

    for file_path in file_paths:
        file_name = os.path.basename(file_path)
        blob_file_path = f"{BLOB_PATH}{file_name}"
        blob_client = blob_service_client.get_blob_client(container=BLOB_CONTAINER_NAME, blob=blob_file_path)

        try:
            with open(file_path, "rb") as data:
                blob_client.upload_blob(data, overwrite=True)
            os.remove(file_path)

            uploaded_files.append(file_name)
            uploaded_paths.append(f"sftp://{BLOB_CONTAINER_NAME}/{blob_file_path}")
        except Exception as e:
            log_error("E_BLOB_UPLOAD_ERROR", f"Upload failed for {file_name}: {e}", SCRIPT_NAME)

    if uploaded_files:
        notify_power_automate("Success", "Files uploaded successfully.", uploaded_files, uploaded_paths)
    else:
        notify_power_automate("Error", "Files not uploaded!")



#**Main Function**
def move_gdrive_to_blob():
    print(f"Checking G-Drive folder: {G_DRIVE_FOLDER}...")

    # **Step 1: Move Files from G-Drive to Local Temp**
    temp_files = move_files_to_temp(G_DRIVE_FOLDER, LOCAL_TEMP_PATH)

    if not temp_files:
        print("No files moved to temp folder. Exiting.")
        return

    # **Step 2: Upload to Azure Blob Storage**
    upload_files_to_blob(temp_files)

    log_success()
    log_final_entry(SCRIPT_NAME)
    
    print("All files processed successfully!")


#**Run the script**
if __name__ == "__main__":
    move_gdrive_to_blob()
    clean_kelsey_folder(G_DRIVE_FOLDER)
