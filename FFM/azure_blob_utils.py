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
def upload_file_to_blob(blob_service_client, local_path: str, blob_path: str, container_name: str = "834analytics-dev") -> str:
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

def get_zoho_secrets():
    keyvault_name = os.getenv("KEY_VAULT_NAME", "")
    key_vault_url = f"https://{keyvault_name}.vault.azure.net/"
    secret_client = SecretClient(vault_url=key_vault_url, credential=DefaultAzureCredential())
    
    zoho_client_id_key = 'dataops-zoho-api-client-id'
    zoho_client_secret_key = 'dataops-zoho-api-client-secret'
    zoho_refresh_token_key = 'dataops-zoho-api-refresh-token'
    try:
        zoho_client_id = secret_client.get_secret(zoho_client_id_key).value
        zoho_client_secret = secret_client.get_secret(zoho_client_secret_key).value
        zoho_refresh_token = secret_client.get_secret(zoho_refresh_token_key).value
    except:
        print("Azure KeyVault could not be accessed. Using debug values.")
        zoho_client_id = '1000.ONC54X0TAP3GORWRZC50VRX97R0IHJ'
        zoho_client_secret = os.getenv("ZOHO_CLIENT_SECRET", "")
        zoho_refresh_token = os.getenv("ZOHO_REFRESH_TOKEN", "")
    
    return zoho_client_id, zoho_client_secret, zoho_refresh_token


#client = authenticate_blob_storage(storage_account_name="agilitydataprd001")
#upload_file_to_blob(client, 'C:/Users/actua/Desktop/work/Projects/RPA Agent Contract Request/Project Files', '/inbound/', container_name='sftpuhc')