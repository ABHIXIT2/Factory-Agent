"""
Agent loop: Groq LLM with tool-calling and confirm-before-write.

- Sessions stored in a thread-safe TTL cache (bounded memory).
- Per-user rate limiting (sliding window).
- Blocking Groq SDK calls offloaded with asyncio.to_thread.
- Tool results returned in OpenAI's `role: "tool"` format (Groq is OpenAI-compatible).
- Write tools (save_*, record_payment, create_customer) defer execution; the
  loop returns AgentResult(confirmation=...) and bot.py shows inline buttons.
"""

import asyncio
import json
import logging
import time
from collections import deque
from dataclasses import dataclass
from threading import Lock
from typing import Any, Deque, Dict, List, Optional, Tuple

from cachetools import TTLCache
from groq import Groq

from src.config import (
    GROQ_API_KEY, GROQ_MODEL, GROQ_MAX_TOKENS, GROQ_TEMPERATURE,
    SYSTEM_PROMPT, TOOLS, MAX_ITERATIONS, CONTEXT_WINDOW,
    SESSION_TTL_SECONDS, SESSION_MAX_USERS,
    RATE_LIMIT_MESSAGES, RATE_LIMIT_WINDOW_SECONDS,
)
from src.tools import execute_tool
from src import pending
from src.utils import format_amount

logger = logging.getLogger(__name__)

_client = Groq(api_key=GROQ_API_KEY)


# ----------------------------------------------------------------------------
# Public types
# ----------------------------------------------------------------------------

@dataclass
class Confirmation:
    token: str
    summary: str


@dataclass
class AgentResult:
    text: str
    confirmation: Optional[Confirmation] = None


# ----------------------------------------------------------------------------
# Write-tool registry: these require user confirmation before executing
# ----------------------------------------------------------------------------

WRITE_TOOLS = {
    "save_sale",
    "record_payment",
    "save_production",
    "save_cash_flow",
    "create_customer",
}


# ----------------------------------------------------------------------------
# Session store: TTL cache + lock (bounded, thread-safe)
# ----------------------------------------------------------------------------

_sessions: TTLCache = TTLCache(maxsize=SESSION_MAX_USERS, ttl=SESSION_TTL_SECONDS)
_sessions_lock = Lock()


def _get_history(user_id: int) -> List[Dict[str, Any]]:
    with _sessions_lock:
        return list(_sessions.get(user_id, []))


def _set_history(user_id: int, messages: List[Dict[str, Any]]) -> None:
    with _sessions_lock:
        cap = max(CONTEXT_WINDOW * 4, 20)
        _sessions[user_id] = messages[-cap:]


def clear_history(user_id: int) -> None:
    with _sessions_lock:
        _sessions.pop(user_id, None)
    pending.clear_user(user_id)


# ----------------------------------------------------------------------------
# Per-user rate limiter (sliding window)
# ----------------------------------------------------------------------------

_rate: Dict[int, Deque[float]] = {}
_rate_lock = Lock()


def _check_rate_limit(user_id: int) -> Tuple[bool, int]:
    now = time.monotonic()
    window = RATE_LIMIT_WINDOW_SECONDS
    with _rate_lock:
        bucket = _rate.setdefault(user_id, deque())
        while bucket and now - bucket[0] > window:
            bucket.popleft()
        if len(bucket) >= RATE_LIMIT_MESSAGES:
            retry = int(window - (now - bucket[0])) + 1
            return False, retry
        bucket.append(now)
        if len(_rate) > SESSION_MAX_USERS:
            _rate.pop(next(iter(_rate)), None)
        return True, 0


# ----------------------------------------------------------------------------
# Confirmation summary formatters
# ----------------------------------------------------------------------------

def _summarize_save_sale(args: Dict[str, Any]) -> str:
    qty = args.get("qty_kg", 0) or 0
    rate = args.get("rate_per_kg", 0) or 0
    total = float(qty) * float(rate)
    lines = [
        "📋 *Sale confirm karo:*",
        f"Customer ID: `{args.get('customer_id')}`",
        f"Qty: *{qty} kg* @ ₹{rate}",
        f"Total: *{format_amount(total)}*",
        f"Date: {args.get('sale_date')}",
        f"Payment: {args.get('payment_status')}"
        + (f" ({args.get('payment_mode')})" if args.get('payment_mode') else ""),
    ]
    if args.get("notes"):
        lines.append(f"Notes: {args['notes']}")
    return "\n".join(lines)


def _summarize_record_payment(args: Dict[str, Any]) -> str:
    return "\n".join([
        "📋 *Payment confirm karo:*",
        f"Customer ID: `{args.get('customer_id')}`",
        f"Amount: *{format_amount(args.get('amount', 0))}*",
        f"Date: {args.get('payment_date')}",
        f"Mode: {args.get('payment_mode') or 'cash'}",
    ])


def _summarize_save_production(args: Dict[str, Any]) -> str:
    return "\n".join([
        "📋 *Production confirm karo:*",
        f"Date: {args.get('prod_date')}",
        f"Qty: *{args.get('total_produced_kg')} kg*",
        f"Packets: {args.get('total_packets')}",
    ])


def _summarize_save_cash_flow(args: Dict[str, Any]) -> str:
    return "\n".join([
        f"📋 *Cash {args.get('flow_type', '?').upper()} confirm karo:*",
        f"Date: {args.get('flow_date')}",
        f"Amount: *{format_amount(args.get('amount', 0))}*",
        f"Category: {args.get('category')}",
        f"Description: {args.get('description')}",
        f"Party: {args.get('party') or '-'}",
    ])


def _summarize_create_customer(args: Dict[str, Any]) -> str:
    return "\n".join([
        "📋 *New customer confirm karo:*",
        f"Shop: *{args.get('shop_name')}*",
        f"Owner: {args.get('owner_name') or '-'}",
        f"Phone: {args.get('owner_phone') or '-'}",
        f"Credit limit: {format_amount(args.get('credit_limit', 0))}",
    ])


_SUMMARIZERS = {
    "save_sale": _summarize_save_sale,
    "record_payment": _summarize_record_payment,
    "save_production": _summarize_save_production,
    "save_cash_flow": _summarize_save_cash_flow,
    "create_customer": _summarize_create_customer,
}


def _build_summary(write_calls: List[pending.PendingToolCall]) -> str:
    parts = []
    for call in write_calls:
        fn = _SUMMARIZERS.get(call.name)
        parts.append(fn(call.arguments) if fn else f"📋 {call.name}: {call.arguments}")
    parts.append("\nReply with the buttons below.")
    return "\n\n".join(parts)


# ----------------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------------

def _serialize_tool_calls(message_tool_calls) -> List[Dict[str, Any]]:
    return [
        {
            "id": tc.id,
            "type": "function",
            "function": {"name": tc.function.name, "arguments": tc.function.arguments},
        }
        for tc in message_tool_calls
    ]


async def _call_groq(messages: List[Dict[str, Any]]):
    return await asyncio.to_thread(
        _client.chat.completions.create,
        model=GROQ_MODEL,
        messages=messages,
        tools=TOOLS,
        tool_choice="auto",
        max_tokens=GROQ_MAX_TOKENS,
        temperature=GROQ_TEMPERATURE,
    )


# ----------------------------------------------------------------------------
# Main loop
# ----------------------------------------------------------------------------

async def agent_loop(
    user_message: str,
    user_id: int,
    username: str = None,
    first_name: str = None,
) -> AgentResult:
    """
    Run one user turn through the LLM.

    Returns an AgentResult. If the LLM stages a write tool, the result
    carries `confirmation` (token + summary) — bot.py renders inline buttons.
    Otherwise `text` is the final reply.

    Read tools execute inline; write tools are deferred. The whole batch is
    deferred if *any* tool in a single LLM response is a write — see
    WRITE_TOOLS for the list.
    """
    allowed, retry = _check_rate_limit(user_id)
    if not allowed:
        logger.warning("Rate limited user %s (retry after %ss)", user_id, retry)
        return AgentResult(text=f"⏳ Bahut messages bhej diye. Thoda ruk ke phir bhejo (~{retry}s).")

    history = _get_history(user_id)
    history.append({"role": "user", "content": user_message})

    messages: List[Dict[str, Any]] = [{"role": "system", "content": SYSTEM_PROMPT}, *history]

    final_text: str = ""
    confirmation: Optional[Confirmation] = None

    try:
        for iteration in range(1, MAX_ITERATIONS + 1):
            logger.debug("Agent iter %s for user %s", iteration, user_id)
            response = await _call_groq(messages)

            if not response.choices or not response.choices[0].message:
                logger.error("Empty response from Groq for user %s", user_id)
                return AgentResult(text="❌ Sorry, kuch samajh nahi aaya. Phir se try karo.")

            message = response.choices[0].message

            if not message.tool_calls:
                final_text = (message.content or "✅ Done.").strip()
                messages.append({"role": "assistant", "content": final_text})
                break

            assistant_msg = {
                "role": "assistant",
                "content": message.content or "",
                "tool_calls": _serialize_tool_calls(message.tool_calls),
            }

            # Parse arguments + classify
            parsed_calls: List[Tuple[Any, Dict[str, Any], bool]] = []  # (tc, args, has_arg_error)
            for tc in message.tool_calls:
                try:
                    args = json.loads(tc.function.arguments or "{}")
                    if not isinstance(args, dict):
                        raise ValueError("tool arguments must be a JSON object")
                    args.setdefault("user_id", user_id)
                    parsed_calls.append((tc, args, False))
                except (json.JSONDecodeError, ValueError) as exc:
                    logger.warning(
                        "Bad tool args for %s from user %s: %s",
                        tc.function.name, user_id, exc,
                    )
                    parsed_calls.append((tc, {}, True))

            has_write = any(
                tc.function.name in WRITE_TOOLS and not err
                for tc, _, err in parsed_calls
            )

            if has_write:
                # Defer ALL calls in this response — stage a confirmation.
                pending_calls = [
                    pending.PendingToolCall(
                        id=tc.id, name=tc.function.name, arguments=args,
                    )
                    for tc, args, err in parsed_calls if not err
                ]
                write_calls = [c for c in pending_calls if c.name in WRITE_TOOLS]
                summary = _build_summary(write_calls)
                token = pending.put(pending.PendingAction(
                    user_id=user_id,
                    assistant_message=assistant_msg,
                    tool_calls=pending_calls,
                    summary=summary,
                ))
                confirmation = Confirmation(token=token, summary=summary)
                # Don't persist the unanswered tool_call to history — we'll add
                # it once the user confirms. This keeps history valid if cancelled.
                final_text = summary
                # Strip the user message we appended; it stays in history regardless
                break

            # No writes → execute every tool inline and continue the loop.
            messages.append(assistant_msg)
            for tc, args, err in parsed_calls:
                if err:
                    tool_result = json.dumps({"ok": False, "error": "invalid tool arguments JSON"})
                else:
                    logger.info("tool_call user=%s name=%s", user_id, tc.function.name)
                    tool_result = await execute_tool(tc.function.name, args)
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "name": tc.function.name,
                    "content": tool_result,
                })
        else:
            logger.warning("Max iterations reached for user %s", user_id)
            final_text = "⏱️ Bahut steps ho gaye. Simpler request bhejo."

    except Exception:
        logger.exception("Agent loop failure for user %s", user_id)
        return AgentResult(text="❌ Kuch gadbad ho gayi. Phir se try karo.")

    _set_history(user_id, [m for m in messages if m["role"] != "system"])
    logger.info("Agent finished for user %s: %s", user_id, final_text[:80])
    return AgentResult(text=final_text or "✅ Done.", confirmation=confirmation)


# ----------------------------------------------------------------------------
# Resume after confirmation: execute the staged tool calls and ask LLM for a
# natural-language closing message.
# ----------------------------------------------------------------------------

async def continue_after_confirmation(
    user_id: int,
    action: pending.PendingAction,
) -> AgentResult:
    """
    User pressed ✅. Replay the staged assistant message + execute every
    deferred tool call, then ask the LLM for a one-line natural-language
    closing message ("Sale saved, baqaya: ₹6,000.").
    """
    history = _get_history(user_id)
    messages: List[Dict[str, Any]] = [{"role": "system", "content": SYSTEM_PROMPT}, *history]

    # Append the assistant message that proposed the writes.
    messages.append(action.assistant_message)

    # Execute all staged tool calls in order, append their results.
    for call in action.tool_calls:
        logger.info("confirmed_tool_call user=%s name=%s", user_id, call.name)
        try:
            tool_result = await execute_tool(call.name, dict(call.arguments))
        except Exception:
            logger.exception("Confirmed tool %s failed for user %s", call.name, user_id)
            tool_result = json.dumps({"ok": False, "error": "internal error"})
        messages.append({
            "role": "tool",
            "tool_call_id": call.id,
            "name": call.name,
            "content": tool_result,
        })

    # Ask the LLM to summarize what just happened.
    try:
        response = await _call_groq(messages)
        msg = response.choices[0].message if response.choices else None
        text = (msg.content if msg else None) or "✅ Done."
        messages.append({"role": "assistant", "content": text})
    except Exception:
        logger.exception("Post-confirmation summary call failed for user %s", user_id)
        text = "✅ Saved."

    _set_history(user_id, [m for m in messages if m["role"] != "system"])
    return AgentResult(text=text)


async def cancel_pending(user_id: int, action: pending.PendingAction) -> AgentResult:
    """User pressed ❌. Drop the staged action; mark cancellation in history so
    the LLM doesn't re-propose the same write on the next turn."""
    history = _get_history(user_id)
    history.append({"role": "assistant", "content": "(User cancelled the pending action.)"})
    _set_history(user_id, history)
    return AgentResult(text="❌ Cancelled. Kuch save nahi hua.")
