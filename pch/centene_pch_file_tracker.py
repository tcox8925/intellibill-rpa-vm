import os
"""
Centene PCH File Tracking — Daily Poller
==========================================
Runs nightly at 11:00 PM America/Chicago (handles CST/CDT automatically).

Scans, for the current month only, BOTH:
    raw/centene_pch/{DD Month YYYY}/            (bare root — files land here too)
    raw/centene_pch/{DD Month YYYY}/archive/
Non-recursive at each level. `temp/` and any other subfolder are never
touched. Prior months are never rescanned — once a period is resolved it
stays resolved.

CONFIRMED WITH EVIDENCE (see conversation history for the file listings
that proved these):
  - The date/month embedded in a Centene filename is NOT reliable evidence
    of which period it belongs to (e.g. a file dated 20250310 uploaded
    fresh on 2026-04-16 sitting in the April 2026 folder; a ROLLING12 file
    dated Aug 28 sitting in the September archive; a Care Gap file labeled
    "SEPT 2025" sitting in the August archive). The ARCHIVE/ROOT FOLDER
    placement is the only trustworthy signal of period — the embedded
    date is ignored for all monthly file types.
  - ROLL12/ROLLING12 variants exist for Capitation, Med Claims, Member
    Eligibility, Other Adjustments, RX Claim, and MKP_FS — each tracked
    as its own distinct row per business decision (separate deliverable,
    not a renamed one).
  - Care Gap (Detail) / Measure Summary has no single stable filename
    convention — three different formats seen across three sample months.
    Tracked via a growable list of known alias patterns (CARE_GAP_ALIASES)
    — add new aliases here as new formats are observed, matched against
    structural shape only (never against a specific month/year value).
  - Real files sometimes land in the bare month-folder root instead of
    archive/ (Roster, MKP_FS, and daily census all observed there) — both
    locations are scanned.
  - Provider Roster shows no delivery at all (root or archive) for
    Apr-Jul 2025 and Apr-Jul 2026 in the sample data — this looks like a
    genuine delivery gap on Centene's side, not a script issue.

Upserts one row per (file_type, period_key) into
wpo.pch_file_tracking on pch-db-dev001. Monthly file types get a
single period_key per month ('2026-07'); the daily census/discharge file
gets one period_key per calendar day so far this month ('2026-07-01',
'2026-07-02', ...) — deliberate backfill, since Centene has been observed
to drop several days' files in one late batch, or skip days entirely.

Usage:
    python centene_pch_file_tracker.py
"""

import re
import sys
import traceback
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import psycopg2
from psycopg2.extras import execute_values
from azure.identity import DefaultAzureCredential, ClientSecretCredential
from azure.keyvault.secrets import SecretClient
from azure.storage.blob import BlobServiceClient, BlobPrefix


# ─── Config ──────────────────────────────────────────────────────────────

KEY_VAULT_NAME = os.getenv("KEY_VAULT_NAME", "")
KEY_VAULT_URL = os.getenv("KEYVAULT_URL", "")

PCH_SERVER = os.getenv("PCH_DB_HOST", "")
PCH_DATABASE = os.getenv("PCH_DB_NAME", "")
PCH_USER = os.getenv("PCH_DB_USER", "")

# Central ops-log server (shared with the rest of the RPA fleet). Only the
# run log is written here; file tracking stays on PCH_SERVER above.
DB_CONFIG_POSTGRES = {
    "server": os.getenv("DEFAULT834_DB_HOST", ""),
    "database": os.getenv("DEFAULT834_DB_NAME", ""),
    "user": os.getenv("DEFAULT834_DB_USER", ""),
    "sslmode": "require",
}

STORAGE_ACCOUNT_NAME = "834analyticsdatalake"
STORAGE_CONTAINER = "834analytics-dev"
BASE_PREFIX = "raw/centene_pch"

# Stamped onto every tracking row — constant for this Centene/PCH feed.
ENTITY_ID = "270681372"
SUB_ENTITY_ID = "270681372001"

TABLE_NAME = "wpo.pch_file_tracking"

TZ = ZoneInfo("America/Chicago")

_cached_pg_token = None
_cached_sp_credential = None


# ─── Auth ────────────────────────────────────────────────────────────────

def get_sp_credential():
    """
    The same service-principal credential used for Postgres/Synapse AAD
    tokens elsewhere in this codebase (see utils/db_utils.py). Reused here
    for blob storage too, on the assumption this SP also carries the
    storage RBAC role — confirm if blob calls get an auth error rather
    than a DNS error.
    """
    global _cached_sp_credential
    if _cached_sp_credential is None:
        bootstrap = DefaultAzureCredential()
        client = SecretClient(vault_url=KEY_VAULT_URL, credential=bootstrap)
        client_id = client.get_secret(os.getenv("KEYVAULT_CLIENT_ID_SECRET_NAME", "")).value
        client_secret = client.get_secret(os.getenv("KEYVAULT_CLIENT_SECRET_NAME", "")).value
        tenant_id = client.get_secret(os.getenv("KEYVAULT_TENANT_ID_SECRET_NAME", "")).value
        _cached_sp_credential = ClientSecretCredential(tenant_id, client_id, client_secret)
    return _cached_sp_credential


def get_pg_token():
    global _cached_pg_token
    if _cached_pg_token is None:
        cred = get_sp_credential()
        _cached_pg_token = cred.get_token(
            "https://ossrdbms-aad.database.windows.net/.default"
        ).token
    return _cached_pg_token


def get_pch_connection():
    conn = psycopg2.connect(
        host=PCH_SERVER, dbname=PCH_DATABASE, user=PCH_USER,
        password=get_pg_token(), sslmode="require",
        keepalives=1, keepalives_idle=30, keepalives_interval=10, keepalives_count=5,
    )
    conn.autocommit = True
    return conn


def get_ops_connection():
    conn = psycopg2.connect(
        host=DB_CONFIG_POSTGRES["server"], dbname=DB_CONFIG_POSTGRES["database"],
        user=DB_CONFIG_POSTGRES["user"], password=get_pg_token(),
        sslmode=DB_CONFIG_POSTGRES["sslmode"],
        keepalives=1, keepalives_idle=30, keepalives_interval=10, keepalives_count=5,
    )
    conn.autocommit = True
    return conn


def get_blob_container_client():
    credential = get_sp_credential()
    account_url = f"https://{STORAGE_ACCOUNT_NAME}.blob.core.windows.net"
    service = BlobServiceClient(account_url=account_url, credential=credential)
    return service.get_container_client(STORAGE_CONTAINER)


# ─── Schema ──────────────────────────────────────────────────────────────

CREATE_TRACKING_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS wpo.pch_file_tracking (
    id                 BIGSERIAL PRIMARY KEY,
    file_type          TEXT NOT NULL,
    cadence            TEXT NOT NULL CHECK (cadence IN ('daily','monthly')),
    period_key         TEXT NOT NULL,
    status             TEXT NOT NULL DEFAULT 'missing' CHECK (status IN ('missing','found')),
    matched_blob_name  TEXT,
    matched_blob_path  TEXT,
    entity             TEXT,
    sub_entity         TEXT,
    first_detected_at  TIMESTAMP,
    last_checked_at    TIMESTAMP NOT NULL DEFAULT now(),
    UNIQUE (file_type, period_key)
);
"""

UPSERT_SQL = """
INSERT INTO wpo.pch_file_tracking
    (file_type, cadence, period_key, status, matched_blob_name, matched_blob_path,
     entity, sub_entity, first_detected_at)
VALUES %s
ON CONFLICT (file_type, period_key) DO UPDATE SET
    status = CASE
        WHEN wpo.pch_file_tracking.status = 'found' THEN 'found'
        ELSE EXCLUDED.status
    END,
    matched_blob_name = CASE
        WHEN wpo.pch_file_tracking.status = 'found' THEN wpo.pch_file_tracking.matched_blob_name
        ELSE EXCLUDED.matched_blob_name
    END,
    matched_blob_path = CASE
        WHEN wpo.pch_file_tracking.status = 'found' THEN wpo.pch_file_tracking.matched_blob_path
        ELSE EXCLUDED.matched_blob_path
    END,
    entity = EXCLUDED.entity,
    sub_entity = EXCLUDED.sub_entity,
    first_detected_at = COALESCE(wpo.pch_file_tracking.first_detected_at, EXCLUDED.first_detected_at),
    last_checked_at = now();
"""


# ─── File-type registry ─────────────────────────────────────────────────

FOLDER_MONTH_NAME = {
    1: "January", 2: "February", 3: "March", 4: "April", 5: "May", 6: "June",
    7: "July", 8: "August", 9: "September", 10: "October", 11: "November", 12: "December",
}

# Care Gap (Detail) / Measure Summary has no single stable filename format —
# three different conventions observed across three sample months. Matched
# structurally only (some letters + a 4-digit year somewhere), never against
# a specific month value, since folder placement — not the embedded label —
# is what determines the period. ADD new aliases here as new formats show up.
CARE_GAP_ALIASES = [
    r"[A-Za-z]+ \d{4}_TX0201718 - Patient Care Health\.(xlsx|csv)",           # "SEPT 2025_TX0201718 - Patient Care Health.xlsx"
    r"SUP [A-Za-z]+ \d{4} Patient Care Health\(\d+\)\.(xlsx|csv)",            # "SUP NOV 2025 Patient Care Health(1).xlsx"
    r"Patient Care Health Q\.RA Performance Report [A-Za-z]+ \d{4}\.(xlsx|csv)",  # "Patient Care Health Q.RA Performance Report May 2026.xlsx"
]


def month_folder_name(d: date) -> str:
    """'06 June 2026' style folder name for the month containing d."""
    return f"{d.month:02d} {FOLDER_MONTH_NAME[d.month]} {d.year}"


def build_monthly_file_types(d: date):
    """
    Return list of (file_type, compiled_regex) for the month containing d.

    All date/day tokens embedded in filenames are wildcarded (\\d{6}, \\d{8},
    etc.) rather than tied to d's year/month — confirmed via real examples
    that Centene's embedded dates do not reliably match the folder they're
    delivered in. Folder placement (the caller only ever looks inside the
    current month's root+archive) is what actually establishes the period.
    """
    specs = [
        ("Roster",
         r"Member_Roster_Preview_TX0201718_\d{6}_\.(xlsx|csv)"),
        ("Provider Roster",
         r"TX_TX0201718_MKP_TCOC_PROVIDER_MEMBER_ROSTER_\d{4}_\d{8}_396\.(csv|xlsx)"),
        ("MKP_FS (TCOC)",
         r"TX_TX0201718_MKP_FS\(TCOC\)_\d{4}_\d{8}_396\.(xlsx|csv)"),
        ("MKP_FS (TCOC_ROLLING12)",
         r"TX_TX0201718_MKP_FS\(TCOC_ROLLING12\)_\d{4}_\d{8}_396\.(xlsx|csv)"),
        ("Capitation",
         r"TX_TX0201718_MKP_TCOC_CAPITATION_FILE_\d{4}_\d{8}_396\.(csv|xlsx)"),
        ("ROLL12 Capitation",
         r"TX_TX0201718_MKP_TCOC_ROLL12_CAPITATION_FILE_\d{4}_\d{8}_396\.(csv|xlsx)"),
        ("Med Claims",
         r"TX_TX0201718_MKP_TCOC_MED_CLAIMS_FILE_\d{4}_\d{8}_396\.(csv|xlsx)"),
        ("ROLL12 Med Claims",
         r"TX_TX0201718_MKP_TCOC_ROLL12_MED_CLAIMS_FILE_\d{4}_\d{8}_396\.(csv|xlsx)"),
        ("Member Eligibility",
         r"TX_TX0201718_MKP_TCOC_MEMBER_ELIG_FILE_\d{4}_\d{8}_396\.(csv|xlsx)"),
        ("ROLL12 Member Eligibility",
         r"TX_TX0201718_MKP_TCOC_ROLL12_MEMBER_ELIG_FILE_\d{4}_\d{8}_396\.(csv|xlsx)"),
        ("Other Adjustments",
         r"TX_TX0201718_MKP_TCOC_OTHER_ADJUSTMENTS_FILE_\d{4}_\d{8}_396\.(csv|xlsx)"),
        ("ROLL12 Other Adjustments",
         r"TX_TX0201718_MKP_TCOC_ROLL12_OTHER_ADJUSTMENTS_FILE_\d{4}_\d{8}_396\.(csv|xlsx)"),
        ("RX Claim",
         r"TX_TX0201718_MKP_TCOC_RX_CLAIM_FILE_\d{4}_\d{8}_396\.(csv|xlsx)"),
        ("ROLL12 RX Claim",
         r"TX_TX0201718_MKP_TCOC_ROLL12_RX_CLAIM_FILE_\d{4}_\d{8}_396\.(csv|xlsx)"),
    ]
    compiled = [(name, re.compile(pattern)) for name, pattern in specs]

    # Care Gap (Detail) and Measure Summary both key off the same alias set
    # (same underlying file, two tracked deliverables).
    care_gap_pattern = re.compile("|".join(f"(?:{a})" for a in CARE_GAP_ALIASES))
    compiled.append(("Care Gap (Detail)", care_gap_pattern))
    compiled.append(("Measure Summary", care_gap_pattern))

    return compiled


def daily_pattern(day: date):
    # Daily cadence is the one case where the embedded date IS the period —
    # this one stays anchored to the specific calendar day, unlike the
    # monthly types above.
    pattern = rf"Daily_IP_Census_and_Discharge_TX0201718{day.isoformat()} \d{{2}}\.\d{{2}}\.\d{{2}}\.\d{{3}}\.xlsx"
    return re.compile(pattern)


# ─── Blob scan ───────────────────────────────────────────────────────────

def list_immediate_files(container_client, prefix: str):
    """Files directly under prefix — non-recursive, skips virtual subfolders."""
    results = []
    for item in container_client.walk_blobs(name_starts_with=prefix, delimiter="/"):
        if isinstance(item, BlobPrefix):
            continue  # a nested virtual folder — never descended into
        basename = item.name[len(prefix):]
        if not basename:
            continue
        results.append((basename, item.name))
    return results


def list_period_files(container_client, month_folder: str):
    """
    Files for the current period = bare month root + archive/, combined.
    `temp/` and any other subfolder are never touched. Returns list of
    (blob_basename, full_blob_path) from both locations pooled together —
    a match in either location counts as "found" for that file type.
    """
    root_prefix = f"{BASE_PREFIX}/{month_folder}/"
    archive_prefix = f"{BASE_PREFIX}/{month_folder}/archive/"
    return list_immediate_files(container_client, root_prefix) + \
           list_immediate_files(container_client, archive_prefix)


# ─── Main poll ───────────────────────────────────────────────────────────

def poll(now_local: datetime):
    today = now_local.date()
    month_folder = month_folder_name(today)

    print(f"Polling {STORAGE_CONTAINER}/{BASE_PREFIX}/{month_folder}/ (root + archive/) ...")
    container_client = get_blob_container_client()
    files = list_period_files(container_client, month_folder)
    print(f"  {len(files)} files found (root + archive combined)")

    rows = []  # (file_type, cadence, period_key, status, matched_name, matched_path, entity, sub_entity, first_detected_at)
    now_ts = datetime.now()

    # ── Monthly file types: one period_key for the whole month ──
    month_period_key = f"{today.year}-{today.month:02d}"
    for file_type, pattern in build_monthly_file_types(today):
        match = next((f for f in files if pattern.fullmatch(f[0])), None)
        if match:
            rows.append((file_type, "monthly", month_period_key, "found", match[0], match[1], ENTITY_ID, SUB_ENTITY_ID, now_ts))
        else:
            rows.append((file_type, "monthly", month_period_key, "missing", None, None, ENTITY_ID, SUB_ENTITY_ID, None))

    # ── Daily file: backfill every day-so-far this month, not just today ──
    # (Centene has dropped multi-day batches late — e.g. 06-01/02/03 all
    # landed on 06-03, and 06-04 didn't land until 06-10. Only re-checking
    # "today" would permanently miss stragglers like that.)
    day_cursor = today.replace(day=1)
    while day_cursor <= today:
        pattern = daily_pattern(day_cursor)
        match = next((f for f in files if pattern.fullmatch(f[0])), None)
        period_key = day_cursor.isoformat()
        if match:
            rows.append(("Daily_IP_Census_and_Discharge", "daily", period_key, "found", match[0], match[1], ENTITY_ID, SUB_ENTITY_ID, now_ts))
        else:
            rows.append(("Daily_IP_Census_and_Discharge", "daily", period_key, "missing", None, None, ENTITY_ID, SUB_ENTITY_ID, None))
        day_cursor += timedelta(days=1)

    return rows


def upsert_rows(rows):
    conn = get_pch_connection()
    cur = conn.cursor()
    cur.execute(CREATE_TRACKING_TABLE_SQL)
    execute_values(cur, UPSERT_SQL, rows, page_size=500)
    cur.close()
    conn.close()


def log_run(start_ts, success: bool, error_message: str = None):
    try:
        conn = get_ops_connection()
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO wpo.ops_rpa_script_logs
                (script_name, start_datetime, end_datetime, error, success, product_name)
            VALUES (%s, %s, %s, %s, %s, %s)
            """,
            (
                "CENTENE_PCH_FILE_TRACKER",
                start_ts,
                datetime.now(),
                None if success else error_message,
                "Process ran successfully" if success else None,
                "CENTENE_PCH_FILE_TRACKING",
            ),
        )
        cur.close()
        conn.close()
    except Exception as e:
        print(f"[LOG-WRITE] Failed to write run log: {e!r}")


def main():
    start_ts = datetime.now()
    now_local = datetime.now(TZ)
    try:
        rows = poll(now_local)
        upsert_rows(rows)
        found = sum(1 for r in rows if r[3] == "found")
        print(f"\nDone. {found}/{len(rows)} tracked periods currently marked 'found'.")
        log_run(start_ts, success=True)
    except Exception as e:
        traceback.print_exc()
        log_run(start_ts, success=False, error_message=repr(e))
        sys.exit(1)


if __name__ == "__main__":
    main()