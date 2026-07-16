import time, re
from typing import Dict, Any, List, Optional
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException
from bs4 import BeautifulSoup

PAC_RE = re.compile(r"(?:PECOS\s+)?PAC\s+ID:\s*(\d{10})", re.I)
ENROLL_RE = re.compile(r"PECOS\s+Enrollment\s+ID:\s*([A-Z0-9]{10,20})", re.I)
ENROLLED_RE = re.compile(r"Is the provider registered in PECOS\?\s*(Yes|No)", re.I)


def _smart_scroll(driver, max_attempts: int = 8, pause: float = 0.6):
    last_h = 0
    for _ in range(max_attempts):
        driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
        time.sleep(pause)
        h = driver.execute_script("return document.body.scrollHeight")
        if h == last_h:
            break
        last_h = h


def _safe_quit(driver: webdriver.Chrome, service: Service):
    try:
        driver.quit()
    except Exception:
        pass
    try:
        service.stop()
    except Exception:
        pass


def scrape_npiprofile_education(npi: str, return_pecos: bool = False):
    url = f"https://npiprofile.com/npi/{npi}"

    opts = Options()
    # opts.add_argument("--headless=new")  # enable once stable
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--disable-gpu")
    opts.page_load_strategy = "eager"

    service = Service()  # chromedriver from PATH
    driver = webdriver.Chrome(service=service, options=opts)
    driver.set_page_load_timeout(20)
    driver.set_script_timeout(20)

    wait = WebDriverWait(driver, 12)

    try:
        driver.get(url)
        driver.maximize_window()
        time.sleep(20)
        # Wait for DOM to be usable
        wait.until(lambda d: d.execute_script("return document.readyState") in ("interactive", "complete"))

        # Best-effort dismiss cookie/modals
        for sel in (
                "//button[contains(@class,'close') or normalize-space(text())='×']",
                "//button[contains(translate(.,'ACEPT','acept'),'accept')]",
                "//button[contains(.,'Accept') or contains(.,'Close')]"
        ):
            try:
                driver.find_element(By.XPATH, sel).click()
                break
            except Exception:
                pass

        # Scroll to trigger lazy content
        _smart_scroll(driver)

        # Wait until either Education or PECOS shows up (whichever first)
        try:
            wait.until(EC.any_of(
                EC.presence_of_element_located((By.XPATH, "//dt[contains(., 'Medical School Name')]")),
                EC.presence_of_element_located((By.XPATH, "//dt[contains(., 'PECOS')]"))
            ))
        except TimeoutException:
            # Try one more scroll + short wait, then proceed anyway
            _smart_scroll(driver, max_attempts=3, pause=0.5)

        # Parse with BS4 (fast + robust for dt/dd)
        soup = BeautifulSoup(driver.page_source, "html.parser")
        dt_tags = soup.find_all("dt")
        print(f"[DEBUG] Found {len(dt_tags)} <dt> tags")

        edu = {}
        for dt in dt_tags:
            label = dt.get_text(strip=True)
            dd = dt.find_next_sibling("dd")
            if not dd:
                continue
            val = dd.get_text(strip=True)
            if not val:
                continue
            if "Medical School Name" in label and "program_name" not in edu:
                edu["program_name"] = val
                edu["type"] = "Medical Education"
            elif "Graduation Year" in label and "grad_year" not in edu:
                # normalize to 4-digit year if present
                m = re.search(r"(19|20)\d{2}", val)
                edu["grad_year"] = m.group(0) if m else None

        education_list = [edu] if edu else []
        if edu:
            print("[DEBUG] Final edu dict:", edu)

        # PECOS extraction from full page text
        page_text = soup.get_text(" ", strip=True)
        pac = None
        m1 = PAC_RE.search(page_text)
        if m1:
            pac = m1.group(1)

        enroll_id = None
        m2 = ENROLL_RE.search(page_text)
        if m2:
            enroll_id = m2.group(1)

        enrolled = None
        m3 = ENROLLED_RE.search(page_text)
        if m3:
            enrolled = (m3.group(1).lower() == "yes")

        if pac and not re.fullmatch(r"\d{10}", pac):
            pac = None

        pecos = {
            "pecos_pac_id": pac,
            "pecos_enrollment_id": enroll_id,
            "pecos_enrolled": enrolled,
            "source": "npiprofile.com",
        }

        return {"education": education_list, "pecos": pecos} if return_pecos else education_list

    except Exception as e:
        print(f"[ERROR] npiprofile scrape failed: {e}")
        try:
            print((driver.page_source or "")[:500])
        except Exception:
            pass
        empty = {"education": [], "pecos": {"pecos_pac_id": None, "pecos_enrollment_id": None, "pecos_enrolled": None,
                                            "source": "npiprofile.com", "error": str(e)}}
        return empty if return_pecos else []

    finally:
        _safe_quit(driver, service)


def scrape_tmb_profile(license_number: str) -> dict:
    """
    Scrape TMB profile by license number and return a flat dict with keys:
      - identifiers: list of dicts (including license)
      - rx_waiver_expiration_date
      - board_cert, board_cert_detail, race, awards
      - education: list of dicts
      - locations: list of dicts
      - regulatory: dict of flags
    """
    options = Options()
    # options.add_argument("--headless")  # uncomment for silent mode
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    driver = webdriver.Chrome(options=options)

    try:
        # Navigate to TMB and accept
        driver.get("https://profile.tmb.state.tx.us/")
        driver.maximize_window()  # ✅ ensure full viewport
        wait = WebDriverWait(driver, 20)
        wait.until(EC.element_to_be_clickable((By.ID, "BodyContent_btnAccept"))).click()

        # Enter license and search
        wait.until(EC.presence_of_element_located((By.ID, "BodyContent_tbLicense")))
        driver.find_element(By.ID, "BodyContent_tbLicense").send_keys(license_number)
        #cb = wait.until(EC.element_to_be_clickable((By.ID, "BodyContent_cbActiveLicensesOnly")))
        #try:
        #    if not cb.is_selected():
        #        driver.execute_script("arguments[0].click();", cb)
        #except Exception:
        #    if not cb.is_selected():
        #        cb.click()

        driver.find_element(By.ID, "BodyContent_btnSearch").click()
        wait.until(EC.presence_of_element_located((By.ID, "BodyContent_gvSearchResults")))
        driver.find_element(By.XPATH, "//table[@id='BodyContent_gvSearchResults']//a").click()

        # Wait for page to load and expand all collapsibles
        wait.until(EC.presence_of_element_located((By.CLASS_NAME, "CollapsiblePanelBody")))
        time.sleep(2)
        for btn in driver.find_elements(By.TAG_NAME, "button"):
            if btn.get_attribute("onclick"):
                try:
                    driver.execute_script("arguments[0].click();", btn)
                    time.sleep(0.3)
                except:
                    pass

        soup = BeautifulSoup(driver.page_source, "html.parser")
        # Extract name
        name_label = soup.find("label", string=re.compile(r"NAME:", re.I))
        name_value = None
        if name_label:
            next_label = name_label.find_next("label", class_="normal-m")
            if next_label:
                name_value = next_label.get_text(strip=True)

        # Extract license status
        status_label = soup.find("label", string=re.compile(r"CURRENT STATUS", re.I))
        license_status = "Active"
        if status_label:
            next_label = status_label.find_next("label", class_="medium-bold")
            if next_label and "NOT ACTIVE" in next_label.get_text(strip=True).upper():
                license_status = "Inactive"

        print(f"[DEBUG] TMB Name: {name_value}")
        print(f"[DEBUG] License Status: {license_status}")

        # Label lookup
        labels = soup.find_all('label')
        label_dict = {}
        for i, label in enumerate(labels):
            if "medium-bold" in label.get("class", []):
                key = label.get_text(strip=True).strip(':')
                if i + 1 < len(labels) and "normal-m" in labels[i + 1].get("class", []):
                    label_dict[key] = labels[i + 1].get_text(strip=True)

        # Initialize flat result
        result = {
            "identifiers": [
                {
                    "id_type": "TMB_LICENSE",
                    "id_issuer": label_dict.get('State', ''),
                    "id_type_value": license_number,
                    "id_description": "TMB License",
                    "id_state": label_dict.get('State', ''),
                    "id_issue_date": None
                }
            ],
            "rx_waiver_expiration_date": None,
            "board_cert": None,
            "board_cert_detail": None,
            "race": None,
            "awards": None,
            "education": [],
            "locations": [],
            "regulatory": {},
            "tmb_name": name_value, 
            "license_status": license_status,
        }

        # RX Waiver
        rx_row = soup.find("tr", id="BodyContent_trERXWaiver")
        if rx_row:
            cells = rx_row.find_all("label")
            if len(cells) >= 2:
                result["rx_waiver_expiration_date"] = cells[1].get_text(strip=True)

        # Board cert, race, awards
        result["board_cert"] = "Yes" if 'Specialty Certification' in label_dict else "No"
        result["board_cert_detail"] = label_dict.get('Specialty Certification', '')
        result["race"] = label_dict.get('Race', '')
        awards = [
            label.get_text(strip=True)
            for label in soup.find_all("label", class_="normal-m")
            if any(x in label.get_text(strip=True).upper() for x in ["AWARD", "FELLOW", "HONOR", "HOUSE OF DELEGATES"])
        ]
        if awards:
            result["awards"] = "; ".join(awards)

        # Education
        for table in soup.find_all('table'):
            if "Education" not in table.get_text():
                continue
            edu = {}
            for row in table.find_all("tr"):
                cells = row.find_all("label")
                if len(cells) != 2:
                    continue
                key = cells[0].get_text(strip=True).strip(':')
                val = cells[1].get_text(strip=True)
                if key in ["Name", "Program Name"]:
                    if edu and 'program_name' in edu:
                        edu.setdefault("type", "Medical Education")
                        result["education"].append(edu)
                        edu = {}
                    edu["program_name"] = val
                elif key == "Location":
                    edu["location"] = val
                elif key in ["Graduation Date", "End Date"]:
                    edu["grad_year"] = val
                elif key == "Type":
                    edu["type"] = val
                elif key == "Specialty":
                    edu["specialty"] = val
            if edu:
                edu.setdefault("type", "Medical Education")
                result["education"].append(edu)

        # Location
        address = {
            'address_1': label_dict.get('Address Line 1', ''),
            'address_2': label_dict.get('Address Line 2', ''),
            'city': label_dict.get('City', ''),
            'state': label_dict.get('State', ''),
            'zip': label_dict.get('Zip Code', '')
        }
        result["locations"].append({
            'source': 'TMB',
            'type': 'Primary',
            'location_name': 'Primary',
            'contact': None,
            'fax': None,
            **address
        })

        # --- Regulatory Checks ---
        def _extract_section_text(soup, section_id):
            sec = soup.find(id=section_id)
            if not sec:
                return ""
            return sec.get_text(separator=" ", strip=True)

        details = []

        # ---- Board Actions ----
        try:
            print("[DEBUG] Opening Complete Board Action History...")
            board_button = wait.until(EC.element_to_be_clickable((By.XPATH, "//input[@type='submit' and contains(@value, 'Complete Board Action History')]")))
            driver.execute_script("arguments[0].scrollIntoView(true);", board_button)
            driver.execute_script("arguments[0].click();", board_button)
            time.sleep(2)

            # expand TMB Actions
            try:
                print("[DEBUG] Expanding TMB Actions section...")
                tmb_plus = driver.find_element(By.ID, "ibBoardActions")
                driver.execute_script("arguments[0].click();", tmb_plus)
                time.sleep(2)
            except Exception as e:
                print(f"[DEBUG] No TMB Actions button found or already expanded: {e}")

            soup_board = BeautifulSoup(driver.page_source, "html.parser")
            board_div = soup_board.find("div", id="pnlBoardActions")
            if board_div:
                rows = board_div.find_all("tr")
                for tr in rows:
                    text = tr.get_text(separator=" ", strip=True)
                    if "Action Date:" in text and "Description:" in text:
                        date_span = tr.find("span", id=re.compile(r"lblDate_\d+"))
                        desc_span = tr.find("span", id=re.compile(r"lblDesc_\d+"))
                        date_text = date_span.get_text(strip=True) if date_span else None
                        desc_text = desc_span.get_text(strip=True) if desc_span else text
                        details.append({
                            "check_type": "BOARD",
                            "action_date": date_text,
                            "description": desc_text,
                            "source": "Texas Medical Board"
                        })
            print(f"[DEBUG] Board Actions found: {len(details)} records")
            result["regulatory"]["BOARD"] = "Fail" if details else "Pass"
        except Exception as e:
            print(f"[DEBUG] Board Actions scrape failed: {e}")
            result["regulatory"]["BOARD"] = "Pass"

        # ---- Criminal, Malpractice, Non-TMB ----
        for check_type, sec_id in [
            ("MALPRACTICE", "pnlMalpractice2"),
            ("CRIMINAL", "pnlCriminal"),
            ("NON-TMB", "pnlDisciplinary")
        ]:
            text = _extract_section_text(soup, sec_id)
            print(f"[DEBUG] {check_type} section text: {text[:200]}...")
            if not text:
                continue
            if "NONE" in text.upper():
                result["regulatory"][check_type] = "Pass"
            else:
                result["regulatory"][check_type] = "Fail"
                # parse embedded ACTION ON <date>
                m = re.search(r"ACTION\s+ON\s+(\d{1,2}/\d{1,2}/\d{2,4})", text, re.I)
                action_date = m.group(1) if m else None
                details.append({
                    "check_type": check_type,
                    "action_date": action_date,
                    "description": text,
                    "source": "Texas Medical Board"
                })

        result["regulatory_details"] = details
        print("[DEBUG] Final regulatory flags:", result["regulatory"])
        print("[DEBUG] Total detailed actions:", len(details))
        for d in details:
            print("  >", d)

        return result

    except Exception as e:
        print(f"TMB scrape failed for license {license_number}: {e}")
        return {}
    finally:
        driver.quit()