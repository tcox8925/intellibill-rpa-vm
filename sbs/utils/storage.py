import os
from azure.identity import DefaultAzureCredential, ClientSecretCredential
from azure.keyvault.secrets import SecretClient
from azure.storage.blob import BlobServiceClient


#config

KEY_VAULT_NAME = os.getenv("KEY_VAULT_NAME", "")
STORAGE_ACCOUNT_NAME = "834analyticsdatalake"

CONTAINER_NAME = "834analytics-dev"
BASE_PATH = "raw/agent_license_update/sbs"
FILE_PREFIX = "raw_alu_license"

CLIENT_ID_KEY = os.getenv("KEYVAULT_CLIENT_ID_SECRET_NAME", "")
CLIENT_SECRET_KEY = os.getenv("KEYVAULT_CLIENT_SECRET_NAME", "")
TENANT_ID_KEY = os.getenv("KEYVAULT_TENANT_ID_SECRET_NAME", "")


#auth

def _get_blob_service_client() -> BlobServiceClient:
    """
    Authenticate to Azure Blob Storage using Service Principal
    credentials stored in Key Vault.
    """
    key_vault_url = os.getenv("KEYVAULT_URL", "")
    account_url = f"https://{STORAGE_ACCOUNT_NAME}.blob.core.windows.net"

    secret_client = SecretClient(
        vault_url=key_vault_url,
        credential=DefaultAzureCredential()
    )

    client_id = secret_client.get_secret(CLIENT_ID_KEY).value
    client_secret = secret_client.get_secret(CLIENT_SECRET_KEY).value
    tenant_id = secret_client.get_secret(TENANT_ID_KEY).value

    credential = ClientSecretCredential(
        tenant_id=tenant_id,
        client_id=client_id,
        client_secret=client_secret
    )

    return BlobServiceClient(
        account_url=account_url,
        credential=credential
    )


#path

def build_blob_name(jur_short: str, formatted_to_date: str) -> str:
    """
    Build canonical SBS blob name.
    NO file extension.

    Example:
    raw/agent_license_update/sbs/raw_alu_license_tx_01_12_2026.csv
    """
    filename = f"{FILE_PREFIX}_{jur_short}_{formatted_to_date}.csv"
    return f"{BASE_PATH}/{filename}"


#retry - check if files exist

def blob_exists(jur_short: str, formatted_to_date: str) -> bool:
    """
    Check whether the expected SBS report blob exists.
    """
    blob_service_client = _get_blob_service_client()
    container_client = blob_service_client.get_container_client(CONTAINER_NAME)

    blob_name = build_blob_name(jur_short, formatted_to_date)
    blob_client = container_client.get_blob_client(blob_name)

    return blob_client.exists()


#upload

def upload_report(
    jur_short: str,
    formatted_to_date: str,
    report_bytes: bytes
):
    """
    Upload SBS report content to Azure Blob Storage.
    Overwrites existing blob if present.
    """
    blob_service_client = _get_blob_service_client()
    container_client = blob_service_client.get_container_client(CONTAINER_NAME)

    blob_name = build_blob_name(jur_short, formatted_to_date)
    blob_client = container_client.get_blob_client(blob_name)

    blob_client.upload_blob(
        report_bytes,
        overwrite=True
    )
