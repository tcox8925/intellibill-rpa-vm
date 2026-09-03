"""
patient_insurance_rpa.py
────────────────────────
Patient Insurance Scraper

Self-contained patient-insurance scraper (part of the ehr package):
  1.  Login to Tebra/Kareo
  2.  Loop through practices (practice_name, entity, sub_entity)
  3.  For each practice: select practice → navigate Analytics → Patients
    4.  Scrape the All Patients MuiDataGrid → upsert into ehr.ehr_patients
  5.  For un-scraped patients: click patient → Account → Insurance tab →
      Edit primary case → scrape Policy #1 + #2 (plan name + policy number)

The table  ehr.ehr_patients  is EHR-agnostic — uses  ehr_name  column
so the same table can serve multiple EHR platforms in the future.

Entry point
-----------
    run_patient_insurance_rpa()
"""

from playwright.sync_api import sync_playwright, Page, BrowserContext
import re
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import psycopg2

from otp_info import handle_tebra_otp_if_present
from email_read import fetch_latest_tebra_otp_code
from .config import (
    EHR_NAME,
    LOGIN_URL,
    EMAIL,
    PASSWORD,
    PATIENTS_TABLE,
    POSTGRES_CONFIG_EHR,
    PLAYWRIGHT_HEADLESS,
    PLAYWRIGHT_LAUNCH_ARGS,
    PLAYWRIGHT_VIEWPORT,
)
from .db import log_run_event


# =========================================================
# CONFIG
# =========================================================

CST = ZoneInfo("America/Chicago")

def _now_cst():
    return datetime.now(CST)


def _resolve_db_password(db_config):
    """Use explicit DB password only."""
    if db_config.get("password"):
        return db_config["password"]
    raise RuntimeError(
        "Database password is required for password auth mode. "
        "Set the corresponding *_DB_PASSWORD in .env."
    )


# =========================================================
# DB CONNECTIONS
# =========================================================

def get_ehr_connection():
    """Connect to the EHR database for ehr schema tables."""
    return psycopg2.connect(
        host=POSTGRES_CONFIG_EHR["host"],
        dbname=POSTGRES_CONFIG_EHR["database"],
        user=POSTGRES_CONFIG_EHR["user"],
        password=_resolve_db_password(POSTGRES_CONFIG_EHR),
        sslmode="require",
    )


# =========================================================
# DDL — generic patients table
# =========================================================

CREATE_PATIENTS_TABLE_SQL = f"""
CREATE TABLE IF NOT EXISTS {PATIENTS_TABLE} (
    id                        SERIAL PRIMARY KEY,

    -- EHR identification
    ehr_name                  TEXT NOT NULL,           -- e.g. 'Tebra', 'eCW', 'Athena'
    patient_id                TEXT NOT NULL,           -- EHR-native patient ID
    entity                    TEXT NOT NULL,
    sub_entity                TEXT NOT NULL,
    practice                  TEXT,

    -- demographics (scraped from grid)
    patient_name              TEXT,
    dob                       TEXT,
    sex                       TEXT,
    marital_status            TEXT,
    email                     TEXT,
    home_phone                TEXT,
    mobile_phone              TEXT,
    address_line_1            TEXT,
    city                      TEXT,
    state                     TEXT,
    zip_code                  TEXT,
    status                    TEXT,                    -- Active / Inactive

    -- insurance info
    primary_insurance_name    TEXT,                    -- e.g. 'Cigna', 'Aetna', 'Medicare of Tennessee'
    primary_insurance_id      TEXT,                    -- policy number (scraped from billing profile)
    secondary_insurance_name  TEXT,
    secondary_insurance_id    TEXT,                    -- policy number (scraped from billing profile)
    primary_plan_name         TEXT,                    -- e.g. 'Medicare B', 'Cigna' (from billing profile)
    secondary_plan_name       TEXT,

    -- processing state
    insurance_scraped         BOOLEAN DEFAULT FALSE,
    insurance_scrape_error    TEXT,

    -- clinical classification (populated by downstream process; NULL = unknown)
    post_op                   BOOLEAN,
    behavioral_postop         BOOLEAN,

    -- SCD Type 2: effective dating
    effective_start_date      DATE DEFAULT CURRENT_DATE,
    effective_end_date        DATE,                    -- NULL = current/active row

    created_date              TIMESTAMPTZ DEFAULT now(),
    updated_date              TIMESTAMPTZ DEFAULT now()
);

-- Only one active (non-termed) row per patient
CREATE UNIQUE INDEX IF NOT EXISTS uix_ehr_patients_active
ON {PATIENTS_TABLE} (ehr_name, patient_id, entity, sub_entity)
WHERE effective_end_date IS NULL;
"""


def ensure_patients_table(cur):
    cur.execute(CREATE_PATIENTS_TABLE_SQL)
    # Idempotent column adds for existing deployments
    cur.execute(f"""
        ALTER TABLE {PATIENTS_TABLE}
        ADD COLUMN IF NOT EXISTS post_op BOOLEAN,
        ADD COLUMN IF NOT EXISTS behavioral_postop BOOLEAN
    """)
    cur.connection.commit()


# =========================================================
# SMALL HELPERS
# =========================================================

def _normalize_text(text):
    return re.sub(r"[^A-Za-z0-9]", "", text).lower()


# =========================================================
# UI HELPERS
# =========================================================

def _wait_for_grid_content_stable(page, checks=3, interval_ms=250, max_polls=20):
    """
    "Some row exists" can be true from the PREVIOUS filter/search's rows while
    the new one's request is still in flight — a stale-DOM race that makes
    row lookups (or a full-grid scrape) read data that's about to be
    replaced. Poll each row's data-id (MUI DataGrid's own row key) until the
    set stops changing across a few consecutive reads, so callers only
    proceed once the grid has actually caught up to the latest change.
    """
    prev = None
    stable = 0
    for _ in range(max_polls):
        try:
            ids = page.evaluate(
                "() => Array.from(document.querySelectorAll('.MuiDataGrid-row'))"
                ".map(r => r.getAttribute('data-id'))"
            )
        except Exception:
            ids = None
        if ids is not None and ids == prev:
            stable += 1
            if stable >= checks:
                return
        else:
            stable = 0
        prev = ids
        page.wait_for_timeout(interval_ms)


def wait_for_grid_settled(page, timeout_ms=60_000):
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
    page.wait_for_timeout(300)
    _wait_for_grid_content_stable(page)


# =========================================================
# SCRAPE ALL PATIENTS GRID
# =========================================================

def scrape_virtual_grid_patients(page, max_scrolls=500):
    """
    Scroll through the All Patients MuiDataGrid and collect every row
    using a single JS evaluate per scroll step (much faster than
    individual Playwright locator calls).

    The MuiDataGrid virtualizes COLUMNS as well as rows — insurance
    name columns at the far right aren't in the DOM until the grid
    is scrolled horizontally.  So we do two passes:
      Pass 1 (scroll left):  demographics + patient_id  (keyed by row data-id)
      Pass 2 (scroll right): insurance company names     (keyed by row data-id)

    We use the row's data-id attribute as a stable join key — PATIENT_ID
    is virtualized out of the DOM when scrolled right.

    Returns dict  { patient_id: { field: value, ... } }
    """

    JS_EXTRACT_LEFT = """
    () => {
        const rows = document.querySelectorAll('.MuiDataGrid-row');
        const results = [];
        for (const row of rows) {
            const rowId = row.getAttribute('data-id');
            if (!rowId) continue;
            const cell = (field) => {
                const el = row.querySelector(`div[data-field='${field}']`);
                return el ? el.innerText.trim() : null;
            };
            const pid = cell('PATIENT_ID');
            if (!pid) continue;
            results.push({
                _row_id: rowId,
                patient_id: pid,
                status: cell('STATUS'),
                patient_name: cell('PATIENT_NAME'),
                dob: cell('DATE_OF_BIRTH'),
                sex: cell('SEX'),
                marital_status: cell('MARITAL_STATUS'),
                email: cell('EMAIL'),
                home_phone: cell('HOME_PHONE'),
                mobile_phone: cell('MOBILE_PHONE'),
                address_line_1: cell('ADDRESS_LINE_1'),
                city: cell('CITY'),
                state: cell('STATE'),
                zip_code: cell('ZIP_CODE'),
            });
        }
        return results;
    }
    """

    JS_EXTRACT_RIGHT = """
    () => {
        const rows = document.querySelectorAll('.MuiDataGrid-row');
        const results = [];
        for (const row of rows) {
            const rowId = row.getAttribute('data-id');
            if (!rowId) continue;
            const cell = (field) => {
                const el = row.querySelector(`div[data-field='${field}']`);
                return el ? el.innerText.trim() : null;
            };
            results.push({
                _row_id: rowId,
                primary_insurance_name: cell('PRIMARY_INSURANCE_POLICY_COMPANY_NAME'),
                secondary_insurance_name: cell('SECONDARY_INSURANCE_POLICY_COMPANY_NAME'),
            });
        }
        return results;
    }
    """

    def _vertical_scroll_pass(js_code, key_field="_row_id"):
        """Scroll vertically through the grid, collecting data keyed by key_field."""
        collected = {}
        _stable = 0
        _last = 0

        # Reset vertical scroll to top
        page.evaluate("""
            () => {
                const g = document.querySelector('.MuiDataGrid-virtualScroller');
                if (g) g.scrollTop = 0;
            }
        """)
        page.wait_for_timeout(300)

        for _ in range(max_scrolls):
            batch = page.evaluate(js_code)
            for rec in batch:
                k = rec.get(key_field)
                if k:
                    collected[k] = rec

            if len(collected) == _last:
                _stable += 1
            else:
                _stable = 0
            if _stable >= 5:
                break
            _last = len(collected)

            page.evaluate("""
                () => {
                    const g = document.querySelector('.MuiDataGrid-virtualScroller');
                    if (g) g.scrollTop += g.clientHeight;
                }
            """)
            page.wait_for_timeout(200)

        return collected

    # ── Pass 1: demographics (grid scrolled left) — keyed by data-id ──
    page.evaluate("""
        () => {
            const g = document.querySelector('.MuiDataGrid-virtualScroller');
            if (g) g.scrollLeft = 0;
        }
    """)
    page.wait_for_timeout(300)

    left_data = _vertical_scroll_pass(JS_EXTRACT_LEFT)
    print(f"[GRID] Pass 1 (demographics): {len(left_data)} patients")

    # ── Pass 2: insurance names (grid scrolled right) — keyed by data-id ──
    page.evaluate("""
        () => {
            const g = document.querySelector('.MuiDataGrid-virtualScroller');
            if (g) g.scrollLeft = g.scrollWidth;
        }
    """)
    page.wait_for_timeout(300)

    right_data = _vertical_scroll_pass(JS_EXTRACT_RIGHT)
    print(f"[GRID] Pass 2 (insurance names): {len(right_data)} rows")

    # ── Merge by data-id, re-key by patient_id ──
    seen = {}
    for row_id, rec in left_data.items():
        pid = rec.get("patient_id")
        if not pid:
            continue

        # Merge insurance names from Pass 2
        ins = right_data.get(row_id, {})
        rec["primary_insurance_name"] = ins.get("primary_insurance_name")
        rec["secondary_insurance_name"] = ins.get("secondary_insurance_name")

        # Clean up internal key
        rec.pop("_row_id", None)
        seen[pid] = rec

    # ── Reset scroll to left for subsequent operations ──
    page.evaluate("""
        () => {
            const g = document.querySelector('.MuiDataGrid-virtualScroller');
            if (g) { g.scrollLeft = 0; g.scrollTop = 0; }
        }
    """)
    page.wait_for_timeout(300)

    return seen


def load_existing_patients(cur, ehr_name, entity, sub_entity, practice_name):
    """
    Load all active patients from DB into a dict for fast diffing.
    Returns { patient_id: { primary_insurance_name, secondary_insurance_name } }
    """
    cur.execute(
        f"""
        SELECT patient_id, primary_insurance_name, secondary_insurance_name
        FROM {PATIENTS_TABLE}
        WHERE ehr_name = %s AND entity = %s AND sub_entity = %s
          AND practice = %s
          AND effective_end_date IS NULL
        """,
        (ehr_name, entity, sub_entity, practice_name),
    )
    return {
        row[0]: {"primary_insurance_name": row[1], "secondary_insurance_name": row[2]}
        for row in cur.fetchall()
    }


def diff_and_upsert(cur, scraped, existing, ehr_name, entity, sub_entity, practice_name):
    """
    Compare scraped grid data vs DB.  Only write to DB for:
      1. New patients (not in DB)
      2. Changed insurance names

    Returns (new_count, updated_count)
    """
    new_count = 0
    updated_count = 0

    for pid, rec in scraped.items():
        # Add common fields
        rec["ehr_name"] = ehr_name
        rec["entity"] = entity
        rec["sub_entity"] = sub_entity
        rec["practice"] = practice_name

        if pid not in existing:
            # New patient → insert
            cur.execute(
                f"""
                INSERT INTO {PATIENTS_TABLE} (
                    ehr_name, patient_id, entity, sub_entity, practice,
                    patient_name, dob, sex, marital_status,
                    email, home_phone, mobile_phone,
                    address_line_1, city, state, zip_code, status,
                    primary_insurance_name, secondary_insurance_name,
                    effective_start_date, effective_end_date,
                    updated_date
                ) VALUES (
                    %(ehr_name)s, %(patient_id)s, %(entity)s, %(sub_entity)s, %(practice)s,
                    %(patient_name)s, %(dob)s, %(sex)s, %(marital_status)s,
                    %(email)s, %(home_phone)s, %(mobile_phone)s,
                    %(address_line_1)s, %(city)s, %(state)s, %(zip_code)s, %(status)s,
                    %(primary_insurance_name)s, %(secondary_insurance_name)s,
                    CURRENT_DATE, NULL, now()
                )
                ON CONFLICT (ehr_name, patient_id, entity, sub_entity)
                    WHERE effective_end_date IS NULL
                DO NOTHING
                """,
                rec,
            )
            new_count += 1
        else:
            # Existing patient → only update if insurance name changed
            db = existing[pid]
            pri_changed = (rec.get("primary_insurance_name") or "") != (db.get("primary_insurance_name") or "")
            sec_changed = (rec.get("secondary_insurance_name") or "") != (db.get("secondary_insurance_name") or "")

            if pri_changed or sec_changed:
                cur.execute(
                    f"""
                    UPDATE {PATIENTS_TABLE}
                    SET primary_insurance_name  = COALESCE(%(primary_insurance_name)s, primary_insurance_name),
                        secondary_insurance_name = COALESCE(%(secondary_insurance_name)s, secondary_insurance_name),
                        updated_date = now()
                    WHERE ehr_name = %(ehr_name)s AND patient_id = %(patient_id)s
                      AND entity = %(entity)s AND sub_entity = %(sub_entity)s
                      AND effective_end_date IS NULL
                    """,
                    rec,
                )
                updated_count += 1

    return new_count, updated_count


def term_and_create_new(cur, ehr_name, patient_id, entity, sub_entity,
                        new_primary_id, new_secondary_id,
                        new_primary_plan_name=None, new_secondary_plan_name=None):
    """
    SCD Type 2: When insurance ID changes:
      1. Term the existing active row (set effective_end_date, rename patient_id)
      2. Create a new active row with the updated insurance IDs

    The termed row's patient_id gets a suffix like '-1', '-2' so it
    never conflicts with Tebra's real patient IDs.
    """
    # Fetch the current active row
    cur.execute(
        f"""
        SELECT id, patient_id, primary_insurance_id, secondary_insurance_id,
               patient_name, dob, sex, marital_status, email, home_phone, mobile_phone,
               address_line_1, city, state, zip_code, status, practice,
               primary_insurance_name, secondary_insurance_name,
               primary_plan_name, secondary_plan_name
        FROM {PATIENTS_TABLE}
        WHERE ehr_name = %s AND patient_id = %s AND entity = %s AND sub_entity = %s
          AND effective_end_date IS NULL
        """,
        (ehr_name, patient_id, entity, sub_entity),
    )
    old_row = cur.fetchone()
    if not old_row:
        return False

    old_id = old_row[0]
    old_primary_id = old_row[2]
    old_secondary_id = old_row[3]

    # Check if anything actually changed
    pri_changed = (new_primary_id and old_primary_id and new_primary_id != old_primary_id)
    sec_changed = (new_secondary_id and old_secondary_id and new_secondary_id != old_secondary_id)

    if not pri_changed and not sec_changed:
        return False  # No change — nothing to term

    # Find the next suffix for the termed row
    cur.execute(
        f"""
        SELECT COUNT(*) FROM {PATIENTS_TABLE}
        WHERE ehr_name = %s AND entity = %s AND sub_entity = %s
          AND patient_id LIKE %s
          AND effective_end_date IS NOT NULL
        """,
        (ehr_name, entity, sub_entity, f"{patient_id}-%"),
    )
    suffix_count = cur.fetchone()[0] + 1
    termed_patient_id = f"{patient_id}-{suffix_count}"

    print(f"[SCD2] Patient {patient_id}: insurance ID changed, terming as {termed_patient_id}")
    if pri_changed:
        print(f"  Primary: {old_primary_id} → {new_primary_id}")
    if sec_changed:
        print(f"  Secondary: {old_secondary_id} → {new_secondary_id}")

    # 1. Term the old row
    cur.execute(
        f"""
        UPDATE {PATIENTS_TABLE}
        SET patient_id = %s,
            effective_end_date = CURRENT_DATE,
            updated_date = now()
        WHERE id = %s
        """,
        (termed_patient_id, old_id),
    )

    # 2. Create new active row with all the same demographics + new insurance IDs
    cur.execute(
        f"""
        INSERT INTO {PATIENTS_TABLE} (
            ehr_name, patient_id, entity, sub_entity, practice,
            patient_name, dob, sex, marital_status, email, home_phone, mobile_phone,
            address_line_1, city, state, zip_code, status,
            primary_insurance_name, secondary_insurance_name,
            primary_insurance_id, secondary_insurance_id,
            primary_plan_name, secondary_plan_name,
            insurance_scraped,
            effective_start_date, effective_end_date,
            created_date, updated_date
        ) VALUES (
            %s, %s, %s, %s, %s,
            %s, %s, %s, %s, %s, %s, %s,
            %s, %s, %s, %s, %s,
            %s, %s,
            %s, %s,
            %s, %s,
            TRUE,
            CURRENT_DATE, NULL,
            now(), now()
        )
        """,
        (
            ehr_name, patient_id, entity, sub_entity, old_row[16],
            old_row[4], old_row[5], old_row[6], old_row[7],
            old_row[8], old_row[9], old_row[10],
            old_row[11], old_row[12], old_row[13], old_row[14],
            old_row[15],
            old_row[17], old_row[18],
            new_primary_id or old_primary_id,
            new_secondary_id or old_secondary_id,
            new_primary_plan_name or old_row[19],
            new_secondary_plan_name or old_row[20],
        ),
    )

    return True


# =========================================================
# NAVIGATE: SEARCH → CLICK PATIENT
# =========================================================

def search_patient_in_grid(page, patient_id):
    """
    Use the grid's quick filter search box to find a patient by ID.
    Much faster than scrolling through a virtual grid.
    """
    # Ensure grid is loaded before looking for search button
    wait_for_grid_settled(page)

    search_btn = page.locator("button[aria-label='Search table data']")
    search_btn.wait_for(state="visible", timeout=15_000)
    search_btn.click()
    page.wait_for_timeout(300)

    search_input = page.locator(
        "#quick-filter input[type='text'], "
        "#quick-filter input[type='search'], "
        ".MuiDataGrid-toolbarQuickFilter input"
    )
    if search_input.count() == 0:
        search_input = page.locator("input.MuiInputBase-input").first

    search_input.click()
    search_input.press("Control+A")
    search_input.fill(str(patient_id))
    page.wait_for_timeout(500)

    wait_for_grid_settled(page)


def clear_grid_search(page):
    """Clear the search filter to show all patients again."""
    try:
        close_btn = page.locator("button[aria-label='Search table data']")
        if close_btn.count():
            close_btn.click()
            page.wait_for_timeout(200)

        search_input = page.locator(
            "#quick-filter input[type='text'], "
            "#quick-filter input[type='search'], "
            ".MuiDataGrid-toolbarQuickFilter input"
        )
        if search_input.count() and search_input.is_visible():
            search_input.click()
            search_input.press("Control+A")
            search_input.press("Backspace")
            page.wait_for_timeout(200)
            page.keyboard.press("Escape")
            page.wait_for_timeout(200)
    except Exception:
        pass


def click_patient_link(page):
    """
    Click the first visible patient link in the grid (PATIENT_ID or PATIENT_NAME).
    Returns (target_page, opened_new_tab).
    """
    id_link = page.locator("div[data-field='PATIENT_ID'] button.MuiLink-button").first
    if id_link.count() == 0:
        id_link = page.locator("div[data-field='PATIENT_NAME'] button.MuiLink-button").first

    if id_link.count() == 0:
        raise RuntimeError("No clickable patient link found in grid")

    id_link.scroll_into_view_if_needed()
    page.wait_for_timeout(100)

    # Try new tab
    try:
        with page.context.expect_page(timeout=10_000) as p:
            id_link.click(force=True)
        fs = p.value
        fs.wait_for_load_state("domcontentloaded")
        return fs, True
    except Exception:
        pass

    # Same tab
    try:
        id_link.click(force=True)
        page.wait_for_load_state("domcontentloaded")
        page.wait_for_timeout(500)
        return page, False
    except Exception as e:
        raise RuntimeError(f"Cannot click patient link: {e}")


# =========================================================
# NAVIGATE: ACCOUNT → INSURANCE → EDIT → SCRAPE POLICIES
# =========================================================

def navigate_to_account(fs_page):
    """From facesheet/chart, click the Account nav link."""
    account_link = fs_page.locator("a[href*='patient-account-history']")
    if account_link.count() == 0:
        account_link = fs_page.locator("a:has-text('Account')").first

    account_link.wait_for(state="visible", timeout=5_000)
    account_link.click()
    fs_page.wait_for_load_state("domcontentloaded")
    fs_page.wait_for_timeout(200)


def navigate_to_insurance_tab(fs_page):
    """
    From Account page, click the Insurance tab.
    Returns False if patient has no insurance on file (skip immediately).
    """
    ins_tab = fs_page.locator("#insuranceTab a, a[ui-sref*='insurance']")
    ins_tab.wait_for(state="visible", timeout=5_000)
    ins_tab.click()
    fs_page.wait_for_load_state("domcontentloaded")

    # Wait for EITHER insurance data OR the "no insurance" message
    try:
        fs_page.wait_for_selector(
            "div.list-content, div[ng-repeat*='insurance'], "
            "div.empty:has-text('Insurance has not been added')",
            timeout=5_000,
        )
    except Exception:
        pass

    fs_page.wait_for_timeout(300)

    # Now check: if "no insurance" message is visible AND no insurance cases exist
    no_ins = fs_page.locator("div.empty:has-text('Insurance has not been added')")
    cases = fs_page.locator("div.list-content, div[ng-repeat*='insurance']")

    if no_ins.count() > 0 and cases.count() == 0:
        return False

    return True


def click_primary_insurance_edit(fs_page):
    """
    On the Insurance tab, find the insurance case marked 'Primary'
    and click its Edit button.  Fast-fails if no insurance cases exist.
    """
    # Wait for insurance list OR "no insurance" state — whichever comes first
    try:
        fs_page.wait_for_selector(
            ".list-content, div[ng-repeat*='insurance'], "
            ".add-plan-button, button:has-text('Add Another Policy')",
            timeout=3_000,
        )
    except Exception:
        # Nothing loaded in 3s — no insurance on file
        print("[INS-EDIT] No insurance data loaded (timeout)")
        return False

    fs_page.wait_for_timeout(150)

    # Quick check: if only the "add plan" button is visible, no cases exist
    cases = fs_page.locator("div.list-content, div[ng-repeat*='insurance']")
    if cases.count() == 0:
        print("[INS-EDIT] No insurance cases on file")
        return False

    for i in range(cases.count()):
        case_el = cases.nth(i)
        case_text = case_el.inner_text()

        if "Primary" not in case_text:
            continue

        edit_btn = case_el.locator(
            "button:has-text('Edit'), "
            "a:has-text('Edit'), "
            "[data-testid='primary-ins-edit-btn']"
        ).first
        if edit_btn.count() == 0:
            continue

        edit_btn.click()
        fs_page.wait_for_load_state("domcontentloaded")

        # Wait for policy cards to appear
        fs_page.wait_for_selector(
            "h2.plan-number-header",
            timeout=5_000,
        )
        fs_page.wait_for_timeout(150)
        return True

    print("[INS-EDIT] No Primary insurance case found")
    return False


def scrape_policy_numbers(fs_page):
    """
    On the 'Edit Insurance Case' page, scrape Policy #1 and Policy #2.
    Each policy card has:
      - h2.plan-number-header  →  "Policy # 1: Cigna"
      - label "Plan Name"      →  next .bold-text = plan name
      - label "Policy Number"  →  next .bold-text = policy number

    Returns dict:
        {
            "primary_plan_name": "Cigna",
            "primary_policy_number": "u1609430801",
            "secondary_plan_name": "Medicare B",
            "secondary_policy_number": "3vv3w68wm84",
        }
    """
    result = {
        "primary_plan_name": None,
        "primary_policy_number": None,
        "secondary_plan_name": None,
        "secondary_policy_number": None,
    }

    # Each policy is inside ng-repeat="insurance in listOfInsuranceSaved"
    policy_cards = fs_page.locator(
        "div[ng-repeat*='listOfInsuranceSaved']"
    )
    card_count = policy_cards.count()
    print(f"[INS-SCRAPE] Found {card_count} policy card(s)")

    for i in range(card_count):
        card = policy_cards.nth(i)

        # Read the header: "Policy # 1: Cigna" or "Policy # 2: Medicare B"
        header = card.locator("h2.plan-number-header")
        header_text = header.inner_text().strip() if header.count() else ""

        # Determine if this is Policy #1 (primary) or #2 (secondary)
        is_primary = "# 1" in header_text or "#1" in header_text
        is_secondary = "# 2" in header_text or "#2" in header_text

        if not is_primary and not is_secondary:
            continue

        prefix = "primary" if is_primary else "secondary"

        # Scrape Plan Name
        plan_name_label = card.locator("label.light-bold:has-text('Plan Name')")
        if plan_name_label.count():
            # The value is the next .bold-text sibling in the same row
            plan_row = plan_name_label.locator("xpath=ancestor::div[contains(@class,'input-group')]")
            plan_val = plan_row.locator("label.bold-text")
            if plan_val.count():
                result[f"{prefix}_plan_name"] = plan_val.first.inner_text().strip() or None

        # Scrape Policy Number
        policy_label = card.locator("label.light-bold:has-text('Policy Number')")
        if policy_label.count():
            policy_row = policy_label.locator("xpath=ancestor::div[contains(@class,'input-group')]")
            policy_val = policy_row.locator("label.bold-text")
            if policy_val.count():
                raw = policy_val.first.inner_text().strip()
                result[f"{prefix}_policy_number"] = raw if raw else None

    print(f"[INS-SCRAPE] Primary: {result['primary_plan_name']} / {result['primary_policy_number']}")
    print(f"[INS-SCRAPE] Secondary: {result['secondary_plan_name']} / {result['secondary_policy_number']}")

    return result


# =========================================================
# PROCESS SINGLE PATIENT'S INSURANCE
# =========================================================

def _scrape_insurance_from_row(
    page: Page,
    row,
    patient_id: str,
    cur,
    conn,
    ehr_name: str,
    entity: str,
    sub_entity: str,
):
    """
    Given a visible grid row, click the patient link (new tab),
    navigate Account → Insurance → Edit, scrape policies, close tab.
    Page stays on the grid throughout.
    """
    fs_page = None
    opened_new_tab = False

    try:
        # ── Click patient name → opens facesheet (prefer new tab) ──
        link_btn = row.locator("button.MuiLink-button").first
        if link_btn.count() == 0:
            link_btn = row.locator("div[data-field='PATIENT_NAME']").first

        link_btn.scroll_into_view_if_needed()

        try:
            with page.context.expect_page(timeout=5_000) as p:
                link_btn.click(force=True)
            fs_page = p.value
            fs_page.wait_for_load_state("domcontentloaded")
            opened_new_tab = True
        except Exception:
            # Same-tab fallback
            link_btn.click(force=True)
            page.wait_for_load_state("domcontentloaded")
            page.wait_for_timeout(300)
            fs_page = page
            opened_new_tab = False

        target = fs_page

        # ── Account → Insurance → Edit ──
        navigate_to_account(target)
        has_insurance = navigate_to_insurance_tab(target)

        if not has_insurance:
            # No insurance on file — mark as scraped, move on
            cur.execute(
                f"""
                UPDATE {PATIENTS_TABLE}
                SET insurance_scraped = TRUE,
                    insurance_scrape_error = 'No insurance on file',
                    updated_date = now()
                WHERE ehr_name = %s AND patient_id = %s AND entity = %s AND sub_entity = %s
                  AND effective_end_date IS NULL
                """,
                (ehr_name, patient_id, entity, sub_entity),
            )
            conn.commit()
            if opened_new_tab:
                fs_page.close()
            return True

        edit_opened = click_primary_insurance_edit(target)

        if not edit_opened:
            # No insurance on file — mark as scraped (nothing to extract)
            cur.execute(
                f"""
                UPDATE {PATIENTS_TABLE}
                SET insurance_scraped = TRUE,
                    insurance_scrape_error = 'No primary insurance case',
                    updated_date = now()
                WHERE ehr_name = %s AND patient_id = %s AND entity = %s AND sub_entity = %s
                  AND effective_end_date IS NULL
                """,
                (ehr_name, patient_id, entity, sub_entity),
            )
            conn.commit()
            if opened_new_tab:
                fs_page.close()
            return True

        # ── Scrape policy numbers ──
        policies = scrape_policy_numbers(target)
        new_pri = policies["primary_policy_number"]
        new_sec = policies["secondary_policy_number"]

        # ── Update DB ──
        termed = term_and_create_new(
            cur, ehr_name, patient_id, entity, sub_entity,
            new_pri, new_sec,
            policies["primary_plan_name"], policies["secondary_plan_name"],
        )

        if not termed:
            cur.execute(
                f"""
                UPDATE {PATIENTS_TABLE}
                SET primary_insurance_id     = %s,
                    secondary_insurance_id   = %s,
                    primary_plan_name        = %s,
                    secondary_plan_name      = %s,
                    insurance_scraped        = TRUE,
                    insurance_scrape_error   = NULL,
                    updated_date             = now()
                WHERE ehr_name = %s AND patient_id = %s AND entity = %s AND sub_entity = %s
                  AND effective_end_date IS NULL
                  AND insurance_scraped = FALSE
                """,
                (new_pri, new_sec,
                 policies["primary_plan_name"], policies["secondary_plan_name"],
                 ehr_name, patient_id, entity, sub_entity),
            )

        conn.commit()

        # ── Close tab → back to grid ──
        if opened_new_tab:
            fs_page.close()
        else:
            page.goto("https://app.kareo.com/v2/#/worklist/patients")
            page.wait_for_load_state("domcontentloaded")
            wait_for_grid_settled(page)

        return True

    except Exception as e:
        print(f"[PATIENT-INS ERROR] {patient_id}: {e}")
        cur.execute(
            f"""
            UPDATE {PATIENTS_TABLE}
            SET insurance_scrape_error = %s, updated_date = now()
            WHERE ehr_name = %s AND patient_id = %s AND entity = %s AND sub_entity = %s
              AND effective_end_date IS NULL
            """,
            (str(e)[:500], ehr_name, patient_id, entity, sub_entity),
        )
        conn.commit()

        # Make sure we're back on the grid
        try:
            if opened_new_tab and fs_page:
                fs_page.close()
            elif not opened_new_tab:
                page.goto("https://app.kareo.com/v2/#/worklist/patients")
                page.wait_for_load_state("domcontentloaded")
                wait_for_grid_settled(page)
        except Exception:
            pass

        return False


def scroll_and_scrape_insurance(
    page: Page,
    context: BrowserContext,
    cur,
    conn,
    needed_ids: set,
    ehr_name: str,
    entity: str,
    sub_entity: str,
    practice_name: str,
    max_scrolls: int = 500,
):
    """
    Single scroll pass through the Patients grid. For each visible row
    whose patient_id is in needed_ids, click → scrape insurance → close tab.

    Much faster than searching one-by-one — no search, no back navigation.
    """
    remaining = set(needed_ids)
    processed = 0
    stable = 0
    last_remaining = len(remaining)

    # Reset scroll to top
    page.evaluate("""
        () => {
            const g = document.querySelector('.MuiDataGrid-virtualScroller');
            if (g) { g.scrollTop = 0; g.scrollLeft = 0; }
        }
    """)
    page.wait_for_timeout(300)

    for scroll_idx in range(max_scrolls):
        if not remaining:
            break

        # Get all visible rows and their patient IDs
        rows = page.locator(".MuiDataGrid-row")
        row_count = rows.count()

        for i in range(row_count):
            if not remaining:
                break

            row = rows.nth(i)
            pid_el = row.locator("div[data-field='PATIENT_ID']")
            if pid_el.count() == 0:
                continue

            pid = pid_el.inner_text().strip()
            if pid not in remaining:
                continue

            # Found one — process it
            processed += 1
            print(f"[{practice_name}] Insurance scrape {processed}: patient_id={pid}")

            success = _scrape_insurance_from_row(
                page, row, pid, cur, conn,
                ehr_name, entity, sub_entity,
            )

            remaining.discard(pid)

            # After closing tab, grid might have re-rendered — break inner
            # loop and re-read rows on next scroll iteration
            break

        else:
            # Inner loop completed without finding any needed patient — scroll down
            if len(remaining) == last_remaining:
                stable += 1
            else:
                stable = 0
            last_remaining = len(remaining)

            if stable >= 5:
                break

            page.evaluate("""
                () => {
                    const g = document.querySelector('.MuiDataGrid-virtualScroller');
                    if (g) g.scrollTop += g.clientHeight;
                }
            """)
            page.wait_for_timeout(200)
            continue

        # If we processed a row (broke out of inner loop), DON'T scroll —
        # re-read the current viewport since grid position is preserved
        continue

    if remaining:
        print(f"[{practice_name}] {len(remaining)} patients not found in grid scroll")

    return processed


# =========================================================
# PER-PRACTICE SCRAPE (called after practice selection)
# =========================================================

def scrape_practice_patients(
    page: Page,
    context: BrowserContext,
    practice_name: str,
    entity: str,
    sub_entity: str,
    ehr_name: str = EHR_NAME,
    test_limit: int = None,
):
    """
    For one practice (already selected in Tebra UI):
      A.  Navigate to Analytics → Patients
            B.  Scrape All Patients grid → upsert into ehr.ehr_patients
      C.  For patients with insurance_scraped=FALSE →
          Account → Insurance → Edit → scrape policy numbers

    test_limit: if set, limit Phase C to this many patients.
    """
    conn = get_ehr_connection()
    cur = conn.cursor()

    ensure_patients_table(cur)

    # ── A: Navigate to Patients via direct URL ──
    print(f"[{practice_name}] Navigating to Patients")
    page.goto("https://app.kareo.com/v2/#/worklist/patients")
    page.wait_for_load_state("domcontentloaded")
    page.wait_for_timeout(300)
    wait_for_grid_settled(page)

    # ── B: Scrape the grid (fast JS extraction) ──
    scraped = scrape_virtual_grid_patients(page)
    print(f"[{practice_name}] Scraped {len(scraped)} patients from grid")

    # Load existing patients from DB for fast diff
    existing = load_existing_patients(cur, ehr_name, entity, sub_entity, practice_name)
    print(f"[{practice_name}] {len(existing)} patients already in DB")

    # Only insert new + update changed insurance names
    new_count, updated_count = diff_and_upsert(
        cur, scraped, existing, ehr_name, entity, sub_entity, practice_name
    )
    conn.commit()
    skipped = len(scraped) - new_count - updated_count
    print(f"[{practice_name}] New={new_count}, Updated={updated_count}, Skipped={skipped}")

    # ── C: Process insurance for un-scraped patients ──
    limit_clause = f"LIMIT {test_limit}" if test_limit else ""
    cur.execute(
        f"""
        SELECT patient_id
        FROM {PATIENTS_TABLE}
        WHERE ehr_name = %s
          AND entity = %s
          AND sub_entity = %s
          AND practice = %s
          AND insurance_scraped = FALSE
          AND effective_end_date IS NULL
        ORDER BY patient_name
        {limit_clause}
        """,
        (ehr_name, entity, sub_entity, practice_name),
    )
    unscraped = set(r[0] for r in cur.fetchall())
    print(f"[{practice_name}] {len(unscraped)} patients to process" +
          (f" (limited to {test_limit})" if test_limit else ""))

    if unscraped:
        # Navigate back to grid (scroll left, top) before Phase C
        page.goto("https://app.kareo.com/v2/#/worklist/patients")
        page.wait_for_load_state("domcontentloaded")
        page.wait_for_timeout(500)
        wait_for_grid_settled(page)

        scroll_and_scrape_insurance(
            page, context, cur, conn,
            needed_ids=unscraped,
            ehr_name=ehr_name,
            entity=entity,
            sub_entity=sub_entity,
            practice_name=practice_name,
        )

    cur.close()
    conn.close()
    print(f"[{practice_name}] Patient insurance scrape complete")


# =========================================================
# PRACTICES CONFIG
# All practices share entity=270681372, sub_entity=270681372001.
# PRACTICES dict controls per-practice test_limit (None = scrape all).
# =========================================================

ENTITY = "270681372"
SUB_ENTITY = "270681372001"

PRACTICES = {
    "PrePost+ Tennessee": {"test_limit": None},
    "PrePostPlus Atlanta (Buckhead)": {"test_limit": None},
    "PrePostPlus Germantown": {"test_limit": None},
    "PrePostPlus Nashville (Midtown)": {"test_limit": None},
    "The PreOp Center": {"test_limit": None},
}


def get_practice_config(ui_name: str) -> dict:
    """
    Look up test_limit for a practice name.
    Returns { practice_name, entity, sub_entity, test_limit }.
    """
    for practice_name, cfg in PRACTICES.items():
        if practice_name.lower() in ui_name.lower() or ui_name.lower() in practice_name.lower():
            return {
                "practice_name": practice_name,
                "entity": ENTITY,
                "sub_entity": SUB_ENTITY,
                "test_limit": cfg.get("test_limit"),
            }
    # Fallback: practice not in config — no test_limit
    return {
        "practice_name": ui_name.strip(),
        "entity": ENTITY,
        "sub_entity": SUB_ENTITY,
        "test_limit": None,
    }


def is_practice_configured(ui_name: str) -> bool:
    """Check if a practice name matches a PRACTICES config entry."""
    for practice_name in PRACTICES:
        if practice_name.lower() in ui_name.lower() or ui_name.lower() in practice_name.lower():
            return True
    return False


# =========================================================
# MAIN ORCHESTRATOR — login + practice loop
# =========================================================

def run_patient_insurance_rpa(
    entity: str = ENTITY,
    sub_entity: str = SUB_ENTITY,
    ehr_name: str = EHR_NAME,
):
    """
    Main entry point.

    Scrapes ALL practices available on the practice-select screen.
    Per-practice test_limit in PRACTICES config controls how many
    patients get insurance-scraped (omit for all).

    Flow:
      1. Launch browser, login to Tebra
      2. On practice-select screen, discover all available practices
      3. For each practice:
         a. Click practice, handle OTP
                 b. Analytics → Patients → scrape grid → upsert ehr.ehr_patients
         c. For un-scraped patients → Account → Insurance → Edit → scrape policy #s
                 d. Emit a run event (writes disabled in this package)
         e. Navigate back to practice-select for next practice
      4. Close browser
    """
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=PLAYWRIGHT_HEADLESS,
            args=PLAYWRIGHT_LAUNCH_ARGS,
        )
        context = browser.new_context(
            no_viewport=(PLAYWRIGHT_VIEWPORT is None), viewport=PLAYWRIGHT_VIEWPORT,
        )
        page = context.new_page()

        # ── Discover practices — login once to get the list ──
        page.goto(LOGIN_URL)
        page.fill("#userName", EMAIL)
        page.fill("#password", PASSWORD)
        page.click("#sign-in")

        page.wait_for_selector("h3:has-text('Practice select')")
        page.wait_for_timeout(2000)  # let all practice tiles render

        practice_elements = page.locator("h6.MuiTypography-subtitle2")
        all_ui_practices = []
        for i in range(practice_elements.count()):
            name = practice_elements.nth(i).inner_text().strip()
            if name:
                all_ui_practices.append(name)
                print(f"[DISCOVER]   {i}: '{name}'")

        print(f"[DISCOVER] Found {len(all_ui_practices)} practices in Tebra UI")
        browser.close()

        # ── Loop through practices (fresh browser each time) ──
        completed = []

        for ui_practice_name in all_ui_practices:

            cfg = get_practice_config(ui_practice_name)
            practice_name = cfg["practice_name"]
            test_limit = cfg.get("test_limit")

            print(f"\n{'='*60}")
            print(f"[PRACTICE] {ui_practice_name}  (entity={entity}, sub_entity={sub_entity})")
            print(f"{'='*60}")

            # ── Fresh browser for each practice ──
            browser = p.chromium.launch(
                headless=PLAYWRIGHT_HEADLESS,
                args=PLAYWRIGHT_LAUNCH_ARGS,
            )
            context = browser.new_context(
                no_viewport=(PLAYWRIGHT_VIEWPORT is None), viewport=PLAYWRIGHT_VIEWPORT,
            )
            page = context.new_page()

            start_dt = _now_cst()
            error_msg = None

            try:
                page.goto(LOGIN_URL)
                page.fill("#userName", EMAIL)
                page.fill("#password", PASSWORD)
                page.click("#sign-in")

                page.wait_for_selector("h3:has-text('Practice select')", timeout=30_000)

                # ── Select practice ──
                practice_found = False
                for i in range(page.locator("h6.MuiTypography-subtitle2").count()):
                    el = page.locator("h6.MuiTypography-subtitle2").nth(i)
                    if el.inner_text().strip() == ui_practice_name:
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
                    raise RuntimeError(f"Practice '{ui_practice_name}' not found in UI")

                # ── Scrape patients + insurance IDs ──
                scrape_practice_patients(
                    page, context,
                    practice_name, entity, sub_entity,
                    ehr_name=ehr_name,
                    test_limit=test_limit,
                )

            except Exception as e:
                error_msg = str(e)[:500]
                print(f"[ERROR] {practice_name}: {error_msg}")

            finally:
                browser.close()

            # ── Log run ──
            log_run_event(
                script_name="OPS_PATIENT_INSURANCE_RPA",
                process_type=f"RCM - {practice_name}",
                status="Error" if error_msg else "Success",
                error=error_msg or None,
                company_id=entity,
                started_at=start_dt,
                ended_at=_now_cst(),
            )

            completed.append(ui_practice_name)

        print(f"\n[DONE] {len(completed)}/{len(all_ui_practices)} practices processed: {completed}")


# =========================================================
# __main__
# =========================================================

if __name__ == "__main__":
    run_patient_insurance_rpa()