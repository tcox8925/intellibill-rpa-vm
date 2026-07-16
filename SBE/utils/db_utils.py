import os
# ==========================================================
#  utils/db_utils.py
# ==========================================================
"""
db_utils.py
------------
Provides secure connection helpers for Synapse and MyOps databases
using Azure Key Vault + Service Principal authentication.

⚙️  Responsibilities:
    - Fetch secrets from Azure Key Vault
    - Return authenticated pyodbc connections
    - NO insert/update/logging logic here
"""

import pyodbc
import psycopg2
from azure.identity import DefaultAzureCredential, ClientSecretCredential
from azure.keyvault.secrets import SecretClient
from datetime import datetime
# ==========================================================
#  CONFIGURATION
# ==========================================================
KEY_VAULT_URL = os.getenv("KEYVAULT_URL", "")

DB_CONFIG = {
    "server": "myopsprd.database.windows.net",
    "database": "myopsprd",
    "driver": "{ODBC Driver 17 for SQL Server}"
}

DB_CONFIG_SYNAPSE = {
    "server": "834analyticsynapse.sql.azuresynapse.net",
    "database": "834_analytics_dev",
    "driver": "{ODBC Driver 17 for SQL Server}"
}

POSTGRES_CONFIG = {
    "host": os.getenv("DEFAULT834_DB_HOST", ""),
    "database": os.getenv("DEFAULT834_DB_NAME", ""),
    "user": os.getenv("DEFAULT834_DB_USER", ""),
}

# ==========================================================
#  KEY VAULT AUTHENTICATION HELPERS
# ==========================================================
def get_db_secrets():
    """
    Retrieve service principal credentials (Client ID, Secret, Tenant ID)
    for MyOps / Synapse authentication.
    """
    credential = DefaultAzureCredential()
    client = SecretClient(vault_url=KEY_VAULT_URL, credential=credential)

    client_id = client.get_secret(os.getenv("KEYVAULT_CLIENT_ID_SECRET_NAME", "")).value
    client_secret = client.get_secret(os.getenv("KEYVAULT_CLIENT_SECRET_NAME", "")).value
    tenant_id = client.get_secret(os.getenv("KEYVAULT_TENANT_ID_SECRET_NAME", "")).value

    return client_id, client_secret, tenant_id


def get_db_secrets_synapse():
    """
    Retrieve Synapse-only credentials when Tenant ID not required.
    """
    credential = DefaultAzureCredential()
    client = SecretClient(vault_url=KEY_VAULT_URL, credential=credential)

    client_id = client.get_secret(os.getenv("KEYVAULT_CLIENT_ID_SECRET_NAME", "")).value
    client_secret = client.get_secret(os.getenv("KEYVAULT_CLIENT_SECRET_NAME", "")).value

    return client_id, client_secret

# ==========================================================
#  CONNECTION HELPERS
# ==========================================================
def get_myops_connection():
    """
    Return authenticated connection to MyOps (Prod SQL DB).
    """
    client_id, client_secret, tenant_id = get_db_secrets()
    conn_str = (
        f"DRIVER={DB_CONFIG['driver']};"
        f"SERVER={DB_CONFIG['server']};"
        f"DATABASE={DB_CONFIG['database']};"
        "Authentication=ActiveDirectoryServicePrincipal;"
        f"UID={client_id};PWD={client_secret};Authority Id={tenant_id};"
        "Encrypt=yes;TrustServerCertificate=no;"
    )
    return pyodbc.connect(conn_str, timeout=30)

def get_synapse_connection():
    """
    Return authenticated connection to Synapse (834_analytics_dev).
    Used for matrix, queue, and logs (Prod + Test).
    """
    client_id, client_secret, tenant_id = get_db_secrets()
    conn_str = (
        f"DRIVER={DB_CONFIG_SYNAPSE['driver']};"
        f"SERVER={DB_CONFIG_SYNAPSE['server']};"
        f"DATABASE={DB_CONFIG_SYNAPSE['database']};"
        "Authentication=ActiveDirectoryServicePrincipal;"
        f"UID={client_id};PWD={client_secret};Authority Id={tenant_id};"
    )
    return pyodbc.connect(conn_str, timeout=30)

def get_postgres_connection():
    """
    Create a Postgres connection using AAD token auth
    via Service Principal stored in Key Vault.
    """
    credential = DefaultAzureCredential()
    client = SecretClient(vault_url=KEY_VAULT_URL, credential=credential)

    client_id = client.get_secret(os.getenv("KEYVAULT_CLIENT_ID_SECRET_NAME", "")).value
    client_secret = client.get_secret(os.getenv("KEYVAULT_CLIENT_SECRET_NAME", "")).value
    tenant_id = client.get_secret(os.getenv("KEYVAULT_TENANT_ID_SECRET_NAME", "")).value

    sp_credential = ClientSecretCredential(
        tenant_id=tenant_id,
        client_id=client_id,
        client_secret=client_secret
    )

    token = sp_credential.get_token(
        "https://ossrdbms-aad.database.windows.net/.default"
    ).token

    return psycopg2.connect(
        host=POSTGRES_CONFIG["host"],
        dbname=POSTGRES_CONFIG["database"],
        user=POSTGRES_CONFIG["user"],
        password=token,
        sslmode="require"
    )
