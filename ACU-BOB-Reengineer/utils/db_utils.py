import os
# ==========================================================
#  utils/db_utils.py
# ==========================================================
"""
db_utils.py
-----------
Purpose:
    - Authenticate to Azure PostgreSQL using AAD token via Key Vault secrets.
    - Return a ready-to-use psycopg2 connection.
"""

from azure.identity import DefaultAzureCredential, ClientSecretCredential
from azure.keyvault.secrets import SecretClient
import psycopg2
import struct

# ==========================================================
#  CONFIGURATION
# ==========================================================
KEY_VAULT_URL = os.getenv("KEYVAULT_URL", "")

DB_CONFIG_POSTGRES = {
    "server": os.getenv("DEFAULT834_DB_HOST", ""),
    "database": os.getenv("DEFAULT834_DB_NAME", ""),
    "user": os.getenv("DEFAULT834_DB_USER", ""),
    "sslmode": "require",
}

DB_CONFIG_SYNAPSE = {
    "server": "834analyticsynapse.sql.azuresynapse.net",
    "port": "1433",
    "database": "834_analytics_dev",
}


# ==========================================================
#  AUTHENTICATION
# ==========================================================
def get_postgres_db_secrets():
    """
    Fetch service principal credentials from Azure Key Vault.

    Returns
    -------
    tuple
        (client_id, client_secret, tenant_id)
    """
    credential = DefaultAzureCredential()
    client = SecretClient(vault_url=KEY_VAULT_URL, credential=credential)

    client_id = client.get_secret(os.getenv("KEYVAULT_CLIENT_ID_SECRET_NAME", "")).value
    client_secret = client.get_secret(os.getenv("KEYVAULT_CLIENT_SECRET_NAME", "")).value
    tenant_id = client.get_secret(os.getenv("KEYVAULT_TENANT_ID_SECRET_NAME", "")).value

    return client_id, client_secret, tenant_id


def get_postgres_connection():
    """
    Create and return a psycopg2 connection to Azure PostgreSQL.
    Uses AAD token authentication via service principal.

    Returns
    -------
    psycopg2.connection
        Active database connection.
    """
    try:
        client_id, client_secret, tenant_id = get_postgres_db_secrets()

        credential = ClientSecretCredential(tenant_id, client_id, client_secret)
        token = credential.get_token(
            "https://ossrdbms-aad.database.windows.net/.default"
        ).token

        conn = psycopg2.connect(
            host=DB_CONFIG_POSTGRES["server"],
            dbname=DB_CONFIG_POSTGRES["database"],
            user=DB_CONFIG_POSTGRES["user"],
            password=token,
            sslmode=DB_CONFIG_POSTGRES["sslmode"],
        )

        print(f"✅ Connected to PostgreSQL ({DB_CONFIG_POSTGRES['server']})")
        return conn

    except Exception as e:
        raise Exception(f"❌ PostgreSQL connection failed: {e}")


def get_synapse_connection():
    """
    Create and return a pyodbc connection to Azure Synapse Analytics.
    Uses AAD token authentication via service principal.

    Returns
    -------
    pyodbc.Connection
        Active database connection.
    """
    import pyodbc

    try:
        client_id, client_secret, tenant_id = get_postgres_db_secrets()

        credential = ClientSecretCredential(tenant_id, client_id, client_secret)
        token = credential.get_token("https://database.windows.net/.default").token

        # pyodbc requires the AAD token as a binary struct
        token_bytes = token.encode("utf-16-le")
        token_struct = struct.pack(f"<I{len(token_bytes)}s", len(token_bytes), token_bytes)

        conn_str = (
            f"DRIVER={{ODBC Driver 17 for SQL Server}};"
            f"SERVER={DB_CONFIG_SYNAPSE['server']},{DB_CONFIG_SYNAPSE['port']};"
            f"DATABASE={DB_CONFIG_SYNAPSE['database']};"
            f"Encrypt=yes;TrustServerCertificate=no;"
        )

        conn = pyodbc.connect(conn_str, attrs_before={1256: token_struct})
        print(f"✅ Connected to Synapse ({DB_CONFIG_SYNAPSE['server']})")
        return conn

    except Exception as e:
        raise Exception(f"❌ Synapse connection failed: {e}")