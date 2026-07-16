"""
JAI v2 System Prompts - Hybrid pipeline (spec → SQL → recovery → fallback).

Architecture:
1. TECHNICAL_SPEC  — Classify query into structured JSON spec (no tools, no DDL)
2. SQL_GENERATION  — Generate SQL from spec + pre-retrieved DDL/values (no tools)
3. ERROR_RECOVERY  — Fix failed SQL with limited tool access
4. TOOL_FALLBACK   — Schema exploration when retrieval found nothing (max 3 calls)

Builder functions:
- build_technical_spec_prompt()
- build_sql_generation_retrieval_prompt()
- build_error_recovery_prompt()
- build_tool_fallback_prompt()
"""

import json
from datetime import date


# =====================================================
# 1. TECHNICAL SPEC — Query → structured JSON spec
# =====================================================

TECHNICAL_SPEC_SYSTEM_PROMPT = """You are a database query planner. Analyze the user's question and produce a structured technical specification as JSON.

You are given synonym mappings, a business glossary, and optionally similar past queries. Use these to translate business language into database terms. Do NOT explore the database — just classify and plan.

PROCESS:
1. Identify which tables the question needs using the synonym mappings and glossary
2. Map business terms to actual column names using the synonyms
3. Determine filters, aggregations, grouping, and join paths
4. If a similar past query matches the pattern, adopt its structure
5. Output a single JSON object

OUTPUT FORMAT (respond with ONLY this JSON, no markdown):
{{
  "tables_needed": ["table_name"],
  "columns_needed": [
    {{"table": "table_name", "column": "col_name", "purpose": "filter|select|join|aggregate"}}
  ],
  "joins": [
    {{"from_table": "t1", "from_col": "c1", "to_table": "t2", "to_col": "c2"}}
  ],
  "filters": [
    {{"column": "table.column", "operator": "=", "value": "SomeValue", "needs_verification": true}}
  ],
  "aggregations": [
    {{"function": "COUNT|SUM|AVG|MIN|MAX", "column": "col_or_*", "alias": "result_name"}}
  ],
  "group_by": ["column_name"],
  "group_by_period": null,
  "order_by": null,
  "limit": null,
  "query_type": "data|existential|navigation|ambiguous",
  "assumptions": ["plain English assumption about ambiguity"],
  "confidence": "high|medium|low"
}}

RULES:
- Use synonym mappings to translate business terms → DB columns (e.g., "commission" → "payment")
- Use glossary sql_hints for known concepts (e.g., "producer" → appointment_type = 'Producer')
- Set needs_verification: true on filter values where exact DB casing is uncertain
- For follow-ups: reuse previous tables/columns and layer on new filters or breakdowns
- group_by_period accepts: "month", "quarter", "year", or null
- If the query is ambiguous, make your best guess and document it in assumptions
- Keep assumptions in plain business English — no table or column names
- confidence: "high" = exact match to known pattern, "medium" = reasonable inference, "low" = uncertain mapping
- query_type: "data" for normal data retrieval, "existential" for yes/no existence checks, "navigation" for UI routing questions, "ambiguous" for multi-entity ambiguity
- If the question is existential (e.g., "do we have X?", "is there a Y?", "does X exist?", "are there any X?"), set query_type to "existential" and generate a spec that checks for existence — return matching rows (not just COUNT). Use ILIKE with wildcards for fuzzy name matching. Limit to 10 results.
- If the question mentions an entity that could match multiple types (e.g., "john smith" could be an agent or a carrier), set query_type to "ambiguous" and list the possible entity types in assumptions.
- Do NOT include DDL, CREATE TABLE, or schema details — just the logical plan
- ONLY list columns you can confirm from the synonym mappings or glossary. Do NOT invent column names.
- For broad "details" or "info" queries: list the table but leave columns_needed empty or minimal — the SQL generator will SELECT all available columns from the schema.

Current date: {current_date}
"""


# =====================================================
# 2. SQL GENERATION — Spec + DDL → SQL (no tools)
# =====================================================

SQL_GENERATION_RETRIEVAL_PROMPT = """You are a SQL expert. Generate a read-only SELECT query from the specification and schema provided in XML tags below.

CRITICAL:
- Use ONLY columns listed in <available_columns>. If a column is not listed there, it does NOT exist.
- If the specification requests columns that don't exist, IGNORE those columns and use the closest available alternatives from <available_columns>. For broad "details" queries, SELECT all key columns from the table.
- Only return "CANNOT_GENERATE" if you truly cannot build ANY useful query from the available columns.

RULES:
1. Single SELECT only. Schema-qualified table names (e.g., wpo.lup_agents).
2. Short aliases: t, t2, t3. Qualify ALL column references with aliases.
3. WHERE clause MUST include {{SCOPE_FILTER}}: WHERE {{SCOPE_FILTER}} AND <conditions>
4. Add LIMIT 50 (PostgreSQL) or SELECT TOP 50 (Synapse).
5. For aggregations on text columns: SUM(COALESCE(NULLIF(t.col::TEXT, '')::NUMERIC, 0))
6. Categorical columns with known values in DDL: use EXACT casing. No LOWER()/ILIKE.
7. Free-text search (names, emails): use ILIKE.
8. LEFT JOIN by default. UUID-to-TEXT joins: t.pk_id::TEXT = t2.fk_col
9. Date as VARCHAR: 'YYYY-MM-DD' → cast directly. 'YYYY-MM' → append '-01'.
10. coverage_month/statement_month are DATE — do NOT append '-01'.
11. Use mem_id (not member_id), company (not company_id), primary_speciality (one L).
12. RESOLVED ENTITIES: When <resolved_entities> provides a value (e.g., npn: '12345'), ALWAYS filter by that key column (WHERE t.npn = '12345') instead of ILIKE on names. Resolved entities are exact DB matches — use them directly.

Current date: {current_date}
Database type: {db_type}

Return ONLY the SQL query. No markdown, no explanation, no code fences.
"""


# =====================================================
# 3. ERROR RECOVERY — Fix failed SQL (limited tools)
# =====================================================

ERROR_RECOVERY_SYSTEM_PROMPT = """You are a SQL debugger. A query failed — diagnose and fix it.

Available tools:
- get_table_schema(table_name): Get columns for ONE table
- get_table_schemas(table_names): Get columns for MULTIPLE tables in one call — prefer this
- get_column_values(table, column): Check exact values for one known column
- get_column_values_bulk(columns): Check values for MULTIPLE columns in one call — prefer this

PROCESS:
1. Read the error message carefully
2. Call get_table_schemas (bulk) ONCE with ALL tables that have failing columns
3. Remove or replace ALL invalid columns in one go using the actual schema
4. If a needed column is on a different related table, add a JOIN
5. Return ONLY the fixed SQL — no markdown, no explanation

RULES:
- Use BULK tools (get_table_schemas, get_column_values_bulk) — never call single-table tools in a loop
- Fix ALL column errors at once after seeing the schema — do NOT iterate one column at a time
- Keep {{SCOPE_FILTER}} in the WHERE clause
- Use exact column names from schema results
- Maximum 7 tool calls total.

Current date: {current_date}
Database type: {db_type}
"""


# =====================================================
# 4. TOOL FALLBACK — Schema exploration (max 3 calls)
# =====================================================

TOOL_FALLBACK_SYSTEM_PROMPT = """You are a database query builder. The automated schema retrieval found nothing useful for this question. Use tools to find the right tables and generate SQL.

Available tools:
- search_tables(keywords): Find tables matching keywords
- get_table_schema(table_name): Get full DDL for one table
- get_table_schemas(table_names): Get DDL for MULTIPLE tables in one call — prefer this
- get_column_values(table, column): Check valid values for one column
- get_column_values_bulk(columns): Check values for MULTIPLE columns in one call — prefer this
- get_foreign_keys(table_name): Get JOIN relationships

STRICT LIMITS:
- Maximum 7 tool calls total. Plan carefully.
- Use BULK tools when you need multiple tables or columns.
- Recommended sequence: search_tables → get_table_schemas (bulk) → generate SQL

PROCESS:
1. Call search_tables with 2-3 key terms from the question
2. Call get_table_schemas with ALL matching tables at once
3. Generate a SELECT query using the schema

SQL RULES:
- Single SELECT only. Schema-qualified names (e.g., wpo.table_name).
- Aliases: t, t2, t3. Include {{SCOPE_FILTER}} in WHERE.
- Add LIMIT 50. Use exact casing for categorical values.
- If you cannot find a relevant table after search, return: CANNOT_GENERATE: No matching tables found for this question.

Current date: {current_date}

Return ONLY the final SQL query after your tool calls. No markdown, no code fences.
"""


# =====================================================
# BUILDER FUNCTIONS
# =====================================================


def _format_similar_queries(similar_queries: list) -> str:
    """Format similar past query+SQL pairs for the technical spec prompt."""
    if not similar_queries:
        return ""
    lines = [
        "SIMILAR PAST QUERIES (reuse these patterns if they match):",
    ]
    for i, sq in enumerate(similar_queries[:5], 1):
        query_text = sq.get("query", "")
        sql_text = sq.get("sql", "")
        similarity = sq.get("similarity", "")
        lines.append(f"  {i}. Q: {query_text}")
        if sql_text:
            lines.append(f"     SQL: {sql_text}")
        if similarity:
            lines.append(f"     Similarity: {similarity}")
    return "\n".join(lines)


def _format_previous_context(previous_context: dict) -> str:
    """Format previous conversation context for follow-up handling."""
    if not previous_context:
        return ""
    lines = [
        "PREVIOUS TURN CONTEXT:",
    ]
    if previous_context.get("module"):
        lines.append(f"  Module: {previous_context['module']}")
    if previous_context.get("domains"):
        domains = previous_context["domains"]
        if isinstance(domains, list):
            domains = ", ".join(domains)
        lines.append(f"  Domains: {domains}")
    if previous_context.get("user_summary"):
        lines.append(f"  Question: {previous_context['user_summary']}")
    if previous_context.get("sql"):
        lines.append(f"  SQL:\n{previous_context['sql']}")
    lines.append(
        "\nIf the current question is a follow-up (references 'these', 'those', "
        "'them', 'out of', 'break down'), reuse the same tables and add new "
        "filters or groupings on top. Only switch tables if the topic clearly changes."
    )
    return "\n".join(lines)


def _format_resolved_entities(resolved_entities: dict) -> str:
    """Format resolved entity values into a prompt block."""
    if not resolved_entities:
        return ""
    lines = ["RESOLVED ENTITY VALUES:"]
    for key, value in resolved_entities.items():
        if isinstance(value, list):
            quoted = ", ".join(f"'{item}'" for item in value)
            lines.append(f"  {key}: IN ({quoted})")
        else:
            lines.append(f"  {key}: '{value}'")
    return "\n".join(lines)


def _format_column_values(column_values: dict) -> str:
    """Format pre-retrieved column values for SQL generation.

    Args:
        column_values: dict of {table.column: [values]} or {table.column: str}
    """
    if not column_values:
        return ""
    lines = ["EXACT COLUMN VALUES (use these — do not guess casing):"]
    for col_key, values in column_values.items():
        if isinstance(values, list):
            formatted = ", ".join(f"'{v}'" for v in values[:30])
            lines.append(f"  {col_key}: {formatted}")
        else:
            lines.append(f"  {col_key}: '{values}'")
    return "\n".join(lines)


# ─── 1. Technical Spec Builder ───────────────────────────────────

def build_technical_spec_prompt(
    query: str,
    module: str = None,
    domains: list = None,
    synonym_context: str = None,
    glossary_context: str = None,
    similar_queries: list = None,
    previous_context: dict = None,
    resolved_entities: dict = None,
    assumption: str = None,
    categorical_values: str = None,
) -> tuple:
    """Build the technical spec prompt (Step 1 — fast classification, no tools).

    Args:
        query: The user's natural language question.
        module: Module identified by intent detection (e.g., "commission_totals").
        domains: Domains from intent detection (e.g., ["agent_profile", "commission_totals"]).
        synonym_context: Pre-formatted synonym string from get_synonym_context().
        glossary_context: Pre-formatted glossary string from get_business_glossary_context().
        similar_queries: List of dicts [{query, sql, similarity}] from query cache.
        previous_context: Dict with previous turn metadata {module, sql, user_summary, domains}.
        resolved_entities: Dict of resolved entity values {carrier_name: "Ambetter"}.
        assumption: Assumption string from intent detection.
        categorical_values: Pre-formatted string of known categorical column values.

    Returns:
        Tuple of (system_prompt, user_message).
    """
    system_prompt = TECHNICAL_SPEC_SYSTEM_PROMPT.replace(
        "{current_date}", date.today().isoformat()
    )

    # Build user message with all context sections
    user_parts = []

    # Module/domain context from intent detection
    if module or domains:
        ctx = "INTENT DETECTION CONTEXT:"
        if module:
            ctx += f"\n  Module: {module}"
        if domains:
            ctx += f"\n  Domains: {', '.join(domains) if isinstance(domains, list) else domains}"
        if assumption:
            ctx += f"\n  Assumption: {assumption}"
        user_parts.append(ctx)

    # Synonym mappings
    if synonym_context:
        user_parts.append(synonym_context)

    # Business glossary
    if glossary_context:
        user_parts.append(glossary_context)

    # Categorical values for filter matching
    if categorical_values:
        user_parts.append(categorical_values)

    # Similar past queries
    similar_block = _format_similar_queries(similar_queries)
    if similar_block:
        user_parts.append(similar_block)

    # Previous conversation context
    prev_block = _format_previous_context(previous_context)
    if prev_block:
        user_parts.append(prev_block)

    # Resolved entities
    entities_block = _format_resolved_entities(resolved_entities)
    if entities_block:
        user_parts.append(entities_block)

    # The actual question (always last)
    user_parts.append(f"USER QUESTION: {query}")

    user_message = "\n\n".join(user_parts)

    return system_prompt, user_message


# ─── 2. SQL Generation Builder ───────────────────────────────────

def build_sql_generation_retrieval_prompt(
    query: str,
    technical_spec: dict,
    ddl_context: str,
    join_context: str,
    column_values: dict = None,
    resolved_entities: dict = None,
    format_hint: str = "auto",
    user_summary: str = None,
    assumption: str = None,
    previous_sql: str = None,
    previous_user_summary: str = None,
) -> tuple:
    """Build the SQL generation prompt (Step 2 — no tools, pre-retrieved context).

    Args:
        query: The user's natural language question.
        technical_spec: Structured JSON spec from step 1.
        ddl_context: Pre-retrieved DDL (CREATE TABLE statements) for relevant tables.
        join_context: Pre-retrieved JOIN context for table pairs.
        column_values: Dict of {table.column: [values]} with exact DB casing.
        resolved_entities: Dict of resolved entity values {npn: "12345678"}.
        format_hint: Display format hint ("auto", "kpi", "bar_chart", etc.).
        user_summary: Short summary of what the user wants.
        assumption: Assumption from intent detection.
        previous_sql: SQL from the previous conversation turn (for follow-ups).
        previous_user_summary: User summary from the previous turn.

    Returns:
        Tuple of (system_prompt, user_message).
    """
    # Determine db_type from technical spec or default
    db_type = "POSTGRESQL"
    if technical_spec and isinstance(technical_spec, dict):
        db_type = technical_spec.get("db_type", "POSTGRESQL")

    system_prompt = SQL_GENERATION_RETRIEVAL_PROMPT.replace(
        "{current_date}", date.today().isoformat()
    ).replace(
        "{db_type}", db_type.upper()
    )

    # Build user message with XML-tagged sections for clear context boundaries
    user_parts = []

    # Technical spec
    if technical_spec:
        user_parts.append(
            "<specification>\n" + json.dumps(technical_spec, indent=2) + "\n</specification>"
        )

    # Format hint
    user_parts.append(f"<format_hint>{format_hint}</format_hint>")

    # DDL context
    if ddl_context:
        user_parts.append(
            "<schema>\n" + ddl_context + "\n</schema>"
        )

        # Extract and inject available columns as flat reference
        from app.utils.jay.schema_assembler import extract_column_inventory
        col_inv = extract_column_inventory(ddl_context)
        if col_inv:
            col_lines = []
            for tbl, cols in col_inv.items():
                col_lines.append(f"  {tbl}: {', '.join(cols)}")
            user_parts.append(
                "<available_columns>\n" + "\n".join(col_lines) + "\n</available_columns>"
            )
    else:
        user_parts.append("<schema>(none provided)</schema>")

    # JOIN context
    if join_context:
        user_parts.append(
            "<join_context>\n" + join_context + "\n</join_context>"
        )

    # Column values
    col_vals_block = _format_column_values(column_values)
    if col_vals_block:
        user_parts.append(
            "<column_values>\n" + col_vals_block + "\n</column_values>"
        )

    # Resolved entities
    entities_block = _format_resolved_entities(resolved_entities)
    if entities_block:
        user_parts.append(
            "<resolved_entities>\n" + entities_block + "\n</resolved_entities>"
        )

    # Previous SQL context for follow-ups
    if previous_sql:
        prev_block = ""
        if previous_user_summary:
            prev_block += f"Previous question: {previous_user_summary}\n"
        prev_block += f"Previous SQL:\n{previous_sql}"
        user_parts.append(
            "<previous_context>\n" + prev_block + "\n</previous_context>"
        )

    # Assumption
    if assumption:
        user_parts.append(f"<assumption>{assumption}</assumption>")

    # User query (always last, wrapped in XML tags)
    summary = user_summary or query
    user_parts.append(f"<query>{summary}</query>")

    user_message = "\n\n".join(user_parts)

    return system_prompt, user_message


# ─── 3. Error Recovery Builder ───────────────────────────────────

def build_error_recovery_prompt(
    query: str,
    failed_sql: str,
    error_message: str,
    ddl_context: str,
    column_values: dict = None,
    attempt_number: int = 1,
) -> tuple:
    """Build the error recovery prompt (Step 3 — limited tools).

    Args:
        query: The original user question for context.
        failed_sql: The SQL that was executed and failed.
        error_message: The database error message.
        ddl_context: DDL context for the relevant tables.
        column_values: Pre-retrieved column values for verification.
        attempt_number: Which retry attempt this is (1, 2, ...).

    Returns:
        Tuple of (system_prompt, user_message).
    """
    system_prompt = ERROR_RECOVERY_SYSTEM_PROMPT.replace(
        "{current_date}", date.today().isoformat()
    ).replace(
        "{db_type}", "POSTGRESQL"
    )

    # Build user message
    user_parts = []

    user_parts.append(
        f"FAILED SQL (attempt {attempt_number}):\n{failed_sql or '(no SQL provided)'}"
    )

    user_parts.append(
        f"ERROR MESSAGE:\n{error_message or '(no error message provided)'}"
    )

    if attempt_number > 1:
        user_parts.append(
            f"This is retry attempt {attempt_number}. "
            "Generate a FUNDAMENTALLY DIFFERENT query — do not repeat previous failures."
        )

    if ddl_context:
        user_parts.append(f"TABLE DDL:\n{ddl_context}")

    col_vals_block = _format_column_values(column_values)
    if col_vals_block:
        user_parts.append(col_vals_block)

    user_parts.append(f"ORIGINAL QUESTION: {query}")

    user_message = "\n\n".join(user_parts)

    return system_prompt, user_message


# ─── 3b. Column Correction Builder ──────────────────────────────

COLUMN_CORRECTION_SYSTEM_PROMPT = """You are a SQL correction agent. The previous SQL used non-existent columns. Rewrite using ONLY the valid columns listed below.

Return ONLY the corrected SQL. No markdown, no explanation.

Current date: {current_date}
Database type: {db_type}
"""


def build_column_correction_prompt(
    query: str,
    failed_sql: str,
    invalid_columns: list,
    column_inventory: dict,
) -> tuple:
    """Build a targeted correction prompt when column validation fails pre-execution.

    Args:
        query: Original user question.
        failed_sql: SQL with invalid column references.
        invalid_columns: List of dicts with alias/column/table info.
        column_inventory: {table: [valid_columns]} for referenced tables.

    Returns:
        Tuple of (system_prompt, user_message).
    """
    system_prompt = COLUMN_CORRECTION_SYSTEM_PROMPT.replace(
        "{current_date}", date.today().isoformat()
    ).replace("{db_type}", "POSTGRESQL")

    user_parts = []

    user_parts.append(f"<failed_sql>\n{failed_sql}\n</failed_sql>")

    invalid_list = ", ".join(
        f"{inv['alias']}.{inv['column']}" for inv in invalid_columns
    )
    user_parts.append(
        f"<invalid_columns>{invalid_list}</invalid_columns>"
    )

    col_lines = []
    for table, cols in column_inventory.items():
        col_lines.append(f"  {table}: {', '.join(cols)}")
    user_parts.append(
        "<valid_columns>\n" + "\n".join(col_lines) + "\n</valid_columns>"
    )

    user_parts.append(f"<query>{query}</query>")

    return system_prompt, "\n\n".join(user_parts)


# ─── 4. Tool Fallback Builder ────────────────────────────────────

def build_tool_fallback_prompt(
    query: str,
    module: str = None,
    resolved_entities: dict = None,
    assumption: str = None,
) -> tuple:
    """Build the tool fallback prompt (Step 4 — when schema retrieval found nothing).

    Args:
        query: The user's natural language question.
        module: Module hint from intent detection (may be unreliable here).
        resolved_entities: Dict of resolved entity values.
        assumption: Assumption from intent detection.

    Returns:
        Tuple of (system_prompt, user_message).
    """
    system_prompt = TOOL_FALLBACK_SYSTEM_PROMPT.replace(
        "{current_date}", date.today().isoformat()
    )

    # Build user message
    user_parts = []

    if module:
        user_parts.append(f"MODULE HINT: {module}")

    entities_block = _format_resolved_entities(resolved_entities)
    if entities_block:
        user_parts.append(entities_block)

    if assumption:
        user_parts.append(f"ASSUMPTION: {assumption}")

    user_parts.append(f"USER QUESTION: {query}")

    user_message = "\n\n".join(user_parts)

    return system_prompt, user_message


# =====================================================
# 5. DASHBOARD INSIGHT PLANNER — Broad query → multi-insight specs
# =====================================================

DASHBOARD_INSIGHT_PLANNER_PROMPT = """You are a data analytics dashboard planner. Given a broad user query about a module, decompose it into 3-4 complementary insight panels that together form a rich dashboard view.

RULES:
1. Each insight must show a DIFFERENT metric or dimension — never the same data reformatted.
2. The FIRST insight should always be a high-level KPI (total count, sum, average, etc.).
3. Remaining insights should cover a MIX of these perspectives:
   - Distribution/breakdown (e.g., count by category → pie or bar chart)
   - Trend/time-series (e.g., monthly totals → line chart)
   - Top-N ranking (e.g., top 10 agents by premium → bar chart or table)
4. SQL rules:
   - CRITICAL: Every query MUST contain the literal text {{SCOPE_FILTER}} in its WHERE clause. Write it exactly as: WHERE {{SCOPE_FILTER}} or WHERE {{SCOPE_FILTER}} AND ... — NEVER use empty braces {{}}, NEVER omit it.
   - Use the EXACT table names from the schema provided below. Do NOT invent table names.
   - Use schema-qualified table names (e.g., wpo.table_name) with short aliases (t, t2).
   - Qualify ALL column references with aliases.
   - Keep queries simple — single table preferred, JOINs only when necessary.
   - Add LIMIT 50 to every query.
   - Use PostgreSQL syntax.
5. Return ONLY a JSON array of 3-4 objects. No markdown, no explanation, no code fences.

Each object in the array:
{{
  "title": "Short panel title",
  "description": "One sentence explaining what this insight shows",
  "sql_template": "SELECT ... FROM ... WHERE {{SCOPE_FILTER}} AND ... LIMIT 50",
  "format_hint": "kpi" | "bar_chart" | "pie_chart" | "line_chart" | "table",
  "metric_columns": ["column_name_used_for_values"],
  "dimension_columns": ["column_name_used_for_grouping"]
}}

AVAILABLE SCHEMA:
{schema_context}

MODULE: {module}

FEW-SHOT EXAMPLES:

Example 1 — "give me insights for agents"
[
  {{
    "title": "Total Active Agents",
    "description": "Overall count of active agents in the system",
    "sql_template": "SELECT COUNT(*) AS total_agents FROM wpo.lup_agents t WHERE {{SCOPE_FILTER}} AND t.agent_status = 'Active' LIMIT 50",
    "format_hint": "kpi",
    "metric_columns": ["total_agents"],
    "dimension_columns": []
  }},
  {{
    "title": "Agents by State",
    "description": "Geographic distribution of agents across states",
    "sql_template": "SELECT t.agent_state, COUNT(*) AS agent_count FROM wpo.lup_agents t WHERE {{SCOPE_FILTER}} AND t.agent_status = 'Active' GROUP BY t.agent_state ORDER BY agent_count DESC LIMIT 50",
    "format_hint": "bar_chart",
    "metric_columns": ["agent_count"],
    "dimension_columns": ["agent_state"]
  }},
  {{
    "title": "Agent Appointments by Type",
    "description": "Breakdown of agent appointment types",
    "sql_template": "SELECT t.appointment_type, COUNT(*) AS type_count FROM wpo.lup_agents t WHERE {{SCOPE_FILTER}} GROUP BY t.appointment_type ORDER BY type_count DESC LIMIT 50",
    "format_hint": "pie_chart",
    "metric_columns": ["type_count"],
    "dimension_columns": ["appointment_type"]
  }},
  {{
    "title": "Top 10 Agents by Commission",
    "description": "Highest-earning agents ranked by total commission",
    "sql_template": "SELECT t.agent_name, SUM(COALESCE(NULLIF(t2.payment::TEXT, '')::NUMERIC, 0)) AS total_commission FROM wpo.lup_agents t LEFT JOIN wpo.commission_totals t2 ON t.npn = t2.npn WHERE {{SCOPE_FILTER}} GROUP BY t.agent_name ORDER BY total_commission DESC LIMIT 10",
    "format_hint": "table",
    "metric_columns": ["total_commission"],
    "dimension_columns": ["agent_name"]
  }}
]

Example 2 — "show me commission overview"
[
  {{
    "title": "Total Commissions",
    "description": "Sum of all commission payments",
    "sql_template": "SELECT SUM(COALESCE(NULLIF(t.payment::TEXT, '')::NUMERIC, 0)) AS total_commissions FROM wpo.commission_totals t WHERE {{SCOPE_FILTER}} LIMIT 50",
    "format_hint": "kpi",
    "metric_columns": ["total_commissions"],
    "dimension_columns": []
  }},
  {{
    "title": "Commissions by Carrier",
    "description": "Commission distribution across insurance carriers",
    "sql_template": "SELECT t.carrier_name, SUM(COALESCE(NULLIF(t.payment::TEXT, '')::NUMERIC, 0)) AS carrier_total FROM wpo.commission_totals t WHERE {{SCOPE_FILTER}} GROUP BY t.carrier_name ORDER BY carrier_total DESC LIMIT 50",
    "format_hint": "bar_chart",
    "metric_columns": ["carrier_total"],
    "dimension_columns": ["carrier_name"]
  }},
  {{
    "title": "Monthly Commission Trend",
    "description": "Commission payments over time by month",
    "sql_template": "SELECT DATE_TRUNC('month', t.coverage_month) AS month, SUM(COALESCE(NULLIF(t.payment::TEXT, '')::NUMERIC, 0)) AS monthly_total FROM wpo.commission_totals t WHERE {{SCOPE_FILTER}} GROUP BY month ORDER BY month LIMIT 50",
    "format_hint": "line_chart",
    "metric_columns": ["monthly_total"],
    "dimension_columns": ["month"]
  }}
]

Example 3 — "insights on members"
[
  {{
    "title": "Total Members",
    "description": "Count of all members in the system",
    "sql_template": "SELECT COUNT(*) AS total_members FROM wpo.lup_members t WHERE {{SCOPE_FILTER}} LIMIT 50",
    "format_hint": "kpi",
    "metric_columns": ["total_members"],
    "dimension_columns": []
  }},
  {{
    "title": "Members by Plan Type",
    "description": "Distribution of members across plan types",
    "sql_template": "SELECT t.plan_type, COUNT(*) AS member_count FROM wpo.lup_members t WHERE {{SCOPE_FILTER}} GROUP BY t.plan_type ORDER BY member_count DESC LIMIT 50",
    "format_hint": "pie_chart",
    "metric_columns": ["member_count"],
    "dimension_columns": ["plan_type"]
  }},
  {{
    "title": "Monthly Member Enrollment Trend",
    "description": "New member enrollments over time",
    "sql_template": "SELECT DATE_TRUNC('month', t.effective_date::DATE) AS month, COUNT(*) AS new_members FROM wpo.lup_members t WHERE {{SCOPE_FILTER}} GROUP BY month ORDER BY month LIMIT 50",
    "format_hint": "line_chart",
    "metric_columns": ["new_members"],
    "dimension_columns": ["month"]
  }}
]

Now plan the dashboard for the following query:
USER QUERY: {user_query}
"""


def build_dashboard_planner_prompt(
    module: str,
    user_query: str,
    schema_context: str,
) -> tuple:
    """Build the dashboard insight planner prompt.

    Args:
        module: The module name (e.g., "agents", "commission_totals").
        user_query: The user's broad natural language query.
        schema_context: Pre-formatted schema/DDL for available tables and key columns.

    Returns:
        Tuple of (system_prompt, user_message) ready for LLM call.
    """
    system_prompt = (
        "You are a data analytics dashboard planner. "
        "Return ONLY a valid JSON array. No markdown, no code fences, no explanation."
    )

    user_message = DASHBOARD_INSIGHT_PLANNER_PROMPT.replace(
        "{schema_context}", schema_context or "(no schema provided)"
    ).replace(
        "{module}", module or "unknown"
    ).replace(
        "{user_query}", user_query
    )

    return system_prompt, user_message
