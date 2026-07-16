
from fastapi import APIRouter, Response, Request
from pydantic import BaseModel, Field
import json, uuid, datetime
from app.utils.callAI.Agility_Contracting_chat import ask_agility_contracting
from app.utils.callAI.Agility_FAQ_chatbot import ask_agility_faq
from app.models.AiChatHistory import AIChatHistory
from sqlalchemy.orm import Session
from app.db.session import get_db
from fastapi import Depends
from typing import Optional
from app.middleware.validator import get_current_user

router = APIRouter(tags=["Agility Chat"])

class AgilityChatRequest(BaseModel):
    question: str
    session_id: Optional[str] = None  

class AgilityFAQRequest(BaseModel):
    question: str
   

def now_utc():
    return datetime.datetime.now(datetime.timezone.utc)


def safe_load_json_list(s: str):
    try:
        val = json.loads(s) if s else []
        return val if isinstance(val, list) else []
    except Exception:
        return []


def append_chat(history_text: str, role: str, message: str):
    items = safe_load_json_list(history_text)
    prefix = "bot" if (role or "").strip().lower() in ("assistant", "bot") else "user"
    items.append(f"{prefix}: {message}")
    return json.dumps(items)


def open_db():
    gen = get_db()
    db = next(gen)
    return db, gen


def _parse_uuid(s: str) -> uuid.UUID | None:
    try:
        return uuid.UUID((s or "").strip())
    except Exception:
        return None


def resolve_or_create_session_id(req: AgilityChatRequest, request: Request, response: Response) -> str:
    """
    Rules:
    - If body.session_id exists -> use it
    - Else if header X-Session-Id exists -> use it
    - Else create new session_id
    Always return session_id in response header.
    """
    sid = (req.session_id or "").strip() if req else ""
    if not sid:
        sid = (request.headers.get("X-Session-Id") or "").strip()

    if sid and _parse_uuid(sid):
        response.headers["X-Session-Id"] = sid
        return sid

    new_sid = str(uuid.uuid4())
    response.headers["X-Session-Id"] = new_sid
    return new_sid


def get_or_create_history(db, session_uuid: uuid.UUID, bot_type: str, ip_address: str | None):
    row = (
        db.query(AIChatHistory)
        .filter(AIChatHistory.id == session_uuid, AIChatHistory.bot_type == bot_type)
        .first()
    )
    if row:
        return row, False

    now = now_utc()
    row = AIChatHistory(
        id=session_uuid,
        bot_type=bot_type,
        session_start=now,
        session_end=None,
        session_status="in progress",
        created_at=now,
        updated_at=now,
        ip_address=ip_address,
        chat_history=json.dumps([]),
        extracted_details=json.dumps({}),
        otp_code=None,
        otp_expires_at=None,
        otp_attempts=0,
    )
    db.add(row)
    return row, True


@router.post("/agility-text-bot")
def agility_contracting_chat(req: AgilityChatRequest, response: Response, request: Request):
    db, gen = open_db()
    try:
        # ✅ no session_id -> new one
        session_id = resolve_or_create_session_id(req, request, response)

        session_uuid = _parse_uuid(session_id)
        if not session_uuid:
            session_id = str(uuid.uuid4())
            response.headers["X-Session-Id"] = session_id
            session_uuid = uuid.UUID(session_id)

        hist, _created = get_or_create_history(
            db,
            session_uuid=session_uuid,
            bot_type="contracting",
            ip_address=(request.client.host if request.client else None),
        )

        # if session ended earlier but user reuses it, reopen as in progress
        if (hist.session_status or "").lower() == "ended":
            hist.session_status = "in progress"
            hist.session_end = None

        hist.chat_history = append_chat(hist.chat_history, "user", req.question)
        hist.updated_at = now_utc()

        result = ask_agility_contracting(req.question, session_id=session_id) or {}

        answer = (result or {}).get("answer", "")
        hist.chat_history = append_chat(hist.chat_history, "assistant", answer)

        # ✅ end session either via tool ended flag OR closing line
        closing_line = "Great, we want to thank you and let you know we do value you as one of our agents! Have a great day!"
        ended_flag = bool((result or {}).get("ended") is True) or (closing_line in (answer or ""))

        if ended_flag:
            hist.session_end = now_utc()
            hist.session_status = "ended"
        else:
            hist.session_status = "in progress"

        db.commit()

        result["session_id"] = session_id
        result["ended"] = ended_flag
        return result
    finally:
        db.close()
        try:
            gen.close()
        except Exception:
            pass

@router.post("/contracting-chat")
def agility_contracting_chat(req: AgilityFAQRequest, response: Response, request: Request):
    db, gen = open_db()
    try:
        # session_id = resolve_session_id(req, request, response, db, "contracting")

        try:
            session_uuid = uuid.UUID(session_id)
        except Exception:
            session_id = str(uuid.uuid4())
            response.headers["X-Session-Id"] = session_id
            session_uuid = uuid.UUID(session_id)

        hist, _created = get_or_create_history(
            db,
            session_uuid=session_uuid,
            bot_type="contracting",
            ip_address=(request.client.host if request.client else None),
        )

        hist.chat_history = append_chat(hist.chat_history, "user", req.question)
        hist.updated_at = now_utc()

        result = ask_agility_contracting(req.question, session_id=session_id) or {}

        hist.chat_history = append_chat(hist.chat_history, "assistant", (result or {}).get("answer", ""))

        if (result or {}).get("ended") is True:
            hist.session_end = now_utc()

        db.commit()

        thread_id = (result or {}).get("thread_id")
        if thread_id:
            response.headers["X-Thread-Id"] = str(thread_id)

        result["session_id"] = session_id
        return result
    finally:
        db.close()
        try:
            gen.close()
        except Exception:
            pass


@router.post("/faq-chat")
def agility_faq_chat(req: AgilityFAQRequest, db: Session = Depends(get_db)):
    now_utc = datetime.datetime.now(datetime.timezone.utc)
    new_chat = AIChatHistory(
        id=uuid.uuid4(),
        session_start=now_utc,
        session_end=now_utc,
        created_at=now_utc,
        updated_at=now_utc,
        ip_address='',
        bot_type="faq",
        chat_history=json.dumps([{"role": "user", "content": req.question}]),
        extracted_details=json.dumps({})
    )
    db.add(new_chat)
    db.commit()
    db.refresh(new_chat)
    return ask_agility_faq(req.question)


# @router.post("/contracting-chat")
# def agility_contracting_chat(req: AgilityFAQRequest, db: Session = Depends(get_db)):
#     now_utc = datetime.datetime.now(datetime.timezone.utc)
#     new_chat = AIChatHistory(
#         id=uuid.uuid4(),
#         session_start=now_utc,
#         session_end=now_utc,
#         created_at=now_utc,
#         updated_at=now_utc,
#         ip_address='',
#         bot_type="contracting",
#         chat_history=json.dumps([{"role": "user", "content": req.question}]),
#         extracted_details=json.dumps({})
#     )
#     db.add(new_chat)
#     db.commit()
#     db.refresh(new_chat)
#     return ask_agility_contracting(req.question)
