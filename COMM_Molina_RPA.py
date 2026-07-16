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
carrier_id_filter = "2931751000020024153"

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
            EC.presence_of_element_located((By.XPATH, "//input[@name='login_id']"))
        )
        email_field.send_keys(email)

        # Enter password
        password_field = driver.find_element(By.XPATH, "//input[@name='password']")
        password_field.send_keys(password)

        # Click login button
        login_button = driver.find_element(By.XPATH, "//input[@name='submit']")
        login_button.click()

        print("Logged in successfully.")
        time.sleep(10)

    except TimeoutException:
        print("Login not required or failed.")
        log_error(ERROR_CODES["login_error"], "Login failed or not required.", script_name)

else:
    print("No login required. Proceeding...")

try:
    original_window = driver.current_window_handle
    WebDriverWait(driver, 10).until(
        EC.element_to_be_clickable((By.XPATH, '//*[@id="domain_container"]/div/div[2]/div/div[2]/div'))
    ).click()
    print("Molina domain selected.")
    WebDriverWait(driver, 10).until(EC.number_of_windows_to_be(2))

    # Switch to the new tab
    for handle in driver.window_handles:
        if handle != original_window:
            driver.switch_to.window(handle)
            break
    print("Switched to Molina tab.")
except TimeoutException:
    print("Login not required or failed.")
    log_error(ERROR_CODES["general_error"], "Failed to switch domain.", script_name)
    time.sleep(20)

try:
    WebDriverWait(driver, 10).until(
        EC.presence_of_element_located((By.XPATH, "//*[@id='menu_header_2_2']"))
    ).click()
    print("Commissions selected")
    time.sleep(10)
except:
    print("Commissions not found")
    log_error(ERROR_CODES["link_not_found"], "Commissions Link not found.", script_name)

try:
    # Enter First of the Month (Three Months Prior)
    statement_from = driver.find_element(By.ID, "statement_from")
    statement_from.clear()
    statement_from.send_keys(date_info["first_of_three_months_prior"])
    print(f"✅ Entered 'From' Date: {date_info['first_of_three_months_prior']}")
except:
    print("'From' input form not found.")
    log_error(ERROR_CODES["link_not_found"], "'From' input form not found.", script_name)
try:
    # Enter Last Day of the Current Month
    statement_to = driver.find_element(By.ID, "statement_to")
    statement_to.clear()
    statement_to.send_keys(date_info["last_of_current_month"])
    time.sleep(5)
    statement_to.send_keys(Keys.ENTER)
    print(f"✅ Entered 'To' Date: {date_info['last_of_current_month']}")
except:
    print("'To' input form not found.")
    log_error(ERROR_CODES["link_not_found"], "'To' input form not found.", script_name)

try:
    time.sleep(10)
    WebDriverWait(driver, 10).until(
        EC.presence_of_element_located((By.XPATH, "//*[@name='searchMember']"))  # ✅ Corrected syntax
    ).click()
except:
    print("Search Button not found.")
    log_error(ERROR_CODES["link_not_found"], "Search Button not found.", script_name)

time.sleep(20)

# Step 1: Ensure the table is visible
try:
    print("🔍 Waiting for the table to be visible...")
    table = WebDriverWait(driver, 20).until(
        EC.presence_of_element_located((By.ID, "portal_member"))
    )
    print("✅ Table is visible.")
except Exception as e:
    print(f"❌ Error: Table not found. {e}")
    log_error(ERROR_CODES["general_error"], "Portal Member table not found.", script_name)
    driver.quit()
    exit()

# Step 2: Locate the first odd row
try:
    print("🔍 Locating the first odd row...")
    first_odd_row = WebDriverWait(driver, 10).until(
        EC.presence_of_element_located((By.CSS_SELECTOR, "#portal_member tbody tr.odd"))
    )
    print("✅ First odd row found.")

    # Step 3: Extract the Stmt Date
    stmt_date_element = first_odd_row.find_element(By.CSS_SELECTOR, "td.text-center.sorting_1")
    stmt_date_text = stmt_date_element.text.strip()  # Extract date as "01/17/2025"
    stmt_date = datetime.strptime(stmt_date_text, "%m/%d/%Y")  # Convert to datetime

    # Extract month and year
    stmt_month = stmt_date.strftime("%m")  # "01"
    stmt_year = stmt_date.strftime("%Y")  # "2025"

    print(f"📅 Stmt Date Found: {stmt_date_text} | Month: {stmt_month}, Year: {stmt_year}")

    # Step 4: Compare with current month and year
    if stmt_month == current_month_number and stmt_year == current_year:
        print(f"✅ Stmt Date matches current month ({stmt_month}/{stmt_year}). Proceeding to download.")

        # Step 5: Click the "Excel" download link
        excel_link = first_odd_row.find_element(By.CSS_SELECTOR, "td.text-center a.card-link")
        excel_link.click()
        print("📥 Download initiated successfully.")
        time.sleep(5)  # Allow time for download

    else:
        print(
            f"⚠ Stmt Date {stmt_month}/{stmt_year} does not match the current month {current_month_number}/{current_year}. Skipping download.")
except Exception as e:
    print(f"❌ Error processing the row: {e}")
    log_error(ERROR_CODES["general_error"], f"Error processing the first row: {e}", script_name)
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
        renamed_csv_file = os.path.join(os.path.dirname(csv_file_path), f"{rename_base}{first_of_curr_month}.xlsx")

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
    file_name = f"{matrix_row['rename_base'].strip()}{first_of_curr_month}.csv"

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