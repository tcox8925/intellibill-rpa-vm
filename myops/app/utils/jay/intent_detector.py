"""
JAI Intent Detector - Step 1 of the new pipeline.

Detects user intent: which module to query, what entities are mentioned,
what format to display, and whether it's a comparison query.

Supports multi-intent detection: ``detect_intents()`` returns up to 3
ranked interpretations from a single LLM call so the pipeline can run
them in parallel.
"""

import json
import logging
import time
from typing import Optional, List, Dict

from app.utils.jay.llm_client import get_llm_client
from app.utils.jay.pipeline_models import IntentResult, EntityMention, PipelineContext, RetryHistory
from app.utils.jay.prompts import build_module_selection_prompt
from app.utils.jay.semantic_registry import MODULES
from app.utils.jay.table_catalog import TABLE_DOMAINS, get_domain_for_module

logger = logging.getLogger(__name__)

# Seed aliases — bootstrap mappings that are always available.
# The dynamic resolver adds to this at runtime via self-learning.
_SEED_ALIASES: Dict[str, str] = {
    "agent_licenses": "licenses",
    "agent_profile": "agents",
    "agent_contracts": "agents_contracts",
    "provider_core": "pch_providers",
    "member_roster": "pch_members",
    "agent": "agents",
    "contracts": "agents_contracts",
    "commissions": "commission_items",
    "commission": "commission_items",
    "providers": "pch_providers",
    "provider": "pch_providers",
    "members": "pch_members",
    "member": "pch_members",
    "assessments": "membercare_assessments",
    "bob": "bob",
    "book_of_business": "bob",
    "enrollment": "pch_members",
    "member_enrollment": "pch_members",
    "member_demographics": "pch_members",
    "licensing": "licenses",
    "license": "licenses",
    "agent_licensing": "licenses",
    "carrier": "commission_items",
    "carriers": "commission_items",
    "carrier_contracts": "agents_contracts",
    "credentialing": "pch_providers",
    "provider_credentialing": "pch_providers",
    "provider_info": "pch_providers",
    "agent_commissions": "commission_items",
}

# Dynamic in-memory cache — grows as new aliases are resolved.
# Thread-safe; cleared only on explicit invalidation.
import threading as _threading

_module_alias_lock = _threading.Lock()
_module_alias_cache: Dict[str, str] = dict(_SEED_ALIASES)


def _learn_module_alias(alias: str, resolved: str) -> None:
    """Cache a newly discovered alias → module mapping (in-memory + DB)."""
    with _module_alias_lock:
        _module_alias_cache[alias.lower()] = resolved
    logger.info(f"Module alias learned: '{alias}' -> '{resolved}'")
    # Persist to DB via alias_cache
    try:
        from app.utils.jay.alias_cache import learn_alias
        from app.db.session import SessionLocal
        db = SessionLocal()
        try:
            learn_alias("module_alias", alias.lower(), resolved, db, source="dynamic_resolver")
        finally:
            db.close()
    except Exception as e:
        logger.debug(f"Module alias DB persist failed (non-fatal): {e}")


def _fuzzy_resolve_module(unknown: str) -> Optional[str]:
    """Dynamically resolve an unknown module name using multiple strategies.

    Strategy 1: Exact match in cache/seed aliases
    Strategy 2: Substring match (module key in unknown, or unknown in key)
    Strategy 3: Word overlap with module descriptions (semantic)
    Strategy 4: Domain name match from table catalog

    Returns the resolved module name, or None if all strategies fail.
    """
    normalized = unknown.lower().replace("-", "_").replace(" ", "_")

    # Strategy 0: Check persistent alias cache (DB-backed)
    try:
        from app.utils.jay.alias_cache import lookup_alias
        db_resolved = lookup_alias("module_alias", unknown)
        if db_resolved and db_resolved in MODULES:
            # Also update in-memory cache
            with _module_alias_lock:
                _module_alias_cache[normalized] = db_resolved
            return db_resolved
    except Exception:
        pass

    # Strategy 1: cached alias (includes seeds + learned)
    with _module_alias_lock:
        cached = _module_alias_cache.get(normalized)
    if cached and cached in MODULES:
        return cached

    # Strategy 2: substring match against module keys
    # Check BOTH directions but prefer exact key matches over partial
    for key in MODULES:
        if normalized == key:
            _learn_module_alias(normalized, key)
            return key
    for key in MODULES:
        # Only match if the key is a meaningful substring (>= 4 chars overlap)
        if key in normalized and len(key) >= 4:
            _learn_module_alias(normalized, key)
            return key
        if normalized in key and len(normalized) >= 4:
            _learn_module_alias(normalized, key)
            return key

    # Strategy 3: word overlap with module descriptions
    # Split the unknown module name into words and check against each module's description
    unknown_words = set(normalized.replace("_", " ").split())
    # Remove very common words that would match everything
    _stopwords = {"the", "a", "an", "is", "are", "for", "of", "in", "to", "and", "or", "by", "with"}
    unknown_words -= _stopwords

    if unknown_words:
        best_match = None
        best_score = 0
        for key, cfg in MODULES.items():
            desc_lower = cfg.get("description", "").lower()
            # Count how many words from the unknown name appear in the description
            score = sum(1 for w in unknown_words if w in desc_lower)
            # Bonus: if the unknown name appears as a whole in the description
            if normalized.replace("_", " ") in desc_lower:
                score += 3
            if score > best_score:
                best_score = score
                best_match = key

        # Require at least 2 word matches OR a whole-phrase match (score >= 2)
        # to avoid false positives from single common words
        if best_match and best_score >= 2:
            logger.info(
                f"Module resolved via description match: '{unknown}' -> '{best_match}' "
                f"(score={best_score}, words={unknown_words})"
            )
            _learn_module_alias(normalized, best_match)
            return best_match

    # Strategy 4: domain name match from table catalog
    try:
        for key, domains in TABLE_DOMAINS.items():
            if isinstance(domains, list):
                for domain in domains:
                    if normalized in domain.lower() or domain.lower() in normalized:
                        _learn_module_alias(normalized, key)
                        return key
    except Exception:
        pass

    return None


class IntentDetectionError(Exception):
    pass


def detect_intents(
    query: str,
    current_module: str = None,
    conversation_history: Optional[List[Dict[str, str]]] = None,
    context: PipelineContext = None,
    retry_history: Optional[RetryHistory] = None,
    db_session=None,
    previous_module: str = None,
    previous_domains: list = None,
) -> List[IntentResult]:
    """Detect user intent(s) from a natural language query.

    Returns up to 3 ranked ``IntentResult`` objects from a single LLM call.
    The LLM is asked to return multiple interpretations when the query is
    ambiguous; for unambiguous queries it may return just one.

    Results are sorted by confidence (descending) and deduplicated by module.

    Args:
        query: The user's question
        current_module: Current frontend module context
        conversation_history: Last N messages for multi-turn context
        context: Pipeline context to populate with intent metadata
        retry_history: Accumulated failure context from previous pipeline attempts.
        db_session: Optional DB session for RAG retrieval.
        previous_module: Module from the previous conversation turn.
        previous_domains: Domains from the previous conversation turn.

    Returns:
        List of 1-3 IntentResult objects sorted by confidence descending.
    """
    client, deployment = get_llm_client(tier="fast")

    # RAG retrieval: targeted synonyms/glossary for this specific query
    rag_t0 = time.time()
    rag_synonym_ctx = None
    rag_glossary_ctx = None
    if db_session is not None:
        try:
            from app.utils.jay.rag_retriever import retrieve_synonyms, retrieve_glossary
            rag_synonym_ctx = retrieve_synonyms(query, db_session)
            rag_glossary_ctx = retrieve_glossary(query, db_session)
        except Exception as e:
            logger.warning(f"RAG retrieval failed for intent detection, using static: {e}")
    rag_elapsed = (time.time() - rag_t0) * 1000
    logger.info(f"[TIMING] Intent RAG retrieval: {rag_elapsed:.0f}ms")

    system_prompt = build_module_selection_prompt(
        current_module,
        rag_synonym_ctx=rag_synonym_ctx,
        rag_glossary_ctx=rag_glossary_ctx,
        previous_module=previous_module,
        previous_domains=previous_domains,
    )

    # Inject retry history into system prompt so LLM reconsiders its approach
    if retry_history and retry_history.attempts:
        retry_ctx = (
            "\n\n--------------------------------------------------\n"
            "CRITICAL: PREVIOUS ATTEMPTS FAILED — RECONSIDER YOUR APPROACH\n"
            "--------------------------------------------------\n\n"
            "The previous intent detection and SQL generation failed. "
            "You must reconsider your module selection, domain choices, and entity interpretation.\n\n"
            f"{retry_history.summary_for_llm()}\n\n"
            "Based on these failures:\n"
            "1. Consider if a DIFFERENT module would be more appropriate.\n"
            "2. Consider if DIFFERENT or ADDITIONAL domains are needed.\n"
            "3. Re-examine entity mentions — maybe the entity type was wrong.\n"
            "4. Consider if a simpler query approach would avoid the errors.\n"
            "5. If column/table errors occurred, choose domains that contain the right tables.\n"
        )
        system_prompt += retry_ctx

    messages = [{"role": "system", "content": system_prompt}]

    # Add conversation history for multi-turn context (last 10 messages)
    if conversation_history:
        for msg in conversation_history[-10:]:
            messages.append({
                "role": msg.get("role", "user"),
                "content": msg.get("content", ""),
            })

    messages.append({"role": "user", "content": query})

    try:
        llm_t0 = time.time()
        resp = client.chat.completions.create(
            model=deployment,
            temperature=0.1,
            messages=messages,
            response_format={"type": "json_object"},
        )
        llm_elapsed = (time.time() - llm_t0) * 1000
        logger.info(f"[TIMING] Intent LLM call: {llm_elapsed:.0f}ms")

        raw = (resp.choices[0].message.content or "").strip()
        data = json.loads(raw)

    except json.JSONDecodeError:
        raise IntentDetectionError("LLM returned invalid JSON")
    except Exception as e:
        logger.error(f"Intent detection LLM call failed: {e}")
        raise IntentDetectionError(f"LLM service error: {str(e)[:100]}")

    results = _parse_intent_results(data)

    # Log first (best) result
    if results:
        best = results[0]
        logger.info(
            f"Intent detected ({len(results)} interpretation(s)): "
            f"action={best.action}, module={best.module}, "
            f"domains={best.domains}, format={best.format_hint}"
        )

    # Populate pipeline context with primary intent metadata
    if context is not None and results:
        best = results[0]
        context.add("intent", "action", best.action)
        if best.module:
            # Use user_summary from the raw data if available
            reasoning = best.user_summary or f"Matched module {best.module}"
            context.add("intent", "module_reasoning", reasoning)
        context.add("intent", "confidence", best.confidence)
        context.add("intent", "num_interpretations", len(results))
        if best.format_hint and best.format_hint != "auto":
            context.add("intent", "format_rationale", f"Format hint: {best.format_hint}")
        if best.assumption:
            context.add("intent", "assumption", best.assumption)

    return results


def detect_intent(
    query: str,
    current_module: str = None,
    conversation_history: Optional[List[Dict[str, str]]] = None,
    context: PipelineContext = None,
    retry_history: Optional[RetryHistory] = None,
    db_session=None,
    previous_module: str = None,
    previous_domains: list = None,
) -> IntentResult:
    """Detect user intent from a natural language query.

    Convenience wrapper around :func:`detect_intents` that returns the single
    best (highest-confidence) interpretation.

    Args:
        query: The user's question
        current_module: Current frontend module context
        conversation_history: Last N messages for multi-turn context
        retry_history: Accumulated failure context from previous pipeline attempts.
        db_session: Optional DB session for RAG retrieval.
        previous_module: Module from the previous conversation turn.
        previous_domains: Domains from the previous conversation turn.

    Returns:
        IntentResult with action, module, entity mentions, format hint, etc.
    """
    results = detect_intents(
        query=query,
        current_module=current_module,
        conversation_history=conversation_history,
        context=context,
        retry_history=retry_history,
        db_session=db_session,
        previous_module=previous_module,
        previous_domains=previous_domains,
    )
    if not results:
        raise IntentDetectionError("No intent could be detected")
    return results[0]


def _parse_intent_results(data: dict) -> List[IntentResult]:
    """Parse LLM JSON into a list of IntentResult objects.

    Supports two formats:
    1. Multi-intent: ``{"interpretations": [{...}, {...}]}``
    2. Legacy single-intent: ``{"action": "data_query", "module": ...}``

    Returns results sorted by confidence descending, deduplicated by module.
    """
    interpretations = data.get("interpretations")

    if isinstance(interpretations, list) and interpretations:
        # Multi-intent format
        raw_results = []
        for interp in interpretations:
            if not isinstance(interp, dict):
                continue
            try:
                raw_results.append(_parse_intent_result(interp))
            except IntentDetectionError as e:
                logger.warning(f"Skipping invalid interpretation: {e}")
                continue
        if not raw_results:
            # All interpretations were invalid; fall back to treating the
            # top-level dict as a legacy single-intent response
            return [_parse_intent_result(data)]
    else:
        # Legacy single-intent format
        raw_results = [_parse_intent_result(data)]

    # Sort by confidence descending
    raw_results.sort(key=lambda r: r.confidence, reverse=True)

    # Deduplicate by module (keep highest-confidence for each module)
    seen_modules = set()
    deduped: List[IntentResult] = []
    for r in raw_results:
        key = r.module or r.action  # group non-data actions by action type
        if key in seen_modules:
            continue
        seen_modules.add(key)
        deduped.append(r)

    # Cap at 3
    return deduped[:3]


def _parse_intent_result(data: dict) -> IntentResult:
    """Convert raw LLM JSON response to IntentResult."""
    action = data.get("action", "data_query")

    # Parse entity mentions
    entity_mentions = []
    for em in data.get("entity_mentions", []):
        if isinstance(em, dict) and em.get("entity_type") and em.get("raw_value"):
            entity_mentions.append(
                EntityMention(
                    entity_type=em["entity_type"],
                    raw_value=em["raw_value"],
                )
            )

    # Normalize and validate module for data queries
    module = data.get("module")
    if action == "data_query" and module:
        if module not in MODULES:
            resolved = _fuzzy_resolve_module(module)
            if resolved:
                logger.info(f"Module dynamically resolved: '{module}' -> '{resolved}'")
                module = resolved
            else:
                logger.warning(f"Invalid module '{module}', all resolution strategies failed")
                raise IntentDetectionError(f"Invalid module: {module}")

    confidence = data.get("confidence", 0.5)
    if not isinstance(confidence, (int, float)):
        confidence = 0.5
    confidence = max(0.0, min(1.0, float(confidence)))

    # Parse domains — LLM may return them; fall back to module-based mapping
    raw_domains = data.get("domains", [])
    if isinstance(raw_domains, str):
        raw_domains = [raw_domains]
    # Validate domain names against catalog
    domains = [d for d in raw_domains if d in TABLE_DOMAINS]
    # Fallback: derive domains from module if LLM didn't specify
    if not domains and module:
        domains = get_domain_for_module(module)

    return IntentResult(
        action=action,
        module=module,
        user_summary=data.get("user_summary"),
        format_hint=data.get("format_hint", "auto"),
        entity_mentions=entity_mentions,
        comparison_periods=data.get("comparison_periods"),
        domains=domains,
        confidence=confidence,
        route=data.get("route"),
        filters=data.get("filters"),
        message=data.get("message"),
        answer_type=data.get("answer_type"),
        assumption=data.get("assumption"),
    )

