"""
JAI Query Understanding - Tool-augmented query analysis for the v2 pipeline.

.. deprecated::
    This module is **NOT used** by the v2 pipeline (``pipeline_v2.py``).
    It was an intermediate approach that was superseded by the tech-spec +
    tool-loop architecture in ``pipeline_v2.py`` and ``tool_engine.py``.
    Kept for reference only — do not add new callers.

Replaces the rigid single-LLM-call intent detection with a richer, multi-step
approach where the LLM uses schema exploration tools before committing to a
query plan.

The main function ``understand_query()`` orchestrates:
1. Building the system prompt via ``build_query_understanding_prompt``
2. Running a tool loop (search_tables, get_table_schema, etc.)
3. Parsing the LLM's final JSON response into a ``QuerySpec``
4. Falling back to heuristic-based spec on any failure

Public API:
- QuerySpec (dataclass) -- structured technical specification for a DB query
- understand_query()    -- main entry point
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from app.utils.jay.pipeline_models import PipelineContext, ToolLoopResult
from app.utils.jay.tools import TOOL_SCHEMAS

logger = logging.getLogger(__name__)


# =============================================================================
# Data Model
# =============================================================================


@dataclass
class QuerySpec:
    """Structured technical specification for a database query.

    Produced by the LLM after exploring the schema via tools. Consumed by
    the SQL generation step to write exact, verified SQL.
    """
    tables_needed: List[str] = field(default_factory=list)
    columns_needed: List[Dict[str, str]] = field(default_factory=list)     # [{table, column, purpose}]
    joins: List[Dict[str, str]] = field(default_factory=list)              # [{from_table, from_col, to_table, to_col}]
    filters: List[Dict[str, str]] = field(default_factory=list)            # [{column, operator, value, source}]
    aggregations: List[Dict[str, str]] = field(default_factory=list)       # [{function, column, alias}]
    assumptions: List[str] = field(default_factory=list)
    format_hint: str = "auto"
    module: str = ""
    domains: List[str] = field(default_factory=list)
    raw_spec: str = ""          # The raw JSON from LLM for debugging
    from_cache: bool = False    # Whether this was from a cached query


# =============================================================================
# Exploration-only tool subset
# =============================================================================

# All tools except validate_sql (not needed during schema exploration phase).
_EXPLORATION_TOOL_NAMES = frozenset({
    "search_tables",
    "get_table_schema",
    "get_column_values",
    "get_foreign_keys",
    "get_join_examples",
    "get_similar_queries",
})

_EXPLORATION_TOOLS: List[Dict[str, Any]] = [
    t for t in TOOL_SCHEMAS
    if t.get("function", {}).get("name") in _EXPLORATION_TOOL_NAMES
]


# =============================================================================
# Keyword -> module heuristic (mirrors intent_detector.MODULE_ALIASES)
# =============================================================================

_KEYWORD_MODULE_MAP: Dict[str, str] = {
    # Agent-related
    "agent": "agents",
    "agents": "agents",
    "producer": "agents",
    "producers": "agents",
    "npn": "agents",
    # License-related
    "license": "licenses",
    "licenses": "licenses",
    "licensing": "licenses",
    "licensed": "licenses",
    "certification": "licenses",
    # Contract-related
    "contract": "agents_contracts",
    "contracts": "agents_contracts",
    "appointed": "agents_contracts",
    "appointment": "agents_contracts",
    "carrier": "agents_contracts",
    # Commission-related
    "commission": "commission_items",
    "commissions": "commission_items",
    "payment": "commission_items",
    "premium": "commission_items",
    "revenue": "commission_totals",
    # Provider-related
    "provider": "pch_providers",
    "providers": "pch_providers",
    "npi": "pch_providers",
    # Member-related
    "member": "pch_members",
    "members": "pch_members",
    "enrollee": "pch_members",
    "enrollment": "pch_members",
    "medicaid": "pch_members",
    "medicare": "pch_members",
    # Assessment-related
    "assessment": "membercare_assessments",
    "assessments": "membercare_assessments",
    "recording": "membercare_assessments",
    "recordings": "membercare_assessments",
    # Book of business
    "bob": "bob",
    "book of business": "bob",
}


# =============================================================================
# Main entry point
# =============================================================================


def understand_query(
    query: str,
    previous_context: Optional[Dict[str, Any]] = None,
    db_session: Session = None,
    join_graph=None,
    context: Optional[PipelineContext] = None,
) -> QuerySpec:
    """Analyse a user query via tool-augmented schema exploration.

    Sends the query (with optional previous-turn context) to the LLM along
    with schema exploration tools. The LLM calls tools to discover tables,
    columns, values, and JOINs, then produces a structured JSON ``QuerySpec``.

    Args:
        query: The user's natural language question.
        previous_context: Optional dict with previous turn metadata::

            {
                "module": "agents_contracts",
                "domains": ["agent_profile", "agent_contracts"],
                "sql": "SELECT ...",
                "user_summary": "Count of active agents in Texas",
            }

        db_session: SQLAlchemy Session for tool execution.
        join_graph: Optional pre-built JoinGraph from fk_inference.
        context: Optional PipelineContext for structured logging.

    Returns:
        A QuerySpec populated from the LLM's tool-informed analysis. On any
        failure, returns a minimal heuristic-based fallback spec.
    """
    # ── 1. Build the system prompt ────────────────────────────────────────
    try:
        from app.utils.jay.prompts_v2 import build_query_understanding_prompt
    except ImportError:
        logger.error("prompts_v2 module not available; falling back to heuristic")
        spec = _fallback_query_spec(query, previous_context)
        if context:
            context.add("query_understanding", "fallback_reason", "prompts_v2 import failed")
        return spec

    system_prompt = build_query_understanding_prompt(previous_context)

    # ── 2. Build the user message ─────────────────────────────────────────
    user_message_parts: List[str] = []

    if previous_context:
        prev_summary = previous_context.get("user_summary")
        prev_sql = previous_context.get("sql")
        if prev_summary or prev_sql:
            user_message_parts.append("CONTEXT FROM PREVIOUS TURN:")
            if prev_summary:
                user_message_parts.append(f"  Previous question: {prev_summary}")
            if prev_sql:
                # Truncate very long SQL to keep the message focused
                sql_preview = prev_sql[:500] + ("..." if len(prev_sql) > 500 else "")
                user_message_parts.append(f"  Previous SQL:\n{sql_preview}")
            user_message_parts.append("")

    user_message_parts.append(f"CURRENT QUESTION: {query}")
    user_message = "\n".join(user_message_parts)

    # ── 3. Run the tool loop ──────────────────────────────────────────────
    try:
        from app.utils.jay.tool_engine import run_tool_loop
    except ImportError:
        logger.error("tool_engine module not available; falling back to heuristic")
        spec = _fallback_query_spec(query, previous_context)
        if context:
            context.add("query_understanding", "fallback_reason", "tool_engine import failed")
        return spec

    try:
        result: ToolLoopResult = run_tool_loop(
            system_prompt=system_prompt,
            user_message=user_message,
            tools=_EXPLORATION_TOOLS,
            db_session=db_session,
            join_graph=join_graph,
            max_iterations=6,
            timeout_seconds=30.0,
            context=context,
            temperature=0.0,
        )
    except Exception as exc:
        logger.exception("Tool loop raised an exception during query understanding")
        spec = _fallback_query_spec(query, previous_context)
        if context:
            context.add("query_understanding", "fallback_reason", f"tool_loop exception: {exc}")
        return spec

    # ── 4. Check for tool loop failure ────────────────────────────────────
    if result.error:
        logger.warning("Tool loop returned error: %s", result.error)
        spec = _fallback_query_spec(query, previous_context)
        if context:
            context.add("query_understanding", "fallback_reason", f"tool_loop error: {result.error}")
        return spec

    # ── 5. Parse the LLM's final response as JSON ─────────────────────────
    try:
        spec = _parse_query_spec(result.content)
    except Exception as exc:
        logger.warning(
            "Failed to parse QuerySpec from tool loop response: %s (content preview: %s)",
            exc,
            result.content[:300] if result.content else "(empty)",
        )
        spec = _fallback_query_spec(query, previous_context)
        if context:
            context.add("query_understanding", "fallback_reason", f"parse failed: {exc}")
        return spec

    # ── 6. Log to context ─────────────────────────────────────────────────
    if context:
        context.add("query_understanding", "tables_needed", spec.tables_needed)
        context.add("query_understanding", "module", spec.module)
        context.add("query_understanding", "domains", spec.domains)
        context.add("query_understanding", "format_hint", spec.format_hint)
        context.add("query_understanding", "columns_count", len(spec.columns_needed))
        context.add("query_understanding", "joins_count", len(spec.joins))
        context.add("query_understanding", "filters_count", len(spec.filters))
        context.add("query_understanding", "assumptions", spec.assumptions)
        context.add("query_understanding", "tool_iterations", result.iterations)
        context.add("query_understanding", "tool_calls_count", len(result.tool_calls_made))
        context.add("query_understanding", "timed_out", result.timed_out)
        if spec.from_cache:
            context.add("query_understanding", "from_cache", True)

    logger.info(
        "QuerySpec produced: module=%s, tables=%s, columns=%d, joins=%d, filters=%d, "
        "tool_calls=%d, iterations=%d",
        spec.module,
        spec.tables_needed,
        len(spec.columns_needed),
        len(spec.joins),
        len(spec.filters),
        len(result.tool_calls_made),
        result.iterations,
    )

    return spec


# =============================================================================
# JSON Parsing Helper
# =============================================================================


def _parse_query_spec(content: str) -> QuerySpec:
    """Parse LLM response content into a QuerySpec.

    Attempts three strategies in order:
    1. Direct ``json.loads`` on the full content
    2. Extract JSON block via regex (handles markdown fences or surrounding text)
    3. Raise ValueError if no valid JSON found

    Args:
        content: The raw text content from the LLM's final response.

    Returns:
        A populated QuerySpec instance.

    Raises:
        ValueError: If no valid JSON could be extracted.
    """
    if not content or not content.strip():
        raise ValueError("Empty content -- nothing to parse")

    data: Optional[Dict[str, Any]] = None

    # Strategy 1: try parsing the entire content as JSON
    try:
        data = json.loads(content.strip())
    except json.JSONDecodeError:
        pass

    # Strategy 2: extract JSON block from surrounding text
    if data is None:
        # Try to find the outermost JSON object. Use a greedy match from
        # the first '{' to the last '}' to handle nested structures.
        match = re.search(r"\{[\s\S]*\}", content)
        if match:
            try:
                data = json.loads(match.group(0))
            except json.JSONDecodeError:
                pass

    # Strategy 3: try inside markdown code fences
    if data is None:
        fence_match = re.search(r"```(?:json)?\s*(\{[\s\S]*?\})\s*```", content)
        if fence_match:
            try:
                data = json.loads(fence_match.group(1))
            except json.JSONDecodeError:
                pass

    if data is None or not isinstance(data, dict):
        raise ValueError(
            f"Could not extract valid JSON from response (length={len(content)})"
        )

    # Map JSON keys to QuerySpec fields, handling missing/wrong-type gracefully
    return QuerySpec(
        tables_needed=_ensure_list_of_str(data.get("tables_needed")),
        columns_needed=_ensure_list_of_dict(data.get("columns_needed")),
        joins=_ensure_list_of_dict(data.get("joins")),
        filters=_ensure_list_of_dict(data.get("filters")),
        aggregations=_ensure_list_of_dict(data.get("aggregations")),
        assumptions=_ensure_list_of_str(data.get("assumptions")),
        format_hint=str(data.get("format_hint", "auto") or "auto"),
        module=str(data.get("module", "") or ""),
        domains=_ensure_list_of_str(data.get("domains")),
        raw_spec=content.strip(),
        from_cache=bool(data.get("from_cache", False)),
    )


# =============================================================================
# Fallback: Heuristic-Based QuerySpec
# =============================================================================


def _fallback_query_spec(
    query: str,
    previous_context: Optional[Dict[str, Any]] = None,
) -> QuerySpec:
    """Build a minimal QuerySpec when the tool loop fails.

    Uses keyword matching against the query text to guess the most likely
    module. If a previous context is provided and the query looks like a
    follow-up, reuses the previous module.

    Args:
        query: The user's natural language question.
        previous_context: Optional dict with previous turn metadata.

    Returns:
        A QuerySpec with at minimum ``module`` and ``format_hint`` populated.
    """
    query_lower = query.lower()
    module = ""
    domains: List[str] = []

    # If follow-up indicators are present and we have previous context, reuse it
    follow_up_markers = {
        "these", "those", "them", "out of", "break down",
        "how about", "what about", "same", "also", "too",
        "instead", "rather", "drill", "expand",
    }
    is_follow_up = any(marker in query_lower for marker in follow_up_markers)

    if is_follow_up and previous_context:
        module = previous_context.get("module", "")
        domains = previous_context.get("domains", []) or []
        if isinstance(domains, str):
            domains = [domains]
    else:
        # Keyword scan: find the first matching keyword in the query
        # Longer phrases first to avoid partial matches
        sorted_keywords = sorted(
            _KEYWORD_MODULE_MAP.keys(), key=len, reverse=True
        )
        for keyword in sorted_keywords:
            # Use word boundary matching for single words, substring for phrases
            if " " in keyword:
                if keyword in query_lower:
                    module = _KEYWORD_MODULE_MAP[keyword]
                    break
            else:
                # Match as a whole word (bounded by non-alpha or start/end)
                pattern = r"(?<![a-z])" + re.escape(keyword) + r"(?![a-z])"
                if re.search(pattern, query_lower):
                    module = _KEYWORD_MODULE_MAP[keyword]
                    break

    # Guess format hint from query patterns
    format_hint = _guess_format_hint(query_lower)

    logger.info(
        "Fallback QuerySpec: module=%s, format_hint=%s, is_follow_up=%s",
        module or "(unknown)",
        format_hint,
        is_follow_up,
    )

    return QuerySpec(
        module=module,
        domains=domains,
        format_hint=format_hint,
    )


# =============================================================================
# Internal Helpers
# =============================================================================


def _guess_format_hint(query_lower: str) -> str:
    """Guess the display format from query text patterns.

    Args:
        query_lower: Lowercased user query.

    Returns:
        One of: "kpi", "bar_chart", "line_chart", "pie_chart", "table", "auto".
    """
    # KPI indicators (single number questions)
    kpi_patterns = [
        r"\bhow many\b", r"\btotal\b", r"\bcount\b", r"\bsum\b",
        r"\baverage\b", r"\bwhat is the\b", r"\bwhat's the\b",
    ]
    for pattern in kpi_patterns:
        if re.search(pattern, query_lower):
            # But not if grouping is also implied
            group_indicators = ["by", "per", "each", "breakdown", "break down", "grouped"]
            if any(g in query_lower for g in group_indicators):
                break
            return "kpi"

    # Trend / timeline indicators
    trend_patterns = [
        r"\bover time\b", r"\btrend\b", r"\bmonth[- ]over[- ]month\b",
        r"\byear[- ]over[- ]year\b", r"\bmonthly\b", r"\byearly\b",
        r"\bquarterly\b", r"\bweekly\b", r"\bdaily\b",
    ]
    for pattern in trend_patterns:
        if re.search(pattern, query_lower):
            return "line_chart"

    # Distribution / breakdown indicators
    distribution_patterns = [
        r"\bby state\b", r"\bby carrier\b", r"\bby type\b", r"\bby status\b",
        r"\bbreakdown\b", r"\bbreak down\b", r"\bdistribution\b",
        r"\bper\b", r"\beach\b", r"\bgrouped by\b", r"\bby\b.*\bgrouped\b",
    ]
    for pattern in distribution_patterns:
        if re.search(pattern, query_lower):
            return "bar_chart"

    # List / table indicators
    list_patterns = [
        r"\blist\b", r"\bshow me\b", r"\bwho are\b", r"\bwhich\b",
        r"\bdetails\b", r"\btop \d+\b", r"\bnames?\b",
    ]
    for pattern in list_patterns:
        if re.search(pattern, query_lower):
            return "table"

    return "auto"


def _ensure_list_of_str(value: Any) -> List[str]:
    """Coerce a value into a list of strings.

    Handles: None, a single string, a list of mixed types.
    """
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [str(item) for item in value if item is not None]
    return []


def _ensure_list_of_dict(value: Any) -> List[Dict[str, str]]:
    """Coerce a value into a list of dicts with string values.

    Filters out non-dict entries. Converts dict values to strings.
    """
    if value is None:
        return []
    if not isinstance(value, list):
        return []
    result: List[Dict[str, str]] = []
    for item in value:
        if isinstance(item, dict):
            result.append({str(k): str(v) for k, v in item.items()})
    return result
