import os
# db_executor.py

import psycopg2
import pyodbc

from azure.identity import DefaultAzureCredential, ClientSecretCredential
from azure.keyvault.secrets import SecretClient
from psycopg2.extras import RealDictCursor

# =========================================================
# KEY VAULT CONFIG
# =========================================================

KEY_VAULT_URL = os.getenv("KEYVAULT_URL", "")

# =========================================================
# POSTGRES CONFIG
# =========================================================

DB_CONFIG_POSTGRES = {
    "server": os.getenv("DEFAULT834_DB_HOST", ""),
    "database": os.getenv("DEFAULT834_DB_NAME", ""),
    "user": os.getenv("DEFAULT834_DB_USER", ""),
}

# =========================================================
# SYNAPSE CONFIG
# =========================================================

DB_CONFIG_SYNAPSE = {
    "server": "834analyticsynapse.sql.azuresynapse.net",
    "database": "834_analytics_dev",
    "driver": "{ODBC Driver 17 for SQL Server}"
}

# =========================================================
# SECRET HELPERS
# =========================================================

def _get_service_principal_credentials():
    credential = DefaultAzureCredential()
    client = SecretClient(vault_url=KEY_VAULT_URL, credential=credential)

    client_id = client.get_secret(os.getenv("KEYVAULT_CLIENT_ID_SECRET_NAME", "")).value
    client_secret = client.get_secret(os.getenv("KEYVAULT_CLIENT_SECRET_NAME", "")).value
    tenant_id = client.get_secret(os.getenv("KEYVAULT_TENANT_ID_SECRET_NAME", "")).value

    return client_id, client_secret, tenant_id


# =========================================================
# POSTGRES CONNECTION
# =========================================================

def get_postgres_connection():
    client_id, client_secret, tenant_id = _get_service_principal_credentials()

    credential = ClientSecretCredential(tenant_id, client_id, client_secret)
    token = credential.get_token(
        "https://ossrdbms-aad.database.windows.net/.default"
    ).token

    return psycopg2.connect(
        host=DB_CONFIG_POSTGRES["server"],
        dbname=DB_CONFIG_POSTGRES["database"],
        user=DB_CONFIG_POSTGRES["user"],
        password=token,
        sslmode="require",
    )


def execute_postgres_query(sql, params):
    conn = get_postgres_connection()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(sql, params)
            return cur.fetchall()
    finally:
        conn.close()


# =========================================================
# SYNAPSE CONNECTION
# =========================================================

def get_synapse_connection():
    client_id, client_secret, _ = _get_service_principal_credentials()

    conn_str = (
        f"DRIVER={DB_CONFIG_SYNAPSE['driver']};"
        f"SERVER={DB_CONFIG_SYNAPSE['server']};"
        f"DATABASE={DB_CONFIG_SYNAPSE['database']};"
        "Authentication=ActiveDirectoryServicePrincipal;"
        f"UID={client_id};"
        f"PWD={client_secret};"
    )

    return pyodbc.connect(conn_str)


def execute_synapse_query(sql: str, params: list):

    conn = get_synapse_connection()
    cursor = conn.cursor()

    try:
        if params:
            cursor.execute(sql, params)
        else:
            cursor.execute(sql)

        columns = [col[0] for col in cursor.description]
        rows = [dict(zip(columns, row)) for row in cursor.fetchall()]
        return rows

    finally:
        cursor.close()
        conn.close()


# =========================================================
# UNIFIED EXECUTION ROUTER
# =========================================================

def execute_query_by_module(module: str, sql: str, params):

    if module == "bob":
        return execute_synapse_query(sql, params)

    # Postgres
    return execute_postgres_query(sql, params)