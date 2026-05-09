"""Detailed LLM debugging logs - raw input/output, iteration details, cache info."""

import logging
import sys
import json
from datetime import datetime
from typing import Any


def _get_logger(name: str) -> logging.Logger:
    """Get or create a logger (no duplicate handlers)."""
    logger = logging.getLogger(name)
    logger.propagate = False  # Don't propagate to root logger

    if not logger.handlers:
        handler = logging.StreamHandler(sys.stderr)
        formatter = logging.Formatter(
            "%(asctime)s - %(name)s - %(levelname)s - %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S"
        )
        handler.setFormatter(formatter)
        logger.addHandler(handler)

    logger.setLevel(logging.DEBUG)
    return logger


# ============================================================================
# LLM Debugging - Raw Input/Output
# ============================================================================

def log_llm_request(user_id: int, iteration: int, model: str, messages: list[dict[str, Any]]) -> None:
    """Log the exact messages being sent to LLM."""
    logger = _get_logger("src.llm.request")

    logger.debug(f"{'='*10}🔄 LLM REQUEST | User {user_id} | Iteration {iteration} | Model: {model}{'='*10}")

    for i, msg in enumerate(messages):
        role = msg.get("role", "unknown")
        content = msg.get("content", "")
        tool_calls = msg.get("tool_calls")

        if role == "system":
            logger.debug(f"[MSG {i}] SYSTEM PROMPT:\n{content}")
        elif role == "user":
            logger.debug(f"[MSG {i}] USER:\n{content}")
        elif role == "assistant":
            logger.debug(f"[MSG {i}] ASSISTANT:")
            if content:
                logger.debug(f"  Content: {content}")
            if tool_calls:
                logger.debug(f"  Tool calls: {json.dumps(tool_calls, indent=2)}")
        elif role == "tool":
            logger.debug(f"[MSG {i}] TOOL ({msg.get('name')}):")
            logger.debug(f"  {content}")

    logger.debug(f"{'='*10}⬆️  REQUEST END{'='*10}")


def log_llm_response(user_id: int, iteration: int, response: Any) -> None:
    """Log the raw LLM response."""
    logger = _get_logger("src.llm.response")

    logger.debug(f"{'='*10}✅ LLM RESPONSE | User {user_id} | Iteration {iteration}{'='*10}")

    if not response or not response.choices:
        logger.debug("ERROR: Empty response from LLM\n")
        return

    choice = response.choices[0]
    message = choice.message

    # Log message content
    if message.content:
        logger.debug(f"CONTENT:\n{message.content}")

    # Log tool calls if present
    if message.tool_calls:
        logger.debug(f"TOOL CALLS ({len(message.tool_calls)}):")
        for i, tc in enumerate(message.tool_calls, 1):
            logger.debug(f"  [{i}] {tc.function.name}")
            logger.debug(f"      ID: {tc.id}")
            args = json.loads(tc.function.arguments or "{}")
            logger.debug(f"      Args: {json.dumps(args, indent=8)}")

    # Log usage if available
    usage = getattr(response, "usage", None)
    if usage:
        p = getattr(usage, "prompt_tokens", 0) or 0
        c = getattr(usage, "completion_tokens", 0) or 0
        t = getattr(usage, "total_tokens", 0) or 0
        logger.debug(f"USAGE: prompt={p} completion={c} total={t}")

    logger.debug(f"{'='*10}⬇️  RESPONSE END{'='*10}")


def log_tool_execution(user_id: int, tool_name: str, args: dict[str, Any], result: str) -> None:
    """Log tool execution with input and output."""
    logger = _get_logger("src.tool.execution")

    logger.debug(f"\n--- Tool: {tool_name} (User {user_id}) ---")
    logger.debug(f"Input: {json.dumps(args, indent=2)}")

    try:
        result_obj = json.loads(result)
        logger.debug(f"Output: {json.dumps(result_obj, indent=2)}")
    except:
        logger.debug(f"Output: {result}")

    logger.debug("---\n")


# ============================================================================
# User & Query Tracking
# ============================================================================

def log_user_message(user_id: int, message: str) -> None:
    """Log incoming user message."""
    logger = _get_logger("src.user.message")
    logger.info(f"{'='*10}🎬 AGENT LOOP START | User {user_id} | 📝 {message}{'='*10}")


def log_query_result(user_id: int, final_response: str, iterations: int, tools_used: list[str]) -> None:
    """Log final query result."""
    logger = _get_logger("src.query.result")
    logger.info(f"{'='*10}✨ AGENT LOOP COMPLETE | User {user_id}{'='*10}")


# ============================================================================
# Confirmation & State
# ============================================================================

def log_confirmation_staged(user_id: int, summary: str, tool_calls: list[dict]) -> None:
    """Log when confirmation is staged."""
    logger = _get_logger("src.confirmation")
    logger.info(f"\n--- CONFIRMATION STAGED (User {user_id}) ---")
    logger.info(f"Summary:\n{summary}")
    logger.info(f"Tool calls to execute: {json.dumps(tool_calls, indent=2)}")
    logger.info("---\n")


def log_confirmation_executed(user_id: int, tool_calls: list[dict]) -> None:
    """Log when user confirms and tools execute."""
    logger = _get_logger("src.confirmation")
    logger.info(f"\n--- CONFIRMATION EXECUTED (User {user_id}) ---")
    logger.info(f"Executing {len(tool_calls)} tool(s)")
    logger.info("---\n")


# ============================================================================
# Error Tracking
# ============================================================================

def log_error(user_id: int, error_type: str, error_msg: str, context: dict = None) -> None:
    """Log errors with context."""
    logger = _get_logger("src.error")
    logger.error(f"\nERROR - User {user_id}, Type: {error_type}")
    logger.error(f"Message: {error_msg}")
    if context:
        logger.error(f"Context: {json.dumps(context, indent=2)}")
    logger.error("")


def log_db_error(
    tool_name: str,
    user_id: int | None,
    exc: Any,
    args: dict[str, Any] | None = None,
) -> None:
    """Log a Supabase/PostgreSQL APIError with full structured context."""
    logger = _get_logger("src.error")
    logger.error(
        "\nDB ERROR — Tool: %s | User: %s | PG code: %s",
        tool_name, user_id, getattr(exc, "code", "?"),
    )
    logger.error("  message : %s", getattr(exc, "message", str(exc)))
    logger.error("  details : %s", getattr(exc, "details", None))
    logger.error("  hint    : %s", getattr(exc, "hint", None))
    if args:
        from src.utils import redact_secrets
        logger.error("  args    : %s", redact_secrets(json.dumps(args, default=str)))
    logger.error("")


def log_agent_error(user_id: int, error_type: str, context: dict | None = None) -> None:
    """Log agent-level errors (max iterations, unexpected loop failures)."""
    log_error(user_id, f"AgentError:{error_type}", error_type, context)


def log_selection_ui(user_id: int, options: list[dict]) -> None:
    """Log customer selection UI."""
    logger = _get_logger("src.selection")
    logger.info(f"\n--- SELECTION UI (User {user_id}) ---")
    logger.info(f"Options ({len(options)}):")
    for i, opt in enumerate(options, 1):
        logger.info(f"  {i}. {opt}")
    logger.info("---\n")
