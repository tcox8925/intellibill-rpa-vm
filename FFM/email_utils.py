import pandas as pd
import requests

import date_utils
import db_connection
from db_connection import connect_to_db
from azure_blob_utils import authenticate_blob_storage
from datetime import datetime
import db_connection as db


def get_email_recipients(notification_process):
    try:
        conn = db_connection.get_postgres_connection()
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
                SELECT to_list, cc_list FROM to_emails CROSS JOIN cc_emails
                """

        cursor = conn.cursor()
        cursor.execute(query, (notification_process, notification_process))
        result = cursor.fetchone()
        conn.close()

        if result:
            return {
                "to": result.to_list.split(",") if result.to_list else [],
                "cc": result.cc_list.split(",") if result.cc_list else []
            }
        else:
            return {"to": [], "cc": []}

    except Exception as e:
        print(f"Error retrieving email recipients: {e}")
        return {"to": [], "cc": []}


def send_email_notification(to, cc, email_subject, email_text):
    payload = {
        "to": to,
        "cc": cc,
        "email_subject": email_subject,
        "email_text": email_text
    }
    headers = {"Content-Type": "application/json"}
    flow_url = 'https://prod-158.westus.logic.azure.com:443/workflows/5203ad938d614c18816659403486fda2/triggers/manual/paths/invoke?api-version=2016-06-01&sp=%2Ftriggers%2Fmanual%2Frun&sv=1.0&sig=V2AeHHV2cD5d6jW4G65MSE8Q-J4NERy_0-m9wCml7jk'

    try:
        response = requests.post(flow_url, json=payload, headers=headers)
        if response.status_code == 200:
            print(f"Email notification triggered successfully.")
        else:
            print(f"Failed to trigger email notification: {response.status_code} - {response.text}")
    except Exception as e:
        print(f"Error triggering email notification: {e}")


def send_carrier_error_alert_email(carrier_id, script_name):
    """
            ACR alert email for unexpected errors.
    """
    print("==Starting ACR error alert email process...")
    notification_process = 'Carrier Error Shutdown Alert'

    carriers_df = db_connection.get_lup_carriers()
    carrier_name = carriers_df.loc[carriers_df['id'] == carrier_id]['vendor_name'].item()

    email_subject = 'ACR - Carrier Error Alert'

    email_str = (f'An unexpected error was encountered when handling the ACR process for {carrier_name} in \'{script_name}\'.<br><br>'
                 f'The carrier\'s ACR process has now been disabled. Anything processed in the last batch will be uploaded to the CRM.')

    print(f"\nTriggering email for ACR...")
    print(f"String contained in email:\n{email_str}")

    send_email_notification(
        to=["dataops@834labs.com","acorcoran@834labs.com"],
        cc=["dataops@834labs.com","acorcoran@834labs.com"],
        email_subject=email_subject,
        email_text=email_str
    )

def send_duplicate_alert_email(script_name, carrier_id, npn, responsible_agent_npn='NA', email='NA', name='NA'):
    """
            ACR alert email for duplicates in spreadsheets.
    """
    print("==Starting ACR duplicate alert email process...")
    notification_process = 'Carrier Duplicate Alert'

    carriers_df = db_connection.get_lup_carriers()
    carrier_name = carriers_df.loc[carriers_df['id'] == carrier_id]['vendor_name'].item()

    email_subject = f'ACR - Spreadsheet Duplicate Alert for {carrier_name}'

    email_str = (
        f'A potential duplicate for \'{carrier_name}\' was found during \'{script_name}\'.<br>'
        f'The duplicate row has been withheld from the spreadsheet and the CRM entry updated accordingly.<br>'
        f'The carrier\'s ACR automatic_export flag is now disabled. Please resolve the duplicate, then re-enable the flag to ensure that the file will be delivered.<br>'
        f'Carrier ID: {carrier_id}<br><br>'
        f'NPN: {npn}<br>'
        f'Responsible Agent NPN: {responsible_agent_npn}<br>'
        f'Email: {email}<br>'
        f'Name: {name}<br><br><br>')

    print(f"\nTriggering email for ACR...")
    print(f"String contained in email:\n{email_str}")

    send_email_notification(
        to=["dataops@834labs.com", "acorcoran@834labs.com"],
        cc=["dataops@834labs.com", "acorcoran@834labs.com"],
        email_subject=email_subject,
        email_text=email_str
    )

def send_acr_summary_email():
    """
        ACR summary email for daily requests.
    """
    print("===Starting ACR summary email logic...")

    email_subject = 'ACR - Latest Run Report Summary'

    df_contracts = db.get_all_contracts_from_this_run()
    run_id = db.get_current_run_id()
    if run_id is None:
        print("==No contracts were processed in this run. Skipping email process.")
        return

    unique_statuses = pd.unique(df_contracts[['contract_status', 'fail_status', 'success_status']].values.ravel('K'))
    print(unique_statuses)
    carrier_ids = pd.unique(df_contracts['carrier_id'])
    print(carrier_ids)
    carriers_df = db_connection.get_lup_carriers()

    email_str = f'Summary for run ID: {run_id}<br><br>'
    for carrier in carrier_ids:
        carrier_name = carriers_df.loc[carriers_df['id'] == carrier]['vendor_name'].item()
        email_str += f'Carrier: {carrier_name}<br>'
        carrier_total = 0
        trimmed_df = df_contracts.loc[df_contracts['carrier_id'] == carrier]
        for status in unique_statuses:
            if status == 'None':
                continue
            status_total = 0
            status_total += len(trimmed_df.loc[trimmed_df['success_status'] == status])
            status_total += len(trimmed_df.loc[(trimmed_df['fail_status'] == status)
                                                    & (trimmed_df['success_status'] == 'None')])
            status_total += len(trimmed_df.loc[(trimmed_df['contract_status'] == status)
                                                    & (trimmed_df['success_status'] == 'None')
                                                    & (trimmed_df['fail_status'] == 'None')])
            #print(f"{status} in success: {len(trimmed_df.loc[trimmed_df['success_status'] == status])}")
            #print(f"{status} in failure: {len(trimmed_df.loc[trimmed_df['fail_status'] == status])}")
            #print(f"{status} in old: {len(trimmed_df.loc[trimmed_df['contract_status'] == status])}")
            if status_total > 0:
                email_str += f"Number of {status} statuses: {status_total}<br>"
            carrier_total += status_total
        email_str += f"Total number of contracts: {carrier_total}<br>"
        email_str += '<br><br>'

    print(f"\nTriggering email for ACR...")
    print(f"String contained in email:\n{email_str}")

    send_email_notification(
        to=["dataops@834labs.com","acorcoran@834labs.com"],
        cc=["dataops@834labs.com","acorcoran@834labs.com"],
        email_subject=email_subject,
        email_text=email_str
    )

def send_ambetter_email(carrier_id, script_name, email_to, email_flow_url, email_message, gdrive_path, gdrive_filename, eod_flag):
    """
            ACR alert email for unexpected errors.
    """
    print("==Starting Ambetter email process...")
    notification_process = 'Ambetter Email'

    email_subject = 'Contract Request for ' + datetime.now().strftime('%m/%d/%Y')

    print(f"\nTriggering Ambetter Email...")
    print(f"String contained in email:\n{email_message}")

    payload = {
        "to": ["dataops@834labs.com","acorcoran@834labs.com"],
        "cc": ["dataops@834labs.com","acorcoran@834labs.com"],
        "email_subject": email_subject,
        "email_text": email_message,
        "gdrive_path": gdrive_path,
        "gdrive_filename": gdrive_filename,
        "eod_flag": eod_flag
    }
    headers = {"Content-Type": "application/json"}
    try:
        response = requests.post(email_flow_url, json=payload, headers=headers)
        if response.status_code == 200:
            print(f"Email notification triggered successfully.")
        else:
            print(f"Failed to trigger email notification: {response.status_code} - {response.text}")
    except Exception as e:
        print(f"Error triggering email notification: {e}")