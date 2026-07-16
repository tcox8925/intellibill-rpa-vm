# ======================================================
# nipr_pull.py  — CLEANED FOR NEW LOGGING MODEL (OPTIMIZED)
# ======================================================

import os
import datetime
import undetected_chromedriver as uc
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from utils import db_utils
import pandas as pd
from utils.azure_blob_utils import authenticate_blob_storage
from io import BytesIO
import time
from datetime import datetime, timedelta, timezone

CST = timezone(timedelta(hours=-6))
def today_cst_str():
    return datetime.now(CST).strftime("%Y%m%d")

# Prevent noisy __del__ warnings from undetected_chromedriver
uc.Chrome.__del__ = lambda self: None

USERNAME = os.getenv("NIPR_USERNAME", "")
PASSWORD = os.getenv("NIPR_PASSWORD", "")

STATE_NAME_MAP = {
    "AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas",
    "CA": "California", "CO": "Colorado", "CT": "Connecticut", "DC": "Washington DC",
    "DE": "Delaware", "FL": "Florida", "GA": "Georgia", "GU": "Guam",
    "HI": "Hawaii", "ID": "Idaho", "IL": "Illinois", "IN": "Indiana",
    "IA": "Iowa", "KS": "Kansas", "KY": "Kentucky", "LA": "Louisiana",
    "ME": "Maine", "MD": "Maryland", "MA": "Massachusetts", "MI": "Michigan",
    "MN": "Minnesota", "MS": "Mississippi", "MO": "Missouri", "MT": "Montana",
    "NE": "Nebraska", "NV": "Nevada", "NH": "New Hampshire", "NJ": "New Jersey",
    "NM": "New Mexico", "NY": "New York", "NC": "North Carolina", "ND": "North Dakota",
    "OH": "Ohio", "OK": "Oklahoma", "OR": "Oregon", "PA": "Pennsylvania",
    "PR": "Puerto Rico", "RI": "Rhode Island", "SC": "South Carolina",
    "SD": "South Dakota", "TN": "Tennessee", "TX": "Texas", "UT": "Utah",
    "VT": "Vermont", "VA": "Virginia", "VI": "Virgin Islands", "WA": "Washington",
    "WV": "West Virginia", "WI": "Wisconsin", "WY": "Wyoming",
}


# ------------------------------------------------------
# Small helpers
# ------------------------------------------------------
def get_state_full_name(code):
    return STATE_NAME_MAP.get(code.upper()) if code else None


def normalize_dob(dob_raw: str):
    try:
        m, d, y = dob_raw.split("/")
        return f"{y}-{m.zfill(2)}-{d.zfill(2)}"
    except Exception:
        return dob_raw


def ensure_license_tab(driver, wait: WebDriverWait):
    """
    Ensure the License tab is selected.
    Only clicks if it's not already the active tab.
    """
    try:
        selected = driver.find_elements(
            By.XPATH, "//button[@id='tab-license' and contains(@class,'Mui-selected')]"
        )
        if selected:
            return  # already selected

        btn = wait.until(EC.element_to_be_clickable((By.ID, "tab-license")))
        driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", btn)
        btn.click()
    except Exception:
        btn = wait.until(
            EC.element_to_be_clickable((By.XPATH, "//button[contains(., 'License')]"))
        )
        driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", btn)
        btn.click()


def ensure_state_selected(driver, wait: WebDriverWait, full_name: str):
    """
    Ensure the state dropdown is set to full_name.
    Only reselects if current value is empty or different.
    """
    if not full_name:
        return

    try:
        current_elems = driver.find_elements(
            By.XPATH, "//label[@id='state-label']/following-sibling::*[1]"
        )
        if current_elems:
            txt = (current_elems[0].text or "").strip()
            if txt and txt.lower() == full_name.lower():
                return  # correct state already selected
    except Exception:
        pass

    # Re-select the state
    dropdown = wait.until(EC.element_to_be_clickable((By.ID, "state")))
    driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", dropdown)
    dropdown.click()

    option = wait.until(
        EC.element_to_be_clickable(
            (By.XPATH, f"//ul[@role='listbox']//li[normalize-space()='{full_name}']")
        )
    )
    driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", option)
    option.click()


def safe_click(driver, xpath: str, timeout: int = 20, attempts: int = 3) -> bool:
    """
    Click an element safely with retries:
      - waits for visibility & clickability
      - scrolls into view
      - retries if intercepted / stale
    """
    last_err = None

    for _ in range(attempts):
        try:
            elem = WebDriverWait(driver, timeout).until(
                EC.element_to_be_clickable((By.XPATH, xpath))
            )
            driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", elem)
            elem.click()
            return True
        except Exception as e:
            last_err = e

    print(f"[SAFE_CLICK_ERR] XPath={xpath}: {last_err}")
    return False


# ------------------------------------------------------
# LOGIN + NAVIGATION
# ------------------------------------------------------
def login_nipr(driver, wait: WebDriverWait):
    driver.get("https://pdb-reports.app.nipr.com/home")

    user = wait.until(EC.presence_of_element_located((By.ID, "username")))
    pwd = wait.until(EC.presence_of_element_located((By.ID, "password")))
    user.clear()
    user.send_keys(USERNAME)
    pwd.clear()
    pwd.send_keys(PASSWORD)

    safe_click(driver, "/html/body/main/section/div/div/div/form/div[2]/button")

    # Wait until we're past login (either home or create-report link appears)
    wait.until(
        EC.presence_of_element_located(
            (By.XPATH, "//a[contains(@href,'create-report/detail-report')]")
        )
    )


def open_detail_report_search(driver, wait: WebDriverWait):
    safe_click(driver, "//a[contains(@href,'create-report/detail-report')]")
    # Wait for License tab to be present as a signal the page is ready
    wait.until(
        EC.presence_of_element_located(
            (By.XPATH, "//button[@id='tab-license' or contains(., 'License')]")
        )
    )


def check_no_results_popup(driver):
    try:
        popup = driver.find_element(
            By.XPATH,
            "//*[contains(@id,'snackbar')][contains(., 'Your search yielded no results')]"
        )
        return popup.is_displayed()
    except:
        return False


def click_reset(driver, wait: WebDriverWait):
    """Click the NIPR Reset button if visible."""
    try:
        btn = wait.until(
            EC.element_to_be_clickable(
                (By.XPATH, "//button[@data-testid='button_detail_license_reset']")
            )
        )
        driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", btn)
        btn.click()
        print("[NIPR] Reset clicked")
    except Exception:
        print("[NIPR] Reset not found (safe to continue)")


def reload_search_page(driver, wait: WebDriverWait):
    """
    Hard reload of the detail-report search page.
    Keeps us in a known-good state between rows.
    """
    driver.get("https://pdb-reports.app.nipr.com/create-report/detail-report")
    wait.until(
        EC.presence_of_element_located(
            (By.XPATH, "//button[@id='tab-license' or contains(., 'License')]")
        )
    )


def select_name_tab(driver, wait: WebDriverWait):
    try:
        btn = wait.until(
            EC.element_to_be_clickable((By.XPATH, "//button[contains(.,'Name')]"))
        )
        driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", btn)
        btn.click()
    except Exception:
        pass


def wait_for_detail(driver, timeout: int = 15) -> bool:
    try:
        WebDriverWait(driver, timeout).until(
            EC.presence_of_element_located(
                (
                    By.XPATH,
                    "//h4[contains(@class,'MuiTypography-h4')][normalize-space()='Detail Report']",
                )
            )
        )
        return True
    except Exception:
        return False


def click_back(driver, wait: WebDriverWait):
    try:
        btn = wait.until(
            EC.element_to_be_clickable(
                (By.CSS_SELECTOR, "button[data-testid='button_detail_back']")
            )
        )
        driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", btn)
        btn.click()
    except Exception:
        pass


def get_input(driver, wait: WebDriverWait, label: str):
    try:
        inp = wait.until(
            EC.presence_of_element_located(
                (By.XPATH, f"//label[normalize-space()='{label}']/following::input[1]")
            )
        )
        return inp.get_attribute("value") or None
    except Exception:
        return None


def scrape_detail(driver, wait: WebDriverWait):
    d = {}
    d["nipr_name"] = get_input(driver, wait, "Name")
    dob_raw = get_input(driver, wait, "DOB")
    d["nipr_dob"] = dob_raw

    d["nipr_demographics_updated"] = get_input(driver, wait, "Demographics")
    d["nipr_npn"] = get_input(driver, wait, "NPN")
    d["nipr_resident_states"] = get_input(driver, wait, "Resident states")
    d["nipr_producer_licensing_updated"] = get_input(
        driver, wait, "Producer licensing"
    )
    d["nipr_report_price"] = get_input(driver, wait, "Report price")
    d["nipr_appointments_updated"] = get_input(driver, wait, "Appointments")
    return d


# ------------------------------------------------------
# SEARCH MODES
# ------------------------------------------------------
def search_by_license(driver, wait: WebDriverWait, license_number: str, state_code: str):
    if not license_number:
        print("[BULK NIPR] No license number found")
        return None

    sname = get_state_full_name(state_code)
    if not sname:
        print("[BULK NIPR] State name not found")
        return None

    # Only change tab if needed
    ensure_license_tab(driver, wait)

    # Input license number
    inp = wait.until(
        EC.presence_of_element_located((By.XPATH, "//input[@name='licenseNumber']"))
    )
    inp.clear()
    inp.send_keys(license_number)

    # Ensure state selected
    ensure_state_selected(driver, wait, sname)

    # Click search
    safe_click(driver, "//button[@data-testid='button_detail_license_search']")

    # Check immediate no-results toast
    for _ in range(5):
        if check_no_results_popup(driver):
            return "NO_RESULTS"
        time.sleep(0.2)

    # Wait for detail page
    if not wait_for_detail(driver):
        return None

    d = scrape_detail(driver, wait)
    d["license_number"] = license_number
    return d


def search_by_name(driver, wait: WebDriverWait, full_name: str, state_code: str):
    if not full_name:
        return None

    sname = get_state_full_name(state_code)
    if not sname:
        return None

    select_name_tab(driver, wait)

    inp = wait.until(
        EC.presence_of_element_located(
            (By.XPATH, "//input[@name='name' or @name='fullName' or @name='personName']")
        )
    )
    inp.clear()
    inp.send_keys(full_name)

    ensure_state_selected(driver, wait, sname)
    safe_click(driver, "//button[@data-testid='button_detail_license_search']")

    if not wait_for_detail(driver):
        return None

    d = scrape_detail(driver, wait)
    d["full_name"] = full_name
    return d


def try_load_existing_nipr_csv(state_code: str, nipr_pull: str):
    """
    Load today's NIPR CSV if it exists.
    Returns a SET of values for nipr_pull column (license_number or full_name).
    Returns empty set if no CSV found.
    """
    today = today_cst_str()
    blob_path = f"raw/agent_data_source/nipr_temp/nipr_bulk_{state_code}_{today}.csv"

    try:
        bsc = authenticate_blob_storage()
        blob_client = bsc.get_blob_client(container="834analytics-dev", blob=blob_path)

        if not blob_client.exists():
            print(f"[NIPR] No prior CSV for {state_code}")
            return set()

        csv_bytes = blob_client.download_blob().readall()
        df = pd.read_csv(BytesIO(csv_bytes))

        if nipr_pull not in df.columns:
            print(f"[NIPR] CSV missing nipr_pull column {nipr_pull}")
            return set()

        values = df[nipr_pull].dropna().astype(str).str.strip()
        unique = set(values.tolist())

        print(f"[NIPR] Loaded {len(unique)} historical {nipr_pull} for {state_code}")
        return unique

    except Exception as e:
        print(f"[NIPR] Failed to load existing CSV for {state_code}: {e}")
        return set()


# ------------------------------------------------------
# STATE-LEVEL ENTRY POINT (NO LOGGING)
# ------------------------------------------------------
def bulk_enrich_state(state_cfg: dict, max_rows=None):
    """
    Scrapes NIPR results for ALL pending rows in a state.
    Returns a LIST OF RESULT DICTS.
    Does NOT update DB. That happens later in the bulk loader.
    """
    state_code = state_cfg["state_code"]
    nipr_field = (state_cfg.get("nipr_pull") or "").strip().lower()

    conn = db_utils.get_postgres_connection()
    cur = conn.cursor()

    nipr_pull_col = nipr_field  # license_number or full_name

    # Load today's CSV cache
    already_scraped = try_load_existing_nipr_csv(state_code, nipr_pull_col)

    # Load pending rows
    cur.execute(
        """
        SELECT id, full_name, license_number, state_code
        FROM raw.sbe_certs
        WHERE state_code = %s AND status='Pending'
    """,
        (state_code,),
    )
    all_rows = cur.fetchall()
    cur.close()
    conn.close()

    # Filter to only rows we have NOT scraped yet today
    rows = []
    for cid, fn, lic, st in all_rows:
        key = lic if nipr_pull_col == "license_number" else fn
        key = str(key or "").strip()
        if key and key not in already_scraped:
            rows.append((cid, fn, lic, st))

    print(f"[BULK_NIPR] {state_code}: {len(rows)} rows needing scrape today")

    if not rows:
        return []

    # Start browser
    driver = uc.Chrome()
    driver.maximize_window()
    wait = WebDriverWait(driver, 20)

    results = []

    try:
        # prepare browser once
        login_nipr(driver, wait)
        open_detail_report_search(driver, wait)

        # Select License tab once
        ensure_license_tab(driver, wait)

        # Select state once
        sname = get_state_full_name(state_code)
        ensure_state_selected(driver, wait, sname)
        total = len(rows) if max_rows is None else min(len(rows), max_rows)
        for idx, (cid, full_name, license_number, row_state) in enumerate(rows[:total], 1):
            print(f"[BULK_NIPR] {state_code} ({idx}/{total}) — id={cid}")

            try:
                # Always remain in the SAME PAGE CONTEXT
                # Just edit input + search

                no_results_retries = 0
                while no_results_retries < 3:
                    try:
                        # Search
                        if nipr_field == "license_number":
                            data = search_by_license(driver, wait, license_number, row_state)
                        else:
                            data = search_by_name(driver, wait, full_name, row_state)
                        if data["nipr_npn"] is None or data is None:
                            print("[BULK_NIPR] Retrieved no data from search.")
                            raise Exception
                        break
                    except Exception as e:
                        print("[BULK_NIPR] Failed to search, attempting retry...")
                        print(e)
                        time.sleep(4)
                        driver.refresh()
                        time.sleep(4)
                        ensure_license_tab(driver, wait)
                        ensure_state_selected(driver, wait, get_state_full_name(state_code))
                        time.sleep(4)
                        no_results_retries += 1
                print("[BULK_NIPR] Finished search segment")

                # === CASE 1: NO RESULTS ===
                if data == "NO_RESULTS" or check_no_results_popup(driver):
                    print(f"[BULK_NIPR] NO RESULTS → {cid}")

                    results.append({
                        "id": cid,
                        "state_code": row_state,
                        "license_number": license_number,
                        "full_name": full_name,
                        "status": "License Not Found",
                    })

                    no_results_retries = 0
                    while no_results_retries < 3:
                        try:
                            print('[BULK_NIPR] No results found, resetting page...')
                            # 1. Try reset
                            click_reset(driver, wait)

                            # 2. HARD RELOAD (critical to fix stuck dropdown)
                            driver.get("https://pdb-reports.app.nipr.com/create-report/detail-report")

                            # 3. Re-select license tab and state
                            ensure_license_tab(driver, wait)
                            ensure_state_selected(driver, wait, get_state_full_name(state_code))
                            break
                        except Exception as e:
                            print("[BULK_NIPR] Failed to reload or reset page after no results found...")
                            print(e)
                            time.sleep(5)
                            driver.refresh()
                            time.sleep(5)
                            no_results_retries += 1
                    continue

                # SUCCESS
                if data:
                    data["id"] = cid
                    data["state_code"] = row_state
                    data["status"] = "NIPR"
                    results.append(data)

                    # Just click back
                    click_back(driver, wait)
                    continue

                # Unexpected → soft recovery
                click_back(driver, wait)

            except Exception as e:
                print(f"[BULK_NIPR_ROW_ERR] id={cid}: {e}")
                click_back(driver, wait)

        return results

    except Exception as e:
        print(f"[BULK_NIPR_FATAL] {state_code}: {e}")
        return []

    finally:
        try:
            driver.quit()
        except Exception:
            pass