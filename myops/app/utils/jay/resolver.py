"""
Jay Entity Resolver - Dynamic entity resolution for fuzzy name matching.

Resolves human-readable names to database primary keys:
- Agent name -> NPN
- Provider name -> NPI
- Member name -> amisys_number
- Carrier name -> carrier_name
- Categorical values -> exact DB values

Resolution tiers (in order):
1. Exact primary key match
2. Alias cache lookup (self-learning, persistent)
3. Partial ILIKE match on display column
3b. Categorical value fuzzy match (semantic registry known values)
4. Fuzzy match (ILIKE pool + SequenceMatcher scoring)
5. LLM expansion + re-search (fallback, with 5s timeout, result cached permanently)
"""

import difflib
import logging
import concurrent.futures
from typing import Dict, Any, List, Optional

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.utils.jay.semantic_registry import MODULES, GLOBAL_ENTITIES

# Fuzzy matching thresholds
_FUZZY_AUTO_RESOLVE_THRESHOLD = 0.82  # Auto-resolve if single match above this
_FUZZY_CANDIDATE_THRESHOLD = 0.45     # Include as candidate above this
_FUZZY_POOL_LIMIT = 200               # Max rows to fetch for fuzzy scoring
_CATEGORICAL_FUZZY_THRESHOLD = 0.75   # Threshold for categorical value fuzzy match
_LLM_EXPANSION_TIMEOUT = 5            # Max seconds for LLM expansion call

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Normalization helpers
# ---------------------------------------------------------------------------

def _normalize_name(name: str) -> str:
    """Normalize name for matching: handle 'Last, First' format and casing."""
    name = name.strip()
    if ',' in name:
        parts = [p.strip() for p in name.split(',', 1)]
        if len(parts) == 2:
            name = f"{parts[1]} {parts[0]}"
    return name.lower()


def _normalize_npn(npn: str) -> str:
    """Strip leading zeros for NPN matching."""
    return npn.lstrip('0') or '0'


def _is_numeric_key(key_name: str) -> bool:
    """Determine if a primary key column is numeric (NPN, NPI, IDs)."""
    numeric_indicators = ("npn", "npi", "id", "number")
    key_lower = key_name.lower()
    return any(ind in key_lower for ind in numeric_indicators)


class Resolver:

    def __init__(self, db: Session, scope: Optional[Dict[str, str]] = None):
        self.db = db
        self.scope = scope or {}

    def _execute(self, sql: str, params: dict = None) -> List[Dict[str, Any]]:
        result = self.db.execute(text(sql), params or {})
        columns = list(result.keys())
        rows = result.fetchall()
        return [dict(zip(columns, row)) for row in rows]

    def _apply_display_template(self, rows, entity_cfg):
        template = entity_cfg.get("display_template")
        for r in rows:
            if template:
                try:
                    r["display_value"] = template.format(**r)
                except Exception:
                    r["display_value"] = f"{r.get('display_value')} ({r.get('resolve_value')})"
            else:
                if r.get("display_value") and r.get("resolve_value"):
                    r["display_value"] = f"{r.get('display_value')} ({r.get('resolve_value')})"
        return rows

    def _build_scope_clause(self, entity_type: str):
        """Build optional scope clause for entity-scoped tables."""
        scope_clause = ""
        scope_params = {}
        if entity_type in ("pch_provider", "pch_member") and self.scope:
            if "entity_id" in self.scope and self.scope["entity_id"]:
                scope_clause += " AND company_id = :scope_entity"
                scope_params["scope_entity"] = self.scope["entity_id"]
            if "sub_entity_id" in self.scope and self.scope["sub_entity_id"]:
                if entity_type == "pch_provider":
                    scope_clause += " AND group_id = :scope_sub_entity"
                    scope_params["scope_sub_entity"] = self.scope["sub_entity_id"]
        return scope_clause, scope_params

    def _categorical_fuzzy_match(
        self,
        entity_type: str,
        value: str,
        resolve_key: str,
        display_column: str,
        table: str,
        scope_clause: str,
        scope_params: dict,
        limit: int,
    ) -> Optional[Dict[str, Any]]:
        """Tier 3b: Fuzzy match against known categorical values from the semantic registry.

        For entity types like carrier_name, the semantic registry knows all valid
        categorical values. This tier checks the user value against those known values
        using SequenceMatcher, which catches common abbreviations and misspellings
        without hitting the DB or LLM.

        Returns a resolution dict if matched, or None to continue to next tier.
        """
        try:
            from app.utils.jay.semantic_registry import get_all_categorical_value_maps

            cat_maps = get_all_categorical_value_maps()
            if not cat_maps:
                return None

            # For carrier_name entities, check against carrier_name categorical values
            # For other types, check against the resolve_key column
            check_column = resolve_key if entity_type != "carrier_name" else "carrier_name"

            col_map = cat_maps.get(check_column)
            if not col_map:
                return None

            value_lower = value.strip().lower()
            # col_map is {lowercase_value: db_value}
            # First try exact match in categorical values
            if value_lower in col_map:
                canonical = col_map[value_lower]
                logger.info(
                    "Tier 3b: exact categorical match '%s' → '%s' for %s",
                    value, canonical, entity_type,
                )
                # Verify against DB
                return self._verify_categorical_match(
                    canonical, resolve_key, display_column, table,
                    scope_clause, scope_params, limit, entity_type, value,
                )

            # Fuzzy match against all known categorical values
            best_score = 0.0
            best_canonical = None
            for cat_lower, db_value in col_map.items():
                score = difflib.SequenceMatcher(
                    None, value_lower, cat_lower,
                ).ratio()
                if score > best_score:
                    best_score = score
                    best_canonical = db_value

            if best_score >= _CATEGORICAL_FUZZY_THRESHOLD and best_canonical:
                logger.info(
                    "Tier 3b: fuzzy categorical match '%s' → '%s' (score=%.3f) for %s",
                    value, best_canonical, best_score, entity_type,
                )
                return self._verify_categorical_match(
                    best_canonical, resolve_key, display_column, table,
                    scope_clause, scope_params, limit, entity_type, value,
                )

        except Exception as e:
            logger.debug("Categorical fuzzy match failed for %s/%s: %s", entity_type, value, e)

        return None

    def _verify_categorical_match(
        self,
        canonical: str,
        resolve_key: str,
        display_column: str,
        table: str,
        scope_clause: str,
        scope_params: dict,
        limit: int,
        entity_type: str,
        original_value: str,
    ) -> Optional[Dict[str, Any]]:
        """Verify a categorical match exists in the DB and learn the alias."""
        entity_cfg = GLOBAL_ENTITIES.get(entity_type, {})

        if _is_numeric_key(resolve_key):
            sql = (
                f"SELECT DISTINCT {resolve_key} AS resolve_value, "
                f"{display_column} AS display_value "
                f"FROM {table} "
                f"WHERE {resolve_key} = :value{scope_clause} "
                f"LIMIT :limit"
            )
        else:
            sql = (
                f"SELECT DISTINCT {resolve_key} AS resolve_value, "
                f"{display_column} AS display_value "
                f"FROM {table} "
                f"WHERE LOWER({resolve_key}) = LOWER(:value){scope_clause} "
                f"LIMIT :limit"
            )
        params = {"value": canonical, "limit": limit, **scope_params}
        rows = self._execute(sql, params)

        if not rows:
            # Try ILIKE partial match
            sql = (
                f"SELECT DISTINCT {resolve_key} AS resolve_value, "
                f"{display_column} AS display_value "
                f"FROM {table} "
                f"WHERE {display_column} ILIKE :value{scope_clause} "
                f"LIMIT :limit"
            )
            rows = self._execute(
                sql, {"value": f"%{canonical}%", "limit": limit, **scope_params},
            )

        if rows:
            rows = self._apply_display_template(rows, entity_cfg)
            # Learn this alias for future lookups
            try:
                from app.utils.jay.alias_cache import learn_alias
                resolved_value = rows[0].get("resolve_value", canonical)
                learn_alias(entity_type, original_value, resolved_value, self.db, source="categorical")
            except Exception as e:
                logger.debug("Failed to learn categorical alias: %s", e)

            if len(rows) == 1:
                return {"resolved": True, "match": rows[0], "_resolution_tier": "Tier 3b", "_resolution_details": "categorical fuzzy match"}
            return {"resolved": False, "candidates": rows[:limit], "_resolution_tier": "Tier 3b", "_resolution_details": "categorical fuzzy, multiple matches"}

        return None

    def resolve_entity(
        self,
        entity_type: str,
        value: str,
        limit: int = 10,
        user_question: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Resolve a human name to a database primary key.

        Uses a 6-tier resolution cascade:
        1. Exact primary key match
        2. Alias cache lookup (self-learning)
        3. Partial ILIKE match
        3b. Categorical value fuzzy match (semantic registry)
        4. Fuzzy match (SequenceMatcher)
        5. LLM expansion + re-search (5s timeout, cached permanently)

        Args:
            entity_type: Type of entity (agent, pch_provider, pch_member, etc.)
            value: Human-readable value to resolve
            limit: Max candidates to return
            user_question: Full user question for LLM context (optional)

        Returns:
            {resolved: True, match: {resolve_value, display_value}} or
            {resolved: False, candidates: [...]}
        """
        entity_cfg = GLOBAL_ENTITIES.get(entity_type)
        if not entity_cfg:
            return {"resolved": False, "candidates": []}

        table = entity_cfg["table"]
        resolve_key = entity_cfg["primary_key"]
        display_column = entity_cfg["display_column"]

        # Fast path: self-referencing entities (primary_key == display_column, e.g. carrier_name)
        # Check in-memory categorical values to avoid slow view queries.
        if resolve_key == display_column:
            try:
                from app.utils.jay.semantic_registry import get_all_categorical_value_maps
                cat_maps = get_all_categorical_value_maps()
                col_map = cat_maps.get(resolve_key, {}) if cat_maps else {}
                value_lower = value.strip().lower()
                if value_lower in col_map:
                    canonical = col_map[value_lower]
                    logger.info("Tier 0: fast categorical match '%s' → '%s' for %s", value, canonical, entity_type)
                    return {
                        "resolved": True,
                        "match": {"resolve_value": canonical, "display_value": canonical},
                        "_resolution_tier": "Tier 0",
                        "_resolution_details": "in-memory categorical match (no DB)",
                    }
                # Also try fuzzy match against categorical values
                best_score = 0.0
                best_canonical = None
                for cat_lower, db_value in col_map.items():
                    score = difflib.SequenceMatcher(None, value_lower, cat_lower).ratio()
                    if score > best_score:
                        best_score = score
                        best_canonical = db_value
                if best_score >= _CATEGORICAL_FUZZY_THRESHOLD and best_canonical:
                    logger.info("Tier 0: fuzzy categorical match '%s' → '%s' (%.3f) for %s", value, best_canonical, best_score, entity_type)
                    return {
                        "resolved": True,
                        "match": {"resolve_value": best_canonical, "display_value": best_canonical},
                        "_resolution_tier": "Tier 0",
                        "_resolution_details": f"in-memory categorical fuzzy (score={best_score:.2f})",
                    }
            except Exception as e:
                logger.debug("Tier 0 categorical fast path failed: %s", e)

        scope_clause, scope_params = self._build_scope_clause(entity_type)

        # Normalize the input value for name matching
        normalized_value = _normalize_name(value)

        # 1. Primary key match (case-insensitive for strings, NPN-normalized for numerics)
        if _is_numeric_key(resolve_key):
            # NPN/NPI: try exact first, then leading-zero-normalized
            sql = (
                f"SELECT DISTINCT {resolve_key} AS resolve_value, "
                f"{display_column} AS display_value "
                f"FROM {table} "
                f"WHERE {resolve_key} = :value{scope_clause} "
                f"LIMIT :limit"
            )
            params = {"value": value, "limit": limit, **scope_params}
            rows = self._execute(sql, params)

            # If no exact match, try NPN leading-zero normalization
            if not rows:
                stripped = _normalize_npn(value)
                sql = (
                    f"SELECT DISTINCT {resolve_key} AS resolve_value, "
                    f"{display_column} AS display_value "
                    f"FROM {table} "
                    f"WHERE LTRIM({resolve_key}, '0') = :stripped{scope_clause} "
                    f"LIMIT :limit"
                )
                params = {"stripped": stripped, "limit": limit, **scope_params}
                rows = self._execute(sql, params)
        else:
            # String-type key: case-insensitive match
            sql = (
                f"SELECT DISTINCT {resolve_key} AS resolve_value, "
                f"{display_column} AS display_value "
                f"FROM {table} "
                f"WHERE LOWER({resolve_key}) = LOWER(:value){scope_clause} "
                f"LIMIT :limit"
            )
            params = {"value": value, "limit": limit, **scope_params}
            rows = self._execute(sql, params)

        if rows:
            rows = self._apply_display_template(rows, entity_cfg)
            if len(rows) == 1:
                return {"resolved": True, "match": rows[0], "_resolution_tier": "Tier 1", "_resolution_details": "exact primary key match"}
            return {"resolved": False, "candidates": rows, "_resolution_tier": "Tier 1", "_resolution_details": "multiple primary key matches"}

        # 2. Alias cache lookup (self-learning, O(1))
        try:
            from app.utils.jay.alias_cache import lookup_alias, record_cache_hit
            canonical = lookup_alias(entity_type, value)
            if canonical:
                # Re-run exact match with the cached canonical value
                if _is_numeric_key(resolve_key):
                    sql = (
                        f"SELECT DISTINCT {resolve_key} AS resolve_value, "
                        f"{display_column} AS display_value "
                        f"FROM {table} "
                        f"WHERE {resolve_key} = :value{scope_clause} "
                        f"LIMIT :limit"
                    )
                else:
                    sql = (
                        f"SELECT DISTINCT {resolve_key} AS resolve_value, "
                        f"{display_column} AS display_value "
                        f"FROM {table} "
                        f"WHERE LOWER({resolve_key}) = LOWER(:value){scope_clause} "
                        f"LIMIT :limit"
                    )
                params = {"value": canonical, "limit": limit, **scope_params}
                alias_rows = self._execute(sql, params)
                if not alias_rows:
                    # Try ILIKE partial match with the canonical value
                    sql = (
                        f"SELECT DISTINCT {resolve_key} AS resolve_value, "
                        f"{display_column} AS display_value "
                        f"FROM {table} "
                        f"WHERE {display_column} ILIKE :value{scope_clause} "
                        f"LIMIT :limit"
                    )
                    alias_rows = self._execute(
                        sql, {"value": f"%{canonical}%", "limit": limit, **scope_params},
                    )
                if alias_rows:
                    alias_rows = self._apply_display_template(alias_rows, entity_cfg)
                    record_cache_hit(entity_type, value, self.db)
                    if len(alias_rows) == 1:
                        return {"resolved": True, "match": alias_rows[0], "_resolution_tier": "Tier 2", "_resolution_details": "alias cache hit"}
                    return {"resolved": False, "candidates": alias_rows[:limit], "_resolution_tier": "Tier 2", "_resolution_details": "alias cache, multiple matches"}
        except Exception as e:
            logger.debug("Alias cache lookup failed for %s/%s: %s", entity_type, value, e)

        # 3. Partial match on display column (ILIKE with normalized name variants)
        search_variants = [value]
        if normalized_value != value.lower():
            search_variants.append(
                " ".join(w.capitalize() for w in normalized_value.split())
            )

        all_rows = []
        seen_keys = set()
        for variant in search_variants:
            sql = (
                f"SELECT DISTINCT {resolve_key} AS resolve_value, "
                f"{display_column} AS display_value "
                f"FROM {table} "
                f"WHERE {display_column} ILIKE :value{scope_clause} "
                f"LIMIT :limit"
            )
            params = {"value": f"%{variant}%", "limit": limit, **scope_params}
            rows = self._execute(sql, params)
            for row in rows:
                rk = row.get("resolve_value")
                if rk not in seen_keys:
                    seen_keys.add(rk)
                    all_rows.append(row)

        if all_rows:
            all_rows = self._apply_display_template(all_rows, entity_cfg)
            if len(all_rows) == 1:
                return {"resolved": True, "match": all_rows[0], "_resolution_tier": "Tier 3", "_resolution_details": "partial ILIKE match"}
            return {"resolved": False, "candidates": all_rows[:limit], "_resolution_tier": "Tier 3", "_resolution_details": "partial ILIKE, multiple matches"}

        # 3b. Categorical value fuzzy match (for carrier_name and similar entities)
        cat_result = self._categorical_fuzzy_match(
            entity_type, value, resolve_key, display_column, table,
            scope_clause, scope_params, limit,
        )
        if cat_result:
            return cat_result

        # 4. Fuzzy match: broader ILIKE prefix + Python similarity scoring
        fuzzy_results = self._fuzzy_search(entity_type, value, limit)
        if fuzzy_results:
            fuzzy_results = self._apply_display_template(fuzzy_results, entity_cfg)
            best_score = fuzzy_results[0].get("_fuzzy_score", 0)
            # Auto-resolve if one clear winner with high confidence
            if (
                len(fuzzy_results) == 1
                and best_score >= _FUZZY_AUTO_RESOLVE_THRESHOLD
            ):
                return {"resolved": True, "match": fuzzy_results[0], "_resolution_tier": "Tier 4", "_resolution_details": f"fuzzy match (score={best_score:.2f})"}
            if (
                len(fuzzy_results) >= 2
                and best_score >= _FUZZY_AUTO_RESOLVE_THRESHOLD
                and best_score - fuzzy_results[1].get("_fuzzy_score", 0) > 0.15
            ):
                return {"resolved": True, "match": fuzzy_results[0], "_resolution_tier": "Tier 4", "_resolution_details": f"fuzzy match (score={best_score:.2f})"}

            # If we have candidates but no clear winner, try LLM to pick
            if user_question and len(fuzzy_results) >= 2:
                try:
                    from app.utils.jay.alias_cache import expand_with_llm, learn_alias
                    candidate_values = [
                        r.get("resolve_value") or r.get("display_value", "")
                        for r in fuzzy_results
                    ]
                    selected = expand_with_llm(
                        entity_type, value, user_question,
                        candidate_values=candidate_values,
                    )
                    if selected:
                        for r in fuzzy_results:
                            if r.get("resolve_value") == selected:
                                learn_alias(entity_type, value, selected, self.db)
                                return {"resolved": True, "match": r, "_resolution_tier": "Tier 4+LLM", "_resolution_details": "fuzzy + LLM candidate selection"}
                except Exception as e:
                    logger.debug("LLM candidate selection failed: %s", e)

            return {"resolved": False, "candidates": fuzzy_results, "_resolution_tier": "Tier 4", "_resolution_details": "fuzzy match, ambiguous"}

        # 5. LLM expansion: ask LLM to expand abbreviation, then re-search
        if user_question and not _is_numeric_key(resolve_key):
            try:
                from app.utils.jay.alias_cache import expand_with_llm, learn_alias
                logger.info(
                    "Tier 5: attempting LLM expansion for '%s' (type=%s)",
                    value, entity_type,
                )
                # Timeout protection: cap LLM expansion at _LLM_EXPANSION_TIMEOUT seconds
                with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
                    future = executor.submit(
                        expand_with_llm, entity_type, value, user_question,
                    )
                    try:
                        expanded = future.result(timeout=_LLM_EXPANSION_TIMEOUT)
                    except concurrent.futures.TimeoutError:
                        logger.warning(
                            "LLM expansion timed out after %ds for '%s' (type=%s)",
                            _LLM_EXPANSION_TIMEOUT, value, entity_type,
                        )
                        expanded = None
                if expanded and expanded.lower() != value.lower():
                    logger.info(
                        "LLM expanded '%s' → '%s', re-searching %s",
                        value, expanded, entity_type,
                    )
                    # Re-run partial match with the LLM-expanded name
                    sql = (
                        f"SELECT DISTINCT {resolve_key} AS resolve_value, "
                        f"{display_column} AS display_value "
                        f"FROM {table} "
                        f"WHERE {display_column} ILIKE :value{scope_clause} "
                        f"LIMIT :limit"
                    )
                    params = {"value": f"%{expanded}%", "limit": limit, **scope_params}
                    llm_rows = self._execute(sql, params)

                    if not llm_rows:
                        # Try exact match on resolve key
                        sql = (
                            f"SELECT DISTINCT {resolve_key} AS resolve_value, "
                            f"{display_column} AS display_value "
                            f"FROM {table} "
                            f"WHERE LOWER({resolve_key}) = LOWER(:value){scope_clause} "
                            f"LIMIT :limit"
                        )
                        llm_rows = self._execute(
                            sql, {"value": expanded, "limit": limit, **scope_params},
                        )

                    if llm_rows:
                        llm_rows = self._apply_display_template(llm_rows, entity_cfg)
                        if len(llm_rows) == 1:
                            # Cache this alias permanently
                            canonical = llm_rows[0].get("resolve_value", expanded)
                            learn_alias(entity_type, value, canonical, self.db)
                            return {"resolved": True, "match": llm_rows[0], "_resolution_tier": "Tier 5", "_resolution_details": f"LLM expansion '{value}' -> '{expanded}'"}
                        return {"resolved": False, "candidates": llm_rows[:limit], "_resolution_tier": "Tier 5", "_resolution_details": f"LLM expansion '{value}' -> '{expanded}', multiple matches"}
            except Exception as e:
                logger.warning("LLM entity expansion tier failed: %s", e)

        return {"resolved": False, "candidates": [], "_resolution_tier": "unresolved", "_resolution_details": "all tiers exhausted"}

    def _fuzzy_search(
        self,
        entity_type: str,
        value: str,
        limit: int = 10,
    ) -> List[Dict[str, Any]]:
        """Fuzzy search using multiple ILIKE strategies + Python similarity scoring.

        Strategy — build a broad candidate pool via several ILIKE patterns:
        1. Prefix search: first 4/3/2 characters + '%'
        2. Suffix search: '%' + last 3/4 characters (catches missing/extra chars
           in the middle, e.g. "Cadace" → "%dace%" matches "Candace")
        3. Contains search: '%' + value[:-1] + '%'
        Then score every candidate with SequenceMatcher and return the best.
        """
        entity_cfg = GLOBAL_ENTITIES.get(entity_type)
        if not entity_cfg or len(value) < 2:
            return []

        table = entity_cfg["table"]
        resolve_key = entity_cfg["primary_key"]
        display_column = entity_cfg["display_column"]
        scope_clause, scope_params = self._build_scope_clause(entity_type)

        all_rows: List[Dict[str, Any]] = []
        seen_keys: set = set()

        def _fetch(pattern: str):
            sql = (
                f"SELECT DISTINCT {resolve_key} AS resolve_value, "
                f"{display_column} AS display_value "
                f"FROM {table} "
                f"WHERE {display_column} ILIKE :pattern{scope_clause} "
                f"LIMIT :pool_limit"
            )
            params = {
                "pattern": pattern,
                "pool_limit": _FUZZY_POOL_LIMIT,
                **scope_params,
            }
            for row in self._execute(sql, params):
                rk = row.get("resolve_value")
                if rk not in seen_keys:
                    seen_keys.add(rk)
                    all_rows.append(row)

        # Strategy: use MULTIPLE search angles to build a diverse candidate pool.
        # Suffix/contains patterns are critical for typos in early characters
        # (e.g. "Cadace" → "%dace%" finds "Candace"), so they always run.

        # 1. Targeted suffix/contains patterns (always run — most important for typos)
        if len(value) >= 4:
            _fetch(f"%{value[-4:]}%")          # last 4 chars
            _fetch(f"%{value[:-1]}%")          # full minus last char
        if len(value) >= 3:
            _fetch(f"%{value[-3:]}%")          # last 3 chars

        # 2. Prefix searches (progressively shorter, stop when pool is large enough)
        for prefix_len in (min(len(value), 4), 3, 2):
            if prefix_len < 2 or len(all_rows) >= _FUZZY_POOL_LIMIT:
                break
            _fetch(f"{value[:prefix_len]}%")

        if not all_rows:
            return []

        # Score candidates by similarity
        value_lower = _normalize_name(value)
        scored = []
        for row in all_rows:
            display = (row.get("display_value") or "").lower().strip()
            if not display:
                continue

            # Score against full display name
            full_score = difflib.SequenceMatcher(
                None, value_lower, display,
            ).ratio()

            # Also score against individual words (handles "Candace Smith" vs "Cadace")
            word_score = max(
                (
                    difflib.SequenceMatcher(None, value_lower, word).ratio()
                    for word in display.split()
                ),
                default=0.0,
            )

            best_score = max(full_score, word_score)
            if best_score >= _FUZZY_CANDIDATE_THRESHOLD:
                row["_fuzzy_score"] = round(best_score, 3)
                scored.append((best_score, row))

        # Sort by score descending
        scored.sort(key=lambda x: x[0], reverse=True)
        return [row for _, row in scored[:limit]]

    def resolve_entity_broad(
        self,
        value: str,
        limit: int = 10,
    ) -> Dict[str, Any]:
        """Search for a value across ALL entity tables.

        Used when entity type is unknown or initial typed search fails.
        Tries each GLOBAL_ENTITIES table in turn and collects candidates.
        """
        all_candidates = []
        for entity_type in GLOBAL_ENTITIES:
            result = self.resolve_entity(entity_type, value, limit=5)
            if result.get("resolved"):
                result["entity_type"] = entity_type
                return result
            candidates = result.get("candidates", [])
            for c in candidates:
                c["_entity_type"] = entity_type
            all_candidates.extend(candidates)

        if all_candidates:
            return {"resolved": False, "candidates": all_candidates[:limit]}
        return {"resolved": False, "candidates": []}

    def resolve_member_auto(
        self,
        value: str,
        intent_module: Optional[str] = None,
        limit: int = 10,
    ) -> Dict[str, Any]:
        """Resolve a 'member' entity by trying both commission_member and pch_member.

        When the intent module is pch_members, try pch_member first then fall back
        to commission_member. Otherwise, try commission_member first then pch_member.

        Args:
            value: Human-readable member name or ID to resolve
            intent_module: The intent module name (e.g. "pch_members", "commissions")
            limit: Max candidates to return

        Returns:
            Tuple-like dict with additional 'entity_type' key indicating which matched:
            {resolved: True, match: {...}, entity_type: "pch_member"|"commission_member"}
        """
        if intent_module and intent_module.startswith("pch"):
            order = ["pch_member", "commission_member"]
        else:
            order = ["commission_member", "pch_member"]

        all_candidates = []
        for etype in order:
            resolution = self.resolve_entity(etype, value, limit=limit)
            if resolution.get("resolved"):
                resolution["entity_type"] = etype
                return resolution
            candidates = resolution.get("candidates", [])
            if candidates:
                for c in candidates:
                    c["_entity_type"] = etype
                all_candidates.extend(candidates)

        if all_candidates:
            return {"resolved": False, "candidates": all_candidates[:limit]}
        return {"resolved": False, "candidates": []}

    def resolve_categorical_value(
        self,
        table: str,
        column: str,
        value: str,
        limit: int = 100,
    ) -> Dict[str, Any]:
        """Resolve a user-provided categorical value to the exact DB value."""
        if not value or not isinstance(value, str):
            return {"resolved": False, "candidates": []}

        value_clean = value.strip()

        # Get distinct values
        sql = (
            f"SELECT DISTINCT {column} AS display_value "
            f"FROM {table} "
            f"WHERE {column} IS NOT NULL AND TRIM({column}) <> '' "
            f"LIMIT :limit"
        )
        rows = self._execute(sql, {"limit": limit})
        distinct_values = [r["display_value"] for r in rows if r.get("display_value")]

        if not distinct_values:
            return {"resolved": False, "candidates": []}

        # 1. Exact match
        exact = [v for v in distinct_values if v.strip().lower() == value_clean.lower()]
        if len(exact) == 1:
            return {"resolved": True, "match": {"resolve_value": exact[0]}}
        if len(exact) > 1:
            return {
                "resolved": False,
                "candidates": [{"resolve_value": v, "display_value": v} for v in exact],
            }

        # 2. Partial match
        partial = [v for v in distinct_values if value_clean.lower() in v.lower()]
        if len(partial) == 1:
            return {"resolved": True, "match": {"resolve_value": partial[0]}}
        if len(partial) > 1:
            return {
                "resolved": False,
                "candidates": [{"resolve_value": v, "display_value": v} for v in partial],
            }

        # 3. No match - show top suggestions
        return {
            "resolved": False,
            "candidates": [
                {"resolve_value": v, "display_value": v} for v in distinct_values[:10]
            ],
        }


def resolve_across_entities(
    value: str,
    db_session: Session,
    scope: Optional[Dict[str, str]] = None,
    limit_per_type: int = 5,
) -> List[Dict[str, Any]]:
    """Check a value against ALL entity types and return matches from each.

    Unlike Resolver.resolve_entity_broad() which short-circuits on the first
    resolved match, this function always checks every entity type and returns
    all matches with scores. This enables the multi-hypothesis approach for
    disambiguation (e.g., "john smith" could be an agent OR a member).

    Args:
        value: The search term (e.g., "john smith", "bcbs").
        db_session: SQLAlchemy session for DB queries.
        scope: Optional scope dict for entity-scoped tables.
        limit_per_type: Max matches to return per entity type.

    Returns:
        List of dicts sorted by score descending:
        [
            {"entity_type": "carrier_name", "match": "Blue Cross Blue Shield", "score": 0.95, "resolved": True},
            {"entity_type": "agent", "match": "John Smith (NPN: 12345)", "score": 0.80, "resolved": True},
            ...
        ]
    """
    resolver = Resolver(db_session, scope=scope)
    all_matches: List[Dict[str, Any]] = []

    for entity_type in GLOBAL_ENTITIES:
        try:
            result = resolver.resolve_entity(entity_type, value, limit=limit_per_type)

            if result.get("resolved"):
                match = result["match"]
                display = match.get("display_value", match.get("resolve_value", ""))
                # Exact/alias/categorical resolution -> high score
                tier = result.get("_resolution_tier", "")
                score = _tier_to_score(tier, match.get("_fuzzy_score"))
                all_matches.append({
                    "entity_type": entity_type,
                    "match": display,
                    "resolve_value": match.get("resolve_value"),
                    "score": score,
                    "resolved": True,
                    "tier": tier,
                })
            else:
                candidates = result.get("candidates", [])
                for candidate in candidates:
                    display = candidate.get("display_value", candidate.get("resolve_value", ""))
                    fuzzy_score = candidate.get("_fuzzy_score")
                    tier = result.get("_resolution_tier", "")
                    score = _tier_to_score(tier, fuzzy_score)
                    all_matches.append({
                        "entity_type": entity_type,
                        "match": display,
                        "resolve_value": candidate.get("resolve_value"),
                        "score": score,
                        "resolved": False,
                        "tier": tier,
                    })
        except Exception as e:
            logger.warning(
                "Cross-entity resolution failed for type=%s value='%s': %s",
                entity_type, value, e,
            )

    # Sort by score descending
    all_matches.sort(key=lambda m: m.get("score", 0), reverse=True)
    return all_matches


def _tier_to_score(tier: str, fuzzy_score: Optional[float] = None) -> float:
    """Convert a resolution tier to a numeric score for cross-entity ranking.

    Higher tiers (more exact matches) get higher scores.
    """
    if fuzzy_score is not None:
        return round(float(fuzzy_score), 3)
    tier_lower = tier.lower() if tier else ""
    if "tier 0" in tier_lower:
        return 0.99  # In-memory categorical exact
    if "tier 1" in tier_lower:
        return 0.98  # Exact primary key
    if "tier 2" in tier_lower:
        return 0.95  # Alias cache
    if "tier 3b" in tier_lower:
        return 0.90  # Categorical fuzzy
    if "tier 3" in tier_lower:
        return 0.85  # Partial ILIKE
    if "tier 4" in tier_lower:
        return 0.70  # Fuzzy match (no explicit score)
    if "tier 5" in tier_lower:
        return 0.65  # LLM expansion
    return 0.50  # Unknown/unresolved


def _collect_filter_value(
    filters: Dict[str, Any],
    key: str,
    value: Any,
) -> None:
    """Append a resolved value to a filter key, supporting multi-entity.

    If the key already exists, converts to a list to hold multiple values.
    This enables IN (...) SQL generation for multiple entities of the same type.
    """
    existing = filters.get(key)
    if existing is None:
        filters[key] = value
    elif isinstance(existing, list):
        if value not in existing:
            existing.append(value)
    else:
        if existing != value:
            filters[key] = [existing, value]


def resolve_all_filters(
    intent,
    resolver: Resolver,
    resolved_filters: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    """Resolve all dynamic filters in an intent.

    Supports multi-entity resolution: when multiple values resolve for the same
    filter key, they are collected into a list so the SQL generator can produce
    IN (...) clauses.

    Returns {"resolved": True} or {"resolved": False, "resolution_type": ..., "clarification": [...]}
    """
    module_cfg = MODULES[intent.module]
    filters_cfg = module_cfg["filters"]
    entity_links = module_cfg.get("entity_links", {})

    # Apply pre-resolved filters
    if resolved_filters:
        for key, value in resolved_filters.items():
            intent.filters[key] = value
        return {"resolved": True}

    # Normalize carrier logical routing
    if "carrier" in intent.filters:
        value = intent.filters["carrier"]
        if isinstance(value, str):
            if value.isdigit():
                intent.filters["carrier_id"] = value
            else:
                intent.filters["carrier_name"] = value
        del intent.filters["carrier"]

    # Resolve dynamic filters
    for key, value in list(intent.filters.items()):
        filter_cfg = filters_cfg.get(key, {})
        filter_type = filter_cfg.get("type")

        if not isinstance(value, str) or not value.strip():
            continue

        if filter_type == "logical_time":
            continue

        # Dynamic agent
        if filter_type == "dynamic_agent":
            resolution = resolver.resolve_entity("agent", value)
            if not resolution.get("resolved"):
                return {
                    "resolved": False,
                    "resolution_type": key,
                    "clarification": resolution.get("candidates"),
                }
            local_key = entity_links.get("agent", {}).get("local_key")
            if local_key:
                del intent.filters[key]
                _collect_filter_value(intent.filters, local_key, resolution["match"]["resolve_value"])
            continue

        # Dynamic upline
        if filter_type == "dynamic_upline":
            resolution = resolver.resolve_entity("agent", value)
            if not resolution.get("resolved"):
                return {
                    "resolved": False,
                    "resolution_type": key,
                    "clarification": resolution.get("candidates"),
                }
            del intent.filters[key]
            _collect_filter_value(intent.filters, "upline_npn", resolution["match"]["resolve_value"])
            continue

        # Dynamic top upline
        if filter_type == "dynamic_top_upline":
            resolution = resolver.resolve_entity("agent", value)
            if not resolution.get("resolved"):
                return {
                    "resolved": False,
                    "resolution_type": key,
                    "clarification": resolution.get("candidates"),
                }
            del intent.filters[key]
            _collect_filter_value(intent.filters, "top_upline_npn", resolution["match"]["resolve_value"])
            continue

        # Dynamic carrier
        if filter_type == "dynamic_carrier":
            resolution = resolver.resolve_entity("carrier_name", value)
            if not resolution.get("resolved"):
                return {
                    "resolved": False,
                    "resolution_type": key,
                    "clarification": resolution.get("candidates"),
                }
            del intent.filters[key]
            _collect_filter_value(intent.filters, "carrier_name", resolution["match"]["resolve_value"])
            continue

        # Dynamic member — try both commission_member and pch_member
        if filter_type == "dynamic_member":
            resolution = resolver.resolve_member_auto(
                value,
                intent_module=getattr(intent, "module", None),
            )
            if not resolution.get("resolved"):
                return {
                    "resolved": False,
                    "resolution_type": key,
                    "clarification": resolution.get("candidates"),
                }
            del intent.filters[key]
            matched_type = resolution.get("entity_type", "commission_member")
            if matched_type == "pch_member":
                _collect_filter_value(intent.filters, "amisys_number", resolution["match"]["resolve_value"])
            else:
                _collect_filter_value(intent.filters, "account_number", resolution["match"]["resolve_value"])
            continue

        # Dynamic PCH provider
        if filter_type == "dynamic_pch_provider":
            resolution = resolver.resolve_entity("pch_provider", value)
            if not resolution.get("resolved"):
                return {
                    "resolved": False,
                    "resolution_type": key,
                    "clarification": resolution.get("candidates"),
                }
            del intent.filters[key]
            _collect_filter_value(intent.filters, "npi", resolution["match"]["resolve_value"])
            continue

        # Dynamic PCH member
        if filter_type == "dynamic_pch_member":
            resolution = resolver.resolve_entity("pch_member", value)
            if not resolution.get("resolved"):
                return {
                    "resolved": False,
                    "resolution_type": key,
                    "clarification": resolution.get("candidates"),
                }
            del intent.filters[key]
            _collect_filter_value(intent.filters, "amisys_number", resolution["match"]["resolve_value"])
            continue

        # Categorical resolution
        if filter_type in ("categorical_strict", "categorical_token", "state_flag"):
            table = module_cfg["table"]
            column = filter_cfg.get("column")
            resolution = resolver.resolve_categorical_value(table, column, value)
            if not resolution.get("resolved"):
                return {
                    "resolved": False,
                    "resolution_type": key,
                    "clarification": resolution.get("candidates"),
                }
            intent.filters[key] = resolution["match"]["resolve_value"]
            continue

    return {"resolved": True}
