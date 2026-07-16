"""
JAI Knowledge Cache — Thread-safe TTL cache for DB-driven business knowledge.

Loads synonyms, glossary, filter values, and query examples from PostgreSQL
config tables. Falls back to hardcoded dicts if DB is empty or throws.

Public API matches the signatures of the original functions in
business_knowledge.py, semantic_registry.py, and table_catalog.py.
"""

import os
import time
import threading
import logging
from typing import Optional

logger = logging.getLogger(__name__)

# TTL in seconds — configurable via env var (default: 5 minutes)
_CACHE_TTL = int(os.environ.get("JAI_KNOWLEDGE_CACHE_TTL", "3600"))

# Thread-safe cache storage
_lock = threading.RLock()
_cache: dict = {}
_timestamps: dict = {}


def _is_expired(key: str) -> bool:
    ts = _timestamps.get(key)
    if ts is None:
        return True
    return (time.time() - ts) > _CACHE_TTL


def _get_session():
    """Get a new DB session (lazy import to avoid circular deps)."""
    from app.db.session import SessionLocal
    return SessionLocal()


# ─── Synonym loader ──────────────────────────────────────────────

def _load_synonyms_from_db() -> Optional[dict]:
    """Load synonyms from jay_config_synonyms table."""
    try:
        from app.models.jay.JayConfig import JaySynonym
        session = _get_session()
        try:
            rows = session.query(JaySynonym).filter(
                JaySynonym.is_active == True
            ).all()
            if not rows:
                return None
            return {row.term: row.db_column for row in rows}
        finally:
            session.close()
    except Exception as e:
        logger.warning(f"Failed to load synonyms from DB: {e}")
        return None


def _get_fallback_synonyms() -> dict:
    from app.utils.jay.business_knowledge import SYNONYMS
    return SYNONYMS


def get_synonym_context() -> str:
    """Build synonym context string for LLM prompts (DB-backed with fallback)."""
    key = "synonyms"
    with _lock:
        if not _is_expired(key) and key in _cache:
            synonyms = _cache[key]
        else:
            synonyms = _load_synonyms_from_db()
            if synonyms is None:
                synonyms = _get_fallback_synonyms()
            _cache[key] = synonyms
            _timestamps[key] = time.time()

    lines = ["Business Term -> Database Column:"]
    for biz, db_col in synonyms.items():
        lines.append(f'  "{biz}" = column "{db_col}"')
    return "\n".join(lines)


def get_synonym_context_for_module(module: str) -> str:
    """Build synonym context filtered to synonyms relevant to the given module.

    Falls back to full synonym context because the JaySynonym model does not
    have a ``module`` column — all synonyms are global.
    """
    # JaySynonym has no module column, so we return the full context.
    return get_synonym_context()


# ─── Glossary loader ─────────────────────────────────────────────

def _load_glossary_from_db() -> Optional[dict]:
    """Load glossary from jay_config_glossary table."""
    try:
        from app.models.jay.JayConfig import JayGlossaryEntry
        session = _get_session()
        try:
            rows = session.query(JayGlossaryEntry).filter(
                JayGlossaryEntry.is_active == True
            ).all()
            if not rows:
                return None
            return {
                row.term: {
                    "description": row.description or "",
                    "sql_hint": row.sql_hint or "",
                    "module": row.module or "",
                }
                for row in rows
            }
        finally:
            session.close()
    except Exception as e:
        logger.warning(f"Failed to load glossary from DB: {e}")
        return None


def _get_fallback_glossary() -> dict:
    from app.utils.jay.business_knowledge import BUSINESS_GLOSSARY
    return BUSINESS_GLOSSARY


def get_business_glossary_context() -> str:
    """Build business glossary context string for LLM prompts (DB-backed with fallback)."""
    key = "glossary"
    with _lock:
        if not _is_expired(key) and key in _cache:
            glossary = _cache[key]
        else:
            glossary = _load_glossary_from_db()
            if glossary is None:
                glossary = _get_fallback_glossary()
            _cache[key] = glossary
            _timestamps[key] = time.time()

    lines = ["BUSINESS GLOSSARY (concept -> SQL mapping):"]
    for term, info in glossary.items():
        lines.append(f'  "{term}": {info["description"]} | SQL: {info["sql_hint"]}')
    return "\n".join(lines)


def get_glossary_context_for_module(module: str) -> str:
    """Build glossary context filtered to entries relevant to the given module.

    Includes entries whose ``module`` field matches the given module name
    as well as global entries (module is empty/NULL).  Falls back to the
    full glossary context if the module is empty or filtering yields no
    results.
    """
    key = "glossary"
    with _lock:
        if not _is_expired(key) and key in _cache:
            glossary = _cache[key]
        else:
            glossary = _load_glossary_from_db()
            if glossary is None:
                glossary = _get_fallback_glossary()
            _cache[key] = glossary
            _timestamps[key] = time.time()

    if not module:
        # No module specified — return full glossary
        return get_business_glossary_context()

    module_lower = module.lower()
    filtered = {
        term: info
        for term, info in glossary.items()
        if not info.get("module") or info["module"].lower() == module_lower
    }

    if not filtered:
        return "BUSINESS GLOSSARY (concept -> SQL mapping):\n  (no entries for this module)"

    lines = ["BUSINESS GLOSSARY (concept -> SQL mapping):"]
    for term, info in filtered.items():
        lines.append(f'  "{term}": {info["description"]} | SQL: {info["sql_hint"]}')
    return "\n".join(lines)


# ─── Filter values loader ────────────────────────────────────────

def _load_filter_values_from_db() -> Optional[dict]:
    """Load filter values grouped by module from jay_config_filter_values."""
    try:
        from app.models.jay.JayConfig import JayFilterValue
        session = _get_session()
        try:
            rows = session.query(JayFilterValue).filter(
                JayFilterValue.is_active == True
            ).order_by(JayFilterValue.module, JayFilterValue.filter_name, JayFilterValue.sort_order).all()
            if not rows:
                return None
            # Structure: {module: {column: {lower_val: db_val}}}
            result = {}
            for row in rows:
                mod = result.setdefault(row.module, {})
                col = mod.setdefault(row.column_name, {})
                col[row.valid_value.lower()] = row.valid_value
            return result
        finally:
            session.close()
    except Exception as e:
        logger.warning(f"Failed to load filter values from DB: {e}")
        return None


def _get_fallback_filter_values() -> dict:
    """Build filter value map from hardcoded MODULES."""
    from app.utils.jay.semantic_registry import MODULES
    result = {}
    for module_name, module_cfg in MODULES.items():
        mod = {}
        for _filter_name, filter_cfg in module_cfg.get("filters", {}).items():
            col = filter_cfg.get("column")
            valid = filter_cfg.get("valid_values")
            if col and valid:
                mod[col] = {v.lower(): v for v in valid}
        if mod:
            result[module_name] = mod
    return result


def _get_all_filter_values() -> dict:
    """Get the full filter values map (from cache/DB/fallback)."""
    key = "filter_values"
    with _lock:
        if not _is_expired(key) and key in _cache:
            return _cache[key]
        data = _load_filter_values_from_db()
        if data is None:
            data = _get_fallback_filter_values()
        _cache[key] = data
        _timestamps[key] = time.time()
        return data


def get_categorical_value_map(module: str) -> dict:
    """Get column -> {lowercase_value: db_value} mapping for a module."""
    all_maps = _get_all_filter_values()
    return all_maps.get(module, {})


def get_all_categorical_value_maps() -> dict:
    """Get column -> {lowercase_value: db_value} mapping across ALL modules.

    Also merges carrier names from jay_carrier_lookup (loaded at startup
    into _carrier_lookup_cache) so entity resolution can match carriers
    in-memory without hitting the slow commission views.
    """
    all_maps = _get_all_filter_values()
    merged = {}
    for _module, mod_map in all_maps.items():
        for col, mapping in mod_map.items():
            if col not in merged:
                merged[col] = {}
            merged[col].update(mapping)

    # Inject carrier names from lookup table cache
    if _carrier_lookup_cache:
        if "carrier_name" not in merged:
            merged["carrier_name"] = {}
        merged["carrier_name"].update(_carrier_lookup_cache)

    return merged


# ─── Carrier lookup cache (loaded at startup) ────────────────────
_carrier_lookup_cache: dict = {}  # {lowercase_name: db_name}


def warmup_carrier_lookup(db_session=None) -> int:
    """Load carrier names from jay_carrier_lookup into memory.

    Called during bootstrap. Returns count of carriers loaded.
    """
    global _carrier_lookup_cache
    try:
        from sqlalchemy import text
        session = db_session or _get_session()
        try:
            result = session.execute(
                text("SELECT carrier_name FROM wpo.jay_carrier_lookup WHERE carrier_name IS NOT NULL")
            )
            carriers = {}
            for row in result:
                name = row[0].strip()
                if name:
                    carriers[name.lower()] = name
            with _lock:
                _carrier_lookup_cache = carriers
            logger.info("Carrier lookup cache warmed: %d carriers", len(carriers))
            return len(carriers)
        finally:
            if db_session is None:
                session.close()
    except Exception as e:
        logger.warning("Carrier lookup warmup failed: %s", e)
        return 0


# ─── Query examples loader ───────────────────────────────────────

def _load_query_examples_from_db() -> Optional[dict]:
    """Load query examples from jay_config_query_examples table."""
    try:
        from app.models.jay.JayConfig import JayQueryExample
        session = _get_session()
        try:
            rows = session.query(JayQueryExample).filter(
                JayQueryExample.is_active == True,
                JayQueryExample.example_type == "sql_example",
            ).all()
            if not rows:
                return None
            return {row.name: row.sql_text for row in rows}
        finally:
            session.close()
    except Exception as e:
        logger.warning(f"Failed to load query examples from DB: {e}")
        return None


def _get_fallback_query_examples() -> dict:
    from app.utils.jay.table_catalog import QUERY_EXAMPLES
    return QUERY_EXAMPLES


def get_query_examples(domain_names: list) -> str:
    """Get relevant SQL examples for the specified domains (DB-backed with fallback)."""
    key = "query_examples"
    with _lock:
        if not _is_expired(key) and key in _cache:
            examples = _cache[key]
        else:
            examples = _load_query_examples_from_db()
            if examples is None:
                examples = _get_fallback_query_examples()
            _cache[key] = examples
            _timestamps[key] = time.time()

    # Match by domain tables (same logic as original)
    from app.utils.jay.table_catalog import TABLE_DOMAINS
    seen_tables = set()
    for domain_name in domain_names:
        domain_cfg = TABLE_DOMAINS.get(domain_name)
        if not domain_cfg:
            continue
        for table_cfg in domain_cfg["tables"]:
            short_name = table_cfg["name"].split(".")[-1]
            seen_tables.add(short_name)
            seen_tables.add(table_cfg["name"])

    relevant = []
    for example_name, example_sql in examples.items():
        for table in seen_tables:
            if table in example_sql:
                relevant.append(f"-- Example: {example_name}\n{example_sql}")
                break

    if not relevant:
        return ""

    return "\n\n".join(["-- QUERY EXAMPLES FROM CODEBASE"] + relevant)


# ─── Cache management ────────────────────────────────────────────

def invalidate_cache(key: Optional[str] = None):
    """Clear one or all cache keys. Forces fresh DB load on next request."""
    with _lock:
        if key:
            _cache.pop(key, None)
            _timestamps.pop(key, None)
        else:
            _cache.clear()
            _timestamps.clear()
