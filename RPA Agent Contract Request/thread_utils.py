import threading

from selenium.webdriver import ActionChains
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.common.by import By
from selenium.common.exceptions import TimeoutException
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import Select
import time
import random
from datetime import datetime, timedelta, timezone
from calendar import month_name
import os
import pytz
from logger import log_error, ERROR_CODES
import zoho_utils
import contract_rules
import db_connection as db


def run_batch_through_portal(driver, matrix_row, batch_id, threaded_function):
    print(f"==Starting threads for portal upload for batch_id '{batch_id}'...")
    contract_batch = db.get_contracts_by_batch_id(batch_id, limit=3)
    print(f"==Contracts being sent to portal upload threads:\n{contract_batch.to_string()}")

    threads = []
    for i, contract in contract_batch.iterrows():
        t = threading.Thread(target=threaded_function, args=('[row information]', 'test string'))
        threads.append(t)

    # Start each thread
    for t in threads:
        t.start()

    # Wait for all threads to finish
    for t in threads:
        t.join()





