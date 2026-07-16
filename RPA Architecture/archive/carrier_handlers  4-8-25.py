from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.common.by import By
from selenium.common.exceptions import TimeoutException
from selenium.webdriver.common.keys import Keys
import time
from datetime import datetime
import os
from logger import log_error, ERROR_CODES

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
            login_button = driver.find_element(By.XPATH, "//*[@id='centerPanel']/div/div[2]/div/div[2]/div/div[3]/button")
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
        #print(current_month_year)
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
            year_label = driver.find_element(By.XPATH, "//div[contains(@class, 'h-MN8A3cVo21zWfTWXnG1M')]//label[starts-with(@id, 'dropdown-value-')]").text.strip()

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
                EC.presence_of_all_elements_located((By.XPATH, "//table[contains(@class, 'h-vByaJeB2ClPYjOMVsT4a')]/tbody/tr"))
            )

            if not rows:
                print("No commission rows found.")
                return None

            first_row = rows[0]
            payment_sent_element = first_row.find_element(By.XPATH, "./td[3]//div[contains(@class, 'h-EZI01T80ueSUTCG7eqH5')]")
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

            current_payee_label = driver.find_element(By.XPATH, "//label[contains(text(), 'Select a payee')]").text.strip()

            if current_payee_label != "Agility Insurance Services":
                print("Selecting 'Agility Insurance Services' from dropdown...")
                payee_label = driver.find_element(By.XPATH, "//label[contains(text(), 'Select a payee')]")
                driver.execute_script("arguments[0].scrollIntoView();", payee_label)
                time.sleep(1)
                driver.execute_script("arguments[0].click();", payee_label)

                payee_option = WebDriverWait(driver, 10).until(
                    EC.element_to_be_clickable((By.XPATH, "//li[@role='option']/div[contains(text(), 'Agility Insurance Services')]"))
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
                EC.presence_of_all_elements_located((By.XPATH, "//table[contains(@class, 'h-vByaJeB2ClPYjOMVsT4a')]/tbody/tr"))
            )

            if not rows:
                print("⚠ No rows found in the table.")
                return None

            print(f"🔍 Found {len(rows)} rows. Checking the first row...")
            first_row = rows[0]

            try:
                payment_sent_element = first_row.find_element(By.XPATH, "./td[3]//div[contains(@class, 'h-EZI01T80ueSUTCG7eqH5')]")
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
                EC.element_to_be_clickable((By.XPATH, '//*[@id="domain_container"]/div/div[2]/div/div[2]/div'))
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
                print(f"Stmt Date {stmt_month}/{stmt_year} does not match the current month {current_month_number}/{current_year}. Skipping download.")
        except Exception as e:
            print(f"Error processing the row: {e}")
            return None

    except Exception as e:
        print(f"Unhandled error in Molina handler: {e}")
        return download_folder

def run_acu_ambetter(driver, matrix_row, date_info):
    print("Running COMM Ambetter handler...")

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
            login_button = driver.find_element(By.XPATH, "//*[@id='centerPanel']/div/div[2]/div/div[2]/div/div[3]/button")
            login_button.click()
            print("Logged in successfully!")
            time.sleep(30)
        except TimeoutException:
            log_error(ERROR_CODES["login_error"], "Login page timeout or login fields not found.")
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
    except TimeoutException:
        log_error(ERROR_CODES["download_button_not_found"], "'Download' button not found.")
        print("Download button not found, ending process.")
        driver.quit()
        return None

    return download_folder


handler_map = {
    "COMM_Ambetter_RPA": run_comm_ambetter,
    "COMM_Anthem_RPA": run_comm_anthem,
    "COMM_Oscar_GA_RPA": run_comm_oscar_ga,
    "COMM_Oscar_SUBS_RPA": run_comm_oscar_subs,
    "COMM_Molina_RPA": run_comm_molina,
    #"ACU_Ambetter_RPA":run_acu_ambetter,
    "__default__": lambda *args, **kwargs: print("No valid handler matched.")
}

