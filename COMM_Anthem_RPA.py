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

# Read the RPA matrix
rpa_matrix = read_rpa_matrix()
rpa_matrix = rpa_matrix.astype(str).apply(lambda x: x.str.strip())  # Clean DataFrame

# Define filter criteria
process_name_filter = "COMM"
carrier_id_filter = "2931751000113881001"

# Filter the matrix
matrix_row = rpa_matrix[
    (rpa_matrix["process_name"] == process_name_filter) &
    (rpa_matrix["carrier_id"] == carrier_id_filter)
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
current_month_short = date_info["current_month_short"]
#current_month_year = "Jan 2025"  # 🔹 Hardcoding for testing purposes
first_of_prev_month = date_info["first_of_prev_month"]

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
            EC.presence_of_element_located((By.XPATH, "//input[@name='username']"))
        )
        email_field.send_keys(email)

        # Enter password
        password_field = driver.find_element(By.XPATH, "//input[@name='password']")
        password_field.send_keys(password)

        # Click login button
        login_button = driver.find_element(By.XPATH, "/html/body/app-root/feature-toggle-provider/app-main/div/div/app-login/div/div[2]/div[2]/div[1]/div/div[2]/form/div[2]/div[1]/button")
        login_button.click()

        print("Logged in successfully.")
        time.sleep(10)

    except TimeoutException:
        print("Login not required or failed.")
        log_error(ERROR_CODES["login_error"], "Login failed or not required.", script_name)

else:
    print("No login required. Proceeding...")



try:
    print("Checking the dashboard status...")

    dashboard_text = WebDriverWait(driver, 10).until(
        EC.presence_of_element_located((By.XPATH, "//p[@class='paragraph css-lbl8vh-cssText']"))
    ).text.strip()

    if dashboard_text != "Switch To Medicare Dashboard":
        print("🔀 Switching to All Markets Dashboard...")
        all_markets_button = driver.find_element(By.XPATH, "//p[contains(text(), 'Switch To All Markets Dashboard')]")
        all_markets_button.click()
        time.sleep(5)  # Allow time for switch
    else:
        print("Already in Medicare Dashboard.")

except Exception as e:
    print(f"Error while switching dashboard: {e}")

# Step 2: Navigate to "Book of Business"
try:
    print("Navigating to 'Book of Business'...")

    book_of_business = WebDriverWait(driver, 10).until(
        EC.element_to_be_clickable((By.XPATH, "//div[@title='Book of Business']"))
    )
    book_of_business.click()
    time.sleep(3)

    # Click on "Commissions" from the dropdown
    commissions_option = WebDriverWait(driver, 10).until(
        EC.element_to_be_clickable((By.XPATH, "//li[@id='mnuCommissions']"))
    )
    commissions_option.click()
    print("'Commissions' selected.")

except Exception as e:
    print(f"Error while navigating to 'Commissions': {e}")

# Step 3: Navigate to Summary List View
try:
    print("Waiting for Summary List View to load...")

    summary_list = WebDriverWait(driver, 20).until(
        EC.presence_of_element_located((By.CLASS_NAME, "commissionSummaryListView"))
    )
    print("Summary List View loaded.")

except TimeoutException:
    print("Error: Summary List View did not load.")
    log_error(ERROR_CODES["general_error"], "Summary List View not found.", script_name)
    driver.quit()
    exit()

# Step 4: Locate & Process the First Row
try:
    print("Searching for commission rows...")

    # Locate all rows inside the summary list
    rows = WebDriverWait(driver, 30).until(
        EC.presence_of_all_elements_located((By.CSS_SELECTOR, ".row-cont"))
    )

    if not rows:
        print("No rows found in Summary List.")
        log_error(ERROR_CODES["general_error"], "No commission rows found.", script_name)
        driver.quit()
        exit()

    print(f"Found {len(rows)} rows. Checking for the correct period...")

    matching_row_found = False
    for row in rows:
        try:
            # **Extract Period Column Value**
            period_element = row.find_element(By.CSS_SELECTOR, "#Period-columnAndValue .columnValue")
            period = period_element.text.strip()

            # **Extract Total Commission Earned**
            total_commission_element = row.find_element(By.CSS_SELECTOR,
                                                        "#TotalCommissionsEarned-columnAndValue .columnValue")
            total_commission_text = total_commission_element.text.strip()
            total_commission = float(total_commission_text.replace("$", "").replace(",",
                                                                                    "")) if total_commission_text and total_commission_text != "-" else 0

            print(f"📅 Checking row: Period = {period}, Commission = {total_commission}")

            # ✅ **Check if the row matches the criteria**
            if period == current_month_year and total_commission > 0:
                print(f"✅ Matching row found: Period = {period}, Commission = {total_commission}")

                # **Expand row details**
                arrow = row.find_element(By.CSS_SELECTOR, ".arrow-up")
                driver.execute_script("arguments[0].click();", arrow)
                time.sleep(2)  # Allow time for expansion
                matching_row_found = True

                # **Check if 'Group, Individual and Specialty Commissions' section exists**
                commission_headers = driver.find_elements(By.CSS_SELECTOR, ".commissionsWrapperChild.columnLabel")
                if any("Group, Individual and Specialty Commissions" in h.text for h in commission_headers):
                    print("✅ Required commission section found. Proceeding to download.")
                    time.sleep(2)

                    # **Locate and Click CSV Download Link**
                    csv_download = WebDriverWait(driver, 15).until(
                        EC.element_to_be_clickable((By.XPATH, "//*[@id='1']/div[2]/div[5]/div[2]/div/div/a"))
                    )
                    csv_download.click()
                    print("📥 Download initiated successfully.")
                    time.sleep(60)
                    break  # Stop after first match

                else:
                    print("⚠ Required commission section NOT found. Skipping row.")

        except Exception as row_error:
            print(f"⚠ Error processing row: {row_error}")

    if not matching_row_found:
        print("⚠ No matching row found for the current period with valid commission.")
        log_error(ERROR_CODES["general_error"], "No matching commission data found.", script_name)
        driver.quit()
        exit()

except TimeoutException:
    print("❌ Error: Could not locate commission rows.")
    log_error(ERROR_CODES["download_error"], "Commission rows not found.", script_name)
    driver.quit()
    exit()

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
        renamed_csv_file = os.path.join(os.path.dirname(csv_file_path), f"{rename_base}{first_of_prev_month}.csv")

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
    file_name = f"{matrix_row['rename_base'].strip()}{first_of_prev_month}.csv"

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