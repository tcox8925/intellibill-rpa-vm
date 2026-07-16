"""
Jay SQL Validator - Whitelist-based SQL security validation.

Ensures all generated SQL:
1. Is a SELECT statement only
2. Contains no blocked DML/DDL keywords
3. References only allowed tables from the registry
4. Contains no multi-statement injection attempts
"""

import re
from app.utils.jay.semantic_registry import MODULES, BLOCKED_KEYWORDS, GLOBAL_ENTITIES
from app.utils.jay.table_catalog import get_all_registered_tables


class SQLValidationError(Exception):
    pass


def validate_sql(sql: str) -> None:
    """Validate SQL against security whitelist rules.

    Raises SQLValidationError if any rule is violated.
    """
    normalized = sql.upper().strip()

    # 1. Only SELECT statements allowed
    if not normalized.startswith("SELECT"):
        raise SQLValidationError("Only SELECT statements allowed")

    # 2. Blocked keywords
    for keyword in BLOCKED_KEYWORDS:
        pattern = rf"\b{re.escape(keyword)}\b"
        if re.search(pattern, normalized):
            # Special case: allow UNION ALL but block bare UNION
            if keyword == "UNION":
                # Check if every occurrence of UNION is followed by ALL
                union_pattern = r"\bUNION\b(?!\s+ALL\b)"
                if not re.search(union_pattern, normalized):
                    continue  # All UNION occurrences are UNION ALL — allowed
            raise SQLValidationError(f"Blocked keyword detected: {keyword}")

    # 3. Multi-statement block (semicolons except at very end)
    sql_body = sql.strip()
    if sql_body.endswith(";"):
        sql_body = sql_body[:-1]
    if ";" in sql_body:
        raise SQLValidationError("Multiple statements not allowed")

    # 4. Allowed tables only — union of semantic registry modules, global entities, and full table catalog
    allowed_tables = {
        m["table"].upper() for m in MODULES.values()
    }.union({
        e["table"].upper() for e in GLOBAL_ENTITIES.values()
    }).union(
        get_all_registered_tables()
    )

    # Extract table references after FROM or JOIN
    table_matches = re.findall(r"\bFROM\s+([A-Z0-9_\.\[\]]+)", normalized)
    table_matches += re.findall(r"\bJOIN\s+([A-Z0-9_\.\[\]]+)", normalized)

    if not table_matches:
        raise SQLValidationError("No table reference found")

    for table in table_matches:
        table_name = table.strip().rstrip(",")
        if table_name not in allowed_tables:
            raise SQLValidationError(f"Query references unauthorized table: {table_name}")

    # 5. No subquery to unauthorized tables (check nested FROM/JOIN)
    subquery_tables = re.findall(
        r"\(\s*SELECT\s+.*?\bFROM\s+([A-Z0-9_\.\[\]]+)", normalized
    )
    for table in subquery_tables:
        table_name = table.strip().rstrip(",")
        if table_name not in allowed_tables:
            raise SQLValidationError(f"Subquery references unauthorized table: {table_name}")
