# ==========================================================
#  utils/azure_blob_utils.py
# ==========================================================
"""
azure_blob_utils.py
-------------------
Purpose:
    - Authenticate to Azure Blob Storage using Key Vault secrets.
    - Return a ready-to-use BlobServiceClient.
"""

import os
from azure.identity import DefaultAzureCredential, ClientSecretCredential
from azure.keyvault.secrets import SecretClient
from azure.storage.blob import BlobServiceClient

# ==========================================================
#  CONFIGURATION
# ==========================================================
DEFAULT_KEYVAULT_NAME = os.getenv("KEY_VAULT_NAME", "")
DEFAULT_STORAGE_ACCOUNT = "834analyticsdatalake"
DEFAULT_CONTAINER = "834analytics-dev"


# ==========================================================
#  AUTHENTICATION
# ==========================================================
def authenticate_blob_storage(
    keyvault_name: str = DEFAULT_KEYVAULT_NAME,
    storage_account_name: str = DEFAULT_STORAGE_ACCOUNT,
    client_id_key: str = os.getenv("KEYVAULT_CLIENT_ID_SECRET_NAME", ""),
    client_secret_key: str = os.getenv("KEYVAULT_CLIENT_SECRET_NAME", ""),
    tenant_id_key: str = os.getenv("KEYVAULT_TENANT_ID_SECRET_NAME", ""),
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

        secret_client = SecretClient(
            vault_url=key_vault_url, credential=DefaultAzureCredential()
        )
        client_id = secret_client.get_secret(client_id_key).value
        client_secret = secret_client.get_secret(client_secret_key).value
        tenant_id = secret_client.get_secret(tenant_id_key).value

        credential = ClientSecretCredential(tenant_id, client_id, client_secret)
        blob_service_client = BlobServiceClient(
            account_url=account_url, credential=credential
        )

        print(f"✅ Authenticated to Azure Blob Storage ({storage_account_name})")
        return blob_service_client

    except Exception as e:
        raise Exception(f"❌ Blob authentication failed: {e}")


# ==========================================================
#  FILE READING (local override support)
# ==========================================================

def read_file_bytes(blob_service_client, blob_name, container_name=DEFAULT_CONTAINER):
    """
    Read file bytes — from local disk if blob_name is a local path,
    otherwise from Azure Blob Storage.

    When config.FEATURES["file_override"] is True, the scan step
    passes local file paths as blob_name. This function detects that
    automatically via os.path.isfile().
    """
    import os
    if os.path.isfile(blob_name):
        with open(blob_name, "rb") as f:
            return f.read()
    container_client = blob_service_client.get_container_client(container_name)
    return container_client.get_blob_client(blob_name).download_blob().readall()