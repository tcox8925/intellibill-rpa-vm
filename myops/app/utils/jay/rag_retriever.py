"""
JAI RAG Retriever - Semantic search over knowledge embeddings.

Provides targeted retrieval of synonyms, glossary entries, and query examples
from ``wpo.jay_knowledge_embeddings`` using pgvector cosine similarity with
HNSW index for fast approximate nearest-neighbor search. Falls back to
Python-side numpy cosine if pgvector operators are unavailable.

Public API:
- retrieve_synonyms(query, db_session, top_k=20) -> str
- retrieve_glossary(query, db_session, top_k=8) -> str
- retrieve_examples(query, db_session, top_k=5) -> str

All functions fall back to existing static context functions on any failure,
ensuring the pipeline never breaks due to embedding issues.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from typing import List, Optional, Tuple

import numpy as np
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.utils.jay.embedding_cache import (
    get_query_embedding as _unified_get_query_embedding,
    parse_embedding as _parse_embedding_shared,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# In-memory knowledge embedding cache (avoids DB round-trip on every query)
# ---------------------------------------------------------------------------

_knowledge_cache_lock = threading.Lock()
_knowledge_cache: dict[str, Tuple[float, list]] = {}  # category -> (loaded_at, rows)
_KNOWLEDGE_CACHE_TTL = 14400  # 4 hours (knowledge entries change infrequently)


def _get_cached_rows(category: str, db_session: Session, sql, params: dict = None) -> list:
    """Fetch rows for a category from cache or DB (5-min TTL)."""
    now = time.monotonic()
    with _knowledge_cache_lock:
        cached = _knowledge_cache.get(category)
        if cached and (now - cached[0]) < _KNOWLEDGE_CACHE_TTL:
            return cached[1]

    rows = db_session.execute(sql, params or {}).fetchall()
    rows_list = list(rows)

    with _knowledge_cache_lock:
        _knowledge_cache[category] = (time.monotonic(), rows_list)

    return rows_list


def invalidate_knowledge_cache():
    """Clear the in-memory knowledge embedding cache."""
    with _knowledge_cache_lock:
        _knowledge_cache.clear()


# ---------------------------------------------------------------------------
# Table existence check (cached — only runs once per process)
# ---------------------------------------------------------------------------

_table_exists_lock = threading.Lock()
_table_exists: Optional[bool] = None


def _check_table_exists(db_session: Session) -> bool:
    """Check if jay_knowledge_embeddings exists (cached per process)."""
    global _table_exists
    if _table_exists is not None:
        return _table_exists
    with _table_exists_lock:
        if _table_exists is not None:
            return _table_exists
        try:
            db_session.execute(text(
                "SELECT 1 FROM information_schema.tables "
                "WHERE table_schema = 'wpo' AND table_name = 'jay_knowledge_embeddings'"
            ))
            row = db_session.execute(text(
                "SELECT EXISTS(SELECT 1 FROM information_schema.tables "
                "WHERE table_schema = 'wpo' AND table_name = 'jay_knowledge_embeddings')"
            )).scalar()
            _table_exists = bool(row)
        except Exception:
            _table_exists = False
        return _table_exists


# ---------------------------------------------------------------------------
# Token estimation
# ---------------------------------------------------------------------------


def estimate_tokens(text_str: str) -> int:
    """Estimate token count (~4 chars per token for English text)."""
    if not text_str:
        return 0
    return max(1, len(text_str) // 4)


# ---------------------------------------------------------------------------
# Cosine similarity helpers (Python-side fallback)
# ---------------------------------------------------------------------------


def _batch_cosine_similarity(
    query_vec: List[float],
    rows: list,
    embedding_index: int,
) -> List[Tuple[float, int]]:
    """Compute cosine similarity for all rows at once using numpy.

    Returns list of (similarity, row_index) sorted descending.
    """
    embeddings = []
    valid_indices = []
    for i, row in enumerate(rows):
        emb = _parse_embedding(row[embedding_index])
        if emb is not None:
            embeddings.append(emb)
            valid_indices.append(i)

    if not embeddings:
        return []

    q = np.array(query_vec, dtype=np.float32)
    mat = np.array(embeddings, dtype=np.float32)

    # Batch cosine: dot / (norm_q * norms)
    dots = mat @ q
    norms = np.linalg.norm(mat, axis=1)
    norm_q = np.linalg.norm(q)
    denom = norms * norm_q
    denom[denom == 0] = 1.0
    sims = dots / denom

    # Return (similarity, original_row_index) sorted desc
    order = np.argsort(-sims)
    return [(float(sims[idx]), valid_indices[idx]) for idx in order]


def _parse_embedding(raw) -> Optional[List[float]]:
    """Parse embedding (delegates to unified cache)."""
    return _parse_embedding_shared(raw)


def _get_query_embedding(query: str) -> List[float]:
    """Embed a query string (delegates to unified embedding cache)."""
    return _unified_get_query_embedding(query)


# ---------------------------------------------------------------------------
# SQL templates (Python-side fallback)
# ---------------------------------------------------------------------------

_FETCH_BY_CATEGORY_SQL = text("""
    SELECT text, metadata, score_weight, embedding
    FROM wpo.jay_knowledge_embeddings
    WHERE category = :category
      AND is_active = TRUE
      AND embedding IS NOT NULL
""")

_FETCH_EXAMPLES_SQL = text("""
    SELECT text, metadata, score_weight, category, embedding
    FROM wpo.jay_knowledge_embeddings
    WHERE category IN ('example', 'learned')
      AND is_active = TRUE
      AND embedding IS NOT NULL
""")

# ---------------------------------------------------------------------------
# pgvector SQL templates (HNSW-accelerated cosine distance)
# ---------------------------------------------------------------------------

_PGVECTOR_SEARCH_BY_CATEGORY_SQL = text("""
    SELECT text, metadata, score_weight, embedding
    FROM wpo.jay_knowledge_embeddings
    WHERE category = :category
      AND is_active = TRUE
      AND embedding IS NOT NULL
    ORDER BY embedding <=> CAST(:query_embedding AS vector)
    LIMIT :top_k
""")

_PGVECTOR_SEARCH_EXAMPLES_SQL = text("""
    SELECT text, metadata, score_weight, category, embedding
    FROM wpo.jay_knowledge_embeddings
    WHERE category IN ('example', 'learned')
      AND is_active = TRUE
      AND embedding IS NOT NULL
    ORDER BY embedding <=> CAST(:query_embedding AS vector)
    LIMIT :top_k
""")

# ---------------------------------------------------------------------------
# pgvector availability check + search helper
# ---------------------------------------------------------------------------

_pgvector_available: Optional[bool] = None
_pgvector_check_lock = threading.Lock()


def _is_pgvector_available(db_session: Session) -> bool:
    """Check if pgvector operators work (cached per process)."""
    global _pgvector_available
    if _pgvector_available is not None:
        return _pgvector_available
    with _pgvector_check_lock:
        if _pgvector_available is not None:
            return _pgvector_available
        try:
            db_session.execute(text(
                "SELECT '[1,2,3]'::vector <=> '[1,2,3]'::vector"
            ))
            _pgvector_available = True
        except Exception:
            # CRITICAL: rollback to clear the failed transaction state,
            # otherwise all subsequent queries on this session will fail
            # with InFailedSqlTransaction.
            try:
                db_session.rollback()
            except Exception:
                pass
            _pgvector_available = False
            logger.info("pgvector operators not available, using Python cosine fallback")
        return _pgvector_available


def _pgvector_search(
    query_embedding: list,
    category_sql,
    params: dict,
    db_session: Session,
) -> Optional[list]:
    """Execute a pgvector similarity search. Returns None on failure."""
    try:
        embedding_str = "[" + ",".join(str(x) for x in query_embedding) + "]"
        params["query_embedding"] = embedding_str
        result = db_session.execute(category_sql, params)
        return result.fetchall()
    except Exception as e:
        try:
            db_session.rollback()
        except Exception:
            pass
        logger.debug("pgvector search failed, will use Python fallback: %s", e)
        return None


# ---------------------------------------------------------------------------
# Synonym retrieval
# ---------------------------------------------------------------------------

def retrieve_synonyms(
    query: str,
    db_session: Session,
    top_k: int = 20,
) -> str:
    """Retrieve the most relevant synonym mappings for a user query."""
    try:
        if not _check_table_exists(db_session):
            return _fallback_synonym_context()

        query_vec = _get_query_embedding(query)

        # Try pgvector first (uses HNSW index, ~1ms)
        rows = None
        if _is_pgvector_available(db_session):
            rows = _pgvector_search(
                query_vec,
                _PGVECTOR_SEARCH_BY_CATEGORY_SQL,
                {"category": "synonym", "top_k": top_k},
                db_session,
            )

        if rows is not None:
            top_results = [(None, row) for row in rows]
        else:
            # Fallback: Python-side cosine with cached embeddings
            cached_rows = _get_cached_rows("synonym", db_session, _FETCH_BY_CATEGORY_SQL, {"category": "synonym"})
            if not cached_rows:
                return _fallback_synonym_context()
            ranked = _batch_cosine_similarity(query_vec, cached_rows, embedding_index=3)
            top_results = [(sim, cached_rows[idx]) for sim, idx in ranked[:top_k]]

        if not top_results:
            return _fallback_synonym_context()

        lines = ["Business Term -> Database Column:"]
        for _sim, row in top_results:
            metadata = row[1]
            if isinstance(metadata, str):
                metadata = json.loads(metadata)
            term = metadata.get("term", "")
            db_column = metadata.get("db_column", "")
            if term and db_column:
                lines.append(f'  "{term}" = column "{db_column}"')

        result_text = "\n".join(lines)

        full_context_tokens = estimate_tokens(_fallback_synonym_context())
        rag_context_tokens = estimate_tokens(result_text)
        savings_pct = round((1 - rag_context_tokens / max(full_context_tokens, 1)) * 100, 1)
        logger.info(
            "RAG synonyms: %d tokens (vs %d static, %.1f%% savings)",
            rag_context_tokens, full_context_tokens, savings_pct,
        )

        return result_text

    except Exception as e:
        logger.warning("Synonym RAG retrieval failed, falling back to static: %s", e)
        return _fallback_synonym_context()


def _fallback_synonym_context() -> str:
    """Fall back to the full static synonym injection."""
    from app.utils.jay.knowledge_cache import get_synonym_context
    return get_synonym_context()


# ---------------------------------------------------------------------------
# Glossary retrieval
# ---------------------------------------------------------------------------

def retrieve_glossary(
    query: str,
    db_session: Session,
    top_k: int = 8,
) -> str:
    """Retrieve the most relevant glossary entries for a user query."""
    try:
        if not _check_table_exists(db_session):
            return _fallback_glossary_context()

        query_vec = _get_query_embedding(query)

        # Try pgvector first (uses HNSW index, ~1ms)
        rows = None
        if _is_pgvector_available(db_session):
            rows = _pgvector_search(
                query_vec,
                _PGVECTOR_SEARCH_BY_CATEGORY_SQL,
                {"category": "glossary", "top_k": top_k},
                db_session,
            )

        if rows is not None:
            top_results = [(None, row) for row in rows]
        else:
            # Fallback: Python-side cosine with cached embeddings
            cached_rows = _get_cached_rows("glossary", db_session, _FETCH_BY_CATEGORY_SQL, {"category": "glossary"})
            if not cached_rows:
                return _fallback_glossary_context()
            ranked = _batch_cosine_similarity(query_vec, cached_rows, embedding_index=3)
            top_results = [(sim, cached_rows[idx]) for sim, idx in ranked[:top_k]]

        if not top_results:
            return _fallback_glossary_context()

        lines = ["BUSINESS GLOSSARY (concept -> SQL mapping):"]
        for _sim, row in top_results:
            metadata = row[1]
            if isinstance(metadata, str):
                metadata = json.loads(metadata)
            term = metadata.get("term", "")
            description = metadata.get("description", "")
            sql_hint = metadata.get("sql_hint", "")
            if term:
                lines.append(f'  "{term}": {description} | SQL: {sql_hint}')

        result_text = "\n".join(lines)

        full_context_tokens = estimate_tokens(_fallback_glossary_context())
        rag_context_tokens = estimate_tokens(result_text)
        savings_pct = round((1 - rag_context_tokens / max(full_context_tokens, 1)) * 100, 1)
        logger.info(
            "RAG glossary: %d tokens (vs %d static, %.1f%% savings)",
            rag_context_tokens, full_context_tokens, savings_pct,
        )

        return result_text

    except Exception as e:
        logger.warning("Glossary RAG retrieval failed, falling back to static: %s", e)
        return _fallback_glossary_context()


def _fallback_glossary_context() -> str:
    """Fall back to the full static glossary injection."""
    from app.utils.jay.knowledge_cache import get_business_glossary_context
    return get_business_glossary_context()


# ---------------------------------------------------------------------------
# Example retrieval
# ---------------------------------------------------------------------------

def retrieve_examples(
    query: str,
    db_session: Session,
    top_k: int = 5,
) -> str:
    """Retrieve the most relevant query examples for a user query."""
    try:
        if not _check_table_exists(db_session):
            return ""

        query_vec = _get_query_embedding(query)

        # Try pgvector first (uses HNSW index, ~1ms)
        pgvector_rows = None
        if _is_pgvector_available(db_session):
            # Fetch more than top_k so score_weight reranking has room
            pgvector_rows = _pgvector_search(
                query_vec,
                _PGVECTOR_SEARCH_EXAMPLES_SQL,
                {"top_k": top_k * 3},
                db_session,
            )

        if pgvector_rows is not None:
            # Apply score_weight reranking on pgvector results
            weighted = []
            for row in pgvector_rows:
                score_weight = float(row[2]) if row[2] is not None else 1.0
                category = row[3]
                # pgvector returns in distance order; use inverse rank as base score
                base_score = 1.0
                weighted_score = base_score * score_weight if category == "learned" else base_score
                weighted.append((weighted_score, row))
            # Learned examples with higher score_weight bubble up
            weighted.sort(key=lambda x: x[0], reverse=True)
            top_results = weighted[:top_k]
        else:
            # Fallback: Python-side cosine with cached embeddings
            cached_rows = _get_cached_rows("examples", db_session, _FETCH_EXAMPLES_SQL)
            if not cached_rows:
                return ""

            # Examples have embedding at index 4, and need score_weight weighting
            ranked = _batch_cosine_similarity(query_vec, cached_rows, embedding_index=4)

            # Apply score_weight for learned examples
            weighted = []
            for sim, idx in ranked:
                row = cached_rows[idx]
                score_weight = float(row[2]) if row[2] is not None else 1.0
                category = row[3]
                weighted_score = sim * score_weight if category == "learned" else sim
                weighted.append((weighted_score, row))

            weighted.sort(key=lambda x: x[0], reverse=True)
            top_results = weighted[:top_k]

        if not top_results:
            return ""

        parts = ["-- RELEVANT QUERY EXAMPLES"]
        learned_count = 0
        for _score, row in top_results:
            raw_text = row[0]
            metadata = row[1]
            category = row[3]

            if isinstance(metadata, str):
                metadata = json.loads(metadata)

            name = metadata.get("name", metadata.get("natural_query", ""))
            sql_text = metadata.get("sql_text", "")

            if category == "learned":
                learned_count += 1

            if sql_text:
                label = " (learned)" if category == "learned" else ""
                parts.append(f"\n-- Example: {name}{label}\n{sql_text}")
            elif raw_text:
                parts.append(f"\n{raw_text}")

        result_text = "\n".join(parts)

        rag_context_tokens = estimate_tokens(result_text)
        logger.info(
            "RAG examples: %d tokens, %d examples (%d learned, %d curated)",
            rag_context_tokens, len(top_results), learned_count,
            len(top_results) - learned_count,
        )

        return result_text

    except Exception as e:
        logger.warning("Example RAG retrieval failed: %s", e)
        return ""


# ---------------------------------------------------------------------------
# Startup warmup
# ---------------------------------------------------------------------------

_FETCH_ALL_KNOWLEDGE_SQL = text("""
    SELECT text, metadata, score_weight, category, embedding
    FROM wpo.jay_knowledge_embeddings
    WHERE is_active = TRUE
      AND embedding IS NOT NULL
      AND category IN ('synonym', 'glossary', 'example', 'learned')
""")


def warmup_knowledge_cache(db_session: Session) -> dict:
    """Pre-load all knowledge embedding categories in a SINGLE DB query.

    Previous implementation ran 3 separate queries (synonym, glossary, examples).
    This merges them into one round-trip, then partitions in-memory.

    Call during startup to eliminate cold-start DB round-trips on first query.
    Returns dict with counts per category.
    """
    if not _check_table_exists(db_session):
        logger.info("Knowledge embeddings table not found, skipping warmup")
        return {}

    try:
        t0 = time.monotonic()
        all_rows = db_session.execute(_FETCH_ALL_KNOWLEDGE_SQL).fetchall()

        # Partition rows by category
        synonym_rows = []
        glossary_rows = []
        example_rows = []

        for row in all_rows:
            cat = row[3]  # category column at index 3
            if cat == "synonym":
                # _FETCH_BY_CATEGORY_SQL returns (text, metadata, score_weight, embedding)
                synonym_rows.append((row[0], row[1], row[2], row[4]))
            elif cat == "glossary":
                glossary_rows.append((row[0], row[1], row[2], row[4]))
            elif cat in ("example", "learned"):
                # _FETCH_EXAMPLES_SQL returns (text, metadata, score_weight, category, embedding)
                example_rows.append(row)

        # Store in cache
        now = time.monotonic()
        with _knowledge_cache_lock:
            _knowledge_cache["synonym"] = (now, synonym_rows)
            _knowledge_cache["glossary"] = (now, glossary_rows)
            _knowledge_cache["examples"] = (now, example_rows)

        elapsed_ms = (time.monotonic() - t0) * 1000
        stats = {
            "synonym": len(synonym_rows),
            "glossary": len(glossary_rows),
            "examples": len(example_rows),
        }
        logger.info(
            "Knowledge cache warmed in 1 query (%.0fms): %s",
            elapsed_ms, stats,
        )
        return stats

    except Exception as e:
        logger.warning("Unified knowledge warmup failed, trying per-category: %s", e)
        # Fallback to individual queries
        stats = {}
        for category, sql, params in [
            ("synonym", _FETCH_BY_CATEGORY_SQL, {"category": "synonym"}),
            ("glossary", _FETCH_BY_CATEGORY_SQL, {"category": "glossary"}),
            ("examples", _FETCH_EXAMPLES_SQL, None),
        ]:
            try:
                rows = _get_cached_rows(category, db_session, sql, params or {})
                stats[category] = len(rows)
            except Exception as ex:
                logger.warning("Failed to warmup %s cache: %s", category, ex)
                stats[category] = 0
        return stats
