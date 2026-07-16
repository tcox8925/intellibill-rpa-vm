# ==========================================================
#  utils/azure_blob_utils.py
# ==========================================================
"""
azure_blob_utils.py
-------------------
Purpose:
    - Authenticate to Azure Blob Storage using Key Vault secrets.
    - Return a ready-to-use BlobServiceClient.
    - No upload, download, or file handling logic here.
"""

from azure.identity import DefaultAzureCredential, ClientSecretCredential
from azure.keyvault.secrets import SecretClient
from azure.storage.blob import BlobServiceClient
import os

# ==========================================================
#  AUTHENTICATION
# ==========================================================
def authenticate_blob_storage(
    keyvault_name: str = os.getenv("KEY_VAULT_NAME", ""),
    storage_account_name: str = "834analyticsdatalake",
    client_id_key: str = os.getenv("KEYVAULT_CLIENT_ID_SECRET_NAME", ""),
    client_secret_key: str = os.getenv("KEYVAULT_CLIENT_SECRET_NAME", ""),
    tenant_id_key: str = os.getenv("KEYVAULT_TENANT_ID_SECRET_NAME", "")
) -> BlobServiceClient:
    """
    Authenticates to Azure Blob Storage via Key Vault secrets.

    Parameters
    ----------
    keyvault_name : str
        Name of the Azure Key Vault.
    storage_account_name : str
        Name of the Azure Storage Account.
    client_id_key : str
        Key Vault secret name for Client ID.
    client_secret_key : str
        Key Vault secret name for Client Secret.
    tenant_id_key : str
        Key Vault secret name for Tenant ID.

    Returns
    -------
    BlobServiceClient
        Authenticated client for blob operations.
    """
    try:
        key_vault_url = f"https://{keyvault_name}.vault.azure.net/"
        account_url = f"https://{storage_account_name}.blob.core.windows.net"

        print(f"🔐 Connecting to Key Vault: {key_vault_url}")

        secret_client = SecretClient(vault_url=key_vault_url, credential=DefaultAzureCredential())
        client_id = secret_client.get_secret(client_id_key).value
        client_secret = secret_client.get_secret(client_secret_key).value
        tenant_id = secret_client.get_secret(tenant_id_key).value

        credential = ClientSecretCredential(tenant_id, client_id, client_secret)
        blob_service_client = BlobServiceClient(account_url=account_url, credential=credential)

        print(f"✅ Authenticated to Azure Blob Storage ({storage_account_name})")
        return blob_service_client

    except Exception as e:
        raise Exception(f"❌ Blob authentication failed: {e}")

# ==========================================================
#  FILE UPLOAD
# ==========================================================
def upload_file_to_blob(blob_service_client, local_path: str, blob_path: str, container_name: str = "834analytics-dev", overwrite = True) -> str:
    """
    Uploads a local file to Azure Blob Storage.
    Uses existing authenticated BlobServiceClient.
    Returns the full blob URL if successful.

    Parameters
    ----------
    blob_service_client : BlobServiceClient
        Authenticated client from authenticate_blob_storage()
    local_path : str
        Full path to local file
    blob_path : str
        Target path within container (e.g. 'raw/agent_contract_request_carrier/success/filename.xlsx')
    container_name : str
        Target container (default: 'data')

    Returns
    -------
    str
        Public or direct blob URL (depending on storage permissions)
    """
    try:
        container_client = blob_service_client.get_container_client(container_name)
        blob_client = container_client.get_blob_client(blob_path)

        with open(local_path, "rb") as data:
            blob_client.upload_blob(data, overwrite=True)

        url = f"{blob_service_client.url}/{container_name}/{blob_path}"
        print(f"☁️ Uploaded {local_path} → {url}")
        return url

    except Exception as e:
        print(f"❌ upload_file_to_blob failed for {local_path}: {e}")
        return None

# ==========================================================
#  FILE DOWNLOAD
# ==========================================================
def download_file_from_blob(blob_service_client, blob_path: str, local_path: str, container_name: str = "834analytics-dev") -> str:
    """
    Downloads a blob file from Azure Storage to local path.

    Parameters
    ----------
    blob_service_client : BlobServiceClient
        Authenticated client from authenticate_blob_storage()
    blob_path : str
        Blob path within container (e.g. 'templates/CareSource_Template.xlsx')
    local_path : str
        Full local file destination path (e.g. 'C:/Downloads/CareSource_Template.xlsx')
    container_name : str
        Container name (default: '834analytics-dev')

    Returns
    -------
    str
        Local file path if successful, None otherwise
    """
    try:
        container_client = blob_service_client.get_container_client(container_name)
        blob_client = container_client.get_blob_client(blob_path)

        os.makedirs(os.path.dirname(local_path), exist_ok=True)
        with open(local_path, "wb") as file:
            data = blob_client.download_blob()
            file.write(data.readall())

        print(f"⬇️ Downloaded blob {blob_path} → {local_path}")
        return local_path
    except Exception as e:
        print(f"❌ download_file_from_blob failed for {blob_path}: {e}")
        return None

# ==========================================================
#  CSV BUFFER HELPERS (FOR SBE SCRAPER)
# ==========================================================
import io
import csv


def blob_exists(blob_service_client, blob_path: str, container_name: str = "834analytics-dev") -> bool:
    """Checks if a blob exists."""
    container_client = blob_service_client.get_container_client(container_name)
    blob_client = container_client.get_blob_client(blob_path)
    return blob_client.exists()


def ensure_csv_exists(blob_service_client, blob_path: str, header: list,
                      container_name: str = "834analytics-dev"):
    """
    Creates a CSV file with header if it does not already exist.
    """
    if blob_exists(blob_service_client, blob_path, container_name):
        return

    container_client = blob_service_client.get_container_client(container_name)
    blob_client = container_client.get_blob_client(blob_path)

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(header)

    blob_client.upload_blob(output.getvalue().encode("utf-8"), overwrite=True)
    print(f"📄 Created new CSV in blob: {blob_path}")


def append_csv_row_to_blob(blob_service_client, blob_path: str, row: dict,
                           container_name: str = "834analytics-dev"):
    """
    Appends a dict row to an existing CSV on Blob.
    The CSV is downloaded, row appended, and uploaded back.
    Good enough for 2–10k rows/day.
    """
    container_client = blob_service_client.get_container_client(container_name)
    blob_client = container_client.get_blob_client(blob_path)

    # Download existing CSV (if present)
    csv_data = ""
    if blob_client.exists():
        csv_data = blob_client.download_blob().readall().decode("utf-8")

    # Load CSV in memory
    input_buffer = io.StringIO(csv_data)
    reader = list(csv.reader(input_buffer))

    # Prepare to append
    output_buffer = io.StringIO()
    writer = csv.writer(output_buffer)

    # If file empty: write header first
    if len(reader) == 0:
        writer.writerow(list(row.keys()))
    else:
        writer.writerows(reader)

    # Now append row
    writer.writerow([row.get(k) for k in reader[0]])

    # Upload back
    blob_client.upload_blob(output_buffer.getvalue().encode("utf-8"), overwrite=True)
    print(f"☁️ Appended row → {blob_path}")


def read_csv_from_blob(blob_service_client, blob_path: str,
                       container_name: str = "834analytics-dev") -> list:
    """
    Reads a CSV from blob and returns it as a list of dict rows.
    """
    container_client = blob_service_client.get_container_client(container_name)
    blob_client = container_client.get_blob_client(blob_path)

    if not blob_client.exists():
        return []

    csv_data = blob_client.download_blob().readall().decode("utf-8")

    input_buffer = io.StringIO(csv_data)
    reader = csv.DictReader(input_buffer)
    return list(reader)

def read_filenames_from_blob(blob_service_client, blob_path: str, container_name: str = "834analytics-dev") -> list:
    """
    Reads all filenames from blob and returns it as a list of strings.
    """
    container_client = blob_service_client.get_container_client(container_name)
    blob_client = container_client.get_blob_client(blob_path)

    if not blob_client.exists():
        return []

    filenames = container_client.list_blob_names(name_starts_with=blob_path)

    return list(filenames)


def delete_blob(blob_service_client, blob_path: str, container_name: str = "834analytics-dev"):
    """
    Deletes a blob (CSV cleanup once uploaded into SQL).
    """
    container_client = blob_service_client.get_container_client(container_name)
    blob_client = container_client.get_blob_client(blob_path)

    try:
        blob_client.delete_blob()
        print(f"🗑️ Deleted blob: {blob_path}")
    except Exception as e:
        print(f"❌ delete_blob failed for {blob_path}: {e}")

