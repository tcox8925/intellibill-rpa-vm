

import os
import time
from typing import Optional, Dict

import undetected_chromedriver as uc
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC


#config
USERNAME = os.getenv("NIPR_USERNAME", "")
PASSWORD = os.getenv("NIPR_PASSWORD", "")

# Silence uc destructor warnings
uc.Chrome.__del__ = lambda self: None


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


#normalization
def normalize_state(state_input: str) -> str:
    if not state_input:
        raise ValueError("State is required")

    s = state_input.strip().upper()

    if len(s) == 2 and s in STATE_NAME_MAP:
        return s

    for code, name in STATE_NAME_MAP.items():
        if name.upper() == s:
            return code

    raise ValueError("Invalid state value: %s" % state_input)


def normalize_applicant_type(applicant_type: Optional[str]) -> str:
    if not applicant_type:
        return "AGENT"

    t = applicant_type.strip().upper()
    if t not in ("AGENT", "AGENCY"):
        raise ValueError("Invalid applicant type: %s" % applicant_type)

    return t


def get_state_full_name(code: str) -> str:
    return STATE_NAME_MAP.get(code)

#selenium helpers

def safe_click(driver, wait, xpath: str, attempts: int = 3):
    last_err = None
    for _ in range(attempts):
        try:
            elem = wait.until(EC.element_to_be_clickable((By.XPATH, xpath)))
            driver.execute_script(
                "arguments[0].scrollIntoView({block:'center'});", elem
            )
            elem.click()
            return
        except Exception as e:
            last_err = e
            time.sleep(0.4)
    raise RuntimeError("Click failed for %s: %s" % (xpath, last_err))


def has_no_results(driver) -> bool:
    try:
        toast = driver.find_element(
            By.XPATH,
            "//*[contains(@id,'notistack') and contains(., 'no results')]"
        )
        return toast.is_displayed()
    except Exception:
        return False


#nav

def login_nipr(driver, wait):
    driver.get("https://pdb-reports.app.nipr.com/home")

    user = wait.until(EC.presence_of_element_located((By.ID, "username")))
    pwd = wait.until(EC.presence_of_element_located((By.ID, "password")))

    user.clear()
    user.send_keys(USERNAME)
    pwd.clear()
    pwd.send_keys(PASSWORD)

    login_btn = wait.until(
        EC.presence_of_element_located((By.XPATH, "//button[@type='submit']"))
    )

    driver.execute_script("arguments[0].click();", login_btn)

    wait.until(
        EC.presence_of_element_located(
            (By.XPATH, "//a[contains(@href,'create-report/detail-report')]")
        )
    )


def open_license_search(driver, wait):
    safe_click(driver, wait, "//a[contains(@href,'create-report/detail-report')]")
    wait.until(EC.presence_of_element_located((By.XPATH, "//button[contains(.,'License')]")))


def ensure_license_tab(driver, wait):
    selected = driver.find_elements(
        By.XPATH,
        "//button[contains(.,'License') and contains(@class,'Mui-selected')]"
    )
    if not selected:
        safe_click(driver, wait, "//button[contains(.,'License')]")


def ensure_applicant_type(driver, wait, applicant_type: str):
    if applicant_type == "AGENT":
        return

    radio = wait.until(
        EC.element_to_be_clickable(
            (By.XPATH, "//input[@name='applicantType' and @value='AGENCY']")
        )
    )
    driver.execute_script("arguments[0].click();", radio)


def ensure_state_selected(driver, wait, full_state_name: str):
    dropdown = wait.until(EC.element_to_be_clickable((By.ID, "state")))
    dropdown.click()

    option = wait.until(
        EC.element_to_be_clickable(
            (By.XPATH, "//ul[@role='listbox']//li[normalize-space()='%s']" % full_state_name)
        )
    )
    driver.execute_script(
        "arguments[0].scrollIntoView({block:'center'});", option
    )
    option.click()


#scrape

def wait_for_detail(driver, timeout: int = 15) -> bool:
    try:
        WebDriverWait(driver, timeout).until(
            EC.presence_of_element_located(
                (By.XPATH, "//h4[normalize-space()='Detail Report']")
            )
        )
        return True
    except Exception:
        return False


def get_field_value(driver, wait, label: str):
    try:
        inp = wait.until(
            EC.presence_of_element_located(
                (By.XPATH, "//label[normalize-space()='%s']/following::input[1]" % label)
            )
        )
        return inp.get_attribute("value")
    except Exception:
        return None


def scrape_detail(driver, wait) -> Dict[str, Optional[str]]:
    return {
        "nipr_name": get_field_value(driver, wait, "Name"),
        "nipr_dob": get_field_value(driver, wait, "DOB"),
        "nipr_npn": get_field_value(driver, wait, "NPN"),
        "nipr_resident_states": get_field_value(driver, wait, "Resident states"),
        "nipr_demographics_updated": get_field_value(driver, wait, "Demographics"),
        "nipr_appointments_updated": get_field_value(driver, wait, "Appointments"),
    }


#fastapi function

def run_ai_nipr_scrape(
    license_number: str,
    state: str,
    applicant_type: Optional[str] = "AGENT"
) -> Dict[str, Optional[str]]:

    state_code = normalize_state(state)
    applicant_type = normalize_applicant_type(applicant_type)
    full_state = get_state_full_name(state_code)

    driver = uc.Chrome()
    driver.maximize_window()
    wait = WebDriverWait(driver, 20)

    try:
        login_nipr(driver, wait)
        open_license_search(driver, wait)

        ensure_license_tab(driver, wait)
        ensure_applicant_type(driver, wait, applicant_type)

        lic_input = wait.until(
            EC.presence_of_element_located((By.NAME, "licenseNumber"))
        )
        lic_input.clear()
        lic_input.send_keys(license_number)

        ensure_state_selected(driver, wait, full_state)

        safe_click(driver, wait, "//button[@data-testid='button_detail_license_search']")

        for _ in range(10):
            if has_no_results(driver):
                return {
                    "status": "NO_RESULTS",
                    "license_number": license_number,
                    "state_code": state_code,
                    "applicant_type": applicant_type,
                }
            time.sleep(0.2)

        if not wait_for_detail(driver):
            raise RuntimeError("Detail page did not load")

        data = scrape_detail(driver, wait)
        data.update({
            "status": "FOUND",
            "license_number": license_number,
            "state_code": state_code,
            "applicant_type": applicant_type,
        })
        return data

    finally:
        try:
            driver.quit()
        except Exception:
            pass