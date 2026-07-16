"""
Jay DB Executor - Unified query execution using existing DB sessions.

Uses the application's existing get_db() and get_synapse_db() sessions
instead of creating raw connections.
"""

import re
import logging
from typing import List, Dict, Any, Tuple
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError

from sqlalchemy import text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

QUERY_TIMEOUT_SECONDS = 30
SYNAPSE_TIMEOUT_SECONDS = 60


class QueryExecutionError(Exception):
    """Raised when a query fails to execute."""
    pass


def _prepare_sql(sql: str, params: List[Any]) -> Tuple[str, dict]:
    """Convert %s-parameterized SQL to SQLAlchemy :named style.

    The SQL planner outputs %s placeholders (psycopg2 style).
    SQLAlchemy text() needs :named placeholders.
    """
    param_dict = {}
    counter = 0

    def replacer(match):
        nonlocal counter
        name = f"p{counter}"
        param_dict[name] = params[counter] if counter < len(params) else None
        counter += 1
        return f":{name}"

    # Also handle ? placeholders (Synapse style)
    modified_sql = re.sub(r"%s|\?", replacer, sql)
    return modified_sql, param_dict


def execute_query(
    module: str,
    sql: str,
    params: List[Any],
    db: Session,
    synapse_db: Session = None,
) -> List[Dict[str, Any]]:
    """Execute a validated SQL query and return results as list of dicts.

    Args:
        module: Module name (determines which DB to use)
        sql: Validated parameterized SQL string (%s or ? placeholders)
        params: Ordered parameter values
        db: PostgreSQL SQLAlchemy session
        synapse_db: Synapse SQLAlchemy session (optional, for bob module)

    Returns:
        List of dicts, each representing a row

    Raises:
        QueryExecutionError: On timeout or execution failure
    """
    from app.utils.jay.semantic_registry import MODULES

    module_cfg = MODULES.get(module, {})
    db_type = module_cfg.get("db_type", "postgres")

    # Convert to SQLAlchemy named params (skip if no params — LLM-generated SQL uses inline values)
    if params:
        prepared_sql, param_dict = _prepare_sql(sql, params)
    else:
        prepared_sql, param_dict = sql, {}

    try:
        if db_type == "synapse":
            if synapse_db is None:
                raise QueryExecutionError("Synapse session required for bob module")
            return _execute_synapse(synapse_db, prepared_sql, param_dict)
        else:
            return _execute_postgres(db, prepared_sql, param_dict)

    except QueryExecutionError:
        raise
    except OperationalError as e:
        error_msg = str(e).lower()
        if any(
            phrase in error_msg
            for phrase in ("connection refused", "could not connect", "server closed the connection unexpectedly",
                           "connection reset", "broken pipe", "connection timed out")
        ):
            logger.error(f"Database connection error for module {module}: {e}")
            raise QueryExecutionError(
                "Unable to connect to the database. Please try again in a moment."
            )
        logger.error(f"Query execution failed for module {module}: {e}")
        raise QueryExecutionError(f"Query failed: {str(e)[:200]}")
    except Exception as e:
        logger.error(f"Query execution failed for module {module}: {e}")
        raise QueryExecutionError(f"Query failed: {str(e)[:200]}")


def _execute_postgres(
    db: Session, sql: str, params: dict
) -> List[Dict[str, Any]]:
    """Execute query on PostgreSQL via SQLAlchemy session with timeout."""
    try:
        db.execute(text(f"SET statement_timeout = '{QUERY_TIMEOUT_SECONDS * 1000}'"))
        result = _execute_session(db, sql, params)
        return result

    except Exception as e:
        error_msg = str(e)
        if "statement timeout" in error_msg.lower() or "canceling statement" in error_msg.lower():
            raise QueryExecutionError("Query timed out. Try a more specific request.")
        raise
    finally:
        try:
            db.execute(text("RESET statement_timeout"))
        except Exception:
            logger.warning("Failed to reset statement_timeout on pooled session")


def _execute_synapse(
    db: Session, sql: str, params: dict
) -> List[Dict[str, Any]]:
    """Execute query on Synapse with a timeout.

    Synapse/pyodbc does not natively support per-query statement_timeout
    like PostgreSQL, so we wrap execution in a thread with a timeout.
    """
    def _run():
        return _execute_session(db, sql, params)

    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(_run)
        try:
            return future.result(timeout=SYNAPSE_TIMEOUT_SECONDS)
        except FuturesTimeoutError:
            logger.error(
                f"Synapse query timed out after {SYNAPSE_TIMEOUT_SECONDS}s"
            )
            raise QueryExecutionError(
                f"Query timed out after {SYNAPSE_TIMEOUT_SECONDS} seconds. "
                "Try a more specific request or add filters to reduce the data set."
            )


def _execute_session(
    db: Session, sql: str, params: dict
) -> List[Dict[str, Any]]:
    """Execute a query using a SQLAlchemy session."""
    result = db.execute(text(sql), params)
    columns = list(result.keys())
    rows = result.fetchall()
    return [dict(zip(columns, row)) for row in rows]
