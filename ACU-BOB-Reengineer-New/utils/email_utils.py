# ==========================================================
#  utils/email_utils.py
# ==========================================================
"""
email_utils.py
--------------
Purpose:
    - Send emails via Azure Communication Services.
    - Supports plain text, HTML, and attachments.
    - Teams notifications sent via channel email addresses.
"""

import os
import base64
from typing import List, Dict, Optional, Union
from azure.communication.email import EmailClient
from azure.identity import DefaultAzureCredential
from azure.keyvault.secrets import SecretClient

# ==========================================================
#  CONFIGURATION
# ==========================================================
KEYVAULT_NAME = os.getenv("KEY_VAULT_NAME", "")
KEYVAULT_URL = f"https://{KEYVAULT_NAME}.vault.azure.net/"
ACS_SECRET_NAME = "ACS-EMAIL-CONNECTION-STRING"
SENDER_ADDRESS = "dataops@834labs.com"

_cached_connection_string = None


def _get_connection_string() -> str:
    """Fetch ACS connection string from Key Vault (cached after first call)."""
    global _cached_connection_string
    if _cached_connection_string is None:
        credential = DefaultAzureCredential()
        client = SecretClient(vault_url=KEYVAULT_URL, credential=credential)
        _cached_connection_string = client.get_secret(ACS_SECRET_NAME).value
        print(f"🔐 ACS connection string loaded from Key Vault")
    return _cached_connection_string


# ==========================================================
#  HELPERS
# ==========================================================
def _split_recipients(to: Union[str, List[str]]) -> List[Dict[str, str]]:
    """
    Accepts a string with addresses separated by ';' or ','
    OR a list[str]. Returns a list of {'address': '<email>'} dicts.
    """
    if isinstance(to, list):
        parts = to
    else:
        parts = [p.strip() for p in to.replace(";", ",").split(",") if p.strip()]
    return [{"address": addr} for addr in parts]


def _get_email_client() -> EmailClient:
    """Return an authenticated EmailClient instance."""
    return EmailClient.from_connection_string(_get_connection_string())


def _extract_message_id(result) -> Optional[str]:
    """Extract message ID from ACS send result (handles SDK variations)."""
    message_id = getattr(result, "message_id", None)
    if not message_id and isinstance(result, dict):
        message_id = result.get("messageId") or result.get("id")
    return message_id


# ==========================================================
#  TEAMS CHANNEL EMAIL
# ==========================================================
DEFAULT_TEAMS_CHANNEL = (
    "f3eb5d04.agilityins.onmicrosoft.com@amer.teams.ms"
)


# ==========================================================
#  SEND EMAIL
# ==========================================================
def send_email(
    to: Union[str, List[str]],
    subject: str,
    body_plain: str = "",
    body_html: Optional[str] = None,
    attachments: Optional[List[Dict]] = None,
) -> Optional[str]:
    """
    Send an email via Azure Communication Services.

    Parameters
    ----------
    to : str or list
        Recipient(s). Semicolon/comma-separated string or list.
    subject : str
        Email subject line.
    body_plain : str
        Plain text body.
    body_html : str, optional
        HTML body. If None, wraps plain text in <pre> tags.
    attachments : list, optional
        List of dicts with 'name', 'content_type', 'content_bytes_base64'.

    Returns
    -------
    str or None
        Message ID if successful, None if failed.
    """
    try:
        client = _get_email_client()

        if body_html is None:
            body_html = f"<html><body><pre style='font-family: Consolas, monospace; font-size: 13px;'>{body_plain}</pre></body></html>"

        message = {
            "senderAddress": SENDER_ADDRESS,
            "recipients": {"to": _split_recipients(to)},
            "content": {
                "subject": subject,
                "plainText": body_plain,
                "html": body_html,
            },
        }

        if attachments:
            message["attachments"] = attachments

        poller = client.begin_send(message)
        result = poller.result()
        mid = _extract_message_id(result)

        print(f"📨 Email sent to {to} (id={mid})")
        return mid

    except Exception as e:
        print(f"❌ Email send failed: {e}")
        return None


def send_teams_notification(
    subject: str,
    body: str,
    channel_email: str = DEFAULT_TEAMS_CHANNEL,
    body_html: Optional[str] = None,
    attachments: Optional[List[Dict]] = None,
) -> Optional[str]:
    """
    Send a notification to a Teams channel via its email address.

    Parameters
    ----------
    subject : str
        Notification subject.
    body : str
        Plain text body.
    channel_email : str
        Teams channel email address.
    body_html : str, optional
        HTML body override.
    attachments : list, optional
        List of dicts with 'name', 'content_type', 'content_bytes_base64'.

    Returns
    -------
    str or None
        Message ID if successful, None if failed.
    """
    return send_email(
        to=channel_email,
        subject=subject,
        body_plain=body,
        body_html=body_html,
        attachments=attachments,
    )