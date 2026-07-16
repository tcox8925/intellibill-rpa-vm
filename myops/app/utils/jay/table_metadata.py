"""
Jay Table Metadata - Sync hardcoded catalog data into wpo.jay_table_metadata
and enrich wpo.jay_column_embeddings with FK and join information.

Reads from:
- table_catalog.TABLE_DOMAINS: domain configs with tables, DDL, join_keys
- table_catalog.CROSS_DOMAIN_JOINS: pre-defined cross-domain join relationships
- semantic_registry.MODULES: module definitions with sample_queries, descriptions
- fk_inference.discover_join_graph(): runtime FK discovery from DB catalogs

Public API:
- sync_table_metadata(db_session) -> dict
- sync_column_metadata(db_session) -> dict
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set, Tuple

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.utils.jay.llm_client import embed_texts
from app.utils.jay.table_catalog import CROSS_DOMAIN_JOINS, TABLE_DOMAINS
from app.utils.jay.semantic_registry import MODULES

logger = logging.getLogger(__name__)


# =============================================================================
# Internal helpers
# =============================================================================

def _extract_short_table_name(full_name: str) -> str:
    """Extract short table name from schema-qualified name.

    Args:
        full_name: e.g. "wpo.lup_agents" or "ops_sec.entity"

    Returns:
        Short name, e.g. "lup_agents" or "entity"
    """
    return full_name.split(".")[-1] if "." in full_name else full_name


def _extract_schema_from_name(full_name: str) -> str:
    """Extract schema from a schema-qualified name, defaulting to 'wpo'.

    Args:
        full_name: e.g. "wpo.lup_agents" or "ops_sec.entity"

    Returns:
        Schema name, e.g. "wpo" or "ops_sec"
    """
    if "." in full_name:
        return full_name.split(".")[0]
    return "wpo"


def _parse_columns_from_ddl(ddl: str) -> List[str]:
    """Parse column names from a CREATE TABLE DDL string.

    Extracts lines that look like column definitions (starting with
    whitespace + identifier), ignoring constraint lines and comments.

    Args:
        ddl: CREATE TABLE DDL text

    Returns:
        List of column names found in the DDL.
    """
    columns: List[str] = []
    # Match lines inside the CREATE TABLE (...) block that define columns
    # Column lines start with optional whitespace, then an identifier (not a keyword)
    col_pattern = re.compile(
        r'^\s+'             # leading whitespace
        r'(?:"([^"]+)"'    # quoted identifier like "in" or "or"
        r'|([a-zA-Z_]\w*))'  # or unquoted identifier
        r'\s+'              # space before data type
        r'(?:UUID|VARCHAR|TEXT|NVARCHAR|INT|INTEGER|BIGINT|SMALLINT|SERIAL|'
        r'NUMERIC|REAL|BOOLEAN|DATE|TIMESTAMP|FLOAT|DOUBLE|MONEY|BIT)',
        re.IGNORECASE
    )
    for line in ddl.split("\n"):
        line_stripped = line.strip()
        # Skip comments and constraint lines
        if line_stripped.startswith("--") or line_stripped.startswith("CONSTRAINT"):
            continue
        m = col_pattern.match(line)
        if m:
            col_name = m.group(1) or m.group(2)
            if col_name:
                columns.append(col_name.lower())
    return columns


def _extract_primary_keys_from_ddl(ddl: str) -> List[str]:
    """Extract columns marked as PRIMARY KEY from DDL text.

    Args:
        ddl: CREATE TABLE DDL text

    Returns:
        List of primary key column names.
    """
    pks: List[str] = []
    pk_pattern = re.compile(
        r'^\s+'
        r'(?:"([^"]+)"'
        r'|([a-zA-Z_]\w*))'
        r'\s+\w+.*?PRIMARY\s+KEY',
        re.IGNORECASE
    )
    for line in ddl.split("\n"):
        m = pk_pattern.match(line)
        if m:
            col_name = m.group(1) or m.group(2)
            if col_name:
                pks.append(col_name.lower())
    return pks


def _build_module_lookup() -> Dict[str, Dict]:
    """Build a lookup from table name -> module config.

    Scans MODULES dict and creates mappings from both full table name
    and short table name to the module config.

    Returns:
        Dict mapping table name variants to module entries.
    """
    lookup: Dict[str, Dict] = {}
    for module_name, module_cfg in MODULES.items():
        table = module_cfg.get("table", "")
        if table:
            short = _extract_short_table_name(table)
            lookup[table] = {"name": module_name, **module_cfg}
            lookup[short] = {"name": module_name, **module_cfg}
    return lookup


def _find_join_examples_for_table(
    full_table_name: str,
    short_name: str,
) -> List[Dict[str, str]]:
    """Find CROSS_DOMAIN_JOINS entries involving a specific table.

    Args:
        full_table_name: Schema-qualified table name (e.g. "wpo.lup_agents")
        short_name: Short table name (e.g. "lup_agents")

    Returns:
        List of join example dicts with keys: name, left, right, on, type
    """
    examples: List[Dict[str, str]] = []
    for join_name, join_cfg in CROSS_DOMAIN_JOINS.items():
        left = join_cfg.get("left", "")
        right = join_cfg.get("right", "")

        # Match by full name or short name appearing in left/right
        left_match = (
            left == full_table_name
            or _extract_short_table_name(left) == short_name
            or left.startswith("*")  # wildcard entries like "*.company_id"
        )
        right_match = (
            right == full_table_name
            or _extract_short_table_name(right) == short_name
        )

        if left_match or right_match:
            examples.append({
                "name": join_name,
                "left": left,
                "right": right,
                "on": join_cfg.get("on", ""),
                "type": join_cfg.get("type", "LEFT JOIN"),
            })
    return examples


def _build_fk_list_from_join_keys(
    join_keys: Dict[str, str],
    full_table_name: str,
) -> List[Dict[str, str]]:
    """Convert a table's join_keys dict to a list of FK reference dicts.

    The join_keys dict maps local_column -> "remote_table.remote_column, ..."

    Args:
        join_keys: Dict from table config, e.g. {"npn": "lup_agents.npn, com_totals.npn"}
        full_table_name: Source table name for context.

    Returns:
        List of FK reference dicts: [{from_col, to_table, to_column}, ...]
    """
    fk_list: List[Dict[str, str]] = []
    for local_col, targets_str in join_keys.items():
        # Clean up the local column (remove casts like ::TEXT)
        clean_local = local_col.split("::")[0].strip()

        for target in targets_str.split(","):
            target = target.strip()
            if not target:
                continue
            # Skip wildcard/note targets
            if target.startswith("*") or target.startswith("<"):
                continue
            # Handle "table.column" or "table.column (note)"
            target_clean = target.split("(")[0].strip()
            if "." in target_clean:
                to_table, to_col = target_clean.rsplit(".", 1)
                fk_list.append({
                    "from_column": clean_local,
                    "to_table": to_table.strip(),
                    "to_column": to_col.strip(),
                })
    return fk_list


def _build_table_embedding_text(
    table_name: str,
    description: str,
    columns: List[str],
    tags: List[str],
) -> str:
    """Build embedding text for a table metadata record.

    Args:
        table_name: Full table name.
        description: Human-readable description.
        columns: List of column names.
        tags: List of tag strings (domain, module, etc.)

    Returns:
        Multi-line string for embedding.
    """
    lines = [
        f"Table: {table_name}",
        f"Description: {description}",
    ]
    if columns:
        lines.append(f"Columns: {', '.join(columns[:50])}")
    if tags:
        lines.append(f"Tags: {', '.join(tags)}")
    return "\n".join(lines)


# =============================================================================
# Upsert SQL for jay_table_metadata
# =============================================================================

_TABLE_UPSERT_SQL = text("""
    INSERT INTO wpo.jay_table_metadata (
        schema_name,
        table_name,
        description,
        columns,
        primary_keys,
        foreign_keys,
        join_examples,
        sample_queries,
        tags,
        embedding,
        row_count,
        updated_at
    ) VALUES (
        :schema_name,
        :table_name,
        :description,
        :columns,
        :primary_keys,
        :foreign_keys,
        :join_examples,
        :sample_queries,
        :tags,
        :embedding,
        :row_count,
        :updated_at
    )
    ON CONFLICT ON CONSTRAINT jay_table_metadata_uq DO UPDATE SET
        description     = EXCLUDED.description,
        columns         = EXCLUDED.columns,
        primary_keys    = EXCLUDED.primary_keys,
        foreign_keys    = EXCLUDED.foreign_keys,
        join_examples   = EXCLUDED.join_examples,
        sample_queries  = EXCLUDED.sample_queries,
        tags            = EXCLUDED.tags,
        embedding       = EXCLUDED.embedding,
        row_count       = EXCLUDED.row_count,
        updated_at      = EXCLUDED.updated_at
""")


# =============================================================================
# sync_table_metadata
# =============================================================================

def sync_table_metadata(db_session: Session) -> dict:
    """Sync hardcoded catalog metadata into wpo.jay_table_metadata with embeddings.

    Workflow:
        1. Iterate TABLE_DOMAINS to collect all known tables.
        2. For each table: build description, extract columns from DDL, find PKs/FKs,
           collect join examples, match MODULES for sample_queries, build tags.
        3. Generate embedding text for each table.
        4. Batch embed all table texts.
        5. Upsert into wpo.jay_table_metadata.

    Args:
        db_session: Active SQLAlchemy session.

    Returns:
        Dict with sync stats: tables_synced, tables_skipped, errors.
    """
    stats = {
        "tables_synced": 0,
        "tables_skipped": 0,
        "errors": 0,
    }

    logger.info("Starting table metadata sync from TABLE_DOMAINS (%d domains)", len(TABLE_DOMAINS))

    # Build module lookup for matching tables -> sample_queries
    module_lookup = _build_module_lookup()

    # Collect all table metadata records
    table_records: List[Dict[str, Any]] = []
    embedding_texts: List[str] = []

    for domain_name, domain_cfg in TABLE_DOMAINS.items():
        domain_desc = domain_cfg.get("description", "")
        domain_db_type = domain_cfg.get("db_type", "postgres")
        domain_sample_queries = domain_cfg.get("sample_queries", [])

        for table_cfg in domain_cfg.get("tables", []):
            full_name = table_cfg["name"]
            short_name = _extract_short_table_name(full_name)
            schema_name = _extract_schema_from_name(full_name)
            table_desc = table_cfg.get("description", "")
            ddl = table_cfg.get("ddl", "")

            try:
                # Build combined description
                combined_desc = table_desc
                if not combined_desc:
                    combined_desc = f"Table in {domain_name} domain. {domain_desc}"

                # Extract columns from DDL
                columns = _parse_columns_from_ddl(ddl) if ddl else []

                # Extract primary keys from DDL
                primary_keys = _extract_primary_keys_from_ddl(ddl) if ddl else []

                # Build FK references from join_keys
                join_keys = table_cfg.get("join_keys", {})
                foreign_keys = _build_fk_list_from_join_keys(join_keys, full_name)

                # Find join examples from CROSS_DOMAIN_JOINS
                join_examples = _find_join_examples_for_table(full_name, short_name)

                # Get sample queries: prefer module match, fall back to domain
                module_match = module_lookup.get(full_name) or module_lookup.get(short_name)
                sample_queries = []
                if module_match:
                    sample_queries = module_match.get("sample_queries", [])
                if not sample_queries:
                    sample_queries = domain_sample_queries

                # Build tags
                tags = [domain_name]
                if module_match:
                    tags.append(module_match["name"])
                if domain_db_type:
                    tags.append(domain_db_type)
                # Add scope info
                scope_col = table_cfg.get("scope_column")
                if scope_col:
                    tags.append(f"scoped:{scope_col}")

                # Build embedding text
                emb_text = _build_table_embedding_text(
                    table_name=full_name,
                    description=combined_desc,
                    columns=columns,
                    tags=tags,
                )

                table_records.append({
                    "schema_name": schema_name,
                    "table_name": short_name,
                    "description": combined_desc,
                    "columns": json.dumps(columns),
                    "primary_keys": json.dumps(primary_keys),
                    "foreign_keys": json.dumps(foreign_keys),
                    "join_examples": json.dumps(join_examples),
                    "sample_queries": json.dumps(sample_queries),
                    "tags": tags,
                    "row_count": None,  # Will be populated separately if needed
                })
                embedding_texts.append(emb_text)

            except Exception as e:
                logger.error("Error processing table %s: %s", full_name, e)
                stats["errors"] += 1

    if not table_records:
        logger.warning("No table records to sync.")
        return stats

    logger.info("Collected %d table records for embedding", len(table_records))

    # ─── Generate embeddings in batches ───
    embeddings: List[Optional[List[float]]] = []
    batch_size = 16  # match embed_texts batch limit
    error_indices: set = set()

    for batch_start in range(0, len(embedding_texts), batch_size):
        batch_end = min(batch_start + batch_size, len(embedding_texts))
        batch = embedding_texts[batch_start:batch_end]

        try:
            batch_embeddings = embed_texts(batch)
            embeddings.extend(batch_embeddings)
        except Exception as e:
            logger.error(
                "Embedding failed for table batch [%d:%d]: %s",
                batch_start, batch_end, e,
            )
            for idx in range(batch_start, batch_end):
                error_indices.add(idx)
            embeddings.extend([None] * len(batch))

    # ─── Upsert each table record ───
    now = datetime.now(timezone.utc)
    upserted = 0

    for i, record in enumerate(table_records):
        if i in error_indices or embeddings[i] is None:
            stats["errors"] += 1
            continue

        try:
            params = {
                **record,
                "embedding": json.dumps(embeddings[i]),  # stored as JSON text
                "updated_at": now,
            }
            db_session.execute(_TABLE_UPSERT_SQL, params)
            upserted += 1
        except Exception as e:
            logger.error(
                "Upsert failed for table %s.%s: %s",
                record["schema_name"], record["table_name"], e,
            )
            stats["errors"] += 1

    # ─── Commit ───
    if upserted > 0:
        try:
            db_session.commit()
            logger.info(
                "Committed %d table metadata upserts to wpo.jay_table_metadata",
                upserted,
            )
        except Exception as e:
            logger.error("Commit failed during table metadata sync: %s", e)
            db_session.rollback()
            stats["errors"] += upserted
            upserted = 0

    stats["tables_synced"] = upserted
    stats["tables_skipped"] = len(table_records) - upserted - stats["errors"]

    logger.info(
        "Table metadata sync complete: %d synced, %d skipped, %d errors",
        stats["tables_synced"], stats["tables_skipped"], stats["errors"],
    )
    return stats


# =============================================================================
# sync_column_metadata
# =============================================================================

def sync_column_metadata(db_session: Session) -> dict:
    """Enrich wpo.jay_column_embeddings with FK references, join examples, and tags.

    Workflow:
        1. Build the join graph using discover_join_graph().
        2. Build a domain lookup: table_name -> domain_name.
        3. For each row in jay_column_embeddings:
           - Find FK edges from the join graph where this column is involved.
           - Find join examples from CROSS_DOMAIN_JOINS for this table.
           - Build tags from domain name + column characteristics.
        4. Batch update jay_column_embeddings with fk_references, join_examples, tags.

    Args:
        db_session: Active SQLAlchemy session.

    Returns:
        Dict with stats: columns_updated, columns_skipped, errors.
    """
    stats = {
        "columns_updated": 0,
        "columns_skipped": 0,
        "errors": 0,
    }

    logger.info("Starting column metadata enrichment")

    # ─── Step 1: Build join graph ───
    try:
        from app.utils.jay.fk_inference import discover_join_graph, JoinGraph
        join_graph: JoinGraph = discover_join_graph(db_session)
        logger.info(
            "Join graph built: %d edges, %d tables",
            len(join_graph.edges), len(join_graph.tables),
        )
    except Exception as e:
        logger.error("Failed to build join graph: %s", e)
        # Proceed with empty graph -- we can still add domain tags
        from app.utils.jay.fk_inference import JoinGraph
        join_graph = JoinGraph()

    # ─── Step 2: Build domain lookup (short_table_name -> domain_name) ───
    table_to_domain: Dict[str, str] = {}
    table_to_scope: Dict[str, Optional[str]] = {}

    for domain_name, domain_cfg in TABLE_DOMAINS.items():
        for table_cfg in domain_cfg.get("tables", []):
            short = _extract_short_table_name(table_cfg["name"])
            table_to_domain[short] = domain_name
            table_to_scope[short] = table_cfg.get("scope_column")

    # ─── Step 3: Fetch all existing column embedding rows ───
    try:
        rows = db_session.execute(text("""
            SELECT id, schema_name, table_name, column_name, is_categorical
            FROM wpo.jay_column_embeddings
            ORDER BY table_name, column_name
        """)).fetchall()
    except Exception as e:
        logger.error("Failed to fetch column embeddings: %s", e)
        return stats

    if not rows:
        logger.warning("No column embeddings found to enrich.")
        return stats

    logger.info("Found %d column embedding rows to enrich", len(rows))

    # ─── Step 4: Build FK reference lookup from join graph ───
    #
    # For each table+column, collect edges where this column participates.
    # join_graph.edges contains JoinEdge objects:
    #   source_table, source_column, target_table, target_column, confidence, is_explicit
    #
    # We index by (table, column) for O(1) lookup.

    fk_by_col: Dict[Tuple[str, str], List[Dict[str, str]]] = {}
    for edge in join_graph.edges:
        # Source side
        src_key = (edge.source_table, edge.source_column)
        if src_key not in fk_by_col:
            fk_by_col[src_key] = []
        fk_by_col[src_key].append({
            "to_table": edge.target_table,
            "to_column": edge.target_column,
            "confidence": edge.confidence.value,
            "is_explicit": edge.is_explicit,
        })

        # Target side (reverse direction)
        tgt_key = (edge.target_table, edge.target_column)
        if tgt_key not in fk_by_col:
            fk_by_col[tgt_key] = []
        fk_by_col[tgt_key].append({
            "to_table": edge.source_table,
            "to_column": edge.source_column,
            "confidence": edge.confidence.value,
            "is_explicit": edge.is_explicit,
        })

    # ─── Step 5: Pre-compute join examples per table ───
    join_examples_by_table: Dict[str, List[Dict[str, str]]] = {}
    for domain_name, domain_cfg in TABLE_DOMAINS.items():
        for table_cfg in domain_cfg.get("tables", []):
            full_name = table_cfg["name"]
            short_name = _extract_short_table_name(full_name)
            examples = _find_join_examples_for_table(full_name, short_name)
            if examples:
                join_examples_by_table[short_name] = examples

    # ─── Step 6: Build and execute batch updates ───
    update_sql = text("""
        UPDATE wpo.jay_column_embeddings
        SET fk_references  = :fk_references,
            join_examples  = :join_examples,
            tags           = :tags
        WHERE id = :id
    """)

    updated = 0
    for row in rows:
        row_id = row[0]
        schema_name = row[1]
        table_name = row[2]
        column_name = row[3]
        is_categorical = row[4]

        try:
            # FK references for this column
            col_fk_refs = fk_by_col.get((table_name, column_name), [])

            # Join examples for this table
            col_join_examples = join_examples_by_table.get(table_name, [])

            # Build tags
            tags: List[str] = []
            domain = table_to_domain.get(table_name)
            if domain:
                tags.append(domain)
            scope = table_to_scope.get(table_name)
            if scope and column_name == scope:
                tags.append("scope_column")
            if is_categorical:
                tags.append("categorical")
            if col_fk_refs:
                tags.append("has_fk")

            db_session.execute(update_sql, {
                "id": row_id,
                "fk_references": json.dumps(col_fk_refs),
                "join_examples": json.dumps(col_join_examples),
                "tags": tags,
            })
            updated += 1

        except Exception as e:
            logger.error(
                "Failed to update column %s.%s: %s",
                table_name, column_name, e,
            )
            stats["errors"] += 1

    # ─── Commit ───
    if updated > 0:
        try:
            db_session.commit()
            logger.info(
                "Committed %d column metadata enrichments to wpo.jay_column_embeddings",
                updated,
            )
        except Exception as e:
            logger.error("Commit failed during column metadata enrichment: %s", e)
            db_session.rollback()
            stats["errors"] += updated
            updated = 0

    stats["columns_updated"] = updated

    logger.info(
        "Column metadata enrichment complete: %d updated, %d errors",
        stats["columns_updated"], stats["errors"],
    )
    return stats
