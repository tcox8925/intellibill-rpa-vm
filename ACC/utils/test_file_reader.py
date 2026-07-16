import os
from azure.identity import ClientSecretCredential
import psycopg2

from azure.identity import DefaultAzureCredential
from azure.keyvault.secrets import SecretClient

KEY_VAULT_URL = os.getenv("KEYVAULT_URL", "")

def get_postgres_db_secrets():
    credential = DefaultAzureCredential()
    client = SecretClient(vault_url=KEY_VAULT_URL, credential=credential)

    client_id = client.get_secret(os.getenv("KEYVAULT_CLIENT_ID_SECRET_NAME", "")).value
    client_secret = client.get_secret(os.getenv("KEYVAULT_CLIENT_SECRET_NAME", "")).value
    tenant_id = client.get_secret(os.getenv("KEYVAULT_TENANT_ID_SECRET_NAME", "")).value

    return client_id, client_secret, tenant_id

DB_CONFIG_POSTGRES = {
    'server': os.getenv("DEFAULT834_DB_HOST", ""),
    'database': os.getenv("DEFAULT834_DB_NAME", "")
}



def get_postgres_connection():
    client_id, client_secret, tenant_id = get_postgres_db_secrets()

    # Generate AAD token for PostgreSQL
    credential = ClientSecretCredential(tenant_id, client_id, client_secret)
    token = credential.get_token("https://ossrdbms-aad.database.windows.net/.default").token

    conn = psycopg2.connect(
        host=DB_CONFIG_POSTGRES['server'],
        dbname=DB_CONFIG_POSTGRES['database'],
        user="834data_syndb_adm",
        password=token,
        sslmode="require"
    )
    return conn

def list_postgres_tables():
    conn = get_postgres_connection()
    cur = conn.cursor()
    cur.execute("""
        SELECT table_schema, table_name 
        FROM information_schema.tables 
        WHERE table_type='BASE TABLE'
        ORDER BY table_schema, table_name;
    """)
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return rows

if __name__ == "__main__":
    print("📋 Listing Tables from Azure PostgreSQL Flexible Server:\n")
    for schema, table in list_postgres_tables():
        print(f"{schema}.{table}")

