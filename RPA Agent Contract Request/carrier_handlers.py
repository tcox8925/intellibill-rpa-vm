import glob
import shutil

import pandas as pd
from selenium.webdriver import ActionChains
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.common.by import By
from selenium.common.exceptions import TimeoutException, NoSuchElementException
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import Select
import time
import random
from datetime import datetime, timedelta, timezone
from calendar import month_name
import os
import pytz

import date_utils
import db_connection
import email_utils
import string_utils
from drive_utils import get_drive_service, download_file, list_all_files_recursive, upload_file, update_file
from email_utils import send_ambetter_email
from eod_utils import end_of_day_check
from logger import log_error, ERROR_CODES
import zoho_utils
import contract_rules
import db_connection as db
import xlwings as xlw
import paramiko
from typing import Any, Dict, Optional
from azure_blob_utils import authenticate_blob_storage, upload_file_to_blob

CONTRACTS_MASTER_TABLE_NAME = 'wpo.lup_master_agents_contracts'
CONTRACTS_MASTER_TABLE_PK = 'pk_id'


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


def run_bcbsmi_acr(driver, matrix_row, df_rules, date_info):
    print("Running BCBSMI handler...")

    using_postgres = True
    # Pull contracts from postgres
    df_contracts = db_connection.get_contracts_by_carrier(matrix_row["carrier_id"], matrix_row['status_message'],
                                                          matrix_row['use_test_npns'],
                                                          batch_size=int(matrix_row['batch_size']))
    # If postgres is empty, pull from zoho
    if df_contracts is None:
        using_postgres = False
        df_contracts = zoho_utils.get_contracts_by_carrier(matrix_row["carrier_id"], matrix_row['status_message'],
                                                           matrix_row['use_test_npns'],
                                                           batch_size=int(matrix_row['batch_size']))
    # If both CRMs are empty, carrier is finished
    if df_contracts is None:
        return None

    while len(df_contracts) > 0 and not end_of_day_check():
        print(f"==Beginning new batch for {matrix_row['script_name']}")

        if using_postgres:
            df_contracts.loc[df_contracts['agent_type'].str.contains('Firm')] = db_connection.bcbs_mi_firm_adjustments(
                matrix_row["carrier_id"], matrix_row['status_message'],
                df_contracts.loc[df_contracts['agent_type'].str.contains('Firm')])
        else:
            df_contracts.loc[df_contracts['agent_type'].str.contains('Firm')] = zoho_utils.bcbs_mi_firm_adjustments(
                matrix_row["carrier_id"], matrix_row['status_message'],
                df_contracts.loc[df_contracts['agent_type'].str.contains('Firm')])

        # Pit data against rules, add data/notes where necessary, process_flag = 0
        df_contracts = contract_rules.check_against_rules(df_contracts, df_rules)
        print(f"==Data going into: ops_acr_contract_queue")
        db.upload_contracts_into_queue(df_contracts)

        rpa_rules = df_rules.loc[df_rules['rule_type'] == 'rpa']

        print("==Fetching newest batch_id")
        batch_id = df_contracts['batch_id'].astype(int).max()

        # Launch threads that will pull from temp_table to handle contracts
        # Make sure to handle retries! Insert a new entry when you do?
        while db.contract_to_process_exists(batch_id):
            print("==Performing web navigation")
            contract = db.fetch_next_contract_for_processing(batch_id)
            print(f"==Contract going into webnav:\n{contract}")
            replacement_row = run_bcbsmi_acr_webnav(driver, matrix_row, contract, rpa_rules, date_info)
            print("==Replacement row going into synapse:")
            print(replacement_row.to_string())
            db.update_contract_by_npn(replacement_row)

        finished_contracts = db.get_contracts_by_batch_id(batch_id)
        print(f"\n\n==Contract dataframe going into CRM:\n{finished_contracts.to_string()}")
        if matrix_row['dry_run'].lower() == 'no':
            print("==Script does not have dry run enabled, continuing to CRM bulk write and notes.")
            # Zoho Upload
            zoho_utils.update_contract_batch_on_crm(finished_contracts.drop(columns='pk_id', errors='ignore'))
            zoho_utils.update_notes(finished_contracts.drop(columns='pk_id', errors='ignore'))
            # Postgres Upload ( drop anything without a pk_id )
            finished_contracts = finished_contracts.loc[finished_contracts['pk_id'] != None]
            finished_contracts = finished_contracts.loc[finished_contracts['pk_id'] != 'None']
            db_connection.update_contract_batch_in_master_table(finished_contracts, CONTRACTS_MASTER_TABLE_PK,
                                                                CONTRACTS_MASTER_TABLE_NAME)
            db_connection.update_notes(finished_contracts)
        else:
            print("==Dry run enabled, skipping CRM writes.")

        print("==Batch completed.")
        time.sleep(2)
        print("==Reassigning df_contracts.")
        using_postgres = True
        df_contracts = db_connection.get_contracts_by_carrier(matrix_row["carrier_id"], matrix_row['status_message'],
                                                              matrix_row['use_test_npns'],
                                                              batch_size=int(matrix_row['batch_size']))
        # If postgres is empty, pull from zoho
        if df_contracts is None:
            using_postgres = False
            df_contracts = zoho_utils.get_contracts_by_carrier(matrix_row["carrier_id"], matrix_row['status_message'],
                                                               matrix_row['use_test_npns'],
                                                               batch_size=int(matrix_row['batch_size']))
        # If both CRMs are empty, carrier is finished
        if df_contracts is None:
            print(f"==Carrier level finished for {matrix_row['script_name']}")
            return None
        if matrix_row['use_test_npns'].lower() == 'yes':
            print(f"==Finished doing test run for {matrix_row['script_name']}, stopping carrier.")
            return None
    print(f"==Carrier level finished for {matrix_row['script_name']}")
    return None


def run_bcbsmi_acr_webnav(driver, matrix_row, contract, rpa_rules, date_info):
    print("==Entered webnav")
    print(contract["process_flag"])
    if str(contract["process_flag"]) == '1':
        print("==Process flag already marked 1, skipping contract upload.")
        return contract
    print(f"==RPA Rules:\n{rpa_rules.to_string()}")
    first_name = contract["agent_first_name"]
    last_name = contract["agent_last_name"]
    url = matrix_row["url"]
    print("==Beginning contract upload")
    print(f"==Navigating to URL: {url}")
    driver.get(url)

    # Perform login if needed
    if matrix_row["log_in"].upper() == "YES":
        try:
            print("Implement Login Functionality")
        except Exception as e:
            log_error(ERROR_CODES["login_error"], "Login page timeout or login fields not found.",
                      matrix_row["script_name"])
            print(f"Login page not found or timeout occurred. Exiting... {e}")
            contract['process_flag'] = 1
            contract['error_message'] = f'Error occurred during login process.'
            raise Exception
            return contract

    # Enter information
    try:
        email_field = WebDriverWait(driver, 30).until(
            EC.presence_of_element_located(
                (By.XPATH, '//*[@name="customFields[1].simpleValue"]'))
        )
        email_field.send_keys(contract["email_address"])

        first_name_field = WebDriverWait(driver, 30).until(
            EC.presence_of_element_located(
                (By.XPATH, '//*[@name="customFields[2].simpleValue"]'))
        )
        first_name_field.send_keys(first_name)

        npn_field = WebDriverWait(driver, 30).until(
            EC.presence_of_element_located(
                (By.XPATH, '//*[@name="customFields[6].simpleValue"]'))
        )
        npn_field.send_keys(contract["npn"])

        last_name_field = WebDriverWait(driver, 30).until(
            EC.presence_of_element_located(
                (By.XPATH, '//*[@name="customFields[7].simpleValue"]'))
        )
        last_name_field.send_keys(last_name)

        agency_dropdown = WebDriverWait(driver, 30).until(
            EC.presence_of_element_located(
                (By.XPATH, '//*[@name="customFields[3].values"]'))
        )
        select = Select(agency_dropdown)
        agency_name = rpa_rules.loc[rpa_rules['field'] == 'Agency Name']['expected_value'].to_string()
        agency_name = 'Agility Insurance Services, LLC'
        print("Agency name: " + agency_name)
        select.select_by_visible_text(agency_name)

        time.sleep(2)

        try:
            email_error_fetch = WebDriverWait(driver, 10).until(
                EC.presence_of_element_located(
                    (By.XPATH, '//span[@id="info_cf_caseEditPageForm_1406_1"]'))
            )
            error_text = email_error_fetch.text
            if error_text == '' or error_text is None:
                raise Exception
            print(error_text)
            matching_rule = rpa_rules.loc[rpa_rules['expected_value'] == error_text]
            print(f"==Matching rule to expected text:\n{matching_rule.to_string()}")
            contract = contract_rules.check_against_rules(contract, matching_rule, field_value_override=error_text)
            print(contract.to_string())

            return contract
        except Exception as e:
            print(f"No error found after searching. Attempting to continue. {e}")

        check_info_button = WebDriverWait(driver, 10).until(
            EC.presence_of_element_located(
                (By.XPATH, '//*[@value="Check Producer Information"]'))
        )
        check_info_button.click()

        time.sleep(2)

        try:
            error_fetch = WebDriverWait(driver, 10).until(
                EC.presence_of_element_located(
                    (By.XPATH, '//table[@class="cfHolder"]/tbody/tr/td/p'))
            )
            error_text = error_fetch.text
            print(error_text)
            matching_rule = rpa_rules.loc[rpa_rules['expected_value'] == error_text]
            print(f"==Matching rule to expected text:\n{matching_rule.to_string()}")
            contract = contract_rules.check_against_rules(contract, matching_rule, field_value_override=error_text)
            print(contract.to_string())

            return contract
        except Exception as e:
            print(f"No error found after searching. Attempting to continue. {e}")

        time.sleep(4)

        name_fetch = WebDriverWait(driver, 10).until(
            EC.presence_of_element_located(
                (By.XPATH, '//table[@id="tableControllerEntryTable61007"]/tbody'))
        )

    except Exception as e:
        log_error(ERROR_CODES["navigation_error"], "Error during inputting information.", matrix_row["script_name"])
        print(f"Error during inputting information, ending process: {e}")
        contract['process_flag'] = 1
        contract['error_message'] = f'Error occurred during information entering process.'
        raise Exception
        return contract

    # Submit information
    try:
        print(f"Table data:\n==\n{name_fetch.text}\n==")
        print(f"Name matches? {name_fetch.text.lower()}")
        name_fetch_text = string_utils.trim_name(name_fetch.text.lower())
        first_name_crm = string_utils.trim_name(first_name.item().lower())
        last_name_crm = string_utils.trim_name(last_name.item().lower())
        print(f"Portal name: {name_fetch_text}")
        print(f"CRM first name: {first_name_crm}")
        print(f"CRM last name: {last_name_crm}")
        print(((first_name_crm in name_fetch_text) and (last_name_crm in name_fetch_text)) or (
                (name_fetch_text.split(',')[1].strip() in first_name_crm) and (
                name_fetch_text.split(',')[0].strip() in last_name_crm)))
        if ((first_name_crm in name_fetch_text) and (last_name_crm in name_fetch_text)) \
                or ((name_fetch_text.split(',')[1].strip() in first_name_crm) and (
                name_fetch_text.split(',')[0].strip() in last_name_crm)):
            print("Creating contract...")
            time.sleep(2)

            if matrix_row['dry_run'].lower() == 'no':
                create_button = WebDriverWait(driver, 30).until(
                    EC.presence_of_element_located(
                        (By.XPATH, '//*[@value="Create"]'))
                )
                create_button.click()

                # Wait for portal to refresh itself
                time.sleep(15)
                result_fetch = WebDriverWait(driver, 30).until(
                    EC.presence_of_element_located(
                        (By.XPATH, '//*[@id="body-content"]'))
                )
                # Take this text, locate it in the rules, then use that row to set CRM note, status, etc.
                print("==Reading result...")
                result = result_fetch.text
                print(result)
                result = str(result)
                print(result)
                result = result.strip()
                print(result)
                matching_rule = rpa_rules.loc[rpa_rules['expected_value'].str.contains(result)]
                print(f"Matching rule to expected text: {matching_rule.to_string()}")
                contract = contract_rules.check_against_rules(contract, matching_rule, field_value_override=result)
                print(contract.to_string())
            else:
                print("Dry run enabled. Skipping submission steps.")
        else:
            print("Error: Name was not contained in table.")
            matching_rule = rpa_rules.loc[rpa_rules['field'] == 'agent name']
            print(f"Matching rule to field: {matching_rule.to_string()}")
            contract = contract_rules.check_against_rules(contract, matching_rule, field_value_override='agent_name',
                                                          result_override=False)
            return contract

    except Exception as e:
        log_error(ERROR_CODES["download_button_not_found"], "", matrix_row["script_name"])
        print(f"Error during submission, ending process: {e}")
        contract['process_flag'] = 1
        contract['error_message'] = f'Error occurred during submission process.'
        raise Exception

    return contract


def run_bcbstx_acr(driver, matrix_row, df_rules, date_info):
    print("Running BCBSTX handler...")

    using_postgres = True
    # Pull contracts from postgres
    df_contracts = db.get_contracts_by_carrier(matrix_row["carrier_id"], matrix_row['status_message'],
                                               matrix_row['use_test_npns'],
                                               batch_size=int(matrix_row['batch_size']),
                                               appointment_type_limit='Producer')
    # If postgres is empty, pull from zoho
    if df_contracts is None:
        using_postgres = False
        df_contracts = zoho_utils.get_contracts_by_carrier(matrix_row["carrier_id"], matrix_row['status_message'],
                                                           matrix_row['use_test_npns'],
                                                           batch_size=int(matrix_row['batch_size']),
                                                           appointment_type_limit='Producer')
    # If both CRMs are empty, carrier is finished
    if df_contracts is None:
        return None

    while len(df_contracts) > 0 and not end_of_day_check():
        print(f"==Beginning new batch for {matrix_row['script_name']}")

        # Run firm adjustments (if needed)
        if using_postgres:
            df_contracts.loc[df_contracts['agent_type'].str.contains('Firm')] = db_connection.bcbstx_firm_adjustments(
                matrix_row["carrier_id"], matrix_row['status_message'],
                df_contracts.loc[df_contracts['agent_type'].str.contains('Firm')])
        else:
            df_contracts.loc[df_contracts['agent_type'].str.contains('Firm')] = zoho_utils.bcbstx_firm_adjustments(
                matrix_row["carrier_id"], matrix_row['status_message'],
                df_contracts.loc[df_contracts['agent_type'].str.contains('Firm')])

        # Pit data against rules, add data/notes where necessary, process_flag = 0
        df_contracts = contract_rules.check_against_rules(df_contracts, df_rules)

        print(f"==Data going into: ops_acr_contract_queue")
        db.upload_contracts_into_queue(df_contracts)

        rpa_rules = df_rules.loc[df_rules['rule_type'] == 'rpa']

        print("==Fetching newest batch_id:")
        batch_id = df_contracts['batch_id'].astype(int).max()
        print(batch_id)

        # Launch threads that will pull from temp_table to handle contracts
        # Make sure to handle retries! Insert a new entry when you do?
        while db.contract_to_process_exists(batch_id):
            print("==Performing web navigation")
            contract = db.fetch_next_contract_for_processing(batch_id)
            print(f"==Contract going into webnav:\n{contract.to_string()}")
            replacement_row = run_bcbstx_acr_webnav(driver, matrix_row, contract, rpa_rules, date_info)
            print("Replacement row going into synapse:")
            print(replacement_row.to_string())
            db.update_contract_by_npn(replacement_row)

        finished_contracts = db.get_contracts_by_batch_id(batch_id)
        print(f"\n\n==Contract dataframe going into CRM:\n{finished_contracts.to_string()}")
        if matrix_row['dry_run'].lower() == 'no':
            # Zoho Upload
            zoho_utils.update_contract_batch_on_crm(finished_contracts.drop(columns='pk_id', errors='ignore'))
            zoho_utils.update_notes(finished_contracts.drop(columns='pk_id', errors='ignore'))
            # Postgres Upload ( drop anything without a pk_id )
            finished_contracts = finished_contracts.loc[finished_contracts['pk_id'] != None]
            finished_contracts = finished_contracts.loc[finished_contracts['pk_id'] != 'None']
            db_connection.update_contract_batch_in_master_table(finished_contracts, CONTRACTS_MASTER_TABLE_PK,
                                                                CONTRACTS_MASTER_TABLE_NAME)
            db_connection.update_notes(finished_contracts)
        else:
            print("==Dry run enabled, skipping CRM bulk write and notes.")

        print("==Batch completed.")
        print("==Reassigning df_contracts.")
        using_postgres = True
        df_contracts = db_connection.get_contracts_by_carrier(matrix_row["carrier_id"], matrix_row['status_message'],
                                                              matrix_row['use_test_npns'],
                                                              batch_size=int(matrix_row['batch_size']))
        # If postgres is empty, pull from zoho
        if df_contracts is None:
            using_postgres = False
            df_contracts = zoho_utils.get_contracts_by_carrier(matrix_row["carrier_id"], matrix_row['status_message'],
                                                               matrix_row['use_test_npns'],
                                                               batch_size=int(matrix_row['batch_size']))
        # If both CRMs are empty, carrier is finished
        if df_contracts is None:
            print(f"==Carrier level finished for {matrix_row['script_name']}")
            return None

        if matrix_row['use_test_npns'].lower() == 'yes':
            print(f"==Finished doing test run for {matrix_row['script_name']}, stopping carrier.")
            return None
    print(f"==Carrier level finished for {matrix_row['script_name']}")
    return None


def run_bcbstx_acr_webnav(driver, matrix_row, contract, rpa_rules, date_info):
    print("Running BCBSTX ACR Webnav handler...")

    agent_type = contract['agent_type'].item()
    first_name = contract['agent_first_name'].item()
    last_name = contract['agent_last_name'].item()
    npn = contract['npn'].item()
    email = contract['email_address'].item()
    phone = contract['selected_phone'].item() or contract['phone'].item() or contract['other_phone'].item() or contract[
        'mobile_phone'].item()
    phone = phone.replace('+', '').replace('-', '')
    phone = phone.replace('(', '').replace(')', '')
    phone = phone.replace(' ', '')
    if len(phone) > 10:
        phone = phone[1:]

    if agent_type == 'Firm':
        agency_npn = contract['agency_npn'].item()
        agency_name = contract['agency_full_name'].item()
        agency_email = contract['email_address'].item()
        email = contract['responsible_agent_email'].item() or contract['email_address'].item()

    logged_in_check = None
    try:
        logged_in_check = WebDriverWait(driver, 10).until(
            EC.presence_of_element_located(
                (By.XPATH, '//*[@id="newCaseMenuItem"]'))
        )
    except Exception as e:
        print("==Could not confirm that process is already logged in.")

    # Perform login if needed
    if matrix_row["log_in"].upper() == "YES" and logged_in_check is None:
        try:
            print("==Logging in...")
            driver.get(matrix_row['url'])

            username_field = WebDriverWait(driver, 30).until(
                EC.presence_of_element_located(
                    (By.XPATH, '//*[@id="loginNameInput"]'))
            )
            username_field.send_keys(matrix_row['email'])

            password_field = WebDriverWait(driver, 30).until(
                EC.presence_of_element_located(
                    (By.XPATH, '//*[@id="passwordField"]'))
            )
            password_field.send_keys(matrix_row['password'])

            check_info_button = WebDriverWait(driver, 30).until(
                EC.presence_of_element_located(
                    (By.XPATH, '//*[@id="loginSubmitButton"]'))
            )
            check_info_button.click()
        except Exception as e:
            log_error(ERROR_CODES["login_error"], "Login page timeout or login fields not found.",
                      matrix_row["script_name"])
            print("Login page not found or timeout occurred. Exiting...")
            contract['process_flag'] = 1
            contract['error_message'] = f'Error occurred during login on the portal.'
            raise Exception
    else:
        print("==Already logged in, moving to navigation.")

    # Navigate through pages
    try:
        plus_dropdown = WebDriverWait(driver, 30).until(
            EC.presence_of_element_located(
                (By.XPATH, '//*[@id="newCaseMenuItem"]'))
        )
        plus_dropdown.click()

        onboarding_link = WebDriverWait(driver, 30).until(
            EC.presence_of_element_located(
                (By.XPATH, '//*[@selid="workflow:link:Onboarding"]'))
        )
        onboarding_link.click()
    except Exception as e:
        log_error(ERROR_CODES["navigation_error"], "Navigation process failed.", matrix_row["script_name"])
        print("Navigation process failed, ending process.")
        contract['process_flag'] = 1
        contract['error_message'] = f'Error occurred during navigation on the portal.'
        raise Exception

    # Enter information
    try:
        print('step1')
        if agent_type != 'Firm':
            onboarding_type_dropdown = Select(WebDriverWait(driver, 30).until(
                EC.presence_of_element_located(
                    (By.XPATH, '//*[@clientname="onboarding_type"]')))
            )
            onboarding_type_dropdown.select_by_visible_text('Producer')
        elif agent_type == 'Firm':
            onboarding_type_dropdown = Select(WebDriverWait(driver, 30).until(
                EC.presence_of_element_located(
                    (By.XPATH, '//*[@clientname="onboarding_type"]')))
            )
            onboarding_type_dropdown.select_by_visible_text('Agency')
        time.sleep(3)

        print('step2')
        checkbox_tx = WebDriverWait(driver, 30).until(
            EC.presence_of_element_located(
                (By.XPATH, '//*[@systemid="tx"]'))
        )
        checkbox_tx.click()
        time.sleep(3)

        if agent_type == 'Firm':
            print('==Processing a firm contract, entering firm data')
            print('step2.1')
            agency_npn_field = WebDriverWait(driver, 30).until(
                EC.presence_of_element_located(
                    (By.XPATH, '//*[@clientname="ob_invitation_agencynpn"]'))
            )
            agency_npn_field.send_keys(agency_npn)
            time.sleep(3)
            print('step2.2')
            agency_name_field = WebDriverWait(driver, 30).until(
                EC.presence_of_element_located(
                    (By.XPATH, '//*[@clientname="agency_name"]'))
            )
            agency_name_field.send_keys(agency_name)
            time.sleep(3)
            print('step2.3')
            agency_email_field = WebDriverWait(driver, 30).until(
                EC.presence_of_element_located(
                    (By.XPATH, '//*[@clientname="agency_email"]'))
            )
            agency_email_field.send_keys(agency_email)
            time.sleep(3)

        print('step3')
        npn_field = WebDriverWait(driver, 30).until(
            EC.presence_of_element_located(
                (By.XPATH, '//*[@clientname="ob_invitation_npn"]'))
        )
        npn_field.send_keys(npn)
        time.sleep(3)

        print('step4')
        first_name_field = WebDriverWait(driver, 30).until(
            EC.presence_of_element_located(
                (By.XPATH, '//*[@clientname="first_name"]'))
        )
        first_name_field.send_keys(first_name)
        time.sleep(3)

        print('step5')
        last_name_field = WebDriverWait(driver, 30).until(
            EC.presence_of_element_located(
                (By.XPATH, '//*[@clientname="last_name"]'))
        )
        last_name_field.send_keys(last_name)
        time.sleep(3)

        print('step6')
        email_field = WebDriverWait(driver, 30).until(
            EC.presence_of_element_located(
                (By.XPATH, '//*[@clientname="email"]'))
        )
        email_field.send_keys(email)
        time.sleep(3)

        print('step7')
        phone_field = WebDriverWait(driver, 30).until(
            EC.presence_of_element_located(
                (By.XPATH, '//*[@clientname="contact_phone"]'))
        )
        phone_field.send_keys(phone[0:3] + '-' + phone[3:6] + '-' + phone[6:10])
        time.sleep(5)

        print('step8')
        checkbox_acknowledge = WebDriverWait(driver, 5).until(
            EC.presence_of_element_located(
                (By.XPATH, '//li/label/input[@clientname="ob_acknowlegement_msg"]/..'))
        )
        last_name_field.click()  # Trigger page load before clicking checkbox
        time.sleep(7)
        checkbox_acknowledge.click()
        time.sleep(4)

        print('step9')
        market_select = WebDriverWait(driver, 5).until(
            EC.presence_of_element_located(
                (By.XPATH, '//*[@systemid="retail"]'))
        )
        last_name_field.click()  # Trigger page load before clicking checkbox

        time.sleep(7)
        market_select.click()
        print('step10')
        time.sleep(3)

        if matrix_row['dry_run'].lower() == 'no':
            print("==Dry run is disabled, clicking continue button.")
            continue_button = WebDriverWait(driver, 30).until(
                EC.presence_of_element_located(
                    (By.XPATH, '//*[@value="Continue"]'))
            )
            continue_button.click()
            print('step11')
        else:
            print("==Dry run is enabled, stopping at creation step.")
            return contract
    except Exception as e:
        log_error(ERROR_CODES["input_error"], "Error during data entry on portal.", matrix_row["script_name"])
        print(f"Error during data entry, ending process: {e}")
        contract['process_flag'] = 1
        contract['error_message'] = f'Error occurred during data entry on the portal.'
        raise Exception

    time.sleep(6)

    # if next element present, skip error check
    tx_govt_ppid_presence_check = None
    try:
        tx_govt_ppid_presence_check = WebDriverWait(driver, 10).until(
            EC.presence_of_element_located(
                (By.XPATH, '//*[@id="contract_id_0_2"]/input'))
        )
    except:
        print("")
    if tx_govt_ppid_presence_check is None:
        print("==Could not find next element, maybe the next screen was not reached because of an error message?")
        try:
            print("==Checking for error message...")
            case_table = WebDriverWait(driver, 30).until(
                EC.presence_of_element_located((By.XPATH, '//*[@systemid="obmessage_text_0"]'))
            )
            case_table_text = case_table.text
            print(f"==Error message text:\n{case_table_text}\n========================")

            if case_table_text != '':
                print(f"==An error message was found")
                matching_rule = rpa_rules.loc[rpa_rules['expected_value'] == case_table_text]
                print(f"Matching rule to expected text:\n{matching_rule.to_string()}")
                contract = contract_rules.check_against_rules(contract, matching_rule,
                                                              field_value_override=case_table_text)
                return contract
            else:
                print("==No error message found. Continuing...")
        except Exception as e:
            log_error(ERROR_CODES["navigation_error"], "Error during error checking on portal.",
                      matrix_row["script_name"])
            print("Error during error checking, ending process.")
            contract['process_flag'] = 1
            contract['error_message'] = f'Error occurred during error checking on the portal.'
            raise Exception
    else:
        print("Next screen detected, continuing...")

    # Submit information
    try:
        tx_govt_ppid = WebDriverWait(driver, 30).until(
            EC.presence_of_element_located(
                (By.XPATH, '//*[@id="contract_id_0_2"]/input'))
        )
        tx_govt_ppid.send_keys('075309000')
        tx_retail_ppid = WebDriverWait(driver, 30).until(
            EC.presence_of_element_located(
                (By.XPATH, '//*[@id="contract_id_1_2"]/input'))
        )
        tx_retail_ppid.send_keys('075309000')
        time.sleep(4)
        tx_govt_ppid.click()

        time.sleep(10)
        result1_fetch = WebDriverWait(driver, 30).until(
            EC.presence_of_element_located(
                (By.XPATH, '//*[@id="producer_name_0_3"]/div'))
        )
        result1 = result1_fetch.text
        print(f"r1: {result1}")

        result2_fetch = WebDriverWait(driver, 30).until(
            EC.presence_of_element_located(
                (By.XPATH, '//*[@id="producer_name_1_3"]/div'))
        )
        result2 = result2_fetch.text
        print(f"r2: {result2}")

        if result1 != 'AGILITY INSURANCE SERVICES':
            print("Mismatch result1")
            matching_rule = rpa_rules.loc[rpa_rules['field'] == 'Govt Parent Producer']
            print(f"Matching rule to field: {matching_rule.to_string()}")
            contract = contract_rules.check_against_rules(contract, matching_rule, field_value_override=result1)
            return contract
        if result2 != 'AGILITY INSURANCE SERVICES':
            print("Mismatch result2")
            matching_rule = rpa_rules.loc[rpa_rules['field'] == 'Retail Parent Producer']
            print(f"Matching rule to field: {matching_rule.to_string()}")
            contract = contract_rules.check_against_rules(contract, matching_rule, field_value_override=result2)
            return contract

        save_button = WebDriverWait(driver, 30).until(
            EC.presence_of_element_located(
                (By.XPATH, '//*[@id="tableControllerConfirmationSave"]'))
        )
        save_button.click()
        time.sleep(2)
        send_invitation_button = WebDriverWait(driver, 30).until(
            EC.presence_of_element_located(
                (By.XPATH, '//*[@value="Send Invitation"]'))
        )
        send_invitation_button.click()

    except Exception as e:
        log_error(ERROR_CODES["navigation_error"], "Error during submitting information.", matrix_row["script_name"])
        print("Error during submission, ending process.")
        contract['process_flag'] = 1
        contract['error_message'] = f'Error occurred during submission on the portal.'
        raise Exception
        return contract

    # Confirm contract was submitted and name is present in current cases table
    try:
        try_count = 0
        success = False
        while (try_count < 5) and not success:
            time.sleep(6)
            print(f"==Confirming submission is present... Attempt #{try_count}")

            home_page = WebDriverWait(driver, 30).until(
                EC.element_to_be_clickable((By.ID, "menuItem_homePage"))
            )
            home_page.click()
            print("Home page clicked")

            case_table = WebDriverWait(driver, 30).until(
                EC.presence_of_element_located((By.XPATH, '//*[@id="tbl_smart_list_236"]'))
            )

            case_table_text = case_table.text
            matching_rule = rpa_rules.loc[rpa_rules['field'] == 'Verification']
            print(f"Matching rule to field: {matching_rule.to_string()}")
            try:
                first_name_text = first_name.item()
                last_name_text = last_name.item()
            except:
                first_name_text = first_name
                last_name_text = last_name
            if (first_name_text.lower() in case_table_text.lower()) and (
                    last_name_text.lower() in case_table_text.lower()):
                print("==Name contained in table, submission confirmed.")
                success = True
                contract = contract_rules.check_against_rules(contract, matching_rule,
                                                              field_value_override=case_table_text,
                                                              result_override=True)
            else:
                print("==Name not found in table, retrying...")
            print(f"Name present? {success}")
            try_count += 1
        if not success:
            print("==Name not found in table, submission could not be confirmed.")
            contract = contract_rules.check_against_rules(contract, matching_rule, field_value_override=case_table_text,
                                                          result_override=False)

    except Exception as e:
        log_error(ERROR_CODES["navigation_error"], "Error during submission confirmation.", matrix_row["script_name"])
        print(f"Error during submission confirmation, ending process. {e}")
        contract['process_flag'] = 1
        contract['error_message'] = f'Error occurred during submission confirmation on the portal.'
        raise Exception
        return contract

    return contract


def run_molina_acr(driver, matrix_row, df_rules, date_info):
    print("Running Molina handler...")

    # While zoho has entries, we can start a new batch:
    df_contracts = zoho_utils.get_contracts_by_carrier(matrix_row["carrier_id"], matrix_row['status_message'],
                                                       matrix_row['use_test_npns'],
                                                       batch_size=int(matrix_row['batch_size']))
    if df_contracts is None:
        return None

    while len(df_contracts) > 0 and not end_of_day_check():
        print(f"==Beginning new batch for {matrix_row['script_name']}")

        # Pit data against rules, add data/notes where necessary, process_flag = 0
        df_contracts = contract_rules.check_against_rules(df_contracts, df_rules)
        print(f"==Data going into: ops_acr_contract_queue")
        db.upload_contracts_into_queue(df_contracts)

        rpa_rules = df_rules.loc[df_rules['rule_type'] == 'rpa']

        print("==Fetching newest batch_id:")
        batch_id = df_contracts['batch_id'].astype(int).max()
        print(batch_id)

        # Launch threads that will pull from temp_table to handle contracts
        # Make sure to handle retries! Insert a new entry when you do?
        while db.contract_to_process_exists(batch_id):
            print("==Performing web navigation")
            contract = db.fetch_next_contract_for_processing(batch_id)
            print(f"==Contract going into webnav:\n{contract.to_string()}")
            replacement_row = run_molina_acr_webnav(driver, matrix_row, contract, rpa_rules, date_info)
            print("Replacement row going into synapse:")
            print(replacement_row.to_string())
            db.update_contract_by_npn(replacement_row)

        finished_contracts = db.get_contracts_by_batch_id(batch_id)
        print(f"\n\n==Contract dataframe going into CRM:\n{finished_contracts.to_string()}")
        if matrix_row['dry_run'].lower() == 'no':
            print("==Script does not have dry run enabled, continuing to CRM bulk write and notes.")
            zoho_utils.update_contract_batch_on_crm(finished_contracts)
            zoho_utils.update_notes(finished_contracts)
            # db_connection.update_contract_batch_in_master_table(finished_contracts,CONTRACTS_MASTER_TABLE_PK,CONTRACTS_MASTER_TABLE_NAME)
            # db_connection.update_notes(finished_contracts)
        else:
            print("==Dry run enabled, skipping CRM bulk write and notes.")

        print("==Batch completed.")
        print("==Reassigning df_contracts.")
        df_contracts = zoho_utils.get_contracts_by_carrier(matrix_row["carrier_id"], matrix_row['status_message'],
                                                           matrix_row['use_test_npns'],
                                                           batch_size=int(matrix_row['batch_size']))
        if df_contracts is None:
            print(f"==Carrier level finished for {matrix_row['script_name']}")
            return None
        if matrix_row['use_test_npns'].lower() == 'yes':
            print(f"==Finished doing test run for {matrix_row['script_name']}, stopping carrier.")
            return None
    print(f"==Carrier level finished for {matrix_row['script_name']}")
    return None


def run_molina_acr_webnav(driver, matrix_row, contract, rpa_rules, date_info):
    print("Running Molina ACR handler...")

    driver.get(matrix_row['url'])

    # Perform login if needed
    if matrix_row["log_in"].upper() == "YES":
        try:
            time.sleep(2)
            username_field = WebDriverWait(driver, 30).until(
                EC.presence_of_element_located(
                    (By.XPATH, '//*[@id="login_id"]'))
            )
            username_field.send_keys(matrix_row['email'])
            time.sleep(2)

            password_field = WebDriverWait(driver, 30).until(
                EC.presence_of_element_located(
                    (By.XPATH, '//*[@id="password"]'))
            )
            password_field.send_keys(matrix_row['password'])
            time.sleep(2)

            login_button = WebDriverWait(driver, 30).until(
                EC.presence_of_element_located(
                    (By.XPATH, '//*[@id="submit"]'))
            )
            login_button.click()
            time.sleep(2)

            try:  # Continue from "Password expiring soon" screen if needed
                continue_with_login = WebDriverWait(driver, 6).until(
                    EC.presence_of_element_located(
                        (By.XPATH, '//*[@id="goToDomain"]'))
                )
                continue_with_login.click()
            except:
                print("Password change screen not detected. Continuing...")
        except Exception as e:
            log_error(ERROR_CODES["login_error"], "Login page timeout or login fields not found.",
                      matrix_row["script_name"])
            print(f"Login page not found or timeout occurred. Exiting... {e}")
            contract['process_flag'] = 1
            contract['error_message'] = f'Error occurred during login process.'
            raise Exception

    # Navigate through pages
    print('step1')
    original_window = driver.current_window_handle
    print('step2')
    time.sleep(7)

    try:
        molina_portal = WebDriverWait(driver, 240).until(
            EC.presence_of_element_located(
                (By.XPATH, "//*[@onclick=\"doLogin('molina.evolvenxt.com', 456336, 212)\"]"))
        )
        molina_portal.click()
        print('step3')
        # Switch to the new tab
        time.sleep(4)
        driver.close()
        print('step4')
        for handle in driver.window_handles:
            if handle != original_window:
                driver.switch_to.window(handle)
                break
        print("Switched to Molina tab.")
    except Exception as e:
        log_error(ERROR_CODES["navigation_error"], "Navigation process failed during portal select.",
                  matrix_row["script_name"])
        print(f"Navigation process failed during portal select, ending process. {e}")
        contract['process_flag'] = 1
        contract['error_message'] = f'Error occurred during navigation process portal select. {e}'
        raise Exception

    max_retry = 6
    retries = 0
    time.sleep(3)
    while retries < max_retry:
        try:
            print('Clicking downline broker...')
            downline_broker_dropdown = WebDriverWait(driver, 240).until(
                EC.presence_of_element_located(
                    (By.XPATH, '//*[@href="/portal/mpc_detail.htm"]'))
            )
            downline_broker_dropdown.click()
            time.sleep(4)

            print('Clicking onboarding...')
            onboarding = WebDriverWait(driver, 240).until(
                EC.presence_of_element_located(
                    (By.XPATH, '//*[@href="/portal_onboarding/onboarding_downline.htm"]'))
            )
            onboarding.click()
            time.sleep(4)

            print('Clicking create invite...')
            create_invite = WebDriverWait(driver, 240).until(
                EC.presence_of_element_located(
                    (By.XPATH, '//*[@onclick = "InsertNewCase();"]'))
            )
            create_invite.click()
            break
        except Exception as e:
            if retries < max_retry:
                print(f'==Navigation attempt {retries} failed.. retrying')
                retries += 1
                time.sleep(5)
                continue
            print('==Too many retry failures, giving up.')
            log_error(ERROR_CODES["navigation_error"], "Navigation process failed.", matrix_row["script_name"])
            print(f"Navigation process failed, ending process. {e}")
            contract['process_flag'] = 1
            contract['error_message'] = f'Error occurred during navigation process. {e}'
            raise Exception

    # Enter information
    try:
        onboarding_type = Select(WebDriverWait(driver, 30).until(
            EC.presence_of_element_located(
                (By.XPATH, '//*[@id="add_lob"]'))
        ))
        onboarding_type.select_by_visible_text("Initial")

        lob_field = Select(WebDriverWait(driver, 30).until(
            EC.presence_of_element_located(
                (By.XPATH, '//*[@id="brk_lob"]'))
        ))
        lob_field.select_by_visible_text("ACA - ACA")

        broker_type = Select(WebDriverWait(driver, 30).until(
            EC.presence_of_element_located(
                (By.XPATH, '//*[@id="brk_type"]'))
        ))
        broker_type.select_by_visible_text("Field Broker")

        sub_type = Select(WebDriverWait(driver, 30).until(
            EC.presence_of_element_located(
                (By.XPATH, '//*[@id="ACA_brk_sub_type"]'))
        ))
        sub_type.select_by_visible_text("Licensed Only Agent")

        sales_level = Select(WebDriverWait(driver, 30).until(
            EC.presence_of_element_located(
                (By.XPATH, '//*[@id="ACA_brk_level"]'))
        ))
        sales_level.select_by_visible_text("Agent - 01")

        npn_field = WebDriverWait(driver, 30).until(
            EC.presence_of_element_located(
                (By.XPATH, '//*[@id="create_npn"]'))
        )
        npn_field.send_keys(contract["npn"])

        print("==Inputting email...")

        email_field = WebDriverWait(driver, 30).until(
            EC.presence_of_element_located(
                (By.XPATH, '//*[@id="create_email"]'))
        )
        email_field.send_keys(contract["email_address"])

        print("==Email entered...")

        time.sleep(2)
        npn_field = WebDriverWait(driver, 30).until(
            EC.presence_of_element_located(
                (By.XPATH, '//*[@id="create_npn"]'))
        )
        npn_field.click()
    except Exception as e:
        log_error(ERROR_CODES["input_error"], "Error during inputting information.", matrix_row["script_name"])
        print(f"Error during inputting information, ending process. {e}")
        contract['process_flag'] = 1
        contract['error_message'] = f'Error occurred during data entry on the portal.'
        raise Exception

    time.sleep(4)
    # Check for data validation errors
    try:
        print("==Checking for data validation errors...")
        outcome = ''
        try:
            npn_error = driver.find_element(By.XPATH, '//*[@id="npn_fail"]')
            print(npn_error.text)
            if outcome == '' and npn_error.text != '':
                outcome = npn_error.text
        except Exception as e:
            print(f'==Error searching for "NPN error". {e}')
        try:
            email_error = driver.find_element(By.XPATH, '//*[@id="email_fail"]')
            print(email_error.text)
            if outcome == '' and email_error.text != '':
                outcome = email_error.text
        except Exception as e:
            print(f'==Error searching for "email error". {e}')
        try:
            npn_exc_error = driver.find_element(By.XPATH, '//*[@id="npn_exclude"]')
            print(npn_exc_error.text)
            if outcome == '' and npn_exc_error.text != '':
                outcome = npn_exc_error.text
        except Exception as e:
            print(f'==Error searching for "NPN exclusion error". {e}')

    except Exception as e:
        log_error(ERROR_CODES["data_validation_error"], "Error during portal data validation.",
                  matrix_row["script_name"])
        print(f"Error during portal data validation, ending process. {e}")
        contract['process_flag'] = 1
        contract['error_message'] = f'Error occurred during data validation process on the portal.'
        raise Exception

    print(f"outcome: {outcome}")
    if outcome != '':
        print("==Data validation error found.")
        matching_rule = rpa_rules.loc[rpa_rules['expected_value'] == outcome]
        print(f"Matching rule to expected text: {matching_rule.to_string()}")
        contract = contract_rules.check_against_rules(contract, matching_rule, field_value_override=outcome)
        print(contract.to_string())
    else:
        print("==No validation errors detected, attempting to submit...")
        # Submit information
        try:
            if matrix_row['dry_run'].lower() == 'no':
                print("==Script is not set to dry run, continuing to submission.")

                create_case = WebDriverWait(driver, 30).until(
                    EC.presence_of_element_located(
                        (By.XPATH, '//*[@id="Insert_button"]'))
                )
                create_case.click()

            else:
                print("==Dry run enabled, skipping creation step.")
        except Exception as e:
            log_error(ERROR_CODES["submit_button_not_found"], "", matrix_row["script_name"])
            print("Submit button not found, ending process.")
            contract['process_flag'] = 1
            contract['error_message'] = f'Error occurred during submission process.'
            raise Exception

        # Check for submission error or submission success message
        try:
            print("==Checking for portal errors...")
            outcome = ''
            try_count = 1
            while (try_count < 6) and (outcome == ''):
                print(f"==Waiting for message... Attempt #{try_count}")
                time.sleep(20)
                # Handles NPN production mismatch, contract request submission, and NIPR-related error messages
                try:
                    # failed_error = driver.find_element(By.XPATH, '//*[@id="custom_error_msg"]')
                    failed_error = WebDriverWait(driver, 20).until(
                        EC.presence_of_element_located(
                            (By.XPATH, '//*[@id="custom_error_msg"]'))
                    )
                    print(failed_error.text)
                    if outcome == '' and failed_error.text != '':
                        outcome = failed_error.text
                except Exception as e:
                    print(f'==Error searching for "failed error". {e}')

                try:
                    # success_message = driver.find_element(By.XPATH, '//*[@id="saved_msg"]')
                    success_message = WebDriverWait(driver, 20).until(
                        EC.presence_of_element_located(
                            (By.XPATH, '//*[@id="saved_msg"]'))
                    )
                    print(success_message.text)
                    if outcome == '' and success_message.text != '':
                        outcome = success_message.text
                except Exception as e:
                    print(f'==Error searching for "Success message". {e}')

                print(f"outcome: {outcome}")
                try_count += 1
            matching_rule = rpa_rules.loc[rpa_rules['expected_value'] == outcome]
            print(f"Matching rule to expected text: \n{matching_rule.to_string()}")
            contract = contract_rules.check_against_rules(contract, matching_rule, field_value_override=outcome)
            print(contract.to_string())

        except Exception as e:
            log_error(ERROR_CODES["data_verification_error"], "Error during portal error checking.",
                      matrix_row["script_name"])
            print("Error during portal error checking, ending process.")
            contract['process_flag'] = 1
            contract['error_message'] = f'Error occurred during submission process.'
            raise Exception

    print("==Webnav finished")
    return contract


def run_uhc_aca_acr(driver, matrix_row, df_rules, date_info):
    print("Running UHC ACA handler...")

    using_postgres = True
    # Pull contracts from postgres
    df_contracts = db_connection.get_contracts_by_carrier(matrix_row["carrier_id"], matrix_row['status_message'],
                                                          matrix_row['use_test_npns'],
                                                          batch_size=int(matrix_row['batch_size']))
    # If postgres is empty, pull from zoho
    if df_contracts is None:
        using_postgres = False
        df_contracts = zoho_utils.get_contracts_by_carrier(matrix_row["carrier_id"], matrix_row['status_message'],
                                                           matrix_row['use_test_npns'],
                                                           batch_size=int(matrix_row['batch_size']))
    # If both CRMs are empty, carrier is finished
    if df_contracts is None:
        return None

    while len(df_contracts) > 0 and not end_of_day_check():
        print(f"==Beginning new batch for {matrix_row['script_name']}")

        if using_postgres:
            # Run firm adjustments (if needed)
            df_contracts.loc[df_contracts['agent_type'].str.contains('Firm')] = db.uhc_aca_firm_adjustments(
                matrix_row["carrier_id"], matrix_row['status_message'],
                df_contracts.loc[df_contracts['agent_type'].str.contains('Firm')])
            # Run parent contract adjustments (if needed)
            df_contracts.loc[
                df_contracts['appointment_type'].str.contains('Subproducer')] = db.uhc_aca_subproducer_adjustments(
                matrix_row["carrier_id"], matrix_row['status_message'],
                df_contracts.loc[df_contracts['appointment_type'].str.contains('Subproducer')])
        else:
            # Run firm adjustments (if needed)
            df_contracts.loc[df_contracts['agent_type'].str.contains('Firm')] = zoho_utils.uhc_aca_firm_adjustments(
                matrix_row["carrier_id"], matrix_row['status_message'],
                df_contracts.loc[df_contracts['agent_type'].str.contains('Firm')])
            # Run parent contract adjustments (if needed)
            df_contracts.loc[
                df_contracts['appointment_type'].str.contains(
                    'Subproducer')] = zoho_utils.uhc_aca_subproducer_adjustments(
                matrix_row["carrier_id"], matrix_row['status_message'],
                df_contracts.loc[df_contracts['appointment_type'].str.contains('Subproducer')])

        # Pit data against rules, add data/notes where necessary, process_flag = 0
        df_contracts = contract_rules.check_against_rules(df_contracts, df_rules)
        print(f"==Data going into: ops_acr_contract_queue")
        db.upload_contracts_into_queue(df_contracts)

        rpa_rules = df_rules.loc[df_rules['rule_type'] == 'rpa']

        print("==Fetching newest batch_id:")
        batch_id = df_contracts['batch_id'].astype(int).max()
        print(batch_id)

        # Launch threads that will pull from acr_contract_queue to handle contracts
        # Make sure to handle retries! Insert a new entry when you do?
        while db.contract_to_process_exists(batch_id):
            print("==Performing spreadsheet operations")
            contract = db.fetch_next_contract_for_processing(batch_id)
            print(f"==Contract going into spreadsheet:\n{contract.to_string()}")
            replacement_row = run_uhc_aca_acr_spreadsheet(driver, matrix_row, contract, rpa_rules, date_info)
            print("Replacement row going into synapse:")
            print(replacement_row.to_string())
            db.update_contract_by_npn(replacement_row)

        finished_contracts = db.get_contracts_by_batch_id(batch_id)
        print(f"\n\n==Contract dataframe going into CRM:\n{finished_contracts.to_string()}")
        if matrix_row['dry_run'].lower() == 'no':
            print("==Script does not have dry run enabled, continuing to CRM bulk write and notes.")
            # Zoho Upload
            zoho_utils.update_contract_batch_on_crm(finished_contracts.drop(columns='pk_id', errors='ignore'))
            zoho_utils.update_notes(finished_contracts.drop(columns='pk_id', errors='ignore'))
            # Postgres Upload ( drop anything without a pk_id )
            finished_contracts = finished_contracts.loc[finished_contracts['pk_id'] != None]
            finished_contracts = finished_contracts.loc[finished_contracts['pk_id'] != 'None']
            db_connection.update_contract_batch_in_master_table(finished_contracts, CONTRACTS_MASTER_TABLE_PK,
                                                                CONTRACTS_MASTER_TABLE_NAME)
            db_connection.update_notes(finished_contracts)
        else:
            print("==Dry run enabled, skipping CRM bulk write and notes.")

        print("==Batch completed.")
        print("==Reassigning df_contracts.")
        using_postgres = True
        df_contracts = db_connection.get_contracts_by_carrier(matrix_row["carrier_id"], matrix_row['status_message'],
                                                              matrix_row['use_test_npns'],
                                                              batch_size=int(matrix_row['batch_size']))
        # If postgres is empty, pull from zoho
        if df_contracts is None:
            using_postgres = False
            df_contracts = zoho_utils.get_contracts_by_carrier(matrix_row["carrier_id"], matrix_row['status_message'],
                                                               matrix_row['use_test_npns'],
                                                               batch_size=int(matrix_row['batch_size']))
        # If both CRMs are empty, carrier is finished
        if df_contracts is None:
            print(f"==Carrier level finished for {matrix_row['script_name']}")
            return None

        if matrix_row['use_test_npns'].lower() == 'yes':
            print(f"==Finished doing test run for {matrix_row['script_name']}, stopping carrier.")
            return None
    print(f"==Carrier level finished for {matrix_row['script_name']}")
    return None


def run_uhc_aca_acr_spreadsheet(driver, matrix_row, contract, rpa_rules, date_info):
    print("Running UHC ACA ACR spreadsheet handler...")

    # Establish headers for spreadsheet that we will line our information up with
    insert_df = pd.DataFrame(columns=['entity_type', 'npn', 'first_name', 'last_name', 'agent_email', 'upline_name',
                                      'upline_wid', 'level', 'fein', 'agency_name', 'agency_npn',
                                      'responsible_agent_npn'])

    # Assign data following rules to the header table
    agent_type = contract['agent_type'].item()
    appointment_type = contract['appointment_type'].item()

    insert_df['first_name'] = contract['agent_first_name']
    insert_df['last_name'] = contract['agent_last_name']
    insert_df['agent_email'] = contract['email_address']

    if agent_type.lower() == 'individual':
        print("==Applying individual logic")
        insert_df['entity_type'] = 'IL'
        insert_df['npn'] = contract['npn']
        insert_df['agency_name'] = None
        insert_df['fein'] = None
        insert_df['responsible_agent_npn'] = None
    elif agent_type.lower() == 'firm':
        print("==Applying firm logic")
        insert_df['entity_type'] = 'CORP'
        insert_df['npn'] = contract['agency_npn']
        insert_df['fein'] = contract['agency_fein']
        insert_df['responsible_agent_npn'] = contract['npn']

        # Cleanup agency names, preserve true '.' endings
        agency_name = contract['agency_full_name'].item().strip()
        if agency_name[-2:] == ' .':
            agency_name = agency_name[0:len(agency_name) - 1].strip()
        if agency_name[-1] == ',':
            agency_name = agency_name[0:len(agency_name) - 1].strip()
        insert_df['agency_name'] = agency_name.strip()

    if appointment_type.lower() == 'producer':
        print("==Applying producer logic")
        insert_df['upline_name'] = 'AGILITY INSURANCE SERVICES, LLC'
        insert_df['upline_wid'] = 'ACA3072'
        insert_df['level'] = '20'
    elif appointment_type.lower() == 'subproducer':
        print("==Applying subproducer logic")
        insert_df['upline_name'] = contract['parent_full_name']
        insert_df['upline_wid'] = contract['parent_wn']
        insert_df['level'] = '1'

    # Cleanup upline names, preserve true '.' endings
    upline_name = insert_df['upline_name'].item().strip()
    if upline_name[-2:] == ' .':
        upline_name = upline_name[0:len(upline_name) - 1].strip()
    if upline_name[-1] == ',':
        upline_name = upline_name[0:len(upline_name) - 1].strip()
    insert_df['upline_name'] = upline_name.strip()

    print(f"==Data going into spreadsheet.")
    # if output does not exist, pull from new path
    date_info = date_utils.get_current_date_info()
    output_filepath = (matrix_row['g_drive_to_path'] + '\\' + date_info['current_month_number'] + ' ' + date_info[
        'current_month_year']
                       + '\\' + matrix_row['storage_file_name'] + date_info['today_date_mmddyyyy']
                       + '.' + matrix_row['file_extension'])
    if not os.path.exists(matrix_row['g_drive_to_path'] + '\\' + date_info['current_month_number'] + ' ' + date_info[
        'current_month_year']):
        print("current month folder not found, creating new one at:")
        print(matrix_row['g_drive_to_path'] + '\\' + date_info['current_month_number'] + ' ' + date_info[
            'current_month_year'])
        os.makedirs(matrix_row['g_drive_to_path'] + '\\' + date_info['current_month_number'] + ' ' + date_info[
            'current_month_year'])
    if not os.path.exists(output_filepath):
        # Use template filepath to generate the file for today, if it did not exist
        template_filepath = matrix_row['file_path'] + matrix_row['file_name'] + '.' + matrix_row['file_extension']
        shutil.copyfile(template_filepath, output_filepath)

    from_filepath = output_filepath

    print(f"From path: {from_filepath}")
    print(f"To path: {output_filepath}")
    spreadsheet_attempts = 1

    while spreadsheet_attempts < 4:
        print(f"==UHC Spreadsheet attempt #{spreadsheet_attempts}")
        try:
            book = xlw.Book(from_filepath)
            sheet = book.sheets[0]
            df = sheet.range("E7").expand().options(pd.DataFrame, header=1, index=False, numbers=int).value
            print(f"Data in spreadsheet:\n{df.to_string()}")

            print("==Checking for duplicates...")
            no_duplicates_present = True
            if (((str(insert_df['npn'].item()) in str(df['NPN']))
                 and (str(insert_df['responsible_agent_npn'].item()) in str(df['NPN of Principal'])))
                    or (str(insert_df['agent_email'].item()) in str(df['Producer Email']))):
                no_duplicates_present = False
            if no_duplicates_present:
                print("==No duplicates found, adding row to spreadsheet...")
                sheet.range(f"A{8 + len(df)}").value = [insert_df['fein'].item(), insert_df['agency_name'].item(),
                                                        insert_df['last_name'].item(), insert_df['first_name'].item()
                    , insert_df['npn'].item(), insert_df['responsible_agent_npn'].item(),
                                                        insert_df['agent_email'].item()
                    , insert_df['upline_name'].item(), insert_df['upline_wid'].item(), insert_df['level'].item()]
                book.save(output_filepath)

                matching_rule = rpa_rules.loc[rpa_rules['field'] == 'insert outcome']
                print(f"Matching rule to end result: \n{matching_rule.to_string()}")
                contract['insert outcome'] = 'Success'
                contract = contract_rules.check_against_rules(contract, matching_rule)
                print(contract.to_string())
            else:
                print("==Duplicate found, disabling export flag and sending alert email...")
                db_connection.disable_carrier_automatic_export_flag(matrix_row['carrier_id'])
                email_utils.send_duplicate_alert_email(matrix_row['script_name'], matrix_row['carrier_id'],
                                                       insert_df['npn'].item(),
                                                       responsible_agent_npn=insert_df['responsible_agent_npn'].item(),
                                                       email=insert_df['agent_email'].item())

                matching_rule = rpa_rules.loc[rpa_rules['field'] == 'insert outcome']
                print(f"Matching rule to end result: \n{matching_rule.to_string()}")
                contract['insert outcome'] = 'Failure'
                contract = contract_rules.check_against_rules(contract, matching_rule)
                print(contract.to_string())
            print("==Spreadsheet handling finished, closing excel")
            time.sleep(4)
            book.app.quit()

            try:
                contract.drop(labels='insert outcome', inplace=True)
            except Exception as e:
                print(f'==Minor exception during spreadsheet handling: {e}\n{contract.to_string()}')
            try:
                contract.drop(columns='insert outcome', inplace=True)
            except Exception as e:
                print(f'==Minor exception during spreadsheet handling: {e}\n{contract.to_string()}')
                
            return contract
        except Exception as e:
            print(f"==Exception during UHC spreadsheet handling: {e}")
        spreadsheet_attempts += 1
    raise Exception  # Raise exception if we could not add after all attempts


def run_uhc_aca_acr_eod(matrix_row):
    print("==Beginning UHC ACA end-of-day functions...")
    # Open spreadsheet, activate macro
    date_info = date_utils.get_current_date_info()
    original_filepath = (matrix_row['g_drive_to_path'] + '\\' + date_info['current_month_number'] + ' ' + date_info[
        'current_month_year']
                         + '\\' + matrix_row['storage_file_name'] + date_info['today_date_mmddyyyy']
                         + '.' + matrix_row['file_extension'])
    temp_filepath = (matrix_row['g_drive_to_path'] + '\\' + date_info['current_month_number'] + ' ' + date_info[
        'current_month_year']
                     + '\\' + matrix_row['storage_file_name'] + date_info['today_date_mmddyyyy']
                     + '_temp.' + matrix_row['file_extension'])
    # Check if file exists
    if not os.path.exists(original_filepath):
        print('==No file was created/found for today. Aborting.')
        return
    print(f"Original path: {original_filepath}")
    shutil.copyfile(original_filepath, temp_filepath)
    book = xlw.Book(temp_filepath)
    macro = book.macro('TransferData')
    macro()
    time.sleep(10)
    book.app.quit()
    # Check if file exists on desktop
    output_filepath = 'C:\\Users\\myopsadmin\\Desktop\\ACA3072_*.csv'
    try:
        filepath = glob.glob(output_filepath)[0]
        if len(filepath) == 0:
            raise Exception
    except Exception as e:
        print("Error occurred during UHC Export File Search. Perhaps no file was found?")
        raise Exception

    # === Blob Storage Dropoff ===
    file_name = os.path.basename(filepath)
    print(f"File name: {file_name}")
    remote_path = matrix_row['sftp_to_path'] + '/' + file_name
    print(f"Remote path: {remote_path}")

    try:
        client = authenticate_blob_storage(storage_account_name="agilitydataprd001")
        upload_file_to_blob(client, filepath, remote_path, container_name='sftpuhc')
    except Exception as e:
        print(
            f"Error during blob upload to primary dropoff: {e} || local filepath: {filepath} || remote_path: {remote_path}")
        raise Exception

    # === TEMPORARY SECONDARY DROPOFF ===
    remote_path = '/inbound/' + file_name
    print(f"Remote path: {remote_path}")

    try:
        client = authenticate_blob_storage()
        upload_file_to_blob(client, filepath, remote_path, container_name='834analytics-sftp-uhc')
        os.remove(filepath)
    except Exception as e:
        print(
            f"Error during blob upload to secondary dropoff: {e} || local filepath: {filepath} || remote_path: {remote_path}")
        raise Exception

    print("==Done")


def run_ambetter_acr(driver, matrix_row, df_rules, date_info):
    print("Running Ambetter handler...")

    using_postgres = True
    # Pull contracts from postgres
    df_contracts = db_connection.get_contracts_by_carrier(matrix_row["carrier_id"], matrix_row['status_message'],
                                                          matrix_row['use_test_npns'],
                                                          batch_size=int(matrix_row['batch_size']))
    #### Zoho pickup disabled
    #### This solves both the resident_state issue easily (Zoho has a # of columns limit per query) and causes the process to run just once, after EOD
    # If postgres is empty, pull from zoho
    # if df_contracts is None:
    #    using_postgres = False
    #    df_contracts = zoho_utils.get_contracts_by_carrier(matrix_row["carrier_id"], matrix_row['status_message'],
    #                                                       matrix_row['use_test_npns'],
    #                                                       batch_size=int(matrix_row['batch_size']))
    # If both CRMs are empty, carrier is finished
    if df_contracts is None:
        return None

    while len(df_contracts) > 0 and not end_of_day_check():
        print(f"==Beginning new batch for {matrix_row['script_name']}")

        # Pit data against rules, add data/notes where necessary, process_flag = 0
        if using_postgres:
            df_contracts['requested_states'] = df_contracts[
                'requested_state']  # For parity between postgres & zoho rules
        df_contracts = contract_rules.check_against_rules(df_contracts, df_rules)
        print(f"==Data going into: ops_acr_contract_queue")
        db.upload_contracts_into_queue(df_contracts)

        rpa_rules = df_rules.loc[df_rules['rule_type'] == 'rpa']

        print("==Fetching newest batch_id:")
        batch_id = df_contracts['batch_id'].astype(int).max()
        print(batch_id)

        # Launch threads that will pull from acr_contract_queue to handle contracts
        # Make sure to handle retries! Insert a new entry when you do?
        while db.contract_to_process_exists(batch_id):
            print("==Performing spreadsheet operations")
            contract = db.fetch_next_contract_for_processing(batch_id)
            print(f"==Contract going into spreadsheet:\n{contract.to_string()}")
            replacement_row = run_ambetter_acr_spreadsheet(driver, matrix_row, contract, rpa_rules, date_info)
            print("Replacement row going into synapse:")
            print(replacement_row.to_string())

            db.update_contract_by_npn(replacement_row)

        finished_contracts = db.get_contracts_by_batch_id(batch_id)
        print(f"\n\n==Contract dataframe going into CRM:\n{finished_contracts.to_string()}")
        if matrix_row['dry_run'].lower() == 'no':
            print("==Script does not have dry run enabled, continuing to CRM bulk write and notes.")
            # Zoho Upload
            zoho_utils.update_contract_batch_on_crm(finished_contracts.drop(columns='pk_id', errors='ignore'))
            zoho_utils.update_notes(finished_contracts.drop(columns='pk_id', errors='ignore'))
            # Postgres Upload ( drop anything without a pk_id )
            finished_contracts = finished_contracts.loc[finished_contracts['pk_id'] != None]
            finished_contracts = finished_contracts.loc[finished_contracts['pk_id'] != 'None']
            db_connection.update_contract_batch_in_master_table(finished_contracts, CONTRACTS_MASTER_TABLE_PK,
                                                                CONTRACTS_MASTER_TABLE_NAME)
            db_connection.update_notes(finished_contracts)
        else:
            print("==Dry run enabled, skipping CRM bulk write and notes.")

        print("==Batch completed.")
        print("==Reassigning df_contracts.")
        using_postgres = True
        df_contracts = db_connection.get_contracts_by_carrier(matrix_row["carrier_id"], matrix_row['status_message'],
                                                              matrix_row['use_test_npns'],
                                                              batch_size=int(matrix_row['batch_size']))
        # If postgres is empty, pull from zoho
        # if df_contracts is None:
        #    using_postgres = False
        #    df_contracts = zoho_utils.get_contracts_by_carrier(matrix_row["carrier_id"], matrix_row['status_message'],
        #                                                       matrix_row['use_test_npns'],
        #                                                       batch_size=int(matrix_row['batch_size']))
        # If both CRMs are empty, carrier is finished
        if df_contracts is None:
            print(f"==Carrier level finished for {matrix_row['script_name']}")
            return None
        if matrix_row['use_test_npns'].lower() == 'yes':
            print(f"==Finished doing test run for {matrix_row['script_name']}, stopping carrier.")
            return None
    print(f"==Carrier level finished for {matrix_row['script_name']}")
    return None


def run_ambetter_acr_spreadsheet(driver, matrix_row, contract, rpa_rules, date_info):
    print("Running Ambetter ACR spreadsheet handler...")

    # Establish headers for spreadsheet that we will line our information up with
    insert_df = pd.DataFrame(columns=['requested_date', 'name', 'npn', 'email',
                                      'phone_number', 'resident_state', 'selling_aca',
                                      'requested_states', 'membership_goals', 'ichra'])

    insert_df.loc[0] = None
    insert_df['requested_date'] = datetime.now().strftime("%Y-%m-%d")
    insert_df['name'] = contract['agent_first_name'] + " " + contract['agent_last_name']
    insert_df['npn'] = contract['npn']
    insert_df['email'] = contract['email_address']
    insert_df['phone_number'] = contract['selected_phone']
    insert_df['resident_state'] = contract['resident_state']
    insert_df['selling_aca'] = 'Y'
    insert_df['requested_states'] = contract['requested_states']
    insert_df['membership_goals'] = '25-50'
    insert_df['ichra'] = 'No'

    print(f"==Data going into spreadsheet.")

    # Download file through Google Drive API
    print('==Downloading spreadsheet...')
    file_id = matrix_row['storage_file_name']
    output_filepath = (matrix_row['file_path'] + '\\' + matrix_row['file_name'] + '.' + matrix_row['file_extension'])
    download_file(file_id, output_filepath)

    # Check for downloaded file
    try:
        if not os.path.exists(output_filepath):
            raise Exception
    except Exception as e:
        print(f"==Spreadsheet could not be located: {e}")
        raise Exception

    print(f"Spreadsheet path: {output_filepath}")
    spreadsheet_attempts = 1
    max_attempts = 4

    while spreadsheet_attempts < max_attempts:
        try:
            print(f"Ambetter spreadsheet attempt #{spreadsheet_attempts}")
            book = xlw.Book(output_filepath)
            sheet = book.sheets[0]
            df = sheet.range("A1").expand().options(pd.DataFrame, header=1, index=False, numbers=int).value

            print("==Checking for duplicates...")
            no_duplicates_present = True
            if (str(insert_df['npn'].item()) in str(df['**Broker NPN'])) \
                    and (str(insert_df['name'].item()) in str(df['**Broker name'])):
                no_duplicates_present = False
            if no_duplicates_present:
                print("==No duplicates found, adding row to spreadsheet...")
                sheet.range(f"A{2 + len(df)}").value = [insert_df['requested_date'].item(), insert_df['name'].item(),
                                                        insert_df['npn'].item(), insert_df['email'].item(),
                                                        insert_df['phone_number'].item(),
                                                        insert_df['resident_state'].item(),
                                                        insert_df['selling_aca'].item(),
                                                        insert_df['requested_states'].item(),
                                                        insert_df['membership_goals'].item(), insert_df['ichra'].item()]
                book.save(output_filepath)
                time.sleep(3)
                matching_rule = rpa_rules.loc[rpa_rules['field'] == 'insert outcome']
                print(f"Matching rule to end result: \n{matching_rule.to_string()}")
                contract['insert outcome'] = 'Success'
                contract = contract_rules.check_against_rules(contract, matching_rule)
                print(contract.to_string())
            else:
                print("==Duplicate found, disabling export flag and sending alert email...")
                db_connection.disable_carrier_automatic_export_flag(matrix_row['carrier_id'])
                email_utils.send_duplicate_alert_email(matrix_row['script_name'], matrix_row['carrier_id'],
                                                       insert_df['npn'].item(), name=insert_df['name'].item(),
                                                       email=insert_df['email'].item())

                matching_rule = rpa_rules.loc[rpa_rules['field'] == 'insert outcome']
                print(f"Matching rule to end result: \n{matching_rule.to_string()}")
                contract['insert outcome'] = 'Failure'
                contract = contract_rules.check_against_rules(contract, matching_rule)
                print(contract.to_string())
            print("==Spreadsheet writing finished")
            time.sleep(4)
            book.app.quit()
            break
        except Exception as e:
            print(f"Exception during Ambetter spreadsheet handling: {e}")
        spreadsheet_attempts += 1
        if spreadsheet_attempts >= max_attempts:
            raise Exception

    # Update file in Google Drive
    print('==Updating spreadsheet...')
    svc = get_drive_service()
    update_file(svc, file_id, output_filepath)
    print("==Finished contract addition")
    try:
        contract.drop(labels='insert outcome', inplace=True)
    except Exception as e:
        print(f'==Minor exception during spreadsheet handling: {e}\n{contract.to_string()}')
    try:
        contract.drop(columns='insert outcome', inplace=True)
    except Exception as e:
        print(f'==Minor exception during spreadsheet handling: {e}\n{contract.to_string()}')
    return contract



def run_goldkidney_acr(driver, matrix_row, df_rules, date_info):
    print("Running Gold Kidney handler...")

    using_postgres = True
    # Pull contracts from postgres
    df_contracts = db.get_contracts_by_carrier(matrix_row["carrier_id"], matrix_row['status_message'],
                                               matrix_row['use_test_npns'],
                                               batch_size=int(matrix_row['batch_size']),
                                               appointment_type_limit='Producer')
    # If postgres is empty, pull from zoho
    if df_contracts is None:
        using_postgres = False
        df_contracts = zoho_utils.get_contracts_by_carrier(matrix_row["carrier_id"], matrix_row['status_message'],
                                                           matrix_row['use_test_npns'],
                                                           batch_size=int(matrix_row['batch_size']),
                                                           appointment_type_limit='Producer')
    # If both CRMs are empty, carrier is finished
    if df_contracts is None:
        return None

    while len(df_contracts) > 0 and not end_of_day_check():
        print(f"==Beginning new batch for {matrix_row['script_name']}")

        # Run firm adjustments (if needed)
        if using_postgres:
            df_contracts.loc[
                df_contracts['agent_type'].str.contains('Firm')] = db_connection.goldkidney_firm_adjustments(
                matrix_row["carrier_id"], matrix_row['status_message'],
                df_contracts.loc[df_contracts['agent_type'].str.contains('Firm')])
        else:
            df_contracts.loc[df_contracts['agent_type'].str.contains('Firm')] = zoho_utils.goldkidney_firm_adjustments(
                matrix_row["carrier_id"], matrix_row['status_message'],
                df_contracts.loc[df_contracts['agent_type'].str.contains('Firm')])

        # Pit data against rules, add data/notes where necessary, process_flag = 0
        df_contracts = contract_rules.check_against_rules(df_contracts, df_rules)
        print(f"==Data going into: ops_acr_contract_queue")
        db.upload_contracts_into_queue(df_contracts)

        rpa_rules = df_rules.loc[df_rules['rule_type'] == 'rpa']

        print("==Fetching newest batch_id:")
        batch_id = df_contracts['batch_id'].astype(int).max()
        print(batch_id)

        # Launch threads that will pull from temp_table to handle contracts
        # Make sure to handle retries! Insert a new entry when you do?
        while db.contract_to_process_exists(batch_id):
            print("==Performing web navigation")
            contract = db.fetch_next_contract_for_processing(batch_id)
            print(f"==Contract going into webnav:\n{contract.to_string()}")
            replacement_row = run_goldkidney_acr_webnav(driver, matrix_row, contract, rpa_rules, date_info)
            print("Replacement row going into synapse:")
            print(replacement_row.to_string())
            db.update_contract_by_npn(replacement_row)

        finished_contracts = db.get_contracts_by_batch_id(batch_id)
        print(f"\n\n==Contract dataframe going into CRM:\n{finished_contracts.to_string()}")
        if matrix_row['dry_run'].lower() == 'no':
            print("==Script does not have dry run enabled, continuing to CRM bulk write and notes.")
            # Zoho Upload
            zoho_utils.update_contract_batch_on_crm(finished_contracts.drop(columns='pk_id', errors='ignore'))
            zoho_utils.update_notes(finished_contracts.drop(columns='pk_id', errors='ignore'))
            # Postgres Upload ( drop anything without a pk_id )
            finished_contracts = finished_contracts.loc[finished_contracts['pk_id'] != None]
            finished_contracts = finished_contracts.loc[finished_contracts['pk_id'] != 'None']
            db_connection.update_contract_batch_in_master_table(finished_contracts, CONTRACTS_MASTER_TABLE_PK,
                                                                CONTRACTS_MASTER_TABLE_NAME)
            db_connection.update_notes(finished_contracts)
        else:
            print("==Dry run enabled, skipping CRM bulk write and notes.")

        print("==Batch completed.")
        print("==Reassigning df_contracts.")
        using_postgres = True
        df_contracts = db_connection.get_contracts_by_carrier(matrix_row["carrier_id"], matrix_row['status_message'],
                                                              matrix_row['use_test_npns'],
                                                              batch_size=int(matrix_row['batch_size']))
        # If postgres is empty, pull from zoho
        if df_contracts is None:
            using_postgres = False
            df_contracts = zoho_utils.get_contracts_by_carrier(matrix_row["carrier_id"], matrix_row['status_message'],
                                                               matrix_row['use_test_npns'],
                                                               batch_size=int(matrix_row['batch_size']))
        # If both CRMs are empty, carrier is finished
        if df_contracts is None:
            print(f"==Carrier level finished for {matrix_row['script_name']}")
            return None
        if matrix_row['use_test_npns'].lower() == 'yes':
            print(f"==Finished doing test run for {matrix_row['script_name']}, stopping carrier.")
            return None
    print(f"==Carrier level finished for {matrix_row['script_name']}")
    return None


def run_goldkidney_acr_webnav(driver, matrix_row, contract, rpa_rules, date_info):
    print("==Running Gold Kidney Navigation ACR handler...")

    driver.get(matrix_row['url'])

    try:
        downline_broker_dropdown = WebDriverWait(driver, 5).until(
            EC.presence_of_element_located(
                (By.XPATH, '//*[@href="/portal/mpc_detail.htm"]'))
        )
    except Exception as e:
        print('System was not already logged in, logging in now...')

        time.sleep(1)

        # Perform login if needed
        if matrix_row["log_in"].upper() == "YES":
            try:
                time.sleep(2)
                username_field = WebDriverWait(driver, 30).until(
                    EC.presence_of_element_located(
                        (By.XPATH, '//*[@id="login_id"]'))
                )
                username_field.send_keys(matrix_row['email'])
                time.sleep(2)

                password_field = WebDriverWait(driver, 30).until(
                    EC.presence_of_element_located(
                        (By.XPATH, '//*[@id="password"]'))
                )
                password_field.send_keys(matrix_row['password'])
                time.sleep(2)

                login_button = WebDriverWait(driver, 30).until(
                    EC.presence_of_element_located(
                        (By.XPATH, '//*[@id="submit"]'))
                )
                login_button.click()
                time.sleep(2)

                try:  # Continue from "Password expiring soon" screen if needed
                    continue_with_login = WebDriverWait(driver, 6).until(
                        EC.presence_of_element_located(
                            (By.XPATH, '//*[@id="goToDomain"]'))
                    )
                    continue_with_login.click()
                except:
                    print("Password change screen not detected. Continuing...")
            except Exception as e:
                log_error(ERROR_CODES["login_error"], "Login page timeout or login fields not found.",
                          matrix_row["script_name"])
                print(f"Login page not found or timeout occurred. Exiting... {e}")
                contract['process_flag'] = 1
                contract['error_message'] = f'Error occurred during login process.'
                raise Exception

    # Navigate through pages
    print('step1')
    original_window = driver.current_window_handle
    print('step2')
    time.sleep(7)

    max_retry = 6
    retries = 0
    time.sleep(3)
    while retries < max_retry:
        try:
            print('Clicking downline broker...')
            downline_broker_dropdown = WebDriverWait(driver, 240).until(
                EC.presence_of_element_located(
                    (By.XPATH, '//*[@href="/portal/mpc_detail.htm"]'))
            )
            downline_broker_dropdown.click()
            time.sleep(4)

            print('Clicking onboarding...')
            onboarding = WebDriverWait(driver, 240).until(
                EC.presence_of_element_located(
                    (By.XPATH, '//*[@href="/portal_onboarding/onboarding_downline.htm"]'))
            )
            onboarding.click()
            time.sleep(4)

            print('Clicking create invite...')
            create_invite = WebDriverWait(driver, 240).until(
                EC.presence_of_element_located(
                    (By.XPATH, '//*[@onclick = "InsertNewCase();"]'))
            )
            create_invite.click()
            break  # No more retries needed
        except Exception as e:
            if retries < max_retry:
                print(f'==Navigation attempt {retries} failed.. retrying')
                retries += 1
                time.sleep(5)
                continue
            print('==Too many retry failures, giving up.')
            log_error(ERROR_CODES["navigation_error"], "Navigation process failed.", matrix_row["script_name"])
            print(f"Navigation process failed, ending process. {e}")
            contract['process_flag'] = 1
            contract['error_message'] = f'Error occurred during navigation process. {e}'
            raise Exception

    time.sleep(1)
    # Enter information
    try:
        rep_type_dropdown = WebDriverWait(driver, 240).until(
            EC.presence_of_element_located(
                (By.XPATH, '//*[@data-id="brk_type"]'))
        )
        rep_type_dropdown.click()
        print('step3')
        if contract['agent_type'].item() == 'Individual':
            WebDriverWait(driver, 30).until(
                EC.element_to_be_clickable(
                    (By.XPATH, '//span[contains(text(),"Field Broker")]'))
            ).click()
        elif contract['agent_type'].item() == 'Firm':
            WebDriverWait(driver, 30).until(
                EC.element_to_be_clickable(
                    (By.XPATH, '//span[contains(text(),"Agency")]'))
            ).click()
        else:
            print(f"==Unhandled agent type detected: {contract['agent_type'].item()}")
            raise Exception
        time.sleep(1)

        print('step4')
        sub_type_dropdown = WebDriverWait(driver, 240).until(
            EC.presence_of_element_located(
                (By.XPATH, '//*[@data-id="MA_brk_sub_type"]'))
        )
        sub_type_dropdown.click()
        subtype_id = 'id="bs-select-5-2"'
        if contract['agent_type'].item() == 'Firm':
            subtype_id = 'id="bs-select-5-1"'
        WebDriverWait(driver, 30).until(
            EC.element_to_be_clickable(
                (By.XPATH, f'//*[@{subtype_id}]/span[contains(text(),"Downline Only")]'))
        ).click()
        time.sleep(1)

        print('step5')
        sub_type_dropdown = WebDriverWait(driver, 240).until(
            EC.presence_of_element_located(
                (By.XPATH, '//*[@data-id="MA_brk_level"]'))
        )
        sub_type_dropdown.click()
        if contract['agent_type'].item() == 'Individual':
            WebDriverWait(driver, 30).until(
                EC.element_to_be_clickable(
                    (By.XPATH, '//*[@id="bs-select-6-1"]/span[contains(text(),"Broker - 01")]'))
            ).click()
        elif contract['agent_type'].item() == 'Firm':
            WebDriverWait(driver, 30).until(
                EC.element_to_be_clickable(
                    (By.XPATH, '//span[contains(text(),"GA - 10")]'))
            ).click()
        time.sleep(1)

        print("==Inputting npn...")
        npn_field = WebDriverWait(driver, 30).until(
            EC.presence_of_element_located(
                (By.XPATH, '//*[@id="create_npn"]'))
        )
        npn_field.send_keys(contract["npn"])
        time.sleep(1)

        print("==Inputting email...")
        email_field = WebDriverWait(driver, 30).until(
            EC.presence_of_element_located(
                (By.XPATH, '//*[@id="create_email"]'))
        )
        email_field.send_keys(contract["email_address"])
        time.sleep(1)

        print("==Email entered...")

        time.sleep(2)
        npn_field = WebDriverWait(driver, 30).until(
            EC.presence_of_element_located(
                (By.XPATH, '//*[@id="create_npn"]'))
        )
        npn_field.click()
        time.sleep(1)
    except Exception as e:
        log_error(ERROR_CODES["input_error"], "Error during inputting information.", matrix_row["script_name"])
        print(f"Error during inputting information, ending process. {e}")
        contract['process_flag'] = 1
        contract['error_message'] = f'Error occurred during data entry on the portal.'
        raise Exception

    print("==Checking for data validation errors...")
    outcome = ''
    email_error_flag = False

    time.sleep(4)
    # Check for data validation errors
    try:
        print("==Checking for data validation errors...")
        outcome = ''
        try:
            npn_error = driver.find_element(By.XPATH, '//*[@id="npn_fail"]')
            print(npn_error.text)
            if outcome == '' and npn_error.text != '':
                outcome = npn_error.text
        except Exception as e:
            print(f'==Error searching for "NPN error". {e}')
        try:
            email_error = driver.find_element(By.XPATH, '//*[@id="email_fail"]')
            print(email_error.text)
            if outcome == '' and email_error.text != '':
                outcome = email_error.text
                email_error_flag = True
        except Exception as e:
            print(f'==Error searching for "email error". {e}')
        try:
            npn_exc_error = driver.find_element(By.XPATH, '//*[@id="npn_exclude"]')
            print(npn_exc_error.text)
            if outcome == '' and npn_exc_error.text != '':
                outcome = npn_exc_error.text
        except Exception as e:
            print(f'==Error searching for "NPN exclusion error". {e}')

    except Exception as e:
        log_error(ERROR_CODES["data_validation_error"], "Error during portal data validation.",
                  matrix_row["script_name"])
        print(f"Error during portal data validation, ending process. {e}")
        contract['process_flag'] = 1
        contract['error_message'] = f'Error occurred during data validation process on the portal.'
        raise Exception

    # If 'Email in use' error is encountered, firms must try to use the responsible agent email instead
    if email_error_flag and contract['agent_type'].item() == 'Firm':
        print("==Firm email was in use, attempting with responsible agent's email...")
        email_field = WebDriverWait(driver, 30).until(
            EC.presence_of_element_located(
                (By.XPATH, '//*[@id="create_email"]'))
        )
        email_field.clear()
        email_field.send_keys(contract["responsible_agent_email"].item())

        print("==Email entered...")

        time.sleep(2)
        npn_field = WebDriverWait(driver, 30).until(
            EC.presence_of_element_located(
                (By.XPATH, '//*[@id="create_npn"]'))
        )
        npn_field.click()

        time.sleep(5)
        try:
            print("==Checking for data validation errors...")
            outcome = ''
            try:
                email_error = driver.find_element(By.XPATH, '//*[@id="email_fail"]')
                print(email_error.text)
                if outcome == '' and email_error.text != '':
                    outcome = email_error.text
            except Exception as e:
                print(f'==Error searching for "email error". {e}')
        except Exception as e:
            log_error(ERROR_CODES["data_validation_error"], "Error during portal data validation.",
                      matrix_row["script_name"])
            print(f"Error during portal data validation, ending process. {e}")
            contract['process_flag'] = 1
            contract['error_message'] = f'Error occurred during data validation process on the portal.'
            raise Exception

    print(f"outcome: {outcome}")
    if outcome != '':
        print("==Data validation error found.")
        matching_rule = rpa_rules.loc[rpa_rules['expected_value'] == outcome]
        print(f"Matching rule to expected text: {matching_rule.to_string()}")
        contract = contract_rules.check_against_rules(contract, matching_rule, field_value_override=outcome)
        print(contract.to_string())
    else:
        print("==No validation errors detected, attempting to submit...")
        # Submit information
        try:
            if matrix_row['dry_run'].lower() == 'no':
                print("==Script is not set to dry run, continuing to submission.")

                create_case = WebDriverWait(driver, 30).until(
                    EC.presence_of_element_located(
                        (By.XPATH, '//*[@id="Insert_button"]'))
                )
                create_case.click()
            else:
                print("==Dry run enabled, skipping creation step.")
        except Exception as e:
            log_error(ERROR_CODES["submit_button_not_found"], "", matrix_row["script_name"])
            print("Submit button not found, ending process.")
            contract['process_flag'] = 1
            contract['error_message'] = f'Error occurred during submission process.'
            raise Exception

        # Check for submission error or submission success message
        try:
            print("==Checking for portal errors...")
            outcome = ''
            try_count = 1
            while (try_count < 6) and (outcome == ''):
                print(f"==Waiting for message... Attempt #{try_count}")
                time.sleep(20)
                # Handles NPN production mismatch, contract request submission, and NIPR-related error messages
                try:
                    # failed_error = driver.find_element(By.XPATH, '//*[@id="custom_error_msg"]')
                    failed_error = WebDriverWait(driver, 20).until(
                        EC.presence_of_element_located(
                            (By.XPATH, '//*[@id="custom_error_msg"]'))
                    )
                    print(failed_error.text)
                    if outcome == '' and failed_error.text != '':
                        outcome = failed_error.text
                except Exception as e:
                    print(f'==Error searching for "failed error". {e}')

                try:
                    # success_message = driver.find_element(By.XPATH, '//*[@id="saved_msg"]')
                    success_message = WebDriverWait(driver, 20).until(
                        EC.presence_of_element_located(
                            (By.XPATH, '//*[@id="saved_msg"]'))
                    )
                    print(success_message.text)
                    if outcome == '' and success_message.text != '':
                        outcome = success_message.text
                except Exception as e:
                    print(f'==Error searching for "Success message". {e}')

                print(f"outcome: {outcome}")
                try_count += 1
            matching_rule = rpa_rules.loc[rpa_rules['expected_value'] == outcome]
            print(f"Matching rule to expected text: \n{matching_rule.to_string()}")
            contract = contract_rules.check_against_rules(contract, matching_rule, field_value_override=outcome)
            print(contract.to_string())

        except Exception as e:
            log_error(ERROR_CODES["data_verification_error"], "Error during portal error checking.",
                      matrix_row["script_name"])
            print("Error during portal error checking, ending process.")
            contract['process_flag'] = 1
            contract['error_message'] = f'Error occurred during submission process.'
            raise Exception

    print("==Webnav finished")
    return contract


def run_uhc_mdc_acr(driver, matrix_row, df_rules, date_info):
    print("Running UHC MDC handler...")

    using_postgres = True
    # Pull contracts from postgres
    df_contracts = db_connection.get_contracts_by_carrier(matrix_row["carrier_id"], matrix_row['status_message'],
                                                          matrix_row['use_test_npns'],
                                                          batch_size=int(matrix_row['batch_size']),
                                                          appointment_type_limit='Producer')
    # If postgres is empty, pull from zoho
    if df_contracts is None:
        using_postgres = False
        df_contracts = zoho_utils.get_contracts_by_carrier(matrix_row["carrier_id"], matrix_row['status_message'],
                                                           matrix_row['use_test_npns'],
                                                           batch_size=int(matrix_row['batch_size']),
                                                           appointment_type_limit='Producer')
    # If both CRMs are empty, carrier is finished
    if df_contracts is None:
        return None

    while len(df_contracts) > 0 and not end_of_day_check():
        print(f"==Beginning new batch for {matrix_row['script_name']}")

        if using_postgres:
            df_contracts.loc[df_contracts['agent_type'].str.contains('Firm')] = db.uhc_aca_firm_adjustments(
                matrix_row["carrier_id"], matrix_row['status_message'],
                df_contracts.loc[df_contracts['agent_type'].str.contains('Firm')])
        else:
            df_contracts.loc[df_contracts['agent_type'].str.contains('Firm')] = zoho_utils.uhc_aca_firm_adjustments(
                matrix_row["carrier_id"], matrix_row['status_message'],
                df_contracts.loc[df_contracts['agent_type'].str.contains('Firm')])

        # Pit data against rules, add data/notes where necessary, process_flag = 0
        df_contracts = contract_rules.check_against_rules(df_contracts, df_rules)
        print(f"==Data going into: ops_acr_contract_queue")
        db.upload_contracts_into_queue(df_contracts)

        rpa_rules = df_rules.loc[df_rules['rule_type'] == 'rpa']

        print("==Fetching newest batch_id:")
        batch_id = df_contracts['batch_id'].astype(int).max()
        print(batch_id)

        # Launch threads that will pull from acr_contract_queue to handle contracts
        # Make sure to handle retries! Insert a new entry when you do?
        while db.contract_to_process_exists(batch_id):
            print("==Performing webnav operations")
            contract = db.fetch_next_contract_for_processing(batch_id)
            print(f"==Contract going into web navigation:\n{contract.to_string()}")
            replacement_row = run_uhc_mdc_acr_webnav(driver, matrix_row, contract, rpa_rules, date_info)
            print("Replacement row going into synapse:")
            print(replacement_row.to_string())
            db.update_contract_by_npn(replacement_row)

        finished_contracts = db.get_contracts_by_batch_id(batch_id)
        print(f"\n\n==Contract dataframe going into CRM:\n{finished_contracts.to_string()}")
        if matrix_row['dry_run'].lower() == 'no':
            print("==Script does not have dry run enabled, continuing to CRM bulk write and notes.")
            # Zoho Upload
            zoho_utils.update_contract_batch_on_crm(finished_contracts.drop(columns='pk_id', errors='ignore'))
            zoho_utils.update_notes(finished_contracts.drop(columns='pk_id', errors='ignore'))
            # Postgres Upload ( drop anything without a pk_id )
            finished_contracts = finished_contracts.loc[finished_contracts['pk_id'] != None]
            finished_contracts = finished_contracts.loc[finished_contracts['pk_id'] != 'None']
            db_connection.update_contract_batch_in_master_table(finished_contracts, CONTRACTS_MASTER_TABLE_PK,
                                                                CONTRACTS_MASTER_TABLE_NAME)
            db_connection.update_notes(finished_contracts)
        else:
            print("==Dry run enabled, skipping CRM bulk write and notes.")

        print("==Batch completed.")
        print("==Reassigning df_contracts.")
        using_postgres = True
        df_contracts = db_connection.get_contracts_by_carrier(matrix_row["carrier_id"], matrix_row['status_message'],
                                                              matrix_row['use_test_npns'],
                                                              batch_size=int(matrix_row['batch_size']),
                                                              appointment_type_limit='Producer')
        # If postgres is empty, pull from zoho
        if df_contracts is None:
            using_postgres = False
            df_contracts = zoho_utils.get_contracts_by_carrier(matrix_row["carrier_id"], matrix_row['status_message'],
                                                               matrix_row['use_test_npns'],
                                                               batch_size=int(matrix_row['batch_size']),
                                                               appointment_type_limit='Producer')
        if df_contracts is None:
            print(f"==Carrier level finished for {matrix_row['script_name']}")
            return None
        if matrix_row['use_test_npns'].lower() == 'yes':
            print(f"==Finished doing test run for {matrix_row['script_name']}, stopping carrier.")
            return None
    print(f"==Carrier level finished for {matrix_row['script_name']}")
    return None


def run_uhc_mdc_acr_webnav(driver, matrix_row, contract, rpa_rules, date_info):
    print("==Running UHC MDC web navigation...")

    driver.get(matrix_row['url'])

    if matrix_row["log_in"].upper() == "YES":
        try:
            username_field = WebDriverWait(driver, 30).until(
                EC.element_to_be_clickable((By.XPATH, "//input[@name='username']"))
            )
            username_field.clear()  # Wipes existing username
            username_field.send_keys(matrix_row['email'])

            WebDriverWait(driver, 30).until(
                EC.element_to_be_clickable((By.XPATH, "//input[@name='password']"))
            ).send_keys(matrix_row['password'])

            WebDriverWait(driver, 30).until(
                EC.element_to_be_clickable((By.XPATH, "//input[@name='submit.save']"))
            ).click()

        except TimeoutException as e:
            log_error(ERROR_CODES["login_error"], "Login page timeout or login fields not found.",
                      matrix_row["script_name"])
            print(f"Login page not found or timeout occurred. Exiting... {e}")
            contract['process_flag'] = 1
            contract['error_message'] = 'Error occurred during login process.'
            raise Exception("Login failed")

    # Enter information
    try:
        # Click "Create New Packet"
        WebDriverWait(driver, 30).until(
            EC.presence_of_element_located((By.XPATH, "//input[@onclick='return createNewPacket();']"))
        ).click()
        print('Clicked "Create New Packet"')
        time.sleep(1)
    except Exception as e:
        log_error(ERROR_CODES["input_error"], "Error during web navigation.", matrix_row["script_name"])
        print(f"Error during new packet creation, ending process. {e}")
        contract['process_flag'] = 1
        contract['error_message'] = f'Error occurred during data entry on the portal.'
        raise Exception

    try:
        packet_type = ""
        if contract['agent_type'].item() == 'Individual':
            packet_type = "ECONINFMO1"
        elif contract['agent_type'].item() == 'Firm':
            packet_type = "ECONCRPFMO"
        else:
            print(f"==Unhandled agent type received: {contract['agent_type']}")
            raise Exception
        # Select dropdown option by value
        select_el = WebDriverWait(driver, 20).until(EC.visibility_of_element_located((By.ID, "r_select")))
        Select(select_el).select_by_value(packet_type)
        print('Selected dropdown option by value')
        time.sleep(1)

        # Click "Continue"
        WebDriverWait(driver, 30).until(
            EC.presence_of_element_located((By.XPATH, "//input[@value='Continue']"))
        ).click()
        print('Clicked on "Continue"')
        time.sleep(1)

        # Click on "Load Preset"
        WebDriverWait(driver, 30).until(
            EC.presence_of_element_located((By.XPATH, "//div[@onclick='toggleTemplateMenu();']"))
        ).click()
        print('Clicked on "Load Preset"')
        time.sleep(1)

        # Select "SMA"
        WebDriverWait(driver, 30).until(
            EC.presence_of_element_located((By.XPATH, "//td[@onclick='loadTemplate(55425);toggleTemplateMenu();']"))
        ).click()
        print('Selected "SMA"')
        time.sleep(1)

        # Click "Continue"
        WebDriverWait(driver, 30).until(
            EC.presence_of_element_located((By.XPATH, "//input[@value='Continue']"))
        ).click()
        print('Clicked on "Continue"')
        time.sleep(1)

        if contract['agent_type'].item() == 'Firm':
            print("==A firm is being handled, adding additional firm information")
            # Input Last Name
            agency_full_name_field = WebDriverWait(driver, 30).until(
                EC.presence_of_element_located((By.XPATH, "//input[@label='Agency Name']"))
            )
            agency_full_name_field.send_keys(contract['agency_full_name'])
            print('Added agency full name')

            # Input Last Name
            fein_field = WebDriverWait(driver, 30).until(
                EC.presence_of_element_located((By.XPATH, "//input[@label='EIN']"))
            )
            fein_field.send_keys(contract['agency_fein'])
            print('Added EIN')

        # Input Last Name
        last_name_field = WebDriverWait(driver, 30).until(
            EC.presence_of_element_located((By.XPATH, "//input[@label='Last Name']"))
        )
        last_name_field.send_keys(contract['agent_last_name'])
        print('Added Last Name')

        # Input First Name
        first_name_field = WebDriverWait(driver, 30).until(
            EC.presence_of_element_located((By.XPATH, "//input[@label='First Name']"))
        )
        first_name_field.send_keys(contract['agent_first_name'])
        print('Added First Name')

        # Input email
        email_field = WebDriverWait(driver, 30).until(
            EC.presence_of_element_located((By.XPATH, "//input[@label='Email Address']"))
        )
        email_field.send_keys(contract['email_address'])
        print('Added email')
        time.sleep(3)

        # Click "Continue"
        WebDriverWait(driver, 30).until(
            EC.presence_of_element_located((By.XPATH, "//input[@value='Continue']"))
        ).click()
        print('Clicked on "Continue"')
        time.sleep(1)

        # Input Writing Number
        writing_number = "6598242"
        writing_number_field = WebDriverWait(driver, 30).until(
            EC.presence_of_element_located((By.XPATH, "//input[@name='writingNumber']"))
        )
        writing_number_field.send_keys(writing_number)

        print(f"Added Writing Number: {writing_number}")
        time.sleep(1)

        # Click "Find Producer"
        WebDriverWait(driver, 30).until(
            EC.presence_of_element_located((By.XPATH, "//input[@value='Find Producer']"))
        ).click()
        print('Clicked on "Find Producer"')
        time.sleep(2)

        # Click "Continue"
        WebDriverWait(driver, 30).until(
            EC.presence_of_element_located((By.XPATH, "//input[@value='Continue']"))
        ).click()
        print('Clicked on "Continue"')
        time.sleep(1)

        # Click on "Select Agreement"
        WebDriverWait(driver, 30).until(
            EC.presence_of_element_located((By.XPATH, "//input[@value='Select Agreement']"))
        ).click()
        print('Clicked on "Select Agreement"')
        time.sleep(5)

        # Click on "AGT-NMA80 / Agent" radio
        hierarchy_template = ''
        if contract['agent_type'].item() == 'Individual':
            hierarchy_template = "AGT-NMA80"
        elif contract['agent_type'].item() == 'Firm':
            hierarchy_template = "GA-NMA80"
        else:
            print(f"==Unhandled agent type received: {contract['agent_type']}")
            raise Exception
        print(hierarchy_template)
        WebDriverWait(driver, 30).until(
            EC.presence_of_element_located((By.XPATH, f"//input[@value='{hierarchy_template}']"))
        ).click()
        print(f'Selected Hierarchy template: {hierarchy_template}')
        time.sleep(1)

        # Click on "Select Template"
        WebDriverWait(driver, 30).until(
            EC.presence_of_element_located((By.XPATH, "//input[@value='Select Template']"))
        ).click()
        print('Clicked on "Select Template"')
        time.sleep(10)

        # Click "Continue"
        WebDriverWait(driver, 30).until(
            EC.presence_of_element_located((By.XPATH, "//input[@value='Continue']"))
        ).click()
        print('Clicked on "Continue"')

        if matrix_row['dry_run'].lower() == 'no':
            print("==Sending contract...")
            # Click "Send"
            WebDriverWait(driver, 30).until(
                EC.presence_of_element_located((By.XPATH, "//input[@value='Send']"))
            ).click()
            contract['process_flag'] = 2
            contract['success_status'] = 'Sent to Agent'
        else:
            print("==Dry run enabled, skipping CRM bulk write and notes.")

    except Exception as e:
        log_error(ERROR_CODES["input_error"], "Error during inputting information.", matrix_row["script_name"])
        print(f"Error during inputting information, ending process. {e}")
        contract['process_flag'] = 1
        contract['error_message'] = f'Error occurred during data entry on the portal.'
        contract['fail_status'] = 'Needs Attention'
        contract['error_note'] = 'RPA: Contract submission failed.'
        raise Exception

    print("==Webnav finished")
    return contract


try:
    log_error  # type: ignore[name-defined]
    ERROR_CODES  # type: ignore[name-defined]
except Exception:
    def log_error(code: str, message: str, script_name: str = "") -> None:
        print(f"[LOG_ERROR] {code}: {message} (script={script_name})")


    ERROR_CODES = {
        "login_error": "LOGIN_ERROR",
        "navigation_error": "NAV_ERROR",
        "input_error": "INPUT_ERROR",
        "unexpected_error": "UNEXPECTED_ERROR",
        "crm_update_error": "CRM_UPDATE_ERROR",
    }


def emergency_upload_incomplete_batch(matrix_row):
    print("=====Beginning emergency incomplete batch CRM uploader.")
    print("==Fetching newest batch_id:")
    batch_id = db.get_latest_batch_id()
    print(batch_id)
    finished_contracts = db.get_contracts_by_batch_id(batch_id)
    finished_contracts = finished_contracts.loc[finished_contracts['process_flag'] != '0']
    print(f"\n\n==Contract dataframe going into CRM:\n{finished_contracts.to_string()}")
    if matrix_row['dry_run'].lower() == 'no':
        zoho_utils.update_contract_batch_on_crm(finished_contracts)
        zoho_utils.update_notes(finished_contracts)
        # db_connection.update_contract_batch_in_master_table(finished_contracts,CONTRACTS_MASTER_TABLE_PK,CONTRACTS_MASTER_TABLE_NAME)
        # db_connection.update_notes(finished_contracts)
    else:
        print("==Dry run enabled, skipping CRM writes.")


"""
def run_template(driver, matrix_row, df_rules, date_info):
    print("Running ________ handler...")

    # Perform login if needed
    if matrix_row["log_in"].upper() == "YES":
        try:
            print("Implement Login Functionality")
        except Exception as e:
            log_error(ERROR_CODES["login_error"], "Login page timeout or login fields not found.", matrix_row["script_name"])
            print("Login page not found or timeout occurred. Exiting...")
            driver.quit()
            return None

    # Navigate through pages
    try:
        print("Implement navigation functionality (if needed)")
    except Exception as e:
        log_error(ERROR_CODES["navigation_error"], "Navigation process failed.", matrix_row["script_name"])
        print("Navigation process failed, ending process.")
        driver.quit()
        return None

    # Enter information
    try:
        print("Implement info fill functionality")
    except Exception as e:
        log_error(ERROR_CODES["input_error"], "Error during inputting information.", matrix_row["script_name"])
        print("Error during inputting information, ending process.")
        driver.quit()
        return None

    # Submit information
    try:
        print("Implement info submit functionality")
    except Exception as e:
        log_error(ERROR_CODES["submit_button_not_found"], "", matrix_row["script_name"])
        print("Submit button not found, ending process.")
        driver.quit()
        return None

    return None
"""

handler_map = {
    "ACR_BCBSMI_RPA": run_bcbsmi_acr,
    "ACR_BCBSTX_RPA": run_bcbstx_acr,
    "ACR_Molina_RPA": run_molina_acr,
    "ACR_UHC_ACA_RPA": run_uhc_aca_acr,
    "ACR_UHC_ACA_RPA_eod": run_uhc_aca_acr_eod,
    "ACR_Ambetter_RPA": run_ambetter_acr,
    "ACR_GoldKidney_RPA": run_goldkidney_acr,
    "ACR_UHC_MDC_RPA": run_uhc_mdc_acr,
    "__default__": lambda *args, **kwargs: print("No valid handler matched.")
}
