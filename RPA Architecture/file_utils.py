import os
import shutil
import zipfile
from bs4 import BeautifulSoup
import csv
import time
import glob
import pdfplumber


def move_file(source_path, destination_path):
    """Moves a file to a new location."""
    try:
        shutil.move(source_path, destination_path)
        print(f"== File moved to {destination_path}")
    except Exception as e:
        print(f"== Error moving file: {e}")


def delete_file(file_path):
    """Deletes a file if it exists."""
    try:
        if os.path.exists(file_path):
            os.remove(file_path)
            print(f"== File deleted: {file_path}")
        else:
            print(f"== File not found: {file_path}")
    except Exception as e:
        print(f"== Error deleting file: {e}")


def unzip_file(zip_path, extract_to):
    """Extracts a ZIP file to the specified directory."""
    try:
        with zipfile.ZipFile(zip_path, 'r') as zip_ref:
            zip_ref.extractall(extract_to)
        print(f"== Extracted ZIP file to {extract_to}")
    except Exception as e:
        print(f"== Error extracting ZIP file: {e}")


def build_gdrive_path(base_path: str, process_name: str, date_info: dict) -> str:
    """
    Constructs the G-Drive folder path based on the process type and current date info.

    - For 'BOB' and 'ACU': Returns "MM Month YYYY" (e.g., "01 January 2025") as subfolder.
    - For all others: Uses the same format.
    - Note: 'COM' should be handled outside this function (i.e., not passed in).
    """
    folder = f"{date_info['current_month_number']} {date_info['current_month_year']}"
    return os.path.join(base_path, folder)


def build_blob_path(row: dict, date_info: dict, file_name: str) -> str:
    """
    Constructs the Blob folder path based on the process type and current date info.
    Format:
    - ACU: MM Mon YYYY (e.g., "01 Jan 2025")
    - COM/BOB: YYYY MM Mon (e.g., "2025 01 Jan")
    """
    base_path = row.get("blob_base_path", "").strip()
    process_name = str(row.get("process_name", "")).strip().upper()
    com_date = f"{date_info['current_month_short'].lower()}_{date_info['current_year']}"

    if process_name == "ACU":
        folder = f"{date_info['current_month_number']} {date_info['current_month_short']} {date_info['current_year']}"
    elif process_name in ["BOB"]:
        folder = f"{date_info['current_year']} {date_info['current_month_number']} {date_info['current_month_short']}"
    else:
        folder = com_date

    return os.path.join(base_path, folder, file_name)


from datetime import datetime, timedelta
import os


def build_renamed_file_path(download_folder, rename_base, process_name, cadence, schedule, date_info, file_extension,
                            suffix=""):
    """
    Builds the renamed file path dynamically based on process type and cadence.
    """
    process_name = str(process_name).upper()
    cadence = str(cadence).lower()
    schedule = str(schedule).lower()
    fixed_date = date_info["first_of_month"]
    filename = ""

    if process_name == "COM":
        if cadence == "monthly":
            report_date = (
                f"{date_info['first_of_prev_month'][:2]}01{date_info['first_of_prev_month'][-4:]}"
                if schedule == "prev_month"
                else f"{date_info['first_of_month'][:2]}01{date_info['first_of_month'][-4:]}"
            )
        else:
            # For non-monthly cadence, fallback to MMDDYYYY (today)
            report_date = datetime.today().strftime("%m%d%Y")
        filename = f"{rename_base}{fixed_date}_{report_date}.{file_extension}"
    elif process_name in ["BOB", "ACU"]:
        if cadence == "daily":
            report_date = datetime.today().strftime("%m%d%Y")
        else:  # Weekly
            monday = datetime.today() - timedelta(days=datetime.today().weekday())
            report_date = monday.strftime("%m%d%Y")
        filename = f"{rename_base}{report_date}.{file_extension}"
    else:
        report_date = datetime.today().strftime("%m%Y")
        filename = f"{rename_base}{report_date}.{file_extension}"

    # filename = f"{rename_base}_{fixed_date}_{report_date}.{file_extension}"

    return os.path.join(download_folder, filename)



def extract_agent_info_table_to_csv(html_content, matrix_row):
    """
    Extracts name, writing number, and email from the Contracts Approved table and writes to CSV.
    """
    import os
    from bs4 import BeautifulSoup
    import csv

    output_csv_path = os.path.join(
        os.path.normpath(matrix_row["download_path"]),
        f"{matrix_row['extracted_file_prefix']}.csv"
    )
    soup = BeautifulSoup(html_content, "html.parser")
    table = soup.find("table", id="MainContent_gvApproved")
    if not table:
        print("No Contracts Approved table found!")
        return

    data = []
    rows = table.find_all("tr")
    for row in rows:
        # Skip header rows
        if row.find("th"):
            continue
        cols = row.find_all("td")
        if len(cols) < 2:
            continue
        info_td = cols[1]
        # Name
        name_span = info_td.find("span", id=lambda x: x and "Label2" in x)
        name = name_span.text.strip() if name_span else ""
        # Writing number (agent id)
        agent_id_span = info_td.find("span", id=lambda x: x and "lblAgentId" in x)
        writing_number = agent_id_span.text.strip() if agent_id_span else ""
        # Email
        email = ""
        for line in info_td.stripped_strings:
            if "@" in line:
                if "Email:" in line:
                    email = line.split("Email:")[-1].strip()
                else:
                    email = line.strip()
                break
        if name or writing_number or email:
            data.append([name, writing_number, email])

    with open(output_csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["name", "writing_number", "email"])
        writer.writerows(data)
    print(f"== Data extracted to {output_csv_path}")



def extract_bcbs_az_agents_to_csv(driver, matrix_row):
    """
    Scrapes Office User Management table and each agent's details page.
    Stores CSV in download_folder with name from extracted_file_prefix.
    Columns: full name, role, broker npn, email

    Handles the case where after returning from an agent's page, the table resets to default view
    even though the dropdown shows 1000. After each agent, refresh the page, re-select 1000, and
    continue from the next agent in the list, preserving already gathered data.
    """
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support.ui import WebDriverWait
    from selenium.webdriver.support import expected_conditions as EC
    from selenium.common.exceptions import TimeoutException

    def set_page_size():
        """Set the page size to 1000 (or max) after each refresh."""
        try:
            page_size_dropdown = WebDriverWait(driver, 10).until(
                EC.presence_of_element_located((By.ID, "PageSizeList"))
            )
            page_size_dropdown.click()
            option = WebDriverWait(driver, 5).until(
                EC.element_to_be_clickable((By.XPATH, "//select[@id='PageSizeList']/option[@value='1000']"))
            )
            option.click()
            print("Set page size to 1000.")
            time.sleep(5)
        except Exception:
            print("Could not set page size to 1000. Continuing with default.")

    # Go to the main agent list page and set page size to 1000
    set_page_size()
    time.sleep(5)

    agent_data = []
    agent_idx = 0  # Index of the agent to process

    while True:

        try:
            # Explicitly wait for the table to be present in the DOM
            WebDriverWait(driver, 20).until(
                EC.presence_of_element_located((By.ID, "BrokerOfficeUserGrid"))
            )
        except TimeoutException:
            print("Agent table did not load in time. Ending extraction.")
            break # Exit the while loop if the table never appears

        # Parse the current table and get all agent rows and links
        soup = BeautifulSoup(driver.page_source, "html.parser")
        table = soup.find("table", id="BrokerOfficeUserGrid")
        if not table:
            print("Could not find the agent table in the page source. Ending extraction.")
            break
        tbody = table.find("tbody")
        if not tbody:
            print("Could not find the table body. Ending extraction.")
            break
        
        rows = tbody.find_all("tr")
        agent_links = driver.find_elements(By.XPATH, "//table[@id='BrokerOfficeUserGrid']//tbody//tr//td[1]//a")

        # If no agents found or all processed, break
        if not agent_links or agent_idx >= len(agent_links):
            print("No more agent links found, ending extraction.")
            break

        # Get the row and link for the current agent
        row = rows[agent_idx]
        link = agent_links[agent_idx]

        cols = row.find_all("td")
        if len(cols) < 2:
            agent_idx += 1
            continue
        name_link = cols[0].find("a")
        if not name_link:
            agent_idx += 1
            continue
        name_text = name_link.text.strip()
        # Convert "LAST, FIRST" to "First Last"
        if "," in name_text:
            last, first = [x.strip() for x in name_text.split(",", 1)]
            full_name = f"{first} {last}"
        else:
            full_name = name_text
        role = cols[1].text.strip()

        try:
            # Click the agent link by index (always fresh after refresh)
            link.click()
            time.sleep(5)  # Wait for agent detail page to load

            # Scrape agent details
            profile_soup = BeautifulSoup(driver.page_source, "html.parser")
            # Try to get Broker NPN from the hidden input first
            broker_npn = ""
            broker_npn_input = profile_soup.find("input", {"id": "BrokerNPN"})
            if broker_npn_input and broker_npn_input.has_attr("value"):
                broker_npn = broker_npn_input["value"].strip()
            else:
                # Fallback: try to get it from the text after the label
                broker_npn_label = profile_soup.find("label", {"for": "BrokerNPN"})
                if broker_npn_label:
                    broker_npn_div = broker_npn_label.find_parent("div")
                    if broker_npn_div:
                        # Get all text nodes in the div, find the one after the label
                        texts = list(broker_npn_div.stripped_strings)
                        for i, t in enumerate(texts):
                            if "Broker NPN:" in t and i + 1 < len(texts):
                                broker_npn = texts[i + 1].strip()
                                break            
            email = ""
            email_span = profile_soup.find("span", class_="oumEmailAddr")
            if email_span:
                email = email_span.text.strip()

            # Go back to the agent list
            driver.back()
            time.sleep(2)

            # Refresh the page to reset the table state
            driver.refresh()
            time.sleep(3)

            # Set page size to 1000 again after refresh
            set_page_size()
            time.sleep(2)

        except Exception as e:
            print(f"Error processing agent at index {agent_idx}: {e}")
            broker_npn = ""
            email = ""

        # Store the collected data for this agent
        agent_data.append({
            "full_name": full_name,
            "role": role,
            "broker_npn": broker_npn,
            "email": email
        })

        # Move to the next agent
        agent_idx += 1

    # Write all collected agent data to the CSV file
    output_csv_path = os.path.join(
        os.path.normpath(matrix_row["download_path"]),
        f"{matrix_row['extracted_file_prefix']}.csv"
    )
    with open(output_csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["full name", "role", "broker npn", "email"])
        for agent in agent_data:
            writer.writerow([agent["full_name"], agent["role"], agent["broker_npn"], agent["email"]])
    print(f"== Data extracted to {output_csv_path}")


def wait_for_file(download_dir, ext=".pdf", timeout=120):
    """Wait until a non-empty file with given extension appears in folder and size is stable."""
    last_size = -1
    stable_count = 0

    for _ in range(timeout):
        files = glob.glob(os.path.join(download_dir, f"*{ext}"))
        if files:
            latest = max(files, key=os.path.getctime)
            size = os.path.getsize(latest)
            if size > 0:
                if size == last_size:
                    stable_count += 1
                else:
                    stable_count = 0
                last_size = size
                if stable_count >= 3:
                    return latest
        time.sleep(1)
    raise TimeoutError("PDF download did not complete in time.")

def convert_pdf_to_csv(pdf_path, csv_path):
    rows = []
    headers = None
    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            tables = page.extract_tables()
            for table in tables:
                for row in table:
                    cleaned = [
                        (cell or "").replace("\n", "").replace("  ", " ").strip()
                        for cell in row
                    ]
                    if headers is None and "Name" in cleaned[0]:
                        headers = cleaned
                        rows.append(headers)
                        continue
                    if headers and cleaned == headers:
                        continue
                    if cleaned[0] == "" and rows:
                        cleaned[0] = rows[-1][0]
                    rows.append(cleaned)
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerows(rows)
    print(f"✅ Cleaned {len(rows)} rows into {csv_path}")

    

# Example usage (uncomment for testing):
# move_file("./downloaded_file.csv", "./processed/downloaded_file.csv")
# delete_file("./temp_file.csv")
# unzip_file("./compressed.zip", "./unzipped/")
# print(build_gdrive_path("G:/Shared drives/ExamplePath", "ACU", {
#     "current_month_number": "01",
#     "current_month_year": "January 2025",
#     "current_year": "2025"
# }))
# print(build_blob_path("raw/agent_contract_update", "ACU", {
#     "current_month_number": "01",
#     "current_month_short": "Jan",
#     "current_year": "2025"
# }))
# print(build_renamed_file_path("C:/Downloads", "raw_acu_ambetter_aca", "03252025", "csv", suffix="test"))
