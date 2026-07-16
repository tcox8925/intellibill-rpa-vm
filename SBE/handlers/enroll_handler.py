# ======================================================
# handlers/enroll_handler.py  (FINAL — CLEAN + STABLE)
# ======================================================

import re
import time
import math

from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

from utils.upload_utils import (
    parse_address,
    buffer_sbe_batch_to_csv,
    load_sbe_existing_dedupe_values,
)


BATCH_SIZE = 500  # rows flushed per batch save


# ------------------------------------------------------
# Wait until list view is present
# ------------------------------------------------------
def wait_for_list(driver, timeout=20):
    WebDriverWait(driver, timeout).until(
        EC.presence_of_element_located((By.CSS_SELECTOR, "#list-only tbody tr"))
    )


# ------------------------------------------------------
# Extract <dd> data from profile page
# ------------------------------------------------------
def extract_dd(profile, label):
    try:
        dts = profile.find_elements(By.TAG_NAME, "dt")
        dds = profile.find_elements(By.TAG_NAME, "dd")
        for dt, dd in zip(dts, dds):
            if dt.text.strip().startswith(label):
                return dd.text.strip()
    except:
        pass
    return None


# ------------------------------------------------------
# Scrape a profile page
# ------------------------------------------------------
def scrape_profile(driver):
    profile = driver.find_element(By.ID, "profile-info")

    full_name = profile.find_element(By.TAG_NAME, "h1").text.strip()

    raw_text = profile.find_element(By.TAG_NAME, "p").text
    lines = [l.strip() for l in raw_text.split("\n") if l.strip()]

    # Address
    address_line = lines[0]
    street, city, st, zipcode = parse_address(address_line)

    # Optional phone line
    phone = lines[1] if len(lines) > 1 else None

    # Optional email scanning
    email = None
    for l in lines:
        if "@" in l:
            email = l
            break

    product_expertise = extract_dd(profile, "Product Expertise")
    languages = extract_dd(profile, "Languages Spoken")
    license_number = extract_dd(profile, "State License Number")

    return {
        "full_name": full_name,
        "email": email,
        "phone": phone,
        "street": street,
        "city": city,
        "state": st,
        "zipcode": zipcode,
        "product_expertise": product_expertise,
        "languages": languages,
        "license_number": license_number,
        "profile_url": driver.current_url,
    }


# ------------------------------------------------------
# Scrape one LIST page (rows + profiles)
# ------------------------------------------------------
def scrape_current_page(driver, state_cfg, existing_keys, batch):
    state_code = state_cfg["state_code"]
    dedupe_field = state_cfg.get("nipr_pull")

    rows = driver.find_elements(By.CSS_SELECTOR, "#list-only tbody tr")

    brokers = []
    for row in rows:
        link = row.find_element(By.CSS_SELECTOR, "td.broker-name a")
        onclick_js = link.get_attribute("onclick") or ""
        m = re.search(r"getDetails\('([^']+)'", onclick_js)
        uid = m.group(1) if m else None

        if uid:
            brokers.append((link.text.strip(), uid))

    print(f"[{state_code}] Found {len(brokers)} brokers")

    for name, uid in brokers:

        list_page_url = driver.current_url

        try:
            # Load profile via JavaScript call
            driver.execute_script("getDetails(arguments[0], 1);", uid)

            WebDriverWait(driver, 20).until(
                EC.presence_of_element_located((By.ID, "profile-info"))
            )

            scraped = scrape_profile(driver)
            scraped.update({
                "state_code": state_cfg["state_code"],
                "company_id": state_cfg["company_id"],
                "crm_field": state_cfg["crm_field"],
                "broker_uid": uid,
                "distance": None
            })

            # --------- DEDUPE ---------
            if dedupe_field:
                dval = (scraped.get(dedupe_field) or "").strip()
                if not dval:
                    continue
                if dval in existing_keys:
                    print(f"[{state_code}] Duplicate {dedupe_field}={dval} → skip")
                    continue

                existing_keys.add(dval)

            batch.append(scraped)

            # Flush batch to CSV/Blob
            if len(batch) >= BATCH_SIZE:
                buffer_sbe_batch_to_csv(state_cfg, batch)
                batch.clear()

        finally:
            # Go back to list page (GA, PA, NJ, etc.)
            try:
                back = driver.find_element(By.CSS_SELECTOR, "a[href*='navType=back']")
                driver.execute_script("arguments[0].click();", back)
            except Exception:
                driver.get(list_page_url)


# ------------------------------------------------------
# Extract page number from pagination link ("Page 11", "11", "Page 3 Current Page")
# ------------------------------------------------------
def extract_page_number(a_element):
    txt = a_element.text.strip()
    m = re.search(r'\b(\d+)\b', txt)
    return int(m.group(1)) if m else None


# ------------------------------------------------------
# FULL HANDLER (entry point for each state)
# ------------------------------------------------------
def run_enroll_group_handler_buffered(state_cfg, driver, max_pages=None):
    """
    - Determine total pages from "Agents found"
    - Loop pages by direct URL (fast + stable)
    - For each page → read brokers → scrape profiles
    - Write to CSV + upload to Blob per batch
    """
    state_code = state_cfg["state_code"]
    base_url = state_cfg["url"]

    driver.maximize_window()

    # Load existing dedupe from CSV+DB
    dedupe_field, existing_keys = load_sbe_existing_dedupe_values(state_cfg)

    # Load first page
    driver.get(base_url.replace('{{page_num}}','1'))

    WebDriverWait(driver, 20).until(
        EC.presence_of_element_located((By.CSS_SELECTOR, "#list-only tbody tr"))
    )

    # ===========================================================
    # 1️⃣ Determine total_pages via "Agents found"
    # ===========================================================
    try:
        skip_elem = driver.find_element(By.CSS_SELECTOR, "a.skip")
        text = skip_elem.text.strip()
        # Example: "15190 Agents found"
        total_agents = int(text.split()[0])
        total_pages = math.ceil(total_agents / 10)
    except Exception as e:
        print(f"[{state_code}] Could not determine agent count: {e}")
        total_pages = 1

    print(f"[{state_code}] Agents found = {total_agents}, total_pages = {total_pages}")

    # Apply runner override
    if max_pages is not None:
        total_pages = min(total_pages, max_pages)
        print(f"[{state_code}] Limiting to max_pages = {total_pages}")

    batch = []

    # ===========================================================
    # 2️⃣ DIRECT URL PAGE LOOP (No pagination buttons)
    # ===========================================================
    for page_num in range(1, total_pages + 1):

        print(f"[{state_code}] Navigating to page {page_num}")
        
        page_url = ''
        if '{{page_num}}' in base_url:
            page_url = base_url.replace('{{page_num}}',str(page_num))
        else:
            page_url = f"{base_url}&pageNumber={page_num}&pageSize=10"

        try:
            driver.get(page_url)

            WebDriverWait(driver, 20).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, "#list-only tbody tr"))
            )

            scrape_current_page(driver, state_cfg, existing_keys, batch)

            # Extra flush safety
            if len(batch) >= BATCH_SIZE:
                buffer_sbe_batch_to_csv(state_cfg, batch)
                batch.clear()

        except Exception as e:
            print(f"[{state_code}] Failed page {page_num}: {e}")
            break

    # Final remaining flush
    if batch:
        buffer_sbe_batch_to_csv(state_cfg, batch)

    print(f"[{state_code}] Completed scrape phase.")
