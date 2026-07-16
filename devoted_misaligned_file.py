import os
import time
import base64
from datetime import datetime, timedelta
from typing import List, Dict, Optional, Union
from getpass import getpass

from azure.communication.email import EmailClient

from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.chrome.options import Options
from webdriver_manager.chrome import ChromeDriverManager


# ==========================================================
#  CONFIGURATION
# ==========================================================

DEVOTED_USERNAME = os.getenv("DEVOTED_USERNAME", "")
DEVOTED_PASSWORD = os.getenv("DEVOTED_PASSWORD", "")

EMAIL_TO = ["compliance@agilityholdingsgroup.com","smeeks@enrollinsurance.com"]
#EMAIL_TO = ["jpoorna@834labs.com","dataops@834labs.com"]
EMAIL_CC = ["dataops@834labs.com"]

CONNECTION_STRING = (
    "endpoint=https://myopsemailservice.unitedstates.communication.azure.com/;"
    f"accesskey={os.getenv('ACS_ACCESS_KEY', '')}"
)

SENDER_ADDRESS = "dataops@834labs.com"

# *** REQUIRED WINDOWS PATH ***
DOWNLOAD_DIR = r"C:\Users\myopsadmin\Downloads"
#DOWNLOAD_DIR = r"C:\Users\poorn\Microsoft\Downloads\acc"

# ==========================================================
#  EMAIL UTIL
# ==========================================================

def _split_recipients(to: Union[str, List[str]]) -> List[Dict[str, str]]:
    if isinstance(to, list):
        parts = to
    else:
        parts = [p.strip() for p in to.replace(";", ",").split(",") if p.strip()]
    return [{"address": addr} for addr in parts]


def send_email_with_attachments(
    to: Union[str, List[str]],
    subject: str,
    body: str,
    attachment_paths: List[str],
    mimetype: str = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    cc: Optional[Union[str, List[str]]] = None,
) -> Optional[str]:

    try:
        client = EmailClient.from_connection_string(CONNECTION_STRING)

        recipients = {"to": _split_recipients(to)}
        if cc:
            recipients["cc"] = _split_recipients(cc)

        attachments = []
        for path in attachment_paths:
            with open(path, "rb") as f:
                encoded = base64.b64encode(f.read()).decode()
            attachments.append({
                "name": os.path.basename(path),
                "contentType": mimetype,
                "contentInBase64": encoded,
            })

        message = {
            "senderAddress": SENDER_ADDRESS,
            "recipients": recipients,
            "content": {
                "subject": subject,
                "plainText": body,
                "html": f"<html><body><pre>{body}</pre></body></html>"
            },
            "attachments": attachments,
        }

        poller = client.begin_send(message)
        result = poller.result()
        print("Email sent.")
        return result

    except Exception as ex:
        print(f"Email failed: {ex}")
        return None


# ==========================================================
#  SELENIUM HELPERS
# ==========================================================

DEVOTED_URL = "https://agent.devoted.com/"


def build_driver(download_dir: str) -> webdriver.Chrome:
    chrome_options = Options()
    chrome_options.add_experimental_option("prefs", {
        "download.default_directory": download_dir,
        "download.prompt_for_download": False,
        "safebrowsing.enabled": True
    })
    service = Service(ChromeDriverManager().install())
    driver = webdriver.Chrome(service=service, options=chrome_options)
    driver.maximize_window()
    return driver


def wait_for_single_file(download_dir: str, prefix: str, timeout: int = 120) -> str:
    """
    Wait until exactly one file with the prefix appears and download completes.
    """
    print(f"Waiting for file starting with: {prefix}")
    end = time.time() + timeout

    while time.time() < end:
        files = os.listdir(download_dir)

        # Skip while Chrome is still downloading
        if any(f.endswith(".crdownload") for f in files):
            time.sleep(1)
            continue

        for f in files:
            if f.startswith(prefix):
                full_path = os.path.join(download_dir, f)
                print(f"Downloaded: {full_path}")
                return full_path

        time.sleep(1)

    raise TimeoutError(f"Download timeout for prefix: {prefix}")


def login(driver, username, password):
    wait = WebDriverWait(driver, 60)
    driver.get(DEVOTED_URL)

    wait.until(EC.element_to_be_clickable(
        (By.CSS_SELECTOR, "button[data-testid='login-auth0-button']")
    )).click()
    print("Clicked Login")

    # Username
    user_input = wait.until(
        EC.element_to_be_clickable((By.CSS_SELECTOR, "input[name='username']"))
    )
    time.sleep(0.5)
    user_input.send_keys(username)
    print("Entered username")

    # Password
    pwd_input = wait.until(
        EC.element_to_be_clickable((By.CSS_SELECTOR, "input[name='password']"))
    )
    time.sleep(0.5)
    pwd_input.send_keys(password)
    print("Entered password")

    # Submit
    submit_btn = wait.until(
        EC.element_to_be_clickable((By.NAME, "submit"))
    )
    submit_btn.click()
    print("Submitted login")

    time.sleep(4)


def goto_reports(driver):
    wait = WebDriverWait(driver, 60)
    reports_btn = wait.until(
        EC.element_to_be_clickable((
            By.XPATH,
            "//li[.//div[contains(text(),'Reports')]]"
        ))
    )
    reports_btn.click()
    print("Opened Reports page")

    time.sleep(2)


def download_report(driver, row_title):
    wait = WebDriverWait(driver, 60)
    row = wait.until(
        EC.presence_of_element_located((
            By.XPATH,
            f"//tr[td[1][normalize-space(text())='{row_title}']]"
        ))
    )

    button = row.find_element(By.XPATH, ".//button[.//span[text()='Download Report']]")
    wait.until(EC.element_to_be_clickable(button)).click()
    print(f"Download triggered: {row_title}")


# ==========================================================
#  MAIN WORKFLOW
# ==========================================================

def cleanup_downloads(file_paths: List[str]):
    print("\nCleaning up downloaded reports…")
    for path in file_paths:
        try:
            if os.path.exists(path):
                os.remove(path)
                print(f"Deleted: {os.path.basename(path)}")
            else:
                print(f"File already missing: {os.path.basename(path)}")
        except Exception as ex:
            print(f"Failed to delete {os.path.basename(path)} — {ex}")


def main():
    driver = build_driver(DOWNLOAD_DIR)

    date_tag = datetime.now().strftime("%Y_%m_%d")

    agency_prefix      = f"misaligned_plans_by_agency_report_{date_tag}"
    agent_prefix       = f"misaligned_plans_by_agent_report_{date_tag}"
    app_prefix         = f"misaligned_plans_application_report_{date_tag}"
    complaints_prefix  = f"agent_complaints_report_{date_tag}"  # ✅ NEW

    try:
        login(driver, DEVOTED_USERNAME, DEVOTED_PASSWORD)
        goto_reports(driver)

        # ------------------------------
        # DOWNLOAD ONE-BY-ONE (IMPORTANT)
        # ------------------------------

        download_report(driver, "Misaligned Plans by Agency Report")
        agency_file = wait_for_single_file(DOWNLOAD_DIR, agency_prefix)

        download_report(driver, "Misaligned Plans by Agent Report")
        agent_file = wait_for_single_file(DOWNLOAD_DIR, agent_prefix)

        download_report(driver, "Misaligned Plans Application Report")
        app_file = wait_for_single_file(DOWNLOAD_DIR, app_prefix)

        # ✅ NEW: Agent Complaints Report (row-title anchored like the others)
        download_report(driver, "Agent Complaints Report")
        complaints_file = wait_for_single_file(DOWNLOAD_DIR, complaints_prefix)

        attachments = [agency_file, agent_file, app_file, complaints_file]  # ✅ NEW

        # ------------------------------
        # EMAIL
        # ------------------------------
        run_ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        today_str = datetime.now().strftime("%Y-%m-%d")
        subject = f"Devoted Misaligned Plans Compliance Reports – {today_str}"
        #subject = f"Devoted Misaligned Plans Compliance Reports – {week_ending}"

        body = (
            f"Run Timestamp: {run_ts}\n"
            f"Source System: Devoted Agent Portal\n\n"
            "Attached Reports:\n"
            " • Misaligned Plans by Agency Report\n"
            " • Misaligned Plans by Agent Report\n"
            " • Misaligned Plans Application Report\n"
            " • Agent Complaints Report\n"  # ✅ NEW
        )

        send_email_with_attachments(
            to=EMAIL_TO,
            cc=EMAIL_CC,
            subject=subject,
            body=body,
            attachment_paths=attachments,
        )
        print("Email sent")
        cleanup_downloads(attachments)
        print("Local Files Cleaned!")

        print("Process complete.")

    finally:
        driver.quit()
        print("Browser closed.")


if __name__ == "__main__":
    main()