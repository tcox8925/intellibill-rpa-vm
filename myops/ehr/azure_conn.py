"""
Azure identity / Key Vault — kept separate from DB and pipeline logic.

Provides the cached ServicePrincipalCredential used to mint AAD tokens for
Postgres and (elsewhere) blob/storage access. Nothing here knows about the
pipeline; it only brokers credentials.
"""

import os
from pathlib import Path

from azure.identity import DefaultAzureCredential, ClientSecretCredential
from azure.keyvault.secrets import SecretClient
from dotenv import load_dotenv

ROOT_ENV_FILE = Path(__file__).resolve().parents[2] / ".env"
load_dotenv(ROOT_ENV_FILE, override=False)

KEY_VAULT_NAME = os.environ.get("KEY_VAULT_NAME", "keyvault-834analytics").strip()
KEY_VAULT_URL = os.environ.get("KEYVAULT_URL", "").strip() or f"https://{KEY_VAULT_NAME}.vault.azure.net/"

CLIENT_ID_KEY = os.environ.get("KEYVAULT_CLIENT_ID_SECRET_NAME", "SynapseAccessClientId").strip()
CLIENT_SECRET_KEY = os.environ.get("KEYVAULT_CLIENT_SECRET_NAME", "SynapseAccessSecret").strip()
TENANT_ID_KEY = os.environ.get("KEYVAULT_TENANT_ID_SECRET_NAME", "TenantId").strip()

_cached_sp_credential = None


def get_sp_credential():
    """
    Build and cache the ServicePrincipalCredential so we don't hit Key Vault
    on every DB / blob call. Prefers the creds already present in the
    environment (.env); only hits Key Vault if they're not set there.
    """
    global _cached_sp_credential
    if _cached_sp_credential is None:
        tenant_id = os.environ.get("AZURE_TENANT_ID", "").strip()
        client_id = os.environ.get("AZURE_CLIENT_ID", "").strip()
        client_secret = os.environ.get("AZURE_CLIENT_SECRET", "").strip()

        if not (tenant_id and client_id and client_secret):
            kv = SecretClient(
                vault_url=KEY_VAULT_URL,
                credential=DefaultAzureCredential(),
            )
            tenant_id = kv.get_secret(TENANT_ID_KEY).value
            client_id = kv.get_secret(CLIENT_ID_KEY).value
            client_secret = kv.get_secret(CLIENT_SECRET_KEY).value

        _cached_sp_credential = ClientSecretCredential(
            tenant_id=tenant_id,
            client_id=client_id,
            client_secret=client_secret,
        )
    return _cached_sp_credential
