import os
import json
import psycopg2
from azure.identity import DefaultAzureCredential, ClientSecretCredential
from azure.keyvault.secrets import SecretClient
from datetime import datetime, date
from pytz import timezone


# =========================
# HELPERS
# =========================
def to_date(value):
    if value is None:
        return None
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, str):
        return datetime.fromisoformat(value.replace("Z", "")).date()
    raise TypeError(f"Unsupported type for date conversion: {type(value)}")


def get_process_code(process_name: str) -> str:
    if not process_name:
        return ""
    return process_name.strip().upper()[:3]


def safe_json_loads(s: str):
    try:
        return json.loads(s) if s else None
    except Exception:
        return None


def safe_json_dumps(obj):
    return json.dumps(obj, ensure_ascii=False)


# =========================
# CONFIG
# =========================
KEY_VAULT_URL = os.getenv("KEYVAULT_URL", "")

DB_CONFIG_POSTGRES = {
    "server": os.getenv("DEFAULT834_DB_HOST", ""),
    "database": os.getenv("DEFAULT834_DB_NAME", ""),
    "user": os.getenv("DEFAULT834_DB_USER", ""),
}

DEFAULT_ENTITY_ID = "990980340"
DEFAULT_SUB_ENTITY_ID = "990980340001"


# =========================
# AUTH
# =========================
def get_postgres_db_secrets():
    credential = DefaultAzureCredential()
    client = SecretClient(vault_url=KEY_VAULT_URL, credential=credential)

    client_id = client.get_secret(os.getenv("KEYVAULT_CLIENT_ID_SECRET_NAME", "")).value
    client_secret = client.get_secret(os.getenv("KEYVAULT_CLIENT_SECRET_NAME", "")).value
    tenant_id = client.get_secret(os.getenv("KEYVAULT_TENANT_ID_SECRET_NAME", "")).value

    return client_id, client_secret, tenant_id


def get_postgres_connection():
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
        sslmode="require",
    )
    return conn


# =========================
# COLUMN EXISTENCE CHECK
# =========================
def column_exists(cur, schema: str, table: str, column: str) -> bool:
    cur.execute("""
        SELECT 1
        FROM information_schema.columns
        WHERE table_schema = %s
          AND table_name   = %s
          AND column_name  = %s
        LIMIT 1
    """, (schema, table, column))
    return cur.fetchone() is not None


# =========================
# QUERY TO GET LATEST STATE
# =========================
LATEST_STATE_SQL = """
WITH today_logs AS (
    SELECT *
    FROM wpo.ops_rpa_script_logs
    WHERE
        end_datetime IS NOT NULL
        AND process_type IN ('BOB','ACU')
        AND timezone('America/Chicago', end_datetime::timestamp)::date =
            timezone('America/Chicago', now())::date
),

ordered AS (
    SELECT *,
           ROW_NUMBER() OVER (
               PARTITION BY script_name
               ORDER BY end_datetime DESC
           ) AS rn
    FROM today_logs
),

latest AS (
    SELECT * FROM ordered WHERE rn = 1
),

previous AS (
    SELECT script_name, error, success
    FROM ordered
    WHERE rn = 2
),

resolved_state AS (
    SELECT
        l.script_name,
        l.process_type,
        l.end_datetime,
        l.file_path,
        l.sub_entity_id,
        l.success,
        l.error,

        CASE
            WHEN l.error IS NOT NULL
                THEN SUBSTRING(l.error FROM 'E[0-9]{3}')

            WHEN l.error IS NULL
                 AND l.success IS NULL
                 AND p.error IS NOT NULL
                THEN SUBSTRING(p.error FROM 'E[0-9]{3}')

            ELSE NULL
        END AS error_code

    FROM latest l
    LEFT JOIN previous p
      ON l.script_name = p.script_name
)

SELECT
    timezone('America/Chicago', now())::date   AS report_date,
    f.process_type                            AS process_name,
    m.carrier_id,
    c.vendor_name                             AS carrier_name,
    f.file_path                               AS raw_file_name,
    timezone('America/Chicago', f.end_datetime::timestamp)::date AS issue_date,
    m.cadence,
    m.company_id                              AS business_entity,
    f.sub_entity_id                           AS business_sub_entity,
    f.success,
    f.error,
    f.error_code,

    CASE
        WHEN error_code = 'E001' THEN
            'Login Issue — Unable to sign in to the carrier system. Automation could not access the carrier portal.'

        WHEN error_code IN ('E002','E010','E011','E012','E015') THEN
            'Portal / Navigation Issue — ' ||
            CASE error_code
                WHEN 'E002' THEN 'Expected download option was not available on the carrier portal.'
                WHEN 'E010' THEN 'Required link was not found on the carrier portal.'
                WHEN 'E011' THEN 'Navigation within the carrier portal failed.'
                WHEN 'E012' THEN 'One-time password (OTP) verification could not be completed.'
                WHEN 'E015' THEN 'Required filter or search condition could not be applied.'
            END

        ELSE
            'Operational Issue — ' ||
            CASE error_code
                WHEN 'E003' THEN 'Automation process was interrupted before completion.'
                WHEN 'E004' THEN 'File download from the carrier portal failed.'
                WHEN 'E005' THEN 'Downloaded file could not be uploaded to internal systems.'
                WHEN 'E006' THEN 'Temporary system connectivity issue prevented processing.'
                WHEN 'E007' THEN 'Downloaded data could not be saved to the database.'
                WHEN 'E008' THEN 'Temporary file cleanup failed during processing.'
                WHEN 'E009' THEN 'Automation did not complete and no specific error was logged.'
                WHEN 'E013' THEN 'File download did not complete successfully.'
                WHEN 'E014' THEN 'Expected file was not available for processing.'
            END
    END AS issue_description

FROM resolved_state f
JOIN wpo.ops_rpa_matrix m
  ON f.script_name = m.script_name
JOIN wpo.lup_carriers c
  ON m.carrier_id = c.id
ORDER BY f.end_datetime DESC;
"""


# =========================
# NOTES CARRY FORWARD
# =========================
def carry_forward_if_missing(cur, *, carrier_id: str, process_code: str, today_cst: date):
    if not process_code:
        return

    cur.execute("""
        SELECT id, notes
        FROM ops_srv.ops_automation_dashboard
        WHERE carrier_id = %s
          AND record_date = %s
        LIMIT 1
    """, (carrier_id, today_cst))
    today_row = cur.fetchone()

    if not today_row:
        return

    today_id, today_notes_raw = today_row
    today_notes_obj = today_notes_raw

    if isinstance(today_notes_obj, dict) and process_code in today_notes_obj:
        return

    cur.execute("""
        SELECT notes
        FROM ops_srv.ops_automation_dashboard
        WHERE carrier_id = %s
          AND record_date < %s
        ORDER BY record_date DESC
        LIMIT 1
    """, (carrier_id, today_cst))
    prev_row = cur.fetchone()

    if not prev_row:
        return

    prev_notes_obj = prev_row[0]

    if not isinstance(prev_notes_obj, dict):
        return

    if process_code not in prev_notes_obj:
        return

    if not isinstance(today_notes_obj, dict):
        today_notes_obj = {}

    today_notes_obj[process_code] = prev_notes_obj[process_code]

    cur.execute("""
        UPDATE ops_srv.ops_automation_dashboard
        SET notes = %s,
            carry_over_flag = 'true'::jsonb,
            last_updated = NOW()
        WHERE id = %s
    """, (safe_json_dumps(today_notes_obj), today_id))


# =========================
# MAIN ENGINE (PATCHED)
# =========================
def run_service_interruption_engine():
    conn = get_postgres_connection()
    cur = conn.cursor()

    has_rpa_col = column_exists(cur, "ops_srv", "service_interruption", "rpa")
    if has_rpa_col:
        print("[INFO] 'rpa' column detected — using rpa filter")
    else:
        print("[WARN] 'rpa' column NOT found — falling back to (process_name, carrier_id) only")

    RPA_FLAG = 1

    print("[INFO] Checking latest RPA states...")
    cur.execute(LATEST_STATE_SQL)
    rows = cur.fetchall()
    cols = [d[0] for d in cur.description]

    central = timezone("America/Chicago")
    today_cst = datetime.now(central).date()

    for row in rows:
        r = dict(zip(cols, row))

        interruption_touched = False

        process_name = r["process_name"]
        process_code = get_process_code(process_name)

        carrier_id = str(r["carrier_id"])
        carrier_name = r["carrier_name"]

        issue_date_raw = r["issue_date"]
        issue_date = to_date(issue_date_raw)

        desc = r.get("issue_description")
        error_code = r.get("error_code")

        print(f"\n🔎 Checking {carrier_name} | {process_name} | {issue_date}")

        business_entity = str(r.get("business_entity")) if r.get("business_entity") is not None else None
        business_sub_entity = str(r.get("business_sub_entity")) if r.get("business_sub_entity") is not None else None

        # ── FIX #4: Prioritize Open records in dedup lookup ──────────────
        # Previously ordered by issue_date DESC, id DESC only.
        # If a Resolved row had a later issue_date than an existing Open row,
        # the Resolved row would be found first, causing the engine to
        # insert a duplicate via the "prev_status != Open" branch.
        # Now Open records are always preferred, preventing duplicate inserts.
        if has_rpa_col:
            cur.execute("""
                SELECT id, issue_date, issue_description, issue_status, issue_count
                FROM ops_srv.service_interruption
                WHERE process_name = %s
                  AND carrier_id = %s
                  AND rpa = %s
                ORDER BY
                    CASE WHEN issue_status = 'Open' THEN 0 ELSE 1 END,
                    issue_date DESC,
                    id DESC
                LIMIT 1
            """, (process_name, carrier_id, RPA_FLAG))
        else:
            cur.execute("""
                SELECT id, issue_date, issue_description, issue_status, issue_count
                FROM ops_srv.service_interruption
                WHERE process_name = %s
                  AND carrier_id = %s
                ORDER BY
                    CASE WHEN issue_status = 'Open' THEN 0 ELSE 1 END,
                    issue_date DESC,
                    id DESC
                LIMIT 1
            """, (process_name, carrier_id))
        existing = cur.fetchone()

        # ── No prior interruption ──
        if not existing:
            if error_code:
                insert_interruption(
                    cur, r,
                    default_entity_id=DEFAULT_ENTITY_ID,
                    default_sub_entity_id=DEFAULT_SUB_ENTITY_ID,
                    business_entity=business_entity,
                    business_sub_entity=business_sub_entity,
                    issue_count=1,
                    report_date=today_cst,
                    rpa_flag=RPA_FLAG,
                    has_rpa_col=has_rpa_col,
                )
                interruption_touched = True
                print(f"[CREATE]  {carrier_name} {process_name} — new failure logged")

                if interruption_touched:
                    carry_forward_if_missing(
                        cur, carrier_id=carrier_id,
                        process_code=process_code, today_cst=today_cst
                    )
            else:
                print(f"[IGNORE]  {carrier_name} {process_name} — success, no prior issue")
            conn.commit()
            continue

        prev_id, prev_issue_date_raw, prev_desc, prev_status, prev_count = existing
        prev_issue_date = to_date(prev_issue_date_raw)

        # ── FIX #1: Resolve on success — no date constraint ──
        # If the latest run succeeded and the most recent issue is open, resolve it.
        # Previously required prev_issue_date == today_cst, which meant
        # issues from prior days could never be resolved by a success today.
        if (error_code is None) and (prev_status == "Open"):
            cur.execute("""
                UPDATE ops_srv.service_interruption
                SET issue_status = 'Resolved',
                    resolution_date = %s,
                    resolution_description = 'Automation completed successfully in latest run',
                    updated_on = NOW() AT TIME ZONE 'America/Chicago'
                WHERE id = %s
                  AND issue_status = 'Open'
            """, (issue_date, prev_id))

            interruption_touched = True
            print(f"[RESOLVE] {carrier_name} {process_name} — recovered (issue was from {prev_issue_date})")
            conn.commit()
            continue

        # ── Current run is a failure ──
        if error_code:

            # Same open issue persisting across days — increment count
            if (prev_status == "Open") and (prev_desc == desc) and (issue_date is not None) and (prev_issue_date is not None) and (issue_date > prev_issue_date):
                cur.execute("""
                    UPDATE ops_srv.service_interruption
                    SET issue_count = COALESCE(issue_count, 1) + 1,
                        issue_date = %s,
                        updated_on = NOW() AT TIME ZONE 'America/Chicago'
                    WHERE id = %s
                      AND issue_status = 'Open'
                """, (issue_date, prev_id))

                interruption_touched = True
                print(f"[INCREMENT] {carrier_name} {process_name} — recurring issue count updated")

                if interruption_touched:
                    carry_forward_if_missing(
                        cur, carrier_id=carrier_id,
                        process_code=process_code, today_cst=today_cst
                    )
                conn.commit()
                continue

            # Same day + same description — skip
            if (prev_issue_date == issue_date) and (prev_desc == desc) and (prev_status == "Open"):
                print(f"[SKIP]    {carrier_name} {process_name} — already recorded today")

                carry_forward_if_missing(
                    cur, carrier_id=carrier_id,
                    process_code=process_code, today_cst=today_cst
                )
                conn.commit()
                continue

            # ── FIX #2: Description changed — close old before inserting new ──
            if prev_status == "Open" and prev_desc != desc:
                if prev_issue_date == issue_date:
                    # Same day, description changed — update in place
                    cur.execute("""
                        UPDATE ops_srv.service_interruption
                        SET issue_description = %s,
                            raw_file_name = %s,
                            updated_on = NOW() AT TIME ZONE 'America/Chicago'
                        WHERE id = %s
                          AND issue_status = 'Open'
                    """, (desc, r["raw_file_name"], prev_id))

                    interruption_touched = True
                    print(f"[UPDATE]  {carrier_name} {process_name} — issue type changed, updated existing row")
                else:
                    # Different day, description changed — close old, insert new
                    cur.execute("""
                        UPDATE ops_srv.service_interruption
                        SET issue_status = 'Resolved',
                            resolution_date = %s,
                            resolution_description = 'Issue type changed — superseded by new interruption',
                            updated_on = NOW() AT TIME ZONE 'America/Chicago'
                        WHERE id = %s
                          AND issue_status = 'Open'
                    """, (issue_date, prev_id))
                    print(f"[CLOSE]   {carrier_name} {process_name} — closed prior issue (desc changed)")

                    insert_interruption(
                        cur, r,
                        default_entity_id=DEFAULT_ENTITY_ID,
                        default_sub_entity_id=DEFAULT_SUB_ENTITY_ID,
                        business_entity=business_entity,
                        business_sub_entity=business_sub_entity,
                        issue_count=1,
                        report_date=today_cst,
                        rpa_flag=RPA_FLAG,
                        has_rpa_col=has_rpa_col,
                    )
                    interruption_touched = True
                    print(f"[CREATE]  {carrier_name} {process_name} — new issue type logged")

                if interruption_touched:
                    carry_forward_if_missing(
                        cur, carrier_id=carrier_id,
                        process_code=process_code, today_cst=today_cst
                    )
                conn.commit()
                continue

            # Previous is resolved and we have a failure — new incident
            if prev_status != "Open":
                insert_interruption(
                    cur, r,
                    default_entity_id=DEFAULT_ENTITY_ID,
                    default_sub_entity_id=DEFAULT_SUB_ENTITY_ID,
                    business_entity=business_entity,
                    business_sub_entity=business_sub_entity,
                    issue_count=1,
                    report_date=today_cst,
                    rpa_flag=RPA_FLAG,
                    has_rpa_col=has_rpa_col,
                )
                interruption_touched = True
                print(f"[CREATE]  {carrier_name} {process_name} — new failure after resolution")

                if interruption_touched:
                    carry_forward_if_missing(
                        cur, carrier_id=carrier_id,
                        process_code=process_code, today_cst=today_cst
                    )
                conn.commit()
                continue

    conn.commit()
    cur.close()
    conn.close()


# =========================
# INSERT
# =========================
def insert_interruption(cur, r, *, default_entity_id: str, default_sub_entity_id: str,
                       business_entity: str, business_sub_entity: str, issue_count: int,
                       report_date: date, rpa_flag: int, has_rpa_col: bool):
    if has_rpa_col:
        cur.execute("""
            INSERT INTO ops_srv.service_interruption (
                report_date,
                process_name,
                carrier_id,
                carrier_name,
                raw_file_name,
                received,
                processed,
                issue_description,
                issue_status,
                issue_date,
                cadence,
                entity_id,
                sub_entity_id,
                buisness_entity,
                buisness_sub_entity,
                issue_count,
                rpa,
                updated_on
            )
            VALUES (
                %s,
                %s,%s,%s,%s,
                FALSE,FALSE,
                %s,'Open',
                %s,%s,
                %s,%s,
                %s,%s,
                %s,
                %s,
                NOW() AT TIME ZONE 'America/Chicago'
            )
        """, (
            report_date,
            r["process_name"],
            str(r["carrier_id"]),
            r["carrier_name"],
            r["raw_file_name"],
            r["issue_description"],
            to_date(r["issue_date"]),
            r["cadence"],
            default_entity_id,
            default_sub_entity_id,
            business_entity,
            business_sub_entity,
            int(issue_count),
            int(rpa_flag),
        ))
    else:
        cur.execute("""
            INSERT INTO ops_srv.service_interruption (
                report_date,
                process_name,
                carrier_id,
                carrier_name,
                raw_file_name,
                received,
                processed,
                issue_description,
                issue_status,
                issue_date,
                cadence,
                entity_id,
                sub_entity_id,
                buisness_entity,
                buisness_sub_entity,
                issue_count,
                updated_on
            )
            VALUES (
                %s,
                %s,%s,%s,%s,
                FALSE,FALSE,
                %s,'Open',
                %s,%s,
                %s,%s,
                %s,%s,
                %s,
                NOW() AT TIME ZONE 'America/Chicago'
            )
        """, (
            report_date,
            r["process_name"],
            str(r["carrier_id"]),
            r["carrier_name"],
            r["raw_file_name"],
            r["issue_description"],
            to_date(r["issue_date"]),
            r["cadence"],
            default_entity_id,
            default_sub_entity_id,
            business_entity,
            business_sub_entity,
            int(issue_count),
        ))


if __name__ == "__main__":
    run_service_interruption_engine()