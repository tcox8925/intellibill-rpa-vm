import os
import paramiko
import shutil

# === GDrive path ===
gdrive_path = r'G:\Shared drives\834 Labs\#0 - Product Enablement\Data Analytics Projects\AmeriLife Files\Commissions - SFTP'
archive_path = os.path.join(gdrive_path, "archive")
os.makedirs(archive_path, exist_ok=True)

# === SFTP connection details ===
sftp_hostname = "ftp.amerilife.com"
sftp_port = 22
sftp_username = os.getenv("AMERILIFE_SFTP_USERNAME", "")
sftp_password = os.getenv("AMERILIFE_SFTP_PASSWORD", "")
remote_path = "/inbound"  # Remote path on SFTP

# --- allowed extensions ---
ALLOWED_EXTENSIONS = {".xls", ".xlsx", ".csv"}

def is_allowed_file(filename):
    """Check if file has an allowed extension."""
    return os.path.splitext(filename)[1].lower() in ALLOWED_EXTENSIONS

try:
    # === Connect to SFTP ===
    transport = paramiko.Transport((sftp_hostname, sftp_port))
    transport.connect(username=sftp_username, password=sftp_password)
    sftp = paramiko.SFTPClient.from_transport(transport)
    print("Connected to SFTP server.")

    # Ensure remote directory exists
    try:
        sftp.chdir(remote_path)
    except IOError:
        print(f"Remote folder '{remote_path}' not found. Creating it.")
        sftp.mkdir(remote_path)
        sftp.chdir(remote_path)

    # === Scan GDrive for allowed files ===
    all_files = os.listdir(gdrive_path)
    upload_files = [
        f for f in all_files
        if os.path.isfile(os.path.join(gdrive_path, f))
        and is_allowed_file(f)
    ]

    if not upload_files:
        print("No XLS/XLSX/CSV files found in GDrive.")
    else:
        print(f"Found {len(upload_files)} file(s) to upload from GDrive.")

        for file_name in upload_files:
            local_file_path = os.path.join(gdrive_path, file_name)
            remote_file_path = f"{remote_path}/{file_name}".replace("\\", "/")
            archive_file_path = os.path.join(archive_path, file_name)

            print(f"Uploading '{file_name}' to SFTP...")
            sftp.put(local_file_path, remote_file_path)
            print(f"Uploaded: {file_name}")

            # === Move to archive ===
            shutil.move(local_file_path, archive_file_path)
            print(f"Archived: {archive_file_path}")

    # === Close connection ===
    sftp.close()
    transport.close()
    print("SFTP connection closed.")

except Exception as e:
    print(f"Error: {e}")