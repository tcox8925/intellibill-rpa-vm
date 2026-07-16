"""
JAI Embedding Cache - Unified query embedding cache shared across all pipeline modules.

Provides a single thread-safe cache for query embeddings, eliminating duplicate
embedding API calls across column_retriever, rag_retriever, and query_cache.

Also provides shared utility functions for cosine similarity and embedding parsing.
"""

import json
import logging
import math
import threading
from typing import List, Optional

import numpy as np

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Unified query embedding cache (thread-safe, shared across all modules)
# ---------------------------------------------------------------------------

_query_cache_lock = threading.Lock()
_query_cache: dict[str, List[float]] = {}
_MAX_CACHE_SIZE = 1024  # Shared across all modules, generous for long sessions


def get_query_embedding(query: str) -> List[float]:
    """Embed a query string with thread-safe in-memory caching.

    Normalizes the query to lowercase/stripped before caching so that
    trivial variations share a single cached embedding.

    Thread-safe: releases the lock during the embedding API call.

    Args:
        query: Natural-language query string.

    Returns:
        Embedding vector (list of floats, typically 1536-dim).

    Raises:
        RuntimeError: If the embedding API call fails.
    """
    normalized = query.strip().lower()

    with _query_cache_lock:
        cached = _query_cache.get(normalized)
        if cached is not None:
            return cached

    from app.utils.jay.llm_client import embed_texts

    vectors = embed_texts([normalized])
    embedding = vectors[0]

    with _query_cache_lock:
        if len(_query_cache) >= _MAX_CACHE_SIZE:
            oldest_key = next(iter(_query_cache))
            del _query_cache[oldest_key]
        _query_cache[normalized] = embedding

    return embedding


def clear_query_cache() -> int:
    """Clear the query embedding cache. Returns count of evicted entries."""
    with _query_cache_lock:
        count = len(_query_cache)
        _query_cache.clear()
    logger.info("Cleared unified query embedding cache (%d entries)", count)
    return count


# ---------------------------------------------------------------------------
# Shared utility functions
# ---------------------------------------------------------------------------


def cosine_similarity(a: List[float], b: List[float]) -> float:
    """Compute cosine similarity between two vectors using pure Python."""
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


def parse_embedding(raw) -> Optional[List[float]]:
    """Parse an embedding from TEXT column (JSON array string)."""
    if raw is None:
        return None
    if isinstance(raw, list):
        return raw
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return None


def batch_cosine_similarity(
    query_vec: List[float],
    embedding_matrix: np.ndarray,
    norms: np.ndarray,
) -> np.ndarray:
    """Compute cosine similarity for all rows at once using numpy.

    Args:
        query_vec: Query embedding vector.
        embedding_matrix: Pre-parsed numpy matrix of embeddings (N x dim).
        norms: Pre-computed L2 norms of each row.

    Returns:
        Array of cosine similarities (N,), or empty array if inputs invalid.
    """
    if embedding_matrix is None or len(embedding_matrix) == 0:
        return np.array([])

    q = np.array(query_vec, dtype=np.float32)
    norm_q = np.linalg.norm(q)
    if norm_q == 0:
        return np.zeros(len(embedding_matrix))

    dots = embedding_matrix @ q
    denom = norms * norm_q
    denom[denom == 0] = 1.0
    return dots / denom
