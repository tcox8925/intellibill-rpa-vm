import os
import time
import json
import pandas as pd
import requests
from datetime import datetime
import datetime as dt

import db_connection
import email_utils
from date_utils import get_current_date_info
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException
from selenium.webdriver.chrome.service import Service
from azure.identity import DefaultAzureCredential, ClientSecretCredential
from azure.storage.blob import BlobServiceClient
from azure.keyvault.secrets import SecretClient
from chrome_utils import get_chrome_driver
import carrier_handlers as ch
import matrix_loader as ml
import azure_blob_utils
from eod_utils import end_of_day_check
from logger import setup_logger, log_error, log_success, log_final_entry, ERROR_CODES, init_log_entry,update_log_extra_fields
import eod_utils

# Set up logger
script_name = "ACU_CRM_Upload"

today = datetime.today().date()
date_info = get_current_date_info()
today_str = datetime.now().strftime("%m%d%Y")

# Configuration
print("==Setting chrome options")
chrome_options = webdriver.ChromeOptions()
chrome_options.add_argument("user-data-dir=C:/Users/actua/AppData/Local/Google/Chrome/User Data Testing")
chrome_options.add_argument("profile-directory=DefaultTesting")

download_folder = "C:\\Users\\actua\\Downloads"
today_date = datetime.now().strftime("%Y_%m_%d")
month_folder = datetime.now().strftime("%Y %m %b")

key_vault_name = os.getenv("KEY_VAULT_NAME", "")
linked_service_name = '834_analytics_key_vault'
last_eod_flag_refresh_date = dt.date.today()

# Selenium Configuration
print("==Loading selenium configuration")
driver_path = "C:\\Users\\actua\\Desktop\\work\\Tools\\chromedriver-win64\\chromedriver.exe"
service = Service(driver_path)

########################################### Endless Main Run ###########################################
while True:
    print("==Continuing process loop...")
    # Main loop
    while not eod_utils.end_of_day_check():
        process_matrix = ml.get_process_matrix()
        load_matrix = ml.get_load_matrix()

        for _, row in process_matrix.iterrows():
            script_name = row["script_name"]
            carrier_id = row["carrier_id"]
            company_id = row["company_id"]
            disabled_until = row["disabled_until"]

            if disabled_until is not None and disabled_until != 'None':
                if datetime.strptime(disabled_until, '%Y-%m-%d').date() > dt.date.today():
                    print(f"==Entry for {script_name} has been disabled until {row['disabled_until']}.")
                    continue

            if row.get("active_flag", "").lower() != 'yes': # AND EOD flag does not say we should skip carrier
                print(f"==Entry for {script_name} is not marked active. Skipping.")
                continue

            # SINGLE CARRIER DEBUG FILTER
            #if row.get("script_name", "") != 'ACR_BCBSTX_RPA':
            #    print(f"==Entry for {script_name} is not the selected debug target. Skipping.")
            #    continue

            # Exclude carriers with currently maxed EOD flags (Will reset every day!)
            if row['eod_times'] is not None and row['eod_times'] != 'None':
                print(row['script_name'])
                print(row['eod_times'])
                num_of_eod_triggers = len(row['eod_times'].split(','))
                if int(row['eod_flag']) >= num_of_eod_triggers:
                    print(f"=={script_name}'s EOD flag is maxed out. Skipping process, EOD flag will reset tomorrow.")
                    continue


            script_name_logged = setup_logger(script_name)
            init_log_entry(script_name_logged)
            update_log_extra_fields(
                script_name_logged,
                process_type="ACR",
                carrier_id=carrier_id,
                company_id=company_id,
                flow_id="",
                sub_entity_id="270681372001"
            )
            try:
                print(f"Processing script: {script_name}")
                schedule = row.get("schedule", "").lower()
                if schedule != 'daily':
                    print(f"Unimplemented schedule type: '{schedule}', skipping")
                    continue

                df_rules = load_matrix[(load_matrix['global'] == 'Yes') | (load_matrix['carrier_id'] == carrier_id)]
                print(f"Rules:\n{df_rules.to_string()}")

                print("use_profile_path value:", row.get("use_profile_path", ""))
                profile_path = row["profile_path"] if str(row.get("use_profile_path", "")) == "YES" else None

                driver = get_chrome_driver(profile_path=profile_path, download_folder=download_folder)
                handler = ch.handler_map.get(script_name)
                print("==Attempting handler()")
                result = handler(driver, row, df_rules, date_info)
                print(result)
                driver.quit()

                log_success()
                log_final_entry(script_name_logged)

            except Exception as e:
                print(f"General exception caught, applying general error to log: {e}")
                print("==Setting carrier active_flag to disabled... Flag will need to be re-enabled after the error is resolved.")
                db_connection.disable_carrier_active_flag(carrier_id)
                ch.emergency_upload_incomplete_batch(row)
                email_utils.send_carrier_error_alert_email(carrier_id, script_name)
                log_error(ERROR_CODES["general_error"], str(e), script_name_logged)
                log_final_entry(script_name_logged)

        print("==Sending run summary email...")
        email_utils.send_acr_summary_email()
        print("==Resetting run_id...")
        db_connection.reset_run_id()
        print("==All carriers processed. Waiting an hour to check again...")

        time.sleep(3600)

    print("==An eod trigger time has been passed and needs to be handled. Waiting 30 seconds...")
    time.sleep(30)
    eod_utils.run_eods()
    print('==Finished eod functions...')

print("The process loop was exited somehow...?")