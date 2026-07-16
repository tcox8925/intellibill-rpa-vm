import re
import os, time
from datetime import datetime
from azure.identity import ClientSecretCredential, ManagedIdentityCredential
from app.core.config import settings
from azure.ai.projects import AIProjectClient
import json
from typing import Iterable, Optional, Any, Dict

# from transcript_score import create_transcript_lines, fast_transcribe, get_token, AUDIO_FILE

# transcript_text = create_transcript_lines(fast_transcribe(get_token(), AUDIO_FILE))


def log(msg: str):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


# print("[INFO] Initializing...")


# ---------- Foundry client ----------
def get_foundry_client(*, check_rbac: bool = True) -> AIProjectClient:
    # Build credential inline
    tenant_id = settings.AZURE_TENANT_ID
    client_id = os.getenv("MYOPS_AZURE_CLIENT_ID", "")
    client_secret = os.getenv("MYOPS_AZURE_CLIENT_SECRET", "")
    if tenant_id and client_id and client_secret:
        cred = ClientSecretCredential(tenant_id, client_id, client_secret)
    else:
        cred = ManagedIdentityCredential(client_id=client_id or None)

    # Resolve endpoint inline
    endpoint = (
        "https://call-centre-agent.services.ai.azure.com/api/projects/call-centre-agent"
    )

    if "/api/projects/" not in endpoint:
        raise RuntimeError(
            "PROJECT_ENDPOINT must be a Foundry *Project* endpoint "
            "(…services.ai.azure.com/api/projects/<projectName>). "
            f"Got: {endpoint}"
        )

    client = AIProjectClient(endpoint=endpoint, credential=cred)

    if check_rbac:
        try:
            next(iter(client.agents.list_agents()), None)
        except HttpResponseError as e:
            if getattr(e, "status_code", None) in (
                401,
                403,
            ) or "PermissionDenied" in str(e):
                raise PermissionError(
                    "RBAC check failed for agents/read. Assign the principal used by this server "
                    "the 'Cognitive Services OpenAI User' or 'Cognitive Services OpenAI Contributor' role "
                    "on the AI Services (Foundry) account."
                ) from e
            raise

    return client


# print(get_foundry_client())


def resolve_agent_id(project: AIProjectClient, *, agent_name: str) -> str:
    # agent_id = ""
    # agent_name = "Final_Score"
    # if agent_id:
    #     project.agents.get_agent(agent_id)  # verify exists
    #     return agent_id
    # if not agent_name:
    #     raise RuntimeError("Set AGENT_ID or AGENT_NAME in env")
    agent_name = (agent_name or "").strip()
    if not agent_name:
        raise RuntimeError("agent_name is required")
    matches = [
        a for a in project.agents.list_agents() if getattr(a, "name", "") == agent_name
    ]
    if not matches:
        raise RuntimeError(f"Agent named '{agent_name}' not found")
    return matches[0].id


# print(resolve_agent_id(get_foundry_client()))


# # ---------- Message parsing ----------
def extract_text_from_message(msg) -> str:
    content = getattr(msg, "content", None)
    if content:
        for block in content:
            t = (
                block.get("type")
                if isinstance(block, dict)
                else getattr(block, "type", None)
            )
            if t == "text":
                if isinstance(block, dict):
                    return block.get("text", {}).get("value", "") or ""
                txt = getattr(block, "text", None)
                return getattr(txt, "value", "") if txt else ""
    text_messages = getattr(msg, "text_messages", None)
    if text_messages:
        try:
            return text_messages[-1].text.value
        except Exception:
            return ""
    return ""


# print(extract_text_from_message(get_foundry_client()))

def strip_markdown_json(text: str) -> str:
    """
    If agent returns ```json ... ``` or ``` ... ```, strip fences.
    """
    if not text:
        return ""
    s = text.strip()

    # ```json ... ```
    s = re.sub(r"^\s*```json\s*", "", s, flags=re.IGNORECASE)
    s = re.sub(r"^\s*```\s*", "", s)
    s = re.sub(r"\s*```\s*$", "", s)
    return s.strip()

def try_parse_score_json(score_text: str) -> Optional[dict]:
    
    if not score_text:
        return None

    cleaned = strip_markdown_json(score_text)

    try:
        obj = json.loads(cleaned)
        return obj if isinstance(obj, dict) else None
    except Exception:
        return None

# ---------- Scoring utilities ----------
# 
def total_score_transcript_lines(payload: Any, criteria_ids: Iterable[int] = range(1, 18)) -> int:
    if not isinstance(payload, dict):
        raise TypeError("payload must be a dict")

    details: Dict[str, Any] = payload.get("detailscore", {}) or {}
    total = 0

    for cid in criteria_ids:
        cblock = details.get(str(cid), {}) or {}
        val = cblock.get("awarded_score", None)

        used_val = None
        if val is not None:
            try:
                used_val = int(float(val))
            except (ValueError, TypeError):
                used_val = None

        if used_val is None:
            sub = cblock.get("subcriteria", {}) or {}
            sub_sum = 0
            for sc in sub.values():
                try:
                    sub_sum += int(float(sc.get("awarded_points", 0)))
                except (ValueError, TypeError):
                    pass
            used_val = sub_sum

        total += used_val

    return int(total)

def _json_coerce(text: str) -> Any:
    try:
        return json.loads(text)
    except Exception as e:
        raise ValueError(f"Could not parse JSON: {e}") from e

# ---------- Main scoring function ----------
def score_transcript_lines(
    lines: list[str],
    *,
    agent_name: str = "",
    poll_timeout_s: int = 1800
) -> str:
    """
    Sends transcript lines to the Foundry Agent chosen by agent_name.
    Returns assistant JSON with ONLY 'awarded_total_score' updated.
    """
    if not isinstance(lines, list) or not all(isinstance(l, str) for l in lines):
        raise TypeError("score_transcript_lines expects a list[str]")

    project = get_foundry_client()
    agent_id = resolve_agent_id(project, agent_name=agent_name)

    transcript_text = "\n".join(lines)

    thread = project.agents.threads.create()
    project.agents.messages.create(
        thread_id=thread.id,
        role="user",
        content=[{"type": "text", "text": transcript_text}],
    )

    run = project.agents.runs.create(thread_id=thread.id, agent_id=agent_id)

    terminal = {"completed", "failed", "cancelled", "expired"}
    deadline = time.monotonic() + poll_timeout_s

    while run.status not in terminal:
        if time.monotonic() > deadline:
            raise TimeoutError(f"Run polling exceeded {poll_timeout_s}s (last status={run.status})")
        time.sleep(1.0)
        run = project.agents.runs.get(thread.id, run.id)

    if run.status != "completed":
        raise RuntimeError(f"Run did not complete successfully (status={run.status})")

    score_text = ""
    for msg in project.agents.messages.list(thread_id=thread.id, order="asc"):
        if getattr(msg, "role", None) == "assistant" and getattr(msg, "run_id", None) == run.id:
            score_text = extract_text_from_message(msg) or score_text

    score_text = (score_text or "").strip()

    # --- Postprocess: recompute and inject ONLY awarded_total_score ---
    try:
        payload = _json_coerce(score_text)
        awarded_total = total_score_transcript_lines(payload, criteria_ids=range(1, 20))
        payload["awarded_total_score"] = int(awarded_total)
        score_text = json.dumps(payload, indent=2, ensure_ascii=False)
    except Exception as e:
        log(f"Postprocess warning: could not update awarded_total_score ({e})")

    return score_text

# def score_transcript_lines(lines: list[str], *, poll_timeout_s: int = 1800) -> str:
#     """
#     Sends lines to the Foundry Agent and returns the assistant JSON with ONLY
#     'awarded_total_score' updated (no changes to 'max_total_score' or other fields).
#     """
#     if not isinstance(lines, list) or not all(isinstance(l, str) for l in lines):
#         raise TypeError("score_transcript_lines expects a list[str]")

#     project = get_foundry_client()
#     agent_id = resolve_agent_id(project)
#     transcript_text = "\n".join(lines)
#     thread = project.agents.threads.create()
#     project.agents.messages.create(
#         thread_id=thread.id,
#         role="user",
#         content=[{"type": "text", "text": transcript_text}],
#     )

#     run = project.agents.runs.create(thread_id=thread.id, agent_id=agent_id)

#     terminal = {"completed", "failed", "cancelled", "expired"}
#     deadline = time.monotonic() + poll_timeout_s
#     while run.status not in terminal:
#         if time.monotonic() > deadline:
#             raise TimeoutError(
#                 f"Run polling exceeded {poll_timeout_s}s (last status={run.status})"
#             )
#         time.sleep(1.0)
#         run = project.agents.runs.get(thread.id, run.id)

#     if run.status != "completed":
#         raise RuntimeError(f"Run did not complete successfully (status={run.status})")

#     score_text = ""
#     for msg in project.agents.messages.list(thread_id=thread.id, order="asc"):
#         if (
#             getattr(msg, "role", None) == "assistant"
#             and getattr(msg, "run_id", None) == run.id
#         ):
#             score_text = extract_text_from_message(msg) or score_text

#     score_text = score_text.strip()

#     # --- Postprocess: recompute and inject ONLY awarded_total_score ---
#     try:
#         payload = _json_coerce(score_text)
#         awarded_total = total_score_transcript_lines(payload, criteria_ids=range(1, 18))

#         # Update only this field per request
#         payload["awarded_total_score"] = int(awarded_total)

#         # Do NOT touch max_total_score, total_score, or any other fields.
#         score_text = json.dumps(payload, indent=2, ensure_ascii=False)
#     except Exception as e:
#         log(f"Postprocess warning: could not update awarded_total_score ({e})")

#     return score_text


# def score_transcript_lines(lines: list[str]) -> str:
#     """
#     Send transcript lines (['Agent: ...', 'Caller: ...']) to Foundry Agent.
#     Returns the agent's reply (expected to be the score).
#     """
#     if not isinstance(lines, list) or not all(isinstance(l, str) for l in lines):
#         raise TypeError("score_transcript_lines expects a list[str]")

#     transcript_text = "\n".join(lines)

#     project = get_foundry_client()
#     agent_id = resolve_agent_id(project)

#     # Create thread and send transcript
#     thread = project.agents.threads.create()
#     project.agents.messages.create(
#         thread_id=thread.id,
#         role="user",
#         content=[{"type": "text", "text": transcript_text}],
#     )

#     run = project.agents.runs.create(thread_id=thread.id, agent_id=agent_id)

#     while run.status not in {"completed", "failed", "cancelled", "expired"}:
#         time.sleep(1.0)
#         run = project.agents.runs.get(thread.id, run.id)

#     if run.status != "completed":
#         raise RuntimeError(f"Run did not complete (status={run.status})")

#     # Collect agent reply
#     score_text = ""
#     for msg in project.agents.messages.list(thread_id=thread.id, order="asc"):
#         if getattr(msg, "role", None) == "assistant" and getattr(msg, "run_id", None) == run.id:
#             score_text = extract_text_from_message(msg)

#     return (score_text or "").strip()
# print(score_transcript_lines(transcript_text))
