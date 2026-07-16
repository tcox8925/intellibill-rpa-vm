"""
JAI Error Classifier - Classifies raw DB errors into structured categories
with LLM-friendly remediation hints for improved retry behavior.
"""

import re
import logging
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class ClassifiedError:
    """Structured error with category and remediation guidance."""
    category: str          # e.g., "column_not_found", "type_mismatch"
    raw_message: str       # Original error string
    detail: Optional[str]  # Extracted detail (e.g., the missing column name)
    remediation: str       # LLM-friendly instruction on how to fix


# Patterns checked in order — first match wins
_PATTERNS = [
    # Column does not exist
    (
        re.compile(r'column\s+"?([^"]+)"?\s+does\s+not\s+exist', re.I),
        "column_not_found",
        lambda m: m.group(1),
        "Column '{detail}' does not exist. Check the DDL for correct column names. "
        "Use ONLY columns listed in the CREATE TABLE statement. "
        "Common mistake: using a display name instead of the actual column name.",
    ),
    # Relation / table does not exist
    (
        re.compile(r'relation\s+"?([^"]+)"?\s+does\s+not\s+exist', re.I),
        "table_not_found",
        lambda m: m.group(1),
        "Table '{detail}' does not exist. Use ONLY tables from the provided DDL. "
        "Ensure you use the fully-qualified schema name (e.g., wpo.table_name).",
    ),
    # Invalid input syntax for type (date, numeric, integer, etc.)
    (
        re.compile(r'invalid\s+input\s+syntax\s+for\s+(?:type\s+)?(\w+)(?:.*?)"([^"]*)"', re.I),
        "type_mismatch",
        lambda m: f"type={m.group(1)}, value={m.group(2)}",
        "Type mismatch: tried to cast '{detail}' but the value is incompatible. "
        "Check the DDL comments for the column's actual data type and format. "
        "For date columns: if format is YYYY-MM, append '-01' before casting. "
        "If format is YYYY-MM-DD, cast directly. For numeric columns stored as VARCHAR, "
        "use NULLIF and COALESCE to handle empty strings.",
    ),
    # Division by zero
    (
        re.compile(r'division\s+by\s+zero', re.I),
        "division_by_zero",
        lambda m: None,
        "Division by zero occurred. Wrap the divisor with NULLIF(divisor, 0) "
        "to return NULL instead of erroring.",
    ),
    # Ambiguous column reference
    (
        re.compile(r'column\s+reference\s+"?([^"]+)"?\s+is\s+ambiguous', re.I),
        "ambiguous_column",
        lambda m: m.group(1),
        "Column '{detail}' is ambiguous because it exists in multiple joined tables. "
        "Qualify ALL column references with table aliases (e.g., t.{detail}, t2.{detail}).",
    ),
    # Syntax error
    (
        re.compile(r'syntax\s+error\s+(?:at\s+or\s+near\s+"?([^"]*)"?)?', re.I),
        "syntax_error",
        lambda m: m.group(1) if m.group(1) else None,
        "SQL syntax error near '{detail}'. Review the SQL structure carefully. "
        "Common issues: missing commas, unbalanced parentheses, incorrect keyword order, "
        "or using PostgreSQL syntax in Synapse (or vice versa).",
    ),
    # Permission denied
    (
        re.compile(r'permission\s+denied', re.I),
        "permission_denied",
        lambda m: None,
        "Permission denied for this table or operation. Use only tables from the provided DDL "
        "and generate only SELECT statements.",
    ),
    # Timeout
    (
        re.compile(r'(?:statement\s+)?timeout|canceling\s+statement', re.I),
        "timeout",
        lambda m: None,
        "Query timed out — it was too slow. Simplify the query: add stricter WHERE filters, "
        "reduce JOINs, use a smaller LIMIT, or avoid expensive subqueries. "
        "Consider using EXISTS instead of IN for large subqueries.",
    ),
    # Cannot cast / type conversion
    (
        re.compile(r'cannot\s+cast\s+type\s+(\w+)\s+to\s+(\w+)', re.I),
        "type_mismatch",
        lambda m: f"cannot cast {m.group(1)} to {m.group(2)}",
        "Type cast failed: {detail}. Check the DDL comments for the column's actual type. "
        "Use explicit intermediate casts if needed (e.g., text → numeric → integer).",
    ),
    # Aggregate function in WHERE clause
    (
        re.compile(r'aggregate\s+functions?\s+(?:are\s+)?not\s+allowed\s+in\s+WHERE', re.I),
        "aggregate_in_where",
        lambda m: None,
        "You cannot use aggregate functions (SUM, COUNT, AVG) in a WHERE clause. "
        "Move the aggregate condition to a HAVING clause instead.",
    ),
    # GROUP BY issues
    (
        re.compile(r'must\s+appear\s+in\s+the\s+GROUP\s+BY\s+clause', re.I),
        "group_by_missing",
        lambda m: None,
        "A selected column is not in the GROUP BY clause. Every non-aggregated column "
        "in the SELECT list must appear in the GROUP BY clause.",
    ),
    # Subquery returns more than one row
    (
        re.compile(r'more\s+than\s+one\s+row\s+returned', re.I),
        "subquery_multiple_rows",
        lambda m: None,
        "A scalar subquery returned multiple rows. Use IN, ANY, or add LIMIT 1 "
        "if you only need one value.",
    ),
    # Connection refused / unavailable
    (
        re.compile(r'connection\s+refused|could\s+not\s+connect|server\s+closed\s+the\s+connection\s+unexpectedly', re.I),
        "connection_refused",
        lambda m: None,
        "Database connection unavailable. Please try again in a moment.",
    ),
    # Out of memory
    (
        re.compile(r'out\s+of\s+memory|insufficient\s+memory', re.I),
        "out_of_memory",
        lambda m: None,
        "Query requires too much memory. Try adding filters to reduce the data set.",
    ),
    # Deadlock / lock timeout
    (
        re.compile(r'deadlock\s+detected|lock\s+timeout', re.I),
        "deadlock",
        lambda m: None,
        "Database busy with concurrent operations. Please retry.",
    ),
    # Numeric overflow
    (
        re.compile(r'numeric\s+field\s+overflow|integer\s+out\s+of\s+range|bigint\s+out\s+of\s+range', re.I),
        "numeric_overflow",
        lambda m: None,
        "Numeric value too large. Try using a different aggregation.",
    ),
    # Synapse-specific errors
    (
        re.compile(r'EXTERNAL\s+TABLE|Distribution|Polybase|Serverless\s+SQL\s+pool', re.I),
        "synapse_specific",
        lambda m: None,
        "Synapse query limitation. Try simplifying the query.",
    ),
    # Invalid object name (Synapse T-SQL)
    (
        re.compile(r'Invalid\s+object\s+name|invalid\s+column\s+name', re.I),
        "invalid_object",
        lambda m: None,
        "Table or column not found in Synapse. Check table/column names.",
    ),
]


def classify_error(raw_error: str) -> ClassifiedError:
    """Classify a raw database error into a structured category with remediation.

    Args:
        raw_error: Raw error message from database execution

    Returns:
        ClassifiedError with category, detail, and remediation hint
    """
    error_str = str(raw_error)

    for pattern, category, detail_extractor, remediation_template in _PATTERNS:
        match = pattern.search(error_str)
        if match:
            detail = detail_extractor(match)
            remediation = remediation_template.replace("{detail}", str(detail)) if detail else remediation_template.replace(" '{detail}'", "").replace("{detail}", "the value")
            return ClassifiedError(
                category=category,
                raw_message=error_str[:300],
                detail=detail,
                remediation=remediation,
            )

    # Fallback: unclassified error — log full error but don't leak to user
    logger.error(f"Unclassified DB error: {error_str}")

    # Sanitize for LLM hint: first 100 chars, strip schema/table-like identifiers
    sanitized = re.sub(r'["\']?\b\w+\.\w+\b["\']?', '[table]', error_str[:100])

    return ClassifiedError(
        category="unknown",
        raw_message="An unexpected database error occurred. Please rephrase your question or try a simpler query.",
        detail=None,
        remediation=(
            f"Database error (sanitized): {sanitized}. "
            "Review the SQL against the DDL carefully. Ensure all column names, "
            "table names, and data types match the provided schema exactly."
        ),
    )


def format_classified_error_for_llm(
    classified: ClassifiedError,
    attempt: int,
    max_attempts: int,
) -> str:
    """Format a classified error into a structured prompt section for the LLM.

    Returns a string ready for injection into the retry context.
    """
    return (
        f"ERROR TYPE: {classified.category}\n"
        f"ATTEMPT: {attempt}/{max_attempts}\n"
        f"RAW ERROR: {classified.raw_message}\n"
        f"FIX GUIDANCE: {classified.remediation}\n"
        f"You MUST generate a DIFFERENT SQL query. Do NOT repeat the same query."
    )
