import time
from datetime import datetime, timezone
from uuid import uuid4
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from utils import upload_utils
from utils.audit_utils import generate_audit_id
from insert_npi_registry_row import get_npi_registry_data


def _current_ts():
    """Returns current UTC timestamp as string."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]


def _selenium_oig_check(npi: str):
    """
    Perform OIG exclusion check via Selenium-driven UI.

    Returns dict:
        {
          "status": "Pass" | "Fail" | "Unknown",
          "description": str | None,
          "action_date": str | None
        }
    """
    profile = get_npi_registry_data(npi)
    entity = "Individual" if profile.get("enumeration_type") == "NPI-1" else "Organization"

    options = Options()
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--headless")

    driver = webdriver.Chrome(options=options)
    result = {"status": "Unknown", "description": None, "action_date": None}

    try:
        driver.get("https://exclusions.oig.hhs.gov/")
        wait = WebDriverWait(driver, 15)

        # --- Perform Search ---
        if entity == "Individual":
            first = profile.get("basic", {}).get("first_name", "")
            last = profile.get("basic", {}).get("last_name", "")
            wait.until(EC.presence_of_element_located((By.ID, "ctl00_cpExclusions_txtSPFirstName"))).send_keys(first)
            wait.until(EC.presence_of_element_located((By.ID, "ctl00_cpExclusions_txtSPLastName"))).send_keys(last)
            wait.until(EC.element_to_be_clickable((By.ID, "ctl00_cpExclusions_ibSearchSP"))).click()
        else:
            wait.until(EC.element_to_be_clickable((By.ID, "ctl00_cpExclusions_Linkbutton1"))).click()
            org = profile.get("basic", {}).get("organization_name", "")
            if org:
                wait.until(EC.presence_of_element_located((By.ID, "ctl00_cpExclusions_txtSBName"))).send_keys(org)
                wait.until(EC.element_to_be_clickable((By.ID, "ctl00_cpExclusions_ibSearchSB"))).click()

        time.sleep(3)

        # --- No results → Pass ---
        if not driver.find_elements(By.ID, "ctl00_cpExclusions_tblBack"):
            result["status"] = "Pass"
            return result

        # --- Results table present → potential fail ---
        table = driver.find_element(By.ID, "ctl00_cpExclusions_gvEmployees")
        rows = table.find_elements(By.TAG_NAME, "tr")[1:]  # skip header

        found_match = False
        exclusion_desc, exclusion_date = None, None

        for idx, row in enumerate(rows, start=2):  # ctl02, ctl03, etc.
            try:
                link_id = f"ctl00_cpExclusions_gvEmployees_ctl{idx:02d}_cmdVerify"
                link = driver.find_element(By.ID, link_id)
                driver.execute_script("arguments[0].click();", link)
                time.sleep(2)

                # Check for NPI in details page
                if driver.find_elements(By.XPATH, "//th[text()='NPI']/following-sibling::td"):
                    found_npi = driver.find_element(
                        By.XPATH, "//th[text()='NPI']/following-sibling::td"
                    ).text.strip()

                    if found_npi and found_npi == npi:
                        found_match = True
                        try:
                            exclusion_desc = driver.find_element(
                                By.XPATH, "//th[text()='Excl. Type']/following-sibling::td"
                            ).text.strip()
                        except:
                            exclusion_desc = None
                        try:
                            exclusion_date = driver.find_element(
                                By.XPATH, "//th[text()='Excl. Date']/following-sibling::td"
                            ).text.strip()
                        except:
                            exclusion_date = None
                        break

                # No NPI match → go back to results
                driver.back()
                time.sleep(2)

            except Exception:
                continue

        if found_match:
            result.update({
                "status": "Fail",
                "description": exclusion_desc or "OIG exclusion identified for this provider.",
                "action_date": exclusion_date
            })
        else:
            # Multiple results but no NPI match
            result.update({
                "status": "Fail",
                "description": "Multiple potential matches identified; NPI not verified. Further manual verification required.",
                "action_date": datetime.now().strftime("%Y-%m-%d")
            })

    except Exception as e:
        result.update({"status": "Unknown", "description": f"Error: {str(e)}"})
    finally:
        driver.quit()

    return result


def run_oig_check(npi: str, txn_id_provider: str, dry_run: bool = False):
    """
    Perform OIG exclusion check and upload results.

    Actions:
      - Inserts summary into [wpo].[pch_regulatory_validation]
      - On Fail, inserts detail into [wpo].[pch_regulatory_fail_details]
    """
    outcome = _selenium_oig_check(npi)
    result = {"source": "OIG", "status": outcome["status"]}

    if dry_run:
        print(f"[DRY RUN] OIG check for NPI {npi}: {outcome}")
        return outcome

    # 1️⃣ Upload summary validation
    reg_txn_ids = upload_utils.upload_regulatory_validation(txn_id_provider, [result])
    print(f"[UPLOAD] OIG check for NPI {npi}: {result}")

    # 2️⃣ Upload fail details (if applicable)
    if outcome["status"] == "Fail":
        txn_id_reg = reg_txn_ids.get("OIG")

        fail_row = [{
            "txn_id_reg": txn_id_reg,
            "check_type": "OIG",
            "description": outcome.get("description") or "OIG exclusion identified for this provider.",
            "action_date": outcome.get("action_date"),
            "source": "OIG"
        }]
        upload_utils.upload_regulatory_fail_details(txn_id_provider, fail_row)
        print(f"[DETAILS] OIG failure details inserted for NPI {npi}")

    return outcome