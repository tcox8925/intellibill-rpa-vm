r"""
Practice Fusion RPA -> CSV  (Playwright port)

What it does
- Launches Chrome with a specific Chrome profile via a Playwright persistent context.
- Logs into Practice Fusion if the login screen appears.
- Opens Reports -> Patient list report.
- Adds Age criteria, sets Between age_from and age_to, runs report.
- Scrapes patient rows from the report table.
- Opens each patient chart and scrapes demographics, contact, address, insurance, guarantor, pharmacy.
- Writes one CSV row per patient, incrementally.

IMPORTANT ("opens a browser but does nothing")
- Close ALL Chrome windows for the target profile FIRST. A running Chrome locks the
  profile inside the User Data dir, and neither Selenium nor Playwright can attach —
  you get a blank window that just sits there. Check for stray chrome.exe processes.
- Do not sign into that same profile manually while the script runs.

Security
- Do NOT hard-code passwords. Set PF_PASSWORD in your environment.

Example PowerShell:
  $env:PF_USERNAME="your_login@email.com"
  $env:PF_PASSWORD="your_password"
  python practice_fusion_rpa_to_csv.py `
    --chrome-user-data-dir "C:\Users\YOUR_USER\AppData\Local\Google\Chrome\User Data" `
    --profile-directory "Profile 11" `
    --practice "NWARK Internal Medicine" `
    --age-from 0 `
    --age-to 120 `
    --out "practice_fusion_patients.csv"

Install:
  pip install playwright
  # Uses your installed Google Chrome via channel="chrome"; no `playwright install` needed for the browser.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import shutil
import sys
import time
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Dict, Iterable, List, Optional, Tuple

from playwright.sync_api import (
    ElementHandle,
    Error as PWError,
    Page,
    TimeoutError as PWTimeout,
    sync_playwright,
)

from pf_sync_pkg.models import ScheduleScrapeConfig

# Loads PF_USERNAME/PF_PASSWORD/etc from the repo-root .env, same as
# pf_sync_pkg.constants -- importing that module would also trigger it, but this
# module is sometimes run standalone (`python pull_patients.py ...`) rather than
# always via the pf_sync_pkg package, so it loads dotenv itself too. Idempotent and
# never overrides a value the environment already set explicitly.
try:
    from dotenv import find_dotenv, load_dotenv

    load_dotenv(find_dotenv(usecwd=False))
except ImportError:  # pragma: no cover - python-dotenv not installed
    pass

LOGIN_URL = "https://static.practicefusion.com/apps/ehr/index.html#/login"
EHR_BASE_URL = "https://static.practicefusion.com/apps/ehr/index.html"

# Playwright uses milliseconds.
DEFAULT_TIMEOUT = 30_000
SHORT_TIMEOUT = 5_000

CSV_COLUMNS = [
    # Existing wpo.ehr_patients-style fields
    "id",
    "ehr_name",
    "patient_id",
    "entity",
    "sub_entity",
    "practice",
    "patient_name",
    "dob",
    "sex",
    "marital_status",
    "email",
    "home_phone",
    "mobile_phone",
    "address_line_1",
    "city",
    "state",
    "zip_code",
    "status",
    "primary_insurance_id",
    "secondary_insurance_id",
    "insurance_scraped",
    "insurance_scrape_error",
    "created_date",
    "updated_date",
    "primary_insurance_name",
    "secondary_insurance_name",
    "effective_start_date",
    "effective_end_date",
    "primary_plan_name",
    "secondary_plan_name",
    "post_op",
    "behavioral_postop",
    # Extra one-table fields from Practice Fusion profile/report
    "ehr_patient_guid",
    "ehr_patient_url",
    "record_number",
    "age_at_scrape",
    "preferred_contact",
    "work_phone",
    "address_line_2",
    "payment_preference",
    "primary_coverage_type",
    "primary_plan_type",
    "primary_copay",
    "primary_eligibility_status",
    "secondary_coverage_type",
    "secondary_plan_type",
    "secondary_copay",
    "secondary_eligibility_status",
    "eligibility_last_checked",
    "inactive_insurance_count",
    "inactive_insurance_json",
    "guarantor_name",
    "guarantor_relation",
    "guarantor_dob",
    "guarantor_sex",
    "guarantor_primary_phone",
    "guarantor_secondary_phone",
    "guarantor_address_line_1",
    "guarantor_address_line_2",
    "guarantor_city",
    "guarantor_state",
    "guarantor_zip_code",
    "preferred_pharmacy_name",
    "preferred_pharmacy_phone",
    "preferred_pharmacy_fax",
    "preferred_pharmacy_address_1",
    "preferred_pharmacy_city",
    "preferred_pharmacy_state",
    "preferred_pharmacy_zip_code",
    "scrape_source",
    "scrape_run_id",
    "last_scraped_at",
    "report_signature",
    "patient_note_json",
    "summary",
    "raw_patient_json",
    "raw_insurance_json",
]


@dataclass
class ReportPatient:
    first_name: str = ""
    last_name: str = ""
    patient_id: str = ""
    dob: str = ""
    age: str = ""
    sex: str = ""
    preferred_contact: str = ""
    status: str = ""  # Patient List report's active/inactive registry status -- NOT
    # the appointment's Seen/Confirmed status. Kept separate from the fields below
    # so a Schedule-row scrape can never collide with the registry semantics this
    # field already carries elsewhere (see collect_all_report_patients_bucketed).
    ehr_patient_guid: str = ""
    ehr_patient_url: str = ""

    # Schedule-row-only fields (scrape_schedule_day): confirmed live 2026-08-21 that
    # the Appointments list row carries all four of these alongside name/DOB/phone --
    # they were simply never read. See scrape_schedule_day's docstring for the exact
    # selectors/attributes each comes from.
    appointment_status: str = ""
    appointment_start_time: str = ""
    appointment_type: str = ""
    provider_name: str = ""


def clean(value: Optional[str]) -> str:
    if value is None:
        return ""
    value = re.sub(r"\s+", " ", value).strip()
    return "" if value == "--" else value


def now_iso() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def selector(data_element: str) -> str:
    return f"[data-element='{data_element}']"


# ---------------------------------------------------------------------------
# Playwright helpers (scope can be a Page or an ElementHandle; both expose
# query_selector / query_selector_all).
# ---------------------------------------------------------------------------
def el_text(el: Optional[ElementHandle]) -> str:
    if el is None:
        return ""
    try:
        return clean(el.inner_text())
    except Exception:
        try:
            return clean(el.text_content() or "")
        except Exception:
            return ""


def safe_text(scope, css: str, default: str = "") -> str:
    try:
        el = scope.query_selector(css)
        if el is None:
            return default
        text = el_text(el)
        return text if text != "" else default
    except Exception:
        return default


def safe_text_by_data(scope, data_element: str, default: str = "") -> str:
    return safe_text(scope, selector(data_element), default)


def visible(page: Page, css: str, timeout: int = DEFAULT_TIMEOUT) -> ElementHandle:
    """Waits for an element to be visible and returns its handle."""
    return page.wait_for_selector(css, state="visible", timeout=timeout)


def click_sel(page: Page, selector_str: str, timeout: int = DEFAULT_TIMEOUT) -> None:
    """Click by CSS or XPath (prefix XPath selectors with 'xpath=')."""
    loc = page.locator(selector_str).first
    loc.wait_for(state="visible", timeout=timeout)
    try:
        loc.scroll_into_view_if_needed(timeout=timeout)
    except Exception:
        pass
    try:
        loc.click(timeout=timeout)
    except Exception:
        # JS click fallback for overlays / intercepted clicks.
        handle = loc.element_handle()
        if handle is not None:
            handle.evaluate("el => el.click()")


def click_sel_any(page: Page, selectors: List[str], timeout: int = DEFAULT_TIMEOUT, label: str = "") -> None:
    """Click the first selector in the list that becomes visible, trying each in
    order rather than betting the whole run on one exact attribute.

    Added 2026-08-11 after `a[data-tracking='Patient list report']` silently
    stopped matching mid-run (PF dropped that attribute; the link only carries
    `data-element='patient-list-report'` now) -- that was a single hardcoded
    selector with no fallback, so the break surfaced as a 30s timeout deep into
    a run instead of failing fast with a useful message. Mirrors the
    try-selector-list-in-order pattern pf_pdf_sync_config.json/models.py already
    use for the PDF pipeline (e.g. export_report_button_selectors) -- this file
    just never had it. Every remaining selector in `selectors` is a defense
    against exactly this kind of PF markup change, not a preference/style thing.
    """
    deadline = time.time() + timeout / 1000.0
    last_error: Optional[Exception] = None
    while True:
        for sel in selectors:
            try:
                click_sel(page, sel, timeout=SHORT_TIMEOUT)
                return
            except Exception as exc:  # noqa: BLE001 - try the next selector
                last_error = exc
                continue
        if time.time() >= deadline:
            break
        time.sleep(0.2)
    diagnostics = []
    for sel in selectors:
        try:
            count = page.locator(sel).count()
        except Exception:
            count = -1
        diagnostics.append(f"{sel!r} -> {count} match(es)")
    where = f" ({label})" if label else ""
    raise RuntimeError(
        f"click_sel_any{where}: none of the selectors matched within {timeout}ms. "
        + "; ".join(diagnostics)
        + (f" | last error: {last_error}" if last_error else "")
    )


def safe_click_handle(page: Page, el: ElementHandle) -> None:
    """Click a resolved ElementHandle with a JS fallback."""
    try:
        el.scroll_into_view_if_needed()
        time.sleep(0.2)
        el.click()
    except Exception:
        try:
            el.evaluate("el => el.click()")
        except Exception:
            pass


def clear_and_type(el: ElementHandle, text: str) -> None:
    """Clear a framework-controlled input and type a value, verifying it took.

    Confirmed live 2026-08-10 on the age-range sweep: Control+A/Delete does not
    reliably clear these inputs between successive buckets -- each bucket's digits
    concatenated onto the previous ones (e.g. range-low ending up as
    '12015100500505050505050505050' after ~10 buckets), silently producing garbage
    age ranges and wrong/zero report totals for every bucket after the first few.
    Same bug class as the login-field garbling fix in browser.py. Now clears via
    both keyboard AND a direct JS value reset, then reads the value back and
    retries (up to 4 attempts) until it matches what was intended.
    """
    text = str(text)
    for _ in range(4):
        try:
            el.click()
            el.press("Control+a")
            el.press("Delete")
        except Exception:
            pass
        try:
            el.evaluate(
                """
                el => {
                    el.value = '';
                    el.dispatchEvent(new Event('input', {bubbles: true}));
                    el.dispatchEvent(new Event('change', {bubbles: true}));
                }
                """
            )
        except Exception:
            pass
        try:
            el.type(text)
        except Exception:
            try:
                el.fill(text)
            except Exception:
                pass
        try:
            current = el.input_value()
        except Exception:
            current = None
        if current == text:
            return
    raise RuntimeError(
        f"AGE_FIELD_NOT_SETTABLE: could not get the age range field to hold "
        f"the intended value {text!r} after retries."
    )


def parse_city_state_zip(value: str) -> Tuple[str, str, str]:
    """Parses strings like 'Gravette, AR 72736' into city/state/zip."""
    value = clean(value)
    if not value:
        return "", "", ""
    m = re.match(r"^(?P<city>.*?),\s*(?P<state>[A-Z]{2})\s*(?P<zip>[0-9A-Za-z\- ]+)?$", value)
    if not m:
        return value, "", ""
    return clean(m.group("city")), clean(m.group("state")), clean(m.group("zip") or "")


def parse_effective_range(value: str) -> Tuple[str, str]:
    """Returns start/end if PF displays one date or a date range."""
    value = clean(value)
    if not value:
        return "", ""
    # Examples handled: 01/01/2025, 01/01/2025 - 12/31/2025, 01/01/2025 to 12/31/2025
    parts = re.split(r"\s*(?:-|to|–|—)\s*", value, maxsplit=1, flags=re.I)
    if len(parts) == 1:
        return clean(parts[0]), ""
    return clean(parts[0]), clean(parts[1])


def parse_guid_from_href(href: str) -> str:
    href = href or ""
    m = re.search(r"/patients/([0-9a-fA-F-]{20,})", href)
    return m.group(1) if m else ""


# Only skip pure disk caches and lock files. Everything that can hold the
# logged-in + trusted-device state (Cookies, Login Data, Local Storage,
# Session Storage, IndexedDB, Service Worker, Preferences, Web Data, Network\)
# is copied, so Practice Fusion recognizes the browser and does not re-prompt 2FA.
_CLONE_IGNORE = shutil.ignore_patterns(
    "Cache",
    "Code Cache",
    "GPUCache",
    "GraphiteDawnCache",
    "DawnCache",
    "DawnGraphiteCache",
    "DawnWebGPUCache",
    "ShaderCache",
    "GrShaderCache",
    "Crashpad",
    "component_crx_cache",
    "optimization_guide_model_store",
    "*.tmp",
    "*.log",
    "SingletonLock",
    "SingletonCookie",
    "SingletonSocket",
    "lockfile",
)


def clone_profile_if_needed(args: argparse.Namespace) -> None:
    """
    Copies a real Chrome profile (e.g. 'Profile 11') into the dedicated
    automation user-data dir as 'Default', so the logged-in Practice Fusion
    session carries over and no OTP is triggered.

    Runs only when the dedicated dir has no profile yet, or --refresh-profile
    is passed. Once created, the automation profile persists across runs, so a
    device-trust/OTP prompt (if any) happens at most once.

    IMPORTANT: Chrome must be fully closed while copying, or the Cookies /
    Login Data SQLite files are locked and the session will not copy cleanly.
    """
    dest = os.path.abspath(args.chrome_user_data_dir)
    dest_default = os.path.join(dest, "Default")

    already_have_profile = os.path.isdir(dest_default)
    if already_have_profile and not args.refresh_profile:
        print(f"Reusing existing automation profile at: {dest_default}", flush=True)
        return

    if not args.source_user_data_dir:
        # No source given: fall back to a fresh profile (may prompt OTP once).
        print("No --source-user-data-dir given; starting a fresh profile.", flush=True)
        return

    src_profile = os.path.join(args.source_user_data_dir, args.source_profile)
    if not os.path.isdir(src_profile):
        raise SystemExit(
            f"ERROR: source profile not found:\n  {src_profile}\n"
            "Check --source-user-data-dir and --source-profile "
            "(the folder name shown as 'Profile Path' in chrome://version)."
        )

    print(f"Cloning profile:\n  from: {src_profile}\n  to:   {dest_default}", flush=True)
    if args.refresh_profile and os.path.isdir(dest_default):
        shutil.rmtree(dest_default, ignore_errors=True)

    os.makedirs(dest, exist_ok=True)
    shutil.copytree(src_profile, dest_default, ignore=_CLONE_IGNORE, dirs_exist_ok=True)

    # 'Local State' (at the User Data root) holds the encryption key metadata used
    # to decrypt cookies. Copy it so the session decrypts in the new location.
    src_local_state = os.path.join(args.source_user_data_dir, "Local State")
    if os.path.isfile(src_local_state):
        shutil.copy2(src_local_state, os.path.join(dest, "Local State"))

    print("Profile clone complete.", flush=True)


_CHROME_PROC = None  # tracked so we can shut the launched Chrome down at exit


def _find_chrome_exe(explicit: str = "") -> str:
    if explicit and os.path.isfile(explicit):
        return explicit
    candidates = [
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        "/usr/bin/google-chrome",
    ]
    for c in candidates:
        if c and os.path.isfile(c):
            return c
    raise SystemExit(
        "Could not locate chrome.exe. Pass it explicitly with --chrome-exe "
        r'"C:\Program Files\Google\Chrome\Application\chrome.exe".'
    )


def _wait_devtools(endpoint: str, timeout: float = 30.0) -> None:
    import urllib.request

    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(endpoint + "/json/version", timeout=1) as resp:
                if resp.status == 200:
                    return
        except Exception:
            time.sleep(0.4)
    raise SystemExit(
        f"Chrome DevTools endpoint {endpoint} did not come up. "
        "Make sure no other Chrome is already using this profile/port."
    )


def build_context(args: argparse.Namespace):
    """
    Two modes:

    ATTACH (--attach): connect to a Chrome YOU already launched with a debug port
    and logged into by hand. The script does not launch or close Chrome; it just
    drives the already-authenticated session. No credentials, no 2FA handling.

    LAUNCH (default): the script starts Chrome itself (no automation flags) and
    attaches over CDP, cloning the source profile if needed.

    Why launch without automation flags: launching through Playwright normally adds
    --enable-automation, which sets navigator.webdriver=true and shows the
    "controlled by automated software" banner; Practice Fusion treats that as an
    unrecognized browser and forces 2FA. A plain launch avoids that.

    Chrome cannot use its DEFAULT "User Data" folder with a debug port, so
    """
    global _CHROME_PROC
    import subprocess

    port = int(args.debug_port)
    endpoint = f"http://127.0.0.1:{port}"

    # ---- ATTACH MODE: connect to an already-running, already-logged-in Chrome ----
    if args.attach:
        print(f"Attaching to running Chrome at {endpoint} ...", flush=True)
        try:
            _wait_devtools(endpoint, timeout=8.0)
        except SystemExit:
            raise SystemExit(
                f"No Chrome is listening on {endpoint}.\n"
                "Start Chrome with a debug port and log into Practice Fusion FIRST, e.g.:\n\n"
                '  & "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe" '
                f'--remote-debugging-port={port} '
                '--user-data-dir="C:\\Users\\poorn\\pf_rpa_chrome" --profile-directory=Default\n\n'
                "Log in (do the OTP once), leave that window open, then re-run with --attach."
            )
        pw = sync_playwright().start()
        browser = pw.chromium.connect_over_cdp(endpoint)
        context = browser.contexts[0] if browser.contexts else browser.new_context()
        page = find_logged_in_page(context)
        if page is None:
            page = context.pages[0] if context.pages else context.new_page()
        page.set_default_timeout(DEFAULT_TIMEOUT)
        page.set_default_navigation_timeout(DEFAULT_TIMEOUT)
        try:
            page.bring_to_front()
        except Exception:
            pass
        # _CHROME_PROC stays None, so teardown will NOT close your browser.
        print(f"Attached. Active tab: {page.url}", flush=True)
        return pw, context, page

    # ---- LAUNCH MODE ----
    udd = os.path.abspath(args.chrome_user_data_dir)
    base = os.path.basename(udd.rstrip("\\/")).lower()
    default_marker = os.path.join("google", "chrome", "user data").lower()
    if base == "user data" or default_marker in udd.lower():
        raise SystemExit(
            "ERROR: --chrome-user-data-dir points at Chrome's DEFAULT profile folder:\n"
            f"  {udd}\n"
            "Chrome blocks a debugging port on the default data directory. Point\n"
            "--chrome-user-data-dir at a dedicated folder and clone Profile 11 into it:\n"
            '  --chrome-user-data-dir "C:\\Users\\poorn\\pf_rpa_chrome" \\\n'
            '  --source-user-data-dir "C:\\Users\\poorn\\AppData\\Local\\Google\\Chrome\\User Data" \\\n'
            '  --source-profile "Profile 11"'
        )

    # No cloning: you log into this profile by hand. On the first run you do the
    # OTP once; a clean close persists it so later runs skip straight to the pause.
    chrome_exe = _find_chrome_exe(args.chrome_exe)
    port = int(args.debug_port)
    endpoint = f"http://127.0.0.1:{port}"

    profile_dir = args.profile_directory or "Default"
    launch_cmd = [
        chrome_exe,
        f"--remote-debugging-port={port}",
        f"--user-data-dir={udd}",
        f"--profile-directory={profile_dir}",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-notifications",
        "--start-maximized",
        "about:blank",
    ]
    # NOTE: deliberately NO --enable-automation / --disable-extensions etc.

    print("Launching Chrome (real profile, no automation flags)...", flush=True)
    _CHROME_PROC = subprocess.Popen(launch_cmd)
    _wait_devtools(endpoint, timeout=30.0)

    pw = sync_playwright().start()
    browser = pw.chromium.connect_over_cdp(endpoint)
    context = browser.contexts[0] if browser.contexts else browser.new_context()

    deadline = time.time() + 10
    while not context.pages and time.time() < deadline:
        time.sleep(0.2)
    page = context.pages[0] if context.pages else context.new_page()

    page.set_default_timeout(DEFAULT_TIMEOUT)
    page.set_default_navigation_timeout(DEFAULT_TIMEOUT)
    try:
        page.bring_to_front()
    except Exception:
        pass
    print(f"Attached over CDP. Current URL: {page.url}", flush=True)
    return pw, context, page


def is_logged_in(page: Page) -> bool:
    try:
        url = (page.url or "").lower()
        # Still in a login / 2FA route -> not logged in yet.
        if "securitycheck" in url or "/login" in url or url.rstrip("/").endswith("#/login"):
            return False
        # Explicit in-app signals.
        if page.query_selector(
            "xpath=//div[contains(@class,'menu-label') and normalize-space()='Reports']"
        ):
            return True
        if page.query_selector("a[data-tracking='Patient list report']"):
            return True
        # Generic: we're inside the EHR SPA (any #/... route) and not on login.
        if "#/pf" in url:
            return True
        if "index.html#/" in url and "login" not in url:
            return True
    except Exception:
        pass
    return False


def find_logged_in_page(context) -> Optional[Page]:
    for p in list(context.pages):
        try:
            if is_logged_in(p):
                return p
        except Exception:
            continue
    return None


def on_security_check(page: Page) -> bool:
    try:
        if "securitycheck" in (page.url or "").lower():
            return True
        if page.query_selector("xpath=//*[contains(normalize-space(),'Security check')]"):
            return True
    except Exception:
        pass
    return False


def wait_logged_in(page: Page, timeout: int = DEFAULT_TIMEOUT) -> Page:
    """
    Waits for the post-login UI across ALL open tabs (PF sometimes lands the
    dashboard in a different tab than the one we drove). Prints the open tab
    URLs periodically so it's clear where things are. If PF shows its 2FA
    'Security check', pauses for the user to enter the phone code; because the
    profile is persistent and Chrome is closed cleanly, that is a one-time step.

    Returns the Page that is logged in (may differ from the input page).
    """
    context = page.context
    deadline = time.time() + timeout / 1000.0
    warned = False
    last_diag = 0.0
    while time.time() < deadline:
        found = find_logged_in_page(context)
        if found is not None:
            try:
                found.bring_to_front()
            except Exception:
                pass
            return found

        now = time.time()
        if now - last_diag > 10:
            try:
                urls = [ (pp.url or "") for pp in context.pages ]
            except Exception:
                urls = ["<unavailable>"]
            print(f"  [waiting for login] open tabs: {urls}", flush=True)
            last_diag = now

        if not warned and any(on_security_check(pp) for pp in list(context.pages)):
            print(
                "\n"
                "==================================================================\n"
                " Practice Fusion wants a 2-factor code (unrecognized browser).\n"
                " ACTION NEEDED in the Chrome window:\n"
                "   1. Click 'Send code', enter the code from the phone.\n"
                "   2. If offered, check 'remember this device' / 'don't ask again'.\n"
                " The script is waiting and will continue automatically once you're in.\n"
                f" You have up to {int(timeout/1000)} seconds.\n"
                "==================================================================\n",
                flush=True,
            )
            warned = True
        time.sleep(1.0)
    raise PWTimeout(
        "Timed out waiting for post-login UI. The tab URLs printed above show where "
        "it was stuck — send me the last '[waiting for login] open tabs: [...]' line."
    )


def login_if_needed(page: Page, username: str, password: str) -> Page:
    print(f"Navigating to login: {LOGIN_URL}", flush=True)
    page.goto(LOGIN_URL, wait_until="domcontentloaded")
    time.sleep(2)
    print(f"After goto, URL is: {page.url}", flush=True)

    if page.query_selector("#inputUsername"):
        print("Login form detected, signing in...", flush=True)

        user_loc = page.locator("#inputUsername")
        user_loc.wait_for(state="visible", timeout=DEFAULT_TIMEOUT)
        user_loc.click()
        user_loc.fill(username)          # fill fires the input events Ember needs
        user_loc.blur()

        pwd_loc = page.locator("input[type='password']").first
        pwd_loc.wait_for(state="visible", timeout=DEFAULT_TIMEOUT)
        pwd_loc.click()
        pwd_loc.fill(password)
        pwd_loc.blur()
        time.sleep(0.5)

        # Click Log in, then confirm we actually left the login route. If the
        # button was still disabled / didn't submit, fall back to pressing Enter.
        try:
            click_sel(page, "#loginButton", DEFAULT_TIMEOUT)
        except Exception:
            pass

        left_login = False
        for _ in range(10):
            time.sleep(1)
            u = (page.url or "").lower()
            if "securitycheck" in u or ("#/login" not in u and "index.html#" in u):
                left_login = True
                break
        if not left_login:
            print("Sign-in didn't advance; retrying via Enter key...", flush=True)
            try:
                pwd_loc.press("Enter")
            except Exception:
                pass
            time.sleep(2)

    print("Waiting for post-login UI...", flush=True)
    page = wait_logged_in(page, 30_000)  # 30s; enough to detect an already-trusted session
    print(f"Logged in. Active tab: {page.url}", flush=True)
    print("Opening patient list report...", flush=True)
    return page


def open_patient_list_report(page: Page) -> None:
    """One-time: open the report and add the Age criterion set to 'Between'."""
    # Left nav Reports. data-tracking is PF's analytics attribute -- it has already
    # proven itself removable without notice (see below), so it's listed as a
    # fallback rather than the primary selector; the visible menu-label text is
    # the more durable signal.
    click_sel_any(
        page,
        [
            "xpath=//div[contains(@class,'menu-label') and normalize-space()='Reports']",
            "a[data-tracking='Reports']",
        ],
        DEFAULT_TIMEOUT,
        label="Reports nav",
    )

    # Patient list report. Re-confirmed live 2026-08-10 (later in the day): the
    # Reports page now renders BOTH the classic report ("Patient list report",
    # data-element='patient-list-report') and a "-v2" variant ("Patient list
    # report NEW", data-element='patient-list-report-v2') -- the link no longer
    # carries the data-tracking='Patient list report' attribute this selector
    # used to rely on, which is why this broke (a single hardcoded selector, no
    # fallback). Deliberately targeting the classic (non-v2) report: it uses real
    # numbered pagination and, on the account this was confirmed against,
    # returned the full roster -- 8249 patients -- from a single Age Range 0-120
    # query with no cap encountered. The "-v2" grid uses a virtualized-scroll
    # results table with a 1000-row display cap, which is why it's avoided here
    # -- so the text fallback below deliberately excludes "NEW" to avoid ever
    # landing on the v2 variant if data-element also changes.
    click_sel_any(
        page,
        [
            "a[data-element='patient-list-report']",
            "a[data-tracking='Patient list report']",
            "a:text-is('Patient list report')",
        ],
        DEFAULT_TIMEOUT,
        label="Patient list report link",
    )

    # Add Criteria -> Age. The criterion dropdown's own option list uses
    # data-element='select-query-criterion-option-N' (Age is index 2 today), but
    # text is matched first since PF's own ordering is not a contract -- and a bare
    # `text=Age` locator is a known trap here (confirmed live: it once matched
    # unrelated page chrome and navigated to Messages instead). Always scope to the
    # open listbox's own option role.
    click_sel(page, "[data-element='select-query-criterion-dropdown']", DEFAULT_TIMEOUT)
    time.sleep(0.6)  # let the option list paint (rendered in an overlay)
    try:
        click_sel(page, "li[role='option']:text-is('Age')", SHORT_TIMEOUT)
    except Exception:
        click_sel(page, "[data-element='select-query-criterion-option-2']", DEFAULT_TIMEOUT)

    # Wait for the comparator dropdown (starts on '=') to appear.
    visible(page, "[data-element='select-query-comparator-dropdown']", DEFAULT_TIMEOUT)

    # Change '=' to 'Range' (this classic report's two-value comparator is labeled
    # "Range", not "Between" -- confirmed live from the open dropdown's own option
    # list, data-element='select-query-comparator-option-3').
    click_sel(page, "[data-element='select-query-comparator-dropdown']", DEFAULT_TIMEOUT)
    time.sleep(0.4)
    try:
        click_sel(page, "li[role='option']:text-is('Range')", SHORT_TIMEOUT)
    except Exception:
        click_sel(page, "[data-element='select-query-comparator-option-3']", DEFAULT_TIMEOUT)
    visible(page, "[data-element='text-input-search-criteria-range-low']", DEFAULT_TIMEOUT)


def open_schedule_appointments_view(page: Page, config: Optional[ScheduleScrapeConfig] = None) -> None:
    """Navigate to Schedule and select the 'Appointments' (list/agenda) tab.

    Confirmed live 2026-08-11: the Schedule page has two distinct views sharing
    the same date-nav controls -- 'Appointments' (list/agenda,
    data-element='scheduler-tab-0'; renders one <td data-element='cell-patient-N'>
    per appointment with the patient's name+chart-link, DOB, and phone all inline)
    and 'Day' (calendar grid, scheduler-tab-1; appointments render as positioned
    grid blocks, with none of the cell-patient-N markup at all). Stepping through
    dates with btn-date-next/btn-date-previous was observed live to NOT reliably
    keep 'Appointments' selected -- the view silently fell back to the empty-looking
    'Day' grid mid-walk, which read as "0 appointments" even though the header's
    own count (e.g. "26 Appointments") proved the data was there. Always
    (re)assert this tab is selected before scraping a day; see go_to_schedule_date
    for the same defensive re-check during a multi-day walk.
    """
    config = config or ScheduleScrapeConfig()
    page.goto(f"{EHR_BASE_URL}#/PF/schedule", wait_until="domcontentloaded")
    click_sel_any(
        page,
        [config.scheduler_tab_selector, "text='Appointments'"],
        DEFAULT_TIMEOUT,
        label="Appointments (list) tab",
    )
    time.sleep(0.6)


def read_schedule_facility(page: Page, config: Optional[ScheduleScrapeConfig] = None) -> str:
    """Reads the Schedule toolbar's facility selector (e.g. 'NWARK Internal
    Medicine') -- the only facility/location signal that lives on the
    Schedule screen itself, confirmed live 2026-08-26. Never navigates
    anywhere; just reads whatever's already on the currently-open Schedule
    page. Returns "" (never raises) if the selector doesn't match -- callers
    should treat a blank service_location the same as any other best-effort
    field, not fail the whole scrape over it."""
    config = config or ScheduleScrapeConfig()
    try:
        el = page.query_selector(config.schedule_facility_selector)
        return clean(el.inner_text()) if el is not None else ""
    except PWError:
        return ""


def _appointments_tab_active(page: Page, config: Optional[ScheduleScrapeConfig] = None) -> bool:
    config = config or ScheduleScrapeConfig()
    tab = page.query_selector(config.scheduler_tab_selector)
    if tab is None:
        return False
    try:
        return "active" in (tab.get_attribute("class") or "")
    except Exception:
        return False


def _read_schedule_selected_date(page: Page, config: Optional[ScheduleScrapeConfig] = None):
    """Parses scheduler-selected-date's text (e.g. 'Tue, Aug 11, 2026') into a date."""
    config = config or ScheduleScrapeConfig()
    txt = safe_text_by_data(page, config.scheduler_selected_date_data_element)
    try:
        return datetime.strptime(clean(txt), "%a, %b %d, %Y").date()
    except Exception:
        return None


def go_to_schedule_date(
    page: Page, target_date, max_steps: int = 400, config: Optional[ScheduleScrapeConfig] = None
) -> bool:
    """Steps the Schedule view to target_date via btn-date-next/btn-date-previous,
    reading scheduler-selected-date after each click to know when to stop (no
    URL-based date navigation exists on this page -- confirmed live, both
    '#/PF/schedule/<date>' and '?date=<date>' either 404 or are silently ignored).

    Re-verifies the landing after the loop exits rather than trusting a single
    read: caught live 2026-08-11 on a 22-day jump (the first date walked in a
    multi-day run, straight off open_schedule_appointments_view) landing one day
    short (Sun instead of Mon) and reading a stale/in-transition date label as
    already matching -- the day before the target turned out to have 0
    appointments, which is what made the miss visible (a 21-appointment
    Monday silently came back as an empty Sunday). A single-day call to the
    same date worked fine in isolation, so this is specific to a long rapid
    click burst outrunning the label's own repaint, not the date-nav mechanism
    itself. One re-read after a short settle catches that without slowing down
    the common one-click-per-day case in the surrounding range loop.
    """
    config = config or ScheduleScrapeConfig()
    if not _appointments_tab_active(page, config):
        open_schedule_appointments_view(page, config)
    current = _read_schedule_selected_date(page, config)
    if current is None:
        return False
    steps = 0
    while current != target_date and steps < max_steps:
        forward = target_date > current
        click_sel(
            page,
            config.date_next_selector if forward else config.date_previous_selector,
            SHORT_TIMEOUT,
        )
        time.sleep(0.9)
        new_current = _read_schedule_selected_date(page, config)
        if new_current is None or new_current == current:
            time.sleep(0.6)
            new_current = _read_schedule_selected_date(page, config)
        current = new_current
        steps += 1
        if current is None:
            return False
    if current != target_date:
        return False
    # Re-verify after a short settle -- guards against the stale-label-read race
    # described above, where the loop above exits believing it arrived one step
    # early. If the label has since moved on, resume stepping from wherever it
    # actually is instead of returning a false positive.
    time.sleep(0.5)
    confirmed = _read_schedule_selected_date(page, config)
    if confirmed == target_date:
        return True
    if confirmed is None:
        return False
    return go_to_schedule_date(page, target_date, max_steps=max_steps - steps, config=config)


def _read_header_appointment_count(page: Page) -> Optional[int]:
    """Reads the 'Schedule<N> Appointments' H1 count -- confirmed live 2026-08-11
    this updates immediately with the date, ahead of the row table repainting
    (see _wait_for_schedule_rows). No dedicated data-element exists for it; it's
    plain H1 text with the title and count glued together, e.g. 'Schedule26
    Appointments'."""
    try:
        text = page.locator("h1.h1").first.inner_text()
    except Exception:
        return None
    m = re.search(r"(\d+)\s*Appointments", text)
    return int(m.group(1)) if m else None


def _wait_for_schedule_rows(
    page: Page, expected_count: int, timeout_ms: int = 10_000, config: Optional[ScheduleScrapeConfig] = None
) -> int:
    """Waits until the cell-patient-N row count catches up with the header's own
    appointment count for the currently displayed date.

    Added 2026-08-11 after a real, reproducible bug: go_to_schedule_date only
    confirmed scheduler-selected-date's TEXT had updated, then scraped
    immediately -- but the date label updates before the row table repaints, so
    a 26-appointment day was scraped as 0 rows (caught by cross-checking against
    pf_appointment_queue.json's already-known 26 rows for that same date; the
    header's own count was correct the whole time). If expected_count is 0
    (header genuinely says no appointments), returns immediately -- no rows will
    ever appear for that day.
    """
    config = config or ScheduleScrapeConfig()
    if expected_count <= 0:
        return 0
    deadline = time.time() + timeout_ms / 1000.0
    while time.time() < deadline:
        count = len(page.query_selector_all(config.cell_patient_prefix))
        if count >= expected_count:
            return count
        time.sleep(0.3)
    return len(page.query_selector_all(config.cell_patient_prefix))


def _schedule_row_scope(cell: ElementHandle) -> ElementHandle:
    """Widens from one cell-patient-N <td> to its enclosing <tr> so sibling
    columns (provider, appointment type, status) can be read too. Falls back to
    the cell itself if no <tr> ancestor is found -- keeps name/dob/phone
    extraction (already scoped to `cell`) unaffected either way. This is a plain
    DOM-structure walk (nearest <tr>), not a selector, so it has nothing to read
    from ScheduleScrapeConfig."""
    try:
        row = cell.query_selector("xpath=ancestor::tr[1]")
        return row if row is not None else cell
    except PWError:
        return cell


def _schedule_row_provider(row: ElementHandle, config: ScheduleScrapeConfig) -> str:
    """cell-provider-name-N: the trailing N is an Ember element id, not a row
    index -- confirmed live 2026-08-21 it does not line up with cell-patient-N's
    own N on the same row, so this matches on the stable prefix only (from
    config.provider_name_prefix), scoped to the row, never the exact
    data-element string."""
    try:
        el = row.query_selector(config.provider_name_prefix)
        return el_text(el) if el is not None else ""
    except PWError:
        return ""


def _schedule_row_appointment_type(row: ElementHandle, config: ScheduleScrapeConfig) -> str:
    """cell-appointment-type-N: same trailing-id caveat as provider above. The
    title attribute holds the clean label (e.g. 'Lab Results - New') without the
    hidden video-camera icon's markup that inner_text would otherwise pick up,
    so it's read first and inner_text is only a fallback."""
    try:
        el = row.query_selector(config.appointment_type_prefix)
        if el is None:
            return ""
        title = clean(el.get_attribute("title") or "")
        return title or el_text(el)
    except PWError:
        return ""


def _schedule_row_status(row: ElementHandle, config: ScheduleScrapeConfig) -> str:
    """intake-status-select-N-dropdown: the visible status text ('Seen',
    'Confirmed', ...) sits on a nested div's title attribute, not as its own
    data-element -- confirmed live 2026-08-21. Deliberately does NOT select by
    that div's class name (something like 'item--TBn'), which reads as an
    Ember-generated hash that can change between builds/deploys; the [title]
    attribute is the stable part. The sibling badge div in the same button
    (a two-letter provider-initials badge) carries no title, so a plain [title]
    search inside the button resolves to the status div without ambiguity."""
    try:
        button = row.query_selector(config.intake_status_button_prefix)
        if button is None:
            return ""
        titled = button.query_selector("[title]")
        return clean(titled.get_attribute("title") or "") if titled is not None else ""
    except PWError:
        return ""


def _schedule_row_start_time(row: ElementHandle, config: ScheduleScrapeConfig) -> str:
    """start-time carried no numeric suffix as scraped live 2026-08-21, but
    config.start_time_prefix_fallback is tried too in case PF adds one later the
    same way the other three fields already have."""
    try:
        el = row.query_selector(config.start_time_selector) or row.query_selector(
            config.start_time_prefix_fallback
        )
        return el_text(el) if el is not None else ""
    except PWError:
        return ""


def scrape_schedule_day(page: Page, config: Optional[ScheduleScrapeConfig] = None) -> List[ReportPatient]:
    """Scrapes name/DOB/phone/chart-GUID/appointment-status/start-time/type/
    provider from every appointment row on the currently displayed Schedule
    day, assuming open_schedule_appointments_view() already selected the
    'Appointments' tab and the target date is showing.

    cell-preferred-phone's text is e.g. 'M. (805) 704-2338' -- confirmed live
    2026-08-11 that the leading letter is a PHONE-TYPE label (Mobile/Home/Work),
    NOT sex: a 26-row test batch came back 100% "M." for the phone prefix, which
    would be an absurd sex split for a real patient population but is completely
    ordinary for "most patients list a mobile number first". Sex is deliberately
    left blank here (this view has no dedicated gender field the way the age-sweep
    Patient List report's td-gender column does) rather than guessed wrong from
    that prefix. The phone number itself is intentionally written into
    ReportPatient.preferred_contact (not a new field) because matching.py's
    PHONE_HEADER_ALIASES already treats a "preferred_contact"/"preferred contact"
    column as a phone-number source for the tiebreak scoring -- so this rides the
    existing alias rather than needing a matching.py change.

    Status/start-time/appointment-type/provider (added 2026-08-21) live in
    SIBLING columns of the same <tr>, not inside the cell-patient-N <td> itself,
    and none of their data-element attributes' numeric suffixes line up with
    cell-patient-N's own N -- see _schedule_row_scope and the per-field helpers
    above for why each is matched by stable prefix/attribute within the row,
    never by an exact suffixed data-element string. Each of the four is wrapped
    so a selector miss on any one of them degrades to "" instead of losing the
    row's name/dob/phone/guid, which is the part matching actually depends on.
    """
    config = config or ScheduleScrapeConfig()
    rows: List[ReportPatient] = []
    for cell in page.query_selector_all(config.cell_patient_prefix):
        try:
            name_link = cell.query_selector(config.cell_name_selector)
            if name_link is None:
                # Fallback: cell_name_selector requires an <a> tag
                # (a[data-element='cell-name']), but a No-show appointment has
                # no chart visit to link to -- PF renders that row's name as
                # plain text under the SAME data-element, no anchor wrapper.
                # The strict anchor-only lookup above silently skipped the
                # whole row before it even reached status/name parsing, which
                # is why No-show never showed up anywhere, not even as a
                # blank-status row (2026-08-26 fix). href/guid stay blank
                # here on purpose -- there genuinely is no chart link for this
                # row, unlike the require_guid=False case where a link exists
                # but parse_guid_from_href couldn't read it.
                name_link = cell.query_selector("[data-element='cell-name']")
                if name_link is None:
                    continue
                print(f"  [schedule-scrape] name cell had no <a> link (likely No-show or "
                      f"similar no-chart-visit status) -- captured via fallback text lookup",
                      flush=True)
            href = name_link.get_attribute("href") or ""
            guid = parse_guid_from_href(href)
            full_name = el_text(name_link)
            dob = safe_text_by_data(cell, config.cell_dob_data_element)
            phone_raw = safe_text_by_data(cell, config.cell_preferred_phone_data_element)
            # Strip a leading "<Letter>. " phone-type label (Mobile/Home/Work) if
            # present; keep the raw text as-is otherwise rather than guess wrong.
            phone_match = re.match(r"^[A-Za-z]\.\s*(.*)$", phone_raw)
            phone = clean(phone_match.group(1)) if phone_match else phone_raw
            parts = full_name.split(" ", 1)
            first_name = parts[0] if parts else ""
            last_name = parts[1] if len(parts) > 1 else ""

            row_scope = _schedule_row_scope(cell)
            appointment_status = _schedule_row_status(row_scope, config)
            appointment_start_time = _schedule_row_start_time(row_scope, config)
            appointment_type = _schedule_row_appointment_type(row_scope, config)
            provider_name = _schedule_row_provider(row_scope, config)

            rows.append(
                ReportPatient(
                    first_name=first_name,
                    last_name=last_name,
                    dob=dob,
                    preferred_contact=phone,
                    ehr_patient_guid=guid,
                    ehr_patient_url=f"{EHR_BASE_URL}#/PF/charts/patients/{guid}" if guid else href,
                    appointment_status=appointment_status,
                    appointment_start_time=appointment_start_time,
                    appointment_type=appointment_type,
                    provider_name=provider_name,
                )
            )
        except PWError:
            continue
    return rows


def _schedule_row_key(row: ReportPatient) -> str:
    """Identity for dedupe across scroll steps in scroll_schedule_day_and_collect:
    guid + start time, so two distinct same-day appointments for the same
    patient (a lab draw and a follow-up, say) are NOT collapsed into one --
    only the SAME row reappearing as it scrolls back into view gets deduped.
    Falls back to guid alone if start time didn't scrape (still correct for
    the overwhelmingly common one-appointment-per-day-per-patient case)."""
    guid = row.ehr_patient_guid
    return f"{guid}|{row.appointment_start_time}" if row.appointment_start_time else guid


def scroll_schedule_day_and_collect(
    page: Page,
    config: Optional[ScheduleScrapeConfig] = None,
    expected: Optional[int] = None,
    max_scrolls: int = 150,
) -> List[ReportPatient]:
    """scrape_schedule_day, but scrolling config.schedule_table_scroller_selector
    first -- same reasoning as scroll_report_and_collect's docstring (the
    Patient List Report / Appointment Report tables render only ~half their
    rows into the DOM at a time, the rest lazily as an inner scroller
    container is scrolled; scraping without scrolling silently returns a
    fraction of a busy page). The Schedule Appointments row DOM uses that same
    data-table__cell/appointments-table__col--sm PF component family, so a day
    with more appointments than fit in one viewport needs the same fix, not
    just a passive wait for the row count to catch up.

    Collects at EVERY scroll step, not just once at the end: if the container
    is truly virtualized, rows already scraped can unmount as you scroll
    further, so scraping only after reaching the bottom would lose whatever
    scrolled out along the way. Deduped via _schedule_row_key (guid+start
    time) so the same row staying in view across consecutive steps doesn't
    get counted twice.

    If no scroller element is found (this day's table isn't virtualized, or
    PF's markup doesn't match), falls back to a single plain
    scrape_schedule_day call -- unchanged behavior from before this existed.
    """
    # Diagnostic logging added 2026-08-25: schedule_table_scroller_selector
    # (models.py) was never independently confirmed against the live Schedule
    # DOM -- "very likely the same component" per its own comment, not proven.
    # These print()s make that visible in every run's console output instead
    # of silently under-scraping a busy day. Zero behavior change otherwise.
    config = config or ScheduleScrapeConfig()
    scroller = page.query_selector(config.schedule_table_scroller_selector)
    if scroller is None:
        fallback = scrape_schedule_day(page, config)
        print(f"  [schedule-scroll] scroller selector {config.schedule_table_scroller_selector!r} "
              f"not found on this day's Schedule -- falling back to ONE unscrolled scrape "
              f"({len(fallback)} row(s)). If the real day has more appointments than that, "
              f"this selector is stale/wrong and rows below the fold are being silently dropped.",
              flush=True)
        return fallback

    try:
        scroll_height = scroller.evaluate("el => el.scrollHeight")
        client_height = scroller.evaluate("el => el.clientHeight")
        print(f"  [schedule-scroll] scroller found (scrollHeight={scroll_height}, "
              f"clientHeight={client_height}, scrollable={scroll_height > client_height + 5})",
              flush=True)
    except Exception as exc:
        print(f"  [schedule-scroll] found scroller but could not read its dimensions: "
              f"{type(exc).__name__}: {exc}", flush=True)

    try:
        scroller.evaluate("el => { el.scrollTop = 0; }")
        time.sleep(0.2)
    except Exception:
        pass

    collected: Dict[str, ReportPatient] = {}
    last_seen_size = -1
    stuck = 0
    steps = 0

    for _ in range(max_scrolls):
        for row in scrape_schedule_day(page, config):
            key = _schedule_row_key(row)
            if key.strip("|"):
                collected[key] = row

        size = len(collected)
        steps += 1
        print(f"  [schedule-scroll] step {steps}: collected={size}"
              + (f" (expected={expected})" if expected is not None else ""), flush=True)
        if expected is not None and size >= expected:
            print(f"  [schedule-scroll] reached expected count ({size} >= {expected}), stopping", flush=True)
            break
        if size == last_seen_size:
            stuck += 1
        else:
            stuck = 0
            last_seen_size = size

        try:
            old_top = scroller.evaluate("el => el.scrollTop")
            scroller.evaluate("el => { el.scrollTop = el.scrollTop + Math.max(200, el.clientHeight * 0.6); }")
            time.sleep(0.25)
            new_top = scroller.evaluate("el => el.scrollTop")
            max_top = scroller.evaluate("el => el.scrollHeight - el.clientHeight")
        except Exception as exc:
            print(f"  [schedule-scroll] scroll step {steps} failed: {type(exc).__name__}: {exc}", flush=True)
            break

        at_bottom = int(new_top) >= int(max_top) - 5 or int(new_top) == int(old_top)
        if at_bottom and stuck >= 2:
            print(f"  [schedule-scroll] stopping after {steps} step(s): at_bottom={at_bottom}, "
                  f"stuck={stuck}, final collected={size}", flush=True)
            break
        if steps >= max_scrolls:
            print(f"  [schedule-scroll] WARNING: hit max_scrolls={max_scrolls} without settling "
                  f"-- collected={size} may still be incomplete", flush=True)

    return list(collected.values())


@dataclass
class ScheduledAppointment:
    """One row scraped off ONE Schedule day, tagged with the date it actually came
    from. discover_via_schedule_range (below) collapses repeat patients down to a
    single Dict[guid, ReportPatient] entry -- fine for registry-merge, which only
    wants one current name/DOB/phone snapshot per patient, but it silently drops
    every appointment except whichever day was scraped last for that patient. Use
    discover_appointments_via_schedule_range when a patient's second (third, ...)
    visit inside the requested range must not be lost -- e.g. injecting one
    synthetic queue record per visit, not per patient."""

    appointment_date: date
    patient: ReportPatient


def discover_appointments_via_schedule_range(
    page: Page,
    start_date,
    end_date,
    on_day=None,
    config: Optional[ScheduleScrapeConfig] = None,
    require_guid: bool = True,
    on_day_diagnostic=None,
) -> List[ScheduledAppointment]:
    """Walks the Schedule 'Appointments' view for every date in [start_date,
    end_date] and returns EVERY row scraped on EVERY day, each tagged with its
    own date -- no per-GUID dedupe, so a patient with two visits in the range
    keeps both. discover_via_schedule_range (below) is a thin GUID-deduped view
    over this same walk; see its docstring for when that's the one you want
    instead. `on_day(date, count, running_total)` is an optional progress
    callback -- running_total counts rows scraped so far, not unique patients.

    on_day_diagnostic(date, dict): optional, fires for EVERY date in range
    (including navigation failures, which `continue` past `on_day` entirely).
    dict carries {"navigated": bool, "header_count": Optional[int],
    "scraped_count": int} -- lets a caller like cli.run_appointments_by_date
    surface WHY a day came back empty (couldn't navigate there at all vs. PF's
    own header genuinely says 0 appointments vs. navigated fine and scraped
    fine) instead of a bare empty list that looks identical for all three.

    config: ScheduleScrapeConfig -- every selector this walk and scrape_schedule_day
    use comes from here, defaulting to ScheduleScrapeConfig()'s built-in confirmed
    values when not passed. Load from a JSON file (ScheduleScrapeConfig.load) to
    override without touching code if Practice Fusion's Schedule markup changes.

    require_guid: True (default) drops any row scrape_schedule_day couldn't pull
    a chart GUID for -- callers that inject a synthetic queue record and need to
    open that patient's chart (sync-schedules-by-date, facesheet-pull-by-date)
    cannot do anything with a GUID-less row anyway. PF only renders the
    patient-name cell as a clickable chart link (the href parse_guid_from_href
    reads) for SOME appointment statuses -- e.g. Confirmed/No-show rows can
    render as plain text before/without a chart visit, so requiring a GUID
    silently drops those statuses wholesale. cli.run_appointments_by_date (a
    read-only listing with no chart/queue interaction at all) passes
    require_guid=False so a status like Confirmed or No-show still shows up in
    that listing even with no GUID to attach.
    """
    config = config or ScheduleScrapeConfig()
    results: List[ScheduledAppointment] = []
    open_schedule_appointments_view(page, config)
    day_count = (end_date - start_date).days + 1
    for i in range(day_count):
        target = start_date + timedelta(days=i)
        if not go_to_schedule_date(page, target, config=config):
            print(f"  [schedule {target.isoformat()}] WARNING: could not navigate here, skipping", flush=True)
            if on_day_diagnostic is not None:
                on_day_diagnostic(target, {"navigated": False, "header_count": None, "scraped_count": 0})
            continue
        expected = _read_header_appointment_count(page)
        if expected:
            _wait_for_schedule_rows(page, expected, config=config)
        # scroll_and_paginate_schedule_day (not a plain scrape_schedule_day call):
        # a busy day's row count can exceed what the virtualized table renders
        # into the DOM at once (handled by scrolling) AND/OR exceed what fits on
        # one page at all (handled by clicking through a "Next" pager if one is
        # found) -- see that function's docstring for both. Falls back to one
        # plain scrape internally if neither applies to this day.
        day_rows = scroll_and_paginate_schedule_day(page, config, expected=expected)
        if expected is not None and len(day_rows) < expected:
            print(
                f"  [schedule {target.isoformat()}] WARNING: header says {expected} appointments, "
                f"only scraped {len(day_rows)} -- table may not have finished rendering",
                flush=True,
            )
        for r in day_rows:
            if r.ehr_patient_guid or not require_guid:
                results.append(ScheduledAppointment(appointment_date=target, patient=r))
            else:
                print(f"  [schedule {target.isoformat()}] dropped (no chart GUID -- likely status "
                      f"{r.appointment_status!r}): {r.first_name} {r.last_name}", flush=True)
        if on_day is not None:
            on_day(target, len(day_rows), len(results))
        else:
            print(f"  [schedule {target.isoformat()}] {len(day_rows)} appointments, running total {len(results)}", flush=True)
        if on_day_diagnostic is not None:
            on_day_diagnostic(target, {"navigated": True, "header_count": expected, "scraped_count": len(day_rows)})
    return results


def discover_via_schedule_range(
    page: Page, start_date, end_date, on_day=None, config: Optional[ScheduleScrapeConfig] = None
) -> Dict[str, ReportPatient]:
    """Walks the Schedule 'Appointments' view for every date in
    [start_date, end_date] and returns unique patients (keyed by GUID) found
    across that range -- name/DOB/phone/GUID, no chart visits.

    Use this instead of the full age-bucket discover() sweep when the pull is
    already scoped to a date range: it only surfaces patients who actually have
    an appointment in that window, which is smaller and faster than sweeping
    the entire roster, and it captures phone (the age-sweep seed does not),
    improving match-patients' ambiguous-match tiebreaking for this range.

    Trade-off vs. the full sweep: a patient with no appointment in [start_date,
    end_date] will never appear here -- this is not a registry replacement, only
    a fast source of GUIDs for a known date window. `on_day(date, count,
    running_total)` is an optional progress callback (see pull_patients.py's
    CLI for how discover-mode prints per-bucket progress the same way).

    Deliberately GUID-deduped -- a patient with two visits in the range collapses
    to whichever day was scraped LAST, which is fine for this function's only
    real consumer (registry merge: one current name/DOB/phone snapshot per
    patient is all it needs). Anything that must keep every visit -- e.g. the
    seen-but-not-in-report catch-up flow -- needs
    discover_appointments_via_schedule_range instead, not this.
    """
    collected: Dict[str, ReportPatient] = {}
    for appt in discover_appointments_via_schedule_range(page, start_date, end_date, on_day=on_day, config=config):
        collected[appt.patient.ehr_patient_guid] = appt.patient
    return collected


def _no_results_shown(page: Page) -> bool:
    """True when the results area shows the inline 'no patients' message.

    Confirmed live 2026-08-10: this classic report has no modal for zero results --
    it renders "No patients found matching current criteria" directly where the
    table would be, with no pager-label at all. (No dismissal needed, unlike the
    "-v2" grid's popup modal this function used to look for.)
    """
    try:
        return page.query_selector(
            "xpath=//*[contains(normalize-space(),'No patients found matching current criteria')]"
        ) is not None
    except Exception:
        return False


def run_age_bucket(page: Page, age_from, age_to) -> Optional[int]:
    """
    Sets the Range age inputs and runs the report, then reads the total for
    THIS range. A short pre-delay lets the report XHR start, and networkidle
    waits for it to finish, so we never read a leftover count from the prior
    range (which was making large ranges skip the >=cap split). Works for
    nested sub-ranges too, where the count/rows can look identical.
    """
    age0 = visible(page, "[data-element='text-input-search-criteria-range-low']", DEFAULT_TIMEOUT)
    age1 = visible(page, "[data-element='text-input-search-criteria-range-high']", DEFAULT_TIMEOUT)
    clear_and_type(age0, str(age_from))
    clear_and_type(age1, str(age_to))
    click_sel(page, "xpath=//button[not(@disabled) and normalize-space()='Run Report']", DEFAULT_TIMEOUT)

    # Let the report query fire, then wait for it to finish before reading.
    time.sleep(0.6)
    try:
        page.wait_for_load_state("networkidle", timeout=12_000)
    except Exception:
        pass

    deadline = time.time() + DEFAULT_TIMEOUT / 1000.0
    while time.time() < deadline:
        if _no_results_shown(page):
            return None
        total = get_total_count(page)
        if total is not None:
            time.sleep(0.4)  # tiny settle so grid rows are attached
            return total
        time.sleep(0.3)

    if _no_results_shown(page):
        return None
    return get_total_count(page)


def iter_age_buckets(start: int, stop: int, size: int):
    """Yields inclusive (lo, hi) windows, e.g. size=5 -> (0,4),(5,9),... up to stop."""
    lo = start
    while lo <= stop:
        hi = min(lo + size - 1, stop)
        yield lo, hi
        lo = hi + 1


def get_total_count(page: Page) -> Optional[int]:
    txt = safe_text_by_data(page, "pager-label")
    # Example: 1 - 50 of 89
    m = re.search(r"of\s+([0-9,]+)", txt, flags=re.I)
    if not m:
        return None
    return int(m.group(1).replace(",", ""))


def get_pager_range(page: Page):
    """Parses the pager label 'A - B of C' -> (shown_start, shown_end, total).
    Returns (None, None, None) if it can't be read."""
    txt = safe_text_by_data(page, "pager-label")
    m = re.search(r"([0-9,]+)\s*-\s*([0-9,]+)\s*of\s*([0-9,]+)", txt, flags=re.I)
    if not m:
        total = get_total_count(page)
        return (None, None, total)
    to_int = lambda s: int(s.replace(",", ""))
    return (to_int(m.group(1)), to_int(m.group(2)), to_int(m.group(3)))


def is_multi_page(page: Page) -> bool:
    """True only if the current result set spans more than one page."""
    start, end, total = get_pager_range(page)
    if end is not None and total is not None:
        return end < total
    return False


def _first_row_signature(page: Page) -> str:
    """Cheap fingerprint of the currently rendered first result row, used to detect
    a stale tbody after pagination (see collect_current_result_set)."""
    try:
        row = page.query_selector("tr[data-element='patient-list-result-row']")
        if row is None:
            return ""
        prn = safe_text_by_data(row, "td-prn")
        dob = safe_text_by_data(row, "td-dob")
        return f"{prn}|{dob}"
    except Exception:
        return ""


def scrape_visible_report_rows(page: Page) -> List[ReportPatient]:
    """Reads every row of the CURRENT page of the classic Patient list report.

    Confirmed live 2026-08-10: rows are `tr[data-element='patient-list-result-row']`,
    with plain data-element cells (td-prn/td-dob/td-age/td-gender/td-contact/
    td-status) plus two cells that are themselves anchor tags carrying the chart
    link -- td-first-name/td-last-name -- which is the only place this report
    exposes the patient's GUID (its own "Export CSV" has no GUID/link column at all).
    """
    rows: List[ReportPatient] = []
    for tr in page.query_selector_all("tr[data-element='patient-list-result-row']"):
        try:
            first_link = tr.query_selector("[data-element='td-first-name']")
            href = (first_link.get_attribute("href") or "") if first_link is not None else ""
            guid = parse_guid_from_href(href)
            first = el_text(first_link)
            last = safe_text_by_data(tr, "td-last-name")

            if not href or not guid:
                last_link = tr.query_selector("[data-element='td-last-name']")
                if last_link is not None:
                    if not href:
                        href = last_link.get_attribute("href") or ""
                    if not guid:
                        guid = parse_guid_from_href(href)

            rows.append(
                ReportPatient(
                    first_name=first,
                    last_name=last,
                    patient_id=safe_text_by_data(tr, "td-prn"),
                    dob=safe_text_by_data(tr, "td-dob"),
                    age=safe_text_by_data(tr, "td-age"),
                    sex=safe_text_by_data(tr, "td-gender"),
                    preferred_contact=safe_text_by_data(tr, "td-contact"),
                    status=safe_text_by_data(tr, "td-status"),
                    ehr_patient_guid=guid,
                    ehr_patient_url=f"{EHR_BASE_URL}#/PF/charts/patients/{guid}" if guid else href,
                )
            )
        except PWError:
            # Element went stale / detached during the read.
            continue
    return rows


def scroll_report_and_collect(page: Page, expected: Optional[int] = None, max_scrolls: int = 150) -> List[ReportPatient]:
    """Collects the CURRENT (numbered) page's rows.

    Confirmed live 2026-08-10: despite using real numbered pagination (not
    infinite/virtual-scroll pages), each individual page's row LIST is itself
    virtualized inside a `[data-element='data-table-scroller']` container -- a
    50-row page only renders ~23 `tr` elements into the DOM at a time; the rest
    render lazily as that inner container is scrolled. Scraping without scrolling
    silently returns roughly half of each page (confirmed: an 728-patient, 15-page
    sweep collected only 345 unique rows before this fix). This mirrors
    report_pull.py's scrape_report_to_csv() scroll loop for the appointment report,
    which hits the same PF data-table convention.
    """
    visible(page, "[data-element='patient-list-result-row']", DEFAULT_TIMEOUT)
    scroller = page.query_selector("[data-element='data-table-scroller']")
    if scroller is None:
        return scrape_visible_report_rows(page)

    try:
        scroller.evaluate("el => { el.scrollTop = 0; }")
        time.sleep(0.2)
    except Exception:
        pass

    collected: Dict[str, ReportPatient] = {}
    last_seen_size = -1
    stuck = 0

    for _ in range(max_scrolls):
        for row in scrape_visible_report_rows(page):
            key = row.ehr_patient_guid or f"{row.patient_id}|{row.dob}|{row.first_name}|{row.last_name}"
            if key.strip("|"):
                collected[key] = row

        size = len(collected)
        if expected is not None and size >= expected:
            break
        if size == last_seen_size:
            stuck += 1
        else:
            stuck = 0
            last_seen_size = size

        try:
            old_top = scroller.evaluate("el => el.scrollTop")
            scroller.evaluate("el => { el.scrollTop = el.scrollTop + Math.max(200, el.clientHeight * 0.6); }")
            time.sleep(0.25)
            new_top = scroller.evaluate("el => el.scrollTop")
            max_top = scroller.evaluate("el => el.scrollHeight - el.clientHeight")
        except Exception:
            break

        at_bottom = int(new_top) >= int(max_top) - 5 or int(new_top) == int(old_top)
        if at_bottom and stuck >= 2:
            break

    return list(collected.values())


def click_next_report_page(page: Page) -> bool:
    """Advances to the next numbered page. Confirmed live 2026-08-10: the real
    control is [data-element='pager-btn-next'], disabled via a CSS class (no
    `disabled` attribute) on the last page -- so a plain `:not([disabled])` CSS
    selector never actually filters it out; the class is checked explicitly.
    Generic candidates are kept as a fallback in case PF's markup changes again.
    """
    primary = page.query_selector("[data-element='pager-btn-next']")
    if primary is not None:
        try:
            classes = primary.get_attribute("class") or ""
            if "disabled" not in classes and primary.is_visible():
                safe_click_handle(page, primary)
                time.sleep(2)
                visible(page, "[data-element='pager-label']", DEFAULT_TIMEOUT)
                return True
        except Exception:
            pass

    candidates = [
        "button[data-element*='next']:not([disabled])",
        "a[data-element*='next']:not([disabled])",
        "button[aria-label*='Next']:not([disabled])",
        "a[aria-label*='Next']:not([disabled])",
        "button[title*='Next']:not([disabled])",
        "a[title*='Next']:not([disabled])",
    ]
    for css in candidates:
        for el in page.query_selector_all(css):
            try:
                if el.is_visible() and el.is_enabled():
                    safe_click_handle(page, el)
                    time.sleep(2)
                    visible(page, "[data-element='pager-label']", DEFAULT_TIMEOUT)
                    return True
            except Exception:
                continue

    # Text-based fallback.
    xpaths = [
        "xpath=//button[not(@disabled) and (normalize-space()='Next' or contains(normalize-space(), 'Next'))]",
        "xpath=//a[normalize-space()='Next' or contains(normalize-space(), 'Next')]",
        "xpath=//button[not(@disabled)]//*[contains(@class,'next')]/ancestor::button",
    ]
    for xp in xpaths:
        for el in page.query_selector_all(xp):
            try:
                if el.is_visible() and el.is_enabled():
                    safe_click_handle(page, el)
                    time.sleep(2)
                    return True
            except Exception:
                continue

    return False


def scroll_and_paginate_schedule_day(
    page: Page,
    config: Optional[ScheduleScrapeConfig] = None,
    expected: Optional[int] = None,
) -> List[ReportPatient]:
    """scroll_schedule_day_and_collect, but also clicks through NUMBERED pages
    if Practice Fusion shows one for this day's Schedule list.

    This is a SEPARATE concern from the virtualized-scroll fix in
    scroll_schedule_day_and_collect: that one handles "more rows than render
    into the DOM at once on ONE page"; this one handles "more rows than fit on
    one page at all, with a Next control to click through". A busy day could
    hit either, or both.

    Reuses get_pager_range/click_next_report_page AS-IS -- the same generic
    'pager-label'/'pager-btn-next' widget already confirmed live for the
    Patient List Report and Appointment Report pages, not a new selector
    guess. This is NOT independently confirmed against the Schedule
    Appointments view specifically, though: if no pager-label element is found
    at all, this assumes the day genuinely has just one page (a single
    virtualized list, which is what's been observed for normal daily
    appointment volumes) and behaves exactly like calling
    scroll_schedule_day_and_collect alone -- zero behavior change for that
    case, including on every day scraped so far. If Schedule ever turns out to
    use a DIFFERENTLY-named pager control, this silently won't catch it --
    discover_appointments_via_schedule_range's own "header says N, only
    scraped M" warning is the signal that something is still being missed
    beyond what scrolling alone fixed.
    """
    config = config or ScheduleScrapeConfig()
    collected: Dict[str, ReportPatient] = {}

    def merge(rows: List[ReportPatient]) -> None:
        for r in rows:
            key = _schedule_row_key(r)
            if key.strip("|"):
                collected[key] = r

    start, end, total = get_pager_range(page)
    if start is None:
        # No pager label found at all -- treat as a single page, no separate
        # "Next" control to click through.
        return scroll_schedule_day_and_collect(page, config, expected=expected)

    expected_page = (end - start + 1) if (start is not None and end is not None) else None
    merge(scroll_schedule_day_and_collect(page, config, expected=expected_page))

    guard = 0
    while end is not None and total is not None and end < total and guard < 300:
        guard += 1
        prev_start = start
        if not click_next_report_page(page):
            break
        for _ in range(20):
            start, end, total = get_pager_range(page)
            if start is not None and start != prev_start:
                break
            time.sleep(0.25)
        if start is None or start == prev_start:
            break  # no progress -> stop
        expected_page = (end - start + 1) if (start is not None and end is not None) else None
        merge(scroll_schedule_day_and_collect(page, config, expected=expected_page))

    return list(collected.values())


def load_checkpoint(path: str) -> dict:
    if not (path and os.path.exists(path)):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_checkpoint(path: str, data: dict) -> None:
    """Atomic write so a crash mid-save can't corrupt the checkpoint."""
    if not path:
        return
    try:
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f)
            f.flush()
        os.replace(tmp, path)
    except Exception:
        pass


def _rp_key(p: "ReportPatient") -> str:
    return p.ehr_patient_guid or f"{p.patient_id}|{p.dob}|{p.first_name}|{p.last_name}"


def collect_current_result_set(page: Page, sink=None) -> List[ReportPatient]:
    """Collects every row of the report currently displayed.

    Single page (e.g. '1 - 38 of 38'): scroll-scrape once. Multi-page: click
    through, using the pager's advancing 'A - B of C' range both to know how
    many rows each page should have and to detect when to stop.

    `sink`, if given, is called with the running list of rows after each page,
    so the caller can checkpoint progress per page.
    """
    collected: Dict[str, ReportPatient] = {}

    def merge(rows: List[ReportPatient]) -> None:
        for p in rows:
            collected[_rp_key(p)] = p
        if sink is not None:
            try:
                sink(list(collected.values()))
            except Exception:
                pass

    start, end, total = get_pager_range(page)
    expected_page = (end - start + 1) if (start is not None and end is not None) else None
    merge(scroll_report_and_collect(page, expected=expected_page))

    # Only paginate if the current page doesn't already cover everything.
    guard = 0
    while (end is not None and total is not None and end < total and guard < 300):
        guard += 1
        prev_start = start
        prev_first_row_sig = _first_row_signature(page)
        if not click_next_report_page(page):
            break

        # Wait for the pager LABEL to advance...
        for _ in range(20):
            start, end, total = get_pager_range(page)
            if start is not None and start != prev_start:
                break
            time.sleep(0.25)
        if start is None or start == prev_start:
            break  # no progress -> stop

        # ...and separately confirm the TABLE BODY actually repainted. Confirmed
        # live 2026-08-10: the pager-label can update one render tick before the
        # tbody does, and scraping right after the label-only change silently
        # re-reads the previous page's rows -- every other "page" collapsed into
        # its predecessor via GUID dedup, undercounting by roughly half.
        for _ in range(20):
            if _first_row_signature(page) != prev_first_row_sig:
                break
            time.sleep(0.25)
        else:
            print("    table rows did not repaint after pager advanced; scraping anyway.", flush=True)

        expected_page = (end - start + 1) if (start is not None and end is not None) else None
        merge(scroll_report_and_collect(page, expected=expected_page))
        print(f"    page rows {start}-{end}/{total} (collected {len(collected)})", flush=True)

    return list(collected.values())


def collect_all_report_patients_bucketed(
    page: Page,
    start: int,
    stop: int,
    size: int,
    max_empty: int,
    cap: int = 1000,
    limit: int = 0,
    checkpoint_path: str = "",
) -> List[ReportPatient]:
    """
    Runs the report across successive age windows (size-year buckets) to stay
    under PF's result cap, merging everyone found. A bucket returning >= cap
    (1000) is treated as TRUNCATED and re-run in 1-year sub-windows.

    Resumable: collected GUIDs and completed buckets are written to
    `checkpoint_path` after every page and every bucket. If a run stops midway,
    the next run restores what was collected and continues from the next bucket
    instead of re-sweeping from the start.
    """
    all_patients: Dict[str, ReportPatient] = {}
    completed: set = set()
    empty_streak = 0

    # --- resume from checkpoint if params match ---
    ckpt = load_checkpoint(checkpoint_path)
    params = {"age_from": start, "age_to": stop, "size": size}
    if ckpt and ckpt.get("params") == params:
        for guid, d in (ckpt.get("collected") or {}).items():
            try:
                all_patients[guid] = ReportPatient(**d)
            except Exception:
                pass
        completed = {tuple(b) for b in (ckpt.get("completed_buckets") or [])}
        if all_patients or completed:
            print(f"Resuming sweep: {len(all_patients)} patients already collected, "
                  f"{len(completed)} buckets done.", flush=True)

    def flush(sweep_complete: bool = False) -> None:
        save_checkpoint(checkpoint_path, {
            "version": 1,
            "params": params,
            "sweep_complete": sweep_complete,
            "completed_buckets": [list(b) for b in completed],
            "collected": {g: p.__dict__ for g, p in all_patients.items()},
        })

    def merge(rows: List[ReportPatient]) -> None:
        for p in rows:
            key = _rp_key(p)
            if key.strip("|"):
                all_patients[key] = p

    def sink(rows: List[ReportPatient]) -> None:
        merge(rows)
        flush()  # per-page checkpoint

    def collect_range(lo: int, hi: int, depth: int = 0) -> None:
        """Runs [lo,hi]; if truncated (>= cap), splits in half and recurses so
        every collected range is under PF's result cap."""
        indent = "  " + "  " * depth
        total = run_age_bucket(page, lo, hi)
        label = f"age {lo}-{hi}"

        if not total:
            print(f"{indent}[{label}] 0 patients (empty)", flush=True)
            return

        if total < cap:
            rows = collect_current_result_set(page, sink=sink)
            merge(rows)
            print(f"{indent}[{label}] {total} patients (+{len(rows)}, running total {len(all_patients)})", flush=True)
            return

        # total >= cap -> truncated by PF.
        if lo >= hi:
            # Single year already; can't split age further. Grab what we can.
            rows = collect_current_result_set(page, sink=sink)
            merge(rows)
            print(f"{indent}[{label}] {total}+ TRUNCATED at a single year — PF caps here; "
                  f"collected {len(rows)} (some rows unreachable via age).", flush=True)
            return

        mid = (lo + hi) // 2
        print(f"{indent}[{label}] {total}+ (TRUNCATED); splitting -> {lo}-{mid} / {mid+1}-{hi}", flush=True)
        collect_range(lo, mid, depth + 1)
        collect_range(mid + 1, hi, depth + 1)

    for lo, hi in iter_age_buckets(start, stop, size):
        if (lo, hi) in completed:
            continue  # already collected in a previous run

        before = len(all_patients)
        collect_range(lo, hi)
        got = len(all_patients) - before

        if got == 0:
            empty_streak += 1
            completed.add((lo, hi))
            flush()
            if max_empty > 0 and empty_streak >= max_empty:
                print(f"  Stopping: {max_empty} consecutive empty buckets.", flush=True)
                break
            continue

        empty_streak = 0
        completed.add((lo, hi))
        flush()

        if limit and len(all_patients) >= limit:
            print(f"  Reached limit of {limit}; stopping sweep early (test mode).", flush=True)
            break

    flush(sweep_complete=True)
    return list(all_patients.values())


def scroll_profile_to_load_sections(page: Page, step_pause: float = 0.1) -> None:
    # PF loads profile sections as you scroll. Step through the page.
    try:
        page.evaluate("() => window.scrollTo(0, 0)")
        total_height = page.evaluate(
            "() => document.body.scrollHeight || document.documentElement.scrollHeight"
        )
        viewport = page.evaluate("() => window.innerHeight") or 800
        pos = 0
        while pos < total_height:
            pos += int(viewport * 0.9)
            page.evaluate("y => window.scrollTo(0, y)", pos)
            time.sleep(step_pause)
            total_height = page.evaluate(
                "() => document.body.scrollHeight || document.documentElement.scrollHeight"
            )
        page.evaluate("() => window.scrollTo(0, 0)")
        time.sleep(0.5)
    except Exception:
        pass


def scrape_insurance_card(card: ElementHandle) -> Dict[str, str]:
    label = safe_text_by_data(card, "plan-payment-preference")
    payer = safe_text_by_data(card, "payer-name")
    plan_name = safe_text_by_data(card, "plan-name")
    plan_type = safe_text_by_data(card, "plan-type")
    insured_id = safe_text_by_data(card, "insured-id")
    copay = safe_text_by_data(card, "plan-copay")
    effective = safe_text_by_data(card, "effective-range")
    start, end = parse_effective_range(effective)
    eligibility = (
        safe_text_by_data(card, "text-eligibility-unavailable")
        or safe_text_by_data(card, "eligibility-status")
        or safe_text(card, "[data-element*='eligibility']")
    )
    coverage_type = safe_text_by_data(card, "coverage-type-value")

    return {
        "label": label,
        "payer_name": payer,
        "plan_name": plan_name,
        "plan_type": plan_type,
        "insured_id": insured_id,
        "copay": copay,
        "effective_raw": effective,
        "effective_start_date": start,
        "effective_end_date": end,
        "eligibility_status": eligibility,
        "coverage_type": coverage_type,
    }


def scrape_insurances(page: Page, include_inactive: bool = True) -> List[Dict[str, str]]:
    cards = page.query_selector_all("li[data-element^='insurance-plan-']")
    insurances = [scrape_insurance_card(card) for card in cards]

    if include_inactive:
        try:
            btn = page.query_selector("[data-element='btn-toggle-inactive']")
            if btn is not None and btn.is_visible() and "show inactive" in el_text(btn).lower():
                safe_click_handle(page, btn)
                time.sleep(1)
                cards = page.query_selector_all("li[data-element^='insurance-plan-']")
                expanded = [scrape_insurance_card(card) for card in cards]
                # Merge without duplicates.
                seen = {json.dumps(i, sort_keys=True) for i in insurances}
                for item in expanded:
                    key = json.dumps(item, sort_keys=True)
                    if key not in seen:
                        insurances.append(item)
                        seen.add(key)
        except Exception:
            pass

    return [i for i in insurances if any(i.values())]


def find_insurance(insurances: List[Dict[str, str]], rank: str) -> Dict[str, str]:
    rank_l = rank.lower()
    for ins in insurances:
        if rank_l in (ins.get("label") or "").lower():
            return ins
    if rank_l == "primary" and insurances:
        return insurances[0]
    if rank_l == "secondary" and len(insurances) > 1:
        return insurances[1]
    return {}


SUMMARY_SECTIONS = [
    ("flowsheet-card", "flowsheets"),
    ("diagnoses-summary-card", "diagnoses"),
    ("patient-risk-score-card", "patient_risk_score"),
    ("social-history-card", "social_history"),
    ("family-health-history-card", "family_health_history"),
    ("past-medical-history-card", "past_medical_history"),
    ("advanced-directives-card", "advance_directives"),
    ("allergies-card", "allergies"),
    ("medication-summary-card", "medications"),
    ("implantable-devices-card", "implantable_devices"),
    ("health-concerns-card", "health_concerns"),
    ("goals-section-card", "goals"),
    # Deliberately excluded: sia-card (screenings), encounter-summary-card,
    # messages-card, appointment-list-card.
]


def scrape_patient_summary(page: Page) -> Dict[str, str]:
    """Scrapes the Summary tab cards into {section: text}, skipping the
    excluded sections. Assumes the Summary tab is currently loaded."""
    result: Dict[str, str] = {}
    for data_element, key in SUMMARY_SECTIONS:
        try:
            card = page.query_selector(f"[data-element='{data_element}']")
            if card is None:
                continue
            content = card.query_selector(".card__content") or card
            text = el_text(content)
            if text:
                result[key] = text
        except Exception:
            continue
    return result


def close_active_chart_tab(page: Page, guid: str = "") -> None:
    """Closes the PF patient chart tab so charts don't pile up in memory.
    PF's close button is a span with data-element='close-patient-<GUID>'."""
    selectors = []
    if guid:
        selectors.append(f"[data-element='close-patient-{guid}']")
    selectors += [
        "[data-element^='close-patient-']",
        "xpath=//span[starts-with(@data-element,'close-patient-')]",
    ]
    for sel in selectors:
        try:
            # Close all matching chart tabs (not just the active one) to be safe.
            handles = page.query_selector_all(sel)
            if not handles:
                continue
            for el in handles:
                try:
                    el.click()
                    time.sleep(0.1)
                except Exception:
                    try:
                        el.evaluate("e => e.click()")
                    except Exception:
                        pass
            return
        except Exception:
            continue


def scrape_patient_note(page: Page) -> Dict[str, str]:
    """Scrapes the pinned note + free-text note if present; empty dict if none."""
    note: Dict[str, str] = {}
    try:
        placeholder = safe_text_by_data(page, "pinned-note-placeholder")
        body = safe_text_by_data(page, "pinned-note-body")
        edited_by = safe_text_by_data(page, "pinned-note-edited-by")
        last_mod = safe_text_by_data(page, "pinned-note-last-modified")
        # Free-text note is a textarea; read its value via the DOM.
        note_text = ""
        try:
            ta = page.query_selector("[data-element='txt-patient-note']")
            if ta is not None:
                note_text = clean(ta.input_value() or "")
        except Exception:
            pass

        if body and body.lower() not in ("", "no pinned note for this patient"):
            note["pinned_note"] = body
        if edited_by:
            note["pinned_note_edited_by"] = edited_by
        if last_mod:
            note["pinned_note_last_modified"] = last_mod
        if note_text:
            note["note"] = note_text
    except Exception:
        pass
    return note


def scrape_patient_profile(page: Page, report_row: ReportPatient, args: argparse.Namespace, scrape_run_id: str) -> Dict[str, str]:
    guid = report_row.ehr_patient_guid
    if guid:
        base = f"{EHR_BASE_URL}#/PF/charts/patients/{guid}"
        summary_url = base + "/summary"
        url = base + "/profile"
    elif report_row.ehr_patient_url:
        base = report_row.ehr_patient_url.rstrip("/")
        for suffix in ("/summary", "/profile", "/timeline", "/documents"):
            if base.endswith(suffix):
                base = base[: -len(suffix)]
                break
        summary_url = base + "/summary"
        url = base + "/profile"
    else:
        raise RuntimeError("Patient row has no chart URL/GUID")

    # --- Summary tab: capture the clinical summary sections as JSON ---
    summary_data: Dict[str, str] = {}
    if not getattr(args, "no_summary", False):
        try:
            page.goto(summary_url, wait_until="domcontentloaded")
            try:
                page.wait_for_selector(
                    "[data-element='allergies-card'], [data-element='flowsheet-card']",
                    state="visible",
                    timeout=DEFAULT_TIMEOUT,
                )
            except Exception:
                pass
            scroll_profile_to_load_sections(page)
            summary_data = scrape_patient_summary(page)
        except Exception:
            summary_data = {}

    # --- Profile tab: demographics, contact, address, insurance, note ---
    expected_last = clean(report_row.last_name).lower()
    page.goto(url, wait_until="domcontentloaded")
    # Wait until the profile shows THIS patient (name matches the report row),
    # rather than a fixed networkidle wait. Resolves in a fraction of a second
    # when loaded, and guarantees we're not reading the previous patient.
    deadline = time.time() + 10
    while time.time() < deadline:
        nm = safe_text_by_data(page, "full-name").lower()
        if nm and (not expected_last or expected_last in nm):
            break
        time.sleep(0.2)
    visible(page, "[data-element='full-name']", DEFAULT_TIMEOUT)
    scroll_profile_to_load_sections(page)

    # Top-level profile data
    patient_name = safe_text_by_data(page, "full-name") or clean(f"{report_row.first_name} {report_row.last_name}")
    sex = safe_text_by_data(page, "gender-text") or report_row.sex
    dob = safe_text_by_data(page, "birth-date-text") or report_row.dob
    status = safe_text_by_data(page, "is-active-text") or report_row.status
    record_number = safe_text_by_data(page, "prn-text") or report_row.patient_id

    address_line_1 = safe_text_by_data(page, "address1")
    address_line_2 = safe_text_by_data(page, "address2")
    city, state, zip_code = parse_city_state_zip(safe_text_by_data(page, "city-state-zip"))

    # Insurance
    insurances = scrape_insurances(page, include_inactive=not args.skip_inactive_insurance)
    primary = find_insurance(insurances, "Primary")
    secondary = find_insurance(insurances, "Secondary")
    inactive = [i for i in insurances if "inactive" in (i.get("label", "") + " " + i.get("eligibility_status", "")).lower()]

    # Guarantor
    guarantor_name_relation = safe_text_by_data(page, "guarantor-name-relation")
    guarantor_name = guarantor_name_relation
    guarantor_relation = ""
    if "|" in guarantor_name_relation:
        parts = [clean(x) for x in guarantor_name_relation.split("|", 1)]
        guarantor_name = parts[0]
        guarantor_relation = parts[1] if len(parts) > 1 else ""

    guarantor_city, guarantor_state, guarantor_zip = parse_city_state_zip(safe_text_by_data(page, "guarantor-city-state-zip"))
    pharm_city, pharm_state, pharm_zip = parse_city_state_zip(safe_text_by_data(page, "pharmacy-city-state-zip"))

    raw_patient = {
        "report_row": report_row.__dict__,
        "profile_url": url,
        "patient_name": patient_name,
        "sex": sex,
        "dob": dob,
        "status": status,
        "record_number": record_number,
    }

    insurance_error = ""
    insurance_scraped = "1" if insurances else "0"
    if not insurances:
        insurance_error = "No insurance cards found"

    effective_start = primary.get("effective_start_date", "") or secondary.get("effective_start_date", "")
    effective_end = primary.get("effective_end_date", "") or secondary.get("effective_end_date", "")

    row = {col: "" for col in CSV_COLUMNS}
    row.update(
        {
            "id": guid or report_row.ehr_patient_guid,
            "report_signature": report_signature(report_row),
            "ehr_name": "practice_fusion",
            "patient_id": record_number or report_row.patient_id,
            "entity": "patient",
            "sub_entity": "profile",
            "practice": args.practice or "",
            "patient_name": patient_name,
            "dob": dob,
            "sex": sex,
            "marital_status": "",
            "email": safe_text_by_data(page, "email"),
            "home_phone": safe_text_by_data(page, "phone-home"),
            "mobile_phone": safe_text_by_data(page, "phone-mobile"),
            "address_line_1": address_line_1,
            "city": city,
            "state": state,
            "zip_code": zip_code,
            "status": status,
            "primary_insurance_id": primary.get("insured_id", ""),
            "secondary_insurance_id": secondary.get("insured_id", ""),
            "insurance_scraped": insurance_scraped,
            "insurance_scrape_error": insurance_error,
            "created_date": now_iso(),
            "updated_date": now_iso(),
            "primary_insurance_name": primary.get("payer_name", ""),
            "secondary_insurance_name": secondary.get("payer_name", ""),
            "effective_start_date": effective_start,
            "effective_end_date": effective_end,
            "primary_plan_name": primary.get("plan_name", ""),
            "secondary_plan_name": secondary.get("plan_name", ""),
            "post_op": "",
            "behavioral_postop": "",
            "ehr_patient_guid": report_row.ehr_patient_guid,
            "ehr_patient_url": url,
            "record_number": record_number,
            "age_at_scrape": report_row.age,
            "preferred_contact": safe_text_by_data(page, "communication-method") or report_row.preferred_contact,
            "work_phone": safe_text_by_data(page, "phone-work"),
            "address_line_2": address_line_2,
            "payment_preference": safe_text_by_data(page, "payment-preference"),
            "primary_coverage_type": primary.get("coverage_type", ""),
            "primary_plan_type": primary.get("plan_type", ""),
            "primary_copay": primary.get("copay", ""),
            "primary_eligibility_status": primary.get("eligibility_status", ""),
            "secondary_coverage_type": secondary.get("coverage_type", ""),
            "secondary_plan_type": secondary.get("plan_type", ""),
            "secondary_copay": secondary.get("copay", ""),
            "secondary_eligibility_status": secondary.get("eligibility_status", ""),
            "eligibility_last_checked": safe_text_by_data(page, "last-eligibility-check-date"),
            "inactive_insurance_count": str(len(inactive)) if inactive else "",
            "inactive_insurance_json": json.dumps(inactive, ensure_ascii=False),
            "guarantor_name": guarantor_name,
            "guarantor_relation": guarantor_relation,
            "guarantor_dob": safe_text_by_data(page, "guarantor-dob"),
            "guarantor_sex": safe_text_by_data(page, "guarantor-sex"),
            "guarantor_primary_phone": safe_text_by_data(page, "guarantor-primary-phone"),
            "guarantor_secondary_phone": safe_text_by_data(page, "guarantor-secondary-phone"),
            "guarantor_address_line_1": safe_text_by_data(page, "guarantor-street-address"),
            "guarantor_address_line_2": "",
            "guarantor_city": guarantor_city,
            "guarantor_state": guarantor_state,
            "guarantor_zip_code": guarantor_zip,
            "preferred_pharmacy_name": safe_text_by_data(page, "pharmacy-name-location"),
            "preferred_pharmacy_phone": safe_text_by_data(page, "pharmacy-office-phone"),
            "preferred_pharmacy_fax": safe_text_by_data(page, "pharmacy-office-fax"),
            "preferred_pharmacy_address_1": safe_text_by_data(page, "pharmacy-address-1"),
            "preferred_pharmacy_city": pharm_city,
            "preferred_pharmacy_state": pharm_state,
            "preferred_pharmacy_zip_code": pharm_zip,
            "scrape_source": "rpa_chrome_profile",
            "scrape_run_id": scrape_run_id,
            "last_scraped_at": now_iso(),
            "patient_note_json": json.dumps(scrape_patient_note(page), ensure_ascii=False),
            "summary": json.dumps(summary_data, ensure_ascii=False),
            "raw_patient_json": json.dumps(raw_patient, ensure_ascii=False),
            "raw_insurance_json": json.dumps(insurances, ensure_ascii=False),
        }
    )

    if args.include_ssn:
        # Not in CSV_COLUMNS by default to avoid accidental SSN exports.
        ssn = safe_text_by_data(page, "ssn-text")
        row["raw_patient_json"] = json.dumps({**raw_patient, "ssn_text": ssn}, ensure_ascii=False)

    return row


def report_signature(rp: "ReportPatient") -> str:
    """Cheap change-detector from report-row fields (no chart load needed)."""
    return "|".join([
        clean(rp.last_name), clean(rp.first_name), clean(rp.dob),
        clean(rp.sex), clean(rp.status), clean(rp.preferred_contact),
        clean(rp.patient_id),
    ])


def load_existing_rows(path: str) -> Dict[str, Dict[str, str]]:
    """Loads existing CSV rows keyed by patient GUID (id/ehr_patient_guid)."""
    out: Dict[str, Dict[str, str]] = {}
    if not (os.path.exists(path) and os.path.getsize(path) > 0):
        return out
    try:
        with open(path, newline="", encoding="utf-8-sig") as f:
            for r in csv.DictReader(f):
                key = (r.get("id") or r.get("ehr_patient_guid") or "").strip()
                if key:
                    out[key] = r
    except Exception:
        pass
    return out


def rewrite_csv(path: str, rows_by_guid: Dict[str, Dict[str, str]]) -> None:
    """Writes the whole CSV fresh with the current columns (dedups by GUID)."""
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_COLUMNS, extrasaction="ignore")
        writer.writeheader()
        for row in rows_by_guid.values():
            writer.writerow(row)
        f.flush()


def write_csv_header_if_needed(path: str) -> None:
    exists = os.path.exists(path) and os.path.getsize(path) > 0
    if not exists:
        with open(path, "w", newline="", encoding="utf-8-sig") as f:
            writer = csv.DictWriter(f, fieldnames=CSV_COLUMNS, extrasaction="ignore")
            writer.writeheader()


def append_csv_row(path: str, row: Dict[str, str]) -> None:
    with open(path, "a", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_COLUMNS, extrasaction="ignore")
        writer.writerow(row)
        f.flush()


def save_report_seed(path: str, patients: Iterable[ReportPatient]) -> None:
    seed_path = re.sub(r"\.csv$", "_report_seed.csv", path, flags=re.I)
    with open(seed_path, "w", newline="", encoding="utf-8-sig") as f:
        cols = list(ReportPatient().__dict__.keys())
        writer = csv.DictWriter(f, fieldnames=cols)
        writer.writeheader()
        for p in patients:
            writer.writerow(p.__dict__)
    print(f"Saved report seed rows: {seed_path}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Practice Fusion patient list/profile scraper to CSV (Playwright)")
    parser.add_argument("--username", default=os.getenv("PF_USERNAME", ""), help="Practice Fusion username/email. Prefer the PF_USERNAME environment variable (loaded from the repo-root .env).")
    parser.add_argument("--password", default=os.getenv("PF_PASSWORD", ""), help="Practice Fusion password. Prefer PF_PASSWORD so it is not stored in shell history.")
    parser.add_argument("--attach", action="store_true", help="Attach to a Chrome you already launched (with --remote-debugging-port) and logged into. Skips launching, cloning, and login.")
    parser.add_argument("--chrome-user-data-dir", default="", help="DEDICATED Chrome user-data dir (launch mode only), e.g. C:\\Users\\you\\pf_rpa_chrome")
    parser.add_argument("--profile-directory", default="", help="Optional profile subdir inside the dedicated dir. Leave unset to use the cloned Default profile.")
    parser.add_argument("--source-user-data-dir", default="", help="Real Chrome User Data dir to clone the logged-in session from, e.g. C:\\Users\\you\\AppData\\Local\\Google\\Chrome\\User Data")
    parser.add_argument("--source-profile", default="Default", help="Profile folder inside the source dir to clone, e.g. 'Profile 11'")
    parser.add_argument("--refresh-profile", action="store_true", help="Re-clone the source profile even if the dedicated dir already exists (use when the session expired).")
    parser.add_argument("--chrome-exe", default="", help="Path to chrome.exe if not auto-detected.")
    parser.add_argument("--debug-port", default="9222", help="DevTools port for the attached Chrome (default 9222).")
    parser.add_argument("--practice", default="", help="Practice name")
    parser.add_argument("--ehr-name", default="practice_fusion", help="EHR identifier for the queue scope.")
    parser.add_argument("--group", dest="group_name", default="", help="Parent org / MSO group name.")
    parser.add_argument("--entity", default="patient", help="Entity type (patient, provider, ...).")
    parser.add_argument("--sub-entity", default="", help="Optional sub-entity refinement.")
    parser.add_argument("--mode", choices=["discover", "discover-by-date", "scrape", "both"], default="both",
                        help="discover = full age-bucket roster sweep, fills the queue; "
                             "discover-by-date = walk the Schedule 'Appointments' view for --start-date..--end-date "
                             "only (faster, GUIDs only for patients with an appointment in that window; requires "
                             "--start-date/--end-date); scrape = drain the queue; both = discover then scrape.")
    parser.add_argument("--start-date", default="", help="discover-by-date: first date to walk, YYYY-MM-DD.")
    parser.add_argument("--end-date", default="", help="discover-by-date: last date to walk (inclusive), YYYY-MM-DD.")
    parser.add_argument("--queue-dsn", default=os.getenv("RPA_QUEUE_DSN", ""), help="Postgres DSN for the queue (service). If empty, uses --queue-file.")
    parser.add_argument("--queue-file", default="", help="Local JSON queue file (dev fallback when no DSN).")
    parser.add_argument("--job-id", default="", help="Job id for control/login-gate (auto-generated if empty).")
    parser.add_argument("--age-from", default="0", help="Age range lower bound (first bucket start)")
    parser.add_argument("--age-to", default="120", help="Age range upper bound (last bucket end)")
    parser.add_argument("--age-bucket-size", default="5", help="Years per age bucket (default 5) to stay under PF's 1000-row cap.")
    parser.add_argument("--max-empty-buckets", default="0", help="Stop after this many consecutive empty age buckets. 0 (default) = never stop early; sweep every bucket up to --age-to.")
    parser.add_argument("--out", default="practice_fusion_patients.csv", help="Output CSV path")
    parser.add_argument("--limit", type=int, default=0, help="Optional max patients to scrape; 0 means all")
    parser.add_argument("--rescrape-all", action="store_true", help="Re-scrape every patient even if unchanged (default skips patients whose report data is unchanged).")
    parser.add_argument("--no-summary", action="store_true", help="Skip the Summary tab scrape (one fewer page load per patient — roughly 2x faster).")
    parser.add_argument("--reset-checkpoint", action="store_true", help="Ignore/clear the sweep checkpoint and start the age sweep from scratch.")
    parser.add_argument("--flush-every", type=int, default=25, help="Reload the app every N patients to flush PF's accumulated open charts (0 disables).")
    parser.add_argument("--skip-inactive-insurance", action="store_true", help="Do not click Show inactive insurance")
    parser.add_argument("--include-ssn", action="store_true", help="Capture SSN inside raw_patient_json. Not recommended unless required.")
    parser.add_argument("--headless", action="store_true", help="Run Chrome headless. Not recommended for this EHR.")
    parser.add_argument("--keep-browser-open", action="store_true", help="Leave browser open after run")
    args = parser.parse_args()

    if not args.attach and not args.chrome_user_data_dir:
        print("Missing --chrome-user-data-dir (required unless --attach).", file=sys.stderr)
        return 2

    scrape_run_id = str(uuid.uuid4())
    write_csv_header_if_needed(args.out)

    # Queue store + scope + job id (login gate uses these).
    from pf_sync_pkg import rpa_queue
    scope = rpa_queue.Scope(
        ehr_name=args.ehr_name, practice=args.practice, group_name=args.group_name,
        entity=args.entity, sub_entity=args.sub_entity,
    )
    store = rpa_queue.make_queue(dsn=args.queue_dsn, file_path=args.queue_file or (args.out + ".queue.json"))
    job_id = args.job_id or scrape_run_id
    store.set_state(job_id, "starting", message="Launching browser.", scope=scope, mode=args.mode)

    pw = context = page = None
    pw, context, page = build_context(args)
    try:
        if args.attach:
            # You logged in manually in the Chrome you launched separately.
            if "practicefusion.com" not in (page.url or "").lower():
                page.goto(EHR_BASE_URL, wait_until="domcontentloaded")
                time.sleep(2)
            print("Confirming logged-in session...", flush=True)
            page = wait_logged_in(page, 30_000)
            print(f"Session ready. Active tab: {page.url}", flush=True)
        else:
            # Launch mode: open the login page, then wait on the control gate.
            print(f"Opening login page: {LOGIN_URL}", flush=True)
            try:
                page.goto(LOGIN_URL, wait_until="domcontentloaded")
            except Exception:
                pass
            # Replaces the terminal-only input(): a UI (or a local ENTER) releases it.
            ok = rpa_queue.wait_for_start_signal(
                store, job_id, scope, args.mode,
                message=("Log into Practice Fusion in the browser (username, password, "
                         "OTP if asked) until the dashboard is visible, then Continue."),
                interactive=not (args.queue_dsn),  # DSN present => service mode, no TTY
            )
            if not ok:
                print("Login was not confirmed; stopping.", flush=True)
                return 3

            # Pick up whichever tab is now logged in.
            found = find_logged_in_page(context)
            if found is not None:
                page = found
            else:
                print("Couldn't auto-detect the dashboard tab; waiting briefly...", flush=True)
                page = wait_logged_in(page, 20_000)
            try:
                page.bring_to_front()
            except Exception:
                pass
            print(f"Continuing. Active tab: {page.url}", flush=True)

        checkpoint_path = args.out + ".checkpoint.json"
        if args.reset_checkpoint:
            try:
                os.remove(checkpoint_path)
            except Exception:
                pass

        patients: List[ReportPatient] = []
        if args.mode == "discover-by-date":
            if not args.start_date or not args.end_date:
                print("Mode=discover-by-date requires both --start-date and --end-date (YYYY-MM-DD).", file=sys.stderr)
                return 2
            try:
                start_d = datetime.strptime(clean(args.start_date), "%Y-%m-%d").date()
                end_d = datetime.strptime(clean(args.end_date), "%Y-%m-%d").date()
            except ValueError:
                print("Mode=discover-by-date: --start-date/--end-date must be YYYY-MM-DD.", file=sys.stderr)
                return 2
            if end_d < start_d:
                print("Mode=discover-by-date: --end-date is before --start-date.", file=sys.stderr)
                return 2
            collected = discover_via_schedule_range(page, start_d, end_d)
            patients = list(collected.values())
            save_report_seed(args.out, patients)
            for p in patients:
                if not p.ehr_patient_guid:
                    continue
                store.upsert_target(
                    scope, p.ehr_patient_guid,
                    f"{EHR_BASE_URL}#/PF/charts/patients/{p.ehr_patient_guid}",
                    report_signature(p), scrape_run_id,
                )
            print(
                f"Schedule-range discovery complete: {len(patients)} unique patients "
                f"across {start_d.isoformat()}..{end_d.isoformat()}.",
                flush=True,
            )
            store.set_state(job_id, "done", message="Schedule-range discovery finished.",
                            stats=store.stats(scope), scope=scope, mode=args.mode)
            return 0

        if args.mode in ("discover", "both"):
            open_patient_list_report(page)
            patients = collect_all_report_patients_bucketed(
                page,
                start=int(args.age_from),
                stop=int(args.age_to),
                size=int(args.age_bucket_size),
                max_empty=int(args.max_empty_buckets),
                limit=int(args.limit) if args.limit else 0,
                checkpoint_path=checkpoint_path,
            )
            save_report_seed(args.out, patients)
            # Write every discovered target into the queue (idempotent upsert;
            # changed change_signature flips a row back to 'pending').
            for p in patients:
                if not p.ehr_patient_guid:
                    continue
                store.upsert_target(
                    scope, p.ehr_patient_guid,
                    f"{EHR_BASE_URL}#/PF/charts/patients/{p.ehr_patient_guid}",
                    report_signature(p), scrape_run_id,
                )
            print(f"Discovery complete: {len(patients)} targets queued.", flush=True)
            store.set_state(job_id, "running", message="Discovery done.",
                            stats=store.stats(scope), scope=scope, mode=args.mode)

        if args.mode == "discover":
            store.set_state(job_id, "done", message="Discovery-only run finished.",
                            stats=store.stats(scope), scope=scope, mode=args.mode)
            print("Mode=discover: queue filled; not scraping.", flush=True)
            return 0

        # ---- SCRAPE PHASE: drain the queue ----
        # Rebuild ReportPatient objects from claimed queue rows so the existing
        # scrape_patient_profile works unchanged.
        existing = load_existing_rows(args.out)
        merged: Dict[str, Dict[str, str]] = dict(existing)

        # Reclaim any rows a crashed worker left in_progress.
        try:
            store.reclaim_stale(900)
        except Exception:
            pass

        flush_every = int(args.flush_every) if args.flush_every else 0
        done_count = 0
        batch_i = 0
        while True:
            batch = store.claim_batch(scope, limit=(int(args.limit) if args.limit else 50), run_id=scrape_run_id)
            if not batch:
                break
            for qrow in batch:
                batch_i += 1
                guid = qrow.get("source_id")
                qid = qrow.get("queue_id")
                rp = ReportPatient(
                    ehr_patient_guid=guid,
                    ehr_patient_url=qrow.get("source_url", ""),
                )
                if flush_every and batch_i > 1 and (batch_i - 1) % flush_every == 0:
                    try:
                        page.reload(wait_until="domcontentloaded")
                        try:
                            page.wait_for_load_state("networkidle", timeout=15_000)
                        except Exception:
                            pass
                    except Exception:
                        pass
                try:
                    print(f"[{batch_i}] {guid}", flush=True)
                    row = scrape_patient_profile(page, rp, args, scrape_run_id)
                    append_csv_row(args.out, row)
                    if guid:
                        merged[guid] = row
                    store.mark_done(qid, result=None)  # payload also in CSV/patients table
                    close_active_chart_tab(page, guid)
                    done_count += 1
                except Exception as exc:
                    store.mark_error(qid, f"{type(exc).__name__}: {exc}")
                    print(f"  ERROR: {exc}", file=sys.stderr)
            if args.limit:  # test mode: one batch is enough
                break

        rewrite_csv(args.out, merged)
        try:
            os.remove(checkpoint_path)
        except Exception:
            pass
        store.set_state(job_id, "done", message=f"Scrape finished: {done_count} patients.",
                        stats=store.stats(scope), scope=scope, mode=args.mode)
        print(f"Done. Scraped {done_count}. CSV: {args.out}")
        return 0

    finally:
        if args.attach:
            # We attached to YOUR Chrome; leave it open, just disconnect.
            print("Detaching (your Chrome stays open).", flush=True)
            try:
                if pw is not None:
                    pw.stop()
            except Exception:
                pass
        elif args.keep_browser_open:
            print("Browser left open because --keep-browser-open was used.", flush=True)
            try:
                if pw is not None:
                    pw.stop()
            except Exception:
                pass
        else:
            # Ask Chrome to shut down GRACEFULLY so cookies / Local Storage /
            # the trusted-device token flush to disk. A hard kill (terminate)
            # loses them, which is why the 2FA prompt kept coming back.
            print("Closing Chrome cleanly (saving session)...", flush=True)
            try:
                if context is not None and page is not None:
                    session = context.new_cdp_session(page)
                    session.send("Browser.close")
            except Exception:
                pass
            try:
                if pw is not None:
                    pw.stop()
            except Exception:
                pass
            # Give Chrome a moment to exit on its own; only force-kill if it hangs.
            if _CHROME_PROC is not None:
                try:
                    _CHROME_PROC.wait(timeout=15)
                except Exception:
                    try:
                        _CHROME_PROC.terminate()
                    except Exception:
                        pass


if __name__ == "__main__":
    raise SystemExit(main())
