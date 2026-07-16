import os
import time
from datetime import datetime
import shutil
import zipfile
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException
from azure.storage.blob import BlobServiceClient
from azure.identity import ClientSecretCredential
from logger import setup_logger, log_success, log_error, log_final_entry, ERROR_CODES, update_log_extra_fields

# Set up logger
script_name = setup_logger("Zipcode_DB_RPA")
process_type = "ZIP"
company_id = "270681372"
report_month = datetime.now().strftime("%Y-%m-%d")

# Set download folder
download_folder = "C:\\Users\\myopsadmin\\Downloads"

# Blob Storage folder
blob_folder = f"raw/zip_database/"

# Azure credentials
tenant_id = os.getenv("AZURE_TENANT_ID", "")
client_id = os.getenv("AZURE_CLIENT_ID", "")
client_secret = os.getenv("AZURE_CLIENT_SECRET", "")

try:
    azure_credential = ClientSecretCredential(
        tenant_id=tenant_id,
        client_id=client_id,
        client_secret=client_secret
    )
    storage_account_name = "834analyticsdatalake"
    account_url = f"https://{storage_account_name}.blob.core.windows.net"
    blob_service_client = BlobServiceClient(account_url=account_url, credential=azure_credential)
    print("Successfully authenticated to Azure Blob Storage.")
except Exception as e:
    print(f"Error authenticating to Azure Blob Storage: {e}")
    exit(1)

# Initialize WebDriver
options = webdriver.ChromeOptions()
options.add_argument("--start-maximized")
options.add_experimental_option("prefs", {
    "download.default_directory": download_folder,
    "download.prompt_for_download": False,
    "download.directory_upgrade": True,
    "safebrowsing.enabled": True
})
driver = webdriver.Chrome(options=options)

try:
    # Step 1: Open the login page
    driver.get("https://www.zip-codes.com/account_login.asp")
    print("Login page loaded.")

    # Step 2: Enter username
    WebDriverWait(driver, 10).until(
        EC.presence_of_element_located((By.NAME, "loginUsername"))
    ).send_keys(os.getenv("ZIP_DB_LOGIN_EMAIL", ""))

    # Step 3: Enter password
    driver.find_element(By.NAME, "loginPassword").send_keys(os.getenv("ZIP_DB_LOGIN_PASSWORD", ""))

    # Step 4: Click Log In
    driver.find_element(By.XPATH, "//input[@type='submit' and @value='Login']").click()
    print("Logged in successfully!")
    time.sleep(5)

    # Step 5: Click on 'My Customers'
    download_zip = WebDriverWait(driver, 10).until(
        EC.element_to_be_clickable((By.XPATH, "/html/body/table/tbody/tr/td[2]/div/div[5]/div[1]/a"))
    )
    download_zip.click()
    print("Downloading zip")

    # Step 6: Click on 'My Customer Dashboard'
    zip = WebDriverWait(driver, 10).until(
        EC.element_to_be_clickable((By.XPATH, "/html/body/table/tbody/tr/td[2]/div/table/tbody/tr[4]/td[3]/a"))
    )
    zip.click()
    print("Clicked on 'Download'.")
    time.sleep(20)
    start_ts = time.time()

    # Step 12: Process downloaded file
    try:
        downloaded_zip_file = next((f for f in os.listdir(download_folder) if f.lower().startswith("zip-codes-database-deluxe-business") and f.endswith(".zip")), None)
        if not downloaded_zip_file:
            raise FileNotFoundError("No zip file found in the download folder.")

        # Unzip the downloaded file
        with zipfile.ZipFile(os.path.join(download_folder, downloaded_zip_file), 'r') as zip_ref:
            zip_ref.extractall(download_folder)
        print(f"Extracted zip file: {downloaded_zip_file}")

        # Find the extracted CSV file
        extracted_file = next((f for f in os.listdir(download_folder) if f.lower().startswith("zip-codes-database-deluxe-business") and f.endswith(".csv")), None)
        if not extracted_file:
            raise FileNotFoundError("No CSV file found after extracting the zip file.")

        # Generate the current date for the file name
        run_date = datetime.now().strftime("%m%d%Y")
        renamed_file_name = f"raw_zip_database_{run_date}.csv"
        renamed_file_path = os.path.join(download_folder, renamed_file_name)
        os.rename(os.path.join(download_folder, extracted_file), renamed_file_path)
        print(f"Renamed file to: {renamed_file_name}")

        # Upload to Blob Storage
        blob_client = blob_service_client.get_blob_client(container="834analytics-dev", blob=os.path.join(blob_folder, renamed_file_name))
        with open(renamed_file_path, "rb") as data:
            blob_client.upload_blob(data, overwrite=True)
        print(f"Uploaded file to Blob Storage at: {blob_folder}")

         # Step 11: Log result
        update_log_extra_fields(
        script_name=script_name,
        file_status="Ready",
        file_path=renamed_file_path,
        process_type=process_type,
        file_report_month=report_month,
        company_id=company_id,
        sub_entity_id="270681372001"
        )

    # Step 12: Delete all related files
        try:
            for f in os.listdir(download_folder):
                full_path = os.path.join(download_folder, f)
                if os.path.isfile(full_path):
                    modified_ts = os.path.getmtime(full_path)
                    if abs(modified_ts - start_ts) < 180:  # within 3 mins
                        os.remove(full_path)
                        print(f"Deleted: {f}")
        except Exception as e:
            log_error(ERROR_CODES["file_deletion_error"], f"Failed during cleanup: {e}", script_name)

        log_success()

    except Exception as e:
        log_error(ERROR_CODES["upload_error"], f"Error processing file: {e}", script_name)
        print(f"Error processing file: {e}")

finally:
    log_final_entry(script_name)
    driver.quit()
