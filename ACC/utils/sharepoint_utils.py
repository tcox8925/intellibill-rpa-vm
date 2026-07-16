# ==========================================================
#  utils/sharepoint_utils.py
# ==========================================================
"""
sharepoint_utils.py
-------------------
Purpose:
    Minimal SharePoint interface for ACC RPA.

Responsibilities:
    • Authenticate to Microsoft Graph via service principal (Key Vault → MSAL)
    • List folders or files under /ACC
    • Download and upload files
    • Provide lightweight lookup helpers

Notes:
    • No business logic (e.g., moving to archive, naming rules, or folder patterns)
    • No database or matrix interaction
    • All folder structure decisions happen in handlers
"""

import os
import requests
from msal import ConfidentialClientApplication
from azure.identity import DefaultAzureCredential
from azure.keyvault.secrets import SecretClient
import re

# ==========================================================
#  CONFIGURATION
# ==========================================================
KEY_VAULT_URL = os.getenv("KEYVAULT_URL", "")
SITE_HOSTNAME = "agilityins.sharepoint.com"
SITE_PATH = "/sites/834Labs"
GRAPH_API = "https://graph.microsoft.com/v1.0"
GRAPH_SCOPE = ["https://graph.microsoft.com/.default"]
SITE_ID = "agilityins.sharepoint.com,890e2a6f-8064-4bc7-b893-04f95b9564ad,c074cd4a-cfe6-44ec-9381-be3366202a83"


# ==========================================================
#  AUTHENTICATION
# ==========================================================
def get_sharepoint_token() -> str:
    """
    Authenticate to Microsoft Graph using service principal credentials stored in Key Vault.

    Returns
    -------
    str : Bearer access token
    """
    try:
        credential = DefaultAzureCredential()
        client = SecretClient(vault_url=KEY_VAULT_URL, credential=credential)

        tenant_id = client.get_secret(os.getenv("KEYVAULT_TENANT_ID_SECRET_NAME", "")).value
        client_id = client.get_secret(os.getenv("KEYVAULT_CLIENT_ID_SECRET_NAME", "")).value
        client_secret = client.get_secret(os.getenv("KEYVAULT_CLIENT_SECRET_NAME", "")).value

        authority = f"https://login.microsoftonline.com/{tenant_id}"
        app = ConfidentialClientApplication(
            client_id, authority=authority, client_credential=client_secret
        )

        result = app.acquire_token_for_client(scopes=GRAPH_SCOPE)
        if "access_token" not in result:
            raise Exception(f"Graph token request failed: {result.get('error_description')}")

        print("🔐 SharePoint authenticated via service principal.")
        return result["access_token"]

    except Exception as e:
        raise Exception(f"❌ Failed to authenticate with SharePoint: {e}")


# ==========================================================
#  FOLDER AND FILE LISTING
# ==========================================================
def list_folders(access_token: str, folder_path: str = "ACC"):
    """
    List all subfolders inside the specified path.

    Parameters
    ----------
    access_token : str
        Graph API bearer token.
    folder_path : str
        Folder path under the drive root (default: "ACC").

    Returns
    -------
    list[dict] : Metadata of folders found.
    """
    headers = {"Authorization": f"Bearer {access_token}"}
    clean_path = folder_path.strip("/") if folder_path else ""

    if not clean_path:
        url = f"{GRAPH_API}/sites/{SITE_ID}/drive/root/children"
    else:
        url = f"{GRAPH_API}/sites/{SITE_ID}/drive/root:/{clean_path}:/children"

    r = requests.get(url, headers=headers)
    if r.status_code != 200:
        raise Exception(f"❌ Folder listing failed ({r.status_code}): {r.text}")

    data = r.json().get("value", [])
    folders = [i for i in data if i.get("folder")]
    print(f"📁 Found {len(folders)} folders in '{clean_path or 'root'}'")
    return folders


def list_folder_children(access_token: str, folder_path: str):
    """
    List all files/folders directly inside the specified folder.

    Parameters
    ----------
    access_token : str
        Graph API bearer token.
    folder_path : str
        Full path from root (e.g., "ACC/Cigna_ACA_Dental_16505368").

    Returns
    -------
    list[dict] : File and folder metadata under the given path.
    """
    headers = {"Authorization": f"Bearer {access_token}"}
    clean_path = folder_path.strip("/")
    url = f"{GRAPH_API}/sites/{SITE_ID}/drive/root:/{clean_path}:/children"

    r = requests.get(url, headers=headers)
    if r.status_code != 200:
        raise Exception(f"❌ list_folder_children failed ({r.status_code}): {r.text}")

    data = r.json().get("value", [])
    print(f"📂 Found {len(data)} items in '{clean_path}'")
    return data


# ==========================================================
#  FILE DOWNLOAD / UPLOAD
# ==========================================================
def download_file(access_token: str, item_id: str, local_path: str):
    """
    Download a file from SharePoint by item ID.

    Parameters
    ----------
    access_token : str
        Graph API bearer token.
    item_id : str
        File's unique item ID in SharePoint.
    local_path : str
        Local path to save the downloaded file.

    Returns
    -------
    str : Local file path of the downloaded file.
    """
    headers = {"Authorization": f"Bearer {access_token}"}
    url = f"{GRAPH_API}/sites/{SITE_ID}/drive/items/{item_id}/content"

    r = requests.get(url, headers=headers, stream=True)
    if r.status_code != 200:
        raise Exception(f"❌ Download failed ({r.status_code}): {r.text}")

    with open(local_path, "wb") as f:
        for chunk in r.iter_content(chunk_size=8192):
            if chunk:
                f.write(chunk)

    print(f"📥 Downloaded {os.path.basename(local_path)} → {local_path}")
    return local_path


def upload_file(access_token: str, folder_path: str, local_path: str):
    """
    Upload a local file to a specified SharePoint folder.

    Parameters
    ----------
    access_token : str
        Graph API bearer token.
    folder_path : str
        Path in SharePoint under /sites/834Labs (e.g., "/ACC/Cigna/").
    local_path : str
        Full local file path to upload.

    Returns
    -------
    dict : Upload response metadata.
    """
    file_name = os.path.basename(local_path)
    url = f"{GRAPH_API}/sites/{SITE_HOSTNAME}:{SITE_PATH}:{folder_path}/{file_name}:/content"
    headers = {"Authorization": f"Bearer {access_token}"}

    with open(local_path, "rb") as f:
        data = f.read()

    resp = requests.put(url, headers=headers, data=data)
    if resp.status_code not in (200, 201):
        raise Exception(f"❌ Upload failed ({resp.status_code}): {resp.text}")

    print(f"📤 Uploaded {file_name} to {folder_path}")
    return resp.json()


# ==========================================================
#  FILE LOOKUP HELPERS
# ==========================================================
def find_item_by_name(items: list, filename_pattern: str):
    """
    Find the first item whose name matches the regex pattern.

    Parameters
    ----------
    items : list[dict]
        List of SharePoint item metadata.
    filename_pattern : str
        Regex pattern to match file names.

    Returns
    -------
    dict or None
        Matching item if found, otherwise None.
    """
    for item in items:
        name = item.get("name", "")
        if re.search(filename_pattern, name, re.IGNORECASE):
            return item
    return None


def list_accessible_sites(access_token: str, top: int = 50):
    """
    List all SharePoint sites accessible to the service principal.
    Used for diagnostics only.

    Parameters
    ----------
    access_token : str
        Graph API bearer token.
    top : int
        Maximum number of sites to list.

    Returns
    -------
    list[dict] : Site metadata.
    """
    url = f"{GRAPH_API}/sites?search=*"
    headers = {"Authorization": f"Bearer {access_token}"}

    sites = []
    while url:
        r = requests.get(url, headers=headers)
        if r.status_code != 200:
            raise Exception(f"❌ Failed to list sites ({r.status_code}): {r.text}")

        data = r.json()
        sites.extend(data.get("value", []))
        url = data.get("@odata.nextLink")

        if len(sites) >= top:
            break

    print(f"🌐 Found {len(sites)} accessible sites:")
    for s in sites:
        print(f"• {s.get('name', ''):<25} | {s.get('id', '')} | {s.get('webUrl', '')}")

    return sites
