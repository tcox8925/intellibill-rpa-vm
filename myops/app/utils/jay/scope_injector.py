"""
JAI Scope Injector - Step 3b of the new pipeline.

Injects entity_id/company_id WHERE clauses into LLM-generated SQL.
This is the CRITICAL security boundary — scope is NEVER controlled by the LLM.

Strategy A: Replace {SCOPE_FILTER} placeholder (LLM instructed to include it)
Strategy B: Regex fallback — detect WHERE clause, inject before GROUP BY/ORDER BY

Multi-table support:
    For JOINs, scope filters are injected on ALL tables that have known scope
    columns (per TABLE_SCOPE_COLUMNS registry), using each table's alias.
"""

import re
import logging
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# Placeholder that LLM is instructed to include in WHERE clause
SCOPE_PLACEHOLDER = "{SCOPE_FILTER}"

# ---------------------------------------------------------------------------
# Scope Column Registry
# Maps *base* table names (without schema prefix) to their scope column,
# or None when the table is global and never needs scoping.
# ---------------------------------------------------------------------------
TABLE_SCOPE_COLUMNS: Dict[str, Optional[str]] = {
    # Agent tables
    "lup_agents": "company_id",
    "agents_contracts_view": "company_id",
    "lup_agent_licenses": None,       # Licenses are global
    # Commission tables
    "vw_com_items_ai": "company_id",
    "com_totals": "company_id",
    # PCH tables
    "pch_provider_info": "company_id",
    "pch_member_roster": "company_id",
    # Membercare / Agility
    "membercare_agent_assessment_recordings": "entity_id",
    "agility_agent_assessment_recordings": None,
    # BOB (Synapse — global)
    "bob_carrier_memberships_vw": None,
    # Add more as needed...
}


class ScopeInjectionError(Exception):
    """Raised when scope injection cannot be performed safely."""


# ---------------------------------------------------------------------------
# Internal: table/alias parsing helpers
# ---------------------------------------------------------------------------

# Regex that captures  schema.table [AS] alias  or  schema.table  (no alias)
# from FROM / JOIN clauses.  The pattern deliberately stops at SQL keywords so
# that bare table references (no alias) don't swallow the next keyword.
_SQL_KEYWORDS = frozenset({
    "WHERE", "GROUP", "ORDER", "HAVING", "LIMIT", "INNER", "LEFT", "RIGHT",
    "OUTER", "CROSS", "JOIN", "ON", "FULL", "NATURAL", "USING", "SELECT",
    "DISTINCT", "TOP", "SET", "INTO", "VALUES", "UNION", "INTERSECT",
    "EXCEPT", "FETCH", "OFFSET", "WITH", "LATERAL", "WINDOW",
})

_TABLE_REF_RE = re.compile(
    r"""
    (?:FROM|JOIN)\s+               # FROM or any JOIN variant
    ([\w]+\.[\w]+|[\w]+)           # table name (possibly schema-qualified)
    (?:                            # optional alias part
        \s+AS\s+([\w]+)            #   AS alias
      | \s+([\w]+)                 #   bare alias (no AS)
    )?
    """,
    re.IGNORECASE | re.VERBOSE,
)


def _strip_schema(table_name: str) -> str:
    """Return the base table name without a schema prefix."""
    return table_name.split(".")[-1]


def _is_inside_subquery(sql: str, match_start: int) -> bool:
    """Return True if *match_start* is inside a parenthesised subquery."""
    depth = 0
    for ch in sql[:match_start]:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
    return depth > 0


def _parse_table_refs(sql: str) -> List[Tuple[str, str]]:
    """Parse all table references from FROM and JOIN clauses.

    Returns a list of (table_name, alias) tuples.  *alias* equals
    the table name (without schema) when no explicit alias is present.
    Subquery-internal references are excluded.
    """
    results: List[Tuple[str, str]] = []

    for m in _TABLE_REF_RE.finditer(sql):
        # Skip matches inside subqueries
        if _is_inside_subquery(sql, m.start()):
            continue

        table_name = m.group(1)  # schema.table or table
        alias_as = m.group(2)    # from AS alias
        alias_bare = m.group(3)  # from bare alias

        alias = alias_as or alias_bare or ""

        # If the "alias" is actually a SQL keyword, treat as no alias
        if alias and alias.upper() in _SQL_KEYWORDS:
            alias = ""

        # Default alias = base table name when none was given
        if not alias:
            alias = _strip_schema(table_name)

        results.append((table_name, alias))

    return results


def _scope_column_for_table(
    table_name: str,
    scope_columns: Dict[str, str],
) -> Optional[str]:
    """Determine the scope column name for *table_name*.

    Checks TABLE_SCOPE_COLUMNS registry first; falls back to the first
    value in the caller-supplied *scope_columns* mapping (legacy behaviour).
    Returns None if the table is explicitly global or not found in registry
    *and* no fallback is available.
    """
    base = _strip_schema(table_name)

    if base in TABLE_SCOPE_COLUMNS:
        return TABLE_SCOPE_COLUMNS[base]  # may be None (global)

    # Fallback: use the first column from the module-level scope_columns
    # (preserves backward compat with callers that don't use the registry).
    if scope_columns:
        first_col = next(iter(scope_columns.values()), None)
        return first_col

    return None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def inject_scope(
    sql: str,
    scope: Dict[str, str],
    scope_columns: Dict[str, str],
    table_alias: str = None,
    db_type: str = "postgres",
) -> str:
    """Inject entity_id/company_id security filters into SQL.

    Args:
        sql: The LLM-generated SQL query
        scope: Permission scope dict (entity_id, sub_entity_id values from JWT)
        scope_columns: Module's scope column mapping (e.g., {"entity_id": "company_id"})
        table_alias: Table alias to prefix columns (auto-detected if None).
                     Ignored for multi-table queries — each table gets its own alias.
        db_type: Database type (postgres or synapse)

    Returns:
        SQL with scope filters injected (or unmodified if scope values are missing)
    """
    # --- Empty scope validation ---
    if not _validate_scope_values(scope, scope_columns):
        # Scope values missing — run unscoped, caller sets assumption
        logger.info("scope_injector: no entity selected — returning SQL unscoped")
        return _remove_placeholder(sql)

    # --- Build scope clause (multi-table aware) ---
    scope_clause = _build_scope_clause_multi(sql, scope, scope_columns, table_alias)

    if not scope_clause:
        logger.debug("scope_injector: no scope clause produced — removing placeholder if present")
        # No scope needed — remove the placeholder if present
        return _remove_placeholder(sql)

    logger.debug("scope_injector: final scope clause = %s", scope_clause)

    # Strategy A: Replace {SCOPE_FILTER} placeholder
    if SCOPE_PLACEHOLDER in sql:
        injected = sql.replace(SCOPE_PLACEHOLDER, scope_clause)
        logger.debug("scope_injector: injected via placeholder strategy. Final SQL:\n%s", injected)
        return injected

    # Strategy B: Regex fallback — inject into WHERE clause
    injected = _inject_fallback(sql, scope_clause)
    logger.debug("scope_injector: injected via fallback strategy. Final SQL:\n%s", injected)
    return injected


# ---------------------------------------------------------------------------
# Internal: scope clause builders
# ---------------------------------------------------------------------------

def _validate_scope_values(
    scope: Dict[str, str],
    scope_columns: Dict[str, str],
) -> bool:
    """Check if scope values are present for scoped modules.

    Returns True if scope can be applied, False if scope values are missing.
    When False, the caller should skip scope injection (query runs unscoped)
    and set an assumption flag so the user is informed.

    If scope_columns is empty the module is global — always returns True.
    """
    if not scope_columns:
        # Module explicitly has no scope columns — it's global
        return True

    for scope_key, column_name in scope_columns.items():
        value = scope.get(scope_key)
        if not value or (isinstance(value, str) and not value.strip()):
            logger.warning(
                "Scope injection skipped for column '%s' — no scope value for key '%s'. "
                "Query will run without entity filtering.",
                column_name, scope_key,
            )
            return False

    return True


def _build_scope_clause_multi(
    sql: str,
    scope: Dict[str, str],
    scope_columns: Dict[str, str],
    explicit_alias: str = None,
) -> str:
    """Build scope clause covering ALL tables in the query that need scoping.

    For single-table queries this behaves identically to the original
    _build_scope_clause.  For multi-table JOINs each table gets its own
    prefixed condition joined with AND.
    """
    if not scope_columns:
        return ""

    table_refs = _parse_table_refs(sql)
    logger.debug("scope_injector: tables found in SQL: %s", table_refs)

    if not table_refs:
        # No tables detected — fall back to legacy single-alias behaviour
        alias = explicit_alias or _detect_alias(sql)
        logger.debug("scope_injector: no table refs parsed, falling back to alias=%s", alias)
        return _build_scope_clause(scope, scope_columns, alias)

    clauses: List[str] = []
    injected_tables: List[str] = []
    skipped_tables: List[Tuple[str, str]] = []

    for table_name, alias in table_refs:
        col = _scope_column_for_table(table_name, scope_columns)

        if col is None:
            skipped_tables.append((table_name, "global / no scope column"))
            continue

        # Build clause for this table using its alias
        for scope_key, mapped_col in scope_columns.items():
            scope_value = scope.get(scope_key)
            if not scope_value:
                # Already validated above — skip gracefully
                continue

            # Determine the actual column name for this specific table.
            # The registry tells us the table's column; the scope_columns
            # mapping tells us which scope key maps to which DB column.
            # For tables in the registry we use the registry column name;
            # for fallback tables we use the module mapping.
            actual_col = col if col else mapped_col
            safe_value = scope_value.replace("'", "''")
            prefix = f"{alias}." if alias else ""
            clauses.append(f"{prefix}{actual_col} = '{safe_value}'")
            injected_tables.append(f"{table_name} (as {alias})")

    logger.debug("scope_injector: injected scope on tables: %s", injected_tables)
    logger.debug("scope_injector: skipped tables: %s", skipped_tables)

    return " AND ".join(clauses)


def _build_scope_clause(
    scope: Dict[str, str],
    scope_columns: Dict[str, str],
    table_alias: str,
) -> str:
    """Build the scope WHERE clause parts (legacy single-table path)."""
    if not scope_columns:
        return ""

    clauses = []
    prefix = f"{table_alias}." if table_alias else ""

    for scope_key, column_name in scope_columns.items():
        scope_value = scope.get(scope_key)
        if scope_value:
            # Inline the value (safe — comes from JWT session, not user input)
            safe_value = scope_value.replace("'", "''")
            clauses.append(f"{prefix}{column_name} = '{safe_value}'")

    return " AND ".join(clauses)


def _detect_alias(sql: str) -> str:
    """Auto-detect the primary table alias from the first FROM clause.

    Handles:
        FROM schema.table AS alias
        FROM schema.table alias
        FROM table AS alias
        FROM table alias
        JOIN schema.table AS alias ON ...
        JOIN schema.table alias ON ...

    Skips subqueries (matches inside parentheses).
    """
    # Try to find the first FROM that is NOT inside a subquery
    for m in re.finditer(
        r'\b(?:FROM|JOIN)\s+([\w]+(?:\.[\w]+)?)\s+(?:AS\s+)?([\w]+)',
        sql,
        re.IGNORECASE,
    ):
        if _is_inside_subquery(sql, m.start()):
            continue

        alias = m.group(2)
        if alias.upper() not in _SQL_KEYWORDS:
            return alias

    return ""


def _remove_placeholder(sql: str) -> str:
    """Remove the scope placeholder when no scope is needed.

    Handles whitespace/newline variations the LLM may produce:
      WHERE {SCOPE_FILTER} AND ...  →  WHERE ...
      WHERE ... AND {SCOPE_FILTER}  →  WHERE ...
      WHERE {SCOPE_FILTER}          →  (WHERE removed)
    """
    ph = re.escape(SCOPE_PLACEHOLDER)

    # Remove "{SCOPE_FILTER} AND" (with flexible whitespace)
    result = re.sub(rf'{ph}\s+AND\s+', '', sql, flags=re.IGNORECASE)
    # Remove "AND {SCOPE_FILTER}" (with flexible whitespace)
    result = re.sub(rf'\s+AND\s+{ph}', '', result, flags=re.IGNORECASE)
    # Remove bare placeholder
    result = result.replace(SCOPE_PLACEHOLDER, "")

    # Clean up empty WHERE clause
    result = re.sub(r'\bWHERE\s*$', '', result, flags=re.IGNORECASE | re.MULTILINE)
    result = re.sub(r'\bWHERE\s+(GROUP|ORDER|LIMIT|HAVING)\b', r'\1', result, flags=re.IGNORECASE)
    # WHERE followed by nothing meaningful (just whitespace then newline/end)
    result = re.sub(r'\bWHERE\s*\n', '\n', result, flags=re.IGNORECASE)

    return result.strip()


def _inject_fallback(sql: str, scope_clause: str) -> str:
    """Fallback injection when LLM didn't include the placeholder."""

    # Case 1: WHERE clause exists — append scope with AND
    where_match = re.search(r'\bWHERE\b', sql, re.IGNORECASE)
    if where_match:
        where_pos = where_match.end()
        return sql[:where_pos] + f" {scope_clause} AND" + sql[where_pos:]

    # Case 2: No WHERE clause — inject before GROUP BY, ORDER BY, LIMIT, or end
    for keyword in ["GROUP BY", "ORDER BY", "HAVING", "LIMIT"]:
        match = re.search(rf'\b{keyword}\b', sql, re.IGNORECASE)
        if match:
            pos = match.start()
            return sql[:pos] + f"WHERE {scope_clause} " + sql[pos:]

    # Case 3: No WHERE, no GROUP BY, no ORDER BY — append at end
    return sql + f" WHERE {scope_clause}"
