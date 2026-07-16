# Agility_FAQ_chatbot.py
import os
import re
from typing import Dict, Any

from azure.identity import ClientSecretCredential
from azure.ai.agents import AgentsClient

from app.core.config import settings

TENANT_ID = settings.AZURE_TENANT_ID
CLIENT_ID = os.getenv("MYOPS_AZURE_CLIENT_ID", "")
CLIENT_SECRET = os.getenv("MYOPS_AZURE_CLIENT_SECRET", "")
AGENT_NAME_FQA = "agility_bot_faq"
PROJECT_ENDPOINT = "https://call-responder-bot-resource.services.ai.azure.com/api/projects/call-responder_bot"

if not all([TENANT_ID, CLIENT_ID, CLIENT_SECRET, PROJECT_ENDPOINT, AGENT_NAME_FQA]):
    raise RuntimeError("Missing required config values")


# -----------------------
# Client
# -----------------------
def _make_client() -> AgentsClient:
    credential = ClientSecretCredential(
        tenant_id=TENANT_ID,
        client_id=CLIENT_ID,
        client_secret=CLIENT_SECRET,
    )
    return AgentsClient(endpoint=PROJECT_ENDPOINT, credential=credential)


# -----------------------
# Resolve agent_id by name
# -----------------------
_AGENT_ID_CACHE = None

def _get_agent_id_by_name(client: AgentsClient, agent_name: str) -> str:
    global _AGENT_ID_CACHE

    if _AGENT_ID_CACHE:
        return _AGENT_ID_CACHE

    for agent in client.list_agents():
        if agent.name == agent_name:
            _AGENT_ID_CACHE = agent.id
            return agent.id

    raise ValueError(f"Agent '{agent_name}' not found")


# -----------------------
# Text cleaning
# -----------------------
_CITATION_PATTERN = re.compile(r"【[^】]*†source】")

def _extract_text(raw) -> str:
    """
    Normalize different SDK return types to a plain string.
    """
    if raw is None:
        return ""

    # Case 1: already a string
    if isinstance(raw, str):
        return raw

    # Case 2: MessageTextContent → raw.text.value
    if hasattr(raw, "text") and hasattr(raw.text, "value"):
        return raw.text.value

    # Case 3: content blocks list
    if isinstance(raw, list):
        parts = []
        for item in raw:
            if hasattr(item, "text") and hasattr(item.text, "value"):
                parts.append(item.text.value)
        return "\n".join(parts)

    # Fallback
    return str(raw)


def _clean_answer(raw) -> str:
    text = _extract_text(raw)
    text = _CITATION_PATTERN.sub("", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


# -----------------------
# Ask FAQ agent (STATELESS)
# -----------------------
def ask_agility_faq(question: str) -> Dict[str, Any]:
    question = (question or "").strip()
    if not question:
        return {"answer": "Question cannot be empty."}

    client = _make_client()
    agent_id = _get_agent_id_by_name(client, AGENT_NAME_FQA)

    run = client.create_thread_and_process_run(
        agent_id=agent_id,
        thread={
            "messages": [
                {"role": "user", "content": question}
            ]
        },
    )

    # Get last assistant message (raw object)
    raw_answer = client.messages.get_last_message_text_by_role(
        thread_id=run.thread_id,
        role="assistant",   # string role avoids enum issues
    )

    return {"answer": _clean_answer(raw_answer)}
