"""
Read recent emails from a mailbox.

Usage:
    python read_emails.py
    python read_emails.py --mailbox "support@enrollinsurance.com" --count 20
    python read_emails.py --unread-only
    python read_emails.py --sender "account-noreply@ngic.com"
    python read_emails.py --subject "One Time Password"
"""

import argparse
import requests
from graph_auth import get_graph_access_token


def read_emails(mailbox="support@enrollinsurance.com", count=10, unread_only=False, sender=None, subject=None):
    token = get_graph_access_token()
    base = "https://graph.microsoft.com/v1.0"
    url = f"{base}/users/{mailbox}/mailFolders/Inbox/messages"

    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    params = {
        "$select": "id,subject,from,receivedDateTime,isRead,bodyPreview,hasAttachments,body",
        "$top": str(count),
        "$orderby": "receivedDateTime desc",
    }

    if unread_only:
        params["$filter"] = "isRead eq false"

    r = requests.get(url, headers=headers, params=params, timeout=30)
    if r.status_code >= 400:
        print(f"Error: {r.status_code} {r.text[:300]}")
        return

    msgs = r.json().get("value", [])

    # Local filtering
    if sender:
        msgs = [m for m in msgs if sender.lower() in
                (((m.get("from") or {}).get("emailAddress") or {}).get("address") or "").lower()]
    if subject:
        msgs = [m for m in msgs if subject.lower() in (m.get("subject") or "").lower()]

    print(f"\n{'='*70}")
    print(f"  {mailbox} — {len(msgs)} emails")
    filters = []
    if unread_only: filters.append("unread only")
    if sender: filters.append(f"sender={sender}")
    if subject: filters.append(f"subject contains '{subject}'")
    if filters:
        print(f"  Filters: {', '.join(filters)}")
    print(f"{'='*70}\n")

    for i, msg in enumerate(msgs, 1):
        msg_sender = (((msg.get("from") or {}).get("emailAddress") or {}).get("address") or "")
        msg_subject = msg.get("subject", "")
        received = (msg.get("receivedDateTime") or "")[:19]
        read = "read" if msg.get("isRead") else "UNREAD"
        attach = " [attachments]" if msg.get("hasAttachments") else ""
        preview = (msg.get("bodyPreview") or "")[:120]

        print(f"  [{i}] {received}  {read}{attach}")
        print(f"      From:    {msg_sender}")
        print(f"      Subject: {msg_subject}")
        if preview:
            print(f"      Preview: {preview}")

        body_content = (msg.get("body") or {}).get("content") or ""
        if body_content:
            import re
            body_text = re.sub(r'<[^>]+>', ' ', body_content)
            body_text = ' '.join(body_text.split()).strip()
            print(f"      Body:    {body_text[:500]}")
        print()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Read recent emails from a mailbox")
    parser.add_argument("--mailbox", default="support@enrollinsurance.com")
    parser.add_argument("--count", type=int, default=10)
    parser.add_argument("--unread-only", action="store_true")
    parser.add_argument("--sender", default=None, help="Filter by sender email")
    parser.add_argument("--subject", default=None, help="Filter by subject (partial match)")
    args = parser.parse_args()

    read_emails(
        mailbox=args.mailbox,
        count=args.count,
        unread_only=args.unread_only,
        sender=args.sender,
        subject=args.subject,
    )