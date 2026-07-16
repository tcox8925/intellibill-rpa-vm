"""
JAI Tool Engine - Multi-step LLM <-> tool conversation loop.

Sends messages to Azure OpenAI with tool definitions. When the LLM
returns tool_calls, dispatches each via dispatch_tool(), appends results,
and calls the LLM again. Repeats until LLM returns a regular message
or limits are reached.
"""

import json
import logging
import time
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from app.utils.jay.llm_client import get_llm_client
from app.utils.jay.pipeline_models import PipelineContext, ToolLoopResult

logger = logging.getLogger(__name__)


def run_tool_loop(
    system_prompt: str,
    user_message: str,
    tools: list,
    db_session: Session,
    join_graph=None,
    max_iterations: int = 8,
    timeout_seconds: float = 45.0,
    context: Optional[PipelineContext] = None,
    temperature: float = 0.0,
    tier: str = "default",
) -> ToolLoopResult:
    """Run a multi-step LLM <-> tool conversation loop.

    Sends the system prompt and user message to Azure OpenAI along with
    tool definitions. When the model returns ``tool_calls``, each call is
    dispatched via ``dispatch_tool()``, the results are appended to the
    conversation, and the model is called again.  The loop terminates when
    the model returns a regular (non-tool-call) message, or when
    ``max_iterations`` or ``timeout_seconds`` is exceeded.

    Args:
        system_prompt: System-level instruction for the LLM.
        user_message: The user's query text.
        tools: Tool schema list (TOOL_SCHEMAS from tools.py).
        db_session: SQLAlchemy database session for tool execution.
        join_graph: Optional JoinGraph from fk_inference for schema tools.
        max_iterations: Maximum number of LLM round-trips before stopping.
        timeout_seconds: Wall-clock timeout for the entire loop.
        context: Optional PipelineContext for structured logging.
        temperature: LLM sampling temperature (default 0.0 for determinism).
        tier: LLM deployment tier (default/fast/reasoning/fallback).

    Returns:
        ToolLoopResult with the final content, tool call log, and metadata.
    """
    # Lazy import to avoid circular dependency (tools.py imports from other
    # jay modules that may in turn reference pipeline_models).
    from app.utils.jay.tools import dispatch_tool

    client, deployment = get_llm_client(tier=tier)

    messages: List[Dict[str, Any]] = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_message},
    ]

    tool_calls_made: List[Dict[str, Any]] = []
    iterations = 0
    start_time = time.time()

    while iterations < max_iterations:
        # ── Timeout check ───────────────────────────────────────────
        elapsed = time.time() - start_time
        if elapsed >= timeout_seconds:
            logger.warning(
                "Tool loop timed out after %.1fs (%d iterations)",
                elapsed,
                iterations,
            )
            if context:
                context.add("tool_engine", "timeout", True)
            return ToolLoopResult(
                content="I wasn't able to finish processing in time. Please try again.",
                tool_calls_made=tool_calls_made,
                iterations=iterations,
                timed_out=True,
            )

        # ── LLM call ───────────────────────────────────────────────
        # Reasoning models (gpt-5.x) don't support temperature param
        _REASONING_DEPLOYS = {"jai-gpt52", "jai-deepseek-r1"}
        extra_kwargs = {} if deployment in _REASONING_DEPLOYS else {"temperature": temperature}
        try:
            response = client.chat.completions.create(
                model=deployment,
                messages=messages,
                tools=tools if tools else None,
                **extra_kwargs,
            )
        except Exception as exc:
            logger.exception("LLM call failed in tool loop (iteration %d)", iterations)
            return ToolLoopResult(
                content="",
                tool_calls_made=tool_calls_made,
                iterations=iterations,
                timed_out=False,
                error=f"LLM call failed: {exc}",
            )

        response_message = response.choices[0].message
        iterations += 1

        # ── Tool calls present -> dispatch each one ─────────────────
        if response_message.tool_calls:
            # Serialize the assistant message for the conversation history.
            # Azure OpenAI SDK returns objects, not dicts, so we convert.
            assistant_msg: Dict[str, Any] = {
                "role": "assistant",
                "content": response_message.content or "",
                "tool_calls": [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.function.name,
                            "arguments": tc.function.arguments,
                        },
                    }
                    for tc in response_message.tool_calls
                ],
            }
            messages.append(assistant_msg)

            for tc in response_message.tool_calls:
                fn_name = tc.function.name
                try:
                    fn_args = json.loads(tc.function.arguments)
                except (json.JSONDecodeError, TypeError):
                    fn_args = {}

                logger.info(
                    "Tool call [iter=%d]: %s(%s)",
                    iterations,
                    fn_name,
                    json.dumps(fn_args, default=str)[:200],
                )

                # Dispatch the tool
                try:
                    result = dispatch_tool(
                        fn_name, fn_args, db_session, join_graph
                    )
                except Exception as exc:
                    logger.exception(
                        "Tool dispatch failed: %s(%s)", fn_name, fn_args
                    )
                    result = json.dumps({"error": str(exc)})

                # Record for the call log
                call_record = {
                    "name": fn_name,
                    "arguments": fn_args,
                    "result_preview": result[:500] if isinstance(result, str) else str(result)[:500],
                }
                tool_calls_made.append(call_record)

                if context:
                    context.add("tool_engine", f"tool_call_{fn_name}", call_record)

                # Append the tool result to the conversation
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": result if isinstance(result, str) else json.dumps(result, default=str),
                })

            # Continue loop to let the LLM process tool results
            continue

        # ── No tool calls -> final response ─────────────────────────
        final_content = response_message.content or ""
        logger.info(
            "Tool loop completed: %d iterations, %d tool calls, %.1fs",
            iterations,
            len(tool_calls_made),
            time.time() - start_time,
        )
        if context:
            context.add("tool_engine", "iterations", iterations)
            context.add("tool_engine", "tool_calls_count", len(tool_calls_made))
            context.add("tool_engine", "elapsed_seconds", round(time.time() - start_time, 2))

        return ToolLoopResult(
            content=final_content,
            tool_calls_made=tool_calls_made,
            iterations=iterations,
            timed_out=False,
        )

    # ── Max iterations reached ──────────────────────────────────────
    logger.warning(
        "Tool loop hit max iterations (%d), %d tool calls made",
        max_iterations,
        len(tool_calls_made),
    )
    if context:
        context.add("tool_engine", "max_iterations_reached", True)

    # Try to extract any partial content from the last response
    last_content = ""
    if response_message and response_message.content:
        last_content = response_message.content

    return ToolLoopResult(
        content=last_content or "I reached the processing limit. Please try rephrasing your question.",
        tool_calls_made=tool_calls_made,
        iterations=iterations,
        timed_out=True,
    )
