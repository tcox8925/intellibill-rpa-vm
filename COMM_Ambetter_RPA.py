import os
from logger import setup_logger, log_error, ERROR_CODES,log_success,log_final_entry
from rpa_matrix_reader import read_rpa_matrix
from date_utils import get_current_date_info
from chrome_utils import get_chrome_driver  #  Using the Chrome Utils Module
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.common.by import By
from selenium.common.exceptions import TimeoutException
import time
from azure_blob_utils import upload_blob, authenticate_blob_storage
from datetime import datetime
from file_utils import unzip_file
from email_utils import send_email_notification,get_email_recipients
import glob

# Read the RPA matrix
rpa_matrix = read_rpa_matrix()
rpa_matrix = rpa_matrix.astype(str).apply(lambda x: x.str.strip())  # Clean DataFrame

# Define filter criteria
process_name_filter = "COM"
carrier_id_filter = "2931751000020024159"

# Filter the matrix
matrix_row = rpa_matrix[
    (rpa_matrix["process_name"] == process_name_filter) &
    (rpa_matrix["carrier_id"] == carrier_id_filter)
]

if matrix_row.empty:
    print(f"❌ No matching row found for process_name: {process_name_filter} and carrier_id: {carrier_id_filter}.")
    exit(1)

matrix_row = matrix_row.iloc[0]  # Convert DataFrame row to a dictionary-like Series

# Extract script_name dynamically from matrix
script_name = matrix_row["script_name"]
script_name = setup_logger(script_name)  #  Logger setup using the script_name

# Get dynamic date variables
date_info = get_current_date_info()
current_month_year = date_info["current_month_year"]
current_year = date_info["current_year"]
current_month_number = date_info["current_month_number"]
current_month_short = date_info["current_month_short"]
first_of_prev_month = date_info["first_of_prev_month"]
prev_month_year = date_info["prev_month_year_full"]
print(prev_month_year)

#test_month_year = input("Enter test month-year (e.g., 'January 2025', press Enter for current): ").strip()
#current_month_year = test_month_year if test_month_year else date_info["current_month_year"]


# **Dynamic Configurations from the Matrix**
table_url = matrix_row["source_url"]  # URL to navigate
download_folder = os.path.normpath(matrix_row["download_path"])
zip_file_path = os.path.join(download_folder, f"{matrix_row['file_prefix']}.zip")  # ZIP file path
csv_file_path = os.path.join(download_folder, f"{matrix_row['extracted_file_prefix']}.{matrix_row['extracted_file_extension']}")  # CSV file path

# **Extract WebDriver Preferences**
use_profile_path = matrix_row["use_profile_path"].upper() == "YES"
profile_path = matrix_row["profile_path"] if use_profile_path else None

# **Initialize WebDriver using chrome_utils.py **
driver = get_chrome_driver(profile_path=profile_path, download_folder=download_folder)

print(f" WebDriver initialized successfully. Navigating to {table_url}...")

# **Navigate to the URL**
driver.get(table_url)
# Extract login details from the matrix
log_in_required = matrix_row["source_login"].upper() == "YES"
email = matrix_row["source_email"]
password = matrix_row["source_password"]

if log_in_required:
    try:
        print("🔐 Attempting to log in...")

        # Wait for the email input field
        email_field = WebDriverWait(driver, 10).until(
            EC.presence_of_element_located((By.XPATH, "//input[@placeholder='Email']"))
        )
        email_field.send_keys(email)

        # Enter password
        password_field = driver.find_element(By.XPATH, "//input[@placeholder='Password']")
        password_field.send_keys(password)

        # Click login button
        login_button = driver.find_element(By.XPATH, "//*[@id='centerPanel']/div/div[2]/div/div[2]/div/div[3]/button")
        login_button.click()

        print(" Logged in successfully.")
        time.sleep(10)

    except TimeoutException:
        print(" Login not required or failed.")
        log_error(ERROR_CODES["login_error"], "Login failed or not required.", script_name)

else:
    print(" No login required. Proceeding...")

try:
    print(" Waiting for the table to load...")
    table = WebDriverWait(driver, 30).until(
        EC.presence_of_element_located((By.ID, "commissionsTable"))
    )
    print(" Table loaded successfully.")

    rows = WebDriverWait(driver, 30).until(
        EC.presence_of_all_elements_located((By.CSS_SELECTOR, "#commissionsTable tbody tr"))
    )
    print(f"🔍 Found {len(rows)} rows in the table.")

    # Find the row with the matching Check Date
    matching_row_found = False
    for row in rows:
        check_date = row.find_element(By.XPATH, "./td[5]").text.strip()
        if check_date == prev_month_year:
            print(f" Found matching row with Check Date: {check_date}")
            check_number_link = row.find_element(By.XPATH, "./td[3]/a")
            check_number = check_number_link.text.strip()
            print(f" Clicking on Check Number: {check_number}")
            check_number_link.click()  # Click the link
            time.sleep(20)  # Wait for navigation or modal
            matching_row_found = True
            break

    if not matching_row_found:
        print(" No matching row found for the current month and year.")
        log_error(ERROR_CODES["general_error"], "No matching Check Date found in the table.", script_name)
        driver.quit()
        exit()

except TimeoutException:
    log_error(ERROR_CODES["download_error"], "Table or rows not found.", script_name)
    print(" Error: Table or table rows not found.")
    driver.quit()
    exit()

# Step 2: Locate & Click the "Export CSV" Button
try:
    print(" Attempting to locate 'Export CSV' button...")

    # Switch to iframe if needed
    iframe = WebDriverWait(driver, 10).until(
        EC.presence_of_element_located((By.TAG_NAME, "iframe"))
    )
    driver.switch_to.frame(iframe)
    print("️ Switched to iframe.")

    button_found = False
    for attempt in range(5):
        try:
            export_button = WebDriverWait(driver, 10).until(
                EC.presence_of_element_located((By.XPATH, "//button[@title='Export CSV']"))
            )
            if export_button.is_displayed():
                export_button.click()
                print("📂 'Export CSV' button clicked.")
                button_found = True
                break
        except TimeoutException:
            print(f"🔄 Attempt {attempt + 1}/5: Retrying 'Export CSV' button detection...")
            time.sleep(2)

    if not button_found:
        print(" Failed to locate 'Export CSV' button after multiple attempts.")
        log_error(ERROR_CODES["download_error"], "'Export CSV' button not found.", script_name)
        driver.quit()
        exit()

except TimeoutException:
    print(" Unable to locate 'Export CSV' button.")
    log_error(ERROR_CODES["download_error"], "'Export CSV' button not found.", script_name)
    driver.quit()
    exit()

# Step 3: Handle Download Modal & Trigger File Download
try:
    WebDriverWait(driver, 30).until(
        EC.visibility_of_element_located((By.ID, "downloadModal"))
    )
    print(" Download modal visible.")

    # Check for iframe inside modal
    modal_iframe = driver.find_elements(By.TAG_NAME, "iframe")
    if modal_iframe:
        driver.switch_to.frame(modal_iframe[0])
        print("️ Switched to iframe inside the modal.")

    # Locate & Click the Download Button
    download_button = WebDriverWait(driver, 30).until(
        EC.element_to_be_clickable((By.CSS_SELECTOR, "a.btn.btn-primary[download]"))
    )
    download_button.click()
    print(" Download button clicked. Waiting for file to download...")
    time.sleep(30)  # Allow time for download

    log_success()
    print(" File downloaded successfully.")

except TimeoutException:
    print(" Unable to locate Download button.")
    log_error(ERROR_CODES["download_error"], "Download modal or button not found.", script_name)
    driver.quit()
    exit()

except Exception as e:
    print(f" Unexpected error: {e}")
    log_error(ERROR_CODES["general_error"], f"Unexpected error: {e}", script_name)

finally:
    driver.quit()

# Ensure Blob Storage authentication succeeded
blob_service_client = authenticate_blob_storage()
if not blob_service_client:
    print(" Failed to authenticate to Azure Blob Storage. Exiting...")
    exit(1)

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
            print(f" Extracting ZIP file: {zip_file_path} ...")
            unzip_file(zip_file_path, os.path.dirname(csv_file_path))  #  Extract to download folder
            print(f" Extraction complete. Extracted files are in: {os.path.dirname(csv_file_path)}")

        extracted_files = glob.glob(os.path.join(os.path.dirname(csv_file_path),
                                                 "*commission_*.csv"))  #  Matches files containing "commissions_"

        if not extracted_files:
            print(f" No extracted CSV file found containing 'commissions_' in {os.path.dirname(csv_file_path)}")
            log_error(ERROR_CODES["general_error"], f"No extracted CSV file found in {os.path.dirname(csv_file_path)}",
                      script_name)
            return

        # **Pick the first matching file**
        csv_file_path = extracted_files[0]

        # **Rename the dynamically found file**
        renamed_csv_file = os.path.join(os.path.dirname(csv_file_path), f"{rename_base}{first_of_prev_month}_test.csv")
        os.rename(csv_file_path, renamed_csv_file)
        print(f" Renamed file: {csv_file_path} ➡️ {renamed_csv_file}")

        # **Step 3: Define Blob Path**
        current_date_info = get_current_date_info()
        blob_folder = f"{blob_base_path}{date_info['current_year']} {date_info['current_month_number']} {date_info['current_month_short']}/"
        blob_path = f"{blob_folder}{os.path.basename(renamed_csv_file)}"

        # **Step 4: Validate Local File Exists Before Upload**
        if not os.path.exists(renamed_csv_file):
            print(f" File does not exist: {renamed_csv_file}")
            log_error(ERROR_CODES["general_error"], f"File missing before upload: {renamed_csv_file}", script_name)
            return

        # **Step 5: Upload to Blob Storage using `upload_blob()`**
        print(f" Uploading file: {renamed_csv_file} to Blob Path: {blob_path}")
        upload_blob(blob_service_client, container_name="834analytics-dev", local_file_path=renamed_csv_file, blob_path=blob_path)

        # **Step 6: Verify File Exists in Blob Storage**
        blob_client = blob_service_client.get_blob_client(container="834analytics-dev", blob=blob_path)
        if blob_client.exists():
            print(f" Verified: File successfully uploaded to Blob Storage: {blob_path}")
        else:
            print(f" Upload verification failed. File not found in Blob Storage: {blob_path}")
            log_error(ERROR_CODES["general_error"], f"Upload failed, file not in Blob Storage: {blob_path}", script_name)
            return

        # **Step 7: Clean Up Local Files After Successful Upload**
        if os.path.exists(renamed_csv_file):
            os.remove(renamed_csv_file)
            print(f"️ Deleted local CSV file: {renamed_csv_file}")
        else:
            print(f" CSV file already deleted or not found: {renamed_csv_file}")

        if requires_extraction and os.path.exists(zip_file_path):
            os.remove(zip_file_path)
            print(f"️ Deleted local ZIP file: {zip_file_path}")
        elif requires_extraction:
            print(f" ZIP file already deleted or not found: {zip_file_path}")

        # **Step 8: Log Success**
        log_success()
        print(" File processing & cleanup completed successfully!")


    except Exception as e:
        print(f" Error in upload/cleanup process: {e}")
        log_error(ERROR_CODES["general_error"], f"Upload/cleanup error: {e}", script_name)
        exit(1)


try:
    file_name = f"{matrix_row['rename_base'].strip()}{first_of_prev_month}.csv"

    #  **After Successful Upload & Cleanup**
    upload_and_cleanup(
        blob_service_client=blob_service_client,
        zip_file_path=zip_file_path,
        csv_file_path=csv_file_path,
        rename_base=matrix_row["rename_base"].strip(),
        blob_base_path=matrix_row["blob_base_path"].strip(),
        requires_extraction=matrix_row["requires_extraction"].upper() == "YES"
    )

    # **Extract notification details from the matrix**
    #notification_process = matrix_row["notification_process"].strip()
    #flow_url = matrix_row["pautomate_url"].strip()

    # **Get email recipients (to & cc)**
    #email_recipients = get_email_recipients(notification_process)

    # **Prepare dynamic values**

    folder_path = f"{blob_base_path}{current_year} {current_month_number} {current_month_short}/"

    # **Call send_email_notification with values populated**
    #send_email_notification(
    #    flow_url=flow_url,
    #    process_name=script_name,
    #    notification_process=notification_process,
    #    to=email_recipients["to"],  #  Passing the real to-list
    #    cc=email_recipients["cc"],  #  Passing the real cc-list
    #    file_name=file_name,
    #    folder_path=folder_path
    #)

    log_final_entry(script_name)

except Exception as e:
    print(f" Error during file upload or notification: {e}")
