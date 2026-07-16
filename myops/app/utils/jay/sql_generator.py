"""
JAI SQL Generator - Step 3 of the new pipeline.

Generates SQL using LLM with module DDL as context.
Supports retry with error feedback.
"""

import re
import logging
import time
from typing import Optional, Dict, List

from app.utils.jay.llm_client import get_llm_client
from app.utils.jay.pipeline_models import SQLGenerationResult, PipelineContext
from app.utils.jay.prompts import build_sql_generation_prompt
from app.utils.jay.semantic_registry import (
    MODULES,
    get_categorical_value_map,
    get_all_categorical_value_maps,
)

logger = logging.getLogger(__name__)


class SQLGenerationError(Exception):
    pass


def generate_sql(
    query: str,
    module: str,
    resolved_entities: Dict[str, str] = None,
    user_summary: str = None,
    format_hint: str = "auto",
    previous_sql: str = None,
    error_message: str = None,
    domains: list = None,
    context: PipelineContext = None,
    previous_query_sql: str = None,
    dynamic_ddl: str = None,
    dynamic_join_ctx: str = None,
    db_session=None,
    comparison_periods: list = None,
    previous_domains: list = None,
    previous_module: str = None,
    previous_user_summary: str = None,
    pre_rag_synonym_ctx: str = None,
    pre_rag_glossary_ctx: str = None,
    pre_rag_examples_ctx: str = None,
) -> SQLGenerationResult:
    """Generate a SELECT SQL query using LLM with module DDL context.

    Args:
        query: Original user query
        module: Selected module name
        resolved_entities: Dict of resolved entity values (e.g., {"npn": "12345678"})
        user_summary: LLM's understanding of the user's question
        format_hint: Suggested display format
        previous_sql: Previous failed SQL (for retry)
        error_message: Error from previous attempt (for retry)
        domains: List of table catalog domains for multi-table queries
        context: PipelineContext for accumulating reasoning
        previous_query_sql: SQL from previous conversation turn (multi-turn context)
        dynamic_ddl: DDL from semantic column retrieval (overrides domain DDL)
        dynamic_join_ctx: JOIN context from semantic column retrieval
        db_session: Optional DB session for RAG retrieval.
        comparison_periods: List of comparison period labels (e.g., ["this_year", "last_year"]).
        previous_domains: Domains from the previous conversation turn (for multi-turn DDL merging).
        previous_module: Module from the previous conversation turn (for follow-up context).
        previous_user_summary: User summary from the previous conversation turn.
        pre_rag_synonym_ctx: Pre-computed RAG synonym context (skips internal retrieval if set).
        pre_rag_glossary_ctx: Pre-computed RAG glossary context (skips internal retrieval if set).
        pre_rag_examples_ctx: Pre-computed RAG examples context (skips internal retrieval if set).

    Returns:
        SQLGenerationResult with the generated SQL
    """
    if module not in MODULES:
        raise SQLGenerationError(f"Unknown module: {module}")

    client, deployment = get_llm_client()
    module_cfg = MODULES[module]
    db_type = module_cfg.get("db_type", "postgres")

    # RAG retrieval: targeted synonyms/glossary/examples for this specific query
    # Use pre-computed values if provided (from parallel pre-warming), otherwise retrieve inline.
    rag_t0 = time.time()
    if pre_rag_synonym_ctx is not None or pre_rag_glossary_ctx is not None or pre_rag_examples_ctx is not None:
        rag_synonym_ctx = pre_rag_synonym_ctx
        rag_glossary_ctx = pre_rag_glossary_ctx
        rag_examples_ctx = pre_rag_examples_ctx
        logger.info("[TIMING] SQL gen RAG retrieval: pre-computed (skipped)")
    else:
        rag_synonym_ctx = None
        rag_glossary_ctx = None
        rag_examples_ctx = None
        if db_session is not None:
            try:
                from app.utils.jay.rag_retriever import retrieve_synonyms, retrieve_glossary, retrieve_examples
                rag_synonym_ctx = retrieve_synonyms(query, db_session)
                rag_glossary_ctx = retrieve_glossary(query, db_session)
                rag_examples_ctx = retrieve_examples(query, db_session)
            except Exception as e:
                logger.warning(f"RAG retrieval failed for SQL generation, using static: {e}")
        rag_elapsed = (time.time() - rag_t0) * 1000
        logger.info(f"[TIMING] SQL gen RAG retrieval: {rag_elapsed:.0f}ms")

    # Build the prompt with DDL, entities, retry context, and pipeline context
    pipeline_context_str = context.summary() if context else ""
    system_prompt = build_sql_generation_prompt(
        module=module,
        user_summary=user_summary or query,
        resolved_entities=resolved_entities or {},
        format_hint=format_hint,
        previous_sql=previous_sql,
        error_message=error_message,
        domains=domains,
        pipeline_context=pipeline_context_str,
        previous_query_sql=previous_query_sql,
        dynamic_ddl=dynamic_ddl,
        dynamic_join_ctx=dynamic_join_ctx,
        rag_synonym_ctx=rag_synonym_ctx,
        rag_glossary_ctx=rag_glossary_ctx,
        rag_examples_ctx=rag_examples_ctx,
        comparison_periods=comparison_periods,
        previous_domains=previous_domains,
        previous_module=previous_module,
        previous_user_summary=previous_user_summary,
    )

    # Use higher temperature on retries so the LLM explores different SQL approaches
    is_retry = previous_sql is not None and error_message is not None
    temperature = 0.4 if is_retry else 0.0

    try:
        llm_t0 = time.time()
        resp = client.chat.completions.create(
            model=deployment,
            temperature=temperature,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": query},
            ],
        )
        llm_elapsed = (time.time() - llm_t0) * 1000
        logger.info(f"[TIMING] SQL gen LLM call: {llm_elapsed:.0f}ms")

        raw_sql = (resp.choices[0].message.content or "").strip()

    except Exception as e:
        logger.error(f"SQL generation LLM call failed: {e}")
        raise SQLGenerationError(f"LLM service error: {str(e)[:100]}")

    # Clean up the SQL
    sql = _clean_sql(raw_sql)

    # Normalize categorical value casing (e.g., 'active' -> 'Active')
    sql = _normalize_categorical_values(sql, module, domains)

    # Basic validation
    if not sql.upper().startswith("SELECT"):
        raise SQLGenerationError(f"LLM did not generate a SELECT statement: {sql[:100]}")

    # Log SQL approach to pipeline context
    if context is not None:
        context.add("sql_generation", "sql_approach", f"Generated SQL for module {module}")
        if previous_sql and error_message:
            context.add("sql_generation", "retry_reason", error_message)

    return SQLGenerationResult(
        sql=sql,
        module=module,
        db_type=db_type,
    )


def _clean_sql(raw: str) -> str:
    """Clean LLM output to extract pure SQL."""
    sql = raw.strip()

    # Remove markdown code fences
    if sql.startswith("```"):
        # Remove opening fence (```sql or ```)
        sql = re.sub(r"^```(?:sql)?\s*\n?", "", sql)
        # Remove closing fence
        sql = re.sub(r"\n?```\s*$", "", sql)

    # Remove trailing semicolons
    sql = sql.rstrip(";").strip()

    return sql


def _normalize_categorical_values(
    sql: str,
    module: str,
    domains: List[str] = None,
) -> str:
    """Normalize string literal casing for known categorical columns.

    Scans the generated SQL for patterns like:
        column = 'value'
        column='value'
        LOWER(TRIM(t.column)) = 'value'

    and corrects the value to the exact DB casing from valid_values,
    keeping the = operator so indexes are used.

    Also strips any LOWER(TRIM(...)) wrapping on these columns since
    exact-value matching makes it unnecessary.
    """
    # Build the value map: {column_name: {lowercase_val: db_val}}
    if domains:
        value_map = get_all_categorical_value_maps()
    else:
        value_map = get_categorical_value_map(module)

    if not value_map:
        return sql

    for col, mappings in value_map.items():
        # Pattern 1: LOWER(TRIM(alias.col)) = 'value' or LOWER(TRIM(col)) = 'value'
        # Replace with alias.col = 'DbValue'
        lower_trim_pattern = (
            rf"LOWER\s*\(\s*TRIM\s*\(\s*(\w+\.)?{re.escape(col)}\s*\)\s*\)"
            rf"\s*=\s*'([^']*)'"
        )

        def _replace_lower_trim(m, _mappings=mappings, _col=col):
            alias = m.group(1) or ""
            val = m.group(2).strip().lower()
            db_val = _mappings.get(val, m.group(2))
            return f"{alias}{_col} = '{db_val}'"

        sql = re.sub(lower_trim_pattern, _replace_lower_trim, sql, flags=re.IGNORECASE)

        # Pattern 2: alias.col = 'value' or col = 'value' (simple equality)
        simple_pattern = (
            rf"(\w+\.)?{re.escape(col)}\s*=\s*'([^']*)'"
        )

        def _replace_simple(m, _mappings=mappings, _col=col):
            alias = m.group(1) or ""
            val = m.group(2).strip().lower()
            db_val = _mappings.get(val, m.group(2))
            return f"{alias}{_col} = '{db_val}'"

        sql = re.sub(simple_pattern, _replace_simple, sql, flags=re.IGNORECASE)

        # Pattern 3: alias.col IN ('val1', 'val2', ...)
        in_pattern = (
            rf"(\w+\.)?{re.escape(col)}\s+IN\s*\(([^)]+)\)"
        )

        def _replace_in(m, _mappings=mappings, _col=col):
            alias = m.group(1) or ""
            raw_vals = m.group(2)
            # Extract individual quoted values and normalize each
            normalized = re.sub(
                r"'([^']*)'",
                lambda v: f"'{_mappings.get(v.group(1).strip().lower(), v.group(1))}'",
                raw_vals,
            )
            return f"{alias}{_col} IN ({normalized})"

        sql = re.sub(in_pattern, _replace_in, sql, flags=re.IGNORECASE)

    return sql
