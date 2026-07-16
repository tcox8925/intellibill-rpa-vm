from selenium import webdriver
from selenium.webdriver.chrome.service import Service as ChromeService
from selenium.webdriver.chrome.options import Options
from webdriver_manager.chrome import ChromeDriverManager
import os

def get_chrome_driver(profile_path=None, download_folder=None):
    chrome_options = Options()
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")
    chrome_options.add_argument("--start-maximized")

    if profile_path:
        chrome_options.add_argument(f"user-data-dir={profile_path}")

    prefs = {
        "download.prompt_for_download": False,
        "download.directory_upgrade": True,
        "safebrowsing.enabled": True
    }

    if download_folder:
        prefs["download.default_directory"] = os.path.abspath(download_folder)

    chrome_options.add_experimental_option("prefs", prefs)

    #FIXED: Ensure correct argument structure
    service = ChromeService(ChromeDriverManager().install())
    driver = webdriver.Chrome(service=service, options=chrome_options)
    return driver
