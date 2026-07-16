import os
# ai_intent_parser.py

from openai import AzureOpenAI
from azure.identity import ClientSecretCredential, DefaultAzureCredential
from azure.keyvault.secrets import SecretClient
import json
from typing import Tuple
from intent_models import QueryIntent
from semantic_registry import MODULES

KEY_VAULT_URL = os.getenv("KEYVAULT_URL", "")

bootstrap_cred = DefaultAzureCredential()
bootstrap_client = SecretClient(vault_url=KEY_VAULT_URL, credential=bootstrap_cred)

client_id = bootstrap_client.get_secret(os.getenv("KEYVAULT_CLIENT_ID_SECRET_NAME", "")).value
client_secret = bootstrap_client.get_secret(os.getenv("KEYVAULT_CLIENT_SECRET_NAME", "")).value
tenant_id = bootstrap_client.get_secret(os.getenv("KEYVAULT_TENANT_ID_SECRET_NAME", "")).value

sp_cred = ClientSecretCredential(
    tenant_id=tenant_id,
    client_id=client_id,
    client_secret=client_secret
)

kv_client = SecretClient(vault_url=KEY_VAULT_URL, credential=sp_cred)

API_KEY = kv_client.get_secret("sql-gen-bot").value
ENDPOINT = "https://sql-test-resource.cognitiveservices.azure.com/"
DEPLOYMENT_NAME = "sql-test-intent"
API_VERSION = "2024-02-15-preview"

llm_client = AzureOpenAI(
    api_key=API_KEY,
    azure_endpoint=ENDPOINT,
    api_version=API_VERSION
)

SYSTEM_PROMPT = """
You are a structured SQL intent parser.

Return ONLY valid JSON in this format:

{
  "module": "<module_name>",
  "metric": "<metric_name>",
  "dimensions": ["<dimension_name>"],
  "explicit_dimensions": false,
  "filters": {
    "<filter_name>": "value"
  },
  "state_dimension": null,
  "order_by": null,
  "order_direction": "ASC",
  "limit": null,
  "confidence": 0.0
}

--------------------------------------------------
GENERAL RULES
--------------------------------------------------

- Output ONLY valid JSON. No explanations.
- confidence must be between 0 and 1.
- Choose the most appropriate module based on the question.
- Never invent fields.
- Never return null metric.
- Only use filters that exist in the selected module definition.

--------------------------------------------------
MODULE SELECTION GUIDANCE
--------------------------------------------------

agents_contracts:
- Use for agent contracts, appointments, uplines, appointment status.

agents:
- Use for master agent profile information (email, phone, status, states).

licenses:
- Use for license records and license state/status questions.

bob:
- Book of Business.
- Use for members, policies, enrollments, coverage, recruiter production, member counts.

commission_items:
- Agent-level commission detail.
- Use for insured-level commission, split, payment detail, writing agent, policy-level commission.

commission_totals:
- Carrier-level commission summary.
- Use for total commission paid by carrier, statement totals, aggregated revenue.

--------------------------------------------------
METRIC RULES
--------------------------------------------------

- If question contains "how many", "number of", or "count":
    → Select a count metric.

- If question contains "how much", "total", or "sum":
    → Select a SUM metric if available.

- If question contains "show", "list", or "display":
    → Select a list metric.

--------------------------------------------------
FIELD-SPECIFIC OUTPUT RULES (VERY IMPORTANT)
--------------------------------------------------

If the user asks for a specific field or attribute, such as:
- email
- phone
- recruiter
- upline
- writing number
- appointed states
- status
- coverage month
- statement month
- policy number
- member name
- premium
- payment

You MUST:
- Set metric = "list_records"
- Set explicit_dimensions = true
- Include ONLY the requested fields in the dimensions array.

Do NOT return full table ("*") when a specific field is requested.

Examples:

User: "Show me appointed states for David Crockett"
→ explicit_dimensions = true
→ dimensions = ["appointed_states"]

User: "What is David Crockett's email?"
→ explicit_dimensions = true
→ dimensions = ["email"]

--------------------------------------------------
IDENTITY RULES
--------------------------------------------------

AGENT:
- If a person's name appears without hierarchy terms:
  → Use filter "agent".

MEMBER / INSURED:
- If referring to policy holder, member, insured:
  → Use filter "member".

CARRIER:
- If referring to carrier:
  → Use filter "carrier_name" if available.
  
BOB METRIC CLARIFICATION:

For bob module:

- "member count"
- "number of members"
- "total members"
- "how many members"

→ Use metric: sum_members

- "policy count"
- "contract count"
- "number of policies"
- "total contracts"

→ Use metric: sum_contracts

- Generic "how many records"
→ Use metric: count_records

--------------------------------------------------
FINAL RULES
--------------------------------------------------

- Always choose a valid metric from the selected module.
- Only output JSON.

If the user uses relative time expressions like:
- "this month"
- "last month"
- "this year"
- "last year"

DO NOT convert them to hardcoded dates.

Instead:
- Use filter key "relative_time"
- Set value to one of:
  "this_month"
  "last_month"
  "this_year"
  "last_year"
"""

# Dynamically append module catalog
MODULE_CATALOG = {}

for name, cfg in MODULES.items():
    MODULE_CATALOG[name] = {
        "metrics": list(cfg["metrics"].keys()),
        "dimensions": list(cfg["dimensions"].keys()),
        "filters": list(cfg["filters"].keys()),
    }

SYSTEM_PROMPT += "\n\nAvailable modules and fields:\n" + json.dumps(MODULE_CATALOG, indent=2)


class IntentParseError(Exception):
    pass


def _validate_schema(data: dict) -> None:
    if "module" not in data or not data["module"]:
        raise IntentParseError("Missing module")
    if data["module"] not in MODULES:
        raise IntentParseError("Invalid module")

    module_cfg = MODULES[data["module"]]

    if "metric" not in data or not data["metric"]:
        raise IntentParseError("Missing metric")
    if data["metric"] not in module_cfg["metrics"]:
        raise IntentParseError("Invalid metric")

    for dim in data.get("dimensions", []):
        if dim not in module_cfg["dimensions"]:
            raise IntentParseError(f"Invalid dimension: {dim}")

    for f in data.get("filters", {}).keys():
        if f not in module_cfg["filters"]:
            raise IntentParseError(f"Invalid filter: {f}")

    conf = data.get("confidence", None)
    if conf is None or not isinstance(conf, (int, float)) or conf < 0 or conf > 1:
        raise IntentParseError("Invalid confidence")


def parse_with_llm(user_query: str, company_id: int) -> Tuple[QueryIntent, float]:
    resp = llm_client.chat.completions.create(
        model=DEPLOYMENT_NAME,
        temperature=0,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_query},
        ],
    )

    raw = (resp.choices[0].message.content or "").strip()

    try:
        data = json.loads(raw)
    except Exception:
        raise IntentParseError("LLM returned invalid JSON")

    _validate_schema(data)

    intent = QueryIntent(
        module=data["module"],
        metric=data["metric"],
        dimensions=data.get("dimensions", []),
        filters=data.get("filters", {}),
        state_dimension=data.get("state_dimension"),
        order_by=data.get("order_by"),
        order_direction=data.get("order_direction", "ASC"),
        limit=data.get("limit"),
        explicit_dimensions=data.get("explicit_dimensions", False),
    )

    confidence = float(data.get("confidence", 0.5))
    return intent, confidence