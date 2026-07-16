#agility_tools

import json
import asyncio
from typing import Set, Dict, Any, List, Optional
from unittest import result
from azure.ai.agents.models import FunctionTool
import sys
from pathlib import Path
import logging
from sqlalchemy import func
import ast
from sqlalchemy.inspection import inspect
import uuid
from decimal import Decimal
import time
from azure.communication.email import EmailClient
import os
from datetime import datetime, UTC, timedelta
import random

# # ensure project root is on sys.path so `import app` works when running this file directly
sys.path.append(str(Path(__file__).resolve().parents[3]))

from app.models.AiChatHistory import AIChatHistory
from app.db.session import get_db
from app.models import CrmTicket
from app.core.config import settings
logger = logging.getLogger(__name__)

TABLE_NAME = "wpo.lup_agents"
NAME_COLUMN = "full_name"
NPN_COLUMN = "npn"
EMAIL_COLUMN = "email"
TABLE_NAME_HISTORY = 'ai_chat_history'
ACS_EMAIL_CONNECTION_STRING = settings.ACS_EMAIL_CONNECTION_STRING
ACS_SENDER_EMAIL = "dataops@834labs.com"

TEST_AGENT_DIRECTORY: Dict[str, Dict[str, str]] = {
    "5111": {"npn": "5111", "full_name": "Ijya sharma", "email": "isharma@834labs.com","status": "Active"},
    "3111": {"npn": "3111", "full_name": "Karan Chettri", "email": "Karan@834labs.com", "status": "Active"},
    "0007": {"npn": "0007", "full_name": "Timm Cox", "email": "tcox@834labs.com", "status": "Active"},
    "1234": {"npn": "1234", "full_name": "Liz Choi", "email": "echoi@834labs.com","status": "Active"},
    "2345": {"npn": "2345", "full_name": "Jithu Poorna", "email": "JPoorna@834labs.com","status": "Active"},
    "5112": {"npn": "5111", "full_name": "Ijya sharma", "email": "isharma@834labs.com","status": "Suspended"},
    "3112": {"npn": "3111", "full_name": "Karan Chettri", "email": "Karan@834labs.com", "status": "Suspended"},
    "0008": {"npn": "0007", "full_name": "Timm Cox", "email": "tcox@834labs.com", "status": "Quarantined"},
    "1235": {"npn": "1234", "full_name": "Liz Choi", "email": "echoi@834labs.com","status": "Quarantined"},
    "2346": {"npn": "2345", "full_name": "Jithu Poorna", "email": "JPoorna@834labs.com","status": "Quarantined"}
}

OTP_TTL_SECONDS = 30 * 60  # 30 minutes



def _utcnow() -> datetime:
    return datetime.now(UTC)



def _open_db_session():
    gen = get_db()
    db = next(gen)
    return db, gen



def end_conversation() -> str:
    return json.dumps({"ended": True})

def check_agent_by_npn(npn: str) -> str:
    npn = (npn or "").strip()
    if not npn:
        return json.dumps({"exists": False, "error": "npn_missing"})

    if npn in TEST_AGENT_DIRECTORY:
        return json.dumps({"exists": True, "source": "test_dict"})

    db, gen = _open_db_session()
    try:
        q = text(f"SELECT EXISTS (SELECT 1 FROM {TABLE_NAME} WHERE {NPN_COLUMN} = :npn) AS exists;")
        row = db.execute(q, {"npn": npn}).fetchone()
        exists = bool(row[0]) if row else False
        return json.dumps({"exists": exists, "source": "db"})
    except Exception:
        return json.dumps({"exists": False, "error": "db_query_failed"})
    finally:
        db.close()
        try:
            gen.close()
        except Exception:
            pass

def get_agent_by_npn(npn: str) -> str:
    npn = (npn or "").strip()
    if not npn:
        return json.dumps({"found": False, "error": "npn_missing"})

    if npn in TEST_AGENT_DIRECTORY:
        a = TEST_AGENT_DIRECTORY[npn]
        return json.dumps({
            "found": True,
            "npn": npn,
            "full_name": (a.get("full_name") or "").strip(),
            "email": (a.get("email") or "").strip(),
            "source": "test_dict",
        })

    db, gen = _open_db_session()
    try:
        q = text(
            f"SELECT {NAME_COLUMN} AS full_name, {EMAIL_COLUMN} AS email "
            f"FROM {TABLE_NAME} WHERE {NPN_COLUMN} = :npn LIMIT 1;"
        )
        row = db.execute(q, {"npn": npn}).fetchone()
        if not row:
            return json.dumps({"found": False})

        full_name = (row[0] or "").strip() if isinstance(row[0], str) else (str(row[0]).strip() if row[0] else "")
        email = (row[1] or "").strip() if isinstance(row[1], str) else (str(row[1]).strip() if row[1] else "")

        if not email:
            return json.dumps({"found": False, "error": "email_not_found"})

        return json.dumps({"found": True, "npn": npn, "full_name": full_name, "email": email, "source": "db"})
    except Exception:
        return json.dumps({"found": False, "error": "db_query_failed"})
    finally:
        db.close()
        try:
            gen.close()
        except Exception:
            pass


def send_email_acs(*, sender: str, to_email: str, subject: str, html_body: str):

    if not ACS_EMAIL_CONNECTION_STRING:
        raise RuntimeError("ACS_EMAIL_CONNECTION_STRING is not set")

    try:
        client = EmailClient.from_connection_string(
            ACS_EMAIL_CONNECTION_STRING
        )

        message = {
            "senderAddress": sender,
            "recipients": {
                "to": [{"address": to_email}]
            },
            "content": {
                "subject": subject,
                "html": html_body,
            },
        }

        poller = client.begin_send(message)
        result = poller.result()

        message_id = getattr(result, "id", None)

        return {
            "status": "email_sent",
            "message_id": message_id,
        }

    except Exception as e:
        logger.exception("ACS email failed")
        return {
            "status": "error",
            "error": str(e)
        }

def send_verification_code(npn: str, session_id: str = "") -> str:
    """
    Sends OTP to email from get_agent_by_npn(npn)
    Stores OTP in DB per session_id
    """
    npn = (npn or "").strip()
    if not npn:
        return json.dumps({"sent": False, "error": "npn_missing"})

    agent_json = json.loads(get_agent_by_npn(npn))
    if not agent_json.get("found"):
        return json.dumps({"sent": False, "error": "agent_not_found"})

    receiver = (agent_json.get("email") or "").strip()
    if not receiver:
        return json.dumps({"sent": False, "error": "email_not_found"})

    code = str(random.randint(1000, 9999))
    #  expires_at = _utcnow() + datetime.timedelta(seconds=OTP_TTL_SECONDS)
    expires_at = _utcnow() + timedelta(seconds=OTP_TTL_SECONDS)

    if session_id:
        _store_otp_in_db(session_id, code, expires_at)

    html_body = f"""
            <html>
                <body style="font-family: Arial, sans-serif; line-height: 1.2;">
                    <h2>One time password</h2>
                    <p>Hello,</p>
                    <p>Here is your OTP: <strong>{code}</strong></p>
                    <p>If you did not request the OTP, please ignore this email.</p>
                    <p>Thanks,<br/>The MyOps360 Team</p>
                </body>
            </html>
"""
    try:
        res = send_email_acs(
            sender=ACS_SENDER_EMAIL,
            to_email=receiver,
            subject="Your Verification Code",
            html_body=html_body,
        )
        return json.dumps({"sent": True, "to": receiver, "message_id": res.get("message_id")})
    except Exception:
        return json.dumps({"sent": False, "error": "send_failed"})


def create_ticket_local(ticket_data: Any) -> dict:
    db = next(get_db())
    try:
        if isinstance(ticket_data, str):
            ticket_data = json.loads(ticket_data)

        # Generate Ticket ID
        ticket_count = db.query(func.count(CrmTicket.pk_id)).scalar() or 0
        new_ticket_id = f"TCK-{ticket_count + 1}"

        # Create ticket record
        ticket = CrmTicket(
            ticket_id=new_ticket_id,
            subject=ticket_data.get("subject"),
            description=ticket_data.get("description"),
            type="Support",
            status="Open",
            created_by=ticket_data.get("created_by"),
            owner=ticket_data.get("owner"),
            created_at=_utcnow(),
            attachment=None,
            entity_id=ticket_data.get("entity_id"),
            sub_entity_id=ticket_data.get("sub_entity_id"),
            resolution="",
            agent_email=ticket_data.get("agent_email"),
            last_updated=datetime.now(UTC),
        )

        db.add(ticket)
        db.commit()
        db.refresh(ticket)

        return {
            "status": "ticket_created",
            "ticket_id": new_ticket_id
        }

    except Exception as e:
        db.rollback()
        logger.exception("Failed to create ticket")
        return {
            "status": "error",
            "error": str(e)
        }

    finally:
        db.close()

def _parse_uuid(s: str) -> Optional[uuid.UUID]:
    try:
        return uuid.UUID((s or "").strip())
    except Exception:
        return None
        
def _store_otp_in_db(session_id: str, code: str, expires_at: datetime) -> dict:
    session_uuid = _parse_uuid(session_id)
    if not session_uuid:
        return {"ok": False, "error": "invalid_session_id"}

    db, gen = _open_db_session()
    try:
        row = db.query(AIChatHistory).filter(AIChatHistory.id == session_uuid).first()
        if not row:
            return {"ok": False, "error": "session_not_found"}

        row.otp_code = str(code)
        row.otp_expires_at = expires_at
        row.otp_attempts = 0
        row.updated_at = _utcnow()
        db.commit()
        return {"ok": True}
    except Exception as e:
        db.rollback()
        return {"ok": False, "error": "db_update_failed", "detail": repr(e)}
    finally:
        db.close()
        try:
            gen.close()
        except Exception:
            pass


def update_create_crm_ticket_tool(ticket_info: Any) -> str:
    try:
        # normalize input to a dict
        if ticket_info is None:
            ticket_data = {}
        elif isinstance(ticket_info, dict):
            ticket_data = dict(ticket_info)
        elif isinstance(ticket_info, str):
            # try JSON first, then Python literal
            try:
                ticket_data = json.loads(ticket_info)
            except Exception:
                try:
                    ticket_data = ast.literal_eval(ticket_info)
                except Exception:
                    # fallback: put the string into description
                    ticket_data = {"description": ticket_info}
        else:
            # other types -> wrap
            ticket_data = {"value": ticket_info}

        # ensure it's a dict
        if not isinstance(ticket_data, dict):
            ticket_data = {"description": str(ticket_data)}

        # Enforce Support type regardless of agent input
        ticket_data["type"] = "Support"

        result = create_ticket_local(ticket_data)
        # logger.info("Created ticket: %s", result.get("ticket_id"))
        return json.dumps({
            "ended": True,
            "status": "ticket_created",
            "ticket_id": result.get("ticket_id")
        })
    except Exception as e:
        logger.exception("Error creating CRM ticket")
        return json.dumps({
            "ended": True,
            "status": "error",
            "error": str(e)
        })


from sqlalchemy import text

def fetch_lup_carriers_kb_tool() -> str:
    db = next(get_db())
    try:
        result = db.execute(text("""
            SELECT vendor_name, market, state_availability, status FROM wpo.lup_carriers
        """))

        data = [
            {
                "vendor_name": row.vendor_name,
                "market": row.market,
                "state_availability": row.state_availability,
                "status": row.status
            }
            for row in result
        ]

        return json.dumps({
            "status": "success",
            "table": "wpo.lup_carriers",
            "count": len(data),
            "data": data
        })

    except Exception as e:
        logger.exception("Failed to fetch contracting_status and status")
        return json.dumps({
            "status": "error",
            "error": str(e)
        })
    finally:
        db.close()

def send_support_email_tool(email_info: Any) -> str:
    try:
        # email_info = json.loads(email_info)
        # Handle both string and dict safely
        if isinstance(email_info, str):
            email_info = json.loads(email_info)
        elif not isinstance(email_info, dict):
            raise TypeError("email_info must be dict or JSON string")
        
        name = email_info.get("name")
        email = email_info.get("email")
        ticket_id = email_info.get("ticket_id")
        reason = email_info.get("reason")

        if not email or not name:
            return json.dumps({
                "status": "error",
                "error": "Missing name or email"
            })

        client = EmailClient.from_connection_string(
            ACS_EMAIL_CONNECTION_STRING
        )

        date = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S UTC")

        subject = f"Support Ticket Confirmation - {ticket_id}"

        html_body = f"""
        <p>Hi {name},</p>

        <p>Thanks for reaching out to Agility Insurance Services, we’ve got your request!</p>

        <p><strong>Here’s a quick summary of your support ticket:</strong></p>
        <ul>
            <li><strong>Ticket:</strong> {ticket_id}</li>
            <li><strong>What you need help with:</strong> {reason}</li>
            <li><strong>Submitted on:</strong> {date}</li>
        </ul>

        <p>Our support team is reviewing this and will follow up as soon as possible.</p>
    
        <p>Thanks,<br>
        Agility Insurance Services Support Team</p>
        """

        message = {
            "senderAddress": "dataops@834labs.com",
            "recipients": {
                "to": [{"address": email}]
            },
            "content": {
                "subject": subject,
                "html": html_body,
            },
        }

        poller = client.begin_send(message)
        result = poller.result()

        message_id = getattr(result, "id", None)

        return json.dumps({
            "status": "email_sent",
            "message_id": message_id
        })

    except Exception as e:
        logger.exception("Email tool failed")
        return json.dumps({
            "status": "error",
            "error": str(e)
        })
    
def _verify_otp_in_db(session_id: str, code: str) -> dict:
    session_uuid = _parse_uuid(session_id)
    if not session_uuid:
        return {"ok": False, "verified": False, "error": "invalid_session_id"}

    db, gen = _open_db_session()
    try:
        row = db.query(AIChatHistory).filter(AIChatHistory.id == session_uuid).first()
        if not row:
            return {"ok": False, "verified": False, "error": "session_not_found"}

        row.otp_attempts = int(row.otp_attempts or 0) + 1

        if not row.otp_code or not row.otp_expires_at:
            row.updated_at = _utcnow()
            db.commit()
            return {"ok": True, "verified": False, "error": "otp_not_set"}

        if _utcnow() > row.otp_expires_at:
            row.updated_at = _utcnow()
            db.commit()
            return {"ok": True, "verified": False, "error": "otp_expired"}

        verified = str(row.otp_code) == str((code or "").strip())
        row.updated_at = _utcnow()
        db.commit()
        return {"ok": True, "verified": verified}
    except Exception as e:
        db.rollback()
        return {"ok": False, "verified": False, "error": "db_verify_failed", "detail": repr(e)}
    finally:
        db.close()
        try:
            gen.close()
        except Exception:
            pass

def verify_verification_code(npn: str, code: str, session_id: str = "") -> str:
    npn = (npn or "").strip()
    code = (code or "").strip()
    if not npn or not code:
        return json.dumps({"verified": False, "error": "missing_np_or_code"})
    if not session_id:
        return json.dumps({"verified": False, "error": "session_id_missing"})

    res = _verify_otp_in_db(session_id, code)
    return json.dumps({"verified": bool(res.get("verified")), "meta": res})


def build_function_tool() -> FunctionTool:
    user_functions: Set = {
        end_conversation,
        update_create_crm_ticket_tool,
        fetch_lup_carriers_kb_tool, 
        send_support_email_tool,
        get_agent_by_npn,
        send_verification_code,
        verify_verification_code,
        # check_agent_by_npn,
    }
    return FunctionTool(functions=user_functions)

# email_info = { "name": "Ijya Sharma", "email": "isharma@834labs.com", "ticket_id": "TCK-140", "reason": "Setup Agent Profile"
# }

# send_support_email_tool(email_info)
# print(update_create_crm_ticket_tool(ticket_info))
# result = fetch_lup_carriers_kb_tool()
# print(json.dumps(json.loads(result), indent=2))

# test_otp = send_email_acs(
#     sender="dataops@834labs.com",
#     to_email="isharma@834labs.com",
#     subject="TEST OTP",
#     html_body="<h1>Test OTP</h1>"
# )
# print(test_otp)
# send_verification_code("5111", str(uuid.uuid4()))
# print(get_agent_by_npn("5111"))