import os
from datetime import datetime
import paramiko
from logger import setup_logger, log_error, log_success, log_final_entry, ERROR_CODES, init_log_entry,update_log_extra_fields
import shutil

# === Current date ===
today = datetime.now()
date_string = today.strftime("%m%d%Y")

# === Local download path ===
local_download_path = 'C:\\Users\\myopsadmin\\Downloads'  # Update as needed
gdrive_path = r'G:\Shared drives\Data Analytics\Data Ops Production Files\834labs sftp files\wellsense'

# === SFTP connection details (MoveIt) ===
sftp_hostname = "MoveIt.bmchp.org"
sftp_port = 22
sftp_username = os.getenv("WELLSENSE_SFTP_USERNAME", "")
sftp_password = os.getenv("WELLSENSE_SFTP_PASSWORD", "")
remote_path = "/FROM WELLSENSE"  # Root directory or specific folder, adjust if needed

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

            gdrive_file_path = os.path.join(gdrive_path, file_name)
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
            file_path=gdrive_path,
            file_report_month=datetime.now().strftime("%Y-%m-%d"),
            flow_id="0C10168A-BF96-4C20-8C57-8C3FC3282062",
            sub_entity_id="270681372001"
)

log_final_entry(script_name_logged)
