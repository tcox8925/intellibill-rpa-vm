import json
from functools import lru_cache
from azure.identity import DefaultAzureCredential, ClientSecretCredential
from azure.keyvault.secrets import SecretClient
from pydantic_settings import BaseSettings, SettingsConfigDict
from azure.storage.blob import (
    BlobServiceClient,
    generate_blob_sas,
    BlobSasPermissions,
    BlobClient,
    ResourceTypes,
)
from datetime import datetime, timezone, timedelta
import io
from azure.core.exceptions import ResourceNotFoundError
from fastapi import HTTPException
import os


class Settings(BaseSettings):
    PROJECT_NAME: str = "MyOpsPortal-Backend"
    API_V1_PREFIX: str = "/api/v1"

    JWT_SECRET_KEY: str
    JWT_ALGORITHM: str
    JWT_EXPIRY_MINUTES: int = 60
    JWT_ISSUER: str

    # Azure Key Vault & Storage
    AZURE_KEYVAULT_URL: str
    AZURE_TENANT_ID: str
    AZURE_CLIENT_ID: str
    AZURE_CLIENT_SECRET: str | None = (
        None  # Optional on Azure App Service / Managed Identity
    )

    # Power BI
    POWER_BI_TENANT_ID: str
    POWER_BI_CLIENT_ID: str
    POWER_BI_CLIENT_SECRET: str

    # Open AI
    SPEECH_KEY: str
    SPEECH_REGION: str

    # Attachment Blob Storage
    ATTACHMENT_ACCOUNT_NAME: str
    ATTACHMENT_CONTAINER_NAME: str

    # Recording Blob Storage (new)
    RECORDING_ACCOUNT_NAME: str
    RECORDING_CONTAINER_NAME: str
    RECORDING_AZURE_FUNCTION_URL: str

    # Consolidated Storage Account
    STORAGE_ACCOUNT_NAME: str = "834analyticsdatalake"

    COMMISSIONS_AZURE_FUNCTION_URL: str
    COMMISSIONS_AZURE_FUNCTION_KEY: str

    # RING CENTRAL
    RING_CENTRAL_CLIENT_ID: str
    RING_CENTRAL_CLIENT_SECRET: str
    RING_CENTRAL_JWT: str
    RING_CENTRAL_SERVER_URL: str = "https://platform.ringcentral.com"

    # Azure Communication Services - Email
    ACS_EMAIL_CONNECTION_STRING: str
    ACS_RESOURCE_GROUP: str = "834analytics"
    ACS_EMAIL_SERVICE_NAME: str = "MyOpsEmailService"
    AZURE_SUBSCRIPTION_ID: str

    # Postgress DB
    PG_HOST: str
    PG_PASSWORD: str
    PG_USER: str
    PG_PORT: int
    PG_DATABASE: str

    # Onboarding URLs
    MYADMIN_STAGING_ONBOARDING_URL: str
    MYOPS_ONBOARDING_URL: str
    
    # PCH Postgress DB
    PCH_PG_HOST: str
    PCH_PG_PASSWORD: str
    PCH_PG_USER: str
    PCH_PG_PORT: int
    PCH_PG_DATABASE: str

    # COM storage Account details
    COMM_ACCOUNT_NAME: str
    COMM_CONTAINER_NAME: str
    
    model_config = SettingsConfigDict(extra="allow")
    # model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    @property
    def SYNAPSE_CONNECTION_STRING(self) -> str:
        """ODBC connection string for Azure Synapse"""
        return (
            f"Driver={{ODBC Driver 18 for SQL Server}};"
            f"Server={self.SYNAPSE_DB_SERVER},{self.SYNAPSE_DB_PORT};"
            f"Database={self.SYNAPSE_DB_NAME};"
            "Authentication=ActiveDirectoryServicePrincipal;"
            f"UID={self.AZURE_CLIENT_ID};"
            f"PWD={self.AZURE_CLIENT_SECRET};"
            f"Authority Id={self.AZURE_TENANT_ID};"
            "Encrypt=Strict;"
            "TrustServerCertificate=no;"
            "Connection Timeout=30;"
        )

    @property
    def DB_CONNECTION_STRING(self) -> str:
        return (
            "Driver={ODBC Driver 18 for SQL Server};"
            f"Server={self.DB_SERVER};"
            f"Database={self.DB_NAME};"
            "Authentication=ActiveDirectoryServicePrincipal;"
            f"UID={self.AZURE_CLIENT_ID};"
            f"PWD={self.AZURE_CLIENT_SECRET};"
            f"Authority Id={self.AZURE_TENANT_ID};"
            "Encrypt=Strict;"
            "TrustServerCertificate=no;"
            "Connection Timeout=30;"
        )

    @property
    def _azure_credential(self):
        """Use SP credentials locally, Managed Identity on Azure."""
        print("Client id", self.AZURE_CLIENT_ID)
        if self.AZURE_CLIENT_SECRET:  # local dev
            return ClientSecretCredential(
                tenant_id=self.AZURE_TENANT_ID,
                client_id=self.AZURE_CLIENT_ID,
                client_secret=self.AZURE_CLIENT_SECRET,
            )
        # App Service / Managed Identity
        return DefaultAzureCredential(exclude_interactive_browser_credential=True)

    @property
    def blob_service_client(self) -> BlobServiceClient:
        """Return BlobServiceClient using AAD credentials."""
        account_url = f"https://{self.ATTACHMENT_ACCOUNT_NAME}.blob.core.windows.net"
        return BlobServiceClient(
            account_url=account_url, credential=self._azure_credential
        )

    @property
    def BLOB_SERVICE_URI(self) -> str:
        return f"https://{self.ATTACHMENT_ACCOUNT_NAME}.blob.core.windows.net/"

    def get_sas_token(self, blob_name: str) -> str:
        """Generate SAS using user delegation key (AAD auth)."""
        blob_service_client = self.blob_service_client

        delegation_key = blob_service_client.get_user_delegation_key(
            key_start_time=datetime.now(timezone.utc),
            key_expiry_time=datetime.now(timezone.utc) + timedelta(hours=1),
        )

        sas_token = generate_blob_sas(
            account_name=self.ATTACHMENT_ACCOUNT_NAME,
            container_name=self.ATTACHMENT_CONTAINER_NAME,
            blob_name=blob_name,
            user_delegation_key=delegation_key,
            permission=BlobSasPermissions(read=True),
            expiry=datetime.now(timezone.utc) + timedelta(hours=1),
        )

        return sas_token

    @property
    def recording_blob_service_client(self) -> BlobServiceClient:
        """Return BlobServiceClient for recordings storage."""
        account_url = f"https://{self.ATTACHMENT_ACCOUNT_NAME}.blob.core.windows.net"
        print(account_url, self._azure_credential)
        return BlobServiceClient(
            account_url=account_url,
            credential=self._azure_credential,
            api_version="2025-07-05",
        )

    @property
    def RECORDING_BLOB_SERVICE_URI(self) -> str:
        return f"https://{self.ATTACHMENT_ACCOUNT_NAME}.blob.core.windows.net/"


@lru_cache()
def load_settings_from_keyvault() -> Settings:
    """Fetch secrets from Key Vault and load into Settings."""
    vault_url = "https://labs834-kv-dev.vault.azure.net/"
    if not vault_url:
        raise ValueError("AZURE_KEYVAULT_URL is not set in environment variables.")

    # Credential uses SP locally, Managed Identity on App Service
    if os.getenv("AZURE_CLIENT_SECRET"):
        credential = ClientSecretCredential(
            tenant_id=os.getenv("AZURE_TENANT_ID"),
            client_id=os.getenv("AZURE_CLIENT_ID"),
            client_secret=os.getenv("AZURE_CLIENT_SECRET"),
        )
    else:
        credential = DefaultAzureCredential(exclude_interactive_browser_credential=True)

    client = SecretClient(vault_url=vault_url, credential=credential)

    # Fetch secret JSON
    secret_bundle = client.get_secret("backendEnv")
    secret_dict = json.loads(secret_bundle.value)

    return Settings(**secret_dict)


@lru_cache()
def get_settings() -> Settings:
    return load_settings_from_keyvault()
    # return Settings()


settings = get_settings()


def get_blob_service_client() -> BlobServiceClient:
    """FastAPI dependency to get a BlobServiceClient (via AAD)."""
    return settings.blob_service_client


def get_recording_blob_service_client() -> BlobServiceClient:
    """FastAPI dependency to get the recordings BlobServiceClient."""
    return settings.recording_blob_service_client


def download_blob(folder: str) -> bytes:
    """
    Download a blob from the default storage and return its bytes.
    """
    try:
        container_client = get_recording_blob_service_client().get_container_client(
            settings.RECORDING_CONTAINER_NAME
        )

        prefix = f"search results/{folder}/"
        blobs = container_client.list_blobs(name_starts_with=prefix)

        return blobs

    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"Error listing recordings: {str(e)}"
        )


def download_recording(
    blob_path: str, recording_container_name: str = settings.RECORDING_CONTAINER_NAME
) -> bytes:
    """
    Download a blob from the recordings storage and return its bytes.
    """
    blob_client = get_recording_blob_service_client().get_blob_client(
        container=recording_container_name, blob=blob_path
    )

    print(
        f"RECORDING_CONTAINER_NAME: {recording_container_name}, blob_path: {blob_path}"
    )
    try:
        stream = blob_client.download_blob()
        file_like = io.BytesIO()
        stream.readinto(file_like)
        file_like.seek(0)
        return file_like
    except ResourceNotFoundError:
        raise


def upload_to_azure_blob(file_path: str, blob_name: str, container_name: str) -> str:
    """
    Uploads a file to Azure Blob Storage in the specified container.
    Creates the container if it does not exist.
    Returns the blob URL.
    """
    blob_service_client = get_recording_blob_service_client()
    container_client = blob_service_client.get_container_client(container_name)

    try:
        container_client.create_container()
    except Exception as e:
        pass

    blob_client = container_client.get_blob_client(blob_name)
    with open(file_path, "rb") as data:
        blob_client.upload_blob(data, overwrite=True, timeout=10000)

    return blob_client.url


def get_graph_token() -> str:
    """
    Retrieve an access token for Microsoft Graph API using the existing Azure credentials.
    Works with both ClientSecretCredential and Managed Identity.
    """
    credential = settings._azure_credential
    token = credential.get_token("https://graph.microsoft.com/.default")
    # below is bearer Token to use in all Graph API calls
    return token.token
