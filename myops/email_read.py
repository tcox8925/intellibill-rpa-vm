import requests
import re
import time
import os
from pathlib import Path
from datetime import datetime, timezone
from dotenv import load_dotenv

from otp_info import OTP_SUBJECT, OTP_SENDER
from graph_auth import get_graph_access_token

ROOT_ENV_FILE = Path(__file__).resolve().parent.parent / ".env"
load_dotenv(ROOT_ENV_FILE, override=False)

MAILBOX_UPN = os.environ.get("TEBRA_MAILBOX_UPN", "").strip()
CODE_RE = re.compile(r"\b(\d{6})\b")

def _parse_graph_dt(dt_str: str) -> datetime:
    # Graph returns e.g. "2026-02-20T17:18:02Z" or "...+00:00"
    if not dt_str:
        return None
    s = dt_str.replace("Z", "+00:00")
    return datetime.fromisoformat(s).astimezone(timezone.utc)

def fetch_latest_tebra_otp_code_graph(
    access_token: str,
    *,
    since_dt_utc: datetime,
    poll_seconds=60,
    poll_interval=5,
    page_size=25
) -> str:
    """
    App-only safe implementation:
    - Query recent inbox messages (small page) ordered by receivedDateTime desc
    - Filter locally to avoid Graph InefficientFilter
    - Extract 6-digit code
    - Mark message read
    """

    if since_dt_utc.tzinfo is None:
        since_dt_utc = since_dt_utc.replace(tzinfo=timezone.utc)
    since_dt_utc = since_dt_utc.astimezone(timezone.utc)

    base = "https://graph.microsoft.com/v1.0"
    inbox_url = f"{base}/users/{MAILBOX_UPN}/mailFolders/Inbox/messages"

    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
    }

    params = {
        "$select": "id,subject,from,receivedDateTime,isRead,body",
        "$top": str(page_size),
        "$orderby": "receivedDateTime desc",
    }

    deadline = time.time() + poll_seconds
    last_seen_ids = set()

    def _sender_addr(msg) -> str:
        return (((msg.get("from") or {}).get("emailAddress") or {}).get("address") or "").lower()

    while time.time() < deadline:
        r = requests.get(inbox_url, headers=headers, params=params, timeout=30)
        if r.status_code >= 400:
            raise RuntimeError(f"Graph messages query failed: {r.status_code} {r.text}")

        msgs = r.json().get("value") or []

        for msg in msgs:
            msg_id = msg.get("id")
            if not msg_id or msg_id in last_seen_ids:
                continue

            # Track we've looked at it this polling cycle (prevents reprocessing same one repeatedly)
            last_seen_ids.add(msg_id)

            # Unread only
            if msg.get("isRead") is True:
                continue

            # Sender + subject
            if _sender_addr(msg) != OTP_SENDER.lower():
                continue
            if (msg.get("subject") or "").strip() != OTP_SUBJECT:
                continue

            # Since timestamp
            received = _parse_graph_dt(msg.get("receivedDateTime"))
            if not received or received < since_dt_utc:
                continue

            body = (msg.get("body") or {}).get("content") or ""
            m = CODE_RE.search(body)
            if not m:
                continue

            code = m.group(1)

            # Mark read (best-effort)
            patch_url = f"{base}/users/{MAILBOX_UPN}/messages/{msg_id}"
            patch = requests.patch(patch_url, headers=headers, json={"isRead": True}, timeout=30)
            if patch.status_code >= 400:
                print(f"[OTP] Warning: failed to mark email read: {patch.status_code} {patch.text}")

            print(f"[OTP] Got code={code} received={received.isoformat()}")
            return code

        time.sleep(poll_interval)

    raise RuntimeError(f"[OTP] No unread '{OTP_SUBJECT}' email from '{OTP_SENDER}' found within {poll_seconds}s")

def fetch_latest_tebra_otp_code(*, since_dt_utc, poll_seconds=60) -> str:
    access_token = get_graph_access_token()
    return fetch_latest_tebra_otp_code_graph(
        access_token,
        since_dt_utc=since_dt_utc,
        poll_seconds=poll_seconds,
        poll_interval=5,
        page_size=25
    )