"""Postgres connection helpers for EHR pipeline writes."""

import psycopg2

from .config import POSTGRES_CONFIG_EHR


def _resolve_db_password(db_config):
    """Use explicit DB password only."""
    if db_config.get("password"):
        return db_config["password"]
    raise RuntimeError(
        "Database password is required for password auth mode. "
        "Set the corresponding *_DB_PASSWORD in .env."
    )


def get_ehr_connection():
    """Connect to the EHR database for ehr schema tables."""
    return psycopg2.connect(
        host=POSTGRES_CONFIG_EHR["host"],
        dbname=POSTGRES_CONFIG_EHR["database"],
        user=POSTGRES_CONFIG_EHR["user"],
        password=_resolve_db_password(POSTGRES_CONFIG_EHR),
        sslmode="require",
    )


def log_run_event(script_name, process_type, status, error,
                  company_id, started_at, ended_at,
                  carrier_id=None, file_path=None):
    """Run-log writes are intentionally disabled in the new package."""
    return


def ensure_appointments_schema():
    """Apply idempotent column additions required by the EHR pipeline."""
    conn = get_ehr_connection()
    try:
        cur = conn.cursor()
        try:
            cur.execute(
                f"""
                ALTER TABLE {TABLE_NAME}
                ADD COLUMN IF NOT EXISTS patient_match BOOLEAN,
                ADD COLUMN IF NOT EXISTS file_path TEXT
                """
            )
            conn.commit()
        finally:
            cur.close()
    finally:
        conn.close()


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
