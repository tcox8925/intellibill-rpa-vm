import os
import time
import json
import pandas as pd
import requests
from datetime import datetime
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException
from azure.storage.blob import BlobServiceClient
from azure.identity import DefaultAzureCredential, ClientSecretCredential
from azure.keyvault.secrets import SecretClient
from selenium.webdriver.chrome.service import Service
from logger import (
    setup_logger, init_log_entry, log_success, log_error, log_final_entry,
    record_carrier_result, log_overall_result, ERROR_CODES, update_log_extra_fields
)
from db_connection import connect_to_db

# Set up logger
script_name = "ACU_CRM_Upload"

# Configuration
print("==Setting chrome options")
chrome_options = webdriver.ChromeOptions()
chrome_options.add_argument("user-data-dir=C:/Users/myopsadmin/AppData/Local/Google/Chrome/User Data Testing")
chrome_options.add_argument("profile-directory=DefaultTesting")

print("==Establishing chrome driver")
driver = webdriver.Chrome(options=chrome_options)
print("==Maximizing window")
driver.maximize_window()
download_folder = "C:\\Users\\myopsadmin\\Downloads"
today_date = datetime.now().strftime("%m%d%Y")
month_folder = datetime.now().strftime("%Y %m %b")

# Azure Blob Storage Configuration
keyvault_name: str = os.getenv("KEY_VAULT_NAME", "")
KEY_VAULT_URL = os.getenv("KEYVAULT_URL", "")
storage_account_name: str = "agilitydatadev001"
container_name = "agilityops"

base_blob_path = f"results/agent_contract_update (acu)/acu_new_process/{month_folder}/acu_results_{today_date}_"
local_csv_path = os.path.join(download_folder, f"results_acu_contracts_{today_date}.csv")

tenant_id_key: str = os.getenv("KEYVAULT_TENANT_ID_SECRET_NAME", "")
client_id_key: str = os.getenv("KEYVAULT_CLIENT_ID_SECRET_NAME", "")
client_secret_key: str = os.getenv("KEYVAULT_CLIENT_SECRET_NAME", "")
try:
    credential = DefaultAzureCredential()
    client = SecretClient(vault_url=KEY_VAULT_URL, credential=credential)
    tenant_id = client.get_secret(tenant_id_key).value
    client_id = client.get_secret(client_id_key).value
    client_secret = client.get_secret(client_secret_key).value
except Exception as e:
    print(f'==Failed to retrieve secrets: {e}')
    exit(0)


# Zoho CRM Selenium Configuration
print("==Loading selenium configuration")
driver_path = "C:\\Users\\myopsadmin\\Desktop\\chromedriver.exe"
service = Service(driver_path)

# Carrier Processing Status (not used directly anymore)
carrier_status = {"success": [], "failed": []}

login_email = os.getenv("ACU_CRM_LOGIN_EMAIL", "")
password_needed = os.getenv("ACU_CRM_PASSWORD", "")
email_domain = login_email.split('@')[-1]

def download_csv_from_blob():
    try:
        azure_credential = ClientSecretCredential(
            tenant_id=tenant_id,
            client_id=client_id,
            client_secret=client_secret
        )
        account_url = f"https://{storage_account_name}.blob.core.windows.net"
        blob_service_client = BlobServiceClient(account_url=account_url, credential=azure_credential)

        container_client = blob_service_client.get_container_client(container_name)
        matching_blob = None
        for blob in container_client.list_blobs(name_starts_with=base_blob_path):
            matching_blob = blob.name
            break

        if not matching_blob:
            log_error(ERROR_CODES["download_error"], f"No blob found starting with {base_blob_path}", script_name)
            raise FileNotFoundError(f"No blob found starting with {base_blob_path}")

        blob_client = blob_service_client.get_blob_client(container=container_name, blob=matching_blob)
        print(f"Downloading blob '{matching_blob}' from Azure Storage...")
        with open(local_csv_path, "wb") as file:
            file.write(blob_client.download_blob().readall())
        print(f"File downloaded successfully to: {local_csv_path}")
        return matching_blob
    except Exception as e:
        log_error(ERROR_CODES["download_error"], f"Error downloading file: {e}", script_name)
        print(f"Error downloading file from Azure Blob Storage: {e}")
        raise


def get_unique_carriers(file_path):
    try:
        df = pd.read_csv(file_path, dtype=str, keep_default_na=False)
        unique_carriers = df['carrier_name'].unique()
        print(f"Found {len(unique_carriers)} unique carriers: {unique_carriers}")
        return unique_carriers, df
    except Exception as e:
        log_error(ERROR_CODES["general_error"], f"Error reading carriers: {e}", script_name)
        raise


def upload_carrier_data(driver, carrier, carrier_file):
    try:
        carrier_script_name = f"{script_name}_{carrier}"
        setup_logger(carrier_script_name)
        init_log_entry(carrier_script_name)
        update_log_extra_fields(
            carrier_script_name,
            flow_id="CC4C3D2E-D212-4C3E-89D4-41FCA781FE4F",
            sub_entity_id="270681372001"
        )
        
        print(f"Processing carrier: {carrier}")

        try:
            if email_domain == "enrollinsurance.com":
                print("Logging in as enrollinsurance.com user. Clicking 'Visible_CustomModule8' button.")
                custom_module_button = WebDriverWait(driver, 30).until(
                    EC.presence_of_element_located((By.XPATH, '//*[@id="Visible_CustomModule8"]'))
                )
                custom_module_button.click()
            elif email_domain == "834labs.com":
                print("Logging in as 834labs.com user. Clicking 'Contracting' button.")
                try:
                    contracting_button = WebDriverWait(driver, 30).until(
                        EC.presence_of_element_located(
                            (By.XPATH, '//a[contains(@href, "/tab/CustomModule8")]'))
                    )
                    contracting_button.click()
                except Exception as e:
                    print("==Could not locate 'Contracting' button. Attempting menu opener.")
                    menu_button = WebDriverWait(driver, 60).until(
                        EC.presence_of_element_located((By.XPATH, '//*[@id="minimizedAppMenuExpandIcon"]'))
                    )
                    menu_button.click()
                    time.sleep(3)
                    contracting_button = WebDriverWait(driver, 30).until(
                        EC.presence_of_element_located(
                            (By.XPATH, '//a[contains(@href, "/tab/CustomModule8")]'))
                    )
                    contracting_button.click()
            else:
                print(f"Unknown email domain: {email_domain}. Unable to proceed.")
        except TimeoutException:
            print(f"Failed to locate the module button for domain {email_domain}.")
        time.sleep(10)

        dropdown_button = WebDriverWait(driver, 60).until(
            EC.presence_of_element_located((By.XPATH, '//*[@id="importButton"]'))
        )
        dropdown_button.click()
        time.sleep(10)

        import_contracting_option = WebDriverWait(driver, 30).until(
            EC.presence_of_element_located(
                (By.XPATH, '//a[contains(@href, "/settings/import?module=CustomModule8")]'))
        )
        import_contracting_option.click()
        time.sleep(5)

        file_input = WebDriverWait(driver, 60).until(
            EC.presence_of_element_located((By.ID, "fileselect"))
        )
        file_input.send_keys(carrier_file)
        print(f"File uploaded for {carrier} successfully.")
        time.sleep(10)

        next_button = WebDriverWait(driver, 60).until(
            EC.element_to_be_clickable((By.XPATH, "//*[@id='fileUploadNextBtn']"))
        )
        next_button.click()
        print("Import initiated successfully.")
        time.sleep(20)

        radio_button = WebDriverWait(driver, 30).until(
            EC.presence_of_element_located((By.XPATH, '//input[@value="overwriteOnly"]'))
        )
        radio_button.click()
        print("Selected 'Overwrite Only' radio button.")
        time.sleep(5)

        dropdown = WebDriverWait(driver, 30).until(
            EC.presence_of_element_located((By.XPATH, '//span[@id="select2-findBy-container"]'))
        )
        dropdown.click()
        dropdown_option = WebDriverWait(driver, 30).until(
            EC.presence_of_element_located(
                (By.XPATH, '//li[contains(@id, "select2-findBy-result") and span[text()="Contract ID"]]'))
        )
        dropdown_option.click()
        print("Set dropdown to 'Contract ID'.")
        time.sleep(5)

        operation_next_button = WebDriverWait(driver, 30).until(
            EC.element_to_be_clickable((By.XPATH, '//*[@id="operationSubmitBtn"]'))
        )
        operation_next_button.click()
        print("Clicked 'Next' after setting operation.")
        time.sleep(20)

        fields = [
            {"index": "0", "name": "Contract ID", "dropdown": "Contract ID"},
            {"index": "1", "name": "Agent NPN", "dropdown": "Agent NPN"},
            {"index": "2", "name": "Writing Number", "dropdown": "Writing Number"},
            {"index": "3", "name": "--Select None--", "dropdown": "--Select None--"},
            {"index": "4", "name": "Contract Status", "dropdown": "Contract Status"},
            {"index": "5", "name": "Appointment Type", "dropdown": "Appointment Type"},
            {"index": "6", "name": "Appointed States", "dropdown": "Appointed States"},
            {"index": "7", "name": "States Confirmed", "dropdown": "States Confirmed"},
            {"index": "11", "name": "2026 MED RTS", "dropdown": "2026 MED RTS"},
            {"index": "12", "name": "2026 MED RTS Date", "dropdown": "2026 MED RTS Date"},
            {"index": "13", "name": "2026 ACA RTS", "dropdown": "2026 ACA RTS"},
            {"index": "9", "name": "2025 MED RTS", "dropdown": "2025 MED RTS"},
            {"index": "10", "name": "2025 MED RTS Date", "dropdown": "2025 MED RTS Date"}
        ]

        for field in fields:
            field_input = WebDriverWait(driver, 30).until(
                EC.presence_of_element_located(
                    (By.XPATH, f'//input[@id="fieldChoose" and @data-index="{field["index"]}"]'))
            )
            field_input.click()
            field_input.clear()
            print(f"Cleared existing input for data-index='{field['index']}'.")
            field_input.send_keys(field["name"])
            print(f"Entered field name for data-index='{field['index']}'.")
            dropdown_item = WebDriverWait(driver, 30).until(
                EC.presence_of_element_located((By.XPATH, f'//span[@data-label="{field["dropdown"]}"]'))
            )
            dropdown_item.click()
            print(f"Selected the dropdown option for data-index='{field['index']}'.")
            
            try:
                remap_warning = WebDriverWait(driver, 2).until(
                    EC.presence_of_element_located((By.XPATH, f'//*[@id="mapToAnotherFieldBtn"]'))
                )
                remap_warning.click()
            except:
                pass
            
            time.sleep(1)

        try:
            # Locate the Parent Contract input field
            parent_contract_input = WebDriverWait(driver, 30).until(
                EC.presence_of_element_located((By.XPATH, '//input[@id="fieldChoose" and @data-index="8"]'))
            )

            # Check if the field is empty
            if parent_contract_input.get_attribute("value").strip() == "":
                print("Parent Contract field is blank. Typing manually...")

                # Click the field and type "Parent Contract"
                parent_contract_input.click()
                time.sleep(2)
                parent_contract_input.send_keys("Parent Contract")
                time.sleep(3)

                # Click the first dropdown (expands the list)
                dropdown = WebDriverWait(driver, 10).until(
                    EC.presence_of_element_located((By.XPATH, '//span[@data-label="Parent Contract"]'))
                )
                driver.execute_script("arguments[0].click();", dropdown)
                print("Clicked Parent Contract dropdown.")
                time.sleep(3)

                # Select "NPN" from the list
                npn_option = WebDriverWait(driver, 10).until(
                    EC.presence_of_element_located(
                        (By.XPATH, '//li[@data-parent="Parent_Contract" and @data-sysrefname="NPN"]'))
                )
                driver.execute_script("arguments[0].scrollIntoView(true);", npn_option)
                driver.execute_script("arguments[0].click();", npn_option)
                print("Selected 'NPN' for Parent Contract.")
                time.sleep(5)

            else:
                print("Parent Contract already has a value. Using existing logic.")

                parent_contract_input.clear()
                print("Cleared existing value for 'Parent Contract (NPN)'.")
                time.sleep(5)
                parent_contract_input.click()
                print("Reopened 'Parent Contract' dropdown.")
                time.sleep(5)

                second_dropdown_item = WebDriverWait(driver, 30).until(
                    EC.presence_of_element_located(
                        (By.XPATH, '//li[@data-parent="Parent_Contract" and @data-sysrefname="NPN"]')
                    )
                )
                WebDriverWait(driver, 30).until(EC.element_to_be_clickable(second_dropdown_item))
                driver.execute_script("arguments[0].scrollIntoView(true);", second_dropdown_item)
                time.sleep(5)
                driver.execute_script("arguments[0].click();", second_dropdown_item)
                time.sleep(10)
                print("Selected 'NPN' from the second dropdown for 'Parent Contract'.")

        except Exception as e:
            log_error(ERROR_CODES["general_error"], f"Error handling 'Parent Contract': {e}", script_name)
            print(f"Error handling 'Parent Contract (NPN)': {e}")

        time.sleep(10)

        # Check if any Import Notification Popup appears (handles dynamic ID)
        try:
            popup = WebDriverWait(driver, 5).until(
                EC.presence_of_element_located((By.XPATH, '//*[starts-with(@id, "IMPORTNOTIFICATIONPOP")]'))
            )
            print("Import Notification Popup detected! Collapsing it...")

            # Locate the Title bar of the popup (which collapses it when clicked)
            title_bar = driver.find_element(By.XPATH,
                                            '//*[starts-with(@id, "IMPORTNOTIFICATIONPOP")]//div[contains(@class, "migCardTitle")]')

            # Click the title to collapse the popup (using JavaScript for reliability)
            driver.execute_script("arguments[0].click();", title_bar)
            time.sleep(2)

            close_title = driver.find_element(By.XPATH,
                                              '//*[starts-with(@id, "IMPORTNOTIFICATIONPOP")]//div[contains(@class, "migCardClose ico-close-white-small2")]')
            driver.execute_script("arguments[0].click();", close_title)
            time.sleep(2)

            print("Popup collapsed successfully.")

        except TimeoutException:
            print("No Import Notification Popup detected. Proceeding with mapping.")

        mapping_button = WebDriverWait(driver, 30).until(
            EC.presence_of_element_located((By.XPATH, '//*[@id="mappingSubmitBtn"]'))
        )
        mapping_button.click()
        print("Mapping Submitted")
        time.sleep(5)

        try:
            continue_button = WebDriverWait(driver, 30).until(
                EC.presence_of_element_located((By.XPATH, '//*[@id="mappingFieldConfirmBtn"]'))
            )
            continue_button.click()
            print("Continue Button Clicked")
        except:
            print("Continue Button not found. Attempting to check checkbox and finish process.")

        time.sleep(10)

        check_box = WebDriverWait(driver, 30).until(
            EC.presence_of_element_located((By.XPATH, '//*[@id="isTriggerWorkflow"]'))
        )
        check_box.click()
        print("Checkbox checked")

        finish_button = WebDriverWait(driver, 30).until(
            EC.presence_of_element_located((By.XPATH, '//*[@id="advanceOptSubmitBtn"]')) 
        )  #advanceOptCancelBtn
        #advanceOptSubmitBtn
        finish_button.click()
        print("Finish Button Clicked")

        log_success()
        record_carrier_result(carrier, True, "Uploaded successfully.")
        log_final_entry(carrier_script_name)
    except Exception as e:
        error_msg = f"Error processing carrier '{carrier}': {e}"
        log_error(ERROR_CODES["upload_error"], error_msg, script_name)
        record_carrier_result(carrier, False, str(e))
    time.sleep(20)


def get_email_recipients():
    try:
        conn = connect_to_db()

        query = """
        WITH to_emails AS(
            SELECT STRING_AGG(email,',') AS to_list
            FROM wpo.ops_email_notification
            WHERE process_type = 'ACU'  
            AND recipient_type = 'to'
        ),
        cc_emails AS(
            SELECT STRING_AGG(email,',') AS cc_list
            FROM wpo.ops_email_notification
            WHERE process_type = 'ACU'  
            AND recipient_type = 'cc'
        )
        SELECT to_list, cc_list FROM to_emails CROSS JOIN cc_emails
        """

        cursor = conn.cursor()
        cursor.execute(query)
        result = cursor.fetchone()

        conn.close()

        if result:
            return {
                "to": result.to_list.split(",") if result.to_list else [],
                "cc": result.cc_list.split(",") if result.cc_list else []
            }
        else:
            return {"to": [], "cc": []}

    except Exception as e:
        print(f"Error retrieving email recipients: {e}")
        return {"to": [], "cc": []}


def send_results_and_archive(flow_url, blob_name):
    try:
        # Ensure the email recipients are fetched before using them
        email_recipients = get_email_recipients()

        payload = {
            "results": {
                "success": record_carrier_result.__globals__['carrier_successes'],
                "failed": record_carrier_result.__globals__['carrier_errors']
            },
            "to": email_recipients["to"],
            "cc": email_recipients["cc"]
        }

        headers = {"Content-Type": "application/json"}
        response = requests.post(flow_url, json=payload, headers=headers)

        if response.status_code == 200:
            print("Email notification sent successfully.")
        else:
            log_error(ERROR_CODES["upload_error"], f"Email notification failed: {response.text}", script_name)
            print(f"Failed to send email notification: {response.status_code} - {response.text}")

    except Exception as e:
        log_error(ERROR_CODES["upload_error"], f"Error sending email notification: {e}", script_name)
        print(f"Error sending email notification: {e}")


def move_blob_to_archive(matching_blob):
    try:
        azure_credential = ClientSecretCredential(
            tenant_id=tenant_id,
            client_id=client_id,
            client_secret=client_secret
        )
        account_url = f"https://{storage_account_name}.blob.core.windows.net"
        blob_service_client = BlobServiceClient(account_url=account_url, credential=azure_credential)
        archive_blob_path = matching_blob.replace(f"/{month_folder}/", f"/{month_folder}/archive/")
        source_blob = blob_service_client.get_blob_client(container=container_name, blob=matching_blob)
        destination_blob = blob_service_client.get_blob_client(container=container_name, blob=archive_blob_path)
        print(f"Moving blob '{matching_blob}' to archive at '{archive_blob_path}'...")
        destination_blob.start_copy_from_url(source_blob.url)
        source_blob.delete_blob()
        print("Blob moved to archive successfully.")
    except Exception as e:
        log_error(ERROR_CODES["upload_error"], f"Error processing file: {e}", script_name)
        print(f"Error moving blob to archive: {e}")
        raise


try:
    print("==Setting up logger...")
    setup_logger(script_name)
    print("==Logger established, initializing entry...")
    init_log_entry(script_name)
    update_log_extra_fields(
        script_name,
        flow_id="CC4C3D2E-D212-4C3E-89D4-41FCA781FE4F",
        sub_entity_id="270681372001"
    )

    print("==Downloading blob...")
    matching_blob = download_csv_from_blob()
    print("==Download complete")
    print("==Fetching unique carriers")
    unique_carriers, df = get_unique_carriers(local_csv_path)
    print("==Unique carriers stored")

    # options = webdriver.ChromeOptions()
    # options.add_argument("--start-maximized")
    # driver = webdriver.Chrome(service=service, options=options)

    driver.get("https://accounts.zoho.com/signin?servicename=ZohoCRM")
    time.sleep(5)
    try:
        email_input = WebDriverWait(driver, 30).until(
            EC.presence_of_element_located((By.XPATH, '//*[@id="login_id"]'))
        )
        email_input.send_keys(login_email)
        next_button = driver.find_element(By.XPATH, '//*[@id="nextbtn"]')
        next_button.click()
        time.sleep(5)
        password_input = WebDriverWait(driver, 30).until(
            EC.presence_of_element_located((By.XPATH, '//*[@id="password"]'))
        )
        password_input.send_keys(password_needed)
        sign_in_button = driver.find_element(By.XPATH, '//*[@id="nextbtn"]')
        sign_in_button.click()
        print("==Waiting up to 30 minutes for 'Contracting' element to appear... Enter OTP if needed.")
        WebDriverWait(driver, 1800).until(
            EC.presence_of_element_located((By.XPATH, '//a[contains(@href, "/tab/CustomModule8")]'))
        )
        print("=='Contracting' element detected, continuing process...")
        time.sleep(5)
    except TimeoutException:
        print("Already logged in. Skipping login process.")

    for carrier in unique_carriers:
        carrier_file = os.path.join(download_folder, f"{carrier}_data.csv")
        df[df['carrier_name'] == carrier].to_csv(carrier_file, index=False, quoting=1)
        try:
            upload_carrier_data(driver, carrier, carrier_file)
        finally:
            if os.path.exists(carrier_file):
                os.remove(carrier_file)
                print(f"Deleted temp carrier file: {carrier_file}")

    driver.quit()

    flow_url = ("https://prod-121.westus.logic.azure.com:443/workflows/"
                "394c6c03f0d54922b244f7729fcf5b68/triggers/manual/paths/invoke?"
                "api-version=2016-06-01&sp=%2Ftriggers%2Fmanual%2Frun&sv=1.0&sig=TbeSVXMMPg10tzGSQN_DOtcRLU_ten25ZpsviTlmdWo")
    send_results_and_archive(flow_url, matching_blob)
    move_blob_to_archive(matching_blob)


finally:
    if os.path.exists(local_csv_path):
        os.remove(local_csv_path)
        print(f"Temporary CSV file deleted: {local_csv_path}")

    update_log_extra_fields(
        script_name,
        flow_id="CC4C3D2E-D212-4C3E-89D4-41FCA781FE4F",
        sub_entity_id="270681372001"
    )
    log_overall_result(script_name)
