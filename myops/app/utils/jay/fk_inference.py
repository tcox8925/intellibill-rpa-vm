"""
JAI FK Inference - Foreign key discovery and join graph expansion.

Task 2.1: Discovers explicit FKs from PostgreSQL system catalogs and infers
implicit FKs by column-name matching across tables, building an adjacency
graph of joinable table pairs.

Task 2.2: Given a set of seed tables, expands via BFS through the join graph
to find bridge tables needed to connect them (max 2 hops).

No external graph libraries required -- uses pure-Python BFS over a dict-based
adjacency structure.
"""

import hashlib
import json as _json
import logging
import os
import time as _time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Dict, FrozenSet, List, Optional, Set, Tuple

from sqlalchemy import text
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

# Persistent cache file for FK graph (avoids information_schema queries on restart)
_FK_CACHE_DIR = Path(os.environ.get(
    "JAI_CACHE_DIR",
    Path(__file__).resolve().parent.parent.parent.parent / ".jay_cache",
))
_FK_CACHE_FILE = _FK_CACHE_DIR / "fk_graph.json"


# =============================================================================
# Data Models
# =============================================================================


class Confidence(str, Enum):
    """Confidence level for an inferred FK edge."""
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


@dataclass(frozen=True)
class JoinEdge:
    """A single joinable edge between two tables."""
    source_table: str
    source_column: str
    target_table: str
    target_column: str
    confidence: Confidence
    is_explicit: bool  # True = real FK constraint; False = inferred by name match

    def __repr__(self) -> str:
        kind = "FK" if self.is_explicit else f"inferred({self.confidence.value})"
        return (
            f"JoinEdge({self.source_table}.{self.source_column} -> "
            f"{self.target_table}.{self.target_column} [{kind}])"
        )


@dataclass
class JoinGraph:
    """Adjacency-based join graph over database tables."""
    edges: List[JoinEdge] = field(default_factory=list)
    adjacency: Dict[str, Set[Tuple[str, JoinEdge]]] = field(
        default_factory=lambda: defaultdict(set),
    )
    tables: Set[str] = field(default_factory=set)

    def add_edge(self, edge: JoinEdge) -> None:
        """Add a join edge and update adjacency in both directions."""
        self.edges.append(edge)
        self.tables.add(edge.source_table)
        self.tables.add(edge.target_table)
        self.adjacency[edge.source_table].add((edge.target_table, edge))
        self.adjacency[edge.target_table].add((edge.source_table, edge))

    def neighbors(self, table: str) -> Set[Tuple[str, JoinEdge]]:
        """Return set of (neighbor_table, edge) for a given table."""
        return self.adjacency.get(table, set())

    def to_dict(self) -> dict:
        """Serialize graph to a JSON-compatible dict for file persistence."""
        return {
            "edges": [
                {
                    "source_table": e.source_table,
                    "source_column": e.source_column,
                    "target_table": e.target_table,
                    "target_column": e.target_column,
                    "confidence": e.confidence.value,
                    "is_explicit": e.is_explicit,
                }
                for e in self.edges
            ],
        }

    @classmethod
    def from_dict(cls, data: dict) -> "JoinGraph":
        """Deserialize graph from a dict (inverse of to_dict)."""
        graph = cls()
        for e in data.get("edges", []):
            graph.add_edge(JoinEdge(
                source_table=e["source_table"],
                source_column=e["source_column"],
                target_table=e["target_table"],
                target_column=e["target_column"],
                confidence=Confidence(e["confidence"]),
                is_explicit=e["is_explicit"],
            ))
        return graph


@dataclass(frozen=True)
class JoinPath:
    """A single step in a multi-hop join path."""
    from_table: str
    from_col: str
    to_table: str
    to_col: str


@dataclass
class ExpandedResult:
    """Result of expanding seed tables through the join graph."""
    expanded_tables: Set[str] = field(default_factory=set)
    join_paths: List[JoinPath] = field(default_factory=list)
    bridge_tables: Set[str] = field(default_factory=set)


# =============================================================================
# Constants
# =============================================================================

# Columns that should NEVER be treated as FK candidates even if names match
_EXCLUDED_COLUMNS: FrozenSet[str] = frozenset({
    "created_at",
    "updated_at",
    "modified_time",
    "created_time",
    "is_active",
    "description",
    "notes",
    "created_by",
    "updated_by",
    "modified_by",
    "row_number",
    "sort_order",
    "display_order",
})

# Substrings that signal a column is ID-like (HIGH confidence)
_ID_LIKE_SUBSTRINGS: Tuple[str, ...] = (
    "id",
    "npn",
    "_key",
    "pk_id",
    "txn_id",
)

# Exact column name patterns for MEDIUM confidence (known FK patterns)
_MEDIUM_PATTERNS: FrozenSet[str] = frozenset({
    "agent_npn",
    "company_id",
    "carrier_id",
    "entity_id",
    "provider_id",
    "member_id",
    "contract_id",
    "policy_id",
    "plan_id",
    "group_id",
    "user_id",
    "account_id",
    "org_id",
    "location_id",
    "ticket_id",
    "case_id",
    "enrollment_id",
})

# Generic columns that share names across tables but are NOT FKs (LOW confidence)
_LOW_CONFIDENCE_COLUMNS: FrozenSet[str] = frozenset({
    "status",
    "name",
    "type",
    "state",
    "code",
    "value",
    "label",
    "category",
    "source",
    "level",
    "role",
    "email",
    "phone",
    "address",
    "city",
    "zip",
    "county",
    "country",
    "first_name",
    "last_name",
    "full_name",
    "line_of_business",
    "product",
    "effective_date",
    "expiration_date",
    "start_date",
    "end_date",
    "date",
    "month",
    "year",
    "amount",
    "total",
    "count",
    "percentage",
    "rate",
    "flag",
    "active",
    "enabled",
    "deleted",
    "version",
})

# Compatible PostgreSQL types for FK inference (normalized groups)
_TYPE_COMPATIBILITY: Dict[str, str] = {
    "integer": "int",
    "int": "int",
    "int4": "int",
    "int8": "int",
    "bigint": "int",
    "smallint": "int",
    "serial": "int",
    "bigserial": "int",
    "character varying": "str",
    "varchar": "str",
    "text": "str",
    "char": "str",
    "character": "str",
    "uuid": "uuid",
    "numeric": "num",
    "decimal": "num",
    "real": "num",
    "double precision": "num",
    "float": "num",
    "float4": "num",
    "float8": "num",
}


# =============================================================================
# SQL Queries
# =============================================================================

_EXPLICIT_FK_QUERY = """\
SELECT
    tc.table_name   AS source_table,
    kcu.column_name AS source_column,
    ccu.table_name  AS target_table,
    ccu.column_name AS target_column
FROM information_schema.table_constraints tc
JOIN information_schema.key_column_usage kcu
    ON tc.constraint_name = kcu.constraint_name
    AND tc.table_schema = kcu.table_schema
JOIN information_schema.constraint_column_usage ccu
    ON tc.constraint_name = ccu.constraint_name
    AND tc.table_schema = ccu.table_schema
WHERE tc.constraint_type = 'FOREIGN KEY'
    AND tc.table_schema = :schema
"""

_ALL_COLUMNS_QUERY = """\
SELECT
    table_name,
    column_name,
    data_type
FROM information_schema.columns
WHERE table_schema = :schema
ORDER BY table_name, ordinal_position
"""


# =============================================================================
# Task 2.1: FK Discovery
# =============================================================================


def _normalize_type(data_type: str) -> str:
    """Map a PostgreSQL data type to a compatibility group."""
    dt = data_type.lower().strip()
    return _TYPE_COMPATIBILITY.get(dt, dt)


def _score_column_confidence(column_name: str) -> Confidence:
    """Assign a confidence level to an inferred FK based on column name."""
    col = column_name.lower()

    # Check exclusion list first
    if col in _EXCLUDED_COLUMNS:
        return Confidence.LOW

    # Check LOW-confidence generic columns
    if col in _LOW_CONFIDENCE_COLUMNS:
        return Confidence.LOW

    # Check MEDIUM-confidence known FK patterns (exact match)
    if col in _MEDIUM_PATTERNS:
        return Confidence.MEDIUM

    # Check HIGH-confidence ID-like substrings
    for substr in _ID_LIKE_SUBSTRINGS:
        if substr in col:
            return Confidence.HIGH

    # Default to MEDIUM for anything not explicitly LOW or HIGH
    return Confidence.MEDIUM


def _discover_explicit_fks(
    db_session: Session,
    schema: str,
) -> List[JoinEdge]:
    """Query PostgreSQL system catalogs for declared foreign key constraints."""
    edges: List[JoinEdge] = []
    try:
        result = db_session.execute(text(_EXPLICIT_FK_QUERY), {"schema": schema})
        rows = result.fetchall()
        logger.info("Discovered %d explicit FK constraints in schema '%s'", len(rows), schema)

        for row in rows:
            source_table, source_column, target_table, target_column = (
                row[0], row[1], row[2], row[3],
            )
            edge = JoinEdge(
                source_table=source_table,
                source_column=source_column,
                target_table=target_table,
                target_column=target_column,
                confidence=Confidence.HIGH,
                is_explicit=True,
            )
            edges.append(edge)

    except Exception as e:
        logger.error("Failed to discover explicit FKs for schema '%s': %s", schema, e)

    return edges


def _discover_implicit_fks(
    db_session: Session,
    schema: str,
    explicit_pairs: Set[Tuple[str, str, str, str]],
    min_confidence: Confidence = Confidence.MEDIUM,
) -> List[JoinEdge]:
    """Infer FK relationships by column-name + type matching across tables.

    Args:
        db_session: Active SQLAlchemy session.
        schema: Database schema to scan.
        explicit_pairs: Set of (src_table, src_col, tgt_table, tgt_col) already
                        discovered as explicit FKs, so we don't duplicate them.
        min_confidence: Minimum confidence threshold -- edges below this are
                        filtered out. Defaults to MEDIUM (excludes LOW).

    Returns:
        List of inferred JoinEdge objects.
    """
    # Confidence ordering for comparison
    _conf_rank = {Confidence.LOW: 0, Confidence.MEDIUM: 1, Confidence.HIGH: 2}
    min_rank = _conf_rank[min_confidence]

    edges: List[JoinEdge] = []

    try:
        result = db_session.execute(text(_ALL_COLUMNS_QUERY), {"schema": schema})
        rows = result.fetchall()
    except Exception as e:
        logger.error("Failed to query columns for schema '%s': %s", schema, e)
        return edges

    # Group: column_name -> [(table_name, normalized_type)]
    column_tables: Dict[str, List[Tuple[str, str]]] = defaultdict(list)
    for row in rows:
        table_name, column_name, data_type = row[0], row[1], row[2]
        column_tables[column_name].append((table_name, _normalize_type(data_type)))

    inferred_count = 0
    skipped_low = 0

    for col_name, table_type_pairs in column_tables.items():
        # Column must appear in 2+ tables
        if len(table_type_pairs) < 2:
            continue

        # Skip excluded columns entirely
        if col_name.lower() in _EXCLUDED_COLUMNS:
            continue

        confidence = _score_column_confidence(col_name)

        # Filter by minimum confidence threshold
        if _conf_rank[confidence] < min_rank:
            skipped_low += 1
            continue

        # Group tables by normalized type for compatibility check
        type_groups: Dict[str, List[str]] = defaultdict(list)
        for table_name, norm_type in table_type_pairs:
            type_groups[norm_type].append(table_name)

        # Only create edges between tables with compatible types
        for norm_type, tables in type_groups.items():
            if len(tables) < 2:
                continue

            # Create edges for all pairs (undirected -- adjacency handles both ways)
            for i in range(len(tables)):
                for j in range(i + 1, len(tables)):
                    src, tgt = tables[i], tables[j]

                    # Skip if already an explicit FK
                    if (src, col_name, tgt, col_name) in explicit_pairs:
                        continue
                    if (tgt, col_name, src, col_name) in explicit_pairs:
                        continue

                    edge = JoinEdge(
                        source_table=src,
                        source_column=col_name,
                        target_table=tgt,
                        target_column=col_name,
                        confidence=confidence,
                        is_explicit=False,
                    )
                    edges.append(edge)
                    inferred_count += 1

    logger.info(
        "Inferred %d implicit FK edges in schema '%s' (skipped %d LOW-confidence)",
        inferred_count,
        schema,
        skipped_low,
    )
    return edges


def _get_schema_fingerprint(db_session: Session, schema: str) -> str:
    """Quick fingerprint of schema state: table count + column count.

    Used to detect schema changes and invalidate the cached FK graph.
    """
    try:
        row = db_session.execute(text(
            "SELECT COUNT(DISTINCT table_name), COUNT(*) "
            "FROM information_schema.columns WHERE table_schema = :schema"
        ), {"schema": schema}).fetchone()
        raw = f"{schema}:{row[0]}:{row[1]}"
        return hashlib.md5(raw.encode()).hexdigest()[:16]
    except Exception:
        return ""


def _save_graph_cache(graph: JoinGraph, fingerprint: str) -> None:
    """Persist FK graph to disk for fast restart."""
    try:
        _FK_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        payload = {"fingerprint": fingerprint, "graph": graph.to_dict()}
        _FK_CACHE_FILE.write_text(_json.dumps(payload))
        logger.info("FK graph cached to %s", _FK_CACHE_FILE)
    except Exception as e:
        logger.debug("Could not save FK graph cache: %s", e)


def _load_graph_cache(fingerprint: str) -> Optional[JoinGraph]:
    """Load FK graph from disk if fingerprint matches (schema unchanged)."""
    try:
        if not _FK_CACHE_FILE.exists():
            return None
        payload = _json.loads(_FK_CACHE_FILE.read_text())
        if payload.get("fingerprint") != fingerprint:
            logger.info("FK graph cache stale (fingerprint mismatch), rebuilding")
            return None
        graph = JoinGraph.from_dict(payload["graph"])
        logger.info(
            "FK graph loaded from cache: %d tables, %d edges",
            len(graph.tables), len(graph.edges),
        )
        return graph
    except Exception as e:
        logger.debug("Could not load FK graph cache: %s", e)
        return None


def discover_join_graph(
    db_session: Session,
    schema: str = "wpo",
    min_confidence: Confidence = Confidence.MEDIUM,
) -> JoinGraph:
    """Build a complete join graph from explicit and inferred foreign keys.

    On first call, queries information_schema and saves result to disk.
    On subsequent startups, loads from cache if schema hasn't changed
    (same table+column count), saving 5-15s of DB queries.

    Args:
        db_session: Active SQLAlchemy session with access to information_schema.
        schema: PostgreSQL schema to scan (default: "wpo").
        min_confidence: Minimum confidence for inferred edges. Defaults to
                        MEDIUM, which filters out generic LOW-confidence columns
                        like 'status', 'name', 'type'.

    Returns:
        A JoinGraph with all discovered edges and an adjacency dict for O(1)
        neighbor lookups.
    """
    t0 = _time.time()

    # Try loading from persistent cache
    fingerprint = _get_schema_fingerprint(db_session, schema)
    if fingerprint:
        cached = _load_graph_cache(fingerprint)
        if cached is not None:
            logger.info("FK graph loaded from cache in %.1fs", _time.time() - t0)
            return cached

    # Full discovery from information_schema
    graph = JoinGraph()

    # Step 1: Explicit FKs from system catalogs
    explicit_edges = _discover_explicit_fks(db_session, schema)
    explicit_pairs: Set[Tuple[str, str, str, str]] = set()

    for edge in explicit_edges:
        graph.add_edge(edge)
        explicit_pairs.add((
            edge.source_table, edge.source_column,
            edge.target_table, edge.target_column,
        ))

    # Step 2: Inferred FKs by column name + type matching
    implicit_edges = _discover_implicit_fks(
        db_session, schema, explicit_pairs, min_confidence,
    )
    for edge in implicit_edges:
        graph.add_edge(edge)

    elapsed = _time.time() - t0
    logger.info(
        "Join graph built: %d tables, %d edges (%d explicit, %d inferred) in %.1fs",
        len(graph.tables),
        len(graph.edges),
        len(explicit_edges),
        len(implicit_edges),
        elapsed,
    )

    # Persist to disk for next startup
    if fingerprint:
        _save_graph_cache(graph, fingerprint)

    return graph


# =============================================================================
# Task 2.2: Graph Expansion (BFS)
# =============================================================================


def _bfs_shortest_path(
    graph: JoinGraph,
    start: str,
    end: str,
    max_hops: int,
) -> Optional[List[Tuple[str, JoinEdge]]]:
    """Find the shortest path between two tables using BFS.

    Args:
        graph: The join graph to search.
        start: Starting table name.
        end: Target table name.
        max_hops: Maximum number of edges to traverse.

    Returns:
        A list of (table, edge) tuples representing the path, or None if no
        path exists within max_hops.
    """
    if start == end:
        return []

    if start not in graph.tables or end not in graph.tables:
        return None

    # BFS state: queue of (current_table, path_so_far)
    visited: Set[str] = {start}
    queue: deque[Tuple[str, List[Tuple[str, JoinEdge]]]] = deque()
    queue.append((start, []))

    while queue:
        current, path = queue.popleft()

        # Stop expanding if we've reached max depth
        if len(path) >= max_hops:
            continue

        for neighbor, edge in graph.neighbors(current):
            if neighbor in visited:
                continue

            new_path = path + [(neighbor, edge)]

            if neighbor == end:
                return new_path

            visited.add(neighbor)
            queue.append((neighbor, new_path))

    return None


def expand_tables_via_joins(
    seed_tables: Set[str],
    join_graph: JoinGraph,
    max_hops: int = 2,
) -> ExpandedResult:
    """Expand seed tables through the join graph to find bridge tables.

    Given a set of seed tables (e.g., from column matching in the query), finds
    the shortest join paths between every pair of seed tables and adds any
    intermediate "bridge" tables needed to connect them.

    Args:
        seed_tables: Set of table names that the query needs.
        join_graph: Pre-built join graph from discover_join_graph().
        max_hops: Maximum number of join hops to traverse (default: 2).

    Returns:
        ExpandedResult with expanded_tables, join_paths, and bridge_tables.
    """
    result = ExpandedResult()
    result.expanded_tables = set(seed_tables)

    if len(seed_tables) < 2:
        logger.debug("Fewer than 2 seed tables; no expansion needed")
        return result

    # Track edges we've already added to avoid duplicates
    seen_paths: Set[Tuple[str, str, str, str]] = set()
    seed_list = sorted(seed_tables)  # deterministic ordering

    for i in range(len(seed_list)):
        for j in range(i + 1, len(seed_list)):
            src, tgt = seed_list[i], seed_list[j]

            path = _bfs_shortest_path(join_graph, src, tgt, max_hops)

            if path is None:
                logger.debug(
                    "No join path found between '%s' and '%s' within %d hops",
                    src, tgt, max_hops,
                )
                continue

            # Walk the path and collect edges + bridge tables
            prev_table = src
            for hop_table, edge in path:
                # Determine correct column orientation
                if edge.source_table == prev_table:
                    from_tbl = edge.source_table
                    from_col = edge.source_column
                    to_tbl = edge.target_table
                    to_col = edge.target_column
                else:
                    from_tbl = edge.target_table
                    from_col = edge.target_column
                    to_tbl = edge.source_table
                    to_col = edge.source_column

                path_key = (from_tbl, from_col, to_tbl, to_col)
                if path_key not in seen_paths:
                    seen_paths.add(path_key)
                    result.join_paths.append(JoinPath(
                        from_table=from_tbl,
                        from_col=from_col,
                        to_table=to_tbl,
                        to_col=to_col,
                    ))

                result.expanded_tables.add(hop_table)
                prev_table = hop_table

    # Bridge tables = expanded minus original seeds
    result.bridge_tables = result.expanded_tables - seed_tables

    if result.bridge_tables:
        logger.info(
            "Expansion added %d bridge table(s): %s",
            len(result.bridge_tables),
            sorted(result.bridge_tables),
        )
    else:
        logger.debug("No bridge tables needed -- seed tables are directly connected")

    return result
