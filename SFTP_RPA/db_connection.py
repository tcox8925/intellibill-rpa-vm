import os
import psycopg2
from azure.identity import DefaultAzureCredential, ClientSecretCredential
from azure.keyvault.secrets import SecretClient

# Database Connection Settings
DB_CONFIG = {
    'host': os.getenv("DEFAULT834_DB_HOST", ""),
    'database': os.getenv("DEFAULT834_DB_NAME", ""),
    'username': os.getenv("DEFAULT834_DB_USER", "")
}
KEY_VAULT_URL = os.getenv("KEYVAULT_URL", "")

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


def connect_to_db():
    """Establishes and returns a database connection."""
    try:
        client_id, client_secret, tenant_id = get_db_secrets()

        sp_credential = ClientSecretCredential(
            tenant_id=tenant_id,
            client_id=client_id,
            client_secret=client_secret
        )

        token = sp_credential.get_token(
            "https://ossrdbms-aad.database.windows.net/.default"
        ).token

        conn = psycopg2.connect(
            host=DB_CONFIG["host"],
            dbname=DB_CONFIG["database"],
            user=DB_CONFIG["username"],
            password=token,
            sslmode="require"
        )
        print("Database connection established successfully.")
        return conn
    except psycopg2.Error as e:
        print(f"Database connection failed: {e}")
        return None
    except Exception as e:
        print(f"Failed during database connection: {e}")
        return None
