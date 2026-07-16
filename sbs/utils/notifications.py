import os

from typing import List, Dict, Union
from azure.communication.email import EmailClient
from utils.db import get_postgres_connection


# ==========================================================
# CONFIG
# ==========================================================

PROCESS_NAME = "SBS Agent License Update"

# ----------------------------------------------------------
# Teams channel email
# ----------------------------------------------------------
TEAMS_CHANNEL_EMAIL = "7d733b72.enrollinsurance.com@amer.teams.ms"

# ----------------------------------------------------------
# Azure Communication Services (SEPARATE)
# ----------------------------------------------------------

# Teams notifications
ACS_TEAMS_CONNECTION_STRING = (
    os.getenv("ACS_CONNECTION_STRING", "")
)

TEAMS_SENDER_ADDRESS = (
    "DoNotReply@enrollinsurance.com"
)

# Invoice / external email
ACS_EMAIL_CONNECTION_STRING = (
    os.getenv("ACS_CONNECTION_STRING", "")
)

EMAIL_SENDER_ADDRESS = (
    "dataops@834labs.com"
)


# ==========================================================
# EMAIL CORE
# ==========================================================

def _split_recipients(to: Union[str, List[str]]) -> List[Dict[str, str]]:
    if isinstance(to, list):
        parts = to
    else:
        parts = [p.strip() for p in to.replace(";", ",").split(",") if p.strip()]
    return [{"address": addr} for addr in parts]


def send_email(
    connection_string: str,
    sender_address: str,
    to: Union[str, List[str]],
    subject: str,
    body: str
):
    """
    Generic email sender.
    """
    client = EmailClient.from_connection_string(connection_string)

    message = {
        "senderAddress": sender_address,
        "recipients": {
            "to": _split_recipients(to)
        },
        "content": {
            "subject": subject,
            "plainText": body,
            "html": f"<html><body><pre>{body}</pre></body></html>",
        },
    }

    poller = client.begin_send(message)
    poller.result()


# ==========================================================
# DATA FETCH
# ==========================================================

def fetch_matrix_snapshot() -> List[Dict]:
    """
    Fetch current SBS matrix snapshot for notifications.
    """
    query = """
        SELECT
            jurisdiction,
            processed,
            rows_count,
            fee_amount
        FROM wpo.ops_sbs_matrix
        ORDER BY jurisdiction;
    """

    with get_postgres_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(query)
            cols = [desc[0] for desc in cur.description]
            return [dict(zip(cols, row)) for row in cur.fetchall()]


# ==========================================================
# FORMATTING
# ==========================================================

def _format_table(rows: List[Dict]) -> str:
    header = f"{'State':<22} {'Processed':<10} {'Rows':<8} {'Fee ($)':<10}"
    divider = "-" * len(header)

    lines = [header, divider]

    for r in rows:
        processed = "Y" if r["processed"] else "N"
        rows_count = "-" if r["rows_count"] is None else str(r["rows_count"])
        fee = "-" if r["fee_amount"] is None else f"{r['fee_amount']:.2f}"

        lines.append(
            f"{r['jurisdiction']:<22} "
            f"{processed:<10} "
            f"{rows_count:<8} "
            f"{fee:<10}"
        )

    return "\n".join(lines)


def _build_email_body(
    rows: List[Dict],
    from_date: str,
    to_date: str
) -> str:
    total = len(rows)
    processed = sum(1 for r in rows if r["processed"])
    failed = total - processed

    table_text = _format_table(rows)

    return f"""
{PROCESS_NAME} – Run Complete

Date Range Used:
  From: {from_date}
  To  : {to_date}

Summary:
  Total States   : {total}
  Processed     : {processed}
  Not Processed : {failed}

Details:
{table_text}
""".strip()


# ==========================================================
# TEAMS NOTIFICATION
# ==========================================================

def send_end_of_process_notification(
    from_date: str,
    to_date: str
):
    """
    Send final SBS process notification to Teams channel.
    """
    rows = fetch_matrix_snapshot()
    body = _build_email_body(rows, from_date, to_date)

    subject = f"{PROCESS_NAME} – Run Completed ({to_date})"

    send_email(
        connection_string=ACS_TEAMS_CONNECTION_STRING,
        sender_address=TEAMS_SENDER_ADDRESS,
        to=TEAMS_CHANNEL_EMAIL,
        subject=subject,
        body=body
    )


# ==========================================================
# INVOICE EMAIL
# ==========================================================

def send_invoice_email():
    """
    Send invoice summary email.
    """
    query = """
        SELECT COALESCE(SUM(fee_amount), 0)
        FROM wpo.ops_sbs_matrix
        WHERE fee_amount IS NOT NULL;
    """

    with get_postgres_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(query)
            total_fee = cur.fetchone()[0]

    body = f"""
SBS Agent License Update – Invoice

Total Amount Charged : ${total_fee:.2f}

This amount represents the total fees incurred
across all processed jurisdictions for this run.
""".strip()

    send_email(
        connection_string=ACS_EMAIL_CONNECTION_STRING,
        sender_address=EMAIL_SENDER_ADDRESS,
        to="spend@834labs.com",
        subject="SBS Invoice",
        body=body
    )
