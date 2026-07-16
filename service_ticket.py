import os
import psycopg2
from psycopg2.extras import RealDictCursor
from datetime import datetime, timezone
import argparse
import json
import re

from azure.identity import DefaultAzureCredential, ClientSecretCredential
from azure.keyvault.secrets import SecretClient


# =========================
# ARGUMENT PARSING
# =========================
def parse_args():
    parser = argparse.ArgumentParser(description="Create CRM Service Ticket")
    parser.add_argument("--subject", required=True)
    parser.add_argument("--body", required=False)
    parser.add_argument("--sender", required=True)
    parser.add_argument("--has_attachments", required=True)
    return parser.parse_args()


args = parse_args()

subject = args.subject
description = args.body or ""
created_by = args.sender
has_attachments = args.has_attachments.strip().lower() == "true"


# =========================
# KEY VAULT + POSTGRES CONFIG
# =========================
KEY_VAULT_URL = os.getenv("KEYVAULT_URL", "")

POSTGRES_CONFIG = {
    "host": os.getenv("DEFAULT834_DB_HOST", ""),
    "database": os.getenv("DEFAULT834_DB_NAME", ""),
    "user": os.getenv("DEFAULT834_DB_USER", ""),
}

ENTITY_ID = 270681372
SUB_ENTITY_ID = 270681372001


# =========================
# HELPERS
# =========================
def extract_ticket_number(text: str) -> str | None:
    """Pull TCK-XXX from subject if it exists."""
    match = re.search(r'TCK-\d+', text, re.IGNORECASE)
    return match.group(0).upper() if match else None


# =========================
# POSTGRES CONNECTION
# =========================
def get_postgres_connection():
    credential = DefaultAzureCredential()
    client = SecretClient(vault_url=KEY_VAULT_URL, credential=credential)

    client_id = client.get_secret(os.getenv("KEYVAULT_CLIENT_ID_SECRET_NAME", "")).value
    client_secret = client.get_secret(os.getenv("KEYVAULT_CLIENT_SECRET_NAME", "")).value
    tenant_id = client.get_secret(os.getenv("KEYVAULT_TENANT_ID_SECRET_NAME", "")).value

    sp_credential = ClientSecretCredential(
        tenant_id=tenant_id,
        client_id=client_id,
        client_secret=client_secret
    )

    token = sp_credential.get_token(
        "https://ossrdbms-aad.database.windows.net/.default"
    ).token

    return psycopg2.connect(
        host=POSTGRES_CONFIG["host"],
        dbname=POSTGRES_CONFIG["database"],
        user=POSTGRES_CONFIG["user"],
        password=token,
        sslmode="require"
    )


# =========================
# MAIN LOGIC
# =========================
conn = get_postgres_connection()

try:
    with conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:

            # ── Detect if this is a reply to an existing ticket ──────────────
            existing_ticket_id = extract_ticket_number(subject)
            is_response = False
            insert_status = "Open"

            if existing_ticket_id:
                # Verify the ticket actually exists in the table
                cur.execute("""
                    SELECT ticket_id, status
                    FROM wpo.crm_tickets
                    WHERE ticket_id = %s
                    ORDER BY created_at DESC
                    LIMIT 1;
                """, (existing_ticket_id,))

                found = cur.fetchone()
                if found:
                    is_response = True
                    ticket_id = existing_ticket_id
                    ticket_type = "Support - Response"

                    current_status = found["status"]
                    new_status = "Reopened" if current_status == "Closed" else current_status
                    insert_status = new_status

                    # Update last_updated and reopen if closed on all rows for this ticket
                    cur.execute("""
                        UPDATE wpo.crm_tickets
                        SET
                            last_updated = now(),
                            status = %s
                        WHERE ticket_id = %s;
                    """, (new_status, ticket_id))

                    print(f"Reply detected — linking to existing ticket: {ticket_id}")
                    if new_status == "Reopened":
                        print(f"Ticket was Closed — status set to Reopened")
                else:
                    # TCK number in subject but not in DB — treat as new
                    print(f"Warning: {existing_ticket_id} found in subject but not in DB — creating new ticket")

            # ── If new ticket, auto-increment ────────────────────────────────
            if not is_response:
                cur.execute("""
                    SELECT ticket_id
                    FROM wpo.crm_tickets
                    ORDER BY created_at DESC
                    LIMIT 1;
                """)
                row = cur.fetchone()
                last_number = int(row["ticket_id"].replace("TCK-", "")) if row and row.get("ticket_id") else 0
                ticket_id = f"TCK-{last_number + 1}"
                ticket_type = "Support - New"
                insert_status = "Open"

            # ── Round Robin Owner Selection ──────────────────────────────────
            cur.execute("""
                SELECT
                    ctu.pk_id,
                    ctu.user_id,
                    u.email AS owner_email
                FROM wpo.crm_tickets_users ctu
                JOIN ops_sec.users u ON u.user_id = ctu.user_id
                WHERE ctu.status = 'Active'
                ORDER BY ctu.time_stamp ASC
                LIMIT 1;
            """)

            rr = cur.fetchone()
            if not rr:
                raise Exception("No active users found for round robin assignment")

            owner_pk = rr["pk_id"]
            owner_email = rr["owner_email"]

            # Bump user to back of queue
            cur.execute("""
                UPDATE wpo.crm_tickets_users
                SET time_stamp = now()
                WHERE pk_id = %s;
            """, (owner_pk,))

            # ── Insert row ───────────────────────────────────────────────────
            cur.execute("""
                INSERT INTO wpo.crm_tickets (
                    ticket_id,
                    subject,
                    description,
                    type,
                    status,
                    created_at,
                    created_by,
                    owner,
                    attachment,
                    entity_id,
                    sub_entity_id
                )
                VALUES (
                    %s, %s, %s,
                    %s, %s,
                    %s, %s,
                    %s,
                    %s::jsonb,
                    %s, %s
                );
            """, (
                ticket_id,
                subject,
                description,
                ticket_type,
                insert_status,
                datetime.now(timezone.utc),
                created_by,
                owner_email,
                json.dumps(has_attachments),
                ENTITY_ID,
                SUB_ENTITY_ID
            ))

            print(f"Ticket row created: {ticket_id} | Type: {ticket_type} | Status: {insert_status}")
            print(f"Assigned to: {owner_email}")

finally:
    conn.close()