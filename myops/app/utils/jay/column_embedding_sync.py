"""
JAI Column Embedding Sync - Incremental column profiling and embedding pipeline.

Compares live database column profiles against stored hashes in the
``wpo.jay_column_embeddings`` table and re-embeds only those columns
whose metadata has changed (or all columns when ``force=True``).

Public API:
- sync_column_embeddings(db_session, schema, force) -> dict
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timezone
from typing import Dict, List

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.utils.jay.column_profiler import (
    ColumnProfile,
    build_column_aliases_from_synonyms,
    build_column_embedding_text,
    profile_all_columns,
)
from app.utils.jay.llm_client import embed_texts

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Profile hashing
# ---------------------------------------------------------------------------

def _compute_profile_hash(profile: ColumnProfile) -> str:
    """Compute a deterministic SHA-256 hash of the profile's core fields.

    The hash captures the fields that influence the embedding text. When any
    of these change, the column's embedding must be regenerated.

    Args:
        profile: A ColumnProfile with metadata from the live database.

    Returns:
        64-character lowercase hex digest (SHA-256).
    """
    data = json.dumps(
        {
            "table": profile.table_name,
            "column": profile.column_name,
            "type": profile.data_type,
            "desc": profile.description,
            "vals": profile.most_common_vals[:30],
            "distinct": profile.distinct_count,
            "null_rate": round(profile.null_rate, 4),
            "min": str(profile.min_val),
            "max": str(profile.max_val),
        },
        sort_keys=True,
    )
    return hashlib.sha256(data.encode()).hexdigest()


# ---------------------------------------------------------------------------
# Existing-hash fetcher
# ---------------------------------------------------------------------------

def _fetch_existing_hashes(
    db_session: Session,
    schema: str,
) -> Dict[str, str]:
    """Load current profile_hash values from the embeddings table.

    Args:
        db_session: Active SQLAlchemy session.
        schema: Schema name to filter on.

    Returns:
        Dict mapping ``"table_name.column_name"`` to its stored
        ``profile_hash`` (or empty string if NULL).
    """
    sql = text("""
        SELECT table_name, column_name, COALESCE(profile_hash, '') AS profile_hash
        FROM wpo.jay_column_embeddings
        WHERE schema_name = :schema
    """)

    rows = db_session.execute(sql, {"schema": schema}).fetchall()
    return {
        f"{row[0]}.{row[1]}": row[2]
        for row in rows
    }


# ---------------------------------------------------------------------------
# Upsert helper
# ---------------------------------------------------------------------------

_UPSERT_SQL = text("""
    INSERT INTO wpo.jay_column_embeddings (
        schema_name,
        table_name,
        column_name,
        data_type,
        description,
        sample_values,
        distinct_count,
        null_rate,
        is_categorical,
        business_aliases,
        profile_hash,
        embedding_text,
        embedding,
        profiled_at,
        embedded_at
    ) VALUES (
        :schema_name,
        :table_name,
        :column_name,
        :data_type,
        :description,
        :sample_values,
        :distinct_count,
        :null_rate,
        :is_categorical,
        :business_aliases,
        :profile_hash,
        :embedding_text,
        :embedding,
        :profiled_at,
        :embedded_at
    )
    ON CONFLICT (schema_name, table_name, column_name) DO UPDATE SET
        data_type        = EXCLUDED.data_type,
        description      = EXCLUDED.description,
        sample_values    = EXCLUDED.sample_values,
        distinct_count   = EXCLUDED.distinct_count,
        null_rate        = EXCLUDED.null_rate,
        is_categorical   = EXCLUDED.is_categorical,
        business_aliases = EXCLUDED.business_aliases,
        profile_hash     = EXCLUDED.profile_hash,
        embedding_text   = EXCLUDED.embedding_text,
        embedding        = EXCLUDED.embedding,
        profiled_at      = EXCLUDED.profiled_at,
        embedded_at      = EXCLUDED.embedded_at
""")


def _build_upsert_params(
    profile: ColumnProfile,
    profile_hash: str,
    embedding_text: str,
    embedding: List[float],
    aliases: Dict[str, List[str]],
    now: datetime,
) -> dict:
    """Construct the parameter dict for a single upsert row."""
    col_aliases = aliases.get(profile.column_name, [])

    return {
        "schema_name": profile.schema_name,
        "table_name": profile.table_name,
        "column_name": profile.column_name,
        "data_type": profile.data_type,
        "description": profile.description,
        "sample_values": json.dumps(profile.most_common_vals[:50]),
        "distinct_count": profile.distinct_count,
        "null_rate": round(profile.null_rate, 4),
        "is_categorical": profile.is_categorical,
        "business_aliases": json.dumps(col_aliases),
        "profile_hash": profile_hash,
        "embedding_text": embedding_text,
        "embedding": str(embedding),  # pgvector accepts text '[0.1, 0.2, ...]'
        "profiled_at": now,
        "embedded_at": now,
    }


# ---------------------------------------------------------------------------
# Main sync function
# ---------------------------------------------------------------------------

def sync_column_embeddings(
    db_session: Session,
    schema: str = "wpo",
    force: bool = False,
) -> dict:
    """Profile all columns and sync embeddings for those that have changed.

    Workflow:
        1. Profile every column in *schema* via ``profile_all_columns``.
        2. Compute a SHA-256 hash for each profile.
        3. Compare against stored hashes in ``wpo.jay_column_embeddings``.
        4. Re-embed only the columns whose hash differs (or all if *force*).
        5. Upsert results into the embeddings table.

    Args:
        db_session: Active SQLAlchemy session.
        schema: PostgreSQL schema to profile (default ``"wpo"``).
        force: When ``True``, re-embed every column regardless of hash.

    Returns:
        Stats dict with keys:
            - ``total_columns``: number of columns profiled
            - ``changed_columns``: number of columns whose hash changed
            - ``embedded_columns``: number successfully embedded and upserted
            - ``skipped_columns``: number skipped (hash unchanged)
            - ``errors``: number of columns that failed during embedding
    """
    stats = {
        "total_columns": 0,
        "changed_columns": 0,
        "embedded_columns": 0,
        "skipped_columns": 0,
        "errors": 0,
    }

    try:
        # -----------------------------------------------------------------
        # Pre-check: Verify target table exists before expensive profiling
        # -----------------------------------------------------------------
        logger.info("Starting column embedding sync (schema=%s, force=%s)", schema, force)
        try:
            exists = db_session.execute(text(
                "SELECT EXISTS(SELECT 1 FROM information_schema.tables "
                "WHERE table_schema = :schema AND table_name = 'jay_column_embeddings')"
            ), {"schema": schema}).scalar()
            if not exists:
                logger.warning(
                    "Table %s.jay_column_embeddings does not exist; skipping column sync.", schema
                )
                return stats
        except Exception as e:
            logger.warning("Could not verify jay_column_embeddings exists: %s", e)
            return stats

        # -----------------------------------------------------------------
        # Step 1: Profile all columns
        # -----------------------------------------------------------------
        profiles = profile_all_columns(db_session, schema=schema)
        stats["total_columns"] = len(profiles)

        if not profiles:
            logger.warning("No columns profiled in schema '%s'; nothing to sync.", schema)
            return stats

        logger.info("Profiled %d columns in schema '%s'", len(profiles), schema)

        # -----------------------------------------------------------------
        # Step 2: Build aliases once (used for both embedding text + storage)
        # -----------------------------------------------------------------
        aliases = build_column_aliases_from_synonyms()

        # -----------------------------------------------------------------
        # Step 3: Compute hashes and determine which columns need embedding
        # -----------------------------------------------------------------
        existing_hashes = _fetch_existing_hashes(db_session, schema)

        changed_profiles: List[ColumnProfile] = []
        changed_hashes: List[str] = []

        for profile in profiles:
            new_hash = _compute_profile_hash(profile)
            key = f"{profile.table_name}.{profile.column_name}"
            old_hash = existing_hashes.get(key, "")

            if force or new_hash != old_hash:
                changed_profiles.append(profile)
                changed_hashes.append(new_hash)
            else:
                stats["skipped_columns"] += 1

        stats["changed_columns"] = len(changed_profiles)

        if not changed_profiles:
            logger.info("All %d column embeddings are up-to-date; nothing to sync.", stats["total_columns"])
            return stats

        logger.info(
            "%d of %d columns need re-embedding%s",
            len(changed_profiles),
            len(profiles),
            " (force=True)" if force else "",
        )

        # -----------------------------------------------------------------
        # Step 4: Build embedding texts for changed columns
        # -----------------------------------------------------------------
        embedding_texts: List[str] = [
            build_column_embedding_text(profile, aliases)
            for profile in changed_profiles
        ]

        # -----------------------------------------------------------------
        # Step 5: Generate embeddings (embed_texts handles batching)
        # -----------------------------------------------------------------
        embeddings: List[List[float]] = []
        batch_size = 100  # chunk into manageable groups for error isolation
        error_indices: set = set()

        for batch_start in range(0, len(embedding_texts), batch_size):
            batch_end = min(batch_start + batch_size, len(embedding_texts))
            batch_texts = embedding_texts[batch_start:batch_end]

            try:
                batch_embeddings = embed_texts(batch_texts)
                embeddings.extend(batch_embeddings)
            except Exception as e:
                logger.error(
                    "Embedding failed for batch [%d:%d] (%d texts): %s",
                    batch_start,
                    batch_end,
                    len(batch_texts),
                    e,
                )
                # Mark these indices as errored; fill with None placeholders
                for idx in range(batch_start, batch_end):
                    error_indices.add(idx)
                embeddings.extend([None] * len(batch_texts))  # type: ignore[list-item]

        # -----------------------------------------------------------------
        # Step 6: Upsert each successfully-embedded column
        # -----------------------------------------------------------------
        now = datetime.now(timezone.utc)
        upserted = 0

        for i, (profile, profile_hash, emb_text) in enumerate(
            zip(changed_profiles, changed_hashes, embedding_texts)
        ):
            if i in error_indices or embeddings[i] is None:
                stats["errors"] += 1
                continue

            try:
                params = _build_upsert_params(
                    profile=profile,
                    profile_hash=profile_hash,
                    embedding_text=emb_text,
                    embedding=embeddings[i],
                    aliases=aliases,
                    now=now,
                )
                db_session.execute(_UPSERT_SQL, params)
                upserted += 1
            except Exception as e:
                logger.error(
                    "Upsert failed for %s.%s.%s: %s",
                    profile.schema_name,
                    profile.table_name,
                    profile.column_name,
                    e,
                )
                stats["errors"] += 1

        # -----------------------------------------------------------------
        # Step 7: Commit all upserts in a single transaction
        # -----------------------------------------------------------------
        if upserted > 0:
            db_session.commit()
            logger.info(
                "Committed %d column embedding upserts to wpo.jay_column_embeddings",
                upserted,
            )

        stats["embedded_columns"] = upserted

        logger.info(
            "Column embedding sync complete: %d total, %d changed, "
            "%d embedded, %d skipped, %d errors",
            stats["total_columns"],
            stats["changed_columns"],
            stats["embedded_columns"],
            stats["skipped_columns"],
            stats["errors"],
        )

    except Exception as e:
        logger.exception("Fatal error during column embedding sync: %s", e)
        try:
            db_session.rollback()
        except Exception:
            logger.warning("Rollback also failed after fatal sync error")
        raise

    return stats
