"""
Test Single Carrier (Selenium)
===============================
Currently configured for: Medica BOB (Individual Health → My Policies → Export)

Usage:
    python test_single_carrier.py --username "USERNAME" --password "PASSWORD"
"""

import argparse
import os
import time

from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.common.by import By
from selenium.webdriver.common.action_chains import ActionChains
from selenium.common.exceptions import TimeoutException

from chrome_utils import get_chrome_driver


def handle_bob_medica(driver, username, password, download_folder):

    try:
        # STEP 1: Login
        print("[1/6] Logging in ...")
        driver.get("https://www.ociservices.com/login/")
        time.sleep(5)

        username_field = WebDriverWait(driver, 60).until(
            EC.presence_of_element_located((By.XPATH, "//input[@name='txtUsername']"))
        )
        username_field.send_keys(username)
        time.sleep(2)

        password_field = WebDriverWait(driver, 60).until(
            EC.presence_of_element_located((By.XPATH, "//input[@name='txtPassword']"))
        )
        password_field.send_keys(password)
        time.sleep(2)

        login_btn = WebDriverWait(driver, 60).until(
            EC.element_to_be_clickable((By.CSS_SELECTOR, "input[name='btnLogin']"))
        )
        login_btn.click()

        print("  Login submitted.")
        time.sleep(10)

        # STEP 2: Navigate to My Policies
        print("[2/6] Navigating to My Policies ...")

        driver.get("https://agb.ociservices.com/Individual/Policy")
        time.sleep(15)

        # STEP 4: Click Export to Excel
        print("[3/5] Clicking Export to Excel ...")

        export_btn = WebDriverWait(driver, 30).until(
            EC.element_to_be_clickable((By.XPATH,
                "//a[contains(@method, 'ExportToExcel')]//img[@src='https://agb.ociservices.com/images/gridexcel.png']"
            ))
        )
        export_btn.click()
        time.sleep(5)

        # STEP 5: Handle Export dialog — click "All Records"
        print("[4/5] Clicking All Records ...")

        all_records_btn = WebDriverWait(driver, 15).until(
            EC.element_to_be_clickable((By.XPATH,
                "//div[contains(@class, 'ui-dialog')]//button[contains(text(), 'All Records')]"
            ))
        )
        all_records_btn.click()

        print("  Export triggered, waiting for download ...")
        time.sleep(30)

        # STEP 6: Check downloads
        print("[5/5] Checking downloads ...")

        if os.path.exists(download_folder):
            files = os.listdir(download_folder)
            for f in files:
                fpath = os.path.join(download_folder, f)
                if os.path.isfile(fpath):
                    print(f"    {f} ({os.path.getsize(fpath):,} bytes)")

        return download_folder

    except Exception as e:
        print(f"\n  HANDLER FAILED: {e}")
        return None


def run(username, password, download_dir=None):

    download_dir = download_dir or os.path.join(os.getcwd(), "downloads")
    os.makedirs(download_dir, exist_ok=True)

    driver = get_chrome_driver(download_folder=download_dir)

    try:
        result = handle_bob_medica(driver, username, password, download_dir)

        if result:
            print("\n" + "=" * 50)
            print("  DONE")
            print("=" * 50)
        else:
            print("\n" + "=" * 50)
            print("  FAILED")
            print("=" * 50)

        input("\n  Press Enter to close browser ...")

    except Exception as e:
        print(f"\n  FAILED: {e}")
        input("\n  Press Enter to close browser ...")
    finally:
        driver.quit()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Test single carrier RPA flow")
    parser.add_argument("--username", required=True)
    parser.add_argument("--password", required=True)
    parser.add_argument("--download-dir", default=None)
    args = parser.parse_args()

    run(
        username=args.username,
        password=args.password,
        download_dir=args.download_dir,
    )