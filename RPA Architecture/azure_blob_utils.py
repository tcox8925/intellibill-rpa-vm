import os
from azure.identity import DefaultAzureCredential, ClientSecretCredential
from azure.storage.blob import BlobServiceClient
from azure.keyvault.secrets import SecretClient
from rpa_matrix_reader import read_rpa_matrix

def get_blob_config(process_name, company_id, carrier_id):
    """
    Reads blob storage config for the given identifiers from the RPA Matrix.
    """
    df = read_rpa_matrix()
    if df is None or df.empty:
        raise Exception("RPA matrix is empty or failed to load.")

    row = df[
        (df["process_name"].str.lower() == process_name.lower()) &
        (df["company_id"].str.lower() == company_id.lower()) &
        (df["carrier_id"].str.lower() == carrier_id.lower())
    ]

    if row.empty:
        raise Exception("No matching configuration found in RPA matrix.")

    record = row.iloc[0]
    return {
        "storage_account_name": record["storage_account_name"],
        "container_name": record["container_name"],
        "keyvault_name": record["source_password_secret_name"],
        "client_id": record["client_id"],
        "client_secret": record["client_secret"],
        "tenant_id": record["tenant_id"]
    }

def authenticate_blob_storage(process_name=None, company_id=None, carrier_id=None):
    """
    Authenticates and returns a BlobServiceClient.
    - If process_name, company_id, and carrier_id are provided, it uses matrix-based secrets.
    - Else, falls back to default credentials for matrix upload only.
    """
    try:
        if process_name and company_id and carrier_id:
            config = get_blob_config(process_name, company_id, carrier_id)
            keyvault_name = config["keyvault_name"]
            storage_account_name = config["storage_account_name"]
            client_id_key = config["client_id"]
            client_secret_key = config["client_secret"]
            tenant_id_key = config["tenant_id"]
        else:
            # Manual upload fallback
            keyvault_name = os.getenv("KEY_VAULT_NAME", "")
            storage_account_name = "834analyticsdatalake"
            client_id_key = os.getenv("KEYVAULT_CLIENT_ID_SECRET_NAME", "")
            client_secret_key = os.getenv("KEYVAULT_CLIENT_SECRET_NAME", "")
            tenant_id_key = os.getenv("KEYVAULT_TENANT_ID_SECRET_NAME", "")

        account_url = f"https://{storage_account_name}.blob.core.windows.net"
        key_vault_url = f"https://{keyvault_name}.vault.azure.net/"
        print(f"Connecting to Key Vault: {key_vault_url}")

        secret_client = SecretClient(vault_url=key_vault_url, credential=DefaultAzureCredential())
        client_id = secret_client.get_secret(client_id_key).value
        client_secret = secret_client.get_secret(client_secret_key).value
        tenant_id = secret_client.get_secret(tenant_id_key).value

        credential = ClientSecretCredential(tenant_id, client_id, client_secret)
        blob_service_client = BlobServiceClient(account_url=account_url, credential=credential)
        print("== Successfully authenticated to Azure Blob Storage.")
        return blob_service_client

    except Exception as e:
        raise Exception(f"== Blob authentication failed: {e}")

def download_blob(blob_service_client, container_name, blob_path=None, local_file_path=None):
    """
    Downloads a file from Azure Blob Storage.
    """
    try:
        if not blob_path or not local_file_path:
            raise ValueError("Blob path and local file path must be provided.")

        blob_client = blob_service_client.get_blob_client(container=container_name, blob=blob_path)
        with open(local_file_path, "wb") as file:
            file.write(blob_client.download_blob().readall())
        print(f"== File downloaded successfully: {local_file_path}")

    except Exception as e:
        print(f"== Error downloading file '{blob_path}': {e}")

def upload_blob(blob_service_client, container_name, local_file_path=None, blob_path=None):
    """
    Uploads a file to Azure Blob Storage.
    """
    try:
        if not local_file_path or not blob_path:
            raise ValueError("Local file path and blob path must be provided.")

        blob_client = blob_service_client.get_blob_client(container=container_name, blob=blob_path)
        with open(local_file_path, "rb") as file:
            blob_client.upload_blob(file, overwrite=True)
        print(f"== File uploaded successfully to: {blob_path}")

    except Exception as e:
        print(f"== Error uploading file '{local_file_path}' to '{blob_path}': {e}")
