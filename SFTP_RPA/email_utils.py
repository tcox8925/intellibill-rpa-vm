import requests
from db_connection import connect_to_db
from azure_blob_utils import authenticate_blob_storage
from datetime import datetime


def get_email_recipients(notification_process):
    try:
        conn = connect_to_db()
        if not conn:
            print("Failed to establish database connection.")
            return {"to": [], "cc": []}

        query = """
            WITH to_emails AS (
                SELECT STRING_AGG(email, ',') AS to_list
                FROM wpo.ops_email_notification
                WHERE process_type = %s AND recipient_type = 'to'
            ),
            cc_emails AS (
                SELECT STRING_AGG(email, ',') AS cc_list
                FROM wpo.ops_email_notification
                WHERE process_type = %s AND recipient_type = 'cc'
            )
            SELECT to_list, cc_list 
            FROM to_emails CROSS JOIN cc_emails
        """

        cursor = conn.cursor()
        cursor.execute(query, (notification_process, notification_process))
        result = cursor.fetchone()
        conn.close()

        if result:
            to_list = result[0]  # first column
            cc_list = result[1]  # second column

            return {
                "to": to_list.split(",") if to_list else [],
                "cc": cc_list.split(",") if cc_list else []
            }

        return {"to": [], "cc": []}

    except Exception as e:
        print(f"Error retrieving email recipients: {e}")
        return {"to": [], "cc": []}


def send_email_notification(flow_url, process_name, notification_process, to, cc, file_name="", folder_path="", success_files=None, failed_files=None, process_type=""):
    # Only send email if process_type is COM


    success_files = success_files or []
    failed_files = failed_files or []

    payload = {
        "process_name": process_name,
        "notification_process": notification_process,
        "to": to,
        "cc": cc,
        "file_name": file_name,
        "folder_path": folder_path,
        "success_files": success_files,
        "failed_files": failed_files,
        "process_type": process_type
    }
    headers = {"Content-Type": "application/json"}

    try:
        response = requests.post(flow_url, json=payload, headers=headers)
        if response.status_code == 200:
            print(f"Email notification triggered successfully for {process_name} ({notification_process}).")
        else:
            print(f"Failed to trigger email notification: {response.status_code} - {response.text}")
    except Exception as e:
        print(f"Error triggering email notification: {e}")


def send_acu_bob_summary_email(matrix_df, today_date_str):
    """
    ACU/BOB summary email for daily downloads. Checks Azure Blob Storage for success/fail.
    Sends email to recipients retrieved from the ops_email_notification table.
    """
    print("Starting ACU/BOB summary email logic...")

    blob_service_client = authenticate_blob_storage()
    if not blob_service_client:
        print("Failed to authenticate with Azure Blob Storage.")
        return

    summary_targets = matrix_df[
        (matrix_df["process_name"].isin(["ACU", "BOB"])) &
        (matrix_df["cadence"].str.lower() == "daily") &
        (matrix_df["disabled"].str.lower() != "yes") &
        (matrix_df["run_sandbox_only"].str.lower() != "yes")
    ]
    summary_targets = summary_targets.astype(str).apply(lambda x: x.str.strip())

    current_acu_blob_folder = datetime.now().strftime("%m %b %Y")
    current_bob_blob_folder = datetime.now().strftime("%Y %m %b")
    print(f"Current ACU Blob Folder: {current_acu_blob_folder}")
    print(f"Current BOB Blob Folder: {current_bob_blob_folder}")
    print(f"Found {len(summary_targets)} rows for ACU/BOB daily processes")

    process_map = {"ACU": [], "BOB": []}
    failed_map = {"ACU": [], "BOB": []}

    for _, row in summary_targets.iterrows():
        extension = row["extracted_file_extension"].strip().lower()
        expected_filename = f"{row['rename_base']}{today_date_str}.{extension}"
        base_blob_path = row["blob_base_path"]

        if row["process_name"] == "ACU":
            blob_path = f"{base_blob_path}{current_acu_blob_folder}/{expected_filename}"
        else:
            blob_path = f"{base_blob_path}{current_bob_blob_folder}/{expected_filename}"

        process_name = row["process_name"]
        print(f"Checking blob path: {blob_path}")

        blob_client = blob_service_client.get_blob_client(container="834analytics-dev", blob=blob_path)

        if blob_client.exists():
            print(f"File found in Blob for {process_name} – {row['carrier_name']}")
            process_map[process_name].append(row["carrier_name"])
        else:
            print(f"File NOT found for {process_name} – {row['carrier_name']}")
            failed_map[process_name].append(row["carrier_name"])

    for process_name in ["ACU", "BOB"]:
        process_rows = matrix_df[
            (matrix_df["process_name"] == process_name) &
            (matrix_df["cadence"].str.lower() == "daily")
        ]
        if not process_rows.empty:
            flow_url = process_rows.iloc[0]["pautomate_url"].strip()
            notification_process = process_rows.iloc[0]["notification_process"].strip()

            # 🔁 Get dynamic recipients from DB
            email_info = get_email_recipients(notification_process)
            recipient_list = email_info.get("to", [])
            cc_list = email_info.get("cc", [])

            print(f"\nTriggering email for {process_name}...")
            print(f" Success List: {process_map[process_name]}")
            print(f" Failed List: {failed_map[process_name]}")
            print(f" Sending to: {recipient_list}")
            print(f" CC: {cc_list}")
            print(f" Flow URL: {flow_url}")

            send_email_notification(
                flow_url=flow_url,
                process_name=process_name,
                notification_process=notification_process,
                to=recipient_list,
                cc=cc_list,
                success_files=process_map[process_name],
                failed_files=failed_map[process_name],
                process_type=process_name
            )
