from selenium.webdriver import ActionChains
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.common.by import By
from selenium.common.exceptions import TimeoutException
from selenium.webdriver.common.keys import Keys
import time
import random
from datetime import datetime, timedelta, timezone
import os
import pytz
import paramiko
import re
from chrome_utils import get_chrome_driver
from file_utils import extract_bcbs_az_agents_to_csv
from logger import log_error, ERROR_CODES


def fetch_otp_code(matrix_row):
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

    download_folder = os.path.normpath(matrix_row["download_path"])
    current_month_year = date_info["current_month_year_short"]
    # Navigate to carrier URL
    driver.get(matrix_row["source_url"])

    # Perform login if needed
    if matrix_row["source_login"].upper() == "YES":
        try:
            email_field = WebDriverWait(driver, 10).until(
                EC.presence_of_element_located((By.XPATH, "//input[@placeholder='Email']"))
            )
            email_field.send_keys(matrix_row["source_email"])
            password_field = driver.find_element(By.XPATH, "//input[@placeholder='Password']")
            password_field.send_keys(matrix_row["source_password"])
            login_button = driver.find_element(By.XPATH,
                                               "//*[@id='centerPanel']/div/div[2]/div/div[2]/div/div[3]/button")
            login_button.click()
            print("Logged in successfully.")
            time.sleep(10)
        except TimeoutException:
            print("Login not needed or failed.")

    # Step 1: Locate check date row
    try:
        WebDriverWait(driver, 30).until(
            EC.presence_of_element_located((By.ID, "commissionsTable"))
        )
        rows = WebDriverWait(driver, 30).until(
            EC.presence_of_all_elements_located((By.CSS_SELECTOR, "#commissionsTable tbody tr"))
        )
        for row in rows:
            check_date = row.find_element(By.XPATH, "./td[5]").text.strip()
            if check_date == current_month_year:
                row.find_element(By.XPATH, "./td[3]/a").click()
                print(f"Found and clicked row for check date: {check_date}")
                time.sleep(20)
                break
        else:
            print("No matching Check Date found.")
            return None
    except Exception as e:
        print(f"Error locating commission table: {e}")
        return None

    # Step 2: Click Export button
    try:
        iframe = WebDriverWait(driver, 10).until(
            EC.presence_of_element_located((By.TAG_NAME, "iframe"))
        )
        driver.switch_to.frame(iframe)
        export_btn = WebDriverWait(driver, 10).until(
            EC.element_to_be_clickable((By.XPATH, "//button[@title='Export CSV']"))
        )
        export_btn.click()
        print("Export CSV clicked.")
        time.sleep(30)
    except Exception as e:
        print(f"Error clicking export: {e}")
        return None

    # Step 3: Download modal interaction
    try:
        WebDriverWait(driver, 30).until(
            EC.visibility_of_element_located((By.ID, "downloadModal"))
        )
        print("Download modal visible.")

        modal_iframe = driver.find_elements(By.TAG_NAME, "iframe")
        if modal_iframe:
            driver.switch_to.frame(modal_iframe[0])
            print("Switched to modal iframe.")

        download_button = WebDriverWait(driver, 30).until(
            EC.element_to_be_clickable((By.CSS_SELECTOR, "a.btn.btn-primary[download]"))
        )
        download_button.click()
        print("Download button clicked. Waiting...")
        time.sleep(30)
    except TimeoutException:
        print("Download button not found.")
        return None

    # Step 4: Return downloaded file path
    return download_folder


def run_comm_anthem(driver, matrix_row, date_info):
    print("Running COMM Anthem handler...")

    try:
        url = matrix_row["source_url"]
        download_folder = os.path.normpath(matrix_row["download_path"])
        email = matrix_row["source_email"]
        password = matrix_row["source_password"]
        login_required = matrix_row["source_login"].upper() == "YES"
        current_month_year = date_info["prev_month_year"]
        # print(current_month_year)
        driver.get(url)

        if login_required:
            try:
                print("Attempting login...")
                email_field = WebDriverWait(driver, 10).until(
                    EC.presence_of_element_located((By.XPATH, "//input[@name='username']"))
                )
                email_field.send_keys(email)
                password_field = driver.find_element(By.XPATH, "//input[@name='password']")
                password_field.send_keys(password)
                login_button = driver.find_element(By.XPATH,
                                                   "/html/body/app-root/feature-toggle-provider/app-main/div/div/app-login/div/div[2]/div[2]/div[1]/div/div[2]/form/div[2]/div[1]/button")
                login_button.click()
                print("Logged in successfully.")
                time.sleep(10)
            except TimeoutException:
                print("Login not required or failed.")

        # Dashboard switch (if needed)
        try:
            dashboard_text = WebDriverWait(driver, 10).until(
                EC.presence_of_element_located((By.XPATH, "//p[@class='paragraph css-lbl8vh-cssText']"))
            ).text.strip()
            if dashboard_text != "Switch To Medicare Dashboard":
                print("Switching to All Markets Dashboard...")
                all_markets_button = driver.find_element(By.XPATH,
                                                         "//p[contains(text(), 'Switch To All Markets Dashboard')]")
                all_markets_button.click()
                time.sleep(5)
            else:
                print("Already in Medicare Dashboard.")
        except Exception as e:
            print(f"Dashboard switch skipped or failed: {e}")

        # Navigate to Book of Business > Commissions
        try:
            book_of_business = WebDriverWait(driver, 10).until(
                EC.element_to_be_clickable((By.XPATH, "//div[@title='Book of Business']"))
            )
            book_of_business.click()
            time.sleep(5)
            commissions_option = WebDriverWait(driver, 10).until(
                EC.element_to_be_clickable((By.XPATH, "//li[@id='mnuCommissions']"))
            )
            commissions_option.click()
        except Exception as e:
            print(f"Error navigating to 'Commissions': {e}")

        # Wait for summary list
        try:
            WebDriverWait(driver, 20).until(
                EC.presence_of_element_located((By.CLASS_NAME, "commissionSummaryListView"))
            )
        except TimeoutException:
            print("Summary List View not found.")
            return None

        # Search for commission row
        try:
            rows = WebDriverWait(driver, 30).until(
                EC.presence_of_all_elements_located((By.CSS_SELECTOR, ".row-cont"))
            )
            for row in rows:
                period = row.find_element(By.CSS_SELECTOR, "#Period-columnAndValue .columnValue").text.strip()
                commission_text = row.find_element(By.CSS_SELECTOR,
                                                   "#TotalCommissionsEarned-columnAndValue .columnValue").text.strip()
                commission_value = float(commission_text.replace("$", "").replace(",",
                                                                                  "")) if commission_text and commission_text != "-" else 0

                if period == current_month_year and commission_value > 0:
                    print(f"Matching period: {period}, commission: {commission_value}")
                    arrow = row.find_element(By.CSS_SELECTOR, ".arrow-up")
                    driver.execute_script("arguments[0].click();", arrow)
                    time.sleep(2)

                    # Verify section exists
                    section_headers = driver.find_elements(By.CSS_SELECTOR, ".commissionsWrapperChild.columnLabel")
                    if any("Group, Individual and Specialty Commissions" in h.text for h in section_headers):
                        download_link = WebDriverWait(driver, 15).until(
                            EC.element_to_be_clickable((By.XPATH, "//*[@id='1']/div[2]/div[5]/div[2]/div/div/a"))
                        )
                        download_link.click()
                        print("Download triggered. Waiting for file...")
                        time.sleep(90)

                        # Return most recent matching file
                        return download_folder  # Let the runner scan it
            print("No matching commission row found.")
            return None

        except Exception as e:
            print(f"Error scanning commission rows: {e}")
            return None

    except Exception as e:
        print(f"Unhandled error in Anthem handler: {e}")
        return None


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

    try:
        url = matrix_row["source_url"]
        download_folder = os.path.normpath(matrix_row["download_path"])
        email = matrix_row["source_email"]
        password = matrix_row["source_password"]
        login_required = matrix_row["source_login"].upper() == "YES"
        current_month_year = date_info["current_month_year"]
        current_year = date_info["current_year"]
        current_month_number = date_info["current_month_number"]

        driver.get(url)

        if login_required:
            try:
                print("Attempting to log in...")
                email_field = WebDriverWait(driver, 10).until(
                    EC.presence_of_element_located((By.NAME, "login_id"))
                )
                email_field.send_keys(email)

                password_field = driver.find_element(By.NAME, "password")
                password_field.send_keys(password)

                login_button = driver.find_element(By.NAME, "submit")
                login_button.click()
                print("Logged in successfully.")
                time.sleep(10)

            except TimeoutException:
                print("Login not required or failed.")

        try:
            original_window = driver.current_window_handle
            WebDriverWait(driver, 10).until(
                EC.element_to_be_clickable((By.XPATH, '//*[@id="domain_container"]/div/div[3]/div/div[2]/div'))
            ).click()
            print("Molina domain selected.")
            WebDriverWait(driver, 10).until(EC.number_of_windows_to_be(2))

            for handle in driver.window_handles:
                if handle != original_window:
                    driver.switch_to.window(handle)
                    break
            print("Switched to Molina tab.")
        except TimeoutException:
            print("Failed to switch domain.")
            return None

        try:
            WebDriverWait(driver, 10).until(
                EC.presence_of_element_located((By.XPATH, "//*[@id='menu_header_2_2']"))
            ).click()
            print("Commissions selected")
            time.sleep(10)
        except:
            print("Commissions not found")
            return None

        try:
            statement_from = driver.find_element(By.ID, "statement_from")
            statement_from.clear()
            statement_from.send_keys(date_info["first_of_three_months_prior"])
            print(f"✅ Entered 'From' Date: {date_info['first_of_three_months_prior']}")
        except:
            print("'From' input form not found.")
            return None

        try:
            statement_to = driver.find_element(By.ID, "statement_to")
            statement_to.clear()
            statement_to.send_keys(date_info["last_of_current_month"])
            time.sleep(5)
            statement_to.send_keys(Keys.ENTER)
            print(f"✅ Entered 'To' Date: {date_info['last_of_current_month']}")
        except:
            print("'To' input form not found.")
            return None

        try:
            time.sleep(10)
            WebDriverWait(driver, 10).until(
                EC.presence_of_element_located((By.NAME, "searchMember"))
            ).click()
        except:
            print("Search Button not found.")
            return None

        time.sleep(20)

        try:
            print("Waiting for the table to be visible...")
            table = WebDriverWait(driver, 20).until(
                EC.presence_of_element_located((By.ID, "portal_member"))
            )
            print("✅ Table is visible.")
        except Exception as e:
            print(f"❌ Error: Table not found. {e}")
            return None

        try:
            print("Locating the first odd row...")
            first_odd_row = WebDriverWait(driver, 10).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, "#portal_member tbody tr.odd"))
            )
            print("✅ First odd row found.")

            stmt_date_element = first_odd_row.find_element(By.CSS_SELECTOR, "td.text-center.sorting_1")
            stmt_date_text = stmt_date_element.text.strip()
            stmt_date = datetime.strptime(stmt_date_text, "%m/%d/%Y")

            stmt_month = stmt_date.strftime("%m")
            stmt_year = stmt_date.strftime("%Y")

            print(f"Stmt Date Found: {stmt_date_text} | Month: {stmt_month}, Year: {stmt_year}")

            if stmt_month == current_month_number and stmt_year == current_year:
                print(f"✅ Stmt Date matches current month ({stmt_month}/{stmt_year}). Proceeding to download.")
                excel_link = first_odd_row.find_element(By.CSS_SELECTOR, "td.text-center a.card-link")
                excel_link.click()
                print("Download initiated successfully.")
                time.sleep(5)
            else:
                print(
                    f"Stmt Date {stmt_month}/{stmt_year} does not match the current month {current_month_number}/{current_year}. Skipping download.")
        except Exception as e:
            print(f"Error processing the row: {e}")
            return None

    except Exception as e:
        print(f"Unhandled error in Molina handler: {e}")
        return download_folder


def run_acu_ambetter(driver, matrix_row, date_info):
    print("Running ACU Ambetter handler...")

    download_folder = os.path.normpath(matrix_row["download_path"])
    driver.get(matrix_row["source_url"])

    # Perform login if needed
    if matrix_row["source_login"].upper() == "YES":
        try:
            email_field = WebDriverWait(driver, 10).until(
                EC.presence_of_element_located((By.XPATH, "//input[@placeholder='Email']"))
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
            time.sleep(60)  # Allow time for login redirection
        except Exception as e:
            log_error(ERROR_CODES["login_error"], "Login page timeout or login fields not found.",
                      matrix_row["script_name"])
            print("Login page not found or timeout occurred. Exiting...")
            driver.quit()
            return None

    # Navigate to download page
    try:
        # Step 3: Navigate to Agent Hierarchy page
        driver.get("https://v10.eagentcenter.com/agent/Hierarchy.aspx")
        print("Navigated to Agent Hierarchy page.")
        time.sleep(20)
    except Exception as e:
        log_error(ERROR_CODES["navigation_error"], "Navigation process failed.", matrix_row["script_name"])
        print("Navigation process failed, ending process.")
        driver.quit()
        return None

    # Click the download button
    try:
        export_button = WebDriverWait(driver, 30).until(
            EC.element_to_be_clickable((By.XPATH, "//ul[@id='Ul1']/li[@id='li6']"))
        )
        export_button.click()
        print("Export button clicked, file should start downloading...")
        time.sleep(30)  # Allow time for the download
    except Exception as e:
        log_error(ERROR_CODES["download_button_not_found"], "'Download' button not found.", matrix_row["script_name"])
        print("Download button not found, ending process.")
        driver.quit()
        return None

    return download_folder


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
            WebDriverWait(driver, 120).until(
                EC.element_to_be_clickable((By.XPATH, "//*[@id='tfaChoiceEmailButton']"))
            ).click()
            print("Code sent!")
            time.sleep(30)  # Wait for 2FA process

            if matrix_row["otp_needed"].upper() == "YES":
                try:
                    otp_code = fetch_otp_code(matrix_row)
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
        commissions = WebDriverWait(driver, 15).until(
            EC.element_to_be_clickable((By.XPATH, "//*[@id='commissions']"))
        )
        commissions.click()
        print("Commissions Clicked")

        # click Agent Hierarchy
        ah = WebDriverWait(driver, 15).until(
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
            print("Logged in successfully!")
            time.sleep(10)

            # OTP function
            try:
                WebDriverWait(driver, 60).until(
                    EC.element_to_be_clickable((By.XPATH, "//input[@value='Send Email']"))
                ).click()
                print("OTP sent!")

                otp_code = fetch_otp_code(matrix_row)

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


def run_bob_ambetter(driver, matrix_row, date_info):
    print("Running BOB Ambetter handler...")

    download_folder = os.path.normpath(matrix_row["download_path"])
    driver.get(matrix_row["source_url"])

    # Perform login if needed
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

    # Step 2: Click the download button
    try:
        download_button = WebDriverWait(driver, 120).until(
            EC.element_to_be_clickable((By.XPATH, "//*[contains(@id, 'j_id')]/div/table/tbody/tr/td[1]/a"))
        )
        download_button.click()
        print("Download button clicked. Waiting for download...")
        time.sleep(60)
    except Exception as e:
        log_error(ERROR_CODES["download_button_not_found"], "'Download' button not found.", matrix_row["script_name"])
        print("Download button not found, ending process.")
        driver.quit()
        return None

    return download_folder


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
            EC.element_to_be_clickable((By.XPATH,"//div[@class='filter-options show-popup']//a[text()='Apply']"))
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
            WebDriverWait(driver, 120).until(
                EC.element_to_be_clickable((By.XPATH, "//*[@id='tfaChoiceEmailButton']"))
            ).click()
            print("Code sent!")
            time.sleep(30)  # Wait for 2FA process

            if matrix_row["otp_needed"].upper() == "YES":
                try:
                    otp_code = fetch_otp_code(matrix_row)
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

    # Perform login if needed
    if matrix_row["source_login"].upper() == "YES":
        try:
            # Enter the login email address
            WebDriverWait(driver, 60).until(
                EC.presence_of_element_located((By.XPATH, "//*[@id='Form1']/input[3]"))
            ).send_keys(matrix_row["source_email"])

            # Enter Password
            WebDriverWait(driver, 60).until(
                EC.presence_of_element_located((By.XPATH, "//*[@id='Form1']/input[4]"))
            ).send_keys(matrix_row["source_password"])

            # Press Log In
            login_button = WebDriverWait(driver, 60).until(
                EC.element_to_be_clickable((By.XPATH, "//*[@id='Form1']/input[5]"))
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
        # Wait for the new tab to open (this waits for 2 tabs to be open)
        WebDriverWait(driver, 60).until(
            EC.number_of_windows_to_be(2)  # Wait until 2 tabs are open
        )
        print(driver.window_handles)
        # Switch to the new tab (the last handle should correspond to the new tab)
        time.sleep(5)
        driver.switch_to.window(driver.window_handles[0])
        driver.close()
        driver.switch_to.window(driver.window_handles[0])
        time.sleep(10)
        driver.refresh()
        time.sleep(20)

        retries = 0
        max_retries = 3
        while retries < max_retries:
            try:
                # click Individual Health
                ih = WebDriverWait(driver, 90).until(
                    EC.element_to_be_clickable((By.XPATH, "//*[@class='ui-menu-item-wrapper' and text()='Individual Health']"))
                )
                ih.click()
                print("Individual Health clicked")

                # click My Policies
                mp = WebDriverWait(driver, 90).until(
                    EC.element_to_be_clickable((By.XPATH, "//div[normalize-space()='Individual Health']/following-sibling::ul//a[normalize-space()='My Policies']"))
                )
                mp.click()
                print("My Policies clicked")
                break

            except TimeoutException as e:
                retries += 1
                print(f"Attempt {retries} failed. Retrying. Error: {e}")
                # Refresh the page after failure
                driver.refresh()
                # just to ensure that page is properly loaded after refresh
                WebDriverWait(driver, 90).until(
                    EC.presence_of_element_located((By.ID, "ui-id-23"))
                )
    except Exception as e:
        log_error(ERROR_CODES["navigation_error"], "Navigation process failed, could not reach download page.",
                  matrix_row["script_name"])
        print("Navigation process failed to reach download page, ending process.")
        driver.quit()
        return None

    try:
        # click excel icon
        excel_icon = WebDriverWait(driver, 150).until(
            EC.element_to_be_clickable((By.XPATH,
                                        "//a[@class='form-button' and img[@src='https://agb.ociservices.com/images/gridexcel.png']]"))
        )
        excel_icon.click()
        print("Excel icon clicked")

        WebDriverWait(driver, 60).until(
        EC.visibility_of_element_located((By.CLASS_NAME, "ui-dialog-title")))
        print("Export dialog appeared")

        # export to excel all records
        export_excel = WebDriverWait(driver, 150).until(
            EC.element_to_be_clickable((By.XPATH, "//button[normalize-space(text())='All Records']"))
        )
        export_excel.click()
        print("Export to Excel All Records file clicked")
        print("File Downloading. Waiting 3 minutes.")
        time.sleep(180)  # Allow time for download to complete
    except Exception as e:
        log_error(ERROR_CODES["download_button_not_found"], "'Download' button not found.", matrix_row["script_name"])
        print("Download button not found, ending process.")
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
            
            # OTP function
            try:
                WebDriverWait(driver, 60).until(
                    EC.element_to_be_clickable((By.XPATH, "//input[@value='Send Email']"))
                ).click()
                print("OTP sent!")

                otp_code = fetch_otp_code(matrix_row)

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
                EC.presence_of_element_located((By.ID, "agent_id"))
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
            password_field = driver.find_element(By.ID, "password")
            for char in password:
                password_field.send_keys(char)
                time.sleep(random.uniform(0.1, 0.4))  # Random delay between keystrokes
            print("Entered password.")

            # Step 4: Click Submit with a delay to simulate human behavior
            time.sleep(random.uniform(1, 3))  # Random delay before clicking submit
            driver.find_element(By.XPATH, "//input[@type='submit' and @value='Submit']").click()
            print("Clicked on 'Submit'.")

            try:
                # Handle potential CAPTCHA by retyping username and resubmitting
                time.sleep(random.uniform(2, 5))  # Wait for a random time between 2 to 5 seconds
                username_field = WebDriverWait(driver, 30).until(
                    EC.presence_of_element_located((By.ID, "agent_id"))
                )
                username_field.clear()
                for char in username:
                    username_field.send_keys(char)
                    time.sleep(random.uniform(0.1, 0.3))  # Random delay between keystrokes
                print("Retyped username.")

                time.sleep(random.uniform(2, 5))  # Wait for a random time between 2 to 5 seconds

                # Enter password with human-like typing again
                password_field = driver.find_element(By.ID, "password")
                for char in password:
                    password_field.send_keys(char)
                    time.sleep(random.uniform(0.1, 0.4))  # Random delay between keystrokes
                print("Retyped password.")       

                driver.find_element(By.XPATH, "//input[@type='submit' and @value='Submit']").click()
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
            EC.element_to_be_clickable((By.XPATH, "//*[@href='https://pivothealth.com/agent/admin']"))
        ).click()

        # Step 5: Input start date as first day of the current year
        start_date_field = WebDriverWait(driver, 120).until(
            EC.presence_of_element_located((By.ID, "startDate"))
        )
        time.sleep(10)
        start_date_field.clear()
        start_date_field.send_keys(f"{current_year}-01-01")
        print("Start date set to first day of the current year.")

        # Step 6: Click on 'Export Book of Business'
        export_button = WebDriverWait(driver, 120).until(
            EC.element_to_be_clickable((By.XPATH, "//button[@type='submit' and text()='Export Book of Business']"))
        )
        export_button.click()
        print("Clicked on 'Export Book of Business'.")

        # Step 7: Wait for the download to complete
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
    driver.get(matrix_row["source_url"])

    # Perform login if needed
    for attempt in range(3):  # Retry up to 3 times. (Only if code does not return a folder OR an error!)
        if matrix_row["source_login"].upper() == "YES":
            try:
                # Step 2: Log in
                WebDriverWait(driver, 60).until(
                    EC.presence_of_element_located((By.XPATH, "//input[@placeholder='Email']"))
                ).send_keys(matrix_row["source_email"])
                WebDriverWait(driver, 60).until(
                    EC.presence_of_element_located((By.XPATH, "//input[@placeholder='Password']"))
                ).send_keys(matrix_row["source_password"])
                WebDriverWait(driver, 60).until(
                    EC.element_to_be_clickable(
                        (By.CLASS_NAME, 'slds-button.slds-button--brand.loginButton.uiButton--none.uiButton'))
                ).click()
                print("Login submitted.")
                time.sleep(10)

                # Step 3: Retrieve OTP Code
                if matrix_row["otp_needed"].upper() == "YES":
                    try:
                        otp_code = fetch_otp_code(matrix_row)
                        print(f"OTP Code found: {otp_code}")

                        # Step 4: Input & Submit OTP
                        # Input OTP
                        WebDriverWait(driver, 30).until(
                            EC.presence_of_element_located((By.XPATH, '//input[@id="input-46"]'))
                        ).send_keys(otp_code)
                        print("OTP entered.")

                        # Click Submit
                        WebDriverWait(driver, 30).until(
                            EC.element_to_be_clickable(
                                (By.XPATH, '//*[@class="slds-button flow-button__NEXT"]'))
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
            # Step 5: Select Reports
            element = WebDriverWait(driver, 120).until(
                EC.visibility_of_element_located(
                    (By.XPATH,
                     '//*[@href="https://bcbsm.force.com/ibuac/s/report/Report/Recent/Report/?queryScope=everything"]'))
            )
            time.sleep(5)
            driver.execute_script("arguments[0].click();", element)
            print("Clicking reports.")
            time.sleep(10)

            # Step 6: Select Book of Business
            element = WebDriverWait(driver, 120).until(
                EC.visibility_of_element_located(
                    (By.XPATH, '//*[@title="Book of Business"]'))
            )
            time.sleep(5)
            element = WebDriverWait(driver, 120).until(
                EC.element_to_be_clickable((By.XPATH, '//*[@title="Book of Business"]'))
            )

            try:
                element.click()
                print("Clicked 'Book of Business' with regular click")
                time.sleep(15)
            except Exception as e1:
                print(f"Regular click failed, trying JavaScript click... Reason: {e1}")
                try:
                    driver.execute_script("arguments[0].click();", element)
                    print("Clicked on 'BOB' with JS click")
                    time.sleep(15)
                except Exception as e2:
                    print(f"Both click methods failed: {e2}")
                    log_error(ERROR_CODES["navigation_error"], "Could not click 'Book of Business'",
                              matrix_row["script_name"])
                    driver.quit()
                    return None

        except Exception as e:
            log_error(ERROR_CODES["navigation_error"], "Navigation process failed.", matrix_row["script_name"])
            print("Navigation process failed, ending process.")
            driver.quit()
            return None

        # Click the download button
        try:
            # Step 7: Download BOB Report
            # Wait for detection of Report iframe
            WebDriverWait(driver, 30).until(
                EC.presence_of_element_located(
                    (By.XPATH,
                     '//*[@class="isView reportsReportBuilder"]'))
            )
            time.sleep(3)
            # Switch to Report iframe
            driver.switch_to.frame(driver.find_element(By.XPATH, '//*[@title="Report Viewer"]'))

            # Open Export menu
            element = WebDriverWait(driver, 30).until(
                EC.visibility_of_element_located(
                    (By.XPATH,
                     '//*[@class="slds-button slds-button_neutral action-bar-action-ReportExportAction reportAction report-action-ReportExportAction filtersButton"]'))
            )
            driver.execute_script("arguments[0].click();", element)
            print("Opening Export menu.")
            time.sleep(5)

            # Switch out of Report iframe
            driver.switch_to.default_content()

            # Export file
            WebDriverWait(driver, 30).until(
                EC.visibility_of_element_located(
                    (By.XPATH,
                     '//span[text()="Export"]'))
            ).click()
            print("Downloading Report. Waiting 3 minutes for download to finish.")
            time.sleep(180)
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


def run_bob_cigna_aca(driver, matrix_row, date_info):
    print("Running BOB Cigna ACA handler...")

    download_folder = os.path.normpath(matrix_row["download_path"])
    driver.get(matrix_row["source_url"])

    # Perform login if needed
    if matrix_row["source_login"].upper() == "YES":
        try:
            # Step 1: Close Cookie Preferences pop-up
            print("Waiting for cookie preferences pop-up...")
            time.sleep(8)
            try:
                WebDriverWait(driver, 10).until(
                    EC.presence_of_element_located((By.CLASS_NAME, "onetrust-close-btn-handler.onetrust-close-btn-ui.banner-close-button.ot-close-icon"))
                ).click()
                print("Closed cookie preferences pop-up. Continuing.")
            except:
                print("No cookie preferences button found. Continuing.")
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

                    otp_code = fetch_otp_code(matrix_row)

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
                    otp_code = fetch_otp_code(matrix_row)

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
            EC.presence_of_element_located((By.XPATH, '//*[@class="hidden largeTablet:flex flex-1 pb-16"]'))
        )
        action.move_to_element(element).perform()
        print("Hovering over Export dropdown menu")
        time.sleep(1)

        # Click export All Customers (.csv)
        WebDriverWait(driver, 120).until(
            EC.element_to_be_clickable(
                # uuid-XXXXX-X- changes every time
                # (By.XPATH, '//*[@data-menu-id="rc-menu-uuid-31384-3-allRecordsLegacyCsv"]'))
                (By.XPATH, "//*[contains(@data-menu-id,'allRecordsLegacyCsv')]"))
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
                    otp_code = fetch_otp_code(matrix_row)

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
                    otp_code = fetch_otp_code(matrix_row)

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
                    otp_code = fetch_otp_code(matrix_row)

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
            EC.element_to_be_clickable((By.XPATH, "//*[@role='button' and contains(text(), 'Production Reports')]"))
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
            EC.presence_of_element_located((By.XPATH, "//div[@data-grid-row='1']"))
        )
        file_name_button = newest_file_container.find_element(
            By.XPATH, ".//span[@role='button']"
        )
        file_name = file_name_button.text.strip()

        filenames = []
        for num in range(7):
            date = (datetime.now() - timedelta(days=num))
            example_date_production = f"{date.month}.{date.day}.{date.year}"
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
            log_error(ERROR_CODES["target_file_not_found"], f"{full_name_production} was not located on the SMA page.",
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
                    otp_code = fetch_otp_code(matrix_row)

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
        # Step 9: Click "RTS Reports"
        rts_reports_button = WebDriverWait(driver, 10).until(
            EC.element_to_be_clickable((By.XPATH, "//*[@role='button' and contains(text(), 'RTS Reports')]"))
        )
        rts_reports_button.click()
        print("Navigated to 'RTS Reports'.")
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
            EC.presence_of_element_located((By.XPATH, "//div[@data-grid-row='1']"))
        )
        file_name_button = newest_file_container.find_element(
            By.XPATH, ".//span[@role='button']"
        )
        file_name = file_name_button.text.strip()

        filenames = []
        for num in range(5):
            date = (datetime.now() - timedelta(days=num))
            example_date_rts = f"{date.month}.{date.day}.{date.year}"
            full_name_rts = f"{base_name_rts}{example_date_rts}.xlsx"
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
            log_error(ERROR_CODES["target_file_not_found"], f"{full_name_rts} was not located on the SMA page.",
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
            print("Waiting for cookie preferences pop-up...")
            time.sleep(8)
            try:
                WebDriverWait(driver, 10).until(
                    EC.presence_of_element_located((By.CLASS_NAME, "onetrust-close-btn-handler.onetrust-close-btn-ui.banner-close-button.ot-close-icon"))
                ).click()
                print("Closed cookie preferences pop-up. Continuing.")
            except:
                print("No cookie preferences button found. Continuing.")
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
            time.sleep(10)

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
                    otp_code = fetch_otp_code(matrix_row)

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
                print(f"Clicked 'Export' button. Waiting 90 seconds...")

                time.sleep(90)  # Wait for 2 minutes

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
    utc_now = datetime.now(timezone.utc)  # Get current UTC time
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


def run_acu_hcsc(driver, matrix_row, date_info):
    print("Running ACU HCSC handler (SFTP)...")

    local_download_path = os.path.normpath(matrix_row["download_path"])
    sftp_hostname = matrix_row["source_url"]
    sftp_username = matrix_row["source_email"]
    sftp_password = matrix_row["source_password"]
    sftp_port = int(matrix_row["sftp_port"])
    remote_path = matrix_row["remote_path"]
    keywords = {
        "ACU": [matrix_row["extracted_file_prefix"]],  # Replace with actual BOB keyword
    }
    # Define CST timezone
    cst = pytz.timezone('America/Chicago')

    # Get current date and time in CST
    utc_now = datetime.now(timezone.utc)  # Get current UTC time
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



################################################################################ LUCAS TESTS ################################################################################

def run_acu_bcbs_az(driver, matrix_row, date_info):
    print("Running ACU BCBS AZ handler...")

    download_folder = os.path.normpath(matrix_row["download_path"])
    driver.get(matrix_row["source_url"])

    for attempt in range(3):
        if matrix_row["source_login"].upper() == "YES":
            try:
                #Step 1: Navigate to Login Page
                WebDriverWait(driver, 60).until(
                    EC.element_to_be_clickable((By.XPATH, "//nav[@id='navigation-footer']//button[normalize-space()='Login / Register']"))
                ).click()
                print("Clicked on 'Login / Register' button.")
                time.sleep(2)
                
                WebDriverWait(driver, 60).until(
                    EC.element_to_be_clickable((By.XPATH, "//a[contains(@class, 'mainnav-broker-link')]"))
                ).click()
                print("Clicked on 'Broker' link.")
                time.sleep(10)

                #Step 2: Log in
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
                EC.element_to_be_clickable((By.XPATH, "//a[@class='menuLinks' and contains(text(), 'Office User Management')]"))
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
                    (By.XPATH, "//div[normalize-space()='Individual Health']/following-sibling::ul//a[normalize-space()='My Policies']"))
            ).click()
            print("Clicking My Policies.")
            time.sleep(10)
            
            try:
                filters_present = WebDriverWait(driver, 10).until(
                    EC.presence_of_element_located((By.XPATH, "//div[contains(@class, 'filter-button') and (contains(., 'Clear Grid Filters') or contains(., 'Clear Grid Sort'))]"))
                )
                if filters_present:
                    print("Existing filters or sorting detected, proceeding to clear them.")
                    # Clear filters if present
                    try:
                        clear_filters_btn = driver.find_element(By.XPATH, "//div[contains(@class, 'filter-button') and contains(., 'Clear Grid Filters')]")
                        clear_filters_btn.click()
                        print("Cleared existing filters.")
                        time.sleep(5)
                    except Exception:
                        print("No 'Clear Grid Filters' button found.")
                    # Clear sorting if present
                    try:
                        clear_sort_btn = driver.find_element(By.XPATH, "//div[contains(@class, 'filter-button') and contains(., 'Clear Grid Sort')]")
                        clear_sort_btn.click()
                        print("Cleared existing sorting.")
                        time.sleep(5)
                    except Exception:
                        print("No 'Clear Grid Sort' button found.")
                else:
                    print("No filters or sorting in place, proceeding.")
            except Exception:
                print("No filters or sorting in place, proceeding.")

            #Click to filter by carrier
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

            #Step 4: Click to download excel file
            WebDriverWait(driver, 120).until(
                EC.element_to_be_clickable((By.XPATH, "//*[@class='agb-links']//img[@src='https://agb.ociservices.com/images/gridexcel.png']"))
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
            log_error(ERROR_CODES["download_button_not_found"], "'Download' button not found.", matrix_row["script_name"])
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
            
            #Step 4: Click on Broker Appointments tab
            WebDriverWait(driver, 120).until(
                EC.element_to_be_clickable((By.XPATH, "//a[@class='ui-tabs-anchor' and text()='Broker Appointments']"))
            ).click()
            print("Clicked on Broker Appointments tab.")
            time.sleep(10)            
            
            #Step 5: Check if existing filters button and existing sorting is visible
            try:
                filters_present = WebDriverWait(driver, 10).until(
                    EC.presence_of_element_located((By.XPATH, "//div[contains(@class, 'filter-button') and (contains(., 'Clear Grid Filters') or contains(., 'Clear Grid Sort'))]"))
                )
                if filters_present:
                    print("Existing filters or sorting detected, proceeding to clear them.")
                    # Clear filters if present
                    try:
                        clear_filters_btn = driver.find_element(By.XPATH, "//div[contains(@class, 'filter-button') and contains(., 'Clear Grid Filters')]")
                        clear_filters_btn.click()
                        print("Cleared existing filters.")
                        time.sleep(5)
                    except Exception:
                        print("No 'Clear Grid Filters' button found.")
                    # Clear sorting if present
                    try:
                        clear_sort_btn = driver.find_element(By.XPATH, "//div[contains(@class, 'filter-button') and contains(., 'Clear Grid Sort')]")
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

            #Step 7: Click to download excel file
            WebDriverWait(driver, 120).until(
                EC.element_to_be_clickable((By.XPATH, "//*[@id='ttabbrintbroker-appointments']"))
            ).click()
            print("Clicked on Broker Appointments tab.")
            time.sleep(2)
            WebDriverWait(driver, 120).until(
                EC.element_to_be_clickable((By.XPATH, "//*[@class='agb-links']//img[@src='https://agb.ociservices.com/images/gridexcel.png']"))
            ).click()
            print("Clicked on Download Excel icon.")
            time.sleep(2)
            WebDriverWait(driver, 120).until(
                EC.element_to_be_clickable((By.XPATH, "//button[@type='button' and text()='As Displayed']"))
            ).click()
            print("Clicked on As Displayed button.")
            time.sleep(30)  # Wait for download to complete
        except Exception as e:
            log_error(ERROR_CODES["download_button_not_found"], "'Download' button not found.", matrix_row["script_name"])
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

            #Step 4: Click on Broker Appointments tab
            WebDriverWait(driver, 120).until(
                EC.element_to_be_clickable((By.XPATH, "//a[@class='ui-tabs-anchor' and text()='Broker Appointments']"))
            ).click()
            print("Clicked on Broker Appointments tab.")
            time.sleep(10)  

            # Step 5: Check and clear existing filters and sorting
            try:
                WebDriverWait(driver, 10).until(
                    EC.presence_of_element_located((By.XPATH, "//div[contains(@class, 'filter-button') and (contains(., 'Clear Grid Filters') or contains(., 'Clear Grid Sort'))]"))
                )
                print("Existing filters or sorting detected, proceeding to clear them.")

                # Try clearing filters
                for label in ['Clear Grid Filters', 'Clear Grid Sort']:
                    try:
                        button = driver.find_element(By.XPATH, f"//div[contains(@class, 'filter-button') and contains(., '{label}')]")
                        button.click()
                        print(f"Cleared: {label}")
                        time.sleep(5)
                    except Exception:
                        print(f"No '{label}' button found.")
            except Exception:
                print("No filters or sorting in place, proceeding.")
            
            #Step 6: Click to download excel file
            WebDriverWait(driver, 120).until(
                EC.element_to_be_clickable((By.XPATH, "//*[@method='!brint!bacg!ExportToExcel{brinbtca}']/img"))
            ).click()
            print("Clicked on Download Excel icon.")
            WebDriverWait(driver, 120).until(
                EC.element_to_be_clickable((By.XPATH, "//button[@type='button' and text()='All Records']"))
            ).click()
            print("Clicked on All Records button.")
            time.sleep(60)  # Wait for download to complete
        except Exception as e:
            log_error(ERROR_CODES["download_button_not_found"], "'Download' button not found.", matrix_row["script_name"])
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

                #Step 3: Click Next
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

                    otp_code = fetch_otp_code(matrix_row)

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

                #Step 10: Click to download CSV file
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
                        log_error(ERROR_CODES["download_error"], "Download folder empty after wait.", matrix_row["script_name"])
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
                    log_error(ERROR_CODES["general_error"], f"File rename failed: {rename_error}", matrix_row["script_name"])
                    driver.quit()
                    return None

            except Exception as e:
                log_error(ERROR_CODES["download_button_not_found"], f"'Download' button not found. Error: {e}", matrix_row["script_name"])
                print("Download button not found, ending process.")
                driver.quit()
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
    for attempt in range(3):  # Retry up to 3 times. (Only if code does not return a folder OR an error!)
        if matrix_row["source_login"].upper() == "YES":
            try:
                #Step 1: Navigate to login page
                first_window = driver.current_window_handle
                WebDriverWait(driver, 120).until(
                    EC.element_to_be_clickable((By.XPATH, "//*[@data-tid='MainNav-Sign In']"))
                ).click()
                time.sleep(2)
                WebDriverWait(driver, 120).until(
                    EC.element_to_be_clickable((By.XPATH, "//a[@class='ma-signInTypeLink' and @title='Go to Sales Professional Access']"))
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

                    otp_code = fetch_otp_code(matrix_row)

                    # Step 6: Enter OTP into the field
                    otp_input = WebDriverWait(driver, 60).until(
                        EC.presence_of_element_located((By.XPATH, "//*[@name='credentials.passcode']]"))
                    )
                    otp_input.send_keys(otp_code)
                    print("OTP entered!")
                    time.sleep(2)

                    # Step 7: Click Submit OTP button
                    WebDriverWait(driver, 60).until(
                        EC.element_to_be_clickable((By.XPATH, "//*[@data-type='save']"))
                    ).click()
                    print("OTP submitted!")
                except FileNotFoundError:
                    log_error(ERROR_CODES["OTP_error"], "OTP File was not found or was not submitted correctly.",
                            matrix_row["script_name"])
                    print("OTP File was not found or was not submitted correctly. Exiting...")
                    driver.quit()
                    return None

                time.sleep(10)
            except Exception as e:
                log_error(ERROR_CODES["login_error"], "Login page timeout or login fields not found.",
                          matrix_row["script_name"])
                print("Login page not found or timeout occurred. Exiting...")
                driver.quit()
                return None

            # Step 8: Navigate to download page
            try:
                third_window = driver.current_window_handle
                WebDriverWait(driver, 120).until(
                    EC.element_to_be_clickable((By.XPATH, "//a[@id='bookOfBusinessDownloadLink']"))
                ).click()
                print("Clicked on Book of Business Download and another tab opens.")
                time.sleep(10)

                for handle in driver.window_handles:
                    if handle != third_window:
                        driver.switch_to.window(handle)
                        break
                print("Switched to Download page.")
            except TimeoutException:
                print("Failed to switch tab.")
                return None

            #Step 9: Select 'All In-Forces Policies' option
            
            try:
                WebDriverWait(driver, 120).until(
                    EC.element_to_be_clickable((By.XPATH, "//div[normalize-space()='All In-Force Policies']/preceding-sibling::span"))
                ).click()
                print("Selected 'All In-Force Policies' option.")

                WebDriverWait(driver, 120).until(
                    EC.element_to_be_clickable((By.XPATH, "//button[@data-tid='bob-download-button']"))
                ).click()
                print("Clicked on Download button to download file.")
                time.sleep(60)  # Wait for download to complete
            except Exception as e:
                log_error(ERROR_CODES["download_button_not_found"], "'Download' button not found.", matrix_row["script_name"])
                print("Download button not found, ending process.")
                driver.quit()
            return None
        return download_folder
    print("RPA process failed 3 attempts. Skipping carrier.")
    log_error(ERROR_CODES["general_error"], "RPA process failed 3 attempts.", matrix_row["script_name"])
    return None


def run_bob_zing(driver, matrix_row, date_info):
    print("Running BOB Zing Health handler...")

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

            login_button = WebDriverWait(driver, 30).until(
                EC.element_to_be_clickable((By.ID, "submit"))
            )
            login_button.click()

            print("Login submitted.")
            time.sleep(10)

        # STEP 2: Handle password expiry modal if present
        print("Checking for password expiry modal...")
        try:
            continue_login_button = WebDriverWait(driver, 5).until(
                EC.element_to_be_clickable((By.ID, "goToDomain"))
            )
            continue_login_button.click()
            print("Password expiry modal dismissed.")
            time.sleep(10)
        except TimeoutException:
            print("No password expiry modal.")

        # STEP 3: Confirm Agility profile
        print("Confirming Agility profile...")
        WebDriverWait(driver, 30).until(
            EC.presence_of_element_located(
                (By.XPATH, "//*[contains(text(), 'Agility Insurance Services')]")
            )
        )
        print("Agility profile confirmed.")

        # STEP 4: Navigate to Book of Business
        print("Opening Book of Business...")
        book_of_business_dropdown = WebDriverWait(driver, 30).until(
            EC.element_to_be_clickable(
                (
                    By.XPATH,
                    "//a[contains(@href, 'member_bob') or contains(normalize-space(), 'Book of Business')]"
                )
            )
        )
        book_of_business_dropdown.click()
        time.sleep(5)

        book_of_business_link = WebDriverWait(driver, 30).until(
            EC.element_to_be_clickable(
                (
                    By.XPATH,
                    '//div/div/a[@href="/portal/member_search.htm"]'
                )
            )
        )
        book_of_business_link.click()
        time.sleep(5)
        href = "/portal/member_search.htm"

        # STEP 5: Click Search with default filters
        print("Clicking Search...")
        search_button = WebDriverWait(driver, 60).until(
            EC.element_to_be_clickable((By.ID, "submit"))
        )
        search_button.click()

        print("Waiting for results...")
        time.sleep(60)

        WebDriverWait(driver, 60).until(
            EC.presence_of_element_located((By.ID, "portal_members"))
        )

        try:
            info = driver.find_element(By.ID, "portal_members_info").text
            print(f"Results: {info}")
        except Exception:
            print("Results loaded.")

        time.sleep(3)

        # STEP 6: Download CSV
        print("Clicking Download...")
        download_button = WebDriverWait(driver, 60).until(
            EC.element_to_be_clickable((By.ID, "dButton"))
        )
        download_button.click()

        print("Waiting for file download...")
        time.sleep(45)

        log_success()
        return download_folder

    except Exception as e:
        log_error(
            ERROR_CODES["general_error"],
            f"BOB Zing Health handler failed: {e}",
            script_name
        )
        print(f"BOB Zing Health handler failed: {e}")
        driver.quit()
        return None




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

handler_map = {
    "ACU_NewEra_RPA": run_acu_newera,
    "COMM_Ambetter_RPA": run_comm_ambetter,
    "COMM_Anthem_RPA": run_comm_anthem,
    "COMM_Oscar_GA_RPA": run_comm_oscar_ga,
    "COMM_Oscar_SUBS_RPA": run_comm_oscar_subs,
    "COMM_Molina_RPA": run_comm_molina,
    "ACU_Ambetter_RPA": run_acu_ambetter,
    #"ACU_Aetna_RPA":run_acu_aetna,
    #"ACU_AetnaSenior_Supp_RPA":run_acu_aetnasenior_supp,
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
    "BOB_Ambetter_RPA": run_bob_ambetter,
    "BOB_Amerihealth_RPA": run_bob_amerihealth,
    "BOB_Anthem_RPA": run_bob_anthem,
    "BOB_Caresource_RPA": run_bob_caresource,
    "BOB_geoblue_RPA": run_bob_geoblue,
    "BOB_Gerber_RPA": run_bob_gerber,
    "BOB_ManhattanLife_RPA": run_bob_manhattanlife,
    "BOB_Medica_RPA": run_bob_medica,
    "BOB_Molina_RPA": run_bob_molina,
    "BOB_NewEra_RPA": run_bob_newera,
    "BOB_Oscar_RPA": run_bob_oscar,
    "BOB_Pivot_RPA": run_bob_pivot,
    "BOB_BCBS_MI_RPA": run_bob_bcbs_mi,
    "BOB_Cigna_ACA_RPA": run_bob_cigna_aca,
    "BOB_Ethos_RPA": run_bob_ethos,
    "BOB_KelseyCare_RPA": run_bob_kelseycare,
    "BOB_PriorityHealth_RPA": run_bob_priorityhealth,
    "BOB_SMA_RPA": run_bob_sma,
    "ACU_SMA_RPA": run_acu_sma,
    "ACU_Cigna_RPA": run_acu_cigna,
    "BOB_Devoted_RPA": run_bob_devoted,
    "ACU_Devoted_RPA": run_acu_devoted,
    "BOB_HCSC_RPA": run_bob_hcsc,
    "ACU_HCSC_RPA": run_acu_hcsc,
    ##### New carriers ####
    "ACU_BCBS_AZ_RPA": run_acu_bcbs_az,
    "BOB_BCBS_NE_RPA": run_bob_bcbs_ne,
    "ACU_BCBS_NE_RPA": run_acu_bcbs_ne,
    "ACU_Medica_RPA": run_acu_medica,
    "BOB_AmeritasDentalandVision_RPA": run_bob_ameritas_dentalandvision,
    "BOB_MutualofOmaha_RPA": run_bob_mutualofomaha,
    "BOB_Zing_RPA": run_bob_zing,

    "__default__": lambda *args, **kwargs: print("No valid handler matched.")
}
