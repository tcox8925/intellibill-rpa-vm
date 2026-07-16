"""
JAI Safety Reviewer - Step 4 of the new pipeline.

Programmatic-only validation using validate_sql() as a hard gate.
Checks: SELECT-only, blocked keywords, table whitelist, no multi-statement.
"""

import logging

from app.utils.jay.pipeline_models import SafetyReviewResult, PipelineContext
from app.utils.jay.sql_validator import validate_sql, SQLValidationError

logger = logging.getLogger(__name__)


def review_sql_safety(
    sql: str,
    module: str,
    ddl: str = None,
    context: PipelineContext = None,
    domains: list = None,
) -> SafetyReviewResult:
    """Review SQL query safety with programmatic validation.

    Args:
        sql: The SQL query to validate
        module: Module name (for table whitelist context)
        ddl: Module DDL (unused, kept for API compatibility)
        context: Pipeline context for logging
        domains: List of domains (unused, kept for API compatibility)

    Returns:
        SafetyReviewResult with is_safe, issues, and severity
    """
    # Programmatic validation (HARD GATE)
    try:
        validate_sql(sql)
    except SQLValidationError as e:
        return SafetyReviewResult(
            is_safe=False,
            issues=[str(e)],
            severity="block",
        )

    result = SafetyReviewResult(
        is_safe=True,
        issues=[],
        severity="pass",
    )

    if context is not None:
        context.add("safety_review", "safety_status", "pass")

    return result
