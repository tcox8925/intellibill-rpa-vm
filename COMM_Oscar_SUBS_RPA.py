import os
from logger import setup_logger, log_error, ERROR_CODES,log_success,log_final_entry
from rpa_matrix_reader import read_rpa_matrix
from date_utils import get_current_date_info
from chrome_utils import get_chrome_driver  # ✅ Using the Chrome Utils Module
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.common.by import By
from selenium.common.exceptions import TimeoutException
import time
from azure_blob_utils import upload_blob, authenticate_blob_storage
from datetime import datetime
from file_utils import unzip_file
from email_utils import send_email_notification,get_email_recipients
from date_utils import get_current_month_year
from selenium.webdriver.common.keys import Keys

# Read the RPA matrix
rpa_matrix = read_rpa_matrix()
rpa_matrix = rpa_matrix.astype(str).apply(lambda x: x.str.strip())  # Clean DataFrame

# Define filter criteria
process_name_filter = "COMM"
carrier_id_filter = "2931751000020024152"
download_options = ["Yes", "No"]



for more_than_one_download in download_options:
    # Filter the matrix
    matrix_row = rpa_matrix[
        (rpa_matrix["process_name"] == process_name_filter) &
        (rpa_matrix["carrier_id"] == carrier_id_filter) &
        (rpa_matrix["more_than_one_download"]==more_than_one_download)
    ]

    if matrix_row.empty:
        print(f"No matching row found for process_name: {process_name_filter} and carrier_id: {carrier_id_filter}.")
        exit(1)

    matrix_row = matrix_row.iloc[0]  # Convert DataFrame row to a dictionary-like Series

    # Extract script_name dynamically from matrix
    script_name = matrix_row["script_name"]
    script_name = setup_logger(script_name)  # ✅ Logger setup using the script_name

    # Get dynamic date variables
    date_info = get_current_date_info()
    current_month_year = date_info["current_month_year"]
    current_year = date_info["current_year"]
    current_month_number = date_info["current_month_number"]
    #current_month_number = "01" # 🔹 Hardcoding for testing purposes
    current_month_short = date_info["current_month_short"]
    #current_month_year = "Jan 2025"  # 🔹 Hardcoding for testing purposes
    #first_of_prev_month = date_info["first_of_prev_month"]
    first_of_curr_month = date_info["first_of_month"]

    # **Dynamic Configurations from the Matrix**
    table_url = matrix_row["source_url"]  # URL to navigate
    download_folder = os.path.normpath(matrix_row["download_path"])
    #zip_file_path = os.path.join(download_folder, f"{matrix_row['file_prefix']}.csv")  # ZIP file path
    #csv_file_path = os.path.join(download_folder, f"{matrix_row['extracted_file_prefix']}.{matrix_row['extracted_file_extension']}")  # CSV file path

    # **Extract WebDriver Preferences**
    use_profile_path = matrix_row["use_profile_path"].upper() == "YES"
    profile_path = matrix_row["profile_path"] if use_profile_path else None

    # **Initialize WebDriver using chrome_utils.py ✅**
    driver = get_chrome_driver(profile_path=profile_path, download_folder=download_folder)
    print(f"WebDriver initialized successfully. Navigating to {table_url}...")

    # **Search for the Downloaded File Using "Contains" Instead of a Fixed Name**
    filename_contains = matrix_row["extracted_file_prefix"].strip()
    file_extension = matrix_row["extracted_file_extension"].strip().lower()

    # **Navigate to the URL**
    driver.get(table_url)
    # Extract login details from the matrix
    log_in_required = matrix_row["source_login"].upper() == "YES"
    email = matrix_row["source_email"]
    password = matrix_row["source_password"]

    if log_in_required:
        try:
            print("Attempting to log in...")

            # Wait for the email input field
            email_field = WebDriverWait(driver, 10).until(
                EC.presence_of_element_located((By.XPATH, "//input[@name='email']"))
            )
            email_field.send_keys(email)

            # Enter password
            password_field = driver.find_element(By.XPATH, "//input[@name='password']")
            password_field.send_keys(password)

            # Click login button
            login_button = driver.find_element(By.XPATH, "/html/body/div/div/div/div/div/form/button")
            login_button.click()

            print("Logged in successfully.")
            time.sleep(10)



        except TimeoutException:
            print("Login not required or failed.")
            log_error(ERROR_CODES["login_error"], "Login failed or not required.", script_name)

    else:
        print("No login required. Proceeding...")


    try:
            oscar_for_business_link = WebDriverWait(driver, 20).until(
                EC.element_to_be_clickable((By.XPATH, "//a[@title='Oscar For Business']"))
            )
            oscar_for_business_link.click()
            print("Clicked on 'Oscar For Business'.")
            time.sleep(10)
    except TimeoutException:
            log_error(ERROR_CODES["process_interrupted"], "'Oscar For Business' link not found.", script_name)

    try:
        print("🔍 Checking for welcome popup...")
        popup_button = WebDriverWait(driver, 5).until(
            EC.element_to_be_clickable((By.XPATH, "//button[contains(text(), 'Done')]"))
        )
        driver.execute_script("arguments[0].click();", popup_button)
        print("✅ Popup closed successfully.")

    except TimeoutException:
        print("🔹 No popup detected. Moving forward.")

    time.sleep(20)

    try:
        print("🔍 Checking for second 'Go to book' popup...")

        # Wait for the close button (X) in the second popup
        popup_close_button = WebDriverWait(driver, 5).until(
            EC.element_to_be_clickable((By.XPATH, "//button[@aria-label='Close']"))
        )
        driver.execute_script("arguments[0].click();", popup_close_button)
        print("✅ Closed second popup.")

    except TimeoutException:
        print("🔹 Second popup not found. Moving forward.")

    time.sleep(20)

    try:
        menu_link = WebDriverWait(driver, 20).until(
            EC.element_to_be_clickable((By.XPATH,"//a[@class='h-RR8CaZ7LFRek__CMKTZh h-XNn6n95xd75HHq5fBk7B']"))
        ).click()
    except TimeoutException:
        log_error(ERROR_CODES["link_not_found"], "Menu not found.", script_name)


    time.sleep(5)

    try:
        print("🔍 Waiting for 'Commissions' link to be clickable...")

        # Wait for all matching links to load
        comms_links = WebDriverWait(driver, 30).until(
            EC.presence_of_all_elements_located((By.XPATH, "//a[contains(@class, 'h-V9HpjLEp4EvaJfWvVq3B')]"))
        )

        # Determine index based on 'download_options'
        if matrix_row["more_than_one_download"].strip().upper() == "YES":
            index = 0  # Click the first link
        else:
            index = 1  # Click the second link

        # Ensure we have enough links before attempting to click
        if len(comms_links) > index:
            comms_link = comms_links[index]  # Select the correct link
            print(f"✅ 'Commissions' link found! Clicking index [{index}].")

            driver.execute_script("arguments[0].scrollIntoView();", comms_link)
            time.sleep(1)
            driver.execute_script("arguments[0].click();", comms_link)

            print("✅ Clicked 'Commissions' link successfully.")
    except TimeoutException:
            print("❌ 'Commissions' link not found at expected index.")
            log_error(ERROR_CODES["link_not_found"], "Commissions Link not found at expected index.", script_name)


    time.sleep(10)

    try:
        print("🔍 Waiting for 'Filter by Year' dropdown...")

        # Locate the dropdown button
        dropdown_button = WebDriverWait(driver, 20).until(
            EC.presence_of_element_located((By.XPATH, "//button[contains(@aria-owns, 'filter-by-year')]"))
        )

        # Check if the dropdown already shows the correct year
        current_year_label = driver.find_element(By.XPATH, "//div[contains(@class, 'h-MN8A3cVo21zWfTWXnG1M')]//label[starts-with(@id, 'dropdown-value-')]").text.strip()

        if current_year_label != current_year:
            print(f"📌 Current selected year is '{current_year_label}', selecting {current_year}...")

            # Scroll into view & Click using JavaScript
            driver.execute_script("arguments[0].scrollIntoView();", dropdown_button)
            time.sleep(1)
            driver.execute_script("arguments[0].click();", dropdown_button)

            print("✅ Clicked 'Filter by Year' dropdown successfully.")

            # Wait for dropdown options to appear
            time.sleep(2)

            # Select the first option (Index 0)
            print("🔍 Selecting first option...")
            year_options = WebDriverWait(driver, 10).until(
                EC.presence_of_all_elements_located((By.XPATH, "//li[@role='option']"))
            )

            if year_options:
                first_option = year_options[0]  # Select the first option
                driver.execute_script("arguments[0].scrollIntoView();", first_option)
                time.sleep(1)  # Small pause
                driver.execute_script("arguments[0].click();", first_option)  # Click using JS
                print("✅ Selected first option successfully.")
            else:
                print("❌ No options found in the dropdown.")
                log_error(ERROR_CODES.get("dropdown_error", "E_UNKNOWN"), "Dropdown options not found.", script_name)

        else:
            print(f"✅ '{current_year}' is already selected. No need to change.")

    except TimeoutException:
        print("❌ 'Filter by Year' dropdown not found.")
        log_error(ERROR_CODES.get("dropdown_error", "E_UNKNOWN"), "Filter by Year dropdown not found.", script_name)

    time.sleep(10)

    if more_than_one_download == "No":
        try:
            print("🔍 Waiting for 'Select a Payee' dropdown...")

            # Locate the dropdown button
            payee_dropdown_button = WebDriverWait(driver, 20).until(
                EC.presence_of_element_located((By.XPATH, "//button[contains(@aria-owns, 'select-a-payee')]"))
            )

            # Get the currently selected payee
            current_payee_label = driver.find_element(By.XPATH, "//label[starts-with(@id, 'dropdown-label-') and contains(text(), 'Select a payee')]").text.strip()
            current_payee = driver.find_element(By.XPATH, "//label[starts-with(@id, 'dropdown-label-') and contains(text(), 'Select a payee')]")

            if current_payee_label != "Agility Insurance Services":
                print(f"📌 Current selected payee is '{current_payee_label}', selecting 'Agility Insurance Services'")

                # Scroll into view & Click using JavaScript
                driver.execute_script("arguments[0].scrollIntoView();", current_payee)
                time.sleep(5)
                driver.execute_script("arguments[0].click();", current_payee)

                print("✅ Clicked 'Select a Payee' dropdown successfully.")

                # Wait for dropdown options to appear
                time.sleep(10)

                # Select "Agility Insurance Services"
                print("🔍 Searching for 'Agility Insurance Services'...")
                payee_option = WebDriverWait(driver, 10).until(
                    EC.element_to_be_clickable((By.XPATH, "//li[@role='option']/div[contains(text(), 'Agility Insurance Services')]"))
                )

                # Click the option
                driver.execute_script("arguments[0].scrollIntoView();", payee_option)
                time.sleep(1)
                driver.execute_script("arguments[0].click();", payee_option)

                print("✅ Successfully selected 'Agility Insurance Services'.")

            else:
                print(f"✅ 'Agility Insurance Services' is already selected. No need to change.")

        except TimeoutException:
            print("❌ 'Select a Payee' dropdown not found.")
            log_error(ERROR_CODES.get("dropdown_error", "E_UNKNOWN"), "Select a Payee dropdown not found.", script_name)

    time.sleep(10)


    try:
        print("📊 Waiting for the commission table to load...")

        # **Step 1: Ensure Table is Visible**
        table = WebDriverWait(driver, 20).until(
            EC.presence_of_element_located((By.XPATH, "//table[contains(@class, 'h-vByaJeB2ClPYjOMVsT4a')]"))
        )
        print("✅ Table loaded successfully.")

        # **Step 2: Locate all visible rows in the table**
        rows = WebDriverWait(driver, 20).until(
            EC.presence_of_all_elements_located((By.XPATH, "//table[contains(@class, 'h-vByaJeB2ClPYjOMVsT4a')]/tbody/tr"))
        )

        if not rows:
            print("⚠ No rows found in the table.")
            log_error(ERROR_CODES["table_error"], "No rows found in the commission table.", script_name)
            driver.quit()
            exit()

        print(f"🔍 Found {len(rows)} rows. Checking the first row...")

        # **Step 3: Extract "Payment Sent" Date from first row**
        first_row = rows[0]  # Select the first row

        try:
            # **Locate the "Payment Sent" date element**
            payment_sent_element = first_row.find_element(By.XPATH, "./td[3]//div[contains(@class, 'h-EZI01T80ueSUTCG7eqH5')]")
            payment_sent_text = payment_sent_element.text.strip()  # Example: "Jan 21, 2025"
            print(f"📅 Checking row: Full Payment Sent Date = {payment_sent_text}")

            # **Strictly Extract Only "month year" from "date"**
            payment_sent_parts = payment_sent_text.split()
            if len(payment_sent_parts) >= 3:
                payment_month_year = f"{payment_sent_parts[0]} {payment_sent_parts[2]}"  # Extracts "Month Year"
            else:
                payment_month_year = payment_sent_text  # Fallback

            print(f"📆 Extracted Payment Sent Month-Year: {payment_month_year}")

        except Exception as e:
            print(f"⚠ Error extracting payment sent date: {e}")
            log_error(ERROR_CODES["table_error"], f"Error extracting payment sent date: {e}", script_name)
            driver.quit()
            exit()

        # **Step 4: Check if the row is for the current month**
        if payment_month_year == current_month_year:
            print(f"✅ Matching row found for {current_month_year}")

            # **Step 5: Click the "Payment Sent" Date to Expand the Row**
            try:
                driver.execute_script("arguments[0].scrollIntoView();", payment_sent_element)
                time.sleep(1)  # Pause to ensure it's fully visible
                driver.execute_script("arguments[0].click();", payment_sent_element)
                print("🔽 Clicked on Payment Sent date to expand the row.")
                time.sleep(3)  # Allow time for content to load
            except Exception as e:
                print(f"⚠ Error clicking 'Payment Sent' date: {e}")
                log_error(ERROR_CODES["table_error"], f"Error clicking 'Payment Sent' date: {e}", script_name)
                driver.quit()
                exit()

            # **Step 6: Locate & Click the Download Button**
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
                log_error(ERROR_CODES["download_error"], f"Error clicking download button: {e}", script_name)

        else:
            print(f"⚠ No matching row found for {current_month_year}. Skipping.")

    except Exception as e:
        print(f"❌ Error processing table rows: {e}")
        log_error(ERROR_CODES["download_error"], f"Error processing table rows: {e}", script_name)


    # Ensure Blob Storage authentication succeeded
    blob_service_client = authenticate_blob_storage()
    if not blob_service_client:
        print("Failed to authenticate to Azure Blob Storage. Exiting...")
        exit(1)

    # **Search for the Downloaded File Using "Contains" Instead of a Fixed Name**
    filename_contains = matrix_row["extracted_file_prefix"].strip()
    file_extension = matrix_row["extracted_file_extension"].strip().lower()

    # **Construct the File Path Dynamically Using "Contains"**
    matching_files = [
        os.path.join(download_folder, f)
        for f in os.listdir(download_folder)
        if filename_contains in f and f.lower().endswith(f".{file_extension}")
    ]

    if matching_files:
        csv_file_path = max(matching_files, key=os.path.getmtime)  # Pick the most recent matching file
        print(f"✅ Found file: {csv_file_path}")
    else:
        print(f"❌ No matching file found in {download_folder} containing '{filename_contains}'")
        log_error(ERROR_CODES["download_error"], "CSV file not found.", script_name)
        driver.quit()
        exit()

    # Extract relevant details from the matrix
    rename_base = matrix_row["rename_base"].strip()  # Base name for renaming files
    blob_base_path = matrix_row["blob_base_path"].strip()  # Base path in Blob Storage
    requires_extraction = matrix_row["requires_extraction"].upper() == "YES"  # Whether extraction is required


    def upload_and_cleanup(blob_service_client, zip_file_path, csv_file_path, rename_base, blob_base_path, requires_extraction):
        """
        Extracts, renames, uploads to Blob Storage, and deletes local files after successful upload.
        """
        try:
            # **Step 1: Extract ZIP if required**
            if requires_extraction:
                print(f"Extracting ZIP file: {zip_file_path} ...")
                unzip_file(zip_file_path, os.path.dirname(csv_file_path))  # ✅ Extract to download folder
                print(f"Extraction complete. Extracted files are in: {os.path.dirname(csv_file_path)}")

            # **Step 2: Rename the extracted CSV file**
            renamed_csv_file = os.path.join(os.path.dirname(csv_file_path), f"{rename_base}{first_of_curr_month}_test.xlsx")

            # Ensure CSV file exists before renaming
            if os.path.exists(csv_file_path):
                os.rename(csv_file_path, renamed_csv_file)
                print(f"✅ Renamed file: {csv_file_path} ➡️ {renamed_csv_file}")
            else:
                print(f"❌ CSV file not found: {csv_file_path}")
                log_error(ERROR_CODES["general_error"], f"CSV file not found before rename: {csv_file_path}", script_name)
                return

            # **Step 3: Define Blob Path**
            current_date_info = get_current_date_info()
            blob_folder = f"{blob_base_path}{date_info['current_year']} {date_info['current_month_number']} {date_info['current_month_short']}/"
            blob_path = f"{blob_folder}{os.path.basename(renamed_csv_file)}"

            # **Step 4: Validate Local File Exists Before Upload**
            if not os.path.exists(renamed_csv_file):
                print(f"❌ File does not exist: {renamed_csv_file}")
                log_error(ERROR_CODES["general_error"], f"File missing before upload: {renamed_csv_file}", script_name)
                return

            # **Step 5: Upload to Blob Storage using `upload_blob()`**
            print(f"🔍 Uploading file: {renamed_csv_file} to Blob Path: {blob_path}")
            upload_blob(blob_service_client, container_name="834analytics-dev", local_file_path=renamed_csv_file, blob_path=blob_path)

            # **Step 6: Verify File Exists in Blob Storage**
            blob_client = blob_service_client.get_blob_client(container="834analytics-dev", blob=blob_path)
            if blob_client.exists():
                print(f"✅ Verified: File successfully uploaded to Blob Storage: {blob_path}")
            else:
                print(f"❌ Upload verification failed. File not found in Blob Storage: {blob_path}")
                log_error(ERROR_CODES["general_error"], f"Upload failed, file not in Blob Storage: {blob_path}", script_name)
                return

            # **Step 7: Clean Up Local Files After Successful Upload**
            if os.path.exists(renamed_csv_file):
                os.remove(renamed_csv_file)
                print(f"🗑️ Deleted local CSV file: {renamed_csv_file}")
            else:
                print(f"⚠ CSV file already deleted or not found: {renamed_csv_file}")

            if requires_extraction and os.path.exists(zip_file_path):
                os.remove(zip_file_path)
                print(f"🗑️ Deleted local ZIP file: {zip_file_path}")
            elif requires_extraction:
                print(f"⚠ ZIP file already deleted or not found: {zip_file_path}")

            # **Step 8: Log Success**
            log_success()
            print("🎉 File processing & cleanup completed successfully!")


        except Exception as e:
            print(f"❌ Error in upload/cleanup process: {e}")
            log_error(ERROR_CODES["general_error"], f"Upload/cleanup error: {e}", script_name)
            exit(1)


    try:
        #first_of_month = datetime.now().replace(day=1).strftime("%m%d%Y")
        file_name = f"{matrix_row['rename_base'].strip()}{first_of_curr_month}_test.csv"

        # ✅ **After Successful Upload & Cleanup**
        upload_and_cleanup(
            blob_service_client=blob_service_client,
            zip_file_path=" ",
            csv_file_path=csv_file_path,
            rename_base=matrix_row["rename_base"].strip(),
            blob_base_path=matrix_row["blob_base_path"].strip(),
            requires_extraction=matrix_row["requires_extraction"].upper() == "YES"
        )

        # **Extract notification details from the matrix**
        notification_process = matrix_row["notification_process"].strip()
        flow_url = matrix_row["pautomate_url"].strip()

        # **Get email recipients (to & cc)**
        email_recipients = get_email_recipients(notification_process)

        # **Prepare dynamic values**

        folder_path = f"{blob_base_path}{current_year} {current_month_number} {current_month_short}/"

        # **Call send_email_notification with values populated**
        send_email_notification(
            flow_url=flow_url,
            process_name=script_name,
            notification_process=notification_process,
            to=email_recipients["to"],  # ✅ Passing the real to-list
            cc=email_recipients["cc"],  # ✅ Passing the real cc-list
            file_name=file_name,
            folder_path=folder_path
        )

        log_final_entry(script_name)

    except Exception as e:
        print(f"❌ Error during file upload or notification: {e}")