"""
JAI Entity Alias Cache — Self-learning alias resolution via LLM.

Provides a persistent, self-maintaining cache that maps entity
aliases/abbreviations to their canonical database values.
Uses LLM expansion as fallback for unknown aliases, then caches
successful resolutions permanently.

Architecture:
  1. In-memory dict-of-dicts for O(1) lookup
  2. PostgreSQL backing table (jay_entity_aliases) for persistence
  3. LLM expansion for unknown aliases (GPT-4o-mini / primary deployment)
  4. Self-learning: successful LLM expansions auto-cached

Public API:
  - lookup_alias(entity_type, value) -> str | None
  - expand_with_llm(entity_type, value, user_question) -> str | None
  - learn_alias(entity_type, alias_value, canonical_value, db_session, source) -> None
  - warmup(db_session) -> dict
  - invalidate() -> None
"""

from __future__ import annotations

import json
import logging
import threading
import time
from typing import Dict, List, Optional

from sqlalchemy import text
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# In-memory cache: {entity_type: {normalized_alias: canonical_value}}
# ---------------------------------------------------------------------------
_cache_lock = threading.Lock()
_alias_cache: Optional[Dict[str, Dict[str, str]]] = None
_cache_loaded_at: float = 0.0


def _normalize(value: str) -> str:
    """Normalize alias for lookup: lowercase, strip, remove dots/hyphens."""
    return value.strip().lower().replace(".", "").replace("-", " ")


# ---------------------------------------------------------------------------
# Lookup (O(1) in-memory)
# ---------------------------------------------------------------------------

def lookup_alias(entity_type: str, value: str) -> Optional[str]:
    """Check the in-memory alias cache for a known mapping.

    Returns the canonical DB value if found, or None.
    """
    if _alias_cache is None:
        return None
    normalized = _normalize(value)
    canonical = _alias_cache.get(entity_type, {}).get(normalized)
    if canonical:
        logger.debug("Alias cache hit: %s/%s → %s", entity_type, value, canonical)
    return canonical


# ---------------------------------------------------------------------------
# LLM Expansion (fallback for unknown aliases)
# ---------------------------------------------------------------------------

_TYPE_DESCRIPTIONS = {
    "carrier_name": "insurance carrier or insurance company",
    "agent": "insurance agent or producer",
    "pch_provider": "healthcare provider or doctor",
    "pch_member": "health plan member or patient",
    "commission_member": "insurance policy holder or insured member",
}


def expand_with_llm(
    entity_type: str,
    value: str,
    user_question: str,
    candidate_values: Optional[List[str]] = None,
) -> Optional[str]:
    """Use the LLM to expand an abbreviation/alias to its canonical form.

    If candidate_values are provided, the LLM selects from the closed list
    (prevents hallucination). Otherwise, it generates an expansion that the
    caller must validate against the DB.

    Returns the expanded name or None if the LLM cannot determine it.
    """
    try:
        from app.utils.jay.llm_client import get_llm_client

        client, deployment = get_llm_client()
        type_desc = _TYPE_DESCRIPTIONS.get(entity_type, entity_type)

        if candidate_values:
            # Closed-list selection (preferred — prevents hallucination)
            candidates_text = "\n".join(
                f"  {i+1}. {v}" for i, v in enumerate(candidate_values[:20])
            )
            prompt = (
                f'You are an entity resolver for an insurance operations database.\n'
                f'The user asked: "{user_question}"\n'
                f'They mentioned "{value}" which appears to be a {type_desc}.\n\n'
                f'Select the best matching value from this list:\n{candidates_text}\n\n'
                f'Rules:\n'
                f'- You MUST select from the list above or return null.\n'
                f'- Do NOT invent any value not in the list.\n'
                f'- Consider abbreviations, acronyms, alternate spellings, and common aliases.\n'
                f'- Use the full question context to resolve ambiguity.\n\n'
                f'Return JSON: {{"selected_value": "<exact value from list or null>", '
                f'"confidence": <float 0.0-1.0>}}'
            )
        else:
            # Open expansion (when no candidates available)
            prompt = (
                f'You are an entity name resolver for an insurance operations database.\n'
                f'The user asked: "{user_question}"\n'
                f'They mentioned "{value}" which appears to be a {type_desc} '
                f'name, abbreviation, or alias.\n\n'
                f'What is the most likely full/canonical name for "{value}"?\n\n'
                f'Rules:\n'
                f'- Return ONLY the expanded canonical name.\n'
                f'- If "{value}" is already a complete name, return it unchanged.\n'
                f'- If you cannot determine what "{value}" refers to, return "UNKNOWN".\n'
                f'- Consider common industry abbreviations '
                f'(e.g., BCBS = Blue Cross Blue Shield, UHC = UnitedHealthcare).\n'
                f'- Use the context of the full question to disambiguate.\n\n'
                f'Return JSON: {{"canonical_name": "<the expanded name or UNKNOWN>", '
                f'"confidence": <float 0.0-1.0>}}'
            )

        response = client.chat.completions.create(
            model=deployment,
            temperature=0,
            max_tokens=100,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You resolve entity abbreviations and aliases to their "
                        "full canonical names in an insurance/healthcare context. "
                        "Return valid JSON only."
                    ),
                },
                {"role": "user", "content": prompt},
            ],
            response_format={"type": "json_object"},
        )

        raw = (response.choices[0].message.content or "").strip()
        data = json.loads(raw)

        if candidate_values:
            selected = (data.get("selected_value") or "").strip()
            confidence = float(data.get("confidence", 0))
            if not selected or selected.lower() == "null" or confidence < 0.5:
                logger.info(
                    "LLM could not select from candidates for '%s' (type=%s)",
                    value, entity_type,
                )
                return None
            # Validate the LLM actually picked from the list
            if selected not in candidate_values:
                logger.warning(
                    "LLM selected '%s' which is not in candidate list for '%s'",
                    selected, value,
                )
                return None
            logger.info(
                "LLM selected '%s' for '%s' (type=%s, confidence=%.2f)",
                selected, value, entity_type, confidence,
            )
            return selected
        else:
            canonical = (data.get("canonical_name") or "").strip()
            confidence = float(data.get("confidence", 0))
            if not canonical or canonical.upper() == "UNKNOWN" or confidence < 0.5:
                logger.info(
                    "LLM could not expand '%s' (type=%s): %s",
                    value, entity_type, raw,
                )
                return None
            logger.info(
                "LLM expanded '%s' → '%s' (type=%s, confidence=%.2f)",
                value, canonical, entity_type, confidence,
            )
            return canonical

    except Exception as e:
        logger.warning("LLM entity expansion failed for '%s': %s", value, e)
        return None


# ---------------------------------------------------------------------------
# Self-learning: persist successful resolutions
# ---------------------------------------------------------------------------

_UPSERT_ALIAS_SQL = text("""
    INSERT INTO wpo.jay_entity_aliases
        (entity_type, alias_value, canonical_value, source, confidence, usage_count,
         last_used_at)
    VALUES (
        :entity_type, :alias_value, :canonical_value, :source,
        CASE WHEN :source = 'admin' THEN 1.0
             WHEN :source = 'seed' THEN 0.9
             ELSE 0.5
        END,
        1, NOW()
    )
    ON CONFLICT (entity_type, alias_value)
    DO UPDATE SET
        usage_count = wpo.jay_entity_aliases.usage_count + 1,
        confidence  = LEAST(0.95,
                            wpo.jay_entity_aliases.confidence + 0.05),
        updated_at  = NOW(),
        last_used_at = NOW()
    WHERE wpo.jay_entity_aliases.source != 'admin'
""")

_UPDATE_HIT_COUNT_SQL = text("""
    UPDATE wpo.jay_entity_aliases
    SET hit_count = hit_count + 1, last_used_at = NOW()
    WHERE entity_type = :entity_type AND alias_value = :alias_value
""")


def learn_alias(
    entity_type: str,
    alias_value: str,
    canonical_value: str,
    db_session: Session,
    source: str = "llm",
) -> None:
    """Cache a successful alias resolution (in-memory + DB).

    Called after LLM expansion succeeds and the expanded name matches a
    real DB value. The alias is permanently stored so future lookups
    skip the LLM entirely.
    """
    normalized = _normalize(alias_value)

    # Skip learning if alias == canonical (no value in caching identity)
    if normalized == _normalize(canonical_value):
        return

    # Update in-memory cache immediately
    with _cache_lock:
        if _alias_cache is not None:
            if entity_type not in _alias_cache:
                _alias_cache[entity_type] = {}
            _alias_cache[entity_type][normalized] = canonical_value

    # Persist to DB
    try:
        db_session.execute(_UPSERT_ALIAS_SQL, {
            "entity_type": entity_type,
            "alias_value": normalized,
            "canonical_value": canonical_value,
            "source": source,
        })
        db_session.commit()
        logger.info(
            "Learned alias: %s/%s → %s (source=%s)",
            entity_type, alias_value, canonical_value, source,
        )
    except Exception as e:
        logger.warning(
            "Failed to persist alias %s/%s: %s", entity_type, alias_value, e,
        )
        try:
            db_session.rollback()
        except Exception:
            pass


def record_cache_hit(
    entity_type: str,
    alias_value: str,
    db_session: Session,
) -> None:
    """Bump the hit_count for analytics. Fire-and-forget."""
    try:
        db_session.execute(_UPDATE_HIT_COUNT_SQL, {
            "entity_type": entity_type,
            "alias_value": _normalize(alias_value),
        })
        db_session.commit()
    except Exception:
        try:
            db_session.rollback()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Startup warmup
# ---------------------------------------------------------------------------

_LOAD_ALL_SQL = text("""
    SELECT entity_type, alias_value, canonical_value
    FROM wpo.jay_entity_aliases
    WHERE is_active = TRUE
    ORDER BY confidence DESC
""")


def warmup(db_session: Session) -> dict:
    """Load all aliases from DB into memory. Call at startup."""
    global _alias_cache, _cache_loaded_at

    try:
        rows = db_session.execute(_LOAD_ALL_SQL).fetchall()

        cache: Dict[str, Dict[str, str]] = {}
        for entity_type, alias_value, canonical_value in rows:
            if entity_type not in cache:
                cache[entity_type] = {}
            cache[entity_type][alias_value] = canonical_value

        with _cache_lock:
            _alias_cache = cache
            _cache_loaded_at = time.monotonic()

        total = sum(len(v) for v in cache.values())
        logger.info(
            "Alias cache warmed: %d aliases across %d entity types",
            total, len(cache),
        )
        return {"total_aliases": total, "entity_types": len(cache)}

    except Exception as e:
        logger.warning("Alias cache warmup failed: %s", e)
        with _cache_lock:
            if _alias_cache is None:
                _alias_cache = {}
            _cache_loaded_at = time.monotonic()
        return {"total_aliases": 0, "entity_types": 0}


def invalidate() -> None:
    """Clear in-memory cache. Forces reload on next warmup."""
    global _alias_cache, _cache_loaded_at
    with _cache_lock:
        _alias_cache = None
        _cache_loaded_at = 0.0
