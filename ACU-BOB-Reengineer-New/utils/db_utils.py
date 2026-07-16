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
import time

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


def get_postgres_connection(max_retries=4):
    """
    Create and return a psycopg2 connection to Azure PostgreSQL.
    Uses AAD token authentication via service principal.

    Resilience:
      - Retries transient failures (e.g. "server closed the connection
        unexpectedly", connection resets, gateway timeouts) with exponential
        backoff, so a single dropped socket doesn't kill a carrier mid-run.
      - A fresh AAD token is acquired on each attempt (cheap, and avoids a
        stale/expired token causing a hard failure).
      - TCP keepalives keep the long-lived per-carrier connection alive across
        Azure gateway idle windows and detect a dead socket quickly.

    Returns
    -------
    psycopg2.connection
        Active database connection.
    """
    # libpq keepalive params: begin probing after 30s idle, every 10s, drop
    # after 5 missed probes. connect_timeout bounds each connect attempt.
    keepalive_kwargs = {
        "connect_timeout": 30,
        "keepalives": 1,
        "keepalives_idle": 30,
        "keepalives_interval": 10,
        "keepalives_count": 5,
    }

    last_error = None
    for attempt in range(1, max_retries + 1):
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
                **keepalive_kwargs,
            )

            if attempt > 1:
                print(f"✅ Connected to PostgreSQL ({DB_CONFIG_POSTGRES['server']}) "
                      f"[recovered on attempt {attempt}/{max_retries}]")
            else:
                print(f"✅ Connected to PostgreSQL ({DB_CONFIG_POSTGRES['server']})")
            return conn

        except Exception as e:
            last_error = e
            if attempt < max_retries:
                delay = 1.5 * (2 ** (attempt - 1))  # 1.5s, 3s, 6s
                print(f"⚠️  PostgreSQL connect attempt {attempt}/{max_retries} failed: {e} "
                      f"— retrying in {delay:.0f}s")
                time.sleep(delay)

    raise Exception(f"❌ PostgreSQL connection failed after {max_retries} attempts: {last_error}")


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