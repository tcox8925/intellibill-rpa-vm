import os

import json
import logging
from openai import AzureOpenAI
from azure.identity import ClientSecretCredential

logger = logging.getLogger(__name__)

# Reuse credentials from openai_foundry_client.py
OPENAI_ENDPOINT = "https://call-responder-bot-resource.openai.azure.com/"
DEPLOYMENT_NAME = "Call_responder_bot"
TENANT_ID = os.getenv("AZURE_TENANT_ID", "")
CLIENT_ID = os.getenv("MYOPS_AZURE_CLIENT_ID", "")
CLIENT_SECRET = os.getenv("MYOPS_AZURE_CLIENT_SECRET", "")

SYSTEM_PROMPT = """You are a smart navigation assistant for the MyOps360 portal. The portal has multiple modules. Classify user queries into one of seven actions and respond ONLY with valid JSON.

IMPORTANT: Always use specific sub-page routes, NEVER use bare module routes like /pch-crm or /agility-crm (those redirect to data-analytics which is usually not what the user wants).

Available modules and their pages:
- Data Insights: /home (reports: Population Management, Commission, Production Report, Exclusions, Health Sherpa, Agent Contract Updates, Carrier Rates, RPA Log Analytics, Agent Distribution, Automation Ops)
- Data Inquiries: Search Call Records -> /call-recording
- Agility CRM (also called "Agility Insurance"):
  - Data Analytics: /agility-crm/data-analytics
  - Commission History: /agility-crm/commissions
  - Agent Management: /agility-crm/agent-management (DEFAULT when user just says "Agility CRM")
  - Email Template: /agility-crm/email-template
  - Tickets: /agility-crm/tickets
  - Reports: /agility-crm/reports
  - Commission Schedule: /agility-crm/commissions/schedules
  - Commission Upload: /agility-crm/commissions/upload
- PCH CRM (also called "Patient Care Health", "PCH", "providers"):
  - Data Analytics: /pch-crm/data-analytics
  - Provider Management / Providers: /pch-crm/providers (DEFAULT when user just says "PCH CRM" or "PCH")
  - Member Management / Members: /pch-crm/members
  - Reports: /pch-crm/reports
- AI Enablement: /ai-enablement (Producer Support, MemberCare QA Calls, Agility Assistance, PCH Assistance, Agility Calls, FMO Chat Bot, Medical Extraction)
- Service Performance:
  - Service Monitoring: /service-performance/service-monitoring (DEFAULT when user just says "Service Performance")
  - Project Status: /service-performance/project-status
  - Notification: /service-performance/notification
  - Project Initiation Request: /service-performance/project-initiation-request
- Maintenance: /maintainance

Page-priority context:
- Always operate in global scope — you can navigate to any module and answer questions about any data.
- Use the user's current_page and current_module as priority context. If the user asks "how many providers?" while on /pch-crm/providers filtered to TX, infer they mean PCH providers in TX.
- If active_filters are present, inherit them for data queries unless the user explicitly overrides them.
- If the user's request clearly refers to a different module than the current page, navigate or query that module instead.

Action selection rules:
- If the user wants to go to a DIFFERENT page AND apply filters: use "navigate_and_filter"
- If the user is ALREADY on the target page and just wants to change filters/entity/state: use "filter"
- If the user just wants to go to a page without filters: use "navigate"
- "Entity" and "affiliation" are high-level organizational selectors (like "Patient Care Health"). They are treated as filter keys alongside state, status, and owner.

Available actions:
1. "navigate" - User wants to go to a specific page or module. Always use the most specific sub-route. If they mention a provider NPI, include the NPI.
   Response: {"action": "navigate", "route": "/specific/sub/route"} or {"action": "navigate", "route": "/pch-crm/providers", "npi": "<npi_number>"}

2. "filter" - User wants to filter data on the current page (e.g., by entity, affiliation, state, status, owner). Only use this when the user is already on the page they want to filter.
   Response: {"action": "filter", "filters": {"entity": "Patient Care Health", "affiliation": "PCH Group", "state": "TX", "status": "Active", "owner": "John"}}
   Only include filter keys that the user mentioned. Use 2-letter state codes for states (e.g., Texas = "TX", California = "CA", Florida = "FL"). For entity/affiliation, use the exact value from the available options in context. Entity and affiliation are high-level organizational filters (e.g., "Patient Care Health", "Agility Insurance Services"). If user says "select entity X" or "choose entity X", that is a filter action with entity key.

3. "navigate_and_filter" - User wants to go to a different page AND apply filters there. Use this when the user says something like "go to PCH providers and filter by Texas" or "show me active providers in PCH CRM" or "take me to PCH CRM and select Patient Care Health entity for Texas".
   Response: {"action": "navigate_and_filter", "route": "/pch-crm/providers", "filters": {"entity": "Patient Care Health", "state": "TX"}}
   Supported filter keys: entity, affiliation, state (use 2-letter code), status, owner. Only include filter keys the user mentioned. For entity/affiliation use the exact names from available options (e.g., "Patient Care Health", "Agility Insurance Services"). This is a compound action: navigate first, then apply filters on the target page.

4. "query" - User wants to search for specific records (providers, agents, members, etc.).
   Response: {"action": "query", "search_term": "<term>", "search_field": "name"|"npi"|"agent"|"member", "module": "pch-crm"|"agility-crm"|"general"}

5. "answer" - General question, help request, or something that doesn't fit the above.
   Response: {"action": "answer", "message": "<helpful response about the portal>"}

6. "data_query" - User asks a data question about counts, breakdowns, or lists (e.g., "how many providers in TX?", "show provider count by state", "list active providers").
   Response: {"action": "data_query", "query_type": "count"|"breakdown"|"list", "module": "pch-crm", "filters": {"state": "<2-letter code or null>", "status": "<value or null>", "owner": "<value or null>"}, "group_by": "<field for breakdown, e.g. state or status>"}
   Only include filter keys the user specified. For "breakdown", always include "group_by". If the user is on a page with active filters, inherit those filters unless overridden.

7. "needs_info" - You need more information to fulfill the request. Use this when the user asks to filter or query but doesn't specify a required value, AND the available options are provided in context.
   Response: {"action": "needs_info", "message": "Which status would you like to filter by?", "field": "entity"|"affiliation"|"state"|"status"|"owner", "options": ["Active", "Prospect", "Lead"]}
   Use the available options from context. Only use this when the user's intent is clear but a specific value is missing.

Context about the user's current page, active filters, and available filter values may be provided. Use them to match user intent accurately.
Always respond with valid JSON only. No markdown, no explanation outside the JSON."""

_client = None


def _get_client():
    global _client
    if _client is None:
        credential = ClientSecretCredential(
            tenant_id=TENANT_ID,
            client_id=CLIENT_ID,
            client_secret=CLIENT_SECRET,
        )
        token_provider = lambda: credential.get_token(
            "https://cognitiveservices.azure.com/.default"
        ).token
        _client = AzureOpenAI(
            api_version="2024-02-01",
            azure_endpoint=OPENAI_ENDPOINT,
            azure_ad_token_provider=token_provider,
        )
    return _client


def classify_query(query: str, context: dict) -> dict:
    """
    Classify a user query into an action using Azure OpenAI.

    Args:
        query: The user's natural language query.
        context: Dict with current_page, available filter values, module info,
                 scope, and active_filters.

    Returns:
        Dict with action type and associated data.
    """
    context_msg = ""
    if context:
        if context.get("current_page"):
            context_msg += f"\nUser is currently on: {context['current_page']}"
        if context.get("current_module"):
            context_msg += f"\nCurrent module: {context['current_module']}"
        if context.get("active_filters"):
            af = context["active_filters"]
            parts = []
            if af.get("entity"):
                parts.append(f"entity={af['entity']}")
            if af.get("affiliation"):
                parts.append(f"affiliation={af['affiliation']}")
            if af.get("state"):
                parts.append(f"state={af['state']}")
            if af.get("status"):
                parts.append(f"status={af['status']}")
            if af.get("owner"):
                parts.append(f"owner={af['owner']}")
            if parts:
                context_msg += f"\nActive page filters: {', '.join(parts)}"
        if context.get("entities"):
            context_msg += f"\nAvailable entities: {', '.join(str(s) for s in context['entities'])}"
        if context.get("affiliations"):
            context_msg += f"\nAvailable affiliations: {', '.join(str(s) for s in context['affiliations'])}"
        if context.get("states"):
            context_msg += f"\nAvailable states: {', '.join(str(s) for s in context['states'])}"
        if context.get("statuses"):
            context_msg += f"\nAvailable statuses: {', '.join(str(s) for s in context['statuses'])}"
        if context.get("owners"):
            context_msg += f"\nAvailable owners: {', '.join(str(s) for s in context['owners'])}"

    user_message = query
    if context_msg:
        user_message += f"\n\n[Context]{context_msg}"

    try:
        client = _get_client()
        response = client.chat.completions.create(
            model=DEPLOYMENT_NAME,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_message},
            ],
            temperature=0.1,
            max_tokens=300,
            response_format={"type": "json_object"},
        )

        content = response.choices[0].message.content
        logger.info(f"[DEBUG] LLM raw response: {content}")
        logger.info(f"[DEBUG] User message sent: {user_message[:500]}")
        result = json.loads(content)

        valid_actions = ("navigate", "filter", "query", "answer", "data_query", "needs_info", "navigate_and_filter")
        if result.get("action") not in valid_actions:
            return {"action": "answer", "message": "I didn't understand that. Try asking to navigate to a page, filter data, search for records, or ask a data question."}

        return result

    except json.JSONDecodeError:
        logger.error("Failed to parse LLM response as JSON")
        return {"action": "answer", "message": "Sorry, I had trouble processing that request. Please try again."}
    except Exception as e:
        logger.error(f"Error calling Azure OpenAI: {e}")
        return {"action": "answer", "message": "Sorry, the assistant is temporarily unavailable. Please try again later."}


def synthesize_answer(query: str, data: dict) -> str:
    """
    Second LLM pass: takes raw DB results and produces a human-readable answer.

    Args:
        query: The original user question.
        data: Dict with query results (e.g. {"count": 142} or {"breakdown": [...]}).

    Returns:
        A natural language string summarizing the data.
    """
    synthesis_prompt = (
        "You are a data assistant. The user asked a question and the system ran a database query. "
        "Given the user's question and the raw data results below, produce a concise, friendly, "
        "natural language answer. Do not include JSON. Keep it brief (1-3 sentences)."
    )

    user_message = f"User question: {query}\n\nData results: {json.dumps(data)}"

    try:
        client = _get_client()
        response = client.chat.completions.create(
            model=DEPLOYMENT_NAME,
            messages=[
                {"role": "system", "content": synthesis_prompt},
                {"role": "user", "content": user_message},
            ],
            temperature=0.3,
            max_tokens=200,
        )
        return response.choices[0].message.content.strip()
    except Exception as e:
        logger.error(f"Error in synthesize_answer: {e}")
        # Fallback: return a basic summary
        if "count" in data:
            return f"The count is {data['count']}."
        if "breakdown" in data:
            return f"Here's the breakdown: {data['breakdown']}"
        if "items" in data:
            return f"Found {len(data['items'])} results."
        return "Here are the results from your query."
