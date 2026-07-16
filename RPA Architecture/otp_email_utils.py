# otp_email_utils.py
"""
OTP Email Utils
===============
Polls a mailbox via Graph API for an OTP email, extracts the code, returns it.
That's it. The handler deals with the browser.

Usage:
    from otp_email_utils import fetch_otp_code

    code = fetch_otp_code(
        sender="info@accountmanagercomms.com",
        subject="Your Verification Code Request",
    )
    # code = "353255"

    # Or with a matrix row:
    code = fetch_otp_code(
        sender=matrix_row["sender_email"],
        subject=matrix_row["subject_key"],
    )
"""

import re
import time
import requests
from datetime import datetime, timedelta, timezone

from graph_auth import get_graph_access_token

DEFAULT_MAILBOX = "dataops@834labs.com"
CODE_RE = re.compile(r"\b(\d{6})\b")


def _parse_dt(dt_str):
    if not dt_str:
        return None
    return datetime.fromisoformat(dt_str.replace("Z", "+00:00")).astimezone(timezone.utc)


def fetch_otp_code(
    sender,
    subject,
    mailbox=DEFAULT_MAILBOX,
    since_dt_utc=None,
    poll_seconds=120,
    poll_interval=5,
    code_pattern=None,
):
    """
    Poll mailbox for an unread email matching sender + subject.
    Extract 6-digit code from body. Mark email as read. Return the code.
    """
    if code_pattern is None:
        code_pattern = CODE_RE

    if since_dt_utc is None:
        since_dt_utc = datetime.now(timezone.utc) - timedelta(minutes=2)
    if since_dt_utc.tzinfo is None:
        since_dt_utc = since_dt_utc.replace(tzinfo=timezone.utc)

    token = get_graph_access_token()
    base = "https://graph.microsoft.com/v1.0"
    url = f"{base}/users/{mailbox}/mailFolders/Inbox/messages"

    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    params = {
        "$select": "id,subject,from,receivedDateTime,isRead,body",
        "$top": "50",
        "$orderby": "receivedDateTime desc",
    }

    deadline = time.time() + poll_seconds
    seen_debug = set()  # only for suppressing repeated debug lines

    print(f"[OTP] Polling {mailbox} for code from {sender} ...")

    while time.time() < deadline:
        r = requests.get(url, headers=headers, params=params, timeout=30)
        if r.status_code >= 400:
            raise RuntimeError(f"[OTP] Graph error: {r.status_code} {r.text[:200]}")

        for msg in r.json().get("value", []):
            msg_id = msg.get("id")
            if not msg_id:
                continue

            # Debug — only print each email once
            if msg_id not in seen_debug:
                seen_debug.add(msg_id)
                _dbg_sender = (((msg.get("from") or {}).get("emailAddress") or {}).get("address") or "")
                _dbg_subject = (msg.get("subject") or "")[:60]
                _dbg_read = msg.get("isRead")
                _dbg_received = (msg.get("receivedDateTime") or "")[:19]
                print(f"[OTP DEBUG] {_dbg_received} | from={_dbg_sender} | subject={_dbg_subject} | read={_dbg_read}")

            if msg.get("isRead"):
                continue

            msg_sender = (((msg.get("from") or {}).get("emailAddress") or {}).get("address") or "").lower()
            if msg_sender != sender.lower():
                print(f"[OTP DEBUG]   -> skipped (sender mismatch: {msg_sender} != {sender.lower()})")
                continue

            msg_subject = (msg.get("subject") or "").strip()
            if subject.lower() not in msg_subject.lower():
                print(f"[OTP DEBUG]   -> skipped (subject mismatch)")
                continue

            received = _parse_dt(msg.get("receivedDateTime"))
            if not received or received < since_dt_utc:
                print(f"[OTP DEBUG]   -> skipped (too old: {received} < {since_dt_utc})")
                continue

            body = (msg.get("body") or {}).get("content") or ""
            # Strip HTML tags to avoid matching CSS values like color #000000
            body_text = re.sub(r'<[^>]+>', ' ', body)
            m = code_pattern.search(body_text)
            if not m:
                continue

            code = m.group(1)

            # Mark read
            requests.patch(
                f"{base}/users/{mailbox}/messages/{msg_id}",
                headers=headers, json={"isRead": True}, timeout=15
            )

            print(f"[OTP] Code: {code}")
            return code

        print(f"[OTP] Waiting... ({int(deadline - time.time())}s left)")
        time.sleep(poll_interval)

    raise RuntimeError(f"[OTP] No code found within {poll_seconds}s")


def mark_matching_as_read(sender, subject, mailbox=DEFAULT_MAILBOX):
    """Mark all unread emails matching sender + subject as read. Call before requesting a new OTP."""
    token = get_graph_access_token()
    base = "https://graph.microsoft.com/v1.0"
    url = f"{base}/users/{mailbox}/mailFolders/Inbox/messages"

    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    params = {
        "$select": "id,subject,from,isRead",
        "$top": "25",
        "$orderby": "receivedDateTime desc",
    }

    r = requests.get(url, headers=headers, params=params, timeout=30)
    if r.status_code >= 400:
        print(f"[OTP] Warning: could not fetch messages to mark read: {r.status_code}")
        return

    count = 0
    for msg in r.json().get("value", []):
        if msg.get("isRead"):
            continue

        msg_sender = (((msg.get("from") or {}).get("emailAddress") or {}).get("address") or "").lower()
        if msg_sender != sender.lower():
            continue

        msg_subject = (msg.get("subject") or "").strip()
        if subject.lower() not in msg_subject.lower():
            continue

        requests.patch(
            f"{base}/users/{mailbox}/messages/{msg['id']}",
            headers=headers, json={"isRead": True}, timeout=15,
        )
        count += 1

    if count:
        print(f"[OTP] Marked {count} old OTP email(s) as read.")