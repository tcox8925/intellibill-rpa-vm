import pandas as pd
import requests

import date_utils
import db_connection
from db_connection import connect_to_db
from azure_blob_utils import authenticate_blob_storage
from datetime import datetime
import os
import base64
from typing import List, Dict, Optional, Union
from azure.communication.email import EmailClient

# ==========================================================
#  CONFIGURATION
# ==========================================================

CONNECTION_STRING = (
    "endpoint=https://myopsemailservice.unitedstates.communication.azure.com/;"
    f"accesskey={os.getenv('ACS_ACCESS_KEY', '')}"
)
SENDER_ADDRESS = "dataops@834labs.com"

# ----------------------------------------------------------
# Helpers
# ----------------------------------------------------------
def _split_recipients(to: Union[str, List[str]]) -> List[Dict[str, str]]:
    """
    Accepts a string with addresses separated by ';' or ',' OR a list[str].
    Returns a list of {'address': '<email>'} dicts as ACS expects.
    """
    if isinstance(to, list):
        parts = to
    else:
        # allow semicolon or comma separated
        parts = [p.strip() for p in to.replace(";", ",").split(",") if p.strip()]
    return [{"address": addr} for addr in parts]

# ----------------------------------------------------------
# Plain email (no attachment)
# ----------------------------------------------------------
def send_email(
    subject: str,
    body: str,
    to: Union[str, List[str]] = None,
    cc: Optional[Union[str, List[str]]] = None,
    bcc: Optional[Union[str, List[str]]] = None,
    html: bool = False,
) -> Optional[str]:
    """
    Send an email via Azure Communication Services (no attachments).
    Returns message_id on success, or None if sending fails.
    """
    try:
        client = EmailClient.from_connection_string(CONNECTION_STRING)

        recipients = {"to": _split_recipients(to)}
        if cc:
            recipients["cc"] = _split_recipients(cc)
        if bcc:
            recipients["bcc"] = _split_recipients(bcc)

        content = {
            "subject": subject,
            "plainText": body if not html else None,
            "html": f"<html><body><pre>{body}</pre></body></html>" if html else None,
        }

        message = {
            "senderAddress": SENDER_ADDRESS,
            "recipients": recipients,
            "content": content,
        }

        poller = client.begin_send(message)
        result = poller.result()
        # Different SDK versions expose id differently; try both
        message_id = getattr(result, "message_id", None)
        if not message_id and isinstance(result, dict):
            message_id = result.get("messageId") or result.get("id")
        print(f"📨 Email sent to {recipients.get('to')} message_id={message_id}")
        return message_id
    except Exception as ex:
        print(f"❌ Failed to send email: {ex}")
        return None

def get_email_recipients():
    try:
        conn = db_connection.get_postgres_connection()
        if not conn:
            print("Failed to establish database connection.")
            return {"to": [], "cc": []}

        query = """
                SELECT STRING_AGG(user_email,',') AS to_list
                FROM ops_srv.process_type_users
                WHERE process_id = '5536b85c-52a1-422c-b181-54496851753e' AND email = 'True'
                """
        cursor = conn.cursor()
        cursor.execute(query)
        to_list = cursor.fetchone()
        print(to_list)

        teams_channel_query = """
                        SELECT teams_channel FROM ops_srv.process_type
                        WHERE process_id = '5536b85c-52a1-422c-b181-54496851753e'
                        """
        cursor = conn.cursor()
        cursor.execute(teams_channel_query)
        teams_channel = cursor.fetchone()[0][0].get('email')
        print(teams_channel)
        conn.close()

        to_list = to_list[0] + ',' + (teams_channel)

        return {
            "to": to_list
        }
    except Exception as e:
        print(f"Error retrieving email recipients: {e}")
        return {"to": [], "cc": []}

def send_email_notification(email_subject, email_text, to=None, cc=None):
    try:
        recipients = get_email_recipients()
        send_email(email_subject,email_text,to=recipients.get('to') if not to else to,cc=recipients.get('cc') if not cc else cc,html=True)
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
        email_subject=email_subject,
        email_text=email_str
    )

def send_acr_summary_email():
    """
        ACR summary email for daily requests.
    """
    print("===Starting ACR summary email logic...")

    email_subject = 'ACR - Latest Run Report Summary'

    df_contracts = db_connection.get_all_contracts_from_this_run()
    run_id = db_connection.get_current_run_id()
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
