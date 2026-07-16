"""
email_read.py
─────────────
Utility module for reading and processing emails via MS Graph API.
Mailbox: agilitydata@834labs.com

Responsibilities:
  • Find emails matching sender_email + subject_key from the RPA matrix
  • Download attachments from matched emails
  • Extract secure links from email HTML bodies (Convey, Proofpoint, etc.)
  • Mark processed emails as read
"""

import os
import re
import html
import base64
import requests
from datetime import datetime, timezone, timedelta
from typing import Optional, List
from graph_auth import get_graph_access_token

# ──────────────────────────────────────────────────────────────
# Constants
# ──────────────────────────────────────────────────────────────
MAILBOX_UPN = os.getenv("RPA_ARCHITECTURE_MAILBOX_UPN", "")
GRAPH_BASE = "https://graph.microsoft.com/v1.0"


# ──────────────────────────────────────────────────────────────
# Internal helpers
# ──────────────────────────────────────────────────────────────
def _headers(access_token: str) -> dict:
    return {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
    }


def _sender_address(msg: dict) -> str:
    """Extract lowercase sender email from a Graph message object."""
    return (
        ((msg.get("from") or {}).get("emailAddress") or {}).get("address") or ""
    ).lower().strip()


def _parse_graph_dt(dt_str: str) -> Optional[str]:
    if not dt_str:
        return None
    return datetime.fromisoformat(dt_str.replace("Z", "+00:00")).astimezone(timezone.utc)


# ──────────────────────────────────────────────────────────────
# Find matching email
# ──────────────────────────────────────────────────────────────
def find_matching_email(
    access_token: str,
    sender_emails: str,
    subject_key: str,
    *,
    lookback_minutes: int = 5,
    page_size: int = 25,
    test_mode: bool = False,
) -> Optional[str]:
    """
    Searches the mailbox for the most recent UNREAD email within
    the last `lookback_minutes` that matches sender + subject_key.
    Designed for Power Automate-triggered flows.
    """

    allowed_senders = {
        s.strip().lower() for s in sender_emails.split(",") if s.strip()
    }
    subject_keys_lower = [
        s.strip().lower() for s in subject_key.split(",") if s.strip()
    ]

    print(f"== [EmailRead] ── find_matching_email ──")
    print(f"== [EmailRead]   Allowed senders : {allowed_senders}")
    print(f"== [EmailRead]   Subject keys    : {subject_keys_lower}")
    print(f"== [EmailRead]   Test mode       : {test_mode}")

    if not allowed_senders or not subject_keys_lower:
        print("== [EmailRead]   ✗ No sender_email or subject_key configured. Skipping.")
        return None

    since_dt = (
        datetime.now(timezone.utc) - timedelta(minutes=lookback_minutes)
    ).strftime("%Y-%m-%dT%H:%M:%SZ")

    headers = _headers(access_token)
    inbox_url = f"{GRAPH_BASE}/users/{MAILBOX_UPN}/mailFolders/Inbox/messages"

    # In test mode: no date filter — match any recent unread email
    if test_mode:
        odata_filter = "isRead eq false"
        print("== [EmailRead] [TEST MODE] Date filter disabled — searching all unread emails.")
    else:
        odata_filter = f"isRead eq false and receivedDateTime ge {since_dt}"

    params = {
        "$select": "id,subject,from,receivedDateTime,isRead,body,hasAttachments",
        "$top": str(page_size),
        "$orderby": "receivedDateTime desc",
        "$filter": odata_filter,
    }

    resp = requests.get(inbox_url, headers=headers, params=params, timeout=30)

    if resp.status_code >= 400:
        print(f"== [EmailRead] Graph query failed: {resp.status_code} {resp.text}")
        return None

    messages = resp.json().get("value") or []
    if test_mode:
        print(f"== [EmailRead] [TEST MODE] Fetched {len(messages)} unread messages (no date filter).")
    else:
        print(f"== [EmailRead] Fetched {len(messages)} unread messages in last {lookback_minutes}m.")

    for idx, msg in enumerate(messages, 1):
        sender = _sender_address(msg)
        subject = (msg.get("subject") or "").lower()
        received = msg.get("receivedDateTime", "?")

        print(f"== [EmailRead]   [{idx}/{len(messages)}] from='{sender}' | subject='{subject}' | received={received}")

        if sender not in allowed_senders:
            print(f"== [EmailRead]     ✗ Sender not in allowed list, skipping.")
            continue
        if not any(sk in subject for sk in subject_keys_lower):
            print(f"== [EmailRead]     ✗ Subject did not match any key, skipping.")
            continue

        print(
            f"== [EmailRead]   ✓ MATCHED: subject='{msg.get('subject')}', "
            f"from='{sender}', received={received}"
        )
        return msg

    print(
        f"== [EmailRead] No match found for senders={allowed_senders}, "
        f"subject_keys={subject_keys_lower}."
    )
    return None


# ──────────────────────────────────────────────────────────────
# Extract secure link from email body
# ──────────────────────────────────────────────────────────────
# Patterns that indicate a secure-email portal URL
_SECURE_PATTERNS = re.compile(
    r"proofpoint\.com|conveyhs\.com|zixmail|voltage|securemail"
    r"|securemessage|safelinks\.protection\.outlook\.com",
    re.IGNORECASE,
)
_SKIP_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".svg", ".ico", ".bmp"}


def extract_secure_link(email_body_html: str) -> Optional[str]:
    """
    Parses the email HTML body and extracts the secure-message URL.

    Priority order:
      1. Anchor whose visible text contains "Click here" (Convey / Proofpoint).
      2. Anchor whose href matches a known secure-email provider domain.
      3. First non-image https anchor as a fallback.

    Returns
    -------
    str | None
        The URL, or None if no link could be identified.
    """
    if not email_body_html:
        print("== [EmailRead] extract_secure_link: email body is empty, returning None.")
        return None

    # Lightweight regex-based extraction — no BeautifulSoup dependency
    anchors_raw = re.findall(
        r'<a\s[^>]*href=["\']([^"\']+)["\'][^>]*>(.*?)</a>',
        email_body_html,
        re.IGNORECASE | re.DOTALL,
    )
    # Unescape HTML entities in hrefs (e.g. &amp; → &, &#61; → =)
    anchors = [(html.unescape(href), text) for href, text in anchors_raw]
    print(f"== [EmailRead] extract_secure_link: found {len(anchors)} anchor(s) in email body.")

    # Pass 1: "Click here" text
    for href, text in anchors:
        if "click here" in text.lower():
            print(f"== [EmailRead] Found 'Click here' link: {href}")
            return href

    # Pass 2: Known secure-provider domains
    for href, _ in anchors:
        if _SECURE_PATTERNS.search(href):
            print(f"== [EmailRead] Found secure-provider link: {href}")
            return href

    # Pass 3: First non-image https link
    for href, _ in anchors:
        if href.startswith("http") and not any(
            href.lower().endswith(ext) for ext in _SKIP_EXTENSIONS
        ):
            print(f"== [EmailRead] Fallback link: {href}")
            return href

    print("== [EmailRead] No secure link found in email body.")
    return None


# ──────────────────────────────────────────────────────────────
# Download attachment(s) from an email
# ──────────────────────────────────────────────────────────────
def download_attachment(
    access_token: str,
    message_id: str,
    download_folder: str,
    target_prefix: str = "",
    target_extension: str = "",
) -> Optional[str]:
    """
    Downloads the first attachment that matches the given prefix (contains)
    and extension (endswith) from the specified email.

    Parameters
    ----------
    access_token : str
    message_id : str
        Graph message ID.
    download_folder : str
        Local directory to save the file into.
    target_prefix : str
        Substring the filename must contain (case-insensitive). Empty = any.
    target_extension : str
        File extension the filename must end with (without leading dot).
        Empty = any.

    Returns
    -------
    str | None
        Full path to the saved file, or None if no matching attachment found.
    """
    os.makedirs(download_folder, exist_ok=True)
    target_prefix_lower = target_prefix.strip().lower()
    target_ext_lower = target_extension.strip().lower().lstrip(".")

    print(f"== [EmailRead] ── download_attachment ──")
    print(f"== [EmailRead]   Message ID      : {message_id[:30]}...")
    print(f"== [EmailRead]   Download folder  : {download_folder}")
    print(f"== [EmailRead]   Target prefix    : '{target_prefix_lower}' (empty=any)")
    print(f"== [EmailRead]   Target extension : '{target_ext_lower}' (empty=any)")

    headers = _headers(access_token)
    att_url = f"{GRAPH_BASE}/users/{MAILBOX_UPN}/messages/{message_id}/attachments"
    resp = requests.get(att_url, headers=headers, timeout=30)

    if resp.status_code >= 400:
        print(f"== [EmailRead] Attachments query failed: {resp.status_code} {resp.text}")
        return None

    attachments = resp.json().get("value") or []
    print(f"== [EmailRead] Found {len(attachments)} attachment(s).")

    for att in attachments:
        att_name = (att.get("name") or "").strip()
        att_lower = att_name.lower()
        att_size = att.get("size", "?")

        prefix_ok = (not target_prefix_lower) or (target_prefix_lower in att_lower)
        ext_ok = (not target_ext_lower) or att_lower.endswith(f".{target_ext_lower}")

        print(f"== [EmailRead]   Checking: '{att_name}' ({att_size} bytes) | prefix_ok={prefix_ok}, ext_ok={ext_ok}")

        if not (prefix_ok and ext_ok):
            print(f"== [EmailRead]     ✗ Skipping '{att_name}' — no match.")
            continue

        # Decode content
        content_bytes = att.get("contentBytes")
        if content_bytes:
            raw = base64.b64decode(content_bytes)
            print(f"== [EmailRead]     ✓ Decoded from contentBytes ({len(raw):,} bytes)")
        else:
            att_id = att.get("id")
            print(f"== [EmailRead]     Fetching attachment bytes via /$value (att_id={att_id[:30]}...)")
            dl_resp = requests.get(
                f"{att_url}/{att_id}/$value", headers=headers, timeout=60
            )
            if dl_resp.status_code >= 400:
                print(f"== [EmailRead]     ✗ Failed to fetch attachment bytes: {dl_resp.status_code}")
                continue
            raw = dl_resp.content
            print(f"== [EmailRead]     ✓ Fetched via /$value ({len(raw):,} bytes)")

        save_path = os.path.join(download_folder, att_name)
        with open(save_path, "wb") as f:
            f.write(raw)
        print(f"== [EmailRead]   ✓ SAVED: {save_path} ({len(raw):,} bytes)")
        return save_path

    print(
        f"== [EmailRead] No attachment matched prefix='{target_prefix}', "
        f"ext='{target_extension}'."
    )
    return None


def download_all_attachments(
    access_token: str,
    message_id: str,
    download_folder: str,
) -> List[str]:
    """
    Downloads every attachment from the email. Returns list of saved file paths.
    """
    os.makedirs(download_folder, exist_ok=True)
    saved = []

    print(f"== [EmailRead] ── download_all_attachments ──")
    print(f"== [EmailRead]   Message ID     : {message_id[:30]}...")
    print(f"== [EmailRead]   Download folder: {download_folder}")

    headers = _headers(access_token)
    att_url = f"{GRAPH_BASE}/users/{MAILBOX_UPN}/messages/{message_id}/attachments"
    resp = requests.get(att_url, headers=headers, timeout=30)

    if resp.status_code >= 400:
        print(f"== [EmailRead] Attachments query failed: {resp.status_code}")
        return saved

    for att in resp.json().get("value") or []:
        att_name = (att.get("name") or "attachment").strip()
        content_bytes = att.get("contentBytes")
        if content_bytes:
            raw = base64.b64decode(content_bytes)
        else:
            att_id = att.get("id")
            dl_resp = requests.get(
                f"{att_url}/{att_id}/$value", headers=headers, timeout=60
            )
            if dl_resp.status_code >= 400:
                continue
            raw = dl_resp.content

        save_path = os.path.join(download_folder, att_name)
        with open(save_path, "wb") as f:
            f.write(raw)
        saved.append(save_path)
        print(f"== [EmailRead] Saved: {save_path}")

    return saved


# ──────────────────────────────────────────────────────────────
# Mark email as read
# ──────────────────────────────────────────────────────────────
def mark_as_read(access_token: str, message_id: str) -> bool:
    """Marks the email as read so it won't be picked up again."""
    print(f"== [EmailRead] ── mark_as_read ──")
    print(f"== [EmailRead]   Message ID: {message_id[:30]}...")
    headers = _headers(access_token)
    url = f"{GRAPH_BASE}/users/{MAILBOX_UPN}/messages/{message_id}"
    resp = requests.patch(url, headers=headers, json={"isRead": True}, timeout=30)
    if resp.status_code >= 400:
        print(f"== [EmailRead] Warning: mark-as-read failed: {resp.status_code}")
        return False
    print("== [EmailRead] Email marked as read.")
    return True


# ──────────────────────────────────────────────────────────────
# Convenience: get a fresh token + find + return
# ──────────────────────────────────────────────────────────────
def find_email_with_fresh_token(sender_emails: str, subject_key: str, **kwargs):
    """
    Helper that acquires a Graph token and searches in one call.
    Returns (access_token, message_dict) or (access_token, None).
    """
    print(f"== [EmailRead] ── find_email_with_fresh_token ──")
    print(f"== [EmailRead]   Acquiring Graph access token...")
    token = get_graph_access_token()
    print(f"== [EmailRead]   ✓ Token acquired. Searching for email...")
    msg = find_matching_email(token, sender_emails, subject_key, **kwargs)
    return token, msg
