# Agility_Contracting_chat.py
import os
import re
from typing import Dict, Any
import time
import json
import ast
import logging
import uuid
from threading import Lock
import requests
import random
from azure.identity import ClientSecretCredential
from azure.ai.agents import AgentsClient
from azure.ai.agents.models import MessageRole, FileSearchTool
from azure.core.pipeline.transport import RequestsTransport
from azure.core.pipeline.policies import RetryPolicy

from app.utils.callAI.agility_tools import (
    build_function_tool,
    end_conversation,
    update_create_crm_ticket_tool,
    fetch_lup_carriers_kb_tool,
    send_support_email_tool,
    # check_agent_by_npn,
    get_agent_by_npn,
    send_verification_code,
    verify_verification_code
)

from app.core.config import settings
from app.db.session import get_db
from app.models.AiChatHistory import AIChatHistory

TENANT_ID = settings.AZURE_TENANT_ID
CLIENT_ID = os.getenv("MYOPS_AZURE_CLIENT_ID", "")
CLIENT_SECRET = os.getenv("MYOPS_AZURE_CLIENT_SECRET", "")
AGENT_NAME_CONTRACTING = "agility-agent-contracting"
# AGENT_NAME_CONTRACTING = 'agility-fmo'
PROJECT_ENDPOINT = "https://call-responder-bot-resource.services.ai.azure.com/api/projects/call-responder_bot"
MODEL_DEPLOYMENT = 'Call_responder_bot'
VECTOR_STORE_ID = "vs_iS4idYgGD4A7KNCk7aTXJZur"

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
log = logging.getLogger("agility-chat.contracting")

for name in ["azure", "azure.core.pipeline.policies.http_logging_policy", "azure.ai.agents"]:
    logging.getLogger(name).setLevel(logging.WARNING)

_CITATION_PATTERN = re.compile(r"【\d+:\d+†source】")
_AGENTS_CLIENT: AgentsClient | None = None
_AGENT_ID: str | None = None
_CLIENT_LOCK = Lock()

def _strip_citations(text: str) -> str:
    if not isinstance(text, str):
        text = str(text)
    text = _CITATION_PATTERN.sub("", text)
    text = re.sub(r"[ \t]+", " ", text).strip()
    return text

def unwrap_text(obj) -> str:
    if isinstance(obj, str):
        s = obj.strip()
        if s.startswith("{'type':") or s.startswith('{"type"'):
            try:
                parsed = ast.literal_eval(s)
                return unwrap_text(parsed)
            except Exception:
                return obj
        return obj

    if hasattr(obj, "text"):
        txt_obj = obj.text
        if isinstance(txt_obj, dict):
            val = txt_obj.get("value")
            if isinstance(val, str):
                return val
        if hasattr(txt_obj, "value"):
            val = txt_obj.value
            if isinstance(val, str):
                return val

    if isinstance(obj, dict):
        txt = obj.get("text")
        if isinstance(txt, dict):
            val = txt.get("value")
            if isinstance(val, str):
                return val
        return str(obj)

    if isinstance(obj, list):
        return "\n".join(filter(None, (unwrap_text(x) for x in obj)))

    return str(obj)

AGENT_ID_CACHED: str | None = None
AGENT_LOCK = Lock()

def _open_db():
    gen = get_db()
    db = next(gen)
    return db, gen


def _parse_uuid(s: str) -> uuid.UUID | None:
    try:
        return uuid.UUID((s or "").strip())
    except Exception:
        return None


def _get_history_window_size() -> int:
    raw = os.getenv("AGILITY_HISTORY_WINDOW", "20").strip()
    try:
        n = int(raw)
    except Exception:
        n = 20
    return max(15, min(30, n))


def _load_session_context(session_id: str, bot_type: str = "contracting") -> tuple[dict, list[str]]:
    session_uuid = _parse_uuid(session_id)
    if not session_uuid:
        return {}, []

    db, gen = _open_db()
    try:
        row = (
            db.query(AIChatHistory)
            .filter(AIChatHistory.id == session_uuid, AIChatHistory.bot_type == bot_type)
            .first()
        )
        if not row:
            return {}, []

        try:
            extracted = json.loads(row.extracted_details or "{}")
            if not isinstance(extracted, dict):
                extracted = {}
        except Exception:
            extracted = {}

        try:
            history = json.loads(row.chat_history or "[]")
            if not isinstance(history, list):
                history = []
        except Exception:
            history = []

        return extracted, history
    finally:
        db.close()
        try:
            gen.close()
        except Exception:
            pass


# def get_agents_client() -> AgentsClient:
#     global _AGENTS_CLIENT
#     with _CLIENT_LOCK:
#         if _AGENTS_CLIENT is None:
#             credential = ClientSecretCredential(
#                 tenant_id=TENANT_ID,
#                 client_id=CLIENT_ID,
#                 client_secret=CLIENT_SECRET,
#             )
#             _AGENTS_CLIENT = AgentsClient(
#                 endpoint=PROJECT_ENDPOINT,
#                 credential=credential,
#                 logging_enable=False,
#             )
#         return _AGENTS_CLIENT



def get_agents_client() -> AgentsClient:
    global _AGENTS_CLIENT
    with _CLIENT_LOCK:
        if _AGENTS_CLIENT is None:
            credential = ClientSecretCredential(
                tenant_id=TENANT_ID,
                client_id=CLIENT_ID,
                client_secret=CLIENT_SECRET,
            )

            # ✅ IMPORTANT: Custom transport
            transport = RequestsTransport(
                connection_timeout=60,
                read_timeout=300  # agents need long polling
            )

            retry_policy = RetryPolicy(
                retry_total=5,
                retry_backoff_factor=2,
                retry_backoff_max=30
            )

            _AGENTS_CLIENT = AgentsClient(
                endpoint=PROJECT_ENDPOINT,
                credential=credential,
                transport=transport,
                retry_policy=retry_policy,
                logging_enable=False,
            )
        return _AGENTS_CLIENT

def _instructions_text() -> str:
    return """
Support Assistant Instructions

ROLE
You are a customer support assistant for this website or service.
Communicate naturally, clearly, and professionally.

INTENT HANDLING
Supported intents:
1. Contracting
2. Commission
3. Support
4. General Information

-------------------
KNOWLEDGE USAGE RULES

When answering questions related to carriers, markets, or state availability:

1. Call the carrier knowledge tool fetch_lup_carriers_kb_tool.
2. Treat the returned data as the single source of truth.
3. Reason over the data to identify relevant records.
4. Filter logically using available fields such as:
   - vendor_name
   - market
   - state_availability
5. Give only response as asked no Extra information.
   (examples if asked about carriers then give response only related to that)
6. Do not invent carriers, markets, or availability.
7. Do not request additional data unless required information is missing.

1. Information Usage
a) Use the information that has been provided to you as the primary source.
b) Match the user’s intent before responding.
c) If a relevant URL is available for the identified intent, include it.
d) For quantitative questions, use only these approved figures:
i. 200K plus active agents
ii. Operating in nearly 50 states
iii. 200 plus available carriers

2. FAQ Capability
a) Answer common FAQ and informational questions reliably.
b) Use content from the website, especially key and high-traffic pages.
c) Handle general platform, scale, and capability questions without escalation when possible.

URL RULES
1. Provide URLs for training, courses, or blog content only when explicitly available.
2. Use only URLs that are explicitly provided.
3. Do not invent, modify, or guess URLs.
4. Do not include unnecessary or unrelated links.

WHEN EXACT INFORMATION IS NOT AVAILABLE
1. Provide general, high-level information related to the user’s query.
2. Keep responses generic and non-specific.
3. Do not imply that general information represents exact policies, features, or processes of this service.

--------------------
CONTRACTING INTENT

1. Context
a) Agents may request new carrier contracts during interactions.
b) Carrier contracting is handled through Sally.
c) Contact Sally for assistance.
Reply: "Please contact Sally for assistance with carrier contracting at 833-239-3303."


-------------------
SUPPORT INTENT 
When Support is selected. Ask Each information one by one:
1. Ask for NPN then wait for response. = {npn}
   Reply: "Glad to Help!  What’s your NPN ? 
           I'll get your support ticket started."
    When get the npn number  to check if the npn details like {full_name} , {email} from get_agent_by_npn(npn).
    Reply: "Great! I found your NPN. 
            Let’s proceed with creating your support ticket.
            For Security reason, I'll sent a one-time passcode to the email address on your profile". 
    After that 
    call send_verification_code(npn) to {email} from get_agent_by_npn(npn)
2. Ask for otp:
   Reply: "Please provide 4-digit one-time passcode when you receive it"
   call verify_verification_code(npn, otp, session_id)
   IF OTP Verification == TRUE:
   “Awesome, thank you! You’re all verified now." 
3. Ask for Reason then wait for response. = {reason}
   Reply: "Can you help me understand the issue?"
   When get the npn number  to check if the npn details like {full_name} , {email} from get_agent_by_npn(npn).
   description = f"Name: {full_name}, Email: {email}, Reason: {reason}"
   call update_create_crm_ticket_tool(ticket_info)
    ticket_info = { "subject": "Agiltiy Support", 
                 "description": description, 
                 "type": "Support", 
                 "created_by": "user@example.com", 
                 "owner": "support@agility.com", 
                 "entity_id": "270681372", 
                 "sub_entity_id": "270681372001", 
                 "resolution": "", 
                 "agent_email": f"{email} }  

4. Reply: " All set ! Your support ticket is in.
            I’ve sent a confirmation email with the details.
            Our team will review it and follow up as soon as possible.
            If you need anything else, I’m right here! "
    Send support confirmation email send_support_email_tool(email_info) on {email} from get_agent_by_npn(npn).
    email_info = { "subject": "Agiltiy Support", 
                 "name": {full_name}, 
                 "email": {email}, 
                 "ticket_id": get from update_create_crm_ticket_tool(ticket_info),
                "reason": {reason}
                 }  
     

   If NPN exsist = FALSE:
1. Ask name then wait for response. = {full_name}
   Reply: "Thanks for explaining that, I just need a couple quick details to finish this up. What’s your full name?" 
2. Ask for email address then wait for response. = {email} 
   Reply: "What’s the best email address to send updates to?"
    ticket_info = { "subject": "Agiltiy Support", 
                 "description": f"Name: {full_name}, Email: {email}, Contact: {contact}, Reason: {reason}", 
                 "type": "Support", 
                 "created_by": "user@example.com", 
                 "owner": "support@agility.com", 
                 "entity_id": "270681372", 
                 "sub_entity_id": "270681372001", 
                 "resolution": "", 
                 "agent_email": f"{email} }  
                 
Do not ask follow-up questions.
-------------------------------
Be clear, concise, and human.
Avoid technical or internal terminology.
Do not fabricate information.
Do not use emojis or special characters.

-------------------
BEHAVIOR RESTRICTIONS

No fabricated policies, pricing, or links.
No references to internal systems.
Do not use Markdown, Emoji, Special Characters
---------------------
FORMATTING RULES

- Do not use markdown.
- Do not use bold text, bullet points, or numbered lists.
- When listing items, return them as a comma-separated plain text list.
- Do not duplicate response.
""".strip()

# def _instructions_text() -> str:
#     return """SUPPORT INTENT 
# When Support is selected. Ask Each information one by one:
# 1. Ask for NPN then wait for response. = {npn}
#    Reply: "Glad to Help!  What’s your NPN ? 
#            I'll get your support ticket started."
#     When get the npn number  to check if the npn details like {full_name} , {email} from get_agent_by_npn(npn).
#     Reply: "Great! I found your NPN. 
#             Let’s proceed with creating your support ticket.
#             For Security reason, I'll sent a one-time passcode to the email address on your profile". 
#     After that 
#     call send_verification_code(npn) to {email} from get_agent_by_npn(npn)
# 2. Ask for otp:
#    Reply: "Please provide 4-digit one-time passcode when you receive it"
#    call verify_verification_code(npn, otp, session_id)
#    IF OTP Verification == TRUE:
#    “Awesome, thank you! You’re all verified now." 
# 3. Ask for Reason then wait for response. = {reason}
#    Reply: "Can you help me understand the issue?"
#    {full_name} , {email} from get_agent_by_npn(npn)
#    description = f"Name: {full_name}, Email: {email}, Reason: {reason}"
#    call update_create_crm_ticket_tool(ticket_info)
#     ticket_info = { "subject": "Agiltiy Support", 
#                  "description": description, 
#                  "type": "Support", 
#                  "created_by": "user@example.com", 
#                  "owner": "support@agility.com", 
#                  "entity_id": "270681372", 
#                  "sub_entity_id": "270681372001", 
#                  "resolution": "", 
#                  "agent_email": f"{email} }  

# 4. Reply: " All set ! Your support ticket is in.
#             I’ve sent a confirmation email with the details.
#             Our team will review it and follow up as soon as possible.
#             If you need anything else, I’m right here! "
#     Then call  send_support_email_tool(email_info) for sending confirmation email.
#     email_info = { "subject": "Agiltiy Support", 
#                  "name": {full_name}, 
#                  "email": {email}, 
#                  "ticket_id": get from update_create_crm_ticket_tool(ticket_info),
#                 "reason": {reason}
#                  }  
     

#    If NPN exsist = FALSE:
# 1. Ask name then wait for response. = {full_name}
#    Reply: "Thanks for explaining that, I just need a couple quick details to finish this up. What’s your full name?" 
# 2. Ask for email address then wait for response. = {email} 
#    Reply: "What’s the best email address to send updates to?"
#     ticket_info = { "subject": "Agiltiy Support", 
#                  "description": f"Name: {full_name}, Email: {email}, Contact: {contact}, Reason: {reason}", 
#                  "type": "Support", 
#                  "created_by": "user@example.com", 
#                  "owner": "support@agility.com", 
#                  "entity_id": "270681372", 
#                  "sub_entity_id": "270681372001", 
#                  "resolution": "", 
#                  "agent_email": f"{email} }  

# """.strip()

def _find_agent_by_name(agents_client: AgentsClient, name: str):
    for a in agents_client.list_agents():
        if getattr(a, "name", None) == name:
            return a
    return None

def _build_file_search_tool() -> FileSearchTool | None:
    if not VECTOR_STORE_ID:
        return None
    return FileSearchTool(vector_store_ids=[VECTOR_STORE_ID])

def _update_agent_via_rest(assistant_id: str, name: str, instructions: str, tools_defs, model: str) -> None:
    """
    REST fallback using Foundry Update Agent:
    POST {endpoint}/assistants/{assistantId}?api-version=v1  :contentReference[oaicite:3]{index=3}
    """
    credential = ClientSecretCredential(
        tenant_id=TENANT_ID,
        client_id=CLIENT_ID,
        client_secret=CLIENT_SECRET,
    )
    token = credential.get_token("https://ai.azure.com/.default").token  # scope shown in REST docs :contentReference[oaicite:4]{index=4}

    url = f"{PROJECT_ENDPOINT.rstrip('/')}/assistants/{assistant_id}?api-version=v1"
    payload = {
        "name": name,
        "instructions": instructions,
        "model": model,
        "tools": tools_defs,
    }
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    r = requests.post(url, headers=headers, json=payload, timeout=30)
    if r.status_code >= 300:
        raise RuntimeError(f"Update agent REST failed: {r.status_code} {r.text}")

def get_or_create_and_update_agent(agents_client: AgentsClient, tools):
    """
    If agent exists by name -> UPDATE it.
    Else -> CREATE it.
    Adds File Search (vector store) if AGILITY_VECTOR_STORE_ID is configured.
    """
    desired_name = AGENT_NAME_CONTRACTING
    desired_instructions = _instructions_text()

    file_search_tool = _build_file_search_tool()

    # Tools: your function tools + file_search definitions (if enabled)
    combined_tools = list(tools.definitions)
    if file_search_tool:
        combined_tools += file_search_tool.definitions

    # Tool resources: MUST be a ToolResources object, NOT a dict
    combined_tool_resources = file_search_tool.resources if file_search_tool else None

    existing = _find_agent_by_name(agents_client, desired_name)

    if existing:
        agent_id = existing.id

        if hasattr(agents_client, "update_agent"):
            agents_client.update_agent(
                agent_id,
                name=desired_name,
                instructions=desired_instructions,
                model=MODEL_DEPLOYMENT,
                tools=combined_tools,
                tool_resources=combined_tool_resources,
            )
        else:
            _update_agent_via_rest(
                assistant_id=agent_id,
                name=desired_name,
                instructions=desired_instructions,
                tools_defs=combined_tools,
                model=MODEL_DEPLOYMENT,
                tool_resources=combined_tool_resources,
            )

        if hasattr(agents_client, "get_agent"):
            return agents_client.get_agent(agent_id)
        return existing

    return agents_client.create_agent(
        model=MODEL_DEPLOYMENT,
        name=desired_name,
        instructions=desired_instructions,
        tools=combined_tools,
        tool_resources=combined_tool_resources,
    )


def _get_or_create_agent_id(agents_client: AgentsClient, tools) -> str:
    global AGENT_ID_CACHED
    with AGENT_LOCK:
        if AGENT_ID_CACHED:
            # validate cached id still present
            try:
                for a in agents_client.list_agents():
                    if getattr(a, "id", None) == AGENT_ID_CACHED:
                        return AGENT_ID_CACHED
            except Exception as e:
                log.warning("list_agents failed; refreshing agent id. err=%s", e)
            AGENT_ID_CACHED = None

        agent = get_or_create_and_update_agent(agents_client, tools)
        AGENT_ID_CACHED = agent.id
        return AGENT_ID_CACHED

def _convert_history_to_messages(history: list[str], max_items: int) -> list[tuple[MessageRole, str]]:
    if not history:
        return []
    window = history[-max_items:]

    out: list[tuple[MessageRole, str]] = []
    for item in window:
        if not isinstance(item, str):
            continue
        s = item.strip()
        if s.lower().startswith("user:"):
            out.append((MessageRole.USER, s.split(":", 1)[1].strip()))
        elif s.lower().startswith("bot:") or s.lower().startswith("assistant:"):
            out.append((MessageRole.AGENT, s.split(":", 1)[1].strip()))
        else:
            out.append((MessageRole.USER, s))
    return out

def ask_agility_contracting(question: str, session_id: str) -> Dict[str, Any]:
    if not question or not question.strip():
        return {"answer": "Please enter a valid question.", "ended": False}

    extracted_details, history = _load_session_context(
        session_id, bot_type="contracting"
    )
    history_window = _get_history_window_size()
    history_msgs = _convert_history_to_messages(
        history, max_items=history_window
    )

    # ✅ Use singleton client (DO NOT use `with`)
    agents_client = get_agents_client()
    tools = build_function_tool()

    # Thread-level tool resources
    file_search_tool = _build_file_search_tool()
    thread_tool_resources = (
        file_search_tool.resources if file_search_tool else None
    )

    # Resolve agent (cached)
    agent_id = _get_or_create_agent_id(agents_client, tools)

    # Create thread
    thread = agents_client.threads.create(
        tool_resources=thread_tool_resources
    )
    thread_id = thread.id

    # System context message
    system_prompt = (
        "SYSTEM PROMPT (CONTEXT ONLY - NOT A USER REQUEST):\n"
        f"session_id={session_id}\n"
        f"extracted_details={json.dumps(extracted_details or {}, ensure_ascii=False)}\n"
        f"history_window={history_window}\n"
        "Use this context to answer the next user message.\n"
    )

    agents_client.messages.create(
        thread_id=thread_id,
        role=MessageRole.USER,
        content=system_prompt,
    )

    # Replay history
    for role, content in history_msgs:
        agents_client.messages.create(
            thread_id=thread_id,
            role=role,
            content=content,
        )

    # User message
    agents_client.messages.create(
        thread_id=thread_id,
        role=MessageRole.USER,
        content=question.strip(),
    )

    # Run agent
    run = agents_client.runs.create(
        thread_id=thread_id,
        agent_id=agent_id,
    )

    ended_forced = False

    # Poll run
    while run.status in ("queued", "in_progress", "requires_action"):
        if run.status == "requires_action":
            tool_calls = run.required_action.submit_tool_outputs.tool_calls
            tool_outputs = []

            for tc in tool_calls:
                fn_name = tc.function.name
                try:
                    args = json.loads(tc.function.arguments or "{}")
                except Exception:
                    args = {}

                if not isinstance(args, dict):
                    args = {}

                if not (args.get("session_id") or "").strip():
                    args["session_id"] = session_id

                if fn_name == "end_conversation":
                    ended_forced = True
                    output = end_conversation()

                elif fn_name == "update_create_crm_ticket_tool":
                    ticket_info = args.get("ticket_info", {})
                    output = update_create_crm_ticket_tool(ticket_info)
                elif fn_name == "fetch_lup_carriers_kb_tool":
                    output = fetch_lup_carriers_kb_tool()
                elif fn_name == "send_support_email_tool":
                    output = send_support_email_tool(args.get("email_info", {}))
                elif fn_name == "get_agent_by_npn":
                    output = get_agent_by_npn(args.get("npn", ""))
                elif fn_name == "send_verification_code":
                    output = send_verification_code(args.get("npn", ""), session_id)
                elif fn_name == "verify_verification_code":
                    output = verify_verification_code(
                        npn=args.get("npn", ""),
                        code=args.get("code", ""),
                        session_id=session_id
                    )

                # elif fn_name == "check_agent_by_npn":
                #     npn = args.get("npn", "")
                #     output = check_agent_by_npn(npn)
                else:
                    output = json.dumps({
                        "error": "unknown_tool",
                        "tool": fn_name
                    })

                tool_outputs.append({
                    "tool_call_id": tc.id,
                    "output": output,
                })

            agents_client.runs.submit_tool_outputs(
                thread_id=thread_id,
                run_id=run.id,
                tool_outputs=tool_outputs,
            )

        time.sleep(0.6)
        run = agents_client.runs.get(
            thread_id=thread_id,
            run_id=run.id,
        )

    if run.status != "completed":
        return {
            "answer": f"Request failed with status {run.status}",
            "thread_id": thread_id,
            "ended": False,
        }

    # Final agent response
    last_raw = agents_client.messages.get_last_message_text_by_role(
        thread_id=thread_id,
        role=MessageRole.AGENT,
    )

    text_out = unwrap_text(last_raw)
    text_out = _strip_citations(text_out)
    text_out = re.sub(r"[ \t]+", " ", text_out).strip()

    closing_line = (
        "Great, we want to thank you and let you know we do value you as one of our agents! Have a great day!"
    )

    ended = ended_forced or (closing_line in (text_out or ""))

    return {
        "answer": text_out,
        "thread_id": thread_id,
        "ended": ended,
    }
