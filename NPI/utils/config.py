import os
from azure.identity import DefaultAzureCredential
from azure.keyvault.secrets import SecretClient

KEY_VAULT_URL = os.getenv("KEYVAULT_URL", "")

# =============================================================================
# Postgres write targets, keyed by `source`.
# SAME server, SAME login, SAME AAD token — only the database name differs.
#   source == "myops" (default) -> database 'postgres'    (current behavior)
#   source == "rcm"             -> database '834rcmdev'
# Both live on pch-db-dev001; schema (wpo) and table names are identical,
# including each DB's own ops_pch_logs table (logs follow the source).
# =============================================================================
DB_CONFIG_POSTGRES = {
    'server': os.getenv("PCH_DB_HOST", ""),
    'database': os.getenv("PCH_DB_NAME", ""),
    'user': os.getenv("PCH_DB_USER", ""),
}

DB_CONFIG_POSTGRES_RCM = {
    'server': os.getenv("PCH_DB_HOST", ""),               # same instance
    'database': os.getenv("PCH_RCM_DB_NAME", ""),        # only this differs
    'user': os.getenv("PCH_DB_USER", ""),                # same login
}

# Maps a `source` value to its Postgres target config. Unknown / missing -> myops.
POSTGRES_TARGETS = {
    'myops': DB_CONFIG_POSTGRES,
    'rcm': DB_CONFIG_POSTGRES_RCM,
}


def get_postgres_db_secrets():
    """
    Service-principal creds used to mint the AAD access token for Postgres.
    Both targets are on the same server and use the same SP (SynapseAccess),
    so a single token works for either database.
    """
    credential = DefaultAzureCredential()
    client = SecretClient(vault_url=KEY_VAULT_URL, credential=credential)

    client_id = client.get_secret(os.getenv("KEYVAULT_CLIENT_ID_SECRET_NAME", "")).value
    client_secret = client.get_secret(os.getenv("KEYVAULT_CLIENT_SECRET_NAME", "")).value
    tenant_id = client.get_secret(os.getenv("KEYVAULT_TENANT_ID_SECRET_NAME", "")).value

    return client_id, client_secret, tenant_id