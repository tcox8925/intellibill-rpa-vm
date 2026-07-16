# ==========================================================
#  utils/drive_utils.py
# ==========================================================
"""
drive_utils.py
--------------
Purpose:
    Authenticate to Google Drive via service account credentials
    stored securely in Azure Key Vault, and provide minimal,
    reusable helpers for folder and file operations.

Notes:
    - No matrix, DB, or carrier-specific logic here.
    - Folder structures (E&O, Contracting, etc.) handled in handlers.
    - Can be imported safely across modules.
"""

import os
import re
import json
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from azure.identity import DefaultAzureCredential
from azure.keyvault.secrets import SecretClient

# ==========================================================
#  CONFIGURATION
# ==========================================================
KEY_VAULT_NAME = os.getenv("KEY_VAULT_NAME", "")
SERVICE_ACCOUNT_SECRET_NAME = "gdrive-api-access"
DELEGATED_USER = "dataops@834labs.com"
SCOPES = ["https://www.googleapis.com/auth/drive"]


# ==========================================================
#  AUTHENTICATION
# ==========================================================
def get_drive_service():
    """
    Authenticate to Google Drive using service account JSON stored in Key Vault.

    Returns
    -------
    service : googleapiclient.discovery.Resource
        Authenticated Google Drive service client.
    """
    try:
        kv_url = f"https://{KEY_VAULT_NAME}.vault.azure.net/"
        credential = DefaultAzureCredential()
        secret_client = SecretClient(vault_url=kv_url, credential=credential)

        secret_value = secret_client.get_secret(SERVICE_ACCOUNT_SECRET_NAME).value
        service_account_info = json.loads(secret_value)

        creds = service_account.Credentials.from_service_account_info(
            service_account_info, scopes=SCOPES
        ).with_subject(DELEGATED_USER)

        service = build("drive", "v3", credentials=creds)
        print(f"✅ Authenticated to Google Drive as {DELEGATED_USER}")
        return service

    except Exception as e:
        raise Exception(f"❌ Failed to authenticate with Google Drive: {e}")


# ==========================================================
#  HELPERS
# ==========================================================
def extract_folder_id(drive_url: str) -> str:
    """
    Extract the folder ID from a Google Drive folder URL.

    Parameters
    ----------
    drive_url : str
        Google Drive folder URL.

    Returns
    -------
    str or None
        Folder ID if found, otherwise None.
    """
    match = re.search(r"/folders/([a-zA-Z0-9_-]+)", drive_url)
    return match.group(1) if match else None


def find_or_create_subfolder(service, parent_id: str, name: str) -> str:
    """
    Check if a subfolder exists under parent_id, otherwise create it.

    Parameters
    ----------
    service : googleapiclient.discovery.Resource
        Authenticated Drive service client.
    parent_id : str
        ID of the parent folder.
    name : str
        Name of the subfolder to find or create.

    Returns
    -------
    str
        Folder ID.
    """
    try:
        query = (
            f"'{parent_id}' in parents and "
            f"name = '{name}' and "
            "mimeType = 'application/vnd.google-apps.folder' and trashed = false"
        )

        results = service.files().list(
            q=query,
            fields="files(id, name)",
            supportsAllDrives=True,
            includeItemsFromAllDrives=True,
        ).execute()

        items = results.get("files", [])
        if items:
            folder_id = items[0]["id"]
            print(f"📂 Found existing subfolder: {name} ({folder_id})")
            return folder_id

        print(f"📁 Creating subfolder: {name}")
        file_metadata = {
            "name": name,
            "mimeType": "application/vnd.google-apps.folder",
            "parents": [parent_id],
        }

        folder = service.files().create(
            body=file_metadata,
            fields="id",
            supportsAllDrives=True,
        ).execute()

        folder_id = folder.get("id")
        print(f"✅ Created subfolder: {name} ({folder_id})")
        return folder_id

    except Exception as e:
        raise Exception(f"❌ Failed to find/create subfolder '{name}': {e}")


def upload_file(service, folder_id: str, local_file_path: str) -> str:
    """
    Upload a file to the specified Google Drive folder.

    Parameters
    ----------
    service : googleapiclient.discovery.Resource
        Authenticated Drive service client.
    folder_id : str
        ID of the destination folder.
    local_file_path : str
        Full path of the local file to upload.

    Returns
    -------
    str or None
        WebViewLink of uploaded file, or None if failed.
    """
    try:
        if not os.path.exists(local_file_path):
            print(f"⚠️ File not found: {local_file_path}")
            return None

        file_name = os.path.basename(local_file_path)
        file_metadata = {"name": file_name, "parents": [folder_id]}
        media = MediaFileUpload(local_file_path, resumable=True)

        print(f"📤 Uploading {file_name} to Drive folder {folder_id}...")
        uploaded = service.files().create(
            body=file_metadata,
            media_body=media,
            fields="id, webViewLink",
            supportsAllDrives=True,
        ).execute()

        link = uploaded.get("webViewLink")
        print(f"✅ Uploaded {file_name} → {link}")
        return link

    except Exception as e:
        print(f"❌ Upload failed for {local_file_path}: {e}")
        return None

# ==========================================================
#  LISTING / DOWNLOAD HELPERS
# ==========================================================
def list_all_files_recursive(folder_id: str) -> list:
    """
    Recursively list all files/subfolders under a given Drive folder ID.
    Returns a flat list of dicts: {id, name, mimeType, modifiedTime, parents}.
    """
    try:
        service = get_drive_service()
        results = []
        queue = [folder_id]

        while queue:
            current = queue.pop(0)
            query = f"'{current}' in parents and trashed = false"
            page_token = None

            while True:
                resp = service.files().list(
                    q=query,
                    fields="nextPageToken, files(id, name, mimeType, modifiedTime, parents)",
                    supportsAllDrives=True,
                    includeItemsFromAllDrives=True,
                    pageToken=page_token,
                ).execute()
                if resp is not None:
                    for f in resp.get("files", []):
                        results.append(f)
                        if f["mimeType"] == "application/vnd.google-apps.folder":
                            queue.append(f["id"])

                    page_token = resp.get("nextPageToken")
                    if not page_token:
                        break

        print(f"📂 Recursively found {len(results)} file(s)/folder(s) under {folder_id}")
        return results

    except Exception as e:
        raise Exception(f"❌ Drive listing failed: {e}")


def download_file(file_id: str, local_path: str) -> str:
    """
    Download a Google Drive file by ID to a local path.
    """
    try:
        from googleapiclient.http import MediaIoBaseDownload
        import io

        service = get_drive_service()
        request = service.files().get_media(fileId=file_id, supportsAllDrives=True)
        fh = io.FileIO(local_path, "wb")
        downloader = MediaIoBaseDownload(fh, request)
        done = False
        while not done:
            status, done = downloader.next_chunk()
            if status:
                print(f"⬇️  Downloading {os.path.basename(local_path)}: {int(status.progress() * 100)}%")
        print(f"✅ Download complete → {local_path}")
        return local_path

    except Exception as e:
        raise Exception(f"❌ Download failed for file {file_id}: {e}")
