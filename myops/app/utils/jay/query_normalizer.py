"""JAI Query Normalizer - Expands known abbreviations in user queries before intent detection.

Two-pass approach:
1. Regex-based alias substitution from alias cache (no LLM, <1ms)
2. If aliases were found, optional lightweight LLM rewrite for grammar (max_tokens=100)
"""

from __future__ import annotations

import logging
import re
from typing import Dict, Optional, Tuple

logger = logging.getLogger(__name__)


def get_all_aliases() -> Dict[str, str]:
    """Return {alias: canonical} from the alias cache across all entity types.

    Sorted by alias length descending so longer aliases are replaced first,
    preventing partial-match corruption (e.g. "BCBS of TX" before "BCBS").
    """
    from app.utils.jay.alias_cache import _alias_cache, _cache_lock

    result: Dict[str, str] = {}
    with _cache_lock:
        if _alias_cache is None:
            return result
        for _entity_type, alias_map in _alias_cache.items():
            for alias_key, canonical in alias_map.items():
                # Skip identity mappings (alias == canonical normalized)
                if alias_key == canonical.strip().lower().replace(".", "").replace("-", " "):
                    continue
                result[alias_key] = canonical

    # Sort by length descending to replace longer aliases first
    return dict(sorted(result.items(), key=lambda x: len(x[0]), reverse=True))


def normalize_query(query: str, db_session=None) -> Tuple[str, bool]:
    """Normalize a user query by expanding known abbreviations.

    Returns:
        (normalized_query, was_modified) tuple.

    Pass 1: Regex word-boundary substitution from alias cache (<1ms).
    Pass 2: If substitutions were made and db_session is provided,
             lightweight LLM call to fix grammar (~200ms).
             If LLM fails, regex-substituted version is still returned.
    """
    aliases = get_all_aliases()
    if not aliases:
        return query, False

    # Pass 1: Regex-based alias substitution
    substituted = query
    any_replaced = False

    for alias, canonical in aliases.items():
        pattern = r"\b" + re.escape(alias) + r"\b"
        new_text, count = re.subn(pattern, canonical, substituted, flags=re.IGNORECASE)
        if count > 0:
            any_replaced = True
            substituted = new_text

    if not any_replaced:
        return query, False

    logger.info(
        "Query alias substitution: '%s' -> '%s'",
        query, substituted,
    )

    # Pass 2: Lightweight LLM grammar fix (optional)
    final = substituted
    if db_session is not None:
        try:
            from app.utils.jay.llm_client import get_llm_client

            client, deployment = get_llm_client()
            response = client.chat.completions.create(
                model=deployment,
                temperature=0.0,
                max_tokens=100,
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "You clean up grammar after abbreviation expansion. "
                            "Output ONLY the corrected sentence."
                        ),
                    },
                    {"role": "user", "content": substituted},
                ],
            )
            llm_text = (response.choices[0].message.content or "").strip()
            if llm_text:
                final = llm_text
                logger.info(
                    "Query grammar fix: '%s' -> '%s'",
                    substituted, final,
                )
        except Exception as e:
            logger.warning(
                "LLM grammar fix failed (using regex result): %s", e,
            )
            # Fall through with regex-substituted version

    logger.info(
        "Query normalization complete: '%s' -> '%s'",
        query, final,
    )
    return final, True
