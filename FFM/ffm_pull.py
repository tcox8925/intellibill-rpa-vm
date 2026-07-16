import glob
import os
import time
from datetime import datetime
import datetime as dt

import pytz
from selenium.webdriver.support.wait import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.common.by import By
from azure_blob_utils import authenticate_blob_storage, upload_file_to_blob
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from datetime import datetime as dt
import pandas as pd

from chrome_utils import get_chrome_driver
from logger import setup_logger, log_error, log_success, log_final_entry, ERROR_CODES, init_log_entry,update_log_extra_fields

LOCAL_DOWNLOAD_FOLDER = "C:\\Users\\myopsadmin\\Downloads\\FFM"
URL = 'https://data.healthcare.gov/ab-registration-completion-list'

# Set up logger
script_name = "RPA_FFM_Pull"

# Configuration
print("==Setting chrome options")
chrome_options = webdriver.ChromeOptions()
chrome_options.add_argument("user-data-dir=C:/Users/actua/AppData/Local/Google/Chrome/User Data Testing")
chrome_options.add_argument("profile-directory=DefaultTesting")

# Selenium Configuration
print("==Loading selenium configuration")
driver_path = "C:\\Users\\actua\\Desktop\\work\\Tools\\chromedriver-win64\\chromedriver.exe"
service = Service(driver_path)

try:
    print(f"==Navigating to URL: {URL}")
    driver = get_chrome_driver(profile_path=None, download_folder=LOCAL_DOWNLOAD_FOLDER)
    driver.get(URL)
    time.sleep(2)
except Exception as e:
    print(f'==Error navigating to URL: {e}')
    raise Exception

try:
    WebDriverWait(driver, 10).until(
        EC.element_to_be_clickable((By.XPATH, '//*[contains(text(),"Export to .CSV")]'))
    ).click()
    time.sleep(20)
except Exception as e:
    print(f'==Error downloading CSV file: {e}')
    raise Exception

try:
    local_csv_path = LOCAL_DOWNLOAD_FOLDER + '\\data.csv'
    local_parquet_path = LOCAL_DOWNLOAD_FOLDER + '\\data.parquet'
    print('==Converting csv to parquet...')
    df = pd.read_csv(local_csv_path)

    column_mapping = {
        'NPN': 'npn',
        'Applicable Plan Year': 'applicable_plan_year',
        'Individual Registration Completion Date': 'individual_registration_completion_date',
        'Individual Marketplace End Date': 'individual_marketplace_end_date',
        'Shop Registration Completion Date': 'shop_registration_completion_date',
        'Shop End Date': 'shop_end_date',
        'NPN Valid (Current Plan Year Only)': 'npn_valid_current_plan_year_only'
    }

    df.rename(columns=column_mapping, inplace=True)

    date_columns = [
        'individual_registration_completion_date',
        'individual_marketplace_end_date',
        'shop_registration_completion_date',
        'shop_end_date'
    ]
    for column_name in date_columns:
        df[column_name] = pd.to_datetime(df[column_name], format='%m/%d/%Y').dt.strftime('%Y-%m-%d')

    df['npn'] = df['npn'].astype(str).str.replace('.0', '', regex=False)
    df['load_date'] = dt.now(pytz.timezone('US/Central')).strftime('%Y-%m-%d %H:%M:%S')

    df.to_parquet(local_parquet_path, engine='pyarrow', index=False)
except Exception as e:
    print(f'==Error converting csv to parquet: {e}')
    raise Exception

try:
    try:
        local_file_path = glob.glob(local_parquet_path)[0]
        if len(local_file_path) == 0:
            raise Exception
    except Exception as e:
        print("Error occurred during UHC Export File Search. Perhaps no file was found?")
        raise Exception

    today = dt.now(pytz.timezone('US/Central')).strftime('%Y-%m-%d')
    fixed_path = '/raw/ffm_agents'
    date_path = dt.strptime(today, '%Y-%m-%d').strftime('%Y %m %b')
    upload_file_name = f"raw_ffm_agents_{dt.strptime(today, '%Y-%m-%d').strftime('%m%d%Y')}.parquet"
    blob_path = f'{fixed_path}/{date_path}/{upload_file_name}'

    blob_service = authenticate_blob_storage()
    print(local_file_path)
    print(blob_path)
    upload_file_to_blob(blob_service,local_file_path,blob_path)
    if os.path.exists(local_parquet_path):
        os.remove(local_parquet_path)
    if os.path.exists(local_csv_path):
        os.remove(local_csv_path)
except Exception as e:
    print(f'==Error uploading file to blob: {e}')

