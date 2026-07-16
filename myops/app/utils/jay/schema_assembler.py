"""
JAI Schema Assembler - Converts a technical spec into concrete DDL + values + JOIN context.

Part of the v2 hybrid pipeline:
  1. LLM generates a technical spec (JSON with tables_needed, columns_needed, joins, filters)
  2. **This module** converts that spec into actual DDL + values + JOIN context
     by querying indexed metadata (column_retriever, table_catalog, query_cache, etc.)
  3. LLM generates SQL using the assembled context

This module makes NO LLM calls. It is pure Python + DB queries.
"""

import logging
import re
import time
from typing import Any, Dict, List, Optional, Set

from sqlalchemy.orm import Session

from app.utils.jay.fk_inference import JoinGraph, JoinPath
from app.utils.jay.pipeline_models import SchemaContext

logger = logging.getLogger(__name__)


# =============================================================================
# Table name resolution helpers
# =============================================================================


def _build_table_name_index() -> Dict[str, str]:
    """Build a mapping from short table names to schema-qualified names.

    Lazily imports TABLE_DOMAINS to avoid circular imports and builds
    a dict like ``{"lup_agents": "wpo.lup_agents", "com_totals": "wpo.com_totals", ...}``.
    Also includes schema-qualified names mapping to themselves for pass-through.
    """
    from app.utils.jay.table_catalog import TABLE_DOMAINS

    index: Dict[str, str] = {}
    for domain_cfg in TABLE_DOMAINS.values():
        for table_cfg in domain_cfg.get("tables", []):
            full_name = table_cfg["name"]  # e.g. "wpo.lup_agents"
            short_name = full_name.split(".")[-1]  # e.g. "lup_agents"
            index[short_name] = full_name
            index[full_name] = full_name  # schema-qualified maps to itself
    return index


# Module-level cache (built once per process)
_table_name_index: Optional[Dict[str, str]] = None


def _get_table_name_index() -> Dict[str, str]:
    """Return the cached table name index, building it on first access."""
    global _table_name_index
    if _table_name_index is None:
        _table_name_index = _build_table_name_index()
    return _table_name_index


def _resolve_table_name(name: str) -> str:
    """Resolve a table name (short or qualified) to its full schema-qualified form.

    Falls back to the original name if not found in the catalog (the table
    may exist in the DB but not be registered in TABLE_DOMAINS).
    """
    if not name:
        return name
    idx = _get_table_name_index()
    # Try exact match first, then lowercase
    return idx.get(name) or idx.get(name.lower()) or name


# =============================================================================
# Spec parsing helpers
# =============================================================================


def _extract_seed_tables_from_spec(technical_spec: dict) -> Set[str]:
    """Extract table names from all parts of the technical spec.

    Parses ``tables_needed``, ``columns_needed`` (extracts table part of
    ``table.column``), and ``joins`` (extracts table names from each join).

    All names are resolved to schema-qualified form where possible.
    """
    tables: Set[str] = set()

    # tables_needed: list of table name strings
    for t in technical_spec.get("tables_needed", []):
        if isinstance(t, str) and t.strip():
            tables.add(_resolve_table_name(t.strip()))

    # columns_needed: list of "table.column" strings (or just column names)
    for col_ref in technical_spec.get("columns_needed", []):
        if isinstance(col_ref, str) and "." in col_ref:
            table_part = col_ref.split(".")[0].strip()
            if table_part:
                tables.add(_resolve_table_name(table_part))

    # joins: list of dicts like {"left": "table_a", "right": "table_b", ...}
    #   or list of strings like "table_a JOIN table_b ON ..."
    for join in technical_spec.get("joins", []):
        if isinstance(join, dict):
            for key in ("left", "right", "table", "from", "to"):
                val = join.get(key)
                if isinstance(val, str) and val.strip():
                    tables.add(_resolve_table_name(val.strip()))
        elif isinstance(join, str):
            # Try to extract table names from a JOIN string
            for part in join.replace(",", " ").split():
                resolved = _resolve_table_name(part.strip())
                # Only add if it actually resolved to something in the catalog
                if resolved != part.strip() or "." in resolved:
                    tables.add(resolved)

    return tables


def _extract_filters_needing_values(
    technical_spec: dict,
    resolved_entities: dict,
) -> List[Dict[str, str]]:
    """Build a list of (table, column) pairs that need value lookups.

    Sources:
    1. Filters in the spec where ``needs_verification`` is True.
    2. Columns referenced by resolved entities (to confirm valid values).

    Each entry is ``{"table": "...", "column": "..."}``.
    """
    lookups: List[Dict[str, str]] = []
    seen: Set[str] = set()

    # From spec filters
    for f in technical_spec.get("filters", []):
        if not isinstance(f, dict):
            continue
        if not f.get("needs_verification", False):
            continue
        table = f.get("table", "")
        column = f.get("column", "")
        if table and column:
            key = f"{table}.{column}"
            if key not in seen:
                lookups.append({"table": _resolve_table_name(table), "column": column})
                seen.add(key)

    # From resolved entities — look up columns that match entity types
    # (e.g., resolved_entities might have {"npn": "12345"} and we want to
    # verify that value exists in whatever table has an npn column)
    # We don't know the exact table here, so skip table-level lookup for entities;
    # the caller can add them if the table is known from the spec context.

    return lookups


# =============================================================================
# JOIN context builder (mirrors pipeline._build_join_context_from_retrieval)
# =============================================================================


def _build_join_context_from_retrieval(result) -> str:
    """Build SQL JOIN context text from a SchemaRetrievalResult.

    Formats each join path as a LEFT JOIN clause. This is a local copy
    of the same logic in pipeline.py to avoid a tight coupling.
    """
    if not result or not result.join_paths:
        return ""
    lines = ["-- JOIN CONTEXT (from semantic column retrieval)", ""]
    for jp in result.join_paths:
        lines.append(f"-- {jp.from_table}.{jp.from_col} -> {jp.to_table}.{jp.to_col}")
        lines.append(
            f"LEFT JOIN {jp.to_table} ON {jp.from_table}.{jp.from_col} = {jp.to_table}.{jp.to_col}"
        )
        lines.append("")
    return "\n".join(lines)


# =============================================================================
# DDL merging
# =============================================================================


def _merge_ddl(domain_ddl: str, dynamic_ddl: str) -> str:
    """Merge domain-based DDL and dynamic DDL, deduplicating tables.

    Domain DDL takes priority (it has curated comments and valid-value annotations).
    If a table appears in both, the domain version wins.
    """
    if not dynamic_ddl:
        return domain_ddl
    if not domain_ddl:
        return dynamic_ddl

    # Extract table names from domain DDL to avoid duplicating them
    domain_tables: Set[str] = set()
    for line in domain_ddl.splitlines():
        stripped = line.strip().upper()
        if stripped.startswith("CREATE TABLE "):
            # "CREATE TABLE wpo.lup_agents (" -> "wpo.lup_agents"
            parts = stripped.replace("(", " ").split()
            if len(parts) >= 3:
                domain_tables.add(parts[2].strip())

    # Filter dynamic DDL: split by CREATE TABLE blocks and skip duplicates
    filtered_blocks: List[str] = []
    current_block: List[str] = []
    skip_block = False

    for line in dynamic_ddl.splitlines():
        stripped = line.strip().upper()
        if stripped.startswith("CREATE TABLE "):
            # Flush previous block
            if current_block and not skip_block:
                filtered_blocks.append("\n".join(current_block))
            # Start new block
            parts = stripped.replace("(", " ").split()
            table_name = parts[2].strip() if len(parts) >= 3 else ""
            skip_block = table_name in domain_tables
            current_block = [line]
        else:
            current_block.append(line)

    # Flush last block
    if current_block and not skip_block:
        filtered_blocks.append("\n".join(current_block))

    if not filtered_blocks:
        return domain_ddl

    return domain_ddl + "\n\n" + "\n\n".join(filtered_blocks)


def _merge_join_context(domain_join_ctx: str, dynamic_join_ctx: str) -> str:
    """Merge domain-based and dynamic JOIN context strings."""
    parts = [p for p in (domain_join_ctx, dynamic_join_ctx) if p and p.strip()]
    return "\n\n".join(parts)


# =============================================================================
# Compact DDL format
# =============================================================================


def _convert_ddl_to_compact(full_ddl: str) -> str:
    """Convert full CREATE TABLE DDL to a compact single-line-per-table format.

    Full format (input):
        CREATE TABLE wpo.lup_agents (
            agent_id UUID PRIMARY KEY,
            first_name VARCHAR(100),
            status VARCHAR,  -- Values: Active, Inactive
        );

    Compact format (output):
        wpo.lup_agents: agent_id UUID PK, first_name VARCHAR, status VARCHAR [Active|Inactive]

    Typically saves 40-60% tokens on DDL context. Comments prefixed with
    ``-- Values:`` are converted to inline ``[val1|val2|...]`` annotations.
    Domain header comments (``-- ===== DOMAIN: ...``) are preserved.

    Args:
        full_ddl: DDL text containing one or more CREATE TABLE blocks.

    Returns:
        Compact DDL string, or the original DDL if parsing fails.
    """
    if not full_ddl or not full_ddl.strip():
        return ""

    # Regex to extract "-- Values: ..." or "-- Key values: ..." from comments
    _values_re = re.compile(
        r"--\s*(?:Values|Key values|Valid values):\s*(.+?)(?:\s*\(\d+\s+total[^)]*\))?\s*$",
        re.IGNORECASE,
    )

    output_lines: List[str] = []
    current_table: Optional[str] = None
    current_columns: List[str] = []
    in_table = False

    for raw_line in full_ddl.splitlines():
        line = raw_line.strip()

        # Preserve domain header comments
        if line.startswith("-- ====="):
            # Flush any in-progress table
            if current_table and current_columns:
                output_lines.append(f"{current_table}: {', '.join(current_columns)}")
                current_table = None
                current_columns = []
                in_table = False
            output_lines.append(line)
            continue

        # Detect CREATE TABLE header
        if line.upper().startswith("CREATE TABLE "):
            # Flush previous table
            if current_table and current_columns:
                output_lines.append(f"{current_table}: {', '.join(current_columns)}")

            # Extract table name: "CREATE TABLE wpo.lup_agents (" -> "wpo.lup_agents"
            match = re.match(r"CREATE\s+TABLE\s+([\w.\"]+)", line, re.IGNORECASE)
            if match:
                current_table = re.sub(r'"', '', match.group(1))
            else:
                current_table = None
            current_columns = []
            in_table = True
            continue

        # End of table block
        if in_table and line.startswith(")"):
            if current_table and current_columns:
                output_lines.append(f"{current_table}: {', '.join(current_columns)}")
            current_table = None
            current_columns = []
            in_table = False
            continue

        # Skip standalone comment lines inside a table (but extract values from inline comments)
        if in_table and line.startswith("--") and not current_columns:
            # Comment before any column — skip (table-level comment)
            continue

        # Parse column definition
        if in_table and current_table and line and not line.startswith("--"):
            # Remove trailing comma
            col_def = line.rstrip(",;").strip()

            # Split off inline comment
            comment = ""
            comment_idx = col_def.find("--")
            if comment_idx >= 0:
                comment = col_def[comment_idx:].strip()
                col_def = col_def[:comment_idx].strip()

            if not col_def:
                continue

            # Parse: column_name TYPE [NOT NULL] [DEFAULT ...] [PRIMARY KEY]
            parts = col_def.split()
            if not parts:
                continue

            col_name = parts[0]

            # Extract type (second token, if present)
            col_type = parts[1] if len(parts) > 1 else ""

            # Simplify type: VARCHAR(100) -> VARCHAR, CHARACTER VARYING -> VARCHAR
            col_type_upper = col_type.upper()
            if col_type_upper.startswith("VARCHAR") or col_type_upper.startswith("CHARACTER"):
                col_type = "VARCHAR"
            elif "(" in col_type:
                # Keep type name but strip precision: NUMERIC(10,2) -> NUMERIC
                col_type = col_type.split("(")[0]

            # Check for PRIMARY KEY
            rest_upper = " ".join(parts[2:]).upper()
            pk_marker = " PK" if "PRIMARY KEY" in rest_upper or "PRIMARY" in rest_upper else ""

            # Check for NOT NULL (only if not PK, since PK implies NOT NULL)
            nn_marker = ""
            if not pk_marker and "NOT NULL" in rest_upper:
                nn_marker = " NOT NULL"

            # Extract values and description from comment
            values_annotation = ""
            desc_annotation = ""
            if comment:
                m = _values_re.search(comment)
                if m:
                    raw_values = m.group(1).strip()
                    vals = [v.strip().strip("'\"") for v in raw_values.split(",")]
                    vals = [v for v in vals if v][:10]
                    if vals:
                        values_annotation = f" [{' | '.join(vals)}]"
                else:
                    # Preserve non-Values description comments (truncated to 40 chars)
                    desc_text = comment.lstrip("- ").strip()
                    if desc_text:
                        if len(desc_text) > 40:
                            desc_text = desc_text[:37] + "..."
                        desc_annotation = f" -- {desc_text}"

            compact_col = f"{col_name} {col_type}{pk_marker}{nn_marker}{values_annotation}{desc_annotation}"
            current_columns.append(compact_col)

    # Flush final table if DDL didn't end with );
    if current_table and current_columns:
        output_lines.append(f"{current_table}: {', '.join(current_columns)}")

    result = "\n".join(output_lines)

    # Fallback: if conversion produced nothing useful, return original
    if not result.strip():
        return full_ddl

    return result


# =============================================================================
# Column inventory extraction (used by column_validator and prompt builders)
# =============================================================================

_CREATE_TABLE_RE = re.compile(
    r'CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?'
    r'(?:"?(\w+)"?\.)?'
    r'"?(\w+)"?'
    r'\s*\((.*?)\);',
    re.IGNORECASE | re.DOTALL,
)

_COMPACT_HEADER_RE = re.compile(r'^(\w+(?:\.\w+)?):\s*(.*)', re.MULTILINE)


def extract_column_inventory(ddl: str) -> dict:
    """Extract {table_name: [column_names]} from DDL (full or compact format).

    Handles both CREATE TABLE statements and compact 'table: col TYPE, ...' format.
    Keys are lowercase. Pure function, no DB calls.
    """
    inventory = {}

    # Full CREATE TABLE format
    for m in _CREATE_TABLE_RE.finditer(ddl):
        schema, table = m.group(1), m.group(2)
        full_name = f"{schema}.{table}".lower() if schema else table.lower()
        body = m.group(3)
        cols = []
        for line in body.split("\n"):
            line = line.strip().rstrip(",")
            if not line or line.upper().startswith(
                ("PRIMARY", "FOREIGN", "UNIQUE", "CHECK", "CONSTRAINT", "INDEX", ")")
            ):
                continue
            col_match = re.match(r'"?(\w+)"?\s+\w+', line)
            if col_match:
                cols.append(col_match.group(1).lower())
        if cols:
            inventory[full_name] = cols

    # Compact format: "schema.table: col1 TYPE, col2 TYPE [val1|val2] -- desc"
    for m in _COMPACT_HEADER_RE.finditer(ddl):
        table_name = m.group(1).lower()
        if table_name in inventory:
            continue  # already parsed from CREATE TABLE
        cols_str = m.group(2).strip()
        if not cols_str:
            # Multi-line compact: collect until next header
            start = m.end()
            next_m = _COMPACT_HEADER_RE.search(ddl, start)
            cols_str = ddl[start:next_m.start()] if next_m else ddl[start:]
        cols = []
        for part in re.split(r',\s*(?=\w+\s+\w+)', cols_str):
            part = part.strip()
            if not part:
                continue
            col_match = re.match(r'(\w+)\s+\w+', part)
            if col_match:
                cols.append(col_match.group(1).lower())
        if cols:
            inventory[table_name] = cols

    return inventory


# =============================================================================
# Main entry point
# =============================================================================


def assemble_schema_context(
    query: str,
    technical_spec: dict,
    module: str,
    domains: list,
    resolved_entities: dict,
    db_session: Session,
    join_graph: JoinGraph,
    previous_context: str = None,
    seed_tables: set = None,
    compact: bool = False,
) -> SchemaContext:
    """Assemble complete schema context from a technical spec and metadata.

    Bridges the gap between the LLM's abstract technical spec and the
    concrete database schema. Queries column_retriever, table_catalog,
    query_cache, and tool_handlers to build DDL, JOIN context, column
    values, and similar queries.

    This function makes NO LLM calls. All operations are Python + DB.

    Args:
        query: The original user question.
        technical_spec: Dict from LLM step 1 with keys like tables_needed,
            columns_needed, joins, filters, etc. May be empty or malformed.
        module: Selected module name (from intent detection).
        domains: List of table catalog domain names.
        resolved_entities: Dict of resolved entity values
            (e.g. {"npn": "12345"}).
        db_session: Active SQLAlchemy session.
        join_graph: Pre-built JoinGraph for FK expansion.
        previous_context: Summary from the previous conversation turn
            (passed to column_retriever for multi-turn context).
        seed_tables: Additional table names to seed into column retrieval
            (e.g. from previous SQL).
        compact: If True, convert DDL to compact single-line-per-table
            format to save tokens (typically 40-60% reduction).

    Returns:
        SchemaContext with all assembled fields populated.
    """
    t_start = time.monotonic()

    # Ensure technical_spec is a dict (LLM may return None or garbage)
    if not isinstance(technical_spec, dict):
        technical_spec = {}

    # Ensure domains is a list
    if not isinstance(domains, list):
        domains = []

    # Ensure resolved_entities is a dict
    if not isinstance(resolved_entities, dict):
        resolved_entities = {}

    ctx = SchemaContext()

    # -----------------------------------------------------------------
    # Step 1: Extract seed tables from the technical spec
    # -----------------------------------------------------------------
    spec_tables = _extract_seed_tables_from_spec(technical_spec)

    # Merge with any externally provided seed tables
    combined_seed_tables = set(seed_tables) if seed_tables else set()
    combined_seed_tables.update(spec_tables)

    logger.info(
        "Schema assembler: spec_tables=%s, external_seeds=%s, combined=%s",
        spec_tables,
        seed_tables,
        combined_seed_tables,
    )

    # -----------------------------------------------------------------
    # Step 2: Column retrieval (reuse existing infrastructure)
    # -----------------------------------------------------------------
    retrieval_result = None
    try:
        from app.utils.jay.column_retriever import retrieve_schema_for_query

        # Only force FK expansion when the spec requests JOINs or multiple tables
        spec_joins = technical_spec.get("joins", [])
        spec_tables_count = len(technical_spec.get("tables_needed", []))
        needs_expansion = bool(spec_joins) or spec_tables_count >= 2

        retrieval_result = retrieve_schema_for_query(
            query=query,
            db_session=db_session,
            join_graph=join_graph,
            seed_tables=combined_seed_tables or None,
            previous_context=previous_context,
            force_expand=needs_expansion,
        )
    except Exception as e:
        logger.warning("Schema assembler: column retrieval failed: %s", e)

    # Collect expanded tables from retrieval (cap at 20 to avoid slow DDL builds)
    MAX_EXPANDED_TABLES = 20
    if retrieval_result and retrieval_result.expanded_tables:
        all_expanded = set(retrieval_result.expanded_tables)
        if len(all_expanded) > MAX_EXPANDED_TABLES:
            # Prioritize: spec tables first, then selected tables, then expanded
            priority = set()
            for t in combined_seed_tables:
                resolved = _resolve_table_name(t)
                if resolved in all_expanded:
                    priority.add(resolved)
            if retrieval_result.selected_tables:
                for t in retrieval_result.selected_tables:
                    if len(priority) >= MAX_EXPANDED_TABLES:
                        break
                    if t in all_expanded:
                        priority.add(t)
            # Fill remaining from expanded
            for t in sorted(all_expanded):
                if len(priority) >= MAX_EXPANDED_TABLES:
                    break
                priority.add(t)
            logger.info(
                "Schema assembler: capped expanded tables from %d to %d",
                len(all_expanded), len(priority),
            )
            ctx.expanded_tables = priority
        else:
            ctx.expanded_tables = all_expanded

    # -----------------------------------------------------------------
    # Step 3: Build DDL context (domain + dynamic, merged/deduped)
    # -----------------------------------------------------------------
    domain_ddl = ""
    dynamic_ddl = ""

    try:
        from app.utils.jay.table_catalog import get_domain_ddl

        if domains:
            domain_ddl = get_domain_ddl(domains)
    except Exception as e:
        logger.warning("Schema assembler: domain DDL retrieval failed: %s", e)

    try:
        from app.utils.jay.table_catalog import build_ddl_for_tables

        if retrieval_result and retrieval_result.expanded_tables:
            dynamic_ddl = build_ddl_for_tables(
                retrieval_result.expanded_tables, db_session
            )
    except Exception as e:
        logger.warning("Schema assembler: dynamic DDL build failed: %s", e)

    merged_ddl = _merge_ddl(domain_ddl, dynamic_ddl)
    if compact and merged_ddl:
        ctx.ddl_context = _convert_ddl_to_compact(merged_ddl)
    else:
        ctx.ddl_context = merged_ddl

    # -----------------------------------------------------------------
    # Step 4: Build JOIN context (domain + dynamic, merged)
    # -----------------------------------------------------------------
    domain_join_ctx = ""
    dynamic_join_ctx = ""

    try:
        from app.utils.jay.table_catalog import get_join_context

        if domains:
            domain_join_ctx = get_join_context(domains)
    except Exception as e:
        logger.warning("Schema assembler: domain join context failed: %s", e)

    try:
        if retrieval_result:
            dynamic_join_ctx = _build_join_context_from_retrieval(retrieval_result)
    except Exception as e:
        logger.warning("Schema assembler: dynamic join context failed: %s", e)

    ctx.join_context = _merge_join_context(domain_join_ctx, dynamic_join_ctx)

    # -----------------------------------------------------------------
    # Step 5: Fetch column values for filters needing verification
    # -----------------------------------------------------------------
    try:
        from app.utils.jay.tool_handlers import handle_get_column_values

        filter_lookups = _extract_filters_needing_values(
            technical_spec, resolved_entities
        )

        for lookup in filter_lookups:
            try:
                result = handle_get_column_values(
                    table_name=lookup["table"],
                    column_name=lookup["column"],
                    limit=25,
                    db_session=db_session,
                )
                values = result.get("values", [])
                if values:
                    key = f"{lookup['table']}.{lookup['column']}"
                    ctx.column_values[key] = values
            except Exception as e:
                logger.debug(
                    "Schema assembler: value lookup failed for %s.%s: %s",
                    lookup["table"],
                    lookup["column"],
                    e,
                )
    except Exception as e:
        logger.warning("Schema assembler: filter value fetching failed: %s", e)

    # -----------------------------------------------------------------
    # Step 6: Get similar query examples
    # -----------------------------------------------------------------
    try:
        from app.utils.jay.query_cache import find_similar_queries

        ctx.similar_queries = find_similar_queries(
            query=query,
            db_session=db_session,
            top_k=3,
        )
    except Exception as e:
        logger.warning("Schema assembler: similar query lookup failed: %s", e)

    try:
        from app.utils.jay.table_catalog import get_query_examples

        if domains:
            ctx.query_examples = get_query_examples(domains)
    except Exception as e:
        logger.warning("Schema assembler: domain query examples failed: %s", e)

    # -----------------------------------------------------------------
    # Step 7: Compute confidence (has_good_context)
    # -----------------------------------------------------------------
    requested_tables = technical_spec.get("tables_needed", [])
    if requested_tables and ctx.expanded_tables:
        # Check if at least one requested table was found in expanded tables
        # (match by short name since spec may use short names)
        expanded_short = {t.split(".")[-1] for t in ctx.expanded_tables}
        ctx.has_good_context = any(
            _resolve_table_name(t).split(".")[-1] in expanded_short
            for t in requested_tables
            if isinstance(t, str)
        )
    elif ctx.ddl_context:
        # No specific tables requested but we have DDL — moderate confidence
        ctx.has_good_context = True
    else:
        ctx.has_good_context = False

    # -----------------------------------------------------------------
    # Step 8: Timing
    # -----------------------------------------------------------------
    ctx.retrieval_time_ms = (time.monotonic() - t_start) * 1000

    logger.info(
        "Schema assembler complete: tables=%d, ddl_len=%d, joins_len=%d, "
        "values=%d, similar=%d, good_context=%s, time=%.1fms",
        len(ctx.expanded_tables),
        len(ctx.ddl_context),
        len(ctx.join_context),
        len(ctx.column_values),
        len(ctx.similar_queries),
        ctx.has_good_context,
        ctx.retrieval_time_ms,
    )

    return ctx
