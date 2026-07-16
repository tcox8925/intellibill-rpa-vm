import os
import base64
import pandas as pd
from datetime import datetime, timedelta, date
from concurrent.futures import ThreadPoolExecutor, as_completed
import pytz
import psycopg2
from typing import List, Dict, Union
from azure.identity import DefaultAzureCredential, ClientSecretCredential
from azure.keyvault.secrets import SecretClient
from azure.communication.email import EmailClient

# =========================
# KEY VAULT
# =========================

KEY_VAULT_URL  = os.getenv("KEYVAULT_URL", "")

# =========================
# TIME UTILS
# =========================

CST       = pytz.timezone("US/Central")
today_cst = datetime.now(CST).date()

# =========================
# OUTPUT
# =========================

OUTPUT_DIR  = r"C:\Users\myopsadmin\Downloads"
os.makedirs(OUTPUT_DIR, exist_ok=True)

OUTPUT_FILE = f"agent_notifications_{today_cst}.xlsx"
OUTPUT_PATH = os.path.join(OUTPUT_DIR, OUTPUT_FILE)

# =========================
# EMAIL CONFIG
# =========================

EMAIL_TO = ["Gpruse@luminoscreative.com", "fvasquez@luminoscreative.com", "marketing@luminoscreative.com"]
EMAIL_CC = ["dataops@834labs.com"]
SENDER_ADDRESS = "dataops@834labs.com"

CONNECTION_STRING = (
    "endpoint=https://myopsemailservice.unitedstates.communication.azure.com/;"
    f"accesskey={os.getenv('ACS_ACCESS_KEY', '')}"
)

# =========================
# DB CONFIG
# =========================

DB_CONFIG_POSTGRES = {
    "server":   os.getenv("DEFAULT834_DB_HOST", ""),
    "database": os.getenv("DEFAULT834_DB_NAME", ""),
}

# =========================
# DB UTILS
# Cache secrets so parallel threads don't each hit Key Vault
# =========================

_secrets_cache: dict = {}

def get_postgres_db_secrets():
    if not _secrets_cache:
        credential = DefaultAzureCredential()
        client     = SecretClient(vault_url=KEY_VAULT_URL, credential=credential)
        _secrets_cache["client_id"]     = client.get_secret(os.getenv("KEYVAULT_CLIENT_ID_SECRET_NAME", "")).value
        _secrets_cache["client_secret"] = client.get_secret(os.getenv("KEYVAULT_CLIENT_SECRET_NAME", "")).value
        _secrets_cache["tenant_id"]     = client.get_secret(os.getenv("KEYVAULT_TENANT_ID_SECRET_NAME", "")).value
    return _secrets_cache["client_id"], _secrets_cache["client_secret"], _secrets_cache["tenant_id"]


def get_postgres_connection():
    client_id, client_secret, tenant_id = get_postgres_db_secrets()
    credential = ClientSecretCredential(tenant_id, client_id, client_secret)
    token = credential.get_token(
        "https://ossrdbms-aad.database.windows.net/.default"
    ).token
    conn = psycopg2.connect(
        host=DB_CONFIG_POSTGRES["server"],
        dbname=DB_CONFIG_POSTGRES["database"],
        user=os.getenv("DEFAULT834_DB_USER", ""),
        password=token,
        sslmode="require",
    )
    return conn

# =========================
# DATE WINDOWS
# =========================

def birthday_window(run_date: date):
    """Birthdays 14 days ahead, Sunday → Saturday"""
    anchor = run_date + timedelta(days=14)
    start  = anchor + timedelta(days=1)
    end    = start  + timedelta(days=6)
    return start, end

def business_week_window(run_date: date):
    """Previous completed business week: Friday → Thursday"""
    days_since_thursday = (run_date.weekday() - 3) % 7
    end   = run_date - timedelta(days=days_since_thursday)
    start = end - timedelta(days=6)
    return start, end

birthday_start, birthday_end = birthday_window(today_cst)
license_start,  license_end  = business_week_window(today_cst)
ffm_start,      ffm_end      = license_start, license_end

# =========================
# SQL (PostgreSQL / wpo schema)
# - NPN filtering pushed into SQL via JOIN
# - date_of_birth cast to date (stored as text)
# - npn cast to text for cross-type joins
# =========================

EXCLUDED_STATUSES_SQL = """
    'active captive',
    'active - released',
    'prospect captive',
    'active captive - principal',
    'agility employee',
    'active multiple uplines',
    'suspended',
    'quarantined'
"""

BIRTHDAY_SQL = f"""
WITH base AS (
    SELECT
        a.npn,
        a.date_of_birth,
        a.languages,
        a.gender,
        a.status,
        a.do_not_call,
        a.mailing_street,
        a.mailing_street_2,
        a.mailing_state,
        a.mailing_zip,
        a.mailing_county,
        a.mailing_city,
        a.email,
        a.first_name,
        a.last_name,
        a.phone,
        CASE
            WHEN EXTRACT(MONTH FROM a.date_of_birth::date) = 2
             AND EXTRACT(DAY   FROM a.date_of_birth::date) = 29
             AND (
                    EXTRACT(YEAR FROM CURRENT_DATE)::int %% 4 <> 0
                 OR (EXTRACT(YEAR FROM CURRENT_DATE)::int %% 100 = 0
                    AND EXTRACT(YEAR FROM CURRENT_DATE)::int %% 400 <> 0)
                 )
            THEN MAKE_DATE(EXTRACT(YEAR FROM CURRENT_DATE)::int, 2, 28)
            ELSE MAKE_DATE(
                EXTRACT(YEAR FROM CURRENT_DATE)::int,
                EXTRACT(MONTH FROM a.date_of_birth::date)::int,
                EXTRACT(DAY   FROM a.date_of_birth::date)::int
            )
        END AS this_year_birthday
    FROM wpo.lup_agents a
    WHERE a.npn IS NOT NULL
      AND a.date_of_birth IS NOT NULL
      AND a.date_of_birth::text <> ''
      AND a.mailing_street IS NOT NULL
      AND TRIM(a.mailing_street) <> ''
      AND LOWER(COALESCE(a.status, '')) NOT IN ({EXCLUDED_STATUSES_SQL})
),
next_birthdays AS (
    SELECT *,
        CASE
            WHEN this_year_birthday >= CURRENT_DATE
            THEN this_year_birthday
            ELSE this_year_birthday + INTERVAL '1 year'
        END AS next_birthday
    FROM base
)
SELECT *
FROM next_birthdays
WHERE next_birthday::date BETWEEN %s AND %s
ORDER BY next_birthday;
"""

LICENSE_SQL = """
SELECT
    l.npn,
    l.status,
    l.first_name,
    l.last_name,
    l.mailing_street,
    l.mailing_street_2,
    l.mailing_city,
    l.mailing_state,
    l.mailing_zip,
    l.license_date
FROM wpo.alu_licenses l
INNER JOIN wpo.lup_agents a ON a.npn = l.npn::text
WHERE l.license_date::date BETWEEN %s AND %s
ORDER BY l.license_date::date DESC;
"""

FFM_SQL = """
SELECT
    f.*,
    a.first_name,
    a.last_name,
    a.mailing_street,
    a.mailing_street_2,
    a.mailing_city,
    a.mailing_county,
    a.mailing_state,
    a.mailing_zip
FROM wpo.ffm_agents_updates f
INNER JOIN wpo.lup_agents a ON a.npn = f.npn::text
WHERE f.ffm_date::date BETWEEN %s AND %s
ORDER BY f.ffm_date::date DESC;
"""

# =========================
# QUERY RUNNER
# Each thread gets its own connection
# =========================

def _run_query(sql: str, params: list) -> pd.DataFrame:
    conn = get_postgres_connection()
    try:
        return pd.read_sql(sql, conn, params=params)
    finally:
        conn.close()

# =========================
# EMAIL UTILS
# =========================

def _split_recipients(to: Union[str, List[str]]) -> List[Dict[str, str]]:
    if isinstance(to, list):
        parts = to
    else:
        parts = [p.strip() for p in to.replace(";", ",").split(",") if p.strip()]
    return [{"address": addr} for addr in parts]


def send_email_with_attachments(to, subject, body, attachment_paths, cc=None):
    client     = EmailClient.from_connection_string(CONNECTION_STRING)
    recipients = {"to": _split_recipients(to)}
    if cc:
        recipients["cc"] = _split_recipients(cc)

    attachments = []
    for path in attachment_paths:
        with open(path, "rb") as f:
            encoded = base64.b64encode(f.read()).decode()
        attachments.append({
            "name": os.path.basename(path),
            "contentType": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            "contentInBase64": encoded,
        })

    message = {
        "senderAddress": SENDER_ADDRESS,
        "recipients": recipients,
        "content": {
            "subject": subject,
            "plainText": body,
        },
        "attachments": attachments,
    }

    poller = client.begin_send(message)
    poller.result()
    return True

# =========================
# ORCHESTRATOR
# =========================

def run_export():
    print(f"[START] {datetime.utcnow()}")

    # Warm secrets cache before spawning threads (avoids 3x Key Vault calls)
    get_postgres_db_secrets()

    tasks = {
        "birthdays": (BIRTHDAY_SQL, [birthday_start, birthday_end]),
        "licenses":  (LICENSE_SQL,  [license_start,  license_end]),
        "ffm":       (FFM_SQL,      [ffm_start,      ffm_end]),
    }

    results = {}
    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = {
            pool.submit(_run_query, sql, params): name
            for name, (sql, params) in tasks.items()
        }
        for future in as_completed(futures):
            name = futures[future]
            results[name] = future.result()
            print(f"  [DONE] {name} ({len(results[name])} rows)")

    df_birthdays = results["birthdays"]
    df_licenses  = results["licenses"]
    df_ffm       = results["ffm"]

    # =========================
    # LICENSES – FINAL COLUMNS
    # =========================

    df_licenses.columns = [c.lower() for c in df_licenses.columns]
    license_cols = [
        "npn", "status", "first_name", "last_name",
        "mailing_street", "mailing_street_2", "mailing_city",
        "mailing_state", "mailing_zip", "license_date",
    ]
    df_licenses = df_licenses[[c for c in license_cols if c in df_licenses.columns]]

    # =========================
    # FFM – FINAL COLUMNS
    # =========================

    # Coalesce address cols — prefer ffm row, fall back to agent join
    for col in ["first_name", "last_name", "mailing_street", "mailing_street_2",
                "mailing_city", "mailing_county", "mailing_state", "mailing_zip"]:
        agent_col = f"{col}_agent"
        if agent_col in df_ffm.columns:
            df_ffm[col] = df_ffm[col].fillna(df_ffm[agent_col])
            df_ffm.drop(columns=[agent_col], inplace=True)

    df_ffm["name"] = (
        df_ffm["first_name"].fillna("") + " " + df_ffm["last_name"].fillna("")
    ).str.strip()

    ffm_cols = [
        "name", "npn", "applicable_plan_year",
        "individual_registration_completion_date",
        "first_name", "last_name",
        "mailing_street", "mailing_street_2", "mailing_city",
        "mailing_county", "mailing_state", "mailing_zip",
    ]
    df_ffm = df_ffm[[c for c in ffm_cols if c in df_ffm.columns]]

    # =========================
    # WRITE EXCEL
    # =========================

    with pd.ExcelWriter(OUTPUT_PATH, engine="xlsxwriter") as writer:
        df_birthdays.to_excel(writer, "Upcoming_Birthdays", index=False)
        df_licenses.to_excel(writer,  "Recent_Licenses",    index=False)
        df_ffm.to_excel(writer,       "Recent_FFM",         index=False)

    print(f"[EXCEL] Written to {OUTPUT_PATH}")

    # =========================
    # EMAIL
    # =========================

    def fmt(d):
        return d.strftime("%b %d, %Y")

    send_email_with_attachments(
        to=EMAIL_TO,
        cc=EMAIL_CC,
        subject="Weekly Agent Notifications Report",
        body=(
            f"Attached is the weekly agent notifications report.\n\n"
            f"Report run date: {fmt(today_cst)}\n\n"
            f"Included date ranges:\n"
            f"- Upcoming birthdays: {fmt(birthday_start)} – {fmt(birthday_end)}\n"
            f"- Licenses: {fmt(license_start)} – {fmt(license_end)}\n"
            f"- FFM: {fmt(ffm_start)} – {fmt(ffm_end)}\n\n"
            f"This file is generated automatically."
        ),
        attachment_paths=[OUTPUT_PATH],
    )

    print("[EMAIL] Sent successfully")

    if os.path.exists(OUTPUT_PATH):
        os.remove(OUTPUT_PATH)
        print("[CLEANUP] Report deleted")

    print(f"[DONE] {datetime.utcnow()}")


if __name__ == "__main__":
    run_export()