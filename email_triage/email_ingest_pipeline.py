import os
"""
email_ingest_pipeline.py

Cron job (every 3 min) that:
  1. Reads last_pull.txt for cutoff datetime
  2. Polls Graph API for new emails in dataops@834labs.com
  3. For each new email:
     - If TCK-XXX in subject -> reply -> insert into tickets_email_threads
     - If no TCK-XXX -> classify via API -> new ticket -> crm_tickets + tickets_email_store
     - Uploads attachments to agilitydatadev001/agilityops/emails/{email_store_pk}/
     - Looks up sender in wpo.lup_agents (all email columns) -> populates agent_pk_id, npn
  4. Updates last_pull.txt

FIXES APPLIED:
  1. NPN sourced from wpo.lup_agents.npn (was ops_sec.users.agent_npn)
  2. ticket_id left to DB sequence (no explicit value passed)
  3. owner column receives user_id (UUID) from round-robin (was email)
  4. user_uuid column removed from INSERT (column dropped from table)
  5. agent_pk_id sourced from wpo.lup_agents.pk_id (was ops_sec.users.user_id)
  6. Agent lookup checks all email columns: email, secondary_email, contracting_email, accounting_email
  7. agent_email stores whichever email column matched the sender
  8. created_by receives agent_pk_id (UUID from lup_agents) — sender's identity; None if not found
  9. timeout=30 added to all requests.get calls to prevent zombie hangs
 10. Classification payload expanded to {sender_email, receiver_email, subject, description}:
        - sender_email   = parsed["sender"]      (from address)
        - receiver_email = parsed["recipients"]  (to address)
        - subject        = parsed["subject"]
        - description    = parsed["body"]         (full body, no truncation)
"""

import psycopg2
from psycopg2.extras import RealDictCursor
import requests
import json
import re
import base64
import html as html_lib
import uuid
import logging
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

from azure.identity import DefaultAzureCredential, ClientSecretCredential
from azure.keyvault.secrets import SecretClient
from azure.storage.blob import BlobServiceClient


# =============================================================================
# LOGGING
# =============================================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("email_ingest_pipeline.log", mode="a"),
    ],
)
log = logging.getLogger(__name__)


# =============================================================================
# CONFIG
# =============================================================================

KEY_VAULT_URL = os.getenv("KEYVAULT_URL", "")

POSTGRES_CONFIG = {
    "host": os.getenv("DEFAULT834_DB_HOST", ""),
    "database": os.getenv("DEFAULT834_DB_NAME", ""),
    "user": os.getenv("DEFAULT834_DB_USER", ""),
}

MAILBOX = "dataops@834labs.com"
GRAPH_API_BASE = "https://graph.microsoft.com/v1.0"

BLOB_ACCOUNT_URL = "https://agilitydatadev001.blob.core.windows.net"
BLOB_CONTAINER = "agilityops"
BLOB_EMAILS_PREFIX = "emails"

CLASSIFY_API_URL = (
    "https://834-fun-dev001-g7dngcg3byewg2bv.centralus-01.azurewebsites.net"
    "/api/email/classify"
)
CLASSIFY_TIMEOUT = 30  # seconds

HTTP_TIMEOUT = 30  # seconds — applied to all Graph API calls

ENTITY_ID = 270681372
SUB_ENTITY_ID = 270681372001

LAST_PULL_FILE = r"C:\Users\myopsadmin\Documents\last_pull.txt"
DEFAULT_LOOKBACK_MINUTES = 5


# =============================================================================
# AUTH
# =============================================================================

def get_keyvault_client():
    credential = DefaultAzureCredential()
    return SecretClient(vault_url=KEY_VAULT_URL, credential=credential)


def get_sp_credential(kv_client):
    client_id = kv_client.get_secret(os.getenv("KEYVAULT_CLIENT_ID_SECRET_NAME", "")).value
    client_secret = kv_client.get_secret(os.getenv("KEYVAULT_CLIENT_SECRET_NAME", "")).value
    tenant_id = kv_client.get_secret(os.getenv("KEYVAULT_TENANT_ID_SECRET_NAME", "")).value

    return ClientSecretCredential(
        tenant_id=tenant_id,
        client_id=client_id,
        client_secret=client_secret,
    ), tenant_id, client_id, client_secret


def get_postgres_connection(sp_credential):
    token = sp_credential.get_token(
        "https://ossrdbms-aad.database.windows.net/.default"
    ).token

    return psycopg2.connect(
        host=POSTGRES_CONFIG["host"],
        dbname=POSTGRES_CONFIG["database"],
        user=POSTGRES_CONFIG["user"],
        password=token,
        sslmode="require",
    )


def get_graph_token(tenant_id, client_id, client_secret):
    url = f"https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/token"
    data = {
        "grant_type": "client_credentials",
        "client_id": client_id,
        "client_secret": client_secret,
        "scope": "https://graph.microsoft.com/.default",
    }
    resp = requests.post(url, data=data, timeout=HTTP_TIMEOUT)
    resp.raise_for_status()
    return resp.json()["access_token"]


def get_blob_service_client(sp_credential):
    return BlobServiceClient(account_url=BLOB_ACCOUNT_URL, credential=sp_credential)


# =============================================================================
# EMAIL CLASSIFICATION
# =============================================================================

def classify_email(sender_email, receiver_email, subject, description):
    """
    Call the classification API to determine ticket type.
    Returns the category string (e.g. 'Junk', 'Review', etc.)
    Falls back to 'Review' on any failure.

    Payload:
        sender_email   - from address
        receiver_email - to address (recipients)
        subject        - email subject
        description    - email body (full)
    """
    try:
        resp = requests.post(
            CLASSIFY_API_URL,
            json={
                "sender_email": sender_email,
                "receiver_email": receiver_email,
                "subject": subject,
                "description": description,
            },
            headers={"Content-Type": "application/json", "Accept": "application/json"},
            timeout=CLASSIFY_TIMEOUT,
        )
        resp.raise_for_status()
        result = resp.json()
        category = result.get("category", "Review")
        confidence = result.get("confidence", 0.0)
        log.info(f"  Classification: {category} (confidence: {confidence:.4f})")
        return category
    except Exception as e:
        log.warning(f"  Classification API failed, defaulting to 'Review': {e}")
        return "Review"


# =============================================================================
# LAST PULL TRACKING
# =============================================================================

def read_last_pull():
    path = Path(LAST_PULL_FILE)
    if path.exists():
        text = path.read_text().strip()
        if text:
            return datetime.fromisoformat(text)
    return datetime.now(timezone.utc) - timedelta(minutes=DEFAULT_LOOKBACK_MINUTES)


def write_last_pull(dt):
    # Add 1 second buffer to avoid sub-second precision re-fetch
    buffered = dt + timedelta(seconds=1)
    Path(LAST_PULL_FILE).write_text(buffered.isoformat())
    log.info(f"last_pull.txt -> {buffered.isoformat()}")


# =============================================================================
# GRAPH API
# =============================================================================

def fetch_new_emails(graph_token, since_dt):
    since_str = since_dt.strftime("%Y-%m-%dT%H:%M:%SZ")

    url = (
        f"{GRAPH_API_BASE}/users/{MAILBOX}/messages"
        f"?$filter=receivedDateTime gt {since_str}"
        f"&$orderby=receivedDateTime asc"
        f"&$top=50"
        f"&$select=id,subject,from,toRecipients,ccRecipients,bccRecipients,"
        f"body,receivedDateTime,hasAttachments"
    )

    headers = {"Authorization": f"Bearer {graph_token}"}
    all_emails = []

    while url:
        resp = requests.get(url, headers=headers, timeout=HTTP_TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
        all_emails.extend(data.get("value", []))
        url = data.get("@odata.nextLink")

    log.info(f"Fetched {len(all_emails)} emails since {since_str}")
    return all_emails


def fetch_attachments(graph_token, message_id):
    url = f"{GRAPH_API_BASE}/users/{MAILBOX}/messages/{message_id}/attachments"
    headers = {"Authorization": f"Bearer {graph_token}"}

    resp = requests.get(url, headers=headers, timeout=HTTP_TIMEOUT)
    resp.raise_for_status()
    return resp.json().get("value", [])


# =============================================================================
# HELPERS
# =============================================================================

def extract_ticket_number(subject):
    """Pull TCK-XXX from subject if present."""
    match = re.search(r"TCK-\d+", subject or "", re.IGNORECASE)
    return match.group(0).upper() if match else None


def strip_html(text):
    """Convert HTML email body to clean plain text."""
    if not text:
        return ""
    clean = re.sub(r"<(style|script)[^>]*>.*?</\1>", "", text, flags=re.DOTALL | re.IGNORECASE)
    clean = re.sub(r"<br\s*/?>", "\n", clean, flags=re.IGNORECASE)
    clean = re.sub(r"</(p|div|tr)>", "\n", clean, flags=re.IGNORECASE)
    clean = re.sub(r"<[^>]+>", "", clean)
    clean = html_lib.unescape(clean)
    clean = re.sub(r"[ \t]+", " ", clean)
    clean = re.sub(r"\n\s*\n+", "\n", clean)
    return clean.strip()


def parse_email(email):
    sender_raw = email.get("from", {}).get("emailAddress", {})
    sender_email = (sender_raw.get("address") or "").strip().lower()

    def recipients(field):
        return ", ".join(
            r.get("emailAddress", {}).get("address", "")
            for r in email.get(field, [])
        )

    body_obj = email.get("body", {})
    body_raw = (body_obj.get("content") or "").strip()
    body_format = body_obj.get("contentType", "text")

    body_clean = strip_html(body_raw) if body_format.lower() == "html" else body_raw

    return {
        "message_id": email["id"],
        "subject": (email.get("subject") or "").strip(),
        "sender": sender_email,
        "recipients": recipients("toRecipients"),
        "cc": recipients("ccRecipients"),
        "bcc": recipients("bccRecipients"),
        "body": body_clean,
        "body_format": body_format,
        "received_datetime": email.get("receivedDateTime"),
        "has_attachments": email.get("hasAttachments", False),
    }


# FIX #3: Round robin returns user_id (UUID) for the owner column
def get_round_robin_owner(cur):
    cur.execute("""
        SELECT ctu.pk_id, ctu.user_id, u.email AS owner_email
        FROM wpo.crm_tickets_users ctu
        JOIN ops_sec.users u ON u.user_id = ctu.user_id
        WHERE ctu.status = 'Active'
        ORDER BY ctu.time_stamp ASC
        LIMIT 1;
    """)
    rr = cur.fetchone()
    if not rr:
        raise Exception("No active users for round robin assignment")

    cur.execute("""
        UPDATE wpo.crm_tickets_users SET time_stamp = now()
        WHERE pk_id = %s;
    """, (rr["pk_id"],))

    return rr["user_id"]


# FIX #1 + #5: Agent lookup now uses wpo.lup_agents for pk_id and npn
# FIX #6: Checks all email columns (email, secondary_email, contracting_email, accounting_email)
# FIX #7: Stores whichever email column matched the sender
def lookup_agent(cur, sender_email):
    """Look up sender in wpo.lup_agents checking all email columns.
    Returns agent_pk_id (lup_agents.pk_id), npn (lup_agents.npn),
    and agent_email (whichever email column matched).
    NPN is optional — left None if not found.
    """
    sender_lower = sender_email.lower()
    cur.execute("""
        SELECT la.pk_id, la.npn,
               la.email, la.secondary_email,
               la.contracting_email, la.accounting_email
        FROM wpo.lup_agents la
        WHERE LOWER(la.email) = %s
           OR LOWER(la.secondary_email) = %s
           OR LOWER(la.contracting_email) = %s
           OR LOWER(la.accounting_email) = %s
        LIMIT 1;
    """, (sender_lower, sender_lower, sender_lower, sender_lower))

    row = cur.fetchone()
    if row:
        # Determine which email column matched
        matched_email = sender_lower
        for col in ('email', 'secondary_email', 'contracting_email', 'accounting_email'):
            if row[col] and row[col].strip().lower() == sender_lower:
                matched_email = row[col].strip()
                break

        log.info(
            f"  Agent match: {matched_email} "
            f"(agent_pk_id: {row['pk_id']}, npn: {row['npn'] or 'N/A'})"
        )
        return {
            "agent_pk_id": row["pk_id"],       # from lup_agents.pk_id
            "agent_email": matched_email,       # whichever email column matched
            "npn": row["npn"],                  # from lup_agents.npn (may be None)
        }

    log.info(f"  No agent match for {sender_email}")
    return None


# =============================================================================
# INSERT tickets_email_store (new ticket emails)
# =============================================================================

def insert_email_store(cur, parsed, ticket_pk, agent_info=None):
    # Safety: never store replies (with TCK-XXX) in email_store
    if re.search(r"TCK-\d+", parsed.get("subject", ""), re.IGNORECASE):
        log.warning(f"  Blocked reply from tickets_email_store: {parsed['subject'][:60]}")
        return None

    cur.execute("""
        INSERT INTO wpo.tickets_email_store (
            sender, recipients, cc, bcc,
            subject, body, body_format,
            email_type, sent_datetime,
            attachments, status, ticket_id,
            message_id,
            agent_id, npn
        )
        VALUES (
            %s, %s, %s, %s,
            %s, %s, %s,
            %s, %s,
            NULL, %s, %s,
            %s,
            %s, %s
        )
        RETURNING pk_id;
    """, (
        parsed["sender"],
        parsed["recipients"],
        parsed["cc"],
        parsed["bcc"],
        parsed["subject"],
        parsed["body"],
        parsed["body_format"],
        "Service Ticket",
        parsed["received_datetime"],
        "processed",
        ticket_pk,
        parsed["message_id"],
        agent_info["agent_pk_id"] if agent_info else None,
        agent_info["npn"] if agent_info else None,
    ))

    return cur.fetchone()["pk_id"]


# =============================================================================
# INSERT crm_tickets (new tickets only)
#
# FIX #1: npn sourced from wpo.lup_agents (not ops_sec.users)
# FIX #2: ticket_id omitted — DB sequence generates it automatically
# FIX #3: owner receives user_id (UUID) from round-robin
# FIX #4: user_uuid column removed — not in INSERT
# FIX #5: agent_pk_id sourced from wpo.lup_agents.pk_id
# FIX #8: created_by receives agent_pk_id (UUID) — who sent the email; None if unknown
# =============================================================================

def create_ticket(cur, parsed, owner_user_id, ticket_type="Review", agent_info=None):
    cur.execute("""
        INSERT INTO wpo.crm_tickets (
            subject, description,
            type, status,
            created_at, created_by, owner,
            attachment,
            entity_id, sub_entity_id,
            source,
            agent_email, agent_pk_id, npn
        )
        VALUES (
            %s, %s,
            %s, %s,
            %s, %s, %s,
            %s::jsonb,
            %s, %s,
            %s,
            %s, %s, %s
        )
        RETURNING pk_id, ticket_id;
    """, (
        parsed["subject"],
        parsed["body"],
        ticket_type,
        "Open",
        datetime.now(timezone.utc),
        agent_info["agent_pk_id"] if agent_info else None,      # created_by (uuid) — who sent the email
        owner_user_id,                                          # owner (uuid) — round-robin assignee
        json.dumps(parsed["has_attachments"]),
        ENTITY_ID,
        SUB_ENTITY_ID,
        "email_ingest_pipeline",
        agent_info["agent_email"] if agent_info else None,      # agent_email (varchar) — matched email
        agent_info["agent_pk_id"] if agent_info else None,      # agent_pk_id (uuid) — lup_agents.pk_id
        agent_info["npn"] if agent_info else None,              # npn (varchar) — lup_agents.npn
    ))

    row = cur.fetchone()
    return row["pk_id"], row["ticket_id"]


# =============================================================================
# REPLY HANDLING — tickets_email_threads
# =============================================================================

def find_parent_email(cur, ticket_id):
    """Find the parent email_store pk_id for a given ticket_id (e.g. TCK-244)."""
    cur.execute("""
        SELECT es.pk_id
        FROM wpo.tickets_email_store es
        JOIN wpo.crm_tickets t ON es.ticket_id = t.pk_id
        WHERE t.ticket_id = %s
        ORDER BY es.sent_datetime ASC
        LIMIT 1;
    """, (ticket_id,))

    row = cur.fetchone()
    return row["pk_id"] if row else None


def insert_email_thread(cur, parsed, parent_email_id):
    """Insert a reply into tickets_email_threads."""
    cur.execute("""
        INSERT INTO wpo.tickets_email_threads (
            parent_email_id,
            sender, recipients, cc, bcc,
            subject, body, body_format,
            sent_datetime,
            attachments, status,
            message_id
        )
        VALUES (
            %s,
            %s, %s, %s, %s,
            %s, %s, %s,
            %s,
            NULL, %s,
            %s
        )
        RETURNING pk_id;
    """, (
        parent_email_id,
        parsed["sender"],
        parsed["recipients"],
        parsed["cc"],
        parsed["bcc"],
        parsed["subject"],
        parsed["body"],
        parsed["body_format"],
        parsed["received_datetime"],
        "processed",
        parsed["message_id"],
    ))

    return cur.fetchone()["pk_id"]


def update_ticket_on_reply(cur, ticket_id):
    """Update ticket status on reply — reopen if closed."""
    cur.execute("""
        SELECT status FROM wpo.crm_tickets
        WHERE ticket_id = %s LIMIT 1;
    """, (ticket_id,))

    found = cur.fetchone()
    if not found:
        return

    new_status = "Reopened" if found["status"] == "Closed" else found["status"]

    cur.execute("""
        UPDATE wpo.crm_tickets
        SET last_updated = now(), status = %s
        WHERE ticket_id = %s;
    """, (new_status, ticket_id))

    if new_status == "Reopened":
        log.info(f"  Ticket {ticket_id} reopened")


# =============================================================================
# UPLOAD ATTACHMENTS
# =============================================================================

def upload_attachments(cur, blob_service, graph_token, message_id, folder_pk, update_table, update_pk):
    """
    Upload attachments to emails/{folder_pk}/ and update the specified table/row.
    folder_pk = which blob folder to upload into
    update_table = which table to write attachment metadata to
    update_pk = which row pk_id to update
    """
    attachments = fetch_attachments(graph_token, message_id)

    if not attachments:
        return 0

    container_client = blob_service.get_container_client(BLOB_CONTAINER)
    att_records = []

    for att in attachments:
        if att.get("@odata.type") != "#microsoft.graph.fileAttachment":
            continue

        filename = att.get("name", "unknown")
        content_bytes = base64.b64decode(att.get("contentBytes", ""))
        blob_path = f"{BLOB_EMAILS_PREFIX}/{folder_pk}/{filename}"

        container_client.get_blob_client(blob_path).upload_blob(
            content_bytes, overwrite=True
        )

        att_records.append({
            "id": str(uuid.uuid4()),
            "size": len(content_bytes),
            "blob_path": blob_path,
            "file_name": filename,
            "mime_type": att.get("contentType", "application/octet-stream"),
        })

        log.info(f"  Attachment: {blob_path} ({len(content_bytes)} bytes)")

    if att_records:
        cur.execute(f"""
            UPDATE wpo.{update_table}
            SET attachments = %s::jsonb
            WHERE pk_id = %s;
        """, (json.dumps(att_records), update_pk))

    return len(att_records)


# =============================================================================
# MAIN
# =============================================================================

def run():
    log.info("=" * 60)
    log.info("Email Ingest Pipeline -- starting")
    log.info("=" * 60)

    # Auth
    kv_client = get_keyvault_client()
    sp_credential, tenant_id, client_id, client_secret = get_sp_credential(kv_client)
    conn = get_postgres_connection(sp_credential)
    graph_token = get_graph_token(tenant_id, client_id, client_secret)
    blob_service = get_blob_service_client(sp_credential)

    # Warm up classification API (cold start can take 10-20s)
    try:
        log.info("Warming up classification API...")
        requests.post(
            CLASSIFY_API_URL,
            json={
                "sender_email": "warmup@warmup.com",
                "receiver_email": MAILBOX,
                "subject": "warmup",
                "description": "warmup",
            },
            headers={"Content-Type": "application/json"},
            timeout=CLASSIFY_TIMEOUT,
        )
        log.info("Classification API warm.")
    except Exception as e:
        log.warning(f"Classification API warmup failed: {e}")

    try:
        with conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:

                # Read cutoff
                since_dt = read_last_pull()
                log.info(f"Polling since: {since_dt.isoformat()}")

                # Fetch emails
                emails = fetch_new_emails(graph_token, since_dt)

                if not emails:
                    log.info("No new emails. Done.")
                    return

                processed = 0
                threaded = 0
                skipped = 0
                errors = 0
                latest_received = since_dt

                for email in emails:
                    try:
                        cur.execute("SAVEPOINT sp_email")
                        parsed = parse_email(email)

                        # Track latest received for last_pull.txt
                        received = email.get("receivedDateTime", "")
                        if received:
                            received_dt = datetime.fromisoformat(
                                received.replace("Z", "+00:00")
                            )
                            if received_dt > latest_received:
                                latest_received = received_dt

                        # Check for TCK-XXX in subject
                        ticket_ref = extract_ticket_number(parsed["subject"])

                        # Dedup — check if message_id already processed
                        cur.execute("""
                            SELECT 1 FROM wpo.tickets_email_store WHERE message_id = %s
                            UNION ALL
                            SELECT 1 FROM wpo.tickets_email_threads WHERE message_id = %s
                            LIMIT 1;
                        """, (parsed["message_id"], parsed["message_id"]))

                        if cur.fetchone():
                            log.info(f"  Skipped duplicate: {parsed['subject'][:60]}")
                            skipped += 1
                            continue

                        if ticket_ref:
                            # ── REPLY: thread it ──────────────────────
                            parent_email_id = find_parent_email(cur, ticket_ref)

                            if not parent_email_id:
                                log.warning(
                                    f"  Reply to {ticket_ref} but no parent email found "
                                    f"-- skipping: {parsed['subject'][:60]}"
                                )
                                skipped += 1
                                continue

                            # Insert thread row
                            thread_pk = insert_email_thread(cur, parsed, parent_email_id)

                            # Upload attachments to parent's folder, update thread row
                            att_count = 0
                            if parsed["has_attachments"]:
                                att_count = upload_attachments(
                                    cur, blob_service, graph_token,
                                    parsed["message_id"],
                                    folder_pk=parent_email_id,
                                    update_table="tickets_email_threads",
                                    update_pk=thread_pk,
                                )

                            # Update ticket status
                            update_ticket_on_reply(cur, ticket_ref)

                            threaded += 1
                            log.info(
                                f"  Reply -> {ticket_ref} | {parsed['sender']} "
                                f"| thread_pk={thread_pk} | attachments: {att_count}"
                            )

                        else:
                            # ── NEW EMAIL ─────────────────────────────

                            # Only process emails with "service ticket" in subject
                            if not re.search(r"service\s*ticket", parsed["subject"], re.IGNORECASE):
                                log.info(f"  Skipped (no service ticket tag): {parsed['subject'][:60]}")
                                skipped += 1
                                continue

                            # Classify email via API
                            ticket_type = classify_email(
                                sender_email=parsed["sender"],
                                receiver_email=parsed["recipients"],
                                subject=parsed["subject"],
                                description=parsed["body"],
                            )

                            # Agent lookup from wpo.lup_agents (all email columns)
                            agent_info = lookup_agent(cur, parsed["sender"])

                            # Round-robin owner as user_id (UUID)
                            owner_user_id = get_round_robin_owner(cur)

                            # Create ticket — ticket_id generated by DB sequence
                            # created_by = agent_pk_id (who sent the email), None if unknown
                            ticket_pk, ticket_id = create_ticket(
                                cur, parsed, owner_user_id,
                                ticket_type=ticket_type,
                                agent_info=agent_info,
                            )

                            # Insert tickets_email_store with agent info
                            email_store_pk = insert_email_store(
                                cur, parsed, ticket_pk,
                                agent_info=agent_info,
                            )

                            if not email_store_pk:
                                skipped += 1
                                continue

                            # Upload attachments
                            att_count = 0
                            if parsed["has_attachments"]:
                                att_count = upload_attachments(
                                    cur, blob_service, graph_token,
                                    parsed["message_id"],
                                    folder_pk=email_store_pk,
                                    update_table="tickets_email_store",
                                    update_pk=email_store_pk,
                                )

                            processed += 1
                            log.info(
                                f"  {ticket_id} | {parsed['sender']} "
                                f"| type: {ticket_type} "
                                f"| agent: {agent_info['agent_email'] if agent_info else 'N/A'} "
                                f"| npn: {agent_info['npn'] if agent_info else 'N/A'} "
                                f"| {parsed['subject'][:50]} "
                                f"| owner: {owner_user_id} | attachments: {att_count}"
                            )

                        cur.execute("RELEASE SAVEPOINT sp_email")

                    except Exception as e:
                        cur.execute("ROLLBACK TO SAVEPOINT sp_email")
                        errors += 1
                        log.error(f"Error: {e}", exc_info=True)

                # Update last pull
                write_last_pull(latest_received)

                log.info(
                    f"Complete: {processed} new, {threaded} replies, "
                    f"{skipped} skipped, {errors} errors"
                )

    finally:
        conn.close()
        log.info("Done.\n")


if __name__ == "__main__":
    run()