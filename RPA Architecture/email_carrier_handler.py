"""
email_carrier_handler.py
────────────────────────
Handles email-based RPA file pickup for insurance carriers.

Flow A: Secure-link portals (Convey, Proofpoint, etc.) via Playwright
Flow B: Direct Graph API attachment download

TEST_MODE:
  - Queries the same DB table but filters to TEST_SCRIPT_NAMES only
  - Email search matches any recent unread email (no date filter)
  - Skips Azure Blob upload, mark-as-read, and Service Interruption trigger
"""

import os
import re
import time
import zipfile
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeoutError
from datetime import datetime
from email_read import (
    find_matching_email,
    extract_secure_link,
    download_attachment,
    download_all_attachments,
    mark_as_read,
)
from logger import (
    log_error, log_success, ERROR_CODES,
    init_log_entry, log_final_entry, update_log_extra_fields, setup_logger,
)
from db_connection import connect_to_db
from typing import Optional, List, Tuple


# ──────────────────────────────────────────────────────────────
# TEST MODE CONFIG
# ──────────────────────────────────────────────────────────────
TEST_MODE = True

# Only these script_names are processed when TEST_MODE = True
TEST_SCRIPT_NAMES = [
    "ACU_AMERIHTH_ACA",   # Flow A: secure-link login via Convey
    "ACU_PHYMTL_SUP",     # Flow B: direct attachment
]


# ──────────────────────────────────────────────────────────────
# Playwright browser helpers
# ──────────────────────────────────────────────────────────────
def _launch_browser(download_folder: str, headless: bool = False):
    """Launch Chromium via Playwright. Caller must call _close_browser()."""
    print(f"== [Handler] Launching Playwright browser (headless={headless})...")
    print(f"== [Handler]   Download folder: {download_folder}")
    pw = sync_playwright().start()
    browser = pw.chromium.launch(headless=headless)
    context = browser.new_context(accept_downloads=True)
    page = context.new_page()
    page.context.set_default_timeout(60_000)
    print(f"== [Handler]   ✓ Browser launched successfully.")
    return pw, browser, context, page


def _close_browser(pw, browser):
    """Safely shut down Playwright resources."""
    print("== [Handler] Closing Playwright browser...")
    try:
        browser.close()
    except Exception:
        pass
    try:
        pw.stop()
    except Exception:
        pass
    print("== [Handler]   ✓ Browser closed.")


def _find_visible(page, selectors, timeout=5000):
    """Return the first visible AND enabled locator, or None."""
    for sel in selectors:
        try:
            loc = page.locator(sel).first
            if loc.is_visible(timeout=timeout) and loc.is_enabled(timeout=1_000):
                print(f"== [Handler]   _find_visible: matched '{sel}'")
                return loc
            elif loc.is_visible(timeout=500):
                print(f"== [Handler]   _find_visible: '{sel}' is visible but DISABLED, skipping.")
        except Exception:
            continue
    return None


def _click_first_visible(page, selectors, timeout=5000):
    """Click the first visible element matching any selector. Returns True if clicked."""
    for sel in selectors:
        try:
            loc = page.locator(sel).first
            if loc.is_visible(timeout=timeout):
                loc.click()
                print(f"== [Handler]   _click_first_visible: clicked '{sel}'")
                return True
        except Exception:
            continue
    return False


# ──────────────────────────────────────────────────────────────
# File helpers
# ──────────────────────────────────────────────────────────────
def _locate_final_file(download_folder, matrix_row):
    """Find the extracted file using extracted_file_prefix + extracted_file_extension."""
    prefix = matrix_row.get("extracted_file_prefix", "").strip().lower()
    ext = matrix_row.get("extracted_file_extension", "").strip().lower().lstrip(".")

    print(f"== [Handler] ── _locate_final_file ──")
    print(f"== [Handler]   Folder    : {download_folder}")
    print(f"== [Handler]   Prefix    : '{prefix}' (empty=any)")
    print(f"== [Handler]   Extension : '{ext}' (empty=any)")

    files_in_folder = os.listdir(download_folder)
    print(f"== [Handler]   Files in folder ({len(files_in_folder)}): {files_in_folder}")

    for f in files_in_folder:
        f_lower = f.lower()
        prefix_ok = (not prefix) or (prefix in f_lower)
        ext_ok = (not ext) or f_lower.endswith(f".{ext}")
        if prefix_ok and ext_ok:
            matched_path = os.path.join(download_folder, f)
            print(f"== [Handler]   ✓ Matched: {matched_path}")
            return matched_path

    print(f"== [Handler]   ✗ No file matched.")
    return None


def _rename_file(file_path, rename_base):
    today = datetime.now()
    date_str = today.strftime("%m%d%Y")

    folder = os.path.dirname(file_path)
    ext = os.path.splitext(file_path)[1]

    new_name = f"{rename_base}_{date_str}{ext}"
    new_path = os.path.join(folder, new_name)

    print(f"== [Handler] ── _rename_file ──")
    print(f"== [Handler]   Original : {file_path}")
    print(f"== [Handler]   New name : {new_name}")

    os.rename(file_path, new_path)
    print(f"== [Handler]   ✓ Renamed successfully.")

    return new_path


def _build_blob_path(matrix_row, renamed_file_path):
    # type: (dict, str) -> Tuple[str, str]
    process_name = matrix_row.get("process_name", "").lower()

    if "acu" in process_name:
        base_path = "raw/agent_contract_update/acu_new_process"
    elif "bob" in process_name:
        base_path = "raw/production_report"
    else:
        raise Exception(f"Unknown process_name: {process_name}")

    month_folder = f"{datetime.now().year} {datetime.now().strftime('%m')} {datetime.now().strftime('%b')}"
    filename = os.path.basename(renamed_file_path)
    blob_path = f"{base_path}/{month_folder}/{filename}"

    print(f"== [Handler] ── _build_blob_path ──")
    print(f"== [Handler]   Container : {matrix_row['container_name']}")
    print(f"== [Handler]   Blob path : {blob_path}")

    return matrix_row["container_name"], blob_path


def _check_file_in_folder(folder, prefix, extension):
    """Check if any file matching prefix/extension exists."""
    if not os.path.isdir(folder):
        return False
    prefix_lower = prefix.lower()
    ext_lower = extension.lower().lstrip(".")
    for f in os.listdir(folder):
        f_lower = f.lower()
        prefix_ok = (not prefix_lower) or (prefix_lower in f_lower)
        ext_ok = (not ext_lower) or f_lower.endswith(f".{ext_lower}")
        if prefix_ok and ext_ok:
            return True
    return False


# ──────────────────────────────────────────────────────────────
# Load matrix rows
# ──────────────────────────────────────────────────────────────
def load_email_matrix_rows():
    """
    Returns matrix rows to process.
    In TEST_MODE: queries the DB but filters to TEST_SCRIPT_NAMES only.
    In production: queries all active email pickup rows.
    """
    conn = connect_to_db()
    cursor = conn.cursor()

    if TEST_MODE:
        placeholders = ",".join(["%s"] * len(TEST_SCRIPT_NAMES))
        query = f"""
            SELECT *
            FROM wpo.ops_rpa_matrix
            WHERE pickup_method = 'RPA - Email'
            AND script_name IN ({placeholders})
        """
        print(f"== [TEST MODE] Querying DB for test carriers: {TEST_SCRIPT_NAMES}")
        cursor.execute(query, TEST_SCRIPT_NAMES)
    else:
        cursor.execute("""
            SELECT *
            FROM wpo.ops_rpa_matrix
            WHERE pickup_method = 'RPA - Email'
            AND disabled = 'false'
        """)

    columns = [col[0] for col in cursor.description]
    rows = cursor.fetchall()
    matrix_rows = [dict(zip(columns, row)) for row in rows]

    print(f"== [{'TEST MODE' if TEST_MODE else 'Handler'}] Loaded {len(matrix_rows)} email matrix row(s) from DB.")
    for i, row in enumerate(matrix_rows):
        print(f"==   [{i+1}] {row.get('carrier_name', '?')} / {row.get('process_name', '?')} (log_in={row.get('log_in', '?')})")

    cursor.close()
    conn.close()

    return matrix_rows


# ──────────────────────────────────────────────────────────────
# Secure-link login (Playwright)
# ──────────────────────────────────────────────────────────────
def _attempt_secure_login(page, email_val, password_val, script_name):
    """
    Tries common login form patterns on secure email portals.
    If the email field is disabled (pre-filled), skips straight to password.
    """
    print(f"== [Handler] ── _attempt_secure_login ({script_name}) ──")
    print(f"== [Handler]   Portal email: {email_val}")
    print(f"== [Handler]   Current URL : {page.url}")

    email_selectors = [
        'input[type="email"]',
        'input[name="email"]',
        'input[id="email"]',
        'input[placeholder*="mail" i]',
        'input[placeholder*="Email" i]',
        'input[name="loginUsername"]',
        'input[name="username"]',
        'input[id="username"]',
        'input[type="text"]',
    ]

    password_selectors = [
        'input[type="password"]',
        'input[name="password"]',
        'input[id="password"]',
        'input[placeholder*="assword" i]',
    ]

    submit_selectors = [
        'button[type="submit"]',
        'input[type="submit"]',
        'button:has-text("Log In")',
        'button:has-text("Log in")',
        'button:has-text("Login")',
        'button:has-text("Sign In")',
        'button:has-text("Sign in")',
        'button:has-text("Continue")',
        'button:has-text("Submit")',
        'button:has-text("Go")',
        'button:has-text("Enter")',
        'button:has-text("Retrieve")',
        'button:has-text("Open")',
        'input[type="button"]',
        'a:has-text("Log In")',
        'a:has-text("Sign In")',
        'a:has-text("Continue")',
        'a:has-text("Submit")',
        '#submitBtn',
        '.submit-btn',
        '.login-btn',
        'button.btn-primary',
        'input.btn-primary',
    ]

    # ── Step 1: Fill email (skip if disabled / pre-filled) ──
    email_field = _find_visible(page, email_selectors, timeout=3_000)
    if email_field:
        email_field.click()
        email_field.fill(email_val)
        print("== [Handler]   Email entered.")
    else:
        print("== [Handler]   Email field not found or disabled — skipping (may be pre-filled).")

    # ── Step 2: Fill password (may be on same page or next page) ──
    password_field = _find_visible(page, password_selectors, timeout=3_000)

    if not password_field:
        # Password might appear after submitting email
        print("== [Handler]   Password field not visible yet, submitting email first...")
        email_submitted = _click_first_visible(page, submit_selectors, timeout=5_000)
        if not email_submitted:
            print("== [Handler]   No submit button found for email step — pressing Enter.")
            page.keyboard.press("Enter")
        try:
            page.wait_for_load_state("networkidle", timeout=15_000)
        except PWTimeoutError:
            print("== [Handler]   Warning: networkidle timeout after email submit.")
        password_field = _find_visible(page, password_selectors, timeout=10_000)

    if password_field:
        password_field.click()
        password_field.fill(password_val)
        print("== [Handler]   ✓ Password entered.")
    else:
        print("== [Handler]   ✗ No password field found — cannot authenticate.")
        return

    # ── Step 3: Submit ──
    submitted = _click_first_visible(page, submit_selectors, timeout=5_000)
    if submitted:
        print("== [Handler]   ✓ Login submitted via button click.")
    else:
        print("== [Handler]   No submit button found — pressing Enter as fallback.")
        page.keyboard.press("Enter")

        # Diagnostics: what buttons/links are visible on this page?
        try:
            all_buttons = page.locator("button").all_text_contents()
            all_inputs = page.locator("input[type='submit'], input[type='button']").all()
            input_vals = [inp.get_attribute("value") or inp.get_attribute("type") for inp in all_inputs]
            all_links = page.locator("a").all_text_contents()
            print(f"== [Handler]   [DEBUG] Visible buttons : {all_buttons}")
            print(f"== [Handler]   [DEBUG] Visible inputs  : {input_vals}")
            print(f"== [Handler]   [DEBUG] Visible links   : {[t.strip() for t in all_links if t.strip()]}")
        except Exception as diag_err:
            print(f"== [Handler]   [DEBUG] Could not read page elements: {diag_err}")

    try:
        page.wait_for_load_state("networkidle", timeout=30_000)
    except PWTimeoutError:
        print("== [Handler]   Warning: networkidle timeout after login submit.")
    page.wait_for_timeout(5_000)
    print(f"== [Handler]   Post-login URL: {page.url}")
    print(f"== [Handler]   Page title    : {page.title()}")


# ──────────────────────────────────────────────────────────────
# Download file from secure portal (Playwright)
# ──────────────────────────────────────────────────────────────
def _download_from_portal(page, matrix_row, download_folder):
    """Locate and trigger the file download after authenticating."""
    file_prefix = matrix_row.get("file_prefix", "").strip()
    expected_ext = matrix_row.get("expected_extension", "").strip().lower()

    print(f"== [Handler] ── _download_from_portal ──")
    print(f"== [Handler]   Current URL    : {page.url}")
    print(f"== [Handler]   File prefix    : '{file_prefix}'")
    print(f"== [Handler]   Expected ext   : '{expected_ext}'")
    print(f"== [Handler]   Download folder: {download_folder}")

    download_selectors = [
        f'a:has-text("{file_prefix}")' if file_prefix else None,
        'a[download]',
        'a:has-text("Download")',
        'button:has-text("Download")',
        'a:has-text("download")',
        f'a[href$=".{expected_ext}"]' if expected_ext else None,
        'a:has-text("Open")',
        'a:has-text("Attachment")',
        'button:has-text("Open")',
        'a:has-text("Export")',
        'button:has-text("Export")',
        'a[class*="download"]',
        'a[class*="attachment"]',
        'a[id*="attachment"]',
        'a[id*="download"]',
    ]
    download_selectors = [s for s in download_selectors if s is not None]

    for i, sel in enumerate(download_selectors, 1):
        try:
            loc = page.locator(sel).first
            if not loc.is_visible(timeout=5_000):
                continue

            print(f"== [Handler]   [{i}] Trying selector: {sel}")

            with page.expect_download(timeout=120_000) as download_info:
                loc.click()
            download = download_info.value

            save_path = os.path.join(download_folder, download.suggested_filename)
            download.save_as(save_path)
            print(f"== [Handler]   ✓ Downloaded: {save_path}")
            return True

        except PWTimeoutError:
            print(f"== [Handler]   [{i}] Timeout waiting for download with selector: {sel}")
            continue
        except Exception as e:
            print(f"== [Handler]   [{i}] Selector '{sel}' failed: {e}")
            continue

    # Fallback: check if file was auto-downloaded on page load
    page.wait_for_timeout(15_000)
    if _check_file_in_folder(download_folder, file_prefix, expected_ext):
        print("== [Handler]   ✓ File found (auto-downloaded).")
        return True

    # Diagnostics: what's on the page?
    try:
        all_links = page.locator("a").all_text_contents()
        all_buttons = page.locator("button").all_text_contents()
        print(f"== [Handler]   [DEBUG] Page URL     : {page.url}")
        print(f"== [Handler]   [DEBUG] Page title   : {page.title()}")
        print(f"== [Handler]   [DEBUG] Visible links  : {[t.strip() for t in all_links if t.strip()]}")
        print(f"== [Handler]   [DEBUG] Visible buttons: {all_buttons}")
    except Exception:
        pass

    print("== [Handler]   ✗ Could not download file from secure portal.")
    return False


# ──────────────────────────────────────────────────────────────
# Flow A: Secure-link download  (log_in = YES)
# ──────────────────────────────────────────────────────────────
def _handle_secure_link(secure_url, matrix_row, download_folder):
    """Opens the secure link in Playwright, authenticates, downloads the file."""
    script_name = matrix_row["script_name"]
    os.makedirs(download_folder, exist_ok=True)

    print(f"== [Handler] ── _handle_secure_link ({script_name}) ──")
    print(f"== [Handler]   Secure URL     : {secure_url}")
    print(f"== [Handler]   Download folder: {download_folder}")

    pw, browser, context, page = _launch_browser(download_folder)
    try:
        print(f"== [Handler]   Opening secure link...")
        page.goto(secure_url, wait_until="networkidle", timeout=60_000)
        page.wait_for_timeout(5_000)

        email_val = matrix_row.get("email", "").strip()
        password_val = matrix_row.get("password", "").strip()
        if email_val or password_val:
            print(f"== [Handler]   Credentials provided, attempting login...")
            _attempt_secure_login(page, email_val, password_val, script_name)
        else:
            print(f"== [Handler]   No credentials configured, skipping login.")

        print(f"== [Handler]   Attempting to download from portal...")
        if _download_from_portal(page, matrix_row, download_folder):
            print(f"== [Handler]   ✓ Secure link flow completed successfully.")
            return download_folder

        log_error(
            ERROR_CODES["download_error"],
            "Failed to download file from secure portal.",
            script_name,
        )
        return None

    except Exception as e:
        log_error(ERROR_CODES["general_error"], f"Secure-link handler failed: {e}", script_name)
        print(f"== [Handler]   ✗ Error: {e}")
        return None
    finally:
        _close_browser(pw, browser)


# ──────────────────────────────────────────────────────────────
# Flow A (variant): SecureMessageAtt.html attachment
# ──────────────────────────────────────────────────────────────
def _handle_secure_html_attachment(access_token, message_id, matrix_row, download_folder):
    """Download HTML gateway attachment, open in Playwright, authenticate, download report."""
    script_name = matrix_row["script_name"]
    os.makedirs(download_folder, exist_ok=True)

    print(f"== [Handler] ── _handle_secure_html_attachment ({script_name}) ──")
    print(f"== [Handler]   Message ID     : {message_id[:30]}...")
    print(f"== [Handler]   Downloading HTML attachment via Graph API...")

    html_path = download_attachment(
        access_token, message_id, download_folder,
        target_prefix="", target_extension="html",
    )
    if not html_path:
        print("== [Handler]   ✗ No HTML secure attachment found.")
        return None

    print(f"== [Handler]   ✓ HTML attachment saved: {html_path}")

    pw, browser, context, page = _launch_browser(download_folder)
    try:
        file_url = "file:///" + html_path.replace(os.sep, "/")
        print(f"== [Handler]   Opening HTML file: {file_url}")
        page.goto(file_url, wait_until="networkidle", timeout=60_000)
        page.wait_for_timeout(5_000)

        email_val = matrix_row.get("email", "").strip()
        password_val = matrix_row.get("password", "").strip()
        if email_val or password_val:
            _attempt_secure_login(page, email_val, password_val, script_name)

        if _download_from_portal(page, matrix_row, download_folder):
            print(f"== [Handler]   ✓ HTML attachment flow completed.")
            try:
                os.remove(html_path)
            except OSError:
                pass
            return download_folder

        print(f"== [Handler]   ✗ Failed to download from HTML attachment portal.")
        log_error(
            ERROR_CODES["download_error"],
            "Failed to download from HTML secure attachment.",
            script_name,
        )
        return None

    except Exception as e:
        log_error(ERROR_CODES["general_error"], f"HTML attachment handler failed: {e}", script_name)
        return None
    finally:
        _close_browser(pw, browser)


# ──────────────────────────────────────────────────────────────
# Flow B: Direct attachment  (log_in = NO)
# ──────────────────────────────────────────────────────────────
def _handle_direct_attachment(access_token, message_id, matrix_row, download_folder):
    """Downloads the matching attachment from the email via Graph API."""
    script_name = matrix_row["script_name"]
    os.makedirs(download_folder, exist_ok=True)

    requires_extraction = str(matrix_row.get("requires_extraction", "")).upper() == "YES"

    print(f"== [Handler] ── _handle_direct_attachment ({script_name}) ──")
    print(f"== [Handler]   Message ID          : {message_id[:30]}...")
    print(f"== [Handler]   Download folder     : {download_folder}")
    print(f"== [Handler]   Requires extraction : {requires_extraction}")

    if requires_extraction:
        target_prefix = matrix_row.get("file_prefix", "").strip()
        target_ext = matrix_row.get("expected_extension", "").strip()
    else:
        target_prefix = matrix_row.get("extracted_file_prefix", "").strip()
        target_ext = matrix_row.get("extracted_file_extension", "").strip()

    print(f"== [Handler]   Target prefix   : '{target_prefix}'")
    print(f"== [Handler]   Target extension: '{target_ext}'")
    print(f"== [Handler]   Downloading attachment via Graph API...")

    saved_path = download_attachment(
        access_token, message_id, download_folder,
        target_prefix=target_prefix,
        target_extension=target_ext,
    )

    if saved_path:
        print(f"== [Handler]   ✓ Attachment downloaded successfully.")
        return download_folder

    print(f"== [Handler]   ✗ No attachment matched.")
    log_error(
        ERROR_CODES["download_error"],
        f"No attachment matched prefix='{target_prefix}', ext='{target_ext}'.",
        script_name,
    )
    return None


# ──────────────────────────────────────────────────────────────
# Extraction helper
# ──────────────────────────────────────────────────────────────
def _handle_extraction(download_folder, matrix_row):
    """If requires_extraction=YES, find and extract the archive."""
    file_prefix = matrix_row.get("file_prefix", "").strip().lower()
    expected_ext = matrix_row.get("expected_extension", "").strip().lower().lstrip(".")

    print(f"== [Handler] ── _handle_extraction ──")
    print(f"== [Handler]   Folder       : {download_folder}")
    print(f"== [Handler]   Archive prefix: '{file_prefix}'")
    print(f"== [Handler]   Archive ext   : '{expected_ext}'")
    print(f"== [Handler]   Files in folder: {os.listdir(download_folder)}")

    archive_file = None
    for f in os.listdir(download_folder):
        f_lower = f.lower()
        prefix_ok = (not file_prefix) or (file_prefix in f_lower)
        ext_ok = (not expected_ext) or f_lower.endswith(f".{expected_ext}")
        if prefix_ok and ext_ok:
            archive_file = os.path.join(download_folder, f)
            break

    if not archive_file:
        print(f"== [Handler]   ✗ No archive matching prefix='{file_prefix}', ext='{expected_ext}'.")
        return

    if archive_file.lower().endswith(".zip"):
        try:
            with zipfile.ZipFile(archive_file, "r") as zf:
                names = zf.namelist()
                print(f"== [Handler]   ZIP contents ({len(names)} files): {names}")
                zf.extractall(download_folder)
            print(f"== [Handler]   ✓ Extracted ZIP: {archive_file}")
            print(f"== [Handler]   Files after extraction: {os.listdir(download_folder)}")
        except Exception as e:
            print(f"== [Handler]   ✗ ZIP extraction error: {e}")
    else:
        print(f"== [Handler]   Non-ZIP archive not auto-handled: {archive_file}")


# ──────────────────────────────────────────────────────────────
# Main entry point
# ──────────────────────────────────────────────────────────────
def run_email_handler(matrix_row, date_info, access_token):
    # type: (dict, dict, str) -> Optional[str]

    script_name = matrix_row.get("script_name", "email_handler")
    carrier_name = matrix_row.get("carrier_name", "?")
    process_name = matrix_row.get("process_name", "?")
    company_id = matrix_row.get("company_id")
    carrier_id = matrix_row.get("carrier_id")

    # ── Setup Logging ──
    script_name_logged = setup_logger(script_name)
    init_log_entry(script_name_logged)

    update_log_extra_fields(
        script_name_logged,
        process_type=process_name,
        company_id=company_id,
        carrier_id=carrier_id,
        product_name=matrix_row.get("product_name", ""),
        flow_id="6496A5D2-FF34-4074-81C2-C44C9F4CDD04",
        sub_entity_id="270681372001"
    )

    try:
        print(f"\n{'='*80}")
        print(f"== [Email Handler] {carrier_name} / {process_name}")
        if TEST_MODE:
            print(f"== [TEST MODE] Date filter RELAXED — matching any recent email")
        print(f"{'='*80}")

        # ── Step 1: Find the matching email ──
        msg = find_matching_email(
            access_token,
            matrix_row.get("sender_email", "").strip(),
            matrix_row.get("subject_key", "").strip(),
            test_mode=TEST_MODE,
        )

        if not msg:
            print(f"== [Handler] ✗ No matching email found. Aborting.")
            log_error(ERROR_CODES["target_file_not_found"], "No matching email found.", script_name_logged)
            log_final_entry(script_name_logged)
            return None

        message_id = msg["id"]
        raw_dl_path = (matrix_row.get("download_path") or "").strip()
        if not raw_dl_path or raw_dl_path == ".":
            raw_dl_path = os.path.join(os.getcwd(), "downloads", script_name)
            print(f"== [Handler]   download_path was empty, defaulting to: {raw_dl_path}")
        download_folder = os.path.normpath(raw_dl_path)
        login_required = str(matrix_row.get("log_in", "NO")).upper() == "YES"

        print(f"== [Handler] Step 1 ✓ Email found.")
        print(f"== [Handler]   Message ID     : {message_id[:30]}...")
        print(f"== [Handler]   Has attachments: {msg.get('hasAttachments', False)}")
        print(f"== [Handler]   Download folder: {download_folder}")
        print(f"== [Handler]   Login required : {login_required}")

        # ── Step 2: Route to correct flow ──
        print(f"== [Handler] Step 2: Routing to {'Flow A (secure-link)' if login_required else 'Flow B (direct attachment)'}...")

        if login_required:
            email_body = (msg.get("body") or {}).get("content") or ""
            print(f"== [Handler]   Email body length: {len(email_body)} chars")
            secure_url = extract_secure_link(email_body)

            if secure_url:
                print(f"== [Handler]   ✓ Secure URL found: {secure_url[:100]}...")
                result = _handle_secure_link(secure_url, matrix_row, download_folder)
            elif msg.get("hasAttachments", False):
                print(f"== [Handler]   No secure URL, but has attachments — trying HTML attachment flow.")
                result = _handle_secure_html_attachment(
                    access_token, message_id, matrix_row, download_folder,
                )
            else:
                print(f"== [Handler]   ✗ No secure URL and no attachments — cannot proceed.")
                log_error(ERROR_CODES["link_not_found"], "log_in=YES but no secure link or HTML attachment found.", script_name_logged)
                log_final_entry(script_name_logged)
                return None
        else:
            result = _handle_direct_attachment(
                access_token, message_id, matrix_row, download_folder,
            )

        if result is None:
            print(f"== [Handler] ✗ Download flow returned None. Aborting.")
            log_final_entry(script_name_logged)
            return None

        print(f"== [Handler] Step 2 ✓ Download flow completed.")

        # ── Step 3: Extract if needed ──
        if str(matrix_row.get("requires_extraction", "")).upper() == "YES":
            print(f"== [Handler] Step 3: Extraction required, extracting...")
            _handle_extraction(download_folder, matrix_row)
        else:
            print(f"== [Handler] Step 3: No extraction needed, skipping.")

        # ── Step 4: Locate final file ──
        print(f"== [Handler] Step 4: Locating final file...")
        final_file = _locate_final_file(download_folder, matrix_row)

        if not final_file:
            print(f"== [Handler] ✗ Step 4 failed — final file not found.")
            log_error(ERROR_CODES["target_file_not_found"], "Extracted file not found after download.", script_name_logged)
            log_final_entry(script_name_logged)
            return None

        # ── Step 5: Rename file ──
        print(f"== [Handler] Step 5: Renaming file...")
        rename_base = matrix_row.get("rename_base", "").strip()
        renamed_file = _rename_file(final_file, rename_base)

        # ── Step 6: Upload to Azure Blob ──
        print(f"== [Handler] Step 6: Azure Blob upload...")
        if TEST_MODE:
            print(f"== [TEST MODE] Skipping Azure Blob upload. File: {renamed_file}")
        else:
            from azure_blob_utils import authenticate_blob_storage, upload_blob

            blob_service_client = authenticate_blob_storage(
                process_name=process_name,
                company_id=company_id,
                carrier_id=carrier_id
            )
            container_name, blob_path = _build_blob_path(matrix_row, renamed_file)
            upload_blob(
                blob_service_client, container_name,
                local_file_path=renamed_file, blob_path=blob_path,
            )
            print(f"== [Handler]   ✓ Uploaded to Azure Blob: {container_name}/{blob_path}")

        # ── Step 7: Log metadata ──
        print(f"== [Handler] Step 7: Updating log metadata...")
        blob_path_for_log = (
            f"[TEST] local:{renamed_file}" if TEST_MODE
            else _build_blob_path(matrix_row, renamed_file)[1]
        )
        update_log_extra_fields(
            script_name_logged,
            file_status="Ready",
            file_path=blob_path_for_log,
            file_report_month=datetime.now().strftime("%Y-%m-%d"),
        )
        log_success(script_name_logged, f"Uploaded: {blob_path_for_log}")
        log_final_entry(script_name_logged)

        # ── Step 8: Mark email read ──
        print(f"== [Handler] Step 8: Mark email as read...")
        if TEST_MODE:
            print("== [TEST MODE] Skipping mark-as-read.")
        else:
            mark_as_read(access_token, message_id)

        # ── Step 9: Service Interruption ──
        print(f"== [Handler] Step 9: Service Interruption trigger...")
        if TEST_MODE:
            print("== [TEST MODE] Skipping Service Interruption trigger.")
        else:
            try:
                import requests
                requests.post("http://localhost:8000/service_interruptions/run", timeout=30)
            except Exception as e:
                print(f"== [Handler]   Service Interruption trigger failed: {e}")

        print(f"\n{'='*80}")
        print(f"== [Handler] ✓ ALL STEPS COMPLETE for {carrier_name} / {process_name}")
        print(f"== [Handler]   Final file: {renamed_file}")
        print(f"{'='*80}\n")

        return renamed_file

    except Exception as e:
        print(f"== [Handler] ✗ UNHANDLED EXCEPTION in run_email_handler: {e}")
        log_error(ERROR_CODES["general_error"], str(e), script_name_logged)
        log_final_entry(script_name_logged)
        return None


# ──────────────────────────────────────────────────────────────
# Runner
# ──────────────────────────────────────────────────────────────
if __name__ == "__main__":
    from graph_auth import get_graph_access_token

    print("\n" + "=" * 80)
    print("== EMAIL CARRIER HANDLER — STARTING")
    print(f"== Mode      : {'TEST' if TEST_MODE else 'PRODUCTION'}")
    print(f"== Timestamp : {datetime.now().isoformat()}")
    print("=" * 80 + "\n")

    # ── Get Graph API token ──
    print("== [Runner] Acquiring Graph API access token...")
    access_token = get_graph_access_token()
    print("== [Runner] ✓ Token acquired.\n")

    # ── Load matrix rows ──
    matrix_rows = load_email_matrix_rows()
    print(f"\n== [Runner] Processing {len(matrix_rows)} row(s)...\n")

    # ── Date info ──
    today = datetime.now()
    date_info = {
        "today": today.strftime("%Y-%m-%d"),
        "month": today.strftime("%m"),
        "year": today.strftime("%Y"),
    }

    # ── Run each row ──
    results = {}
    for i, row in enumerate(matrix_rows, 1):
        carrier = row.get("carrier_name", "?")
        script = row.get("script_name", "?")
        print(f"\n== [Runner] ── Row {i}/{len(matrix_rows)}: {carrier} ({script}) ──")
        result = run_email_handler(row, date_info, access_token)
        results[script] = result

    # ── Summary ──
    print("\n" + "=" * 80)
    print("== EMAIL CARRIER HANDLER — SUMMARY")
    print("=" * 80)
    for script, result in results.items():
        status = "✓ SUCCESS" if result else "✗ FAILED"
        print(f"==   {script}: {status}  {'→ ' + result if result else ''}")
    print("=" * 80 + "\n")
