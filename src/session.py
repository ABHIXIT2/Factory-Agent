"""Session store: history management, rate limiting, history compaction."""

import json
import logging
import time
from collections import deque
from threading import Lock
from typing import Any

from cachetools import TTLCache

from src.config import (
    SESSION_TTL_SECONDS, SESSION_MAX_USERS,
    RATE_LIMIT_MESSAGES, RATE_LIMIT_WINDOW_SECONDS,
    CONTEXT_WINDOW, TOOL_RESULT_HISTORY_MAX_CHARS,
    HISTORY_COMPACT_THRESHOLD,
)

logger = logging.getLogger(__name__)

# Session store: TTL cache + lock (bounded, thread-safe)
_sessions: TTLCache = TTLCache(maxsize=SESSION_MAX_USERS, ttl=SESSION_TTL_SECONDS)
_sessions_lock = Lock()

# Per-user rate limiter (sliding window)
_rate: dict[int, deque[float]] = {}
_rate_lock = Lock()

# Domain-relevant args to extract per tool when summarizing dropped history.
_SUMMARY_KEY_FIELDS: dict[str, list[str]] = {
    "search_customer": ["name_fragment"],
    "save_sale": ["customer_id", "qty_kg", "rate_per_kg", "sale_date"],
    "record_payment": ["customer_id", "amount", "payment_date"],
    "create_customer": ["shop_name"],
    "save_production": ["prod_date", "total_produced_kg"],
    "save_cash_flow": ["flow_date", "flow_type", "amount", "category"],
    "get_customer": ["customer_id"],
    "query_customers": ["name_fragment", "min_balance", "max_balance"],
    "get_customer_balance": ["customer_id"],
    "query_sales": ["customer_id", "date_from", "date_to"],
    "query_production": ["date_from", "date_to"],
    "query_cash_flow": ["date_from", "date_to", "flow_type", "category"],
    "query_credit_ledger": ["customer_id", "transaction_type", "date_from", "date_to"],
    "get_all_balances": [],
    "delete_record": ["table", "record_id"],
    "get_cash_position": ["date_from", "date_to"],
}


def get_history(user_id: int) -> list[dict[str, Any]]:
    """Retrieve session history for a user."""
    with _sessions_lock:
        return list(_sessions.get(user_id, []))


def _compact_tool_result(content: str) -> str:  # Used by agent.py for in-flight compaction
    """Shrink oversized tool-result JSON for session history (full sent to LLM in-flight)."""
    if not content or len(content) <= TOOL_RESULT_HISTORY_MAX_CHARS:
        return content
    try:
        payload = json.loads(content)
    except (json.JSONDecodeError, TypeError):
        return json.dumps({
            "ok": True, "truncated": True,
            "preview": content[:600], "total_size": len(content),
        })
    if not isinstance(payload, dict):
        return json.dumps({"ok": True, "truncated": True, "total_size": len(content)})
    summary: dict[str, Any] = {"ok": payload.get("ok", True), "truncated": True}
    for k, v in payload.items():
        if k == "ok":
            continue
        if isinstance(v, list) and len(v) > 3:
            summary[k] = v[:3]
            summary[f"{k}_total"] = len(v)
        else:
            summary[k] = v
    return json.dumps(summary)


def _compact_history_tool_results(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Walk the persisted-message list and shrink any oversized tool result."""
    out: list[dict[str, Any]] = []
    for msg in messages:
        if msg.get("role") == "tool":
            new_content = _compact_tool_result(msg.get("content") or "")
            if new_content != msg.get("content"):
                msg = {**msg, "content": new_content}
        out.append(msg)
    return out


def _find_safe_boundary(messages: list[dict[str, Any]], proposed_cut: int) -> int:
    """Return an index >= proposed_cut that is safe to slice at — i.e. doesn't
    leave a tool message orphaned from its assistant tool_call. Always preserves
    the most recent user turn (floor)."""
    n = len(messages)
    if proposed_cut <= 0 or n == 0:
        return 0
    # Floor: most recent user message — never cut past this.
    floor = 0
    for i in range(n - 1, -1, -1):
        if messages[i].get("role") == "user":
            floor = i
            break
    cut = min(proposed_cut, floor)
    while cut < n:
        msg = messages[cut]
        role = msg.get("role")
        if role == "user":
            return cut
        if role == "assistant" and not msg.get("tool_calls"):
            return cut
        cut += 1
    return n


def _compact_dropped_messages(dropped: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Heuristically extract structural facts from messages we're about to drop.
    Returns one role='system' message with bullet-list facts, or None if there
    is nothing salient. No LLM call — pure Python."""
    lines: list[str] = []
    for msg in dropped:
        role = msg.get("role")
        if role == "system":
            # Already-compacted prior summary: keep its body verbatim.
            body = (msg.get("content") or "").strip()
            if body.startswith("[Earlier turns"):
                # Drop the header, keep the bullets.
                tail = body.split("\n", 1)[1] if "\n" in body else ""
                if tail:
                    lines.append(tail)
        elif role == "user":
            content = (msg.get("content") or "").strip()
            if content:
                lines.append(f'- User: "{content[:80]}"')
        elif role == "assistant":
            for tc in (msg.get("tool_calls") or []):
                fn = tc.get("function") or {}
                name = fn.get("name", "?")
                try:
                    args = json.loads(fn.get("arguments") or "{}")
                except (json.JSONDecodeError, TypeError):
                    args = {}
                key_fields = _SUMMARY_KEY_FIELDS.get(name, [])
                snippet = ", ".join(f"{k}={args[k]}" for k in key_fields if k in args)
                lines.append(f"- {name}({snippet})")
        elif role == "tool":
            name = msg.get("name", "?")
            try:
                payload = json.loads(msg.get("content") or "{}")
            except (json.JSONDecodeError, TypeError):
                continue
            if not isinstance(payload, dict):
                continue
            if name == "search_customer":
                rows = payload.get("results") or []
                if rows:
                    pairs = ", ".join(
                        f"id={r.get('id')}({r.get('shop_name')})" for r in rows[:3]
                    )
                    lines.append(f"  -> {pairs}")
            elif name == "save_sale":
                lines.append(
                    f"  -> sale_id={payload.get('sale_id')}, total={payload.get('total_bill')}"
                )
            elif name == "record_payment":
                lines.append(
                    f"  -> ledger_id={payload.get('ledger_id')}, "
                    f"new_balance={payload.get('new_balance')}"
                )
            elif name == "create_customer":
                lines.append(f"  -> customer_id={payload.get('id')}")
            elif name == "get_customer_balance":
                lines.append(f"  -> outstanding={payload.get('outstanding_balance')}")
            elif name in ("save_production", "save_cash_flow"):
                lines.append(f"  -> id={payload.get('id')}")
    if not lines:
        return None
    body = "[Earlier turns - compacted]\n" + "\n".join(lines)
    # Self-cap: collapse oldest entries if the summary itself grew too big.
    while len(body) > TOOL_RESULT_HISTORY_MAX_CHARS and len(lines) > 5:
        omitted = len(lines) - 5
        lines = [f"- (...{omitted} earlier entries omitted)"] + lines[-5:]
        body = "[Earlier turns - compacted]\n" + "\n".join(lines)
    return {"role": "system", "content": body}


def set_history(user_id: int, messages: list[dict[str, Any]]) -> None:
    """Persist with three-stage compaction:
       1. Shrink any oversized tool result.
       2. Find a tool_call/tool-pair-safe boundary.
       3. Replace the dropped chunk with a one-message factual summary.
    """
    compacted = _compact_history_tool_results(messages)
    cap = HISTORY_COMPACT_THRESHOLD
    if len(compacted) <= cap:
        with _sessions_lock:
            _sessions[user_id] = compacted
        return
    boundary = _find_safe_boundary(compacted, len(compacted) - cap)
    if boundary <= 0:
        with _sessions_lock:
            _sessions[user_id] = compacted
        return
    dropped = compacted[:boundary]
    survivors = compacted[boundary:]
    summary_msg = _compact_dropped_messages(dropped)
    final = ([summary_msg] + survivors) if summary_msg else survivors
    with _sessions_lock:
        _sessions[user_id] = final


def clear_history(user_id: int) -> None:
    """Clear session history and pending actions for a user."""
    with _sessions_lock:
        _sessions.pop(user_id, None)
    from src import pending
    pending.clear_user(user_id)


def inject_selected_customer(user_id: int, customer_id: int, shop_name: str) -> None:
    """Inject a synthetic search_customer result into session history so the
    next agent_loop call knows which customer was chosen without an LLM call."""
    history = get_history(user_id)
    synthetic_id = f"sel_{customer_id}"
    history.append({
        "role": "assistant",
        "content": None,
        "tool_calls": [{
            "id": synthetic_id,
            "type": "function",
            "function": {
                "name": "search_customer",
                "arguments": json.dumps({"name_fragment": shop_name}),
            },
        }],
    })
    history.append({
        "role": "tool",
        "tool_call_id": synthetic_id,
        "name": "search_customer",
        "content": json.dumps({
            "ok": True,
            "results": [{"id": customer_id, "shop_name": shop_name}],
            "count": 1,
        }),
    })
    set_history(user_id, history)


def inject_created_customer(user_id: int, customer_id: int, shop_name: str) -> None:
    """Inject a synthetic create_customer result into session history so the
    next agent_loop call knows which customer was just created without querying the DB."""
    history = get_history(user_id)
    synthetic_id = f"created_{customer_id}"
    history.append({
        "role": "assistant",
        "content": f"✅ Customer created: {shop_name}. Continuing with your request...",
        "tool_calls": [{
            "id": synthetic_id,
            "type": "function",
            "function": {
                "name": "create_customer",
                "arguments": json.dumps({"shop_name": shop_name}),
            },
        }],
    })
    history.append({
        "role": "tool",
        "tool_call_id": synthetic_id,
        "name": "create_customer",
        "content": json.dumps({
            "ok": True,
            "customer_id": customer_id,
            "shop_name": shop_name,
        }),
    })
    set_history(user_id, history)


def check_rate_limit(user_id: int) -> tuple[bool, int]:
    """Check sliding-window rate limit. Returns (allowed, retry_after_seconds)."""
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
