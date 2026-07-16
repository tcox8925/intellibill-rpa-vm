"""
JAI v2 Pipeline Orchestrator - Hybrid spec-then-SQL pipeline.

Architecture (happy path = 2 LLM calls, <10s):
  0. Setup           - PipelineContext, db_type, join_graph, seed_tables
  1. Cache check     - Exact-match in query cache -> skip to scope injection
  2. LLM call 1      - Technical spec generation (no tools, JSON response)
  3. Schema assembly  - Python-only: DDL, JOIN context, column values (no LLM)
  4. SQL generation   - Branch:
       4a. good context -> LLM call 2 (no tools)
       4b. bad context  -> slim tool loop (3 iterations, 20s timeout)
  5. Scope injection  - Programmatic security (NOT LLM-controlled)
  6. Safety review    - Programmatic validation gate
  7. Execution        - Run query against DB
  8. Error recovery   - On failure: tool-assisted fix (max 2 attempts)
  9. Cache store      - Persist successful query for reuse
 10. Return           - PipelineResult compatible with format engine
"""

import json
import logging
import re
import time
import threading
from typing import Dict, List, Optional, Any

from sqlalchemy.orm import Session

from app.utils.jay.pipeline_models import (
    PipelineResult,
    PipelineContext,
    RetryHistory,
    RetryAttempt,
    ToolLoopResult,
)
from app.utils.jay.llm_client import get_llm_client
from app.utils.jay.scope_injector import inject_scope, ScopeInjectionError
from app.utils.jay.safety_reviewer import review_sql_safety
from app.utils.jay.db_executor import execute_query, QueryExecutionError
from app.utils.jay.error_classifier import classify_error, format_classified_error_for_llm
from app.utils.jay.semantic_registry import MODULES

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

PIPELINE_TIMEOUT_SECONDS = 60   # 60s total budget (down from 120s in old v2)
MAX_ERROR_RECOVERY_ATTEMPTS = 2

# Tool subsets
_TOOL_FALLBACK_NAMES = ("search_tables", "get_table_schema", "get_table_schemas", "get_column_values", "get_column_values_bulk", "get_foreign_keys")
_ERROR_RECOVERY_NAMES = ("get_column_values", "get_column_values_bulk", "get_table_schema", "get_table_schemas")


# ---------------------------------------------------------------------------
# Timeout
# ---------------------------------------------------------------------------

class PipelineTimeoutError(Exception):
    """Raised when the overall pipeline timeout is exceeded."""
    pass


def _check_pipeline_timeout(start_time: float, stage: str):
    elapsed = time.time() - start_time
    if elapsed > PIPELINE_TIMEOUT_SECONDS:
        logger.error(
            "v2 pipeline timeout exceeded at stage '%s' after %.1fs", stage, elapsed
        )
        raise PipelineTimeoutError(
            f"Pipeline timeout exceeded ({elapsed:.0f}s > {PIPELINE_TIMEOUT_SECONDS}s) at {stage}"
        )


def _remaining_seconds(start_time: float) -> float:
    """Seconds left before the global timeout."""
    return max(PIPELINE_TIMEOUT_SECONDS - (time.time() - start_time), 0)


# ---------------------------------------------------------------------------
# Reasoning model detection (models that don't support temperature)
# ---------------------------------------------------------------------------

_REASONING_DEPLOYMENTS = frozenset({"jai-gpt52", "jai-deepseek-r1"})


def _get_llm_kwargs(deployment: str, temperature: float = 0.0) -> dict:
    """Return extra kwargs for LLM call, omitting temperature for reasoning models."""
    if deployment in _REASONING_DEPLOYMENTS:
        return {}  # reasoning models only accept temperature=1 (default)
    return {"temperature": temperature}


# ---------------------------------------------------------------------------
# Join graph cache (singleton, thread-safe)
# ---------------------------------------------------------------------------

_join_graph_cache = None
_join_graph_lock = threading.Lock()


def _get_join_graph(db_session):
    """Get or create the cached join graph (singleton, thread-safe)."""
    global _join_graph_cache
    if _join_graph_cache is None:
        with _join_graph_lock:
            if _join_graph_cache is None:
                from app.utils.jay.fk_inference import discover_join_graph
                _join_graph_cache = discover_join_graph(db_session)
    return _join_graph_cache


# ---------------------------------------------------------------------------
# SQL extraction helper
# ---------------------------------------------------------------------------

def _extract_sql(content: str) -> str:
    """Extract SQL from LLM response, handling markdown fences and mixed text.

    Strategies (in order):
    1. ```sql ... ``` fences (take the last block)
    2. ``` ... ``` generic fences (take the last block)
    3. Raw text starting with SELECT/WITH (strip explanation lines)
    4. CANNOT_GENERATE marker -> return as-is for upstream handling

    Returns:
        Cleaned SQL string, or empty string if nothing found.
    """
    if not content or not content.strip():
        return ""

    text = content.strip()

    # Check for CANNOT_GENERATE marker
    if text.upper().startswith("CANNOT_GENERATE"):
        return text

    # Strategy 1+2: Extract from ``` fences (take the last block)
    fence_pattern = r'```(?:sql)?\s*\n?(.*?)\n?\s*```'
    fence_matches = re.findall(fence_pattern, text, re.DOTALL | re.IGNORECASE)
    if fence_matches:
        sql = fence_matches[-1].strip()
        if sql:
            return sql

    # Strategy 3: Raw SQL starting with SELECT/WITH
    lines = text.splitlines()
    sql_started = False
    sql_lines: list = []

    for line in lines:
        stripped = line.strip()
        # Detect SQL start
        if not sql_started:
            upper = stripped.upper()
            if upper.startswith(("SELECT", "WITH")):
                sql_started = True
                sql_lines.append(line)
            continue

        # Stop at obvious explanation markers
        if stripped.lower().startswith(
            ("this query", "note:", "explanation:", "the above", "this will")
        ):
            break

        # Skip blank lines at the boundary
        if not stripped and sql_lines:
            remaining = "\n".join(lines[len(sql_lines) + 1:]).strip()
            if remaining and not any(
                kw in remaining.upper()
                for kw in ("SELECT", "FROM", "WHERE", "JOIN", "GROUP", "ORDER", "LIMIT", "HAVING")
            ):
                break

        sql_lines.append(line)

    if sql_lines:
        return "\n".join(sql_lines).strip()

    # Fallback: return the whole text if it looks like SQL
    upper_text = text.upper()
    if any(upper_text.startswith(kw) for kw in ("SELECT", "WITH")):
        return text

    return ""


# ---------------------------------------------------------------------------
# Tool schema filtering
# ---------------------------------------------------------------------------

def _filter_tool_schemas(allowed_names: tuple) -> list:
    """Filter TOOL_SCHEMAS to only include specified tool names."""
    from app.utils.jay.tools import TOOL_SCHEMAS
    return [
        tool for tool in TOOL_SCHEMAS
        if tool["function"]["name"] in allowed_names
    ]


# ---------------------------------------------------------------------------
# Negative example storage
# ---------------------------------------------------------------------------

def _try_store_negative_example(query: str, sql: Optional[str], error: str, db_session) -> None:
    """Attempt to store a negative example for self-learning. Never raises."""
    if db_session is None:
        return
    try:
        from app.utils.jay.self_learning import store_negative_example
        store_negative_example(query, sql or "", error, db_session)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Table extraction helper
# ---------------------------------------------------------------------------

def _extract_tables_from_sql(sql: str) -> set:
    """Extract table names from FROM/JOIN clauses in SQL.

    Returns short table names (without schema prefix).
    """
    if not sql:
        return set()
    tables = set()
    pattern = r'(?:FROM|JOIN)\s+(\w+\.\w+|\w+)(?:\s+(?:AS\s+)?(\w+))?'
    for match in re.finditer(pattern, sql, re.IGNORECASE):
        table = match.group(1)
        tables.add(table.split('.')[-1] if '.' in table else table)
    return tables


# ---------------------------------------------------------------------------
# Cache helper
# ---------------------------------------------------------------------------

def _try_cache_store(
    query: str,
    sql: str,
    module: str,
    domains: List[str],
    exec_ms: float,
    db: Session,
    technical_spec: dict = None,
    cache_id: int = None,
    success: bool = True,
) -> None:
    """Attempt to store or update query cache. Never raises."""
    if db is None:
        return
    try:
        if cache_id is not None:
            from app.utils.jay.query_cache import update_query_stats
            update_query_stats(
                cache_id=cache_id,
                success=success,
                exec_ms=exec_ms,
                db_session=db,
            )
        elif success:
            from app.utils.jay.query_cache import store_successful_query
            spec_text = json.dumps(technical_spec) if technical_spec else None
            store_successful_query(
                query=query,
                spec=spec_text,
                sql=sql,
                module=module,
                domains=domains,
                exec_ms=exec_ms,
                db_session=db,
            )
    except Exception as e:
        logger.debug("v2 pipeline: cache store/update failed (non-fatal): %s", e)


# ===========================================================================
# Main Pipeline
# ===========================================================================

def run_pipeline_v2(
    query: str,
    module: str,
    scope: Dict[str, str],
    resolved_entities: Dict[str, str] = None,
    user_summary: str = None,
    format_hint: str = "auto",
    db: Session = None,
    synapse_db: Session = None,
    domains: List[str] = None,
    context: PipelineContext = None,
    previous_query_sql: str = None,
    previous_module: str = None,
    previous_domains: List[str] = None,
    previous_user_summary: str = None,
    assumption: str = None,
    progress_callback=None,
) -> PipelineResult:
    """Run the v2 hybrid pipeline: spec -> schema assembly -> SQL generation.

    Happy path makes exactly 2 LLM calls (technical spec + SQL generation)
    with no tools, targeting <10s total latency. Falls back to a slim tool
    loop only when schema assembly has low confidence, and to error recovery
    tool loops on execution failure.

    Args:
        query: Original user query.
        module: Selected module name (from intent detection).
        scope: Permission scope dict (entity_id, sub_entity_id from JWT).
        resolved_entities: Resolved entity values (e.g., {"npn": "12345"}).
        user_summary: LLM's summary of the user's question.
        format_hint: Suggested display format.
        db: PostgreSQL session.
        synapse_db: Synapse session (for bob module).
        domains: List of table catalog domains.
        context: PipelineContext for accumulating reasoning.
        previous_query_sql: SQL from previous conversation turn.
        previous_module: Module from previous conversation turn.
        previous_domains: Domains from previous conversation turn.
        previous_user_summary: User summary from previous conversation turn.
        assumption: LLM's assumption about ambiguous queries.
        progress_callback: Optional callable(stage: str, detail: str) for
            streaming progress updates. Failures in the callback never
            interrupt the pipeline.

    Returns:
        PipelineResult with raw_data, sql, module, etc.
    """
    start_time = time.time()
    history = RetryHistory()

    def _notify(stage: str, detail: str):
        """Fire progress_callback if set; never raise."""
        if progress_callback is not None:
            try:
                progress_callback(stage, detail)
            except Exception:
                pass

    # ── Step 0: Setup ─────────────────────────────────────────────────

    # Validate module
    if module not in MODULES:
        return PipelineResult(
            success=False,
            error_message=f"Unknown module: {module}",
            module=module,
        )

    module_cfg = MODULES[module]
    scope_columns = module_cfg.get("scope_columns", {})
    db_type = module_cfg.get("db_type", "postgres")

    if context is None:
        context = PipelineContext()

    context.add("pipeline_v2", "module", module)
    context.add("pipeline_v2", "db_type", db_type)

    # Enrich user_summary with assumption for downstream prompts
    effective_summary = user_summary
    if assumption:
        if effective_summary:
            effective_summary = (
                f"{effective_summary}\n\n"
                f"Note: This query is ambiguous. Assumption made: {assumption}"
            )
        else:
            effective_summary = f"Assumption made about ambiguous query: {assumption}"
        context.add("pipeline_v2", "assumption_passed", assumption)

    # Persist user_summary on context for format engine (e.g. KPI label)
    if user_summary:
        context.user_summary = user_summary

    # Build join graph (singleton)
    join_graph = None
    try:
        join_graph = _get_join_graph(db)
        context.add("pipeline_v2", "join_graph", "loaded")
    except Exception as e:
        logger.warning("v2 pipeline: failed to load join graph: %s", e)
        context.add("pipeline_v2", "join_graph_error", str(e)[:200])

    # Build seed_tables from previous SQL for follow-up context
    seed_tables = None
    if previous_query_sql:
        seed_tables = _extract_tables_from_sql(previous_query_sql)

    # ── Step 1: Cache Check ──────────────────────────────────────────

    _notify("cache_check", "Checking query cache...")

    cache_id = None
    try:
        from app.utils.jay.query_cache import find_exact_match
        cached = find_exact_match(query, db)
        if cached:
            cached_sql = cached.get("sql")
            cache_id = cached.get("id")
            if cached_sql:
                logger.info(
                    "v2 pipeline: cache hit (id=%s, similarity=%.4f)",
                    cache_id,
                    cached.get("similarity", 0),
                )
                context.add("pipeline_v2", "cache_hit", True)
                context.add("pipeline_v2", "cache_id", cache_id)

                # Skip directly to scope injection -> safety -> execute
                _notify("executing", "Running cached query...")
                return _scope_safety_execute(
                    sql=cached_sql,
                    query=query,
                    module=module,
                    scope=scope,
                    scope_columns=scope_columns,
                    db_type=db_type,
                    format_hint=format_hint,
                    db=db,
                    synapse_db=synapse_db,
                    domains=domains,
                    context=context,
                    history=history,
                    start_time=start_time,
                    assumption=assumption,
                    cache_id=cache_id,
                    # No schema_ctx/technical_spec available on cache path
                    schema_ctx=None,
                    technical_spec=None,
                    resolved_entities=resolved_entities,
                    join_graph=join_graph,
                    progress_callback=progress_callback,
                )
    except Exception as e:
        logger.debug("v2 pipeline: cache check failed (non-fatal): %s", e)

    context.add("pipeline_v2", "cache_hit", False)

    # ── Step 2: LLM Call 1 — Technical Spec ──────────────────────────

    _notify("technical_spec", "Analyzing your question...")
    _check_pipeline_timeout(start_time, "technical_spec")

    technical_spec = None
    try:
        technical_spec = _generate_technical_spec(
            query=query,
            module=module,
            domains=domains,
            previous_query_sql=previous_query_sql,
            previous_module=previous_module,
            previous_domains=previous_domains,
            previous_user_summary=previous_user_summary,
            resolved_entities=resolved_entities,
            assumption=assumption,
            db=db,
            context=context,
            db_type=db_type,
        )
    except PipelineTimeoutError:
        raise
    except Exception as e:
        logger.error("v2 pipeline: technical spec generation failed: %s", e)
        context.add("technical_spec", "error", str(e)[:300])
        # Fall through to tool fallback (technical_spec stays None)

    if technical_spec:
        context.add("technical_spec", "tables_needed", technical_spec.get("tables_needed", []))
        context.add("technical_spec", "confidence", technical_spec.get("confidence", "unknown"))
        # Store query_type for format engine (existential, ambiguous, etc.)
        query_type = technical_spec.get("query_type", "data")
        context.add("technical_spec", "query_type", query_type)
        logger.info(
            "v2 pipeline: technical spec generated (tables=%s, confidence=%s)",
            technical_spec.get("tables_needed", []),
            technical_spec.get("confidence", "unknown"),
        )

    # ── Step 3: Schema Assembly (Python, no LLM) ─────────────────────

    _notify("schema_assembly", "Finding relevant tables...")

    schema_ctx = None
    if technical_spec:
        _check_pipeline_timeout(start_time, "schema_assembly")
        try:
            from app.utils.jay.schema_assembler import assemble_schema_context

            schema_ctx = assemble_schema_context(
                query=query,
                technical_spec=technical_spec,
                module=module,
                domains=domains or [],
                resolved_entities=resolved_entities or {},
                db_session=db,
                join_graph=join_graph,
                previous_context=previous_user_summary,
                seed_tables=seed_tables,
                compact=True,
            )

            context.add("schema_assembly", "has_good_context", schema_ctx.has_good_context)
            context.add("schema_assembly", "retrieval_time_ms", round(schema_ctx.retrieval_time_ms, 1))
            context.add("schema_assembly", "ddl_length", len(schema_ctx.ddl_context))
            context.add("schema_assembly", "tables_found", len(schema_ctx.expanded_tables))
            logger.info(
                "v2 pipeline: schema assembly complete (good=%s, tables=%d, ddl=%d chars, %.0fms)",
                schema_ctx.has_good_context,
                len(schema_ctx.expanded_tables),
                len(schema_ctx.ddl_context),
                schema_ctx.retrieval_time_ms,
            )
        except PipelineTimeoutError:
            raise
        except Exception as e:
            logger.warning("v2 pipeline: schema assembly failed, falling back to tool loop: %s", e)
            context.add("schema_assembly", "error", str(e)[:300])
            # schema_ctx stays None -> triggers tool fallback

    # ── Step 4: SQL Generation ───────────────────────────────────────

    _notify("sql_generation", "Generating query...")
    _check_pipeline_timeout(start_time, "sql_generation")
    sql = None

    # 4a: If we have good schema context -> direct LLM call (no tools)
    if schema_ctx and schema_ctx.has_good_context:
        try:
            sql = _generate_sql_with_context(
                query=query,
                technical_spec=technical_spec,
                schema_ctx=schema_ctx,
                resolved_entities=resolved_entities,
                format_hint=format_hint,
                user_summary=effective_summary,
                assumption=assumption,
                previous_query_sql=previous_query_sql,
                previous_user_summary=previous_user_summary,
                db_type=db_type,
                context=context,
            )
        except PipelineTimeoutError:
            raise
        except Exception as e:
            logger.error("v2 pipeline: SQL generation (retrieval path) failed: %s", e)
            context.add("sql_generation", "retrieval_error", str(e)[:300])
            # Fall through to tool fallback

    # Treat CANNOT_GENERATE from retrieval path as empty -> fall through to tool fallback
    if sql and sql.upper().startswith("CANNOT_GENERATE"):
        logger.info("v2 pipeline: retrieval path returned CANNOT_GENERATE, trying tool fallback")
        context.add("sql_generation", "retrieval_cannot_generate", sql)
        sql = None

    # 4b: Tool loop fallback (no good context, or retrieval path failed)
    if not sql:
        context.add("sql_generation", "path", "tool_fallback")
        try:
            sql = _generate_sql_with_tool_fallback(
                query=query,
                module=module,
                resolved_entities=resolved_entities,
                assumption=assumption,
                db=db,
                join_graph=join_graph,
                context=context,
                start_time=start_time,
            )
        except PipelineTimeoutError:
            raise
        except Exception as e:
            logger.error("v2 pipeline: SQL generation (tool fallback) failed: %s", e)
            context.add("sql_generation", "fallback_error", str(e)[:300])

    if not sql:
        return PipelineResult(
            success=False,
            module=module,
            format_hint=format_hint,
            error_message=(
                "I couldn't generate a valid query for your question. "
                "Please try rephrasing."
            ),
            attempts=0,
            retry_history=history,
            context=context,
        )

    # Check for CANNOT_GENERATE
    if sql.upper().startswith("CANNOT_GENERATE"):
        reason = sql.split(":", 1)[1].strip() if ":" in sql else "Unknown reason"
        logger.warning("v2 pipeline: LLM returned CANNOT_GENERATE: %s", reason)
        return PipelineResult(
            success=False,
            module=module,
            format_hint=format_hint,
            error_message=(
                f"I couldn't find the right tables or columns to answer that. "
                f"Reason: {reason}"
            ),
            attempts=0,
            retry_history=history,
            context=context,
        )

    context.add("sql_generation", "generated_sql", sql)

    # ── Step 4c: Column Validation Gate (pre-execution) ──────────────

    if schema_ctx and schema_ctx.ddl_context:
        try:
            from app.utils.jay.column_validator import validate_columns
            from app.utils.jay.schema_assembler import extract_column_inventory

            col_inventory = extract_column_inventory(schema_ctx.ddl_context)
            logger.info(
                "Column validation: inventory has %d tables: %s",
                len(col_inventory),
                {k: len(v) for k, v in col_inventory.items()},
            )
            if col_inventory:
                validation = validate_columns(sql, col_inventory)
                context.add("column_validation", "is_valid", validation.is_valid)
                context.add("column_validation", "inventory_tables", list(col_inventory.keys()))

                if not validation.is_valid:
                    context.add("column_validation", "invalid_columns",
                                [f"{c['alias']}.{c['column']}" for c in validation.invalid_columns])
                    context.add("column_validation", "suggestions", validation.suggestions)

                    if validation.corrected_sql:
                        # Auto-corrected (fuzzy match found)
                        logger.info("Column validation: auto-corrected SQL")
                        sql = validation.corrected_sql
                        context.add("column_validation", "auto_corrected", True)
                    else:
                        # Try LLM-based column correction (1 attempt)
                        logger.warning("Column validation failed, attempting LLM correction")
                        _check_pipeline_timeout(start_time, "column_correction")
                        try:
                            from app.utils.jay.prompts_v2 import build_column_correction_prompt

                            corr_sys, corr_user = build_column_correction_prompt(
                                query=query,
                                failed_sql=sql,
                                invalid_columns=validation.invalid_columns,
                                column_inventory=col_inventory,
                            )
                            # Use fast tier to avoid content filter on correction prompts
                            client, deployment = get_llm_client(tier="fast")
                            corr_resp = client.chat.completions.create(
                                model=deployment,
                                messages=[
                                    {"role": "system", "content": corr_sys},
                                    {"role": "user", "content": corr_user},
                                ],
                                **_get_llm_kwargs(deployment, temperature=0.0),
                            )
                            corrected_raw = (corr_resp.choices[0].message.content or "").strip()
                            corrected_sql = _extract_sql(corrected_raw)
                            if corrected_sql:
                                # Re-validate the corrected SQL
                                re_val = validate_columns(corrected_sql, col_inventory)
                                if re_val.is_valid:
                                    sql = corrected_sql
                                    context.add("column_validation", "llm_corrected", True)
                                    logger.info("Column correction succeeded via LLM")
                                else:
                                    logger.warning("LLM correction still has invalid columns, proceeding anyway")
                                    context.add("column_validation", "llm_correction_failed", True)
                        except Exception as e:
                            logger.warning("Column correction LLM call failed: %s", e)
                            context.add("column_validation", "correction_error", str(e)[:200])
        except Exception as e:
            logger.debug("Column validation skipped: %s", e)

    # ── Steps 5-9: Scope Injection -> Safety -> Execute -> Recovery ──

    _notify("executing", "Running query...")
    return _scope_safety_execute(
        sql=sql,
        query=query,
        module=module,
        scope=scope,
        scope_columns=scope_columns,
        db_type=db_type,
        format_hint=format_hint,
        db=db,
        synapse_db=synapse_db,
        domains=domains,
        context=context,
        history=history,
        start_time=start_time,
        assumption=assumption,
        cache_id=None,
        schema_ctx=schema_ctx,
        technical_spec=technical_spec,
        resolved_entities=resolved_entities,
        join_graph=join_graph,
        progress_callback=progress_callback,
    )


# ===========================================================================
# Sub-pipelines
# ===========================================================================


def _generate_technical_spec(
    query: str,
    module: str,
    domains: List[str],
    previous_query_sql: str,
    previous_module: str,
    previous_domains: List[str],
    previous_user_summary: str,
    resolved_entities: Dict[str, str],
    assumption: str,
    db: Session,
    context: PipelineContext,
    db_type: str = "postgres",
) -> Optional[dict]:
    """LLM Call 1: Generate a structured technical spec from the user's query.

    Single LLM call, no tools, JSON response format.

    Returns:
        Parsed dict with tables_needed, columns_needed, joins, filters, etc.
        or None on failure.
    """
    from app.utils.jay.knowledge_cache import (
        get_synonym_context_for_module,
        get_glossary_context_for_module,
    )
    from app.utils.jay.query_cache import find_similar_queries
    from app.utils.jay.prompts_v2 import build_technical_spec_prompt

    # Gather context inputs — scoped to the current module to save tokens
    synonym_ctx = ""
    try:
        synonym_ctx = get_synonym_context_for_module(module)
    except Exception as e:
        logger.debug("v2 pipeline: synonym context failed: %s", e)

    glossary_ctx = ""
    try:
        glossary_ctx = get_glossary_context_for_module(module)
    except Exception as e:
        logger.debug("v2 pipeline: glossary context failed: %s", e)

    similar = []
    try:
        similar = find_similar_queries(query, db, top_k=3, threshold=0.80)
    except Exception as e:
        logger.debug("v2 pipeline: similar queries failed: %s", e)

    # Gather categorical values for filter matching
    categorical_ctx = ""
    try:
        from app.utils.jay.knowledge_cache import get_all_categorical_value_maps
        cat_maps = get_all_categorical_value_maps()
        if cat_maps:
            lines = ["KNOWN CATEGORICAL VALUES (use exact casing from these):"]
            for col, value_map in cat_maps.items():
                # Limit to 30 values per column to keep prompt reasonable
                values = list(value_map.values())[:30]
                if values:
                    lines.append(f"  {col}: {', '.join(values)}")
            categorical_ctx = "\n".join(lines)
    except Exception as e:
        logger.debug("v2 pipeline: categorical values failed: %s", e)

    # Build previous context dict for follow-up handling
    previous_context = None
    if previous_query_sql or previous_user_summary or previous_module:
        previous_context = {}
        if previous_module:
            previous_context["module"] = previous_module
        if previous_domains:
            previous_context["domains"] = previous_domains
        if previous_user_summary:
            previous_context["user_summary"] = previous_user_summary
        if previous_query_sql:
            previous_context["sql"] = previous_query_sql

    system_prompt, user_message = build_technical_spec_prompt(
        query=query,
        module=module,
        domains=domains,
        synonym_context=synonym_ctx,
        glossary_context=glossary_ctx,
        similar_queries=similar,
        previous_context=previous_context,
        resolved_entities=resolved_entities,
        assumption=assumption,
        categorical_values=categorical_ctx,
    )

    # Single LLM call - no tools (fast tier: classification task)
    client, deployment = get_llm_client(tier="fast")
    response = client.chat.completions.create(
        model=deployment,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message},
        ],
        **_get_llm_kwargs(deployment, temperature=0.0),
        response_format={"type": "json_object"},
    )

    raw_content = (response.choices[0].message.content or "").strip()
    context.add("technical_spec", "raw_response_preview", raw_content[:500])

    if not raw_content:
        logger.warning("v2 pipeline: technical spec LLM returned empty response")
        return None

    # Parse JSON (handle possible markdown fences)
    spec = _parse_json_response(raw_content)
    if spec and isinstance(spec, dict):
        # Inject db_type so downstream prompts know the dialect
        spec["db_type"] = "SYNAPSE" if db_type == "synapse" else "POSTGRESQL"
        return spec

    logger.warning("v2 pipeline: could not parse technical spec from LLM response")
    return None


def _parse_json_response(content: str) -> Optional[dict]:
    """Parse a JSON object from LLM response content.

    Handles: pure JSON, JSON in ```json ... ``` fences, JSON embedded in text.
    """
    if not content:
        return None

    text = content.strip()

    # Try code fences
    fence_pattern = r'```(?:json)?\s*\n?(.*?)\n?\s*```'
    fence_matches = re.findall(fence_pattern, text, re.DOTALL | re.IGNORECASE)
    if fence_matches:
        for match in reversed(fence_matches):
            try:
                return json.loads(match.strip())
            except (json.JSONDecodeError, TypeError):
                continue

    # Try parsing as-is
    try:
        return json.loads(text)
    except (json.JSONDecodeError, TypeError):
        pass

    # Try to find JSON object with balanced braces
    brace_start = text.find("{")
    if brace_start >= 0:
        depth = 0
        for i in range(brace_start, len(text)):
            if text[i] == "{":
                depth += 1
            elif text[i] == "}":
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(text[brace_start:i + 1])
                    except (json.JSONDecodeError, TypeError):
                        pass
                    break

    return None


def _generate_sql_with_context(
    query: str,
    technical_spec: dict,
    schema_ctx,  # SchemaContext
    resolved_entities: Dict[str, str],
    format_hint: str,
    user_summary: str,
    assumption: str,
    previous_query_sql: str,
    previous_user_summary: str,
    db_type: str,
    context: PipelineContext,
) -> Optional[str]:
    """LLM Call 2 (happy path): Generate SQL from spec + pre-retrieved context.

    Single LLM call, no tools. All schema context is provided in the prompt.

    Returns:
        SQL string, or None on failure.
    """
    from app.utils.jay.prompts_v2 import build_sql_generation_retrieval_prompt

    # Inject db_type into spec for the prompt builder
    spec_with_db = dict(technical_spec)
    spec_with_db["db_type"] = "SYNAPSE" if db_type == "synapse" else "POSTGRESQL"

    system_prompt, user_message = build_sql_generation_retrieval_prompt(
        query=query,
        technical_spec=spec_with_db,
        ddl_context=schema_ctx.ddl_context,
        join_context=schema_ctx.join_context,
        column_values=schema_ctx.column_values,
        resolved_entities=resolved_entities,
        format_hint=format_hint,
        user_summary=user_summary,
        assumption=assumption,
        previous_sql=previous_query_sql,
        previous_user_summary=previous_user_summary,
    )

    client, deployment = get_llm_client()
    response = client.chat.completions.create(
        model=deployment,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message},
        ],
        **_get_llm_kwargs(deployment, temperature=0.0),
    )

    raw_content = (response.choices[0].message.content or "").strip()
    context.add("sql_generation", "path", "retrieval")
    context.add("sql_generation", "raw_response_preview", raw_content[:500])

    sql = _extract_sql(raw_content)
    if sql:
        logger.info("v2 pipeline: SQL generated via retrieval path (%d chars)", len(sql))
    else:
        logger.warning(
            "v2 pipeline: could not extract SQL from retrieval response (len=%d)",
            len(raw_content),
        )
        context.add("sql_generation", "extraction_failed", True)

    return sql


def _generate_sql_with_tool_fallback(
    query: str,
    module: str,
    resolved_entities: Dict[str, str],
    assumption: str,
    db: Session,
    join_graph,
    context: PipelineContext,
    start_time: float,
) -> Optional[str]:
    """Fallback path: Schema exploration via slim tool loop when retrieval failed.

    Uses 4 tools, max 3 iterations, 20s timeout.

    Returns:
        SQL string, or None on failure.
    """
    from app.utils.jay.tool_engine import run_tool_loop
    from app.utils.jay.prompts_v2 import build_tool_fallback_prompt

    system_prompt, user_message = build_tool_fallback_prompt(
        query=query,
        module=module,
        resolved_entities=resolved_entities,
        assumption=assumption,
    )

    fallback_tools = _filter_tool_schemas(_TOOL_FALLBACK_NAMES)

    # Calculate timeout: reserve 15s for scope+safety+execute+recovery
    remaining = _remaining_seconds(start_time) - 15
    fallback_timeout = max(min(remaining, 20.0), 8.0)

    logger.info(
        "v2 pipeline: starting tool fallback (timeout=%.1fs, tools=%d)",
        fallback_timeout,
        len(fallback_tools),
    )

    # Use fast tier (gpt-4o-mini) for tool fallback to avoid Azure
    # content filter jailbreak false positives on reasoning models.
    result: ToolLoopResult = run_tool_loop(
        system_prompt=system_prompt,
        user_message=user_message,
        tools=fallback_tools,
        db_session=db,
        join_graph=join_graph,
        max_iterations=3,
        timeout_seconds=fallback_timeout,
        context=context,
        temperature=0.0,
        tier="fast",
    )

    if result.error:
        logger.error("v2 pipeline: tool fallback error: %s", result.error)
        context.add("sql_generation", "fallback_tool_error", result.error)
        return None

    context.add("sql_generation", "fallback_iterations", result.iterations)
    context.add("sql_generation", "fallback_tool_calls", len(result.tool_calls_made))

    sql = _extract_sql(result.content)
    if sql:
        logger.info("v2 pipeline: SQL generated via tool fallback (%d chars)", len(sql))
    else:
        logger.warning("v2 pipeline: tool fallback did not produce valid SQL")
        context.add("sql_generation", "fallback_extraction_failed", True)
        context.add("sql_generation", "fallback_raw_preview", result.content[:500])

    return sql


# ===========================================================================
# Scope Injection -> Safety Review -> Execute -> Error Recovery
# ===========================================================================


def _notify(callback, stage, detail):
    """Fire progress callback if set; never raise."""
    if callback:
        try:
            callback(stage, detail)
        except Exception:
            pass


def _scope_safety_execute(
    sql: str,
    query: str,
    module: str,
    scope: Dict[str, str],
    scope_columns: Dict[str, str],
    db_type: str,
    format_hint: str,
    db: Session,
    synapse_db: Session,
    domains: List[str],
    context: PipelineContext,
    history: RetryHistory,
    start_time: float,
    assumption: str = None,
    cache_id: int = None,
    schema_ctx=None,    # SchemaContext or None
    technical_spec: dict = None,
    resolved_entities: Dict[str, str] = None,
    join_graph=None,
    progress_callback=None,
) -> PipelineResult:
    """Execute the post-generation pipeline with error recovery.

    Steps:
        5. Scope injection (programmatic)
        6. Safety review (programmatic)
        7. Execute query
        8. Error recovery (tool-assisted, up to MAX_ERROR_RECOVERY_ATTEMPTS)
        9. Cache store on success

    On execution failure, runs error recovery tool loops with limited tools
    (get_column_values, get_table_schema).
    """
    current_sql = sql
    attempt = 0

    while attempt <= MAX_ERROR_RECOVERY_ATTEMPTS:
        attempt += 1

        try:
            # ── Step 5: Scope Injection (CRITICAL SECURITY) ───────────
            _notify(progress_callback, "scope_injection", "Applying security scope...")
            _check_pipeline_timeout(start_time, "scope_injection")

            # Normalize double-brace placeholder from LLM to single-brace
            normalized_sql = current_sql.replace("{{SCOPE_FILTER}}", "{SCOPE_FILTER}")
            scoped_sql = normalized_sql
            try:
                scoped_sql = inject_scope(
                    sql=normalized_sql,
                    scope=scope,
                    scope_columns=scope_columns,
                    db_type=db_type,
                )
            except ScopeInjectionError as e:
                # Structural failure (e.g., unparseable SQL) — log and continue
                logger.warning(
                    "v2 pipeline: scope injection structural failure (non-fatal): %s", e
                )
                context.add("scope_injection", "warning", str(e)[:200])
                if not assumption:
                    assumption = (
                        "Showing data across all entities. "
                        "Select an entity for filtered results."
                    )

            # ── Step 6: Safety Review ─────────────────────────────────
            _notify(progress_callback, "safety_review", "Reviewing query safety...")
            _check_pipeline_timeout(start_time, "safety_review")

            safety = review_sql_safety(
                sql=scoped_sql,
                module=module,
                context=context,
                domains=domains,
            )

            if not safety.is_safe:
                logger.error(
                    "v2 pipeline: safety review blocked SQL (attempt %d): %s",
                    attempt,
                    safety.issues,
                )
                context.add(
                    "safety_review",
                    f"attempt_{attempt}_blocked",
                    "; ".join(safety.issues),
                )

                history.add(RetryAttempt(
                    attempt=attempt,
                    module=module,
                    sql=current_sql,
                    error_category="safety_blocked",
                    error_message="; ".join(safety.issues),
                    remediation=(
                        "Query was blocked by safety review. "
                        "Use only SELECT on tables from the provided DDL."
                    ),
                    stage="safety_review",
                ))

                # Try error recovery if budget allows
                if attempt <= MAX_ERROR_RECOVERY_ATTEMPTS:
                    recovered = _try_error_recovery(
                        query=query,
                        failed_sql=current_sql,
                        error_message=f"Safety review blocked: {'; '.join(safety.issues)}",
                        schema_ctx=schema_ctx,
                        db=db,
                        join_graph=join_graph,
                        context=context,
                        start_time=start_time,
                        history=history,
                        attempt=attempt,
                    )
                    if recovered:
                        current_sql = recovered
                        continue

                _try_store_negative_example(
                    query, current_sql,
                    f"safety_blocked: {'; '.join(safety.issues)}", db,
                )
                return PipelineResult(
                    success=False,
                    sql=current_sql,
                    module=module,
                    format_hint=format_hint,
                    error_message=(
                        "I wasn't able to answer that question safely. "
                        "Could you try rephrasing it?"
                    ),
                    attempts=attempt,
                    safety_blocked=True,
                    retry_history=history,
                    context=context,
                )

            # ── Step 7: Execution ─────────────────────────────────────
            _notify(progress_callback, "executing", "Running query...")
            _check_pipeline_timeout(start_time, "execution")

            raw_data = execute_query(
                module=module,
                sql=scoped_sql,
                params=[],
                db=db,
                synapse_db=synapse_db,
            )

            exec_time_ms = (time.time() - start_time) * 1000

            logger.info(
                "v2 pipeline: success (module=%s, attempt=%d, rows=%d, time=%.0fms)",
                module,
                attempt,
                len(raw_data),
                exec_time_ms,
            )

            context.add("execution", "row_count", len(raw_data))
            context.add(
                "execution",
                "execution_note",
                f"Returned {len(raw_data)} data points",
            )

            # ── Step 9: Cache Store ───────────────────────────────────
            _try_cache_store(
                query=query,
                sql=current_sql,
                module=module,
                domains=domains,
                exec_ms=exec_time_ms,
                db=db,
                technical_spec=technical_spec,
                cache_id=cache_id,
                success=True,
            )

            _notify(progress_callback, "formatting", "Formatting results...")

            return PipelineResult(
                success=True,
                raw_data=raw_data,
                sql=scoped_sql,
                module=module,
                format_hint=format_hint,
                attempts=attempt,
                context=context,
                retry_history=history,
                assumption=assumption,
            )

        except QueryExecutionError as e:
            # ── Step 8: Error Recovery ────────────────────────────────
            logger.warning(
                "v2 pipeline: query execution failed (attempt %d): %s",
                attempt,
                e,
            )

            classified = classify_error(str(e))
            logger.info(
                "v2 pipeline: classified error: %s - %s",
                classified.category,
                classified.remediation[:80],
            )

            context.add("execution", f"attempt_{attempt}_error", str(e)[:300])
            context.add(
                "execution",
                f"attempt_{attempt}_error_type",
                classified.category,
            )

            history.add(RetryAttempt(
                attempt=attempt,
                module=module,
                sql=current_sql,
                error_category=classified.category,
                error_message=classified.raw_message,
                remediation=classified.remediation,
                stage="execution",
            ))

            # Attempt error recovery if budget and time allow
            if attempt <= MAX_ERROR_RECOVERY_ATTEMPTS:
                # Skip recovery if we are running low on time
                if _remaining_seconds(start_time) < 8:
                    logger.warning(
                        "v2 pipeline: skipping error recovery (%.1fs remaining)",
                        _remaining_seconds(start_time),
                    )
                else:
                    error_for_llm = format_classified_error_for_llm(
                        classified, attempt, MAX_ERROR_RECOVERY_ATTEMPTS + 1
                    )
                    recovered = _try_error_recovery(
                        query=query,
                        failed_sql=current_sql,
                        error_message=error_for_llm,
                        schema_ctx=schema_ctx,
                        db=db,
                        join_graph=join_graph,
                        context=context,
                        start_time=start_time,
                        history=history,
                        attempt=attempt,
                    )
                    if recovered:
                        current_sql = recovered
                        continue

            # No more recovery attempts or recovery failed
            _try_store_negative_example(query, current_sql, str(e), db)
            _try_cache_store(
                query=query,
                sql=current_sql,
                module=module,
                domains=domains,
                exec_ms=(time.time() - start_time) * 1000,
                db=db,
                technical_spec=technical_spec,
                cache_id=cache_id,
                success=False,
            )

            return PipelineResult(
                success=False,
                sql=current_sql,
                module=module,
                format_hint=format_hint,
                error_message=(
                    "An error occurred while processing your query. "
                    "Please try rephrasing."
                ),
                attempts=attempt,
                retry_history=history,
                context=context,
            )

        except PipelineTimeoutError:
            logger.error("v2 pipeline: timeout on attempt %d", attempt)
            return PipelineResult(
                success=False,
                module=module,
                format_hint=format_hint,
                error_message="Pipeline timeout exceeded. Please try a simpler query.",
                attempts=attempt,
                retry_history=history,
                context=context,
            )

        except Exception as e:
            logger.error(
                "v2 pipeline: unexpected error (attempt %d): %s", attempt, e
            )

            classified = classify_error(str(e))
            context.add(
                "execution", f"attempt_{attempt}_unexpected", str(e)[:300]
            )

            history.add(RetryAttempt(
                attempt=attempt,
                module=module,
                sql=current_sql,
                error_category=classified.category,
                error_message=str(e)[:200],
                remediation=classified.remediation,
                stage="execution",
            ))

            # Try recovery for unexpected errors too
            if attempt <= MAX_ERROR_RECOVERY_ATTEMPTS:
                recovered = _try_error_recovery(
                    query=query,
                    failed_sql=current_sql,
                    error_message=str(e)[:500],
                    schema_ctx=schema_ctx,
                    db=db,
                    join_graph=join_graph,
                    context=context,
                    start_time=start_time,
                    history=history,
                    attempt=attempt,
                )
                if recovered:
                    current_sql = recovered
                    continue

            _try_store_negative_example(query, current_sql, str(e), db)
            return PipelineResult(
                success=False,
                sql=current_sql,
                module=module,
                format_hint=format_hint,
                error_message="An unexpected error occurred. Please try again.",
                attempts=attempt,
                retry_history=history,
                context=context,
            )

    # Should not reach here, but handle gracefully
    return PipelineResult(
        success=False,
        module=module,
        format_hint=format_hint,
        error_message="Pipeline exhausted all recovery attempts. Please try rephrasing.",
        attempts=attempt,
        retry_history=history,
        context=context,
    )


# ===========================================================================
# Error Recovery
# ===========================================================================


def _try_error_recovery(
    query: str,
    failed_sql: str,
    error_message: str,
    schema_ctx,  # SchemaContext or None
    db: Session,
    join_graph,
    context: PipelineContext,
    start_time: float,
    history: RetryHistory,
    attempt: int,
) -> Optional[str]:
    """Attempt error recovery via a limited tool loop. Returns corrected SQL or None.

    Uses build_error_recovery_prompt with DDL context from schema_ctx (if available)
    and limited tools (get_column_values, get_table_schema). Max 2 iterations, 15s timeout.

    Never raises — returns None on any failure.
    """
    try:
        _check_pipeline_timeout(start_time, "error_recovery")
    except PipelineTimeoutError:
        logger.warning("v2 pipeline: timeout before error recovery")
        return None

    try:
        from app.utils.jay.tool_engine import run_tool_loop
        from app.utils.jay.prompts_v2 import build_error_recovery_prompt

        ddl_context = schema_ctx.ddl_context if schema_ctx else ""
        column_values = schema_ctx.column_values if schema_ctx else {}

        system_prompt, user_message = build_error_recovery_prompt(
            query=query,
            failed_sql=failed_sql,
            error_message=error_message,
            ddl_context=ddl_context,
            column_values=column_values,
            attempt_number=attempt,
        )

        recovery_tools = _filter_tool_schemas(_ERROR_RECOVERY_NAMES)

        # Calculate timeout: reserve 5s for re-execution
        remaining = _remaining_seconds(start_time) - 5
        recovery_timeout = max(min(remaining, 15.0), 5.0)

        logger.info(
            "v2 pipeline: starting error recovery (attempt=%d, timeout=%.1fs)",
            attempt,
            recovery_timeout,
        )

        # Use fast tier (gpt-4o-mini) for error recovery to avoid Azure
        # content filter jailbreak false positives on reasoning models.
        result: ToolLoopResult = run_tool_loop(
            system_prompt=system_prompt,
            user_message=user_message,
            tools=recovery_tools,
            db_session=db,
            join_graph=join_graph,
            max_iterations=4,
            timeout_seconds=recovery_timeout,
            context=context,
            temperature=0.0,
            tier="fast",
        )

        if result.error:
            logger.error("v2 pipeline: error recovery tool loop failed: %s", result.error)
            return None

        sql = _extract_sql(result.content)
        if sql:
            logger.info(
                "v2 pipeline: error recovery produced corrected SQL (%d chars)", len(sql)
            )
            context.add("error_recovery", f"attempt_{attempt}_corrected_sql", sql)
            context.add(
                "error_recovery",
                f"attempt_{attempt}_tool_calls",
                len(result.tool_calls_made),
            )
            return sql
        else:
            logger.warning("v2 pipeline: error recovery did not produce valid SQL")
            context.add("error_recovery", f"attempt_{attempt}_extraction_failed", True)
            return None

    except PipelineTimeoutError:
        logger.warning("v2 pipeline: timeout during error recovery")
        return None
    except Exception as e:
        logger.error("v2 pipeline: error recovery unexpected failure: %s", e)
        return None
