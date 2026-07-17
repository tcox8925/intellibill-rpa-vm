# graph_auth.py
import os
from pathlib import Path

from azure.identity import DefaultAzureCredential, ClientSecretCredential
from azure.keyvault.secrets import SecretClient
from dotenv import load_dotenv

ROOT_ENV_FILE = Path(__file__).resolve().parent.parent / ".env"
load_dotenv(ROOT_ENV_FILE, override=False)

KEY_VAULT_NAME = os.environ.get("KEY_VAULT_NAME", "keyvault-834analytics").strip()
KEY_VAULT_URL = os.environ.get("KEYVAULT_URL", "").strip() or f"https://{KEY_VAULT_NAME}.vault.azure.net/"

CLIENT_ID_KEY = os.environ.get("KEYVAULT_CLIENT_ID_SECRET_NAME", "SynapseAccessClientId").strip()
CLIENT_SECRET_KEY = os.environ.get("KEYVAULT_CLIENT_SECRET_NAME", "SynapseAccessSecret").strip()
TENANT_ID_KEY = os.environ.get("KEYVAULT_TENANT_ID_SECRET_NAME", "TenantId").strip()

GRAPH_SCOPE = "https://graph.microsoft.com/.default"

def get_graph_access_token() -> str:
    """
    Uses the same SynapseAccess SP creds from Key Vault to get a Graph access token.
    Returns raw bearer token string.
    """
    credential = DefaultAzureCredential()
    client = SecretClient(vault_url=KEY_VAULT_URL, credential=credential)

    sp = ClientSecretCredential(
        tenant_id=client.get_secret(TENANT_ID_KEY).value,
        client_id=client.get_secret(CLIENT_ID_KEY).value,
        client_secret=client.get_secret(CLIENT_SECRET_KEY).value,
    )

    return sp.get_token(GRAPH_SCOPE).token