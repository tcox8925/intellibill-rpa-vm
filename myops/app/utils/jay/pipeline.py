"""
JAI Pipeline Orchestrator - Orchestrates the multi-step LLM pipeline with retry.

Flow:
  Attempts 1-2: SQL Generation → Scope Injection → Safety Review → Execute
    -> On error: classified error + retry history fed to next attempt
  Attempt 3 (full restart): Re-run intent detection with full failure context,
    then SQL Generation → Scope Injection → Safety Review → Execute

Features:
  - Classified errors with structured remediation hints
  - Duplicate SQL detection across retry attempts
  - Full pipeline restart from intent detection on final attempt
  - Accumulated RetryHistory passed through every step
"""

import os
import re
import time
import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Dict, Optional, List, Any

from sqlalchemy.orm import Session

from app.utils.jay.pipeline_models import (
    PipelineResult, SQLGenerationResult, PipelineContext,
    RetryHistory, RetryAttempt,
)
from app.utils.jay.llm_client import get_llm_client
from app.utils.jay.sql_generator import generate_sql, SQLGenerationError
from app.utils.jay.scope_injector import inject_scope, ScopeInjectionError
from app.utils.jay.safety_reviewer import review_sql_safety
from app.utils.jay.db_executor import execute_query, QueryExecutionError
from app.utils.jay.error_classifier import classify_error, format_classified_error_for_llm
from app.utils.jay.semantic_registry import MODULES

logger = logging.getLogger(__name__)


def _try_store_negative_example(query: str, sql: Optional[str], error: str, db_session) -> None:
    """Attempt to store a negative example for self-learning. Never raises."""
    if db_session is None:
        return
    try:
        from app.utils.jay.self_learning import store_negative_example
        store_negative_example(query, sql or "", error, db_session)
    except Exception:
        pass  # Don't fail pipeline over learning failure


MAX_RETRY_ATTEMPTS = 3
# If LLM produces identical SQL on retry, bump temperature and regenerate (up to this many times)
MAX_DUPLICATE_RETRIES = 2
# Global timeout for entire pipeline (prevents runaway LLM calls)
PIPELINE_TIMEOUT_SECONDS = 120  # 2 minutes max for entire pipeline


class PipelineTimeoutError(Exception):
    """Raised when the overall pipeline timeout is exceeded."""
    pass


def _check_pipeline_timeout(start_time: float, stage: str):
    """Check if pipeline has exceeded the global timeout.

    Args:
        start_time: time.time() when the pipeline started.
        stage: Name of the current stage (for logging).

    Raises:
        PipelineTimeoutError if time exceeded.
    """
    elapsed = time.time() - start_time
    if elapsed > PIPELINE_TIMEOUT_SECONDS:
        logger.error(f"Pipeline timeout exceeded at stage '{stage}' after {elapsed:.1f}s")
        raise PipelineTimeoutError(
            f"Pipeline timeout exceeded ({elapsed:.0f}s > {PIPELINE_TIMEOUT_SECONDS}s) at {stage}"
        )

# --- Column Retrieval helpers ---

_join_graph_cache = None
_join_graph_lock = threading.Lock()


def _is_column_retrieval_enabled() -> bool:
    """Check if semantic column retrieval is enabled via env var (kill switch).

    Defaults to "true". Set JAI_COLUMN_RETRIEVAL_ENABLED=false to disable.
    """
    return os.environ.get("JAI_COLUMN_RETRIEVAL_ENABLED", "true").lower() in ("true", "1", "yes")


def _get_join_graph(db_session):
    """Get or create the cached join graph (singleton, thread-safe).

    The join graph is expensive to build (queries information_schema), so
    it is cached at module level for the lifetime of the process.
    """
    global _join_graph_cache
    if _join_graph_cache is None:
        with _join_graph_lock:
            if _join_graph_cache is None:
                from app.utils.jay.fk_inference import discover_join_graph
                _join_graph_cache = discover_join_graph(db_session)
    return _join_graph_cache


def _build_join_context_from_retrieval(result) -> str:
    """Build SQL JOIN context text from a SchemaRetrievalResult.

    Formats each join path as a LEFT JOIN clause that can be injected
    into the SQL generation prompt.

    Args:
        result: SchemaRetrievalResult with join_paths.

    Returns:
        Multi-line string with JOIN context comments and SQL, or empty string.
    """
    if not result.join_paths:
        return ""
    lines = ["-- JOIN CONTEXT (from semantic column retrieval)", ""]
    for jp in result.join_paths:
        lines.append(f"-- {jp.from_table}.{jp.from_col} -> {jp.to_table}.{jp.to_col}")
        lines.append(f"LEFT JOIN {jp.to_table} ON {jp.from_table}.{jp.from_col} = {jp.to_table}.{jp.to_col}")
        lines.append("")
    return "\n".join(lines)


def _extract_tables_from_sql(sql: str) -> set:
    """Extract table names from FROM and JOIN clauses in SQL.

    Handles schema-qualified names (e.g., wpo.lup_agents) and optional
    aliases (with or without AS keyword). Returns short table names
    (without schema prefix) for matching against column retriever results.

    Args:
        sql: SQL query string.

    Returns:
        Set of short table names (e.g., {"lup_agents", "agents_contracts_view"}).
    """
    if not sql:
        return set()
    tables = set()
    # Match FROM/JOIN table patterns (handles schema.table and aliases)
    pattern = r'(?:FROM|JOIN)\s+(\w+\.\w+|\w+)(?:\s+(?:AS\s+)?(\w+))?'
    for match in re.finditer(pattern, sql, re.IGNORECASE):
        table = match.group(1)
        # Remove schema prefix for matching
        tables.add(table.split('.')[-1] if '.' in table else table)
    return tables


def _normalize_sql_for_comparison(sql: str) -> str:
    """Normalize SQL for duplicate comparison (collapse whitespace, lowercase, strip comments)."""
    s = re.sub(r'--[^\n]*', '', sql)             # strip line comments
    s = re.sub(r'/\*.*?\*/', '', s, flags=re.S)  # strip block comments
    s = re.sub(r'\s+', ' ', s).strip().lower()   # collapse whitespace, lowercase
    return s


def _is_duplicate_sql(sql: str, previous_sqls: List[str]) -> bool:
    """Check if the generated SQL is a duplicate of any previous attempt."""
    normalized = _normalize_sql_for_comparison(sql)
    for prev in previous_sqls:
        if _normalize_sql_for_comparison(prev) == normalized:
            return True
    return False


def _rewrite_follow_up_query(
    query: str,
    previous_user_summary: str,
    previous_module: str = None,
    context: PipelineContext = None,
) -> str:
    """Rewrite an elliptical follow-up into a standalone question.

    Only rewrites if the query looks like a follow-up (short or contains
    referential markers like 'these', 'those', 'them', etc.). Uses a
    lightweight LLM call to produce a complete, standalone question.

    Args:
        query: The user's current query.
        previous_user_summary: Summary of the previous question.
        previous_module: Module from the previous turn (for context).
        context: PipelineContext for logging the rewrite.

    Returns:
        The rewritten query, or the original query if no rewrite was needed.
    """
    referential_markers = [
        'these', 'those', 'them', 'that', 'it', 'this',
        'out of', 'from the', 'among', 'of them',
        'break down', 'break that', 'the same',
    ]
    query_lower = query.lower().strip()

    is_follow_up = (
        len(query.split()) <= 10
        or any(marker in query_lower for marker in referential_markers)
    )

    if not is_follow_up:
        return query  # Not a follow-up, return as-is

    rewrite_prompt = (
        "Rewrite the user's follow-up question as a complete, standalone question. "
        "Include all relevant context from the conversation.\n\n"
        f"Previous question: {previous_user_summary}\n"
        f"Current follow-up: {query}\n\n"
        "Rewritten standalone question (one sentence, no explanation):"
    )

    try:
        client, deployment = get_llm_client()
        resp = client.chat.completions.create(
            model=deployment,
            temperature=0.0,
            max_tokens=150,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You rewrite follow-up questions into standalone questions. "
                        "Output ONLY the rewritten question, nothing else."
                    ),
                },
                {"role": "user", "content": rewrite_prompt},
            ],
        )
        rewritten = (resp.choices[0].message.content or "").strip().strip('"').strip("'")
        if rewritten and len(rewritten) > len(query) * 0.5:
            if context:
                context.add("rewrite", "original_query", query)
                context.add("rewrite", "rewritten_query", rewritten)
            logger.info(f"Query rewritten: '{query}' -> '{rewritten}'")
            return rewritten
    except Exception as e:
        logger.warning(f"Query rewrite failed, using original: {e}")

    return query


def _sql_has_where_filters(sql: str) -> bool:
    """Check if SQL has meaningful WHERE filters beyond just the scope placeholder.

    Returns True if the SQL has a WHERE clause with conditions other than
    {SCOPE_FILTER}. Used to determine if an empty result set is likely a
    semantic error vs. a legitimate zero-count query.
    """
    if not sql:
        return False
    # Remove scope placeholder and check if WHERE clause has real conditions
    cleaned = sql.replace("{SCOPE_FILTER}", "").replace("1=1", "")
    # Look for WHERE with actual conditions after scope removal
    where_match = re.search(r'\bWHERE\b(.+?)(?:\bGROUP\b|\bORDER\b|\bLIMIT\b|\bHAVING\b|$)',
                            cleaned, re.IGNORECASE | re.DOTALL)
    if not where_match:
        return False
    # Check if anything meaningful remains after stripping whitespace and AND/OR
    conditions = where_match.group(1).strip()
    conditions = re.sub(r'\bAND\b|\bOR\b', '', conditions, flags=re.IGNORECASE).strip()
    return len(conditions) > 0


def run_column_retrieval(
    query: str,
    db_session: Session,
    previous_query_sql: str = None,
    previous_user_summary: str = None,
    previous_module: str = None,
    context: PipelineContext = None,
) -> dict:
    """Run column retrieval as a standalone step (can be parallelized).

    Extracts the column retrieval logic from run_data_query_pipeline() so it
    can be executed in parallel with entity resolution.  The returned dict
    contains pre-computed results that can be fed into run_data_query_pipeline()
    via the ``pre_*`` parameters.

    Args:
        query: Original user query (may be rewritten internally for follow-ups).
        db_session: PostgreSQL session for schema queries and column embeddings.
        previous_query_sql: SQL from the previous conversation turn (multi-turn).
        previous_user_summary: Summary from the previous turn.
        previous_module: Module from the previous turn.
        context: PipelineContext for logging.

    Returns:
        Dict with keys:
            column_retrieval_result: SchemaRetrievalResult or None
            dynamic_ddl: str or None
            dynamic_join_ctx: str or None
            query: str (possibly rewritten follow-up)
            rag_synonym_ctx: str or None
            rag_glossary_ctx: str or None
            rag_examples_ctx: str or None
    """
    result = {
        "column_retrieval_result": None,
        "dynamic_ddl": None,
        "dynamic_join_ctx": None,
        "query": query,
        "rag_synonym_ctx": None,
        "rag_glossary_ctx": None,
        "rag_examples_ctx": None,
    }

    if not _is_column_retrieval_enabled():
        # Still do RAG retrieval even if column retrieval is disabled
        try:
            from app.utils.jay.rag_retriever import retrieve_synonyms, retrieve_glossary, retrieve_examples
            result["rag_synonym_ctx"] = retrieve_synonyms(query, db_session)
            result["rag_glossary_ctx"] = retrieve_glossary(query, db_session)
            result["rag_examples_ctx"] = retrieve_examples(query, db_session)
        except Exception as e:
            logger.warning(f"RAG retrieval failed in run_column_retrieval: {e}")
        return result

    # --- Follow-up Query Rewriting ---
    prev_tables = _extract_tables_from_sql(previous_query_sql) if previous_query_sql else None

    if previous_query_sql and previous_user_summary:
        # Overlap rewrite (LLM call) with join graph warm-up
        with ThreadPoolExecutor(max_workers=2) as executor:
            rewrite_future = executor.submit(
                _rewrite_follow_up_query,
                query=query,
                previous_user_summary=previous_user_summary,
                previous_module=previous_module,
                context=context,
            )
            join_graph_future = executor.submit(_get_join_graph, db_session)

            query = rewrite_future.result()
            _prefetched_join_graph = join_graph_future.result()
    else:
        _prefetched_join_graph = None

    result["query"] = query

    # --- Column Retrieval + RAG in parallel ---
    try:
        from app.utils.jay.column_retriever import retrieve_schema_for_query
        from app.utils.jay.table_catalog import build_ddl_for_tables

        cr_t0 = time.time()

        # Run RAG retrieval in parallel with column retrieval
        rag_future = None
        with ThreadPoolExecutor(max_workers=2) as executor:
            # Start RAG retrieval in background
            def _do_rag():
                try:
                    from app.utils.jay.rag_retriever import retrieve_synonyms, retrieve_glossary, retrieve_examples
                    return {
                        "rag_synonym_ctx": retrieve_synonyms(query, db_session),
                        "rag_glossary_ctx": retrieve_glossary(query, db_session),
                        "rag_examples_ctx": retrieve_examples(query, db_session),
                    }
                except Exception as e:
                    logger.warning(f"RAG retrieval failed: {e}")
                    return {}

            rag_future = executor.submit(_do_rag)

            # Column retrieval on current thread context
            join_graph = _prefetched_join_graph or _get_join_graph(db_session)

            column_retrieval_result = retrieve_schema_for_query(
                query=query,
                db_session=db_session,
                join_graph=join_graph,
                force_expand=False,
                previous_context=previous_user_summary,
                seed_tables=prev_tables,
            )

            # Collect RAG results
            rag_results = rag_future.result(timeout=10)

        cr_elapsed = (time.time() - cr_t0) * 1000
        logger.info(f"[TIMING] Column retrieval + RAG: {cr_elapsed:.0f}ms")

        result["column_retrieval_result"] = column_retrieval_result
        result.update(rag_results)

        if column_retrieval_result and column_retrieval_result.expanded_tables:
            result["dynamic_ddl"] = build_ddl_for_tables(
                column_retrieval_result.expanded_tables, db_session
            )
            result["dynamic_join_ctx"] = _build_join_context_from_retrieval(column_retrieval_result)

            if context:
                context.add("column_retrieval", "matched_columns",
                    [f"{m.table_name}.{m.column_name} ({m.similarity:.3f})"
                     for m in column_retrieval_result.matched_columns[:10]])
                context.add("column_retrieval", "selected_tables",
                    list(column_retrieval_result.selected_tables))
                context.add("column_retrieval", "retrieval_time_ms",
                    column_retrieval_result.retrieval_time_ms)
                context.add("column_retrieval", "vector_matches",
                    column_retrieval_result.vector_matches)
                context.add("column_retrieval", "lsh_matches",
                    column_retrieval_result.lsh_matches)
                context.add("column_retrieval", "fallback_used", False)

    except Exception as e:
        logger.warning(f"Column retrieval failed in run_column_retrieval, falling back to intent-based: {e}")
        if context:
            context.add("column_retrieval", "error", str(e))
            context.add("column_retrieval", "fallback_used", True)
        # Still try RAG even if column retrieval failed
        try:
            from app.utils.jay.rag_retriever import retrieve_synonyms, retrieve_glossary, retrieve_examples
            result["rag_synonym_ctx"] = retrieve_synonyms(query, db_session)
            result["rag_glossary_ctx"] = retrieve_glossary(query, db_session)
            result["rag_examples_ctx"] = retrieve_examples(query, db_session)
        except Exception as rag_e:
            logger.warning(f"RAG fallback also failed: {rag_e}")

    return result


def run_data_query_pipeline(
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
    retry_history: RetryHistory = None,
    assumption: str = None,
    previous_domains: List[str] = None,
    previous_module: str = None,
    previous_user_summary: str = None,
    pre_column_retrieval: dict = None,
) -> PipelineResult:
    """Run the data query pipeline with retry, duplicate detection, and classified errors.

    Attempts 1-2 retry within the pipeline (SQL gen → safety → execute).
    If both fail, returns needs_full_restart=True so the caller can re-run
    intent detection with the accumulated failure context before attempt 3.

    Args:
        query: Original user query
        module: Selected module name (from intent detection)
        scope: Permission scope dict (entity_id, sub_entity_id from JWT)
        resolved_entities: Resolved entity values (e.g., {"npn": "12345"})
        user_summary: LLM's summary of the user's question
        format_hint: Suggested display format
        db: PostgreSQL session
        synapse_db: Synapse session (for bob module)
        domains: List of table catalog domains for multi-table queries
        context: PipelineContext for accumulating reasoning
        previous_query_sql: SQL from previous conversation turn (multi-turn context)
        retry_history: Accumulated retry history (passed from caller on restart)
        assumption: LLM's assumption about ambiguous queries (from intent detection)
        previous_domains: Domains from the previous conversation turn (for multi-turn DDL merging).
        previous_module: Module from the previous conversation turn (for follow-up context).
        previous_user_summary: User summary from the previous conversation turn.
        pre_column_retrieval: Pre-computed column retrieval results from run_column_retrieval().
            If provided, the pipeline skips its internal column retrieval and uses these values.
            Dict keys: column_retrieval_result, dynamic_ddl, dynamic_join_ctx, query,
            rag_synonym_ctx, rag_glossary_ctx, rag_examples_ctx.

    Returns:
        PipelineResult with raw_data, sql, module, etc.
    """
    start_time = time.time()

    if module not in MODULES:
        return PipelineResult(
            success=False,
            error_message=f"Unknown module: {module}",
            module=module,
        )

    module_cfg = MODULES[module]
    scope_columns = module_cfg.get("scope_columns", {})
    db_type = module_cfg.get("db_type", "postgres")

    # Initialize or continue retry history
    history = retry_history or RetryHistory()

    # Determine attempt range based on whether this is a restart
    if retry_history and retry_history.attempts:
        # This is the final attempt after a full restart
        attempt_range = [MAX_RETRY_ATTEMPTS]
    else:
        # First run: attempts 1 and 2 only (attempt 3 requires restart)
        attempt_range = list(range(1, MAX_RETRY_ATTEMPTS))

    # --- Column Retrieval (semantic schema selection) ---
    # If pre_column_retrieval was provided (from run_column_retrieval() called
    # in parallel with entity resolution), use those results directly. Otherwise
    # run column retrieval internally for backward compatibility.

    column_retrieval_result = None
    dynamic_ddl = None
    dynamic_join_ctx = None
    pre_rag_synonym_ctx = None
    pre_rag_glossary_ctx = None
    pre_rag_examples_ctx = None

    if pre_column_retrieval is not None:
        # Use pre-computed results from run_column_retrieval()
        column_retrieval_result = pre_column_retrieval.get("column_retrieval_result")
        dynamic_ddl = pre_column_retrieval.get("dynamic_ddl")
        dynamic_join_ctx = pre_column_retrieval.get("dynamic_join_ctx")
        pre_rag_synonym_ctx = pre_column_retrieval.get("rag_synonym_ctx")
        pre_rag_glossary_ctx = pre_column_retrieval.get("rag_glossary_ctx")
        pre_rag_examples_ctx = pre_column_retrieval.get("rag_examples_ctx")
        # Use possibly-rewritten query from column retrieval
        rewritten_query = pre_column_retrieval.get("query")
        if rewritten_query:
            query = rewritten_query
        logger.info("[TIMING] Column retrieval + RAG: pre-computed (parallel)")
    else:
        # --- Internal column retrieval (backward compatible path) ---
        prev_tables = _extract_tables_from_sql(previous_query_sql) if previous_query_sql else None

        if previous_query_sql and previous_user_summary and _is_column_retrieval_enabled():
            with ThreadPoolExecutor(max_workers=2) as executor:
                rewrite_future = executor.submit(
                    _rewrite_follow_up_query,
                    query=query,
                    previous_user_summary=previous_user_summary,
                    previous_module=previous_module,
                    context=context,
                )
                join_graph_future = executor.submit(_get_join_graph, db)

                query = rewrite_future.result()
                _prefetched_join_graph = join_graph_future.result()
        elif previous_query_sql and previous_user_summary:
            query = _rewrite_follow_up_query(
                query=query,
                previous_user_summary=previous_user_summary,
                previous_module=previous_module,
                context=context,
            )
            _prefetched_join_graph = None
        else:
            _prefetched_join_graph = None

        if _is_column_retrieval_enabled():
            try:
                from app.utils.jay.column_retriever import retrieve_schema_for_query
                from app.utils.jay.table_catalog import build_ddl_for_tables

                cr_t0 = time.time()

                join_graph = _prefetched_join_graph or _get_join_graph(db)

                column_retrieval_result = retrieve_schema_for_query(
                    query=query,
                    db_session=db,
                    join_graph=join_graph,
                    force_expand=False,
                    previous_context=previous_user_summary,
                    seed_tables=prev_tables,
                )

                cr_elapsed = (time.time() - cr_t0) * 1000
                logger.info(f"[TIMING] Column retrieval: {cr_elapsed:.0f}ms")

                if column_retrieval_result and column_retrieval_result.expanded_tables:
                    dynamic_ddl = build_ddl_for_tables(
                        column_retrieval_result.expanded_tables, db
                    )
                    dynamic_join_ctx = _build_join_context_from_retrieval(column_retrieval_result)

                    if context:
                        context.add("column_retrieval", "matched_columns",
                            [f"{m.table_name}.{m.column_name} ({m.similarity:.3f})"
                             for m in column_retrieval_result.matched_columns[:10]])
                        context.add("column_retrieval", "selected_tables",
                            list(column_retrieval_result.selected_tables))
                        context.add("column_retrieval", "retrieval_time_ms",
                            column_retrieval_result.retrieval_time_ms)
                        context.add("column_retrieval", "vector_matches",
                            column_retrieval_result.vector_matches)
                        context.add("column_retrieval", "lsh_matches",
                            column_retrieval_result.lsh_matches)
                        context.add("column_retrieval", "fallback_used", False)

            except Exception as e:
                logger.warning(f"Column retrieval failed, falling back to intent-based: {e}")
                if context:
                    context.add("column_retrieval", "error", str(e))
                    context.add("column_retrieval", "fallback_used", True)

    # Store user_summary on context for downstream use (e.g. KPI label)
    if context and user_summary:
        context.user_summary = user_summary

    # Enrich user_summary with assumption so SQL generator is aware
    if assumption:
        if user_summary:
            user_summary = f"{user_summary}\n\nNote: This query is ambiguous. Assumption made: {assumption}"
        else:
            user_summary = f"Assumption made about ambiguous query: {assumption}"
        if context:
            context.add("pipeline", "assumption_passed", assumption)

    previous_sql = None
    error_message = None

    # Build retry context from history if restarting
    if history.attempts:
        last = history.attempts[-1]
        previous_sql = last.sql
        if last.error_category and last.remediation:
            error_message = (
                f"[{last.error_category}] {last.error_message or ''}\n"
                f"FIX GUIDANCE: {last.remediation}\n\n"
                f"{history.summary_for_llm()}"
            )
        else:
            error_message = history.summary_for_llm()

    for attempt in attempt_range:
        try:
            # --- Pipeline Timeout Check ---
            _check_pipeline_timeout(start_time, "sql_generation")

            # Step 3: SQL Generation (LLM)
            sql_gen_t0 = time.time()
            sql_result = generate_sql(
                query=query,
                module=module,
                resolved_entities=resolved_entities,
                user_summary=user_summary,
                format_hint=format_hint,
                previous_sql=previous_sql,
                error_message=error_message,
                domains=domains,
                context=context,
                previous_query_sql=previous_query_sql,
                dynamic_ddl=dynamic_ddl,
                dynamic_join_ctx=dynamic_join_ctx,
                db_session=db,
                previous_domains=previous_domains,
                previous_module=previous_module,
                previous_user_summary=previous_user_summary,
                pre_rag_synonym_ctx=pre_rag_synonym_ctx,
                pre_rag_glossary_ctx=pre_rag_glossary_ctx,
                pre_rag_examples_ctx=pre_rag_examples_ctx,
            )

            sql = sql_result.sql
            sql_gen_elapsed = (time.time() - sql_gen_t0) * 1000
            logger.info(f"[TIMING] SQL generation (attempt {attempt}): {sql_gen_elapsed:.0f}ms")
            logger.info(f"Generated SQL (attempt {attempt}): {sql}")

            # --- Duplicate SQL Detection ---
            if _is_duplicate_sql(sql, history.previous_sqls):
                logger.warning(f"Duplicate SQL detected on attempt {attempt}, regenerating with higher temperature")
                if context:
                    context.add("sql_generation", f"attempt_{attempt}_duplicate", "Duplicate SQL detected")

                # Try regenerating with escalating temperature
                dup_resolved = False
                for dup_retry in range(1, MAX_DUPLICATE_RETRIES + 1):
                    dup_error = (
                        f"You generated IDENTICAL SQL to a previous failed attempt. "
                        f"This is attempt {attempt}, duplicate retry {dup_retry}. "
                        f"You MUST use a fundamentally different approach. "
                        f"Try different tables, different JOINs, different column aliases, "
                        f"or a completely restructured query.\n\n"
                        f"{history.summary_for_llm()}"
                    )
                    sql_result = generate_sql(
                        query=query,
                        module=module,
                        resolved_entities=resolved_entities,
                        user_summary=user_summary,
                        format_hint=format_hint,
                        previous_sql=sql,
                        error_message=dup_error,
                        domains=domains,
                        context=context,
                        previous_query_sql=previous_query_sql,
                        dynamic_ddl=dynamic_ddl,
                        dynamic_join_ctx=dynamic_join_ctx,
                        db_session=db,
                        previous_domains=previous_domains,
                        previous_module=previous_module,
                        previous_user_summary=previous_user_summary,
                        pre_rag_synonym_ctx=pre_rag_synonym_ctx,
                        pre_rag_glossary_ctx=pre_rag_glossary_ctx,
                        pre_rag_examples_ctx=pre_rag_examples_ctx,
                    )
                    sql = sql_result.sql
                    if not _is_duplicate_sql(sql, history.previous_sqls):
                        dup_resolved = True
                        break

                if not dup_resolved:
                    logger.error(f"Could not generate unique SQL after {MAX_DUPLICATE_RETRIES} duplicate retries")
                    history.add(RetryAttempt(
                        attempt=attempt,
                        module=module,
                        sql=sql,
                        error_category="duplicate_sql",
                        error_message="LLM keeps generating identical SQL despite explicit instructions",
                        stage="sql_generation",
                    ))
                    if attempt >= MAX_RETRY_ATTEMPTS:
                        _try_store_negative_example(query, sql, "duplicate_sql: LLM keeps generating identical SQL", db)
                        return PipelineResult(
                            success=False,
                            module=module,
                            format_hint=format_hint,
                            error_message="I couldn't find a different approach after multiple attempts. Please try rephrasing your question.",
                            attempts=attempt,
                            retry_history=history,
                        )
                    continue

            if context:
                context.add("sql_generation", f"attempt_{attempt}_sql", sql)

            # Step 3b: Scope Injection (programmatic - CRITICAL SECURITY)
            try:
                sql = inject_scope(
                    sql=sql,
                    scope=scope,
                    scope_columns=scope_columns,
                    db_type=db_type,
                )
            except ScopeInjectionError as e:
                # Scope injection structural failure (e.g., unparseable SQL) —
                # log and continue without scope rather than blocking the user.
                logger.warning(f"Scope injection structural failure (non-fatal): {e}")
                if context:
                    context.add("scope_injection", "warning", str(e)[:200])
                if not assumption:
                    assumption = "Showing data across all entities. Select an entity for filtered results."

            # --- Pipeline Timeout Check ---
            _check_pipeline_timeout(start_time, "safety_review")

            # Step 4: Safety Review
            safety = review_sql_safety(sql=sql, module=module, context=context, domains=domains)
            if not safety.is_safe:
                logger.error(
                    f"SQL safety review blocked query (attempt {attempt}): {safety.issues}"
                )
                if context:
                    context.add("safety_review", f"attempt_{attempt}_issues", "; ".join(safety.issues))

                history.add(RetryAttempt(
                    attempt=attempt,
                    module=module,
                    sql=sql,
                    error_category="safety_blocked",
                    error_message="; ".join(safety.issues),
                    remediation=(
                        "Query was blocked by safety review. "
                        "Use only SELECT on tables from the provided DDL. "
                        "Avoid DML/DDL keywords and unauthorized tables."
                    ),
                    stage="safety_review",
                ))

                if attempt < MAX_RETRY_ATTEMPTS:
                    previous_sql = sql
                    error_message = (
                        f"Safety review BLOCKED this SQL (attempt {attempt}/{MAX_RETRY_ATTEMPTS}): "
                        f"{'; '.join(safety.issues)}. "
                        f"You MUST generate a completely different query that avoids this issue. "
                        f"Use only tables and columns from the provided DDL.\n\n"
                        f"{history.summary_for_llm()}"
                    )
                    continue

                # Attempt 2 failed safety — signal full restart
                if attempt == MAX_RETRY_ATTEMPTS - 1:
                    return PipelineResult(
                        success=False,
                        sql=sql,
                        module=module,
                        format_hint=format_hint,
                        needs_full_restart=True,
                        retry_history=history,
                        attempts=attempt,
                    )

                _try_store_negative_example(query, sql, f"safety_blocked: {'; '.join(safety.issues)}", db)
                return PipelineResult(
                    success=False,
                    sql=sql,
                    module=module,
                    format_hint=format_hint,
                    error_message="I wasn't able to answer that question. Could you try rephrasing it, or ask something more specific?",
                    attempts=attempt,
                    safety_blocked=True,
                    retry_history=history,
                )

            # --- Pipeline Timeout Check ---
            _check_pipeline_timeout(start_time, "execution")

            # Step 5: Execute SQL
            exec_t0 = time.time()
            raw_data = execute_query(
                module=module,
                sql=sql,
                params=[],
                db=db,
                synapse_db=synapse_db,
            )
            exec_elapsed = (time.time() - exec_t0) * 1000
            logger.info(f"[TIMING] Query execution (attempt {attempt}): {exec_elapsed:.0f}ms, rows={len(raw_data)}")

            # --- Empty Result Set Retry ---
            # If the query returned 0 rows and has WHERE filters, treat it as
            # a potential semantic error (wrong column, wrong filter value) and
            # retry once. Bare count-all queries that legitimately return 0 are
            # not retried because they lack WHERE filters.
            if (
                raw_data is not None
                and len(raw_data) == 0
                and attempt < max(attempt_range)
                and _sql_has_where_filters(sql)
            ):
                if context:
                    context.add("execution", "empty_result_retry",
                                f"Query returned 0 rows on attempt {attempt}")
                logger.info(
                    "Query returned 0 rows with WHERE filters — treating as potential "
                    "semantic error, retrying"
                )
                history.add(RetryAttempt(
                    attempt=attempt,
                    module=module,
                    sql=sql,
                    error_category="semantic_empty_result",
                    error_message=(
                        "Query executed successfully but returned 0 rows. "
                        "The filter values, column names, or JOIN conditions "
                        "may be incorrect."
                    ),
                    remediation=(
                        "Try different column names, check filter values against "
                        "valid values in DDL comments, or adjust JOIN conditions."
                    ),
                    stage="execution",
                ))
                previous_sql = sql
                error_message = (
                    f"Previous SQL returned 0 rows (attempt {attempt}). "
                    f"This likely means a filter value, column name, or JOIN is wrong. "
                    f"Check the DDL comments for correct values and column names.\n\n"
                    f"{history.summary_for_llm()}"
                )
                continue  # retry loop

            logger.info(
                f"Pipeline success: module={module}, attempt={attempt}, rows={len(raw_data)}"
            )

            if context is not None:
                context.add("execution", "row_count", len(raw_data))
                context.add("execution", "execution_note", f"Returned {len(raw_data)} data points")

            return PipelineResult(
                success=True,
                raw_data=raw_data,
                sql=sql,
                module=module,
                format_hint=format_hint,
                attempts=attempt,
                context=context,
                retry_history=history,
                assumption=assumption,
            )

        except SQLGenerationError as e:
            logger.warning(f"SQL generation failed (attempt {attempt}): {e}")
            if context:
                context.add("sql_generation", f"attempt_{attempt}_error", str(e))

            failed_sql = getattr(e, 'sql', previous_sql)
            history.add(RetryAttempt(
                attempt=attempt,
                module=module,
                sql=failed_sql,
                error_category="sql_generation_error",
                error_message=str(e)[:200],
                remediation="LLM failed to produce valid SQL. Check if the query is too ambiguous or uses terms not in the schema.",
                stage="sql_generation",
            ))
            previous_sql = failed_sql
            error_message = f"SQL generation failed: {str(e)}\n\n{history.summary_for_llm()}"

            # Signal full restart after attempt 2
            if attempt == MAX_RETRY_ATTEMPTS - 1:
                return PipelineResult(
                    success=False,
                    module=module,
                    format_hint=format_hint,
                    needs_full_restart=True,
                    retry_history=history,
                    attempts=attempt,
                )

            if attempt >= MAX_RETRY_ATTEMPTS:
                _try_store_negative_example(query, failed_sql, str(e), db)
                return PipelineResult(
                    success=False,
                    module=module,
                    format_hint=format_hint,
                    error_message=f"I couldn't generate a valid query after {attempt} attempts. Please try rephrasing.",
                    attempts=attempt,
                    retry_history=history,
                )

        except QueryExecutionError as e:
            logger.warning(f"Query execution failed (attempt {attempt}): {e}")

            # Classify the error
            classified = classify_error(str(e))
            logger.info(f"Classified error: {classified.category} — {classified.remediation[:80]}")

            if context:
                context.add("execution", f"attempt_{attempt}_error", str(e))
                context.add("execution", f"attempt_{attempt}_error_type", classified.category)

            history.add(RetryAttempt(
                attempt=attempt,
                module=module,
                sql=sql,
                error_category=classified.category,
                error_message=classified.raw_message,
                remediation=classified.remediation,
                stage="execution",
            ))
            previous_sql = sql

            # Build structured error message for LLM
            error_message = (
                format_classified_error_for_llm(classified, attempt, MAX_RETRY_ATTEMPTS)
                + "\n\n"
                + history.summary_for_llm()
            )

            # Signal full restart after attempt 2
            if attempt == MAX_RETRY_ATTEMPTS - 1:
                return PipelineResult(
                    success=False,
                    sql=sql,
                    module=module,
                    format_hint=format_hint,
                    needs_full_restart=True,
                    retry_history=history,
                    attempts=attempt,
                )

            if attempt >= MAX_RETRY_ATTEMPTS:
                logger.error(f"Pipeline error (final attempt {attempt}): {str(e)}")
                _try_store_negative_example(query, sql, str(e), db)
                return PipelineResult(
                    success=False,
                    sql=sql,
                    module=module,
                    format_hint=format_hint,
                    error_message="An error occurred while processing your query. Please try rephrasing.",
                    attempts=attempt,
                    retry_history=history,
                )

        except PipelineTimeoutError:
            logger.error(f"Pipeline timeout on attempt {attempt}")
            return PipelineResult(
                success=False,
                module=module,
                format_hint=format_hint,
                error_message="Pipeline timeout exceeded. Please try a simpler query.",
                attempts=attempt,
                retry_history=history,
            )

        except Exception as e:
            logger.error(f"Unexpected pipeline error (attempt {attempt}): {e}")

            classified = classify_error(str(e))
            # Determine which stage the error originated from based on
            # whether sql_result / sql were successfully set in this iteration
            _failed_sql = locals().get("sql")
            if _failed_sql is None:
                _fail_stage = "sql_generation"
            else:
                _fail_stage = "execution"

            if context:
                context.add(_fail_stage, f"attempt_{attempt}_error", str(e))

            history.add(RetryAttempt(
                attempt=attempt,
                module=module,
                sql=_failed_sql,
                error_category=classified.category,
                error_message=str(e)[:200],
                remediation=classified.remediation,
                stage=_fail_stage,
            ))
            previous_sql = _failed_sql or previous_sql
            error_message = (
                format_classified_error_for_llm(classified, attempt, MAX_RETRY_ATTEMPTS)
                + "\n\n"
                + history.summary_for_llm()
            )

            # Signal full restart after attempt 2
            if attempt == MAX_RETRY_ATTEMPTS - 1:
                return PipelineResult(
                    success=False,
                    module=module,
                    format_hint=format_hint,
                    needs_full_restart=True,
                    retry_history=history,
                    attempts=attempt,
                )

            if attempt >= MAX_RETRY_ATTEMPTS:
                logger.error(f"Unexpected pipeline error (final attempt {attempt}): {str(e)}")
                _try_store_negative_example(query, _failed_sql, str(e), db)
                return PipelineResult(
                    success=False,
                    module=module,
                    format_hint=format_hint,
                    error_message="An unexpected error occurred. Please try again.",
                    attempts=attempt,
                    retry_history=history,
                )

    # After attempts 1-2 exhausted without success or explicit restart signal
    return PipelineResult(
        success=False,
        module=module,
        format_hint=format_hint,
        needs_full_restart=True,
        retry_history=history,
        attempts=max(attempt_range) if attempt_range else 0,
    )
