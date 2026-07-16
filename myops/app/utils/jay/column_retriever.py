"""
JAI Column Retriever - Semantic column-level schema discovery.

Replaces rule-based schema selection with hybrid vector + LSH search over
the ``wpo.jay_column_embeddings`` table. Given a natural-language query:

1. Embeds the query via text-embedding-3-small (cached).
2. Vector-searches ``jay_column_embeddings`` via pgvector cosine similarity
   to find the most semantically relevant columns.
3. Performs LSH (locality-sensitive hashing) value matching over categorical
   column sample values to catch abbreviations/typos that embeddings miss.
4. Merges results, deduplicates, and scores tables by column relevance.
5. Optionally expands the selected table set through the FK join graph to
   find bridge tables needed for multi-table JOINs.

Public API:
- retrieve_schema_for_query(query, db_session, ...) -> SchemaRetrievalResult
- ColumnValueLSH  (singleton index for categorical value matching)
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import pickle
import threading
import time
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

import numpy as np
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.utils.jay.embedding_cache import (
    get_query_embedding as _unified_get_query_embedding,
    parse_embedding as _parse_embedding_shared,
)
from app.utils.jay.fk_inference import (
    ExpandedResult,
    JoinGraph,
    JoinPath,
    expand_tables_via_joins,
)

logger = logging.getLogger(__name__)


# =============================================================================
# Data Models
# =============================================================================


@dataclass
class ColumnMatch:
    """A single column matched by vector or LSH search."""

    schema_name: str
    table_name: str
    column_name: str
    similarity: float
    match_source: str  # "vector" or "lsh"
    sample_values: list = field(default_factory=list)

    def __repr__(self) -> str:
        return (
            f"ColumnMatch({self.table_name}.{self.column_name} "
            f"sim={self.similarity:.3f} src={self.match_source})"
        )


@dataclass
class SchemaRetrievalResult:
    """Complete result of semantic schema retrieval for a query."""

    matched_columns: List[ColumnMatch] = field(default_factory=list)
    selected_tables: Set[str] = field(default_factory=set)
    expanded_tables: Set[str] = field(default_factory=set)
    join_paths: List[JoinPath] = field(default_factory=list)
    retrieval_time_ms: float = 0.0
    vector_matches: int = 0
    lsh_matches: int = 0

    def summary(self) -> str:
        """Return a human-readable summary for logging."""
        return (
            f"SchemaRetrievalResult("
            f"columns={len(self.matched_columns)}, "
            f"tables={sorted(self.selected_tables)}, "
            f"expanded={sorted(self.expanded_tables)}, "
            f"joins={len(self.join_paths)}, "
            f"vector={self.vector_matches}, "
            f"lsh={self.lsh_matches}, "
            f"time={self.retrieval_time_ms:.1f}ms)"
        )


# =============================================================================
# Task 3.4: Query Embedding Cache
# =============================================================================

def _get_query_embedding(query: str) -> List[float]:
    """Embed a query string (delegates to unified embedding cache)."""
    return _unified_get_query_embedding(query)


def clear_query_cache() -> int:
    """Clear the query embedding cache."""
    from app.utils.jay.embedding_cache import clear_query_cache as _clear
    return _clear()


# =============================================================================
# Task 3.1: Vector Search
# =============================================================================

_FETCH_ALL_EMBEDDINGS_SQL = text("""
    SELECT schema_name, table_name, column_name, embedding, sample_values
    FROM wpo.jay_column_embeddings
    WHERE embedding IS NOT NULL
""")


# ---------------------------------------------------------------------------
# In-memory cache for column embeddings (loaded once, reused across queries)
# ---------------------------------------------------------------------------

_col_emb_cache_lock = threading.Lock()
_col_emb_cache: Optional[dict] = None  # {"rows": [...], "matrix": np.array, "valid_indices": [...], "norms": np.array}
_col_emb_cache_time: float = 0.0
_COL_EMB_CACHE_TTL = 28800  # 8 hours (column embeddings rarely change)

# Disk cache for parsed numpy matrix (avoids 76s DB fetch + JSON parsing on restart)
_DISK_CACHE_DIR = Path(os.environ.get(
    "JAI_CACHE_DIR",
    Path(__file__).resolve().parent.parent.parent.parent / ".jay_cache",
))
_DISK_CACHE_FILE = _DISK_CACHE_DIR / "col_embeddings.npz"
_DISK_META_FILE = _DISK_CACHE_DIR / "col_embeddings_meta.pkl"


def _get_row_count_fingerprint(db_session: Session) -> str:
    """Quick fingerprint: total embedding row count + checksum of first/last IDs."""
    try:
        row = db_session.execute(text(
            "SELECT COUNT(*), COALESCE(MIN(id), 0), COALESCE(MAX(id), 0) "
            "FROM wpo.jay_column_embeddings WHERE embedding IS NOT NULL"
        )).fetchone()
        raw = f"{row[0]}:{row[1]}:{row[2]}"
        return hashlib.md5(raw.encode()).hexdigest()[:16]
    except Exception:
        return ""


def _save_disk_cache(cache: dict, fingerprint: str) -> None:
    """Save parsed numpy matrix + row metadata to disk for fast restart."""
    try:
        _DISK_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        # Save numpy arrays
        np.savez_compressed(
            str(_DISK_CACHE_FILE),
            matrix=cache["matrix"],
            norms=cache["norms"],
        )
        # Save row metadata (schema, table, column, sample_values — no embeddings)
        row_meta = [
            (r[0], r[1], r[2], r[4]) for r in cache["rows"]  # skip embedding col
        ]
        meta = {
            "fingerprint": fingerprint,
            "valid_indices": cache["valid_indices"],
            "row_meta": row_meta,
        }
        with open(_DISK_META_FILE, "wb") as f:
            pickle.dump(meta, f, protocol=pickle.HIGHEST_PROTOCOL)
        logger.info("Column embeddings saved to disk cache (%d rows)", len(row_meta))
    except Exception as e:
        logger.debug("Could not save column embedding disk cache: %s", e)


def _load_disk_cache(fingerprint: str) -> Optional[dict]:
    """Load pre-parsed numpy matrix from disk if fingerprint matches."""
    try:
        if not _DISK_CACHE_FILE.exists() or not _DISK_META_FILE.exists():
            return None

        with open(_DISK_META_FILE, "rb") as f:
            meta = pickle.load(f)

        if meta.get("fingerprint") != fingerprint:
            logger.info("Column embedding disk cache stale, will reload from DB")
            return None

        npz = np.load(str(_DISK_CACHE_FILE))
        matrix = npz["matrix"]
        norms = npz["norms"]

        # Reconstruct row tuples (without embedding column — set to None)
        rows = [
            (r[0], r[1], r[2], None, r[3]) for r in meta["row_meta"]
        ]

        cache = {
            "rows": rows,
            "matrix": matrix,
            "valid_indices": meta["valid_indices"],
            "norms": norms,
        }
        logger.info(
            "Column embeddings loaded from disk cache: %d rows, %d with embeddings",
            len(rows), len(meta["valid_indices"]),
        )
        return cache
    except Exception as e:
        logger.debug("Could not load column embedding disk cache: %s", e)
        return None


def _get_column_embeddings(db_session: Session) -> dict:
    """Load and cache column embeddings with pre-parsed numpy matrix.

    Tries (in order):
    1. In-memory cache (TTL-based)
    2. Disk cache (.npz file — avoids DB fetch + JSON parsing)
    3. Full DB load + JSON parsing (slowest, saves to disk for next time)
    """
    global _col_emb_cache, _col_emb_cache_time
    now = time.monotonic()

    with _col_emb_cache_lock:
        if _col_emb_cache is not None and (now - _col_emb_cache_time) < _COL_EMB_CACHE_TTL:
            return _col_emb_cache

    t0 = time.monotonic()

    # Try disk cache first (avoids 76s DB load)
    fingerprint = _get_row_count_fingerprint(db_session)
    if fingerprint:
        disk_cache = _load_disk_cache(fingerprint)
        if disk_cache is not None:
            with _col_emb_cache_lock:
                _col_emb_cache = disk_cache
                _col_emb_cache_time = time.monotonic()
            logger.info(
                "Column embeddings loaded from disk in %.1fs",
                time.monotonic() - t0,
            )
            return disk_cache

    # Full DB load
    try:
        result = db_session.execute(_FETCH_ALL_EMBEDDINGS_SQL)
        rows = result.fetchall()
    except Exception:
        logger.exception("Failed to fetch column embeddings")
        return {"rows": [], "matrix": None, "valid_indices": [], "norms": None}

    # Pre-parse embeddings into numpy matrix
    embeddings = []
    valid_indices = []
    for i, row in enumerate(rows):
        emb = _parse_embedding(row[3])
        if emb is not None:
            embeddings.append(emb)
            valid_indices.append(i)

    matrix = None
    norms = None
    if embeddings:
        matrix = np.array(embeddings, dtype=np.float32)
        norms = np.linalg.norm(matrix, axis=1)

    cache = {
        "rows": rows,
        "matrix": matrix,
        "valid_indices": valid_indices,
        "norms": norms,
    }

    with _col_emb_cache_lock:
        _col_emb_cache = cache
        _col_emb_cache_time = time.monotonic()

    elapsed = time.monotonic() - t0
    logger.info(
        "Column embeddings cached from DB: %d rows, %d with embeddings (%.1fs)",
        len(rows), len(valid_indices), elapsed,
    )

    # Save to disk for next startup
    if fingerprint and matrix is not None:
        _save_disk_cache(cache, fingerprint)

    return cache


def invalidate_column_embedding_cache():
    """Clear in-memory and disk column embedding caches."""
    global _col_emb_cache, _col_emb_cache_time
    with _col_emb_cache_lock:
        _col_emb_cache = None
        _col_emb_cache_time = 0.0
    # Also clear disk cache so next load goes to DB
    try:
        if _DISK_CACHE_FILE.exists():
            _DISK_CACHE_FILE.unlink()
        if _DISK_META_FILE.exists():
            _DISK_META_FILE.unlink()
        logger.info("Column embedding disk cache cleared")
    except Exception:
        pass


def warmup_column_cache(db_session: Session) -> dict:
    """Pre-load column embeddings into the in-memory numpy matrix cache.

    Call during startup to eliminate cold-start latency on first vector search.
    Returns dict with cache stats.
    """
    cache = _get_column_embeddings(db_session)
    rows_count = len(cache.get("rows", []))
    valid_count = len(cache.get("valid_indices", []))
    logger.info(
        "Column embedding cache warmed: %d rows, %d with embeddings",
        rows_count, valid_count,
    )
    return {"total_rows": rows_count, "with_embeddings": valid_count}


def _parse_embedding(raw) -> Optional[List[float]]:
    """Parse an embedding from TEXT column (delegates to unified cache)."""
    return _parse_embedding_shared(raw)


def _vector_search(
    query_embedding: List[float],
    db_session: Session,
    top_k: int = 25,
    threshold: float = 0.35,
) -> List[ColumnMatch]:
    """Search jay_column_embeddings by cosine similarity using numpy batch ops.

    Uses cached embeddings matrix for fast vectorized similarity computation.

    Args:
        query_embedding: The embedded query vector (1536-dim).
        db_session: Active SQLAlchemy session.
        top_k: Maximum number of results after threshold filtering.
        threshold: Minimum cosine similarity (0-1) to include a result.

    Returns:
        List of ColumnMatch objects sorted by descending similarity,
        all with ``match_source="vector"``.
    """
    matches: List[ColumnMatch] = []
    cache = _get_column_embeddings(db_session)
    rows = cache["rows"]
    matrix = cache["matrix"]
    valid_indices = cache["valid_indices"]
    norms = cache["norms"]

    if matrix is None or len(valid_indices) == 0:
        return matches

    # Batch cosine similarity via numpy
    q = np.array(query_embedding, dtype=np.float32)
    norm_q = np.linalg.norm(q)
    if norm_q == 0:
        return matches

    dots = matrix @ q
    denom = norms * norm_q
    denom[denom == 0] = 1.0
    sims = dots / denom

    # Filter by threshold and get top_k
    mask = sims >= threshold
    if not np.any(mask):
        return matches

    filtered_indices = np.where(mask)[0]
    filtered_sims = sims[filtered_indices]
    top_order = np.argsort(-filtered_sims)[:top_k]

    for pos in top_order:
        sim = float(filtered_sims[pos])
        row_idx = valid_indices[filtered_indices[pos]]
        row = rows[row_idx]

        raw_values = row[4]
        sample_vals: list = []
        if raw_values is not None:
            if isinstance(raw_values, list):
                sample_vals = raw_values
            elif isinstance(raw_values, str):
                try:
                    sample_vals = json.loads(raw_values)
                except (json.JSONDecodeError, TypeError):
                    sample_vals = []

        matches.append(ColumnMatch(
            schema_name=row[0],
            table_name=row[1],
            column_name=row[2],
            similarity=sim,
            match_source="vector",
            sample_values=sample_vals,
        ))

    logger.debug(
        "Vector search returned %d matches (top_k=%d, threshold=%.2f, "
        "total rows=%d)",
        len(matches),
        top_k,
        threshold,
        len(rows),
    )
    return matches


# =============================================================================
# Task 3.2: LSH Value Index
# =============================================================================

_LSH_QUERY_SQL = text("""
    SELECT schema_name, table_name, column_name, sample_values
    FROM wpo.jay_column_embeddings
    WHERE is_categorical = true AND sample_values IS NOT NULL
""")

# Minimum word length to index -- filters out noise words like "a", "of", etc.
_MIN_WORD_LENGTH = 3

# Fixed similarity score for LSH matches. Set slightly below vector matches'
# typical high range so that pure-vector high-confidence results rank above
# LSH results, but LSH results rank above low-vector results.
_LSH_SIMILARITY_SCORE = 0.85

# Common stop words to exclude from LSH indexing to reduce noise
_STOP_WORDS = frozenset({
    "the", "and", "for", "are", "but", "not", "you", "all", "any",
    "can", "had", "her", "was", "one", "our", "out", "has", "his",
    "how", "its", "may", "new", "now", "old", "see", "way", "who",
    "did", "get", "got", "let", "say", "she", "too", "use", "yes",
    "yet", "per", "via", "etc", "non", "pre", "sub", "total", "count",
    "many", "much", "each", "from", "with", "that", "this", "what",
    "when", "where", "which", "have", "been", "does", "will", "than",
    "them", "then", "they", "were", "more", "some", "into", "over",
})


class ColumnValueLSH:
    """MinHash-based approximate string matching for column values.

    Built at startup (or on first use) from categorical column sample values
    stored in ``wpo.jay_column_embeddings``. The index maps normalized value
    strings and their individual words to the columns that contain them.

    This catches abbreviations, typos, and value-level matches that pure
    embedding search may miss. For example, a query mentioning "Subproducer"
    will match the ``appointment_type`` column because "Subproducer" appears
    in its sample values.

    Thread-safe: building and querying are protected by a reentrant lock.

    Usage:
        lsh = get_lsh_index()      # module-level singleton
        lsh.build(db_session)       # call once at startup or lazily
        matches = lsh.query("active subproducers in Texas")
    """

    def __init__(self) -> None:
        self._index: Dict[str, List[Tuple[str, str, str, str]]] = {}
        # Each value: list of (schema_name, table_name, column_name, original_value)
        self._built = False
        self._lock = threading.RLock()
        self._entry_count = 0  # total (normalized_key -> entries) mappings

    @property
    def is_built(self) -> bool:
        """Whether the index has been populated."""
        return self._built

    @property
    def entry_count(self) -> int:
        """Total number of index keys."""
        with self._lock:
            return len(self._index)

    def build(self, db_session: Session) -> None:
        """Build the value index from categorical columns in jay_column_embeddings.

        Reads all categorical columns with non-null sample values, then
        indexes each value by its normalized form and individual words.

        Safe to call multiple times -- rebuilds from scratch each time.

        Args:
            db_session: Active SQLAlchemy session.
        """
        with self._lock:
            self._index.clear()
            self._entry_count = 0

            try:
                result = db_session.execute(_LSH_QUERY_SQL)
                rows = result.fetchall()
            except Exception:
                logger.exception("Failed to query categorical columns for LSH index")
                self._built = False
                return

            indexed_values = 0

            for row in rows:
                schema_name = row[0]
                table_name = row[1]
                column_name = row[2]
                values = row[3]  # JSONB array -> Python list

                if not values or not isinstance(values, (list, tuple)):
                    continue

                for val in values:
                    if not val or not isinstance(val, str):
                        continue

                    normalized = val.strip().lower()
                    if not normalized:
                        continue

                    entry = (schema_name, table_name, column_name, val)

                    # Index by full normalized value
                    self._index.setdefault(normalized, []).append(entry)
                    indexed_values += 1

                    # Also index individual words for partial matching
                    for word in normalized.split():
                        word = word.strip(".,;:!?()[]{}\"'")
                        if len(word) >= _MIN_WORD_LENGTH and word not in _STOP_WORDS:
                            self._index.setdefault(word, []).append(entry)

            self._entry_count = len(self._index)
            self._built = True

            logger.info(
                "LSH value index built: %d index keys from %d categorical values "
                "across %d rows",
                self._entry_count,
                indexed_values,
                len(rows),
            )

    def query(self, text_query: str, top_k: int = 10) -> List[ColumnMatch]:
        """Search the index for columns whose values match query terms.

        Tokenizes the query into words and checks each against the index.
        Results are deduplicated by (table, column) -- the first match for
        each column wins, accumulating matched sample values.

        Args:
            text_query: Natural-language query string.
            top_k: Maximum number of distinct (table, column) matches to return.

        Returns:
            List of ColumnMatch objects with ``match_source="lsh"``.
        """
        if not self._built:
            logger.debug("LSH index not built; returning empty results")
            return []

        words = text_query.strip().lower().split()
        if not words:
            return []

        # Deduplicate by (schema, table, column) and collect sample values
        # key: (schema, table, col) -> ColumnMatch
        matches: Dict[Tuple[str, str, str], ColumnMatch] = {}

        with self._lock:
            for word in words:
                word = word.strip(".,;:!?()[]{}\"'")
                if len(word) < _MIN_WORD_LENGTH or word in _STOP_WORDS:
                    continue

                entries = self._index.get(word)
                if not entries:
                    continue

                for schema_name, table_name, column_name, original_value in entries:
                    key = (schema_name, table_name, column_name)
                    if key in matches:
                        # Append the matched value if not already present
                        existing = matches[key]
                        if original_value not in existing.sample_values:
                            existing.sample_values.append(original_value)
                    else:
                        matches[key] = ColumnMatch(
                            schema_name=schema_name,
                            table_name=table_name,
                            column_name=column_name,
                            similarity=_LSH_SIMILARITY_SCORE,
                            match_source="lsh",
                            sample_values=[original_value],
                        )

        result = list(matches.values())[:top_k]

        logger.debug(
            "LSH query '%s' returned %d matches from %d query words",
            text_query[:80],
            len(result),
            len(words),
        )
        return result

    def rebuild_if_stale(self, db_session: Session) -> None:
        """Rebuild the index only if it has not been built yet.

        This is a convenience method for lazy initialization. If the index
        is already built, this is a no-op.
        """
        if not self._built:
            logger.info("LSH index not yet built; building now")
            self.build(db_session)


# Module-level singleton for the LSH index
_lsh_instance: Optional[ColumnValueLSH] = None
_lsh_instance_lock = threading.Lock()


def get_lsh_index() -> ColumnValueLSH:
    """Get or create the module-level LSH singleton.

    Thread-safe lazy initialization using double-checked locking.

    Returns:
        The shared ColumnValueLSH instance.
    """
    global _lsh_instance
    if _lsh_instance is not None:
        return _lsh_instance

    with _lsh_instance_lock:
        # Double-check after acquiring lock
        if _lsh_instance is None:
            _lsh_instance = ColumnValueLSH()
            logger.debug("Created LSH singleton instance")
        return _lsh_instance


# =============================================================================
# Task 3.3: Hybrid Merge + Table Scoring
# =============================================================================


def _merge_and_score(
    vector_matches: List[ColumnMatch],
    lsh_matches: List[ColumnMatch],
) -> List[ColumnMatch]:
    """Merge vector and LSH matches, deduplicate, and score tables.

    Deduplication strategy:
    - Key by (table_name, column_name).
    - When both sources match the same column, keep the one with higher
      similarity. Merge sample_values from both.

    Table scoring:
    - Each table's score is the sum of its top-3 column similarities.
    - Matches are sorted by table score (descending), then by individual
      column similarity (descending) within each table.

    Args:
        vector_matches: Matches from vector search.
        lsh_matches: Matches from LSH value search.

    Returns:
        Merged, deduplicated, and scored list of ColumnMatch objects.
    """
    # Step 1: Deduplicate by (table, column), keeping higher similarity
    merged: Dict[Tuple[str, str], ColumnMatch] = {}

    for match in vector_matches:
        key = (match.table_name, match.column_name)
        merged[key] = match

    for match in lsh_matches:
        key = (match.table_name, match.column_name)
        existing = merged.get(key)
        if existing is None:
            merged[key] = match
        else:
            # Keep the higher-similarity match
            if match.similarity > existing.similarity:
                # Preserve sample values from the existing match
                combined_values = list(existing.sample_values)
                for v in match.sample_values:
                    if v not in combined_values:
                        combined_values.append(v)
                match.sample_values = combined_values
                merged[key] = match
            else:
                # Keep existing but merge in new sample values
                for v in match.sample_values:
                    if v not in existing.sample_values:
                        existing.sample_values.append(v)

    # Step 2: Score tables by sum of top-3 column similarities
    table_columns: Dict[str, List[ColumnMatch]] = defaultdict(list)
    for match in merged.values():
        table_columns[match.table_name].append(match)

    table_scores: Dict[str, float] = {}
    for table_name, columns in table_columns.items():
        # Sort columns by similarity descending, take top 3
        top_sims = sorted(
            [c.similarity for c in columns],
            reverse=True,
        )[:3]
        table_scores[table_name] = sum(top_sims)

    # Step 3: Sort -- primary by table score (desc), secondary by
    # individual column similarity (desc)
    all_matches = list(merged.values())
    all_matches.sort(
        key=lambda m: (table_scores.get(m.table_name, 0.0), m.similarity),
        reverse=True,
    )

    if all_matches:
        logger.debug(
            "Merge produced %d unique columns across %d tables. "
            "Top table: %s (score=%.3f)",
            len(all_matches),
            len(table_scores),
            all_matches[0].table_name,
            table_scores.get(all_matches[0].table_name, 0.0),
        )

    return all_matches


# =============================================================================
# Task 3.1: Main Retrieval Function
# =============================================================================


def retrieve_schema_for_query(
    query: str,
    db_session: Session,
    join_graph: Optional[JoinGraph] = None,
    top_k: int = 25,
    threshold: float = 0.35,
    force_expand: bool = False,
    previous_context: str = None,
    seed_tables: set = None,
) -> SchemaRetrievalResult:
    """Retrieve the most relevant database columns and tables for a query.

    This is the main entry point for semantic schema discovery. It runs
    vector search and LSH value matching in sequence, merges results,
    scores tables, and optionally expands through the FK join graph.

    Args:
        query: Natural-language user query (e.g., "How many active agents
               in Texas?").
        db_session: Active SQLAlchemy session with access to the
                    ``wpo.jay_column_embeddings`` table and pgvector.
        join_graph: Pre-built JoinGraph from ``fk_inference.discover_join_graph``.
                    If provided, the selected table set will be expanded
                    through the graph to find bridge tables.
        top_k: Maximum vector search candidates (before threshold filter).
        threshold: Minimum cosine similarity for vector matches (0.0-1.0).
        force_expand: If True, run FK graph expansion even when only 1 table
                      is selected. Useful for multi-turn follow-ups where the
                      query references a single table but may need joins to
                      related tables from prior context.
        previous_context: Summary from the previous conversation turn. When
                          provided, it is prepended to the query for embedding
                          so that vector search captures multi-turn context.
        seed_tables: Additional table names to include in the selected table
                     set (e.g., tables from a previous SQL query). These are
                     unioned with tables discovered by vector+LSH search.

    Returns:
        SchemaRetrievalResult with matched columns, selected/expanded tables,
        join paths, timing, and match counts.

    Raises:
        RuntimeError: If the embedding API call fails (propagated from
                      ``embed_texts``).
    """
    t_start = time.monotonic()
    result = SchemaRetrievalResult()

    logger.info("Retrieving schema for query: %s", query[:120])

    # Build search query enriched with previous turn context for better
    # multi-turn vector search relevance.
    search_query = f"{previous_context} {query}" if previous_context else query

    # -----------------------------------------------------------------
    # Step 1: Get query embedding (cached)
    # -----------------------------------------------------------------
    try:
        query_embedding = _get_query_embedding(search_query)
    except Exception:
        logger.exception("Failed to embed query; returning empty result")
        result.retrieval_time_ms = (time.monotonic() - t_start) * 1000
        return result

    # -----------------------------------------------------------------
    # Step 2: Vector search against jay_column_embeddings
    # -----------------------------------------------------------------
    vector_matches = _vector_search(
        query_embedding,
        db_session,
        top_k=top_k,
        threshold=threshold,
    )
    result.vector_matches = len(vector_matches)

    # -----------------------------------------------------------------
    # Step 3: LSH value search
    # -----------------------------------------------------------------
    lsh = get_lsh_index()
    lsh.rebuild_if_stale(db_session)
    lsh_matches = lsh.query(query, top_k=top_k)
    result.lsh_matches = len(lsh_matches)

    # -----------------------------------------------------------------
    # Step 4: Merge + deduplicate + score tables
    # -----------------------------------------------------------------
    merged = _merge_and_score(vector_matches, lsh_matches)
    result.matched_columns = merged

    # Collect selected tables from matched columns
    result.selected_tables = {m.table_name for m in merged}

    # Union seed tables from previous turn SQL (multi-turn context)
    if seed_tables:
        added = seed_tables - result.selected_tables
        if added:
            logger.info("Seed tables added from previous SQL: %s", sorted(added))
        result.selected_tables = result.selected_tables | seed_tables

    # -----------------------------------------------------------------
    # Step 5: Expand tables via FK graph (if provided)
    # -----------------------------------------------------------------
    if join_graph is not None and (len(result.selected_tables) >= 2 or force_expand):
        try:
            expansion: ExpandedResult = expand_tables_via_joins(
                seed_tables=result.selected_tables,
                join_graph=join_graph,
                max_hops=2,
            )
            result.expanded_tables = expansion.expanded_tables
            result.join_paths = expansion.join_paths
        except Exception:
            logger.exception(
                "FK graph expansion failed for tables %s; "
                "proceeding with selected tables only",
                sorted(result.selected_tables),
            )
            result.expanded_tables = set(result.selected_tables)
    else:
        result.expanded_tables = set(result.selected_tables)

    # -----------------------------------------------------------------
    # Step 6: Finalize timing and log
    # -----------------------------------------------------------------
    result.retrieval_time_ms = (time.monotonic() - t_start) * 1000

    logger.info(
        "Schema retrieval complete in %.1fms: %d vector + %d LSH -> "
        "%d columns, %d tables (expanded to %d). %s",
        result.retrieval_time_ms,
        result.vector_matches,
        result.lsh_matches,
        len(result.matched_columns),
        len(result.selected_tables),
        len(result.expanded_tables),
        f"Join paths: {len(result.join_paths)}" if result.join_paths else "No joins needed",
    )

    return result
