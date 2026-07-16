"""
JAI Tool Handlers - Implementations for the 7 tool-augmented pipeline tools.

Each handler is invoked by the dispatcher in ``tools.py``.  Handlers are
intentionally defensive: every database call is wrapped in try/except so a
single tool failure never crashes the pipeline.

Public API (all return ``dict``):
- handle_search_tables(query, top_k, db_session, join_graph)
- handle_get_table_schema(table_name, db_session)
- handle_get_column_values(table_name, column_name, limit, db_session)
- handle_get_foreign_keys(table_name, db_session, join_graph)
- handle_get_join_examples(table1, table2, db_session, join_graph)
- handle_get_similar_queries(query, top_k, db_session)
- handle_validate_sql(sql)
"""

import json
import logging
import math
import re
from typing import Dict, List, Optional

from sqlalchemy import text
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

# Lazy-imported at first use to avoid circular imports at module load time.
_table_domains_cache = None
_cross_domain_joins_cache = None


def _get_table_domains() -> Dict:
    """Lazy import TABLE_DOMAINS from table_catalog."""
    global _table_domains_cache
    if _table_domains_cache is None:
        from app.utils.jay.table_catalog import TABLE_DOMAINS
        _table_domains_cache = TABLE_DOMAINS
    return _table_domains_cache


def _get_cross_domain_joins() -> Dict:
    """Lazy import CROSS_DOMAIN_JOINS from table_catalog."""
    global _cross_domain_joins_cache
    if _cross_domain_joins_cache is None:
        from app.utils.jay.table_catalog import CROSS_DOMAIN_JOINS
        _cross_domain_joins_cache = CROSS_DOMAIN_JOINS
    return _cross_domain_joins_cache


def _sanitize_identifier(name: str) -> str:
    """Strip everything except alphanumeric chars and underscores.

    Prevents SQL injection in places where identifiers are interpolated
    directly into query strings (table/column names).

    Returns:
        Sanitized identifier, or empty string if nothing valid remains.
    """
    return re.sub(r"[^a-zA-Z0-9_]", "", name)


def _short_name(table_name: str) -> str:
    """Extract the short table name from a possibly schema-qualified name.

    ``"wpo.lup_agents"`` -> ``"lup_agents"``
    ``"lup_agents"`` -> ``"lup_agents"``
    """
    return table_name.split(".")[-1] if "." in table_name else table_name


def _cosine_similarity(a: List[float], b: List[float]) -> float:
    """Compute cosine similarity between two vectors."""
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


def _parse_embedding(raw) -> Optional[List[float]]:
    """Parse embedding from TEXT column (JSON array string)."""
    if raw is None:
        return None
    if isinstance(raw, list):
        return raw
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return None


# =============================================================================
# 1. handle_search_tables
# =============================================================================


def handle_search_tables(
    query: str,
    top_k: int,
    db_session: Session,
    join_graph=None,
) -> dict:
    """Search for database tables relevant to *query*.

    Strategy:
    1. Try vector search against ``wpo.jay_table_metadata`` (embedding cosine
       similarity).
    2. If that table is empty or the search fails, fall back to keyword
       matching against the static ``TABLE_DOMAINS`` dictionary.

    Returns:
        ``{"tables": [{"table_name", "description", "tags", "columns_preview"}]}``
    """
    top_k = min(max(top_k, 1), 20)  # clamp to [1, 20]

    # -- Attempt 1: embedding similarity against jay_table_metadata -----------
    try:
        from app.utils.jay.llm_client import embed_texts

        embeddings = embed_texts([query])
        if embeddings:
            query_vec = embeddings[0]
            # Load all table metadata (small dataset — ~10 rows)
            result = db_session.execute(
                text(
                    "SELECT table_name, description, tags, columns, embedding "
                    "FROM wpo.jay_table_metadata "
                    "WHERE embedding IS NOT NULL"
                ),
            )
            rows = result.fetchall()

            if rows:
                # Compute cosine similarity in Python
                scored = []
                for row in rows:
                    stored_emb = _parse_embedding(row[4])
                    if stored_emb is None:
                        continue
                    sim = _cosine_similarity(query_vec, stored_emb)
                    scored.append((sim, row))
                scored.sort(key=lambda x: x[0], reverse=True)

                tables = []
                for sim, row in scored[:top_k]:
                    columns_json = row[3] if row[3] else []
                    if isinstance(columns_json, str):
                        try:
                            columns_json = json.loads(columns_json)
                        except (json.JSONDecodeError, TypeError):
                            columns_json = []
                    if isinstance(columns_json, list):
                        col_preview = [
                            c.get("name", c) if isinstance(c, dict) else str(c)
                            for c in columns_json[:10]
                        ]
                    else:
                        col_preview = []

                    tables.append({
                        "table_name": row[0],
                        "description": row[1] or "",
                        "tags": list(row[2]) if row[2] else [],
                        "columns_preview": col_preview,
                        "similarity": round(sim, 4),
                    })
                if tables:
                    logger.debug(
                        "search_tables: embedding search returned %d results for '%s'",
                        len(tables), query,
                    )
                    return {"tables": tables}

    except Exception:
        logger.debug(
            "search_tables: embedding search unavailable, falling back to keyword match",
            exc_info=True,
        )

    # -- Attempt 2: keyword fallback against TABLE_DOMAINS -------------------
    return _search_tables_keyword_fallback(query, top_k)


def _search_tables_keyword_fallback(query: str, top_k: int) -> dict:
    """Keyword-match tables from the static TABLE_DOMAINS catalog."""
    TABLE_DOMAINS = _get_table_domains()
    query_lower = query.lower()
    query_words = set(query_lower.split())

    scored: List[tuple] = []  # (score, table_info_dict)

    for domain_name, domain_cfg in TABLE_DOMAINS.items():
        domain_desc = (domain_cfg.get("description") or "").lower()
        for tbl_cfg in domain_cfg.get("tables", []):
            table_name = tbl_cfg.get("name", "")
            tbl_desc = (tbl_cfg.get("description") or "").lower()
            ddl = (tbl_cfg.get("ddl") or "").lower()

            # Simple keyword scoring
            score = 0
            for word in query_words:
                if len(word) < 3:
                    continue
                if word in table_name.lower():
                    score += 3
                if word in tbl_desc:
                    score += 2
                if word in domain_desc:
                    score += 1
                if word in ddl:
                    score += 1

            if score > 0:
                # Extract column names from DDL for preview
                col_names = re.findall(
                    r"^\s+(\w+)\s+(?:VARCHAR|TEXT|INTEGER|INT|FLOAT|BOOLEAN|UUID|TIMESTAMP|DATE|NUMERIC|SERIAL|BIGINT|SMALLINT|JSONB|REAL)",
                    tbl_cfg.get("ddl", ""),
                    re.MULTILINE | re.IGNORECASE,
                )
                scored.append((
                    score,
                    {
                        "table_name": table_name,
                        "description": tbl_cfg.get("description", ""),
                        "tags": [domain_name],
                        "columns_preview": col_names[:10],
                    },
                ))

    # Sort descending by score, take top_k
    scored.sort(key=lambda x: x[0], reverse=True)
    tables = [info for _, info in scored[:top_k]]

    logger.debug(
        "search_tables: keyword fallback returned %d results for '%s'",
        len(tables), query,
    )
    return {"tables": tables}


# =============================================================================
# 2. handle_get_table_schema
# =============================================================================


def handle_get_table_schema(table_name: str, db_session: Session) -> dict:
    """Get the full DDL and column details for a specific table.

    Priority:
    1. Look up pre-written DDL in ``TABLE_DOMAINS``.
    2. If not found, query ``information_schema.columns`` dynamically and
       include column comments from ``pg_description``.

    Returns:
        ``{"table_name", "ddl", "join_context"|"columns"}``
    """
    short = _short_name(table_name)

    # -- Attempt 1: static catalog -------------------------------------------
    TABLE_DOMAINS = _get_table_domains()
    for domain_name, domain_cfg in TABLE_DOMAINS.items():
        for tbl_cfg in domain_cfg.get("tables", []):
            catalog_short = _short_name(tbl_cfg.get("name", ""))
            if catalog_short == short or tbl_cfg.get("name") == table_name:
                # Build join context for this table
                join_ctx = _get_join_context_for_table(tbl_cfg["name"])
                return {
                    "table_name": tbl_cfg["name"],
                    "ddl": tbl_cfg.get("ddl", ""),
                    "join_context": join_ctx,
                    "source": "catalog",
                }

    # -- Attempt 2: dynamic from information_schema --------------------------
    safe_table = _sanitize_identifier(short)
    if not safe_table:
        return {"table_name": table_name, "error": "Invalid table name"}

    try:
        result = db_session.execute(
            text("""
                SELECT
                    c.column_name,
                    c.data_type,
                    c.is_nullable,
                    d.description AS column_comment
                FROM information_schema.columns c
                LEFT JOIN pg_catalog.pg_statio_all_tables st
                    ON st.schemaname = c.table_schema
                    AND st.relname = c.table_name
                LEFT JOIN pg_catalog.pg_description d
                    ON d.objoid = st.relid
                    AND d.objsubid = c.ordinal_position
                WHERE c.table_schema = :schema
                    AND c.table_name = :table
                ORDER BY c.ordinal_position
            """),
            {"schema": "wpo", "table": safe_table},
        )
        rows = result.fetchall()

        if not rows:
            return {
                "table_name": table_name,
                "error": f"Table '{table_name}' not found in wpo schema",
            }

        # Build DDL string
        ddl_lines = [f"CREATE TABLE wpo.{safe_table} ("]
        columns = []
        col_lines = []
        for row in rows:
            col_name, data_type, is_nullable, comment = row[0], row[1], row[2], row[3]
            nullable = "" if is_nullable == "YES" else " NOT NULL"
            comment_str = f"  -- {comment}" if comment else ""
            col_lines.append(f"    {col_name} {data_type.upper()}{nullable}{comment_str}")
            columns.append({
                "name": col_name,
                "type": data_type,
                "comment": comment or "",
            })
        # Join column lines with commas (no trailing comma on last)
        ddl_lines.append(",\n".join(col_lines))
        ddl_lines.append(");")

        return {
            "table_name": f"wpo.{safe_table}",
            "ddl": "\n".join(ddl_lines),
            "columns": columns,
            "source": "information_schema",
        }

    except Exception as exc:
        logger.exception("get_table_schema: failed for '%s'", table_name)
        return {"table_name": table_name, "error": str(exc)}


def handle_get_table_schemas_bulk(
    table_names: List[str],
    db_session: Session,
) -> dict:
    """Get DDL and column details for multiple tables in one call.

    Calls handle_get_table_schema for each table and returns combined results.
    Limits to 10 tables to prevent abuse.

    Returns:
        ``{"tables": {table_name: {ddl, columns, ...}}}``
    """
    table_names = table_names[:10]  # cap at 10
    results = {}
    for name in table_names:
        results[name] = handle_get_table_schema(name, db_session)
    logger.debug("get_table_schemas: returned %d schemas", len(results))
    return {"tables": results}


def _get_join_context_for_table(full_table_name: str) -> List[str]:
    """Collect CROSS_DOMAIN_JOINS entries relevant to a table."""
    CROSS_DOMAIN_JOINS = _get_cross_domain_joins()
    short = _short_name(full_table_name)
    results = []

    for join_label, join_cfg in CROSS_DOMAIN_JOINS.items():
        left = join_cfg.get("left", "")
        right = join_cfg.get("right", "")
        if short in left or short in right or full_table_name in left or full_table_name in right:
            join_type = join_cfg.get("type", "LEFT JOIN")
            on_clause = join_cfg.get("on", "")
            note = join_cfg.get("note", "")
            entry = f"{join_type}: {on_clause}"
            if note:
                entry += f" (NOTE: {note})"
            results.append(entry)

    return results


# =============================================================================
# 3. handle_get_column_values
# =============================================================================


def handle_get_column_values(
    table_name: str,
    column_name: str,
    limit: int,
    db_session: Session,
) -> dict:
    """Get distinct values for a specific column.

    Priority:
    1. Check ``wpo.jay_column_embeddings`` for cached ``sample_values``.
    2. If not available, run a live ``SELECT DISTINCT`` query.

    IMPORTANT: table_name and column_name are sanitized to prevent injection.

    Returns:
        ``{"table_name", "column_name", "values", "source"}``
    """
    safe_table = _sanitize_identifier(_short_name(table_name))
    safe_column = _sanitize_identifier(column_name)
    limit = min(max(limit, 1), 100)  # clamp to [1, 100]

    if not safe_table or not safe_column:
        return {
            "table_name": table_name,
            "column_name": column_name,
            "values": [],
            "error": "Invalid table or column name",
        }

    # -- Attempt 1: cached sample_values from jay_column_embeddings ----------
    try:
        result = db_session.execute(
            text(
                "SELECT sample_values, is_categorical "
                "FROM wpo.jay_column_embeddings "
                "WHERE table_name = :table AND column_name = :column "
                "LIMIT 1"
            ),
            {"table": safe_table, "column": safe_column},
        )
        row = result.fetchone()
        if row and row[0]:
            sample_values = row[0]
            if isinstance(sample_values, str):
                try:
                    sample_values = json.loads(sample_values)
                except (json.JSONDecodeError, TypeError):
                    sample_values = []
            if isinstance(sample_values, list) and len(sample_values) > 0:
                return {
                    "table_name": safe_table,
                    "column_name": safe_column,
                    "values": sample_values[:limit],
                    "is_categorical": bool(row[1]) if row[1] is not None else None,
                    "source": "cached",
                }
    except Exception:
        logger.debug(
            "get_column_values: cached lookup failed for %s.%s, trying live query",
            safe_table, safe_column, exc_info=True,
        )

    # -- Attempt 2: live DISTINCT query --------------------------------------
    try:
        # Build query with sanitized identifiers (NOT parameterized -- identifiers
        # cannot be parameterized in SQL, but we have sanitized them above).
        query = (
            f"SELECT DISTINCT {safe_column} "
            f"FROM wpo.{safe_table} "
            f"WHERE {safe_column} IS NOT NULL "
            f"ORDER BY {safe_column} "
            f"LIMIT :lim"
        )
        result = db_session.execute(text(query), {"lim": limit})
        rows = result.fetchall()
        values = [str(r[0]) for r in rows]

        return {
            "table_name": safe_table,
            "column_name": safe_column,
            "values": values,
            "source": "live",
        }

    except Exception as exc:
        logger.exception(
            "get_column_values: live query failed for %s.%s", safe_table, safe_column,
        )
        return {
            "table_name": safe_table,
            "column_name": safe_column,
            "values": [],
            "error": str(exc),
        }


def handle_get_column_values_bulk(
    columns: List[dict],
    limit: int,
    db_session: Session,
) -> dict:
    """Get distinct values for multiple columns in one call.

    Each entry in columns is {table_name, column_name}.
    Limits to 10 columns to prevent abuse.

    Returns:
        ``{"results": [{table_name, column_name, values, source}, ...]}``
    """
    columns = columns[:10]  # cap at 10
    limit = min(max(limit, 1), 100)
    results = []
    for col_spec in columns:
        table_name = col_spec.get("table_name", "")
        column_name = col_spec.get("column_name", "")
        if table_name and column_name:
            result = handle_get_column_values(table_name, column_name, limit, db_session)
            results.append(result)
    logger.debug("get_column_values_bulk: returned %d column value sets", len(results))
    return {"results": results}


# =============================================================================
# 4. handle_get_foreign_keys
# =============================================================================


def handle_get_foreign_keys(
    table_name: str,
    db_session: Session,
    join_graph=None,
) -> dict:
    """Get foreign key relationships for a table.

    Sources:
    1. ``join_graph.neighbors(table_name)`` if a JoinGraph is provided.
    2. ``CROSS_DOMAIN_JOINS`` static catalog.

    Returns:
        ``{"table_name", "foreign_keys": [{"from_table", "from_column",
          "to_table", "to_column", "confidence", "is_explicit"}]}``
    """
    short = _short_name(table_name)
    fk_entries: List[dict] = []
    seen_keys: set = set()  # dedup key: (from_table, from_col, to_table, to_col)

    # -- Source 1: JoinGraph -------------------------------------------------
    if join_graph is not None:
        try:
            neighbors = join_graph.neighbors(short)
            for neighbor_table, edge in neighbors:
                key = (edge.source_table, edge.source_column,
                       edge.target_table, edge.target_column)
                if key not in seen_keys:
                    seen_keys.add(key)
                    fk_entries.append({
                        "from_table": edge.source_table,
                        "from_column": edge.source_column,
                        "to_table": edge.target_table,
                        "to_column": edge.target_column,
                        "confidence": edge.confidence.value if hasattr(edge.confidence, "value") else str(edge.confidence),
                        "is_explicit": edge.is_explicit,
                    })
        except Exception:
            logger.debug(
                "get_foreign_keys: join_graph lookup failed for '%s'",
                short, exc_info=True,
            )

    # -- Source 2: CROSS_DOMAIN_JOINS ----------------------------------------
    CROSS_DOMAIN_JOINS = _get_cross_domain_joins()
    for join_label, join_cfg in CROSS_DOMAIN_JOINS.items():
        left = join_cfg.get("left", "")
        right = join_cfg.get("right", "")
        on_clause = join_cfg.get("on", "")

        left_short = _short_name(left)
        right_short = _short_name(right)

        # Check if either side matches (handle wildcard entries like "*.company_id")
        if short not in (left_short, right_short) and not left.startswith("*"):
            continue

        if left.startswith("*") and short not in on_clause.lower():
            continue

        # Parse the ON clause to extract column pairs
        # Typical format: "table_a.col_x = table_b.col_y"
        on_parts = on_clause.split("=")
        if len(on_parts) == 2:
            left_part = on_parts[0].strip()
            right_part = on_parts[1].strip()
            # Handle compound ON conditions (AND separated)
            left_col = left_part.split(".")[-1].strip() if "." in left_part else left_part.strip()
            right_col = right_part.split(".")[-1].strip() if "." in right_part else right_part.strip()

            key = (left_short, left_col, right_short, right_col)
            if key not in seen_keys:
                seen_keys.add(key)
                fk_entries.append({
                    "from_table": left,
                    "from_column": left_col,
                    "to_table": right,
                    "to_column": right_col,
                    "confidence": "high",
                    "is_explicit": True,
                    "join_type": join_cfg.get("type", "LEFT JOIN"),
                    "source": "cross_domain_joins",
                })

    logger.debug(
        "get_foreign_keys: found %d FK entries for '%s'",
        len(fk_entries), short,
    )
    return {"table_name": short, "foreign_keys": fk_entries}


# =============================================================================
# 5. handle_get_join_examples
# =============================================================================


def handle_get_join_examples(
    table1: str,
    table2: str,
    db_session: Session,
    join_graph=None,
) -> dict:
    """Get example JOIN SQL between two tables.

    Sources:
    1. ``CROSS_DOMAIN_JOINS`` static catalog.
    2. ``wpo.jay_table_metadata.join_examples`` for both tables.
    3. If no direct join found, suggest intermediate tables via the join graph.

    Returns:
        ``{"table1", "table2", "examples": [{"sql", "type", "on_condition"}]}``
    """
    short1 = _short_name(table1)
    short2 = _short_name(table2)
    examples: List[dict] = []

    # -- Source 1: CROSS_DOMAIN_JOINS ----------------------------------------
    CROSS_DOMAIN_JOINS = _get_cross_domain_joins()
    for join_label, join_cfg in CROSS_DOMAIN_JOINS.items():
        left = join_cfg.get("left", "")
        right = join_cfg.get("right", "")
        left_short = _short_name(left)
        right_short = _short_name(right)

        # Match in either direction
        match = (
            (short1 in (left_short, right_short) and short2 in (left_short, right_short))
            or (short1 == left_short and short2 == right_short)
            or (short2 == left_short and short1 == right_short)
        )
        if match:
            join_type = join_cfg.get("type", "LEFT JOIN")
            on_clause = join_cfg.get("on", "")
            note = join_cfg.get("note", "")

            sql_example = f"{join_type} {right} ON {on_clause}"
            examples.append({
                "sql": sql_example,
                "type": join_type,
                "on_condition": on_clause,
                "note": note,
                "source": "cross_domain_joins",
            })

    # -- Source 2: jay_table_metadata.join_examples --------------------------
    try:
        result = db_session.execute(
            text(
                "SELECT table_name, join_examples "
                "FROM wpo.jay_table_metadata "
                "WHERE table_name IN (:t1, :t2) "
                "  AND join_examples IS NOT NULL "
                "  AND join_examples != '[]'::jsonb"
            ),
            {"t1": short1, "t2": short2},
        )
        rows = result.fetchall()

        for row in rows:
            tbl_name = row[0]
            join_ex = row[1]
            if isinstance(join_ex, str):
                try:
                    join_ex = json.loads(join_ex)
                except (json.JSONDecodeError, TypeError):
                    join_ex = []
            if isinstance(join_ex, list):
                for ex in join_ex:
                    # Check if the join example references the other table
                    other = short2 if tbl_name == short1 else short1
                    ex_str = json.dumps(ex).lower()
                    if other.lower() in ex_str:
                        examples.append({
                            "sql": ex.get("sql", str(ex)),
                            "type": ex.get("type", "LEFT JOIN"),
                            "on_condition": ex.get("on_condition", ex.get("on", "")),
                            "source": "table_metadata",
                        })
    except Exception:
        logger.debug(
            "get_join_examples: jay_table_metadata lookup failed for %s/%s",
            short1, short2, exc_info=True,
        )

    # -- Source 3: suggest intermediates via join graph -----------------------
    if not examples and join_graph is not None:
        try:
            from app.utils.jay.fk_inference import expand_tables_via_joins
            expansion = expand_tables_via_joins(
                seed_tables={short1, short2},
                join_graph=join_graph,
                max_hops=2,
            )
            if expansion.bridge_tables:
                for bridge in sorted(expansion.bridge_tables):
                    examples.append({
                        "sql": f"-- No direct join. Consider bridging through: {bridge}",
                        "type": "indirect",
                        "on_condition": "",
                        "bridge_table": bridge,
                        "source": "join_graph_expansion",
                    })
            if expansion.join_paths:
                for jp in expansion.join_paths:
                    examples.append({
                        "sql": (
                            f"LEFT JOIN wpo.{jp.to_table} "
                            f"ON {jp.from_table}.{jp.from_col} = {jp.to_table}.{jp.to_col}"
                        ),
                        "type": "LEFT JOIN",
                        "on_condition": f"{jp.from_table}.{jp.from_col} = {jp.to_table}.{jp.to_col}",
                        "source": "join_graph_expansion",
                    })
        except Exception:
            logger.debug(
                "get_join_examples: join_graph expansion failed for %s/%s",
                short1, short2, exc_info=True,
            )

    logger.debug(
        "get_join_examples: found %d examples for %s <-> %s",
        len(examples), short1, short2,
    )
    return {"table1": short1, "table2": short2, "examples": examples}


# =============================================================================
# 6. handle_get_similar_queries
# =============================================================================


def handle_get_similar_queries(
    query: str,
    top_k: int,
    db_session: Session,
) -> dict:
    """Search for similar past queries in the cache.

    Strategy:
    1. Vector search ``wpo.jay_query_cache`` with a 0.75 similarity threshold.
    2. If that table is empty or yields no results, search
       ``wpo.jay_knowledge_embeddings`` where ``category='learned'``.

    Returns:
        ``{"similar_queries": [{"query", "sql", "module", "similarity"}]}``
    """
    top_k = min(max(top_k, 1), 10)  # clamp to [1, 10]
    similarity_threshold = 0.75
    query_vec = None  # computed once, reused across both attempts

    # -- Generate embedding once ---------------------------------------------
    try:
        from app.utils.jay.llm_client import embed_texts

        embeddings = embed_texts([query])
        if embeddings:
            query_vec = embeddings[0]
    except Exception:
        logger.debug(
            "get_similar_queries: embedding generation failed", exc_info=True,
        )

    if query_vec is None:
        return {"similar_queries": []}

    # -- Attempt 1: jay_query_cache (Python-side cosine similarity) ----------
    try:
        result = db_session.execute(
            text(
                "SELECT query_text, sql_text, module, embedding "
                "FROM wpo.jay_query_cache "
                "WHERE embedding IS NOT NULL"
            ),
        )
        rows = result.fetchall()

        scored = []
        for row in rows:
            stored_emb = _parse_embedding(row[3])
            if stored_emb is None:
                continue
            sim = _cosine_similarity(query_vec, stored_emb)
            if sim >= similarity_threshold:
                scored.append((sim, row[0], row[1], row[2]))

        scored.sort(key=lambda x: x[0], reverse=True)

        matches = [
            {
                "query": q,
                "sql": s,
                "module": m or "",
                "similarity": round(sim, 4),
                "source": "query_cache",
            }
            for sim, q, s, m in scored[:top_k]
        ]

        if matches:
            logger.debug(
                "get_similar_queries: found %d matches in query_cache for '%s'",
                len(matches), query,
            )
            return {"similar_queries": matches}

    except Exception:
        logger.debug(
            "get_similar_queries: query_cache search failed, trying knowledge_embeddings",
            exc_info=True,
        )

    # -- Attempt 2: jay_knowledge_embeddings (category='learned') ------------
    try:
        result = db_session.execute(
            text(
                "SELECT text, metadata, embedding "
                "FROM wpo.jay_knowledge_embeddings "
                "WHERE category = 'learned' "
                "  AND is_active = true "
                "  AND embedding IS NOT NULL"
            ),
        )
        rows = result.fetchall()

        scored = []
        for row in rows:
            stored_emb = _parse_embedding(row[2])
            if stored_emb is None:
                continue
            sim = _cosine_similarity(query_vec, stored_emb)
            if sim >= similarity_threshold:
                scored.append((sim, row[0], row[1]))

        scored.sort(key=lambda x: x[0], reverse=True)

        matches = []
        for sim, txt, meta in scored[:top_k]:
            metadata = meta
            if isinstance(metadata, str):
                try:
                    metadata = json.loads(metadata)
                except (json.JSONDecodeError, TypeError):
                    metadata = {}
            if not isinstance(metadata, dict):
                metadata = {}

            matches.append({
                "query": metadata.get("query", txt),
                "sql": metadata.get("sql", ""),
                "module": metadata.get("module", ""),
                "similarity": round(sim, 4),
                "source": "knowledge_embeddings",
            })

        logger.debug(
            "get_similar_queries: found %d matches in knowledge_embeddings for '%s'",
            len(matches), query,
        )
        return {"similar_queries": matches}

    except Exception:
        logger.debug(
            "get_similar_queries: knowledge_embeddings search also failed",
            exc_info=True,
        )
        return {"similar_queries": []}


# =============================================================================
# 7. handle_validate_sql
# =============================================================================


def handle_validate_sql(sql: str) -> dict:
    """Validate SQL syntax without executing it.

    Checks:
    1. Is it a SELECT statement?
    2. Does it have a FROM clause?
    3. Are there obvious syntax issues (unbalanced parens, missing keywords)?
    4. Are referenced tables known (from TABLE_DOMAINS or jay_table_metadata)?

    Uses ``sqlparse`` if available; otherwise falls back to regex validation.

    Returns:
        ``{"valid": bool, "issues": [...], "tables_referenced": [...]}``
    """
    issues: List[str] = []
    tables_referenced: List[str] = []

    if not sql or not sql.strip():
        return {"valid": False, "issues": ["Empty SQL string"], "tables_referenced": []}

    normalized = sql.strip()

    # -- Try sqlparse first --------------------------------------------------
    sqlparse_available = False
    try:
        import sqlparse  # noqa: F401
        sqlparse_available = True
    except ImportError:
        pass

    if sqlparse_available:
        issues, tables_referenced = _validate_with_sqlparse(normalized)
    else:
        issues, tables_referenced = _validate_with_regex(normalized)

    # -- Check tables against known tables -----------------------------------
    known_tables = _get_known_table_names()
    for table in tables_referenced:
        table_upper = table.upper()
        table_short = _short_name(table).upper()
        if table_upper not in known_tables and table_short not in known_tables:
            # Also check with wpo. prefix
            prefixed = f"WPO.{table_short}"
            if prefixed not in known_tables:
                issues.append(f"Unknown table: {table}")

    valid = len(issues) == 0
    return {
        "valid": valid,
        "issues": issues,
        "tables_referenced": tables_referenced,
    }


def _validate_with_sqlparse(sql: str) -> tuple:
    """Validate SQL using the sqlparse library.

    Returns:
        Tuple of (issues: List[str], tables_referenced: List[str]).
    """
    import sqlparse

    issues: List[str] = []
    tables_referenced: List[str] = []

    parsed = sqlparse.parse(sql)
    if not parsed:
        issues.append("Could not parse SQL")
        return issues, tables_referenced

    stmt = parsed[0]

    # Check statement type
    if stmt.get_type() != "SELECT":
        stmt_type = stmt.get_type() or "UNKNOWN"
        issues.append(f"Expected SELECT statement, got {stmt_type}")

    # Check for FROM clause
    upper_sql = sql.upper()
    if "FROM" not in upper_sql:
        issues.append("Missing FROM clause")

    # Check for unbalanced parentheses
    open_parens = sql.count("(")
    close_parens = sql.count(")")
    if open_parens != close_parens:
        issues.append(
            f"Unbalanced parentheses: {open_parens} opening vs {close_parens} closing"
        )

    # Check for unbalanced single quotes
    single_quotes = sql.count("'")
    if single_quotes % 2 != 0:
        issues.append("Unbalanced single quotes")

    # Check for multiple statements (semicolons in the middle)
    body = sql.strip().rstrip(";")
    if ";" in body:
        issues.append("Multiple statements detected (semicolons in SQL body)")

    # Extract table names
    tables_referenced = _extract_table_names(sql)

    return issues, tables_referenced


def _validate_with_regex(sql: str) -> tuple:
    """Validate SQL using basic regex when sqlparse is not available.

    Returns:
        Tuple of (issues: List[str], tables_referenced: List[str]).
    """
    issues: List[str] = []
    upper_sql = sql.upper().strip()

    # Check: is it a SELECT?
    if not upper_sql.startswith("SELECT"):
        issues.append("SQL does not start with SELECT")

    # Check: has FROM?
    if "FROM" not in upper_sql:
        issues.append("Missing FROM clause")

    # Check unbalanced parentheses
    open_parens = sql.count("(")
    close_parens = sql.count(")")
    if open_parens != close_parens:
        issues.append(
            f"Unbalanced parentheses: {open_parens} opening vs {close_parens} closing"
        )

    # Check unbalanced quotes
    single_quotes = sql.count("'")
    if single_quotes % 2 != 0:
        issues.append("Unbalanced single quotes")

    # Check for multiple statements
    body = sql.strip().rstrip(";")
    if ";" in body:
        issues.append("Multiple statements detected")

    # Check for dangerous keywords
    dangerous = ["DROP", "DELETE", "INSERT", "UPDATE", "ALTER", "CREATE", "TRUNCATE"]
    for kw in dangerous:
        if re.search(rf"\b{kw}\b", upper_sql):
            issues.append(f"Dangerous keyword detected: {kw}")

    # Extract table names
    tables_referenced = _extract_table_names(sql)

    return issues, tables_referenced


def _extract_table_names(sql: str) -> List[str]:
    """Extract table names from FROM and JOIN clauses in SQL.

    Returns:
        List of table references found in the SQL.
    """
    upper = sql.upper()
    tables = []

    # Match FROM <table> and JOIN <table> patterns
    # Handles schema-qualified names like wpo.table_name
    from_matches = re.findall(
        r"\bFROM\s+([A-Za-z0-9_\.]+)", upper,
    )
    join_matches = re.findall(
        r"\bJOIN\s+([A-Za-z0-9_\.]+)", upper,
    )

    seen = set()
    for match in from_matches + join_matches:
        name = match.strip().rstrip(",")
        if name and name not in seen:
            seen.add(name)
            tables.append(name)

    return tables


def _get_known_table_names() -> set:
    """Collect all known table names from TABLE_DOMAINS (uppercase, both short and qualified)."""
    TABLE_DOMAINS = _get_table_domains()
    names = set()
    for domain_cfg in TABLE_DOMAINS.values():
        for tbl_cfg in domain_cfg.get("tables", []):
            full_name = tbl_cfg.get("name", "")
            names.add(full_name.upper())
            names.add(_short_name(full_name).upper())
    return names
