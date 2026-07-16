import os
import shutil
from datetime import datetime, timezone
from azure.identity import ClientSecretCredential
from azure.storage.blob import BlobServiceClient
import paramiko
import pytz

# Define CST timezone
cst = pytz.timezone('America/Chicago')

# Get current date and time in CST
utc_now = datetime.now(timezone.utc)  # Get current UTC time
cst_now = utc_now.astimezone(cst)    # Convert to CST
cst_date = cst_now.date()            # Get the current date in CST
date_string = cst_now.strftime("%m%d%Y")  # mmddyyyy format
current_month_name = cst_now.strftime("%B")
current_month_short = cst_now.strftime("%b")
current_month_number = cst_now.strftime("%m")
current_year = cst_now.strftime("%Y")

# G-Drive and Blob folders
g_drive_base_folder_bob = r"G:\Shared drives\834 Labs\Data Ops Production Files\834labs raw files\production reports (BOB)"
blob_base_folder_bob = "raw/production_report"
g_drive_base_folder_acu = r"G:\Shared drives\834 Labs\Data Ops Production Files\834labs raw files\agent contract updates (acu)"
blob_base_folder_acu = "raw/agent_contract_update"

g_drive_folder_bob = os.path.join(g_drive_base_folder_bob, f"{current_month_number} {current_month_name} {current_year}")
g_drive_folder_acu = os.path.join(g_drive_base_folder_acu, f"{current_month_number} {current_month_name} {current_year}")

# Azure credentials
tenant_id = os.getenv("AZURE_TENANT_ID", "")
client_id = os.getenv("AZURE_CLIENT_ID", "")
client_secret = os.getenv("AZURE_CLIENT_SECRET", "")

# SFTP connection details
sftp_hostname = "egateway.hcsc.net"  # Replace with your SFTP hostname
sftp_username = os.getenv("HCSC_SFTP_USERNAME", "")
sftp_password = os.getenv("HCSC_SFTP_PASSWORD", "")  # Replace with your SFTP password
remote_path = f"/{sftp_username}/outbound"  # Replace with your remote directory path
local_download_path = 'C:\\Users\\myopsadmin\\Downloads'  # Replace with your local directory path

# Keywords for BOB and ACU
keywords = {
    "ACU": ["Producer Active Roster"],  # Replace with actual ACU keyword
    "BOB": ["Producer Book of Business"],  # Replace with actual BOB keyword
}

# Authenticate with Azure Blob Storage
try:
    azure_credential = ClientSecretCredential(
        tenant_id=tenant_id, client_id=client_id, client_secret=client_secret
    )
    storage_account_name = "834analyticsdatalake"
    account_url = f"https://{storage_account_name}.blob.core.windows.net"
    blob_service_client = BlobServiceClient(account_url=account_url, credential=azure_credential)
    print("Successfully authenticated to Azure Blob Storage.")
except Exception as e:
    print(f"Error authenticating to Azure Blob Storage: {e}")
    exit(1)

# Connect to SFTP server
try:
    transport = paramiko.Transport((sftp_hostname, 22))  # Ensure port is provided
    transport.connect(username=sftp_username, password=sftp_password)
    sftp = paramiko.SFTPClient.from_transport(transport)
    print("Connected to SFTP server.")

    # List files in the remote directory
    try:
        files = sftp.listdir_attr(remote_path)
    except FileNotFoundError:
        print(f"Error: Remote path '{remote_path}' not found.")
        exit(1)

    # Process BOB and ACU keywords
    for category, category_keywords in keywords.items():
        for keyword in category_keywords:
            filtered_files = []
            for file in files:
                # Convert SFTP file modification time to CST
                file_mod_time_utc = datetime.fromtimestamp(file.st_mtime, tz=timezone.utc)
                file_mod_time_cst = file_mod_time_utc.astimezone(cst)

                # Check if the file matches the keyword and was modified today in CST
                if keyword.lower() in file.filename.lower() and file_mod_time_cst.date() == cst_date:
                    filtered_files.append((file.filename, file_mod_time_cst))

            # If matching files are found, download the latest
            if filtered_files:
                filtered_files.sort(key=lambda x: x[1], reverse=True)  # Sort by modification time
                latest_file = filtered_files[0]  # Get the latest file
                file_name, file_mod_time = latest_file
                remote_file = f"{remote_path}/{file_name}"

                # Rename the file based on category and date
                if category == "ACU":
                    new_file_name = f"raw_acu_hcsc_mdc_{date_string}.csv"
                elif category == "BOB":
                    new_file_name = f"raw_bob_hcsc_aca_{date_string}.xlsx"
                else:
                    new_file_name = file_name  # Fallback to original name if category doesn't match

                local_file = f"{local_download_path}/{new_file_name}"

                print(f"Downloading {file_name} (Last Modified: {file_mod_time}) as {new_file_name} for category '{category}'...")
                sftp.get(remote_file, local_file)
                print(f"Downloaded {file_name} to {local_file}")

                # Move to G-Drive
                target_g_drive_folder = g_drive_folder_acu if category == "ACU" else g_drive_folder_bob
                if not os.path.exists(target_g_drive_folder):
                    os.makedirs(target_g_drive_folder)
                g_drive_target_path = os.path.join(target_g_drive_folder, new_file_name)

                shutil.move(local_file, g_drive_target_path)
                print(f"Moved file to G-Drive: {g_drive_target_path}")

                # Upload to Blob Storage
                if category == "ACU":
                    blob_folder = f"{blob_base_folder_acu}/{current_month_number} {current_month_short} {current_year}/"
                elif category == "BOB":
                    blob_folder = f"{blob_base_folder_bob}/{current_year} {current_month_number} {current_month_short}/"

                blob_client = blob_service_client.get_blob_client(container="834analytics-dev", blob=f"{blob_folder}{new_file_name}")
                with open(g_drive_target_path, "rb") as file_data:
                    blob_client.upload_blob(file_data, overwrite=True)
                print(f"Uploaded {new_file_name} to Blob Storage at {blob_folder}{new_file_name}")
            else:
                print(f"No files found for category '{category}' and keyword '{keyword}' matching today's date.")

    # Close the SFTP connection
    sftp.close()
    transport.close()
    print("SFTP connection closed.")

except Exception as e:
    print(f"Error: {e}")
