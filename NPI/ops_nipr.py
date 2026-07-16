"""
ops_nipr.py
-----------
Single endpoint that:
  1. Checks wpo.nipr_producer_info -- if fresh (< 1 year), returns early.
  2. Checks blob for existing PDF   -- if fresh (< 1 year), reuses it.
  3. Otherwise scrapes NIPR via Playwright, uploads PDF to blob.
  4. Parses PDF and upserts into all nipr_* Postgres tables.
"""

import os
import io
import pdfplumber
from datetime import datetime, timedelta, timezone
from azure.storage.blob import BlobServiceClient
from azure.identity import DefaultAzureCredential

from utils.db_utils import set_db_source
from utils.config import get_postgres_db_secrets
from azure.identity import ClientSecretCredential
import psycopg2
from NIPR.azure_blob_utils import authenticate_blob_storage, upload_file_to_blob
from NIPR.nipr_crawler_parser import download_detail_report
from NIPR.nipr_extraction import (
    parse_header,
    parse_contacts,
    parse_licenses,
    upsert,
)

# ---- Config ----
BLOB_CONTAINER = "834analytics-dev"
BLOB_PREFIX = "raw/agent_license_update/NIPR"
ACCOUNT_URL = "https://834analyticsdatalake.blob.core.windows.net"
DOWNLOAD_DIR = r"C:\Users\myopsadmin\Downloads"
FRESHNESS_DAYS = 365

# NIPR tables live on 834-db-dev001, NOT pch-db-dev001
NIPR_DB_CONFIG = {
    'server': os.getenv("DEFAULT834_DB_HOST", ""),
    'database': os.getenv("DEFAULT834_DB_NAME", ""),
    'user': os.getenv("DEFAULT834_DB_USER", ""),
}


def _get_nipr_connection():
    """Connect to 834-db-dev001 where the nipr_* tables live."""
    client_id, client_secret, tenant_id = get_postgres_db_secrets()
    credential = ClientSecretCredential(tenant_id, client_id, client_secret)
    token = credential.get_token("https://ossrdbms-aad.database.windows.net/.default").token

    return psycopg2.connect(
        host=NIPR_DB_CONFIG['server'],
        dbname=NIPR_DB_CONFIG['database'],
        user=NIPR_DB_CONFIG['user'],
        password=token,
        sslmode="require",
        connect_timeout=15,
    )


# ---- Helpers ----
def _blob_service():
    return authenticate_blob_storage()


def _find_existing_pdf(npn: str):
    """
    Look for a non-archived PDF for this NPN in blob.
    Returns (blob_name, last_modified) or (None, None).
    """
    container = _blob_service().get_container_client(BLOB_CONTAINER)
    prefix = f"{BLOB_PREFIX}/detail-report_{npn}_"

    for blob in container.list_blobs(name_starts_with=prefix):
        if blob.name.lower().endswith(".pdf") and "/archive/" not in blob.name:
            return blob.name, blob.last_modified
    return None, None


def _download_blob_to_local(blob_name: str) -> str:
    """Download a blob PDF to the local download dir. Returns local path."""
    filename = blob_name.split("/")[-1]
    local_path = os.path.join(DOWNLOAD_DIR, filename)
    bc = _blob_service().get_blob_client(BLOB_CONTAINER, blob_name)

    os.makedirs(DOWNLOAD_DIR, exist_ok=True)
    with open(local_path, "wb") as f:
        f.write(bc.download_blob().readall())

    print(f"[OPS_NIPR] Downloaded blob to {local_path}")
    return local_path


def _local_pdf_text(path: str) -> str:
    """Extract text from a local PDF using pdfplumber."""
    pages = []
    with pdfplumber.open(path) as pdf:
        for p in pdf.pages:
            pages.append(p.extract_text() or "")
    return "\n".join(pages)


def _check_db_freshness(npn: str) -> dict:
    """
    Check if nipr_producer_info has a row for this NPN refreshed within FRESHNESS_DAYS.
    Returns {"fresh": True/False, "report_date": str or None}.
    """
    conn = _get_nipr_connection()
    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT report_date
            FROM wpo.nipr_producer_info
            WHERE npn = %s
        """, (npn,))
        row = cur.fetchone()
    finally:
        conn.close()

    if not row or not row[0]:
        return {"fresh": False, "report_date": None}

    report_date_raw = row[0]

    # report_date may come back as a datetime.date/datetime (DATE column) or a
    # MM/DD/YYYY string (TEXT column). Handle both.
    from datetime import date as _date
    if isinstance(report_date_raw, datetime):
        report_date = report_date_raw
        report_date_str = report_date_raw.strftime("%m/%d/%Y")
    elif isinstance(report_date_raw, _date):
        report_date = datetime(report_date_raw.year, report_date_raw.month, report_date_raw.day)
        report_date_str = report_date_raw.strftime("%m/%d/%Y")
    else:
        report_date_str = str(report_date_raw)
        try:
            report_date = datetime.strptime(report_date_str, "%m/%d/%Y")
        except (ValueError, TypeError):
            return {"fresh": False, "report_date": report_date_str}

    age = datetime.utcnow() - report_date
    return {
        "fresh": age.days < FRESHNESS_DAYS,
        "report_date": report_date_str,
    }


# ---- Main ----
def run_ops_nipr(npn: str, source: str = "myops") -> dict:
    """
    Full NIPR ops flow:
      1. DB freshness check   -> return early if < 1 year
      2. Blob freshness check -> reuse PDF if < 1 year
      3. Else scrape NIPR     -> download + upload to blob
      4. Parse PDF             -> upsert into nipr_* tables
    """
    set_db_source(source)

    print(f"[OPS_NIPR] Starting for NPN {npn} (source={source})")

    # ----------------------------------------------------------
    # STEP 1: Check DB freshness
    # ----------------------------------------------------------
    print(f"[OPS_NIPR] Step 1: Checking DB freshness...")
    db_check = _check_db_freshness(npn)
    print(f"[OPS_NIPR] Step 1 done: {db_check}")
    if db_check["fresh"]:
        print(f"[OPS_NIPR] NPN {npn} already in DB, report_date={db_check['report_date']} -- skipping")
        return {
            "status": "already_present",
            "npn": npn,
            "source": source,
            "report_date": db_check["report_date"],
            "message": "Data already present and refreshed within a year",
        }

    # ----------------------------------------------------------
    # STEP 2: Check blob for existing PDF
    # ----------------------------------------------------------
    print(f"[OPS_NIPR] Step 2: Checking blob for existing PDF...")
    blob_name, blob_modified = _find_existing_pdf(npn)
    print(f"[OPS_NIPR] Step 2 done: blob_name={blob_name}")
    local_path = None
    pdf_source = None

    if blob_name and blob_modified:
        age = datetime.now(timezone.utc) - blob_modified
        if age.days < FRESHNESS_DAYS:
            print(f"[OPS_NIPR] Reusing existing blob: {blob_name} (age={age.days}d)")
            local_path = _download_blob_to_local(blob_name)
            pdf_source = "blob_reuse"

    # ----------------------------------------------------------
    # STEP 3: Scrape NIPR if no usable PDF
    # ----------------------------------------------------------
    if not local_path:
        print(f"[OPS_NIPR] No fresh PDF in blob -- scraping NIPR for NPN {npn}")
        raw_path = download_detail_report(npn, DOWNLOAD_DIR)

        if raw_path == "NO_REPORT":
            print(f"[OPS_NIPR] NPN {npn}: No Longer Licensed -- skipping")
            return {
                "status": "no_report",
                "npn": npn,
                "source": source,
                "message": "Resident state empty -- No Longer Licensed",
            }

        # Rename
        today = datetime.now().strftime("%Y%m%d")
        new_name = f"detail-report_{npn}_{today}.pdf"
        local_path = os.path.join(DOWNLOAD_DIR, new_name)
        try:
            os.replace(raw_path, local_path)
        except Exception:
            import shutil
            shutil.move(raw_path, local_path)

        # Upload to blob
        print(f"[OPS_NIPR] Uploading PDF to blob...")
        blob_svc = authenticate_blob_storage()
        blob_path = f"{BLOB_PREFIX}/{new_name}"
        upload_file_to_blob(blob_svc, local_path, blob_path, container_name=BLOB_CONTAINER)
        pdf_source = "fresh_scrape"

    # ----------------------------------------------------------
    # STEP 4: Parse PDF
    # ----------------------------------------------------------
    print(f"[OPS_NIPR] Parsing PDF: {local_path}")
    text = _local_pdf_text(local_path)
    header = parse_header(text)
    contacts = parse_contacts(text)
    licenses = parse_licenses(text)

    if not header.get("npn"):
        return {
            "status": "parse_error",
            "npn": npn,
            "source": source,
            "message": "Could not parse NPN from PDF",
        }

    print(f"[OPS_NIPR] Parsed: NPN={header['npn']}, Name={header.get('name')}, "
          f"Licenses={len(licenses)}, Addresses={len(contacts.get('addresses', []))}")

    # ----------------------------------------------------------
    # STEP 5: Upsert into Postgres
    # ----------------------------------------------------------
    print(f"[OPS_NIPR] Upserting into Postgres (source={source})...")
    conn = _get_nipr_connection()
    cur = conn.cursor()
    try:
        upsert(cur, header, contacts, licenses)
        conn.commit()
        print(f"[OPS_NIPR] Committed NPN {npn}")
    except Exception as e:
        conn.rollback()
        print(f"[OPS_NIPR] Upsert failed: {e}")
        return {
            "status": "upsert_error",
            "npn": npn,
            "source": source,
            "error": str(e),
        }
    finally:
        conn.close()

    return {
        "status": "success",
        "npn": npn,
        "source": source,
        "pdf_source": pdf_source,
        "report_date": header.get("report_date"),
        "name": header.get("name"),
        "licenses_count": len(licenses),
        "addresses_count": len(contacts.get("addresses", [])),
    }