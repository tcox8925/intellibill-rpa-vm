import os
import time
import re
import json
import subprocess
from datetime import datetime
from typing import Optional, Tuple
import pdfplumber
from playwright.sync_api import sync_playwright
from NIPR.azure_blob_utils import authenticate_blob_storage, upload_file_to_blob
import NIPR.zoho_crm as zoho_crm



# ============================================================
#  CONFIG
# ============================================================

NIPR_USERNAME = os.getenv("NIPR_USERNAME", "")
NIPR_PASSWORD = os.getenv("NIPR_PASSWORD", "")

#Folder on the VM where Chrome will download the report
DOWNLOAD_DIR = r"C:\Users\myopsadmin\Downloads"

#Azure Blob container + prefix
BLOB_CONTAINER = "834analytics-dev"
BLOB_PREFIX = "raw/agent_license_update/NIPR"

# ============================================================
#  ADDRESS NORMALIZATION ENGINE
# ============================================================

STATE_MAP = {
    "AL": "Alabama","AK": "Alaska","AZ": "Arizona","AR": "Arkansas","CA": "California",
    "CO": "Colorado","CT": "Connecticut","DE": "Delaware","FL": "Florida","GA": "Georgia",
    "HI": "Hawaii","ID": "Idaho","IL": "Illinois","IN": "Indiana","IA": "Iowa",
    "KS": "Kansas","KY": "Kentucky","LA": "Louisiana","ME": "Maine","MD": "Maryland",
    "MA": "Massachusetts","MI": "Michigan","MN": "Minnesota","MS": "Mississippi",
    "MO": "Missouri","MT": "Montana","NE": "Nebraska","NV": "Nevada","NH": "New Hampshire",
    "NJ": "New Jersey","NM": "New Mexico","NY": "New York","NC": "North Carolina",
    "ND": "North Dakota","OH": "Ohio","OK": "Oklahoma","OR": "Oregon","PA": "Pennsylvania",
    "RI": "Rhode Island","SC": "South Carolina","SD": "South Dakota","TN": "Tennessee",
    "TX": "Texas","UT": "Utah","VT": "Vermont","VA": "Virginia","WA": "Washington",
    "WV": "West Virginia","WI": "Wisconsin","WY": "Wyoming"
}


def normalize_address(raw: Optional[str]):
    """
    Normalize PDB address text into structured components:
    - street, street2, city, state, state_full, zip, zip4, country
    Handles ZIP or ZIP+4, and pulls 2-letter state code using STATE_MAP.
    """
    if not raw:
        return None

    original = raw.strip()

    # Remove USA noise
    cleaned = raw.replace("U.S.A.", "").replace("USA", "").strip()

    # Step 1: ZIP or ZIP+4 at the end
    zip_code = None
    zip4 = None
    zip_match = re.search(r"(\d{5})(?:[-\s]?(\d{4}))?$", cleaned)
    if zip_match:
        zip_code = zip_match.group(1)
        zip4 = zip_match.group(2)
        cleaned = cleaned[:zip_match.start()].strip()

    parts = cleaned.split()

    # Step 2: STATE (2-letter code at end that matches STATE_MAP)
    state = None
    if parts:
        last_token = parts[-1].replace(",", "")
        if last_token in STATE_MAP:
            state = last_token
            cleaned = " ".join(parts[:-1]).rstrip(",")
            parts = cleaned.split()
        else:
            # try to find a state token anywhere near the end
            for token in reversed(parts):
                t = token.replace(",", "")
                if t in STATE_MAP:
                    state = t
                    # rebuild string without that token
                    idx = parts.index(token)
                    cleaned = " ".join(parts[:idx] + parts[idx+1:]).strip()
                    parts = cleaned.split()
                    break

    # Step 3: CITY (assume last token after stripping state)
    city = None
    if parts:
        city = parts[-1].replace(",", "")
        cleaned = " ".join(parts[:-1]).strip()
        parts = cleaned.split()

    # Step 4: STREET2 (APT/STE/UNIT etc)
    street2 = None
    unit_match = re.search(
        r"\b(?:APT|UNIT|STE|SUITE|BLDG|FL|FLOOR|RM|ROOM)\s*\w+\b",
        cleaned,
        re.I,
    )
    if unit_match:
        street2 = unit_match.group(0)
        cleaned = cleaned.replace(street2, "").strip()

    # Step 5: STREET = remaining
    street = cleaned.strip() or None

    return {
        "original": original,
        "street": street,
        "street2": street2,
        "city": city,
        "state": state,
        "state_full": STATE_MAP.get(state),
        "zip": zip_code,
        "zip4": zip4,
        "country": "USA",
    }


# ============================================================
#  LOA EXTRACTOR
# ============================================================

def extract_loa(block: str) -> Optional[str]:
    """
    Given a single license block (after "License Summary"), extract the
    first LOA line after the 'Line of Authority' header row.
    """
    if "Line of Authority" not in block:
        return None

    lines = block.splitlines()
    start_idx = None
    for i, ln in enumerate(lines):
        if ln.strip().startswith("Line of Authority"):
            start_idx = i
            break
    if start_idx is None:
        return None

    for ln in lines[start_idx + 1:]:
        clean = ln.strip()
        # skip header row
        if any(k in clean for k in ["Issue Date", "Status", "Status Reason"]):
            continue
        if clean:
            return clean

    return None


# ============================================================
#  PDB TEXT PARSER
# ============================================================

def parse_pdb_text(text: str) -> dict:
    data = {
        "name": None,
        "npn": None,
        "dob": None,
        "active_resident_states": [],
        "resident_states": [],
        "nonresident_states": [],
        "addresses": {
            "business": None,
            "mailing": None,
            "residence": None,
            "business_phone": None,
            "business_email": None,
            "fax": None,
            "date_updated": {
                "business": None,
                "mailing": None,
                "residence": None,
                "business_phone": None,
                "business_email": None,
                "fax": None,
            },
        },
        "licenses": [],
    }

    # -------- BASIC FIELDS --------
    m = re.search(r"Name:\s+(.+?)\s{2,}", text)
    if m:
        data["name"] = m.group(1).strip()

    npn = re.search(r"NPN:\s*(\d+)", text)
    if npn:
        data["npn"] = npn.group(1)

    dob = re.search(r"DOB:\s*(\d{2}/\d{2}/\d{4})", text)
    if dob:
        data["dob"] = dob.group(1)

    # -------- STATE LISTS --------
    def parse_state_list(label: str):
        mm = re.search(label + r":\s*([A-Z, ]+)", text)
        return [s.strip() for s in mm.group(1).split(",")] if mm else []

    data["active_resident_states"] = parse_state_list("Active Resident States")
    data["resident_states"] = parse_state_list("Resident Licensed States")
    data["nonresident_states"] = parse_state_list("Non-Resident Licensed States")

    # -------- ADDRESS HELPERS --------
    def extract_latest(pattern: str) -> Tuple[Optional[str], Optional[str]]:
        """
        Returns (date, address_string) for the latest entry
        matching the pattern where pattern captures (date, value).
        """
        matches = re.findall(pattern, text)
        if not matches:
            return None, None
        latest = sorted(matches, key=lambda x: x[0], reverse=True)[0]
        return latest[0], latest[1].strip()

    # BUSINESS ADDRESS (generic address line w/ U.S.A. + ZIP)
    du, val = extract_latest(
        r"(\d{2}/\d{2}/\d{4})\s+([0-9A-Z ,.\-]+U\.S\.A\.\s*\d+)"
    )
    data["addresses"]["date_updated"]["business"] = du
    data["addresses"]["business"] = normalize_address(val)

    # MAILING ADDRESS
    du, val = extract_latest(
        r"Date Updated Mailing Addresses:\s*\n(\d{2}/\d{2}/\d{4})\s+(.+?U\.S\.A\.\s*\d+)"
    )
    data["addresses"]["date_updated"]["mailing"] = du
    data["addresses"]["mailing"] = normalize_address(val)

    # RESIDENCE ADDRESS
    du, val = extract_latest(
        r"Date Updated Residence Addresses:\s*\n(\d{2}/\d{2}/\d{4})\s+(.+?U\.S\.A\.\s*\d+)"
    )
    data["addresses"]["date_updated"]["residence"] = du
    data["addresses"]["residence"] = normalize_address(val)

    # BUSINESS PHONE
    phone = re.findall(
        r"Date Updated Business Phone:\s*\n(\d{2}/\d{2}/\d{4})\s+([\d\-\(\) ]+)", text
    )
    if phone:
        du, val = sorted(phone, reverse=True)[0]
        data["addresses"]["date_updated"]["business_phone"] = du
        data["addresses"]["business_phone"] = val

    # BUSINESS EMAIL
    email = re.findall(
        r"Date Updated Business Email:\s*\n(\d{2}/\d{2}/\d{4})\s+([\w\.-]+@[\w\.-]+)",
        text,
    )
    if email:
        du, val = sorted(email, reverse=True)[0]
        data["addresses"]["date_updated"]["business_email"] = du
        data["addresses"]["business_email"] = val

    # FAX
    fax = re.findall(
        r"Date Updated Fax:\s*\n(\d{2}/\d{2}/\d{4})\s+([\d\-\(\) ]+)", text
    )
    if fax:
        du, val = sorted(fax, reverse=True)[0]
        data["addresses"]["date_updated"]["fax"] = du
        data["addresses"]["fax"] = val

    # -------- LICENSES --------
    license_blocks = re.split(r"License Summary", text)[1:]

    for block in license_blocks:
        m = re.search(
            r"State:\s*([A-Z]{2}).*?"
            r"License #:\s*(\d+).*?"
            r"Issue Date:\s*(\d{2}/\d{2}/\d{4}).*?"
            r"Expiration Date:\s*(\d{2}/\d{2}/\d{4}).*?"
            r"Residency:\s*([A-Z]{1,2}).*?"
            r"Active:\s*(Yes|No)",
            block,
            flags=re.S,
        )
        if not m:
            continue

        state, lic_no, issue, exp, residency, active = m.groups()

        loa = extract_loa(block)
        loa_lower = loa.lower() if loa else ""

        if "life" in loa_lower and ("health" in loa_lower or "hmo" in loa_lower):
            market = "Life and Health"
        elif "health" in loa_lower:
            market = "Health Only"
        elif "life" in loa_lower:
            market = "Life Only"
        else:
            market = None

        data["licenses"].append(
            {
                "state": state,
                "license_number": lic_no,
                "internal_id": f"{state}{lic_no}",
                "issue_date": issue,
                "expiration_date": exp,
                "residency": residency,
                "active": active == "Yes",
                "line_of_authority": loa,
                "market": market,
            }
        )

    return data

def is_no_longer_licensed_case(parsed: dict) -> bool:
    
    licenses = parsed.get("licenses", [])
    addresses = parsed.get("addresses", {})

    has_any_address = any(
        addresses.get(k)
        for k in ["business", "mailing", "residence"]
    )

    active_states = parsed.get("active_resident_states", [])
    resident_states = parsed.get("resident_states", [])
    nonresident_states = parsed.get("nonresident_states", [])


    has_real_states = any(
        s not in ("N", None, "")
        for s in (active_states + resident_states + nonresident_states)
    )

    name = parsed.get("name")
    dob = parsed.get("dob")

    return (
        len(licenses) == 0 and
        not has_any_address and
        not has_real_states and
        not name and
        not dob
    )


# ============================================================
#  CHROME VERSION DETECTION
# ============================================================

def get_chrome_version():
    """Detect installed Chrome major version on Windows."""
    try:
        result = subprocess.run(
            ['reg', 'query', r'HKEY_CURRENT_USER\Software\Google\Chrome\BLBeacon', '/v', 'version'],
            capture_output=True, text=True
        )
        match = re.search(r'(\d+)\.', result.stdout)
        if match:
            version = int(match.group(1))
            print(f"Detected Chrome version: {version}")
            return version
    except Exception as e:
        print(f"Chrome version detection failed: {e}")

    print("Falling back to Chrome version 145")
    return 145


# ============================================================
#  POPUP / COOKIE HELPERS
# ============================================================

def _close_extra_tabs(driver):
    """Close any popup tabs, keep the first window."""
    main_window = driver.window_handles[0]
    for handle in driver.window_handles[1:]:
        driver.switch_to.window(handle)
        driver.close()
    driver.switch_to.window(main_window)


def _dismiss_cookie_banner(driver):
    """Accept or dismiss Complianz cookie consent banner."""
    try:
        # Complianz-specific accept buttons
        selectors = [
            ".cmplz-btn.cmplz-accept",
            ".cm-btn-accept",
            ".cm-btn-dismiss",
            "#cmplz-cookiebanner-container .cmplz-accept",
            "button.cmplz-deny",
            "[data-testid='cookie-accept']",
        ]
        for sel in selectors:
            buttons = driver.find_elements(By.CSS_SELECTOR, sel)
            for btn in buttons:
                if btn.is_displayed():
                    btn.click()
                    print(f"Cookie banner accepted via: {sel}")
                    time.sleep(1)
                    return

        # Last resort: JS click on any visible accept-like button inside the wrapper
        result = driver.execute_script("""
            const wrapper = document.querySelector('.cm-wrapper');
            if (wrapper) {
                const btn = wrapper.querySelector(
                    'button[class*="accept"], button[class*="agree"], button'
                );
                if (btn) { btn.click(); return 'clicked'; }
                wrapper.remove();
                return 'removed';
            }
            return 'none';
        """)
        print(f"Cookie banner handled via JS fallback: {result}")
    except:
        pass


# ============================================================
#  NIPR BROWSER / DOWNLOAD
# ============================================================

def download_detail_report(npn: str, download_dir: str) -> str:
    """
    Use Playwright to:
      - Login to NIPR
      - Search by NPN
      - Pre-check Resident State
          * If Resident State is blank -> SKIP $1.30 report
          * Marks agent as 'No Longer Licensed' in Zoho
      - If valid -> Continue Generate + Download Detail Report PDF

    Returns:
        * Local PDF path if report was purchased
        * "NO_REPORT" if skipped
    """

    os.makedirs(download_dir, exist_ok=True)

    # cleanup old detail-report files
    for f in os.listdir(download_dir):
        if f.lower().startswith("detail-report") and f.lower().endswith(".pdf"):
            try:
                os.remove(os.path.join(download_dir, f))
            except Exception:
                pass

    print(f"Starting NIPR browser flow for NPN: {npn}")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False, args=[
            "--disable-gpu",
            "--no-sandbox",
            "--disable-dev-shm-usage",
        ])
        context = browser.new_context(
            viewport={"width": 1920, "height": 1080},
            accept_downloads=True,
        )
        page = context.new_page()

        try:
            # -------------------------------------------------
            # LOGIN
            # -------------------------------------------------
            page.goto("https://pdb-reports.app.nipr.com/home", wait_until="networkidle")

            print("Logging in...")
            page.fill("#username", NIPR_USERNAME)
            page.fill("#password", NIPR_PASSWORD)
            page.get_by_role("button", name="Log in").click()
            page.wait_for_load_state("networkidle")

            # ---- Dismiss cookie banner if present ----
            try:
                page.locator(".cmplz-btn.cmplz-accept").click(timeout=3000)
                print("Cookie banner accepted")
            except Exception:
                pass

            # ---- Close any popup tabs ----
            for extra_page in context.pages[1:]:
                extra_page.close()

            # -------------------------------------------------
            # NAVIGATE TO DETAIL REPORT
            # -------------------------------------------------
            print("Navigating to Create Report...")
            page.locator("a[href*='/create-report/detail-report']").click()
            page.wait_for_load_state("networkidle")

            # ---- Dismiss again in case it reappears ----
            try:
                page.locator(".cmplz-btn.cmplz-accept").click(timeout=2000)
            except Exception:
                pass
            for extra_page in context.pages[1:]:
                extra_page.close()

            # -------------------------------------------------
            # SEARCH BY NPN
            # -------------------------------------------------
            print("Entering NPN and searching...")
            page.locator("#npn").fill(npn)
            page.locator("button[data-testid='button_detail_person_search']").click()
            page.wait_for_load_state("networkidle")
            page.wait_for_timeout(3000)

            # -------------------------------------------------
            # RESIDENT STATE PRE-CHECK (SAVES $1.30)
            # -------------------------------------------------
            print("Checking Resident State before creating report...")

            try:
                resident_input = page.locator(
                    "xpath=//label[contains(text(),'Resident states')]/following-sibling::div//input"
                )
                resident_input.wait_for(state="attached", timeout=10000)
                resident_val = (resident_input.input_value() or "").strip()
            except Exception:
                resident_val = ""

            print(f"Resident state detected: '{resident_val}'")

            if resident_val == "" or resident_val in ("N", "None", "No licenses"):
                print("Resident State empty -> SKIPPING $1.30 report")

                try:
                    zoho_crm.update_agent_status_only(
                        npn=npn,
                        status="No Longer Licensed"
                    )
                    print("Zoho status updated -> No Longer Licensed")
                except Exception as ze:
                    print(f"Zoho status update failed: {ze}")

                return "NO_REPORT"

            # -------------------------------------------------
            # CONTINUE WITH PAID REPORT
            # -------------------------------------------------
            print("Clicking Create to generate Detail Report...")
            page.locator("button[data-testid='button_detail_create']").click()

            # -------------------------------------------------
            # WAIT FOR DOWNLOAD BUTTON + DOWNLOAD
            # -------------------------------------------------
            print("Waiting for Download button...")
            download_btn = page.locator("button[data-testid='button_detail_download']")
            download_btn.wait_for(state="visible", timeout=30000)

            print("Clicking Download...")
            with page.expect_download(timeout=60000) as download_info:
                download_btn.click()

            download = download_info.value
            pdf_path = os.path.join(download_dir, download.suggested_filename)
            download.save_as(pdf_path)

            print(f"Downloaded file: {pdf_path}")
            return pdf_path

        finally:
            try:
                browser.close()
                print("Browser closed")
            except Exception:
                pass


#  ORCHESTRATOR: DOWNLOAD + PARSE + UPLOAD
# ============================================================

def run_full_nipr(npn: str) -> dict:
    """
    Orchestrates the full NIPR detail flow:
      1. Download Detail Report PDF for the NPN (or skip if NO_REPORT)
      2. Rename PDF
      3. Parse PDF
      4. Upload PDF to Azure Blob
      5. Return summary dict (NO CRM calls)
    """

    print("==========================================")
    print(f"NIPR Full Detail Pull for NPN {npn}")
    print("==========================================")

    # --------------------------------------------------
    # STEP 1: DOWNLOAD (OR SKIP)
    # --------------------------------------------------
    local_pdf = download_detail_report(npn, DOWNLOAD_DIR)

    # SKIPPED CASE (No Longer Licensed)
    if local_pdf == "NO_REPORT":
        print(f"Skipped PDF generation for NPN {npn} (No Longer Licensed)")

        return {
            "success": True,
            "npn": npn,
            "skipped": True,
            "blob_path": None,
            "local_path": None,
            "parsed": None,
        }

    # HARD FAILURE CASE
    if not os.path.exists(local_pdf):
        raise FileNotFoundError(
            f"NIPR report expected at {local_pdf} but was not found."
        )

    # --------------------------------------------------
    # STEP 2: RENAME
    # --------------------------------------------------
    today = datetime.now().strftime("%Y%m%d")
    new_name = f"detail-report_{npn}_{today}.pdf"
    new_local_path = os.path.join(DOWNLOAD_DIR, new_name)

    try:
        os.replace(local_pdf, new_local_path)
    except Exception:
        import shutil
        shutil.copy2(local_pdf, new_local_path)
        os.remove(local_pdf)

    print(f"Renamed report -> {new_local_path}")

    # --------------------------------------------------
    # STEP 3: PARSE PDF
    # --------------------------------------------------
    print("Reading PDF text...")
    text = ""
    with pdfplumber.open(new_local_path) as pdf:
        for p in pdf.pages:
            txt = p.extract_text() or ""
            text += txt + "\n"

    print("Parsing PDB text...")
    parsed = parse_pdb_text(text)

    # --------------------------------------------------
    # STEP 4: UPLOAD TO AZURE
    # --------------------------------------------------
    print("Uploading PDF to Azure Blob Storage...")
    blob_service_client = authenticate_blob_storage()
    blob_path = f"{BLOB_PREFIX}/{new_name}"

    upload_file_to_blob(
        blob_service_client=blob_service_client,
        container_name=BLOB_CONTAINER,
        local_path=new_local_path,
        blob_path=blob_path,
    )

    os.remove(new_local_path)
    print(f"Uploaded to blob path: {blob_path}")

    # --------------------------------------------------
    # STEP 5: RETURN RESULT
    # --------------------------------------------------
    result = {
        "success": True,
        "npn": npn,
        "skipped": False,
        "blob_path": blob_path,
        "local_path": new_local_path,
        "parsed": parsed,
    }

    print("\n==================== PARSED DATA ====================")
    print(json.dumps(parsed, indent=4))

    return result

# ============================================================
#  NEW: FULL NIPR -> ZOHO WORKFLOW
# ============================================================

def run_full_nipr_and_update(npn: str) -> dict:
    base = run_full_nipr(npn)

    # --------------------------------------------------
    # SKIPPED -> STATUS ALREADY UPDATED IN BROWSER
    # --------------------------------------------------
    if base.get("skipped") is True:
        print(f"[Zoho] NPN {npn} already marked No Longer Licensed via pre-check")

        return {
            "success": True,
            "npn": npn,
            "skipped": True,
            "blob_path": None,
            "agent_status_update": True,
            "contact_update": None,
            "license_update": None,
        }

    parsed = base.get("parsed") or {}

    # --------------------------------------------------
    # NORMAL LICENSED FLOW
    # --------------------------------------------------
    contact_result = None
    license_result = None

    # --- Contact update ---
    try:
        print(f"[Zoho] Applying contact update for NPN {npn} ...")
        contact_result = zoho_crm.apply_contact_update_from_full_nipr(npn, parsed)
        print(f"[Zoho] Contact update done for NPN {npn}")
    except Exception as e:
        print(f"[Zoho] Contact update failed for NPN {npn}: {e}")
        contact_result = {
            "success": False,
            "error": str(e),
        }

    # --- License upsert ---
    try:
        print(f"[Zoho] Upserting licenses for NPN {npn} ...")
        license_result = zoho_crm.upsert_licenses_from_full_nipr(npn, parsed)
        print(f"[Zoho] License upsert done for NPN {npn}")
    except Exception as e:
        print(f"[Zoho] License upsert failed for NPN {npn}: {e}")
        license_result = {
            "success": False,
            "error": str(e),
        }

    return {
        "success": bool(base.get("success"))
                   and bool(contact_result and contact_result.get("success", True))
                   and bool(license_result and license_result.get("success", True)),
        "npn": npn,
        "skipped": False,
        "blob_path": base.get("blob_path"),
        "contact_update": contact_result,
        "license_update": license_result,
    }



if __name__ == "__main__":
    import sys

    TEST_MODE = False  # <-- switch to False for prod

    if TEST_MODE:
        # Hard-coded NPN for testing
        npn_arg = "18823623"    # change to any NPN you want
        print(f"TEST MODE ENABLED -- using NPN = {npn_arg}")
    else:
        # CLI mode
        if len(sys.argv) < 2:
            print("Usage: python nipr_full_report.py <NPN>")
            sys.exit(1)
        npn_arg = sys.argv[1]

    # Run the full NIPR flow (no Zoho) for direct CLI runs
    out = run_full_nipr(npn_arg)

    # Print summary (excluding full parsed content)
    print("\n==================== SUMMARY ====================")
    print(json.dumps({k: v for k, v in out.items() if k != "parsed"}, indent=4))
