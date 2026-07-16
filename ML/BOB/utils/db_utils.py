import os
import pyodbc
import psycopg2
from azure.identity import DefaultAzureCredential, ClientSecretCredential
from azure.keyvault.secrets import SecretClient


# ==========================================================
# CONFIG
# ==========================================================

KEY_VAULT_URL = os.getenv("KEYVAULT_URL", "")

SYNAPSE_CONFIG = {
    "server": "834analyticsynapse.sql.azuresynapse.net",
    "database": "834_analytics_dev",
    "driver": "{ODBC Driver 17 for SQL Server}",
}

POSTGRES_CONFIG = {
    "host": os.getenv("DEFAULT834_DB_HOST", ""),
    "database": os.getenv("DEFAULT834_DB_NAME", ""),
    "user": os.getenv("DEFAULT834_DB_USER", ""),
}


# ==========================================================
# KEY VAULT
# ==========================================================

def get_azure_secrets():
    """Retrieves SynapseAccess SP credentials from Azure Key Vault."""
    try:
        credential = DefaultAzureCredential()
        secret_client = SecretClient(vault_url=KEY_VAULT_URL, credential=credential)

        client_id = secret_client.get_secret(os.getenv("KEYVAULT_CLIENT_ID_SECRET_NAME", "")).value
        client_secret = secret_client.get_secret(os.getenv("KEYVAULT_CLIENT_SECRET_NAME", "")).value
        tenant_id = secret_client.get_secret(os.getenv("KEYVAULT_TENANT_ID_SECRET_NAME", "")).value

        return client_id, client_secret, tenant_id
    except Exception as e:
        print(f"❌ Failed to retrieve secrets from Key Vault: {e}")
        return None, None, None


# ==========================================================
# SYNAPSE (Service Principal via Key Vault)
# ==========================================================

def get_synapse_connection():
    """
    Returns pyodbc connection to Azure Synapse using
    ActiveDirectoryServicePrincipal authentication.
    """
    client_id, client_secret, tenant_id = get_azure_secrets()

    if not client_id or not client_secret:
        print("❌ Missing credentials. Cannot connect to Synapse.")
        return None

    conn_str = (
        f"DRIVER={SYNAPSE_CONFIG['driver']};"
        f"SERVER={SYNAPSE_CONFIG['server']};"
        f"DATABASE={SYNAPSE_CONFIG['database']};"
        "Authentication=ActiveDirectoryServicePrincipal;"
        f"UID={client_id};"
        f"PWD={client_secret};"
    )

    try:
        conn = pyodbc.connect(conn_str)
        print("✅ Synapse connection established.")
        return conn
    except pyodbc.Error as e:
        print(f"❌ Synapse connection failed: {e}")
        return None


# ==========================================================
# POSTGRES (SynapseAccess SP → AAD token)
# ==========================================================

def get_postgres_connection():
    """
    Returns psycopg2 connection to Azure PostgreSQL using
    the SynapseAccess Service Principal to obtain an AAD token.
    """
    client_id, client_secret, tenant_id = get_azure_secrets()

    if not client_id or not client_secret or not tenant_id:
        print("❌ Missing credentials. Cannot connect to Postgres.")
        return None

    sp_credential = ClientSecretCredential(
        tenant_id=tenant_id,
        client_id=client_id,
        client_secret=client_secret,
    )

    token = sp_credential.get_token(
        "https://ossrdbms-aad.database.windows.net/.default"
    ).token

    conn = psycopg2.connect(
        host=POSTGRES_CONFIG["host"],
        dbname=POSTGRES_CONFIG["database"],
        user=POSTGRES_CONFIG["user"],
        password=token,
        sslmode="require",
    )

    print("✅ Postgres connection established.")
    return conn