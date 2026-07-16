import os
from datetime import datetime
import pytz
import paramiko
import requests

from azure.identity import DefaultAzureCredential, ClientSecretCredential
from azure.keyvault.secrets import SecretClient
from azure.storage.blob import BlobServiceClient

from logger import (
    setup_logger,
    log_error,
    log_success,
    log_final_entry,
    ERROR_CODES,
    init_log_entry,
    update_log_extra_fields,
)
from email_utils import get_email_recipients


# =========================================================
# CONFIG
# =========================================================
process_type = "SFTP (Centene)"
recipients = get_email_recipients(process_type)
to_emails = recipients.get("to", [])
cc_emails = recipients.get("cc", [])

# === Power Automate flow URL ===
flow_url = "https://prod-134.westus.logic.azure.com:443/workflows/35d08f2aed194585ba6fc27b6bd397a6/triggers/manual/paths/invoke?api-version=2016-06-01&sp=%2Ftriggers%2Fmanual%2Frun&sv=1.0&sig=XC7OTK9cHS6IcvQioYnUWb1gVPwo4uQfG3Mg02zVDb0"

# === Timezone setup ===
cst = pytz.timezone("America/Chicago")
cst_now = datetime.now(cst)

# === Local download path ===
local_download_path = r"C:\Users\myopsadmin\Downloads"
os.makedirs(local_download_path, exist_ok=True)

# === Key Vault ===
KEY_VAULT_NAME = os.getenv("KEY_VAULT_NAME", "")
KEY_VAULT_URL = os.getenv("KEYVAULT_URL", "")
CENTENE_SFTP_SECRET_NAME = "centene-stp"

# === SFTP connection details ===
sftp_hostname = "sftp.centene.com"
sftp_port = 22
sftp_username = os.getenv("CENTENE_SFTP_USERNAME", "")
remote_path = "/FromCentene"

# === Blob Storage ===
BLOB_ACCOUNT_URL = "https://834analyticsdatalake.blob.core.windows.net"
BLOB_CONTAINER_NAME = "834analytics-dev"
BLOB_BASE_PREFIX = "raw/centene_pch"

# Example: "02 February 2026"
blob_month_folder = cst_now.strftime("%m %B %Y")


# =========================================================
# HELPERS
# =========================================================
def notify_power_automate(status, code="", message=""):
    payload = {
        "status": status,
        "code": code,
        "message": message,
        "to": to_emails or [],
        "cc": cc_emails or [],
    }
    headers = {"Content-Type": "application/json"}

    try:
        response = requests.post(flow_url, json=payload, headers=headers, timeout=30)
        print(f"Power Automate notified: {response.status_code}")
    except Exception as e:
        print(f"Failed to notify Power Automate: {e}")


def get_secret(secret_name: str) -> str:
    credential = DefaultAzureCredential()
    secret_client = SecretClient(vault_url=KEY_VAULT_URL, credential=credential)
    return secret_client.get_secret(secret_name).value


def get_blob_service_client() -> BlobServiceClient:
    """Authenticate to Blob via SynapseAccess SP (same pattern as blob_utils.py)."""
    kv_client = SecretClient(
        vault_url=KEY_VAULT_URL,
        credential=DefaultAzureCredential(),
    )
    credential = ClientSecretCredential(
        tenant_id=kv_client.get_secret(os.getenv("KEYVAULT_TENANT_ID_SECRET_NAME", "")).value,
        client_id=kv_client.get_secret(os.getenv("KEYVAULT_CLIENT_ID_SECRET_NAME", "")).value,
        client_secret=kv_client.get_secret(os.getenv("KEYVAULT_CLIENT_SECRET_NAME", "")).value,
    )
    return BlobServiceClient(account_url=BLOB_ACCOUNT_URL, credential=credential)


def upload_file_to_blob(local_file_path: str, file_name: str) -> str:
    """
    Uploads file to:
      raw/centene_pch/02 February 2026/<filename>

    Returns the blob path within the container.
    """
    blob_service_client = get_blob_service_client()
    container_client = blob_service_client.get_container_client(BLOB_CONTAINER_NAME)

    blob_name = f"{BLOB_BASE_PREFIX}/{blob_month_folder}/{file_name}"
    blob_client = container_client.get_blob_client(blob_name)

    with open(local_file_path, "rb") as data:
        blob_client.upload_blob(data, overwrite=True)

    return blob_name


# =========================================================
# MAIN
# =========================================================
script_name_logged = setup_logger("sftp-centene")
init_log_entry(script_name_logged)

files_downloaded = 0
uploaded_blob_paths = []

try:
    # Pull SFTP password from Key Vault
    sftp_password = get_secret("centene-stp")

    # Connect to SFTP
    transport = paramiko.Transport((sftp_hostname, sftp_port))
    transport.connect(username=sftp_username, password=sftp_password)
    sftp = paramiko.SFTPClient.from_transport(transport)
    print("Connected to SFTP server.")

    files = sftp.listdir_attr(remote_path)

    if files:
        print(
            f"Found {len(files)} file(s) in the remote directory. "
            f"Checking for files modified today (CST)..."
        )

        for file in files:
            file_name = file.filename

            # Convert remote modification time (UTC) to CST
            file_mod_utc = datetime.utcfromtimestamp(file.st_mtime).replace(tzinfo=pytz.utc)
            file_mod_cst = file_mod_utc.astimezone(cst)

            # Only process files modified today in CST
            if file_mod_cst.date() != cst_now.date():
                continue

            remote_file_path = f"{remote_path}/{file_name}"
            local_file_path = os.path.join(local_download_path, file_name)

            print(
                f"Downloading '{file_name}' modified on "
                f"{file_mod_cst.strftime('%Y-%m-%d %H:%M:%S %Z')}..."
            )

            # 1. Download from SFTP to local
            sftp.get(remote_file_path, local_file_path)
            print(f"Downloaded: {local_file_path}")

            try:
                # 2. Upload to Blob
                blob_name = upload_file_to_blob(local_file_path, file_name)
                uploaded_blob_paths.append(blob_name)
                print(f"Uploaded '{file_name}' to blob: {BLOB_CONTAINER_NAME}/{blob_name}")

                # 3. Delete local file after successful upload
                if os.path.exists(local_file_path):
                    os.remove(local_file_path)
                    print(f"Deleted local file: {local_file_path}")

                # 4. Delete from SFTP after successful upload
                sftp.remove(remote_file_path)
                print(f"Deleted '{file_name}' from SFTP.")

                files_downloaded += 1

            except Exception as upload_err:
                print(f"Upload failed for '{file_name}': {upload_err}")

                # Clean up local file if upload failed partially / local file still exists
                if os.path.exists(local_file_path):
                    try:
                        os.remove(local_file_path)
                        print(f"Deleted local file after failed upload: {local_file_path}")
                    except Exception as cleanup_err:
                        print(f"Failed to delete local file after upload failure: {cleanup_err}")

                raise

        if files_downloaded == 0:
            print("No files were modified today (CST) in the remote directory.")
            log_error(
                ERROR_CODES["download_error"],
                "No files modified today found in the directory.",
                script_name_logged,
            )

            notify_power_automate(
                status="error",
                code="E004",
                message="No files were found in the SFTP directory modified today (CST).",
            )
        else:
            print(f"Uploaded {files_downloaded} file(s) modified today successfully.")
            log_success()
            update_log_extra_fields(script_name_logged, file_status="Ready")

            notify_power_automate(
                status="success",
                message="Files downloaded from SFTP, uploaded to Blob Storage, deleted locally, and removed from SFTP successfully.",
            )
    else:
        print("No files found in the remote directory.")
        log_error(
            ERROR_CODES["download_error"],
            "No files were found in the directory.",
            script_name_logged,
        )

        notify_power_automate(
            status="error",
            code="E004",
            message="No files were found in the SFTP directory.",
        )

    sftp.close()
    transport.close()
    print("SFTP connection closed.")

except Exception as e:
    print(f"Error: {e}")
    log_error(ERROR_CODES["general_error"], str(e), script_name_logged)

    notify_power_automate(
        status="error",
        code="E001",
        message=f"SFTP script failed with error: {str(e)}",
    )

update_log_extra_fields(
    script_name_logged,
    file_path=f"{BLOB_CONTAINER_NAME}/{BLOB_BASE_PREFIX}/{blob_month_folder}",
    file_report_month=cst_now.strftime("%Y-%m-%d"),
    flow_id="0C10168A-BF96-4C20-8C57-8C3FC3282062",
)

log_final_entry(script_name_logged)