import os
import psycopg2
from datetime import datetime
from typing import List, Dict
from azure.identity import DefaultAzureCredential, ClientSecretCredential
from azure.keyvault.secrets import SecretClient

#config

KEY_VAULT_URL = os.getenv("KEYVAULT_URL", "")

POSTGRES_CONFIG = {
    "host": os.getenv("DEFAULT834_DB_HOST", ""),
    "database": os.getenv("DEFAULT834_DB_NAME", ""),
    "user": os.getenv("DEFAULT834_DB_USER", ""),
}


#connection

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


def _utc_now():
    return datetime.utcnow()


#read

def fetch_unprocessed_states() -> List[Dict]:
    """
    Fetch all states that have not yet been processed
    for the current run.
    """
    query = """
        SELECT
            jurisdiction,
            entity_type,
            license_type,
            line_of_authority,
            license_type_id,
            jur_short
        FROM wpo.ops_sbs_matrix
        WHERE processed = FALSE
        ORDER BY jurisdiction;
    """

    with get_postgres_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(query)
            cols = [desc[0] for desc in cur.description]
            return [dict(zip(cols, row)) for row in cur.fetchall()]


#updates per state

def mark_state_success(
    jurisdiction: str,
    rows_count: int,
    fee_amount,
    pin_number: str,
    transaction_number: str,
    authorization_payment_number: str
):
    """
    Mark a state as successfully processed.
    """
    query = """
        UPDATE wpo.ops_sbs_matrix
        SET
            processed = TRUE,
            rows_count = %s,
            fee_amount = %s,
            pin_number = %s,
            transaction_number = %s,
            authorization_payment_number = %s,
            start_datetime = COALESCE(start_datetime, %s),
            end_datetime = %s,
            updated_ts = %s
        WHERE jurisdiction = %s;
    """

    now = _utc_now()

    with get_postgres_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                query,
                (
                    rows_count,
                    fee_amount,
                    pin_number,
                    transaction_number,
                    authorization_payment_number,
                    now,
                    now,
                    now,
                    jurisdiction
                )
            )
            conn.commit()


def mark_state_failure(
    jurisdiction: str,
    error_message: str,
    pin_number: str = None,
    transaction_number: str = None,
):
    """
    Mark a state as failed.

    IMPORTANT:
    - Payment may have succeeded even if the state failed later.
    - Preserve pin_number / transaction_number when available.
    """

    query = """
        UPDATE wpo.ops_sbs_matrix
        SET
            processed = TRUE,
            error_message = %s,
            pin_number = %s,
            transaction_number = %s,
            end_datetime = %s,
            updated_ts = %s
        WHERE jurisdiction = %s;
    """

    now = _utc_now()

    with get_postgres_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                query,
                (
                    error_message,
                    pin_number,
                    transaction_number,
                    now,
                    now,
                    jurisdiction,
                )
            )
            conn.commit()



#report tracking

def mark_report_exists(jurisdiction: str):
    query = """
        UPDATE wpo.ops_sbs_matrix
        SET
            report_exists = TRUE,
            updated_ts = %s
        WHERE jurisdiction = %s;
    """

    now = _utc_now()

    with get_postgres_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(query, (now, jurisdiction))
            conn.commit()


#matrix reset

def reset_run_columns():
    """
    Reset only RPA-managed columns.
    MUST be called only after full successful run.
    """
    query = """
        UPDATE wpo.ops_sbs_matrix
        SET
            processed = FALSE,
            rows_count = NULL,
            login = NULL,
            fee_amount = NULL,
            error_message = NULL,
            pin_number = NULL,
            transaction_number = NULL,
            authorization_payment_number = NULL,
            report_exists = FALSE,
            start_datetime = NULL,
            end_datetime = NULL,
            updated_ts = %s;
    """

    now = _utc_now()

    with get_postgres_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(query, (now,))
            conn.commit()

def insert_rpa_run_log(
    script_name: str,
    start_datetime,
    end_datetime,
    success_message: str = None,
    error_message: str = None,
    file_status: str = None,
    file_path: str = None,
):
    """
    Insert a single run-level log into wpo.ops_rpa_script_logs
    """

    query = """
        INSERT INTO wpo.ops_rpa_script_logs (
            script_name,
            start_datetime,
            end_datetime,
            error,
            success,
            file_status,
            file_path,
            process_type,
            company_id,
            sub_entity_id
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, 'ALU', '270681372','270681372001');
    """

    with get_postgres_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                query,
                (
                    script_name,
                    start_datetime,
                    end_datetime,
                    error_message,
                    success_message,
                    file_status,
                    file_path,
                )
            )
            conn.commit()
