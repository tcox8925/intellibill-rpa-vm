from playwright.sync_api import sync_playwright
import os
from datetime import datetime, timedelta, date, timezone
from zoneinfo import ZoneInfo
from collections import defaultdict
import psycopg2
from azure.identity import DefaultAzureCredential, ClientSecretCredential
from azure.keyvault.secrets import SecretClient
from azure.storage.blob import BlobServiceClient
import json
import uuid
import zipfile
import re
import random
import string
from otp_info import handle_tebra_otp_if_present
from email_read import fetch_latest_tebra_otp_code

# =========================================================
# CONFIG
# =========================================================

CST = ZoneInfo("America/Chicago")

def _now_cst():
    return datetime.now(CST)

DEBUG = False

LOGIN_URL = "https://app.kareo.com/v2/#/sign-in?"

EMAIL = os.getenv("TEBRA_EMAIL", "")
PASSWORD = os.getenv("TEBRA_PASSWORD", "")

KEY_VAULT_URL = os.getenv("KEYVAULT_URL", "")

POSTGRES_CONFIG_PCH = {
    "host": os.getenv("PCH_DB_HOST", ""),
    "database": os.getenv("PCH_DB_NAME", ""),
    "user": os.getenv("PCH_DB_USER", ""),
}

POSTGRES_CONFIG_EHR = {
    "host": os.getenv("RCM_DB_HOST", ""),
    "database": os.getenv("RCM_DB_NAME", ""),
    "user": os.getenv("RCM_DB_USER", ""),
}

POSTGRES_CONFIG_MYOPS = {
    "host": os.getenv("MYOPS_DB_HOST", ""),
    "database": os.getenv("MYOPS_DB_NAME", ""),
    "user": os.getenv("MYOPS_DB_USER", ""),
}

TABLE_NAME = "ehr.ehr_appointments"
PATIENTS_TABLE = "ehr.ehr_patients"

DOWNLOAD_DIR = r"C:\Users\myopsadmin\Downloads\acc"
BATCH_SIZE = 50

MANUAL_DATE_FORMAT = "%m/%d/%Y"

# =========================================================
# STORAGE CONFIG
# =========================================================

# Facesheets route to the configured Azure storage account via connection
# string. Facesheet PDFs are downloaded to local disk, zipped locally, and
# only the finished ZIP is delivered to 834labs-sftp/<practice folder> by
# upload_zip_to_rcm_sftp. No intermediate blob container is used.
STORAGE_ACCOUNT_NAME = os.getenv("TEBRA_STORAGE_ACCOUNT_NAME", "")

# Storage account key is sourced from env so the connection string is not
# hardcoded in source.
AZURE_STORAGE_CONNECTION_STRING = (
    f"DefaultEndpointsProtocol=https;AccountName={STORAGE_ACCOUNT_NAME};"
    f"AccountKey={os.getenv('TEBRA_STORAGE_ACCOUNT_KEY', '')};"
    "EndpointSuffix=core.windows.net"
)

CLIENT_ID_KEY = os.getenv("KEYVAULT_CLIENT_ID_SECRET_NAME", "")
CLIENT_SECRET_KEY = os.getenv("KEYVAULT_CLIENT_SECRET_NAME", "")
TENANT_ID_KEY = os.getenv("KEYVAULT_TENANT_ID_SECRET_NAME", "")


# =========================================================
# AZURE CREDENTIAL CACHE
# =========================================================

_cached_sp_credential = None


def _get_sp_credential():
    """
    Build and cache the ServicePrincipalCredential so we don't
    hit Key Vault on every DB / Blob call.
    """
    global _cached_sp_credential
    if _cached_sp_credential is None:
        kv = SecretClient(
            vault_url=KEY_VAULT_URL,
            credential=DefaultAzureCredential(),
        )
        _cached_sp_credential = ClientSecretCredential(
            tenant_id=kv.get_secret(TENANT_ID_KEY).value,
            client_id=kv.get_secret(CLIENT_ID_KEY).value,
            client_secret=kv.get_secret(CLIENT_SECRET_KEY).value,
        )
    return _cached_sp_credential


# =========================================================
# DB CONNECTIONS
# =========================================================

def get_pch_connection():
    """Connect to the PCH database for legacy ops_pch_logs writes."""
    sp = _get_sp_credential()
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
    sp = _get_sp_credential()
    token = sp.get_token("https://ossrdbms-aad.database.windows.net/.default").token

    return psycopg2.connect(
        host=POSTGRES_CONFIG_EHR["host"],
        dbname=POSTGRES_CONFIG_EHR["database"],
        user=POSTGRES_CONFIG_EHR["user"],
        password=token,
        sslmode="require",
    )


def get_myopsprod_connection():
    """Connect to the MyOps database for logging tables."""
    sp = _get_sp_credential()
    token = sp.get_token("https://ossrdbms-aad.database.windows.net/.default").token

    return psycopg2.connect(
        host=POSTGRES_CONFIG_MYOPS["host"],
        dbname=POSTGRES_CONFIG_MYOPS["database"],
        user=POSTGRES_CONFIG_MYOPS["user"],
        password=token,
        sslmode="require",
    )


def log_run_to_pch(script_name, process_type, status, error,
                   company_id, started_at, ended_at,
                   carrier_id=None, file_path=None):
    """
    Write one run-log row to wpo.ops_pch_logs on both the PCH and IBRCM DBs.

    The same txn_id is written to both databases so downstream consumers can
    correlate the mirrored log rows. log_id and created_at are assumed
    DB-populated (identity + default). Never raises — logging must not fail
    a run.
    """
    txn_id = str(uuid.uuid4())
    targets = [
        ("pch", get_pch_connection),
        ("ibrcm", get_ehr_connection),
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
                cur.close()
            finally:
                conn.close()
        except Exception as e:
            print(f"[LOG-WRITE] Failed to write ops_pch_logs row on {target_name}: {e!r}")


# =========================================================
# STORAGE
# =========================================================


# =========================================================
# SMALL HELPERS
# =========================================================

def get_practice_name(practice_name: str):
    return practice_name.strip()


def get_practice_abbr(practice_name: str):
    words = practice_name.replace("&", "").split()
    return "".join(w[0].upper() for w in words if w and w[0].isalpha())


def get_practice_folder_name(practice_name: str):
    return practice_name.strip()


def generate_random_suffix(length=4):
    return "".join(random.choices(string.digits, k=length))


def to_date_obj(appt_date_val):
    if appt_date_val is None:
        return None
    if isinstance(appt_date_val, datetime):
        return appt_date_val.date()
    if isinstance(appt_date_val, date):
        return appt_date_val
    if isinstance(appt_date_val, str):
        return datetime.strptime(appt_date_val.strip(), "%Y-%m-%d").date()
    return datetime.strptime(str(appt_date_val), "%Y-%m-%d").date()


def _normalize_text(text):
    return re.sub(r"[^A-Za-z0-9]", "", text).lower()


def _name_key(text):
    """
    Build a set of name parts for matching.
    'Brown, Sara R' → frozenset({'brown', 'r', 'sara'})
    'SARA R BROWN'  → frozenset({'brown', 'r', 'sara'})
    """
    clean = re.sub(r"[^A-Za-z ]", "", text).lower().split()
    return frozenset(clean)


def _find_name_match(card_key, needed):
    """
    Try to match a card name against the needed dict.
    1. Exact match on frozenset key
    2. Subset match — shorter name's words all appear in longer name
    Returns the matched key or None.
    """
    if card_key in needed:
        return card_key

    for db_key in needed:
        shorter, longer = (card_key, db_key) if len(card_key) <= len(db_key) else (db_key, card_key)
        if shorter.issubset(longer):
            return db_key

    return None


# =========================================================
# UI HELPERS
# =========================================================

def slow_fill(locator, text):
    locator.click()
    locator.press("Control+A")
    locator.press("Backspace")
    locator.fill(text)


def cell(row, field):
    try:
        el = row.locator(f"div[data-field='{field}']")
        if not el.count():
            return None
        # Short timeout: during virtual-scroll a row can be mid-render or
        # detached. Return None so scrape_virtual_grid re-reads it on a later
        # pass rather than hanging 30s and dropping the whole chunk.
        return el.inner_text(timeout=2500).strip()
    except Exception:
        return None


def _drawer_open(page):
    return page.locator("div.MuiDrawer-root").count() > 0


def _close_filters_if_open(page):
    try:
        if _drawer_open(page):
            close_btn = page.locator("button[aria-label='Close']")
            if close_btn.count():
                close_btn.first.click(force=True)
            else:
                page.keyboard.press("Escape")
            page.wait_for_timeout(80)
    except Exception:
        pass


def _open_filters(page):
    _close_filters_if_open(page)
    btn = page.locator("button[aria-label='Table filters']").first
    try:
        btn.click(timeout=5_000)
    except Exception:
        try:
            handle = btn.element_handle()
            page.evaluate("(el) => el && el.click()", handle)
        except Exception:
            btn.click(force=True)
    page.wait_for_timeout(100)


def _close_filters(page):
    try:
        close_btn = page.locator("button[aria-label='Close']").first
        if close_btn.count():
            close_btn.click(force=True)
        else:
            page.keyboard.press("Escape")
    except Exception:
        page.keyboard.press("Escape")
    page.wait_for_timeout(100)


def wait_for_grid_settled(page, timeout_ms=60_000, max_retries=3):
    for attempt in range(1, max_retries + 1):
        try:
            page.wait_for_selector(".MuiDataGrid-virtualScroller", timeout=timeout_ms)
            page.wait_for_function(
                """
                () => {
                  const hasRow = document.querySelectorAll('.MuiDataGrid-row').length > 0;
                  const noRows =
                    !!document.querySelector('.MuiDataGrid-overlayWrapper') ||
                    !!document.querySelector('[class*="MuiDataGrid-overlay"]') ||
                    (document.body && document.body.innerText && document.body.innerText.includes('No rows'));
                  return hasRow || noRows;
                }
                """,
                timeout=timeout_ms,
            )
            page.wait_for_timeout(150)
            return
        except Exception as e:
            if attempt < max_retries:
                print(f"[GRID] Timeout on attempt {attempt}/{max_retries}, refreshing page ...")
                page.reload(wait_until="domcontentloaded")
                page.wait_for_timeout(2000)
            else:
                raise


def clear_all_filters(page):
    _open_filters(page)
    try:
        clear_btn = page.locator("button:has-text('Clear')")
        if clear_btn.count():
            clear_btn.first.click()
    except Exception:
        pass
    _close_filters(page)
    wait_for_grid_settled(page)


def apply_date_filter(page, from_date, to_date):
    _open_filters(page)
    inputs = page.locator("input[placeholder='MM/DD/YYYY']")
    slow_fill(inputs.nth(0), from_date.strftime("%m/%d/%Y"))
    slow_fill(inputs.nth(1), to_date.strftime("%m/%d/%Y"))
    _close_filters(page)
    wait_for_grid_settled(page)


# =========================================================
# BLOB HELPERS
# =========================================================


# =========================================================
# SCRAPE VIRTUAL GRID (PHASE 1)
# =========================================================

def scrape_virtual_grid(page, extract_fn, max_scrolls=300):
    seen = {}
    stable = 0
    last_count = 0

    for _ in range(max_scrolls):
        rows = page.locator(".MuiDataGrid-row")
        for r in rows.all():
            try:
                rec = extract_fn(r)
            except Exception:
                continue
            if rec.get("appt_id"):
                seen[rec["appt_id"]] = rec

        if len(seen) == last_count:
            stable += 1
        else:
            stable = 0

        if stable >= 5:
            break

        last_count = len(seen)
        page.evaluate("""
            () => {
                const g = document.querySelector('.MuiDataGrid-virtualScroller');
                g.scrollTop += g.clientHeight;
            }
        """)
        page.wait_for_timeout(150)

    return seen


# =========================================================
# GRID ROW FINDER
# =========================================================

def find_row_by_appt_id_with_scroll(page, appt_id, max_scrolls=120):
    grid = page.locator(".MuiDataGrid-virtualScroller")
    grid.wait_for(state="visible", timeout=30_000)

    for _ in range(max_scrolls):
        id_locator = page.locator(
            f"div[data-field='APPOINTMENT_ID'] >> text=\"{appt_id}\""
        )
        if id_locator.count() > 0:
            row = id_locator.first.locator(
                "xpath=ancestor::div[contains(@class,'MuiDataGrid-row')]"
            )
            row.scroll_into_view_if_needed()
            page.wait_for_timeout(80)
            return row

        page.evaluate("""
            () => {
                const g = document.querySelector('.MuiDataGrid-virtualScroller');
                g.scrollTop += g.clientHeight;
            }
        """)
        page.wait_for_timeout(120)

    return None


# =========================================================
# PATIENT ROW CLICK → FACESHEET PAGE
# =========================================================

def click_patient_row(page, row):
    """
    Returns (facesheet_page, opened_new_tab: bool).

    Closes any stray tab opened by a failed attempt so tabs don't accumulate
    across a long backfill (open tabs slow the browser and cascade failures).
    """
    link_btn = row.locator("button.MuiLink-button").first
    if link_btn.count() == 0:
        link_btn = row.locator("div[data-field='PATIENT_NAME']").first

    link_btn.scroll_into_view_if_needed()

    pages_before = set(page.context.pages)

    def _cleanup_stray(keep=None):
        for pg in page.context.pages:
            if pg not in pages_before and pg is not keep and pg is not page:
                try:
                    pg.close()
                except Exception:
                    pass

    # Attempt 1: opens in a new tab
    try:
        with page.context.expect_page(timeout=6_000) as p:
            link_btn.click(force=True)
        fs = p.value
        try:
            fs.wait_for_load_state("domcontentloaded")
            fs.wait_for_url("**/Facesheet/**", timeout=15_000)
        except Exception:
            pass
        if "/Facesheet/" in fs.url:
            return fs, True
        _cleanup_stray()  # opened a tab but it wasn't a facesheet
    except Exception:
        _cleanup_stray()

    # Attempt 2: same-tab navigation
    try:
        link_btn.click(force=True)
        page.wait_for_url("**/Facesheet/**", timeout=15_000)
        if "/Facesheet/" in page.url:
            _cleanup_stray(keep=page)  # in case the click also spawned a tab
            return page, False
    except Exception:
        pass

    _cleanup_stray()
    raise RuntimeError("Unable to open facesheet")


# =========================================================
# SCRAPE TEBRA PATIENT ID (NEW)
# =========================================================

def scrape_tebra_patient_id(fs_page):
    """
    From an open facesheet/chart page, navigate to Demographics,
    scrape the Tebra Patient ID, then return to Facesheet.
    """
    try:
        demo_link = fs_page.locator(
            "a[data-testid='clinical-page-nav-link-demographics']"
        )
        try:
            demo_link.wait_for(state="visible", timeout=5_000)
        except Exception:
            print("[PATIENT_ID] Demographics link not found (timeout)")
            return None

        demo_link.click()
        fs_page.wait_for_load_state("domcontentloaded")
        fs_page.wait_for_timeout(300)

        pair = fs_page.locator(
            "div.pair:has(div.label:has-text('Tebra Patient ID'))"
        )
        try:
            pair.wait_for(state="visible", timeout=3_000)
        except Exception:
            print("[PATIENT_ID] Tebra Patient ID pair not found (timeout)")
            return None

        raw = pair.locator("div.value").inner_text().strip().replace("\xa0", "").strip()
        print(f"[PATIENT_ID] Scraped: {raw}")
        return raw if raw else None

    except Exception as e:
        print(f"[PATIENT_ID ERROR] {e}")
        return None


# =========================================================
# DB HELPERS
# =========================================================

def upsert_appointment(cur, rec):
    cur.execute(
        f"SELECT 1 FROM {TABLE_NAME} WHERE entity=%s AND sub_entity=%s AND appt_id=%s AND ehr_name=%s",
        (rec["entity"], rec["sub_entity"], rec["appt_id"], rec["ehr_name"]),
    )

    if cur.fetchone() is None:
        cur.execute(
            f"""
            INSERT INTO {TABLE_NAME} (
                appt_id, appt_date, appt_time,
                patient_name, dob,
                home_phone, mobile_phone,
                provider_name, service_location,
                appt_reason, appt_status,
                retry_flag, retry_reason,
                process_status,
                entity, sub_entity, practice, ehr_name, updated_date
            ) VALUES (
                %(appt_id)s, %(appt_date)s, %(appt_time)s,
                %(patient_name)s, %(dob)s,
                %(home_phone)s, %(mobile_phone)s,
                %(provider_name)s, %(service_location)s,
                %(appt_reason)s, %(appt_status)s,
                %(retry_flag)s, %(retry_reason)s,
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
                practice = %s,
                updated_date = now()
            WHERE entity=%s AND sub_entity=%s AND appt_id=%s AND ehr_name=%s
            """,
            (
                rec.get("appt_status"),
                rec.get("practice"),
                rec["entity"],
                rec["sub_entity"],
                rec["appt_id"],
                rec["ehr_name"],
            ),
        )


def set_missed_charges(cur, appt_ids, entity, sub_entity, ehr_name):
    if appt_ids:
        cur.execute(
            f"""
            UPDATE {TABLE_NAME}
            SET retry_flag = 1,
                retry_reason = 'Missed Charges',
                updated_date = now()
            WHERE entity = %s
              AND sub_entity = %s
              AND ehr_name = %s
              AND appt_id = ANY(%s)
            """,
            (entity, sub_entity, ehr_name, appt_ids),
        )


def clear_retry_flag(cur, db_id):
    cur.execute(
        f"""
        UPDATE {TABLE_NAME}
        SET retry_flag=0, retry_reason=NULL, updated_date=now()
        WHERE id=%s
        """,
        (db_id,),
    )


# =========================================================
# FLUSH BATCH → BLOB + DB UPDATE
# =========================================================

def flush_batch(cur, batch, practice_name, to_date):
    for db_id, facesheet_id, local_path, tebra_patient_id in batch:
        if not os.path.exists(local_path):
            print(f"[FLUSH] Skipping missing file: {local_path}")
            continue

        # PDF stays on local disk; the ZIP is assembled from local files and
        # only the finished ZIP is delivered to 834labs-sftp. No intermediate
        # blob copy.

        # Check if retry_reason is 'Missed Charges' — don't clear it
        cur.execute(
            f"SELECT retry_reason FROM {TABLE_NAME} WHERE id=%s",
            (db_id,),
        )
        row = cur.fetchone()
        keep_retry = row and row[0] == "Missed Charges"

        if keep_retry:
            cur.execute(
                f"""
                UPDATE {TABLE_NAME}
                SET tebra_facesheet_id = COALESCE(%s, tebra_facesheet_id),
                    patient_id = COALESCE(%s, patient_id),
                    process_status = 'Processed',
                    process_error_stage = NULL,
                    process_error_message = NULL,
                    updated_date = now()
                WHERE id=%s
                """,
                (facesheet_id, tebra_patient_id, db_id),
            )
        else:
            cur.execute(
                f"""
                UPDATE {TABLE_NAME}
                SET tebra_facesheet_id = COALESCE(%s, tebra_facesheet_id),
                    patient_id = COALESCE(%s, patient_id),
                    process_status = 'Processed',
                    process_error_stage = NULL,
                    process_error_message = NULL,
                    retry_flag = 0,
                    retry_reason = NULL,
                    updated_date = now()
                WHERE id=%s
                """,
                (facesheet_id, tebra_patient_id, db_id),
            )

    cur.connection.commit()


# =========================================================
# UNIFIED FACESHEET PROCESSOR (DRY for 2A / 2B / 2C)
# =========================================================

def process_facesheet_row(
    page, context, cur, conn,
    db_id, appt_id, patient_name,
    batch, phase_label,
):
    """
    Shared logic for opening a patient row, scraping patient_id,
    downloading the facesheet PDF, and appending to batch.

    Returns True on success, False on failure (already logged to DB).
    """
    fs = None
    opened_new_tab = False
    try:
        row = find_row_by_appt_id_with_scroll(page, appt_id)
        if not row:
            raise RuntimeError("No matching row found in grid")

        fs, opened_new_tab = click_patient_row(page, row)

        m = re.search(r"/Facesheet/(\d+)", fs.url)
        if not m:
            raise RuntimeError("Facesheet ID not found in URL")

        facesheet_id = m.group(1)

        # ---- Scrape Tebra Patient ID ----
        tebra_patient_id = scrape_tebra_patient_id(fs)

        # ---- Download PDF via API (page state doesn't matter) ----
        pdf_url = f"https://app.kareo.com/patients/print/{facesheet_id}.pdf"
        last_name = patient_name.split(",")[0].strip().replace(" ", "_")
        pdf_path = os.path.join(
            DOWNLOAD_DIR, f"{appt_id}_{last_name}_{facesheet_id}.pdf"
        )

        resp = context.request.get(pdf_url)
        if resp.status != 200:
            raise RuntimeError(f"PDF download failed: HTTP {resp.status}")

        with open(pdf_path, "wb") as f:
            f.write(resp.body())

        batch.append((db_id, facesheet_id, pdf_path, tebra_patient_id))
        return True

    except Exception as e:
        print(f"[ERROR-{phase_label}] {appt_id} {e}")
        cur.execute(
            f"""
            UPDATE {TABLE_NAME}
            SET process_status='Error',
                process_error_stage=%s,
                process_error_message=%s,
                updated_date=now()
            WHERE id=%s
            """,
            (phase_label, str(e)[:500], db_id),
        )
        conn.commit()
        return False

    finally:
        # Always tear down the facesheet view so tabs don't accumulate and the
        # next row starts on the grid.
        try:
            if opened_new_tab and fs is not None:
                fs.close()
            else:
                page.go_back()
                wait_for_grid_settled(page)
        except Exception:
            pass


# =========================================================
# RUN FACESHEETS (PHASE 2)
# =========================================================

def run_facesheets(page, context, from_date, to_date, practice_name, entity, sub_entity, ehr_name):
    conn = get_ehr_connection()
    cur = conn.cursor()

    batch = []

    def maybe_flush():
        nonlocal batch
        if len(batch) >= BATCH_SIZE:
            flush_batch(cur, batch, practice_name, to_date)
            batch = []

    # =================================================
    # 2A — NEW (process_status IS NULL)
    # =================================================

    cur.execute(
        f"""
        SELECT id, appt_id, patient_name, dob, appt_status, appt_date
        FROM {TABLE_NAME}
        WHERE entity=%s AND sub_entity=%s AND ehr_name=%s
          AND practice=%s
          AND COALESCE(process_status, '') = ''
          AND appt_note IS NOT NULL
          AND appt_date BETWEEN %s AND %s
        ORDER BY appt_date, appt_time
        """,
        (entity, sub_entity, ehr_name, practice_name, from_date, to_date),
    )
    new_rows = cur.fetchall()
    print(f"[2A NEW] {len(new_rows)} rows")

    grouped_new = defaultdict(list)
    for row in new_rows:
        db_id, appt_id, patient_name, dob, db_status, appt_date = row
        grouped_new[to_date_obj(appt_date)].append(row)

    page.locator("[data-testid='tree-option-All Appointments']").click()
    wait_for_grid_settled(page)

    for appt_day, day_rows in grouped_new.items():
        apply_date_filter(page, appt_day, appt_day)

        for db_id, appt_id, patient_name, dob, db_status, appt_date in day_rows:
            process_facesheet_row(
                page, context, cur, conn,
                db_id, appt_id, patient_name,
                batch, "2A",
            )
            maybe_flush()

    # =================================================
    # 2B — ERROR (retry)
    # =================================================

    cur.execute(
        f"""
        SELECT id, appt_id, patient_name, dob, appt_date
        FROM {TABLE_NAME}
        WHERE entity=%s AND sub_entity=%s AND ehr_name=%s
          AND practice=%s
          AND process_status='Error'
          AND appt_note IS NOT NULL
          AND appt_date BETWEEN %s AND %s
        ORDER BY appt_date, appt_time
        """,
        (entity, sub_entity, ehr_name, practice_name, from_date, to_date),
    )
    err_rows = cur.fetchall()
    print(f"[2B ERROR] {len(err_rows)} rows")

    grouped_err = defaultdict(list)
    for row in err_rows:
        db_id, appt_id, patient_name, dob, appt_date = row
        grouped_err[to_date_obj(appt_date)].append(row)

    for appt_day, day_rows in grouped_err.items():
        apply_date_filter(page, appt_day, appt_day)

        for db_id, appt_id, patient_name, dob, appt_date in day_rows:
            process_facesheet_row(
                page, context, cur, conn,
                db_id, appt_id, patient_name,
                batch, "2B",
            )
            maybe_flush()

    # =================================================
    # 2C — MISSED CHARGES
    # =================================================

    cur.execute(
        f"""
        SELECT id, appt_id, patient_name, dob, appt_date
        FROM {TABLE_NAME}
        WHERE entity=%s AND sub_entity=%s AND ehr_name=%s
          AND practice=%s
          AND retry_flag=1
          AND retry_reason='Missed Charges'
          AND appt_note IS NOT NULL
          AND appt_date BETWEEN %s AND %s
        """,
        (entity, sub_entity, ehr_name, practice_name, from_date, to_date),
    )
    mc_rows = cur.fetchall()
    print(f"[2C MISSED CHARGES] {len(mc_rows)} rows")

    grouped_mc = defaultdict(list)
    for row in mc_rows:
        db_id, appt_id, patient_name, dob, appt_date = row
        grouped_mc[to_date_obj(appt_date)].append(row)

    for appt_day, day_rows in grouped_mc.items():
        # Try Missed Charges view first
        page.locator("[data-testid='tree-option-Missed Charges']").click()
        wait_for_grid_settled(page)
        apply_date_filter(page, appt_day, appt_day)

        for db_id, appt_id, patient_name, dob, appt_date in day_rows:
            row_el = find_row_by_appt_id_with_scroll(page, appt_id)

            if not row_el:
                # Fallback: try All Appointments
                page.locator("[data-testid='tree-option-All Appointments']").click()
                wait_for_grid_settled(page)
                apply_date_filter(page, appt_day, appt_day)

            process_facesheet_row(
                page, context, cur, conn,
                db_id, appt_id, patient_name,
                batch, "2C",
            )

            # Clear retry after successful processing
            if batch and batch[-1][0] == db_id:
                clear_retry_flag(cur, db_id)
                conn.commit()

            maybe_flush()

    # Final flush
    if batch:
        flush_batch(cur, batch, practice_name, to_date)

    cur.close()
    conn.close()


# =========================================================
# DASHBOARD NOTE HELPERS (PHASE 2.5)
# =========================================================

def _close_appointment_drawer(page):
    """Close the appointment detail drawer on the scheduling dashboard."""
    try:
        # The collapse/close caret button in the drawer header
        btn = page.locator(
            "div.MuiDrawer-root button:has(svg[data-testid='RightCaretIcon'])"
        )
        if btn.count():
            btn.first.click(force=True)
            page.wait_for_timeout(200)
            return
    except Exception:
        pass
    try:
        close_btn = page.locator("button[aria-label='Close']").first
        if close_btn.count():
            close_btn.click(force=True)
            page.wait_for_timeout(200)
            return
    except Exception:
        pass
    page.keyboard.press("Escape")
    page.wait_for_timeout(200)


def _scrape_appt_note_from_drawer(page):
    """
    Read the 'Appt Note' cell from the visit-information table in the open
    drawer. Returns None when the drawer has no Appt Note field (some
    practices' drawers don't expose one) — the caller falls back to the
    card's 'Note Signed' badge in that case.
    """
    try:
        row_loc = page.locator("tr:has(th:has-text('Appt Note')) td")
        if row_loc.count() == 0:
            return None
        raw = row_loc.first.inner_text().strip()
        return raw if raw else None
    except Exception:
        return None


# =========================================================
# SCRAPE DASHBOARD NOTES (PHASE 2.5)
# =========================================================

def scrape_dashboard_notes(page, from_date, to_date, practice_name, entity, sub_entity, ehr_name, scope_to_window=False):
    """
    After facesheets, navigate to the scheduling dashboard and scrape
    Appt Notes for appointments that are Checked Out (or have since
    moved to Finished).

    Iterates each appointment date in the range, opens the Dashboard,
    clicks "Finished" tab, then clicks each "Note Signed" card to
    read the Appt Note from the drawer.
    """
    conn = get_ehr_connection()
    cur = conn.cursor()

    # ---- Appointments that need notes ----
    # Daily path leaves scope_to_window=False (unbounded) so late-signed notes
    # from prior days still get picked up on future runs. Backfill passes
    # scope_to_window=True to stay inside the requested --start/--end window.
    date_clause = "AND appt_date BETWEEN %s AND %s" if scope_to_window else ""
    params = [entity, sub_entity, ehr_name, practice_name]
    if scope_to_window:
        params += [from_date, to_date]
    cur.execute(
        f"""
        SELECT id, appt_id, appt_date, patient_name, appt_status
        FROM {TABLE_NAME}
        WHERE entity=%s AND sub_entity=%s AND ehr_name=%s
          AND practice=%s
          AND appt_note IS NULL
          {date_clause}
        ORDER BY appt_date, appt_time
        """,
        params,
    )
    rows = cur.fetchall()

    if not rows:
        print("[NOTES] No appointments need notes. Skipping.")
        cur.close()
        conn.close()
        return

    print(f"[NOTES] {len(rows)} appointments need notes")

    grouped = defaultdict(list)
    for row in rows:
        db_id, appt_id, appt_date, patient_name, appt_status = row
        grouped[to_date_obj(appt_date)].append(row)

    # ---- Ensure all dashboard filters are checked (once) ----
    first_date = sorted(grouped.keys())[0].strftime("%Y-%m-%d")
    page.goto(f"https://app.kareo.com/v2/#/scheduling/dashboard/day/{first_date}")
    page.wait_for_load_state("domcontentloaded")
    try:
        page.wait_for_selector("button[role='tab']", timeout=10_000)
    except Exception:
        pass
    page.wait_for_timeout(500)

    # Check each filter group: Providers, Staff, Rooms, Service Locations
    for group_name in ["Providers", "Staff", "Rooms", "Service Locations"]:
        group = page.locator(f"[data-testid='{group_name}-checkbox-group']")
        if group.count() == 0:
            continue
        parent_cb = group.locator("input[type='checkbox']").first
        if parent_cb.count() and not parent_cb.is_checked():
            parent_cb.click(force=True)
            print(f"[NOTES] Checked filter: {group_name}")
            page.wait_for_timeout(200)

    for appt_day, day_rows in sorted(grouped.items()):
        date_str = appt_day.strftime("%Y-%m-%d")
        print(f"[NOTES] Dashboard date: {date_str} ({len(day_rows)} appointments)")

        # ---- Navigate to dashboard for this date ----
        # Navigate to a neutral page first, then the target date
        # This forces the SPA to fully re-render for the new date
        page.goto("https://app.kareo.com/v2/#/scheduling/dashboard")
        page.wait_for_load_state("domcontentloaded")
        page.wait_for_timeout(300)

        page.goto(
            f"https://app.kareo.com/v2/#/scheduling/dashboard/day/{date_str}"
        )
        page.wait_for_load_state("domcontentloaded")

        # Wait for the tab bar to appear (means dashboard loaded)
        try:
            page.wait_for_selector("button[role='tab']", timeout=10_000)
        except Exception:
            print(f"[NOTES] Dashboard didn't load for {date_str}, skipping")
            continue

        page.wait_for_timeout(500)

        # ---- Click "Finished" tab ----
        finished_tab = page.locator("button[role='tab']:has-text('Finished')")
        if finished_tab.count() == 0:
            print(f"[NOTES] No 'Finished' tab for {date_str}, skipping")
            continue
        finished_tab.first.click()

        # Wait for appointment cards to load
        try:
            page.wait_for_selector(
                "[data-testid^='appointment-list-item']",
                timeout=8_000,
            )
        except Exception:
            print(f"[NOTES] No cards loaded in Finished tab for {date_str}, skipping")
            continue

        page.wait_for_timeout(300)

        # ---- Build lookup by sorted name key ----
        needed = {}
        for db_id, appt_id, appt_date, patient_name, appt_status in day_rows:
            key = _name_key(patient_name)
            needed[key] = (db_id, appt_id, patient_name, appt_status)

        # ---- Read EVERY card in the Finished tab ----
        # The Finished tab lazy-renders ~6 cards at a time. Clicking a card to
        # open its drawer resets the list scroll to the top, which previously
        # stranded every card below the first cluster (marked the top ~3, then
        # bailed). So we split into two phases:
        #   Phase 1 — scroll the whole list WITHOUT clicking, collecting each
        #             card's name key + signed flag. No clicks => scroll stays
        #             put => we reliably reach the bottom.
        #   Phase 2 — match the collected cards against `needed` and mark the
        #             signed ones. Eligibility only needs the "Note Signed"
        #             badge (readable from card text), so no drawer click is
        #             required; appt_note gets the signed sentinel.
        CARD_SEL = "[data-testid^='appointment-list-item']"
        MAX_SCROLL_PASSES = 80

        # ----- Phase 1: collect all cards (no clicking) -----
        collected = {}  # name_key -> note_signed (bool)
        stable = 0
        for _pass in range(MAX_SCROLL_PASSES):
            cards = page.locator(CARD_SEL)
            n = cards.count()
            new_this_pass = 0
            for i in range(n):
                try:
                    card = cards.nth(i)
                    card_text = card.inner_text().strip()
                    link = card.locator("a[data-testid='patient-link']")
                    card_patient = (
                        link.first.inner_text().strip() if link.count() else card_text
                    )
                    key = _name_key(card_patient)
                    if key in collected:
                        continue
                    collected[key] = ("Note Signed" in card_text)
                    new_this_pass += 1
                except Exception as e:
                    print(f"[NOTES ERROR] collect card: {e}")

            if new_this_pass == 0:
                stable += 1
            else:
                stable = 0
            if stable >= 3:
                break

            try:
                cards = page.locator(CARD_SEL)
                cnt = cards.count()
                if cnt:
                    cards.nth(cnt - 1).scroll_into_view_if_needed(timeout=3_000)
            except Exception:
                pass
            page.wait_for_timeout(300)

        print(f"[NOTES] {date_str}: read {len(collected)} finished cards")

        # ----- Phase 2: match + mark signed cards -----
        for card_key, note_signed in collected.items():
            if not needed:
                break
            matched_key = _find_name_match(card_key, needed)
            if not matched_key:
                continue
            if not note_signed:
                # Card is present but note isn't signed yet — leave it in
                # `needed` so it reports as not-found (correctly skipped).
                continue

            db_id, appt_id, patient_name, appt_status = needed[matched_key]
            note_value = "[SIGNED - no note text in drawer]"
            try:
                if appt_status != "Checked Out":
                    cur.execute(
                        f"""
                        UPDATE {TABLE_NAME}
                        SET appt_note = %s,
                            appt_status = 'Checked Out',
                            retry_flag = 0,
                            retry_reason = NULL,
                            updated_date = now()
                        WHERE id = %s
                        """,
                        (note_value, db_id),
                    )
                else:
                    cur.execute(
                        f"""
                        UPDATE {TABLE_NAME}
                        SET appt_note = %s, updated_date = now()
                        WHERE id = %s
                        """,
                        (note_value, db_id),
                    )
                conn.commit()
                print(f"[NOTES] Marked {appt_id} ({patient_name}) eligible via signed-badge")
                del needed[matched_key]
            except Exception as e:
                conn.rollback()
                print(f"[NOTES ERROR] mark {appt_id}: {e}")

        if needed:
            unmatched = list(needed.values())
            print(f"[NOTES] {len(unmatched)} not found in Finished tab for {date_str}")
            for db_id, appt_id, pname, _ in unmatched:
                print(f"  - {appt_id} {pname}")

    cur.close()
    conn.close()


# =========================================================
# CREATE ZIP FILE (PHASE 3)
# =========================================================

def create_daily_zip_with_json(entity, sub_entity, practice_name, end_date, ehr_name):
    conn = get_ehr_connection()
    cur = conn.cursor()

    folder_date = end_date.strftime("%Y-%m-%d")

    temp_dir = os.path.join(DOWNLOAD_DIR, "zip_tmp")
    os.makedirs(temp_dir, exist_ok=True)

    records = []
    included_appt_ids = []  # db_ids for post-upload file_path update
    zipped_pdfs = []        # local PDF paths to clean up after zipping

    # PDFs already live on local disk (flush_batch no longer deletes them).
    # Build the ZIP straight from the download folder — no blob round-trip.
    for filename in os.listdir(DOWNLOAD_DIR):
        if not filename.lower().endswith(".pdf"):
            continue

        local_pdf = os.path.join(DOWNLOAD_DIR, filename)

        parts = filename.replace(".pdf", "").split("_")
        if len(parts) < 3:
            continue

        appt_id = parts[0]
        facesheet_id = parts[-1]

        cur.execute(
            f"""
            SELECT id, appt_id, tebra_facesheet_id, appt_date, appt_time,
                   patient_name, dob, provider_name, service_location,
                   appt_reason, appt_status, patient_id, appt_note, file_path
            FROM {TABLE_NAME}
            WHERE appt_id=%s AND tebra_facesheet_id=%s AND ehr_name=%s
              AND practice=%s
              AND process_status='Processed'
            """,
            (appt_id, facesheet_id, ehr_name, practice_name),
        )

        row = cur.fetchone()
        if not row:
            continue

        previously_delivered = row[13] is not None

        records.append({
            "pdf_file": filename,
            "appt_id": row[1],
            "facesheet_id": row[2],
            "appt_date": str(row[3]),
            "appt_time": str(row[4]),
            "patient_name": row[5],
            "dob": str(row[6]),
            "provider_name": row[7],
            "service_location": row[8],
            "appt_reason": row[9],
            "appt_status": row[10],
            "patient_id": row[11],
            "appt_note": row[12],
            "previously_delivered": previously_delivered,
        })
        included_appt_ids.append(row[0])
        zipped_pdfs.append(local_pdf)

    if not records:
        print("[ZIP] No processed PDFs found. Skipping zip.")
        cur.close()
        conn.close()
        return

    practice_abbr = get_practice_abbr(practice_name)
    random_suffix = generate_random_suffix()

    json_name = f"tebra_facesheets_{practice_abbr}_{folder_date}_{random_suffix}.json"
    json_path = os.path.join(temp_dir, json_name)

    metadata = {
        "generated_on": _now_cst().isoformat(),
        "entity": entity,
        "sub_entity": sub_entity,
        "ehr_name": ehr_name,
        "practice": practice_name,
        "appointments": records,
    }

    with open(json_path, "w") as f:
        json.dump(metadata, f, indent=4)

    zip_name = f"tebra_facesheets_{practice_abbr}_{folder_date}_{random_suffix}.zip"
    zip_path = os.path.join(temp_dir, zip_name)

    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
        for r in records:
            z.write(os.path.join(DOWNLOAD_DIR, r["pdf_file"]), r["pdf_file"])
        z.write(json_path, json_name)

    # Upload the ZIP straight into the practice folder in 834labs-sftp
    # (no raw/pch_tebra tree — the SFTP uploader resolves the practice folder).
    zip_blob_path = None
    try:
        zip_blob_path = upload_zip_to_rcm_sftp(zip_path, zip_name, practice_name)
    except Exception as e:
        print(f"[SFTP ERROR] {e}")

    # ---- Write file_path back to each included appointment ----
    if included_appt_ids and zip_blob_path:
        cur.execute(
            f"""
            UPDATE {TABLE_NAME}
            SET file_path = %s,
                updated_date = now()
            WHERE id = ANY(%s)
            """,
            (zip_blob_path, included_appt_ids),
        )
        conn.commit()

    # Cleanup — the ZIP and the local PDFs it bundled
    try:
        os.remove(zip_path)
    except Exception as e:
        print(f"[CLEANUP ERROR] Could not remove ZIP: {e}")
    for p in zipped_pdfs:
        try:
            os.remove(p)
        except OSError:
            pass

    cur.close()
    conn.close()


# =========================================================
# SFTP UPLOAD
# =========================================================

def upload_zip_to_rcm_sftp(local_zip_path, zip_name, practice_name):
    SFTP_CONTAINER = "834labs-sftp"
    print(f"[SFTP] Uploading zip to {STORAGE_ACCOUNT_NAME}/{SFTP_CONTAINER}")

    service = BlobServiceClient.from_connection_string(AZURE_STORAGE_CONNECTION_STRING)
    container = service.get_container_client(SFTP_CONTAINER)

    normalized_practice = _normalize_text(practice_name)
    print(f"[SFTP] Looking for folder matching: {practice_name} ({normalized_practice})")

    # Collect top-level folders
    folders = set()
    for blob in container.list_blobs():
        parts = blob.name.split("/")
        if len(parts) > 1:
            folders.add(parts[0])

    print(f"[SFTP] Found folders: {folders}")

    matched_folder = None

    # 1) Normalized substring match
    for folder in folders:
        if normalized_practice in _normalize_text(folder):
            matched_folder = folder
            print(f"[SFTP] Matched via normalized match: {matched_folder}")
            break

    # 2) Exact match fallback
    if not matched_folder and practice_name in folders:
        matched_folder = practice_name
        print(f"[SFTP] Matched via exact name: {matched_folder}")

    # 3) Create folder if not found
    if not matched_folder:
        matched_folder = practice_name.strip()
        print(f"[SFTP] No match found. Creating new folder: {matched_folder}")
        container.upload_blob(f"{matched_folder}/.init", b"", overwrite=True)

    # Upload
    blob_path = f"{matched_folder}/{zip_name}"
    with open(local_zip_path, "rb") as f:
        container.upload_blob(blob_path, f, overwrite=True)

    print(f"[SFTP] Uploaded {zip_name} to {matched_folder}/")
    return blob_path


# =========================================================
# CLEANUP
# =========================================================

def cleanup_acc_directory():
    print("[CLEANUP] Cleaning /acc root files (not subfolders)")
    for item in os.listdir(DOWNLOAD_DIR):
        full_path = os.path.join(DOWNLOAD_DIR, item)
        if os.path.isfile(full_path):
            try:
                os.remove(full_path)
            except Exception as e:
                print(f"[CLEANUP ERROR] {item}: {e}")


# =========================================================
# MAIN
# =========================================================

def run_tebra_rpa(
    start_date: datetime,
    end_date: datetime,
    practice_name: str,
    entity: str,
    sub_entity: str,
    ehr_name: str,
    skip_notes: bool = False,
    skip_facesheets: bool = False,
):
    run_start = _now_cst()
    conn = get_ehr_connection()
    cur = conn.cursor()

    os.makedirs(DOWNLOAD_DIR, exist_ok=True)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False, args=["--start-maximized"])
        context = browser.new_context(no_viewport=True)
        page = context.new_page()

        page.goto(LOGIN_URL)
        page.fill("#userName", EMAIL)
        page.fill("#password", PASSWORD)
        page.click("#sign-in")

        page.wait_for_selector("h3:has-text('Practice select')")
        practice_found = False

        for i in range(page.locator("h6.MuiTypography-subtitle2").count()):
            el = page.locator("h6.MuiTypography-subtitle2").nth(i)
            if practice_name.lower() in el.inner_text().lower():
                el.click()
                practice_found = True
                otp_since = datetime.now(timezone.utc)

                handle_tebra_otp_if_present(
                    page,
                    fetch_latest_otp_code_fn=fetch_latest_tebra_otp_code,
                    since_dt_utc=otp_since,
                    poll_seconds=75,
                )
                break

        if not practice_found:
            raise RuntimeError(f"Practice '{practice_name}' not found in Tebra UI")

        # Navigate directly — hover on reporting icon gets blocked by search bar
        page.goto("https://app.kareo.com/v2/#/worklist/appointments")
        wait_for_grid_settled(page)

        from_date = start_date
        to_date = end_date

        apply_date_filter(page, from_date, to_date)

        # ---- Phase 1: Scrape appointments ----
        def extract(row):
            status = cell(row, "APPOINTMENT_STATUS")
            return {
                "appt_id": cell(row, "APPOINTMENT_ID"),
                "appt_date": cell(row, "START_DATE"),
                "appt_time": cell(row, "START_TIME"),
                "patient_name": cell(row, "PATIENT_NAME"),
                "dob": cell(row, "PATIENT_DOB"),
                "home_phone": cell(row, "PATIENT_HOME_PHOME"),
                "mobile_phone": cell(row, "PATIENT_MOBILE_PHONE"),
                "provider_name": cell(row, "PROVIDER_NAME"),
                "service_location": cell(row, "SERVICE_LOCATION_NAME"),
                "appt_reason": cell(row, "APPOINTMENT_REASON"),
                "appt_status": status,
                "retry_flag": 0 if status == "Checked Out" else 1,
                "retry_reason": None,
                "entity": entity,
                "sub_entity": sub_entity,
                "practice": practice_name,
                "ehr_name": ehr_name,
            }

        appts = scrape_virtual_grid(page, extract)
        print(f"[APPTS] scraped={len(appts)}")

        for rec in appts.values():
            upsert_appointment(cur, rec)
        conn.commit()

        # ---- Missed Charges flagging ----
        page.locator("[data-testid='tree-option-Missed Charges']").click()
        wait_for_grid_settled(page)
        apply_date_filter(page, from_date, to_date)

        missed = scrape_virtual_grid(
            page, lambda r: {"appt_id": cell(r, "APPOINTMENT_ID")}
        )
        set_missed_charges(cur, list(missed.keys()), entity, sub_entity, ehr_name)
        conn.commit()

        # ---- Phase 2.5: Dashboard Notes (runs BEFORE facesheets so
        # signed-note detection gates facesheet downloads) ----
        if not skip_notes:
            scrape_dashboard_notes(page, from_date, to_date, practice_name, entity, sub_entity, ehr_name)

        # ---- Phase 2: Facesheets (only for appointments with signed notes) ----
        if not skip_facesheets:
            # scrape_dashboard_notes (Phase 2.5) leaves the page on the Dashboard,
            # so the appointments report tree isn't present. Re-navigate to the
            # worklist first — same entry point used before Phase 1 — otherwise
            # the tree-option click below times out.
            page.goto("https://app.kareo.com/v2/#/worklist/appointments")
            wait_for_grid_settled(page)

            page.locator("[data-testid='tree-option-All Appointments']").click()
            wait_for_grid_settled(page)
            apply_date_filter(page, from_date, to_date)

            run_facesheets(page, context, from_date, to_date, practice_name, entity, sub_entity, ehr_name)

            # ---- Phase 3: ZIP ----
            create_daily_zip_with_json(entity, sub_entity, practice_name, end_date, ehr_name)

        browser.close()

    # ---- Phase 4: Patient-match reconciliation ----
    run_patient_match_pass(practice_name, entity, sub_entity, ehr_name, from_date, to_date)

    cur.close()
    conn.close()

    # ---- Run log (wpo.ops_pch_logs on pch) ----
    # has_error now runs on the pch connection, where ehr_appointments
    # actually lives (this check previously ran on the myopsprod conn — the
    # wrong DB — so it never saw real errors).
    check_conn = get_ehr_connection()
    check_cur = check_conn.cursor()
    check_cur.execute(
        f"""
        SELECT 1
        FROM {TABLE_NAME}
        WHERE entity=%s AND sub_entity=%s AND ehr_name=%s
          AND practice=%s
          AND process_status='Error'
        LIMIT 1
        """,
        (entity, sub_entity, ehr_name, practice_name),
    )
    has_error = check_cur.fetchone() is not None
    check_cur.close()
    check_conn.close()

    log_run_to_pch(
        script_name="OPS_EMR_RPA",
        process_type=f"RCM - {practice_name}",
        status="Error" if has_error else "Success",
        error="One or more records failed" if has_error else None,
        company_id=entity,
        started_at=run_start,
        ended_at=_now_cst(),
    )
    cleanup_acc_directory()


def discover_tebra_practices():
    """
    Launch browser, login to Tebra, read all practice names from
    the practice-select screen, close browser. Returns list of names.
    """
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False, args=["--start-maximized"])
        context = browser.new_context(no_viewport=True)
        page = context.new_page()

        page.goto(LOGIN_URL)
        page.fill("#userName", EMAIL)
        page.fill("#password", PASSWORD)
        page.click("#sign-in")

        page.wait_for_selector("h3:has-text('Practice select')", timeout=30_000)
        page.wait_for_timeout(2000)  # let all practice tiles fully render

        elements = page.locator("h6.MuiTypography-subtitle2")
        count = elements.count()
        print(f"[DISCOVER] Found {count} elements")

        practices = []
        for i in range(count):
            name = elements.nth(i).inner_text().strip()
            print(f"[DISCOVER]   {i}: '{name}'")
            if name:
                practices.append(name)

        browser.close()

    print(f"[DISCOVER] Practices: {practices}")
    return practices


# if __name__ == "__main__":
#     run_tebra_rpa(
#         start_date=datetime(2025, 1, 1),
#         end_date=datetime(2025, 1, 7),
#         practice_name="The PreOp Center",
#         entity="270681372",
#         sub_entity="270681372001",
#         ehr_name="Tebra",
#     )


def run_notes_only(
    start_date: datetime,
    end_date: datetime,
    practice_name: str,
    entity: str,
    sub_entity: str,
    ehr_name: str,
):
    """
    Open a browser session, log in, and run ONLY the dashboard notes
    scrape for the given date range.  Used by the backfill script as a
    single final pass after all appointment/facesheet chunks are done.
    """
    print(f"[NOTES-ONLY] {start_date.date()} -> {end_date.date()}  practice={practice_name}")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False, args=["--start-maximized"])
        context = browser.new_context(no_viewport=True)
        page = context.new_page()

        page.goto(LOGIN_URL)
        page.fill("#userName", EMAIL)
        page.fill("#password", PASSWORD)
        page.click("#sign-in")

        page.wait_for_selector("h3:has-text('Practice select')")
        practice_found = False

        for i in range(page.locator("h6.MuiTypography-subtitle2").count()):
            el = page.locator("h6.MuiTypography-subtitle2").nth(i)
            if practice_name.lower() in el.inner_text().lower():
                el.click()
                practice_found = True
                otp_since = datetime.now(timezone.utc)

                handle_tebra_otp_if_present(
                    page,
                    fetch_latest_otp_code_fn=fetch_latest_tebra_otp_code,
                    since_dt_utc=otp_since,
                    poll_seconds=75,
                )
                break

        if not practice_found:
            raise RuntimeError(f"Practice '{practice_name}' not found in Tebra UI")

        scrape_dashboard_notes(page, start_date, end_date, practice_name, entity, sub_entity, ehr_name, scope_to_window=True)

        browser.close()

    print("[NOTES-ONLY] Done")

# =========================================================
# SCHEMA MIGRATION (idempotent — safe to call on every startup)
# =========================================================

def ensure_appointments_schema():
    """
    Adds columns introduced alongside the signed-note policy:
    - patient_match BOOLEAN    TRUE if patient_id matches ehr.ehr_patients
      - file_path     TEXT       Blob path of the most recent ZIP containing this appointment
    Idempotent via ADD COLUMN IF NOT EXISTS.
    """
    conn = get_ehr_connection()
    cur = conn.cursor()
    try:
        cur.execute(f"""
            ALTER TABLE {TABLE_NAME}
            ADD COLUMN IF NOT EXISTS patient_match BOOLEAN,
            ADD COLUMN IF NOT EXISTS file_path     TEXT
        """)
        conn.commit()
    finally:
        cur.close()
        conn.close()


# =========================================================
# PATIENT-MATCH RECONCILIATION (Phase 4)
# =========================================================

def run_patient_match_pass(practice_name, entity, sub_entity, ehr_name, from_date, to_date):
    """
    For every appointment in [from_date, to_date] for this practice, flag
    whether the patient_id exists in ehr.ehr_patients (active row only).

    patient_match = TRUE   -> patient_id is populated AND found in ehr_patients
    patient_match = FALSE  -> patient_id is populated BUT not found in ehr_patients
    patient_match = NULL   -> patient_id not yet populated (will retry later)
    """
    conn = get_ehr_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            f"""
            UPDATE {TABLE_NAME} a
            SET patient_match = (
                    CASE
                        WHEN a.patient_id IS NULL THEN NULL
                        WHEN EXISTS (
                            SELECT 1 FROM {PATIENTS_TABLE} p
                            WHERE p.ehr_name = a.ehr_name
                              AND p.entity   = a.entity
                              AND p.sub_entity = a.sub_entity
                              AND p.patient_id = a.patient_id
                              AND p.effective_end_date IS NULL
                        ) THEN TRUE
                        ELSE FALSE
                    END
                ),
                updated_date = now()
            WHERE a.entity = %s
              AND a.sub_entity = %s
              AND a.ehr_name = %s
              AND a.practice = %s
              AND a.appt_date BETWEEN %s AND %s
            """,
            (entity, sub_entity, ehr_name, practice_name, from_date, to_date),
        )
        conn.commit()

        # Quick summary log
        cur.execute(
            f"""
            SELECT
                COUNT(*) FILTER (WHERE patient_match IS TRUE)  AS matched,
                COUNT(*) FILTER (WHERE patient_match IS FALSE) AS unmatched,
                COUNT(*) FILTER (WHERE patient_match IS NULL)  AS pending
            FROM {TABLE_NAME}
            WHERE entity=%s AND sub_entity=%s AND ehr_name=%s
              AND practice=%s
              AND appt_date BETWEEN %s AND %s
            """,
            (entity, sub_entity, ehr_name, practice_name, from_date, to_date),
        )
        matched, unmatched, pending = cur.fetchone()
        print(f"[PATIENT-MATCH] matched={matched} unmatched={unmatched} pending={pending}")
    finally:
        cur.close()
        conn.close()


# =========================================================
# BACKFILL: FACESHEETS (Option B — one PDF per facesheet_id)
# =========================================================

def _process_facesheet_row_backfill(
    page, context, cur, conn,
    appt_id, patient_name,
    db_ids_for_this_patient,
    phase_label,
    practice_name, end_date,
):
    """
    Backfill facesheet processor (per-patient dedup).

    Click into ONE appointment for this patient, grab facesheet_id,
    download PDF ONCE with {facesheet_id}_{last_name}.pdf naming (kept on
    local disk for zipping), then UPDATE every db_id for this patient with
    the resolved facesheet_id + patient_id + process_status='Processed'.
    """
    fs = None
    opened_new_tab = False
    try:
        row = find_row_by_appt_id_with_scroll(page, appt_id)
        if not row:
            raise RuntimeError("No matching row found in grid")

        fs, opened_new_tab = click_patient_row(page, row)

        m = re.search(r"/Facesheet/(\d+)", fs.url)
        if not m:
            raise RuntimeError("Facesheet ID not found in URL")

        facesheet_id = m.group(1)

        tebra_patient_id = scrape_tebra_patient_id(fs)

        pdf_url = f"https://app.kareo.com/patients/print/{facesheet_id}.pdf"
        last_name = patient_name.split(",")[0].strip().replace(" ", "_")
        pdf_filename = f"{facesheet_id}_{last_name}.pdf"
        pdf_path = os.path.join(DOWNLOAD_DIR, pdf_filename)

        resp = context.request.get(pdf_url)
        if resp.status != 200:
            raise RuntimeError(f"PDF download failed: HTTP {resp.status}")

        with open(pdf_path, "wb") as f:
            f.write(resp.body())

        # PDF stays on local disk; the backfill ZIP is built from local files.

        cur.execute(
            f"""
            UPDATE {TABLE_NAME}
            SET tebra_facesheet_id = COALESCE(%s, tebra_facesheet_id),
                patient_id = COALESCE(%s, patient_id),
                process_status = 'Processed',
                process_error_stage = NULL,
                process_error_message = NULL,
                retry_flag = 0,
                retry_reason = NULL,
                updated_date = now()
            WHERE id = ANY(%s)
            """,
            (facesheet_id, tebra_patient_id, db_ids_for_this_patient),
        )
        conn.commit()

        return True, facesheet_id

    except Exception as e:
        print(f"[ERROR-{phase_label}] {appt_id} {e}")
        try:
            conn.rollback()
        except Exception:
            pass
        cur.execute(
            f"""
            UPDATE {TABLE_NAME}
            SET process_status='Error',
                process_error_stage=%s,
                process_error_message=%s,
                updated_date=now()
            WHERE id = ANY(%s)
            """,
            (phase_label, str(e)[:500], db_ids_for_this_patient),
        )
        conn.commit()
        return False, None

    finally:
        # Always tear down the facesheet view so tabs don't accumulate and the
        # next patient starts on the grid.
        try:
            if opened_new_tab and fs is not None:
                fs.close()
            else:
                page.go_back()
                wait_for_grid_settled(page)
        except Exception:
            pass


def run_facesheets_backfill(page, context, from_date, to_date,
                            practice_name, entity, sub_entity, ehr_name):
    """
    Backfill facesheet pass — one-shot over the full window.
    Gates on signed notes; dedupes by patient.
    """
    conn = get_ehr_connection()
    cur = conn.cursor()

    cur.execute(
        f"""
        SELECT id, appt_id, patient_name, appt_date
        FROM {TABLE_NAME}
        WHERE entity=%s AND sub_entity=%s AND ehr_name=%s
          AND practice=%s
          AND appt_note IS NOT NULL
          AND COALESCE(process_status, '') IN ('', 'Error')
          AND appt_date BETWEEN %s AND %s
        ORDER BY patient_name, appt_date
        """,
        (entity, sub_entity, ehr_name, practice_name, from_date, to_date),
    )
    rows = cur.fetchall()
    print(f"[BACKFILL FS] {len(rows)} signed-note appointment rows need facesheets")

    by_patient = defaultdict(list)
    for db_id, appt_id, patient_name, appt_date in rows:
        by_patient[patient_name].append((db_id, appt_id, appt_date))

    print(f"[BACKFILL FS] {len(by_patient)} unique patients to process")

    def _reset_to_grid():
        # Return the page to the appointments worklist grid so the next
        # apply_date_filter finds the Table-filters button.
        page.goto("https://app.kareo.com/v2/#/worklist/appointments")
        wait_for_grid_settled(page)
        page.locator("[data-testid='tree-option-All Appointments']").click()
        wait_for_grid_settled(page)

    _reset_to_grid()

    for idx, (patient_name, appts) in enumerate(by_patient.items(), 1):
        appts.sort(key=lambda x: x[2])
        primary_db_id, primary_appt_id, primary_appt_date = appts[0]
        all_db_ids_for_patient = [a[0] for a in appts]
        appt_day = to_date_obj(primary_appt_date)

        print(f"[BACKFILL FS] [{idx}/{len(by_patient)}] {patient_name} "
              f"({len(all_db_ids_for_patient)} appts, using {primary_appt_id} on {appt_day})")

        # One patient's failure must never abort the whole practice — otherwise
        # the ZIP step (which runs after this loop) never executes. Log, mark
        # the rows Error, recover page state, and continue.
        try:
            apply_date_filter(page, appt_day, appt_day)
            _process_facesheet_row_backfill(
                page, context, cur, conn,
                primary_appt_id, patient_name,
                all_db_ids_for_patient,
                "BACKFILL",
                practice_name, to_date,
            )
        except Exception as e:
            print(f"[BACKFILL FS] [{idx}] {patient_name} failed, recovering: {e!r}")
            try:
                conn.rollback()
            except Exception:
                pass
            try:
                cur.execute(
                    f"""
                    UPDATE {TABLE_NAME}
                    SET process_status='Error',
                        process_error_stage='BACKFILL',
                        process_error_message=%s,
                        updated_date=now()
                    WHERE id = ANY(%s)
                    """,
                    (str(e)[:500], all_db_ids_for_patient),
                )
                conn.commit()
            except Exception:
                conn.rollback()
            try:
                _reset_to_grid()
            except Exception as re:
                print(f"[BACKFILL FS] page recovery failed: {re!r}")

    cur.close()
    conn.close()


# =========================================================
# BACKFILL: ZIP (Option B — deduped PDFs, per-appointment JSON)
# =========================================================

def create_backfill_zip_with_json(entity, sub_entity, practice_name,
                                  from_date, to_date, ehr_name):
    """
    ONE ZIP per practice for the entire backfill window.

    ZIP naming follows the daily convention (date = end_date of window).
    PDFs deduped by tebra_facesheet_id. JSON has one record per signed-note
    appointment; multiple records can reference the same pdf_file.
    Writes file_path + previously_delivered the same way daily does.
    """
    conn = get_ehr_connection()
    cur = conn.cursor()
    folder_date = to_date.strftime("%Y-%m-%d")

    temp_dir = os.path.join(DOWNLOAD_DIR, "zip_tmp")
    os.makedirs(temp_dir, exist_ok=True)

    cur.execute(
        f"""
        SELECT id, appt_id, tebra_facesheet_id, appt_date, appt_time,
               patient_name, dob, provider_name, service_location,
               appt_reason, appt_status, patient_id, appt_note, file_path
        FROM {TABLE_NAME}
        WHERE entity=%s AND sub_entity=%s AND ehr_name=%s
          AND practice=%s
          AND appt_note IS NOT NULL
          AND process_status='Processed'
          AND tebra_facesheet_id IS NOT NULL
          AND appt_date BETWEEN %s AND %s
        ORDER BY appt_date, appt_time
        """,
        (entity, sub_entity, ehr_name, practice_name, from_date, to_date),
    )
    appt_rows = cur.fetchall()

    if not appt_rows:
        print("[BACKFILL ZIP] No processed signed-note appointments. Skipping zip.")
        cur.close()
        conn.close()
        return

    records = []
    included_db_ids = []
    needed_pdfs = {}

    for row in appt_rows:
        (db_id, appt_id, facesheet_id, appt_date, appt_time, patient_name, dob,
         provider_name, service_location, appt_reason, appt_status,
         patient_id, appt_note, existing_file_path) = row

        last_name = patient_name.split(",")[0].strip().replace(" ", "_")
        pdf_filename = f"{facesheet_id}_{last_name}.pdf"
        needed_pdfs[facesheet_id] = pdf_filename

        records.append({
            "pdf_file": pdf_filename,
            "appt_id": appt_id,
            "facesheet_id": facesheet_id,
            "appt_date": str(appt_date),
            "appt_time": str(appt_time),
            "patient_name": patient_name,
            "dob": str(dob),
            "provider_name": provider_name,
            "service_location": service_location,
            "appt_reason": appt_reason,
            "appt_status": appt_status,
            "patient_id": patient_id,
            "appt_note": appt_note,
            "previously_delivered": existing_file_path is not None,
        })
        included_db_ids.append(db_id)

    print(f"[BACKFILL ZIP] {len(records)} appointments, {len(needed_pdfs)} unique PDFs")

    for facesheet_id, pdf_filename in list(needed_pdfs.items()):
        local_pdf = os.path.join(DOWNLOAD_DIR, pdf_filename)
        if not os.path.exists(local_pdf):
            print(f"[BACKFILL ZIP] Missing local PDF {pdf_filename}")
            # Drop any appointment records referencing this missing PDF
            records_before = len(records)
            keep_idx = [i for i, r in enumerate(records) if r["facesheet_id"] != facesheet_id]
            records = [records[i] for i in keep_idx]
            included_db_ids = [included_db_ids[i] for i in keep_idx]
            print(f"[BACKFILL ZIP] Dropped {records_before - len(records)} records")

    if not records:
        print("[BACKFILL ZIP] No records left after blob checks. Skipping zip.")
        cur.close()
        conn.close()
        return

    practice_abbr = get_practice_abbr(practice_name)
    random_suffix = generate_random_suffix()

    json_name = f"tebra_facesheets_{practice_abbr}_{folder_date}_{random_suffix}.json"
    json_path = os.path.join(temp_dir, json_name)

    metadata = {
        "generated_on": _now_cst().isoformat(),
        "entity": entity,
        "sub_entity": sub_entity,
        "ehr_name": ehr_name,
        "practice": practice_name,
        "appointments": records,
    }

    with open(json_path, "w") as f:
        json.dump(metadata, f, indent=4)

    zip_name = f"tebra_facesheets_{practice_abbr}_{folder_date}_{random_suffix}.zip"
    zip_path = os.path.join(temp_dir, zip_name)

    unique_pdfs = {r["pdf_file"] for r in records}

    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
        for pdf_filename in unique_pdfs:
            local_pdf = os.path.join(DOWNLOAD_DIR, pdf_filename)
            if os.path.exists(local_pdf):
                z.write(local_pdf, pdf_filename)
        z.write(json_path, json_name)

    # Deliver the ZIP straight into the practice folder in 834labs-sftp
    # (no raw/pch_tebra tree).
    zip_blob_path = None
    try:
        zip_blob_path = upload_zip_to_rcm_sftp(zip_path, zip_name, practice_name)
    except Exception as e:
        print(f"[BACKFILL SFTP ERROR] {e}")

    # Write file_path back to every appointment included in this ZIP
    if included_db_ids and zip_blob_path:
        cur.execute(
            f"""
            UPDATE {TABLE_NAME}
            SET file_path = %s,
                updated_date = now()
            WHERE id = ANY(%s)
            """,
            (zip_blob_path, included_db_ids),
        )
        conn.commit()

    try:
        os.remove(zip_path)
    except Exception as e:
        print(f"[BACKFILL ZIP CLEANUP] Could not remove ZIP: {e}")
    for pdf_filename in unique_pdfs:
        try:
            os.remove(os.path.join(DOWNLOAD_DIR, pdf_filename))
        except OSError:
            pass

    cur.close()
    conn.close()


# =========================================================
# BACKFILL: BROWSER SESSION (facesheets + zip + match)
# =========================================================

def run_facesheets_and_zip_backfill(
    start_date: datetime,
    end_date: datetime,
    practice_name: str,
    entity: str,
    sub_entity: str,
    ehr_name: str,
):
    """
    Backfill's final pass. Assumes appointments + notes are already in DB.
    Opens one browser session, processes facesheets (per-patient deduped),
    builds ONE ZIP for the whole window, then runs patient-match.
    """
    print(f"[BACKFILL-FS+ZIP] {start_date.date()} -> {end_date.date()}  practice={practice_name}")

    os.makedirs(DOWNLOAD_DIR, exist_ok=True)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False, args=["--start-maximized"])
        context = browser.new_context(no_viewport=True)
        page = context.new_page()

        page.goto(LOGIN_URL)
        page.fill("#userName", EMAIL)
        page.fill("#password", PASSWORD)
        page.click("#sign-in")

        page.wait_for_selector("h3:has-text('Practice select')")
        practice_found = False

        for i in range(page.locator("h6.MuiTypography-subtitle2").count()):
            el = page.locator("h6.MuiTypography-subtitle2").nth(i)
            if practice_name.lower() in el.inner_text().lower():
                el.click()
                practice_found = True
                otp_since = datetime.now(timezone.utc)

                handle_tebra_otp_if_present(
                    page,
                    fetch_latest_otp_code_fn=fetch_latest_tebra_otp_code,
                    since_dt_utc=otp_since,
                    poll_seconds=75,
                )
                break

        if not practice_found:
            raise RuntimeError(f"Practice '{practice_name}' not found in Tebra UI")

        page.goto("https://app.kareo.com/v2/#/worklist/appointments")
        wait_for_grid_settled(page)

        run_facesheets_backfill(page, context, start_date, end_date,
                                practice_name, entity, sub_entity, ehr_name)

        browser.close()

    # ZIP + match passes are DB/blob only
    create_backfill_zip_with_json(entity, sub_entity, practice_name,
                                  start_date, end_date, ehr_name)

    run_patient_match_pass(practice_name, entity, sub_entity, ehr_name,
                           start_date, end_date)

    cleanup_acc_directory()
    print("[BACKFILL-FS+ZIP] Done")