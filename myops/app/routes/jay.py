"""
JAI AI Assistant - API Routes

Endpoints:
- POST /jay/chat - Main chat endpoint
- POST /jay/chat/stream - SSE streaming chat endpoint
- GET /jay/conversations - List conversations
- GET /jay/conversations/{id} - Get conversation with messages
- DELETE /jay/conversations/{id} - Delete conversation
- POST /jay/favorites - Add favorite
- DELETE /jay/favorites/{id} - Remove favorite
- GET /jay/favorites - List favorites
- POST /jay/favorites/{id}/execute - Re-execute favorite
- GET /jay/suggestions - Get suggestions
- GET /jay/trending - Top 10 trending queries
"""

import asyncio
import copy
import csv
import io
import json
import os
import queue
import re
import threading
import time as _time
import uuid
import logging
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from sqlalchemy import desc, func, or_

from app.db.session import get_db, get_synapse_db
from app.middleware.validator import get_current_user
from app.models.jay import JayConversation, JayMessage, JayFavorite
from app.models.Entity import Entity

# New pipeline imports
from app.utils.jay.intent_detector import detect_intent, detect_intents, IntentDetectionError
from app.utils.jay.pipeline_models import IntentResult, ResolvedEntities, EntityMention, PipelineContext
from app.utils.jay.pipeline import run_data_query_pipeline, run_column_retrieval
from app.utils.jay.parallel import ParallelPipelineExecutor, create_worker_session, create_worker_synapse_session, WorkerResult
from app.utils.jay.resolver import Resolver
from app.utils.jay.semantic_registry import MODULES, GLOBAL_ENTITIES
from app.utils.jay.permissions import get_permitted_entity_ids

# Kept from original
from app.utils.jay.intent_parser import synthesize_response
from app.utils.jay.format_engine import decide_format, compute_sql_fingerprint, _clean_datetime, build_dashboard_sections
from app.utils.jay.suggestions import merge_suggestions

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/jay", tags=["JAI Assistant"])

# Keys in message.data that are admin-only (SQL, pipeline debug logs)
_ADMIN_ONLY_DATA_KEYS = {"_sql", "_pipeline_log", "_assumption"}


def _strip_admin_data(data: dict) -> dict:
    """Return a copy of the data dict with admin-only keys removed.

    Used to prevent SQL and pipeline debug logs from being exposed
    to non-admin users in API responses.
    """
    if not data or not isinstance(data, dict):
        return data
    return {k: v for k, v in data.items() if k not in _ADMIN_ONLY_DATA_KEYS}


# -------------------------------------------------------
# REQUEST / RESPONSE MODELS
# -------------------------------------------------------

class ChatContext(BaseModel):
    entity_id: Optional[str] = None
    sub_entity_id: Optional[str] = None
    current_page: Optional[str] = None
    current_module: Optional[str] = None


class ChatRequest(BaseModel):
    conversation_id: Optional[str] = None
    query: str = Field(..., min_length=1, max_length=2000)
    context: ChatContext = ChatContext()


class FavoriteRequest(BaseModel):
    prompt_text: str = Field(..., min_length=1, max_length=2000)
    sql_fingerprint: Optional[str] = None
    module: Optional[str] = None


class FavoriteExecuteRequest(BaseModel):
    context: ChatContext = ChatContext()
    conversation_id: Optional[str] = None


# -------------------------------------------------------
# CHAT ENDPOINT
# -------------------------------------------------------

@router.post("/chat")
async def chat(
    request: Request,
    body: ChatRequest,
    db: Session = Depends(get_db),
):
    """Main chat endpoint - processes user query through the JAI pipeline."""
    user = get_current_user(request)
    user_id = str(user.get("user_id", user.get("id")))

    # Load or create conversation
    conversation = None
    conversation_history = []
    conversation_history_sql = None
    previous_module = None
    previous_domains = None
    previous_user_summary = None

    if body.conversation_id:
        conversation = db.query(JayConversation).filter(
            JayConversation.id == body.conversation_id,
            JayConversation.user_id == user_id,
        ).first()

        if conversation:
            # Load last 10 messages for context
            messages = (
                db.query(JayMessage)
                .filter(JayMessage.conversation_id == conversation.id)
                .order_by(JayMessage.created_at)
                .all()
            )
            conversation_history = [
                {"role": m.role, "content": m.content}
                for m in messages[-10:]
            ]
            # Extract last successful SQL and metadata for multi-turn context
            for m in reversed(messages):
                if m.role == "assistant" and m.data and isinstance(m.data, dict):
                    last_sql = m.data.get("_sql")
                    if last_sql:
                        conversation_history_sql = last_sql
                        previous_module = m.data.get("_module")
                        previous_domains = m.data.get("_domains", [])
                        previous_user_summary = m.data.get("_user_summary", "")
                        break

            # --- Implicit Feedback from Conversation Flow ---
            # Scan last 5 messages for corrections, confirmations, and multi-turn signals
            try:
                from app.utils.jay.self_learning import (
                    detect_multi_turn_feedback, process_implicit_feedback,
                )
                # Build a window of recent messages (last 10 entries = ~5 turns)
                recent_window = [
                    {
                        "id": str(m.id),
                        "role": m.role,
                        "content": m.content or "",
                        "data": m.data if isinstance(m.data, dict) else {},
                    }
                    for m in messages[-10:]
                ]
                feedback_hits = detect_multi_turn_feedback(body.query, recent_window)
                for hit in feedback_hits:
                    logger.info(
                        "Implicit feedback: %s (strength=%s) on message %s",
                        hit["feedback_type"], hit["strength"], hit["message_id"],
                    )
                    process_implicit_feedback(
                        hit["feedback_type"],
                        hit["message"],
                        db,
                        strength=hit["strength"],
                    )
            except Exception as e:
                logger.warning(f"Implicit feedback detection failed (non-fatal): {e}")

    if not conversation:
        entity_id_val = body.context.entity_id if body.context and body.context.entity_id else None
        entity_name_val = None
        if entity_id_val:
            entity_row = db.query(Entity.entity_name).filter(Entity.entity_id == entity_id_val).first()
            entity_name_val = entity_row.entity_name if entity_row else None

        conversation = JayConversation(
            id=uuid.uuid4(),
            user_id=user_id,
            title=body.query[:100].strip(),
            entity_id=entity_id_val,
            entity_name=entity_name_val,
        )
        db.add(conversation)
        db.flush()

    # Save user message
    user_msg = JayMessage(
        id=uuid.uuid4(),
        conversation_id=conversation.id,
        role="user",
        content=body.query,
        format="text",
    )
    db.add(user_msg)

    # Build scope from context
    scope = {
        "entity_id": body.context.entity_id,
        "sub_entity_id": body.context.sub_entity_id,
    }

    # Obtain Synapse DB session for modules that query Synapse (e.g., bob)
    synapse_gen = get_synapse_db()
    synapse_session = next(synapse_gen)

    try:
        response = _process_query(
            query=body.query,
            scope=scope,
            current_module=body.context.current_module,
            conversation_history=conversation_history,
            db=db,
            user_role=user.get("role"),
            conversation_id=str(conversation.id),
            previous_sql=conversation_history_sql,
            synapse_db=synapse_session,
            previous_module=previous_module,
            previous_domains=previous_domains,
            previous_user_summary=previous_user_summary,
        )
    except Exception as e:
        logger.error(f"Chat processing error: {e}")
        response = {
            "format": "error",
            "message": "I'm sorry, something went wrong. Please try again.",
            "data": None,
            "action": None,
            "action_data": None,
            "sql_fingerprint": None,
        }
    finally:
        # Close the Synapse session
        try:
            next(synapse_gen)
        except StopIteration:
            pass

    # Save assistant message
    assistant_msg = JayMessage(
        id=uuid.uuid4(),
        conversation_id=conversation.id,
        role="assistant",
        content=response.get("message", ""),
        format=response.get("format", "text"),
        data=response.get("data"),
        sql_fingerprint=response.get("sql_fingerprint"),
        action=response.get("action"),
        action_data=response.get("action_data"),
    )
    db.add(assistant_msg)

    # Update conversation
    conversation.updated_at = datetime.now(timezone.utc)
    db.commit()

    # Strip admin-only fields (SQL, pipeline log, technical assumption) for non-admin users
    response_data = response.get("data")
    if user.get("role") != "admin":
        # Check if assumption existed before stripping it
        has_assumption = (
            response_data
            and isinstance(response_data, dict)
            and response_data.get("_assumption")
        )
        response_data = _strip_admin_data(response_data)
        # Add generic feedback prompt for non-admins when assumption was made
        if has_assumption and isinstance(response_data, dict):
            response_data["_feedback_prompt"] = True

    return {
        "conversation_id": str(conversation.id),
        "message": {
            "id": str(assistant_msg.id),
            "role": "assistant",
            "content": response.get("message", ""),
            "format": response.get("format", "text"),
            "data": response_data,
            "sql_fingerprint": response.get("sql_fingerprint"),
            "action": response.get("action"),
            "action_data": response.get("action_data"),
            "created_at": assistant_msg.created_at.isoformat(),
        },
    }


@router.post("/chat/stream")
async def chat_stream(
    request: Request,
    body: ChatRequest,
    db: Session = Depends(get_db),
):
    """SSE streaming chat endpoint.

    Sends progress events as the pipeline runs, then a final result event
    with the same payload shape as POST /jay/chat.
    """
    user = get_current_user(request)
    user_id = str(user.get("user_id", user.get("id")))

    progress_queue: queue.Queue = queue.Queue()

    def progress_callback(stage: str, detail: str):
        """Push a progress event onto the queue (called from worker thread)."""
        progress_queue.put({"stage": stage, "message": detail})

    # ── Replicate conversation setup from chat() ──────────────────────

    conversation = None
    conversation_history = []
    conversation_history_sql = None
    previous_module = None
    previous_domains = None
    previous_user_summary = None

    if body.conversation_id:
        conversation = db.query(JayConversation).filter(
            JayConversation.id == body.conversation_id,
            JayConversation.user_id == user_id,
        ).first()

        if conversation:
            messages = (
                db.query(JayMessage)
                .filter(JayMessage.conversation_id == conversation.id)
                .order_by(JayMessage.created_at)
                .all()
            )
            conversation_history = [
                {"role": m.role, "content": m.content}
                for m in messages[-10:]
            ]
            for m in reversed(messages):
                if m.role == "assistant" and m.data and isinstance(m.data, dict):
                    last_sql = m.data.get("_sql")
                    if last_sql:
                        conversation_history_sql = last_sql
                        previous_module = m.data.get("_module")
                        previous_domains = m.data.get("_domains", [])
                        previous_user_summary = m.data.get("_user_summary", "")
                        break

            # Implicit feedback (same as chat())
            try:
                from app.utils.jay.self_learning import (
                    detect_multi_turn_feedback, process_implicit_feedback,
                )
                recent_window = [
                    {
                        "id": str(m.id),
                        "role": m.role,
                        "content": m.content or "",
                        "data": m.data if isinstance(m.data, dict) else {},
                    }
                    for m in messages[-10:]
                ]
                feedback_hits = detect_multi_turn_feedback(body.query, recent_window)
                for hit in feedback_hits:
                    logger.info(
                        "Implicit feedback: %s (strength=%s) on message %s",
                        hit["feedback_type"], hit["strength"], hit["message_id"],
                    )
                    process_implicit_feedback(
                        hit["feedback_type"],
                        hit["message"],
                        db,
                        strength=hit["strength"],
                    )
            except Exception as e:
                logger.warning(f"Implicit feedback detection failed (non-fatal): {e}")

    if not conversation:
        entity_id_val = body.context.entity_id if body.context and body.context.entity_id else None
        entity_name_val = None
        if entity_id_val:
            entity_row = db.query(Entity.entity_name).filter(Entity.entity_id == entity_id_val).first()
            entity_name_val = entity_row.entity_name if entity_row else None

        conversation = JayConversation(
            id=uuid.uuid4(),
            user_id=user_id,
            title=body.query[:100].strip(),
            entity_id=entity_id_val,
            entity_name=entity_name_val,
        )
        db.add(conversation)
        db.flush()

    # Save user message
    user_msg = JayMessage(
        id=uuid.uuid4(),
        conversation_id=conversation.id,
        role="user",
        content=body.query,
        format="text",
    )
    db.add(user_msg)

    scope = {
        "entity_id": body.context.entity_id,
        "sub_entity_id": body.context.sub_entity_id,
    }

    # Capture immutable snapshot values for the worker thread
    _conv_id = str(conversation.id)
    _query = body.query
    _current_module = body.context.current_module
    _user_role = user.get("role")

    async def event_generator():
        result_holder: list = [None]
        error_holder: list = [None]

        def run_pipeline():
            """Run the full query pipeline in a worker thread with its own DB sessions."""
            worker_db = None
            worker_synapse = None
            try:
                worker_db = create_worker_session()
                worker_synapse = create_worker_synapse_session()

                response = _process_query(
                    query=_query,
                    scope=scope,
                    current_module=_current_module,
                    conversation_history=conversation_history,
                    db=worker_db,
                    user_role=_user_role,
                    conversation_id=_conv_id,
                    previous_sql=conversation_history_sql,
                    synapse_db=worker_synapse,
                    previous_module=previous_module,
                    previous_domains=previous_domains,
                    previous_user_summary=previous_user_summary,
                    progress_callback=progress_callback,
                )
                result_holder[0] = response
            except Exception as e:
                logger.error(f"SSE pipeline error: {e}")
                error_holder[0] = str(e)
            finally:
                progress_queue.put(None)  # Signal completion
                if worker_synapse:
                    try:
                        worker_synapse.close()
                    except Exception:
                        pass
                if worker_db:
                    try:
                        worker_db.close()
                    except Exception:
                        pass

        thread = threading.Thread(target=run_pipeline, daemon=True)
        thread.start()

        loop = asyncio.get_event_loop()

        while True:
            try:
                item = await loop.run_in_executor(
                    None, lambda: progress_queue.get(timeout=0.5)
                )
                if item is None:
                    break
                yield f"event: progress\ndata: {json.dumps(item)}\n\n"
            except queue.Empty:
                # Keepalive to prevent proxy/client timeouts
                yield f"event: ping\ndata: {{}}\n\n"

        # Pipeline finished — persist messages and send result using the
        # main thread's DB session (which is safe now that the worker is done).

        if error_holder[0]:
            response = {
                "format": "error",
                "message": "I'm sorry, something went wrong. Please try again.",
                "data": None,
                "action": None,
                "action_data": None,
                "sql_fingerprint": None,
            }
        else:
            response = result_holder[0] or {
                "format": "error",
                "message": "I'm sorry, something went wrong. Please try again.",
                "data": None,
                "action": None,
                "action_data": None,
                "sql_fingerprint": None,
            }

        # Save assistant message
        assistant_msg = JayMessage(
            id=uuid.uuid4(),
            conversation_id=conversation.id,
            role="assistant",
            content=response.get("message", ""),
            format=response.get("format", "text"),
            data=response.get("data"),
            sql_fingerprint=response.get("sql_fingerprint"),
            action=response.get("action"),
            action_data=response.get("action_data"),
        )
        db.add(assistant_msg)

        # Update conversation
        conversation.updated_at = datetime.now(timezone.utc)
        db.commit()

        # Strip admin-only fields for non-admin users
        response_data = response.get("data")
        if _user_role != "admin":
            has_assumption = (
                response_data
                and isinstance(response_data, dict)
                and response_data.get("_assumption")
            )
            response_data = _strip_admin_data(response_data)
            if has_assumption and isinstance(response_data, dict):
                response_data["_feedback_prompt"] = True

        result_payload = {
            "conversation_id": _conv_id,
            "message": {
                "id": str(assistant_msg.id),
                "role": "assistant",
                "content": response.get("message", ""),
                "format": response.get("format", "text"),
                "data": response_data,
                "sql_fingerprint": response.get("sql_fingerprint"),
                "action": response.get("action"),
                "action_data": response.get("action_data"),
                "created_at": assistant_msg.created_at.isoformat(),
            },
        }

        if error_holder[0]:
            yield f"event: error\ndata: {json.dumps({'error': error_holder[0]})}\n\n"
        else:
            yield f"event: result\ndata: {json.dumps(result_payload, default=str)}\n\n"

        yield f"event: done\ndata: {{}}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


def _build_pipeline_log(ctx: PipelineContext) -> list:
    """Serialize pipeline context entries for admin debug log."""
    return [
        {"step": e["step"], "key": e["key"], "value": str(e["value"])}
        for e in ctx.entries
    ]


def _process_query(
    query: str,
    scope: dict,
    current_module: str,
    conversation_history: list,
    db: Session,
    user_role: str = None,
    conversation_id: str = None,
    previous_sql: str = None,
    synapse_db: Session = None,
    previous_module: str = None,
    previous_domains: list = None,
    previous_user_summary: str = None,
    progress_callback=None,
) -> dict:
    """Core query processing pipeline (new 3-step LLM architecture).

    Args:
        progress_callback: Optional callable(stage, detail) for SSE streaming.
    """
    pipeline_t0 = _time.time()

    def _notify(stage: str, detail: str):
        """Fire progress_callback if set; never raise."""
        if progress_callback is not None:
            try:
                progress_callback(stage, detail)
            except Exception:
                pass

    # Create pipeline context to accumulate reasoning across all steps
    ctx = PipelineContext()

    # Step 0: Query Normalization (alias expansion, <5ms for cache-only, ~200ms if LLM grammar fix)
    from app.utils.jay.query_normalizer import normalize_query
    t0 = _time.time()
    normalized_query, was_normalized = normalize_query(query, db_session=db)
    t0_elapsed = (_time.time() - t0) * 1000
    if was_normalized:
        logger.info(f"[TIMING] Query normalization: {t0_elapsed:.0f}ms ('{query}' -> '{normalized_query}')")
        ctx.add("normalization", "original_query", query)
        ctx.add("normalization", "normalized_query", normalized_query)
        query = normalized_query  # Use normalized query for all downstream steps
    else:
        logger.info(f"[TIMING] Query normalization: {t0_elapsed:.0f}ms (no changes)")

    # Step 0.5: Early Query Cache Check — skip intent detection for exact matches
    # If we find an exact match (similarity >= 0.995) with a known module, we can
    # bypass the expensive intent detection step entirely and go straight to v2 pipeline.
    # BUT: skip cache for broad insight/dashboard queries — they need the multi-insight path.
    _MULTI_INSIGHT_SKIP_CACHE_KW = {"insights", "overview", "dashboard", "summary for", "breakdown", "analysis"}
    _skip_cache_for_multi = any(kw in query.lower() for kw in _MULTI_INSIGHT_SKIP_CACHE_KW)

    try:
        from app.utils.jay.query_cache import find_exact_match
        _early_cache = find_exact_match(query, db) if not _skip_cache_for_multi else None
        if _early_cache and _early_cache.get("sql") and _early_cache.get("module"):
            _cached_module = _early_cache["module"]
            if _cached_module in MODULES:
                logger.info(
                    f"[TIMING] Early cache hit — skipping intent detection "
                    f"(module={_cached_module}, similarity={_early_cache.get('similarity', 0):.4f})"
                )
                ctx.add("cache", "early_cache_hit", True)
                ctx.add("cache", "cached_module", _cached_module)
                ctx.add("cache", "cached_similarity", _early_cache.get("similarity", 0))

                _cached_domains = _early_cache.get("domains", [])
                if not isinstance(_cached_domains, list):
                    _cached_domains = []

                try:
                    from app.utils.jay.pipeline_v2 import run_pipeline_v2
                    _cache_pipeline_result = run_pipeline_v2(
                        query=query,
                        module=_cached_module,
                        scope=scope,
                        resolved_entities={},
                        user_summary=None,
                        format_hint="auto",
                        db=db,
                        synapse_db=synapse_db,
                        domains=_cached_domains,
                        context=ctx,
                        previous_query_sql=previous_sql,
                        previous_module=previous_module,
                        previous_domains=previous_domains,
                        previous_user_summary=previous_user_summary,
                        progress_callback=progress_callback,
                    )
                    if not _cache_pipeline_result.success:
                        logger.debug(
                            "Early cache v2 pipeline returned success=False (falling through to intent detection): %s",
                            _cache_pipeline_result.error_message or "unknown",
                        )
                    else:
                        raw_data = _cache_pipeline_result.raw_data or []
                        chart_data = _normalize_chart_columns(raw_data)
                        dimensions = _infer_dimensions_from_data(chart_data)
                        module_cfg = MODULES.get(_cached_module, {})
                        formatted = decide_format(
                            raw_data=chart_data,
                            format_hint="auto",
                            dimensions=dimensions,
                            metric=None,
                            dimension_configs=module_cfg.get("dimensions", {}),
                            context=ctx,
                        )

                        t_total = (_time.time() - pipeline_t0) * 1000
                        logger.info(f"[TIMING] Total (early cache path): {t_total:.0f}ms")
                        ctx.add("timing", "total_ms", round(t_total))

                        message = synthesize_response(
                            user_query=query,
                            raw_data=raw_data,
                            format_type=formatted["format"],
                            current_module=current_module,
                            pipeline_context=ctx.summary(),
                        )

                        fingerprint = compute_sql_fingerprint(
                            _cache_pipeline_result.sql or "", [], _cached_module
                        )

                        result_data = formatted.get("data") or {}
                        result_data["_sql"] = _cache_pipeline_result.sql
                        result_data["_module"] = _cached_module
                        result_data["_domains"] = _cached_domains
                        result_data["_pipeline_log"] = _build_pipeline_log(ctx)

                        return {
                            "format": formatted["format"],
                            "message": message,
                            "data": result_data,
                            "action": None,
                            "action_data": None,
                            "sql_fingerprint": fingerprint,
                        }
                except Exception as _v2_err:
                    logger.debug(f"Early cache v2 pipeline failed (non-fatal): {_v2_err}")
    except Exception as _cache_err:
        logger.debug(f"Early cache check failed (non-fatal): {_cache_err}")

    # Pre-warm query embedding in background (will be cached for RAG retrieval)
    from concurrent.futures import ThreadPoolExecutor
    _embedding_executor = ThreadPoolExecutor(max_workers=1)
    try:
        from app.utils.jay.embedding_cache import get_query_embedding
        _embedding_future = _embedding_executor.submit(get_query_embedding, query)
    except Exception:
        _embedding_future = None

    # Step 1: Intent Detection (LLM #1) — returns up to 3 interpretations
    _notify("intent_detection", "Understanding your question...")
    t1 = _time.time()
    all_intents: List[IntentResult] = []
    try:
        all_intents = detect_intents(
            query=query,
            current_module=current_module,
            conversation_history=conversation_history,
            context=ctx,
            db_session=db,
            previous_module=previous_module,
            previous_domains=previous_domains,
        )
    except IntentDetectionError as e:
        error_str = str(e)
        logger.warning(f"Intent detection failed: {error_str}")
        if "Invalid module" in error_str:
            message = (
                "I'm not sure which data area you're asking about. "
                "Could you mention commissions, agents, providers, or members?"
            )
        elif "parse" in error_str.lower() or "json" in error_str.lower():
            message = (
                "I couldn't understand your question. Could you rephrase it? "
                "Try asking about a specific module like agents, commissions, or providers."
            )
        else:
            message = (
                "I couldn't understand your question. Could you rephrase it? "
                "Try asking about a specific module like agents, commissions, or providers."
            )
        return {
            "format": "text",
            "message": message,
            "data": {"_pipeline_log": _build_pipeline_log(ctx)},
            "action": None,
            "action_data": None,
            "sql_fingerprint": None,
        }

    if not all_intents:
        return {
            "format": "text",
            "message": "I couldn't understand your question. Could you rephrase it?",
            "data": {"_pipeline_log": _build_pipeline_log(ctx)},
            "action": None,
            "action_data": None,
            "sql_fingerprint": None,
        }

    # Primary intent is the highest-confidence interpretation
    intent = all_intents[0]

    t1_elapsed = (_time.time() - t1) * 1000
    logger.info(f"[TIMING] Intent detection: {t1_elapsed:.0f}ms ({len(all_intents)} interpretation(s))")
    ctx.add("timing", "intent_detection_ms", round(t1_elapsed))

    # Ensure embedding is ready (should be cached by now from background pre-warm)
    if _embedding_future:
        try:
            _embedding_future.result(timeout=5)
        except Exception:
            pass
    _embedding_executor.shutdown(wait=False)

    action = intent.action

    # Handle non-data actions
    if action == "answer":
        return _handle_answer_action(intent, query, user_role, conversation_id, db)

    if action in ("navigate", "filter", "navigate_and_filter"):
        return {
            "format": "text",
            "message": intent.message or "",
            "data": None,
            "action": action,
            "action_data": {
                "route": intent.route,
                "filters": intent.filters,
            },
            "sql_fingerprint": None,
        }

    # Low confidence
    if intent.confidence < 0.5:
        return {
            "format": "text",
            "message": "I'm not fully confident about this request. Could you provide more details?",
            "data": None,
            "action": None,
            "action_data": None,
            "sql_fingerprint": None,
        }

    # Must have a module for data queries
    if not intent.module:
        return {
            "format": "text",
            "message": "I'm not sure which data area you're asking about. Could you be more specific?",
            "data": None,
            "action": None,
            "action_data": None,
            "sql_fingerprint": None,
        }

    # Step 2: Entity Resolution + Column Retrieval (parallelized)
    _notify("entity_resolution", "Resolving entities...")
    # Entity resolution and column retrieval are independent — both depend only
    # on the intent result. We run them concurrently to save wall-clock time.
    t2 = _time.time()

    # Determine if this will be a multi-intent parallel path (skip pre-column-retrieval
    # in that case since each parallel worker does its own retrieval).
    _parallel_intents = [
        i for i in all_intents
        if i.action == "data_query" and i.module and i.confidence >= 0.5
    ]
    _is_multi_intent = len(_parallel_intents) > 1

    # Check if v2 pipeline is enabled — if so, skip pre-column-retrieval
    # (v2's assemble_schema_context does its own retrieval).
    _v2_check = os.environ.get("JAI_PIPELINE_V2_ENABLED", "true").lower() in ("true", "1", "yes")

    if not _is_multi_intent and not _v2_check:
        # Single-intent + v1 pipeline: run entity resolution + column retrieval in parallel
        def _do_column_retrieval():
            cr_session = create_worker_session()
            try:
                return run_column_retrieval(
                    query=query,
                    db_session=cr_session,
                    previous_query_sql=previous_sql,
                    previous_user_summary=previous_user_summary,
                    previous_module=previous_module,
                    context=ctx,
                )
            except Exception as e:
                logger.warning(f"Parallel column retrieval failed: {e}")
                return None
            finally:
                try:
                    cr_session.close()
                except Exception:
                    pass

        with ThreadPoolExecutor(max_workers=2) as _pool:
            _cr_future = _pool.submit(_do_column_retrieval)
            # Entity resolution on main thread's session
            resolved = _resolve_entities_from_intent(intent, db, scope, user_question=query)
            # Collect column retrieval result
            try:
                _cr_result = _cr_future.result(timeout=30)
            except Exception as e:
                logger.warning(f"Column retrieval future failed: {e}")
                _cr_result = None
    else:
        # Multi-intent or v2 pipeline: just do entity resolution
        # (v2 does its own schema assembly; multi-intent workers do their own retrieval)
        resolved = _resolve_entities_from_intent(intent, db, scope, user_question=query)
        _cr_result = None

    t2_elapsed = (_time.time() - t2) * 1000
    logger.info(f"[TIMING] Entity resolution + column retrieval (parallel): {t2_elapsed:.0f}ms")
    ctx.add("timing", "entity_resolution_ms", round(t2_elapsed))

    # Log entity resolution to pipeline context
    if resolved.resolved:
        for key, val in resolved.resolved.items():
            ctx.add("entity_resolution", "entity_resolution", f"Resolved {key} -> {val}")
    elif not intent.entity_mentions:
        ctx.add("entity_resolution", "entity_resolution", "No entities mentioned")
    if resolved.unresolved:
        ctx.add("entity_resolution", "unresolved_entities", [e.raw_value for e in resolved.unresolved])
    # Log resolution tier details (which tier succeeded for each entity)
    for tier_detail in getattr(resolved, "resolution_tier_details", []):
        ctx.add("entity_resolution", "resolution_details", tier_detail)

    if resolved.unresolved:
        # When v2 pipeline is active, pass unresolved entities as hints instead of blocking
        _v2_enabled = os.environ.get("JAI_PIPELINE_V2_ENABLED", "true").lower() in ("true", "1", "yes")
        if _v2_enabled and not resolved.candidates:
            # Pass raw values as hints — the LLM can use ILIKE or fuzzy matching
            for ue in resolved.unresolved:
                resolved.resolved[ue.entity_type] = ue.raw_value
                ctx.add("entity_resolution", "unresolved_passed_as_hint", f"{ue.entity_type}={ue.raw_value}")
            logger.info("v2 pipeline: passing %d unresolved entities as hints", len(resolved.unresolved))
        elif resolved.candidates:
            # Show disambiguation options (still useful for both v1 and v2)
            options_text = "\n".join(
                f"- {c.get('display_value', c.get('resolve_value', ''))}"
                for c in (resolved.candidates or [])[:10]
            )
            return {
                "format": "text",
                "message": f"I found multiple matches. Which one did you mean?\n\n{options_text}",
                "data": {"_pipeline_log": _build_pipeline_log(ctx), "_module": intent.module},
                "action": None,
                "action_data": None,
                "sql_fingerprint": None,
            }
        else:
            # No candidates, no v2 — show the error (v1 behavior)
            unresolved_names = [e.raw_value for e in resolved.unresolved]
            return {
                "format": "text",
                "message": f"I couldn't find a match for: {', '.join(unresolved_names)}. Could you check the spelling?",
                "data": {"_pipeline_log": _build_pipeline_log(ctx), "_module": intent.module},
                "action": None,
                "action_data": None,
                "sql_fingerprint": None,
            }

    # Handle comparison queries
    VALID_COMPARISON_PERIODS = {"this_month", "last_month", "this_year", "last_year"}
    if intent.comparison_periods and len(intent.comparison_periods) >= 2:
        valid_periods = [p for p in intent.comparison_periods if p in VALID_COMPARISON_PERIODS]
        if len(valid_periods) >= 2:
            return _process_comparison_query(
                query=query,
                intent=intent,
                scope=scope,
                resolved_entities=resolved.resolved,
                current_module=current_module,
                db=db,
                synapse_db=synapse_db,
            )

    # ----------------------------------------------------------------
    # Multi-insight dashboard path (short-circuits normal pipeline)
    # ----------------------------------------------------------------
    # When the user asks for broad "insights", "overview", "dashboard", etc.,
    # we skip the single-query pipeline and instead:
    #   1. Use the LLM planner (or fallback templates) to get 3-4 insight specs
    #   2. Execute them in parallel with scope injection
    #   3. Build a multi-panel dashboard from successful results
    # Falls through to the normal pipeline if fewer than 2 insights succeed.
    # ----------------------------------------------------------------

    _MULTI_INSIGHT_KEYWORDS = {
        "insights", "overview", "dashboard", "give me details",
        "summary for", "summarize", "analysis", "analyze",
        "comprehensive", "tell me about", "show me about",
        "what can you tell", "break down", "breakdown",
    }
    _query_lower = query.lower()
    _is_multi_insight = (
        intent.action == "data_query"
        and intent.module
        and (
            intent.format_hint == "dashboard"
            or any(kw in _query_lower for kw in _MULTI_INSIGHT_KEYWORDS)
        )
    )

    if _is_multi_insight:
        _notify("multi_insight", "Planning dashboard insights...")
        _mi_t0 = _time.time()

        try:
            from app.utils.jay.intent_parser import plan_dashboard_insights, synthesize_multi_insight_summary
            from app.utils.jay.dashboard_templates import get_insight_templates, get_db_type_for_module
            from app.utils.jay.parallel import run_parallel_insights
            from app.utils.jay.format_engine import build_multi_insight_dashboard

            module_cfg = MODULES.get(intent.module, {})
            _mi_db_type = get_db_type_for_module(intent.module)
            _mi_scope_columns = module_cfg.get("scope_columns", {})

            # Build schema context for the planner — extract table info from templates + catalog
            _mi_schema_context = module_cfg.get("ddl_summary", "")
            if not _mi_schema_context:
                # Extract table names from templates to hint the LLM
                _template_specs = get_insight_templates(intent.module)
                if _template_specs:
                    _mi_schema_context = "Available SQL templates for reference:\n"
                    for _ts in _template_specs:
                        _mi_schema_context += f"- {_ts.get('title', '')}: {_ts.get('sql_template', '')}\n"
                else:
                    # Try table catalog
                    try:
                        from app.utils.jay.table_catalog import get_tables_for_module
                        _tables = get_tables_for_module(intent.module)
                        if _tables:
                            _mi_schema_context = "Tables: " + ", ".join(_tables)
                    except Exception:
                        pass

            insight_specs = plan_dashboard_insights(
                user_query=query,
                module=intent.module,
                schema_context=_mi_schema_context,
            )
            _planner_elapsed = (_time.time() - _mi_t0) * 1000
            ctx.add("multi_insight", "planner_elapsed_ms", round(_planner_elapsed))
            ctx.add("multi_insight", "planner_specs_count", len(insight_specs) if insight_specs else 0)

            # Fallback to predefined templates if planner returns nothing
            if not insight_specs:
                insight_specs = get_insight_templates(intent.module)
                ctx.add("multi_insight", "source", "templates")
            else:
                ctx.add("multi_insight", "source", "llm_planner")

            if insight_specs:
                # Execute insights in parallel
                _notify("multi_insight", "Running insight queries...")
                _mi_exec_t0 = _time.time()
                insight_results = run_parallel_insights(
                    insight_specs=insight_specs,
                    scope=scope,
                    scope_columns=_mi_scope_columns,
                    db_type=_mi_db_type,
                )
                _mi_exec_elapsed = (_time.time() - _mi_exec_t0) * 1000
                ctx.add("multi_insight", "execution_elapsed_ms", round(_mi_exec_elapsed))

                # Collect successful results
                successful_insights = [
                    {
                        "title": r.title,
                        "description": r.description,
                        "format_hint": r.format_hint,
                        "raw_data": r.raw_data,
                        "metric_columns": r.metric_columns,
                        "dimension_columns": r.dimension_columns,
                    }
                    for r in insight_results
                    if r.success and r.raw_data
                ]
                ctx.add("multi_insight", "successful_insights", len(successful_insights))
                ctx.add("multi_insight", "total_insights", len(insight_results))

                # Log failed insights for debugging
                for r in insight_results:
                    if not r.success:
                        ctx.add("multi_insight", f"failed_{r.title}", r.error[:200] if r.error else "unknown")

                if len(successful_insights) >= 2:
                    # Build multi-panel dashboard
                    _notify("multi_insight", "Building dashboard...")
                    formatted = build_multi_insight_dashboard(
                        insights=successful_insights,
                        module=intent.module,
                        user_query=query,
                        assumption=intent.assumption,
                        resolved_entities=resolved.resolved if resolved else None,
                    )

                    # Generate summary
                    _notify("multi_insight", "Generating summary...")
                    _mi_synth_t0 = _time.time()
                    message = synthesize_multi_insight_summary(
                        user_query=query,
                        insights=successful_insights,
                        current_module=current_module,
                        assumption=intent.assumption,
                    )
                    _mi_synth_elapsed = (_time.time() - _mi_synth_t0) * 1000
                    ctx.add("multi_insight", "synthesis_elapsed_ms", round(_mi_synth_elapsed))
                    formatted["data"]["summary"] = message

                    _mi_total = (_time.time() - pipeline_t0) * 1000
                    logger.info(f"[TIMING] Total (multi-insight dashboard): {_mi_total:.0f}ms")
                    ctx.add("timing", "total_ms", round(_mi_total))

                    # Build response with metadata
                    result_data = formatted.get("data") or {}
                    result_data["_module"] = intent.module
                    result_data["_domains"] = intent.domains if intent.domains else []
                    result_data["_user_summary"] = intent.user_summary or ""
                    result_data["_pipeline_log"] = _build_pipeline_log(ctx)
                    result_data["_resolved_entities"] = resolved.resolved or {}
                    if intent.assumption:
                        result_data["_assumption"] = intent.assumption
                    # Attach individual SQLs for admin debugging
                    result_data["_sql"] = "; ".join(
                        r.sql for r in insight_results if r.success and r.sql
                    )

                    logger.info(f"Pipeline context:\n{ctx.summary()}")

                    return {
                        "format": formatted["format"],
                        "message": message,
                        "data": result_data,
                        "action": None,
                        "action_data": None,
                        "sql_fingerprint": None,
                    }
                else:
                    logger.info(
                        "[MULTI_INSIGHT] Only %d successful insights, falling through to normal pipeline",
                        len(successful_insights),
                    )
            else:
                logger.info("[MULTI_INSIGHT] No insight specs available, falling through to normal pipeline")

        except Exception as _mi_err:
            logger.warning(f"[MULTI_INSIGHT] Multi-insight path failed (non-fatal), falling through: {_mi_err}")
            ctx.add("multi_insight", "error", str(_mi_err)[:300])

    # Steps 3-5: SQL Generation -> Scope Injection -> Safety Review -> Execute (with retry)
    # Attempts 1-2 run within the pipeline. If both fail, pipeline signals needs_full_restart
    # and we re-run the entire pipeline from intent detection (attempt 3).
    domains = intent.domains if intent.domains else None

    # ----------------------------------------------------------------
    # Multi-intent parallel execution
    # ----------------------------------------------------------------
    # When detect_intents() returned multiple data_query interpretations,
    # run entity resolution + pipeline for each in parallel and pick the
    # best successful result.  Single-intent queries skip this overhead.
    # ----------------------------------------------------------------

    if _is_multi_intent:
        pipeline_result, intent, resolved = _run_parallel_pipelines(
            intents=_parallel_intents,
            query=query,
            scope=scope,
            current_module=current_module,
            db=db,
            synapse_db=synapse_db,
            ctx=ctx,
            previous_sql=previous_sql,
            previous_module=previous_module,
            previous_domains=previous_domains,
            previous_user_summary=previous_user_summary,
            resolved_primary=resolved,
        )
    else:
        # --- Single-intent path ---
        # --- Pipeline v2 (tool-augmented) ---
        _v2_enabled = os.environ.get("JAI_PIPELINE_V2_ENABLED", "true").lower() in ("true", "1", "yes")
        if _v2_enabled:
            try:
                from app.utils.jay.pipeline_v2 import run_pipeline_v2
                pipeline_result = run_pipeline_v2(
                    query=query,
                    module=intent.module,
                    scope=scope,
                    resolved_entities=resolved.resolved,
                    user_summary=intent.user_summary,
                    format_hint=intent.format_hint,
                    db=db,
                    synapse_db=synapse_db,
                    domains=domains,
                    context=ctx,
                    previous_query_sql=previous_sql,
                    previous_module=previous_module,
                    previous_domains=previous_domains,
                    previous_user_summary=previous_user_summary,
                    assumption=intent.assumption,
                    progress_callback=progress_callback,
                )
                if pipeline_result.success or not pipeline_result.needs_full_restart:
                    pass
                else:
                    logger.warning("Pipeline v2 exhausted retries, falling back to v1")
                    _v2_enabled = False
            except Exception as e:
                logger.error(f"Pipeline v2 failed, falling back to v1: {e}")
                _v2_enabled = False

        if not _v2_enabled:
            t3 = _time.time()
            pipeline_result = run_data_query_pipeline(
                query=query,
                module=intent.module,
                scope=scope,
                resolved_entities=resolved.resolved,
                user_summary=intent.user_summary,
                format_hint=intent.format_hint,
                db=db,
                synapse_db=synapse_db,
                domains=domains,
                context=ctx,
                previous_query_sql=previous_sql,
                assumption=intent.assumption,
                pre_column_retrieval=_cr_result,
                previous_domains=previous_domains,
                previous_module=previous_module,
                previous_user_summary=previous_user_summary,
            )
            t3_elapsed = (_time.time() - t3) * 1000
            logger.info(f"[TIMING] Pipeline (SQL gen + scope + safety + execute): {t3_elapsed:.0f}ms")
            ctx.add("timing", "pipeline_ms", round(t3_elapsed))

            # --- Full Pipeline Restart (Attempt 3) ---
            if not pipeline_result.success and pipeline_result.needs_full_restart:
                retry_history = pipeline_result.retry_history
                logger.info(
                    f"Full pipeline restart: re-running intent detection with "
                    f"{len(retry_history.attempts) if retry_history else 0} previous failures"
                )
                ctx.add("pipeline", "full_restart", "Re-running intent detection for attempt 3")

                try:
                    intent2 = detect_intent(
                        query=query,
                        current_module=current_module,
                        conversation_history=conversation_history,
                        context=ctx,
                        retry_history=retry_history,
                        db_session=db,
                        previous_module=previous_module,
                        previous_domains=previous_domains,
                    )
                except IntentDetectionError as e:
                    logger.warning(f"Intent re-detection failed on restart: {e}")
                    intent2 = None

                if intent2 and intent2.action == "data_query" and intent2.module:
                    ctx.add("intent", "restart_module", intent2.module)
                    ctx.add("intent", "restart_domains", str(intent2.domains))

                    resolved2 = _resolve_entities_from_intent(intent2, db, scope, user_question=query)
                    if resolved2.unresolved:
                        resolved2 = resolved
                        logger.info("Entity re-resolution had unresolved; keeping original")

                    domains2 = intent2.domains if intent2.domains else None
                    pipeline_result = run_data_query_pipeline(
                        query=query,
                        module=intent2.module,
                        scope=scope,
                        resolved_entities=resolved2.resolved,
                        user_summary=intent2.user_summary,
                        format_hint=intent2.format_hint,
                        db=db,
                        synapse_db=synapse_db,
                        domains=domains2,
                        context=ctx,
                        previous_query_sql=previous_sql,
                        retry_history=retry_history,
                        assumption=intent2.assumption,
                        previous_domains=previous_domains,
                        previous_module=previous_module,
                        previous_user_summary=previous_user_summary,
                    )

                    if pipeline_result.success:
                        intent = intent2
                        resolved = resolved2

    if not pipeline_result.success:
        if pipeline_result.safety_blocked:
            return {
                "format": "text",
                "message": (
                    "I wasn't able to process that request. "
                    "Could you try being more specific? For example:\n\n"
                    "- \"How many agents do we have?\"\n"
                    "- \"Show total commissions by carrier for November 2025\"\n"
                    "- \"List the top 5 agents by commission\""
                ),
                "data": {
                    "_pipeline_log": _build_pipeline_log(ctx),
                    "_module": intent.module,
                    "_sql": pipeline_result.sql,
                    "_resolved_entities": resolved.resolved or {},
                },
                "action": None,
                "action_data": None,
                "sql_fingerprint": None,
            }
        return {
            "format": "error",
            "message": pipeline_result.error_message or "Query failed. Please try again.",
            "data": {
                "_pipeline_log": _build_pipeline_log(ctx),
                "_module": intent.module,
                "_resolved_entities": resolved.resolved or {},
            },
            "action": None,
            "action_data": None,
            "sql_fingerprint": None,
        }

    raw_data = pipeline_result.raw_data or []

    # Normalize 2-column results to {dimension, value} for format engine
    chart_data = _normalize_chart_columns(raw_data)

    # Step 6: Format results
    _notify("formatting", "Formatting results...")
    dimensions = _infer_dimensions_from_data(chart_data)
    module_cfg = MODULES.get(intent.module, {})

    # Check if this should be a dashboard response
    _is_dashboard = (
        intent.format_hint == "dashboard"
        or _should_use_dashboard(raw_data, intent, query)
    )

    if _is_dashboard and len(raw_data) > 1:
        # Build multi-section dashboard — use raw_data (original column names)
        # instead of chart_data (which renames numeric cols to "value")
        formatted = build_dashboard_sections(
            raw_data=raw_data,
            format_hint=intent.format_hint,
            dimensions=dimensions,
            dimension_configs=module_cfg.get("dimensions", {}),
            context=ctx,
            module=intent.module,
            user_query=query,
            assumption=pipeline_result.assumption,
            resolved_entities=resolved.resolved if resolved else None,
        )
        # Use LLM to generate a rich, contextual summary
        from app.utils.jay.intent_parser import synthesize_dashboard_summary
        t_synth = _time.time()
        message = synthesize_dashboard_summary(
            user_query=query,
            raw_data=raw_data,
            dashboard_sections=formatted.get("data", {}).get("sections", []),
            current_module=current_module,
            pipeline_context=ctx.summary(),
            assumption=pipeline_result.assumption,
        )
        synth_elapsed = (_time.time() - t_synth) * 1000
        logger.info(f"[TIMING] Dashboard synthesis: {synth_elapsed:.0f}ms")
        ctx.add("timing", "dashboard_synthesis_ms", round(synth_elapsed))
        # Update the summary in the formatted data
        formatted["data"]["summary"] = message
    else:
        # Standard single-format response
        formatted = decide_format(
            raw_data=chart_data,
            format_hint=intent.format_hint,
            dimensions=dimensions,
            metric=None,  # LLM-generated SQL doesn't use metric registry
            dimension_configs=module_cfg.get("dimensions", {}),
            context=ctx,
        )

        # Step 7: Synthesize response (LLM #4 — skipped for simple results)
        t7 = _time.time()
        message = synthesize_response(
            user_query=query,
            raw_data=raw_data,
            format_type=formatted["format"],
            current_module=current_module,
            pipeline_context=ctx.summary(),
        )
        t7_elapsed = (_time.time() - t7) * 1000
        logger.info(f"[TIMING] Response synthesis: {t7_elapsed:.0f}ms")
        ctx.add("timing", "synthesis_ms", round(t7_elapsed))

    total_elapsed = (_time.time() - pipeline_t0) * 1000
    logger.info(f"[TIMING] Total pipeline: {total_elapsed:.0f}ms")
    ctx.add("timing", "total_ms", round(total_elapsed))

    # Compute SQL fingerprint for favorites
    fingerprint = compute_sql_fingerprint(
        pipeline_result.sql or "", [], intent.module
    )

    # Attach SQL metadata and pipeline log for DB storage and admin transparency
    result_data = formatted.get("data") or {}
    result_data["_sql"] = pipeline_result.sql
    result_data["_module"] = intent.module
    result_data["_domains"] = intent.domains if intent else []
    result_data["_user_summary"] = intent.user_summary or ""
    result_data["_pipeline_log"] = _build_pipeline_log(ctx)
    result_data["_resolved_entities"] = resolved.resolved or {}

    if intent.assumption:
        result_data["_assumption"] = intent.assumption

    # Log full pipeline context for debugging
    logger.info(f"Pipeline context:\n{ctx.summary()}")

    return {
        "format": formatted["format"],
        "message": message,
        "data": result_data,
        "action": None,
        "action_data": None,
        "sql_fingerprint": fingerprint,
    }


def _run_parallel_pipelines(
    intents: List[IntentResult],
    query: str,
    scope: dict,
    current_module: str,
    db: Session,
    synapse_db: Session,
    ctx: PipelineContext,
    previous_sql: str,
    previous_module: str,
    previous_domains: list,
    previous_user_summary: str,
    resolved_primary: ResolvedEntities,
) -> tuple:
    """Run entity resolution + pipeline for multiple intents in parallel.

    Returns:
        Tuple of (pipeline_result, winning_intent, winning_resolved) so the
        caller can continue with format/synthesis on the main thread.
    """
    import threading as _threading
    from app.utils.jay.pipeline_models import PipelineResult

    logger.info(
        f"[PARALLEL] Running {len(intents)} intent interpretations in parallel: "
        + ", ".join(f"{i.module}({i.confidence:.2f})" for i in intents)
    )

    # Build a worker function for each intent
    def _make_worker(worker_intent: IntentResult, worker_idx: int):
        """Create a closure that runs entity resolution + pipeline for one intent."""
        def _worker(cancel_event: _threading.Event, worker_session: Session):
            # Check cancellation before starting
            if cancel_event.is_set():
                return None

            # Determine if this module needs Synapse
            module_cfg = MODULES.get(worker_intent.module, {})
            needs_synapse = module_cfg.get("db_type") == "synapse"
            worker_synapse = None

            try:
                # Entity resolution using the worker's own session
                worker_resolved = _resolve_entities_from_intent(
                    worker_intent, worker_session, scope, user_question=query
                )

                if worker_resolved.unresolved:
                    _v2_enabled = os.environ.get("JAI_PIPELINE_V2_ENABLED", "true").lower() in ("true", "1", "yes")
                    if _v2_enabled and not worker_resolved.candidates:
                        # Pass raw values as hints — the LLM can use ILIKE or fuzzy matching
                        for ue in worker_resolved.unresolved:
                            worker_resolved.resolved[ue.entity_type] = ue.raw_value
                        logger.info(
                            "[PARALLEL] Worker passing %d unresolved entities as hints",
                            len(worker_resolved.unresolved),
                        )
                    elif worker_resolved.candidates:
                        # Disambiguation needed — skip this worker instead of crashing
                        logger.info(
                            "[PARALLEL] Worker skipped: disambiguation needed for %s",
                            [e.raw_value for e in worker_resolved.unresolved],
                        )
                        return None
                    else:
                        raise ValueError(
                            f"Unresolved entities: {[e.raw_value for e in worker_resolved.unresolved]}"
                        )

                if cancel_event.is_set():
                    return None

                # Create Synapse session if needed
                if needs_synapse:
                    worker_synapse = create_worker_synapse_session()

                worker_domains = worker_intent.domains if worker_intent.domains else None
                worker_ctx = PipelineContext()  # each worker gets its own context

                # Check if v2 pipeline is enabled
                _v2_enabled = os.environ.get("JAI_PIPELINE_V2_ENABLED", "true").lower() in ("true", "1", "yes")

                if _v2_enabled:
                    try:
                        from app.utils.jay.pipeline_v2 import run_pipeline_v2
                        result = run_pipeline_v2(
                            query=query,
                            module=worker_intent.module,
                            scope=scope,
                            resolved_entities=worker_resolved.resolved,
                            user_summary=worker_intent.user_summary,
                            format_hint=worker_intent.format_hint,
                            db=worker_session,
                            synapse_db=worker_synapse,
                            domains=worker_domains,
                            context=worker_ctx,
                            previous_query_sql=previous_sql,
                            previous_module=previous_module,
                            previous_domains=previous_domains,
                            previous_user_summary=previous_user_summary,
                            assumption=worker_intent.assumption,
                        )
                        if result.success or not result.needs_full_restart:
                            pass
                        else:
                            logger.warning(f"[PARALLEL] Worker {worker_idx} v2 exhausted retries, falling back to v1")
                            _v2_enabled = False
                    except Exception as e:
                        logger.warning(f"[PARALLEL] Worker {worker_idx} v2 failed, falling back to v1: {e}")
                        _v2_enabled = False

                if not _v2_enabled:
                    result = run_data_query_pipeline(
                        query=query,
                        module=worker_intent.module,
                        scope=scope,
                        resolved_entities=worker_resolved.resolved,
                        user_summary=worker_intent.user_summary,
                        format_hint=worker_intent.format_hint,
                        db=worker_session,
                        synapse_db=worker_synapse,
                        domains=worker_domains,
                        context=worker_ctx,
                        previous_query_sql=previous_sql,
                        assumption=worker_intent.assumption,
                        previous_domains=previous_domains,
                        previous_module=previous_module,
                        previous_user_summary=previous_user_summary,
                    )

                if not result.success:
                    raise ValueError(
                        result.error_message or f"Pipeline failed for module {worker_intent.module}"
                    )

                # Attach metadata for the orchestrator to use
                result.intent_confidence = worker_intent.confidence
                result._worker_intent = worker_intent
                result._worker_resolved = worker_resolved
                result._worker_ctx = worker_ctx
                return result

            finally:
                if worker_synapse is not None:
                    try:
                        worker_synapse.close()
                    except Exception:
                        pass

        return _worker

    worker_fns = [_make_worker(intent, idx) for idx, intent in enumerate(intents)]

    executor = ParallelPipelineExecutor(max_workers=len(worker_fns), timeout=45)
    worker_results = executor.execute(worker_fns)

    # Find the best successful result
    best_worker = next((wr for wr in worker_results if wr.success), None)

    if best_worker and best_worker.result:
        pipeline_result = best_worker.result
        winning_intent = getattr(pipeline_result, "_worker_intent", intents[0])
        winning_resolved = getattr(pipeline_result, "_worker_resolved", resolved_primary)
        winning_ctx = getattr(pipeline_result, "_worker_ctx", None)

        # Merge winning worker's context entries into the main context
        if winning_ctx:
            for entry in winning_ctx.entries:
                ctx.entries.append(entry)

        ctx.add("parallel", "winning_worker", best_worker.worker_id)
        ctx.add("parallel", "winning_module", winning_intent.module)
        ctx.add("parallel", "winning_confidence", winning_intent.confidence)
        ctx.add("parallel", "total_workers", len(worker_results))
        ctx.add("parallel", "elapsed_ms", best_worker.elapsed_ms)

        logger.info(
            f"[PARALLEL] Worker {best_worker.worker_id} won "
            f"(module={winning_intent.module}, confidence={winning_intent.confidence:.2f}, "
            f"elapsed={best_worker.elapsed_ms:.0f}ms)"
        )

        return pipeline_result, winning_intent, winning_resolved
    else:
        # All workers failed -- return best error from highest-confidence worker
        logger.warning("[PARALLEL] All workers failed, using highest-confidence error")
        best_error = next(
            (wr for wr in worker_results if wr.error and not wr.cancelled),
            None,
        )
        error_msg = best_error.error if best_error else "All pipeline workers failed"
        ctx.add("parallel", "all_failed", True)
        ctx.add("parallel", "best_error", error_msg)

        # Classify the error and return a user-friendly message
        if "Unresolved entities" in error_msg:
            # Extract entity names from the error message
            entity_match = re.search(r"\[(.+?)\]", error_msg)
            entity_names = entity_match.group(1) if entity_match else "some values"
            user_error = f"I couldn't find a match for {entity_names}. Could you be more specific?"
            ctx.add("parallel", "error_class", "unresolved_entities")
        elif "timeout" in error_msg.lower() or "timed out" in error_msg.lower():
            user_error = "The query took too long. Try a more specific question."
            ctx.add("parallel", "error_class", "timeout")
        else:
            user_error = error_msg
            ctx.add("parallel", "error_class", "unknown")

        # Return a failed PipelineResult with classified error
        failed_result = PipelineResult(
            success=False,
            error_message=user_error,
            module=intents[0].module,
        )
        return failed_result, intents[0], resolved_primary


def _handle_answer_action(
    intent: IntentResult,
    query: str,
    user_role: str,
    conversation_id: str,
    db: Session,
) -> dict:
    """Handle answer-type actions (greetings, general, meta questions)."""
    answer_type = intent.answer_type or "general"

    # Fallback keyword detection for meta questions
    if answer_type != "meta":
        meta_keywords = [
            "how are you getting", "what query", "what sql", "data source",
            "where does this data", "explain your calculation", "how do you calculate",
            "show me the query", "what table", "how does jai work",
            "how do you get", "methodology", "internal working",
        ]
        query_lower = query.lower()
        if any(kw in query_lower for kw in meta_keywords):
            answer_type = "meta"

    # Meta questions are admin-only
    if answer_type == "meta" and user_role != "admin":
        return {
            "format": "text",
            "message": "That information is only available to administrators. Feel free to ask me about your data instead!",
            "data": None,
            "action": None,
            "action_data": None,
            "sql_fingerprint": None,
        }

    # For admin meta questions, look up the last SQL from conversation
    if answer_type == "meta" and user_role == "admin" and conversation_id:
        last_sql_msg = (
            db.query(JayMessage)
            .filter(
                JayMessage.conversation_id == conversation_id,
                JayMessage.role == "assistant",
                JayMessage.data.isnot(None),
            )
            .order_by(desc(JayMessage.created_at))
            .first()
        )
        if last_sql_msg and last_sql_msg.data and last_sql_msg.data.get("_sql"):
            sql_text = last_sql_msg.data["_sql"]
            module = last_sql_msg.data.get("_module", "unknown")
            return {
                "format": "text",
                "message": (
                    f"**Query Details (Admin Only)**\n\n"
                    f"**Module:** `{module}`\n\n"
                    f"**SQL:**\n```sql\n{sql_text}\n```"
                ),
                "data": None,
                "action": None,
                "action_data": None,
                "sql_fingerprint": None,
            }

    return {
        "format": "text",
        "message": intent.message or "I'm not sure how to help with that.",
        "data": None,
        "action": None,
        "action_data": None,
        "sql_fingerprint": None,
    }


def _resolve_entities_from_intent(
    intent: IntentResult,
    db: Session,
    scope: dict,
    user_question: Optional[str] = None,
) -> ResolvedEntities:
    """Resolve entity mentions from intent to database keys.

    Maps: agent name -> NPN, carrier name -> carrier_name, provider name -> NPI, etc.
    Uses the existing Resolver class.
    """
    if not intent.entity_mentions:
        return ResolvedEntities()

    resolver = Resolver(db, scope=scope)
    resolved = {}
    unresolved = []
    candidates = None
    resolution_tier_details = []  # Collect tier info for pipeline context

    # Map entity_type to GLOBAL_ENTITIES lookup type and result key
    # "member" is special: uses resolve_member_auto to try both commission_member
    # and pch_member depending on the intent module
    ENTITY_TYPE_MAP = {
        "agent": ("agent", "npn"),
        "carrier": ("carrier_name", "carrier_name"),
        "provider": ("pch_provider", "npi"),
        # "member" handled separately via resolve_member_auto
    }

    # Prefixes to strip from raw values (e.g. "NPN 12345" -> "12345")
    IDENTIFIER_PREFIXES = {"npn", "npi", "account", "acct", "id"}

    def _collect(result_key, value):
        """Append value to resolved dict, converting to list for multi-entity."""
        existing = resolved.get(result_key)
        if existing is None:
            resolved[result_key] = value
        elif isinstance(existing, list):
            if value not in existing:
                existing.append(value)
        else:
            if existing != value:
                resolved[result_key] = [existing, value]

    for mention in intent.entity_mentions:
        # Clean raw_value: strip identifier prefixes like "NPN", "NPI", "account"
        clean_value = mention.raw_value.strip()
        for prefix in IDENTIFIER_PREFIXES:
            lower = clean_value.lower()
            if lower.startswith(prefix):
                rest = clean_value[len(prefix):].lstrip(":").strip()
                if rest:
                    clean_value = rest
                    break

        # Special handling for "member" - try both commission_member and pch_member
        if mention.entity_type == "member":
            resolution = resolver.resolve_member_auto(
                clean_value,
                intent_module=intent.module,
            )
            tier = resolution.get("_resolution_tier", "unknown")
            details = resolution.get("_resolution_details", "")
            resolution_tier_details.append(f"member '{clean_value}': {tier} ({details})")
            if resolution.get("resolved"):
                matched_type = resolution.get("entity_type", "commission_member")
                if matched_type == "pch_member":
                    _collect("amisys_number", resolution["match"]["resolve_value"])
                else:
                    _collect("account_number", resolution["match"]["resolve_value"])
            else:
                if resolution.get("candidates"):
                    candidates = resolution["candidates"]
                unresolved.append(mention)
            continue

        mapping = ENTITY_TYPE_MAP.get(mention.entity_type)
        if not mapping:
            # Unknown entity type — try broad search across all tables
            resolution = resolver.resolve_entity_broad(clean_value)
            tier = resolution.get("_resolution_tier", "broad search")
            details = resolution.get("_resolution_details", "")
            resolution_tier_details.append(f"{mention.entity_type} '{clean_value}': {tier} ({details})")
            if resolution.get("resolved"):
                matched_type = resolution.get("entity_type", "")
                broad_mapping = ENTITY_TYPE_MAP.get(mention.entity_type)
                if not broad_mapping and matched_type in ENTITY_TYPE_MAP.values():
                    # Find the result key from the matched entity type
                    for _, (lt, rk) in ENTITY_TYPE_MAP.items():
                        if lt == matched_type:
                            _collect(rk, resolution["match"]["resolve_value"])
                            break
                elif broad_mapping:
                    _collect(broad_mapping[1], resolution["match"]["resolve_value"])
            else:
                if resolution.get("candidates"):
                    candidates = resolution["candidates"]
                unresolved.append(mention)
            continue

        lookup_type, result_key = mapping

        # Fast path for carrier entities: pass value directly as filter hint
        # without DB lookup. The SQL generator uses carrier_name in WHERE clauses.
        # The underlying view (vw_com_items_ai) is too slow for entity resolution.
        if mention.entity_type == "carrier":
            _collect(result_key, clean_value)
            resolution_tier_details.append(f"carrier '{clean_value}': Tier 0 (direct passthrough)")
            continue

        resolution = resolver.resolve_entity(
            lookup_type, clean_value, user_question=user_question,
        )

        tier = resolution.get("_resolution_tier", "unknown")
        details = resolution.get("_resolution_details", "")
        resolution_tier_details.append(f"{mention.entity_type} '{clean_value}': {tier} ({details})")

        if resolution.get("resolved"):
            _collect(result_key, resolution["match"]["resolve_value"])
        else:
            if resolution.get("candidates"):
                candidates = resolution["candidates"]
            unresolved.append(mention)

    # Escape single quotes in resolved values to prevent SQL injection
    # when the LLM inlines these values into WHERE clauses
    def _escape_value(v):
        if isinstance(v, str):
            return v.replace("'", "''")
        elif isinstance(v, list):
            return [item.replace("'", "''") if isinstance(item, str) else item for item in v]
        return v

    safe_resolved = {k: _escape_value(v) for k, v in resolved.items()}

    result = ResolvedEntities(
        resolved=safe_resolved,
        unresolved=unresolved,
        candidates=candidates,
    )
    # Attach resolution tier details for pipeline context logging
    result.resolution_tier_details = resolution_tier_details  # type: ignore[attr-defined]
    return result


def _should_use_dashboard(raw_data: list, intent: IntentResult, query: str) -> bool:
    """Determine if a query result should be presented as a dashboard.

    Dashboard format is used for:
    - Broad/insight queries ("give me insights", "overview", "summary of")
    - Results with enough data to show multiple views (>5 rows with mix of numeric/text columns)
    """
    if not raw_data or len(raw_data) < 2:
        return False

    query_lower = query.lower()

    # Keywords that suggest dashboard format
    _DASHBOARD_KEYWORDS = {
        "insight", "insights", "overview", "summary", "summarize",
        "dashboard", "analysis", "analyze", "comprehensive",
        "tell me about", "show me about", "what can you tell",
        "give me a", "break down", "breakdown",
    }

    for keyword in _DASHBOARD_KEYWORDS:
        if keyword in query_lower:
            return True

    # If intent explicitly requested dashboard
    if intent.format_hint == "dashboard":
        return True

    return False


def _normalize_chart_columns(raw_data: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Normalize 2-column results into {dimension, value} format for the format engine.

    LLM-generated SQL may use descriptive aliases like 'Total Commission' instead of 'value'.
    When there are exactly 2 columns and one is numeric, rename the numeric column to 'value'.
    """
    if not raw_data or len(raw_data[0]) != 2:
        return raw_data
    columns = list(raw_data[0].keys())
    if "value" in columns:
        return raw_data  # Already normalized

    # Detect which column is numeric by sampling the first non-null row
    numeric_col = None
    dim_col = None
    for row in raw_data:
        for c in columns:
            v = row.get(c)
            if v is None:
                continue
            try:
                float(v)
                numeric_col = c
            except (ValueError, TypeError):
                dim_col = c
        if numeric_col and dim_col:
            break

    if numeric_col and dim_col:
        return [{dim_col: row[dim_col], "value": row[numeric_col]} for row in raw_data]
    return raw_data


def _infer_dimensions_from_data(raw_data: List[Dict[str, Any]]) -> List[str]:
    """Infer dimension columns from result data.

    Any column that is NOT 'value' is treated as a dimension.
    This replaces the old intent.dimensions for format_engine compatibility.
    """
    if not raw_data:
        return []

    columns = list(raw_data[0].keys())
    # If there's a 'value' column, everything else is a dimension
    if "value" in columns:
        return [c for c in columns if c != "value"]
    # If no 'value' column, it's likely a list query — no explicit dimensions
    return []


# -------------------------------------------------------
# COMPARISON QUERIES
# -------------------------------------------------------

PERIOD_LABELS = {
    "this_month": "This Month",
    "last_month": "Last Month",
    "this_year": "This Year",
    "last_year": "Last Year",
}

COMPARISON_COLORS = ["#3B82F6", "#10B981", "#F59E0B", "#EF4444"]


def _resolve_relative_time_value(period_label: str) -> str:
    """Convert a relative time label to a date string for the SQL prompt."""
    from datetime import datetime as dt_cls
    from dateutil.relativedelta import relativedelta

    today = dt_cls.today()
    if period_label == "this_month":
        return today.strftime("%Y-%m")
    elif period_label == "last_month":
        return (today - relativedelta(months=1)).strftime("%Y-%m")
    elif period_label == "this_year":
        return today.strftime("%Y")
    elif period_label == "last_year":
        return (today - relativedelta(years=1)).strftime("%Y")
    return ""


def _process_comparison_query(
    query: str,
    intent: IntentResult,
    scope: dict,
    resolved_entities: dict,
    current_module: str,
    db: Session,
    synapse_db: Session = None,
) -> dict:
    """Run the same query for each comparison period and merge results."""
    periods = intent.comparison_periods
    has_dimensions = bool(intent.format_hint in ("bar_chart", "table", "line_chart"))

    period_results: List[Dict[str, Any]] = []

    domains = intent.domains if intent.domains else None

    for period_label in periods:
        time_value = _resolve_relative_time_value(period_label)
        # Modify the query to include the time period context
        period_query = f"{query} (filter to time period: {time_value})"
        period_summary = f"{intent.user_summary or query} for {PERIOD_LABELS.get(period_label, period_label)}"

        pipeline_result = run_data_query_pipeline(
            query=period_query,
            module=intent.module,
            scope=scope,
            resolved_entities=resolved_entities,
            user_summary=period_summary,
            format_hint=intent.format_hint,
            db=db,
            synapse_db=synapse_db,
            domains=domains,
        )

        period_results.append({
            "period": period_label,
            "display_label": PERIOD_LABELS.get(period_label, period_label),
            "data": pipeline_result.raw_data or [],
        })

    # Build comparison chart data
    chart_data = _build_comparison_chart(period_results)

    # Synthesize response
    message = synthesize_response(
        user_query=query,
        raw_data=chart_data.get("table_fallback", {}).get("rows", []),
        format_type="comparison_chart",
        current_module=current_module,
    )

    fingerprint = compute_sql_fingerprint(
        f"comparison:{intent.module}",
        [],
        intent.module,
    )

    if intent.assumption:
        chart_data["_assumption"] = intent.assumption

    return {
        "format": "comparison_chart",
        "message": message,
        "data": chart_data,
        "action": None,
        "action_data": None,
        "sql_fingerprint": fingerprint,
    }


def _build_comparison_chart(
    period_results: List[Dict],
) -> Dict[str, Any]:
    """Build multi-series chart data from period results."""
    # Check if results have dimension columns (non-value columns)
    has_dimensions = False
    for pr in period_results:
        if pr["data"]:
            cols = list(pr["data"][0].keys())
            if len(cols) > 1 and "value" in cols:
                has_dimensions = True
                break

    if has_dimensions:
        return _build_comparison_chart_with_dimensions(period_results)
    else:
        return _build_comparison_chart_scalar(period_results)


def _build_comparison_chart_with_dimensions(
    period_results: List[Dict],
) -> Dict[str, Any]:
    """Build multi-series chart data keyed by dimension values."""
    # Collect all dimension values across periods
    all_dim_values: List[str] = []
    seen: set = set()
    for pr in period_results:
        for row in pr["data"]:
            dim_col = next((k for k in row if k != "value"), None)
            if dim_col is None:
                continue
            dv = _clean_datetime(str(row.get(dim_col, "")))
            if dv not in seen:
                seen.add(dv)
                all_dim_values.append(dv)

    # Build lookup: period -> {dim_value: metric_value}
    period_lookups: Dict[str, Dict[str, float]] = {}
    for pr in period_results:
        lookup: Dict[str, float] = {}
        for row in pr["data"]:
            dim_col = next((k for k in row if k != "value"), None)
            if dim_col:
                raw_val = row.get("value", 0)
                dim_label = _clean_datetime(str(row.get(dim_col, "")))
                try:
                    lookup[dim_label] = float(raw_val) if raw_val else 0
                except (ValueError, TypeError):
                    lookup[dim_label] = 0
        period_lookups[pr["period"]] = lookup

    # Build series and change values
    series = []
    for i, pr in enumerate(period_results):
        series.append({
            "name": pr["display_label"],
            "color": COMPARISON_COLORS[i % len(COMPARISON_COLORS)],
            "values": [period_lookups[pr["period"]].get(dv, 0) for dv in all_dim_values],
        })

    change_values = []
    if len(period_results) == 2:
        for dv in all_dim_values:
            old_val = period_lookups[period_results[0]["period"]].get(dv, 0)
            new_val = period_lookups[period_results[1]["period"]].get(dv, 0)
            diff = new_val - old_val
            if old_val != 0:
                pct = (diff / abs(old_val)) * 100
                change_values.append(f"{diff:+,.2f} ({pct:+.1f}%)")
            else:
                change_values.append(f"{diff:+,.2f}")

    # Build table fallback
    columns = ["Category"] + [pr["display_label"] for pr in period_results]
    if change_values:
        columns.append("Change")

    rows = []
    for idx, dv in enumerate(all_dim_values):
        row = [dv]
        for pr in period_results:
            val = period_lookups[pr["period"]].get(dv, 0)
            row.append(f"{val:,.2f}" if val != int(val) else f"{int(val):,}")
        if change_values:
            row.append(change_values[idx])
        rows.append(row)

    return {
        "labels": all_dim_values,
        "series": series,
        "change_values": change_values,
        "table_fallback": {
            "columns": columns,
            "rows": rows,
            "total_rows": len(rows),
        },
    }


def _build_comparison_chart_scalar(
    period_results: List[Dict],
) -> Dict[str, Any]:
    """Build chart data for scalar (no dimension) comparisons."""
    labels = []
    values = []
    for pr in period_results:
        labels.append(pr["display_label"])
        val = 0
        if pr["data"] and len(pr["data"]) > 0:
            first_row = pr["data"][0]
            val = first_row.get("value", list(first_row.values())[0] if first_row else 0)
        try:
            val = float(val) if val else 0
        except (ValueError, TypeError):
            val = 0
        values.append(val)

    series = [{
        "name": "Value",
        "color": "#3B82F6",
        "values": values,
    }]

    change_values = []
    if len(values) == 2:
        old_val, new_val = values
        diff = new_val - old_val
        if old_val != 0:
            pct = (diff / abs(old_val)) * 100
            change_values.append(f"{diff:+,.2f} ({pct:+.1f}%)")
        else:
            change_values.append(f"{diff:+,.2f}")

    # Table fallback
    columns = ["Period", "Value"]
    if change_values:
        columns.append("Change")
    rows = []
    for i, label in enumerate(labels):
        formatted = f"{values[i]:,.2f}" if values[i] != int(values[i]) else f"{int(values[i]):,}"
        row = [label, formatted]
        rows.append(row)
    if change_values:
        rows[-1].append(change_values[0])
        for r in rows[:-1]:
            r.append("")

    return {
        "labels": labels,
        "series": series,
        "change_values": change_values,
        "table_fallback": {
            "columns": columns,
            "rows": rows,
            "total_rows": len(rows),
        },
    }


# -------------------------------------------------------
# CONVERSATIONS
# -------------------------------------------------------

@router.get("/conversations")
async def list_conversations(
    request: Request,
    page: int = 1,
    page_size: int = 20,
    entity_id: Optional[str] = None,
    db: Session = Depends(get_db),
):
    """List user's conversations, ordered by most recent."""
    user = get_current_user(request)
    user_id = str(user.get("user_id", user.get("id")))

    offset = (page - 1) * page_size

    query = (
        db.query(JayConversation)
        .filter(
            JayConversation.user_id == user_id,
            JayConversation.is_archived == False,
        )
    )

    # Entity-specific filter
    if entity_id:
        query = query.filter(JayConversation.entity_id == entity_id)
    else:
        # Permission-based filter for non-admin
        permitted = get_permitted_entity_ids(user_id, user.get("role"), db)
        if permitted is not None:  # None means admin, skip filter
            query = query.filter(
                or_(
                    JayConversation.entity_id.is_(None),
                    JayConversation.entity_id.in_(permitted),
                )
            )

    conversations = (
        query
        .order_by(desc(JayConversation.updated_at))
        .offset(offset)
        .limit(page_size)
        .all()
    )

    result = []
    for conv in conversations:
        msg_count = (
            db.query(func.count(JayMessage.id))
            .filter(JayMessage.conversation_id == conv.id)
            .scalar()
        )
        result.append({
            "id": str(conv.id),
            "title": conv.title,
            "updated_at": conv.updated_at.isoformat() if conv.updated_at else None,
            "message_count": msg_count,
            "entity_id": conv.entity_id,
            "entity_name": conv.entity_name,
        })

    return result


@router.get("/conversations/{conversation_id}")
async def get_conversation(
    conversation_id: str,
    request: Request,
    db: Session = Depends(get_db),
):
    """Get a conversation with all its messages."""
    user = get_current_user(request)
    user_id = str(user.get("user_id", user.get("id")))

    conversation = db.query(JayConversation).filter(
        JayConversation.id == conversation_id,
        JayConversation.user_id == user_id,
    ).first()

    if not conversation:
        raise HTTPException(status_code=404, detail="Conversation not found")

    # Permission check for entity-scoped conversations
    if conversation.entity_id:
        permitted = get_permitted_entity_ids(user_id, user.get("role"), db)
        if permitted is not None and conversation.entity_id not in permitted:
            raise HTTPException(
                status_code=403,
                detail="You no longer have access to this entity's conversations"
            )

    messages = (
        db.query(JayMessage)
        .filter(JayMessage.conversation_id == conversation.id)
        .order_by(JayMessage.created_at)
        .all()
    )

    is_admin = user.get("role") == "admin"

    return {
        "id": str(conversation.id),
        "title": conversation.title,
        "entity_id": conversation.entity_id,
        "entity_name": conversation.entity_name,
        "created_at": conversation.created_at.isoformat(),
        "messages": [
            {
                "id": str(m.id),
                "role": m.role,
                "content": m.content,
                "format": m.format,
                "data": m.data if is_admin else _strip_admin_data(m.data),
                "sql_fingerprint": m.sql_fingerprint,
                "action": m.action,
                "action_data": m.action_data,
                "rating": m.rating,
                "created_at": m.created_at.isoformat(),
            }
            for m in messages
        ],
    }


@router.delete("/conversations/{conversation_id}")
async def delete_conversation(
    conversation_id: str,
    request: Request,
    db: Session = Depends(get_db),
):
    """Delete a conversation and all its messages."""
    user = get_current_user(request)
    user_id = str(user.get("user_id", user.get("id")))

    conversation = db.query(JayConversation).filter(
        JayConversation.id == conversation_id,
        JayConversation.user_id == user_id,
    ).first()

    if not conversation:
        raise HTTPException(status_code=404, detail="Conversation not found")

    # Permission check for entity-scoped conversations
    if conversation.entity_id:
        permitted = get_permitted_entity_ids(user_id, user.get("role"), db)
        if permitted is not None and conversation.entity_id not in permitted:
            raise HTTPException(
                status_code=403,
                detail="You no longer have access to this entity's conversations"
            )

    db.delete(conversation)
    db.commit()
    return {"status": "deleted"}


# -------------------------------------------------------
# MESSAGE RATING
# -------------------------------------------------------

@router.post("/messages/{message_id}/rate")
async def rate_message(
    message_id: str,
    body: dict,
    request: Request,
    db: Session = Depends(get_db),
):
    """Rate a JAI assistant message (thumbs up/down)."""
    user = get_current_user(request)
    user_id = user.get("user_id") or user.get("id")

    rating = body.get("rating")
    if rating not in (1, -1, None):
        raise HTTPException(status_code=400, detail="rating must be 1, -1, or null")

    # Validate message exists, is assistant role, and belongs to user's conversation
    message = db.query(JayMessage).filter(
        JayMessage.id == message_id,
        JayMessage.role == "assistant",
    ).first()
    if not message:
        raise HTTPException(status_code=404, detail="Assistant message not found")

    # Verify the conversation belongs to the requesting user
    conversation = db.query(JayConversation).filter(
        JayConversation.id == message.conversation_id,
    ).first()
    if not conversation or str(conversation.user_id) != str(user_id):
        raise HTTPException(status_code=403, detail="You can only rate messages in your own conversations")

    message.rating = rating
    db.commit()
    db.refresh(message)

    # Trigger learning based on rating
    if rating == 1:
        # Positive rating: trigger background learning to embed successful pairs
        import threading

        def _trigger_learning():
            try:
                from app.db.session import SessionLocal
                from app.utils.jay.self_learning import update_learned_examples
                bg_session = SessionLocal()
                try:
                    update_learned_examples(bg_session)
                finally:
                    bg_session.close()
            except Exception as e:
                logger.error(f"Background learning trigger failed: {e}")

        threading.Thread(target=_trigger_learning, daemon=True).start()

    elif rating == -1:
        # Negative rating: store as negative example so the LLM avoids similar SQL
        msg_data = message.data or {}
        msg_sql = msg_data.get("_sql")
        if msg_sql:
            # Find the corresponding user query for this assistant message
            user_msg = (
                db.query(JayMessage)
                .filter(
                    JayMessage.conversation_id == message.conversation_id,
                    JayMessage.role == "user",
                    JayMessage.created_at < message.created_at,
                )
                .order_by(desc(JayMessage.created_at))
                .first()
            )
            user_query = user_msg.content if user_msg else "Unknown query"
            try:
                from app.utils.jay.self_learning import store_negative_example
                store_negative_example(user_query, msg_sql, "User rated as incorrect", db)
            except Exception as e:
                logger.error(f"Negative example storage failed: {e}")

    return {"message_id": str(message.id), "rating": message.rating}


# -------------------------------------------------------
# FAVORITES
# -------------------------------------------------------

@router.post("/favorites")
async def add_favorite(
    request: Request,
    body: FavoriteRequest,
    db: Session = Depends(get_db),
):
    """Add a prompt to favorites."""
    user = get_current_user(request)
    user_id = str(user.get("user_id", user.get("id")))

    favorite = JayFavorite(
        id=uuid.uuid4(),
        user_id=user_id,
        prompt_text=body.prompt_text,
        sql_fingerprint=body.sql_fingerprint,
        module=body.module,
    )
    db.add(favorite)
    db.commit()

    return {
        "id": str(favorite.id),
        "prompt_text": favorite.prompt_text,
        "module": favorite.module,
    }


@router.delete("/favorites/{favorite_id}")
async def remove_favorite(
    favorite_id: str,
    request: Request,
    db: Session = Depends(get_db),
):
    """Remove a favorite."""
    user = get_current_user(request)
    user_id = str(user.get("user_id", user.get("id")))

    favorite = db.query(JayFavorite).filter(
        JayFavorite.id == favorite_id,
        JayFavorite.user_id == user_id,
    ).first()

    if not favorite:
        raise HTTPException(status_code=404, detail="Favorite not found")

    db.delete(favorite)
    db.commit()
    return {"status": "deleted"}


@router.get("/favorites")
async def list_favorites(
    request: Request,
    db: Session = Depends(get_db),
):
    """List user's favorites, ordered by most used."""
    user = get_current_user(request)
    user_id = str(user.get("user_id", user.get("id")))

    favorites = (
        db.query(JayFavorite)
        .filter(JayFavorite.user_id == user_id)
        .order_by(desc(JayFavorite.use_count))
        .all()
    )

    return [
        {
            "id": str(f.id),
            "prompt_text": f.prompt_text,
            "module": f.module,
            "use_count": f.use_count,
            "last_used_at": f.last_used_at.isoformat() if f.last_used_at else None,
            "sql_fingerprint": f.sql_fingerprint,
        }
        for f in favorites
    ]


@router.post("/favorites/{favorite_id}/execute")
async def execute_favorite(
    favorite_id: str,
    request: Request,
    body: FavoriteExecuteRequest,
    db: Session = Depends(get_db),
):
    """Re-execute a favorited prompt with current permissions."""
    user = get_current_user(request)
    user_id = str(user.get("user_id", user.get("id")))

    favorite = db.query(JayFavorite).filter(
        JayFavorite.id == favorite_id,
        JayFavorite.user_id == user_id,
    ).first()

    if not favorite:
        raise HTTPException(status_code=404, detail="Favorite not found")

    # Update usage stats
    favorite.use_count += 1
    favorite.last_used_at = datetime.now(timezone.utc)

    # Re-process the prompt with current context
    scope = {
        "entity_id": body.context.entity_id,
        "sub_entity_id": body.context.sub_entity_id,
    }

    # Load or create conversation
    conversation = None
    if body.conversation_id:
        conversation = db.query(JayConversation).filter(
            JayConversation.id == body.conversation_id,
            JayConversation.user_id == user_id,
        ).first()

    if not conversation:
        entity_id_val = body.context.entity_id if body.context and body.context.entity_id else None
        entity_name_val = None
        if entity_id_val:
            entity_row = db.query(Entity.entity_name).filter(Entity.entity_id == entity_id_val).first()
            entity_name_val = entity_row.entity_name if entity_row else None

        conversation = JayConversation(
            id=uuid.uuid4(),
            user_id=user_id,
            title=favorite.prompt_text[:100].strip(),
            entity_id=entity_id_val,
            entity_name=entity_name_val,
        )
        db.add(conversation)
        db.flush()

    # Save user message (the favorited prompt)
    user_msg = JayMessage(
        id=uuid.uuid4(),
        conversation_id=conversation.id,
        role="user",
        content=favorite.prompt_text,
        format="text",
    )
    db.add(user_msg)

    # Obtain Synapse DB session for modules that query Synapse
    synapse_gen = get_synapse_db()
    synapse_session = next(synapse_gen)

    try:
        response = _process_query(
            query=favorite.prompt_text,
            scope=scope,
            current_module=body.context.current_module or favorite.module,
            conversation_history=[],
            db=db,
            user_role=user.get("role"),
            conversation_id=str(conversation.id),
            synapse_db=synapse_session,
        )
    except Exception as e:
        logger.error(f"Favorite execution error: {e}")
        response = {
            "format": "error",
            "message": "Failed to re-execute this query. Please try again.",
            "data": None,
            "action": None,
            "action_data": None,
            "sql_fingerprint": None,
        }
    finally:
        # Close the Synapse session
        try:
            next(synapse_gen)
        except StopIteration:
            pass

    # Save assistant response
    assistant_msg = JayMessage(
        id=uuid.uuid4(),
        conversation_id=conversation.id,
        role="assistant",
        content=response.get("message", ""),
        format=response.get("format", "text"),
        data=response.get("data"),
        sql_fingerprint=response.get("sql_fingerprint"),
    )
    db.add(assistant_msg)
    conversation.updated_at = datetime.now(timezone.utc)
    db.commit()

    # Strip admin-only fields for non-admin users
    fav_response_data = response.get("data")
    if user.get("role") != "admin":
        fav_response_data = _strip_admin_data(fav_response_data)

    return {
        "conversation_id": str(conversation.id),
        "message": {
            "id": str(assistant_msg.id),
            "role": "assistant",
            "content": response.get("message", ""),
            "format": response.get("format", "text"),
            "data": fav_response_data,
            "sql_fingerprint": response.get("sql_fingerprint"),
            "created_at": assistant_msg.created_at.isoformat(),
        },
    }


# -------------------------------------------------------
# CSV EXPORT
# -------------------------------------------------------

def _strip_limit(sql: str) -> str:
    """Remove LIMIT/OFFSET clauses from SQL for full export."""
    return re.sub(r"\s+LIMIT\s+\d+(\s+OFFSET\s+\d+)?", "", sql, flags=re.IGNORECASE)


@router.get("/messages/{message_id}/export")
async def export_message_csv(
    message_id: str,
    request: Request,
    db: Session = Depends(get_db),
):
    """Export full query results as CSV (no row limit).

    Re-executes the stored SQL without LIMIT to return all rows.
    """
    from app.utils.jay.db_executor import execute_query

    user = get_current_user(request)
    user_id = str(user.get("user_id", user.get("id")))

    # Retrieve message and verify ownership
    message = db.query(JayMessage).filter(JayMessage.id == message_id).first()
    if not message:
        raise HTTPException(status_code=404, detail="Message not found")

    conversation = db.query(JayConversation).filter(
        JayConversation.id == message.conversation_id,
        JayConversation.user_id == user_id,
    ).first()
    if not conversation:
        raise HTTPException(status_code=404, detail="Message not found")

    data = message.data or {}
    sql = data.get("_sql")
    module = data.get("_module")

    if not sql or not module:
        raise HTTPException(status_code=400, detail="No exportable data for this message")

    # Strip LIMIT for full export
    export_sql = _strip_limit(sql)

    try:
        rows = execute_query(module=module, sql=export_sql, params=[], db=db)
    except Exception as e:
        logger.error(f"CSV export query failed: {e}")
        raise HTTPException(status_code=500, detail="Export failed. Please try again.")

    if not rows:
        raise HTTPException(status_code=404, detail="No data to export")

    # Build CSV
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=rows[0].keys())
    writer.writeheader()
    writer.writerows(rows)
    output.seek(0)

    filename = f"jai_export_{module}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# -------------------------------------------------------
# SUGGESTIONS
# -------------------------------------------------------

@router.get("/suggestions")
async def get_suggestions(
    request: Request,
    current_module: Optional[str] = None,
    db: Session = Depends(get_db),
):
    """Get top 3 favorites + default suggestions for current module."""
    user = get_current_user(request)
    user_id = str(user.get("user_id", user.get("id")))

    top_favorites = (
        db.query(JayFavorite)
        .filter(JayFavorite.user_id == user_id)
        .order_by(desc(JayFavorite.use_count))
        .limit(3)
        .all()
    )

    top_fav_list = [
        {"prompt_text": f.prompt_text, "use_count": f.use_count}
        for f in top_favorites
    ]

    return merge_suggestions(top_fav_list, current_module)


# -------------------------------------------------------
# TRENDING ENDPOINT
# -------------------------------------------------------

_trending_cache: Dict[str, Any] = {"data": None, "timestamp": 0}
TRENDING_CACHE_TTL = 300  # 5 minutes


@router.get("/trending")
async def get_trending(request: Request, db: Session = Depends(get_db)):
    """Return top 10 trending business queries across all users."""
    get_current_user(request)

    now = _time.time()
    if _trending_cache["data"] is not None and (now - _trending_cache["timestamp"]) < TRENDING_CACHE_TTL:
        return _trending_cache["data"]

    # Subquery: conversations with successful data_query responses
    data_conv_ids = (
        db.query(JayMessage.conversation_id)
        .filter(
            JayMessage.role == "assistant",
            JayMessage.sql_fingerprint.isnot(None),
        )
        .distinct()
        .subquery()
    )

    results = (
        db.query(
            func.lower(func.trim(JayMessage.content)).label("query_text"),
            func.count(func.distinct(JayConversation.user_id)).label("user_count"),
            func.count(JayMessage.id).label("count"),
            func.max(JayMessage.created_at).label("last_asked_at"),
        )
        .join(JayConversation, JayMessage.conversation_id == JayConversation.id)
        .filter(
            JayMessage.role == "user",
            JayMessage.conversation_id.in_(db.query(data_conv_ids.c.conversation_id)),
            func.length(JayMessage.content) > 15,
        )
        .group_by(func.lower(func.trim(JayMessage.content)))
        .having(func.count(func.distinct(JayConversation.user_id)) >= 2)
        .order_by(desc("count"))
        .limit(10)
        .all()
    )

    data = [
        {
            "query_text": r.query_text,
            "count": r.count,
            "last_asked_at": r.last_asked_at.isoformat() if r.last_asked_at else None,
        }
        for r in results
    ]

    _trending_cache["data"] = data
    _trending_cache["timestamp"] = now
    return data
