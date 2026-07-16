"""
JAI Tool Schemas & Dispatcher - Azure OpenAI function-calling tool definitions.

Defines 7 tool schemas in the Azure OpenAI ``tools`` format and a central
dispatcher that routes tool calls to their handler implementations in
``app.utils.jay.tool_handlers``.

Handler imports are lazy (inside the dispatcher) to avoid circular imports
with the pipeline and sql_generator modules that also import from this package.

Public API:
- TOOL_SCHEMAS: list  — all 7 tool dicts, passed directly to the OpenAI API
- dispatch_tool(name, arguments, db_session, join_graph=None) -> str (JSON)
"""

from __future__ import annotations

import json
import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)


# =============================================================================
# Tool Schema Definitions
# =============================================================================

_SEARCH_TABLES = {
    "type": "function",
    "function": {
        "name": "search_tables",
        "description": (
            "Search for database tables relevant to a query. "
            "Returns table names, descriptions, and key columns."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Natural language description of the data you are looking for.",
                },
                "top_k": {
                    "type": "integer",
                    "description": "Maximum number of tables to return.",
                    "default": 5,
                },
            },
            "required": ["query"],
        },
    },
}

_GET_TABLE_SCHEMA = {
    "type": "function",
    "function": {
        "name": "get_table_schema",
        "description": (
            "Get the full DDL (CREATE TABLE statement) and column details for a specific table. "
            "Use this to see exact column names, data types, and comments."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "table_name": {
                    "type": "string",
                    "description": (
                        "Table name to look up. May include schema prefix "
                        "(e.g., 'wpo.lup_agents') or just the table name."
                    ),
                },
            },
            "required": ["table_name"],
        },
    },
}

_GET_COLUMN_VALUES = {
    "type": "function",
    "function": {
        "name": "get_column_values",
        "description": (
            "Get distinct values for a specific column. Useful for understanding filter values "
            "and their exact casing (e.g., 'Active' not 'active')."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "table_name": {
                    "type": "string",
                    "description": "Table name containing the column (schema prefix optional).",
                },
                "column_name": {
                    "type": "string",
                    "description": "Column name to retrieve distinct values for.",
                },
                "limit": {
                    "type": "integer",
                    "description": "Maximum number of distinct values to return.",
                    "default": 25,
                },
            },
            "required": ["table_name", "column_name"],
        },
    },
}

_GET_FOREIGN_KEYS = {
    "type": "function",
    "function": {
        "name": "get_foreign_keys",
        "description": (
            "Get foreign key relationships for a table. "
            "Shows which columns can be used to JOIN with other tables."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "table_name": {
                    "type": "string",
                    "description": "Table name to look up FK relationships for (schema prefix optional).",
                },
            },
            "required": ["table_name"],
        },
    },
}

_GET_JOIN_EXAMPLES = {
    "type": "function",
    "function": {
        "name": "get_join_examples",
        "description": (
            "Get example JOIN SQL between two tables. "
            "Shows the recommended JOIN condition and type."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "table1": {
                    "type": "string",
                    "description": "First table name (schema prefix optional).",
                },
                "table2": {
                    "type": "string",
                    "description": "Second table name to join with (schema prefix optional).",
                },
            },
            "required": ["table1", "table2"],
        },
    },
}

_GET_SIMILAR_QUERIES = {
    "type": "function",
    "function": {
        "name": "get_similar_queries",
        "description": (
            "Search for similar past queries that were successful. "
            "Returns the natural language query and the SQL that worked."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Natural language query to find similar past examples for.",
                },
                "top_k": {
                    "type": "integer",
                    "description": "Maximum number of similar queries to return.",
                    "default": 3,
                },
            },
            "required": ["query"],
        },
    },
}

_VALIDATE_SQL = {
    "type": "function",
    "function": {
        "name": "validate_sql",
        "description": (
            "Validate SQL syntax without executing it. Checks for common errors like "
            "missing columns, invalid table references, and syntax issues."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "sql": {
                    "type": "string",
                    "description": "SQL query string to validate.",
                },
            },
            "required": ["sql"],
        },
    },
}

_GET_TABLE_SCHEMAS_BULK = {
    "type": "function",
    "function": {
        "name": "get_table_schemas",
        "description": (
            "Get DDL and column details for MULTIPLE tables in one call. "
            "More efficient than calling get_table_schema multiple times."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "table_names": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "List of table names to look up. May include schema prefix "
                        "(e.g., ['wpo.lup_agents', 'wpo.agents_contracts_view'])."
                    ),
                },
            },
            "required": ["table_names"],
        },
    },
}

_GET_COLUMN_VALUES_BULK = {
    "type": "function",
    "function": {
        "name": "get_column_values_bulk",
        "description": (
            "Get distinct values for MULTIPLE columns in one call. "
            "More efficient than calling get_column_values multiple times. "
            "Use this when you need to check values for several columns at once."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "columns": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "table_name": {"type": "string"},
                            "column_name": {"type": "string"},
                        },
                        "required": ["table_name", "column_name"],
                    },
                    "description": (
                        "List of {table_name, column_name} pairs to retrieve distinct values for."
                    ),
                },
                "limit": {
                    "type": "integer",
                    "description": "Maximum number of distinct values per column.",
                    "default": 25,
                },
            },
            "required": ["columns"],
        },
    },
}


# Ordered list of all tool schemas — pass this directly to the OpenAI API.
TOOL_SCHEMAS: list = [
    _SEARCH_TABLES,
    _GET_TABLE_SCHEMA,
    _GET_TABLE_SCHEMAS_BULK,
    _GET_COLUMN_VALUES,
    _GET_COLUMN_VALUES_BULK,
    _GET_FOREIGN_KEYS,
    _GET_JOIN_EXAMPLES,
    _GET_SIMILAR_QUERIES,
    _VALIDATE_SQL,
]


# =============================================================================
# Dispatcher
# =============================================================================


def dispatch_tool(
    name: str,
    arguments: Any,
    db_session,
    join_graph=None,
) -> str:
    """Dispatch a tool call to its handler. Returns a JSON string result.

    Handles both pre-parsed ``dict`` arguments and raw JSON strings that the
    Azure OpenAI API may return when ``tool_calls[i].function.arguments`` is
    accessed directly.

    Args:
        name: The tool function name (must match one of the TOOL_SCHEMAS names).
        arguments: Either a dict or a JSON-encoded string of keyword arguments.
        db_session: SQLAlchemy Session for database-backed handlers.
        join_graph: Optional pre-built JoinGraph for FK-based handlers.

    Returns:
        JSON-encoded string.  Always a string — callers must parse if needed.
        On any unhandled exception the return value is ``{"error": "<message>"}``.
    """
    # --- Normalise arguments -----------------------------------------------
    if isinstance(arguments, str):
        try:
            arguments = json.loads(arguments)
        except json.JSONDecodeError as exc:
            logger.warning("dispatch_tool: could not parse arguments JSON for %s: %s", name, exc)
            arguments = {}

    if not isinstance(arguments, dict):
        arguments = {}

    # --- Lazy handler imports (avoids circular imports) --------------------
    try:
        import app.utils.jay.tool_handlers as tool_handlers  # noqa: PLC0415
    except ImportError as exc:
        logger.error("dispatch_tool: could not import tool_handlers: %s", exc)
        return json.dumps({"error": f"tool_handlers module not available: {exc}"})

    # --- Route to the correct handler -------------------------------------
    try:
        if name == "search_tables":
            result = tool_handlers.handle_search_tables(
                query=arguments.get("query", ""),
                top_k=arguments.get("top_k", 5),
                db_session=db_session,
                join_graph=join_graph,
            )

        elif name == "get_table_schema":
            result = tool_handlers.handle_get_table_schema(
                table_name=arguments.get("table_name", ""),
                db_session=db_session,
            )

        elif name == "get_table_schemas":
            result = tool_handlers.handle_get_table_schemas_bulk(
                table_names=arguments.get("table_names", []),
                db_session=db_session,
            )

        elif name == "get_column_values":
            result = tool_handlers.handle_get_column_values(
                table_name=arguments.get("table_name", ""),
                column_name=arguments.get("column_name", ""),
                limit=arguments.get("limit", 25),
                db_session=db_session,
            )

        elif name == "get_column_values_bulk":
            result = tool_handlers.handle_get_column_values_bulk(
                columns=arguments.get("columns", []),
                limit=arguments.get("limit", 25),
                db_session=db_session,
            )

        elif name == "get_foreign_keys":
            result = tool_handlers.handle_get_foreign_keys(
                table_name=arguments.get("table_name", ""),
                db_session=db_session,
                join_graph=join_graph,
            )

        elif name == "get_join_examples":
            result = tool_handlers.handle_get_join_examples(
                table1=arguments.get("table1", ""),
                table2=arguments.get("table2", ""),
                db_session=db_session,
                join_graph=join_graph,
            )

        elif name == "get_similar_queries":
            result = tool_handlers.handle_get_similar_queries(
                query=arguments.get("query", ""),
                top_k=arguments.get("top_k", 3),
                db_session=db_session,
            )

        elif name == "validate_sql":
            result = tool_handlers.handle_validate_sql(
                sql=arguments.get("sql", ""),
            )

        else:
            logger.warning("dispatch_tool: unknown tool name '%s'", name)
            result = {"error": f"Unknown tool: {name}"}

    except Exception as exc:  # noqa: BLE001
        logger.exception("dispatch_tool: handler raised an exception for tool '%s'", name)
        result = {"error": str(exc)}

    # --- Serialise result -------------------------------------------------
    try:
        return json.dumps(result, default=str)
    except (TypeError, ValueError) as exc:
        logger.warning("dispatch_tool: could not JSON-serialise result for %s: %s", name, exc)
        return json.dumps({"error": f"Result serialisation failed: {exc}"})
