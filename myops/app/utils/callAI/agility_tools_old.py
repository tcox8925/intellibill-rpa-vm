import os

import json
# from os import name
# from pydoc import html
# import random
# import uuid
# import datetime
# from typing import Dict, Any, Set, Optional

# from azure.communication.email import EmailClient
# from azure.ai.agents.models import FunctionTool
# from sqlalchemy import text
# import datetime
# import json
# from typing import Set, Dict, Any, Set
from azure.ai.agents.models import FunctionTool
# from sqlalchemy.orm import Session
# from app.db.session import get_db
# from app.models.Agilityagents import LupAgent
# from azure.communication.email import EmailClient
# import random
# from fastapi import Depends
# from app.models.AiChatHistory import AIChatHistory

# TABLE_NAME = "wpo.lup_agents"
# NAME_COLUMN = "full_name"
# NPN_COLUMN = "npn"
# EMAIL_COLUMN = "email"
# STATUS_COLUMN = "status"
# DOB_COLUMN = "bday"
# SSN_COLUMN = "ssn"
# W9_COLUMN = "w9_needed"
# EO_COLUMN = "e_o_needed"
# SCHEMA = 'wpo'
# TABLE_NAME_AGENT = 'lup_agents'
# TABLE_NAME_HISTORY = 'ai_chat_history'
# TABLE_NAME_LICENSE = 'wpo.lup_agent_licenses'
# LICENSE_COLUMN = "license_number"
# LICENSE_NPN = "agent_npn"

# # ACS placeholders (set in env or config)
# ACS_EMAIL_CONNECTION_STRING = os.getenv("ACS_CONNECTION_STRING", "")
# ACS_SENDER_EMAIL = "dataops@834labs.com"



# TEST_AGENT_DIRECTORY: Dict[str, Dict[str, str]] = {
#     "5111": {"npn": "5111", "full_name": "Ijya sharma", "email": "isharma@834labs.com","status": "Active"},
#     "3111": {"npn": "3111", "full_name": "Karan Chettri", "email": "Karan@834labs.com", "status": "Active"},
#     "0007": {"npn": "0007", "full_name": "Timm Cox", "email": "tcox@834labs.com", "status": "Active"},
#     "1234": {"npn": "1234", "full_name": "Liz Choi", "email": "echoi@834labs.com","status": "Active"},
#     "2345": {"npn": "2345", "full_name": "Jithu Poorna", "email": "JPoorna@834labs.com","status": "Active"},
#     "5112": {"npn": "5111", "full_name": "Ijya sharma", "email": "isharma@834labs.com","status": "Suspended"},
#     "3112": {"npn": "3111", "full_name": "Karan Chettri", "email": "Karan@834labs.com", "status": "Suspended"},
#     "0008": {"npn": "0007", "full_name": "Timm Cox", "email": "tcox@834labs.com", "status": "Quarantined"},
#     "1235": {"npn": "1234", "full_name": "Liz Choi", "email": "echoi@834labs.com","status": "Quarantined"},
#     "2346": {"npn": "2345", "full_name": "Jithu Poorna", "email": "JPoorna@834labs.com","status": "Quarantined"}
# }


# TEST_AGENT_DIRECTORY: Dict[str, Dict[str, str]] = {
#     "5111": {"npn": "5111", "full_name": "Ijya sharma", "email": "isharma@834labs.com", "status": "Active",
#              "ssn": "5000","w9_needed": "yes","eo_needed": "yes","license_number":"yes","dob": "March 1"},
#     "5113": {"npn": "5111", "full_name": "Ijya sharma", "email": "isharma@834labs.com", "status": "Active",
#              "ssn": "","w9_needed": "Yes","eo_needed": "Yes","license_number":"Yes","dob":""},
#     "5112": {"npn": "5111", "full_name": "Ijya sharma", "email": "isharma@834labs.com", "status": "Suspended"},

#     "3111": {"npn": "3111", "full_name": "Karan Chettri", "email": "Karan@834labs.com", "status": "Active",
#              "ssn": "3000","w9_needed": "yes","eo_needed": "yes","license_number":"yes","dob": "April 2"},
#     "3113": {"npn": "3111", "full_name": "Karan Chettri", "email": "Karan@834labs.com", "status": "Active",
#              "ssn": "","w9_needed": "yes","eo_needed": "yes","license_number":"yes","dob": ""},
#     "3112": {"npn": "3111", "full_name": "Karan Chettri", "email": "Karan@834labs.com", "status": "Suspended"},

#     "0007": {"npn": "0007", "full_name": "Timm Cox", "email": "tcox@834labs.com", "status": "Active",
#              "ssn": "7000","w9_needed": "yes","eo_needed": "yes","license_number":"yes","dob": "May 3"},
#     "0009": {"npn": "0007", "full_name": "Timm Cox", "email": "tcox@834labs.com", "status": "Active",
#              "ssn": "", "w9_needed": "yes", "eo_needed": "yes", "license_number": "yes", "dob": ""},
#     "0008": {"npn": "0007", "full_name": "Timm Cox", "email": "tcox@834labs.com", "status": "Quarantined"},

#     "1234": {"npn": "1234", "full_name": "Liz Choi", "email": "echoi@834labs.com", "status": "Active",
#              "ssn": "1000","w9_needed": "yes","eo_needed": "yes","license_number":"yes","dob": "June 4"},
#     "1236": {"npn": "1234", "full_name": "Liz Choi", "email": "echoi@834labs.com", "status": "Active",
#              "ssn": "","w9_needed": "yes","eo_needed": "yes","license_number":"yes","dob": ""},
#     "1235": {"npn": "1234", "full_name": "Liz Choi", "email": "echoi@834labs.com", "status": "Quarantined"},

#     "2345": {"npn": "2345", "full_name": "Jithu Poorna", "email": "JPoorna@834labs.com", "status": "Active",
#              "ssn": "2000","w9_needed": "yes","eo_needed": "yes","license_number":"yes","dob": "July 5"},
#     "2347": {"npn": "2345", "full_name": "Jithu Poorna", "email": "JPoorna@834labs.com", "status": "Active",
#              "ssn": "","w9_needed": "yes","eo_needed": "yes","license_number":"yes","dob": ""},
#     "2346": {"npn": "2345", "full_name": "Jithu Poorna", "email": "JPoorna@834labs.com", "status": "Quarantined"}

# }


# OTP_TTL_SECONDS = 30 * 60  # 30 minutes


# def _utcnow() -> datetime.datetime:
#     return datetime.datetime.now(datetime.timezone.utc)


# def _open_db_session():
#     gen = get_db()
#     db = next(gen)
#     return db, gen


# def _parse_uuid(s: str) -> Optional[uuid.UUID]:
#     try:
#         return uuid.UUID((s or "").strip())
#     except Exception:
#         return None


# # merge-only (non-empty) into extracted_details dict json
# def _merge_extracted_details(session_id: str, patch: dict) -> dict:
#     session_uuid = _parse_uuid(session_id)
#     if not session_uuid:
#         return {"ok": False, "error": "invalid_session_id"}

#     db, gen = _open_db_session()
#     try:
#         row = db.query(AIChatHistory).filter(AIChatHistory.id == session_uuid).first()
#         if not row:
#             return {"ok": False, "error": "session_not_found"}

#         try:
#             existing = json.loads(row.extracted_details or "{}")
#             if not isinstance(existing, dict):
#                 existing = {}
#         except Exception:
#             existing = {}

#         for k, v in (patch or {}).items():
#             if v is None:
#                 continue
#             if isinstance(v, str):
#                 v = v.strip()
#                 if not v:
#                     continue
#             existing[k] = v

#         row.extracted_details = json.dumps(existing)
#         row.updated_at = _utcnow()
#         db.commit()
#         return {"ok": True, "merged": existing}
#     except Exception as e:
#         db.rollback()
#         return {"ok": False, "error": "db_update_failed", "detail": repr(e)}
#     finally:
#         db.close()
#         try:
#             gen.close()
#         except Exception:
#             pass


# # ONE tool for storing extracted_details
# def store_extracted_details(
#     session_id: str = "",
#     name: str = "",
#     npn: str = "",
#     email: str = "",
#     license_number: str = "",
#     licensed_states: str = "",
#     market_interest: str = "",
# ) -> str:
#     patch = {
#         "name": name,
#         "npn": npn,
#         "email": email,
#         "license_number": license_number,
#         "licensed_states": licensed_states,
#         "market_interest": market_interest,
#     }
#     return json.dumps(_merge_extracted_details(session_id, patch))


# def check_agent_by_npn(npn: str) -> str:
#     npn = (npn or "").strip()
#     if not npn:
#         return json.dumps({"exists": False, "error": "npn_missing"})

#     if npn in TEST_AGENT_DIRECTORY:
#         return json.dumps({"exists": True, "source": "test_dict"})

#     db, gen = _open_db_session()
#     try:
#         q = text(f"SELECT EXISTS (SELECT 1 FROM {TABLE_NAME} WHERE {NPN_COLUMN} = :npn) AS exists;")
#         row = db.execute(q, {"npn": npn}).fetchone()
#         exists = bool(row[0]) if row else False
#         return json.dumps({"exists": exists, "source": "db"})
#     except Exception:
#         return json.dumps({"exists": False, "error": "db_query_failed"})
#     finally:
#         db.close()
#         try:
#             gen.close()
#         except Exception:
#             pass


# def get_agent_by_npn(npn: str) -> str:
#     npn = (npn or "").strip()
#     if not npn:
#         return json.dumps({"found": False, "error": "npn_missing"})

#     if npn in TEST_AGENT_DIRECTORY:
#         a = TEST_AGENT_DIRECTORY[npn]
#         return json.dumps({
#             "found": True,
#             "npn": npn,
#             "full_name": (a.get("full_name") or "").strip(),
#             "email": (a.get("email") or "").strip(),
#             "source": "test_dict",
#         })

#     db, gen = _open_db_session()
#     try:
#         q = text(
#             f"SELECT {NAME_COLUMN} AS full_name, {EMAIL_COLUMN} AS email "
#             f"FROM {TABLE_NAME} WHERE {NPN_COLUMN} = :npn LIMIT 1;"
#         )
#         row = db.execute(q, {"npn": npn}).fetchone()
#         if not row:
#             return json.dumps({"found": False})

#         full_name = (row[0] or "").strip() if isinstance(row[0], str) else (str(row[0]).strip() if row[0] else "")
#         email = (row[1] or "").strip() if isinstance(row[1], str) else (str(row[1]).strip() if row[1] else "")

#         if not email:
#             return json.dumps({"found": False, "error": "email_not_found"})

#         return json.dumps({"found": True, "npn": npn, "full_name": full_name, "email": email, "source": "db"})
#     except Exception:
#         return json.dumps({"found": False, "error": "db_query_failed"})
#     finally:
#         db.close()
#         try:
#             gen.close()
#         except Exception:
#             pass


# def send_email_acs(*, sender: str, to_email: str, subject: str, html_body: str) -> Dict[str, Any]:
#     if EmailClient is None:
#         raise RuntimeError("azure.communication.email not installed (pip install azure-communication-email)")
#     if not ACS_EMAIL_CONNECTION_STRING:
#         raise RuntimeError("ACS_EMAIL_CONNECTION_STRING is not set")

#     client = EmailClient.from_connection_string(ACS_EMAIL_CONNECTION_STRING)
#     message = {
#         "senderAddress": sender,
#         "recipients": {"to": [{"address": to_email}]},
#         "content": {"subject": subject, "html": html_body},
#     }
#     poller = client.begin_send(message)
#     result = poller.result()
#     msg_id = getattr(result, "message_id", None) or getattr(result, "id", None) or None
#     return {"ok": True, "message_id": msg_id}


# def _store_otp_in_db(session_id: str, code: str, expires_at: datetime.datetime) -> dict:
#     session_uuid = _parse_uuid(session_id)
#     if not session_uuid:
#         return {"ok": False, "error": "invalid_session_id"}

#     db, gen = _open_db_session()
#     try:
#         row = db.query(AIChatHistory).filter(AIChatHistory.id == session_uuid).first()
#         if not row:
#             return {"ok": False, "error": "session_not_found"}

#         row.otp_code = str(code)
#         row.otp_expires_at = expires_at
#         row.otp_attempts = 0
#         row.updated_at = _utcnow()
#         db.commit()
#         return {"ok": True}
#     except Exception as e:
#         db.rollback()
#         return {"ok": False, "error": "db_update_failed", "detail": repr(e)}
#     finally:
#         db.close()
#         try:
#             gen.close()
#         except Exception:
#             pass


# def _verify_otp_in_db(session_id: str, code: str) -> dict:
#     session_uuid = _parse_uuid(session_id)
#     if not session_uuid:
#         return {"ok": False, "verified": False, "error": "invalid_session_id"}

#     db, gen = _open_db_session()
#     try:
#         row = db.query(AIChatHistory).filter(AIChatHistory.id == session_uuid).first()
#         if not row:
#             return {"ok": False, "verified": False, "error": "session_not_found"}

#         row.otp_attempts = int(row.otp_attempts or 0) + 1

#         if not row.otp_code or not row.otp_expires_at:
#             row.updated_at = _utcnow()
#             db.commit()
#             return {"ok": True, "verified": False, "error": "otp_not_set"}

#         if _utcnow() > row.otp_expires_at:
#             row.updated_at = _utcnow()
#             db.commit()
#             return {"ok": True, "verified": False, "error": "otp_expired"}

#         verified = str(row.otp_code) == str((code or "").strip())
#         row.updated_at = _utcnow()
#         db.commit()
#         return {"ok": True, "verified": verified}
#     except Exception as e:
#         db.rollback()
#         return {"ok": False, "verified": False, "error": "db_verify_failed", "detail": repr(e)}
#     finally:
#         db.close()
#         try:
#             gen.close()
#         except Exception:
#             pass


# def send_verification_code(npn: str, session_id: str = "") -> str:
#     """
#     Sends OTP to email from get_agent_by_npn(npn)
#     Stores OTP in DB per session_id
#     """
#     npn = (npn or "").strip()
#     if not npn:
#         return json.dumps({"sent": False, "error": "npn_missing"})

#     agent_json = json.loads(get_agent_by_npn(npn))
#     if not agent_json.get("found"):
#         return json.dumps({"sent": False, "error": "agent_not_found"})

#     receiver = (agent_json.get("email") or "").strip()
#     if not receiver:
#         return json.dumps({"sent": False, "error": "email_not_found"})

#     code = str(random.randint(1000, 9999))
#     expires_at = _utcnow() + datetime.timedelta(seconds=OTP_TTL_SECONDS)

#     if session_id:
#         _store_otp_in_db(session_id, code, expires_at)

#     html_body = f"""
#             <html>
#                 <body style="font-family: Arial, sans-serif; line-height: 1.2;">
#                     <h2>One time password</h2>
#                     <p>Hello,</p>
#                     <p>Here is your OTP: <strong>{code}</strong></p>
#                     <p>If you did not request the OTP, please ignore this email.</p>
#                     <p>Thanks,<br/>The MyOps360 Team</p>
#                 </body>
#             </html>
# """
#     try:
#         res = send_email_acs(
#             sender=ACS_SENDER_EMAIL,
#             to_email=receiver,
#             subject="Your Verification Code",
#             html_body=html_body,
#         )
#         return json.dumps({"sent": True, "to": receiver, "message_id": res.get("message_id")})
#     except Exception:
#         return json.dumps({"sent": False, "error": "send_failed"})


# def verify_verification_code(npn: str, code: str, session_id: str = "") -> str:
#     npn = (npn or "").strip()
#     code = (code or "").strip()
#     if not npn or not code:
#         return json.dumps({"verified": False, "error": "missing_np_or_code"})
#     if not session_id:
#         return json.dumps({"verified": False, "error": "session_id_missing"})

#     res = _verify_otp_in_db(session_id, code)
#     return json.dumps({"verified": bool(res.get("verified")), "meta": res})


# def send_onboarding_email(email: str, full_name: str, phone: str, licensed_states: str, market_interest: str) -> str:
#     email = (email or "").strip()
#     full_name = (full_name or "").strip()
#     phone = (phone or "").strip()
#     licensed_states = (licensed_states or "").strip()
#     market_interest = (market_interest or "").strip()
#     license_number = (license_number or "").strip()

#     if not email:
#         return json.dumps({"sent": False, "error": "email_missing"})

#     html_body = f"""

#     <html>
#   <body>
#     <p>Hi {full_name or "Agent"},</p>
#     <p>Onboarding details for <b>New Agent</b>:</p>
#     <p><b>Captured details:</b></p>
#     <ul>
#       <li><b>Name:</b> {full_name}</li>
#       <li><b>Email:</b> {email}</li>
#       <li><b>Licensed states:</b> {licensed_states}</li>
#       <li><b>Market interest:</b> {market_interest}</li>
#       <li><b>License number:</b> {license_number}</li>
#     </ul>
#     <p></p>
#     <p>Thanks</p>
#   </body>
# </html>
# """
#     try:
#         res = send_email_acs(
#             sender=ACS_SENDER_EMAIL,
#             to_email=email,
#             subject="Agility Onboarding - Next Steps",
#             html_body=html_body,
#         )
#         return json.dumps({"sent": True, "to": email, "message_id": res.get("message_id")})
#     except Exception:
#         return json.dumps({"sent": False, "error": "send_failed"})


# def send_onboarding_email_existing_agent(email: str, full_name: str = "") -> str:
#     email = (email or "").strip()
#     full_name = (full_name or "").strip()
#     if not email:
#         return json.dumps({"sent": False, "error": "email_missing"})

#     subject = "Agility Contracting - Next Steps"
#     html_body = f"""
# <html>
#                 <body style="font-family: Arial, sans-serif; line-height: 1.2;">
#                     <h2>Welcome to MyOps360, {full_name}!</h2>
#                     <p>We're excited to have you on board.</p>
#                     <p>If you have any questions, feel free to reach out to our support team.</p>
#                     <p>Thanks,<br/>The MyOps360 Team</p>
#                 </body>
#             </html>
# """
#     try:
#         res = send_email_acs(
#             sender=ACS_SENDER_EMAIL,
#             to_email=email,
#             subject=subject,
#             html_body=html_body,
#         )
#         return json.dumps({"sent": True, "to": email, "message_id": res.get("message_id")})
#     except Exception:
#         return json.dumps({"sent": False, "error": "send_failed"})


def end_conversation() -> str:
     return json.dumps({"ended": True})

# def get_agent_status_by_npn(npn: str) -> str:
#     npn = (npn or "").strip()

#     if not npn:
#         return json.dumps({"found": False, "error": "npn_missing"})

#     if npn in TEST_AGENT_DIRECTORY:
#         status = (TEST_AGENT_DIRECTORY[npn].get("status") or "").strip()
#         return json.dumps({"found": True, "npn": npn, "status": status, "source": "test_dict"})

#     db, gen = _open_db_session()
#     try:
#         q = text(
#             f"SELECT {STATUS_COLUMN} FROM {TABLE_NAME} WHERE {NPN_COLUMN} = :npn LIMIT 1;"
#         )
        
#         row = db.execute(q, {"npn": npn}).fetchone()

#         if not row:
#             return json.dumps({"found": False, "npn": npn})

#         raw = row[0]
#         status = (raw or "").strip() if isinstance(raw, str) else (str(raw).strip() if raw is not None else "")

#         return json.dumps({"found": True, "npn": npn, "status": status, "source": "db"})

#     except Exception as e:
#         return json.dumps({"found": False, "error": "db_query_failed", "detail": repr(e)})

#     finally:
#         try:
#             db.close()

#         except Exception:
#             pass
#         try:
#             gen.close()            
#         except Exception:
#             pass

# def forward_to_human_agent(npn: str, session_id: str = "", reason: str = "") -> str:
#     patch = {
#         "handoff_required": True,
#         "handoff_reason": (reason or "").strip(),
#         "npn": (npn or "").strip(),
#     }
#     if session_id:
#         _merge_extracted_details(session_id, patch)

#     return json.dumps({"forwarded": True, "team": "Producer Support", "reason": patch.get("handoff_reason")})

# def get_agent_full_profile_by_npn(npn: str) -> str:
#     def _yn_present(v) -> str:
#         s = ("" if v is None else str(v)).strip()
#         if not s:
#             return "no"
#         lowered = s.lower()
#         if lowered in {"na", "n/a", "none", "null", "unknown", "-"}:
#             return "no"
#         if "xxxx" in lowered or "***" in s:
#             return "no"
#         return "yes"

#     def _yn_needed(v) -> str:
#         s = ("" if v is None else str(v)).strip().lower()
#         return "yes" if s == "yes" else "no"

#     def _is_missing(flag_yes_no: str) -> bool:
#         return str(flag_yes_no).strip().lower() != "yes"

#     npn = (npn or "").strip()
#     if not npn:
#         return json.dumps({"found": False, "error": "npn_missing"})

#     # We'll compute these and then emit missing list
#     dob_flag = "no"
#     ssn_flag = "no"
#     w9_flag = "no"
#     eo_flag = "no"
#     license_flag = "no"
#     source = "db"

#     # ✅ 1) TEST DIRECTORY FIRST
#     if npn in TEST_AGENT_DIRECTORY:
#         a = TEST_AGENT_DIRECTORY[npn] or {}
#         source = "test_dict"

#         dob_flag = _yn_present(a.get("dob", ""))
#         ssn_flag = _yn_present(a.get("ssn", ""))
#         w9_flag = _yn_needed(a.get("w9_needed", ""))
#         eo_flag = _yn_needed(a.get("eo_needed", ""))

#         lic_val = (a.get("license_number") or "").strip()
#         if lic_val:
#             license_flag = "yes"
#         else:
#             lp = (a.get("license_present") or "").strip().lower()
#             if lp in {"yes", "true", "1"}:
#                 license_flag = "yes"
#             elif isinstance(a.get("licenses"), list) and len(a.get("licenses")) > 0:
#                 has_any = any(str(x).strip() for x in a.get("licenses"))
#                 license_flag = "yes" if has_any else "no"
#             else:
#                 license_flag = "no"

#     # ✅ 2) ELSE FALL BACK TO DB
#     else:
#         db, gen = _open_db_session()
#         try:
#             q_profile = text(
#                 f"""
#                 SELECT
#                   {DOB_COLUMN} AS dob,
#                   {SSN_COLUMN} AS ssn,
#                   {W9_COLUMN}  AS w9_needed,
#                   {EO_COLUMN}  AS eo_needed
#                 FROM {TABLE_NAME}
#                 WHERE {NPN_COLUMN} = :npn
#                 LIMIT 1;
#                 """
#             )
#             row = db.execute(q_profile, {"npn": npn}).fetchone()
#             if not row:
#                 return json.dumps({"found": False, "npn": npn, "source": "db"})

#             dob_flag = _yn_present(row[0])
#             ssn_flag = _yn_present(row[1])
#             w9_flag = _yn_needed(row[2])
#             eo_flag = _yn_needed(row[3])

#             q_license_exists = text(
#                 f"""
#                 SELECT EXISTS (
#                     SELECT 1
#                     FROM {TABLE_NAME_LICENSE}
#                     WHERE {LICENSE_NPN} = :npn
#                       AND COALESCE({LICENSE_COLUMN}::text,'') <> ''
#                 ) AS license_exists;
#                 """
#             )
#             ex_row = db.execute(q_license_exists, {"npn": npn}).fetchone()
#             license_flag = "yes" if (bool(ex_row[0]) if ex_row else False) else "no"

#         except Exception as e:
#             return json.dumps({"found": False, "error": "db_query_failed", "detail": repr(e)})
#         finally:
#             db.close()
#             try:
#                 gen.close()
#             except Exception:
#                 pass

#     # Build missing list in the exact order you care about
#     missing = []
#     if _is_missing(dob_flag):
#         missing.append("DOB")
#     if _is_missing(ssn_flag):
#         missing.append("SSN")
#     if _is_missing(w9_flag):
#         missing.append("W9")
#     if _is_missing(eo_flag):
#         missing.append("EO")
#     if _is_missing(license_flag):
#         missing.append("LICENSE")

#     return json.dumps({
#         "npn": npn,
#         "missing_document": missing if missing else None,
#         "source": source,
#     })

# def send_missing_documents_email_by_npn(npn: str, session_id: str = "") -> str:
#     npn = (npn or "").strip()
#     if not npn:
#         return json.dumps({"sent": False, "error": "npn_missing"})

#     # 1) Fetch missing docs from your updated get_agent_full_profile_by_npn
#     # expected:
#     # {"npn":"5113","missing_document":["DOB","SSN"],"source":"test_dict"}  OR  missing_document: null
#     try:
#         flags_raw = get_agent_full_profile_by_npn(npn)
#         flags = json.loads(flags_raw or "{}")
#     except Exception as e:
#         return json.dumps({"sent": False, "error": "flags_parse_failed", "detail": repr(e)})

#     missing_docs = (flags or {}).get("missing_document", None)

#     # Normalize: None / [] => nothing missing
#     if not missing_docs:
#         return json.dumps({
#             "sent": False,
#             "npn": npn,
#             "error": "no_missing_documents",
#             "source": (flags or {}).get("source", "unknown"),
#         })

#     # Ensure list[str]
#     if not isinstance(missing_docs, list):
#         missing_docs = [str(missing_docs)]

#     missing_docs = [str(x).strip() for x in missing_docs if str(x).strip()]
#     if not missing_docs:
#         return json.dumps({
#             "sent": False,
#             "npn": npn,
#             "error": "no_missing_documents",
#             "source": (flags or {}).get("source", "unknown"),
#         })

#     # 2) Fetch agent email + name using your existing function
#     try:
#         agent_raw = get_agent_by_npn(npn)
#         agent = json.loads(agent_raw or "{}")
#     except Exception as e:
#         return json.dumps({"sent": False, "error": "agent_parse_failed", "detail": repr(e)})

#     if not agent.get("found"):
#         return json.dumps({"sent": False, "error": "agent_not_found", "npn": npn, "meta": agent})

#     full_name = (agent.get("full_name") or "").strip()
#     email = (agent.get("email") or "").strip()
#     if not email:
#         return json.dumps({"sent": False, "error": "email_not_found", "npn": npn, "meta": agent})

#     # 3) (Optional) store missing docs in extracted_details for session tracking
#     if session_id:
#         try:
#             _merge_extracted_details(session_id, {
#                 "npn": npn,
#                 "missing_documents": ", ".join(missing_docs),
#             })
#         except Exception:
#             pass

#     # 4) Send email
#     subject = "Agility Contracting - Missing Documents Required"
#     missing_html = "".join([f"<li>{d}</li>" for d in missing_docs])

#     html_body = f"""
# <html>
#   <body style="font-family: Arial, sans-serif; line-height: 1.4;">
#     <p>Hi {full_name or "Agent"},</p>
#     <p>We reviewed your profile and still need the following document(s) to proceed:</p>
#     <ul>
#       {missing_html}
#     </ul>
#     <p>Please reply to this email with the requested document(s) attached.</p>
#     <p>Thanks,<br/>Agility Insurance Services</p>
#   </body>
# </html>
# """

#     try:
#         res = send_email_acs(
#             sender=ACS_SENDER_EMAIL,
#             to_email=email,
#             subject=subject,
#             html_body=html_body,
#         )
#         return json.dumps({
#             "sent": True,
#             "npn": npn,
#             "to": email,
#             "missing_documents": missing_docs,
#             "message_id": res.get("message_id"),
#             "source": agent.get("source", "db"),
#         })
#     except Exception as e:
#         return json.dumps({"sent": False, "error": "send_failed", "detail": repr(e), "npn": npn})

# # def send_missing_documents_email_by_npn(npn: str, session_id: str = "") -> str:
# #     npn = (npn or "").strip()
# #     if not npn:
# #         return json.dumps({"sent": False, "error": "npn_missing"})

# #     # 1) Fetch flags from your existing function (already handles TEST_AGENT_DIRECTORY vs DB internally)
# #     try:
# #         flags_raw = get_agent_full_profile_by_npn(npn)
# #         flags = json.loads(flags_raw or "{}")
# #     except Exception as e:
# #         return json.dumps({"sent": False, "error": "flags_parse_failed", "detail": repr(e)})

# #     profile_flags = (flags or {}).get("profile") or {}
# #     if not isinstance(profile_flags, dict):
# #         return json.dumps({"sent": False, "error": "invalid_flags_response", "raw": str(flags_raw)[:300]})

# #     # 2) Determine missing documents (where flag == "no")
# #     doc_label = {
# #         "dob": "Date of Birth (DOB)",
# #         "ssn": "Social Security Number (SSN)",
# #         "w9_needed": "W-9",
# #         "eo_needed": "E&O",
# #         "license_present": "License",
# #     }

# #     missing_docs = []
# #     for k in ["dob", "ssn", "w9_needed", "eo_needed", "license_present"]:
# #         v = str(profile_flags.get(k, "")).strip().lower()
# #         if v == "no":
# #             missing_docs.append(doc_label.get(k, k))

# #     if not missing_docs:
# #         return json.dumps({
# #             "sent": False,
# #             "npn": npn,
# #             "error": "no_missing_documents",
# #             "source": (flags or {}).get("source", "unknown"),
# #         })

# #     # 3) Fetch agent email + name using your existing tool/function
# #     try:
# #         agent_raw = get_agent_by_npn(npn)
# #         agent = json.loads(agent_raw or "{}")
# #     except Exception as e:
# #         return json.dumps({"sent": False, "error": "agent_parse_failed", "detail": repr(e)})

# #     if not agent.get("found"):
# #         return json.dumps({"sent": False, "error": "agent_not_found", "npn": npn, "meta": agent})

# #     full_name = (agent.get("full_name") or "").strip()
# #     email = (agent.get("email") or "").strip()
# #     if not email:
# #         return json.dumps({"sent": False, "error": "email_not_found", "npn": npn, "meta": agent})

# #     # 4) (Optional) store missing docs in extracted_details for session tracking
# #     if session_id:
# #         try:
# #             _merge_extracted_details(session_id, {
# #                 "npn": npn,
# #                 "missing_documents": ", ".join(missing_docs),
# #             })
# #         except Exception:
# #             pass

# #     # 5) Send email
# #     subject = "Agility Contracting - Missing Documents Required"
# #     missing_html = "".join([f"<li>{d}</li>" for d in missing_docs])

# #     html_body = f"""
# # <html>
# #   <body style="font-family: Arial, sans-serif; line-height: 1.4;">
# #     <p>Hi {full_name or "Agent"},</p>
# #     <p>We reviewed your profile and still need the following document(s) to proceed:</p>
# #     <ul>
# #       {missing_html}
# #     </ul>
# #     <p>Please reply to this email with the requested document(s) attached.</p>
# #     <p>Thanks,<br/>Agility Insurance Services</p>
# #   </body>
# # </html>
# # """

# #     try:
# #         res = send_email_acs(
# #             sender=ACS_SENDER_EMAIL,
# #             to_email=email,
# #             subject=subject,
# #             html_body=html_body,
# #         )
# #         return json.dumps({
# #             "sent": True,
# #             "npn": npn,
# #             "to": email,
# #             "missing_documents": missing_docs,
# #             "message_id": res.get("message_id"),
# #             "source": agent.get("source", "db"),
# #         })
# #     except Exception as e:
# #         return json.dumps({"sent": False, "error": "send_failed", "detail": repr(e), "npn": npn})

def build_function_tool() -> FunctionTool:
    user_functions: Set = {
        # store_extracted_details,
        # check_agent_by_npn,
        # get_agent_by_npn,
        # send_verification_code,
        # verify_verification_code,
        # send_onboarding_email,
        # send_onboarding_email_existing_agent,
        end_conversation,
        # get_agent_status_by_npn,
        # forward_to_human_agent,
        # get_agent_full_profile_by_npn,
        # send_missing_documents_email_by_npn
    }
    return FunctionTool(functions=user_functions)



