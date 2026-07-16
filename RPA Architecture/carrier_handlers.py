from selenium.webdriver import ActionChains
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support.ui import Select
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.common.by import By
from selenium.common.exceptions import TimeoutException, NoSuchElementException
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.chrome.options import Options
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
import time
import random
from datetime import datetime, timedelta, timezone
from calendar import month_name
import os
import pytz
import paramiko
import zipfile
import re
from file_utils import extract_bcbs_az_agents_to_csv
from file_utils import extract_agent_info_table_to_csv
from file_utils import wait_for_file
from file_utils import convert_pdf_to_csv
from logger import log_error, ERROR_CODES, log_success
from otp_email_utils import fetch_otp_code, mark_matching_as_read
import shutil
import csv


def dismiss_cookie_popup(driver, timeout=5):
    """
    Attempt to dismiss common cookie consent banners and overlay popups.
    Call this before any element interaction that might be blocked by an overlay.
    Silently does nothing if no popup is found.
    """
    selectors = [
        # OneTrust (Cigna, many insurance portals)
        (By.CLASS_NAME, "onetrust-close-btn-handler"),
        # Generic "Accept" / "Accept All" buttons
        (By.XPATH, "//button[contains(translate(text(),'ACCEPT','accept'),'accept')]"),
        # Generic "I Agree" buttons
        (By.XPATH, "//button[contains(translate(text(),'AGREE','agree'),'agree')]"),
        # Generic "Got it" buttons
        (By.XPATH, "//button[contains(translate(text(),'GOT IT','got it'),'got it')]"),
        # Generic "OK" cookie buttons
        (By.XPATH, "//button[contains(@class,'cookie') and (contains(text(),'OK') or contains(text(),'Accept'))]"),
        # Generic "Close" on cookie banners
        (By.XPATH, "//div[contains(@class,'cookie')]//button[contains(@class,'close')]"),
        # ID-based patterns
        (By.ID, "onetrust-accept-btn-handler"),
        (By.ID, "cookie-accept"),
        (By.ID, "cookieAccept"),
        # ARIA dismiss
        (By.CSS_SELECTOR, "[aria-label='Close cookie banner']"),
        (By.CSS_SELECTOR, "[aria-label='Accept cookies']"),
    ]

    for by, selector in selectors:
        try:
            btn = WebDriverWait(driver, timeout).until(
                EC.element_to_be_clickable((by, selector))
            )
            driver.execute_script("arguments[0].click();", btn)
            print(f"    🍪 Dismissed cookie/popup via {selector}")
            time.sleep(1)
            return True
        except (TimeoutException, NoSuchElementException, Exception):
            continue

    return False


def fetch_otp_code_from_file(matrix_row):
    # Delete any old codes in the folder
    folder_path = matrix_row["otp_path"]
    if os.path.exists(folder_path):
        for file in os.listdir(folder_path):
            if file.startswith(matrix_row["otp_filename"]):
                file_path = os.path.join(folder_path, file)
                try:
                    os.remove(file_path)
                    print(f"Deleted file: {file_path}")
                except Exception as e:
                    print(f"Error deleting file {file_path}: {e}")
    else:
        print(f"Folder {folder_path} does not exist.")

    # Step 1: Wait for OTP file to be available
    print("Waiting for OTP file...")
    timeout = 300  # Wait up to 5 minutes
    start_time = time.time()
    otp_file_folder = matrix_row["otp_path"]
    otp_file_name = matrix_row["otp_filename"]
    otp_file_extension = matrix_row["otp_extension"]
    otp_file_path = otp_file_folder + "\\\\" + otp_file_name + "." + otp_file_extension
    print(f"Waiting for file: {otp_file_path}")
    while time.time() - start_time < timeout:
        if os.path.exists(otp_file_path):
            break
        time.sleep(5)

    if not os.path.exists(otp_file_path):
        log_error(ERROR_CODES["OTP_error"],
                  f"OTP file '{otp_file_name}.{otp_file_extension}' not found in '{otp_file_folder}'.",
                  matrix_row["script_name"])
        raise FileNotFoundError(f"OTP file '{otp_file_name}.{otp_file_extension}' not found in '{otp_file_folder}'.")
    # Step 2: Read OTP from file
    with open(otp_file_path, "r") as otp_file:
        otp_code = otp_file.read().strip()
    print(f"OTP Received: {otp_code}")

    # Step 3: Remove OTP file
    os.remove(otp_file_path)
    print("Deleting OTP File.")

    return otp_code


def run_comm_ambetter(driver, matrix_row, date_info):
    print("Running COMM Ambetter handler...")

    # Extract info needed for navigation
    target_date_match = date_info["current_month_year_short"]  # e.g., "Oct 2023"
    table_url = matrix_row["source_url"]
    download_folder = os.path.normpath(matrix_row["download_path"])
    script_name = matrix_row["script_name"]

    print(f"🚀 Navigating to {table_url}...")
    driver.get(table_url)

    # --- Login Logic ---
    if matrix_row["source_login"].upper() == "YES":
        try:
            print("🔐 Attempting to log in...")
            # Input email
            WebDriverWait(driver, 10).until(
                EC.presence_of_element_located((By.XPATH, "//input[@placeholder='Email']"))
            ).send_keys(matrix_row["source_email"])

            # Input password
            driver.find_element(By.XPATH, "//input[@placeholder='Password']").send_keys(matrix_row["source_password"])

            # Click on login button
            driver.find_element(By.XPATH, "//*[@id='centerPanel']/div/div[2]/div/div[2]/div/div[3]/button").click()

            print("✅ Logged in successfully.")
            time.sleep(10)
        except TimeoutException:
            print("ℹ️ Login skipped (already logged in or elements not found).")

    # --- Step 1: Locate Check Date Row ---
    try:
        print("🔍 Searching for commission table...")
        WebDriverWait(driver, 30).until(EC.presence_of_element_located((By.ID, "commissionsTable")))
        rows = WebDriverWait(driver, 30).until(
            EC.presence_of_all_elements_located((By.CSS_SELECTOR, "#commissionsTable tbody tr")))

        print(f"📊 Found {len(rows)} rows. Searching for: {target_date_match}")

        match_found = False
        for row in rows:
            # Ambetter Column 5 is Date
            check_date = row.find_element(By.XPATH, "./td[5]").text.strip()

            if check_date == target_date_match:
                print(f"✅ Found matching row: {check_date}")
                # Ambetter Column 3 is the Link
                row.find_element(By.XPATH, "./td[3]/a").click()
                time.sleep(20)  # Wait for page load
                match_found = True
                break

        if not match_found:
            log_error(ERROR_CODES["general_error"], f"No matching check date found: {target_date_match}", script_name)
            # We return None so the runner knows it failed
            return None

    except Exception as e:
        log_error(ERROR_CODES["download_error"], f"Table interaction failed: {e}", script_name)
        return None

    # --- Step 2: Export CSV (Iframe Logic) ---
    try:
        print("🔄 Switching to iframe for Export...")
        iframe = WebDriverWait(driver, 10).until(EC.presence_of_element_located((By.TAG_NAME, "iframe")))
        driver.switch_to.frame(iframe)

        export_btn = WebDriverWait(driver, 10).until(
            EC.element_to_be_clickable((By.XPATH, "//button[@title='Export CSV']")))
        export_btn.click()
        print("🖱️ Export clicked.")
        time.sleep(10)
    except Exception as e:
        log_error(ERROR_CODES["download_error"], f"Export button failed: {e}", script_name)
        return None

    # --- Step 3: Handle Download Modal ---
    try:
        driver.switch_to.default_content()
        WebDriverWait(driver, 30).until(EC.visibility_of_element_located((By.ID, "downloadModal")))
        print("visible Download modal.")

        # Check for nested iframe inside modal
        modal_iframes = driver.find_elements(By.TAG_NAME, "iframe")
        if modal_iframes:
            driver.switch_to.frame(modal_iframes[0])
            print("🔄 Switched to modal iframe.")

        download_button = WebDriverWait(driver, 30).until(
            EC.element_to_be_clickable((By.CSS_SELECTOR, "a.btn.btn-primary[download]")))
        download_button.click()
        print("⬇️ Download button clicked.")

        # Wait for file to appear in folder
        time.sleep(30)

        log_success()  # Log success for the *download* portion

    except Exception as e:
        log_error(ERROR_CODES["download_error"], f"Download modal failed: {e}", script_name)
        return None

    return download_folder


def run_comm_anthem(driver, matrix_row, date_info):
    print("Running COMM Anthem handler...")

    # 1. Setup Variables
    url = matrix_row["source_url"]
    download_folder = os.path.normpath(matrix_row["download_path"])
    script_name = matrix_row["script_name"]

    # Anthem logic usually looks for specific month formats (e.g., "Jan 2025")
    # Verify if Anthem releases statements for Current or Previous month.
    # Usually it is Previous.
    current_month_year = date_info.get("prev_month_year")

    # Fallback to current if prev not found, or construct it manually
    if not current_month_year:
        current_month_year = datetime.now().strftime("%b %Y")

    print(f"🚀 Navigating to {url}...")
    driver.get(url)

    # 2. Login Logic
    if matrix_row["source_login"].upper() == "YES":
        try:
            print("🔐 Attempting to log in...")

            # Enter email
            email_field = WebDriverWait(driver, 10).until(
                EC.presence_of_element_located((By.XPATH, "//input[@name='username']"))
            )
            email_field.send_keys(matrix_row["source_email"])

            # Enter password
            password_field = driver.find_element(By.XPATH, "//input[@name='password']")
            password_field.send_keys(matrix_row["source_password"])

            # Specific Login Button path
            login_btn = driver.find_element(By.XPATH,
                                            "/html/body/app-root/feature-toggle-provider/app-main/div/div/app-login/div/div[2]/div[2]/div[1]/div/div[2]/form/div[2]/div[1]/button")
            login_btn.click()

            print("✅ Logged in successfully.")
            time.sleep(10)

        except TimeoutException:
            print("ℹ️ Login skipped (already logged in or elements not found).")
            log_error(ERROR_CODES["login_error"], "Login failed or not required.", script_name)

    # 3. Dashboard Switching Logic
    try:
        print("Checking dashboard status...")
        dashboard_text_element = WebDriverWait(driver, 10).until(
            EC.presence_of_element_located((By.XPATH, "//p[@class='paragraph css-lbl8vh-cssText']"))
        )

        if dashboard_text_element.text.strip() != "Switch To Medicare Dashboard":
            print("🔀 Switching to All Markets Dashboard...")
            all_markets_button = driver.find_element(By.XPATH,
                                                     "//p[contains(text(), 'Switch To All Markets Dashboard')]")
            all_markets_button.click()
            time.sleep(5)
        else:
            print("✅ Already in correct Dashboard.")

    except Exception as e:
        print(f"⚠ Minor error/check while switching dashboard: {e}")

    # 4. Navigation to Commissions
    try:
        print("Navigating to 'Book of Business'...")
        book_of_business = WebDriverWait(driver, 10).until(
            EC.element_to_be_clickable((By.XPATH, "//div[@title='Book of Business']"))
        )
        book_of_business.click()
        time.sleep(2)

        commissions_option = WebDriverWait(driver, 10).until(
            EC.element_to_be_clickable((By.XPATH, "//li[@id='mnuCommissions']"))
        )
        commissions_option.click()
        print("✅ 'Commissions' selected.")
        time.sleep(5)

    except Exception as e:
        log_error(ERROR_CODES["navigation_error"], f"Menu navigation failed: {e}", script_name)
        return None

    # 5. Process Table
    try:
        print("Waiting for Summary List View...")
        WebDriverWait(driver, 20).until(
            EC.presence_of_element_located((By.CLASS_NAME, "commissionSummaryListView"))
        )

        # Locate rows
        rows = WebDriverWait(driver, 30).until(
            EC.presence_of_all_elements_located((By.CSS_SELECTOR, ".row-cont"))
        )

        print(f"📊 Found {len(rows)} rows. Looking for Period: {current_month_year}")

        matching_row_found = False

        for row in rows:
            try:
                # Extract Period
                period_el = row.find_element(By.CSS_SELECTOR, "#Period-columnAndValue .columnValue")
                period_text = period_el.text.strip()

                # Extract Commission Amount (clean $, and check if > 0)
                comm_el = row.find_element(By.CSS_SELECTOR, "#TotalCommissionsEarned-columnAndValue .columnValue")
                comm_text = comm_el.text.strip()

                # Simple cleaning
                comm_val = 0.0
                if comm_text and comm_text != "-":
                    comm_val = float(comm_text.replace("$", "").replace(",", ""))

                print(f"🔎 Checking: {period_text} | ${comm_val}")

                # Check match
                if period_text == current_month_year and comm_val > 0:
                    print(f"✅ Match found! Expanding row...")

                    # Expand Row
                    arrow = row.find_element(By.CSS_SELECTOR, ".arrow-up")
                    driver.execute_script("arguments[0].click();", arrow)
                    time.sleep(3)

                    # Check for specific commission section
                    commission_headers = driver.find_elements(By.CSS_SELECTOR, ".commissionsWrapperChild.columnLabel")
                    section_found = any(
                        "Group, Individual and Specialty Commissions" in h.text for h in commission_headers)

                    if section_found:
                        print("✅ Found correct section. Clicking download...")

                        # Find the download link (using specific ID as per your old script)
                        # Use WebDriverWait to be safe
                        try:
                            csv_download = WebDriverWait(driver, 10).until(
                                EC.element_to_be_clickable((By.XPATH, "//*[@id='1']/div[2]/div[5]/div[2]/div/div/a"))
                            )
                        except TimeoutException:
                            # Fallback if ID '1' isn't correct for this specific row
                            print("⚠ Specific ID not found, trying generic link in expanded row...")
                            csv_download = driver.find_element(By.XPATH, "//a[contains(text(), 'CSV')]")

                        csv_download.click()
                        print("⬇️ Download initiated.")

                        # Wait for download to complete
                        time.sleep(60)

                        matching_row_found = True
                        log_success()  # Log successful download
                        break
                    else:
                        print("⚠ Correct commission section not found in this row.")

            except Exception as row_e:
                print(f"⚠ Error parsing row: {row_e}")
                continue

        if not matching_row_found:
            log_error(ERROR_CODES["general_error"], f"No matching valid commission found for {current_month_year}",
                      script_name)
            return None

    except Exception as e:
        log_error(ERROR_CODES["download_error"], f"Table processing failed: {e}", script_name)
        return None
    return download_folder


def run_comm_oscar_ga(driver, matrix_row, date_info):
    print("Running COMM Oscar GA handler...")

    try:
        url = matrix_row["source_url"]
        download_folder = os.path.normpath(matrix_row["download_path"])
        email = matrix_row["source_email"]
        password = matrix_row["source_password"]
        login_required = matrix_row["source_login"].upper() == "YES"
        current_month_year = date_info["current_month_year_short"]
        more_than_one_file = matrix_row["more_than_one_download"] == "Yes"

        driver.get(url)

        if login_required:
            try:
                email_field = WebDriverWait(driver, 60).until(
                    EC.presence_of_element_located((By.XPATH, "//input[@name='email']"))
                )
                email_field.send_keys(email)

                password_field = WebDriverWait(driver, 60).until(
                    EC.presence_of_element_located((By.XPATH, "//input[@name='password']"))
                )
                password_field.send_keys(password)
                login_button = WebDriverWait(driver, 10).until(
                    EC.element_to_be_clickable((By.XPATH, "//button[@type='submit' and contains(., 'Log in')]"))
                )
                login_button.click()
                print("Logged in successfully.")
                time.sleep(10)
            except Exception as e:
                print(f"Login failed: {e}")
                return None

        try:
            oscar_for_business_link = WebDriverWait(driver, 20).until(
                EC.element_to_be_clickable((By.XPATH, "//a[@title='Oscar For Business']"))
            )
            oscar_for_business_link.click()
            print("Clicked on 'Oscar For Business'.")
            time.sleep(10)
        except Exception as e:
            print(f"Oscar For Business navigation failed: {e}")
            return None

        try:
            menu_link = WebDriverWait(driver, 20).until(
                EC.element_to_be_clickable((By.XPATH, "//a[contains(@class, 'h-RR8CaZ7LFRek__CMKTZh')]"))
            )
            menu_link.click()
            time.sleep(5)
        except Exception as e:
            print(f"Menu navigation failed: {e}")
            return None

        try:
            comms_links = WebDriverWait(driver, 30).until(
                EC.presence_of_all_elements_located((By.XPATH, "//a[contains(@class, 'h-V9HpjLEp4EvaJfWvVq3B')]"))
            )
            comms_link = comms_links[0] if more_than_one_file else comms_links[1]
            driver.execute_script("arguments[0].scrollIntoView();", comms_link)
            time.sleep(1)
            driver.execute_script("arguments[0].click();", comms_link)
            time.sleep(10)
        except Exception as e:
            print(f"Commissions navigation failed: {e}")
            return None

        try:
            year_dropdown = WebDriverWait(driver, 20).until(
                EC.presence_of_element_located((By.XPATH, "//button[contains(@aria-owns, 'filter-by-year')]"))
            )
            year_label = driver.find_element(By.XPATH,
                                             "//div[contains(@class, 'h-MN8A3cVo21zWfTWXnG1M')]//label[starts-with(@id, 'dropdown-value-')]").text.strip()

            if year_label != date_info["current_year"]:
                driver.execute_script("arguments[0].click();", year_dropdown)
                time.sleep(2)
                year_options = WebDriverWait(driver, 10).until(
                    EC.presence_of_all_elements_located((By.XPATH, "//li[@role='option']"))
                )
                driver.execute_script("arguments[0].click();", year_options[0])
                time.sleep(5)
        except Exception as e:
            print(f"Year dropdown selection failed: {e}")

        try:
            table = WebDriverWait(driver, 20).until(
                EC.presence_of_element_located((By.XPATH, "//table[contains(@class, 'h-vByaJeB2ClPYjOMVsT4a')]"))
            )
            rows = WebDriverWait(driver, 20).until(
                EC.presence_of_all_elements_located(
                    (By.XPATH, "//table[contains(@class, 'h-vByaJeB2ClPYjOMVsT4a')]/tbody/tr"))
            )

            if not rows:
                print("No commission rows found.")
                return None

            first_row = rows[0]
            payment_sent_element = first_row.find_element(By.XPATH,
                                                          "./td[3]//div[contains(@class, 'h-EZI01T80ueSUTCG7eqH5')]")
            payment_sent_text = payment_sent_element.text.strip()
            parts = payment_sent_text.split()
            payment_month_year = f"{parts[0]} {parts[2]}" if len(parts) >= 3 else payment_sent_text

            if payment_month_year == current_month_year:
                driver.execute_script("arguments[0].click();", payment_sent_element)
                time.sleep(3)
                download_button = WebDriverWait(driver, 10).until(
                    EC.element_to_be_clickable((By.XPATH, "//button[contains(., 'Download statement')]"))
                )
                driver.execute_script("arguments[0].click();", download_button)
                print("Download triggered. Waiting...")
                time.sleep(60)
            else:
                print(f"No matching commission row for {current_month_year}.")
                return None

        except Exception as e:
            print(f"Error processing commission table: {e}")
            return None

        return download_folder

    except Exception as e:
        print(f"Unhandled error in Oscar GA handler: {e}")
        return None


def run_comm_oscar_subs(driver, matrix_row, date_info):
    print("Running COMM Oscar SUBS handler...")

    try:
        url = matrix_row["source_url"]
        download_folder = os.path.normpath(matrix_row["download_path"])
        email = matrix_row["source_email"]
        password = matrix_row["source_password"]
        login_required = matrix_row["source_login"].upper() == "YES"
        current_month_year = date_info["current_month_year"]
        current_year = date_info["current_year"]
        more_than_one_file = matrix_row["more_than_one_download"] == "No"

        driver.get(url)

        if login_required:
            try:
                print("Attempting to log in...")
                email_field = WebDriverWait(driver, 10).until(
                    EC.presence_of_element_located((By.NAME, "email"))
                )
                email_field.send_keys(email)

                password_field = driver.find_element(By.NAME, "password")
                password_field.send_keys(password)

                login_button = WebDriverWait(driver, 10).until(
                    EC.element_to_be_clickable((By.XPATH, "//button[@type='submit' and contains(., 'Log in')]"))
                )
                login_button.click()
                print("Logged in successfully.")
                time.sleep(10)

            except TimeoutException:
                print("Login not required or failed.")

        try:
            oscar_for_business_link = WebDriverWait(driver, 20).until(
                EC.element_to_be_clickable((By.XPATH, "//a[@title='Oscar For Business']"))
            )
            oscar_for_business_link.click()
            print("Clicked on 'Oscar For Business'.")
            time.sleep(10)
        except TimeoutException:
            print("'Oscar For Business' link not found.")

        try:
            print("Checking for welcome popup...")
            popup_button = WebDriverWait(driver, 5).until(
                EC.element_to_be_clickable((By.XPATH, "//button[contains(text(), 'Done')]"))
            )
            driver.execute_script("arguments[0].click();", popup_button)
            print("Popup closed successfully.")
        except TimeoutException:
            print("No welcome popup detected.")

        try:
            print("Checking for second popup...")
            popup_close_button = WebDriverWait(driver, 5).until(
                EC.element_to_be_clickable((By.XPATH, "//button[@aria-label='Close']"))
            )
            driver.execute_script("arguments[0].click();", popup_close_button)
            print("Closed second popup.")
        except TimeoutException:
            print("No second popup detected.")

        try:
            print("Navigating to menu...")
            menu_link = WebDriverWait(driver, 20).until(
                EC.element_to_be_clickable((By.XPATH, "//a[contains(@class,'h-RR8CaZ7LFRek__CMKTZh')]"))
            )
            menu_link.click()
        except TimeoutException:
            print("Menu not found.")

        try:
            print("Waiting for 'Commissions' links...")
            comms_links = WebDriverWait(driver, 30).until(
                EC.presence_of_all_elements_located((By.XPATH, "//a[contains(@class, 'h-V9HpjLEp4EvaJfWvVq3B')]"))
            )

            if len(comms_links) > 1:
                comms_link = comms_links[1]  # Second link for SUBS
                driver.execute_script("arguments[0].scrollIntoView();", comms_link)
                time.sleep(1)
                driver.execute_script("arguments[0].click();", comms_link)
                print("Clicked 'Commissions' link for SUBS.")
        except TimeoutException:
            print("'Commissions' link for SUBS not found.")

        try:
            print("Waiting for 'Select a Payee' dropdown...")
            payee_dropdown_button = WebDriverWait(driver, 20).until(
                EC.presence_of_element_located((By.XPATH, "//button[contains(@aria-owns, 'select-a-payee')]"))
            )

            current_payee_label = driver.find_element(By.XPATH,
                                                      "//label[contains(text(), 'Select a payee')]").text.strip()

            if current_payee_label != "Agility Insurance Services":
                print("Selecting 'Agility Insurance Services' from dropdown...")
                payee_label = driver.find_element(By.XPATH, "//label[contains(text(), 'Select a payee')]")
                driver.execute_script("arguments[0].scrollIntoView();", payee_label)
                time.sleep(1)
                driver.execute_script("arguments[0].click();", payee_label)

                payee_option = WebDriverWait(driver, 10).until(
                    EC.element_to_be_clickable(
                        (By.XPATH, "//li[@role='option']/div[contains(text(), 'Agility Insurance Services')]"))
                )
                driver.execute_script("arguments[0].scrollIntoView();", payee_option)
                time.sleep(1)
                driver.execute_script("arguments[0].click();", payee_option)

                print("Successfully selected 'Agility Insurance Services'.")
        except TimeoutException:
            print("'Select a Payee' dropdown not found.")

        try:
            print("Waiting for the commission table to load...")
            table = WebDriverWait(driver, 20).until(
                EC.presence_of_element_located((By.XPATH, "//table[contains(@class, 'h-vByaJeB2ClPYjOMVsT4a')]"))
            )
            print("✅ Table loaded successfully.")

            rows = WebDriverWait(driver, 20).until(
                EC.presence_of_all_elements_located(
                    (By.XPATH, "//table[contains(@class, 'h-vByaJeB2ClPYjOMVsT4a')]/tbody/tr"))
            )

            if not rows:
                print("⚠ No rows found in the table.")
                return None

            print(f"🔍 Found {len(rows)} rows. Checking the first row...")
            first_row = rows[0]

            try:
                payment_sent_element = first_row.find_element(By.XPATH,
                                                              "./td[3]//div[contains(@class, 'h-EZI01T80ueSUTCG7eqH5')]")
                payment_sent_text = payment_sent_element.text.strip()
                print(f"📅 Checking row: Full Payment Sent Date = {payment_sent_text}")

                payment_sent_parts = payment_sent_text.split()
                if len(payment_sent_parts) >= 3:
                    payment_month_year = f"{payment_sent_parts[0]} {payment_sent_parts[2]}"
                else:
                    payment_month_year = payment_sent_text

                print(f"📆 Extracted Payment Sent Month-Year: {payment_month_year}")

            except Exception as e:
                print(f"⚠ Error extracting payment sent date: {e}")
                return None

            if payment_month_year == current_month_year:
                print(f"✅ Matching row found for {current_month_year}")

                try:
                    driver.execute_script("arguments[0].scrollIntoView();", payment_sent_element)
                    time.sleep(1)
                    driver.execute_script("arguments[0].click();", payment_sent_element)
                    print("🔽 Clicked on Payment Sent date to expand the row.")
                    time.sleep(3)
                except Exception as e:
                    print(f"⚠ Error clicking 'Payment Sent' date: {e}")
                    return None

                try:
                    download_button = WebDriverWait(driver, 10).until(
                        EC.element_to_be_clickable((By.XPATH, "//button[contains(., 'Download statement')]"))
                    )
                    driver.execute_script("arguments[0].scrollIntoView();", download_button)
                    time.sleep(1)
                    driver.execute_script("arguments[0].click();", download_button)
                    print("📥 Download initiated successfully.")
                    driver.quit()
                except Exception as e:
                    print(f"⚠ Error clicking download button: {e}")
                    return None
            else:
                print(f"⚠ No matching row found for {current_month_year}. Skipping.")

        except Exception as e:
            print(f"❌ Error processing table rows: {e}")
            return None

    except Exception as e:
        print(f"Unhandled error in Oscar SUBS handler: {e}")
        return download_folder


def run_comm_molina(driver, matrix_row, date_info):
    print("Running COMM Molina handler...")

    # 1. Setup Variables
    url = matrix_row["source_url"]
    download_folder = os.path.normpath(matrix_row["download_path"])
    script_name = matrix_row["script_name"]

    # Date logic for filtering
    current_year = date_info["current_year"]
    current_month_number = date_info["current_month_number"]

    # Dates for input fields
    # Assuming date_info has keys like 'first_of_three_months_prior' from your util
    date_from_val = date_info.get("first_of_three_months_prior")
    date_to_val = date_info.get("last_of_current_month")

    if not date_from_val or not date_to_val:
        log_error(ERROR_CODES["general_error"], "Missing date range for search.", script_name)
        return None

    print(f"🚀 Navigating to {url}...")
    driver.get(url)

    # 2. Login Logic
    if matrix_row["source_login"].upper() == "YES":
        try:
            print("🔐 Attempting to log in...")
            WebDriverWait(driver, 10).until(
                EC.presence_of_element_located((By.XPATH, "//input[@name='login_id']"))
            ).send_keys(matrix_row["source_email"])

            driver.find_element(By.XPATH, "//input[@name='password']").send_keys(matrix_row["source_password"])
            driver.find_element(By.XPATH, "//input[@name='submit']").click()

            print("✅ Logged in successfully.")
            time.sleep(10)
        except TimeoutException:
            print("ℹ️ Login skipped (already logged in or elements not found).")
            log_error(ERROR_CODES["login_error"], "Login failed or not required.", script_name)

    # 3. Switch Domain (Tabs)
    try:
        original_window = driver.current_window_handle
        print("Clicking Domain/Portal link...")

        # Click the tile/link that opens the new tab
        WebDriverWait(driver, 15).until(
            EC.element_to_be_clickable((By.XPATH, "//div[@class='card-header py-3']/h5[text()='Molina']"))
        ).click()

        # Wait for new tab
        WebDriverWait(driver, 10).until(EC.number_of_windows_to_be(2))

        # Switch to new tab
        for handle in driver.window_handles:
            if handle != original_window:
                driver.switch_to.window(handle)
                break
        print("✅ Switched to Molina Portal tab.")
        time.sleep(5)  # Let the new tab load

    except TimeoutException:
        log_error(ERROR_CODES["navigation_error"], "Failed to switch domain/tab.", script_name)
        return None

    # 4. Navigate to Statements
    try:
        print("Navigating to Statements...")
        WebDriverWait(driver, 20).until(
            EC.element_to_be_clickable((By.XPATH, "//a[@class='nav-link']/span[text()='Statements ']"))
        ).click()
        print("✅ Statements page loaded.")
        time.sleep(5)
    except TimeoutException:
        log_error(ERROR_CODES["navigation_error"], "Statements link not found.", script_name)
        return None

    # 5. Search Filters
    try:
        print(f"Setting Date Range: {date_from_val} to {date_to_val}")

        # Statement From
        stmt_from = WebDriverWait(driver, 10).until(EC.presence_of_element_located((By.ID, "statement_from")))
        stmt_from.clear()
        stmt_from.send_keys(date_from_val)

        # Statement To
        stmt_to = driver.find_element(By.ID, "statement_to")
        stmt_to.clear()
        stmt_to.send_keys(date_to_val)
        stmt_to.send_keys(Keys.TAB)  # Tab out to trigger validation if needed

        # Click Search
        search_btn = WebDriverWait(driver, 10).until(
            EC.element_to_be_clickable((By.XPATH, "//*[@name='searchMember']"))
        )
        search_btn.click()
        print("🔎 Search button clicked. Waiting for results...")

        # Explicit wait for table to appear
        WebDriverWait(driver, 30).until(EC.presence_of_element_located((By.ID, "portal_member")))
        time.sleep(2)  # Allow table render to finish

    except Exception as e:
        log_error(ERROR_CODES["input_error"], f"Search filter interaction failed: {e}", script_name)
        return None

    # 6. Process Results Table
    try:
        print("Processing search results...")

        # Find the first data row (usually class 'odd' or just the first row in tbody)
        try:
            first_row = WebDriverWait(driver, 10).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, "#portal_member tbody tr.odd"))
            )
        except TimeoutException:
            # Fallback if 'odd' class isn't strictly used
            first_row = driver.find_element(By.CSS_SELECTOR, "#portal_member tbody tr")

        # Extract Date
        # Verify if column index 0 (sorting_1) holds the date. Adjust index if column order changes.
        date_cell = first_row.find_element(By.CSS_SELECTOR, "td.text-center.sorting_1")
        stmt_date_text = date_cell.text.strip()

        try:
            stmt_date = datetime.strptime(stmt_date_text, "%m/%d/%Y")
            stmt_month = stmt_date.strftime("%m")
            stmt_year = stmt_date.strftime("%Y")
            print(f"📅 Found Statement Date: {stmt_date_text} (Month: {stmt_month}, Year: {stmt_year})")
        except ValueError:
            print(f"⚠ Could not parse date: {stmt_date_text}")
            return None

        # Check Match
        if stmt_month == current_month_number and stmt_year == current_year:
            print("✅ Date matches current month. Downloading...")

            # Find download link in that row
            try:
                download_link = first_row.find_element(By.CSS_SELECTOR, "a.card-link")
                download_link.click()
                print("⬇️ Download initiated.")

                time.sleep(60)  # Wait for file download
                log_success()
                return download_folder
            except NoSuchElementException:
                log_error(ERROR_CODES["download_error"], "Download link not found in row.", script_name)
                return None
        else:
            print(f"⚠ Date {stmt_date_text} does not match target {current_month_number}/{current_year}. Skipping.")
            return None

    except Exception as e:
        log_error(ERROR_CODES["table_error"], f"Error processing table rows: {e}", script_name)
        return None


def run_acu_ambetter(driver, matrix_row, date_info):
    print("Running ACU Ambetter handler...")

    download_folder = os.path.normpath(matrix_row["download_path"])
    driver.get(matrix_row["source_url"])

    # Perform login if needed
    if matrix_row["source_login"].upper() == "YES":
        try:
            email_field = WebDriverWait(driver, 10).until(
                EC.presence_of_element_located((By.XPATH, "//input[@placeholder='Username']"))
            )
            email_field.send_keys(matrix_row["source_email"])
            password_field = driver.find_element(By.XPATH, "//input[@placeholder='Password']")
            password_field.send_keys(matrix_row["source_password"])
            login_button = driver.find_element(By.XPATH,
                                               "//*[@id='centerPanel']/div/div[2]/div/div[2]/div/div[3]/button")
            login_button.click()
            print("Logged in successfully!")
            time.sleep(30)
        except Exception as e:
            log_error(ERROR_CODES["login_error"], "Login page timeout or login fields not found.",
                      matrix_row["script_name"])
            print("Login page not found or timeout occurred. Exiting...")
            driver.quit()
            return None

    # Step 2: Click the download button
    try:
        download_button = WebDriverWait(driver, 30).until(
            EC.element_to_be_clickable((By.XPATH, "//*[contains(@id, 'j_id')]/div/table/tbody/tr/td[1]/a"))
        )
        print("Download button found")
        download_button.click()
        print("Download button clicked, file should start downloading...")
        time.sleep(10)
    except Exception as e:
        log_error(ERROR_CODES["download_button_not_found"], "'Download' button not found.", matrix_row["script_name"])
        print("Download button not found, ending process.")
        driver.quit()
        return None

    return download_folder


def run_acu_christus(driver, matrix_row, date_info):
    print("Running ACU CHRISTUS Health handler...")

    download_folder = os.path.normpath(matrix_row["download_path"])
    script_name = matrix_row["script_name"]

    # ──────────────────────────────────────────────
    # STEP 1: Login to EvolveNXT
    # ──────────────────────────────────────────────
    driver.get(matrix_row["source_url"])
    time.sleep(5)

    if matrix_row["source_login"].upper() == "YES":
        try:
            # EvolveNXT login fields can be "not interactable" — use JS fallback
            WebDriverWait(driver, 15).until(
                EC.presence_of_element_located((By.ID, "login_id"))
            )

            # Email — try send_keys on visible field, fallback to JS
            login_fields = driver.find_elements(By.ID, "login_id")
            try:
                visible_field = next(el for el in login_fields if el.is_displayed())
                visible_field.clear()
                visible_field.send_keys(matrix_row["source_email"])
                print("Entered email via send_keys.")
            except (StopIteration, Exception):
                driver.execute_script(
                    "document.getElementById('login_id').value = arguments[0];",
                    matrix_row["source_email"]
                )
                print("Entered email via JS.")

            # Password — try send_keys, fallback to JS
            try:
                pwd_field = driver.find_element(By.ID, "password")
                if pwd_field.is_displayed():
                    pwd_field.clear()
                    pwd_field.send_keys(matrix_row["source_password"])
                    print("Entered password via send_keys.")
                else:
                    raise Exception("not displayed")
            except Exception:
                driver.execute_script(
                    "document.getElementById('password').value = arguments[0];",
                    matrix_row["source_password"]
                )
                print("Entered password via JS.")

            # Submit — try click, fallback to JS click
            login_btn = WebDriverWait(driver, 10).until(
                EC.presence_of_element_located((By.ID, "submit"))
            )
            try:
                login_btn.click()
                print("Login submitted via click.")
            except Exception:
                driver.execute_script("document.getElementById('submit').click();")
                print("Login submitted via JS click.")
            time.sleep(15)

        except Exception as e:
            log_error(ERROR_CODES["login_error"],
                      f"Login failed: {e}", script_name)
            print(f"Login failed: {e}")
            driver.quit()
            return None

    # ──────────────────────────────────────────────
    # STEP 2: Handle password expiry modal if present
    # ──────────────────────────────────────────────
    print("Checking for password expiry modal...")

    try:
        continue_login_btn = WebDriverWait(driver, 5).until(
            EC.element_to_be_clickable((By.ID, "goToDomain"))
        )
        continue_login_btn.click()
        print("Password expiry modal dismissed.")
        time.sleep(10)
    except TimeoutException:
        print("No password expiry modal.")

    # ──────────────────────────────────────────────
    # STEP 3: Select CHRISTUS portal card (opens new tab)
    # ──────────────────────────────────────────────
    try:
        print("Checking for domain/portal selection screen...")
        original_window = driver.current_window_handle

        christus_card = WebDriverWait(driver, 15).until(
            EC.element_to_be_clickable((By.XPATH,
                                        "//div[@class='card-body' and contains(@onclick, 'christus.evolvenxt.com')]"))
        )
        christus_card.click()
        print("Clicked CHRISTUS Health portal card.")
        time.sleep(5)

        # Switch to new tab
        WebDriverWait(driver, 15).until(EC.number_of_windows_to_be(2))
        for handle in driver.window_handles:
            if handle != original_window:
                driver.switch_to.window(handle)
                break
        print(f"Switched to CHRISTUS portal tab: {driver.current_url}")
        time.sleep(10)

    except TimeoutException:
        print("No domain selection screen — already redirected. Continuing.")
    except Exception as e:
        log_error(ERROR_CODES["navigation_error"],
                  f"Failed to select CHRISTUS portal: {e}", script_name)
        driver.quit()
        return None

    # ──────────────────────────────────────────────
    # STEP 3: My Downline Brokers → Broker Credentials
    # ──────────────────────────────────────────────
    try:
        print("Navigating to My Downline Brokers...")

        # Click "My Downline Brokers" sidebar link
        try:
            downline_link = WebDriverWait(driver, 60).until(
                EC.element_to_be_clickable((By.XPATH,
                                            "//a[@href='/portal/mpc_detail.htm' and @data-target='#collapse_15']"))
            )
            driver.execute_script("arguments[0].scrollIntoView(true);", downline_link)
            time.sleep(1)
            downline_link.click()
            print("Clicked 'My Downline Brokers'.")
        except TimeoutException:
            # Fallback: text match
            downline_link = WebDriverWait(driver, 15).until(
                EC.element_to_be_clickable((By.XPATH,
                                            "//a[contains(.,'My Downline Brokers')]"))
            )
            downline_link.click()
            print("Clicked 'My Downline Brokers' (fallback).")
        time.sleep(3)

        # Click "Broker Credentials" sub-menu
        try:
            broker_creds = WebDriverWait(driver, 30).until(
                EC.element_to_be_clickable((By.XPATH,
                                            "//a[@class='collapse-item' and @href='/portal/mpc_detail.htm']"))
            )
            broker_creds.click()
            print("Clicked 'Broker Credentials'.")
        except TimeoutException:
            broker_creds = WebDriverWait(driver, 15).until(
                EC.element_to_be_clickable((By.XPATH,
                                            "//a[contains(text(),'Broker Credentials')]"))
            )
            broker_creds.click()
            print("Clicked 'Broker Credentials' (fallback).")
        time.sleep(10)

    except Exception as e:
        log_error(ERROR_CODES["navigation_error"],
                  f"Navigation to Broker Credentials failed: {e}", script_name)
        print(f"Navigation failed: {e}")
        driver.quit()
        return None

    # ──────────────────────────────────────────────
    # STEP 4: Click "Download Rep Status"
    # ──────────────────────────────────────────────
    try:
        print("Waiting for 'Download Rep Status' button...")

        try:
            download_btn = WebDriverWait(driver, 60).until(
                EC.element_to_be_clickable((By.ID, "producer_status"))
            )
        except TimeoutException:
            # Fallback
            download_btn = WebDriverWait(driver, 15).until(
                EC.element_to_be_clickable((By.XPATH,
                                            "//button[contains(@onclick,'prod_stat') or contains(text(),'Download Rep Status')]"))
            )

        driver.execute_script("arguments[0].scrollIntoView(true);", download_btn)
        time.sleep(1)
        download_btn.click()
        print("Clicked 'Download Rep Status'.")
        time.sleep(120)  # Wait for file download

        log_success()

    except Exception as e:
        log_error(ERROR_CODES["download_button_not_found"],
                  f"'Download Rep Status' button not found: {e}", script_name)
        print(f"Download button not found: {e}")
        driver.quit()
        return None

    return download_folder


"""
def run_acu_aetna(driver, matrix_row, date_info):
    print("Running ACU Aetna handler...")

    download_folder = os.path.normpath(matrix_row["download_path"])
    driver.get(matrix_row["source_url"])

    # Perform login if needed
    if matrix_row["source_login"].upper() == "YES":
        try:
            print("Implement Login Functionality")
        except Exception as e:
            log_error(ERROR_CODES["login_error"], "Login page timeout or login fields not found.")
            print("Login page not found or timeout occurred. Exiting...")
            driver.quit()
            return None

    # Navigate to download page
    try:
        print("Implement navigation functionality (if needed)")
    except Exception as e:
        log_error(ERROR_CODES["navigation_error"], "Navigation process failed.")
        print("Navigation process failed, ending process.")
        driver.quit()
        return None

    # Click the download button
    try:
        print("Implement download functionality")
    except Exception as e:
        log_error(ERROR_CODES["download_button_not_found"], "'Download' button not found.")
        print("Download button not found, ending process.")
        driver.quit()
        return None

    return download_folder

def run_acu_aetnasenior_supp(driver, matrix_row, date_info):
    print("Running ACU AetnaSenior handler...")

    download_folder = os.path.normpath(matrix_row["download_path"])
    driver.get(matrix_row["source_url"])

    # Perform login if needed
    if matrix_row["source_login"].upper() == "YES":
        try:
            print("Implement Login Functionality")
        except Exception as e:
            log_error(ERROR_CODES["login_error"], "Login page timeout or login fields not found.")
            print("Login page not found or timeout occurred. Exiting...")
            driver.quit()
            return None

    # Navigate to download page
    try:
        print("Implement navigation functionality (if needed)")
    except Exception as e:
        log_error(ERROR_CODES["navigation_error"], "Navigation process failed.")
        print("Navigation process failed, ending process.")
        driver.quit()
        return None

    # Click the download button
    try:
        print("Implement download functionality")
    except Exception as e:
        log_error(ERROR_CODES["download_button_not_found"], "'Download' button not found.")
        print("Download button not found, ending process.")
        driver.quit()
        return None

    return download_folder
"""


def run_acu_alignment(driver, matrix_row, date_info):
    print("Running ACU Alignment handler...")

    download_folder = os.path.normpath(matrix_row["download_path"])
    driver.get(matrix_row["source_url"])

    # Perform login if needed
    if matrix_row["source_login"].upper() == "YES":
        try:
            # Step 2: Enter Username
            WebDriverWait(driver, 10).until(EC.presence_of_element_located((By.ID, "logonIdentifier"))).send_keys(
                matrix_row["source_email"]
            )
            driver.find_element(By.ID, "password").send_keys(matrix_row["source_password"])
            driver.find_element(By.ID, "next").click()
            print("Logged in successfully!")
            time.sleep(10)

            # Step 3: Verify page load after login
            WebDriverWait(driver, 60).until(EC.url_contains("agents.alignmenthealthcare.com"))
            time.sleep(10)
        except Exception as e:
            log_error(ERROR_CODES["login_error"], "Login page timeout or login fields not found.",
                      matrix_row["script_name"])
            print("Login page not found or timeout occurred. Exiting...")
            driver.quit()
            return None

    # Navigate to download page
    try:
        # Step 4: Click on "MY AGENTS" link
        WebDriverWait(driver, 10).until(
            EC.element_to_be_clickable((By.CSS_SELECTOR, "a.nav-link[href='/my-clients']"))
        ).click()
        print("Navigated to 'MY AGENTS' page.")
        time.sleep(10)

        # Step 5: Click on "Agent Clients" button
        WebDriverWait(driver, 10).until(
            EC.element_to_be_clickable(
                (By.XPATH, '//*[@id="root"]/div/div/div[1]/div[3]/div/div[2]/div/div/div/button[3]'))
        ).click()
        print("Clicked on 'Agent Clients'.")
        time.sleep(10)
    except Exception as e:
        log_error(ERROR_CODES["navigation_error"], "Navigation process failed.", matrix_row["script_name"])
        print("Navigation process failed, ending process.")
        driver.quit()
        return None

    # Click the download button
    try:
        # Step 6: Click on "Enrollment Report" button
        enrollment_report_button = WebDriverWait(driver, 20).until(
            EC.element_to_be_clickable((By.CSS_SELECTOR, "a.authenticated-file-link.buttonEnrollmentReport"))
        )
        driver.execute_script("arguments[0].scrollIntoView(true);", enrollment_report_button)
        driver.execute_script("arguments[0].click();", enrollment_report_button)
        print("Clicked on 'Enrollment Report'.")
        time.sleep(60)  # Adjust based on download speed
    except Exception as e:
        log_error(ERROR_CODES["download_button_not_found"], "'Download' button not found.", matrix_row["script_name"])
        print("Download button not found, ending process.")
        driver.quit()
        return None

    return download_folder


def run_acu_allstate(driver, matrix_row, date_info):
    print("Running ACU Allstate handler...")

    download_folder = os.path.normpath(matrix_row["download_path"])
    script_name = matrix_row["script_name"]

    try:
        # STEP 1: Login
        driver.get(matrix_row["source_url"])
        time.sleep(5)

        if matrix_row["source_login"].upper() == "YES":
            print("Logging in to Allstate / NGIC portal...")

            username_field = WebDriverWait(driver, 30).until(
                EC.element_to_be_clickable((By.ID, "usernameInput"))
            )
            username_field.clear()
            username_field.send_keys(matrix_row["source_email"])

            password_field = WebDriverWait(driver, 30).until(
                EC.element_to_be_clickable((By.ID, "Password"))
            )
            password_field.clear()
            password_field.send_keys(matrix_row["source_password"])

            login_btn = WebDriverWait(driver, 10).until(
                EC.element_to_be_clickable((By.ID, "loginButton"))
            )
            login_btn.click()

            print("Login submitted.")
            time.sleep(10)

        # STEP 2: Handle OTP
        print("Handling OTP...")

        mark_matching_as_read(
            sender="account-noreply@ngic.com",
            subject="One Time Password",
            mailbox="support@enrollinsurance.com",
        )

        try:
            otp_input = WebDriverWait(driver, 10).until(
                EC.element_to_be_clickable((By.ID, "TwoFactorCode"))
            )
        except TimeoutException:
            print("No OTP page detected, may already be authenticated.")
            otp_input = None

        if otp_input:
            print("Waiting 10s for resend cooldown...")
            time.sleep(10)

            try:
                resend_link = WebDriverWait(driver, 5).until(
                    EC.element_to_be_clickable((By.ID, "sendAnotherCodeLink"))
                )
                resend_link.click()
                print("Clicked 'Send another code'.")
                time.sleep(3)
            except TimeoutException:
                print("No resend link found, OTP may have been sent on login.")

            otp_sent_at = datetime.now(timezone.utc)

            code = fetch_otp_code(
                sender="account-noreply@ngic.com",
                subject="One Time Password",
                mailbox="support@enrollinsurance.com",
                since_dt_utc=otp_sent_at,
                poll_seconds=120,
            )

            print(f"Entering OTP: {code}")
            otp_input.clear()
            otp_input.send_keys(code)
            time.sleep(1)

            verify_btn = WebDriverWait(driver, 10).until(
                EC.element_to_be_clickable((By.ID, "verifyButton"))
            )
            verify_btn.click()

            print("OTP verified.")
            time.sleep(10)

        # STEP 3: Click ABO link
        print("Navigating to ABO...")

        abo_link = WebDriverWait(driver, 30).until(
            EC.element_to_be_clickable((By.CSS_SELECTOR, "a[href='https://ngahagents.ngic.com']"))
        )
        abo_link.click()
        time.sleep(10)

        # STEP 4: Select Agility profile
        print("Selecting Agility Insurance profile...")

        npn_dropdown = WebDriverWait(driver, 30).until(
            EC.presence_of_element_located((By.ID, "SelectedNPN"))
        )
        time.sleep(2)

        select = Select(npn_dropdown)
        select.select_by_value("15124705")

        print("Selected NPN 15124705.")
        time.sleep(3)

        continue_btn = WebDriverWait(driver, 10).until(
            EC.element_to_be_clickable((By.XPATH, "//button[@type='submit' and contains(text(), 'Continue')]"))
        )
        continue_btn.click()

        print("Continue clicked.")
        time.sleep(10)

        # STEP 5: Click Agents
        print("Clicking Agents...")

        agents_link = WebDriverWait(driver, 30).until(
            EC.element_to_be_clickable(
                (By.XPATH, "//span[contains(@class, 'menu-font-size') and contains(text(), 'Agents')]"))
        )
        agents_link.click()

        print("Agents page loading...")
        time.sleep(10)

        # STEP 6: Set Status to Active
        print("Setting Status filter to Active...")

        status_dropdown = WebDriverWait(driver, 30).until(
            EC.presence_of_element_located((By.ID, "ddlLStatus"))
        )
        driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", status_dropdown)
        time.sleep(1)

        select_status = Select(status_dropdown)
        select_status.select_by_value("Active")

        print("Status set to Active.")
        time.sleep(1)

        # STEP 7: Click Search
        print("Clicking Search...")

        search_btn = WebDriverWait(driver, 10).until(
            EC.element_to_be_clickable((By.ID, "btnTopApply"))
        )
        driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", search_btn)
        time.sleep(1)
        search_btn.click()

        print("Searching... waiting for results...")
        time.sleep(30)

        # STEP 8: Wait for results
        print("Waiting for results...")

        WebDriverWait(driver, 60).until(
            EC.presence_of_element_located((By.ID, "agentList"))
        )

        try:
            WebDriverWait(driver, 60).until(
                EC.invisibility_of_element_located((By.ID, "agentList_processing"))
            )
        except TimeoutException:
            print("Processing indicator didn't disappear, proceeding anyway.")

        info = driver.find_element(By.ID, "agentList_info").text
        print(f"Results: {info}")
        time.sleep(5)

        # STEP 9: Download
        print("Downloading...")

        download_btn = WebDriverWait(driver, 30).until(
            EC.element_to_be_clickable((By.XPATH,
                                        "//button[contains(@class, 'btn-primary-light-blue') and contains(text(), 'download')]"
                                        ))
        )
        download_btn.click()
        print("Download modal opened...")
        time.sleep(3)

        download_now_btn = WebDriverWait(driver, 15).until(
            EC.element_to_be_clickable((By.CSS_SELECTOR, "button.csv-download-button"))
        )
        download_now_btn.click()

        print("Download triggered, waiting for file...")
        time.sleep(60)

        log_success()
        return download_folder

    except Exception as e:
        log_error(
            ERROR_CODES["general_error"],
            f"ACU Allstate handler failed: {e}",
            script_name
        )
        print(f"ACU Allstate handler failed: {e}")
        driver.quit()
        return None


def run_acu_americanamicable(driver, matrix_row, date_info):
    print("Running ACU AmericanAmicable handler...")

    download_folder = os.path.normpath(matrix_row["download_path"])
    driver.get(matrix_row["source_url"])

    # Perform login if needed
    if matrix_row["source_login"].upper() == "YES":
        try:
            # Step 2: Enter username
            WebDriverWait(driver, 30).until(
                EC.presence_of_element_located((By.ID, "user"))
            ).send_keys(matrix_row["source_email"])

            # Step 3: Enter password
            driver.find_element(By.ID, "password").send_keys(matrix_row["source_password"])

            # Step 4: Click Submit
            driver.find_element(By.XPATH, "//form/div/input").click()
            print("Logged in successfully!")
            time.sleep(10)
        except Exception as e:
            log_error(ERROR_CODES["login_error"], "Login page timeout or login fields not found.",
                      matrix_row["script_name"])
            print("Login page not found or timeout occurred. Exiting...")
            driver.quit()
            return None

    # Navigate to download page
    try:
        # Step 5: Navigate to the Marketing area
        driver.get("https://www.americanamicable.com/Marketing/area/A/marketing.php")
        print("Navigated to Marketing area.")
        time.sleep(5)

        # Step 6: Navigate to Agent E-file
        driver.get("https://www.americanamicable.com/cgi/agtefile/")
        print("Navigated to Agent E-file.")
        time.sleep(5)

        cookies = driver.get_cookies()
        print("Cookies after login:", cookies)
    except Exception as e:
        log_error(ERROR_CODES["navigation_error"], "Navigation process failed.", matrix_row["script_name"])
        print("Navigation process failed, ending process.")
        driver.quit()
        return None

    # Click the download button
    try:
        # Step 7: Click the download button and retrieve the link directly
        download_button = WebDriverWait(driver, 10).until(
            EC.presence_of_element_located(
                (By.XPATH, "//a[contains(@href, 'agtefile.exe')]/img[contains(@src, 'InfoCSV.png')]"))
        )
        driver.execute_script("arguments[0].scrollIntoView(true);", download_button)
        download_href = download_button.find_element(By.XPATH, "..").get_attribute("href")

        if download_href:
            print("Download link found:", download_href)
            driver.get(download_href)
            print("Download button clicked, file should start downloading...")
        else:
            print("Download link not found.")
        time.sleep(10)
    except Exception as e:
        log_error(ERROR_CODES["download_error"], "Download process failed.", matrix_row["script_name"])
        print("Download process failed, ending process.")
        driver.quit()
        return None

    return download_folder


def run_acu_caresource(driver, matrix_row, date_info):
    print("Running ACU Caresource handler...")

    today_date = datetime.now().strftime("%m%d%Y")
    current_year = datetime.now().strftime("%Y")
    download_folder = os.path.normpath(matrix_row["download_path"])
    driver.get(matrix_row["source_url"])

    # Perform login if needed
    if matrix_row["source_login"].upper() == "YES":
        try:
            print("Logging in...")
            email_field = WebDriverWait(driver, 10).until(
                EC.presence_of_element_located((By.XPATH, "//input[@placeholder='Email or User Name']"))
            )
            email_field.send_keys(matrix_row["source_email"])

            password_field = WebDriverWait(driver, 10).until(
                EC.presence_of_element_located((By.XPATH, "//input[@placeholder='Password']"))
            )
            password_field.send_keys(matrix_row["source_password"])

            login_button = WebDriverWait(driver, 10).until(
                EC.element_to_be_clickable((By.CSS_SELECTOR, "button[id*='logOnFormSubmit']"))
            )
            login_button.click()
            time.sleep(15)
            # WebDriverWait(driver, 120).until(
            # EC.element_to_be_clickable((By.XPATH, "//*[@id='tfaChoiceEmailButton']"))
            # ).click()
            print("Code sent!")
            time.sleep(10)  # Wait for 2FA process

            if matrix_row["otp_needed"].upper() == "YES":
                try:
                    otp_code = fetch_otp_code_from_file(matrix_row)
                    print(f"OTP Code found: {otp_code}")

                    # Input OTP
                    WebDriverWait(driver, 30).until(
                        EC.presence_of_element_located((By.XPATH, "//*[@id='j_otpcode']"))
                    ).send_keys(otp_code)
                    print("OTP entered.")

                    # Click Submit
                    WebDriverWait(driver, 30).until(
                        EC.element_to_be_clickable(
                            (By.XPATH, "//*[@id='logon_continue']"))
                    ).click()
                    print("OTP submitted.")
                    time.sleep(10)

                except FileNotFoundError:
                    log_error(ERROR_CODES["OTP_error"], "OTP File was not found or was not submitted correctly.",
                              matrix_row["script_name"])
                    print("OTP File was not found or was not submitted correctly. Exiting...")
                    driver.quit()
                    return None
        except Exception as e:
            log_error(ERROR_CODES["login_error"], "Login page timeout or login fields not found.",
                      matrix_row["script_name"])
            print("Login page not found or timeout occurred. Exiting...")
            driver.quit()
            return None

    # Navigate to download page
    try:
        driver.refresh()
        time.sleep(30)
        print(f"Selecting period year: {current_year}")

        # Click dropdown
        dropdown_arrow = WebDriverWait(driver, 10).until(
            EC.element_to_be_clickable(
                (By.ID, "application-OverviewPage-display-component---dashboard--PeriodYear-arrow"))
        )
        dropdown_arrow.click()
        time.sleep(2)  # Wait for dropdown to open

        # Select the correct year
        year_option = WebDriverWait(driver, 10).until(
            EC.element_to_be_clickable((By.XPATH, f"//li[contains(text(), '{current_year}')]"))
        )
        year_option.click()
        print(f"Successfully set Period Year to {current_year}")

        print("Opening 'Agent List'...")
        agent_list_button = WebDriverWait(driver, 10).until(
            EC.element_to_be_clickable(
                (By.XPATH, "//div[contains(@class, 'sapFCardHeader')]//span[contains(text(), 'Agent List')]"))
        )
        agent_list_button.click()
        print("'Agent List' opened.")
        time.sleep(60)  # Allow time for content to load
    except Exception as e:
        log_error(ERROR_CODES["navigation_error"], "Navigation process failed.", matrix_row["script_name"])
        print("Navigation process failed, ending process.")
        driver.quit()
        return None

    # Click the download button
    try:
        print("Initiating download...")

        download_button = WebDriverWait(driver, 10).until(
            EC.element_to_be_clickable((By.XPATH, "//button[@title='Export']"))
        )
        download_button.click()
        time.sleep(2)

        # Enter file name
        file_name = f"raw_acu_caresource_aca_{today_date}"
        file_input = WebDriverWait(driver, 10).until(
            EC.presence_of_element_located((By.XPATH, "//input[contains(@id, 'filename-input-inner')]"))
        )
        file_input.clear()
        file_input.send_keys(file_name)
        print(f"Entered file name: {file_name}")

        # Click export
        export_button = WebDriverWait(driver, 10).until(
            EC.element_to_be_clickable((By.XPATH, "//button[contains(@id, 'Export')]"))
        )
        export_button.click()
        print("Clicked 'Export' button")

        time.sleep(60)  # Wait for file to download
    except Exception as e:
        log_error(ERROR_CODES["download_button_not_found"], "'Download' button not found.", matrix_row["script_name"])
        print("Download button not found, ending process.")
        driver.quit()
        return None

    return download_folder


def run_acu_gerber(driver, matrix_row, date_info):
    print("Running ACU Gerber handler...")

    download_folder = os.path.normpath(matrix_row["download_path"])
    driver.get(matrix_row["source_url"])

    # Perform login if needed
    if matrix_row["source_login"].upper() == "YES":
        try:
            # Step 2: Enter username
            WebDriverWait(driver, 30).until(
                EC.presence_of_element_located((By.ID, "UserID"))
            ).send_keys(matrix_row["source_email"])

            # Step 3: Enter password
            WebDriverWait(driver, 30).until(
                EC.presence_of_element_located((By.ID, "Password"))
            ).send_keys(matrix_row["source_password"])

            # Step 4: Click Log In
            WebDriverWait(driver, 30).until(
                EC.presence_of_element_located((By.XPATH, "//input[@type='submit' and @value='Log in']"))
            ).click()
            print("Logged in successfully!")
            time.sleep(5)

            # Step 5: Wait for 60 seconds
            print("Waiting for 20 seconds to ensure page load...")
            time.sleep(20)
        except Exception as e:
            log_error(ERROR_CODES["login_error"], "Login page timeout or login fields not found.",
                      matrix_row["script_name"])
            print("Login page not found or timeout occurred. Exiting...")
            driver.quit()
            return None

    # Navigate to download page
    try:
        # Step 6: Click on 'Commissions & Appointments'
        print("Attempting to click Commissions & Appointments")
        commissions_appointments = WebDriverWait(driver, 30).until(
            EC.element_to_be_clickable((By.LINK_TEXT, "Commissions & Appointments"))
        )
        commissions_appointments.click()
        print("Clicked on 'Commissions & Appointments'.")

        # Step 7: Switch to the new tab
        driver.switch_to.window(driver.window_handles[1])
        print("Switched to the new tab.")
        time.sleep(60)

        # Step 8: Click on 'My Agents'
        my_agents = WebDriverWait(driver, 30).until(
            EC.element_to_be_clickable((By.LINK_TEXT, "My Agents"))
        )
        my_agents.click()
        print("Clicked on 'My Agents'.")
        time.sleep(5)

        # Step 9: Click on 'Agent List by Status'
        agent_list_by_status = WebDriverWait(driver, 30).until(
            EC.element_to_be_clickable((By.XPATH, "//a[@alt='Agents List by Status_Geber']"))
        )
        agent_list_by_status.click()
        print("Clicked on 'Agent List by Status'.")
        time.sleep(30)
        # Step 11: Wait for 60 seconds
        print("Waiting for 60 seconds to ensure search results load...")
        time.sleep(60)
        # Step 10: Click on Search
        search_button = WebDriverWait(driver, 30).until(
            EC.element_to_be_clickable((By.XPATH, "//div[@data-controlname='btnSearch']"))
        )
        search_button.click()
        print("Clicked on Search.")
    except Exception as e:
        log_error(ERROR_CODES["navigation_error"], "Navigation process failed.", matrix_row["script_name"])
        print("Navigation process failed, ending process.")
        driver.quit()
        return None

    # Click the download button
    try:
        # Step 12: Click on the download image and select CSV
        download_menu = WebDriverWait(driver, 30).until(
            EC.element_to_be_clickable((By.ID, "mnuGridExport"))
        )
        download_menu.click()
        print("Clicked on the download menu.")

        csv_option = WebDriverWait(driver, 30).until(
            EC.element_to_be_clickable((By.ID, "mnuGridExportCSV"))
        )
        csv_option.click()
        print("Selected CSV option.")

        # Step 13: Wait for the download to complete
        print("Waiting for 60 seconds to ensure download completes...")
        time.sleep(60)
    except Exception as e:
        log_error(ERROR_CODES["download_button_not_found"], "'Download' button not found.", matrix_row["script_name"])
        print("Download button not found, ending process.")
        driver.quit()
        return None

    return download_folder


def run_acu_manhattanlife(driver, matrix_row, date_info):
    print("Running ACU ManhattanLife handler...")

    download_folder = os.path.normpath(matrix_row["download_path"])
    driver.get(matrix_row["source_url"])

    # Perform login if needed
    if matrix_row["source_login"].upper() == "YES":
        try:
            # Enter the login email address
            WebDriverWait(driver, 15).until(
                EC.presence_of_element_located((By.XPATH, "//*[@id='txtAgentUserName']"))
            ).send_keys(matrix_row["source_email"])

            # Enter Password
            WebDriverWait(driver, 15).until(
                EC.presence_of_element_located((By.XPATH, "//*[@id='txtAgentPassword']"))
            ).send_keys(matrix_row["source_password"])

            # Press Log In
            login_button = WebDriverWait(driver, 15).until(
                EC.element_to_be_clickable((By.XPATH, "//*[@id='btnLogin']"))
            )
            login_button.click()
            print("Login successful")
        except Exception as e:
            log_error(ERROR_CODES["login_error"], "Login page timeout or login fields not found.",
                      matrix_row["script_name"])
            print("Login page not found or timeout occurred. Exiting...")
            driver.quit()
            return None

    # Navigate to download page
    try:
        # click commissions
        commissions = WebDriverWait(driver, 60).until(
            EC.element_to_be_clickable((By.XPATH, "//*[@id='commissions']"))
        )
        commissions.click()
        print("Commissions Clicked")

        # click Agent Hierarchy
        ah = WebDriverWait(driver, 60).until(
            EC.element_to_be_clickable((By.XPATH, "//*[@id='mnuAgentHierarchy']"))
        )
        ah.click()
        print("Agent Hierarchy Clicked")
    except Exception as e:
        log_error(ERROR_CODES["navigation_error"], "Navigation process failed.", matrix_row["script_name"])
        print("Navigation process failed, ending process.")
        driver.quit()
        return None

    # Click the download button
    try:
        # export to excel
        export_excel = WebDriverWait(driver, 15).until(
            EC.element_to_be_clickable((By.XPATH, "//*[@id='MainContent_btnExportToExcel']"))
        )
        export_excel.click()
        print("Export to excel clicked")
        print("File Downloading")
        time.sleep(160)  # Allow time for download to complete

        # locating the iframe element and switching to it
        iframe = driver.find_element(By.ID, "ifProgress")  # Use appropriate locator (ID, name, etc.)
        driver.switch_to.frame(iframe)

        # locating the progress bar inside the iframe and get the progress value
        progress_bar = WebDriverWait(driver, 10).until(
            EC.presence_of_element_located((By.ID, "progressbar"))
        )
        progress_value = progress_bar.get_attribute("aria-valuenow")
        if progress_value == 100:
            driver.switch_to.default_content()
    except Exception as e:
        log_error(ERROR_CODES["download_button_not_found"], "'Download' button not found.", matrix_row["script_name"])
        print("Download button not found, ending process.")
        driver.quit()
        return None

    return download_folder


def run_acu_molina(driver, matrix_row, date_info):
    print("Running ACU Molina handler...")

    download_folder = os.path.normpath(matrix_row["download_path"])
    driver.get(matrix_row["source_url"])

    # Perform login if needed
    if matrix_row["source_login"].upper() == "YES":
        try:
            # Step 2: Log in
            WebDriverWait(driver, 90).until(
                EC.presence_of_element_located((By.XPATH, "//input[@placeholder='username or email']"))
            ).send_keys(matrix_row["source_email"])
            driver.find_element(By.XPATH, "//input[@placeholder='password']").send_keys(matrix_row["source_password"])
            WebDriverWait(driver, 60).until(
                EC.element_to_be_clickable((By.XPATH, '//*[@id="submit"]'))
            ).click()
            print("Logged in successfully!")
            time.sleep(15)
        except Exception as e:
            log_error(ERROR_CODES["login_error"], "Login page timeout or login fields not found.",
                      matrix_row["script_name"])
            print("Login page not found or timeout occurred. Exiting...")
            driver.quit()
            return None

    # Navigate to download page
    try:
        # Step 3: Open Molina Domain
        original_window = driver.current_window_handle
        WebDriverWait(driver, 60).until(
            EC.element_to_be_clickable((By.XPATH, '//*[@id="domain_container"]/div/div[7]/div/div[2]/div'))
        ).click()
        print("Molina domain selected.")
        WebDriverWait(driver, 60).until(EC.number_of_windows_to_be(2))

        # Switch to the new tab
        for handle in driver.window_handles:
            if handle != original_window:
                driver.switch_to.window(handle)
                break
        print("Switched to Molina tab.")
    except Exception as e:
        log_error(ERROR_CODES["navigation_error"], "Navigation process failed.", matrix_row["script_name"])
        print("Navigation process failed, ending process.")
        driver.quit()
        return None

    # Click the download button
    try:
        # Step 4: Navigate to Broker Credentials page

        # Open dropdown: My Downline Brokers
        WebDriverWait(driver, 60).until(
            EC.element_to_be_clickable((By.XPATH, '//*[@id="accordionSidebar"]/li[5]/a'))
        ).click()
        print("Dropdown opened.")
        time.sleep(30)

        # Select Broker Credentials option
        WebDriverWait(driver, 60).until(
            EC.element_to_be_clickable((By.XPATH, '//*[@id="collapse_15"]/div/a[1]'))
        ).click()
        print("Option selected.")
        time.sleep(120)

        # Click on Search button
        WebDriverWait(driver, 60).until(
            EC.element_to_be_clickable((By.ID, 'search_button'))
        ).click()
        print("Search button clicked.")
        time.sleep(10)

        # Step 5: Download file
        WebDriverWait(driver, 60).until(
            EC.element_to_be_clickable((By.ID, 'producer_status'))
        ).click()
        print("File downloading.")
        time.sleep(120)
    except Exception as e:
        log_error(ERROR_CODES["download_button_not_found"], "'Download' button not found.", matrix_row["script_name"])
        print("Download button not found, ending process.")
        driver.quit()
        return None

    return download_folder


def run_acu_newera(driver, matrix_row, date_info):
    print("Running ACU NewEra handler...")

    download_folder = os.path.normpath(matrix_row["download_path"])
    driver.get(matrix_row["source_url"])

    # Perform login if needed
    if matrix_row["source_login"].upper() == "YES":
        try:
            # Step 2: Enter username
            WebDriverWait(driver, 60).until(
                EC.presence_of_element_located((By.ID, "MainContent_txtUserID"))
            ).send_keys(matrix_row["source_email"])

            # Step 3: Enter password
            WebDriverWait(driver, 60).until(
                EC.presence_of_element_located((By.ID, "Password"))
            ).send_keys(matrix_row["source_password"])

            # Step 4: Click Log In
            WebDriverWait(driver, 60).until(
                EC.element_to_be_clickable((By.ID, "MainContent_btnLogIn"))
            ).click()
            print("Logged in successfully!")
            time.sleep(30)
        except Exception as e:
            log_error(ERROR_CODES["login_error"], "Login page timeout or login fields not found.",
                      matrix_row["script_name"])
            print("Login page not found or timeout occurred. Exiting...")
            driver.quit()
            return None

    # Navigate to download page
    try:
        # Step 5: Click on 'Agent Resources'
        agent_resources = WebDriverWait(driver, 150).until(
            EC.element_to_be_clickable(
                (By.XPATH, "//a[@class='menu-toggle waves-effect waves-block' and span[text()='Agent Resources']]")
            ))
        agent_resources.click()
        print("Clicked on 'Agent Resources'.")

        # Step 6: Click on 'Downline Agents'
        downline_agents = WebDriverWait(driver, 150).until(
            EC.element_to_be_clickable((By.XPATH, "//a[@href='agentresources/agenthierarchy/']"))
        )
        downline_agents.click()
        time.sleep(10)
        print("Clicked on 'Downline Agents'.")
    except Exception as e:
        log_error(ERROR_CODES["navigation_error"], "Navigation process failed.", matrix_row["script_name"])
        print("Navigation process failed, ending process.")
        driver.quit()
        return None

    try:
        # Step 7: Open dropdown and select agent number
        dropdown = WebDriverWait(driver, 120).until(
            EC.presence_of_element_located((By.ID, "MainContent_ddlAgentNumbers"))
        )
        dropdown.click()
        option = WebDriverWait(driver, 120).until(
            EC.presence_of_element_located((By.XPATH,
                                            "//option[@value='6248746A3831396F385249774A4965576D4F3245756271516F6442477038314E6F33714F544D5A7A3955673D~PAL']"))
        )
        option.click()
        print("Selected agent number '601183000 PAL'.")

        # Step 8: Click on Search
        search_button = WebDriverWait(driver, 120).until(
            EC.element_to_be_clickable((By.ID, "btnSearch"))
        )
        search_button.click()
        print("Clicked on Search.")
        time.sleep(30)
    except Exception as e:
        log_error(ERROR_CODES["filter_error"], "Failed to apply filters before download.", matrix_row["script_name"])
        print("Failed to apply filters, ending process.")
        driver.quit()
        return None

    # Click the download button
    try:
        # Step 9: Click on 'Click to download'
        download_link = WebDriverWait(driver, 120).until(
            EC.element_to_be_clickable((By.XPATH, "//span[text()='Click to download']"))
        )
        download_link.click()
        time.sleep(120)
        print("Clicked on 'Click to download'.")
    except Exception as e:
        log_error(ERROR_CODES["download_button_not_found"], "'Download' button not found.", matrix_row["script_name"])
        print("Download button not found, ending process.")
        driver.quit()
        return None

    return download_folder


def run_acu_oscar(driver, matrix_row, date_info):
    print("Running ACU Oscar handler...")

    download_folder = os.path.normpath(matrix_row["download_path"])
    driver.get(matrix_row["source_url"])

    # Perform login if needed
    if matrix_row["source_login"].upper() == "YES":
        try:
            # Step 2: Log in
            WebDriverWait(driver, 20).until(
                EC.presence_of_element_located((By.XPATH, "//input[@placeholder='Enter email address']"))
            ).send_keys(matrix_row["source_email"])
            driver.find_element(By.XPATH, "//input[@placeholder='Enter password']").send_keys(matrix_row["source_password"])
            WebDriverWait(driver, 20).until(
                EC.element_to_be_clickable((By.XPATH, "//button[@type='submit']"))
            ).click()
            time.sleep(5)
            if matrix_row["otp_needed"].upper() == "YES":
                try:
                    otp_code = fetch_otp_code_from_file(matrix_row)
                    print(f"OTP Code found: {otp_code}")

                    # Input OTP
                    WebDriverWait(driver, 30).until(
                        EC.presence_of_element_located((By.XPATH, "//input[@placeholder='Enter 6-digit code']"))
                    ).send_keys(otp_code)
                    print("OTP entered.")
                    time.sleep(2)

                    # Click Submit
                    WebDriverWait(driver, 30).until(
                        EC.element_to_be_clickable(
                            (By.XPATH, "//div[text()='Continue']"))
                    ).click()
                    print("OTP submitted.")
                    time.sleep(10)

                except FileNotFoundError:
                    log_error(ERROR_CODES["OTP_error"], "OTP File was not found or was not submitted correctly.",
                              matrix_row["script_name"])
                    print("OTP File was not found or was not submitted correctly. Exiting...")
                    driver.quit()
                    return None

            print("Logged in successfully!")
            time.sleep(10)
        except Exception as e:
            log_error(ERROR_CODES["login_error"], "Login page timeout or login fields not found.",
                      matrix_row["script_name"])
            print("Login page not found or timeout occurred. Exiting...")
            driver.quit()
            return None

    # Navigate to download page
    try:
        oscar_for_business_link = WebDriverWait(driver, 20).until(
            EC.element_to_be_clickable((By.XPATH, "//a[@title='Oscar For Business']"))
        )
        oscar_for_business_link.click()
        time.sleep(10)

        acu_report_link = WebDriverWait(driver, 10).until(
            EC.element_to_be_clickable((By.XPATH, "//a[@href='/agency/broker-readiness']"))
        )
        acu_report_link.click()
        time.sleep(30)
    except Exception as e:
        log_error(ERROR_CODES["navigation_error"], "Navigation process failed.", matrix_row["script_name"])
        print("Navigation process failed, ending process.")
        driver.quit()
        return None

    try:
        dropdown = WebDriverWait(driver, 10).until(
            EC.element_to_be_clickable(
                (By.XPATH,
                 "/html/body/div[1]/div/div[1]/div[2]/div/div/div[1]/div[1]/div/div/div[2]/div/div/button/div/h3"))
        )
        dropdown.click()
        time.sleep(5)

        # Set dropdown options
        ready_to_sell_option = WebDriverWait(driver, 10).until(
            EC.element_to_be_clickable(
                (By.XPATH, "/html/body/div[5]/div/div/div[3]/div/div[1]/div/div[1]/div[5]/label/span[1]"))
        )
        driver.execute_script("arguments[0].click();", ready_to_sell_option)

        active_option = WebDriverWait(driver, 10).until(
            EC.element_to_be_clickable(
                (By.XPATH, "/html/body/div[5]/div/div/div[3]/div/div[1]/div/div[1]/div[6]/label/span[1]"))
        )
        driver.execute_script("arguments[0].click();", active_option)

        # Apply selection
        apply_button = WebDriverWait(driver, 10).until(
            EC.element_to_be_clickable((By.XPATH, "/html/body/div[5]/div/div/div[3]/div/div[2]/div/button[2]/h3/span"))
        )
        apply_button.click()
        time.sleep(30)
    except Exception as e:
        log_error(ERROR_CODES["filter_error"], "Failed to apply filters before download.", matrix_row["script_name"])
        print("Failed to apply filters, ending process.")
        driver.quit()
        return None

    # Click the download button
    try:
        export_button = WebDriverWait(driver, 20).until(
            EC.element_to_be_clickable((By.XPATH, "//button[.//text()[contains(., 'Export CSV')]]"))
        )
        export_button.click()
        print("Export CSV button clicked.")
        time.sleep(300)
    except Exception as e:
        log_error(ERROR_CODES["download_button_not_found"], "'Download' button not found.", matrix_row["script_name"])
        print("Download button not found, ending process.")
        driver.quit()
        return None

    return download_folder


def run_bob_alignment(driver, matrix_row, date_info):
    print("Running BOB Alignment handler...")
    download_folder = os.path.normpath(matrix_row["download_path"])
    driver.get(matrix_row["source_url"])

    if matrix_row["source_login"].upper() == "YES":
        try:
            # Step 2: Enter Username
            WebDriverWait(driver, 90).until(
                EC.presence_of_element_located((By.ID, "logonIdentifier"))
            ).send_keys(matrix_row["source_email"])
            WebDriverWait(driver, 90).until(
                EC.presence_of_element_located((By.ID, "password"))
            ).send_keys(matrix_row["source_password"])
            WebDriverWait(driver, 90).until(
                EC.element_to_be_clickable((By.ID, "next"))
            ).click()
            print("Logged in successfully!")
            time.sleep(10)

            # Step 3: Verify page load after login
            WebDriverWait(driver, 90).until(EC.url_contains("agents.alignmenthealthcare.com"))
            time.sleep(10)
        except Exception as e:
            log_error(ERROR_CODES["login_error"], "Login page timeout or login fields not found.",
                      matrix_row["script_name"])
            print("Login page not found or timeout occurred. Exiting...")
            driver.quit()
            return None

    try:
        # Step 4: Click on "MY AGENTS" link
        WebDriverWait(driver, 10).until(
            EC.element_to_be_clickable((By.CSS_SELECTOR, "a.nav-link[href='/my-clients']"))
        ).click()
        print("Navigated to 'MY AGENTS' page.")
        time.sleep(10)

        # Step 5: Click on "Agent Clients" button
        WebDriverWait(driver, 10).until(
            EC.element_to_be_clickable(
                (By.XPATH, "//button[contains(@class, 'Mui-selected') and contains(text(), 'Agent Clients')]"))
        ).click()
        print("Clicked on 'Agent Clients'.")
        time.sleep(10)
    except Exception as e:
        log_error(ERROR_CODES["navigation_error"], "Navigation process failed, download screen was not reached.",
                  matrix_row["script_name"])
        print("Navigation process failed to reach download location, ending process.")
        driver.quit()
        return None

    try:
        # Step 6: Click on "Enrollment Report" button
        enrollment_report_button = WebDriverWait(driver, 20).until(
            EC.element_to_be_clickable((By.CSS_SELECTOR, "a.authenticated-file-link.buttonEnrollmentReport"))
        )
        driver.execute_script("arguments[0].scrollIntoView(true);", enrollment_report_button)
        driver.execute_script("arguments[0].click();", enrollment_report_button)
        print("Clicked on 'Enrollment Report'.")
        time.sleep(120)  # Adjust based on download speed
    except Exception as e:
        log_error(ERROR_CODES["download_button_not_found"], "'Download' button not found.", matrix_row["script_name"])
        print("Download button not found, ending process.")
        driver.quit()
        return None

    return download_folder


def run_bob_allstate(driver, matrix_row, date_info):
    print("Running BOB Allstate handler...")

    download_folder = os.path.normpath(matrix_row["download_path"])
    driver.get(matrix_row["source_url"])

    # Perform login if needed
    if matrix_row["source_login"].upper() == "YES":
        try:
            comp_id = WebDriverWait(driver, 60).until(
                EC.presence_of_element_located((By.XPATH, "//input[@id='txtCompanyID']"))
            )
            comp_id.send_keys(matrix_row["company_id_field"])

            email_field = WebDriverWait(driver, 30).until(
                EC.presence_of_element_located((By.XPATH, "//input[@id='txtAgentID']"))
            )
            email_field.send_keys(matrix_row["source_email"])

            password_field = WebDriverWait(driver, 30).until(
                EC.presence_of_element_located((By.XPATH, "//input[@id='txtpwd']"))
            )
            password_field.send_keys(matrix_row["source_password"])

            login_button = WebDriverWait(driver, 30).until(
                EC.presence_of_element_located((By.XPATH, "//input[@id='iplogin']"))
            )
            login_button.click()

            print("Login successful.")
            time.sleep(30)  # Allow time for login redirection
        except Exception as e:
            log_error(ERROR_CODES["login_error"], "Login page timeout or login fields not found.",
                      matrix_row["script_name"])
            print("Login page not found or timeout occurred. Exiting...")
            driver.quit()
            return None

    # Step 2: Navigate to Agent Hierarchy page
    try:
        driver.get("https://v10.eagentcenter.com/agent/client.aspx")
        print("Navigated to Downline Clients page.")
        time.sleep(5)
        checkbox = WebDriverWait(driver, 60).until(
            EC.presence_of_element_located((By.ID, "chkdownline"))
        )
        if not checkbox.is_selected():
            checkbox.click()
        print("Checkbox 'chkdownline' selected.")
    except Exception as e:
        log_error(ERROR_CODES["navigation_error"], "'chkdownline' checkbox not found.", matrix_row["script_name"])
        driver.quit()
        return None

    # Step 3: Click the "Go" button
    try:
        go_button = WebDriverWait(driver, 60).until(
            EC.element_to_be_clickable((By.ID, "li5"))
        )
        go_button.click()
        print("Clicked the 'Go' button.")
        time.sleep(60)  # Wait for the table to refresh
    except Exception as e:
        log_error(ERROR_CODES["navigation_error"], "'Go' button not found.", matrix_row["script_name"])
        driver.quit()
        return None

    # Step 4: Click the "Export" button
    try:
        export_button = WebDriverWait(driver, 30).until(
            EC.element_to_be_clickable((By.ID, "li6"))
        )
        export_button.click()
        print("Export button clicked, file should start downloading...")
        time.sleep(60)  # Allow time for the download
    except Exception as e:
        log_error(ERROR_CODES["download_button_not_found"], "'Download' button not found.", matrix_row["script_name"])
        print("Download button not found, ending process.")
        driver.quit()
        return None

    return download_folder


def run_bob_ambetter1(driver, matrix_row, date_info):
    print("Running BOB Ambetter 1 handler...")

    if driver is None:
        raise RuntimeError("WebDriver is None before Ambetter launch")

    raw_url = matrix_row.get("url")

    if not raw_url or not isinstance(raw_url, str):
        raise RuntimeError(f"Ambetter matrix_row['url'] is invalid: {raw_url}")

    print("Ambetter URL validated:", raw_url)

    download_folder = os.path.normpath(matrix_row["download_path"])
    file_prefix = matrix_row.get("file_prefix", "policies")
    offsets = [0, 50000, 100000, 150000, 200000, 250000, 300000]

    os.makedirs(download_folder, exist_ok=True)

    if "offset=" in raw_url:
        base_url = raw_url.split("offset=")[0] + "offset={}"
    else:
        base_url = raw_url + "?offset={}"

    print("Base URL:", base_url)

    # ----------------------------------------------------------
    # LOGIN WITH RETRIES
    # ----------------------------------------------------------
    for attempt in range(3):
        try:
            print(f"Attempt {attempt + 1}/3 — Opening Ambetter login page...")
            driver.get(raw_url)

            if matrix_row.get("log_in", "").upper() == "YES":
                WebDriverWait(driver, 30).until(
                    EC.presence_of_element_located((By.XPATH, "//input[@placeholder='Username']"))
                ).send_keys(matrix_row["source_email"])

                driver.find_element(
                    By.XPATH, "//input[@placeholder='Password']"
                ).send_keys(matrix_row["source_password"])

                driver.find_element(
                    By.XPATH,
                    "//*[@id='centerPanel']/div/div[2]/div/div[2]/div/div[3]/button"
                ).click()

                print("Logged in successfully.")
                time.sleep(35)

            break

        except Exception as e:
            print(f"Login attempt {attempt + 1} failed: {e}")
            if attempt == 2:
                log_error(
                    ERROR_CODES["login_error"],
                    "Ambetter login failed after retries.",
                    matrix_row["script_name"]
                )
                driver.quit()
                return None
            time.sleep(5)

    # ----------------------------------------------------------
    # DOWNLOAD LOOP
    # ----------------------------------------------------------
    for offset in offsets:
        try:
            url = base_url.format(offset)
            print(f"Opening offset {offset}: {url}")
            driver.get(url)

            WebDriverWait(driver, 20).until(
                EC.element_to_be_clickable((By.XPATH, "//a[@download='policies.zip']"))
            ).click()

            print(f"Download triggered for offset={offset}")
            time.sleep(12)

        except Exception as e:
            print(f"Could not download for offset={offset}: {e}")
            continue

    driver.quit()
    time.sleep(10)

    # ----------------------------------------------------------
    # ZIP DISCOVERY
    # ----------------------------------------------------------
    try:
        zip_files = sorted(
            [
                os.path.join(download_folder, f)
                for f in os.listdir(download_folder)
                if f.lower().startswith(file_prefix.lower()) and f.lower().endswith(".zip")
            ],
            key=os.path.getctime
        )
    except Exception as e:
        log_error(ERROR_CODES["general_error"], f"Ambetter ZIP scan error: {e}", matrix_row["script_name"])
        return None

    if not zip_files:
        raise RuntimeError("No Ambetter ZIP files were downloaded.")

    print("ZIPs found for processing:")
    for z in zip_files:
        print("   -", os.path.basename(z))

    # ----------------------------------------------------------
    # EXTRACT + STAGE
    # ----------------------------------------------------------
    temp_csvs = []

    for i, zip_path in enumerate(zip_files):
        offset = offsets[i]

        try:
            print(f"Extracting for offset={offset}: {os.path.basename(zip_path)}")
            extract_folder = os.path.splitext(zip_path)[0]

            with zipfile.ZipFile(zip_path, 'r') as z:
                z.extractall(extract_folder)

            csv_found = None
            for f in os.listdir(extract_folder):
                if f.lower().endswith(".csv"):
                    csv_found = os.path.join(extract_folder, f)
                    break

            if not csv_found:
                print(f"No CSV found inside {zip_path}")
                continue

            temp_csv = os.path.join(download_folder, f"temp_ambetter_offset_{offset}.csv")
            shutil.move(csv_found, temp_csv)
            temp_csvs.append(temp_csv)
            print(f"Extracted & staged → {temp_csv}")

            shutil.rmtree(extract_folder)

        except Exception as e:
            print(f"Error extracting ZIP {zip_path}: {e}")

    if not temp_csvs:
        raise RuntimeError("No CSV files were extracted from Ambetter ZIPs.")

    # ----------------------------------------------------------
    # ✅ FINAL MERGE → policies.csv (STATIC NAME)
    # ----------------------------------------------------------
    prefix = matrix_row["extracted_file_prefix"].strip()
    extension = matrix_row["extracted_file_extension"].strip().lower()

    final_csv = os.path.join(download_folder, f"{prefix}.{extension}")
    print("Merging CSV files →", final_csv)

    try:
        with open(final_csv, "w", encoding="utf-8", newline="") as outfile:

            with open(temp_csvs[0], "r", encoding="utf-8") as f0:
                for line in f0:
                    outfile.write(line)

            for temp in temp_csvs[1:]:
                with open(temp, "r", encoding="utf-8") as fin:
                    for line in fin:
                        stripped = line.strip()
                        if stripped == "" or all(c == "," for c in stripped):
                            continue
                        outfile.write(line)

        print(f"FINAL MERGED CSV → {final_csv}")

    except Exception as e:
        log_error(ERROR_CODES["general_error"], f"Ambetter merge error: {e}", matrix_row["script_name"])
        return None

    # ----------------------------------------------------------
    # CLEANUP
    # ----------------------------------------------------------
    print("Cleaning ZIPs & temporary CSVs...")

    for z in zip_files:
        try:
            os.remove(z)
            print("Removed ZIP:", os.path.basename(z))
        except:
            pass

    for t in temp_csvs:
        try:
            os.remove(t)
            print("Removed temp CSV:", os.path.basename(t))
        except:
            pass

    print("Ambetter BOB processing completed successfully!")
    return download_folder


def run_bob_ambetter2(driver, matrix_row):
    print("Running BOB Ambetter 2 handler...")

    download_folder = os.path.normpath(matrix_row["download_path"])
    driver.get(matrix_row["source_url"])  # Use the URL from the matrix

    if matrix_row["source_login"].upper() == "YES":
        try:
            WebDriverWait(driver, 10).until(
                EC.presence_of_element_located((By.XPATH, "//input[@placeholder='Email']"))
            ).send_keys(matrix_row["source_email"])
            driver.find_element(By.XPATH, "//input[@placeholder='Password']").send_keys(matrix_row["source_password"])
            driver.find_element(By.XPATH, "//*[@id='centerPanel']/div/div[2]/div/div[2]/div/div[3]/button").click()
            print("Logged in successfully!")
            time.sleep(60)
        except Exception as e:
            log_error(ERROR_CODES["login_error"], "Login page timeout or login fields not found.",
                      matrix_row["script_name"])
            print("Login page not found or timeout occurred. Exiting...")
            driver.quit()
            return None

    rename_base = matrix_row.get("rename_base", "raw_bob_ambetter_aca_2")

    try:
        WebDriverWait(driver, 10).until(
            EC.element_to_be_clickable((By.XPATH, "//a[@download='policies.zip']"))
        ).click()
        print(f"Triggered download for {rename_base}1")
    except Exception as e:
        print(f"Download button not found for {rename_base}2: {e}")
        return download_folder

    time.sleep(20)  # Adjust based on download speed

    try:
        # Find the latest ZIP file
        zip_files = [os.path.join(download_folder, f) for f in os.listdir(download_folder) if f.endswith(".zip")]
        latest_zip = max(zip_files, key=os.path.getctime)

        # Extract the ZIP
        with zipfile.ZipFile(latest_zip, 'r') as zip_ref:
            zip_ref.extractall(download_folder)
            extracted_files = zip_ref.namelist()

        # Rename the extracted file
        if extracted_files:
            original_path = os.path.join(download_folder, extracted_files[0])
            new_name = os.path.join(download_folder, f"{rename_base}2.csv")
            os.rename(original_path, new_name)
            print(f"Extracted and renamed to {new_name}")
        else:
            print(f"No files found in ZIP {latest_zip}")

        os.remove(latest_zip)  # Clean up ZIP

    except Exception as e:
        print(f"Error processing ZIP for {rename_base}2: {e}")

    return download_folder


def run_bob_ambetter3(driver, matrix_row):
    print("Running BOB Ambetter 3 handler...")

    download_folder = os.path.normpath(matrix_row["download_path"])
    driver.get(matrix_row["source_url"])  # Use the URL from the matrix

    if matrix_row["source_login"].upper() == "YES":
        try:
            WebDriverWait(driver, 10).until(
                EC.presence_of_element_located((By.XPATH, "//input[@placeholder='Email']"))
            ).send_keys(matrix_row["source_email"])
            driver.find_element(By.XPATH, "//input[@placeholder='Password']").send_keys(matrix_row["source_password"])
            driver.find_element(By.XPATH, "//*[@id='centerPanel']/div/div[2]/div/div[2]/div/div[3]/button").click()
            print("Logged in successfully!")
            time.sleep(60)
        except Exception as e:
            log_error(ERROR_CODES["login_error"], "Login page timeout or login fields not found.",
                      matrix_row["script_name"])
            print("Login page not found or timeout occurred. Exiting...")
            driver.quit()
            return None

    rename_base = matrix_row.get("rename_base", "raw_bob_ambetter_aca_3")

    try:
        WebDriverWait(driver, 10).until(
            EC.element_to_be_clickable((By.XPATH, "//a[@download='policies.zip']"))
        ).click()
        print(f"Triggered download for {rename_base}3")
    except Exception as e:
        print(f"Download button not found for {rename_base}3: {e}")
        return download_folder

    time.sleep(20)  # Adjust based on download speed

    try:
        # Find the latest ZIP file
        zip_files = [os.path.join(download_folder, f) for f in os.listdir(download_folder) if f.endswith(".zip")]
        latest_zip = max(zip_files, key=os.path.getctime)

        # Extract the ZIP
        with zipfile.ZipFile(latest_zip, 'r') as zip_ref:
            zip_ref.extractall(download_folder)
            extracted_files = zip_ref.namelist()

        # Rename the extracted file
        if extracted_files:
            original_path = os.path.join(download_folder, extracted_files[0])
            new_name = os.path.join(download_folder, f"{rename_base}3.csv")
            os.rename(original_path, new_name)
            print(f"Extracted and renamed to {new_name}")
        else:
            print(f"No files found in ZIP {latest_zip}")

        os.remove(latest_zip)  # Clean up ZIP

    except Exception as e:
        print(f"Error processing ZIP for {rename_base}3: {e}")

    return download_folder


def run_com_amerihealth_aca(driver, matrix_row, date_info):
    print("Running COM AmeriHealth ACA handler...")

    download_folder = os.path.normpath(matrix_row["download_path"].strip())
    script_name = matrix_row["script_name"].strip()
    portal_url = matrix_row["source_url"].strip()
    log_in = matrix_row["source_login"].strip().upper()
    username = matrix_row["source_email"].strip()
    password = matrix_row["source_password"].strip()

    try:
        today = datetime.now()

        if today.month == 1:
            process_month = 12
            process_year = today.year - 1
        else:
            process_month = today.month - 1
            process_year = today.year

        process_month_no_zero = str(int(process_month))
        process_month_two_digit = f"{int(process_month):02d}"

        print("AmeriHealth ACA COM target values:")
        print(f"  Portal URL:        {portal_url}")
        print(f"  Process Year:     {process_year}")
        print(f"  Process Month:    {process_month_two_digit}")

        driver.get(portal_url)
        print("AmeriHealth ACA portal opened.")
        time.sleep(5)

        if log_in == "YES":
            try:
                username_field = WebDriverWait(driver, 30).until(
                    EC.element_to_be_clickable((By.ID, "fld_UsrSet_UserId"))
                )
                username_field.clear()
                username_field.send_keys(username)
                print("Username entered successfully.")

                password_field = WebDriverWait(driver, 30).until(
                    EC.element_to_be_clickable((By.ID, "fld_UsrSet_UserPswd"))
                )
                password_field.clear()
                password_field.send_keys(password)
                print("Password entered successfully.")

                sign_in_button = WebDriverWait(driver, 30).until(
                    EC.element_to_be_clickable((By.XPATH, "//button[contains(., 'Sign In')]"))
                )
                sign_in_button.click()
                print("Sign In button clicked.")

                time.sleep(30)

            except Exception as e:
                log_error(
                    ERROR_CODES["login_error"],
                    f"Login page timeout or login fields not found: {e}",
                    script_name
                )
                print("Login page not found or timeout occurred. Exiting...")
                driver.quit()
                return None

        try:
            history_clicked = False

            history_selectors = [
                (By.XPATH, "//a[normalize-space()='History']"),
                (By.XPATH, "//a[contains(normalize-space(), 'History')]"),
                (By.XPATH, "//*[normalize-space()='History']"),
                (By.XPATH, "//*[contains(normalize-space(), 'History')]"),
            ]

            for by, selector in history_selectors:
                try:
                    history_element = WebDriverWait(driver, 15).until(
                        EC.element_to_be_clickable((by, selector))
                    )
                    driver.execute_script(
                        "arguments[0].scrollIntoView({block: 'center'});",
                        history_element
                    )
                    time.sleep(1)
                    driver.execute_script("arguments[0].click();", history_element)
                    print("History menu clicked.")
                    history_clicked = True
                    break
                except Exception:
                    continue

            if not history_clicked:
                raise Exception("History menu was not found or clickable.")

            time.sleep(5)

        except Exception as e:
            log_error(
                ERROR_CODES["navigation_error"],
                f"Navigation process failed, History menu was not reached: {e}",
                script_name
            )
            print("Navigation process failed to reach History menu.")
            driver.quit()
            return None

        try:
            statements_clicked = False

            statements_selectors = [
                (By.XPATH, "//*[normalize-space()='Statements']"),
                (By.XPATH, "//*[contains(normalize-space(), 'Statements')]"),
                (By.XPATH, "//a[normalize-space()='Statements']"),
                (By.XPATH, "//a[contains(normalize-space(), 'Statements')]"),
            ]

            for by, selector in statements_selectors:
                try:
                    statements_element = WebDriverWait(driver, 15).until(
                        EC.element_to_be_clickable((by, selector))
                    )
                    driver.execute_script(
                        "arguments[0].scrollIntoView({block: 'center'});",
                        statements_element
                    )
                    time.sleep(1)
                    driver.execute_script("arguments[0].click();", statements_element)
                    print("Statements menu clicked.")
                    statements_clicked = True
                    break
                except Exception:
                    continue

            if not statements_clicked:
                raise Exception("Statements menu was not found or clickable.")

            time.sleep(15)

        except Exception as e:
            log_error(
                ERROR_CODES["navigation_error"],
                f"Navigation process failed, Statements screen was not reached: {e}",
                script_name
            )
            print("Navigation process failed to reach Statements screen.")
            driver.quit()
            return None

        try:
            print(
                f"Searching for AmeriHealth ACA statement row: "
                f"Process Year={process_year}, Process Month={process_month_no_zero}"
            )

            rows = WebDriverWait(driver, 45).until(
                EC.presence_of_all_elements_located(
                    (By.XPATH, "//tr[contains(@class, 'ui-row-ltr') or @role='row']")
                )
            )

            matching_row = None

            for row in rows:
                row_text = row.text.strip()

                if not row_text:
                    continue

                row_lines = [
                    line.strip()
                    for line in row_text.splitlines()
                    if line.strip()
                ]

                padded_row_text = f" {row_text} "

                if str(process_year) in row_lines and process_month_no_zero in row_lines:
                    matching_row = row
                    break

                if str(process_year) in row_text and f" {process_month_no_zero} " in padded_row_text:
                    matching_row = row
                    break

                if str(process_year) in row_text and f" {process_month_two_digit} " in padded_row_text:
                    matching_row = row
                    break

            if matching_row is None:
                print("Visible rows found:")

                for index, row in enumerate(rows, start=1):
                    row_text = row.text.strip()
                    if row_text:
                        print(f"Row {index}: {row_text}")

                raise Exception(
                    f"No matching statement row found for Process Year={process_year}, "
                    f"Process Month={process_month_no_zero}."
                )

            print("Matching statement row found.")

        except Exception as e:
            log_error(
                ERROR_CODES["navigation_error"],
                f"Could not locate AmeriHealth ACA statement row: {e}",
                script_name
            )
            print("Could not locate matching statement row.")
            driver.quit()
            return None

        try:
            try:
                view_link = matching_row.find_element(
                    By.XPATH,
                    ".//*[normalize-space()='View' or contains(normalize-space(), 'View')]"
                )
                driver.execute_script(
                    "arguments[0].scrollIntoView({block: 'center'});",
                    view_link
                )
                time.sleep(1)
                driver.execute_script("arguments[0].click();", view_link)
                print("View clicked.")
            except Exception:
                driver.execute_script(
                    "arguments[0].scrollIntoView({block: 'center'});",
                    matching_row
                )
                time.sleep(1)
                driver.execute_script("arguments[0].click();", matching_row)
                print("Matching statement row clicked.")

            time.sleep(15)

        except Exception as e:
            log_error(
                ERROR_CODES["navigation_error"],
                f"Could not select AmeriHealth ACA statement row: {e}",
                script_name
            )
            print("Could not select matching statement row.")
            driver.quit()
            return None

        try:
            detail_extract_clicked = False

            detail_extract_selectors = [
                (By.XPATH, "//*[normalize-space()='Statement Detail Extract']"),
                (By.XPATH, "//*[contains(normalize-space(), 'Statement Detail Extract')]"),
                (By.XPATH, "//a[contains(normalize-space(), 'Statement Detail Extract')]"),
                (By.XPATH, "//span[contains(normalize-space(), 'Statement Detail Extract')]"),
            ]

            for by, selector in detail_extract_selectors:
                try:
                    detail_extract_element = WebDriverWait(driver, 20).until(
                        EC.element_to_be_clickable((by, selector))
                    )
                    driver.execute_script(
                        "arguments[0].scrollIntoView({block: 'center'});",
                        detail_extract_element
                    )
                    time.sleep(1)
                    driver.execute_script("arguments[0].click();", detail_extract_element)
                    print("Statement Detail Extract clicked.")
                    detail_extract_clicked = True
                    break
                except Exception:
                    continue

            if not detail_extract_clicked:
                raise Exception("Statement Detail Extract was not found or clickable.")

            time.sleep(10)

        except Exception as e:
            log_error(
                ERROR_CODES["navigation_error"],
                f"Could not open Statement Detail Extract: {e}",
                script_name
            )
            print("Could not open Statement Detail Extract.")
            driver.quit()
            return None

        try:
            try:
                handles_before_run = driver.window_handles
            except Exception:
                handles_before_run = []

            run_clicked = False

            run_selectors = [
                (By.XPATH, "//button[normalize-space()='Run']"),
                (By.XPATH, "//span[normalize-space()='Run']"),
                (By.XPATH, "//*[normalize-space()='Run']"),
                (By.XPATH, "//*[contains(normalize-space(), 'Run')]"),
            ]

            for by, selector in run_selectors:
                try:
                    run_element = WebDriverWait(driver, 30).until(
                        EC.element_to_be_clickable((by, selector))
                    )
                    driver.execute_script(
                        "arguments[0].scrollIntoView({block: 'center'});",
                        run_element
                    )
                    time.sleep(1)
                    driver.execute_script("arguments[0].click();", run_element)
                    print("Run clicked.")
                    run_clicked = True
                    break
                except Exception:
                    continue

            if not run_clicked:
                raise Exception("Run button was not found or clickable.")

            print("Waiting for report viewer/context update...")
            time.sleep(15)

            try:
                handles_after_run = driver.window_handles

                if handles_after_run:
                    new_handles = [
                        handle
                        for handle in handles_after_run
                        if handle not in handles_before_run
                    ]

                    if new_handles:
                        driver.switch_to.window(new_handles[-1])
                        print("Switched to new report viewer window.")
                    else:
                        driver.switch_to.window(handles_after_run[-1])
                        print("Switched to latest available report viewer window.")

            except Exception as e:
                print(f"Window switch after Run encountered an issue, continuing: {e}")

            time.sleep(45)

        except Exception as e:
            log_error(
                ERROR_CODES["download_button_not_found"],
                f"AmeriHealth ACA report Run failed: {e}",
                script_name
            )
            print("Run process failed.")
            driver.quit()
            return None

        try:
            before_files = set(os.listdir(download_folder))

            save_clicked = False

            save_selectors = [
                (By.XPATH, "//span[normalize-space()='Save']"),
                (By.XPATH, "//button[normalize-space()='Save']"),
                (By.XPATH, "//a[normalize-space()='Save']"),
                (By.XPATH, "//*[normalize-space()='Save']"),
                (By.XPATH, "//*[contains(normalize-space(), 'Save')]"),
            ]

            for attempt in range(3):
                try:
                    handles = driver.window_handles
                    if handles:
                        driver.switch_to.window(handles[-1])
                except Exception:
                    pass

                for by, selector in save_selectors:
                    try:
                        save_element = WebDriverWait(driver, 20).until(
                            EC.element_to_be_clickable((by, selector))
                        )
                        driver.execute_script(
                            "arguments[0].scrollIntoView({block: 'center'});",
                            save_element
                        )
                        time.sleep(1)
                        driver.execute_script("arguments[0].click();", save_element)
                        print("Save clicked.")
                        save_clicked = True
                        break
                    except Exception:
                        continue

                if save_clicked:
                    break

                time.sleep(5)

            if not save_clicked:
                raise Exception("Save button was not found or clickable.")

            time.sleep(10)

        except Exception as e:
            log_error(
                ERROR_CODES["download_button_not_found"],
                f"AmeriHealth ACA Save step failed: {e}",
                script_name
            )
            print("Save step failed.")
            driver.quit()
            return None

        try:
            csv_clicked = False

            csv_selectors = [
                (By.XPATH, "//h3[contains(normalize-space(), 'CSV')]"),
                (By.XPATH, "//*[contains(normalize-space(), 'CSV (Comma-separated values)')]"),
                (By.XPATH, "//*[contains(normalize-space(), 'Comma-separated values')]"),
                (By.XPATH, "//*[contains(normalize-space(), 'CSV')]"),
            ]

            for attempt in range(3):
                try:
                    handles = driver.window_handles
                    if handles:
                        driver.switch_to.window(handles[-1])
                except Exception:
                    pass

                for by, selector in csv_selectors:
                    try:
                        csv_element = WebDriverWait(driver, 20).until(
                            EC.element_to_be_clickable((by, selector))
                        )
                        driver.execute_script(
                            "arguments[0].scrollIntoView({block: 'center'});",
                            csv_element
                        )
                        time.sleep(1)
                        driver.execute_script("arguments[0].click();", csv_element)
                        print("CSV format selected.")
                        csv_clicked = True
                        break
                    except Exception:
                        continue

                if csv_clicked:
                    break

                time.sleep(5)

            if not csv_clicked:
                raise Exception("CSV option was not found or clickable.")

        except Exception as e:
            log_error(
                ERROR_CODES["download_button_not_found"],
                f"AmeriHealth ACA CSV selection failed: {e}",
                script_name
            )
            print("CSV selection failed.")
            driver.quit()
            return None

        try:
            print("Waiting for CSV download...")

            downloaded_file = None
            end_time = time.time() + 180

            while time.time() < end_time:
                current_files = set(os.listdir(download_folder))
                new_files = current_files - before_files

                temp_files = [
                    file_name
                    for file_name in current_files
                    if file_name.lower().endswith((".crdownload", ".tmp"))
                ]

                csv_files = [
                    os.path.join(download_folder, file_name)
                    for file_name in new_files
                    if file_name.lower().endswith(".csv")
                ]

                if csv_files and not temp_files:
                    latest_file = max(csv_files, key=os.path.getmtime)

                    size_1 = os.path.getsize(latest_file)
                    time.sleep(2)
                    size_2 = os.path.getsize(latest_file)

                    if size_1 == size_2 and size_2 > 0:
                        downloaded_file = latest_file
                        print(f"Download completed: {os.path.basename(downloaded_file)}")
                        break

                time.sleep(2)

            if downloaded_file is None:
                raise Exception("Timed out waiting for completed CSV download.")

            print("AmeriHealth ACA CSV downloaded successfully.")
            log_success()

            return download_folder

        except Exception as e:
            log_error(
                ERROR_CODES["download_button_not_found"],
                f"AmeriHealth ACA CSV download failed: {e}",
                script_name
            )
            print("CSV download failed.")
            driver.quit()
            return None

    except Exception as e:
        log_error(
            ERROR_CODES["general_error"],
            f"AmeriHealth ACA handler failed: {e}",
            script_name
        )
        print(f"AmeriHealth ACA handler failed: {e}")
        driver.quit()
        return None


def run_bob_amerihealth(driver, matrix_row, date_info):
    print("Running BOB Amerihealth handler...")

    download_folder = os.path.normpath(matrix_row["download_path"])
    driver.get(matrix_row["source_url"])

    # Perform login if needed
    if matrix_row["source_login"].upper() == "YES":
        try:
            # Step 2: Enter Username
            WebDriverWait(driver, 10).until(
                EC.presence_of_element_located((By.ID, "fld_UsrSet_UserId"))
            ).send_keys(matrix_row["source_email"])
            print("Username entered successfully.")

            # Step 3: Enter Password
            WebDriverWait(driver, 10).until(
                EC.presence_of_element_located((By.ID, "fld_UsrSet_UserPswd"))
            ).send_keys(matrix_row["source_password"])
            print("Password entered successfully.")

            # Step 4: Click on Sign In
            WebDriverWait(driver, 10).until(
                EC.element_to_be_clickable((By.XPATH, "//button[contains(text(), 'Sign In')]"))
            ).click()
            print("Sign In button clicked.")
            time.sleep(30)
        except Exception as e:
            log_error(ERROR_CODES["login_error"], "Login page timeout or login fields not found.",
                      matrix_row["script_name"])
            print("Login page not found or timeout occurred. Exiting...")
            driver.quit()
            return None

    try:
        # Step 5: Click on Reports
        WebDriverWait(driver, 10).until(
            EC.element_to_be_clickable((By.XPATH, "//a[contains(text(), 'Reports')]"))
        ).click()
        print("Reports menu clicked.")
        time.sleep(10)
    except Exception as e:
        log_error(ERROR_CODES["navigation_error"], "Navigation process failed, download screen was not reached.",
                  matrix_row["script_name"])
        print("Navigation process failed to reach download location, ending process.")
        driver.quit()
        return None

    try:
        # Step 6: Click on Run
        WebDriverWait(driver, 10).until(
            EC.element_to_be_clickable((By.XPATH, "//span[contains(text(), 'Run')]"))
        ).click()
        print("Run button clicked.")
        time.sleep(60)

        # Step 7: Click on Save
        WebDriverWait(driver, 10).until(
            EC.element_to_be_clickable((By.XPATH, "//span[contains(text(), 'Save')]"))
        ).click()
        print("Save button clicked.")
        time.sleep(60)

        # Step 8: Select CSV format
        WebDriverWait(driver, 10).until(
            EC.element_to_be_clickable((By.XPATH, "//h3[contains(text(), 'CSV (Comma-separated values)')]"))
        ).click()
        print("Downloading in CSV format.")
        time.sleep(90)
    except Exception as e:
        log_error(ERROR_CODES["download_button_not_found"], "Download process failed.", matrix_row["script_name"])
        print("Download process failed, ending process.")
        driver.quit()
        return None

    return download_folder


def run_bob_anthem(driver, matrix_row, date_info):
    print("Running BOB Anthem handler...")

    download_folder = os.path.normpath(matrix_row["download_path"])
    driver.get(matrix_row["source_url"])

    # Perform login if needed
    if matrix_row["source_login"].upper() == "YES":
        try:
            # Step 2: Enter Username
            WebDriverWait(driver, 30).until(
                EC.presence_of_element_located((By.XPATH, "//input[@placeholder='Enter your username']"))
            ).send_keys(matrix_row["source_email"])

            # Step 3: Enter Password
            WebDriverWait(driver, 30).until(
                EC.presence_of_element_located((By.XPATH, "//input[@placeholder='Enter your password']"))
            ).send_keys(matrix_row["source_password"])

            # Step 4: Press Login
            login_button = WebDriverWait(driver, 30).until(
                EC.element_to_be_clickable((By.XPATH, '//*[@id="left-align"]'))
            )
            login_button.click()
            time.sleep(15)
        except Exception as e:
            log_error(ERROR_CODES["login_error"], "Login page timeout or login fields not found.",
                      matrix_row["script_name"])
            print("Login page not found or timeout occurred. Exiting...")
            driver.quit()
            return None

    try:
        # Step 5: Navigate to BOB and Clients
        bob = WebDriverWait(driver, 30).until(
            EC.element_to_be_clickable((By.XPATH, '//*[@id="mnuBookOfBusiness"]/div'))
        )
        bob.click()
        print("BOB Clicked")
        time.sleep(15)

        list_container = WebDriverWait(driver, 30).until(
            EC.visibility_of_element_located((By.XPATH, '//*[@id="bob$Menu"]/li/ul'))
        )
        list_items = list_container.find_elements(By.TAG_NAME, "li")

        for item in list_items:
            if "Clients" in item.text:
                driver.execute_script("arguments[0].scrollIntoView(true);", item)
                driver.execute_script("arguments[0].click();", item)
                print("Selected 'Clients' from the list.")
                break
        else:
            log_error(ERROR_CODES["navigation_error"], "Navigation process failed.", matrix_row["script_name"])
            print("Navigation process failed, ending process.")
            raise TimeoutException("'Clients' option not found in list items.")
        time.sleep(10)

        # Step 6: Click on "Client Status" button
        client_status_btn = WebDriverWait(driver, 30).until(
            EC.element_to_be_clickable((By.ID, "Status-filter-btn"))
        )
        # driver.execute_script("arguments[0].scrollIntoView(true);", client_status_btn)
        client_status_btn.click()
        print("Opened 'Client Status' dropdown.")
        time.sleep(5)

        # Step 7: Select "Active" status
        active_status = WebDriverWait(driver, 30).until(
            EC.element_to_be_clickable((By.ID, "Active-radio-div"))
        )
        active_status.click()
        print("Selected 'Active' status.")
        time.sleep(5)

        # Step 8: Click "Apply"
        apply_btn = WebDriverWait(driver, 30).until(
            EC.element_to_be_clickable((By.XPATH, "//div[@class='filter-options show-popup']//a[text()='Apply']"))
        )
        apply_btn.click()
        print("Applied filter.")
        time.sleep(30)  # Wait for data to refresh

        # Step 9: Click on "Export Spreadsheet"
        export_btn = WebDriverWait(driver, 30).until(
            EC.element_to_be_clickable((By.XPATH, "//a[contains(text(), 'Export Spreadsheet')]"))
        )
        export_btn.click()
        print("File Downloading! Waiting 5 minutes.")
        time.sleep(300)  # Allow time for download to complete

    except Exception as e:
        log_error(ERROR_CODES["download_failed"], "Download process failed.", matrix_row["script_name"])
        print("Download process failed, ending process.")
        driver.quit()
        return None

    return download_folder


def run_bob_caresource(driver, matrix_row, date_info):
    print("Running BOB Caresource handler...")

    today_date = datetime.now().strftime("%m%d%Y")
    current_year = datetime.now().strftime("%Y")
    download_folder = os.path.normpath(matrix_row["download_path"])
    driver.get(matrix_row["source_url"])

    # Perform login if needed
    if matrix_row["source_login"].upper() == "YES":
        try:
            email_field = WebDriverWait(driver, 10).until(
                EC.presence_of_element_located((By.XPATH, "//input[@placeholder='Email or User Name']"))
            )
            email_field.send_keys(matrix_row["source_email"])

            password_field = WebDriverWait(driver, 10).until(
                EC.presence_of_element_located((By.XPATH, "//input[@placeholder='Password']"))
            )
            password_field.send_keys(matrix_row["source_password"])

            login_button = WebDriverWait(driver, 10).until(
                EC.element_to_be_clickable((By.CSS_SELECTOR, "button[id*='logOnFormSubmit']"))
            )
            login_button.click()
            time.sleep(10)  # Wait for login
            print("Choose Two-Factor Authentication Method!")
            # WebDriverWait(driver, 120).until(
            # EC.element_to_be_clickable((By.XPATH, "//*[@id='tfaChoiceEmailButton']"))
            # ).click()
            print("Code sent!")
            time.sleep(10)  # Wait for 2FA process

            if matrix_row["otp_needed"].upper() == "YES":
                try:
                    otp_code = fetch_otp_code_from_file(matrix_row)
                    print(f"OTP Code found: {otp_code}")

                    # Input OTP
                    WebDriverWait(driver, 30).until(
                        EC.presence_of_element_located((By.XPATH, "//*[@id='j_otpcode']"))
                    ).send_keys(otp_code)
                    print("OTP entered.")

                    # Click Submit
                    WebDriverWait(driver, 30).until(
                        EC.element_to_be_clickable(
                            (By.XPATH, "//*[@id='logon_continue']"))
                    ).click()
                    print("OTP submitted.")
                    time.sleep(10)

                except FileNotFoundError:
                    log_error(ERROR_CODES["OTP_error"], "OTP File was not found or was not submitted correctly.",
                              matrix_row["script_name"])
                    print("OTP File was not found or was not submitted correctly. Exiting...")
                    driver.quit()
                    return None

        except Exception as e:
            log_error(ERROR_CODES["login_error"], "Login page timeout or login fields not found.",
                      matrix_row["script_name"])
            print("Login page not found or timeout occurred. Exiting...")
            driver.quit()
            return None

    """Selects the correct period year."""
    try:
        driver.refresh()
        time.sleep(30)

        print(f"Selecting period year: {current_year}")
        # Click dropdown
        dropdown_arrow = WebDriverWait(driver, 10).until(
            EC.element_to_be_clickable(
                (By.ID, "application-OverviewPage-display-component---dashboard--PeriodYear-arrow"))
        )
        dropdown_arrow.click()
        time.sleep(2)  # Wait for dropdown to open

        # Select the correct year
        year_option = WebDriverWait(driver, 10).until(
            EC.element_to_be_clickable((By.XPATH, f"//li[contains(text(), '{current_year}')]"))
        )
        year_option.click()
        print(f"Successfully set Period Year to {current_year}")
    except Exception as e:
        log_error(ERROR_CODES["navigation_error"], "Navigation process failed, could not set Period Year.",
                  matrix_row["script_name"])
        print("Navigation process failed to set Period Year, ending process.")
        driver.quit()
        return None

    try:
        print("Opening 'Agent List'...")

        agent_list_button = WebDriverWait(driver, 10).until(
            EC.element_to_be_clickable(
                (By.XPATH,
                 "//div[contains(@class, 'sapFCardHeader')]//span[contains(text(), 'Downline Active Members')]"))
        )
        agent_list_button.click()
        print("'Agent List' opened.")
        time.sleep(60)  # Allow time for content to load
    except Exception as e:
        log_error(ERROR_CODES["navigation_error"],
                  "Navigation process failed, could not reach Downline Active Members.", matrix_row["script_name"])
        print("Navigation process failed to reach Downline Active Members, ending process.")
        driver.quit()
        return None

    try:
        print("Initiating download...")

        download_button = WebDriverWait(driver, 10).until(
            EC.element_to_be_clickable((By.XPATH, "//button[@title='Export']"))
        )
        download_button.click()
        time.sleep(2)

        # Enter file name
        file_name_prefix = matrix_row["extracted_file_prefix"]
        file_name = f"{file_name_prefix}{today_date}"
        file_input = WebDriverWait(driver, 10).until(
            EC.presence_of_element_located((By.XPATH, "//input[contains(@id, 'filename-input-inner')]"))
        )
        file_input.clear()
        file_input.send_keys(file_name)
        print(f"Entered file name: {file_name}")

        # Click export
        export_button = WebDriverWait(driver, 10).until(
            EC.element_to_be_clickable((By.XPATH, "//button[contains(@id, 'Export')]"))
        )
        export_button.click()
        print("Clicked 'Export' button")
        time.sleep(60)  # Wait for file to download
    except Exception as e:
        log_error(ERROR_CODES["download_error"], "Download process failed.", matrix_row["script_name"])
        print("Download process failed, ending process.")
        driver.quit()
        return None

    return download_folder


def run_bob_geoblue(driver, matrix_row, date_info):
    print("Running BOB Geoblue handler...")

    download_folder = os.path.normpath(matrix_row["download_path"])
    driver.get(matrix_row["source_url"])

    # Perform login if needed
    if matrix_row["source_login"].upper() == "YES":
        try:
            # Step 2: Enter username
            WebDriverWait(driver, 30).until(
                EC.presence_of_element_located((By.ID, "loginUname"))
            ).send_keys(matrix_row["source_email"])

            # Step 3: Enter password
            WebDriverWait(driver, 30).until(
                EC.presence_of_element_located((By.ID, "loginPword"))
            ).send_keys(matrix_row["source_password"])

            # Step 4: Click Sign In
            WebDriverWait(driver, 30).until(
                EC.presence_of_element_located((By.XPATH, "//input[@type='submit' and @value='Sign In']"))
            ).click()
            print("Logged in successfully!")
            time.sleep(10)
        except Exception as e:
            log_error(ERROR_CODES["login_error"], "Login page timeout or login fields not found.",
                      matrix_row["script_name"])
            print("Login page not found or timeout occurred. Exiting...")
            driver.quit()
            return None

    try:
        # Step 5: Click on 2025 Sales button
        sales_button = WebDriverWait(driver, 10).until(
            EC.element_to_be_clickable((By.XPATH, "//*[@id='thsYear']/div"))
        )
        sales_button.click()
        print("Clicked on 2025 Sales button.")

        # Step 6: Wait for 120 seconds
        print("Waiting for 120 seconds to ensure page load...")
        time.sleep(120)
    except Exception as e:
        log_error(ERROR_CODES["navigation_error"], "Navigation process failed, could not reach download page.",
                  matrix_row["script_name"])
        print("Navigation process failed to reach download page, ending process.")
        driver.quit()
        return None

    # Step 2: Click the download button
    try:
        # Step 7: Click on Download to Excel button
        download_button = WebDriverWait(driver, 10).until(
            EC.element_to_be_clickable((By.XPATH, "//*[@id='row1box6']"))
        )
        download_button.click()
        print("Clicked on Download to Excel button.")

        print("Waiting for 120 seconds to ensure download completes...")
        time.sleep(120)
    except Exception as e:
        log_error(ERROR_CODES["download_button_not_found"], "'Download' button not found.", matrix_row["script_name"])
        print("Download button not found, ending process.")
        driver.quit()
        return None

    return download_folder


def run_bob_gerber(driver, matrix_row, date_info):
    print("Running BOB Gerber handler...")

    download_folder = os.path.normpath(matrix_row["download_path"])
    driver.get(matrix_row["source_url"])

    # Perform login if needed
    if matrix_row["source_login"].upper() == "YES":
        try:
            # Step 2: Enter username
            WebDriverWait(driver, 30).until(
                EC.presence_of_element_located((By.ID, "UserID"))
            ).send_keys("B0030910")

            # Step 3: Enter password
            WebDriverWait(driver, 30).until(
                EC.presence_of_element_located((By.ID, "Password"))
            ).send_keys(matrix_row["source_password"])

            # Step 4: Click Log In
            WebDriverWait(driver, 30).until(
                EC.presence_of_element_located((By.XPATH, "//input[@type='submit' and @value='Log in']"))
            ).click()
            print("Logged in successfully!")
            time.sleep(5)
        except Exception as e:
            log_error(ERROR_CODES["login_error"], "Login page timeout or login fields not found.",
                      matrix_row["script_name"])
            print("Login page not found or timeout occurred. Exiting...")
            driver.quit()
            return None

    try:
        # Step 5: Click on 'My Customers'
        my_customers = WebDriverWait(driver, 30).until(
            EC.element_to_be_clickable((By.LINK_TEXT, "My Customers"))
        )
        my_customers.click()
        print("Clicked on 'My Customers'.")

        # Step 6: Click on 'My Customer Dashboard'
        customer_dashboard = WebDriverWait(driver, 30).until(
            EC.element_to_be_clickable((By.LINK_TEXT, "My Customer Dashboard"))
        )
        customer_dashboard.click()
        print("Clicked on 'My Customer Dashboard'.")

        # Step 7: Wait for 40 seconds
        print("Waiting for 40 seconds to ensure page load...")
        time.sleep(40)

        # Step 8: Click on 'view all'
        view_all = WebDriverWait(driver, 30).until(
            EC.element_to_be_clickable((By.LINK_TEXT, "view all"))
        )
        view_all.click()
        print("Clicked on 'view all'.")

        # Step 9: Wait for 60 seconds
        print("Waiting for 60 seconds to ensure page load...")
        time.sleep(60)
    except Exception as e:
        log_error(ERROR_CODES["navigation_error"], "Navigation process failed, could not reach download page.",
                  matrix_row["script_name"])
        print("Navigation process failed to reach download page, ending process.")
        driver.quit()
        return None

    try:
        # Step 9a : Click select all
        select_all = WebDriverWait(driver, 30).until(
            EC.element_to_be_clickable((By.XPATH,
                                        "//*[@id='page']/div[2]/ap-wrapper/div/div/app-product-context/ap-case-statuses/div[2]/div/ap-panel[2]/div/div[2]/div[1]/ap-case-statuses-table/div[1]/div[1]/div/button[1]"))
        )
        select_all.click()
        print("All options selected")
        time.sleep(2)

        # Step 9b : Click select all
        unselect_payment = WebDriverWait(driver, 30).until(
            EC.element_to_be_clickable((By.XPATH,
                                        "//*[@id='page']/div[2]/ap-wrapper/div/div/app-product-context/ap-case-statuses/div[2]/div/ap-panel[2]/div/div[2]/div[1]/ap-case-statuses-table/div[1]/div[2]/div[2]/div[2]/label/input"))
        )
        unselect_payment.click()
        print("Make a payment option unselected")
        time.sleep(1)

        # Step 9c : Click select all
        unselect_policykit = WebDriverWait(driver, 30).until(
            EC.element_to_be_clickable((By.XPATH,
                                        "//*[@id='page']/div[2]/ap-wrapper/div/div/app-product-context/ap-case-statuses/div[2]/div/ap-panel[2]/div/div[2]/div[1]/ap-case-statuses-table/div[1]/div[2]/div[5]/div[2]/label/input"))
        )
        unselect_policykit.click()
        print("Policy Kit option unselected")
        time.sleep(1)

        # Step 10: Scroll down and click on 'Download as Microsoft Excel'
        download_button = WebDriverWait(driver, 30).until(
            EC.element_to_be_clickable((By.ID, "export_xls"))
        )
        driver.execute_script("arguments[0].scrollIntoView(true);", download_button)
        download_button.click()
        print("Clicked on 'Download as Microsoft Excel' button.")

        # Step 11: Wait for 60 seconds to ensure download completes
        print("Waiting for 60 seconds to ensure download completes...")
        time.sleep(60)
    except Exception as e:
        log_error(ERROR_CODES["download_error"], "Download process failed.", matrix_row["script_name"])
        print("Download process failed, ending process.")
        driver.quit()
        return None

    return download_folder


def run_bob_manhattanlife(driver, matrix_row, date_info):
    print("Running BOB ManhattanLife handler...")

    download_folder = os.path.normpath(matrix_row["download_path"])
    driver.get(matrix_row["source_url"])

    # Perform login if needed
    if matrix_row["source_login"].upper() == "YES":
        try:
            # Enter the login email address
            WebDriverWait(driver, 15).until(
                EC.presence_of_element_located((By.XPATH, "//*[@id='txtAgentUserName']"))
            ).send_keys(matrix_row["source_email"])

            # Enter Password
            WebDriverWait(driver, 15).until(
                EC.presence_of_element_located((By.XPATH, "//*[@id='txtAgentPassword']"))
            ).send_keys(matrix_row["source_password"])

            # Press Log In
            login_button = WebDriverWait(driver, 15).until(
                EC.element_to_be_clickable((By.XPATH, "//*[@id='btnLogin']"))
            )
            login_button.click()
            print("Login successful")
        except Exception as e:
            log_error(ERROR_CODES["login_error"], "Login page timeout or login fields not found.",
                      matrix_row["script_name"])
            print("Login page not found or timeout occurred. Exiting...")
            driver.quit()
            return None

    try:
        # click Inforce Business
        ifb = WebDriverWait(driver, 15).until(
            EC.element_to_be_clickable((By.XPATH, "//*[@id='mnuInforceBusiness']"))
        )
        ifb.click()
        print("Inforce Business Clicked")

        # click Agent Policy List
        apl = WebDriverWait(driver, 15).until(
            EC.element_to_be_clickable((By.XPATH, "//*[@id='agentpolicylist']/a"))
        )
        apl.click()
        print("Agent Policy List Clicked")
    except Exception as e:
        log_error(ERROR_CODES["navigation_error"], "Navigation process failed, could not reach download page.",
                  matrix_row["script_name"])
        print("Navigation process failed to reach download page, ending process.")
        driver.quit()
        return None

    try:
        download_excel = WebDriverWait(driver, 15).until(
            EC.element_to_be_clickable((By.XPATH, "//*[@id='MainContent_lblXLSDownload']"))
        )
        download_excel.click()
        print("Download Excel file clicked")
        print("File Downloading")
        time.sleep(150)  # Allow time for download to complete
    except Exception as e:
        log_error(ERROR_CODES["download_button_not_found"], "'Download' button not found.", matrix_row["script_name"])
        print("Download button not found, ending process.")
        driver.quit()
        return None

    return download_folder


def run_bob_medica(driver, matrix_row, date_info):
    print("Running BOB Medica handler...")

    download_folder = os.path.normpath(matrix_row["download_path"])
    driver.get(matrix_row["source_url"])

    # Step 1: Login
    if matrix_row["source_login"].upper() == "YES":
        try:
            WebDriverWait(driver, 60).until(
                EC.presence_of_element_located((By.XPATH, "//*[@id='Form1']/input[3]"))
            ).send_keys(matrix_row["source_email"])

            WebDriverWait(driver, 60).until(
                EC.presence_of_element_located((By.XPATH, "//*[@id='Form1']/input[4]"))
            ).send_keys(matrix_row["source_password"])

            WebDriverWait(driver, 60).until(
                EC.element_to_be_clickable((By.XPATH, "//*[@id='Form1']/input[5]"))
            ).click()

            print("Login successful")

        except Exception as e:
            log_error(
                ERROR_CODES["login_error"],
                "Login page timeout or login fields not found.",
                matrix_row["script_name"]
            )
            print(f"Login failed. Error: {e}")
            driver.quit()
            return None

    # Step 2: Handle new tab
    try:
        WebDriverWait(driver, 60).until(EC.number_of_windows_to_be(2))
        handles = driver.window_handles
        print(handles)

        original_handle = handles[0]
        new_handle = handles[1]

        driver.switch_to.window(original_handle)
        driver.close()
        driver.switch_to.window(new_handle)

        time.sleep(10)
        driver.refresh()
        time.sleep(15)

    except Exception as e:
        log_error(
            ERROR_CODES["navigation_error"],
            "Could not switch to Medica portal tab.",
            matrix_row["script_name"]
        )
        print(f"Tab handling failed. Error: {e}")
        driver.quit()
        return None

    # Step 3: Navigate to My Policies
    try:
        WebDriverWait(driver, 90).until(
            EC.element_to_be_clickable(
                (By.XPATH, "//*[@class='ui-menu-item-wrapper' and text()='Individual Health']")
            )
        ).click()
        print("Individual Health clicked")
        time.sleep(2)

        WebDriverWait(driver, 90).until(
            EC.element_to_be_clickable(
                (By.XPATH,
                 "//div[normalize-space()='Individual Health']/following-sibling::ul//a[normalize-space()='My Policies']")
            )
        ).click()
        print("My Policies clicked")

        WebDriverWait(driver, 120).until(
            EC.presence_of_element_located((By.ID, "fltIpgCarrier"))
        )
        time.sleep(5)

    except Exception as e:
        log_error(
            ERROR_CODES["navigation_error"],
            "Navigation process failed, could not reach My Policies page.",
            matrix_row["script_name"]
        )
        print(f"Navigation failed. Error: {e}")
        driver.quit()
        return None

    # Step 4: Clear existing grid filters if present
    try:
        clear_filters_buttons = driver.find_elements(
            By.XPATH,
            "//div[contains(@class,'filter-button') and contains(normalize-space(.), 'Clear Grid Filters')]"
        )

        cleared = False
        for btn in clear_filters_buttons:
            if btn.is_displayed():
                driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", btn)
                time.sleep(1)
                driver.execute_script("arguments[0].click();", btn)
                print("Cleared existing grid filters")
                time.sleep(5)
                cleared = True
                break

        if not cleared:
            print("No existing grid filters to clear")

    except Exception as e:
        print(f"Could not clear grid filters, continuing. Error: {e}")

    # Step 5: Open carrier dropdown and select Medica checkbox
    try:
        # click carrier dropdown
        carrier_box = WebDriverWait(driver, 60).until(
            EC.element_to_be_clickable((By.ID, "fltIpgCarrier"))
        )
        driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", carrier_box)
        time.sleep(1)
        carrier_box.click()
        print("Carrier filter clicked")
        time.sleep(2)

        # wait until the Medica checkbox is actually clickable after dropdown opens
        medica_checkbox = WebDriverWait(driver, 60).until(
            EC.element_to_be_clickable((By.XPATH, "//*[@id='Ipg_Carrier_8']"))
        )

        driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", medica_checkbox)
        time.sleep(1)

        if not medica_checkbox.is_selected():
            try:
                medica_checkbox.click()
            except Exception:
                driver.execute_script("arguments[0].click();", medica_checkbox)
            print("Medica selected")
        else:
            print("Medica already selected")

        time.sleep(5)

    except Exception as e:
        log_error(
            ERROR_CODES["navigation_error"],
            "Could not select MEDICA from carrier filter dropdown.",
            matrix_row["script_name"]
        )
        print(f"Carrier dropdown selection failed. Error: {e}")
        driver.quit()
        return None

    # Step 6: Export as displayed
    try:
        excel_icon = WebDriverWait(driver, 150).until(
            EC.element_to_be_clickable((
                By.XPATH,
                "//a[@class='form-button' and img[contains(@src,'gridexcel.png')]]"
            ))
        )
        excel_icon.click()
        print("Excel icon clicked")

        WebDriverWait(driver, 60).until(
            EC.visibility_of_element_located((By.CLASS_NAME, "ui-dialog-title"))
        )
        print("Export dialog appeared")

        as_displayed_btn = WebDriverWait(driver, 60).until(
            EC.element_to_be_clickable((
                By.XPATH,
                "//button[normalize-space()='As Displayed']"
            ))
        )
        as_displayed_btn.click()
        print("Exported As Displayed")

        print("File downloading. Waiting 3 minutes.")
        time.sleep(180)

    except Exception as e:
        log_error(
            ERROR_CODES["download_button_not_found"],
            "'Download' button or export option not found.",
            matrix_row["script_name"]
        )
        print(f"Export failed. Error: {e}")
        driver.quit()
        return None

    return download_folder


def run_bob_molina(driver, matrix_row, date_info):
    print("Running BOB Molina handler...")

    download_folder = os.path.normpath(matrix_row["download_path"])
    driver.get(matrix_row["source_url"])

    # Perform login if needed
    if matrix_row["source_login"].upper() == "YES":
        try:
            WebDriverWait(driver, 60).until(
                EC.presence_of_element_located((By.XPATH, "//input[@placeholder='username or email']"))
            ).send_keys(matrix_row["source_email"])
            WebDriverWait(driver, 60).until(
                EC.presence_of_element_located((By.XPATH, "//input[@placeholder='password']"))
            ).send_keys(matrix_row["source_password"])
            WebDriverWait(driver, 60).until(
                EC.element_to_be_clickable((By.XPATH, '//*[@id="submit"]'))
            ).click()
            print("Logged in successfully!")
            time.sleep(10)
        except Exception as e:
            log_error(ERROR_CODES["login_error"], "Login page timeout or login fields not found.",
                      matrix_row["script_name"])
            print("Login page not found or timeout occurred. Exiting...")
            driver.quit()
            return None

    try:
        # Step 3: Open Molina Domain
        original_window = driver.current_window_handle
        WebDriverWait(driver, 60).until(
            EC.element_to_be_clickable((By.XPATH, '//*[@id="domain_container"]/div/div[7]/div/div[2]/div'))
        ).click()
        print("Molina domain selected.")
        WebDriverWait(driver, 60).until(EC.number_of_windows_to_be(2))

        # Switch to the new tab
        for handle in driver.window_handles:
            if handle != original_window:
                driver.switch_to.window(handle)
                break
        print("Switched to Molina tab.")
    except Exception as e:
        log_error(ERROR_CODES["navigation_error"], "Navigation process failed, could not reach Molina tab.",
                  matrix_row["script_name"])
        print("Navigation process failed to reach Molina tab, ending process.")
        driver.quit()
        return None

    try:
        # Step 4: Open the dropdown menu
        WebDriverWait(driver, 60).until(
            EC.element_to_be_clickable((By.XPATH, '//*[@id="accordionSidebar"]/li[3]/a'))
        ).click()
        print("Dropdown menu opened.")
        time.sleep(5)

        WebDriverWait(driver, 60).until(
            EC.element_to_be_clickable((By.XPATH, '//*[@id="collapse_3"]/div/a[2]'))
        ).click()
        print("Option selected.")

        # Step 5: Select the download option
        WebDriverWait(driver, 60).until(
            EC.element_to_be_clickable((By.XPATH, '//*[@id="policy_search_form"]/div[3]/div[1]/div/div/button/div/div/div'))
        ).click()
        print("Dropdown expanded.")
        WebDriverWait(driver, 60).until(
            EC.element_to_be_clickable(
                (By.XPATH, '//*[@id="bs-select-2-0"]'))
        ).click()
        print("Option selected.")
        time.sleep(10)
        WebDriverWait(driver, 60).until(
            EC.element_to_be_clickable(
                (By.ID, 'submit'))
        ).click()
        print("Searching........")
        time.sleep(120)
    except Exception as e:
        log_error(ERROR_CODES["navigation_error"], "Navigation process failed, could not apply filters.",
                  matrix_row["script_name"])
        print("Navigation process failed to apply filters, ending process.")
        driver.quit()
        return None

    try:
        # Step 6: Download the file
        WebDriverWait(driver, 60).until(
            EC.element_to_be_clickable((By.XPATH, '//*[@id="download"]'))
        ).click()
        print("Download initiated. Waiting for completion...")
        time.sleep(120)  # Adjust time for download completion
    except Exception as e:
        log_error(ERROR_CODES["download_button_not_found"], "'Download' button not found.", matrix_row["script_name"])
        print("Download button not found, ending process.")
        driver.quit()
        return None

    return download_folder


def run_bob_molina_medicare(driver, matrix_row, date_info):
    print("Running BOB Molina Medicare handler...")

    download_folder = os.path.normpath(matrix_row["download_path"])
    driver.get(matrix_row["source_url"])

    # Perform login if needed
    if matrix_row["source_login"].upper() == "YES":
        try:
            # Step 1: Log in
            WebDriverWait(driver, 60).until(
                EC.presence_of_element_located((By.XPATH, "//input[@placeholder='username or email']"))
            ).send_keys(matrix_row["source_email"])
            WebDriverWait(driver, 60).until(
                EC.presence_of_element_located((By.XPATH, "//input[@placeholder='password']"))
            ).send_keys(matrix_row["source_password"])
            WebDriverWait(driver, 60).until(
                EC.element_to_be_clickable((By.XPATH, '//*[@id="submit"]'))
            ).click()
            print("Logged in successfully!")
            time.sleep(10)
        except Exception as e:
            log_error(ERROR_CODES["login_error"], "Login page timeout or login fields not found.",
                      matrix_row["script_name"])
            print("Login page not found or timeout occurred. Exiting...")
            driver.quit()
            return None

    try:
        # Step 2: Open Molina Domain
        original_window = driver.current_window_handle
        WebDriverWait(driver, 60).until(
            EC.element_to_be_clickable((By.XPATH, '//*[@id="domain_container"]/div/div[7]/div/div[2]/div'))
        ).click()
        print("Molina domain selected.")
        WebDriverWait(driver, 60).until(EC.number_of_windows_to_be(2))

        # Switch to the new tab
        for handle in driver.window_handles:
            if handle != original_window:
                driver.switch_to.window(handle)
                break
        print("Switched to Molina tab.")
    except Exception as e:
        log_error(ERROR_CODES["navigation_error"], "Navigation process failed, could not reach Molina tab.",
                  matrix_row["script_name"])
        print("Navigation process failed to reach Molina tab, ending process.")
        driver.quit()
        return None

    try:
        # Step 3: Open the Book of Business dropdown menu
        WebDriverWait(driver, 60).until(
            EC.element_to_be_clickable((By.XPATH, '//*[@id="accordionSidebar"]/li[3]/a'))
        ).click()
        print("Book of Business dropdown menu opened.")
        time.sleep(5)

        # Step 4: Select Medicare Search
        WebDriverWait(driver, 60).until(
            EC.element_to_be_clickable(
                (By.XPATH, "//a[contains(@href, '/portal/member_search.htm') and contains(., 'Medicare Search')]"))
        ).click()
        print("Medicare Search selected.")
        time.sleep(10)

        # Step 5: Click Search as-is
        WebDriverWait(driver, 60).until(
            EC.presence_of_element_located((By.ID, "member_search_form"))
        )
        WebDriverWait(driver, 60).until(
            EC.element_to_be_clickable(
                (By.XPATH, "//form[@id='member_search_form']//button[@id='submit' and @name='submit']"))
        ).click()
        print("Searching........")

        # Step 6: Wait for results to populate
        WebDriverWait(driver, 120).until(
            EC.presence_of_element_located((By.ID, "member_result"))
        )
        time.sleep(10)
        print("Results populated.")
    except Exception as e:
        log_error(ERROR_CODES["navigation_error"], "Navigation process failed, could not complete Medicare Search.",
                  matrix_row["script_name"])
        print("Navigation process failed to complete Medicare Search, ending process.")
        driver.quit()
        return None

    try:
        # Step 7: Download the file
        WebDriverWait(driver, 120).until(
            EC.element_to_be_clickable((By.ID, "dButton"))
        ).click()
        print("Download initiated. Waiting for completion...")
        time.sleep(120)
    except Exception as e:
        log_error(ERROR_CODES["download_button_not_found"], "'Download' button not found.", matrix_row["script_name"])
        print("Download button not found, ending process.")
        driver.quit()
        return None

    return download_folder


def run_bob_newera(driver, matrix_row, date_info):
    print("Running BOB NewEra handler...")

    current_year = datetime.now().strftime("%Y")
    download_folder = os.path.normpath(matrix_row["download_path"])
    driver.get(matrix_row["source_url"])

    # Perform login if needed
    if matrix_row["source_login"].upper() == "YES":
        try:
            # Step 2: Enter username
            WebDriverWait(driver, 60).until(
                EC.presence_of_element_located((By.ID, "MainContent_txtUserID"))
            ).send_keys(matrix_row["source_email"])

            # Step 3: Enter password
            WebDriverWait(driver, 60).until(
                EC.presence_of_element_located((By.ID, "Password"))
            ).send_keys(matrix_row["source_password"])

            # Step 4: Click Log In
            WebDriverWait(driver, 60).until(
                EC.element_to_be_clickable((By.ID, "MainContent_btnLogIn"))
            ).click()

            print("Logged in successfully!")
            time.sleep(30)
        except Exception as e:
            log_error(ERROR_CODES["login_error"], "Login page timeout or login fields not found.",
                      matrix_row["script_name"])
            print("Login page not found or timeout occurred. Exiting...")
            driver.quit()
            return None

    try:
        # Step 5: Click on 'Reports'
        reports = WebDriverWait(driver, 150).until(
            EC.element_to_be_clickable(
                (By.XPATH, "//a[@class='menu-toggle waves-effect waves-block' and span[text()='Reports']]"))
        )
        reports.click()
        print("Clicked on 'Reports'.")

        # Step 6: Click on 'Agent Production'
        agent_prod = WebDriverWait(driver, 150).until(
            EC.element_to_be_clickable((By.XPATH, "//*[@id='wgtLeftNav_liReports']/ul/li/a"))
        )
        agent_prod.click()
        time.sleep(60)
        print("Clicked on 'Agent Production'.")
    except Exception as e:
        log_error(ERROR_CODES["navigation_error"], "Navigation process failed, could not reach destination.",
                  matrix_row["script_name"])
        print("Navigation process failed to reach destination, ending process.")
        driver.quit()
        return None

    try:
        # Step 7: Open dropdown and select agent number
        dropdown = WebDriverWait(driver, 60).until(
            EC.presence_of_element_located((By.ID, "ddlAgentNumber"))
        )
        dropdown.click()
        option = driver.find_element(By.XPATH,
                                     "//option[@value='6248746A3831396F385249774A4965576D4F3245756271516F6442477038314E6F33714F544D5A7A3955673D~PAL']")
        option.click()
        print("Selected agent number '601183000 PAL'.")

        # Step 8: Set start date to January 1st of the current year and end date to yesterday
        start_date = driver.find_element(By.ID, "txtStartDate")
        start_date.clear()
        start_date.send_keys(f"01/01/{current_year}")

        end_date = driver.find_element(By.ID, "txtEndDate")
        end_date.clear()
        end_date.send_keys((datetime.now() - timedelta(days=1)).strftime("%m/%d/%Y"))
        print("Set date range for report.")

        # Step 9: Click on Search
        search_button = WebDriverWait(driver, 60).until(
            EC.element_to_be_clickable((By.ID, "btnSearch"))
        )
        search_button.click()
        print("Clicked on Search.")
        time.sleep(60)
    except Exception as e:
        log_error(ERROR_CODES["navigation_error"], "Navigation process failed to apply filters correctly.",
                  matrix_row["script_name"])
        print("Navigation process failed to apply filters correctly, ending process.")
        driver.quit()
        return None

    # Step 2: Click the download button
    try:
        download_link = WebDriverWait(driver, 30).until(
            EC.element_to_be_clickable((By.XPATH, "//span[text()='Click to download']"))
        )
        driver.execute_script("window.scrollBy(0,500);", download_link)
        download_link.click()
        print("Clicked on 'Click to download'.")
        print("Waiting for 30 seconds to ensure download completes...")
        time.sleep(30)
    except Exception as e:
        log_error(ERROR_CODES["download_button_not_found"], "Download button not found.", matrix_row["script_name"])
        print("Download button not found, ending process.")
        driver.quit()
        return None

    return download_folder


def run_bob_oscar(driver, matrix_row, date_info):
    print("Running BOB Oscar handler...")

    download_folder = os.path.normpath(matrix_row["download_path"])
    driver.get(matrix_row["source_url"])

    # Perform login if needed
    if matrix_row["source_login"].upper() == "YES":
        try:
            # Step 2: Log in
            WebDriverWait(driver, 30).until(
                EC.presence_of_element_located((By.XPATH, "//input[@placeholder='Enter email address']"))
            ).send_keys(matrix_row["source_email"])
            WebDriverWait(driver, 30).until(
                EC.presence_of_element_located((By.XPATH, "//input[@placeholder='Enter password']"))
            ).send_keys(matrix_row["source_password"])
            WebDriverWait(driver, 30).until(
                EC.element_to_be_clickable((By.XPATH, "//button[@type='submit']"))
            ).click()
            print("Logged in successfully!")
            time.sleep(10)
        except Exception as e:
            log_error(ERROR_CODES["login_error"], "Login page timeout or login fields not found.",
                      matrix_row["script_name"])
            print("Login page not found or timeout occurred. Exiting...")
            driver.quit()
            return None

    try:
        # Step 3: Navigate to 'Oscar for Business'
        oscar_for_business_link = WebDriverWait(driver, 20).until(
            EC.element_to_be_clickable((By.XPATH, "//a[@title='Oscar For Business']"))
        )
        oscar_for_business_link.click()
        print("Clicked on 'Oscar For Business'.")
        time.sleep(10)

        # Step 4: Open 'Individual Book'
        individual_book_link = WebDriverWait(driver, 10).until(
            EC.element_to_be_clickable((By.XPATH, "//a[@href='/book/ivl']"))
        )
        individual_book_link.click()
        print("Opened 'Individual Book'.")
        time.sleep(300)
    except Exception as e:
        log_error(ERROR_CODES["navigation_error"], "Navigation process failed to reach destination.",
                  matrix_row["script_name"])
        print("Navigation process failed to reach destination, ending process.")
        driver.quit()
        return None

    try:
        # Step 4.1: Click on the dropdown
        dropdown_button = WebDriverWait(driver, 10).until(
            EC.element_to_be_clickable(
                (By.XPATH, "/html/body/div[1]/div/div[1]/div[2]/div[3]/div[2]/div/div[1]/div/div/div/button"))
        )
        dropdown_button.click()
        print("Dropdown clicked.")
        time.sleep(5)  # Give the dropdown some time to expand

        # Step 4.2: Set the dropdown to "Active"
        # Locate the label or span associated with the "Active" checkbox
        active_option_label = WebDriverWait(driver, 10).until(
            EC.element_to_be_clickable(
                (By.XPATH, "//label[@for='checkboxgroup-21']/span[@class='h-HruOvqoaykJJZoDZ7U_Z']")
            )
        )

        # Scroll into view if necessary
        driver.execute_script("arguments[0].scrollIntoView(true);", active_option_label)
        time.sleep(1)  # Allow time for the scroll

        # Click the "Active" checkbox label or span
        active_option_label.click()
        print("Set the dropdown to 'Active'.")
        time.sleep(5)  # Allow some time for the page to apply filters

        # Step 4.3 : Apply dropdown options
        apply_button = WebDriverWait(driver, 10).until(
            EC.element_to_be_clickable(
                (By.XPATH, "//button[@class='h-k_d2JmvaC03wwSuT1kvQ']//span[text()='Apply']"))
        )
        # Scroll using ActionChains
        # Scroll into view
        driver.execute_script("arguments[0].scrollIntoView(true);", apply_button)
        time.sleep(5)  # Allow scrolling to complete
        # Attempt to click the Apply button
        try:
            apply_button.click()  # Standard Selenium click
            time.sleep(30)
            print("Successfully clicked the Apply button using standard click.")
        except Exception as e:
            print(f"Standard click failed: {e}. Attempting JavaScript click.")
            driver.execute_script("arguments[0].click();", apply_button)  # JavaScript click
            time.sleep(30)
            print("Successfully clicked the Apply button using JavaScript click.")
    except Exception as e:
        log_error(ERROR_CODES["navigation_error"], "Navigation process failed to apply filters correctly.",
                  matrix_row["script_name"])
        print("Navigation process failed to apply filters correctly, ending process.")
        driver.quit()
        return None

    try:
        # Step 5: Export CSV
        export_button = WebDriverWait(driver, 20).until(
            EC.element_to_be_clickable((By.XPATH, "//button[.//text()[contains(., 'Export CSV')]]"))
        )
        export_button.click()
        print("Export initiated.")
        time.sleep(10)  # Wait for download to complete
        # Step 5.1 Export Current results
        export_button_curr = WebDriverWait(driver, 20).until(
            EC.element_to_be_clickable((By.XPATH, "//*[@id='filteredCurrent results']"))
        )
        export_button_curr.click()
        print("Export Current Results initiated.")
        time.sleep(300)  # Wait for download to complete
    except Exception as e:
        log_error(ERROR_CODES["download_button_not_found"], "'Download' button not found.", matrix_row["script_name"])
        print("Download button not found, ending process.")
        driver.quit()
        return None

    return download_folder


def run_bob_pivot(driver, matrix_row, date_info):
    print("Running BOB Pivot handler...")

    current_year = datetime.now().strftime("%Y")
    download_folder = os.path.normpath(matrix_row["download_path"])
    driver.get(matrix_row["source_url"])

    # Perform login if needed
    if matrix_row["source_login"].upper() == "YES":
        try:
            # Human-like wait time
            time.sleep(random.uniform(4, 6))
            # Step 2: Enter username with human-like typing
            username_field = WebDriverWait(driver, 60).until(
                EC.presence_of_element_located((By.XPATH, "//input[@data-path='agentId']"))
            )
            username = matrix_row["source_email"]
            password = matrix_row["source_password"]
            for char in username:
                username_field.send_keys(char)
                time.sleep(random.uniform(0.1, 0.4))  # Random delay between keystrokes
            print("Entered username.")

            # Human-like wait time
            time.sleep(random.uniform(1.4, 2))

            # Step 3: Enter password with human-like typing
            password_field = WebDriverWait(driver, 60).until(
                EC.presence_of_element_located((By.XPATH, "//input[@data-path='password']"))
            )
            for char in password:
                password_field.send_keys(char)
                time.sleep(random.uniform(0.1, 0.4))  # Random delay between keystrokes
            print("Entered password.")

            # Step 4: Click Submit with a delay to simulate human behavior
            time.sleep(random.uniform(1, 3))  # Random delay before clicking submit
            sign_in_button = WebDriverWait(driver, 60).until(
                EC.element_to_be_clickable((By.XPATH, "//button[.//span[normalize-space()='Sign In']]"))
            )
            sign_in_button.click()
            print("Clicked on 'Sign In'.")

            try:
                # Handle potential CAPTCHA by retyping username and resubmitting
                time.sleep(random.uniform(2, 5))  # Wait for a random time between 2 to 5 seconds
                username_field = WebDriverWait(driver, 30).until(
                    EC.presence_of_element_located((By.XPATH, "//input[@data-path='agentId']"))
                )
                username_field.clear()
                for char in username:
                    username_field.send_keys(char)
                    time.sleep(random.uniform(0.1, 0.3))  # Random delay between keystrokes
                print("Retyped username.")

                time.sleep(random.uniform(2, 5))  # Wait for a random time between 2 to 5 seconds

                # Enter password with human-like typing again
                password_field = WebDriverWait(driver, 30).until(
                    EC.presence_of_element_located((By.XPATH, "//input[@data-path='password']"))
                )
                for char in password:
                    password_field.send_keys(char)
                    time.sleep(random.uniform(0.1, 0.4))  # Random delay between keystrokes
                print("Retyped password.")

                sign_in_button = WebDriverWait(driver, 60).until(
                    EC.element_to_be_clickable((By.XPATH, "//button[.//span[normalize-space()='Sign In']]"))
                )
                sign_in_button.click()
                print("Retried login after handling CAPTCHA.")
                time.sleep(10)
            except:
                print("Issue handling CAPTCHA or there was no captcha. Attempting to continue.")
                time.sleep(60)
        except Exception as e:
            log_error(ERROR_CODES["login_error"], "Login page timeout or login fields not found.",
                      matrix_row["script_name"])
            print("Login page not found or timeout occurred. Exiting...")
            driver.quit()
            return None

    # Step 2: Click the download button
    try:
        # Click on My Book of Business
        WebDriverWait(driver, 120).until(
            EC.element_to_be_clickable((By.XPATH, "//a[@data-text='Book of Business']"))
        ).click()

        # Step 5: Click Export dropdown/down arrow
        export_dropdown_button = WebDriverWait(driver, 120).until(
            EC.element_to_be_clickable((By.XPATH, "//button[.//span[normalize-space()='Export']]"))
        )
        export_dropdown_button.click()
        print("Clicked on Export dropdown.")

        time.sleep(2)

        # Step 6: Click From Date field
        from_date_button = WebDriverWait(driver, 120).until(
            EC.element_to_be_clickable(
                (By.XPATH, "//button[@data-dates-input='true'][.//span[normalize-space()='MM/DD/YYYY']]"))
        )
        from_date_button.click()
        print("Clicked on From Date field.")

        time.sleep(1)

        # Step 7: Click calendar month/year header,
        calendar_header_button = WebDriverWait(driver, 30).until(
            EC.element_to_be_clickable((By.XPATH, "//button[contains(@class, 'DatePickerInput-calendarHeaderLevel')]"))
        )
        calendar_header_button.click()
        print("Clicked calendar month/year header.")

        time.sleep(1)

        # Step 8: Select January
        jan_month_button = WebDriverWait(driver, 30).until(
            EC.element_to_be_clickable((By.XPATH, "//button[@data-picker-control='true' and normalize-space()='Jan']"))
        )
        jan_month_button.click()
        print("Selected January.")

        time.sleep(1)

        # Step 9: Select January 1st of current year
        jan_first_button = WebDriverWait(driver, 30).until(
            EC.element_to_be_clickable((By.XPATH, f"//button[@aria-label='1 January {current_year}']"))
        )
        jan_first_button.click()

        from_date = f"01/01/{current_year}"
        print(f"From date selected as {from_date}.")

        time.sleep(2)

        # Step 10: Click Export CSV
        export_csv_button = WebDriverWait(driver, 120).until(
            EC.element_to_be_clickable((By.XPATH, "//button[.//span[normalize-space()='Export CSV']]"))
        )
        export_csv_button.click()
        print("Clicked on 'Export CSV'.")

        # Step 11: Wait for the download to complete
        print("Waiting 2 minutes to ensure download completes...")
        time.sleep(120)
    except Exception as e:
        log_error(ERROR_CODES["download_button_not_found"], "'Download' button not found.", matrix_row["script_name"])
        print("Download button not found, ending process.")
        driver.quit()
        return None

    return download_folder


def run_bob_bcbs_mi(driver, matrix_row, date_info):
    print("Running BOB BCBS MI handler...")

    download_folder = os.path.normpath(matrix_row["download_path"])
    base_url = matrix_row["source_url"]
    driver.get(base_url)
    base_window = driver.current_window_handle

    for attempt in range(10):  # Retry up to 3 times
        try:
            try:
                current_handles = driver.window_handles

                # close all extra tabs
                for handle in current_handles:
                    if handle != base_window:
                        driver.switch_to.window(handle)
                        driver.close()

                driver.switch_to.window(base_window)
                driver.get(base_url)
                time.sleep(10)

            except Exception as e:
                print(f"Could not fully reset tabs before attempt {attempt + 1}. Error: {e}")

            if matrix_row["source_login"].upper() == "YES":
                # Step 1: Log in
                email_input = WebDriverWait(driver, 60).until(
                    EC.presence_of_element_located((By.XPATH, "//input[@name='email']"))
                )
                email_input.clear()
                email_input.send_keys(matrix_row["source_email"])

                password_input = WebDriverWait(driver, 60).until(
                    EC.presence_of_element_located((By.XPATH, "//input[@type='password']"))
                )
                password_input.clear()
                password_input.send_keys(matrix_row["source_password"])

                WebDriverWait(driver, 60).until(
                    EC.element_to_be_clickable((By.XPATH, "//button[@data-id='login-button']"))
                ).click()

                print("Login submitted.")
                time.sleep(10)

                # Step 2: Check whether OTP is required by looking for SEND CODE button
                otp_required = False

                if matrix_row["otp_needed"].upper() == "YES":
                    try:
                        WebDriverWait(driver, 15).until(
                            EC.element_to_be_clickable((By.XPATH, "//button[normalize-space()='SEND CODE']"))
                        ).click()
                        print("SEND CODE clicked. OTP flow triggered.")
                        time.sleep(10)
                        otp_required = True

                    except TimeoutException:
                        print("SEND CODE button not shown. Continuing without OTP...")
                        otp_required = False

                # Step 3: Retrieve OTP Code
                if otp_required:
                    try:
                        otp_input = WebDriverWait(driver, 30).until(
                            EC.presence_of_element_located((By.XPATH, "//input[@name='regcode']"))
                        )
                        print("OTP input detected.")

                        otp_code = fetch_otp_code_from_file(matrix_row)
                        print(f"OTP Code found: {otp_code}")

                        otp_input.clear()
                        otp_input.send_keys(otp_code)
                        print("OTP entered.")

                        WebDriverWait(driver, 30).until(
                            EC.element_to_be_clickable((By.XPATH, "//button[contains(normalize-space(),'CONTINUE')]"))
                        ).click()
                        print("OTP submitted.")
                        time.sleep(10)

                    except FileNotFoundError:
                        log_error(
                            ERROR_CODES["OTP_error"],
                            "OTP File was not found or was not submitted correctly.",
                            matrix_row["script_name"]
                        )
                        print("OTP File was not found or was not submitted correctly. Exiting...")
                        driver.quit()
                        return None

            # Step 4: Click Individual/Medicare
            WebDriverWait(driver, 60).until(
                EC.element_to_be_clickable(
                    (By.XPATH, "//a[@data-id='Individual' and normalize-space()='Individual/Medicare']"))
            ).click()
            print("Clicked Individual/Medicare.")
            time.sleep(10)

            # Step 5: Scroll all the way down
            driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
            print("Scrolled to bottom of page.")
            time.sleep(10)

            # Step 6: Click Crosswalk Report - opens new tab
            existing_windows = driver.window_handles

            WebDriverWait(driver, 60).until(
                EC.element_to_be_clickable((
                    By.XPATH,
                    "(//button[@value='VIEW REPORT' and @data-analytics='navigation|inline nav|VIEW REPORT|Dashboard - Individual'])[last()]"
                ))
            ).click()
            print("Clicked Crosswalk Report.")
            time.sleep(10)

            WebDriverWait(driver, 30).until(
                lambda d: len(d.window_handles) > len(existing_windows)
            )

            for handle in driver.window_handles:
                if handle not in existing_windows:
                    driver.switch_to.window(handle)
                    break

            print("Switched to Crosswalk Report tab.")
            time.sleep(10)

            # Step 7: Click Reports
            WebDriverWait(driver, 60).until(
                EC.element_to_be_clickable((By.XPATH, "//img[contains(@src,'Reports')]"))
            ).click()
            print("Clicked Reports.")
            time.sleep(10)

            # Step 8: Click View All Reports - opens new tab
            existing_windows = driver.window_handles

            WebDriverWait(driver, 60).until(
                EC.element_to_be_clickable((By.XPATH, "//a[normalize-space()='View All Reports']"))
            ).click()
            print("Clicked View All Reports.")
            time.sleep(10)

            WebDriverWait(driver, 30).until(
                lambda d: len(d.window_handles) > len(existing_windows)
            )

            for handle in driver.window_handles:
                if handle not in existing_windows:
                    driver.switch_to.window(handle)
                    break

            print("Switched to View All Reports tab.")
            time.sleep(10)

            # Step 9: Click Book of Business
            WebDriverWait(driver, 60).until(
                EC.element_to_be_clickable(
                    (By.XPATH, "//button[@title='Book of Business' and normalize-space()='Book of Business']"))
            ).click()
            print("Clicked Book of Business.")
            time.sleep(10)

            # Step 10: Open export modal from report iframe
            print("Waiting for Book of Business report page to fully load...")

            driver.switch_to.default_content()
            report_iframe = WebDriverWait(driver, 120).until(
                EC.presence_of_element_located((
                    By.XPATH,
                    "//iframe[contains(@src, '/ibuac/reports/lightning')]"
                ))
            )
            driver.switch_to.frame(report_iframe)
            print("Switched to report iframe.")

            export_btn_xpath = "//button[normalize-space()='Export' and contains(@class,'action-bar-action-ReportExportAction')]"
            formatted_report_xpath = "//span[contains(@class,'visual-picker-header') and normalize-space()='Formatted Report']/ancestor::*[contains(@class,'slds-visual-picker')][1]"
            final_export_xpath = "//span[normalize-space()='Export' and contains(@class,'buttonLabel')]/ancestor::button[1]"

            export_button = WebDriverWait(driver, 120).until(
                EC.presence_of_element_located((By.XPATH, export_btn_xpath))
            )
            driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", export_button)
            time.sleep(2)
            driver.execute_script("arguments[0].click();", export_button)
            print("Clicked first Export button.")
            time.sleep(3)

            # Step 11: Select Formatted Report
            try:
                formatted_report_tile = WebDriverWait(driver, 20).until(
                    EC.presence_of_element_located((By.XPATH, formatted_report_xpath))
                )
                driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", formatted_report_tile)
                time.sleep(2)
                driver.execute_script("arguments[0].click();", formatted_report_tile)
                print("Selected Formatted Report inside iframe.")
            except Exception:
                driver.switch_to.default_content()
                formatted_report_tile = WebDriverWait(driver, 20).until(
                    EC.presence_of_element_located((By.XPATH, formatted_report_xpath))
                )
                driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", formatted_report_tile)
                time.sleep(2)
                driver.execute_script("arguments[0].click();", formatted_report_tile)
                print("Selected Formatted Report in top-level page.")

            time.sleep(2)

            # Step 12: Click final Export
            try:
                final_export_button = WebDriverWait(driver, 20).until(
                    EC.presence_of_element_located((By.XPATH, final_export_xpath))
                )
                driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", final_export_button)
                time.sleep(2)
                driver.execute_script("arguments[0].click();", final_export_button)
                print("Clicked final Export button.")
            except Exception:
                driver.switch_to.default_content()
                final_export_button = WebDriverWait(driver, 20).until(
                    EC.presence_of_element_located((By.XPATH, final_export_xpath))
                )
                driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", final_export_button)
                time.sleep(2)
                driver.execute_script("arguments[0].click();", final_export_button)
                print("Clicked final Export button in top-level page.")

            print("Waiting for download to complete.")
            time.sleep(60)

            driver.switch_to.default_content()
            return download_folder

        except Exception as e:
            print(f"Attempt {attempt + 1} failed. Error: {e}")

            if attempt == 2:
                log_error(
                    ERROR_CODES["general_error"],
                    f"RPA process failed 3 attempts. Error: {str(e)}",
                    matrix_row["script_name"]
                )
                driver.quit()
                return None

            time.sleep(5)

    print("RPA process failed 3 attempts. Skipping carrier.")
    log_error(ERROR_CODES["general_error"], "RPA process failed 3 attempts.", matrix_row["script_name"])
    driver.quit()
    return None


def run_bob_cigna_aca(driver, matrix_row, date_info):
    print("Running BOB Cigna ACA handler...")

    download_folder = os.path.normpath(matrix_row["download_path"])
    driver.get(matrix_row["source_url"])

    # Perform login if needed
    if matrix_row["source_login"].upper() == "YES":
        try:
            # Step 1: Close Cookie Preferences pop-up
            dismiss_cookie_popup(driver, timeout=8)
            # Step 2: Enter Username & Password
            WebDriverWait(driver, 60).until(
                EC.presence_of_element_located((By.XPATH, "//*[@data-test-id='username']"))
            ).send_keys(matrix_row["source_email"])

            WebDriverWait(driver, 60).until(
                EC.presence_of_element_located((By.XPATH, "//*[@data-test-id='password']"))
            ).send_keys(matrix_row["source_password"])

            WebDriverWait(driver, 60).until(
                EC.presence_of_element_located((By.XPATH, "//*[@data-test-id='btnSubmitLoginForm']"))
            ).click()
            print("Login form submitted!")
            time.sleep(10)

            if matrix_row["otp_needed"].upper() == "YES":
                try:
                    # Step 3: Select radio button for verification method
                    # Wait for the radio button with "Email Address" label
                    email_radio_button = WebDriverWait(driver, 10).until(
                        EC.element_to_be_clickable(
                            (By.XPATH, "//span[@data-test-id='txt-email-factor']/ancestor::mat-radio-button"))
                    )

                    # Click the radio button
                    email_radio_button.click()
                    print("Selected the Email verification method.")

                    # Step 4: Click "Send Code" to receive OTP
                    WebDriverWait(driver, 60).until(
                        EC.element_to_be_clickable((By.XPATH, "//*[@data-test-id='btncontinueFGForm']"))
                    ).click()
                    print("OTP sent!")

                    otp_code = fetch_otp_code_from_file(matrix_row)

                    # Step 7: Enter OTP into the field
                    otp_input = WebDriverWait(driver, 60).until(
                        EC.presence_of_element_located((By.XPATH, "//*[@data-test-id='mfacode']"))
                    )
                    otp_input.send_keys(otp_code)
                    print("OTP entered!")

                    # Step 8: Click Submit OTP button
                    WebDriverWait(driver, 60).until(
                        EC.element_to_be_clickable((By.XPATH, "//*[@data-test-id='btn-submit-code']"))
                    ).click()
                    print("OTP submitted!")


                except FileNotFoundError:
                    log_error(ERROR_CODES["OTP_error"], "OTP File was not found or was not submitted correctly.",
                              matrix_row["script_name"])
                    print("OTP File was not found or was not submitted correctly. Exiting...")
                    driver.quit()
                    return None

        except Exception as e:
            log_error(ERROR_CODES["login_error"], "Login page timeout or login fields not found.",
                      matrix_row["script_name"])
            print("Login page not found or timeout occurred. Exiting...")
            driver.quit()
            return None

    # Navigate to download page
    try:
        # Step 9: Handle security popup by clicking "Continue"
        WebDriverWait(driver, 10).until(
            EC.element_to_be_clickable((By.XPATH, "//*[@data-test-id='btnSeModalContinue']"))
        ).click()
        print("Security notice accepted, login successful!")

        time.sleep(15)  # Allow page to load
        driver.refresh()
        time.sleep(10)

        print("Expanding 'Individual and Family' menu...")
        ifp_menu = WebDriverWait(driver, 10).until(
            EC.element_to_be_clickable(
                (By.XPATH, "//div[@data-test-id='left-nav-menu' and text()=' Individual and Family ']"))
        )
        driver.execute_script("arguments[0].click();", ifp_menu)
        time.sleep(3)

        print("Clicking 'Book of Business'...")
        bob_link = WebDriverWait(driver, 10).until(
            EC.element_to_be_clickable((By.XPATH,
                                        '//*[@class="mat-mdc-list-item-unscoped-content mdc-list-item__primary-text"]//span[text()= " Book of Business "]'))
        )
        driver.execute_script("arguments[0].click();", bob_link)
        time.sleep(30)  # Wait for page to load
    except Exception as e:
        log_error(ERROR_CODES["navigation_error"], "Navigation process failed.", matrix_row["script_name"])
        print("Navigation process failed, ending process.")
        driver.quit()
        return None

    # Click the download button
    try:
        for attempt in range(2):  # Retry up to 1 times
            try:
                print("Ensuring 'Active' is the only checked option...")
                # Uncheck all checkboxes first
                checkboxes = [
                    "policyStatusesTerminated",
                    "policyStatusesPending",
                    "latestCoverageInfoOnly",
                    "includeNeverInForce"
                ]

                for checkbox_id in checkboxes:
                    try:
                        checkbox = driver.find_element(By.ID, checkbox_id)
                        if checkbox.is_selected():
                            checkbox.click()
                            print(f"Unchecked {checkbox_id}")
                    except:
                        print(f"Checkbox {checkbox_id} not found, skipping.")

                # Ensure "Active" is checked
                active_checkbox = WebDriverWait(driver, 10).until(
                    EC.presence_of_element_located((By.ID, "policyStatusesActive"))
                )
                if not active_checkbox.is_selected():
                    active_checkbox.click()
                    print("'Active' checkbox selected.")

                # Click Apply
                apply_button = WebDriverWait(driver, 10).until(
                    EC.element_to_be_clickable((By.XPATH, "//button[contains(text(),'Apply')]"))
                )
                apply_button.click()
                print("Applied filters.")
                time.sleep(30)  # Wait for filters to apply

                print(f"Attempt {attempt + 1}/3: Clicking 'Export Filtered' button...")

                # Click 'Generate report' to open dropdown
                generate_report_button = WebDriverWait(driver, 10).until(
                    EC.element_to_be_clickable((By.XPATH, "//button[@id='generateMenuButton']"))
                )
                generate_report_button.click()
                print("Clicked 'Generate report' button.")

                # Click 'Book of Business - Filtered' from dropdown
                bob_filtered_option = WebDriverWait(driver, 10).until(
                    EC.element_to_be_clickable((By.XPATH, "//a[contains(text(),'Book of Business - Filtered')]"))
                )
                bob_filtered_option.click()
                print("Clicked on 'Book of Business - Filtered' option.")

                time.sleep(90)  # Wait 90 seconds for download

                # Check if file exists (contains "BookOfBusiness" in filename)
                downloaded_file = next(
                    (f for f in os.listdir(download_folder) if
                     matrix_row["extracted_file_prefix"] in f and f.endswith(".xlsx")), None
                )

                if downloaded_file:
                    print(f"File downloaded successfully: {downloaded_file}")
                    return download_folder
                else:
                    print("File not found. Retrying...")

                    # Refresh page & re-apply filters
                    driver.refresh()
                    time.sleep(10)

            except Exception as e:
                print(f"Error in export attempt {attempt + 1}: {e}")
    except Exception as e:
        log_error(ERROR_CODES["download_button_not_found"], "'Download' button not found.", matrix_row["script_name"])
        print("Download button not found, ending process.")
        driver.quit()
        return None

    return download_folder


def run_bob_ethos(driver, matrix_row, date_info):
    print("Running BOB Ethos handler...")

    download_folder = os.path.normpath(matrix_row["download_path"])
    driver.get(matrix_row["source_url"])

    # Perform login if needed
    if matrix_row["source_login"].upper() == "YES":
        try:
            # Step 2: Log in
            WebDriverWait(driver, 90).until(
                EC.presence_of_element_located((By.XPATH, '//input[@type="text"]'))
            ).send_keys(matrix_row["source_email"])
            WebDriverWait(driver, 90).until(
                EC.element_to_be_clickable((By.XPATH, '//button/*[contains(text(), "Continue")]'))
            ).click()
            print("Login submitted.")
            time.sleep(10)

            if matrix_row["otp_needed"].upper() == "YES":
                try:
                    otp_code = fetch_otp_code_from_file(matrix_row)

                    # Step 4: Input & Submit OTP
                    # Enter OTP
                    WebDriverWait(driver, 90).until(
                        EC.element_to_be_clickable((By.XPATH, '//input[@type="text"]'))
                    ).send_keys(otp_code)
                    print("OTP entered.")

                    # Click Submit
                    WebDriverWait(driver, 90).until(
                        EC.element_to_be_clickable((By.XPATH, '//button/*[contains(text(), "Continue")]'))
                    ).click()
                    print("OTP submitted.")
                    time.sleep(10)
                except FileNotFoundError:
                    log_error(ERROR_CODES["OTP_error"], "OTP File was not found or was not submitted correctly.",
                              matrix_row["script_name"])
                    print("OTP File was not found or was not submitted correctly. Exiting...")
                    driver.quit()
                    return None

        except Exception as e:
            log_error(ERROR_CODES["login_error"], "Login page timeout or login fields not found.",
                      matrix_row["script_name"])
            print("Login page not found or timeout occurred. Exiting...")
            driver.quit()
            return None

    # Navigate to download page
    try:
        # Step 5: Navigate to BOB Report
        # Close data input pop-up if present
        try:
            WebDriverWait(driver, 60).until(
                EC.element_to_be_clickable(
                    (By.CLASS_NAME, 'ant-modal-close'))
            ).click()
        except:
            print("Pop-up could not be closed, or is not present. Attempting to move on.")
        time.sleep(5)

        # Click Customers
        WebDriverWait(driver, 60).until(
            EC.element_to_be_clickable(
                (By.CLASS_NAME, 'svg-inline--fa.fa-customers'))
        ).click()
        print("Navigating to report.")
        time.sleep(60)
    except Exception as e:
        log_error(ERROR_CODES["navigation_error"], "Navigation process failed.", matrix_row["script_name"])
        print("Navigation process failed, ending process.")
        driver.quit()
        return None

    # Click the download button
    try:
        # Step 6: Download BOB Report
        # Hover over Export dropdown
        action = ActionChains(driver)
        element = WebDriverWait(driver, 60).until(
            EC.presence_of_element_located(
                (By.XPATH, "//button[@title='Export' and @data-tid='button-secondary-medium']"))
        )
        action.move_to_element(element).perform()
        print("Hovering over Export dropdown menu")
        time.sleep(1)

        # Click export All Customers (.csv)
        WebDriverWait(driver, 120).until(
            EC.element_to_be_clickable(
                # uuid-XXXXX-X- changes every time
                # (By.XPATH, '//*[@data-menu-id="rc-menu-uuid-31384-3-allRecordsLegacyCsv"]'))
                (By.XPATH, "//span[normalize-space()='All customers (.csv)']"))
        ).click()
        print("Downloading Report. Waiting 2 minutes for download to finish.")
        time.sleep(120)
    except Exception as e:
        log_error(ERROR_CODES["download_button_not_found"], "'Download' button not found.", matrix_row["script_name"])
        print("Download button not found, ending process.")
        driver.quit()
        return None

    return download_folder


def run_bob_kelseycare(driver, matrix_row, date_info):
    print("Running BOB KelseyCare handler...")

    download_folder = os.path.normpath(matrix_row["download_path"])
    driver.get(matrix_row["source_url"])

    # Perform login if needed
    if matrix_row["source_login"].upper() == "YES":
        try:
            # Step 2: Log in
            WebDriverWait(driver, 30).until(
                EC.presence_of_element_located((By.XPATH, "//input[@placeholder='Enter Your User ID']"))
            ).send_keys(matrix_row["source_email"])
            WebDriverWait(driver, 30).until(
                EC.presence_of_element_located((By.XPATH, "//input[@placeholder='Password']"))
            ).send_keys(matrix_row["source_password"])
            WebDriverWait(driver, 30).until(
                EC.element_to_be_clickable(
                    (By.CLASS_NAME, 'btn.logbut'))
            ).click()
            print("Login submitted.")
            time.sleep(10)

            # Step 3: Set Verification Method to Email
            # Click dropdown menu
            WebDriverWait(driver, 30).until(
                EC.element_to_be_clickable(
                    (By.ID, 'SelectedProvider'))
            ).click()
            # Set dropdown to "Email me a code"
            WebDriverWait(driver, 30).until(
                EC.element_to_be_clickable(
                    (By.XPATH, '//*[@value="Email me a code"]'))
            ).click()
            # Click submit
            WebDriverWait(driver, 30).until(
                EC.element_to_be_clickable(
                    (By.CLASS_NAME, 'btn.btn-primary'))
            ).click()
            print("Verification email sent.")
            time.sleep(10)

            if matrix_row["otp_needed"].upper() == "YES":
                try:
                    otp_code = fetch_otp_code_from_file(matrix_row)

                    # Step 5: Input & Submit OTP
                    # Enter OTP Code
                    WebDriverWait(driver, 30).until(
                        EC.presence_of_element_located((By.ID, "Code"))
                    ).send_keys(otp_code)
                    print("OTP entered.")

                    # Click submit
                    WebDriverWait(driver, 30).until(
                        EC.element_to_be_clickable(
                            (By.CLASS_NAME, 'btn.btn-default'))
                    ).click()
                    print("OTP submitted.")
                    time.sleep(10)
                except FileNotFoundError:
                    log_error(ERROR_CODES["OTP_error"], "OTP File was not found or was not submitted correctly.",
                              matrix_row["script_name"])
                    print("OTP File was not found or was not submitted correctly. Exiting...")
                    driver.quit()
                    return None

        except Exception as e:
            log_error(ERROR_CODES["login_error"], "Login page timeout or login fields not found.",
                      matrix_row["script_name"])
            print("Login page not found or timeout occurred. Exiting...")
            driver.quit()
            return None

    # Close potential pop-up
    try:
        announcement = WebDriverWait(driver, 30).until(
            EC.presence_of_element_located(
                (By.ID,
                 'displayannouncement_modal'))
        )
        if announcement is not None:
            WebDriverWait(driver, 30).until(
                EC.element_to_be_clickable(
                    (By.ID, 'btnClose'))
            ).click()
    except:
        print("No announcement found. Attempting to move on.")

    # Navigate to download page
    try:
        # Step 6: Navigate to BOB Report
        # Click Reports
        WebDriverWait(driver, 30).until(
            EC.element_to_be_clickable(
                # (By.XPATH, '//*[@onclick="window.location.href=\'/Report/Search\'"]'))
                (By.XPATH, '//span[text()="Reports"]'))
        ).click()
        # Click Book of Business Report
        WebDriverWait(driver, 30).until(
            EC.element_to_be_clickable(
                (By.PARTIAL_LINK_TEXT, 'Book of Business Report'))
        ).click()
        print("Waiting for report screen to open.")
        time.sleep(10)
    except Exception as e:
        log_error(ERROR_CODES["navigation_error"], "Navigation process failed.", matrix_row["script_name"])
        print("Navigation process failed, ending process.")
        driver.quit()
        return None

    # Apply Filters
    try:
        # Wait for detection of Report iframe
        WebDriverWait(driver, 30).until(
            EC.presence_of_element_located(
                (By.XPATH,
                 '//*[@src="/ReportViewerWebForm.aspx"]'))
        )
        # Switch to Report iframe
        driver.switch_to.frame(driver.find_element(By.XPATH, '//*[@src="/ReportViewerWebForm.aspx"]'))

        # Set Agency ID to 'Agility Insurance Services'
        # Click dropdown
        WebDriverWait(driver, 30).until(
            EC.element_to_be_clickable(
                (By.ID, 'ReportViewer1_ctl04_ctl03'))
        ).click()
        # Click Agility Insurance Services
        WebDriverWait(driver, 30).until(
            EC.element_to_be_clickable(
                (By.XPATH,
                 '//select[@name="ReportViewer1$ctl04$ctl03$ddValue"]/option[@value="1"]'))
        ).click()
        print("Set agency ID.")
        # Set Agent ID to 'ALL'
        # Click dropdown
        WebDriverWait(driver, 30).until(
            EC.element_to_be_clickable(
                (By.ID, 'ReportViewer1_ctl04_ctl05'))
        ).click()
        # Click ALL
        WebDriverWait(driver, 30).until(
            EC.element_to_be_clickable(
                (By.XPATH,
                 '//select[@name="ReportViewer1$ctl04$ctl05$ddValue"]/option[@value="1"]'))
        ).click()
        print("Set agent ID.")
        # Set Status ID to 'ALL'
        # Click dropdown
        WebDriverWait(driver, 30).until(
            EC.element_to_be_clickable(
                (By.ID, 'ReportViewer1_ctl04_ctl07'))
        ).click()
        # Click ALL
        WebDriverWait(driver, 30).until(
            EC.element_to_be_clickable(
                (By.XPATH,
                 '//select[@name="ReportViewer1$ctl04$ctl07$ddValue"]/option[@value="1"]'))
        ).click()
        print("Set status ID.")

        # Click View Report
        WebDriverWait(driver, 30).until(
            EC.element_to_be_clickable(
                (By.ID, 'ReportViewer1_ctl04_ctl00'))
        ).click()
        print("Fetching report.")
        time.sleep(10)
    except Exception as e:
        log_error(ERROR_CODES["filter_error"], "Failed to apply filters correctly.", matrix_row["script_name"])
        print("Failed to apply filters correctly, ending process.")
        driver.quit()
        return None
    # Click the download button
    try:
        # Open (tiny) Export drop down menu
        WebDriverWait(driver, 30).until(
            EC.element_to_be_clickable(
                (By.ID, 'ReportViewer1_ctl05_ctl04_ctl00_ButtonLink'))
        ).click()
        print("Opening export menu.")

        # Export as Excel
        # title = "Excel"
        WebDriverWait(driver, 30).until(
            EC.element_to_be_clickable(
                (By.LINK_TEXT, 'Excel'))
        ).click()
        print("Downloading Report. Waiting 2 minutes for download to finish.")
        time.sleep(120)
    except Exception as e:
        log_error(ERROR_CODES["download_button_not_found"], "'Download' button not found.", matrix_row["script_name"])
        print("Download button not found, ending process.")
        driver.quit()
        return None

    return download_folder


def run_bob_kelseycare_advantage(driver, matrix_row, date_info):
    print("Running BOB KelseyCare Advantage handler...")

    download_folder = os.path.normpath(matrix_row["download_path"])
    script_name = matrix_row["script_name"]

    try:
        # ──────────────────────────────────────────────
        # STEP 1: Open URL and Login
        # ──────────────────────────────────────────────
        driver.get(matrix_row["url"])
        time.sleep(5)

        if matrix_row["log_in"].upper() == "YES":
            print("Logging in to KelseyCare Advantage...")

            username_field = WebDriverWait(driver, 30).until(
                EC.element_to_be_clickable((By.ID, "login_id"))
            )
            username_field.clear()
            username_field.send_keys(matrix_row["email"])

            password_field = WebDriverWait(driver, 30).until(
                EC.element_to_be_clickable((By.ID, "password"))
            )
            password_field.clear()
            password_field.send_keys(matrix_row["password"])

            login_button = WebDriverWait(driver, 30).until(
                EC.element_to_be_clickable((By.ID, "submit"))
            )
            login_button.click()

            print("Login submitted.")
            time.sleep(15)

        # ──────────────────────────────────────────────
        # STEP 2: Navigate to Book of Business
        # ──────────────────────────────────────────────
        print("Opening Book of Business...")

        book_of_business_link = WebDriverWait(driver, 60).until(
            EC.element_to_be_clickable((
                By.XPATH,
                "//a[contains(@href, 'member_search.htm') "
                "and contains(normalize-space(.), 'Book of Business')]"
            ))
        )
        book_of_business_link.click()

        print("Book of Business opened.")
        time.sleep(10)

        # ──────────────────────────────────────────────
        # STEP 3: Run Search with Default Filters
        # ──────────────────────────────────────────────
        print("Running Book of Business search with default filters...")

        search_button = WebDriverWait(driver, 60).until(
            EC.element_to_be_clickable((
                By.XPATH,
                "//button[@id='submit' and normalize-space()='Search']"
            ))
        )
        search_button.click()

        print("Search submitted. Waiting for results...")

        WebDriverWait(driver, 60).until(
            EC.presence_of_element_located((By.ID, "portal_members"))
        )

        try:
            WebDriverWait(driver, 60).until(
                EC.invisibility_of_element_located(
                    (By.ID, "portal_members_processing")
                )
            )
        except TimeoutException:
            print("Results processing indicator did not disappear. Continuing.")

        WebDriverWait(driver, 60).until(
            EC.presence_of_all_elements_located((
                By.CSS_SELECTOR,
                "#portal_members tbody tr"
            ))
        )

        print("Book of Business results loaded.")

        # ──────────────────────────────────────────────
        # STEP 4: Set Results Display to 100 Entries
        # ──────────────────────────────────────────────
        print("Setting results display to 100 entries...")

        entries_dropdown = WebDriverWait(driver, 30).until(
            EC.presence_of_element_located((
                By.NAME,
                "portal_members_length"
            ))
        )

        Select(entries_dropdown).select_by_value("100")

        WebDriverWait(driver, 30).until(
            lambda current_driver: current_driver.find_element(
                By.NAME,
                "portal_members_length"
            ).get_attribute("value") == "100"
        )

        try:
            WebDriverWait(driver, 60).until(
                EC.invisibility_of_element_located(
                    (By.ID, "portal_members_processing")
                )
            )
        except TimeoutException:
            print("Table redraw indicator did not disappear. Continuing.")

        print("Results display set to 100 entries.")
        time.sleep(5)

        # ──────────────────────────────────────────────
        # STEP 5: Download Book of Business Report
        # ──────────────────────────────────────────────
        print("Clicking Download button...")

        download_button = WebDriverWait(driver, 60).until(
            EC.element_to_be_clickable((By.ID, "dButton"))
        )
        download_button.click()

        print("Download initiated. Waiting for file download...")
        time.sleep(120)

        log_success()
        return download_folder

    except Exception as e:
        log_error(
            ERROR_CODES["general_error"],
            f"BOB KelseyCare Advantage failed: {e}",
            script_name
        )
        print(f"BOB KelseyCare Advantage failed: {e}")
        driver.quit()
        return None


def run_bob_priorityhealth(driver, matrix_row, date_info):
    print("Running BOB Priority Health handler...")

    download_folder = os.path.normpath(matrix_row["download_path"])
    driver.get(matrix_row["source_url"])

    # Perform login if needed
    if matrix_row["source_login"].upper() == "YES":
        try:
            # Step 2: Log in
            WebDriverWait(driver, 30).until(
                EC.presence_of_element_located((By.XPATH, "//input[@placeholder='Email']"))
            ).send_keys(matrix_row["source_email"])
            WebDriverWait(driver, 30).until(
                EC.presence_of_element_located((By.XPATH, "//input[@placeholder='Password']"))
            ).send_keys(matrix_row["source_password"])
            WebDriverWait(driver, 30).until(
                EC.element_to_be_clickable(
                    (By.CLASS_NAME, 'slds-button.slds-button_neutral.sfdc_button.uiButton'))
            ).click()
            print("Login submitted.")
            time.sleep(10)

            if matrix_row["otp_needed"].upper() == "YES":
                try:
                    otp_code = fetch_otp_code_from_file(matrix_row)

                    # Step 4: Input & Submit OTP
                    # Input OTP
                    WebDriverWait(driver, 30).until(
                        EC.presence_of_element_located((By.CLASS_NAME, "input.wide.mb8.mt8.focus"))
                    ).send_keys(otp_code)
                    print("OTP entered.")
                    try:
                        # Click Submit
                        WebDriverWait(driver, 30).until(
                            EC.element_to_be_clickable(
                                (By.CLASS_NAME, 'button.primary.wide.mt8.mb16'))
                        ).click()
                        print("OTP submitted.")
                        time.sleep(20)
                    except Exception as e:
                        print(f"Likely ran into a stale element error. Attempting to continue. Error: {e}")
                except FileNotFoundError:
                    log_error(ERROR_CODES["OTP_error"], "OTP File was not found or was not submitted correctly.",
                              matrix_row["script_name"])
                    print("OTP File was not found or was not submitted correctly. Exiting...")
                    driver.quit()
                    return None

        except Exception as e:
            log_error(ERROR_CODES["login_error"], "Login page timeout or login fields not found.",
                      matrix_row["script_name"])
            print("Login page not found or timeout occurred. Exiting...")
            driver.quit()
            return None

    # Navigate to download page
    try:
        # Step 5: Navigate to BOB Report
        print("Navigating to BOB Report...")
        # Click Book of Business
        WebDriverWait(driver, 30).until(
            EC.element_to_be_clickable(
                (By.XPATH, '//*[@data-element-label="block4"]'))
        ).click()
        time.sleep(8)
        # Click MyPriority
        WebDriverWait(driver, 30).until(
            EC.element_to_be_clickable(
                (By.XPATH, '//*[@data-element-label="block4"]'))
        ).click()
        time.sleep(4)
    except Exception as e:
        log_error(ERROR_CODES["navigation_error"], "Navigation process failed.", matrix_row["script_name"])
        print("Navigation process failed, ending process.")
        driver.quit()
        return None

    # Click the download button
    try:
        # Step 6: Download BOB Report
        WebDriverWait(driver, 30).until(
            EC.element_to_be_clickable(
                (By.XPATH, '//*[@title="Export Records"]'))
        ).click()
        print("Downloading Report. Waiting 2 minutes for download to finish.")
        time.sleep(120)
    except Exception as e:
        log_error(ERROR_CODES["download_button_not_found"], "'Download' button not found.", matrix_row["script_name"])
        print("Download button not found, ending process.")
        driver.quit()
        return None

    return download_folder


def run_bob_sma(driver, matrix_row, date_info):
    print("Running BOB SMA handler...")
    download_folder = os.path.normpath(matrix_row["download_path"])

    # If this is NOT the parent process, return download_folder
    if ((matrix_row["process_name"] != matrix_row["parent_process_name"])
            or (matrix_row["carrier_id"] != matrix_row["parent_carrier_id"])
            or (matrix_row["carrier_name"] != matrix_row["parent_carrier_name"])):
        return download_folder

    # Get yesterday's date
    cst_tz = pytz.timezone("America/Chicago")
    today = datetime.now(cst_tz).date()
    # File naming
    base_name_production = matrix_row["extracted_file_prefix"]

    driver.get(matrix_row["source_url"])

    # Perform login if needed
    if matrix_row["source_login"].upper() == "YES":
        try:
            # Step 2: Input email
            email_input = WebDriverWait(driver, 30).until(EC.presence_of_element_located((By.ID, "txtTOAAEmail")))
            email_input.send_keys(matrix_row["source_email"])
            WebDriverWait(driver, 30).until(
                EC.presence_of_element_located((By.ID, "btnSubmitEmail"))
            ).click()
            print("Email submitted for verification.")

            if matrix_row["otp_needed"].upper() == "YES":
                try:
                    otp_code = fetch_otp_code_from_file(matrix_row)

                    # Step 4: Input the verification code
                    code_input = WebDriverWait(driver, 30).until(EC.presence_of_element_located((By.ID, "txtTOAACode")))
                    code_input.send_keys(otp_code)
                    driver.find_element(By.ID, "btnSubmitCode").click()
                    print("Verification code submitted.")
                    time.sleep(30)
                except FileNotFoundError:
                    log_error(ERROR_CODES["OTP_error"], "OTP File was not found or was not submitted correctly.",
                              matrix_row["script_name"])
                    print("OTP File was not found or was not submitted correctly. Exiting...")
                    driver.quit()
                    return None

        except Exception as e:
            log_error(ERROR_CODES["login_error"], "Login page timeout or login fields not found.",
                      matrix_row["script_name"])
            print("Login page not found or timeout occurred. Exiting...")
            driver.quit()
            return None

    # Navigate to download page
    try:
        # Step 5: Navigate to Production Reports
        production_reports_button = WebDriverWait(driver, 20).until(
            EC.element_to_be_clickable(
                (By.XPATH, "//*[@role='button' and contains(text(), 'AGILITY INSURANCE SERVICES LLC_SALES')]"))
        )
        production_reports_button.click()
        print("Navigated to Production Reports.")
        time.sleep(10)

        # Step 6: Sort files by "Modified" (Newest to Oldest)
        modified_column = WebDriverWait(driver, 30).until(
            EC.element_to_be_clickable((By.XPATH, "//span[contains(text(), 'Modified')]"))
        )
        modified_column.click()
        print("Sorted files by 'Modified'.")
        time.sleep(5)

        sort_newest_button = WebDriverWait(driver, 30).until(
            EC.element_to_be_clickable((By.XPATH, "//span[contains(text(), 'Newer to older')]"))
        )
        sort_newest_button.click()
        time.sleep(5)
    except Exception as e:
        log_error(ERROR_CODES["navigation_error"], "Navigation process failed.", matrix_row["script_name"])
        print("Navigation process failed, ending process.")
        driver.quit()
        return None

    # Click the download button
    try:
        # Step 7: Select and download the Production Report
        newest_file_container = WebDriverWait(driver, 30).until(
            EC.presence_of_element_located((By.XPATH, "//div[@data-grid-row='0']"))
        )
        file_name_button = newest_file_container.find_element(
            By.XPATH, ".//span[@role='button']"
        )
        file_name = file_name_button.text.strip()

        filenames = []
        for num in range(7):
            date = (datetime.now() - timedelta(days=num))
            example_date_production = date.strftime('%Y.%m.%d')
            full_name_production = f"{base_name_production}{example_date_production}.xlsx"
            filenames.append(full_name_production)
        print(f"Target files: {filenames}")
        print(f"Found file: {file_name}")

        if file_name in filenames:
            action_chains = ActionChains(driver)
            action_chains.context_click(file_name_button).perform()
            download_option = WebDriverWait(driver, 30).until(
                EC.element_to_be_clickable((By.XPATH, "//button[@data-automationid='downloadCommand']"))
            )
            download_option.click()
            print("Production report downloaded.")
            time.sleep(20)
        else:
            log_error(ERROR_CODES["target_file_not_found"],
                      f"No matching file within the last 7 days was not found on the SMA page.",
                      matrix_row["script_name"])
            print(
                f"Expected production file '{full_name_production}' not found. Found '{file_name}' instead. Exiting...")
            driver.quit()
            return None
    except Exception as e:
        log_error(ERROR_CODES["download_button_not_found"], "'Download' button not found.", matrix_row["script_name"])
        print("Download button not found, ending process.")
        driver.quit()
        return None

    return download_folder


def run_acu_sma(driver, matrix_row, date_info):
    print("Running ACU SMA handler...")
    download_folder = os.path.normpath(matrix_row["download_path"])

    # If this is NOT the parent process, return download_folder
    if ((matrix_row["process_name"] != matrix_row["parent_process_name"])
            or (matrix_row["carrier_id"] != matrix_row["parent_carrier_id"])
            or (matrix_row["carrier_name"] != matrix_row["parent_carrier_name"])):
        return download_folder

    # Get yesterday's date
    cst_tz = pytz.timezone("America/Chicago")
    today = datetime.now(cst_tz).date()
    name_of_month = str(today.month) + '. ' + month_name[today.month][0:3]
    current_year = today.year
    # File naming
    base_name_rts = matrix_row["extracted_file_prefix"]

    driver.get(matrix_row["source_url"])

    # Perform login if needed
    if matrix_row["source_login"].upper() == "YES":
        try:
            # Step 2: Input email
            email_input = WebDriverWait(driver, 30).until(EC.presence_of_element_located((By.ID, "txtTOAAEmail")))
            email_input.send_keys(matrix_row["source_email"])
            driver.find_element(By.ID, "btnSubmitEmail").click()
            print("Email submitted for verification.")

            if matrix_row["otp_needed"].upper() == "YES":
                try:
                    otp_code = fetch_otp_code_from_file(matrix_row)

                    # Step 4: Input the verification code
                    code_input = WebDriverWait(driver, 30).until(EC.presence_of_element_located((By.ID, "txtTOAACode")))
                    code_input.send_keys(otp_code)
                    driver.find_element(By.ID, "btnSubmitCode").click()
                    print("Verification code submitted.")
                    time.sleep(15)
                except FileNotFoundError:
                    log_error(ERROR_CODES["OTP_error"], "OTP File was not found or was not submitted correctly.",
                              matrix_row["script_name"])
                    print("OTP File was not found or was not submitted correctly. Exiting...")
                    driver.quit()
                    return None

        except Exception as e:
            log_error(ERROR_CODES["login_error"], "Login page timeout or login fields not found.",
                      matrix_row["script_name"])
            print("Login page not found or timeout occurred. Exiting...")
            driver.quit()
            return None

    # Navigate to download page
    try:
        # Step 9: Click "RTS Reports"
        rts_reports_button = WebDriverWait(driver, 20).until(
            EC.element_to_be_clickable(
                (By.XPATH, "//*[@role='button' and contains(text(), 'AGILITY INSURANCE SERVICES LLC_RTS')]"))
        )
        rts_reports_button.click()
        print("Navigated to 'RTS Reports'.")
        time.sleep(5)

        # Enter current month folder
        rts_reports_button = WebDriverWait(driver, 10).until(
            EC.element_to_be_clickable((By.XPATH, f"//*[@role='button' and contains(text(), '{current_year}')]"))
        )
        rts_reports_button.click()
        print(f"Navigated to {current_year}.")
        time.sleep(5)

        # Enter current month folder
        print(f"Navigating to {name_of_month}...")
        rts_reports_button = WebDriverWait(driver, 10).until(
            EC.element_to_be_clickable((By.XPATH, f"//*[@role='button' and contains(text(), '{name_of_month}')]"))
        )
        rts_reports_button.click()
        print(f"Navigated to {name_of_month}.")
        time.sleep(5)

        # Step 10: Sort files by "Modified" (Newest to Oldest)
        modified_column = WebDriverWait(driver, 10).until(
            EC.element_to_be_clickable((By.XPATH, "//span[contains(text(), 'Modified')]"))
        )
        modified_column.click()
        print("Sorted files by 'Modified'.")
        time.sleep(5)

        sort_newest_button = WebDriverWait(driver, 10).until(
            EC.element_to_be_clickable((By.XPATH, "//span[contains(text(), 'Newer to older')]"))
        )
        sort_newest_button.click()
        time.sleep(5)
    except Exception as e:
        log_error(ERROR_CODES["navigation_error"], "Navigation process failed.", matrix_row["script_name"])
        print("Navigation process failed, ending process.")
        driver.quit()
        return None

    # Click the download button
    try:
        # Step 11: Select and download the RTS Report
        newest_file_container = WebDriverWait(driver, 10).until(
            EC.presence_of_element_located((By.XPATH, "//div[@data-grid-row='0']"))
        )
        file_name_button = newest_file_container.find_element(
            By.XPATH, ".//span[@role='button']"
        )
        file_name = file_name_button.text.strip()

        filenames = []
        for num in range(7):
            date = (datetime.now() - timedelta(days=num))
            shortyear = date.strftime("%y")
            example_date_rts = f"{date.month}.{date.day}.{shortyear}"
            full_name_rts = f"{base_name_rts} {example_date_rts}.xlsx"
            filenames.append(full_name_rts)
        print(f"Target files: {filenames}")
        print(f"Found file: {file_name}")

        if file_name in filenames:
            action_chains = ActionChains(driver)
            action_chains.context_click(file_name_button).perform()
            download_option = WebDriverWait(driver, 10).until(
                EC.element_to_be_clickable((By.XPATH, "//button[@data-automationid='downloadCommand']"))
            )
            download_option.click()
            print("Production report downloaded.")
            time.sleep(20)
        else:
            log_error(ERROR_CODES["target_file_not_found"],
                      f"No matching file within the last 5 days was not found on the SMA page.",
                      matrix_row["script_name"])
            print(f"Expected production file '{full_name_rts}' not found. Found '{file_name}' instead. Exiting...")
            driver.quit()
            return None
    except Exception as e:
        log_error(ERROR_CODES["download_button_not_found"], "'Download' button not found.", matrix_row["script_name"])
        print("Download button not found, ending process.")
        driver.quit()
        return None

    return download_folder


def run_acu_cigna(driver, matrix_row, date_info):
    print("Running ACU Cigna handler...")

    download_folder = os.path.normpath(matrix_row["download_path"])
    driver.get(matrix_row["source_url"])

    # Perform login if needed
    if matrix_row["source_login"].upper() == "YES":
        try:
            # Step 1: Close Cookie Preferences pop-up
            dismiss_cookie_popup(driver, timeout=8)
            # Step 2: Enter Username & Password
            WebDriverWait(driver, 10).until(
                EC.presence_of_element_located((By.XPATH, "//*[@data-test-id='username']"))
            ).send_keys(matrix_row["source_email"])

            WebDriverWait(driver, 10).until(
                EC.presence_of_element_located((By.XPATH, "//*[@data-test-id='password']"))
            ).send_keys(matrix_row["source_password"])

            WebDriverWait(driver, 10).until(
                EC.presence_of_element_located((By.XPATH, "//*[@data-test-id='btnSubmitLoginForm']"))
            ).click()
            print("Login form submitted!")

            # Step 3: Select radio button for verification method
            # Wait for the radio button with "Email Address" label
            email_radio_button = WebDriverWait(driver, 10).until(
                EC.element_to_be_clickable(
                    (By.XPATH, "//span[@data-test-id='txt-email-factor']/ancestor::mat-radio-button"))
            )

            # Click the radio button
            email_radio_button.click()
            print("Selected the Email verification method.")

            # Step 4: Click "Send Code" to receive OTP
            WebDriverWait(driver, 10).until(
                EC.element_to_be_clickable((By.XPATH, "//*[@data-test-id='btncontinueFGForm']"))
            ).click()
            print("OTP sent!")

            if matrix_row["otp_needed"].upper() == "YES":
                try:
                    otp_code = fetch_otp_code_from_file(matrix_row)

                    # Step 7: Enter OTP into the field
                    otp_input = WebDriverWait(driver, 10).until(
                        EC.presence_of_element_located((By.XPATH, "//*[@data-test-id='mfacode']"))
                    )
                    otp_input.send_keys(otp_code)
                    print("OTP entered!")

                    # Step 8: Click Submit OTP button
                    WebDriverWait(driver, 10).until(
                        EC.element_to_be_clickable((By.XPATH, "//*[@data-test-id='btn-submit-code']"))
                    ).click()
                    print("OTP submitted!")
                except FileNotFoundError:
                    log_error(ERROR_CODES["OTP_error"], "OTP File was not found or was not submitted correctly.",
                              matrix_row["script_name"])
                    print("OTP File was not found or was not submitted correctly. Exiting...")
                    driver.quit()
                    return None

        except Exception as e:
            log_error(ERROR_CODES["login_error"], "Login page timeout or login fields not found.",
                      matrix_row["script_name"])
            print("Login page not found or timeout occurred. Exiting...")
            driver.quit()
            return None

    # Navigate to download page
    try:
        # Step 9: Handle security popup by clicking "Continue"
        WebDriverWait(driver, 10).until(
            EC.element_to_be_clickable((By.XPATH, "//*[@data-test-id='btnSeModalContinue']"))
        ).click()
        print("Security notice accepted, login successful!")

        time.sleep(15)  # Allow page to load
        driver.refresh()
        time.sleep(10)

        # Scroll down and click "IFP"
        ifp_tab = WebDriverWait(driver, 10).until(
            EC.element_to_be_clickable((By.XPATH, "//span[@data-test-id='ifp.tools.lbl']"))
        )
        time.sleep(2)
        ifp_tab.click()
        print("Clicked on 'IFP' tab.")

        # Click on "View License & Appointment Information"
        view_license_link = WebDriverWait(driver, 10).until(
            EC.element_to_be_clickable(
                (By.XPATH, "//a[@data-test-id='ifp-tools-links' and contains(text(), 'View License')]"))
        )
        view_license_link.click()
        print("Clicked on 'View License & Appointment Information'.")
        # Wait for the page to load fully
        time.sleep(60)
    except Exception as e:
        log_error(ERROR_CODES["navigation_error"], "Navigation process failed.", matrix_row["script_name"])
        print("Navigation process failed, ending process.")
        driver.quit()
        return None

    try:
        # Select "Active" from the dropdown
        dropdown = WebDriverWait(driver, 10).until(
            EC.element_to_be_clickable((By.ID, "cignaAppointmentStatus"))
        )
        dropdown.click()
        active_option = WebDriverWait(driver, 5).until(
            EC.element_to_be_clickable((By.XPATH, "//select[@id='cignaAppointmentStatus']/option[@value='Active']"))
        )
        active_option.click()
        print("Selected 'Active' in the dropdown.")
        time.sleep(10)
    except Exception as e:
        log_error(ERROR_CODES["filter_error"], "Failed to apply filters correctly.", matrix_row["script_name"])
        print("Failed to apply filters correctly, ending process.")
        driver.quit()
        return None

    # Click the download button
    try:
        for attempt in range(2):  # Retry up to 1 times
            try:
                if attempt == 0:  # First attempt: Select "Active" only
                    print("First attempt: Selecting 'Active' in dropdown.")

                    dropdown = WebDriverWait(driver, 10).until(
                        EC.element_to_be_clickable((By.ID, "cignaAppointmentStatus"))
                    )
                    dropdown.click()
                    active_option = WebDriverWait(driver, 5).until(
                        EC.element_to_be_clickable(
                            (By.XPATH, "//select[@id='cignaAppointmentStatus']/option[@value='Active']"))
                    )
                    active_option.click()
                    print("Selected 'Active' in dropdown.")
                    time.sleep(5)

                else:  # Retry attempts: Refresh page and toggle filters
                    print(f"Retry Attempt {attempt + 1}/3: Refreshing page and resetting filters.")
                    driver.refresh()
                    time.sleep(30)  # Allow page to reload

                    # Select "Active" from dropdown
                    dropdown = WebDriverWait(driver, 10).until(
                        EC.element_to_be_clickable((By.ID, "cignaAppointmentStatus"))
                    )
                    dropdown.click()
                    active_option = WebDriverWait(driver, 5).until(
                        EC.element_to_be_clickable(
                            (By.XPATH, "//select[@id='cignaAppointmentStatus']/option[@value='Active']"))
                    )
                    active_option.click()
                    print("Selected 'Active' in dropdown.")
                    time.sleep(5)

                    # Change dropdown to "Inactive"
                    dropdown.click()
                    inactive_option = WebDriverWait(driver, 5).until(
                        EC.element_to_be_clickable(
                            (By.XPATH, "//select[@id='cignaAppointmentStatus']/option[@value='Inactive']"))
                    )
                    inactive_option.click()
                    print("Selected 'Inactive' in dropdown.")
                    time.sleep(5)

                    # Change dropdown back to "Active"
                    dropdown.click()
                    active_option = WebDriverWait(driver, 5).until(
                        EC.element_to_be_clickable(
                            (By.XPATH, "//select[@id='cignaAppointmentStatus']/option[@value='Active']"))
                    )
                    active_option.click()
                    print("Re-selected 'Active' in dropdown.")
                    time.sleep(5)

                # Click "Export"
                export_button = WebDriverWait(driver, 10).until(
                    EC.element_to_be_clickable((By.XPATH, "//button[contains(text(), 'Export')]"))
                )
                export_button.click()
                print(f"Clicked 'Export' button. Waiting 300 seconds...")

                time.sleep(300)  # Wait for 5 minutes

                # Check if the file exists in the downloads folder
                downloaded_file = next(
                    (f for f in os.listdir(download_folder) if f == "AgencyView.xlsx"), None
                )

                if downloaded_file:
                    print("File downloaded successfully: AgencyView.xlsx")
                    return download_folder  # Exit function if successful
                else:
                    print("File not found. Retrying...")

            except Exception as e:
                print(f"Error in export attempt {attempt + 1}: {e}")

        print("Failed to download the file after 3 attempts.")
        log_error(ERROR_CODES["download_error"], "Failed to download 'AgencyView.xlsx' after 3 retries.",
                  matrix_row["script_name"])
        return None
    except Exception as e:
        log_error(ERROR_CODES["download_button_not_found"], "'Download' button not found.", matrix_row["script_name"])
        print("Download button not found, ending process.")
        driver.quit()
        return None


def run_bob_devoted(driver, matrix_row, date_info):
    print("Running BOB Devoted handler (SFTP)...")

    local_download_path = os.path.normpath(matrix_row["download_path"])
    sftp_hostname = matrix_row["source_url"]
    sftp_username = matrix_row["source_email"]
    sftp_password = matrix_row["source_password"]
    sftp_port = int(matrix_row["sftp_port"])
    remote_path = matrix_row["remote_path"]
    keywords = {
        "BOB": [matrix_row["extracted_file_prefix"]],  # Replace with actual BOB keyword
    }
    # Define CST timezone
    cst = pytz.timezone('America/Chicago')

    # Get yesterday's date and time in CST
    utc_yesterday = datetime.now(timezone.utc) - timedelta(days=1)
    cst_yesterday = utc_yesterday.astimezone(cst)
    cst_date = cst_yesterday.date()

    date_string = cst_yesterday.strftime("%m%d%Y")  # mmddyyyy format
    try:
        transport = paramiko.Transport((sftp_hostname, sftp_port))
        transport.connect(username=sftp_username, password=sftp_password)
        sftp = paramiko.SFTPClient.from_transport(transport)
        print("Connected to SFTP server.")

        # List files in the remote directory
        files = sftp.listdir_attr(remote_path)

        # Process BOB
        for category, category_keywords in keywords.items():
            for keyword in category_keywords:
                filtered_files = []
                for file in files:
                    # Convert SFTP file modification time to CST
                    file_mod_time_utc = datetime.fromtimestamp(file.st_mtime, tz=timezone.utc)
                    file_mod_time_cst = file_mod_time_utc.astimezone(cst)

                    # Check if the file matches the keyword and was modified today in CST
                    if keyword in file.filename and file_mod_time_cst.date() == cst_date:
                        filtered_files.append((file.filename, file_mod_time_cst))
                # If matching files are found, download the latest
                if filtered_files:
                    print("Matching files found, downloading latest.")
                    filtered_files.sort(key=lambda x: x[1], reverse=True)  # Sort by modification time
                    latest_file = filtered_files[0]  # Get the latest file
                    file_name, file_mod_time = latest_file
                    remote_file = f"{remote_path}/{file_name}"
                    print(f"Remote file: {remote_file}")

                    local_file = f"{local_download_path}/{file_name}"
                    print(f"Targeted local file path: {local_file}")

                    print(
                        f"Downloading {file_name} (Last Modified: {file_mod_time}) as {file_name} for category '{category}'...")
                    sftp.get(remote_file, local_file)
                    print(f"Downloaded {file_name} to {local_file}")
                else:
                    log_error(ERROR_CODES["target_file_not_found"], "No file was found with matching keywords.",
                              matrix_row["script_name"])
                    print("No files were found, ending process.")
                    driver.quit()
                    return None
        # Close the SFTP connection
        sftp.close()
        transport.close()
        print("SFTP connection closed.")

    except Exception as e:
        print(f"Error: {e}")
        return None

    print(f"Returning local download path: {local_download_path}")
    return local_download_path


def run_acu_devoted(driver, matrix_row, date_info):
    print("Running ACU Devoted handler (SFTP)...")

    local_download_path = os.path.normpath(matrix_row["download_path"])
    sftp_hostname = matrix_row["source_url"]
    sftp_username = matrix_row["source_email"]
    sftp_password = matrix_row["source_password"]
    sftp_port = int(matrix_row["sftp_port"])
    remote_path = matrix_row["remote_path"]
    keywords = {
        "ACU": [matrix_row["extracted_file_prefix"]],  # Replace with actual ACU keyword
    }

    # Define CST timezone
    cst = pytz.timezone('America/Chicago')

    # Get yesterday's date and time in CST
    utc_yesterday = datetime.now(timezone.utc) - timedelta(days=1)
    cst_yesterday = utc_yesterday.astimezone(cst)
    cst_date = cst_yesterday.date()

    date_string = cst_yesterday.strftime("%m%d%Y")  # mmddyyyy format

    try:
        transport = paramiko.Transport((sftp_hostname, sftp_port))
        transport.connect(username=sftp_username, password=sftp_password)
        sftp = paramiko.SFTPClient.from_transport(transport)
        print("Connected to SFTP server.")

        # List files in the remote directory
        files = sftp.listdir_attr(remote_path)

        # Process BOB
        for category, category_keywords in keywords.items():
            for keyword in category_keywords:
                filtered_files = []
                for file in files:
                    # Convert SFTP file modification time to CST
                    file_mod_time_utc = datetime.fromtimestamp(file.st_mtime, tz=timezone.utc)
                    file_mod_time_cst = file_mod_time_utc.astimezone(cst)

                    # Check if the file matches the keyword and was modified today in CST
                    if keyword in file.filename and file_mod_time_cst.date() == cst_date:
                        filtered_files.append((file.filename, file_mod_time_cst))

                # If matching files are found, download the latest
                if filtered_files:
                    print("Matching files found, downloading latest.")
                    filtered_files.sort(key=lambda x: x[1], reverse=True)  # Sort by modification time
                    latest_file = filtered_files[0]  # Get the latest file
                    file_name, file_mod_time = latest_file
                    remote_file = f"{remote_path}/{file_name}"
                    print(f"Remote file: {remote_file}")

                    local_file = f"{local_download_path}/{file_name}"
                    print(f"Targeted local file path: {local_file}")

                    print(
                        f"Downloading {file_name} (Last Modified: {file_mod_time}) as {file_name} for category '{category}'...")
                    sftp.get(remote_file, local_file)
                    print(f"Downloaded {file_name} to {local_file}")
                else:
                    log_error(ERROR_CODES["target_file_not_found"], "No file was found with matching keywords.",
                              matrix_row["script_name"])
                    print("No files were found, ending process.")
                    driver.quit()
                    return None
        # Close the SFTP connection
        sftp.close()
        transport.close()
        print("SFTP connection closed.")

    except Exception as e:
        print(f"Error: {e}")
        return None

    print(f"Returning local download path: {local_download_path}")
    return local_download_path


def run_bob_hcsc(driver, matrix_row, date_info):
    print("Running BOB HCSC handler (SFTP)...")

    local_download_path = os.path.normpath(matrix_row["download_path"])
    sftp_hostname = matrix_row["source_url"]
    sftp_username = matrix_row["source_email"]
    sftp_password = matrix_row["source_password"]
    sftp_port = int(matrix_row["sftp_port"])
    remote_path = matrix_row["remote_path"]
    keywords = {
        "BOB": [matrix_row["extracted_file_prefix"]],  # Replace with actual BOB keyword
    }

    # Define CST timezone
    cst = pytz.timezone('America/Chicago')

    # Get current date and time in CST
    # Searching for a file from the previous day!
    utc_now = datetime.now(timezone.utc) - timedelta(days=1)  # Get current UTC time minus 24 hours
    cst_now = utc_now.astimezone(cst)  # Convert to CST
    cst_date = cst_now.date()  # Get the current date in CST

    date_string = cst_now.strftime("%m%d%Y")  # mmddyyyy format

    try:
        transport = paramiko.Transport((sftp_hostname, sftp_port))
        transport.connect(username=sftp_username, password=sftp_password)
        sftp = paramiko.SFTPClient.from_transport(transport)
        print("Connected to SFTP server.")

        # List files in the remote directory
        try:
            files = sftp.listdir_attr(remote_path)
        except FileNotFoundError:
            print(f"Error: Remote path '{remote_path}' not found.")
            return None

        # Process BOB keywords
        for category, category_keywords in keywords.items():
            for keyword in category_keywords:
                filtered_files = []
                for file in files:
                    # Convert SFTP file modification time to CST
                    file_mod_time_utc = datetime.fromtimestamp(file.st_mtime, tz=timezone.utc)
                    file_mod_time_cst = file_mod_time_utc.astimezone(cst)

                    # Check if the file matches the keyword and was modified today in CST
                    if keyword.lower() in file.filename.lower() and file_mod_time_cst.date() == cst_date:
                        print(file.filename.lower())
                        filtered_files.append((file.filename, file_mod_time_cst))
                    else:
                        print(f"Mismatch: {file.filename.lower()}")

                # If matching files are found, download the latest
                if filtered_files:
                    filtered_files.sort(key=lambda x: x[1], reverse=True)  # Sort by modification time
                    latest_file = filtered_files[0]  # Get the latest file
                    file_name, file_mod_time = latest_file
                    remote_file = f"{remote_path}/{file_name}"

                    local_file = f"{local_download_path}/{file_name}"

                    print(
                        f"Downloading {file_name} (Last Modified: {file_mod_time}) as {file_name} for category '{category}'...")
                    sftp.get(remote_file, local_file)
                    print(f"Downloaded {file_name} to {local_file}")
                else:
                    log_error(ERROR_CODES["target_file_not_found"], "No file was found with matching keywords.",
                              matrix_row["script_name"])
                    print("No files were found, ending process.")
                    driver.quit()
                    return None
        # Close the SFTP connection
        sftp.close()
        transport.close()
        print("SFTP connection closed.")

    except Exception as e:
        print(f"Error: {e}")
        return None

    print(f"Returning local download path: {local_download_path}")
    return local_download_path


def run_acu_hcsc(driver, matrix_row, date_info):
    print("Running ACU HCSC handler (SFTP)...")

    local_download_path = os.path.normpath(matrix_row["download_path"])
    sftp_hostname = matrix_row["source_url"]
    sftp_username = matrix_row["source_email"]
    sftp_password = matrix_row["source_password"]
    sftp_port = int(matrix_row["sftp_port"])
    remote_path = matrix_row["remote_path"]
    keywords = {
        "ACU": [matrix_row["extracted_file_prefix"]],  # Replace with actual ACU keyword
    }
    # Define CST timezone
    cst = pytz.timezone('America/Chicago')

    # Get current date and time in CST
    # Searching for a file from the previous day!
    utc_now = datetime.now(timezone.utc) - timedelta(days=1)  # Get current UTC time minus 24 hours
    cst_now = utc_now.astimezone(cst)  # Convert to CST
    cst_date = cst_now.date()  # Get the current date in CST
    date_string = cst_now.strftime("%m%d%Y")  # mmddyyyy format

    try:
        transport = paramiko.Transport((sftp_hostname, sftp_port))
        transport.connect(username=sftp_username, password=sftp_password)
        sftp = paramiko.SFTPClient.from_transport(transport)
        print("Connected to SFTP server.")

        # List files in the remote directory
        try:
            files = sftp.listdir_attr(remote_path)
        except FileNotFoundError:
            print(f"Error: Remote path '{remote_path}' not found.")
            return None

        # Process ACU keywords
        for category, category_keywords in keywords.items():
            for keyword in category_keywords:
                filtered_files = []
                for file in files:
                    # Convert SFTP file modification time to CST
                    file_mod_time_utc = datetime.fromtimestamp(file.st_mtime, tz=timezone.utc)
                    file_mod_time_cst = file_mod_time_utc.astimezone(cst)
                    print(file.filename)  # Dump all file names into console
                    # Check if the file matches the keyword and was modified today in CST
                    if keyword.lower() in file.filename.lower() and file_mod_time_cst.date() == cst_date:
                        filtered_files.append((file.filename, file_mod_time_cst))

                # If matching files are found, download the latest
                if filtered_files:
                    filtered_files.sort(key=lambda x: x[1], reverse=True)  # Sort by modification time
                    latest_file = filtered_files[0]  # Get the latest file
                    file_name, file_mod_time = latest_file
                    remote_file = f"{remote_path}/{file_name}"

                    local_file = f"{local_download_path}/{file_name}"

                    print(
                        f"Downloading {file_name} (Last Modified: {file_mod_time}) as {file_name} for category '{category}'...")
                    sftp.get(remote_file, local_file)
                    print(f"Downloaded {file_name} to {local_file}")
                else:
                    log_error(ERROR_CODES["target_file_not_found"], "No file was found with matching keywords.",
                              matrix_row["script_name"])
                    print("No files were found, ending process.")
                    driver.quit()
                    return None
        # Close the SFTP connection
        sftp.close()
        transport.close()
        print("SFTP connection closed.")

    except Exception as e:
        print(f"Error: {e}")
        return None

    print(f"Returning local download path: {local_download_path}")
    return local_download_path


"""
def run_template(driver, matrix_row, date_info):
    print("Running BOB ________ handler...")

    download_folder = os.path.normpath(matrix_row["download_path"])
    driver.get(matrix_row["source_url"])

    # Perform login if needed
    if matrix_row["source_login"].upper() == "YES":
        try:
            print("Implement Login Functionality")
        except Exception as e:
            log_error(ERROR_CODES["login_error"], "Login page timeout or login fields not found.", matrix_row["script_name"])
            print("Login page not found or timeout occurred. Exiting...")
            driver.quit()
            return None

    # Navigate to download page
    try:
        print("Implement navigation functionality (if needed)")
    except Exception as e:
        log_error(ERROR_CODES["navigation_error"], "Navigation process failed.", matrix_row["script_name"])
        print("Navigation process failed, ending process.")
        driver.quit()
        return None

    # Click the download button
    try:
        print("Implement download functionality")
    except Exception as e:
        log_error(ERROR_CODES["download_button_not_found"], "'Download' button not found.", matrix_row["script_name"])
        print("Download button not found, ending process.")
        driver.quit()
        return None

    return download_folder
"""


def run_comm_bcbs_az(driver, matrix_row, date_info):
    print("Running COMM BCBS AZ handler...")

    download_folder = os.path.normpath(matrix_row["download_path"])
    script_name = matrix_row["script_name"]

    try:
        # STEP 1: Log in
        print("[1/6] Logging in ...")

        driver.get(matrix_row["source_url"])
        time.sleep(5)

        if matrix_row["source_login"].upper() == "YES":
            try:
                username_field = WebDriverWait(driver, 60).until(
                    EC.element_to_be_clickable((By.ID, "username_field"))
                )
                username_field.clear()
                username_field.send_keys(matrix_row["source_email"])
                print("  Entered username.")

                password_field = WebDriverWait(driver, 60).until(
                    EC.element_to_be_clickable((By.ID, "password_field"))
                )
                password_field.clear()
                password_field.send_keys(matrix_row["source_password"])
                print("  Entered password.")

                login_btn = WebDriverWait(driver, 60).until(
                    EC.element_to_be_clickable((By.CSS_SELECTOR, "button.submitButton"))
                )
                login_btn.click()
                print("  Login submitted.")
                time.sleep(10)

            except Exception as e:
                log_error(
                    ERROR_CODES["login_error"],
                    f"BCBS AZ login failed: {e}",
                    script_name
                )
                print(f"BCBS AZ login failed: {e}")
                driver.quit()
                return None

        # STEP 2: Navigate to Reporting → Broker Commission Report
        try:
            print("[2/6] Navigating to Broker Commission Report ...")

            reporting_menu = WebDriverWait(driver, 60).until(
                EC.element_to_be_clickable(
                    (By.XPATH, "//span[normalize-space()='Reporting']")
                )
            )
            reporting_menu.click()
            print("  Reporting menu opened.")
            time.sleep(3)

            broker_commission_report = WebDriverWait(driver, 60).until(
                EC.element_to_be_clickable(
                    (
                        By.CSS_SELECTOR,
                        'a[href="/en/Secure/Reporting/Broker-Commission-Report"]'
                    )
                )
            )
            broker_commission_report.click()
            print("  Broker Commission Report selected.")
            time.sleep(10)

        except Exception as e:
            log_error(
                ERROR_CODES["navigation_error"],
                f"BCBS AZ Broker Commission Report navigation failed: {e}",
                script_name
            )
            print(f"BCBS AZ Broker Commission Report navigation failed: {e}")
            driver.quit()
            return None

        # STEP 3: Select commission statement month
        try:
            print("[3/6] Selecting commission statement month ...")

            today = datetime.now()
            statement_month = today.month - 1
            statement_year = today.year

            if statement_month == 0:
                statement_month = 12
                statement_year -= 1

            month_dropdown = WebDriverWait(driver, 60).until(
                EC.presence_of_element_located((By.ID, "ReportDateMonth"))
            )
            Select(month_dropdown).select_by_value(str(statement_month))
            print(f"  Selected statement month value: {statement_month}")

            year_dropdown = WebDriverWait(driver, 60).until(
                EC.presence_of_element_located((By.ID, "Year"))
            )
            Select(year_dropdown).select_by_value(str(statement_year))
            print(f"  Selected statement year: {statement_year}")

        except Exception as e:
            log_error(
                ERROR_CODES["input_error"],
                f"BCBS AZ statement month/year selection failed: {e}",
                script_name
            )
            print(f"BCBS AZ statement month/year selection failed: {e}")
            driver.quit()
            return None

        # STEP 4: Click Update
        try:
            print("[4/6] Clicking Update ...")

            update_btn = WebDriverWait(driver, 60).until(
                EC.element_to_be_clickable((By.ID, "btnRunReport"))
            )
            update_btn.click()

            print("  Update clicked. Waiting for report to load ...")
            time.sleep(30)

        except Exception as e:
            log_error(
                ERROR_CODES["navigation_error"],
                f"BCBS AZ Update button failed: {e}",
                script_name
            )
            print(f"BCBS AZ Update button failed: {e}")
            driver.quit()
            return None

        # STEP 5: Export to Excel
        try:
            print("[5/6] Exporting to Excel ...")

            export_btn = WebDriverWait(driver, 120).until(
                EC.element_to_be_clickable((By.ID, "ExportExcel"))
            )
            export_btn.click()

            print("  Export to Excel clicked. Waiting for download ...")
            time.sleep(60)

        except Exception as e:
            log_error(
                ERROR_CODES["download_error"],
                f"BCBS AZ Excel export failed: {e}",
                script_name
            )
            print(f"BCBS AZ Excel export failed: {e}")
            driver.quit()
            return None

        # STEP 6: Check downloads
        try:
            print("[6/6] Checking downloads ...")

            if os.path.exists(download_folder):
                files = os.listdir(download_folder)

                if not files:
                    print("  No files found in download folder.")
                else:
                    for f in files:
                        fpath = os.path.join(download_folder, f)

                        if os.path.isfile(fpath):
                            print(f"    {f} ({os.path.getsize(fpath):,} bytes)")

            log_success()
            return download_folder

        except Exception as e:
            log_error(
                ERROR_CODES["download_error"],
                f"BCBS AZ download folder check failed: {e}",
                script_name
            )
            print(f"BCBS AZ download folder check failed: {e}")
            driver.quit()
            return None

    except Exception as e:
        log_error(
            ERROR_CODES["general_error"],
            f"COMM BCBS AZ handler failed: {e}",
            script_name
        )
        print(f"COMM BCBS AZ handler failed: {e}")
        driver.quit()
        return None


def run_acu_bcbs_az(driver, matrix_row, date_info):
    print("Running ACU BCBS AZ handler...")

    download_folder = os.path.normpath(matrix_row["download_path"])
    driver.get(matrix_row["source_url"])

    for attempt in range(3):
        if matrix_row["source_login"].upper() == "YES":
            try:
                # Step 1: Navigate to Login Page
                WebDriverWait(driver, 60).until(
                    EC.element_to_be_clickable(
                        (By.XPATH, "//nav[@id='navigation-footer']//button[normalize-space()='Login / Register']"))
                ).click()
                print("Clicked on 'Login / Register' button.")
                time.sleep(2)

                WebDriverWait(driver, 60).until(
                    EC.element_to_be_clickable((By.XPATH, "//a[contains(@class, 'mainnav-broker-link')]"))
                ).click()
                print("Clicked on 'Broker' link.")
                time.sleep(10)

                # Step 2: Log in
                WebDriverWait(driver, 60).until(
                    EC.element_to_be_clickable((By.XPATH, "//input[@id='username_field']"))
                ).send_keys(matrix_row["source_email"])
                print("Entered username.")
                time.sleep(2)

                WebDriverWait(driver, 60).until(
                    EC.element_to_be_clickable((By.XPATH, "//input[@id='password_field']"))
                ).send_keys(matrix_row["source_password"])
                print("Entered password.")
                time.sleep(2)

                WebDriverWait(driver, 60).until(
                    EC.element_to_be_clickable((By.XPATH, "//button[text()='Log In']"))
                ).click()
                print("Login submitted.")
                time.sleep(10)

            except Exception as e:
                log_error(ERROR_CODES["login_error"], "Login page timeout or login fields not found.",
                          matrix_row["script_name"])
                print("Login page not found or timeout occurred. Exiting...")
                driver.quit()
                return None

        try:
            WebDriverWait(driver, 120).until(
                EC.element_to_be_clickable((By.XPATH, "//span[contains(text(), 'Account')]"))
            ).click()
            print("Navigating to Accounts menu.")
            time.sleep(5)

            WebDriverWait(driver, 120).until(
                EC.element_to_be_clickable(
                    (By.XPATH, "//a[@class='menuLinks' and contains(text(), 'Office User Management')]"))
            ).click()
            print("Clicked on 'Office User Management'.")
            time.sleep(10)

            WebDriverWait(driver, 10).until(
                EC.presence_of_element_located((By.ID, "PageSizeList"))
            ).click()
            all_option = WebDriverWait(driver, 5).until(
                EC.element_to_be_clickable((By.XPATH, "//select[@id='PageSizeList']/option[@value='1000']"))
            )
            all_option.click()
            print("Selected '1000' in the dropdown.")
            time.sleep(10)

            extract_bcbs_az_agents_to_csv(driver, matrix_row)
            print("Agent info table extracted and saved to CSV.")
            return download_folder

        except Exception as e:
            log_error(ERROR_CODES["navigation_error"], "Navigation process failed.", matrix_row["script_name"])
            print("Navigation process failed, ending process.")
            driver.quit()
            return None

    print("RPA process failed 3 attempts. Skipping carrier.")
    log_error(ERROR_CODES["general_error"], "RPA process failed 3 attempts.", matrix_row["script_name"])
    return


def run_acu_prominencehealth(driver, matrix_row, date_info):
    print("Running ACU Prominence Health handler...")

    download_folder = os.path.normpath(matrix_row["download_path"])
    driver.get(matrix_row["source_url"])

    # Perform login if needed
    for attempt in range(3):  # Retry up to 3 times. (Only if code does not return a folder OR an error!)
        if matrix_row["source_login"].upper() == "YES":
            try:
                # Step 2: Log in
                WebDriverWait(driver, 60).until(
                    EC.presence_of_element_located((By.XPATH, "//input[@type='email']"))
                ).send_keys(matrix_row["source_email"])
                WebDriverWait(driver, 60).until(
                    EC.presence_of_element_located((By.XPATH, "//input[@type='password']"))
                ).send_keys(matrix_row["source_password"])
                WebDriverWait(driver, 60).until(
                    EC.element_to_be_clickable(
                        (By.XPATH, "//button[text()='Login']"))
                ).click()
                print("Login submitted.")
                time.sleep(5)
                print("Waiting for home page to appear after login...")
                time.sleep(5)
            except Exception as e:
                log_error(ERROR_CODES["login_error"], "Login page timeout or login fields not found.",
                          matrix_row["script_name"])
                print("Login page not found or timeout occurred. Exiting...")
                driver.quit()
                return None

        # Navigate to download page
        try:
            # Step 3: Click on Downline Agents Menu
            WebDriverWait(driver, 120).until(
                EC.element_to_be_clickable((By.XPATH, "//*[@id='side-menu']//div[text()='Downline Agents']"))
            ).click()
            print("Clicked on Downline Agents menu.")
            time.sleep(10)

            # Step 4: Click to download CSV file
            WebDriverWait(driver, 120).until(
                EC.element_to_be_clickable((By.XPATH, "//button[contains(text(), 'Download CSV')]"))
            ).click()
            print("Clicked on Download CSV button.")
            time.sleep(5)
            # Setp 5: Close download modal
            WebDriverWait(driver, 120).until(
                EC.element_to_be_clickable((By.XPATH, "//button[text()='Close']"))
            ).click()
            print("Closed download modal.")
            time.sleep(10)  # Wait for download to complete
        except Exception as e:
            log_error(ERROR_CODES["download_button_not_found"], "'Download' button not found.",
                      matrix_row["script_name"])
            print("Download button not found, ending process.")
            driver.quit()
            return None

        return download_folder

    print("RPA process failed 3 attempts. Skipping carrier.")
    log_error(ERROR_CODES["general_error"], "RPA process failed 3 attempts.", matrix_row["script_name"])
    return None


def run_bob_bcbs_ne(driver, matrix_row, date_info):
    print("Running BOB BCBS NE handler...")

    download_folder = os.path.normpath(matrix_row["download_path"])
    driver.get(matrix_row["source_url"])

    # Perform login if needed
    for attempt in range(3):  # Retry up to 3 times. (Only if code does not return a folder OR an error!)
        if matrix_row["source_login"].upper() == "YES":
            try:
                # Step 1: Log in
                time.sleep(2)  # Wait for page to load
                WebDriverWait(driver, 60).until(
                    EC.presence_of_element_located((By.XPATH, "//input[@name='txtUsername']"))
                ).send_keys(matrix_row["source_email"])
                print("Entered username.")
                time.sleep(2)

                WebDriverWait(driver, 60).until(
                    EC.presence_of_element_located((By.XPATH, "//input[@name='txtPassword']"))
                ).send_keys(matrix_row["source_password"])
                print("Entered password.")
                time.sleep(2)

                WebDriverWait(driver, 60).until(
                    EC.element_to_be_clickable((By.XPATH, "//input[@id='btnLogin']"))
                ).click()
                print("Login submitted.")
                time.sleep(10)

            except Exception as e:
                log_error(ERROR_CODES["login_error"], "Login page timeout or login fields not found.",
                          matrix_row["script_name"])
                print("Login page not found or timeout occurred. Exiting...")
                driver.quit()
                return None

        # Navigate to download page
        try:
            # Step 2: Select Individual Health
            element = WebDriverWait(driver, 120).until(
                EC.element_to_be_clickable(
                    (By.XPATH,
                     "//*[@class='ui-menu-item-wrapper' and text()='Individual Health']"))
            ).click()
            print("Clicking Individual Health dropdown.")
            time.sleep(2)
            # Step 3: Select My Policies
            element = WebDriverWait(driver, 120).until(
                EC.element_to_be_clickable(
                    (By.XPATH,
                     "//div[normalize-space()='Individual Health']/following-sibling::ul//a[normalize-space()='My Policies']"))
            ).click()
            print("Clicking My Policies.")
            time.sleep(10)

            try:
                filters_present = WebDriverWait(driver, 10).until(
                    EC.presence_of_element_located((By.XPATH,
                                                    "//div[contains(@class, 'filter-button') and (contains(., 'Clear Grid Filters') or contains(., 'Clear Grid Sort'))]"))
                )
                if filters_present:
                    print("Existing filters or sorting detected, proceeding to clear them.")
                    # Clear filters if present
                    try:
                        clear_filters_btn = driver.find_element(By.XPATH,
                                                                "//div[contains(@class, 'filter-button') and contains(., 'Clear Grid Filters')]")
                        clear_filters_btn.click()
                        print("Cleared existing filters.")
                        time.sleep(5)
                    except Exception:
                        print("No 'Clear Grid Filters' button found.")
                    # Clear sorting if present
                    try:
                        clear_sort_btn = driver.find_element(By.XPATH,
                                                             "//div[contains(@class, 'filter-button') and contains(., 'Clear Grid Sort')]")
                        clear_sort_btn.click()
                        print("Cleared existing sorting.")
                        time.sleep(5)
                    except Exception:
                        print("No 'Clear Grid Sort' button found.")
                else:
                    print("No filters or sorting in place, proceeding.")
            except Exception:
                print("No filters or sorting in place, proceeding.")

            # Click to filter by carrier
            WebDriverWait(driver, 120).until(
                EC.element_to_be_clickable((By.ID, "fltIpgCarrier"))
            ).click()
            print("Clicked on Carrier filter box.")
            time.sleep(2)
            WebDriverWait(driver, 120).until(
                EC.presence_of_element_located((By.XPATH, "//input[@id = //label[text()='BCBSNE']/@for]"))
            ).click()
            print("Selected BCBSNE from filter options.")
            time.sleep(10)

            # Step 4: Click to download excel file
            WebDriverWait(driver, 120).until(
                EC.element_to_be_clickable(
                    (By.XPATH, "//*[@class='agb-links']//img[@src='https://agb.ociservices.com/images/gridexcel.png']"))
            ).click()
            time.sleep(2)
            print("Clicked on Download Excel icon.")
            WebDriverWait(driver, 120).until(
                EC.element_to_be_clickable((By.XPATH, "//button[@type='button' and text()='As Displayed']"))
            ).click()
            time.sleep(2)
            print("Clicked on All Records button.")
            time.sleep(30)  # Wait for download to complete
        except Exception as e:
            log_error(ERROR_CODES["download_button_not_found"], "'Download' button not found.",
                      matrix_row["script_name"])
            print("Download button not found, ending process.")
            driver.quit()
            return None
        return download_folder
    print("RPA process failed 3 attempts. Skipping carrier.")
    log_error(ERROR_CODES["general_error"], "RPA process failed 3 attempts.", matrix_row["script_name"])
    return None


def run_acu_bcbs_ne(driver, matrix_row, date_info):
    print("Running ACU BCBS NE handler...")

    download_folder = os.path.normpath(matrix_row["download_path"])
    driver.get(matrix_row["source_url"])

    # Perform login if needed
    for attempt in range(3):  # Retry up to 3 times. (Only if code does not return a folder OR an error!)
        if matrix_row["source_login"].upper() == "YES":
            try:
                # Step 1: Log in
                time.sleep(2)  # Wait for page to load
                WebDriverWait(driver, 60).until(
                    EC.presence_of_element_located((By.XPATH, "//input[@name='txtUsername']"))
                ).send_keys(matrix_row["source_email"])
                time.sleep(2)

                WebDriverWait(driver, 60).until(
                    EC.presence_of_element_located((By.XPATH, "//input[@name='txtPassword']"))
                ).send_keys(matrix_row["source_password"])
                time.sleep(2)

                WebDriverWait(driver, 60).until(
                    EC.element_to_be_clickable(
                        (By.XPATH, "//input[@id='btnLogin']"))
                ).click()
                print("Login submitted.")
                time.sleep(10)
            except Exception as e:
                log_error(ERROR_CODES["login_error"], "Login page timeout or login fields not found.",
                          matrix_row["script_name"])
                print("Login page not found or timeout occurred. Exiting...")
                driver.quit()
                return None

        # Navigate to download page
        try:
            # Step 2: Select My Account
            element = WebDriverWait(driver, 120).until(
                EC.element_to_be_clickable(
                    (By.XPATH,
                     "//*[@class='ui-menu-item-wrapper' and text()='My Account']"))
            ).click()
            print("Clicking My Account dropdown.")
            time.sleep(2)
            # Step 3: Select Carrier Appointments
            element = WebDriverWait(driver, 120).until(
                EC.element_to_be_clickable(
                    (By.XPATH, "//a[text()='Carrier Appointments']"))
            ).click()
            print("Clicking Carrier Appointments.")
            time.sleep(10)

            # Step 4: Click on Broker Appointments tab
            WebDriverWait(driver, 120).until(
                EC.element_to_be_clickable((By.XPATH, "//a[@class='ui-tabs-anchor' and text()='Broker Appointments']"))
            ).click()
            print("Clicked on Broker Appointments tab.")
            time.sleep(10)

            # Step 5: Check if existing filters button and existing sorting is visible
            try:
                filters_present = WebDriverWait(driver, 10).until(
                    EC.presence_of_element_located((By.XPATH,
                                                    "//div[contains(@class, 'filter-button') and (contains(., 'Clear Grid Filters') or contains(., 'Clear Grid Sort'))]"))
                )
                if filters_present:
                    print("Existing filters or sorting detected, proceeding to clear them.")
                    # Clear filters if present
                    try:
                        clear_filters_btn = driver.find_element(By.XPATH,
                                                                "//div[contains(@class, 'filter-button') and contains(., 'Clear Grid Filters')]")
                        clear_filters_btn.click()
                        print("Cleared existing filters.")
                        time.sleep(5)
                    except Exception:
                        print("No 'Clear Grid Filters' button found.")
                    # Clear sorting if present
                    try:
                        clear_sort_btn = driver.find_element(By.XPATH,
                                                             "//div[contains(@class, 'filter-button') and contains(., 'Clear Grid Sort')]")
                        clear_sort_btn.click()
                        print("Cleared existing sorting.")
                        time.sleep(5)
                    except Exception:
                        print("No 'Clear Grid Sort' button found.")
                else:
                    print("No filters or sorting in place, proceeding.")
            except Exception:
                print("No filters or sorting in place, proceeding.")

            # Step 6: Type BCBSNE in Carrier filter box
            WebDriverWait(driver, 120).until(
                EC.presence_of_element_located((By.XPATH, "//input[@name='fltbrinbtcaName']"))
            ).send_keys("BCBSNE")
            print("Typed BCBSNE in Carrier filter box.")
            time.sleep(2)
            WebDriverWait(driver, 120).until(
                EC.presence_of_element_located((By.XPATH, "//input[@name='fltbrinbtcaName']"))
            ).send_keys(Keys.TAB)
            time.sleep(5)

            # Step 7: Click to download excel file
            WebDriverWait(driver, 120).until(
                EC.element_to_be_clickable((By.XPATH, "//*[@id='ttabbrintbroker-appointments']"))
            ).click()
            print("Clicked on Broker Appointments tab.")
            time.sleep(2)
            WebDriverWait(driver, 120).until(
                EC.element_to_be_clickable(
                    (By.XPATH, "//*[@class='agb-links']//img[@src='https://agb.ociservices.com/images/gridexcel.png']"))
            ).click()
            print("Clicked on Download Excel icon.")
            time.sleep(2)
            WebDriverWait(driver, 120).until(
                EC.element_to_be_clickable((By.XPATH, "//button[@type='button' and text()='As Displayed']"))
            ).click()
            print("Clicked on As Displayed button.")
            time.sleep(30)  # Wait for download to complete
        except Exception as e:
            log_error(ERROR_CODES["download_button_not_found"], "'Download' button not found.",
                      matrix_row["script_name"])
            print("Download button not found, ending process.")
            driver.quit()
            return None
        return download_folder
    print("RPA process failed 3 attempts. Skipping carrier.")
    log_error(ERROR_CODES["general_error"], "RPA process failed 3 attempts.", matrix_row["script_name"])
    return None


def run_acu_medica(driver, matrix_row, date_info):
    print("Running ACU Medica handler...")

    download_folder = os.path.normpath(matrix_row["download_path"])
    driver.get(matrix_row["source_url"])

    # Perform login if needed
    for attempt in range(3):  # Retry up to 3 times. (Only if code does not return a folder OR an error!)
        if matrix_row["source_login"].upper() == "YES":
            try:
                # Step 1: Log in
                time.sleep(2)  # Wait for page to load
                WebDriverWait(driver, 60).until(
                    EC.presence_of_element_located((By.XPATH, "//input[@name='txtUsername']"))
                ).send_keys(matrix_row["source_email"])
                time.sleep(2)

                WebDriverWait(driver, 60).until(
                    EC.presence_of_element_located((By.XPATH, "//input[@name='txtPassword']"))
                ).send_keys(matrix_row["source_password"])
                time.sleep(2)

                WebDriverWait(driver, 60).until(
                    EC.element_to_be_clickable(
                        (By.XPATH, "//input[@id='btnLogin']"))
                ).click()
                print("Login submitted.")
                time.sleep(10)
            except Exception as e:
                log_error(ERROR_CODES["login_error"], "Login page timeout or login fields not found.",
                          matrix_row["script_name"])
                print("Login page not found or timeout occurred. Exiting...")
                driver.quit()
                return None

        # Navigate to download page
        try:
            # Step 2: Select My Account
            element = WebDriverWait(driver, 120).until(
                EC.element_to_be_clickable(
                    (By.XPATH,
                     "//*[@class='ui-menu-item-wrapper' and text()='My Account']"))
            ).click()
            print("Clicking My Account dropdown.")
            time.sleep(10)
            # Step 3: Select Carrier Appointments
            element = WebDriverWait(driver, 120).until(
                EC.element_to_be_clickable(
                    (By.XPATH, "//a[text()='Carrier Appointments']"))
            ).click()
            print("Clicking Carrier Appointments.")

            # Step 4: Click on Broker Appointments tab
            WebDriverWait(driver, 120).until(
                EC.element_to_be_clickable((By.XPATH, "//a[@class='ui-tabs-anchor' and text()='Broker Appointments']"))
            ).click()
            print("Clicked on Broker Appointments tab.")
            time.sleep(10)

            # Step 5: Check and clear existing filters and sorting
            try:
                WebDriverWait(driver, 10).until(
                    EC.presence_of_element_located((By.XPATH,
                                                    "//div[contains(@class, 'filter-button') and (contains(., 'Clear Grid Filters') or contains(., 'Clear Grid Sort'))]"))
                )
                print("Existing filters or sorting detected, proceeding to clear them.")

                # Try clearing filters
                for label in ['Clear Grid Filters', 'Clear Grid Sort']:
                    try:
                        button = driver.find_element(By.XPATH,
                                                     f"//div[contains(@class, 'filter-button') and contains(., '{label}')]")
                        button.click()
                        print(f"Cleared: {label}")
                        time.sleep(5)
                    except Exception:
                        print(f"No '{label}' button found.")
            except Exception:
                print("No filters or sorting in place, proceeding.")

            # Step 6: Click to download excel file
            WebDriverWait(driver, 120).until(
                EC.element_to_be_clickable((By.XPATH, "//*[@method='!brint!bacg!ExportToExcel{brinbtca}']/img"))
            ).click()
            print("Clicked on Download Excel icon.")
            time.sleep(2)
            WebDriverWait(driver, 120).until(
                EC.element_to_be_clickable((By.XPATH, "//button[@type='button' and text()='All Records']"))
            ).click()
            print("Clicked on All Records button.")
            time.sleep(10)  # Wait for download to complete
        except Exception as e:
            log_error(ERROR_CODES["download_button_not_found"], "'Download' button not found.",
                      matrix_row["script_name"])
            print("Download button not found, ending process.")
            driver.quit()
            return None
        return download_folder
    print("RPA process failed 3 attempts. Skipping carrier.")
    log_error(ERROR_CODES["general_error"], "RPA process failed 3 attempts.", matrix_row["script_name"])
    return None


def run_bob_prominencehealth(driver, matrix_row, date_info):
    print("Running BOB Prominence Health handler...")

    download_folder = os.path.normpath(matrix_row["download_path"])
    driver.get(matrix_row["source_url"])

    # Perform login if needed
    for attempt in range(3):  # Retry up to 3 times. (Only if code does not return a folder OR an error!)
        if matrix_row["source_login"].upper() == "YES":
            try:
                # Step 2: Log in
                WebDriverWait(driver, 60).until(
                    EC.presence_of_element_located((By.XPATH, "//input[@type='email']"))
                ).send_keys(matrix_row["source_email"])
                WebDriverWait(driver, 60).until(
                    EC.presence_of_element_located((By.XPATH, "//input[@type='password']"))
                ).send_keys(matrix_row["source_password"])
                WebDriverWait(driver, 60).until(
                    EC.element_to_be_clickable(
                        (By.XPATH, "//button[text()='Login']"))
                ).click()
                print("Login submitted.")
                time.sleep(10)
            except Exception as e:
                log_error(ERROR_CODES["login_error"], "Login page timeout or login fields not found.",
                          matrix_row["script_name"])
                print("Login page not found or timeout occurred. Exiting...")
                driver.quit()
                return None

        # Navigate to download page
        try:
            # Step 3: Click on Book of Business button
            WebDriverWait(driver, 120).until(
                EC.element_to_be_clickable((By.XPATH, "//*[text()='Book Of Business']"))
            ).click()
            print("Clicked on Book of Business button.")
            time.sleep(10)

            # Step 4: Click to download file
            WebDriverWait(driver, 120).until(
                EC.element_to_be_clickable((By.XPATH, "//button[contains(text(), 'Download')]"))
            ).click()
            print("Clicked on Download button.")
            time.sleep(60)  # Wait for download to complete
        except Exception as e:
            log_error(ERROR_CODES["download_button_not_found"], "'Download' button not found.",
                      matrix_row["script_name"])
            print("Download button not found, ending process.")
            driver.quit()
            return None

        return download_folder

    print("RPA process failed 3 attempts. Skipping carrier.")
    log_error(ERROR_CODES["general_error"], "RPA process failed 3 attempts.", matrix_row["script_name"])
    return None


def run_bob_solis(driver, matrix_row, date_info):
    print("Running BOB Solis handler...")

    download_folder = os.path.normpath(matrix_row["download_path"])
    driver.get(matrix_row["source_url"])

    # Retry wrapper
    for attempt in range(3):
        print(f"Attempt {attempt + 1} of 3")

        try:
            # ---------------------------------------------
            # LOGIN (Step 1)
            # ---------------------------------------------
            if matrix_row["source_login"].upper() == "YES":

                WebDriverWait(driver, 30).until(
                    EC.presence_of_element_located((By.ID, "Email"))
                ).send_keys(matrix_row["source_email"])

                driver.find_element(By.ID, "Password").send_keys(matrix_row["source_password"])
                driver.find_element(By.XPATH, "//*[@value='Login']").click()
                print("Login submitted.")
                time.sleep(4)

                # Handle "Already Logged In" popup
                try:
                    continue_btn = WebDriverWait(driver, 8).until(
                        EC.element_to_be_clickable((
                            By.XPATH, "//input[@type='submit' and @value='Continue']"
                        ))
                    )
                    continue_btn.click()
                    print("Override continued.")
                    time.sleep(3)
                except:
                    pass

            # ---------------------------------------------
            # STEP 2: Click Book of Business
            # ---------------------------------------------
            bob_menu = WebDriverWait(driver, 60).until(
                EC.element_to_be_clickable((By.XPATH,
                                            "//a[contains(@onclick,'BOBApplication')]"))
            )
            bob_menu.click()
            print("Navigated to Book of Business.")
            time.sleep(4)

            # ---------------------------------------------
            # STEP 3: Wait for search instructions
            # ---------------------------------------------
            WebDriverWait(driver, 60).until(
                EC.presence_of_element_located((By.CLASS_NAME, "selectBoxContainer"))
            )

            # ---------------------------------------------
            # STEP 4: Click Search button
            # ---------------------------------------------
            WebDriverWait(driver, 60).until(
                EC.element_to_be_clickable((By.ID, "btnSearchBOB"))
            ).click()

            print("Search button clicked.")
            time.sleep(5)

            # ---------------------------------------------
            # STEP 5: Set page length to 100 (KENDO)
            # ---------------------------------------------
            print("Setting rows per page to 100...")

            dropdown = WebDriverWait(driver, 30).until(
                EC.element_to_be_clickable((
                    By.XPATH,
                    "//span[contains(@class,'k-dropdown-wrap')]"
                ))
            )
            dropdown.click()

            option_100 = WebDriverWait(driver, 30).until(
                EC.element_to_be_clickable((
                    By.XPATH,
                    "//li[contains(@class,'k-item') and text()='100']"
                ))
            )
            option_100.click()

            # Wait for Kendo grid rows
            WebDriverWait(driver, 60).until(
                lambda d: len(
                    d.find_elements(By.XPATH, "//*[@id='BookOfBusinessGrid']//tbody/tr")
                ) > 0
            )

            print("Grid loaded with 100 rows.")

            # ---------------------------------------------
            # STEP 6: Scrape Rows Page-by-Page (Kendo)
            # ---------------------------------------------
            all_rows = []

            def extract_page():
                rows = driver.find_elements(
                    By.XPATH,
                    "//*[@id='BookOfBusinessGrid']//tbody/tr"
                )
                output = []
                for r in rows:
                    cols = r.find_elements(By.TAG_NAME, "td")
                    output.append([c.text.strip() for c in cols])
                return output

            while True:
                print("Scraping page...")
                all_rows.extend(extract_page())

                try:
                    next_btn = WebDriverWait(driver, 5).until(
                        EC.element_to_be_clickable((
                            By.XPATH,
                            "//*[@id='BookOfBusinessGrid']//a[@title='Go to the next page']"
                        ))
                    )

                    if "k-state-disabled" in next_btn.get_attribute("class"):
                        break

                    next_btn.click()
                    time.sleep(5)

                except:
                    break

            print(f"Total rows scraped: {len(all_rows)}")

            # ---------------------------------------------
            # STEP 7: Build Output Filename
            # ---------------------------------------------
            prefix = matrix_row.get("extracted_file_prefix", "output").strip()
            ext = matrix_row.get("extracted_file_extension", "csv").strip()
            if not ext.startswith("."):
                ext = "." + ext

            output_path = os.path.join(download_folder, prefix + ext)

            # ---------------------------------------------
            # STEP 8: Write CSV
            # ---------------------------------------------
            header = [
                "Member ID", "Member Name", "Enrollment Date",
                "Disenrollment Date", "Benefit Plan", "Agent", "Agency"
            ]

            with open(output_path, "w", newline="", encoding="utf-8") as f:
                w = csv.writer(f)
                w.writerow(header)

                for r in all_rows:
                    if len(r) >= 7:
                        w.writerow([r[0], r[1], r[2], r[3], r[4], r[5], r[6]])

            print(f"✔ File created: {output_path}")
            return download_folder

        except Exception as e:
            print(f"Attempt {attempt + 1} failed: {e}")
            time.sleep(5)

    # ---------------------------------------------
    # FAILED AFTER ALL ATTEMPTS
    # ---------------------------------------------
    print("RPA process failed 3 attempts. Skipping carrier.")
    log_error(ERROR_CODES["general_error"], "RPA process failed 3 attempts.", matrix_row["script_name"])
    return None


def run_bob_americanamicable(driver, matrix_row, date_info):
    print("Running BOB AmericanAmicable handler...")

    download_folder = os.path.normpath(matrix_row["download_path"])
    driver.get(matrix_row["source_url"])

    # Perform login if needed
    for attempt in range(3):  # Retry up to 3 times. (Only if code does not return a folder OR an error!)
        if matrix_row["source_login"].upper() == "YES":
            try:
                # Step 2: Enter username
                WebDriverWait(driver, 30).until(
                    EC.presence_of_element_located((By.ID, "user"))
                ).send_keys(matrix_row["source_email"])
                time.sleep(2)

                # Step 3: Enter password
                WebDriverWait(driver, 30).until(
                    EC.presence_of_element_located((By.ID, "password"))
                ).send_keys(matrix_row["source_password"])
                time.sleep(2)

                # Step 4: Click Submit
                driver.find_element(By.XPATH, "//input[@value='Submit']").click()
                print("Logged in successfully!")
                time.sleep(10)

            except Exception as e:
                log_error(ERROR_CODES["login_error"], "Login page timeout or login fields not found.",
                          matrix_row["script_name"])
                print("Login page not found or timeout occurred. Exiting...")
                driver.quit()
                return None

        # Navigate to download page
        try:
            # Step 5: Navigate to the Marketing area
            WebDriverWait(driver, 30).until(
                EC.element_to_be_clickable((By.XPATH, "/html/body/p[4]/a[1]/img"))
            ).click()
            print("Navigated to Marketing area.")
            time.sleep(5)

            print("Switching to the marketingcontents frame...")
            frame_locator = (By.XPATH, "//frame[@src='marketingcontents.php']")
            WebDriverWait(driver, 10).until(
                EC.frame_to_be_available_and_switch_to_it(frame_locator)
            )

            # Step 6: Navigate to Agent E-file
            WebDriverWait(driver, 30).until(
                EC.element_to_be_clickable((By.XPATH, "//a[@class='btn' and text()='Agent EFile']"))
            ).click()
            print("Navigated to Agent E-file.")
            time.sleep(5)

            cookies = driver.get_cookies()
            print("Cookies after login:", cookies)

            driver.switch_to.default_content()
            print("Switched back to the default content.")

        except Exception as e:
            log_error(ERROR_CODES["navigation_error"], "Navigation process failed.", matrix_row["script_name"])
            print("Navigation process failed, ending process.")
            driver.quit()
            return None

        # Click the download button
        try:
            # Step 7: Click the download button and retrieve the link directly
            print("Locating the 'View All CSV' button...")
            # Wait until the button is clickable
            download_button = WebDriverWait(driver, 10).until(
                EC.element_to_be_clickable(
                    (By.XPATH, "//input[@type='submit' and @value='View All CSV']")
                )
            )

            download_button.click()
            print("Clicked 'View All CSV'. The download should now be in progress.")
            time.sleep(10)

        except Exception as e:
            log_error(ERROR_CODES["download_error"], "Download process failed.", matrix_row["script_name"])
            print("Download process failed, ending process.")
            driver.quit()
            return None

        return download_folder
    print("RPA process failed 3 attempts. Skipping carrier.")
    log_error(ERROR_CODES["general_error"], "RPA process failed 3 attempts.", matrix_row["script_name"])
    return None


def run_acu_heartland(driver, matrix_row, date_info):
    print("Running ACU Heartland handler...")

    download_folder = os.path.normpath(matrix_row["download_path"])
    driver.get(matrix_row["source_url"])

    # Perform login if needed
    for attempt in range(3):  # Retry up to 3 times. (Only if code does not return a folder OR an error!)
        if matrix_row["source_login"].upper() == "YES":
            try:
                WebDriverWait(driver, 60).until(
                    EC.presence_of_element_located((By.ID, "Input_Username"))
                ).send_keys(matrix_row["source_email"])
                WebDriverWait(driver, 60).until(
                    EC.presence_of_element_located((By.ID, "Input_Password"))
                ).send_keys(matrix_row["source_password"])
                WebDriverWait(driver, 60).until(
                    EC.element_to_be_clickable((By.XPATH, "//button[text()='Log in']"))
                ).click()
                print("Login submitted.")
                time.sleep(10)
            except Exception as e:
                log_error(ERROR_CODES["login_error"], "Login page timeout or login fields not found.",
                          matrix_row["script_name"])
                print("Login page not found or timeout occurred. Exiting...")
                driver.quit()
                return None

        # Navigate to listings page
        try:
            original_window = driver.current_window_handle
            WebDriverWait(driver, 120).until(
                EC.element_to_be_clickable((By.XPATH, "//*[@id='contracting']"))
            ).click()
            print("Clicked on Contracting menu.")
            time.sleep(10)

            WebDriverWait(driver, 10).until(EC.number_of_windows_to_be(2))

            for handle in driver.window_handles:
                if handle != original_window:
                    driver.switch_to.window(handle)
                    break
            print("Switched to Dashboard tab.")
            time.sleep(10)
        except TimeoutException:
            print("Failed to switch domain.")
            return None

        # Step 3: Navigate to listings page
        try:
            WebDriverWait(driver, 120).until(
                EC.element_to_be_clickable((By.ID, "ucNavigation_lvLogin_lnkDashboard"))
            ).click()
            print("Clicked on Dashboard link.")
            time.sleep(10)
            WebDriverWait(driver, 120).until(
                EC.element_to_be_clickable((By.ID, "MainContent_btnApproved"))
            ).click()
            print("Clicked on Contracts Approved tab.")
            time.sleep(10)

            # Step 4: Extract agent info table and save to CSV
            html_content = driver.page_source
            print("extract_agent_info_table_to_csv:", extract_agent_info_table_to_csv)
            extract_agent_info_table_to_csv(html_content, matrix_row)
            print("Agent info table extracted and saved to CSV.")
        except Exception as e:
            print(f"Error extracting agent info table: {e}")

        return download_folder

    print("RPA process failed 3 attempts. Skipping carrier.")
    log_error(ERROR_CODES["general_error"], "RPA process failed 3 attempts.", matrix_row["script_name"])
    return None


def run_bob_physiciansmutual(driver, matrix_row, date_info):
    print("Running BOB Physicians Mutual handler...")

    download_folder = os.path.normpath(matrix_row["download_path"])
    driver.get(matrix_row["source_url"])

    # Perform login if needed
    for attempt in range(3):
        if matrix_row["source_login"].upper() == "YES":
            try:
                print("Starting login process...")
                # Step 2: Navigate to Portal page
                WebDriverWait(driver, 30).until(
                    EC.element_to_be_clickable((By.XPATH, "//div[@class='log-in-button-text']"))
                ).click()
                time.sleep(2)

                WebDriverWait(driver, 30).until(
                    EC.element_to_be_clickable((By.XPATH, "//a[@data-automation-id='header-log-in-desktop-eselfcare']"))
                ).click()
                print("Navigated to Portal page.")
                time.sleep(2)

                # Step 3: Click on "Log in to your account"
                WebDriverWait(driver, 30).until(
                    EC.element_to_be_clickable((By.ID, "loginButton"))
                ).click()
                print("Clicked on 'Log in to your account'.")
                time.sleep(5)

                # Step 4: Enter username and password
                WebDriverWait(driver, 30).until(
                    EC.presence_of_element_located((By.ID, "username"))
                ).send_keys(matrix_row["source_email"])
                print("Entered username.")
                time.sleep(2)

                WebDriverWait(driver, 30).until(
                    EC.presence_of_element_located((By.ID, "password"))
                ).send_keys(matrix_row["source_password"])
                print("Entered password.")
                time.sleep(2)

                WebDriverWait(driver, 30).until(
                    EC.element_to_be_clickable((By.ID, "kc-login"))
                ).click()
                print("Logged in successfully!")
                time.sleep(2)

            except TimeoutException as e:
                log_error(ERROR_CODES["login_error"], f"Login failed. Timed out waiting for an element: {e}",
                          matrix_row["script_name"])
                print("Login page not found or an element was not clickable. Exiting...")
                driver.quit()
                return None

        # --- Navigate to Dashboard and Download ---
        try:
            # Step 5: Navigate to Sales Performance Dashboard area
            print("Navigating to Sales Performance Dashboard...")
            WebDriverWait(driver, 45).until(  # Increased wait time for page load after login
                EC.element_to_be_clickable(
                    (By.XPATH, "//a[@data-automation-id='agent-portal-sales-performance-dashboard']"))
            ).click()
            print("Navigated to Sales Performance Dashboard area.")
            time.sleep(10)

            # Switch to the iframe embedded within the Shadow DOM
            print("Waiting for the Tableau visualization host...")
            tableau_viz_host = WebDriverWait(driver, 30).until(
                EC.presence_of_element_located((By.TAG_NAME, "tableau-viz"))
            )

            shadow_root = tableau_viz_host.shadow_root
            inner_iframe = shadow_root.find_element(By.CSS_SELECTOR, "iframe")
            driver.switch_to.frame(inner_iframe)
            print("Switched to the inner Tableau iframe successfully!")

            # Step 6: Click on Book of Business link
            WebDriverWait(driver, 30).until(
                EC.element_to_be_clickable(
                    (By.XPATH, "//div[@data-testid='tab-button-zone-text' and text()='Book of Bus.']"))
            ).click()
            print("Clicked on Book of Business link.")
            time.sleep(30)

            # Step 7: Click to select Crosstab (CSV) file
            # Wait for the download button to be clickable instead of a long sleep
            print("Waiting for download button...")
            WebDriverWait(driver, 30).until(
                EC.element_to_be_clickable(
                    (By.XPATH, "//button[@data-tb-test-id='viz-viewer-toolbar-button-download']"))
            ).click()
            print("Clicked on Dropdown button.")
            time.sleep(2)

            WebDriverWait(driver, 30).until(
                EC.element_to_be_clickable(
                    (By.XPATH, "//div[@data-tb-test-id='download-flyout-download-crosstab-MenuItem']"))
            ).click()
            print("Clicked on Crosstab option")
            time.sleep(10)

            WebDriverWait(driver, 30).until(
                EC.element_to_be_clickable(
                    (By.XPATH, "//label[@data-tb-test-id='crosstab-options-dialog-radio-csv-Label']"))
            ).click()
            print("Selected CSV option.")
            time.sleep(2)

            WebDriverWait(driver, 30).until(
                EC.element_to_be_clickable((By.XPATH, "//button[@data-tb-test-id='export-crosstab-export-Button']"))
            ).click()
            print("Clicked on Export button to download file.")
            print("Waiting for download to complete...")
            time.sleep(30)

        except TimeoutException as e:
            print(f"Error: Timed out waiting for an element during the download process.")
            print(f"Failed on element: {e}")

            with open("debug_page_source.html", "w", encoding="utf-8") as f:
                f.write(driver.page_source)
            print("The current page source has been saved to 'debug_page_source.html' for inspection.")

            log_error(ERROR_CODES["download_button_not_found"],
                      "A button or element in the download process was not found.", matrix_row["script_name"])
            driver.quit()
            return None

        return download_folder
    print("RPA process failed 3 attempts. Skipping carrier.")
    log_error(ERROR_CODES["general_error"], "RPA process failed 3 attempts.", matrix_row["script_name"])
    return None


def run_bob_nationallife(driver, matrix_row, date_info):
    print("Running BOB National Life handler...")

    download_folder = os.path.normpath(matrix_row["download_path"])
    driver.get(matrix_row["source_url"])

    # Perform login if needed
    for attempt in range(3):
        if matrix_row["source_login"].upper() == "YES":
            try:
                # Step 1: Navigate to login page
                WebDriverWait(driver, 30).until(
                    EC.element_to_be_clickable((By.ID, "LoginnavHome"))
                ).click()
                WebDriverWait(driver, 30).until(
                    EC.element_to_be_clickable((By.XPATH, "//a[@href='/agent/' and text()='Agents']"))
                ).click()
                print("Navigating to login page.")
                time.sleep(10)
                # Step 2: Enter username
                WebDriverWait(driver, 30).until(
                    EC.presence_of_element_located((By.ID, "email"))
                ).send_keys(matrix_row["source_email"])

                # Step 3: Enter password
                driver.find_element(By.ID, "password").send_keys(matrix_row["source_password"])

                # Step 4: Click Submit
                driver.find_element(By.ID, "btn-login").click()
                print("Logged in successfully!")
                time.sleep(10)

                # Step 5: Select "Send Code" to email
                try:
                    WebDriverWait(driver, 60).until(
                        EC.element_to_be_clickable((By.XPATH, "//*[@for='ap-sfa-radio-1']"))
                    ).click()
                    print("OTP to email selected!")

                    WebDriverWait(driver, 60).until(
                        EC.element_to_be_clickable((By.ID, "sfaentercodetxt"))
                    ).click()
                    print("OTP sent!")

                    otp_code = fetch_otp_code_from_file(matrix_row)

                    # Step 6: Enter OTP into the field
                    otp_input = WebDriverWait(driver, 60).until(
                        EC.presence_of_element_located((By.ID, "code"))
                    )
                    otp_input.send_keys(otp_code)
                    print("OTP entered!")

                    # Step 7: Click Submit OTP button
                    WebDriverWait(driver, 60).until(
                        EC.element_to_be_clickable((By.ID, "entercodetxt"))
                    ).click()
                    print("OTP submitted!")
                    time.sleep(10)

                except FileNotFoundError:
                    log_error(ERROR_CODES["OTP_error"], "OTP File was not found or was not submitted correctly.",
                              matrix_row["script_name"])
                    print("OTP File was not found or was not submitted correctly. Exiting...")
                    driver.quit()
                    return None
            except Exception as e:
                log_error(ERROR_CODES["login_error"], "Login page timeout or login fields not found.",
                          matrix_row["script_name"])
                print("Login page not found or timeout occurred. Exiting...")
                driver.quit()
                return None

            # Step 8: Navigate to download page
            try:
                """
                if WebDriverWait(driver, 20).until(
                    EC.presence_of_element_located((By.XPATH, "//button[@class='close triggerUserActivity']/span"))
                ):
                    print("Modal detected, proceeding to close it.")
                    WebDriverWait(driver, 120).until(
                    EC.element_to_be_clickable((By.XPATH, "//button[@class='close triggerUserActivity']/span"))
                    ).click()
                    print("Clicked to close modal")
                    time.sleep(10)
                else:
                    print("No modal detected, proceeding.")
                    time.sleep(5)
                    """

                WebDriverWait(driver, 120).until(
                    EC.element_to_be_clickable((By.XPATH, "//p[normalize-space()='Inforce']/following-sibling::a"))
                ).click()
                print("Clicked on Inforce number")
                time.sleep(10)

                WebDriverWait(driver, 120).until(
                    EC.element_to_be_clickable((By.XPATH, "//a[@data-target='#download_modal']"))
                ).click()
                print("Clicked on download button.")
                time.sleep(5)

                WebDriverWait(driver, 120).until(
                    EC.element_to_be_clickable((By.XPATH, "//label[@for='include_contacts']"))
                ).click()
                print("Checked 'Include Contact Information' box.")
                time.sleep(5)

                # Step 9: Click on download button
                WebDriverWait(driver, 120).until(
                    EC.element_to_be_clickable((By.ID, "downloadReportContacts"))
                ).click()
                print("Clicked on Download button to download file.")
                time.sleep(60)  # Wait for download to complete
            except Exception as e:
                log_error(ERROR_CODES["download_button_not_found"], "'Download' button not found.",
                          matrix_row["script_name"])
                print("Download button not found, ending process.")
                driver.quit()
                return None
        return download_folder
    print("RPA process failed 3 attempts. Skipping carrier.")
    log_error(ERROR_CODES["general_error"], "RPA process failed 3 attempts.", matrix_row["script_name"])
    return None


def run_bob_ameritas_dentalandvision(driver, matrix_row, date_info):
    print("Running BOB Ameritas Dental and Vision handler...")

    download_folder = os.path.normpath(matrix_row["download_path"])
    driver.get(matrix_row["source_url"])

    # Perform login if needed
    for attempt in range(3):
        if matrix_row["source_login"].upper() == "YES":
            try:
                # Step 1: Navigate to login page
                time.sleep(5)
                WebDriverWait(driver, 120).until(
                    EC.element_to_be_clickable((By.XPATH, "//button[text()='Sign in with your email address']"))
                ).click()
                print("Navigating to login page.")
                time.sleep(20)

                # Step 2: Enter username
                WebDriverWait(driver, 30).until(
                    EC.presence_of_element_located((By.XPATH, "//input[@id='input28']"))
                ).send_keys(matrix_row["source_email"])
                print("Entered email address.")

                # Step 3: Click Next
                WebDriverWait(driver, 30).until(
                    EC.element_to_be_clickable((By.XPATH, "//input[@value='Next']"))
                ).click()
                print("Clicked Next after entering email.")

                # Step 4: Enter password
                WebDriverWait(driver, 30).until(
                    EC.presence_of_element_located((By.XPATH, "//input[@name='credentials.passcode']"))
                ).send_keys(matrix_row["source_password"])
                print("Entered password.")

                # Step 5: Click Submit
                WebDriverWait(driver, 30).until(
                    EC.presence_of_element_located((By.XPATH, "//input[@value='Verify']"))
                ).click()
                print("Login submitted.")
                time.sleep(10)

                # Step 6: Click "Send Code" to receive OTP
                try:
                    WebDriverWait(driver, 60).until(
                        EC.element_to_be_clickable((By.XPATH, "//input[@value='Send Email']"))
                    ).click()
                    print("OTP sent!")

                    otp_code = fetch_otp_code_from_file(matrix_row)

                    # Step 7: Enter OTP into the field
                    otp_input = WebDriverWait(driver, 60).until(
                        EC.presence_of_element_located((By.XPATH, "//*[@id='input108']"))
                    )
                    otp_input.send_keys(otp_code)
                    print("OTP entered!")

                    # Step 8: Click Submit OTP button
                    WebDriverWait(driver, 60).until(
                        EC.element_to_be_clickable((By.XPATH, "//input[@value='Verify']"))
                    ).click()
                    print("OTP submitted!")
                except FileNotFoundError:
                    log_error(ERROR_CODES["OTP_error"], "OTP File was not found or was not submitted correctly.",
                              matrix_row["script_name"])
                    print("OTP File was not found or was not submitted correctly. Exiting...")
                    driver.quit()
                    return None

            except Exception as e:
                log_error(ERROR_CODES["login_error"], f"Login page timeout or login fields not found: {e}",
                          matrix_row["script_name"])
                print("Login page not found or timeout occurred. Exiting...")
                driver.quit()
                return None

            # Step 9: Navigate to download page
            try:
                WebDriverWait(driver, 120).until(
                    EC.element_to_be_clickable((By.XPATH, "//a[normalize-space()='Compensation']"))
                ).click()
                print("Clicked on Compensation.")
                time.sleep(10)

                WebDriverWait(driver, 120).until(
                    EC.element_to_be_clickable((By.XPATH, "//button[@data-test='Reports']"))
                ).click()
                print("Clicked on Reports")
                time.sleep(10)

                # Step 10: Click to download CSV file
                WebDriverWait(driver, 120).until(
                    EC.element_to_be_clickable((By.XPATH, "(//button[normalize-space()='XLS'])[1]"))
                ).click()
                print("Clicked on XLS to download file.")
                time.sleep(30)  # Wait for download to complete

                # Step 11: Rename downloaded file to remove leading date
                try:
                    print(f"Scanning for downloaded file in: {download_folder}")
                    # Find the latest file in the download folder
                    files = [os.path.join(download_folder, f)
                             for f in os.listdir(download_folder)
                             if os.path.isfile(os.path.join(download_folder, f))]

                    if not files:
                        print("Error: Download folder is empty. No file to rename.")
                        log_error(ERROR_CODES["download_error"], "Download folder empty after wait.",
                                  matrix_row["script_name"])
                        driver.quit()
                        return None

                    latest_file_path = max(files, key=os.path.getctime)
                    file_name = os.path.basename(latest_file_path)

                    print(f"Found latest file: {file_name}")

                    # Regex to match 8 digits (YYYYMMDD) at the start and capture the rest of the name.
                    match = re.match(r'^(\d{8})(.*)', file_name)

                    if match:
                        # The new name is the part *after* the date
                        new_file_name = match.group(2)
                        new_file_path = os.path.join(download_folder, new_file_name)

                        # Rename the file
                        os.rename(latest_file_path, new_file_path)
                        print(f"Successfully renamed '{file_name}' to '{new_file_name}'")
                    else:
                        print(f"File '{file_name}' does not match 'YYYYMMDD...' format. No rename performed.")

                except Exception as rename_error:
                    print(f"An error occurred during file renaming: {rename_error}")
                    log_error(ERROR_CODES["general_error"], f"File rename failed: {rename_error}",
                              matrix_row["script_name"])
                    driver.quit()
                    return None

            except Exception as e:
                log_error(ERROR_CODES["download_button_not_found"], f"'Download' button not found. Error: {e}",
                          matrix_row["script_name"])
                print("Download button not found, ending process.")
                driver.quit()
                return None

            return download_folder
    print("RPA process failed 3 attempts. Skipping carrier.")
    log_error(ERROR_CODES["general_error"], "RPA process failed 3 attempts.", matrix_row["script_name"])
    return None


def run_acu_ameritaslife(driver, matrix_row, date_info):
    print("Running ACU Ameritas Life handler...")

    download_folder = os.path.normpath(matrix_row["download_path"])
    REPORT_URL = "https://service.ameritas.com/FieldReports/retrieveReports.do?reportId=AM14&reportKey=0"
    today_str = datetime.now().strftime("%m%d%Y")
    PDF_TARGET = os.path.join(download_folder, f"acu_ameritaslife_{today_str}.pdf")
    CSV_OUTPUT = os.path.join(download_folder, f"acu_ameritaslife_{today_str}.csv")

    # Unique Chrome config for this handler
    options = Options()
    prefs = {
        "download.default_directory": download_folder,
        "plugins.always_open_pdf_externally": True,
        "download.prompt_for_download": False,
    }
    options.add_experimental_option("prefs", prefs)
    local_driver = webdriver.Chrome(service=Service(), options=options)

    for attempt in range(3):
        if matrix_row["source_login"].upper() == "YES":
            try:
                local_driver.get(matrix_row["source_url"])
                WebDriverWait(local_driver, 30).until(
                    EC.presence_of_element_located((By.ID, "ontUser"))
                ).send_keys(matrix_row["source_email"])
                local_driver.find_element(By.ID, "ontPassword").send_keys(matrix_row["source_password"])
                local_driver.find_element(By.XPATH, "//*[@id='Submit']").click()
                print("Login submitted.")
                time.sleep(10)
                try:
                    otp_code = fetch_otp_code_from_file(matrix_row)
                    otp_input = WebDriverWait(local_driver, 60).until(
                        EC.presence_of_element_located((By.XPATH, "//input[@name='verify']"))
                    )
                    otp_input.send_keys(otp_code)
                    print("OTP entered!")
                    WebDriverWait(local_driver, 60).until(
                        EC.element_to_be_clickable((By.XPATH, "//button[text()='Next']"))
                    ).click()
                    print("OTP submitted!")
                except FileNotFoundError:
                    log_error(ERROR_CODES["OTP_error"], "OTP File was not found or was not submitted correctly.",
                              matrix_row["script_name"])
                    print("OTP File was not found or was not submitted correctly. Exiting...")
                    local_driver.quit()
                    return None
            except Exception as e:
                log_error(ERROR_CODES["login_error"], "Login page timeout or login fields not found.",
                          matrix_row["script_name"])
                print("Login page not found or timeout occurred. Exiting...")
                local_driver.quit()
                return None

        try:
            WebDriverWait(local_driver, 120).until(
                EC.element_to_be_clickable(
                    (By.XPATH, "//a[@class='pwCTA fire-event' and @data-label='Manage Licensing & Contracting']"))
            ).click()
            print("Clicked on Manage Licensing & Contracting.")
            time.sleep(10)
            WebDriverWait(local_driver, 120).until(
                EC.element_to_be_clickable((By.XPATH, "//a[contains(text(),'My Licensing & Contracting')]"))
            ).click()
            print("Clicked on My Licensing & Contracting")
            time.sleep(10)

            print("📄 Navigating to PDF report URL...")
            local_driver.get(REPORT_URL)

            print("⏳ Waiting for PDF to download...")
            pdf_file = wait_for_file(download_folder, ".pdf", timeout=120)

            if os.path.exists(PDF_TARGET):
                os.remove(PDF_TARGET)
            os.rename(pdf_file, PDF_TARGET)
            print(f"📥 PDF saved as: {PDF_TARGET}")

            local_driver.quit()

            convert_pdf_to_csv(PDF_TARGET, CSV_OUTPUT)
            print(f"✅ CSV saved as: {CSV_OUTPUT}")
        except Exception as e:
            log_error(ERROR_CODES["download_button_not_found"], "'Download' button not found.",
                      matrix_row["script_name"])
            print("Download button not found, ending process.")
            local_driver.quit()
            return None
        return download_folder
    print("RPA process failed 3 attempts. Skipping carrier.")
    log_error(ERROR_CODES["general_error"], "RPA process failed 3 attempts.", matrix_row["script_name"])
    return None


def run_bob_mutualofomaha(driver, matrix_row, date_info):
    print("Running BOB Mutual of Omaha handler...")

    download_folder = os.path.normpath(matrix_row["download_path"])
    driver.get(matrix_row["source_url"])

    # Perform login if needed
    for attempt in range(3):  # Retry up to 3 times
        if matrix_row["source_login"].upper() == "YES":
            try:
                # Step 1: Navigate to login page
                first_window = driver.current_window_handle
                WebDriverWait(driver, 120).until(
                    EC.element_to_be_clickable((By.XPATH, "//*[@data-tid='MainNav-Sign In']"))
                ).click()
                time.sleep(2)

                WebDriverWait(driver, 120).until(
                    EC.element_to_be_clickable(
                        (By.XPATH, "//a[@class='ma-signInTypeLink' and @title='Go to Sales Professional Access']"))
                ).click()
                print("Sales Professional Access page selected")
                time.sleep(2)

                WebDriverWait(driver, 10).until(EC.number_of_windows_to_be(2))

                for handle in driver.window_handles:
                    if handle != first_window:
                        driver.switch_to.window(handle)
                        break
                print("Switched to SPA tab.")

            except TimeoutException:
                print("Failed to switch domain.")
                return None

            try:
                second_window = driver.current_window_handle
                WebDriverWait(driver, 120).until(
                    EC.element_to_be_clickable((By.XPATH, "//a[@title='Sign in to Sales Professional Access']"))
                ).click()
                time.sleep(2)
                print("Sign in to Sales Professional Access page selected")

                WebDriverWait(driver, 10).until(EC.number_of_windows_to_be(3))
                for handle in driver.window_handles:
                    if handle != first_window and handle != second_window:
                        driver.switch_to.window(handle)
                        break
                print("Switched to login tab.")

            except TimeoutException:
                print("Failed to switch domain.")
                return None

            try:
                # Step 2: Enter username
                WebDriverWait(driver, 30).until(
                    EC.presence_of_element_located((By.ID, "username"))
                ).send_keys(matrix_row["source_email"])
                time.sleep(2)

                # Step 3: Enter password
                driver.find_element(By.ID, "password").send_keys(matrix_row["source_password"])
                time.sleep(2)

                # Step 4: Click Submit
                driver.find_element(By.XPATH, "//*[@id='signIn']").click()
                print("OTP Page")

                # Step 5: Click "Send Code" to receive OTP
                try:
                    WebDriverWait(driver, 60).until(
                        EC.element_to_be_clickable((By.XPATH, "//*[@type='submit']"))
                    ).click()
                    print("OTP sent!")

                    otp_code = fetch_otp_code_from_file(matrix_row)
                    print(f"Fetched OTP: {otp_code}")

                    print("Looking for OTP input field...")
                    otp_input = WebDriverWait(driver, 60).until(
                        EC.visibility_of_element_located((By.XPATH, "//*[@name='credentials.passcode']"))
                    )
                    print("OTP input field found.")

                    otp_input.click()
                    otp_input.send_keys(otp_code)
                    print("OTP entered!")
                    time.sleep(2)

                    # Step 7: Click Submit OTP button
                    WebDriverWait(driver, 60).until(
                        EC.element_to_be_clickable((By.XPATH, "//*[@data-type='save']"))
                    ).click()
                    print("OTP submitted!")

                except FileNotFoundError:
                    log_error(
                        ERROR_CODES["OTP_error"],
                        "OTP File was not found or was not submitted correctly.",
                        matrix_row["script_name"]
                    )
                    print("OTP File was not found or was not submitted correctly. Exiting...")
                    driver.quit()
                    return None

                time.sleep(10)

            except Exception as e:
                log_error(
                    ERROR_CODES["login_error"],
                    f"Login page timeout or login fields not found. Error: {str(e)}",
                    matrix_row["script_name"]
                )
                print(f"Login page not found or timeout occurred. Exiting... Error: {e}")
                driver.quit()
                return None

            # Step 8: Navigate to download page
            page_loaded = False

            for nav_attempt in range(3):
                try:
                    print(f"Trying to open Book of Business Download page. Attempt {nav_attempt + 1}/3")

                    WebDriverWait(driver, 120).until(
                        EC.element_to_be_clickable((By.XPATH, "//a[@id='bookOfBusinessDownloadLink']"))
                    ).click()
                    print("Clicked on Book of Business Download link.")

                    # Check whether the next page loaded
                    WebDriverWait(driver, 20).until(
                        EC.element_to_be_clickable(
                            (By.XPATH, "//div[normalize-space()='All In-Force Policies']/preceding-sibling::span"))
                    )
                    print("Download page loaded successfully.")
                    page_loaded = True
                    break

                except Exception as e:
                    print(f"Attempt {nav_attempt + 1} failed to load download page. Error: {e}")
                    time.sleep(5)

            if not page_loaded:
                print("Failed to navigate to Download page after 3 attempts.")
                return None

            # Step 9: Select 'All In-Force Policies' option
            try:
                WebDriverWait(driver, 120).until(
                    EC.element_to_be_clickable(
                        (By.XPATH, "//div[normalize-space()='All In-Force Policies']/preceding-sibling::span"))
                ).click()
                print("Selected 'All In-Force Policies' option.")

                WebDriverWait(driver, 120).until(
                    EC.element_to_be_clickable((By.XPATH, "//button[@data-tid='bob-download-button']"))
                ).click()
                print("Clicked on Download button to download file.")
                time.sleep(60)  # Wait for download to complete

                return download_folder

            except Exception as e:
                log_error(
                    ERROR_CODES["download_button_not_found"],
                    f"'Download' button not found. Error: {str(e)}",
                    matrix_row["script_name"]
                )
                print(f"Download button not found, ending process. Error: {e}")
                driver.quit()
                return None

        else:
            return download_folder

    print("RPA process failed 3 attempts. Skipping carrier.")
    log_error(ERROR_CODES["general_error"], "RPA process failed 3 attempts.", matrix_row["script_name"])
    return None


def run_bob_worldtrips(driver, matrix_row, date_info):
    print("Running BOB WorldTrips handler...")

    download_folder = os.path.normpath(matrix_row["download_path"])
    driver.get(matrix_row["source_url"])

    # Perform login if needed
    for attempt in range(3):  # Retry up to 3 times. (Only if code does not return a folder OR an error!)
        if matrix_row["source_login"].upper() == "YES":
            try:
                # Step 2: Enter username
                WebDriverWait(driver, 30).until(
                    EC.element_to_be_clickable((By.XPATH, "//input[@name='username']"))
                ).send_keys(matrix_row["source_email"])
                # Step 3: Enter password
                WebDriverWait(driver, 30).until(
                    EC.element_to_be_clickable((By.XPATH, "//input[@name='password']"))
                ).send_keys(matrix_row["source_password"])
                # Step 4: Click Submit
                WebDriverWait(driver, 30).until(
                    EC.element_to_be_clickable((By.XPATH, "//input[@type='submit']"))
                ).click()
                print("Login submitted.")
                time.sleep(10)

                # Step 5: Input OTP
                try:
                    otp_code = fetch_otp_code_from_file(matrix_row)

                    # Step 6: Enter OTP into the field
                    otp_input = WebDriverWait(driver, 60).until(
                        EC.presence_of_element_located((By.XPATH, "//input[@name='securityCode']"))
                    )
                    otp_input.send_keys(otp_code)
                    print("OTP entered!")

                    # Step 7: Click Submit OTP button
                    WebDriverWait(driver, 60).until(
                        EC.element_to_be_clickable((By.XPATH, "//input[@value='Verify']"))
                    ).click()
                    print("OTP submitted!")
                except FileNotFoundError:
                    log_error(ERROR_CODES["OTP_error"], "OTP File was not found or was not submitted correctly.",
                              matrix_row["script_name"])
                    print("OTP File was not found or was not submitted correctly. Exiting...")
                    driver.quit()
                    return None
            except Exception as e:
                log_error(ERROR_CODES["login_error"], "Login page timeout or login fields not found.",
                          matrix_row["script_name"])
                print("Login page not found or timeout occurred. Exiting...")
                driver.quit()
                return None

            # Step 8: Navigate to download page
            try:
                WebDriverWait(driver, 120).until(
                    EC.element_to_be_clickable((By.XPATH, "//input[@id='cbxIAgree']"))
                ).click()
                print("Agreed to terms and conditions.")
                time.sleep(5)
                WebDriverWait(driver, 120).until(
                    EC.element_to_be_clickable((By.XPATH, "//button[@class='btn hcc-btn-default']"))
                ).click()
                print("Submitted.")
                time.sleep(30)
                WebDriverWait(driver, 120).until(
                    EC.element_to_be_clickable(
                        (By.XPATH, "//a[@class='dropdown-toggle' and contains(.,'My Favorites')]"))
                ).click()
                print("Clicked on My Favorites dropdown.")
                time.sleep(5)
                WebDriverWait(driver, 120).until(
                    EC.element_to_be_clickable(
                        (By.XPATH, "//a[@title='Review production numbers' and text()='Production Zone']"))
                ).click()
                print("Navigated to Production Zone.")
                time.sleep(30)
                WebDriverWait(driver, 120).until(
                    EC.element_to_be_clickable((By.XPATH,
                                                "//input[@type='checkbox'][normalize-space(following-sibling::text())='Print Friendly']"))
                ).click()
                print("Print Friendly selected.")
                time.sleep(5)
                WebDriverWait(driver, 120).until(
                    EC.element_to_be_clickable((By.XPATH,
                                                "//input[@type='checkbox'][normalize-space(following-sibling::text())='Year-to-date']"))
                ).click()
                print("Year-to-date selected.")
                time.sleep(5)
                WebDriverWait(driver, 120).until(
                    EC.element_to_be_clickable((By.XPATH, "//input[@value='Search']"))
                ).click()
                print("Search submitted.")
                time.sleep(30)
                WebDriverWait(driver, 120).until(
                    EC.element_to_be_clickable((By.XPATH, "//input[@value='CSV Download']"))
                ).click()
                print("CSV Download clicked, file should start downloading...")
                time.sleep(60)  # Wait for download to complete
            except Exception as e:
                log_error(ERROR_CODES["download_button_not_found"], "'Download' button not found.",
                          matrix_row["script_name"])
                print("Download button not found, ending process.")
                driver.quit()

        return download_folder
    print("RPA process failed 3 attempts. Skipping carrier.")
    log_error(ERROR_CODES["general_error"], "RPA process failed 3 attempts.", matrix_row["script_name"])
    return None


def run_acu_health_first(driver, matrix_row, date_info):
    print("Running ACU Health First ACA handler...")

    download_folder = os.path.normpath(matrix_row["download_path"])
    script_name = matrix_row["script_name"]

    # Agency user ID for Agility Insurance Services LLC
    # (used by swapUser() in the header dropdown and PickUser() in the modal)
    AGENCY_USER_ID = 731656

    driver.get(matrix_row["source_url"])

    # Perform login if needed
    for attempt in range(3):  # Retry up to 3 times. (Only if code does not return a folder OR an error!)
        # ──────────────────────────────────────────────
        # STEP 1: Login to EvolveNXT
        # ──────────────────────────────────────────────
        time.sleep(5)

        if matrix_row["source_login"].upper() == "YES":
            try:
                # EvolveNXT login fields can be "not interactable" — use JS fallback
                WebDriverWait(driver, 15).until(
                    EC.presence_of_element_located((By.ID, "login_id"))
                )

                # Email — try send_keys on visible field, fallback to JS
                login_fields = driver.find_elements(By.ID, "login_id")
                try:
                    visible_field = next(el for el in login_fields if el.is_displayed())
                    visible_field.clear()
                    visible_field.send_keys(matrix_row["source_email"])
                    print("Entered email via send_keys.")
                except (StopIteration, Exception):
                    driver.execute_script(
                        "document.getElementById('login_id').value = arguments[0];",
                        matrix_row["source_email"]
                    )
                    print("Entered email via JS.")

                # Password — try send_keys, fallback to JS
                try:
                    pwd_field = driver.find_element(By.ID, "password")
                    if pwd_field.is_displayed():
                        pwd_field.clear()
                        pwd_field.send_keys(matrix_row["source_password"])
                        print("Entered password via send_keys.")
                    else:
                        raise Exception("not displayed")
                except Exception:
                    driver.execute_script(
                        "document.getElementById('password').value = arguments[0];",
                        matrix_row["source_password"]
                    )
                    print("Entered password via JS.")

                # Submit — try click, fallback to JS click
                login_btn = WebDriverWait(driver, 10).until(
                    EC.presence_of_element_located((By.ID, "submit"))
                )
                try:
                    login_btn.click()
                    print("Login submitted via click.")
                except Exception:
                    driver.execute_script("document.getElementById('submit').click();")
                    print("Login submitted via JS click.")
                time.sleep(15)

            except Exception as e:
                log_error(ERROR_CODES["login_error"],
                          f"Login failed: {e}", script_name)
                print(f"Login failed: {e}")
                driver.quit()
                return None

        # ──────────────────────────────────────────────
        # STEP 2: Switch user to Agility Insurance Services LLC
        # ──────────────────────────────────────────────
        try:
            print("Opening user dropdown...")
            user_dropdown = WebDriverWait(driver, 30).until(
                EC.element_to_be_clickable((By.ID, "userDropdown"))
            )
            try:
                user_dropdown.click()
            except Exception:
                driver.execute_script("arguments[0].click();", user_dropdown)
            print("Clicked user dropdown.")
            time.sleep(2)

            # Click switch-user link (opens the principal-swap modal)
            swap_link = WebDriverWait(driver, 15).until(
                EC.element_to_be_clickable((By.CSS_SELECTOR,
                                            f"a.dropdown-item[onclick*='swapUser({AGENCY_USER_ID}']"))
            )
            try:
                swap_link.click()
            except Exception:
                driver.execute_script("arguments[0].click();", swap_link)
            print("Clicked switch to Agility Insurance Services LLC.")
            time.sleep(2)

            # Modal appears — click Agency Login (PickUser(731656))
            agency_btn = WebDriverWait(driver, 15).until(
                EC.element_to_be_clickable((By.CSS_SELECTOR,
                                            f"button[onclick='PickUser({AGENCY_USER_ID})']"))
            )
            try:
                agency_btn.click()
            except Exception:
                driver.execute_script("arguments[0].click();", agency_btn)
            print("Clicked Agency Login.")
            time.sleep(10)

        except Exception as e:
            log_error(ERROR_CODES["navigation_error"],
                      f"Failed to switch to Agility agency login: {e}", script_name)
            print(f"Agency switch failed: {e}")
            driver.quit()
            return None

        # ──────────────────────────────────────────────
        # STEP 3: My Downline Brokers → Broker Credentials
        # ──────────────────────────────────────────────
        try:
            print("Navigating to My Downline Brokers...")

            # Click "My Downline Brokers" sidebar link
            try:
                downline_link = WebDriverWait(driver, 60).until(
                    EC.element_to_be_clickable((By.XPATH,
                                                "//a[@href='/portal/mpc_detail.htm' and @data-target='#collapse_15']"))
                )
                driver.execute_script("arguments[0].scrollIntoView(true);", downline_link)
                time.sleep(1)
                downline_link.click()
                print("Clicked 'My Downline Brokers'.")
            except TimeoutException:
                # Fallback: text match
                downline_link = WebDriverWait(driver, 15).until(
                    EC.element_to_be_clickable((By.XPATH,
                                                "//a[contains(.,'My Downline Brokers')]"))
                )
                downline_link.click()
                print("Clicked 'My Downline Brokers' (fallback).")
            time.sleep(3)

            # Click "Broker Credentials" sub-menu
            try:
                broker_creds = WebDriverWait(driver, 30).until(
                    EC.element_to_be_clickable((By.XPATH,
                                                "//a[@class='collapse-item' and @href='/portal/mpc_detail.htm']"))
                )
                broker_creds.click()
                print("Clicked 'Broker Credentials'.")
            except TimeoutException:
                broker_creds = WebDriverWait(driver, 15).until(
                    EC.element_to_be_clickable((By.XPATH,
                                                "//a[contains(text(),'Broker Credentials')]"))
                )
                broker_creds.click()
                print("Clicked 'Broker Credentials' (fallback).")
            time.sleep(10)

        except Exception as e:
            log_error(ERROR_CODES["navigation_error"],
                      f"Navigation to Broker Credentials failed: {e}", script_name)
            print(f"Navigation failed: {e}")
            driver.quit()
            return None

        # ──────────────────────────────────────────────
        # STEP 4: Click "Download Appointment Info"
        # ──────────────────────────────────────────────
        try:
            print("Waiting for 'Download Appointment Info' button...")

            try:
                download_btn = WebDriverWait(driver, 60).until(
                    EC.element_to_be_clickable((By.ID, "appointment_info"))
                )
            except TimeoutException:
                # Fallback
                download_btn = WebDriverWait(driver, 15).until(
                    EC.element_to_be_clickable((By.XPATH,
                                                "//button[contains(@onclick,'appt_info') or contains(text(),'Download Appointment Info')]"))
                )

            driver.execute_script("arguments[0].scrollIntoView(true);", download_btn)
            time.sleep(1)
            download_btn.click()
            print("Clicked 'Download Appointment Info'.")
            time.sleep(120)  # Wait for file download

            log_success()

        except Exception as e:
            log_error(ERROR_CODES["download_button_not_found"],
                      f"'Download Appointment Info' button not found: {e}", script_name)
            print(f"Download button not found: {e}")
            driver.quit()
            return None

        return download_folder

    print("RPA process failed 3 attempts. Skipping carrier.")
    log_error(ERROR_CODES["general_error"], "RPA process failed 3 attempts.", matrix_row["script_name"])
    return None


def raw_bob_goldkidney(driver, matrix_row, date_info):
    print("Running BOB Gold Kidney handler...")

    download_folder = os.path.normpath(matrix_row["download_path"])
    driver.get(matrix_row["source_url"])

    # Perform login if needed
    for attempt in range(3):  # Retry up to 3 times. (Only if code does not return a folder OR an error!)
        if matrix_row["source_login"].upper() == "YES":
            try:

                # Step 1: Enter username
                time.sleep(2)
                WebDriverWait(driver, 30).until(
                    EC.presence_of_element_located((By.ID, "login_id"))
                ).send_keys(matrix_row["source_email"])
                time.sleep(2)

                # Step 2: Enter password
                WebDriverWait(driver, 30).until(
                    EC.presence_of_element_located((By.ID, "password"))
                ).send_keys(matrix_row["source_password"])
                time.sleep(2)

                # Step 3: Click Submit
                WebDriverWait(driver, 30).until(
                    EC.element_to_be_clickable((By.ID, "submit"))
                ).click()
                print("Login submitted.")
                time.sleep(10)

            except Exception as e:
                log_error(ERROR_CODES["login_error"], "Login page timeout or login fields not found.",
                          matrix_row["script_name"])
                print("Login page not found or timeout occurred. Exiting...")
                driver.quit()
                return None

            # Step 4: Navigate to download page
            try:
                WebDriverWait(driver, 120).until(
                    EC.element_to_be_clickable((By.XPATH, "//a[normalize-space()='Book of Business']"))
                ).click()
                print("Clicked on Book of Business link.")
                time.sleep(10)

                WebDriverWait(driver, 120).until(
                    EC.element_to_be_clickable((By.XPATH, "//button[@id='submit']"))
                ).click()
                print("Clicked on Search button.")
                time.sleep(10)

                WebDriverWait(driver, 120).until(
                    EC.element_to_be_clickable((By.XPATH, "//button[@id='dButton']"))
                ).click()
                print("Clicked on Download CSV link to download file.")
                time.sleep(10)  # Wait for download to complete
            except Exception as e:
                log_error(ERROR_CODES["download_button_not_found"], "'Download' button not found.",
                          matrix_row["script_name"])
                print("Download button not found, ending process.")
                driver.quit()
                return None

        return download_folder
    print("RPA process failed 3 attempts. Skipping carrier.")
    log_error(ERROR_CODES["general_error"], "RPA process failed 3 attempts.", matrix_row["script_name"])
    return None


def run_bob_christus_aca(driver, matrix_row, date_info):
    print("Running BOB CHRISTUS ACA handler...")

    download_folder = os.path.normpath(matrix_row["download_path"])
    script_name = matrix_row["script_name"]

    try:
        # STEP 1: Login
        driver.get(matrix_row["source_url"])
        time.sleep(5)

        if matrix_row["source_login"].upper() == "YES":
            print("Logging in...")

            username_field = WebDriverWait(driver, 30).until(
                EC.element_to_be_clickable((By.ID, "login_id"))
            )
            username_field.clear()
            username_field.send_keys(matrix_row["source_email"])

            password_field = WebDriverWait(driver, 30).until(
                EC.element_to_be_clickable((By.ID, "password"))
            )
            password_field.clear()
            password_field.send_keys(matrix_row["source_password"])

            login_btn = WebDriverWait(driver, 10).until(
                EC.element_to_be_clickable((By.ID, "submit"))
            )
            login_btn.click()

            print("Login submitted.")
            time.sleep(10)

        # STEP 2: Handle password expiry modal if present
        print("Checking for password expiry modal...")

        try:
            continue_login_btn = WebDriverWait(driver, 5).until(
                EC.element_to_be_clickable((By.ID, "goToDomain"))
            )
            continue_login_btn.click()
            print("Password expiry modal dismissed.")
            time.sleep(10)
        except TimeoutException:
            print("No password expiry modal.")

        # STEP 3: Click Christus domain card if present
        print("Checking for domain selection...")

        try:
            christus_card = WebDriverWait(driver, 5).until(
                EC.element_to_be_clickable((By.XPATH,
                                            "//div[contains(@onclick, 'doLogin') and contains(@onclick, 'christus.evolvenxt.com')]"))
            )
            christus_card.click()
            print("Christus domain card clicked.")
            time.sleep(5)

            if len(driver.window_handles) > 1:
                driver.switch_to.window(driver.window_handles[-1])
                print("Switched to Christus tab.")
                time.sleep(20)
        except TimeoutException:
            print("No domain selection screen.")

        # STEP 4: Book of Business → Search ACA
        print("Opening Book of Business menu...")

        try:
            bob_menu = WebDriverWait(driver, 60).until(
                EC.element_to_be_clickable((By.XPATH, "//a[contains(., 'Book of Business')]"))
            )
            bob_menu.click()
        except TimeoutException:
            bob_menu = driver.find_element(By.XPATH, "//span[contains(text(), 'Book of Business')]/ancestor::a")
            driver.execute_script("arguments[0].click();", bob_menu)
        time.sleep(3)

        print("Clicking Search ACA...")

        search_aca = WebDriverWait(driver, 30).until(
            EC.element_to_be_clickable((By.XPATH, "//a[contains(text(), 'Search ACA')]"))
        )
        search_aca.click()
        time.sleep(10)

        # STEP 5: Search
        print("Clicking Search...")

        search_btn = WebDriverWait(driver, 60).until(
            EC.element_to_be_clickable((By.XPATH, "//button[@id='submit' and contains(text(), 'Search')]"))
        )
        search_btn.click()

        print("Waiting for results...")
        time.sleep(60)

        # STEP 6: Download
        print("Clicking Download...")

        download_btn = WebDriverWait(driver, 60).until(
            EC.element_to_be_clickable((By.ID, "download"))
        )
        download_btn.click()

        print("Waiting for file download...")
        time.sleep(120)

        log_success()
        return download_folder

    except Exception as e:
        log_error(
            ERROR_CODES["general_error"],
            f"BOB CHRISTUS ACA failed: {e}",
            script_name
        )
        print(f"BOB CHRISTUS ACA failed: {e}")
        driver.quit()
        return None


def run_bob_christus_mdc(driver, matrix_row, date_info):
    print("Running BOB CHRISTUS MDC handler...")

    download_folder = os.path.normpath(matrix_row["download_path"])
    script_name = matrix_row["script_name"]

    try:
        # STEP 1: Login
        driver.get(matrix_row["source_url"])
        time.sleep(5)

        if matrix_row["source_login"].upper() == "YES":
            print("Logging in...")

            username_field = WebDriverWait(driver, 30).until(
                EC.element_to_be_clickable((By.ID, "login_id"))
            )
            username_field.clear()
            username_field.send_keys(matrix_row["source_email"])

            password_field = WebDriverWait(driver, 30).until(
                EC.element_to_be_clickable((By.ID, "password"))
            )
            password_field.clear()
            password_field.send_keys(matrix_row["source_password"])

            login_btn = WebDriverWait(driver, 10).until(
                EC.element_to_be_clickable((By.ID, "submit"))
            )
            login_btn.click()

            print("Login submitted.")
            time.sleep(10)

        # STEP 2: Handle password expiry modal if present
        print("Checking for password expiry modal...")

        try:
            continue_login_btn = WebDriverWait(driver, 5).until(
                EC.element_to_be_clickable((By.ID, "goToDomain"))
            )
            continue_login_btn.click()
            print("Password expiry modal dismissed.")
            time.sleep(10)
        except TimeoutException:
            print("No password expiry modal.")

        # STEP 3: Click Christus domain card if present
        print("Checking for domain selection...")

        try:
            christus_card = WebDriverWait(driver, 5).until(
                EC.element_to_be_clickable((By.XPATH,
                                            "//div[contains(@onclick, 'doLogin') and contains(@onclick, 'christus.evolvenxt.com')]"))
            )
            christus_card.click()
            print("Christus domain card clicked.")
            time.sleep(5)

            if len(driver.window_handles) > 1:
                driver.switch_to.window(driver.window_handles[-1])
                print("Switched to Christus tab.")
                time.sleep(20)
        except TimeoutException:
            print("No domain selection screen.")

        # STEP 4: Book of Business → Search Medicare
        print("Opening Book of Business menu...")

        try:
            bob_menu = WebDriverWait(driver, 60).until(
                EC.element_to_be_clickable((By.XPATH, "//a[contains(., 'Book of Business')]"))
            )
            bob_menu.click()
        except TimeoutException:
            bob_menu = driver.find_element(By.XPATH, "//span[contains(text(), 'Book of Business')]/ancestor::a")
            driver.execute_script("arguments[0].click();", bob_menu)
        time.sleep(3)

        print("Clicking Search Medicare...")

        search_medicare = WebDriverWait(driver, 30).until(
            EC.element_to_be_clickable((By.CSS_SELECTOR, "a.collapse-item[href='/portal/member_search.htm']"))
        )
        search_medicare.click()
        time.sleep(10)

        # STEP 5: Search
        print("Clicking Search...")

        search_btn = WebDriverWait(driver, 60).until(
            EC.element_to_be_clickable((By.ID, "submit"))
        )
        search_btn.click()

        print("Waiting for results...")
        time.sleep(60)

        # STEP 6: Wait for results table
        WebDriverWait(driver, 60).until(
            EC.presence_of_element_located((By.ID, "portal_members"))
        )

        try:
            info = driver.find_element(By.ID, "portal_members_info").text
            print(f"Results: {info}")
        except Exception:
            print("Results loaded.")

        time.sleep(3)

        # STEP 7: Download
        print("Clicking Download...")

        download_btn = WebDriverWait(driver, 60).until(
            EC.element_to_be_clickable((By.ID, "dButton"))
        )
        download_btn.click()

        print("Waiting for file download...")
        time.sleep(120)

        log_success()
        return download_folder

    except Exception as e:
        log_error(
            ERROR_CODES["general_error"],
            f"BOB CHRISTUS MDC failed: {e}",
            script_name
        )
        print(f"BOB CHRISTUS MDC failed: {e}")
        driver.quit()
        return None


def run_bob_gold_kidney_health(driver, matrix_row, date_info):
    print("Running BOB GOLD KIDNEY HEALTH PLAN handler...")

    download_folder = os.path.normpath(matrix_row["download_path"])
    script_name = matrix_row["script_name"]

    try:
        # ──────────────────────────────────────────────
        # STEP 1: Open URL and Login
        # ──────────────────────────────────────────────
        driver.get(matrix_row["source_url"])
        time.sleep(5)

        if matrix_row["source_login"].upper() == "YES":
            print("Logging in...")

            username = WebDriverWait(driver, 30).until(
                EC.element_to_be_clickable((By.ID, "login_id"))
            )
            username.clear()
            username.send_keys(matrix_row["source_email"])

            password = WebDriverWait(driver, 30).until(
                EC.element_to_be_clickable((By.ID, "password"))
            )
            password.clear()
            password.send_keys(matrix_row["source_password"])

            login_btn = WebDriverWait(driver, 30).until(
                EC.element_to_be_clickable((By.ID, "submit"))
            )
            login_btn.click()

            print("Login submitted.")
            time.sleep(15)

        # ──────────────────────────────────────────────
        # STEP 2: Click Book of Business
        # ──────────────────────────────────────────────
        print("Opening Book of Business...")

        bob_menu = WebDriverWait(driver, 60).until(
            EC.element_to_be_clickable((
                By.XPATH,
                "//a[contains(@href, 'member_search.htm') and contains(., 'Book of Business')]"
            ))
        )
        bob_menu.click()
        time.sleep(10)

        # ──────────────────────────────────────────────
        # STEP 3: Click Search
        # ──────────────────────────────────────────────
        print("Clicking Search button...")

        search_btn = WebDriverWait(driver, 60).until(
            EC.element_to_be_clickable((
                By.XPATH,
                "//button[@id='submit' and contains(normalize-space(), 'Search')]"
            ))
        )
        search_btn.click()

        print("Waiting 60 seconds for results to load...")
        time.sleep(60)

        # ──────────────────────────────────────────────
        # STEP 4: Download
        # ──────────────────────────────────────────────
        print("Clicking Download button...")

        download_btn = WebDriverWait(driver, 60).until(
            EC.element_to_be_clickable((By.ID, "dButton"))
        )
        download_btn.click()

        print("Waiting for file download...")
        time.sleep(120)

        log_success()
        return download_folder

    except Exception as e:
        log_error(
            ERROR_CODES["download_button_not_found"],
            f"BOB GOLD KIDNEY HEALTH failed: {e}",
            script_name
        )
        print(f"BOB GOLD KIDNEY HEALTH failed: {e}")
        driver.quit()
        return None


######################################################################################################################################################################


def run_acu_aflac(driver, matrix_row, date_info):
    print("Running ACU Aflac handler...")

    download_folder = os.path.normpath(matrix_row["download_path"])
    script_name = matrix_row["script_name"]

    try:
        # STEP 1: Login
        driver.get(matrix_row["source_url"])
        time.sleep(5)

        if matrix_row["source_login"].upper() == "YES":
            print("Logging in to Aflac portal...")

            username_field = WebDriverWait(driver, 30).until(
                EC.element_to_be_clickable((By.ID, "aetnaUserName"))
            )
            username_field.clear()
            username_field.send_keys(matrix_row["source_email"])

            password_field = WebDriverWait(driver, 30).until(
                EC.element_to_be_clickable((By.ID, "aetnaPassword"))
            )
            password_field.clear()
            password_field.send_keys(matrix_row["source_password"])

            try:
                submit_btn = WebDriverWait(driver, 5).until(
                    EC.element_to_be_clickable((By.CSS_SELECTOR, "button[type='submit'], input[type='submit']"))
                )
                submit_btn.click()
            except TimeoutException:
                password_field.send_keys(Keys.RETURN)

            print("Login submitted.")
            time.sleep(10)

        # STEP 2: Select email OTP method
        print("Selecting email OTP method...")

        try:
            email_radio = WebDriverWait(driver, 10).until(
                EC.element_to_be_clickable((By.ID, "IndividualEmailPreference_0"))
            )
            email_radio.click()
        except Exception:
            try:
                label = driver.find_element(By.CSS_SELECTOR, "label[for='IndividualEmailPreference_0']")
                driver.execute_script("arguments[0].click();", label)
            except Exception:
                print("Could not select email radio, may already be selected.")

        time.sleep(1)

        # STEP 3: Send code + fetch via Graph API
        print("Sending OTP code and polling inbox...")

        mark_matching_as_read(
            sender="support@enrollinsurance.com",
            subject="Your Verification Code Request",
        )

        try:
            send_btn = WebDriverWait(driver, 10).until(
                EC.element_to_be_clickable((By.CSS_SELECTOR, "button[name='Send Code']"))
            )
            send_btn.click()
        except TimeoutException:
            send_btn = driver.find_element(By.XPATH, "//button[@type='submit' and contains(text(),'Send')]")
            driver.execute_script("arguments[0].click();", send_btn)

        otp_sent_at = datetime.now(timezone.utc)
        time.sleep(3)

        code = fetch_otp_code(
            sender="support@enrollinsurance.com",
            subject="Your Verification Code Request",
            since_dt_utc=otp_sent_at,
            poll_seconds=120,
        )

        # STEP 4: Enter OTP + submit
        print(f"Entering OTP code: {code}")

        otp_input = WebDriverWait(driver, 15).until(
            EC.element_to_be_clickable((By.ID, "otpCode"))
        )
        otp_input.clear()
        otp_input.send_keys(code)
        time.sleep(1)

        next_btn = WebDriverWait(driver, 10).until(
            EC.element_to_be_clickable((By.CSS_SELECTOR, "button[name='Next']"))
        )
        next_btn.click()

        print("OTP submitted.")
        time.sleep(10)

        # STEP 5: Navigate to Batch Download Reports
        print("Navigating to Batch Download Reports...")

        my_reports = WebDriverWait(driver, 30).until(
            EC.element_to_be_clickable((
                By.XPATH,
                "//a[@id='navbarDarkDropdownMenuLink' and contains(normalize-space(), 'My Reports')]"
            ))
        )
        my_reports.click()
        time.sleep(2)

        batch_link = WebDriverWait(driver, 10).until(
            EC.element_to_be_clickable((By.CSS_SELECTOR, "a[href*='agencyRoster']"))
        )
        batch_link.click()

        print("Loading reports page...")
        time.sleep(10)

        # Verify agent filter is present (All is default)
        try:
            WebDriverWait(driver, 10).until(
                EC.presence_of_element_located((By.ID, "downlineAgentSearchInput"))
            )
            print("Agent filter present, 'All' selected by default.")
        except TimeoutException:
            print("Agent filter not found, proceeding anyway.")

        time.sleep(2)

        # STEP 6: Download Excel
        print("Downloading Excel...")

        download_btn = WebDriverWait(driver, 30).until(
            EC.element_to_be_clickable((
                By.XPATH,
                "//div[contains(@class, 'label-container') and contains(text(), 'Download Excel')]"
            ))
        )
        download_btn.click()

        print("Waiting for download...")
        time.sleep(60)

        log_success()
        return download_folder

    except Exception as e:
        log_error(
            ERROR_CODES["general_error"],
            f"ACU Aflac handler failed: {e}",
            script_name
        )
        print(f"ACU Aflac handler failed: {e}")
        driver.quit()
        return None


######################################################################################################################################################################


def run_bob_aflac(driver, matrix_row, date_info):
    print("Running BOB Aflac handler...")

    download_folder = os.path.normpath(matrix_row["download_path"])
    script_name = matrix_row["script_name"]

    try:
        # STEP 1: Login (same as ACU Aflac)
        driver.get(matrix_row["source_url"])
        time.sleep(5)

        if matrix_row["source_login"].upper() == "YES":
            print("Logging in to Aflac portal...")

            username_field = WebDriverWait(driver, 30).until(
                EC.element_to_be_clickable((By.ID, "aetnaUserName"))
            )
            username_field.clear()
            username_field.send_keys(matrix_row["source_email"])

            password_field = WebDriverWait(driver, 30).until(
                EC.element_to_be_clickable((By.ID, "aetnaPassword"))
            )
            password_field.clear()
            password_field.send_keys(matrix_row["source_password"])

            try:
                submit_btn = WebDriverWait(driver, 5).until(
                    EC.element_to_be_clickable((By.CSS_SELECTOR, "button[type='submit'], input[type='submit']"))
                )
                submit_btn.click()
            except TimeoutException:
                password_field.send_keys(Keys.RETURN)

            print("Login submitted.")
            time.sleep(10)

        # STEP 2: Select email OTP method
        print("Selecting email OTP method...")

        try:
            email_radio = WebDriverWait(driver, 10).until(
                EC.element_to_be_clickable((By.ID, "IndividualEmailPreference_0"))
            )
            email_radio.click()
        except Exception:
            try:
                label = driver.find_element(By.CSS_SELECTOR, "label[for='IndividualEmailPreference_0']")
                driver.execute_script("arguments[0].click();", label)
            except Exception:
                print("Could not select email radio, may already be selected.")

        time.sleep(1)

        # STEP 3: Send code + fetch via Graph API
        print("Sending OTP code and polling inbox...")

        mark_matching_as_read(
            sender="support@enrollinsurance.com",
            subject="Your Verification Code Request",
        )

        try:
            send_btn = WebDriverWait(driver, 10).until(
                EC.element_to_be_clickable((By.CSS_SELECTOR, "button[name='Send Code']"))
            )
            send_btn.click()
        except TimeoutException:
            send_btn = driver.find_element(By.XPATH, "//button[@type='submit' and contains(text(),'Send')]")
            driver.execute_script("arguments[0].click();", send_btn)

        otp_sent_at = datetime.now(timezone.utc)
        time.sleep(3)

        code = fetch_otp_code(
            sender="support@enrollinsurance.com",
            subject="Your Verification Code Request",
            since_dt_utc=otp_sent_at,
            poll_seconds=120,
        )

        # STEP 4: Enter OTP + submit
        print(f"Entering OTP code: {code}")

        otp_input = WebDriverWait(driver, 15).until(
            EC.element_to_be_clickable((By.ID, "otpCode"))
        )
        otp_input.clear()
        otp_input.send_keys(code)
        time.sleep(1)

        next_btn = WebDriverWait(driver, 10).until(
            EC.element_to_be_clickable((By.CSS_SELECTOR, "button[name='Next']"))
        )
        next_btn.click()

        print("OTP submitted.")
        time.sleep(10)

        # STEP 5: Navigate to Policy Summary
        print("Navigating to Policy Summary...")

        my_reports = WebDriverWait(driver, 30).until(
            EC.element_to_be_clickable((
                By.XPATH,
                "//a[@id='navbarDarkDropdownMenuLink' and contains(normalize-space(), 'My Reports')]"
            ))
        )
        my_reports.click()
        time.sleep(2)

        policy_summary_link = WebDriverWait(driver, 10).until(
            EC.element_to_be_clickable((By.CSS_SELECTOR, "a[href*='policy/summary?channel=tier']"))
        )
        policy_summary_link.click()

        print("Loading Policy Summary page...")
        time.sleep(10)

        # STEP 6: Set agent dropdown to "All"
        print("Setting agent filter to All...")

        agent_dropdown = WebDriverWait(driver, 15).until(
            EC.presence_of_element_located((By.ID, "agent"))
        )
        time.sleep(2)

        select_agent = Select(agent_dropdown)
        select_agent.select_by_value("All")

        print("Agent filter set to All.")
        time.sleep(5)

        # STEP 7: Download Excel
        print("Downloading Excel...")

        download_btn = WebDriverWait(driver, 30).until(
            EC.element_to_be_clickable((By.CSS_SELECTOR, "button.download-btn"))
        )
        download_btn.click()

        print("Waiting for download...")
        time.sleep(60)

        log_success()
        return download_folder

    except Exception as e:
        log_error(
            ERROR_CODES["general_error"],
            f"BOB Aflac handler failed: {e}",
            script_name
        )
        print(f"BOB Aflac handler failed: {e}")
        driver.quit()
        return None


######################################################################################################################################################################


def run_acu_gold_kidney_health(driver, matrix_row, date_info):
    print("Running ACU Gold Kidney Health Plan handler...")

    download_folder = os.path.normpath(matrix_row["download_path"])
    script_name = matrix_row["script_name"]

    try:
        # STEP 1: Login
        driver.get(matrix_row["source_url"])
        time.sleep(5)

        if matrix_row["source_login"].upper() == "YES":
            print("Logging in...")

            username_field = WebDriverWait(driver, 30).until(
                EC.element_to_be_clickable((By.ID, "login_id"))
            )
            username_field.clear()
            username_field.send_keys(matrix_row["source_email"])

            password_field = WebDriverWait(driver, 30).until(
                EC.element_to_be_clickable((By.ID, "password"))
            )
            password_field.clear()
            password_field.send_keys(matrix_row["source_password"])

            login_btn = WebDriverWait(driver, 10).until(
                EC.element_to_be_clickable((By.ID, "submit"))
            )
            login_btn.click()

            print("Login submitted.")
            time.sleep(10)

        # STEP 2: Click "My Downline Brokers"
        print("Clicking My Downline Brokers...")

        downline_link = WebDriverWait(driver, 30).until(
            EC.element_to_be_clickable((By.CSS_SELECTOR, "a[href='/portal/mpc_detail.htm']"))
        )
        downline_link.click()
        time.sleep(3)

        # STEP 3: Click "Broker Credentials"
        print("Clicking Broker Credentials...")

        broker_creds = WebDriverWait(driver, 10).until(
            EC.element_to_be_clickable((By.CSS_SELECTOR, "a.collapse-item[href='/portal/mpc_detail.htm']"))
        )
        broker_creds.click()
        time.sleep(5)

        # STEP 4: Click Search
        print("Clicking Search...")

        search_btn = WebDriverWait(driver, 10).until(
            EC.element_to_be_clickable((By.ID, "search_button"))
        )
        search_btn.click()

        print("Waiting for results...")
        time.sleep(15)

        WebDriverWait(driver, 60).until(
            EC.presence_of_element_located((By.ID, "my_producers"))
        )

        try:
            info = driver.find_element(By.ID, "my_producers_info").text
            print(f"Results: {info}")
        except Exception:
            print("Results loaded.")

        time.sleep(3)

        # STEP 5: Download Rep Status
        print("Clicking Download Rep Status...")

        download_btn = WebDriverWait(driver, 15).until(
            EC.element_to_be_clickable((By.ID, "producer_status"))
        )
        download_btn.click()

        print("Waiting for download...")
        time.sleep(30)

        log_success()
        return download_folder

    except Exception as e:
        log_error(
            ERROR_CODES["general_error"],
            f"ACU Gold Kidney Health handler failed: {e}",
            script_name
        )
        print(f"ACU Gold Kidney Health handler failed: {e}")
        driver.quit()
        return None

######################################################################################################################################################################

def run_bob_quartz(driver, matrix_row, date_info):
    print("Running BOB Quartz handler...")

    download_folder = os.path.normpath(matrix_row["download_path"])
    script_name = matrix_row["script_name"]

    try:
        # STEP 1: Open login page
        print("[1/7] Opening Quartz MyPlanTools login page ...")

        driver.get(matrix_row["url"])
        time.sleep(5)
        print(f"  Loaded URL: {driver.current_url}")

        # STEP 2: Login
        if matrix_row["log_in"].upper() == "YES":
            try:
                print("[2/7] Entering credentials ...")

                username_field = WebDriverWait(driver, 60).until(
                    EC.element_to_be_clickable((By.ID, "Username"))
                )
                username_field.clear()
                username_field.send_keys(matrix_row["email"])
                print("  Entered username.")

                password_field = WebDriverWait(driver, 60).until(
                    EC.element_to_be_clickable((By.ID, "Password"))
                )
                password_field.clear()
                password_field.send_keys(matrix_row["password"])
                print("  Entered password.")

                # STEP 3: Attempt standard reCAPTCHA checkbox click
                print("[3/7] Checking for reCAPTCHA ...")

                try:
                    recaptcha_iframe = WebDriverWait(driver, 15).until(
                        EC.presence_of_element_located(
                            (By.CSS_SELECTOR, "iframe[src*='recaptcha']")
                        )
                    )

                    driver.switch_to.frame(recaptcha_iframe)

                    recaptcha_checkbox = WebDriverWait(driver, 15).until(
                        EC.element_to_be_clickable((By.ID, "recaptcha-anchor"))
                    )
                    recaptcha_checkbox.click()

                    print("  reCAPTCHA checkbox clicked.")
                    time.sleep(5)

                    driver.switch_to.default_content()

                except TimeoutException:
                    driver.switch_to.default_content()
                    print("  No reCAPTCHA iframe detected. Continuing.")

                except Exception as e:
                    driver.switch_to.default_content()
                    print(f"  reCAPTCHA checkbox could not be clicked: {e}")

                # STEP 4: Click Login
                print("[4/7] Clicking Login ...")

                login_btn = WebDriverWait(driver, 60).until(
                    EC.element_to_be_clickable(
                        (
                            By.XPATH,
                            "//button[@type='submit' and contains(normalize-space(), 'Login')]"
                        )
                    )
                )
                login_btn.click()
                print("  Login submitted.")
                time.sleep(10)

                print(f"  Current URL after login: {driver.current_url}")

                WebDriverWait(driver, 60).until(
                    EC.presence_of_element_located(
                        (
                            By.XPATH,
                            "//*[contains(normalize-space(), 'Agent Notice') "
                            "or contains(normalize-space(), 'My Profile') "
                            "or contains(normalize-space(), 'Line of Business')]"
                        )
                    )
                )
                print("  Login verified.")

            except Exception as e:
                log_error(
                    ERROR_CODES["login_error"],
                    f"Quartz login failed or verification failed: {e}",
                    script_name
                )
                print(f"Quartz login failed or verification failed: {e}")
                driver.quit()
                return None

        # STEP 5: Navigate to Individual Member Information page
        try:
            print("[5/7] Navigating to Individual Member Information page ...")

            driver.get("https://myplantools.com/Enrollment/AgentShowIndividual")
            time.sleep(10)

            WebDriverWait(driver, 60).until(
                EC.presence_of_element_located(
                    (
                        By.XPATH,
                        "//*[contains(normalize-space(), 'Member Information')]"
                    )
                )
            )
            print("  Member Information page loaded.")

        except Exception as e:
            log_error(
                ERROR_CODES["navigation_error"],
                f"Quartz Individual Member Information navigation failed: {e}",
                script_name
            )
            print(f"Quartz Individual Member Information navigation failed: {e}")
            driver.quit()
            return None

        # STEP 6: Download Member List → All
        try:
            print("[6/7] Downloading Member List - All ...")

            before_files = set(os.listdir(download_folder))

            download_dropdown = WebDriverWait(driver, 60).until(
                EC.element_to_be_clickable((By.ID, "dropdownMenuLink"))
            )
            download_dropdown.click()
            print("  Download Member List dropdown opened.")
            time.sleep(2)

            all_download_link = WebDriverWait(driver, 60).until(
                EC.element_to_be_clickable(
                    (
                        By.XPATH,
                        "//a[contains(@class, 'dropdown-item') and normalize-space()='All']"
                    )
                )
            )
            all_download_link.click()
            print("  Clicked All download option.")

        except Exception as e:
            log_error(
                ERROR_CODES["download_error"],
                f"Quartz Member List download click failed: {e}",
                script_name
            )
            print(f"Quartz Member List download click failed: {e}")
            driver.quit()
            return None

        # STEP 7: Wait for download
        try:
            print("[7/7] Waiting for download to complete ...")

            downloaded_file = None
            timeout_seconds = 120
            end_time = time.time() + timeout_seconds

            while time.time() < end_time:
                current_files = set(os.listdir(download_folder))
                new_files = current_files - before_files

                temp_files = [
                    file_name
                    for file_name in current_files
                    if file_name.lower().endswith((".crdownload", ".tmp"))
                ]

                completed_files = [
                    os.path.join(download_folder, file_name)
                    for file_name in new_files
                    if not file_name.lower().endswith((".crdownload", ".tmp"))
                ]

                if completed_files and not temp_files:
                    latest_file = max(completed_files, key=os.path.getmtime)

                    size_1 = os.path.getsize(latest_file)
                    time.sleep(2)
                    size_2 = os.path.getsize(latest_file)

                    if size_1 == size_2 and size_2 > 0:
                        downloaded_file = latest_file
                        print(f"  Download completed: {os.path.basename(downloaded_file)}")
                        break

                time.sleep(2)

            if not downloaded_file:
                log_error(
                    ERROR_CODES["download_error"],
                    "Timed out waiting for completed Quartz download.",
                    script_name
                )
                print("Timed out waiting for completed Quartz download.")
                driver.quit()
                return None

            print("  Download folder contents:")

            if os.path.exists(download_folder):
                files = os.listdir(download_folder)

                if not files:
                    print("    No files found.")
                else:
                    for f in files:
                        fpath = os.path.join(download_folder, f)

                        if os.path.isfile(fpath):
                            print(f"    {f} ({os.path.getsize(fpath):,} bytes)")

            log_success()
            return download_folder

        except Exception as e:
            log_error(
                ERROR_CODES["download_error"],
                f"Quartz download wait/check failed: {e}",
                script_name
            )
            print(f"Quartz download wait/check failed: {e}")
            driver.quit()
            return None

    except Exception as e:
        log_error(
            ERROR_CODES["general_error"],
            f"BOB Quartz handler failed: {e}",
            script_name
        )
        print(f"BOB Quartz handler failed: {e}")
        driver.quit()
        return None

######################################################################################################################################################################

def run_acu_zing(driver, matrix_row, date_info):
    print("Running ACU Zing (EvolveNXT) handler...")

    download_folder = os.path.normpath(matrix_row["download_path"])
    script_name = matrix_row["script_name"]

    try:
        # STEP 1: Login
        driver.get(matrix_row["source_url"])
        time.sleep(5)

        if matrix_row["source_login"].upper() == "YES":
            print("Logging in...")

            username_field = WebDriverWait(driver, 30).until(
                EC.element_to_be_clickable((By.ID, "login_id"))
            )
            username_field.clear()
            username_field.send_keys(matrix_row["source_email"])

            password_field = WebDriverWait(driver, 30).until(
                EC.element_to_be_clickable((By.ID, "password"))
            )
            password_field.clear()
            password_field.send_keys(matrix_row["source_password"])

            login_btn = WebDriverWait(driver, 10).until(
                EC.element_to_be_clickable((By.ID, "submit"))
            )
            login_btn.click()

            print("Login submitted.")
            time.sleep(10)

        # STEP 2: Handle password expiry modal if present
        print("Checking for password expiry modal...")

        try:
            continue_login_btn = WebDriverWait(driver, 5).until(
                EC.element_to_be_clickable((By.ID, "goToDomain"))
            )
            continue_login_btn.click()
            print("Password expiry modal dismissed — clicked 'Continue with Login'.")
            time.sleep(10)
        except TimeoutException:
            print("No password expiry modal.")

        # STEP 3: Click domain/portal card if present
        print("Checking for domain selection...")

        try:
            domain_card = WebDriverWait(driver, 5).until(
                EC.element_to_be_clickable(
                    (By.XPATH, "//div[contains(@onclick, 'doLogin') and contains(@onclick, 'zing.evolvenxt.com')]"))
            )
            domain_card.click()
            print("Domain card clicked.")
            time.sleep(5)

            if len(driver.window_handles) > 1:
                driver.switch_to.window(driver.window_handles[-1])
                print("Switched to Zing tab.")
                time.sleep(10)
        except TimeoutException:
            print("No domain selection screen.")

        # STEP 4: Click "My Downline Brokers"
        print("Clicking My Downline Brokers...")

        downline_link = WebDriverWait(driver, 30).until(
            EC.element_to_be_clickable((By.CSS_SELECTOR, "a[href='/portal/mpc_detail.htm']"))
        )
        downline_link.click()
        time.sleep(3)

        # STEP 4: Click "Broker Credentials"
        print("Clicking Broker Credentials...")

        broker_creds = WebDriverWait(driver, 10).until(
            EC.element_to_be_clickable((By.CSS_SELECTOR, "a.collapse-item[href='/portal/mpc_detail.htm']"))
        )
        broker_creds.click()
        time.sleep(5)

        # STEP 5: Click Search
        print("Clicking Search...")

        search_btn = WebDriverWait(driver, 10).until(
            EC.element_to_be_clickable((By.ID, "search_button"))
        )
        search_btn.click()

        print("Waiting for results...")
        time.sleep(15)

        WebDriverWait(driver, 60).until(
            EC.presence_of_element_located((By.ID, "my_producers"))
        )

        try:
            info = driver.find_element(By.ID, "my_producers_info").text
            print(f"Results: {info}")
        except Exception:
            print("Results loaded.")

        time.sleep(3)

        # STEP 6: Download Rep Status
        print("Clicking Download Rep Status...")

        download_btn = WebDriverWait(driver, 15).until(
            EC.element_to_be_clickable((By.ID, "producer_status"))
        )
        download_btn.click()

        print("Waiting for download...")
        time.sleep(30)

        log_success()
        return download_folder

    except Exception as e:
        log_error(
            ERROR_CODES["general_error"],
            f"ACU Zing handler failed: {e}",
            script_name
        )
        print(f"ACU Zing handler failed: {e}")
        driver.quit()
        return None


######################################################################################################################################################################


def run_bob_medica_individual(driver, matrix_row, date_info):
    print("Running BOB Medica Individual Health handler...")

    download_folder = os.path.normpath(matrix_row["download_path"])
    script_name = matrix_row["script_name"]

    try:
        # STEP 1: Login
        driver.get("https://www.ociservices.com/login/")
        time.sleep(5)

        if matrix_row["source_login"].upper() == "YES":
            print("Logging in...")

            username_field = WebDriverWait(driver, 60).until(
                EC.presence_of_element_located((By.XPATH, "//input[@name='txtUsername']"))
            )
            username_field.send_keys(matrix_row["source_email"])
            time.sleep(2)

            password_field = WebDriverWait(driver, 60).until(
                EC.presence_of_element_located((By.XPATH, "//input[@name='txtPassword']"))
            )
            password_field.send_keys(matrix_row["source_password"])
            time.sleep(2)

            login_btn = WebDriverWait(driver, 60).until(
                EC.element_to_be_clickable((By.CSS_SELECTOR, "input[name='btnLogin']"))
            )
            login_btn.click()

            print("Login submitted.")
            time.sleep(10)

        # STEP 2: Navigate to My Policies
        print("Navigating to My Policies...")

        driver.get("https://agb.ociservices.com/Individual/Policy")
        time.sleep(15)

        # STEP 3: Click Export to Excel
        print("Clicking Export to Excel...")

        export_btn = WebDriverWait(driver, 30).until(
            EC.element_to_be_clickable((By.XPATH,
                                        "//a[contains(@method, 'ExportToExcel')]//img[@src='https://agb.ociservices.com/images/gridexcel.png']"
                                        ))
        )
        export_btn.click()
        time.sleep(5)

        # STEP 4: Handle Export dialog — click "All Records"
        print("Clicking All Records...")

        all_records_btn = WebDriverWait(driver, 15).until(
            EC.element_to_be_clickable((By.XPATH,
                                        "//div[contains(@class, 'ui-dialog')]//button[contains(text(), 'All Records')]"
                                        ))
        )
        all_records_btn.click()

        print("Waiting for download...")
        time.sleep(30)

        log_success()
        return download_folder

    except Exception as e:
        log_error(
            ERROR_CODES["general_error"],
            f"BOB Medica Individual handler failed: {e}",
            script_name
        )
        print(f"BOB Medica Individual handler failed: {e}")
        driver.quit()
        return None


######################################################################################################################################################################


def _healthspring_bob_login(driver, matrix_row):
    """Shared login for Healthspring BOB ACA and MDC — evolvenxt login + password modal + swap Agility + Agency Login."""
    driver.get(matrix_row["source_url"])
    time.sleep(5)

    if matrix_row["source_login"].upper() == "YES":
        print("Logging in...")

        username_field = WebDriverWait(driver, 30).until(
            EC.element_to_be_clickable((By.ID, "login_id"))
        )
        username_field.clear()
        username_field.send_keys(matrix_row["source_email"])

        password_field = WebDriverWait(driver, 30).until(
            EC.element_to_be_clickable((By.ID, "password"))
        )
        password_field.clear()
        password_field.send_keys(matrix_row["source_password"])

        login_btn = WebDriverWait(driver, 10).until(
            EC.element_to_be_clickable((By.ID, "submit"))
        )
        login_btn.click()
        print("Login submitted.")
        time.sleep(10)

    # Handle password expiry modal
    print("Checking for password expiry modal...")
    try:
        continue_login_btn = WebDriverWait(driver, 5).until(
            EC.element_to_be_clickable((By.ID, "goToDomain"))
        )
        continue_login_btn.click()
        print("Password expiry modal dismissed.")
        time.sleep(10)
    except TimeoutException:
        print("No password expiry modal.")

    # Swap to Agility
    print("Swapping to Agility...")
    user_dropdown = WebDriverWait(driver, 30).until(
        EC.element_to_be_clickable((By.ID, "userDropdown"))
    )
    user_dropdown.click()
    time.sleep(3)

    agility_swap = WebDriverWait(driver, 10).until(
        EC.element_to_be_clickable((By.CSS_SELECTOR, "a[onclick*='switchToAgency']"))
    )
    driver.execute_script("arguments[0].click();", agility_swap)
    print("Swapped to Agility.")
    time.sleep(10)

    # Agency Login modal
    print("Checking for Agency Login modal...")
    try:
        agency_login_btn = WebDriverWait(driver, 15).until(
            EC.element_to_be_clickable(
                (By.XPATH, "//button[contains(@onclick, 'PickUser') and contains(text(), 'Agency Login')]"))
        )
        agency_login_btn.click()
        print("Clicked Agency Login.")
        time.sleep(10)
    except TimeoutException:
        print("No Agency Login prompt.")
        time.sleep(5)


def run_bob_healthspring_aca(driver, matrix_row, date_info):
    print("Running BOB Healthspring ACA handler...")

    download_folder = os.path.normpath(matrix_row["download_path"])
    script_name = matrix_row["script_name"]

    try:
        _healthspring_bob_login(driver, matrix_row)

        # Book of Business
        print("Clicking Book of Business...")
        bob_menu = WebDriverWait(driver, 30).until(
            EC.element_to_be_clickable((By.CSS_SELECTOR, "a[href='/portal/member_search.htm']"))
        )
        bob_menu.click()
        time.sleep(3)

        # Search IFP BOB
        print("Clicking Search IFP BOB...")
        search_ifp = WebDriverWait(driver, 10).until(
            EC.element_to_be_clickable((By.CSS_SELECTOR, "a.collapse-item[href='/portal/policy_search.htm']"))
        )
        search_ifp.click()
        time.sleep(10)

        # Search
        print("Clicking Search...")
        search_btn = WebDriverWait(driver, 60).until(
            EC.element_to_be_clickable((By.ID, "submit"))
        )
        search_btn.click()
        print("Waiting for results...")
        time.sleep(60)

        # Download
        print("Clicking Download...")
        download_btn = WebDriverWait(driver, 60).until(
            EC.element_to_be_clickable((By.ID, "download"))
        )
        download_btn.click()
        print("Waiting for file download...")
        time.sleep(120)

        log_success()
        return download_folder

    except Exception as e:
        log_error(
            ERROR_CODES["general_error"],
            f"BOB Healthspring ACA failed: {e}",
            script_name
        )
        print(f"BOB Healthspring ACA failed: {e}")
        driver.quit()
        return None


def run_bob_healthspring_mdc(driver, matrix_row, date_info):
    print("Running BOB Healthspring MDC handler...")

    download_folder = os.path.normpath(matrix_row["download_path"])
    script_name = matrix_row["script_name"]

    try:
        _healthspring_bob_login(driver, matrix_row)

        # Book of Business
        print("Clicking Book of Business...")
        bob_menu = WebDriverWait(driver, 30).until(
            EC.element_to_be_clickable((By.CSS_SELECTOR, "a[href='/portal/member_search.htm']"))
        )
        bob_menu.click()
        time.sleep(3)

        # Search MA BOB
        print("Clicking Search MA BOB...")
        search_ma = WebDriverWait(driver, 10).until(
            EC.element_to_be_clickable((By.CSS_SELECTOR, "a.collapse-item[href='/portal/member_search.htm']"))
        )
        search_ma.click()
        time.sleep(10)

        # Search
        print("Clicking Search...")
        search_btn = WebDriverWait(driver, 60).until(
            EC.element_to_be_clickable((By.ID, "submit"))
        )
        search_btn.click()
        print("Waiting for results...")
        time.sleep(60)

        # Wait for results
        WebDriverWait(driver, 60).until(
            EC.presence_of_element_located((By.ID, "portal_members"))
        )
        try:
            info = driver.find_element(By.ID, "portal_members_info").text
            print(f"Results: {info}")
        except Exception:
            print("Results loaded.")
        time.sleep(3)

        # Download
        print("Clicking Download...")
        download_btn = WebDriverWait(driver, 60).until(
            EC.element_to_be_clickable((By.ID, "dButton"))
        )
        download_btn.click()
        print("Waiting for file download...")
        time.sleep(120)

        log_success()
        return download_folder

    except Exception as e:
        log_error(
            ERROR_CODES["general_error"],
            f"BOB Healthspring MDC failed: {e}",
            script_name
        )
        print(f"BOB Healthspring MDC failed: {e}")
        driver.quit()
        return None


######################################################################################################################################################################


def run_acu_wellcare(driver, matrix_row, date_info):
    print("Running ACU Wellcare handler...")

    download_folder = os.path.normpath(matrix_row["download_path"])
    script_name = matrix_row["script_name"]

    try:
        # STEP 1: Navigate to Wellcare and click SSO link
        driver.get(matrix_row["source_url"])
        time.sleep(5)

        sso_link = WebDriverWait(driver, 30).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, "a[href='https://desktop.pingone.com/cnc-workbench-brk']"))
        )
        driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", sso_link)
        time.sleep(2)
        driver.execute_script("arguments[0].click();", sso_link)
        time.sleep(3)

        # STEP 2: Handle "leaving site" modal
        print("Handling leaving site modal...")

        continue_btn = WebDriverWait(driver, 10).until(
            EC.element_to_be_clickable((By.CSS_SELECTOR, "a.modelOptYes"))
        )
        continue_btn.click()
        print("Continue clicked.")
        time.sleep(5)

        # Switch to new window
        if len(driver.window_handles) > 1:
            driver.switch_to.window(driver.window_handles[-1])
            print("Switched to PingOne window.")
            time.sleep(10)

        # STEP 3: Enter credentials
        if matrix_row["source_login"].upper() == "YES":
            print("Entering credentials...")

            username_field = WebDriverWait(driver, 30).until(
                EC.element_to_be_clickable((By.ID, "username"))
            )
            username_field.clear()
            username_field.send_keys(matrix_row["source_email"])

            password_field = WebDriverWait(driver, 30).until(
                EC.element_to_be_clickable((By.ID, "password"))
            )
            password_field.clear()
            password_field.send_keys(matrix_row["source_password"])

            signin_btn = WebDriverWait(driver, 10).until(
                EC.element_to_be_clickable((By.CSS_SELECTOR, "button[data-id='submit-button']"))
            )
            signin_btn.click()
            print("Sign On clicked.")
            time.sleep(10)

        # STEP 4: Mark old OTP emails, click Email OTP tile
        print("Selecting Email OTP method...")

        mark_matching_as_read(
            sender="noreply@pingidentity.com",
            subject="PingOne: New authentication request",
            mailbox="support@enrollinsurance.com",
        )

        email_tile = WebDriverWait(driver, 15).until(
            EC.element_to_be_clickable((By.XPATH,
                                        "//div[contains(@class, 'tile-button') and .//div[contains(text(), 'su****@enrollinsurance.com')]]"
                                        ))
        )
        email_tile.click()

        otp_sent_at = datetime.now(timezone.utc)
        print("Email OTP selected.")
        time.sleep(3)

        # STEP 5: Poll for OTP code (with resend fallback)
        print("Polling for OTP code...")

        code = None
        try:
            code = fetch_otp_code(
                sender="noreply@pingidentity.com",
                subject="PingOne: New authentication request",
                mailbox="support@enrollinsurance.com",
                since_dt_utc=otp_sent_at,
                poll_seconds=30,
            )
        except RuntimeError:
            print("No code after 30s, clicking resend...")

            resend_clicked = False
            resend_selectors = [
                "//a[@data-id='resend-passcode-button']",
                "//a[contains(text(), 'Resend passcode')]",
                "//a[contains(text(), 'Resend')]",
                "//button[contains(text(), 'Resend')]",
            ]

            for sel in resend_selectors:
                try:
                    resend_btn = driver.find_element(By.XPATH, sel)
                    if resend_btn.is_displayed():
                        driver.execute_script("arguments[0].click();", resend_btn)
                        resend_clicked = True
                        print(f"Clicked resend: {sel}")
                        break
                except Exception:
                    continue

            if not resend_clicked:
                print("WARNING: Could not find resend button.")

            otp_sent_at = datetime.now(timezone.utc)
            time.sleep(3)

            code = fetch_otp_code(
                sender="noreply@pingidentity.com",
                subject="PingOne: New authentication request",
                mailbox="support@enrollinsurance.com",
                since_dt_utc=otp_sent_at,
                poll_seconds=90,
            )

        # STEP 6: Enter OTP + Sign On
        print(f"Entering OTP: {code}")

        otp_input = WebDriverWait(driver, 15).until(
            EC.element_to_be_clickable((By.ID, "otp-code"))
        )
        otp_input.clear()
        otp_input.send_keys(code)
        time.sleep(1)

        signon_btn = WebDriverWait(driver, 10).until(
            EC.element_to_be_clickable((By.ID, "signon"))
        )
        signon_btn.click()
        print("OTP submitted.")
        time.sleep(20)

        # STEP 7: Click Centene Workbench app — opens new tab
        print("Clicking Centene Workbench...")

        workbench_selectors = [
            (By.XPATH, "//span[@class='app-name' and text()='Centene Workbench']/ancestor::a"),
            (By.XPATH, "//span[@title='Centene Workbench']/ancestor::a"),
            (By.CSS_SELECTOR, "a.app.loaded"),
        ]

        clicked = False
        for by, sel in workbench_selectors:
            try:
                el = WebDriverWait(driver, 10).until(
                    EC.presence_of_element_located((by, sel))
                )
                driver.execute_script("arguments[0].click();", el)
                clicked = True
                break
            except TimeoutException:
                continue

        if not clicked:
            raise RuntimeError("Could not find Centene Workbench link")

        print("Centene Workbench clicked.")
        time.sleep(5)

        # Switch to new tab
        if len(driver.window_handles) > 2:
            driver.switch_to.window(driver.window_handles[-1])
            print("Switched to Workbench tab.")
            time.sleep(20)

        # STEP 8: Click user dropdown + swap to Agility
        print("Swapping to Agility...")

        user_dropdown = WebDriverWait(driver, 30).until(
            EC.element_to_be_clickable((By.ID, "userDropdown"))
        )
        user_dropdown.click()
        time.sleep(3)

        agility_swap = WebDriverWait(driver, 10).until(
            EC.element_to_be_clickable((By.CSS_SELECTOR, "a[onclick*='switchToAgency']"))
        )
        driver.execute_script("arguments[0].click();", agility_swap)
        print("Swapped to Agility.")
        time.sleep(10)

        # STEP 9: Click "My Downline Brokers"
        print("Clicking My Downline Brokers...")

        downline_link = WebDriverWait(driver, 30).until(
            EC.element_to_be_clickable((By.ID, "menu_header_15"))
        )
        downline_link.click()
        time.sleep(3)

        # STEP 10: Click "Broker Credentials"
        print("Clicking Broker Credentials...")

        broker_creds = WebDriverWait(driver, 10).until(
            EC.element_to_be_clickable((By.ID, "menu_header_15_31"))
        )
        broker_creds.click()
        time.sleep(5)

        # STEP 11: Click Search
        print("Clicking Search...")

        search_btn = WebDriverWait(driver, 10).until(
            EC.element_to_be_clickable((By.ID, "search_button"))
        )
        search_btn.click()

        print("Waiting for results...")
        time.sleep(15)

        WebDriverWait(driver, 60).until(
            EC.presence_of_element_located((By.ID, "my_producers"))
        )

        try:
            info = driver.find_element(By.ID, "my_producers_info").text
            print(f"Results: {info}")
        except Exception:
            print("Results loaded.")

        time.sleep(3)

        # STEP 12: Download Broker Status
        print("Clicking Download Broker Status...")

        download_btn = WebDriverWait(driver, 15).until(
            EC.element_to_be_clickable((By.ID, "producer_status"))
        )
        download_btn.click()

        print("Waiting for download...")
        time.sleep(30)

        log_success()
        return download_folder

    except Exception as e:
        log_error(
            ERROR_CODES["general_error"],
            f"ACU Wellcare handler failed: {e}",
            script_name
        )
        print(f"ACU Wellcare handler failed: {e}")
        driver.quit()
        return None


######################################################################################################################################################################

def run_bob_wellcare(driver, matrix_row, date_info):
    print("Running BOB Wellcare handler...")

    download_folder = os.path.normpath(matrix_row["download_path"])
    script_name = matrix_row["script_name"]

    try:
        # STEP 1: Navigate to WellCare and click SSO link
        print("[1/12] Opening WellCare Broker Resources ...")
        driver.get(matrix_row["source_url"])
        time.sleep(5)

        sso_link = WebDriverWait(driver, 30).until(
            EC.presence_of_element_located(
                (By.CSS_SELECTOR, "a[href='https://desktop.pingone.com/cnc-workbench-brk']")
            )
        )
        driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", sso_link)
        time.sleep(2)
        driver.execute_script("arguments[0].click();", sso_link)
        time.sleep(3)

        # STEP 2: Handle leaving site modal
        print("[2/12] Handling leaving site modal ...")

        continue_btn = WebDriverWait(driver, 10).until(
            EC.element_to_be_clickable((By.CSS_SELECTOR, "a.modelOptYes"))
        )
        continue_btn.click()
        print("Continue clicked.")
        time.sleep(5)

        if len(driver.window_handles) > 1:
            driver.switch_to.window(driver.window_handles[-1])
            print("Switched to PingOne window.")
            time.sleep(10)

        # STEP 3: Enter credentials
        if matrix_row["source_login"].upper() == "YES":
            print("[3/12] Entering credentials ...")

            username_field = WebDriverWait(driver, 30).until(
                EC.element_to_be_clickable((By.ID, "username"))
            )
            username_field.clear()
            username_field.send_keys(matrix_row["source_email"])

            password_field = WebDriverWait(driver, 30).until(
                EC.element_to_be_clickable((By.ID, "password"))
            )
            password_field.clear()
            password_field.send_keys(matrix_row["source_password"])

            signin_btn = WebDriverWait(driver, 10).until(
                EC.element_to_be_clickable((By.CSS_SELECTOR, "button[data-id='submit-button']"))
            )
            signin_btn.click()
            print("Sign On clicked.")
            time.sleep(10)

        # STEP 4: Select Email OTP method
        print("[4/12] Selecting Email OTP method ...")

        mark_matching_as_read(
            sender="noreply@pingidentity.com",
            subject="PingOne: New authentication request",
            mailbox="support@enrollinsurance.com",
        )

        email_tile = WebDriverWait(driver, 15).until(
            EC.element_to_be_clickable((
                By.XPATH,
                "//div[contains(@class, 'tile-button') and .//div[contains(text(), 'su****@enrollinsurance.com')]]"
            ))
        )
        email_tile.click()

        otp_sent_at = datetime.now(timezone.utc)
        print("Email OTP selected.")
        time.sleep(3)

        # STEP 5: Poll for OTP code
        print("[5/12] Polling for OTP code ...")

        try:
            code = fetch_otp_code(
                sender="noreply@pingidentity.com",
                subject="PingOne: New authentication request",
                mailbox="support@enrollinsurance.com",
                since_dt_utc=otp_sent_at,
                poll_seconds=30,
            )
        except RuntimeError:
            print("No code after 30s, clicking resend ...")

            resend_clicked = False
            resend_selectors = [
                "//a[@data-id='resend-passcode-button']",
                "//a[contains(text(), 'Resend passcode')]",
                "//a[contains(text(), 'Resend')]",
                "//button[contains(text(), 'Resend')]",
            ]

            for sel in resend_selectors:
                try:
                    resend_btn = driver.find_element(By.XPATH, sel)
                    if resend_btn.is_displayed():
                        driver.execute_script("arguments[0].click();", resend_btn)
                        resend_clicked = True
                        print(f"Clicked resend: {sel}")
                        break
                except Exception:
                    continue

            if not resend_clicked:
                print("WARNING: Could not find resend button.")

            otp_sent_at = datetime.now(timezone.utc)
            time.sleep(3)

            code = fetch_otp_code(
                sender="noreply@pingidentity.com",
                subject="PingOne: New authentication request",
                mailbox="support@enrollinsurance.com",
                since_dt_utc=otp_sent_at,
                poll_seconds=90,
            )

        # STEP 6: Enter OTP and Sign On
        print("[6/12] Entering OTP ...")

        otp_input = WebDriverWait(driver, 15).until(
            EC.element_to_be_clickable((By.ID, "otp-code"))
        )
        otp_input.clear()
        otp_input.send_keys(code)
        time.sleep(1)

        signon_btn = WebDriverWait(driver, 10).until(
            EC.element_to_be_clickable((By.ID, "signon"))
        )
        signon_btn.click()
        print("OTP submitted.")
        time.sleep(20)

        # STEP 7: Open Centene Workbench if PingOne app dashboard appears
        print("[7/12] Opening Centene Workbench ...")

        workbench_opened = False

        try:
            WebDriverWait(driver, 15).until(
                EC.presence_of_element_located((By.ID, "userDropdown"))
            )
            print("Already inside Workbench.")
            workbench_opened = True
        except TimeoutException:
            workbench_selectors = [
                (By.XPATH, "//span[@class='app-name' and text()='Centene Workbench']/ancestor::a"),
                (By.XPATH, "//span[@title='Centene Workbench']/ancestor::a"),
                (By.CSS_SELECTOR, "a.app.loaded"),
            ]

            for by, sel in workbench_selectors:
                try:
                    el = WebDriverWait(driver, 10).until(
                        EC.presence_of_element_located((by, sel))
                    )
                    driver.execute_script("arguments[0].click();", el)
                    workbench_opened = True
                    print("Centene Workbench clicked.")
                    break
                except TimeoutException:
                    continue

            if not workbench_opened:
                raise RuntimeError("Could not find Centene Workbench link")

            time.sleep(5)

            if len(driver.window_handles) > 2:
                driver.switch_to.window(driver.window_handles[-1])
                print("Switched to Workbench tab.")
                time.sleep(20)

        # STEP 8: Switch profile to Agility Insurance Services LLC
        print("[8/12] Switching profile to Agility ...")

        user_dropdown = WebDriverWait(driver, 30).until(
            EC.element_to_be_clickable((By.ID, "userDropdown"))
        )
        user_dropdown.click()
        time.sleep(3)

        agility_swap = WebDriverWait(driver, 10).until(
            EC.element_to_be_clickable((By.CSS_SELECTOR, "a[onclick*='switchToAgency']"))
        )
        driver.execute_script("arguments[0].click();", agility_swap)
        print("Swapped to Agility.")
        time.sleep(10)

        # STEP 9: Open Book of Business
        print("[9/12] Opening Book of Business ...")

        try:
            bob_menu = WebDriverWait(driver, 30).until(
                EC.element_to_be_clickable((By.XPATH, "//a[contains(., 'Book of Business')]"))
            )
            bob_menu.click()
            print("Book of Business clicked.")
            time.sleep(3)
        except TimeoutException:
            print("Book of Business parent menu not found or already open.")

        # STEP 10: Run Book of Business search
        print("[10/12] Running Book of Business search ...")

        search_btn = WebDriverWait(driver, 60).until(
            EC.element_to_be_clickable((
                By.XPATH,
                "//form[@id='member_search_form']//button[@id='submit']"
            ))
        )

        driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", search_btn)
        time.sleep(1)
        driver.execute_script("arguments[0].click();", search_btn)

        print("Search submitted. Waiting for results...")

        download_btn = WebDriverWait(driver, 120).until(
            EC.element_to_be_clickable((
                By.XPATH,
                "//form[@id='member_search_form']//button[@id='dButton']"
            ))
        )

        print("Search results loaded.")

        try:
            WebDriverWait(driver, 30).until(
                EC.presence_of_element_located((By.ID, "portal_members"))
            )

            info = driver.find_element(By.ID, "portal_members_info").text
            print(f"Results: {info}")

        except Exception:
            print("Results table detected without summary text.")

        time.sleep(2)

        # STEP 11: Download Book of Business report
        print("[11/12] Downloading Book of Business report ...")

        driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", download_btn)
        time.sleep(1)
        driver.execute_script("arguments[0].click();", download_btn)

        print("Waiting for download to complete...")
        time.sleep(60)

        # STEP 12: Verify downloaded file
        print("[12/12] Checking downloaded files ...")

        downloaded_files = []
        if os.path.exists(download_folder):
            for f in os.listdir(download_folder):
                fpath = os.path.join(download_folder, f)
                if os.path.isfile(fpath):
                    downloaded_files.append(f)
                    print(f"    {f} ({os.path.getsize(fpath):,} bytes)")

        if not downloaded_files:
            raise RuntimeError("No downloaded files found after WellCare BOB export.")

        log_success()
        return download_folder

    except Exception as e:
        log_error(
            ERROR_CODES["general_error"],
            f"BOB Wellcare handler failed: {e}",
            script_name
        )
        print(f"BOB Wellcare handler failed: {e}")
        driver.quit()
        return None

######################################################################################################################################################################


def run_acu_verda(driver, matrix_row, date_info):
    print("Running ACU Verda Healthcare handler...")

    download_folder = os.path.normpath(matrix_row["download_path"])
    script_name = matrix_row["script_name"]

    try:
        # STEP 1: Login
        driver.get(matrix_row["source_url"])
        time.sleep(5)

        if matrix_row["source_login"].upper() == "YES":
            print("Logging in...")

            email_field = WebDriverWait(driver, 30).until(
                EC.element_to_be_clickable((By.ID, "email"))
            )
            email_field.clear()
            email_field.send_keys(matrix_row["source_email"])

            password_field = WebDriverWait(driver, 30).until(
                EC.element_to_be_clickable((By.ID, "password"))
            )
            password_field.clear()
            password_field.send_keys(matrix_row["source_password"])

            login_btn = WebDriverWait(driver, 10).until(
                EC.element_to_be_clickable((By.XPATH, "//button[@type='submit' and contains(text(), 'Log in')]"))
            )
            login_btn.click()

            print("Login submitted.")
            time.sleep(10)

        # STEP 2: Click "Agency"
        print("Clicking Agency...")

        agency_link = WebDriverWait(driver, 30).until(
            EC.element_to_be_clickable((By.CSS_SELECTOR, "a[href='https://broker.verdahealthcare.com/agency/admin']"))
        )
        agency_link.click()

        print("Agency page loading...")
        time.sleep(10)

        # STEP 3: Click "Export Agents"
        print("Clicking Export Agents...")

        export_btn = WebDriverWait(driver, 30).until(
            EC.element_to_be_clickable((By.CSS_SELECTOR, "a[wire\\:click='exportAgents']"))
        )
        export_btn.click()

        print("Export triggered, waiting for download...")
        time.sleep(30)

        log_success()
        return download_folder

    except Exception as e:
        log_error(
            ERROR_CODES["general_error"],
            f"ACU Verda Healthcare handler failed: {e}",
            script_name
        )
        print(f"ACU Verda Healthcare handler failed: {e}")
        driver.quit()
        return None


######################################################################################################################################################################


def run_acu_scan(driver, matrix_row, date_info):
    print("Running ACU SCAN Health Plan handler...")

    download_folder = os.path.normpath(matrix_row["download_path"])
    script_name = matrix_row["script_name"]

    try:
        # STEP 1: Login via Okta
        driver.get(matrix_row["source_url"])
        time.sleep(5)

        if matrix_row["source_login"].upper() == "YES":
            print("Logging in via Okta...")

            username_field = WebDriverWait(driver, 30).until(
                EC.element_to_be_clickable((By.ID, "okta-signin-username"))
            )
            username_field.clear()
            username_field.send_keys(matrix_row["source_email"])

            password_field = WebDriverWait(driver, 30).until(
                EC.element_to_be_clickable((By.ID, "okta-signin-password"))
            )
            password_field.clear()
            password_field.send_keys(matrix_row["source_password"])

            signin_btn = WebDriverWait(driver, 10).until(
                EC.element_to_be_clickable((By.ID, "okta-signin-submit"))
            )
            signin_btn.click()

            print("Login submitted.")
            time.sleep(10)

        # STEP 2: Handle security question if present
        print("Checking for security question...")

        try:
            answer_field = WebDriverWait(driver, 10).until(
                EC.element_to_be_clickable((By.CSS_SELECTOR, "input[name='answer']"))
            )
            answer_field.clear()
            answer_field.send_keys("Ruby")

            verify_btn = WebDriverWait(driver, 5).until(
                EC.element_to_be_clickable((By.CSS_SELECTOR, "input[type='submit'][value='Verify']"))
            )
            verify_btn.click()
            print("Security question answered.")
            time.sleep(10)
        except TimeoutException:
            print("No security question, continuing.")

        # STEP 3: Click Producer Dashboard app
        print("Clicking Producer Dashboard...")

        dashboard_link = WebDriverWait(driver, 30).until(
            EC.element_to_be_clickable((By.CSS_SELECTOR, "a[data-se='app-card'][aria-label*='Producer Dashboard']"))
        )
        dashboard_link.click()
        print("Producer Dashboard clicked.")
        time.sleep(5)

        # Switch to new tab
        if len(driver.window_handles) > 1:
            driver.switch_to.window(driver.window_handles[-1])
            print("Switched to new tab.")
            time.sleep(15)

        # STEP 4: Click user dropdown + swap to Agility
        print("Swapping to Agility Insurance Services LLC...")

        user_dropdown = WebDriverWait(driver, 30).until(
            EC.element_to_be_clickable((By.ID, "userDropdown"))
        )
        user_dropdown.click()
        time.sleep(3)

        agility_swap = WebDriverWait(driver, 10).until(
            EC.element_to_be_clickable((By.CSS_SELECTOR, "a[onclick*='switchToAgency']"))
        )
        driver.execute_script("arguments[0].click();", agility_swap)
        print("Swapped to Agility.")
        time.sleep(10)

        # STEP 5: Click "My Downline Brokers"
        print("Clicking My Downline Brokers...")
        print("Before My Downline Brokers URL:", driver.current_url)
        print("Before My Downline Brokers title:", driver.title)

        downline_link = WebDriverWait(driver, 30).until(
            EC.presence_of_element_located((
                By.XPATH,
                "//a[contains(@href, 'mpc_detail.htm') or contains(normalize-space(.), 'My Downline Brokers')]"
            ))
        )

        try:
            downline_link.click()
        except Exception:
            print("Normal My Downline Brokers click failed. Trying JavaScript click.")
            driver.execute_script("arguments[0].click();", downline_link)

        print("My Downline Brokers clicked.")
        time.sleep(3)

        print("After My Downline Brokers URL:", driver.current_url)
        print("After My Downline Brokers title:", driver.title)

        # STEP 6: Click "Broker Credentials"
        print("Clicking Broker Credentials...")

        broker_creds = WebDriverWait(driver, 15).until(
            EC.presence_of_element_located((
                By.XPATH,
                "//a[contains(normalize-space(.), 'Broker Credentials')]"
                " | //a[contains(@class, 'collapse-item') and contains(@href, 'mpc_detail.htm')]"
            ))
        )

        try:
            broker_creds.click()
        except Exception:
            print("Normal Broker Credentials click failed. Trying JavaScript click.")
            driver.execute_script("arguments[0].click();", broker_creds)

        print("Broker Credentials clicked.")
        time.sleep(5)

        print("After Broker Credentials URL:", driver.current_url)
        print("After Broker Credentials title:", driver.title)

        # STEP 7: Click Search
        print("Clicking Search...")

        search_btn = WebDriverWait(driver, 10).until(
            EC.element_to_be_clickable((By.ID, "search_button"))
        )
        search_btn.click()

        print("Waiting for results...")
        time.sleep(15)

        WebDriverWait(driver, 60).until(
            EC.presence_of_element_located((By.ID, "my_producers"))
        )

        try:
            info = driver.find_element(By.ID, "my_producers_info").text
            print(f"Results: {info}")
        except Exception:
            print("Results loaded.")

        time.sleep(3)

        # STEP 8: Download Rep Status
        print("Clicking Download Rep Status...")

        download_btn = WebDriverWait(driver, 15).until(
            EC.element_to_be_clickable((By.ID, "producer_status"))
        )
        download_btn.click()

        print("Waiting for download...")
        time.sleep(30)

        log_success()
        return download_folder

    except Exception as e:
        log_error(
            ERROR_CODES["general_error"],
            f"ACU SCAN Health Plan handler failed: {e}",
            script_name
        )
        print(f"ACU SCAN Health Plan handler failed: {e}")
        driver.quit()
        return None

######################################################################################################################################################################


def run_acu_healthspring(driver, matrix_row, date_info):
    print("Running ACU Healthspring handler...")

    download_folder = os.path.normpath(matrix_row["download_path"])
    script_name = matrix_row["script_name"]

    try:
        # STEP 1: Navigate and click Login
        driver.get(matrix_row["source_url"])
        time.sleep(5)

        login_btn = WebDriverWait(driver, 30).until(
            EC.element_to_be_clickable((By.CSS_SELECTOR, "button.login-button"))
        )
        login_btn.click()
        print("Login button clicked.")
        time.sleep(5)

        # STEP 2: Enter credentials + submit
        if matrix_row["source_login"].upper() == "YES":
            print("Entering credentials...")

            username_field = WebDriverWait(driver, 30).until(
                EC.element_to_be_clickable((By.ID, "username"))
            )
            username_field.clear()
            username_field.send_keys(matrix_row["source_email"])

            password_field = WebDriverWait(driver, 30).until(
                EC.element_to_be_clickable((By.ID, "password"))
            )
            password_field.clear()
            password_field.send_keys(matrix_row["source_password"])

            continue_btn = WebDriverWait(driver, 10).until(
                EC.element_to_be_clickable((By.CSS_SELECTOR, "button[data-action-button-primary='true']"))
            )
            continue_btn.click()
            print("Credentials submitted.")
            time.sleep(5)

        # STEP 3: Click "Try another method"
        print("Clicking 'Try another method'...")

        try_another = WebDriverWait(driver, 15).until(
            EC.element_to_be_clickable((By.CSS_SELECTOR, "button[value='pick-authenticator']"))
        )
        try_another.click()
        time.sleep(3)

        # STEP 4: Mark old OTP emails, then click "Email"
        print("Selecting Email method...")

        mark_matching_as_read(
            sender="notify@communications.healthspringforbrokers.com",
            subject="Your authentication code",
            mailbox="support@enrollinsurance.com",
        )

        email_btn = WebDriverWait(driver, 15).until(
            EC.element_to_be_clickable((By.CSS_SELECTOR, "button[value='email::1']"))
        )
        email_btn.click()

        otp_sent_at = datetime.now(timezone.utc)
        print("Email selected.")
        time.sleep(3)

        # STEP 5: Poll for OTP code
        print("Polling for OTP code...")

        code = fetch_otp_code(
            sender="notify@communications.healthspringforbrokers.com",
            subject="Your authentication code",
            mailbox="support@enrollinsurance.com",
            since_dt_utc=otp_sent_at,
            poll_seconds=120,
        )

        # STEP 6: Enter OTP + Continue
        print(f"Entering OTP: {code}")

        otp_input = WebDriverWait(driver, 15).until(
            EC.element_to_be_clickable((By.ID, "code"))
        )
        otp_input.clear()
        otp_input.send_keys(code)
        time.sleep(1)

        submit_btn = WebDriverWait(driver, 10).until(
            EC.element_to_be_clickable((By.CSS_SELECTOR, "button[data-action-button-primary='true']"))
        )
        submit_btn.click()
        print("OTP submitted.")
        time.sleep(15)

        # Refresh to ensure clean state after OTP
        driver.refresh()
        time.sleep(10)

        # STEP 7: Wait for and click "HealthSpring Supplemental" panel
        print("Clicking HealthSpring Supplemental...")

        hs_panel = WebDriverWait(driver, 30).until(
            EC.element_to_be_clickable((By.ID, "mat-expansion-panel-header-3"))
        )
        hs_panel.click()
        print("Panel clicked, waiting for expansion...")
        time.sleep(8)

        # STEP 8: Click "Contracting" — opens new tab
        print("Clicking Contracting...")

        try:
            contracting_link = WebDriverWait(driver, 15).until(
                EC.element_to_be_clickable(
                    (By.CSS_SELECTOR, "a[data-test-id='leftnav-submenu-hcsc-supplemental.title']"))
            )
            driver.execute_script("arguments[0].click();", contracting_link)
        except TimeoutException:
            contracting_link = WebDriverWait(driver, 10).until(
                EC.presence_of_element_located(
                    (By.XPATH, "//div[contains(@class, 'subMenu') and contains(text(), 'Contracting')]/ancestor::a"))
            )
            driver.execute_script("arguments[0].click();", contracting_link)
        print("Contracting clicked.")
        time.sleep(5)

        # Switch to the new tab
        if len(driver.window_handles) > 1:
            driver.switch_to.window(driver.window_handles[-1])
            print("Switched to Contracting tab.")
            time.sleep(20)

        # STEP 9: Click user dropdown → swap to Agility
        print("Swapping to Agility Insurance Services LLC...")

        user_dropdown = WebDriverWait(driver, 30).until(
            EC.element_to_be_clickable((By.ID, "userDropdown"))
        )
        user_dropdown.click()
        time.sleep(3)

        agility_swap = WebDriverWait(driver, 10).until(
            EC.element_to_be_clickable((By.CSS_SELECTOR, "a[onclick*='switchToAgency']"))
        )
        driver.execute_script("arguments[0].click();", agility_swap)
        print("Swapped to Agility.")
        time.sleep(10)

        # STEP 10: Click "My Downline Brokers"
        print("Clicking My Downline Brokers...")

        downline_link = WebDriverWait(driver, 30).until(
            EC.element_to_be_clickable((By.CSS_SELECTOR, "a[href='/portal/mpc_detail.htm']"))
        )
        downline_link.click()
        time.sleep(3)

        # STEP 11: Click "Broker Credentials"
        print("Clicking Broker Credentials...")

        broker_creds = WebDriverWait(driver, 10).until(
            EC.element_to_be_clickable((By.CSS_SELECTOR, "a.collapse-item[href='/portal/mpc_detail.htm']"))
        )
        broker_creds.click()
        time.sleep(5)

        # STEP 12: Click Search
        print("Clicking Search...")

        search_btn = WebDriverWait(driver, 10).until(
            EC.element_to_be_clickable((By.ID, "search_button"))
        )
        search_btn.click()

        print("Waiting for results...")
        time.sleep(15)

        WebDriverWait(driver, 60).until(
            EC.presence_of_element_located((By.ID, "my_producers"))
        )

        try:
            info = driver.find_element(By.ID, "my_producers_info").text
            print(f"Results: {info}")
        except Exception:
            print("Results loaded.")

        time.sleep(3)

        # STEP 13: Download Rep Status
        print("Clicking Download Rep Status...")

        download_btn = WebDriverWait(driver, 15).until(
            EC.element_to_be_clickable((By.ID, "producer_status"))
        )
        download_btn.click()

        print("Waiting for download...")
        time.sleep(30)

        log_success()
        return download_folder

    except Exception as e:
        log_error(
            ERROR_CODES["general_error"],
            f"ACU Healthspring handler failed: {e}",
            script_name
        )
        print(f"ACU Healthspring handler failed: {e}")
        driver.quit()
        return None


######################################################################################################################################################################


def run_acu_imperial(driver, matrix_row, date_info):
    print("Running ACU Imperial Health Plan handler...")

    download_folder = os.path.normpath(matrix_row["download_path"])
    script_name = matrix_row["script_name"]

    try:
        # STEP 1: Login
        driver.get(matrix_row["source_url"])
        time.sleep(5)

        if matrix_row["source_login"].upper() == "YES":
            print("Logging in to Imperial Health Plan portal...")

            email_field = WebDriverWait(driver, 30).until(
                EC.element_to_be_clickable((By.ID, "email"))
            )
            email_field.clear()
            email_field.send_keys(matrix_row["source_email"])

            password_field = WebDriverWait(driver, 30).until(
                EC.element_to_be_clickable((By.ID, "password"))
            )
            password_field.clear()
            password_field.send_keys(matrix_row["source_password"])

            time.sleep(1)

        # STEP 2: Mark old magic link emails + click Login
        print("Submitting login and polling for magic link...")

        mark_matching_as_read(
            sender="agentportal@imperialhealthplan.com",
            subject="Log in confirmation",
            mailbox="support@enrollinsurance.com",
        )

        login_btn = WebDriverWait(driver, 10).until(
            EC.element_to_be_clickable((By.CSS_SELECTOR, "button.login-btn"))
        )
        login_btn.click()

        login_clicked_at = datetime.now(timezone.utc)
        print("Login clicked.")
        time.sleep(3)

        # STEP 3: Fetch magic link from email and navigate
        print("Fetching magic link from email...")

        login_url = _fetch_imperial_magic_link(
            since_dt_utc=login_clicked_at,
            poll_seconds=120,
        )

        print(f"Navigating to magic link...")
        driver.get(login_url)
        time.sleep(10)

        # STEP 4: Wait for users table to confirm we're logged in
        print("Waiting for users table...")

        try:
            WebDriverWait(driver, 30).until(
                EC.presence_of_element_located((By.ID, "users-dt"))
            )
            print("Users table loaded — logged in successfully.")
        except TimeoutException:
            # If we don't see the table, might need a password reset
            print("WARNING: Users table not found. Login may have failed or password reset needed.")
            log_error(
                ERROR_CODES["login_error"],
                "Imperial portal did not load users table after magic link. Password reset may be needed.",
                script_name
            )
            driver.quit()
            return None

        time.sleep(3)

        # STEP 5: Click Export
        print("Clicking Export...")

        export_btn = WebDriverWait(driver, 15).until(
            EC.element_to_be_clickable((By.XPATH, "//button[contains(text(), 'Export')]"))
        )
        export_btn.click()

        print("Export triggered, waiting for download...")
        time.sleep(30)

        log_success()
        return download_folder

    except Exception as e:
        log_error(
            ERROR_CODES["general_error"],
            f"ACU Imperial handler failed: {e}",
            script_name
        )
        print(f"ACU Imperial handler failed: {e}")
        driver.quit()
        return None


######################################################################################################################################################################


def run_bob_imperial(driver, matrix_row, date_info):
    print("Running BOB Imperial Health Plan handler...")

    download_folder = os.path.normpath(matrix_row["download_path"])
    script_name = matrix_row["script_name"]

    try:
        # STEP 1: Login
        driver.get(matrix_row["source_url"])
        time.sleep(5)

        if matrix_row["source_login"].upper() == "YES":
            print("Logging in to Imperial Health Plan portal...")

            email_field = WebDriverWait(driver, 30).until(
                EC.element_to_be_clickable((By.ID, "email"))
            )
            email_field.clear()
            email_field.send_keys(matrix_row["source_email"])

            password_field = WebDriverWait(driver, 30).until(
                EC.element_to_be_clickable((By.ID, "password"))
            )
            password_field.clear()
            password_field.send_keys(matrix_row["source_password"])
            time.sleep(1)

            login_btn = WebDriverWait(driver, 10).until(
                EC.element_to_be_clickable((By.CSS_SELECTOR, "button.login-btn"))
            )
            login_btn.click()
            print("Login clicked.")
            time.sleep(15)

        # STEP 2: Wait for users table
        print("Waiting for page to load...")

        try:
            WebDriverWait(driver, 30).until(
                EC.presence_of_element_located((By.ID, "users-dt"))
            )
            print("Users table present — logged in.")
        except TimeoutException:
            print("WARNING: Users table not found. Password reset may be needed.")
            log_error(
                ERROR_CODES["login_error"],
                "Imperial portal did not load users table. Password reset may be needed.",
                script_name
            )
            driver.quit()
            return None

        time.sleep(3)

        # STEP 3: Click Member Roster tab
        print("Clicking Member Roster...")

        member_roster_tab = WebDriverWait(driver, 15).until(
            EC.element_to_be_clickable((By.XPATH, "//a[.//span[text()='Member Roster']]"))
        )
        member_roster_tab.click()

        print("Member Roster loaded.")
        time.sleep(10)

        # STEP 4: Click Export
        print("Clicking Export...")

        export_btn = WebDriverWait(driver, 15).until(
            EC.element_to_be_clickable(
                (By.XPATH, "//button[contains(@class, 'btn-primary') and contains(text(), 'Export')]"))
        )
        export_btn.click()

        print("Waiting for download...")
        time.sleep(30)

        log_success()
        return download_folder

    except Exception as e:
        log_error(
            ERROR_CODES["general_error"],
            f"BOB Imperial handler failed: {e}",
            script_name
        )
        print(f"BOB Imperial handler failed: {e}")
        driver.quit()
        return None


def _fetch_imperial_magic_link(since_dt_utc, poll_seconds=120, poll_interval=5):
    """Poll support@enrollinsurance.com for Imperial magic link email, extract URL."""
    import requests as _requests

    if since_dt_utc.tzinfo is None:
        since_dt_utc = since_dt_utc.replace(tzinfo=timezone.utc)

    from graph_auth import get_graph_access_token
    token = get_graph_access_token()
    base = "https://graph.microsoft.com/v1.0"
    mailbox = "support@enrollinsurance.com"
    sender = "agentportal@imperialhealthplan.com"
    subject = "Log in confirmation"
    url = f"{base}/users/{mailbox}/mailFolders/Inbox/messages"

    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    params = {
        "$select": "id,subject,from,receivedDateTime,isRead,body",
        "$top": "50",
        "$orderby": "receivedDateTime desc",
    }

    import time as _time
    deadline = _time.time() + poll_seconds
    seen_debug = set()

    print(f"[MAGIC LINK] Polling {mailbox} for email from {sender}...")

    while _time.time() < deadline:
        r = _requests.get(url, headers=headers, params=params, timeout=30)
        if r.status_code >= 400:
            raise RuntimeError(f"[MAGIC LINK] Graph error: {r.status_code} {r.text[:200]}")

        for msg in r.json().get("value", []):
            msg_id = msg.get("id")
            if not msg_id:
                continue

            if msg_id not in seen_debug:
                seen_debug.add(msg_id)

            if msg.get("isRead"):
                continue

            msg_sender = (((msg.get("from") or {}).get("emailAddress") or {}).get("address") or "").lower()
            if msg_sender != sender.lower():
                continue

            msg_subject = (msg.get("subject") or "").strip()
            if subject.lower() not in msg_subject.lower():
                continue

            received_str = (msg.get("receivedDateTime") or "").replace("Z", "+00:00")
            try:
                received = datetime.fromisoformat(received_str).astimezone(timezone.utc)
            except Exception:
                continue
            if received < since_dt_utc:
                continue

            # Extract login URL from email body
            body = (msg.get("body") or {}).get("content") or ""

            link_patterns = [
                re.compile(r'href=["\']?(https?://[^"\'>\s]*agentportal\.imperialhealthplan\.com[^"\'>\s]*)',
                           re.IGNORECASE),
                re.compile(r'href=["\']?(https?://[^"\'>\s]*imperialhealthplan[^"\'>\s]*)', re.IGNORECASE),
            ]

            login_url = None
            for pattern in link_patterns:
                m = pattern.search(body)
                if m:
                    login_url = m.group(1)
                    break

            if not login_url:
                print("[MAGIC LINK] Found email but couldn't extract URL.")
                continue

            # Mark as read
            _requests.patch(
                f"{base}/users/{mailbox}/messages/{msg_id}",
                headers=headers, json={"isRead": True}, timeout=15,
            )

            print(f"[MAGIC LINK] URL found.")
            return login_url

        remaining = int(deadline - _time.time())
        print(f"[MAGIC LINK] Waiting... ({remaining}s left)")
        _time.sleep(poll_interval)

    raise RuntimeError(f"[MAGIC LINK] No login email found within {poll_seconds}s")


######################################################################################################################################################################


def run_acu_ncd(driver, matrix_row, date_info):
    print("Running ACU NCD (1Enrollment) handler...")

    download_folder = os.path.normpath(matrix_row["download_path"])
    script_name = matrix_row["script_name"]

    try:
        # STEP 1: Login
        driver.get(matrix_row["source_url"])
        time.sleep(5)

        if matrix_row["source_login"].upper() == "YES":
            print("Logging in to 1Enrollment...")

            username_field = WebDriverWait(driver, 30).until(
                EC.element_to_be_clickable((By.NAME, "username"))
            )
            username_field.clear()
            username_field.send_keys(matrix_row["source_email"])

            password_field = WebDriverWait(driver, 30).until(
                EC.element_to_be_clickable((By.NAME, "password"))
            )
            password_field.clear()
            password_field.send_keys(matrix_row["source_password"])

            signin_btn = WebDriverWait(driver, 10).until(
                EC.element_to_be_clickable((By.NAME, "authenticate"))
            )
            signin_btn.click()

            print("Login submitted.")
            time.sleep(10)

        # STEP 2: Mark old OTP emails + click Send Code
        print("Handling OTP...")

        mark_matching_as_read(
            sender="support@enrollinsurance.com",
            subject="One-Time Verification Code",
        )

        send_code_btn = WebDriverWait(driver, 15).until(
            EC.element_to_be_clickable((By.NAME, "mfaOTCSend"))
        )
        send_code_btn.click()

        otp_sent_at = datetime.now(timezone.utc)
        print("Send Code clicked.")
        time.sleep(3)

        # STEP 3: Fetch OTP from email
        print("Polling for OTP code...")

        code = fetch_otp_code(
            sender="support@enrollinsurance.com",
            subject="One-Time Verification Code",
            since_dt_utc=otp_sent_at,
            poll_seconds=120,
        )

        # STEP 4: Enter OTP + Continue
        print(f"Entering OTP: {code}")

        otp_input = WebDriverWait(driver, 15).until(
            EC.element_to_be_clickable((By.ID, "mfaUserOTC"))
        )
        otp_input.clear()
        otp_input.send_keys(code)
        time.sleep(1)

        continue_btn = WebDriverWait(driver, 10).until(
            EC.element_to_be_clickable((By.NAME, "mfaOTCCheck"))
        )
        continue_btn.click()

        print("OTP submitted.")
        time.sleep(10)

        # STEP 5: Click Search
        print("Clicking Search...")

        search_link = WebDriverWait(driver, 30).until(
            EC.element_to_be_clickable((By.CSS_SELECTOR, "a.search"))
        )
        search_link.click()

        print("Search page loading...")
        time.sleep(10)

        # STEP 6: Click Agents tab
        print("Clicking Agents...")
        print("Before Agents URL:", driver.current_url)
        print("Before Agents title:", driver.title)

        agents_link = WebDriverWait(driver, 30).until(
            EC.presence_of_element_located((
                By.XPATH,
                "//a[contains(@href, \"showsearch('agent'\") or contains(@onclick, \"showsearch('agent'\") or contains(normalize-space(.), 'Agents')]"
            ))
        )

        try:
            agents_link.click()
            print("Clicked Agents.")
        except Exception:
            print("Normal Agents click failed. Trying JavaScript click.")
            driver.execute_script("arguments[0].click();", agents_link)
            print("Clicked Agents with JavaScript.")

        print("Agents tab loaded.")
        time.sleep(5)

        print("After Agents URL:", driver.current_url)
        print("After Agents title:", driver.title)

        # STEP 7: Click Search Agents
        print("Clicking Search Agents...")

        search_agents_btn = WebDriverWait(driver, 15).until(
            EC.element_to_be_clickable((By.CSS_SELECTOR, "input[name='submit'][value='Search Agents']"))
        )
        search_agents_btn.click()

        print("Searching agents...")
        time.sleep(30)

        # STEP 8: Click Download
        print("Clicking Download...")

        download_triggered_at = datetime.now()
        print(f"Download triggered at: {download_triggered_at.strftime('%Y-%m-%d %H:%M:%S')}")

        download_btn = WebDriverWait(driver, 30).until(
            EC.element_to_be_clickable((By.CSS_SELECTOR, "input[name='filter'][onclick='SubmitDownload()']"))
        )
        download_btn.click()
        time.sleep(5)

        # STEP 9: Handle "Download Queued" modal — click OK
        print("Handling download queue modal...")

        try:
            ok_btn = WebDriverWait(driver, 15).until(
                EC.element_to_be_clickable((By.XPATH,
                                            "//div[contains(@class, 'ui-dialog')]//button[contains(text(), 'OK')]"
                                            ))
            )
            ok_btn.click()
            print("Clicked OK on queue modal.")
        except TimeoutException:
            print("No queue modal appeared, download may have started directly.")

        time.sleep(5)

        # STEP 10: Navigate to Downloads page
        print("Navigating to Downloads page...")

        downloads_link = WebDriverWait(driver, 15).until(
            EC.element_to_be_clickable((By.CSS_SELECTOR, "a[href='/manage/opsUserDownloads.cfm']"))
        )
        downloads_link.click()
        time.sleep(10)

        # STEP 11: Find and click our download file
        print("Finding our download...")

        import time as _time
        download_deadline = _time.time() + 120
        file_link = None

        while _time.time() < download_deadline:
            links = driver.find_elements(By.CSS_SELECTOR, "a[href*='/manage/reports/download.cfm']")

            if links:
                rows = driver.find_elements(By.CSS_SELECTOR, "table.detail_border tbody tr")

                for row in rows:
                    cells = row.find_elements(By.TAG_NAME, "td")
                    if len(cells) >= 6:
                        label_text = cells[1].text.strip()
                        started_text = cells[3].text.strip()
                        completed_text = cells[4].text.strip()
                        file_cell = cells[5]

                        row_links = file_cell.find_elements(By.CSS_SELECTOR, "a[href*='download.cfm']")
                        if row_links and completed_text:
                            file_link = row_links[0]
                            file_name = file_link.text.strip()
                            print(f"Found: {file_name}")
                            print(f"  Label: {label_text}")
                            print(f"  Started: {started_text}")
                            print(f"  Completed: {completed_text}")
                            break

                if file_link:
                    break

            remaining = int(download_deadline - _time.time())
            print(f"File not ready yet, refreshing... ({remaining}s left)")
            _time.sleep(10)
            driver.refresh()
            _time.sleep(5)

        if file_link:
            file_link.click()
            print("Download clicked, waiting for file...")
            time.sleep(30)
        else:
            print("WARNING: Could not find completed download within timeout.")

        log_success()
        return download_folder

    except Exception as e:
        log_error(
            ERROR_CODES["general_error"],
            f"ACU NCD (1Enrollment) handler failed: {e}",
            script_name
        )
        print(f"ACU NCD (1Enrollment) handler failed: {e}")
        driver.quit()
        return None


######################################################################################################################################################################

def run_acu_physiciansmutual(driver, matrix_row, date_info):
    print("Running BOB Physicians Mutual handler...")

    download_folder = os.path.normpath(matrix_row["download_path"])
    driver.get(matrix_row["source_url"])

    # Perform login if needed
    for attempt in range(3):
        if matrix_row["source_login"].upper() == "YES":
            try:
                print("Starting login process...")
                # Step 2: Navigate to Portal page
                WebDriverWait(driver, 30).until(
                    EC.element_to_be_clickable((By.XPATH, "//div[@class='log-in-button-text']"))
                ).click()
                time.sleep(2)

                WebDriverWait(driver, 30).until(
                    EC.element_to_be_clickable((By.XPATH, "//a[@data-automation-id='header-log-in-desktop-eselfcare']"))
                ).click()
                print("Navigated to Portal page.")
                time.sleep(2)

                # Step 3: Click on "Log in to your account"
                WebDriverWait(driver, 30).until(
                    EC.element_to_be_clickable((By.ID, "loginButton"))
                ).click()
                print("Clicked on 'Log in to your account'.")
                time.sleep(5)

                # Step 4: Enter username and password
                WebDriverWait(driver, 30).until(
                    EC.presence_of_element_located((By.ID, "username"))
                ).send_keys(matrix_row["source_email"])
                print("Entered username.")
                time.sleep(2)

                WebDriverWait(driver, 30).until(
                    EC.presence_of_element_located((By.ID, "password"))
                ).send_keys(matrix_row["source_password"])
                print("Entered password.")
                time.sleep(2)

                WebDriverWait(driver, 30).until(
                    EC.element_to_be_clickable((By.ID, "kc-login"))
                ).click()
                print("Logged in successfully!")
                time.sleep(2)

            except TimeoutException as e:
                log_error(ERROR_CODES["login_error"], f"Login failed. Timed out waiting for an element: {e}",
                          matrix_row["script_name"])
                print("Login page not found or an element was not clickable. Exiting...")
                driver.quit()
                return None

        # --- Navigate to Dashboard and Download ---
        try:
            # Step 5: Navigate to Sales Performance Dashboard area
            print("Navigating to Sales Performance Dashboard...")
            WebDriverWait(driver, 45).until(  # Increased wait time for page load after login
                EC.element_to_be_clickable(
                    (By.XPATH, "//a[@data-automation-id='agent-portal-sales-performance-dashboard']"))
            ).click()
            print("Navigated to Sales Performance Dashboard area.")
            time.sleep(10)

            # Switch to the iframe embedded within the Shadow DOM
            print("Waiting for the Tableau visualization host...")
            tableau_viz_host = WebDriverWait(driver, 30).until(
                EC.presence_of_element_located((By.TAG_NAME, "tableau-viz"))
            )

            shadow_root = tableau_viz_host.shadow_root
            inner_iframe = shadow_root.find_element(By.CSS_SELECTOR, "iframe")
            driver.switch_to.frame(inner_iframe)
            print("Switched to the inner Tableau iframe successfully!")

            # Step 6: Click Ready to Sell tab
            print("Clicking Ready to Sell tab...")

            WebDriverWait(driver, 30).until(
                EC.element_to_be_clickable(
                    (
                        By.XPATH,
                        "//div[@data-testid='tab-button-zone-text' and normalize-space()='Ready to Sell']"
                    )
                )
            ).click()

            print("Clicked on Ready to Sell tab.")
            time.sleep(30)

            # Step 7: Click/sort a Tableau table column
            # Tableau renders the table as image tiles, so column headers are not normal HTML elements.
            # This click selects/sorts the visible table area before downloading Data.
            print("Clicking table header area to sort/select the Ready to Sell table...")

            tableau_tile = WebDriverWait(driver, 30).until(
                EC.presence_of_element_located(
                    (
                        By.XPATH,
                        "//img[contains(@src, '/vizql/tilecache/') or contains(@data-datasrc, '/vizql/tilecache/')]"
                    )
                )
            )

            # Click near the Level 1 Name header area.
            # If this clicks slightly off in your VM, adjust only the first number.
            ActionChains(driver).move_to_element_with_offset(tableau_tile, 725, 20).click().perform()

            print("Clicked table header area.")
            time.sleep(10)

            # Step 8: Click Tableau Download dropdown
            print("Clicking Tableau Download dropdown...")

            main_window = driver.current_window_handle
            existing_windows = set(driver.window_handles)

            WebDriverWait(driver, 30).until(
                EC.element_to_be_clickable(
                    (
                        By.XPATH,
                        "//button[@id='download' or @data-tb-test-id='viz-viewer-toolbar-button-download']"
                    )
                )
            ).click()

            print("Clicked Download dropdown.")
            time.sleep(2)

            # Step 9: Click Data option
            print("Clicking Data option...")

            data_option = WebDriverWait(driver, 30).until(
                EC.element_to_be_clickable(
                    (
                        By.XPATH,
                        "//span[normalize-space()='Data']/ancestor::*[@role='menuitem' or self::button or self::div][1]"
                    )
                )
            )

            driver.execute_script("arguments[0].click();", data_option)

            print("Clicked Data option.")
            time.sleep(5)

            # Step 10: Switch to the new View Data window
            print("Switching to View Data window...")

            WebDriverWait(driver, 30).until(
                lambda d: len(d.window_handles) > len(existing_windows)
            )

            new_window = list(set(driver.window_handles) - existing_windows)[0]
            driver.switch_to.window(new_window)

            print("Switched to View Data window.")
            time.sleep(30)

            # Step 11: Click final Download button in View Data window
            print("Clicking final Download button in View Data window...")

            final_download_button = WebDriverWait(driver, 60).until(
                EC.element_to_be_clickable(
                    (
                        By.XPATH,
                        "//button[@id='download' or normalize-space()='Download' or .//span[normalize-space()='Download']]"
                    )
                )
            )

            driver.execute_script("arguments[0].click();", final_download_button)

            print("Clicked final Download button.")
            print("Waiting for download to complete...")
            time.sleep(30)

        except TimeoutException as e:
            print("Error: Timed out waiting for an element during the download process.")
            print(f"Failed on element: {e}")

            with open("debug_page_source.html", "w", encoding="utf-8") as f:
                f.write(driver.page_source)

            print("The current page source has been saved to 'debug_page_source.html' for inspection.")

            log_error(
                ERROR_CODES["download_button_not_found"],
                "A button or element in the download process was not found.",
                matrix_row["script_name"]
            )

            driver.quit()
            return None

        return download_folder

    print("RPA process failed 3 attempts. Skipping carrier.")

    log_error(
        ERROR_CODES["general_error"],
        "RPA process failed 3 attempts.",
        matrix_row["script_name"]
    )

    return None

##############################################################################################################################################################################

handler_map = {
    "ACU_NewEra_RPA": run_acu_newera,
    "COMM_Ambetter_RPA": run_comm_ambetter,
    "COMM_Anthem_RPA": run_comm_anthem,
    "COMM_Oscar_GA_RPA": run_comm_oscar_ga,
    "COMM_Oscar_SUBS_RPA": run_comm_oscar_subs,
    "COMM_Molina_RPA": run_comm_molina,
    "COMM_AmeriHealth_ACA_RPA": run_com_amerihealth_aca,
    "ACU_Ambetter_RPA": run_acu_ambetter,
    "ACU_HealthFirst_RPA": run_acu_health_first,
    # "ACU_Aetna_RPA":run_acu_aetna,
    # "ACU_AetnaSenior_Supp_RPA":run_acu_aetnasenior_supp,
    "ACU_Alignment_RPA": run_acu_alignment,
    "ACU_Allstate_RPA": run_acu_allstate,
    "ACU_AmericanAmicable_RPA": run_acu_americanamicable,
    "ACU_Caresource_RPA": run_acu_caresource,
    "ACU_Gerber_RPA": run_acu_gerber,
    "ACU_ManhattanLife_RPA": run_acu_manhattanlife,
    "ACU_Molina_RPA": run_acu_molina,
    "ACU_Oscar_RPA": run_acu_oscar,
    "BOB_Alignment_RPA": run_bob_alignment,
    "BOB_Allstate_RPA": run_bob_allstate,
    "BOB_Ambetter_RPA": run_bob_ambetter1,
    # "BOB_Ambetter2_RPA": run_bob_ambetter2,
    # "BOB_Ambetter3_RPA": run_bob_ambetter3,
    "BOB_Amerihealth_RPA": run_bob_amerihealth,
    "BOB_Anthem_RPA": run_bob_anthem,
    "BOB_Caresource_RPA": run_bob_caresource,
    "BOB_geoblue_RPA": run_bob_geoblue,
    "BOB_Gerber_RPA": run_bob_gerber,
    "BOB_ManhattanLife_RPA": run_bob_manhattanlife,
    "BOB_Medica_RPA": run_bob_medica,
    "BOB_Medica_Individual_RPA": run_bob_medica_individual,
    "ACU_Medica_RPA": run_acu_medica,
    "BOB_Molina_RPA": run_bob_molina,
    "BOB_Molina_Medicare_RPA": run_bob_molina_medicare,
    "BOB_NewEra_RPA": run_bob_newera,
    "BOB_Oscar_RPA": run_bob_oscar,
    "BOB_Pivot_RPA": run_bob_pivot,
    "BOB_BCBS_MI_RPA": run_bob_bcbs_mi,
    "BOB_Cigna_ACA_RPA": run_bob_cigna_aca,
    "BOB_Ethos_RPA": run_bob_ethos,
    "BOB_KelseyCare_RPA": run_bob_kelseycare,
    "BOB_KelseyCare_Advantage_RPA": run_bob_kelseycare_advantage,
    "BOB_PriorityHealth_RPA": run_bob_priorityhealth,
    "BOB_SMA_RPA": run_bob_sma,
    "ACU_SMA_RPA": run_acu_sma,
    "ACU_Cigna_RPA": run_acu_cigna,
    "BOB_Devoted_RPA": run_bob_devoted,
    "ACU_Devoted_RPA": run_acu_devoted,
    "BOB_HCSC_RPA": run_bob_hcsc,
    "ACU_HCSC_RPA": run_acu_hcsc,
    "BOB_AmericanAmicable_RPA": run_bob_americanamicable,
    "ACU_BCBS_AZ_RPA": run_acu_bcbs_az,
    "COMM_BCBS_AZ_RPA": run_comm_bcbs_az,
    "ACU_ProminenceHealth_RPA": run_acu_prominencehealth,
    "BOB_ProminenceHealth_RPA": run_bob_prominencehealth,
    "BOB_BCBS_NE_RPA": run_bob_bcbs_ne,
    "ACU_BCBS_NE_RPA": run_acu_bcbs_ne,
    "BOB_PhysiciansMutual_RPA": run_bob_physiciansmutual,
    "BOB_Solis_RPA": run_bob_solis,
    "BOB_AmeritasDentalandVision_RPA": run_bob_ameritas_dentalandvision,
    "ACU_AmeritasLife_RPA": run_acu_ameritaslife,
    "BOB_MutualofOmaha_RPA": run_bob_mutualofomaha,
    "BOB_WorldTrips_RPA": run_bob_worldtrips,
    "ACU_Heartland_RPA": run_acu_heartland,
    "BOB_Nationallife_RPA": run_bob_nationallife,
    "BOB_GoldKidney_RPA": raw_bob_goldkidney,
    "ACU_CHRISTUS_ACA": run_acu_christus,
    "BOB_Christus_RPA": run_bob_christus_aca,
    "BOB_Christus_MDC_RPA": run_bob_christus_mdc,
    "BOB_GoldKidney_RPA": run_bob_gold_kidney_health,
    "ACU_Aflac_RPA": run_acu_aflac,
    "BOB_Aflac_RPA": run_bob_aflac,
    "ACU_GoldKidney_RPA": run_acu_gold_kidney_health,
    "ACU_NCD_RPA": run_acu_ncd,
    "ACU_Imperial_RPA": run_acu_imperial,
    "BOB_Imperial_RPA": run_bob_imperial,
    "ACU_Healthspring_RPA": run_acu_healthspring,
    "ACU_SCAN_RPA": run_acu_scan,
    "ACU_Verda_RPA": run_acu_verda,
    "ACU_Wellcare_RPA": run_acu_wellcare,
    "BOB_Wellcare_RPA": run_bob_wellcare,
    "BOB_Healthfirst_ACA_RPA": run_bob_healthspring_aca,
    "BOB_Healthfirst_MDC_RPA": run_bob_healthspring_mdc,
    "BOB_Quartz_RPA": run_bob_quartz,
    "ACU_Zing_RPA": run_acu_zing,
    "ACU_PhysiciansMutual_RPA": run_acu_physiciansmutual,

    "__default__": lambda *args, **kwargs: print("No valid handler matched.")
}