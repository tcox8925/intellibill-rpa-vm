"""
JAI Semantic Query Cache - Store and retrieve successful query/SQL pairs.

Manages the ``wpo.jay_query_cache`` table, enabling the pipeline to:
1. Detect near-duplicate queries and reuse existing SQL (avoiding LLM cost).
2. Record execution statistics so the most-used, highest-success queries
   can be surfaced as high-confidence suggestions.
3. Update rolling statistics (execution count, avg exec time, success/failure)
   without blocking the response path.

Public API:
- store_successful_query(query, spec, sql, module, domains, exec_ms, db_session) -> int | None
- find_similar_queries(query, db_session, top_k, threshold) -> list[dict]
- find_exact_match(query, db_session, threshold) -> dict | None
- update_query_stats(cache_id, success, exec_ms, db_session) -> None

All functions catch their own exceptions and return gracefully so that a
cache failure never breaks the pipeline.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from typing import List, Optional

import numpy as np

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.utils.jay.embedding_cache import (
    get_query_embedding as _unified_get_query_embedding,
    parse_embedding as _parse_embedding_shared,
)

logger = logging.getLogger(__name__)


def _parse_embedding(raw) -> Optional[List[float]]:
    """Parse embedding (delegates to unified cache)."""
    return _parse_embedding_shared(raw)


def _get_query_embedding(query: str) -> List[float]:
    """Embed a query string (delegates to unified embedding cache)."""
    return _unified_get_query_embedding(query)


# ---------------------------------------------------------------------------
# In-memory cache for query cache rows (avoids DB round-trip on every search)
# ---------------------------------------------------------------------------
_qc_cache_lock = threading.Lock()
_qc_cache: Optional[dict] = None  # {"rows": [...], "embeddings": np.array, "valid_indices": [...], "norms": np.array}
_qc_cache_time: float = 0.0
_QC_CACHE_TTL = 7200  # 2 hours (query patterns are stable within sessions)


def _get_cached_query_rows(db_session: Session) -> dict:
    """Load and cache query cache rows with pre-parsed numpy matrix."""
    global _qc_cache, _qc_cache_time
    now = time.monotonic()

    with _qc_cache_lock:
        if _qc_cache is not None and (now - _qc_cache_time) < _QC_CACHE_TTL:
            return _qc_cache

    try:
        rows = db_session.execute(_FETCH_ALL_EMBEDDINGS_SQL).fetchall()
    except Exception:
        logger.exception("Failed to fetch query cache embeddings")
        return {"rows": [], "embeddings": None, "valid_indices": [], "norms": None}

    embeddings = []
    valid_indices = []
    for i, row in enumerate(rows):
        emb = _parse_embedding(row[6])  # embedding is at index 6
        if emb is not None:
            embeddings.append(emb)
            valid_indices.append(i)

    mat = None
    norms = None
    if embeddings:
        mat = np.array(embeddings, dtype=np.float32)
        norms = np.linalg.norm(mat, axis=1)

    cache = {
        "rows": rows,
        "embeddings": mat,
        "valid_indices": valid_indices,
        "norms": norms,
    }

    with _qc_cache_lock:
        _qc_cache = cache
        _qc_cache_time = time.monotonic()

    logger.info("Query cache loaded: %d rows, %d with embeddings", len(rows), len(valid_indices))
    return cache


def invalidate_query_row_cache():
    """Clear the in-memory query cache row cache. Called after storing new queries."""
    global _qc_cache, _qc_cache_time
    with _qc_cache_lock:
        _qc_cache = None
        _qc_cache_time = 0.0


# ---------------------------------------------------------------------------
# SQL templates
# ---------------------------------------------------------------------------

_INSERT_QUERY_SQL = text("""
    INSERT INTO wpo.jay_query_cache (
        query_text,
        technical_spec,
        sql_text,
        module,
        domains,
        embedding,
        execution_count,
        avg_exec_ms,
        last_used_at,
        success_count,
        failure_count,
        created_at,
        updated_at
    ) VALUES (
        :query_text,
        :technical_spec,
        :sql_text,
        :module,
        :domains,
        :embedding,
        1,
        :avg_exec_ms,
        NOW(),
        1,
        0,
        NOW(),
        NOW()
    )
    RETURNING id
""")

_FETCH_ALL_EMBEDDINGS_SQL = text("""
    SELECT id, query_text, sql_text, module, success_count, execution_count, embedding
    FROM wpo.jay_query_cache
    WHERE embedding IS NOT NULL
""")

_UPDATE_STATS_SUCCESS_SQL = text("""
    UPDATE wpo.jay_query_cache
    SET
        execution_count = execution_count + 1,
        avg_exec_ms     = (avg_exec_ms * execution_count + :exec_ms) / (execution_count + 1),
        success_count   = success_count + 1,
        last_used_at    = NOW(),
        updated_at      = NOW()
    WHERE id = :cache_id
""")

_UPDATE_STATS_FAILURE_SQL = text("""
    UPDATE wpo.jay_query_cache
    SET
        execution_count = execution_count + 1,
        avg_exec_ms     = (avg_exec_ms * execution_count + :exec_ms) / (execution_count + 1),
        failure_count   = failure_count + 1,
        last_used_at    = NOW(),
        updated_at      = NOW()
    WHERE id = :cache_id
""")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def store_successful_query(
    query: str,
    spec: Optional[str],
    sql: str,
    module: Optional[str],
    domains: Optional[List[str]],
    exec_ms: float,
    db_session: Session,
) -> Optional[int]:
    """Embed and store a successful query/SQL pair in the cache.

    The caller is responsible for checking ``find_similar_queries`` first if
    deduplication is desired. This function always INSERTs a new row.

    Args:
        query:      The natural-language user question.
        spec:       Optional technical specification / intent summary.
        sql:        The generated SQL that produced a successful result.
        module:     The JAI module used (e.g. "agents", "commissions").
        domains:    List of domain names resolved during intent detection.
        exec_ms:    Execution time in milliseconds for the query.
        db_session: Active SQLAlchemy session.

    Returns:
        The new cache row ``id`` (integer), or ``None`` on any failure.
    """
    if not query or not sql:
        logger.warning("store_successful_query called with empty query or SQL; skipping.")
        return None

    try:
        embedding = _get_query_embedding(query)
        embedding_str = json.dumps(embedding)  # stored as JSON text

        domains_list = domains or []

        result = db_session.execute(
            _INSERT_QUERY_SQL,
            {
                "query_text": query[:2000],       # guard against runaway text
                "technical_spec": spec,
                "sql_text": sql,
                "module": module,
                "domains": domains_list,           # SQLAlchemy binds list -> TEXT[]
                "avg_exec_ms": float(exec_ms),
                "embedding": embedding_str,
            },
        )

        row = result.fetchone()
        if row is None:
            logger.error("INSERT INTO jay_query_cache returned no row.")
            db_session.rollback()
            return None

        cache_id = int(row[0])
        db_session.commit()
        invalidate_query_row_cache()  # Force refresh on next search

        logger.info(
            "Stored query in cache (id=%d, module=%s, exec_ms=%.1f): %.80s",
            cache_id,
            module,
            exec_ms,
            query,
        )
        return cache_id

    except Exception as e:
        logger.warning("Failed to store query in cache: %s — query: %.80s", e, query)
        try:
            db_session.rollback()
        except Exception:
            pass
        return None


def find_similar_queries(
    query: str,
    db_session: Session,
    top_k: int = 3,
    threshold: float = 0.80,
) -> List[dict]:
    """Search the query cache for semantically similar past queries.

    Embeds *query* and performs a pgvector cosine similarity search against
    ``wpo.jay_query_cache``. Only rows whose similarity meets *threshold*
    are returned.

    Args:
        query:      The natural-language question to look up.
        db_session: Active SQLAlchemy session.
        top_k:      Maximum number of results to return (after threshold
                    filtering). The underlying SQL query fetches ``top_k * 3``
                    candidates before filtering so the threshold can be applied
                    server-side in Python without a second round-trip.
        threshold:  Minimum cosine similarity (0–1) to include a result.

    Returns:
        List of dicts (may be empty), each with keys:
            ``id``, ``query``, ``sql``, ``module``, ``similarity``,
            ``success_count``, ``execution_count``.
        Ordered by similarity descending.
    """
    if not query:
        return []

    try:
        query_embedding = _get_query_embedding(query)
        cache = _get_cached_query_rows(db_session)
        rows = cache["rows"]
        mat = cache["embeddings"]
        valid_indices = cache["valid_indices"]
        norms = cache["norms"]

        if mat is None or len(valid_indices) == 0:
            return []

        # Numpy batch cosine similarity
        q = np.array(query_embedding, dtype=np.float32)
        norm_q = np.linalg.norm(q)
        if norm_q == 0:
            return []

        dots = mat @ q
        denom = norms * norm_q
        denom[denom == 0] = 1.0
        sims = dots / denom

        # Filter by threshold and get top_k
        results = []
        order = np.argsort(-sims)
        for idx in order:
            sim = float(sims[idx])
            if sim < threshold:
                break
            row_idx = valid_indices[idx]
            row = rows[row_idx]
            row_id, row_query, row_sql, row_module, row_success, row_exec_count, _ = row
            results.append({
                "id": int(row_id),
                "query": row_query,
                "sql": row_sql,
                "module": row_module,
                "similarity": round(sim, 4),
                "success_count": int(row_success) if row_success is not None else 0,
                "execution_count": int(row_exec_count) if row_exec_count is not None else 0,
            })
            if len(results) >= top_k:
                break

        logger.debug(
            "Query cache search: %d results above threshold=%.3f for: %.60s",
            len(results),
            threshold,
            query,
        )
        return results

    except Exception as e:
        logger.warning("find_similar_queries failed: %s — query: %.80s", e, query)
        return []


def find_exact_match(
    query: str,
    db_session: Session,
    threshold: float = 0.995,
) -> Optional[dict]:
    """Return a near-exact cached query match, or ``None`` if not found.

    Uses the same vector search as ``find_similar_queries`` but with a
    very high similarity threshold (default 0.995) to ensure only
    practically identical queries reuse cached SQL.

    Additionally filters out rows where ``success_count == 0`` so that
    a query whose previous SQL produced no results is never blindly reused.

    Args:
        query:      The natural-language question to look up.
        db_session: Active SQLAlchemy session.
        threshold:  Minimum cosine similarity to accept as an exact match.
                    Default 0.995 means only near-identical phrasing qualifies.

    Returns:
        A dict with the same keys as ``find_similar_queries`` results, or
        ``None`` if no qualifying match exists.
    """
    if not query:
        return None

    try:
        candidates = find_similar_queries(
            query=query,
            db_session=db_session,
            top_k=1,
            threshold=threshold,
        )

        if not candidates:
            return None

        match = candidates[0]

        # Do not reuse SQL from queries that have never succeeded.
        if match["success_count"] == 0:
            logger.debug(
                "Exact cache match found (id=%d, similarity=%.4f) but success_count=0; ignoring.",
                match["id"],
                match["similarity"],
            )
            return None

        logger.info(
            "Exact cache match (id=%d, similarity=%.4f, success=%d): %.60s",
            match["id"],
            match["similarity"],
            match["success_count"],
            query,
        )
        return match

    except Exception as e:
        logger.warning("find_exact_match failed: %s — query: %.80s", e, query)
        return None


def update_query_stats(
    cache_id: int,
    success: bool,
    exec_ms: float,
    db_session: Session,
) -> None:
    """Update execution statistics for a cached query.

    Increments ``execution_count``, updates the running-average
    ``avg_exec_ms``, and bumps either ``success_count`` or
    ``failure_count`` depending on the outcome.

    The running-average formula avoids storing the full history:
        new_avg = (old_avg * old_count + new_value) / (old_count + 1)
    This is computed atomically in SQL to prevent race conditions when
    the same cached query is running concurrently.

    Args:
        cache_id:   The ``id`` of the row in ``wpo.jay_query_cache``.
        success:    ``True`` if the query produced results; ``False`` if it
                    failed (empty result, SQL error, etc.).
        exec_ms:    Wall-clock execution time in milliseconds for this run.
        db_session: Active SQLAlchemy session.
    """
    if cache_id is None:
        return

    try:
        update_sql = _UPDATE_STATS_SUCCESS_SQL if success else _UPDATE_STATS_FAILURE_SQL
        db_session.execute(update_sql, {"cache_id": cache_id, "exec_ms": float(exec_ms)})
        db_session.commit()

        logger.debug(
            "Updated cache stats (id=%d, success=%s, exec_ms=%.1f)",
            cache_id,
            success,
            exec_ms,
        )

    except Exception as e:
        logger.warning("update_query_stats failed for id=%s: %s", cache_id, e)
        try:
            db_session.rollback()
        except Exception:
            pass


def warmup_query_cache(db_session: Session) -> dict:
    """Pre-load query cache embeddings into memory.

    Call during startup to eliminate cold-start latency.
    """
    cache = _get_cached_query_rows(db_session)
    rows_count = len(cache.get("rows", []))
    valid_count = len(cache.get("valid_indices", []))
    logger.info("Query cache warmed: %d rows, %d with embeddings", rows_count, valid_count)
    return {"total_rows": rows_count, "with_embeddings": valid_count}
