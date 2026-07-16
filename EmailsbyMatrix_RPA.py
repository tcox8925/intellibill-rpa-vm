import os
import sys
import pandas as pd
import re
from datetime import datetime
import pytz
import time
import shutil
from azure.identity import DefaultAzureCredential, ClientSecretCredential
from azure.keyvault.secrets import SecretClient
from azure.storage.blob import BlobServiceClient, BlobClient, ContainerClient
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException
from logger import setup_logger, log_success, log_error, log_final_entry, ERROR_CODES, update_log_extra_fields, init_log_entry


def UnzipFile(folder):
    zip_file = next((f for f in os.listdir(folder) if f != 'RPA_automation_12032024.xlsx' and f.endswith('.zip')), None)
    if zip_file:
        zip_file_path = os.path.join(folder, zip_file)
        try:
            shutil.unpack_archive(os.path.join(folder, zip_file), folder)
            print(f"{os.path.basename(zip_file_path)} unzipped")
            os.remove(zip_file_path)
            print(f"{os.path.basename(zip_file_path)} deleted")
        except Exception as e:
            log_error(ERROR_CODES['file_deletion_error'], f"Error in unzipping or deleting the zip file, {e}", script_name)
            print(f"Error in unzipping deleting the zip file, {e}")
            exit()
    return None


# --- Safe normalization helper ---
def safe_norm(value):
    if pd.isna(value):
        return ""
    return re.sub(r'[^a-zA-Z0-9]', '', str(value)).lower().strip()


# Set up logger
script_name = setup_logger("EmailsbyMatrix_RPA")
init_log_entry(script_name)
update_log_extra_fields(
    script_name,
    flow_id="BB243E91-A3B6-4A9F-85EC-9D10534890C8",
    sub_entity_id="270681372001"
)

# Set today's date in mmddyyyy format
today_date = datetime.now(pytz.timezone("America/Chicago")).strftime("%m%d%Y")

try:
    # reference matrix file for matching the email subject and fetching credentials
    try:
        matrix_file = pd.read_excel(r"C:\Users\myopsadmin\Agility Insurance Services\834 Labs - Documents\#0 - Product Enablement\Data Analytics Projects\Agent Contract Update Automation\Emails by Matrix\RPA_automation_12032024.xlsx")  # change the path accordingly
        login_carrier = matrix_file[matrix_file['Log_In_Required?'] == 'Yes']
        folder_path = r"C:\Users\myopsadmin\Agility Insurance Services\834 Labs - Documents\Data Ops Production Files\834labs raw files\Emails - Matrix"  # update the appropriate folder path
    except Exception as e:
        log_error(ERROR_CODES['process_interrupted'], f"Error in reading the matrix file or the folder path: {e}", script_name)
        print(f"Error in reading the matrix file or the folder path: {e}")
        exit()

    # get the url from text file
    try:
        text_file = next((f for f in os.listdir(folder_path) if f != 'RPA_automation_12032024.xlsx' and f.endswith(".txt")), None)
        text_file_path = os.path.join(folder_path, text_file)
        with open(text_file_path, 'r') as file:
            url = file.read()
        file.close()
        print("Read the URL")
    except Exception as e:
        log_error(ERROR_CODES["File error"], f"Error in reading URL text file, {e}", script_name)
        print(f"Error in reading the URL text file: {e}")
        exit()

    normalized_file_name = safe_norm(os.path.basename(os.path.splitext(text_file_path)[0]))

    # delete the URL text file
    try:
        os.remove(text_file_path)
        print("URL text file deleted")
    except Exception as e:
        log_error(ERROR_CODES["file_deletion_error"], f"Error in deleting URL text file: {e}", script_name)
        print(f"Error in deleting URL text file: {e}")

    # get the login credentials, file rename, and process of the corresponding carrier
    carrier_flag = None
    for index, row in login_carrier.iterrows():
        norm_email_title = safe_norm(row['EMAIL_TITLE'])
        if norm_email_title in normalized_file_name:
            carrier = row['CARRIER']
            login_email = row['ID']
            login_password = row['PW']
            rename = row['RENAME']
            process_type = row['Type']
            downloaded_file = safe_norm(row['Downloaded_File'])
            print(f"Retrieved the carrier name {carrier}, credentials, file rename {rename}, and process type {process_type}")
            carrier_flag = True
            break

    if not carrier_flag:
        log_error(ERROR_CODES['process_interrupted'], f"Carrier name not found in the table", script_name)
        print("Carrier name not found in the table")
        exit()

    # launch browser and log into the portal with the retrieved credentials
    options = webdriver.ChromeOptions()
    options.add_argument("--start-maximized")
    prefs = {
        "download.default_directory": folder_path,
        "download.prompt_for_download": False,
        "download.directory_upgrade": True,
        "safebrowsing.enabled": True
    }
    options.add_experimental_option("prefs", prefs)
    driver = webdriver.Chrome(options=options)

    # --- For Miramar and Aflac ---
    if carrier in ['AmeriHealth Caritas - ACA', 'Amerihealth Caritas - Medicare', 'Anthem - ACA', 'CHRISTUS Health - ACA', 'Aflac - Sup']:
        try:
            driver.get(url)
            print("Login page loaded.")
            time.sleep(10)

            email_placeholder = WebDriverWait(driver, 15).until(
                EC.presence_of_element_located((By.XPATH, "//input[@id='dialog:username']"))
            )
            email_address = email_placeholder.get_attribute("value")

            if email_address == login_email:
                WebDriverWait(driver, 15).until(
                    EC.presence_of_element_located((By.XPATH, "//input[@id='dialog:password']"))
                ).send_keys(login_password)

            continue_button = WebDriverWait(driver, 15).until(
                EC.element_to_be_clickable((By.XPATH, "//input[@id='dialog:continueButton' and @value='Continue']"))
            )
            continue_button.click()
            time.sleep(10)
            print("Login successful")

        except Exception as e:
            log_error(ERROR_CODES['login_error'], f"Login Error: {e}", script_name)
            print(f"Login Error: {e}")
            driver.quit()
            exit()

        try:
            attachment = WebDriverWait(driver, 15).until(
                EC.element_to_be_clickable((By.XPATH, "//a[contains(@onclick, 'mojarra.jsfcljs')]"))
            )
            attachment.click()
            print("Attachment File Downloading")
            time.sleep(30)

        except Exception as e:
            log_error(ERROR_CODES['download_error'], f"Download error: {e}", script_name)
            print(f"Download error: {e}")
            driver.quit()
            exit()

        finally:
            driver.quit()

    # --- For Aetna CVS Health ---
    if carrier in ['Aetna CVS Health - ACA']:
        try:
            driver.get(url)
            print("Login page loaded.")
            time.sleep(10)

            WebDriverWait(driver, 15).until(
                EC.presence_of_element_located((By.XPATH, "//input[@type='email' and @id='username']"))
            ).send_keys(login_email)

            next_button = WebDriverWait(driver, 15).until(
                EC.element_to_be_clickable((By.XPATH, "//button[@type='submit' and @ng-click='appCtrl.stepOne()' and text()='Next']"))
            )
            next_button.click()

            WebDriverWait(driver, 15).until(
                EC.presence_of_element_located((By.XPATH, "//input[@type='password']"))
            ).send_keys(login_password)

            login_button = WebDriverWait(driver, 15).until(
                EC.element_to_be_clickable((By.XPATH, "//button[@type='submit' and @ng-click='appCtrl.submit()' and text()='Log In']"))
            )
            login_button.click()
            time.sleep(10)
            print("Login successful")

        except Exception as e:
            log_error(ERROR_CODES['login_error'], f"Login Error: {e}", script_name)
            print(f"Login Error: {e}")
            driver.quit()
            exit()

        try:
            view_attachment = WebDriverWait(driver, 15).until(
                EC.element_to_be_clickable((By.XPATH, "//a[span[text()='View']]"))
            )
            view_attachment.click()
            print("View attachment clicked")

        except TimeoutException as e:
            log_error(ERROR_CODES['navigation_error'], f"Navigation error, {e}", script_name)
            print(f"Navigation error, {e}")
            driver.quit()
            exit()

        try:
            download_button = WebDriverWait(driver, 15).until(
                EC.element_to_be_clickable((By.XPATH, "//a[contains(@ng-if,'canDownload') and contains(@mc-file-download,'item') and text() = 'Download']"))
            )
            download_button.click()
            time.sleep(30)
            print("Attachment File Downloading")

        except Exception as e:
            log_error(ERROR_CODES['download_error'], f"Download error: {e}", script_name)
            print(f"Download error: {e}")
            driver.quit()
            exit()

        finally:
            driver.quit()

    # check if downloaded file is a zip, if so unpack
    UnzipFile(folder_path)

    # list all files except matrix and txt
    folder_file_list = [f for f in os.listdir(folder_path) if os.path.isfile(os.path.join(folder_path, f))
                        and f != 'RPA_automation_12032024.xlsx' and not f.endswith('.txt')]

    def norm(s): return re.sub(r'[^a-zA-Z0-9]', '', str(s)).lower().strip()
    matches = [f for f in folder_file_list if norm(downloaded_file) in norm(os.path.splitext(f)[0])]

    if not matches:
        local_file = max(folder_file_list, key=lambda fn: os.path.getmtime(os.path.join(folder_path, fn)))
    else:
        local_file = matches[0]

    local_file_path = os.path.join(folder_path, local_file)
    file_extension = os.path.splitext(local_file_path)[1]
    renamed_file = f"{rename}{today_date}{file_extension}"
    renamed_file_path = os.path.join(folder_path, renamed_file)
    try:
        os.rename(local_file_path, renamed_file_path)
        print(f"File renaming successful, Renamed file to: {renamed_file}")
    except Exception as e:
        log_error(ERROR_CODES['file_error'], f"Error in file renaming: {e}", script_name)
        print(f"Error in file renaming: {e}")
        exit()

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
        print("Successfully authenticated to Azure Blob Storage")
    except Exception as e:
        log_error(ERROR_CODES['process_interrupted'], f"Error authenticating to Azure Blob Storage: {e}", script_name)
        print(f"Error authenticating to Azure Blob Storage: {e}")
        exit(1)

    current_month_number = datetime.now().strftime("%m")
    current_month_short = datetime.now().strftime("%b")
    current_year = datetime.now().strftime("%Y")

    if process_type == 'ACU':
        blob_base_folder = "raw/agent_contract_update/"
        blob_folder = f"{blob_base_folder}/{current_month_number} {current_month_short} {current_year}/"
    if process_type == 'BOB':
        blob_base_folder = "raw/production_report/"
        blob_folder = f"{blob_base_folder}/{current_year} {current_month_number} {current_month_short}/"

    blob_client = blob_service_client.get_blob_client(
        container="834analytics-dev",
        blob=os.path.join(blob_folder, renamed_file)
    )

    try:
        with open(renamed_file_path, "rb") as data:
            blob_client.upload_blob(data, overwrite=True)
        print(f"Successfully uploaded {renamed_file} to blob storage {blob_folder}")
    except Exception as e:
        log_error(ERROR_CODES['upload_error'], f"Error uploading file to Azure Blob Storage: {e}", script_name)
        print(f"Error uploading file to Azure Blob Storage: {e}")

    time.sleep(20)

    try:
        os.remove(renamed_file_path)
        print("Local file deleted")
    except Exception as e:
        log_error(ERROR_CODES['file_deletion_error'], f"Error in deleting the local file: {e}", script_name)
        print(f"Error in deleting the local file: {e}")

    log_success()

except Exception as e:
    log_error(ERROR_CODES['general_error'], f"Unexpected error: {e}", script_name)
    print(f"Unexpected error: {e}")

finally:
    log_final_entry(script_name)
