"""
Postgres (pch) connection and run-logging. Azure credential brokering lives
in azure_conn.py (imported here) so DB logic and cloud-auth stay separate.
"""

import uuid
import psycopg2

from .config import POSTGRES_CONFIG_EHR, POSTGRES_CONFIG_PCH
from .azure_conn import get_sp_credential


def get_pch_connection():
    """Connect to the PCH database for legacy log writes."""
    sp = get_sp_credential()
    token = sp.get_token("https://ossrdbms-aad.database.windows.net/.default").token
    return psycopg2.connect(
        host=POSTGRES_CONFIG_PCH["host"],
        dbname=POSTGRES_CONFIG_PCH["database"],
        user=POSTGRES_CONFIG_PCH["user"],
        password=token,
        sslmode="require",
    )


def get_ehr_connection():
    """Connect to the EHR database for ehr schema tables."""
    sp = get_sp_credential()
    token = sp.get_token("https://ossrdbms-aad.database.windows.net/.default").token
    return psycopg2.connect(
        host=POSTGRES_CONFIG_EHR["host"],
        dbname=POSTGRES_CONFIG_EHR["database"],
        user=POSTGRES_CONFIG_EHR["user"],
        password=token,
        sslmode="require",
    )


def log_run_to_pch(script_name, process_type, status, error,
                   company_id, started_at, ended_at,
                   carrier_id=None, file_path=None):
    """
    Write one run-log row to wpo.ops_pch_logs on both PCH and EHR/RCM.
    Never raises — a logging failure must not fail a run.
    """
    txn_id = str(uuid.uuid4())
    targets = [
        ("pch", get_pch_connection),
        ("ehr", get_ehr_connection),
    ]
    for target_name, get_connection in targets:
        try:
            conn = get_connection()
            try:
                cur = conn.cursor()
                cur.execute(
                    """
                    INSERT INTO wpo.ops_pch_logs (
                        txn_id, script_name, process_type, status, error,
                        company_id, carrier_id, file_path, started_at, ended_at
                    )
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    """,
                    (
                        txn_id, script_name, process_type, status, error,
                        company_id, carrier_id, file_path, started_at, ended_at,
                    ),
                )
                conn.commit()
            finally:
                conn.close()
        except Exception as e:
            print(f"[LOG-WRITE] Failed to write run log on {target_name}: {e}")


# =========================================================
# Appointment writes
# =========================================================
from .config import TABLE_NAME  # noqa: E402


def upsert_appointment(cur, rec):
    """Insert a new appointment (process_status explicitly NULL) or update
    status/practice on an existing one."""
    cur.execute(
        f"SELECT 1 FROM {TABLE_NAME} WHERE entity=%s AND sub_entity=%s AND appt_id=%s AND ehr_name=%s",
        (rec["entity"], rec["sub_entity"], rec["appt_id"], rec["ehr_name"]),
    )
    if cur.fetchone() is None:
        cur.execute(
            f"""
            INSERT INTO {TABLE_NAME} (
                appt_id, appt_date, appt_time, patient_name, dob,
                home_phone, mobile_phone, provider_name, service_location,
                appt_reason, appt_status, retry_flag, retry_reason,
                process_status,
                entity, sub_entity, practice, ehr_name, updated_date
            ) VALUES (
                %(appt_id)s, %(appt_date)s, %(appt_time)s, %(patient_name)s, %(dob)s,
                %(home_phone)s, %(mobile_phone)s, %(provider_name)s, %(service_location)s,
                %(appt_reason)s, %(appt_status)s, %(retry_flag)s, %(retry_reason)s,
                NULL,
                %(entity)s, %(sub_entity)s, %(practice)s, %(ehr_name)s, now()
            )
            """,
            rec,
        )
    else:
        cur.execute(
            f"""
            UPDATE {TABLE_NAME}
            SET appt_status = COALESCE(%s, appt_status),
                practice = %s, updated_date = now()
            WHERE entity=%s AND sub_entity=%s AND appt_id=%s AND ehr_name=%s
            """,
            (rec.get("appt_status"), rec.get("practice"),
             rec["entity"], rec["sub_entity"], rec["appt_id"], rec["ehr_name"]),
        )


def set_missed_charges(cur, appt_ids, entity, sub_entity, ehr_name):
    """Flag appt_ids (from Tebra's Missed Charges view) for facesheet re-download."""
    if appt_ids:
        cur.execute(
            f"""
            UPDATE {TABLE_NAME}
            SET retry_flag = 1, retry_reason = 'Missed Charges', updated_date = now()
            WHERE entity = %s AND sub_entity = %s AND ehr_name = %s
              AND appt_id = ANY(%s)
            """,
            (entity, sub_entity, ehr_name, appt_ids),
        )
