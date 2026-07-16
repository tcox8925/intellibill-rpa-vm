import os
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
import time

# Initialize the Chrome driver
driver_path = "C:/Users/myopsadmin/Desktop/chromedriver.exe"  # Replace with your ChromeDriver path
service = Service(driver_path)
options = webdriver.ChromeOptions()
driver = webdriver.Chrome(service=service, options=options)
wait = WebDriverWait(driver, 20)

try:
    # Step 1: Navigate to the URL
    url = "https://us9.five9.com/reporting/runReport.jsp?Id=_300000000001306&type=CustomReports"
    driver.get(url)
    driver.maximize_window()
    print("Opened URL")
    time.sleep(5)

    # Step 2: Log in
    username_field = wait.until(EC.presence_of_element_located((By.ID, "input_username")))
    password_field = driver.find_element(By.ID, "input_password")


    username_field.send_keys(os.getenv("LCL_LOGIN_EMAIL", ""))
    password_field.send_keys(os.getenv("LCL_LOGIN_PASSWORD", ""))
    print("Entered credentials")

    login_button = driver.find_element(By.ID, "input_login_submit")
    login_button.click()
    print("Clicked Login")
    time.sleep(60)

    # Step 3: Select dropdown values
    dropdown1 = wait.until(EC.presence_of_element_located((By.ID, "rdw_tf_interval")))
    dropdown1.click()
    time.sleep(10)
    dropdown1.send_keys("Last hour")  # Adjust as needed
    dropdown1.send_keys(Keys.RETURN)
    print("Selected Last hour in dropdown")
    time.sleep(60)


    # Step 4: Execute the report
    execute_button = wait.until(EC.element_to_be_clickable((By.XPATH, "//*[@id='rw_run_btn']")))
    execute_button.click()
    print("Executed the report")
    time.sleep(60)  # Wait for the report to generate

    # Step 5: Generate and export the report
    generate_button = wait.until(EC.element_to_be_clickable((By.XPATH, "//*[@id='rw_export_btn']")))
    generate_button.click()
    print("Generated and exported the report")
    time.sleep(60)

    # Step 6: Select CSV format
    csv_option = driver.find_element(By.XPATH, "//*[@id='rr_output_format_CSV']")
    csv_option.click()
    print("Selected CSV format")

    # Step 7: Accept and download the report
    accept_button = driver.find_element(By.XPATH, "//*[@id='rr_output_format_apply']")
    accept_button.click()
    print("Accepted download prompt")

    time.sleep(60)

    download_button = wait.until(EC.element_to_be_clickable((By.XPATH, "//*[@id='pd_btn_download']")))
    download_button.click()
    print("Downloaded the report")
    time.sleep(60)

    # Step 8: Log out
    logout_button = driver.find_element(By.XPATH, "//*[@id='page_logout']")
    logout_button.click()
    print("Logged out")

except Exception as e:
    print(f"An error occurred: {e}")

finally:
    # Ensure the driver quits even if an exception occurs
    driver.quit()
    print("Browser closed")
