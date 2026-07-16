import os
import shutil
import zipfile


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
