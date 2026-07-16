import os
# graph_auth.py
from azure.identity import DefaultAzureCredential, ClientSecretCredential
from azure.keyvault.secrets import SecretClient

KEY_VAULT_URL = os.getenv("KEYVAULT_URL", "")

CLIENT_ID_KEY = os.getenv("KEYVAULT_CLIENT_ID_SECRET_NAME", "")
CLIENT_SECRET_KEY = os.getenv("KEYVAULT_CLIENT_SECRET_NAME", "")
TENANT_ID_KEY = os.getenv("KEYVAULT_TENANT_ID_SECRET_NAME", "")

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