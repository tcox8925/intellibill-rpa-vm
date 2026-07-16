from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session
from sqlalchemy import or_, and_, select, func
import logging

from app.db.session import get_db
from app.middleware.validator import get_current_user
from app.models import Pch_Provider_Info
from app.utils.callAI.pch_assistant import classify_query, synthesize_answer

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Assistant"])

# ---------------------------------------------------------------------------
# QUERY_REGISTRY: whitelist of safe, parameterized queries per module.
# The LLM never generates SQL — it only selects a query_type + module + filters
# combo, and we map it to a registry entry here.
# ---------------------------------------------------------------------------
QUERY_REGISTRY = {
    "pch_provider_count": {
        "model": Pch_Provider_Info,
        "agg": "count",
        "scope_fields": ["company_id", "group_id"],
        "filter_fields": ["state", "status", "job_owner_name"],
    },
    "pch_provider_breakdown_by_state": {
        "model": Pch_Provider_Info,
        "agg": "group_by",
        "group_field": "state",
        "scope_fields": ["company_id", "group_id"],
        "filter_fields": ["status", "job_owner_name"],
    },
    "pch_provider_breakdown_by_status": {
        "model": Pch_Provider_Info,
        "agg": "group_by",
        "group_field": "status",
        "scope_fields": ["company_id", "group_id"],
        "filter_fields": ["state", "job_owner_name"],
    },
    "pch_provider_list": {
        "model": Pch_Provider_Info,
        "agg": "list",
        "scope_fields": ["company_id", "group_id"],
        "filter_fields": ["state", "status", "job_owner_name"],
        "select_fields": ["txn_id", "npi", "first_name", "last_name", "state", "status"],
        "limit": 10,
    },
}

# Map LLM filter key names to model column names
_FILTER_COLUMN_MAP = {
    "state": "state",
    "status": "status",
    "owner": "job_owner_name",
}


def _resolve_registry_key(query_type: str, module: str, group_by: str = None) -> str | None:
    """Map LLM classification to a QUERY_REGISTRY key."""
    if module != "pch-crm":
        return None
    if query_type == "count":
        return "pch_provider_count"
    if query_type == "breakdown":
        if group_by == "status":
            return "pch_provider_breakdown_by_status"
        return "pch_provider_breakdown_by_state"
    if query_type == "list":
        return "pch_provider_list"
    return None


def execute_safe_query(query_key: str, scope: dict, filters: dict, db: Session) -> dict:
    """
    Build and execute a SQLAlchemy query from the registry.
    Never uses raw SQL or LLM-generated column names.

    Args:
        query_key: Key into QUERY_REGISTRY.
        scope: {"company_id": ..., "group_id": ...} from user's entity context.
        filters: {"state": "TX", "status": "Active", ...} from LLM classification.
        db: SQLAlchemy session.

    Returns:
        Dict with results: {"count": N}, {"breakdown": [...]}, or {"items": [...]}.
    """
    entry = QUERY_REGISTRY.get(query_key)
    if not entry:
        return {"error": "Unknown query type"}

    model = entry["model"]

    # Build WHERE conditions: always scope by company_id/group_id
    conditions = []
    for sf in entry["scope_fields"]:
        scope_val = scope.get(sf)
        if scope_val:
            conditions.append(getattr(model, sf) == scope_val)

    # Apply user-specified filters (only allowed fields)
    allowed = entry.get("filter_fields", [])
    for filter_key, filter_val in filters.items():
        col_name = _FILTER_COLUMN_MAP.get(filter_key, filter_key)
        if col_name in allowed and filter_val:
            conditions.append(getattr(model, col_name) == filter_val)

    agg = entry["agg"]

    if agg == "count":
        q = select(func.count()).select_from(model)
        if conditions:
            q = q.where(and_(*conditions))
        count = db.execute(q).scalar()
        return {"count": count or 0}

    elif agg == "group_by":
        group_field = entry["group_field"]
        group_col = getattr(model, group_field)
        q = select(group_col, func.count().label("count")).select_from(model)
        if conditions:
            q = q.where(and_(*conditions))
        q = q.group_by(group_col).order_by(func.count().desc())
        rows = db.execute(q).all()
        return {
            "breakdown": [
                {group_field: row[0] or "Unknown", "count": row[1]}
                for row in rows
            ]
        }

    elif agg == "list":
        select_cols = [getattr(model, f) for f in entry.get("select_fields", ["txn_id"])]
        q = select(*select_cols)
        if conditions:
            q = q.where(and_(*conditions))
        limit = entry.get("limit", 10)
        q = q.limit(limit)
        rows = db.execute(q).all()
        field_names = entry.get("select_fields", ["txn_id"])
        return {
            "items": [
                {field_names[i]: row[i] for i in range(len(field_names))}
                for row in rows
            ]
        }

    return {"error": "Unsupported aggregation type"}


class AssistantRequest(BaseModel):
    query: str
    context: dict = {}


@router.post("/assistant")
async def global_assistant(
    payload: AssistantRequest,
    request: Request,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    query = payload.query.strip()
    ctx = payload.context

    if not query:
        raise HTTPException(status_code=400, detail="Query cannot be empty")

    # Build context for LLM
    llm_context = {
        "current_page": ctx.get("current_page", ""),
        "current_module": ctx.get("current_module", ""),
        "active_filters": ctx.get("active_filters", {}),
        "entities": [s.get("value", s.get("id", "")) for s in ctx.get("entities", [])],
        "affiliations": [s.get("value", s.get("id", "")) for s in ctx.get("affiliations", [])],
        "states": [s.get("value", s.get("id", "")) for s in ctx.get("states", [])],
        "statuses": [s.get("value", s.get("id", "")) for s in ctx.get("statuses", [])],
        "owners": [s.get("value", s.get("id", "")) for s in ctx.get("owners", [])],
    }

    result = classify_query(query, llm_context)
    action = result.get("action")

    # For navigate actions with NPI: look up txn_id in PCH providers
    if action == "navigate" and result.get("npi"):
        npi = result["npi"].strip()
        entity_id = ctx.get("entity_id")
        sub_entity_id = ctx.get("sub_entity_id")

        if entity_id and sub_entity_id:
            provider = db.execute(
                select(
                    Pch_Provider_Info.txn_id,
                    Pch_Provider_Info.npi,
                    Pch_Provider_Info.first_name,
                    Pch_Provider_Info.last_name,
                )
                .where(
                    and_(
                        Pch_Provider_Info.company_id == entity_id,
                        Pch_Provider_Info.group_id == sub_entity_id,
                        Pch_Provider_Info.npi == npi,
                    )
                )
                .limit(1)
            ).first()

            if provider:
                result["txn_id"] = provider.txn_id
                result["provider_name"] = f"{provider.first_name or ''} {provider.last_name or ''}".strip()
                result["route"] = f"/pch-crm/providers/{provider.txn_id}"
            else:
                result = {
                    "action": "answer",
                    "message": f"No provider found with NPI {npi} in your current scope.",
                }

    # For navigate_and_filter actions: pass through route + filters for compound navigation
    elif action == "navigate_and_filter":
        # Validate that route and filters are present
        if not result.get("route"):
            result = {
                "action": "answer",
                "message": "I couldn't determine the page to navigate to. Please be more specific.",
            }
        elif not result.get("filters"):
            # If no filters, downgrade to a simple navigate
            result["action"] = "navigate"

    # For query actions: server-side DB lookup scoped to user's entity/affiliation
    elif action == "query":
        search_term = result.get("search_term", "").strip()
        module = result.get("module", "general")

        if search_term and module == "pch-crm":
            entity_id = ctx.get("entity_id")
            sub_entity_id = ctx.get("sub_entity_id")
            search_field = result.get("search_field", "name")

            if entity_id and sub_entity_id:
                filters = [
                    Pch_Provider_Info.company_id == entity_id,
                    Pch_Provider_Info.group_id == sub_entity_id,
                ]

                current_state = ctx.get("state")
                if current_state:
                    filters.append(Pch_Provider_Info.state == current_state)

                if search_field == "npi":
                    filters.append(Pch_Provider_Info.npi.like(f"%{search_term}%"))
                else:
                    filters.append(
                        or_(
                            Pch_Provider_Info.first_name.ilike(f"%{search_term}%"),
                            Pch_Provider_Info.last_name.ilike(f"%{search_term}%"),
                        )
                    )

                providers = db.execute(
                    select(
                        Pch_Provider_Info.txn_id,
                        Pch_Provider_Info.npi,
                        Pch_Provider_Info.first_name,
                        Pch_Provider_Info.last_name,
                        Pch_Provider_Info.state,
                        Pch_Provider_Info.status,
                    )
                    .where(and_(*filters))
                    .limit(10)
                ).all()

                result["results"] = [
                    {
                        "txn_id": p.txn_id,
                        "npi": p.npi,
                        "name": f"{p.first_name or ''} {p.last_name or ''}".strip(),
                        "state": p.state,
                        "status": p.status,
                    }
                    for p in providers
                ]

    # For data_query actions: safe DB query via QUERY_REGISTRY → synthesize answer
    elif action == "data_query":
        entity_id = ctx.get("entity_id")
        sub_entity_id = ctx.get("sub_entity_id")

        if not entity_id or not sub_entity_id:
            result = {
                "action": "answer",
                "message": "Please select an entity and affiliation first to query data.",
            }
        else:
            query_type = result.get("query_type", "count")
            module = result.get("module", "pch-crm")
            llm_filters = result.get("filters", {})
            group_by = result.get("group_by")

            registry_key = _resolve_registry_key(query_type, module, group_by)

            if not registry_key:
                result = {
                    "action": "answer",
                    "message": "I can only answer data questions about PCH providers right now.",
                }
            else:
                scope = {"company_id": entity_id, "group_id": sub_entity_id}
                try:
                    data = execute_safe_query(registry_key, scope, llm_filters, db)
                    if "error" in data:
                        result = {"action": "answer", "message": f"Query error: {data['error']}"}
                    else:
                        answer_text = synthesize_answer(query, data)
                        result = {"action": "answer", "message": answer_text, "data": data}
                except Exception as e:
                    logger.error(f"Error executing data_query: {e}")
                    result = {
                        "action": "answer",
                        "message": "Sorry, I had trouble running that query. Please try again.",
                    }

    # For needs_info actions: pass through with options from context
    elif action == "needs_info":
        # Enrich options from page context if the LLM didn't provide them
        field = result.get("field", "")
        if not result.get("options"):
            field_to_context = {"entity": "entities", "affiliation": "affiliations", "state": "states", "status": "statuses", "owner": "owners"}
            ctx_key = field_to_context.get(field, "")
            if ctx_key and ctx_key in llm_context and llm_context[ctx_key]:
                result["options"] = llm_context[ctx_key]

    return result
