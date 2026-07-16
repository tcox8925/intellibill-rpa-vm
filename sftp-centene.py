import os
from datetime import datetime, timedelta
import paramiko
from logger import setup_logger, log_error, log_success, log_final_entry, ERROR_CODES, init_log_entry, update_log_extra_fields
import shutil
from azure.identity import DefaultAzureCredential
from azure.keyvault.secrets import SecretClient

KEYVAULT_URI = os.getenv("KEYVAULT_URL", "")

def get_secret(secret_name):
    credential = DefaultAzureCredential()
    client = SecretClient(vault_url=KEYVAULT_URI, credential=credential)
    return client.get_secret(secret_name).value

# === Current date ===
today = datetime.now()
date_string = today.strftime("%m%d%Y")

# === Local download path ===
local_download_path = 'C:\\Users\\myopsadmin\\Downloads'  # Update as needed

gdrive_path = r'G:\Shared drives\834 Labs\Data Ops Production Files\834labs pch files\#0 - PCH IPA'
subfolder_name = today.strftime("%m %b %Y")
final_path = os.path.join(gdrive_path, subfolder_name, "centene")
os.makedirs(final_path, exist_ok=True)

# === SFTP connection details (MoveIt) ===
sftp_hostname = "sftp.centene.com"
sftp_port = 22
sftp_username = os.getenv("CENTENE_SFTP_USERNAME", "")
sftp_password = get_secret("centene-stp")
remote_path = "/FromCentene"  

script_name_logged = setup_logger("sftp-centene")
init_log_entry(script_name_logged)

# === Connect to SFTP and download test document ===
try:
    transport = paramiko.Transport((sftp_hostname, sftp_port))
    transport.connect(username=sftp_username, password=sftp_password)
    sftp = paramiko.SFTPClient.from_transport(transport)
    print("Connected to SFTP server.")

    # List files in the remote directory
    files = sftp.listdir_attr(remote_path)

    if files:
        print(f"Found {len(files)} file(s) in the remote directory. Starting download...")

        for file in files:
            file_name = file.filename
            remote_file_path = f"{remote_path}/{file_name}"
            local_file_path = os.path.join(local_download_path, file_name)

            print(f"Downloading '{file_name}' to '{local_file_path}'...")
            sftp.get(remote_file_path, local_file_path)
            print(f"Downloaded: {local_file_path}")

            # === Move to G-Drive ===
            gdrive_file_path = os.path.join(final_path, file_name)
            shutil.move(local_file_path, gdrive_file_path)
            print(f"Moved '{file_name}' to G-Drive: {gdrive_file_path}")

        print("All files downloaded and moved successfully.")
        log_success()
        update_log_extra_fields(script_name_logged, file_status="Ready")
    else:
        print("No files found in the remote directory.")
        log_error(ERROR_CODES["download_error"], "No files were found in the directory.", script_name_logged)

    # Close connection
    sftp.close()
    transport.close()
    print("SFTP connection closed.")

except Exception as e:
    print(f"Error: {e}")
    log_error(ERROR_CODES["general_error"], "", script_name_logged)



update_log_extra_fields(
            script_name_logged,
            file_path=final_path,
            file_report_month=datetime.now().strftime("%Y-%m-%d"),
            flow_id="0C10168A-BF96-4C20-8C57-8C3FC3282062",
            sub_entity_id="270681372001"
)

log_final_entry(script_name_logged)
