import os
import pandas as pd
import pyodbc
import paramiko
from datetime import datetime, date, timedelta
from typing import List, Dict, Union

from azure.identity import DefaultAzureCredential
from azure.keyvault.secrets import SecretClient
from azure.communication.email import EmailClient

# =========================
# RUN MODE
# =========================

TEST_MODE = False                 # True = generate report only
TEST_RUN_DAY = "Thursday"         # Used only if TEST_MODE=True
# Allowed: "Tuesday", "Thursday"

# =========================
# CONFIG
# =========================

SFTP_CONFIG = {
    "host": "ftp.amerilife.com",
    "port": 22,
    "username": os.getenv("AMERILIFE_SFTP_USERNAME", ""),
    "password": os.getenv("AMERILIFE_SFTP_PASSWORD", ""),
    "remote_path": "/inbound/"
}

NPN_LIST = ["14203513", "4562132", "661052"]

OUTPUT_DIR = r"C:\Users\myopsadmin\Downloads"
# OUTPUT_DIR = r"C:\Users\poorn\Microsoft\Downloads\acc"
os.makedirs(OUTPUT_DIR, exist_ok=True)

OUTPUT_FILE = os.path.join(
    OUTPUT_DIR,
    f"COM_Amerilife_{datetime.now().strftime('%m%d%Y')}.csv"
)

# =========================
# EMAIL CONFIG
# =========================

CONNECTION_STRING = (
    "endpoint=https://myopsemailservice.unitedstates.communication.azure.com/;"
    f"accesskey={os.getenv('ACS_ACCESS_KEY', '')}"
)
SENDER_ADDRESS = "dataops@834labs.com"
EMAIL_RECIPIENTS = ["7d733b72.enrollinsurance.com@amer.teams.ms"]

# =========================
# SYNAPSE CONNECTION
# =========================

KEY_VAULT_NAME = os.getenv("KEY_VAULT_NAME", "")
KEY_VAULT_URL  = f"https://{KEY_VAULT_NAME}.vault.azure.net/"

DB_CONFIG = {
    "server":   "834analyticsynapse.sql.azuresynapse.net",
    "database": "834_analytics_dev",
    "driver":   "{ODBC Driver 17 for SQL Server}"
}


def get_azure_secrets():
    try:
        credential    = DefaultAzureCredential()
        secret_client = SecretClient(vault_url=KEY_VAULT_URL, credential=credential)
        client_id     = secret_client.get_secret(os.getenv("KEYVAULT_CLIENT_ID_SECRET_NAME", "")).value
        client_secret = secret_client.get_secret(os.getenv("KEYVAULT_CLIENT_SECRET_NAME", "")).value
        return client_id, client_secret
    except Exception as e:
        print(f"❌ Failed to retrieve secrets from Key Vault: {e}")
        return None, None


def connect_to_db():
    client_id, client_secret = get_azure_secrets()
    if not client_id or not client_secret:
        print("❌ Missing credentials. Cannot connect.")
        return None

    conn_str = (
        f"DRIVER={DB_CONFIG['driver']};"
        f"SERVER={DB_CONFIG['server']};"
        f"DATABASE={DB_CONFIG['database']};"
        "Authentication=ActiveDirectoryServicePrincipal;"
        f"UID={client_id};"
        f"PWD={client_secret};"
    )
    try:
        conn = pyodbc.connect(conn_str)
        print("✅ Database connection established.")
        return conn
    except pyodbc.Error as e:
        print(f"❌ Database connection failed: {e}")
        return None


# =========================
# DATE WINDOW LOGIC
# =========================

def get_reporting_window(test_mode=False, test_run_day=None):
    """
    PROD (Tue/Thu): use the CURRENT run day window
      - Tuesday  run → Thu–Mon (ending Monday night)
      - Thursday run → Tue–Wed (ending Wednesday night)

    TEST: simulate PREVIOUS run day window (last completed cycle)
    """

    today = date.today()

    if test_mode:
        if test_run_day not in ("Tuesday", "Thursday"):
            raise ValueError("TEST_RUN_DAY must be 'Tuesday' or 'Thursday'")
        run_day = test_run_day
    else:
        run_day = today.strftime("%A")
        if run_day not in ("Tuesday", "Thursday"):
            raise RuntimeError("Script should only run on Tuesday or Thursday in PROD")

    weekday_map = {
        "Monday": 0,
        "Tuesday": 1,
        "Wednesday": 2,
        "Thursday": 3,
        "Friday": 4,
        "Saturday": 5,
        "Sunday": 6,
    }

    today_idx = today.weekday()
    run_idx   = weekday_map[run_day]

    if test_mode:
        days_back = (today_idx - run_idx) % 7
        if days_back == 0:
            days_back = 7
        run_date = today - timedelta(days=days_back)
    else:
        run_date = today

    if run_day == "Tuesday":
        start_date = run_date - timedelta(days=5)   # Thursday
        end_date   = run_date                        # Tuesday (exclusive)
    else:  # Thursday
        start_date = run_date - timedelta(days=2)    # Tuesday
        end_date   = run_date                        # Thursday (exclusive)

    return start_date.strftime("%Y-%m-%d"), end_date.strftime("%Y-%m-%d")


# =========================
# EMAIL FUNCTIONS
# =========================

def _split_recipients(to: Union[str, List[str]]) -> List[Dict[str, str]]:
    return [{"address": addr.strip()} for addr in (to if isinstance(to, list) else to.split(","))]


def send_email(to, subject, body):
    client = EmailClient.from_connection_string(CONNECTION_STRING)
    message = {
        "senderAddress": SENDER_ADDRESS,
        "recipients": {"to": _split_recipients(to)},
        "content": {"subject": subject, "plainText": body}
    }
    poller = client.begin_send(message)
    poller.result()
    print("Email notification sent.")


# =========================
# SFTP UPLOAD
# =========================

def upload_to_sftp(file_path):
    print(f"Uploading to SFTP: {SFTP_CONFIG['host']}")
    transport = paramiko.Transport((SFTP_CONFIG["host"], SFTP_CONFIG["port"]))
    transport.connect(
        username=SFTP_CONFIG["username"],
        password=SFTP_CONFIG["password"]
    )
    sftp = paramiko.SFTPClient.from_transport(transport)
    remote_file = os.path.join(
        SFTP_CONFIG["remote_path"],
        os.path.basename(file_path)
    )
    sftp.put(file_path, remote_file)
    sftp.close()
    transport.close()
    print(f"Upload complete: {remote_file}")


# =========================
# MAIN EXPORT
# =========================

def export_data():
    start_date, end_date = get_reporting_window(
        test_mode=TEST_MODE,
        test_run_day=TEST_RUN_DAY
    )
    display_end_date = (
        datetime.strptime(end_date, "%Y-%m-%d").date() - timedelta(days=1)
    ).strftime("%Y-%m-%d")

    print(f"📆 Reporting Window: {start_date} → {display_end_date}")

    npn_sql = ",".join([f"'{x}'" for x in NPN_LIST])

    query = f"""
        WITH npn_filter AS (
            SELECT
                job_id, txn_id_com_header, company_id, company_name,
                carrier_id, carrier_name, npn, agent_name, agility_id,
                associated_statement, writing_agent_npn, writing_agent,
                payment_schedule, payment, payment_type, policy_state,
                effective_date, coverage_month, market, insured_name,
                months, [plan], lives, premium, split, first_year_renewal,
                account_number, memo, statement_month, report_date,
                load_date, raw_file_name,
                1 AS source_priority
            FROM com.com_items
            WHERE npn IN ({npn_sql})
              AND load_date >= '{start_date}'
              AND load_date <  '{end_date}'

            UNION ALL

            SELECT
                job_id, txn_id_com_header, company_id, company_name,
                carrier_id, carrier_name, npn, agent_name, agility_id,
                associated_statement, writing_agent_npn, writing_agent,
                payment_schedule, payment, payment_type, policy_state,
                effective_date, coverage_month, market, insured_name,
                months, [plan], lives, premium, split, first_year_renewal,
                account_number, memo, statement_month, report_date,
                load_date, raw_file_name,
                2 AS source_priority
            FROM com.com_items_history
            WHERE npn IN ({npn_sql})
              AND load_date >= '{start_date}'
              AND load_date <  '{end_date}'

            UNION ALL

            SELECT
                job_id, txn_id_com_header, company_id, company_name,
                carrier_id, carrier_name, npn, agent_name, agility_id,
                associated_statement, writing_agent_npn, writing_agent,
                payment_schedule, payment, payment_type, policy_state,
                effective_date, coverage_month, market, insured_name,
                months, [plan], lives, premium, split, first_year_renewal,
                account_number, memo, statement_month, report_date,
                load_date, raw_file_name,
                3 AS source_priority
            FROM com.vw_unified_com_items
            WHERE npn IN ({npn_sql})
              AND load_date >= '{start_date}'
              AND load_date <  '{end_date}'
        ),

        deduped AS (
            SELECT *,
                ROW_NUMBER() OVER (
                    PARTITION BY txn_id_com_header, npn
                    ORDER BY source_priority ASC
                ) AS rn
            FROM npn_filter
        ),

        base AS (
            SELECT * FROM deduped WHERE rn = 1
        )

        SELECT
            b.carrier_name,
            b.npn,
            b.agent_name,
            b.associated_statement,
            b.writing_agent_npn,
            b.writing_agent,
            b.payment,
            b.payment_type,
            b.policy_state,
            b.effective_date,
            b.coverage_month,
            b.market,
            b.insured_name,
            b.months,
            b.lives,
            b.premium,
            b.report_date,
            b.load_date,
            r.amerilife_affiliate_form_name,
            r.marketer,
            r.upline_npn,
            r.top_upline_npn,

            CASE
                WHEN u.last_name = '.' THEN u.first_name
                WHEN u.first_name IS NULL AND u.last_name IS NULL THEN NULL
                ELSE TRIM(COALESCE(u.first_name, '') + ' ' + COALESCE(u.last_name, ''))
            END AS upline_name,

            CASE
                WHEN tu.last_name = '.' THEN tu.first_name
                WHEN tu.first_name IS NULL AND tu.last_name IS NULL THEN NULL
                ELSE TRIM(COALESCE(tu.first_name, '') + ' ' + COALESCE(tu.last_name, ''))
            END AS top_upline_name

        FROM base b
        LEFT JOIN com.lup_agents_contracts r
            ON b.writing_agent_npn = r.npn
           AND b.carrier_id = r.carrier
        LEFT JOIN com.lup_agents u
            ON r.upline_npn = u.npn
        LEFT JOIN com.lup_agents tu
            ON r.top_upline_npn = tu.npn

        ORDER BY b.npn, b.carrier_name, b.load_date
    """

    conn = connect_to_db()
    if not conn:
        return

    print("🔍 Running query...")
    df = pd.read_sql(query, conn)
    conn.close()

    df.to_csv(OUTPUT_FILE, index=False)

    rows    = len(df)
    size_mb = round(os.path.getsize(OUTPUT_FILE) / (1024 * 1024), 2)

    print(f"File created: {OUTPUT_FILE}")
    print(f"Records: {rows}, Size: {size_mb} MB")

    if not TEST_MODE:
        upload_to_sftp(OUTPUT_FILE)

        body = f"""
Amerilife Commissions File Uploaded

File Name : {os.path.basename(OUTPUT_FILE)}
Records   : {rows}
File Size : {size_mb} MB
Date Range: {start_date} → {display_end_date}
SFTP Host : {SFTP_CONFIG['host']}
"""
        send_email(
            EMAIL_RECIPIENTS,
            "Amerilife Commission File Uploaded",
            body
        )

        os.remove(OUTPUT_FILE)
        print("Local file deleted")
    else:
        print("TEST MODE: No SFTP upload, no email sent")


# =========================
# MAIN
# =========================

if __name__ == "__main__":
    export_data()