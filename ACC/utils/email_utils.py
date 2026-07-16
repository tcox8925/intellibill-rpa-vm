import os
import base64
import time
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
    to: Union[str, List[str]],
    subject: str,
    body: str,
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
 
        for _ in range(12):  # up to ~2 minutes
            status = poller.status()
            print("Status:", status)
        
            if status in ["Succeeded", "Failed"]:
                break
        
            time.sleep(10)   # <-- key: 10 seconds, not 1 second
        
        result = poller.result(10) # 10 second timeout
        print(result)
        if result is None or not isinstance(result, dict):
            print(f"❌ Could not verify that the email was sent in time, continuing...: {ex}")
            return None

        # Different SDK versions expose id differently; try both
        message_id = getattr(result, "message_id", None)
        if not message_id and isinstance(result, dict):
            message_id = result.get("messageId") or result.get("id")
        print(f"📨 Email sent to {recipients.get('to')} message_id={message_id}")
        return message_id
    except Exception as ex:
        print(f"❌ Failed to send email: {ex}")
        return None

# ----------------------------------------------------------
# Email with a single attachment
# ----------------------------------------------------------
def send_email_with_attachment(
    to: Union[str, List[str]],
    subject: str,
    body: str,
    attachment_path: str,
    mimetype: str = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    cc: Optional[Union[str, List[str]]] = None,
    bcc: Optional[Union[str, List[str]]] = None,
) -> Optional[str]:
    """
    Sends an email via Azure Communication Services with one file attachment.
    Returns message_id on success, or None if sending fails.
    """
    try:
        if not os.path.exists(attachment_path):
            raise FileNotFoundError(f"Attachment not found: {attachment_path}")

        client = EmailClient.from_connection_string(CONNECTION_STRING)

        with open(attachment_path, "rb") as f:
            encoded_content = base64.b64encode(f.read()).decode()

        recipients = {"to": _split_recipients(to)}
        if cc:
            recipients["cc"] = _split_recipients(cc)
        if bcc:
            recipients["bcc"] = _split_recipients(bcc)

        message = {
            "senderAddress": SENDER_ADDRESS,
            "recipients": recipients,
            "content": {
                "subject": subject,
                "plainText": body,
                "html": f"<html><body><pre>{body}</pre></body></html>",
            },
            "attachments": [{
                "name": os.path.basename(attachment_path),
                "contentType": mimetype,
                "contentInBase64": encoded_content,
            }],
        }

        poller = client.begin_send(message)
 
        for _ in range(12):  # up to ~2 minutes
            status = poller.status()
            print("Status:", status)
        
            if status in ["Succeeded", "Failed"]:
                break
        
            time.sleep(10)   # <-- key: 10 seconds, not 1 second
        
        result = poller.result(10) # 10 second timeout
        print(result)
        if result is None or not isinstance(result, dict):
            print(f"❌ Could not verify that the email was sent in time, continuing...: {ex}")
            return None
        
        message_id = getattr(result, "message_id", None)
        if not message_id and isinstance(result, dict):
            message_id = result.get("messageId") or result.get("id")
        print(f"📨 Email with attachment sent to {recipients.get('to')} message_id={message_id}")
        return message_id
    except Exception as ex:
        print(f"❌ Email send failed: {ex}")
        return None

# ----------------------------------------------------------
# Email with multiple attachments
# ----------------------------------------------------------
def send_email_with_attachments(
    to: Union[str, List[str]],
    subject: str,
    body: str,
    attachment_paths: List[str],
    mimetype: str = "application/pdf",
    cc: Optional[Union[str, List[str]]] = None,
    bcc: Optional[Union[str, List[str]]] = None,
) -> Optional[str]:
    """
    Sends an email via Azure Communication Services with multiple file attachments.
    Returns message_id on success, or None if sending fails.
    """
    try:
        if not attachment_paths:
            raise ValueError("No attachments provided.")

        client = EmailClient.from_connection_string(CONNECTION_STRING)

        # Build recipient structure
        recipients = {"to": _split_recipients(to)}
        if cc:
            recipients["cc"] = _split_recipients(cc)
        if bcc:
            recipients["bcc"] = _split_recipients(bcc)

        # Encode all valid attachments
        attachments = []
        for path in attachment_paths:
            if not os.path.exists(path):
                print(f"⚠️ Skipping missing attachment: {path}")
                continue
            with open(path, "rb") as f:
                encoded_content = base64.b64encode(f.read()).decode()
            attachments.append({
                "name": os.path.basename(path),
                "contentType": mimetype,
                "contentInBase64": encoded_content,
            })

        if not attachments:
            print("⚠️ No valid attachments found; sending email without files.")
            return send_email(to, subject, body, cc=cc, bcc=bcc)

        message = {
            "senderAddress": SENDER_ADDRESS,
            "recipients": recipients,
            "content": {
                "subject": subject,
                "plainText": body,
                "html": f"<html><body><pre>{body}</pre></body></html>",
            },
            "attachments": attachments,
        }

        poller = client.begin_send(message)
 
        for _ in range(12):  # up to ~2 minutes
            status = poller.status()
            print("Status:", status)
        
            if status in ["Succeeded", "Failed"]:
                break
        
            time.sleep(10)   # <-- key: 10 seconds, not 1 second
        
        result = poller.result(10) # 10 second timeout
        print(result)
        if result is None or not isinstance(result, dict):
            print(f"❌ Could not verify that the email was sent in time, continuing...: {ex}")
            return None
        
        message_id = getattr(result, "message_id", None)
        if not message_id and isinstance(result, dict):
            message_id = result.get("messageId") or result.get("id")

        print(f"📨 Email with {len(attachments)} attachment(s) sent to {recipients.get('to')} message_id={message_id}")
        return message_id

    except Exception as ex:
        print(f"❌ Failed to send multi-attachment email: {ex}")
        return None
