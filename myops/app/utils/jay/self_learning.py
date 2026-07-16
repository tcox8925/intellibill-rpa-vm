"""
JAI Self-Learning Module - Mine successful query/SQL pairs and embed them.

Functions:
- mine_successful_pairs: Extract (query, SQL) pairs from positively-rated conversations
- mine_correction_pairs: Find thumbs-down -> thumbs-up correction patterns
- store_negative_example: Store a failed query/SQL pair as a negative example
- update_learned_examples: Mine pairs and embed them into jay_knowledge_embeddings

Note: update_learned_examples() should be called from the rating endpoint or a
periodic scheduler to keep learned examples up to date.
"""

import json
import logging
import re
from datetime import datetime, timezone
from typing import List, Optional

from sqlalchemy import text
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


def mine_successful_pairs(
    db_session: Session,
    since: Optional[datetime] = None,
    limit: int = 500,
) -> List[dict]:
    """Extract (query, SQL) pairs from successful JAI conversations.

    Criteria for a "successful" pair:
    - Assistant message has role='assistant'
    - Message data contains '_sql' key (indicates SQL was generated)
    - The response had data (non-empty result)
    - Rating is explicitly 1 (thumbs up), OR
    - Rating is NULL but the user sent a follow-up message in the same
      conversation (implicit acceptance — the user continued the conversation
      rather than abandoning it)

    A NULL rating with no follow-up is excluded because the user never
    expressed any positive signal.

    Returns list of dicts: {query, sql, module, rating, conversation_id}
    """
    sql = text("""
        SELECT
            m_user.content AS query,
            m_asst.data->>'_sql' AS sql,
            m_asst.data->>'_module' AS module,
            m_asst.rating,
            m_asst.conversation_id
        FROM wpo.jay_messages m_asst
        JOIN wpo.jay_messages m_user
            ON m_user.conversation_id = m_asst.conversation_id
            AND m_user.role = 'user'
            AND m_user.created_at < m_asst.created_at
        WHERE m_asst.role = 'assistant'
            AND m_asst.data->>'_sql' IS NOT NULL
            AND m_asst.data->>'_sql' != ''
            AND (
                m_asst.rating = 1
                OR (
                    m_asst.rating IS NULL
                    AND EXISTS (
                        SELECT 1
                        FROM wpo.jay_messages m2
                        WHERE m2.conversation_id = m_asst.conversation_id
                            AND m2.created_at > m_asst.created_at
                            AND m2.role = 'user'
                    )
                )
            )
            AND (:since IS NULL OR m_asst.created_at >= :since)
        ORDER BY m_asst.created_at DESC
        LIMIT :limit
    """)

    result = db_session.execute(sql, {"since": since, "limit": limit})

    # Deduplicate by SQL fingerprint
    seen_fingerprints: set = set()
    pairs: List[dict] = []
    for row in result.fetchall():
        fp = _sql_fingerprint(row.sql)
        if fp not in seen_fingerprints:
            seen_fingerprints.add(fp)
            pairs.append({
                "query": row.query,
                "sql": row.sql,
                "module": row.module,
                "rating": row.rating,
                "conversation_id": str(row.conversation_id),
            })

    logger.info(
        "Mined %d unique successful pairs (from %d total, since=%s)",
        len(pairs),
        len(seen_fingerprints),
        since,
    )
    return pairs


def mine_correction_pairs(
    db_session: Session,
    since_days: int = 30,
) -> list:
    """Find (thumbs-down, thumbs-up) pairs in same conversation.

    These represent user corrections and are high-value learning signals.
    When a user gives a thumbs-down on one response and then rephrases
    their question to get a thumbs-up result, the correction pattern
    teaches the LLM both what NOT to do and what TO do.

    Returns list of dicts with keys:
        - negative: {query, sql, module, message_id}
        - positive: {query, sql, module, message_id}
        - conversation_id
    """
    sql = text("""
        WITH bad AS (
            SELECT
                m_bad.id AS bad_id,
                m_bad.conversation_id,
                m_bad.created_at AS bad_at,
                m_bad.data->>'_sql' AS bad_sql,
                m_bad.data->>'_module' AS bad_module,
                m_bad_user.content AS bad_query
            FROM wpo.jay_messages m_bad
            JOIN wpo.jay_messages m_bad_user
                ON m_bad_user.conversation_id = m_bad.conversation_id
                AND m_bad_user.role = 'user'
                AND m_bad_user.created_at < m_bad.created_at
                AND NOT EXISTS (
                    SELECT 1 FROM wpo.jay_messages mx
                    WHERE mx.conversation_id = m_bad.conversation_id
                        AND mx.role = 'user'
                        AND mx.created_at > m_bad_user.created_at
                        AND mx.created_at < m_bad.created_at
                )
            WHERE m_bad.role = 'assistant'
                AND m_bad.rating = -1
                AND m_bad.data->>'_sql' IS NOT NULL
                AND m_bad.created_at >= NOW() - MAKE_INTERVAL(days => :since_days)
        ),
        good AS (
            SELECT
                m_good.id AS good_id,
                m_good.conversation_id,
                m_good.created_at AS good_at,
                m_good.data->>'_sql' AS good_sql,
                m_good.data->>'_module' AS good_module,
                m_good_user.content AS good_query
            FROM wpo.jay_messages m_good
            JOIN wpo.jay_messages m_good_user
                ON m_good_user.conversation_id = m_good.conversation_id
                AND m_good_user.role = 'user'
                AND m_good_user.created_at < m_good.created_at
                AND NOT EXISTS (
                    SELECT 1 FROM wpo.jay_messages mx
                    WHERE mx.conversation_id = m_good.conversation_id
                        AND mx.role = 'user'
                        AND mx.created_at > m_good_user.created_at
                        AND mx.created_at < m_good.created_at
                )
            WHERE m_good.role = 'assistant'
                AND m_good.rating = 1
                AND m_good.data->>'_sql' IS NOT NULL
                AND m_good.created_at >= NOW() - MAKE_INTERVAL(days => :since_days)
        )
        SELECT
            bad.bad_id,
            bad.bad_query,
            bad.bad_sql,
            bad.bad_module,
            good.good_id,
            good.good_query,
            good.good_sql,
            good.good_module,
            bad.conversation_id
        FROM bad
        JOIN good
            ON good.conversation_id = bad.conversation_id
            AND good.good_at > bad.bad_at
        ORDER BY bad.bad_at DESC
    """)

    result = db_session.execute(sql, {"since_days": since_days})

    # Deduplicate by conversation_id (take first correction pair per conversation)
    seen_conversations: set = set()
    correction_pairs: list = []
    for row in result.fetchall():
        conv_id = str(row.conversation_id)
        if conv_id in seen_conversations:
            continue
        seen_conversations.add(conv_id)
        correction_pairs.append({
            "negative": {
                "query": row.bad_query,
                "sql": row.bad_sql,
                "module": row.bad_module,
                "message_id": str(row.bad_id),
            },
            "positive": {
                "query": row.good_query,
                "sql": row.good_sql,
                "module": row.good_module,
                "message_id": str(row.good_id),
            },
            "conversation_id": conv_id,
        })

    logger.info(
        "Mined %d correction pairs (since %d days)",
        len(correction_pairs),
        since_days,
    )
    return correction_pairs


def detect_implicit_feedback(
    current_query: str,
    previous_assistant_message: dict,
) -> str:
    """Detect implicit feedback from a user's follow-up message.

    Analyzes the user's new query in context of the previous assistant response
    to detect:
    - CORRECTION: User is correcting/rejecting the previous response
    - CONFIRMATION: User is confirming/accepting the previous response
    - NEUTRAL: Just a follow-up question, no implicit feedback

    Args:
        current_query: The new user message
        previous_assistant_message: The last assistant message dict with 'content', 'data', etc.

    Returns:
        One of: "correction", "confirmation", "neutral"
    """
    return _classify_feedback_signal(current_query)


def detect_multi_turn_feedback(
    current_query: str,
    recent_messages: list,
) -> List[dict]:
    """Detect implicit feedback across a window of recent messages.

    Scans the last N assistant messages (not just the immediate previous one)
    to find feedback signals. This catches cases like:
    - User confirms 3 messages later: "thanks, that first answer was great"
    - User corrects across turns: "no I keep saying, I meant individuals"
    - Topic abandonment: user asked about X, never followed up (soft negative)

    Args:
        current_query: The new user message
        recent_messages: Last 4-5 messages (alternating user/assistant) from
                         conversation history, each as dict with id, role, content, data.

    Returns:
        List of {message_id, feedback_type} for each message that received feedback.
        Empty list if no feedback detected.
    """
    results = []
    q = current_query.lower().strip()

    # Direct feedback on the immediate previous message (strongest signal)
    signal = _classify_feedback_signal(current_query)
    if signal != "neutral":
        # Find the most recent assistant message
        for msg in reversed(recent_messages):
            if msg.get("role") == "assistant" and msg.get("data"):
                results.append({
                    "message_id": msg.get("id"),
                    "message": msg,
                    "feedback_type": signal,
                    "strength": "strong",
                })
                break
        return results  # Direct signal always targets the last message

    # ------------------------------------------------------------------
    # Multi-turn signals: check if the user references an earlier answer
    # ------------------------------------------------------------------

    # "that first answer" / "the earlier one" / "go back to" patterns
    back_reference_patterns = [
        "first answer", "earlier answer", "the one before",
        "go back to", "the original", "you first said",
        "your earlier", "before that",
    ]
    references_earlier = any(p in q for p in back_reference_patterns)

    # Gratitude with no specific question = confirmation of recent work
    gratitude_patterns = [
        "thanks for", "thank you for", "that helped",
        "this is helpful", "this helps", "appreciate it",
        "this is great", "this is perfect", "this is exactly",
        "all good", "looks good", "that's all",
    ]
    is_gratitude = any(p in q for p in gratitude_patterns)

    if is_gratitude:
        # Gratitude confirms ALL recent assistant messages that have SQL data
        for msg in recent_messages:
            if msg.get("role") == "assistant" and msg.get("data"):
                data = msg["data"] if isinstance(msg["data"], dict) else {}
                if data.get("_sql"):
                    results.append({
                        "message_id": msg.get("id"),
                        "message": msg,
                        "feedback_type": "confirmation",
                        "strength": "weak",
                    })
        return results

    if references_earlier:
        # Referencing an earlier answer — check if positive or negative
        sub_signal = "neutral"
        positive_ref = ["was right", "was correct", "was good", "was helpful", "was what i"]
        negative_ref = ["was wrong", "was incorrect", "wasn't right", "wasn't what"]
        if any(p in q for p in positive_ref):
            sub_signal = "confirmation"
        elif any(p in q for p in negative_ref):
            sub_signal = "correction"

        if sub_signal != "neutral":
            # Find assistant messages with SQL data (skip the most recent one)
            assistant_msgs = [
                m for m in recent_messages
                if m.get("role") == "assistant" and m.get("data")
            ]
            # Target the earliest assistant message in the window
            if len(assistant_msgs) >= 2:
                target = assistant_msgs[0]
                results.append({
                    "message_id": target.get("id"),
                    "message": target,
                    "feedback_type": sub_signal,
                    "strength": "medium",
                })
        if results:
            return results

    # ------------------------------------------------------------------
    # Implicit weak positive: any follow-up after a successful data
    # response implies the user found the previous answer at least useful
    # enough to continue the conversation. Only count it if the previous
    # assistant message had actual data (SQL present = successful query).
    # ------------------------------------------------------------------
    for msg in reversed(recent_messages):
        if msg.get("role") == "assistant":
            data = msg.get("data") if isinstance(msg.get("data"), dict) else {}
            if data.get("_sql"):
                results.append({
                    "message_id": msg.get("id"),
                    "message": msg,
                    "feedback_type": "confirmation",
                    "strength": "weak",
                })
            break  # Only consider the most recent assistant message

    return results


def _classify_feedback_signal(query: str) -> str:
    """Classify a single query as correction, confirmation, or neutral."""
    q = query.lower().strip()

    correction_patterns = [
        "no,", "no ", "not what i", "that's not", "thats not", "that is not",
        "i meant", "i mean", "i'm talking about", "im talking about",
        "i am talking about", "actually,", "actually ", "wrong",
        "incorrect", "not correct", "these are not", "those are not",
        "i was asking", "i asked about", "not these", "not those",
        "different", "i don't want", "i dont want", "not right",
    ]

    confirmation_patterns = [
        "yes,", "yes ", "yes!", "yeah", "yep", "correct", "exactly",
        "that's right", "thats right", "that is right", "right,",
        "perfect", "great,", "great!", "good,", "good ", "thanks",
        "thank you", "looking for this", "what i wanted", "what i needed",
        "now also", "now tell", "now show", "also show", "also tell",
        "and also", "can you also", "what about", "how about",
    ]

    for pattern in correction_patterns:
        if q.startswith(pattern) or f" {pattern}" in f" {q}":
            return "correction"

    for pattern in confirmation_patterns:
        if q.startswith(pattern) or f" {pattern}" in f" {q}":
            return "confirmation"

    return "neutral"


def process_implicit_feedback(
    feedback_type: str,
    previous_message: dict,
    db_session: Session,
    strength: str = "strong",
) -> None:
    """Process implicit feedback from conversation flow.

    Args:
        feedback_type: "correction" or "confirmation"
        previous_message: The assistant message dict with data containing _sql
        db_session: DB session for storing learning data
        strength: "strong", "medium", or "weak" — weak confirmations only
                  set rating, strong corrections also store negative examples.
    """
    if feedback_type == "neutral":
        return

    data = previous_message.get("data") or {}
    sql = data.get("_sql")
    query = previous_message.get("content", "")[:500]

    if not sql:
        return

    try:
        if feedback_type == "correction":
            logger.info("Implicit CORRECTION detected (strength=%s) — storing negative example", strength)
            store_negative_example(
                query=query,
                failed_sql=sql,
                error_message=f"User corrected this response in follow-up conversation (strength: {strength})",
                db_session=db_session,
            )
        elif feedback_type == "confirmation":
            logger.info("Implicit CONFIRMATION detected (strength=%s) — storing as positive rating", strength)
            msg_id = previous_message.get("id")
            if msg_id:
                db_session.execute(
                    text("UPDATE wpo.jay_messages SET rating = 1 WHERE id = :id AND rating IS NULL"),
                    {"id": str(msg_id)},
                )
                db_session.commit()
    except Exception:
        logger.exception("Failed to process implicit feedback")


def store_negative_example(
    query: str,
    failed_sql: str,
    error_message: str,
    db_session: Session,
) -> bool:
    """Store a (query, failed_sql, error) as negative example in knowledge_embeddings.

    These are used to teach the LLM what NOT to do.
    Category: 'negative_example'
    Score weight: 0.3 (low, so they appear in context but don't dominate)

    Returns True if stored successfully, False otherwise.
    """
    from app.utils.jay.llm_client import embed_texts

    emb_text = (
        f"Query: {query}\n"
        f"Failed SQL: {failed_sql}\n"
        f"Error: {error_message}\n"
        f"DO NOT generate similar SQL for this type of query."
    )

    try:
        embeddings = embed_texts([emb_text])
    except Exception:
        logger.exception("Failed to generate embedding for negative example")
        return False

    now = datetime.now(timezone.utc)

    # Ensure the unique index exists
    _ensure_learned_unique_index(db_session)

    upsert_sql = text("""
        INSERT INTO wpo.jay_knowledge_embeddings
            (category, text, metadata, embedding, score_weight, created_at, updated_at)
        VALUES ('negative_example', :text, :metadata, :embedding, :score, :now, :now)
        ON CONFLICT (category, md5(text)) DO UPDATE SET
            embedding = EXCLUDED.embedding,
            score_weight = EXCLUDED.score_weight,
            metadata = EXCLUDED.metadata,
            updated_at = EXCLUDED.updated_at
    """)

    try:
        db_session.execute(upsert_sql, {
            "text": emb_text,
            "metadata": json.dumps({
                "query": query,
                "failed_sql": failed_sql,
                "error_message": error_message,
            }),
            "embedding": json.dumps(embeddings[0]),
            "score": 0.3,
            "now": now,
        })
        db_session.commit()
        logger.info("Stored negative example for query: %.80s", query)
        return True
    except Exception:
        db_session.rollback()
        logger.exception("Failed to store negative example for query: %.80s", query)
        return False


def _sql_fingerprint(sql_str: str) -> str:
    """Create a fingerprint of SQL for deduplication.

    Strips comments, replaces string literals and numeric literals with
    placeholders, normalises whitespace, and lowercases.
    """
    s = re.sub(r"--[^\n]*", "", sql_str)
    s = re.sub(r"/\*.*?\*/", "", s, flags=re.S)
    s = re.sub(r"'[^']*'", "'?'", s)  # Replace string literals
    s = re.sub(r"\b\d+\b", "?", s)  # Replace numbers
    s = re.sub(r"\s+", " ", s).strip().lower()
    return s


# ---------------------------------------------------------------------------
# Task 6.4 - Auto-Embed Learned Examples
# ---------------------------------------------------------------------------

# Base score for learned examples; positively-rated pairs get a 1.5x boost
_LEARNED_BASE_SCORE = 0.85
_POSITIVE_BOOST_MULTIPLIER = 1.5


def update_learned_examples(db_session: Session) -> dict:
    """Mine successful pairs and embed them as learned examples.

    Embeds each pair into wpo.jay_knowledge_embeddings with category='learned'.
    Uses ON CONFLICT upsert keyed on (category, md5(text)) -- a unique constraint
    on those two columns (jay_knowledge_emb_category_text_uq) was created by
    the seed script using MD5 hash of text to keep the index size manageable.

    Should be called from the rating endpoint or a periodic scheduler to keep
    learned examples up to date.

    Returns:
        Dict with keys: mined, embedded, corrections
    """
    from app.utils.jay.llm_client import embed_texts

    # Ensure the unique index exists for the upsert to work
    _ensure_learned_unique_index(db_session)

    pairs = mine_successful_pairs(db_session)
    if not pairs:
        logger.info("No successful pairs to embed.")
        return {"mined": 0, "embedded": 0, "corrections": 0}

    # Build embedding texts
    texts: List[str] = []
    for pair in pairs:
        emb_text = f"Example query: {pair['query']}\nSQL: {pair['sql']}"
        if pair["module"]:
            emb_text += f"\nModule: {pair['module']}"
        texts.append(emb_text)

    # Generate embeddings
    try:
        embeddings = embed_texts(texts)
    except Exception:
        logger.exception("Failed to generate embeddings for learned examples")
        return {"mined": len(pairs), "embedded": 0, "corrections": 0}

    # Upsert into jay_knowledge_embeddings
    now = datetime.now(timezone.utc)
    upsert_sql = text("""
        INSERT INTO wpo.jay_knowledge_embeddings
            (category, text, metadata, embedding, score_weight, created_at, updated_at)
        VALUES ('learned', :text, :metadata, :embedding, :score, :now, :now)
        ON CONFLICT (category, md5(text)) DO UPDATE SET
            embedding = EXCLUDED.embedding,
            score_weight = EXCLUDED.score_weight,
            metadata = EXCLUDED.metadata,
            updated_at = EXCLUDED.updated_at
    """)

    embedded = 0
    for pair, emb_text, embedding in zip(pairs, texts, embeddings):
        score = _LEARNED_BASE_SCORE
        if pair["rating"] == 1:
            score = _LEARNED_BASE_SCORE * _POSITIVE_BOOST_MULTIPLIER  # 1.275

        try:
            db_session.execute(upsert_sql, {
                "text": emb_text,
                "metadata": json.dumps({
                    "query": pair["query"],
                    "sql": pair["sql"],
                    "module": pair["module"],
                }),
                "embedding": json.dumps(embedding),
                "score": score,
                "now": now,
            })
            embedded += 1
        except Exception:
            logger.exception(
                "Failed to upsert learned example for query: %.80s",
                pair["query"],
            )

    db_session.commit()

    # Also process correction pairs
    corrections_stored = _process_correction_pairs(db_session)

    # Backfill query cache (v2 pipeline) with successful pairs
    cache_stored = _backfill_query_cache(pairs, db_session)

    logger.info(
        "Embedded %d learned examples (from %d mined pairs), %d correction pairs, %d cache entries",
        embedded, len(pairs), corrections_stored, cache_stored,
    )
    return {"mined": len(pairs), "embedded": embedded, "corrections": corrections_stored, "cache_stored": cache_stored}


def _backfill_query_cache(pairs: List[dict], db_session: Session) -> int:
    """Store successful query/SQL pairs in the v2 query cache.

    This backfills jay_query_cache with historical successful queries so
    the v2 pipeline can find them via get_similar_queries tool.

    Args:
        pairs: List of {query, sql, module} dicts from mine_successful_pairs.
        db_session: Active DB session.

    Returns:
        Number of pairs stored in the cache.
    """
    if not pairs:
        return 0

    stored = 0
    try:
        from app.utils.jay.query_cache import store_successful_query
    except ImportError:
        logger.debug("query_cache module not available; skipping backfill")
        return 0

    for pair in pairs:
        try:
            store_successful_query(
                query=pair["query"],
                spec="",
                sql=pair["sql"],
                module=pair.get("module", ""),
                domains=[],
                exec_ms=0,
                db_session=db_session,
            )
            stored += 1
        except Exception:
            logger.debug("Failed to backfill cache for: %.60s", pair["query"])

    logger.info("Backfilled %d entries to query cache", stored)
    return stored


def _process_correction_pairs(db_session: Session) -> int:
    """Mine correction pairs and store the positive side as learned, negative as negative_example.

    Returns count of correction pairs processed.
    """
    correction_pairs = mine_correction_pairs(db_session)
    if not correction_pairs:
        return 0

    processed = 0
    for cp in correction_pairs:
        neg = cp["negative"]
        pos = cp["positive"]

        # Store the negative side
        store_negative_example(
            query=neg["query"],
            failed_sql=neg["sql"],
            error_message=f"User gave thumbs-down. Corrected query: {pos['query']}",
            db_session=db_session,
        )

        # The positive side will already be picked up by mine_successful_pairs
        # (since it has rating=1), so no extra action needed here.
        processed += 1

    logger.info("Processed %d correction pairs", processed)
    return processed


def _ensure_learned_unique_index(db_session: Session) -> None:
    """Create unique constraint on (category, md5(text)) if it does not exist.

    The seed script creates this as constraint jay_knowledge_emb_category_text_uq.
    This function is a safety net in case the seed has not been run yet.
    """
    try:
        db_session.execute(text("""
            CREATE UNIQUE INDEX IF NOT EXISTS jay_knowledge_emb_category_text_uq
                ON wpo.jay_knowledge_embeddings (category, md5(text))
        """))
        db_session.commit()
    except Exception:
        db_session.rollback()
        logger.debug(
            "Unique index jay_knowledge_emb_category_text_uq already exists "
            "or could not be created (may be fine if it exists)."
        )
