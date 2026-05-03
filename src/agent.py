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
import sys
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import date, datetime
from threading import Lock
from typing import Any

from cachetools import TTLCache
from groq import Groq
import groq as groq_sdk
import openai
from openai import AsyncOpenAI

from src.config import (
    GROQ_API_KEY, GROQ_MODEL, GROQ_MODEL_FAST, GROQ_MAX_TOKENS, GROQ_TEMPERATURE,
    SYSTEM_PROMPT, TOOLS, MAX_ITERATIONS, CONTEXT_WINDOW,
    SESSION_TTL_SECONDS, SESSION_MAX_USERS,
    RATE_LIMIT_MESSAGES, RATE_LIMIT_WINDOW_SECONDS,
    TOOL_RESULT_HISTORY_MAX_CHARS,
    GOOGLE_AI_STUDIO_KEY, GOOGLE_MODEL, GOOGLE_FALLBACK_ENABLED,
    GROQ_DAILY_TOKEN_LIMIT, GOOGLE_DAILY_TOKEN_LIMIT,
)
from src.tools import execute_tool
from src import pending, selection
from src.utils import format_amount, detect_user_lang

logger = logging.getLogger(__name__)

_client = Groq(api_key=GROQ_API_KEY)

_google_client: AsyncOpenAI | None = (
    AsyncOpenAI(
        api_key=GOOGLE_AI_STUDIO_KEY,
        base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
    )
    if GOOGLE_FALLBACK_ENABLED
    else None
)


# ----------------------------------------------------------------------------
# Per-provider daily usage tracking + colored terminal output
# ----------------------------------------------------------------------------

@dataclass
class _DailyStats:
    tokens: int = 0
    calls: int = 0
    reset_date: str = field(default_factory=lambda: date.today().isoformat())


_groq_day = _DailyStats()
_google_day = _DailyStats()
_usage_lock = Lock()


def _reset_if_new_day(stats: _DailyStats) -> None:
    today = date.today().isoformat()
    if stats.reset_date != today:
        stats.tokens = 0
        stats.calls = 0
        stats.reset_date = today


_IS_TTY: bool = sys.stderr.isatty()


def _color(text: str, code: str) -> str:
    return f"\033[{code}m{text}\033[0m" if _IS_TTY else text


def _token_bar(used: int, total: int, width: int = 20) -> str:
    filled = min(int(width * used / total), width) if total else 0
    return "█" * filled + "░" * (width - filled)


def _print_usage(
    provider: str,
    model: str,
    prompt: int,
    completion: int,
    total: int,
    day_total: int,
    day_limit: int,
) -> None:
    pct = int(100 * day_total / day_limit) if day_limit else 0
    bar = _token_bar(day_total, day_limit)
    ts = datetime.now().strftime("%H:%M:%S")
    bar_code = "33" if pct > 80 else ("36" if provider == "groq" else "32")
    label = _color("◆ GROQ  ", "36") if provider == "groq" else _color("◆ GEMINI", "32")
    line = (
        f"{_color(ts, '2')} {label} {_color(model, '1')}  "
        f"prompt={prompt:,} compl={completion:,} total={total:,}  "
        f"day={_color(f'{day_total:,}/{day_limit:,}', bar_code)}  "
        f"[{_color(bar, bar_code)}] {pct}%"
    )
    sys.stderr.write(line + "\n")
    sys.stderr.flush()


def _warn_rate_limit(provider: str) -> None:
    ts = datetime.now().strftime("%H:%M:%S")
    if provider.lower() == "gemini":
        msg = "⚠  GEMINI RATE LIMITED"
        fallback = "GROQ"
    else:
        msg = "⚠  GROQ RATE LIMITED"
        fallback = "GEMINI"
    line = (
        f"{_color(ts, '2')} "
        f"{_color(msg, '1;33')} "
        f"{_color(f'→ falling back to {fallback}', '33')}"
    )
    sys.stderr.write(line + "\n")
    sys.stderr.flush()
    logger.warning("rate_limited provider=%s falling_back_to=%s", provider, fallback.lower())


# ----------------------------------------------------------------------------
# Public types
# ----------------------------------------------------------------------------

@dataclass
class Confirmation:
    token: str
    summary: str
    _is_selection: bool = False


@dataclass
class AgentResult:
    text: str
    confirmation: Confirmation | None = None


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


def _get_history(user_id: int) -> list[dict[str, Any]]:
    with _sessions_lock:
        return list(_sessions.get(user_id, []))


# Domain-relevant args to extract per tool when summarizing dropped history.
_SUMMARY_KEY_FIELDS: dict[str, list[str]] = {
    "search_customer": ["name_fragment"],
    "save_sale": ["customer_id", "qty_kg", "rate_per_kg", "sale_date"],
    "record_payment": ["customer_id", "amount", "payment_date"],
    "create_customer": ["shop_name"],
    "save_production": ["prod_date", "total_produced_kg"],
    "save_cash_flow": ["flow_date", "flow_type", "amount", "category"],
    "get_customer_balance": ["customer_id"],
    "query_sales": ["customer_id", "date_from", "date_to"],
    "get_all_balances": [],
    "get_cash_position": [],
}


def _compact_tool_result(content: str) -> str:
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
    out: List[Dict[str, Any]] = []
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


def _set_history(user_id: int, messages: list[dict[str, Any]]) -> None:
    """Persist with three-stage compaction:
       1. Shrink any oversized tool result (1.2).
       2. Find a tool_call/tool-pair-safe boundary (1.3).
       3. Replace the dropped chunk with a one-message factual summary (1.5).
    """
    compacted = _compact_history_tool_results(messages)
    cap = max(CONTEXT_WINDOW * 4, 20)
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
    with _sessions_lock:
        _sessions.pop(user_id, None)
    pending.clear_user(user_id)


def inject_selected_customer(user_id: int, customer_id: int, shop_name: str) -> None:
    """Inject a synthetic search_customer result into session history so the
    next agent_loop call knows which customer was chosen without an LLM call."""
    history = _get_history(user_id)
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
    _set_history(user_id, history)


# ----------------------------------------------------------------------------
# Per-user rate limiter (sliding window)
# ----------------------------------------------------------------------------

_rate: Dict[int, Deque[float]] = {}
_rate_lock = Lock()


def _check_rate_limit(user_id: int) -> tuple[bool, int]:
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

def _extract_customer_names(messages: list[dict[str, Any]]) -> dict[int, str]:
    """Walk the message history and build a {customer_id: shop_name} map from
    every search_customer and create_customer tool result. Used to render
    confirmation cards with human-readable names instead of raw IDs."""
    name_map: dict[int, str] = {}
    for msg in messages:
        if msg.get("role") != "tool":
            continue
        tool_name = msg.get("name")
        try:
            payload = json.loads(msg.get("content") or "{}")
        except (json.JSONDecodeError, TypeError):
            continue
        if tool_name == "search_customer":
            for row in payload.get("results", []) or []:
                cid, name = row.get("id"), row.get("shop_name")
                if cid is not None and name:
                    name_map[int(cid)] = name
        elif tool_name == "create_customer" and payload.get("ok"):
            cid = payload.get("customer_id")
            name = payload.get("shop_name")
            if cid is not None and name:
                name_map[int(cid)] = name
    return name_map


def _customer_label(args: dict[str, Any], names: dict[int, str]) -> str:
    """Resolve a customer_id arg to a Markdown-bold shop name; fall back to a
    plain ID line if the name isn't in history."""
    cid = args.get("customer_id")
    try:
        cid_int = int(cid) if cid is not None else None
    except (TypeError, ValueError):
        cid_int = None
    name = names.get(cid_int) if cid_int is not None else None
    return f"Customer: *{name}*" if name else f"Customer ID: `{cid}`"


def _summarize_save_sale(args: dict[str, Any], names: dict[int, str]) -> str:
    qty = args.get("qty_kg", 0) or 0
    rate = args.get("rate_per_kg", 0) or 0
    total = float(qty) * float(rate)
    lines = [
        "📦 *Sale confirm kijiye:*",
        "─────────────────",
        _customer_label(args, names),
        f"Qty: *{qty} kg* @ *₹{rate}/kg*",
        f"Total: *{format_amount(total)}*",
        f"Date: `{args.get('sale_date')}`",
        f"Payment: *{args.get('payment_status')}*"
        + (f" ({args.get('payment_mode')})" if args.get('payment_mode') else ""),
    ]
    if args.get("notes"):
        lines.append(f"Notes: {args['notes']}")
    return "\n".join(lines)


def _summarize_record_payment(args: dict[str, Any], names: dict[int, str]) -> str:
    return "\n".join([
        "💳 *Payment confirm kijiye:*",
        "─────────────────",
        _customer_label(args, names),
        f"Amount: *{format_amount(args.get('amount', 0))}*",
        f"Date: `{args.get('payment_date')}`",
        f"Mode: *{args.get('payment_mode') or 'cash'}*",
    ])


def _summarize_save_production(args: dict[str, Any], _names: dict[int, str]) -> str:
    return "\n".join([
        "🏭 *Production confirm kijiye:*",
        "─────────────────",
        f"Date: `{args.get('prod_date')}`",
        f"Qty: *{args.get('total_produced_kg')} kg*",
        f"Packets: *{args.get('total_packets')}*",
    ])


def _summarize_save_cash_flow(args: dict[str, Any], _names: dict[int, str]) -> str:
    flow_type = args.get('flow_type', '?').upper()
    emoji = "💰" if flow_type == "IN" else "💸"
    return "\n".join([
        f"{emoji} *Cash {flow_type} confirm kijiye:*",
        "─────────────────",
        f"Date: `{args.get('flow_date')}`",
        f"Amount: *{format_amount(args.get('amount', 0))}*",
        f"Category: *{args.get('category')}*",
        f"Description: {args.get('description')}",
        f"Party: {args.get('party') or '–'}",
    ])


def _summarize_create_customer(args: dict[str, Any], _names: dict[int, str]) -> str:
    return "\n".join([
        "👤 *New customer confirm kijiye:*",
        "─────────────────",
        f"Shop: *{args.get('shop_name')}*",
        f"Owner: {args.get('owner_name') or '–'}",
        f"Phone: {args.get('owner_phone') or '–'}",
        f"Credit limit: *{format_amount(args.get('credit_limit', 0))}*",
    ])


_SUMMARIZERS = {
    "save_sale": _summarize_save_sale,
    "record_payment": _summarize_record_payment,
    "save_production": _summarize_save_production,
    "save_cash_flow": _summarize_save_cash_flow,
    "create_customer": _summarize_create_customer,
}


def _build_summary(
    write_calls: list[pending.PendingToolCall],
    customer_names: dict[int, str] | None = None,
) -> str:
    names = customer_names or {}
    parts = []
    for call in write_calls:
        fn = _SUMMARIZERS.get(call.name)
        parts.append(fn(call.arguments, names) if fn else f"📋 {call.name}: {call.arguments}")
    parts.append("\n⬇️ नीचे दिए बटन से confirm kijiye:")
    return "\n\n".join(parts)


# ----------------------------------------------------------------------------
# Post-confirmation closing-message templates
# Two scripts per tool: hi-Latn (Hinglish/English, default) and hi-Deva (Devanagari).
# Pure Python — no LLM round-trip after the user taps ✅.
# ----------------------------------------------------------------------------

def _parse_tool_result(content: str) -> dict[str, Any]:
    try:
        payload = json.loads(content or "{}")
        return payload if isinstance(payload, dict) else {}
    except (json.JSONDecodeError, TypeError):
        return {}


def _customer_text(args: dict[str, Any], names: dict[int, str]) -> str:
    cid = args.get("customer_id")
    try:
        cid_int = int(cid) if cid is not None else None
    except (TypeError, ValueError):
        cid_int = None
    name = names.get(cid_int) if cid_int is not None else None
    return name or f"Customer {cid}"


def _close_save_sale(args, result, names, lang):
    qty = args.get("qty_kg", 0) or 0
    rate = args.get("rate_per_kg", 0) or 0
    total = result.get("total_bill", float(qty) * float(rate))
    customer = _customer_text(args, names)
    if lang == "hi-Deva":
        return (f"✅ बिक्री सेव हो गई: *{qty} किलो* @ *₹{rate}/किलो* = "
                f"*{format_amount(total)}*. {customer} को।")
    return (f"✅ Sale saved: *{qty} kg* @ *₹{rate}/kg* = *{format_amount(total)}*. "
            f"Customer: *{customer}*.")


def _close_record_payment(args, result, names, lang):
    amount = args.get("amount", 0)
    new_bal = result.get("new_balance", 0)
    customer = _customer_text(args, names)
    if lang == "hi-Deva":
        return (f"💳 भुगतान सेव हो गया: *{format_amount(amount)}* — {customer} से। "
                f"नया बकाया: *{format_amount(new_bal)}*.")
    return (f"💳 Payment saved: *{format_amount(amount)}* from *{customer}*. "
            f"Naya baqaya: *{format_amount(new_bal)}*.")


def _close_create_customer(args, result, _names, lang):
    shop = args.get("shop_name", "?")
    cid = result.get("customer_id", "?")
    if lang == "hi-Deva":
        return f"👤 नया ग्राहक जुड़ गया: *{shop}* (id: `{cid}`)."
    return f"👤 New customer added: *{shop}* (id: `{cid}`)."


def _close_save_production(args, _result, _names, lang):
    kg = args.get("total_produced_kg", 0)
    packets = args.get("total_packets", 0)
    date_iso = args.get("prod_date", "?")
    if lang == "hi-Deva":
        return f"🏭 उत्पादन सेव हो गया: *{kg} किलो*, *{packets}* पैकेट (`{date_iso}`)."
    return f"🏭 Production saved: *{kg} kg*, *{packets}* packets (`{date_iso}`)."


def _close_save_cash_flow(args, _result, _names, lang):
    flow = (args.get("flow_type") or "?").lower()
    amount = args.get("amount", 0)
    category = args.get("category", "?")
    if lang == "hi-Deva":
        verb = "जमा" if flow == "in" else "खर्च"
        return f"💰 कैश {verb}: *{format_amount(amount)}* ({category})."
    verb = "in" if flow == "in" else "out"
    return f"💰 Cash {verb}: *{format_amount(amount)}* ({category})."


_CLOSING_TEMPLATES = {
    "save_sale": _close_save_sale,
    "record_payment": _close_record_payment,
    "create_customer": _close_create_customer,
    "save_production": _close_save_production,
    "save_cash_flow": _close_save_cash_flow,
}


def _render_closing(
    call: "pending.PendingToolCall",
    tool_result: str,
    customer_names: dict[int, str],
    user_lang: str,
) -> str:
    fn = _CLOSING_TEMPLATES.get(call.name)
    if fn is None:
        return "✅ Saved."
    parsed = _parse_tool_result(tool_result)
    if not parsed.get("ok", True):
        err = parsed.get("error") or "kuch gadbad"
        return f"❌ Save nahi ho paya: {err}"
    try:
        return fn(call.arguments or {}, parsed, customer_names, user_lang)
    except Exception:
        logger.exception("closing-template render failed for %s", call.name)
        return "✅ Saved."


# ----------------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------------

def _serialize_tool_calls(message_tool_calls) -> list[dict[str, Any]]:
    return [
        {
            "id": tc.id,
            "type": "function",
            "function": {"name": tc.function.name, "arguments": tc.function.arguments},
        }
        for tc in message_tool_calls
    ]


async def _call_groq(messages: list[dict[str, Any]], model: str | None = None):
    chosen_model = model or GROQ_MODEL

    # Google is primary when enabled; Groq is fallback
    if GOOGLE_FALLBACK_ENABLED:
        try:
            return await _call_google(messages)
        except openai.RateLimitError:
            _warn_rate_limit("gemini")
            # Fall through to Groq below
        except (openai.APIError, openai.APIConnectionError) as e:
            logger.warning("Google API error: %s, falling back to Groq", type(e).__name__)
            # Fall through to Groq below

    # Groq: either primary (if Google disabled) or fallback
    try:
        response = await asyncio.to_thread(
            _client.chat.completions.create,
            model=chosen_model,
            messages=messages,
            tools=TOOLS,
            tool_choice="auto",
            max_tokens=GROQ_MAX_TOKENS,
            temperature=GROQ_TEMPERATURE,
        )
    except groq_sdk.RateLimitError:
        raise  # Both providers exhausted

    usage = getattr(response, "usage", None)
    if usage is not None:
        p = getattr(usage, "prompt_tokens", 0) or 0
        c = getattr(usage, "completion_tokens", 0) or 0
        t = getattr(usage, "total_tokens", 0) or 0
        logger.info(
            "groq_usage model=%s prompt=%s completion=%s total=%s msgs=%d",
            chosen_model, p, c, t, len(messages),
        )
        with _usage_lock:
            _reset_if_new_day(_groq_day)
            _groq_day.tokens += t
            _groq_day.calls += 1
            day_total = _groq_day.tokens
        _print_usage("groq", chosen_model, p, c, t, day_total, GROQ_DAILY_TOKEN_LIMIT)
    return response


async def _call_google(messages: list[dict[str, Any]], model: str | None = None):
    chosen_model = model or GOOGLE_MODEL
    response = await _google_client.chat.completions.create(
        model=chosen_model,
        messages=messages,
        tools=TOOLS,
        tool_choice="auto",
        max_tokens=GROQ_MAX_TOKENS,
        temperature=GROQ_TEMPERATURE,
    )
    usage = getattr(response, "usage", None)
    if usage is not None:
        p = getattr(usage, "prompt_tokens", 0) or 0
        c = getattr(usage, "completion_tokens", 0) or 0
        t = getattr(usage, "total_tokens", 0) or 0
        logger.info(
            "gemini_usage model=%s prompt=%s completion=%s total=%s msgs=%d",
            chosen_model, p, c, t, len(messages),
        )
        with _usage_lock:
            _reset_if_new_day(_google_day)
            _google_day.tokens += t
            _google_day.calls += 1
            day_total = _google_day.tokens
        _print_usage("google", chosen_model, p, c, t, day_total, GOOGLE_DAILY_TOKEN_LIMIT)
    return response


# ----------------------------------------------------------------------------
# Main loop
# ----------------------------------------------------------------------------

async def agent_loop(
    user_message: str,
    user_id: int,
    username: str | None = None,
    first_name: str | None = None,
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
        return AgentResult(text=f"⏳ Apne bahut messages bhej diye. Kripiya ruk ke phir bhejein (~{retry}s).")

    history = _get_history(user_id)
    history.append({"role": "user", "content": user_message})

    messages: list[dict[str, Any]] = [{"role": "system", "content": SYSTEM_PROMPT}, *history]

    final_text: str = ""
    confirmation: Confirmation | None = None

    try:
        for iteration in range(1, MAX_ITERATIONS + 1):
            logger.debug("Agent iter %s for user %s", iteration, user_id)
            # Iter 1 — the "which tool with what args" decision — gets the
            # bigger model. Iter 2+ is mostly synthesizing a known tool
            # result, which the cheaper 8B handles at a fraction of the TPD.
            chosen_model = GROQ_MODEL if iteration == 1 else GROQ_MODEL_FAST
            response = await _call_groq(messages, model=chosen_model)

            if not response.choices or not response.choices[0].message:
                # Fallback once to the bigger model if the fast model returned
                # nothing usable on iter 2+.
                if chosen_model != GROQ_MODEL:
                    logger.warning(
                        "Empty response on fast model iter %s — retrying on %s",
                        iteration, GROQ_MODEL,
                    )
                    response = await _call_groq(messages, model=GROQ_MODEL)
                if not response.choices or not response.choices[0].message:
                    logger.error("Empty response from Groq for user %s", user_id)
                    return AgentResult(text="❌ Sorry, main samajh nahi paya. Phir se try karein.")

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
            parsed_calls: list[tuple[Any, dict[str, Any], bool]] = []
            for tc in message.tool_calls:
                try:
                    args = json.loads(tc.function.arguments or "{}")
                    if not isinstance(args, dict):
                        raise ValueError("tool arguments must be a JSON object")
                    args["user_id"] = user_id  # trusted: always wins over any LLM-supplied value
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
                customer_names = _extract_customer_names(messages)
                summary = _build_summary(write_calls, customer_names)
                user_lang = detect_user_lang(user_message)
                token = pending.put(pending.PendingAction(
                    user_id=user_id,
                    assistant_message=assistant_msg,
                    tool_calls=pending_calls,
                    summary=summary,
                    extras={
                        "user_lang": user_lang,
                        "customer_names": customer_names,
                        "original_message": user_message,
                        "username": username,
                        "first_name": first_name,
                    },
                ))
                confirmation = Confirmation(token=token, summary=summary)
                # Don't persist the unanswered tool_call to history — we'll add
                # it once the user confirms. This keeps history valid if cancelled.
                final_text = summary
                # Strip the user message we appended; it stays in history regardless
                break

            # No writes → execute every tool inline and continue the loop.
            messages.append(assistant_msg)
            selection_prompt = None  # Used if search_customer returns multiple close matches
            for tc, args, err in parsed_calls:
                if err:
                    tool_result = json.dumps({"ok": False, "error": "invalid tool arguments JSON"})
                else:
                    logger.info("tool_call user=%s name=%s", user_id, tc.function.name)
                    try:
                        tool_result = await execute_tool(tc.function.name, args)
                    except Exception as exc:
                        exc.add_note(f"tool={tc.function.name}, user_id={user_id}")
                        raise

                # Check if search_customer returned ambiguous matches needing UI selection
                if tc.function.name == "search_customer" and not err:
                    try:
                        result_data = json.loads(tool_result)
                        if result_data.get("selection_required"):
                            # Multiple close matches — show selection UI instead of LLM reasoning
                            user_lang = detect_user_lang(user_message)
                            customer_options = [
                                selection.CustomerOption(
                                    id=opt["id"],
                                    shop_name=opt["shop_name"],
                                )
                                for opt in result_data.get("customer_options", [])
                            ]
                            sel = selection.PendingSelection(
                                user_id=user_id,
                                original_message=user_message,
                                username=username,
                                first_name=first_name,
                                customer_options=customer_options,
                                extras={"user_lang": user_lang},
                            )
                            sel_token = selection.put(sel)
                            selection_prompt = (sel_token, customer_options, user_lang)
                            # Skip adding this tool result to messages; we'll return to bot instead
                            break
                    except (json.JSONDecodeError, TypeError):
                        pass

                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "name": tc.function.name,
                    "content": tool_result,
                })

            # If selection prompt was triggered, break out and return to bot
            if selection_prompt is not None:
                sel_token, customer_options, user_lang = selection_prompt
                if user_lang == "hi-Deva":
                    selection_text = "🔍 *कई ग्राहक मिले। नीचे से चुनिए:*"
                else:
                    selection_text = "🔍 *Multiple customers found. Please select one:*"
                final_text = selection_text
                confirmation = Confirmation(token=sel_token, summary=selection_text, _is_selection=True)
                break
        else:
            logger.warning("Max iterations reached for user %s", user_id)
            final_text = "⏱️ Bahut steps ho gaye. assan task dijiye."

    except (groq_sdk.GroqError, openai.APIError, ValueError, json.JSONDecodeError) as exc:
        logger.exception("Agent loop error for user %s", user_id)
        return AgentResult(text="❌ Kuch gadbad ho gayi. kripeya phir se try karein.")
    except Exception as exc:
        logger.exception("Unexpected agent error for user %s", user_id)
        exc.add_note(f"user_id={user_id}, loop failure")
        raise

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
    """Execute deferred tool calls and render closing via template (no LLM round-trip)."""
    history = _get_history(user_id)
    messages: list[dict[str, Any]] = [{"role": "system", "content": SYSTEM_PROMPT}, *history]
    messages.append(action.assistant_message)

    extras = action.extras or {}
    user_lang = extras.get("user_lang", "hi-Latn")
    customer_names = dict(extras.get("customer_names") or {})
    customer_names.update(_extract_customer_names(messages))

    closing_lines: list[str] = []
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
        if call.name in WRITE_TOOLS:
            closing_lines.append(_render_closing(call, tool_result, customer_names, user_lang))

    text = "\n\n".join(closing_lines) if closing_lines else "✅ Saved."

    # If a customer was just created and there's an original intent that was NOT
    # itself a customer creation, re-run it (e.g., "Sharma ko sale karo" → search failed
    # → create Sharma → re-run sale). Skip if user explicitly asked for create_customer.
    if any(c.name == "create_customer" for c in action.tool_calls):
        orig_msg = extras.get("original_message", "")
        # Only chain if original message doesn't look like a customer creation request
        is_explicit_create = (
            orig_msg and any(
                phrase in orig_msg.lower()
                for phrase in ["naya customer", "new customer", "banao", "bana do", "create customer"]
            )
        )
        if orig_msg and not is_explicit_create:
            creation_text = text
            messages.append({"role": "assistant", "content": text})
            _set_history(user_id, [m for m in messages if m["role"] != "system"])

            follow_up = await agent_loop(
                user_message=orig_msg,
                user_id=user_id,
                username=extras.get("username", ""),
                first_name=extras.get("first_name", ""),
            )
            combined_text = creation_text + "\n\n" + follow_up.text
            return AgentResult(text=combined_text, confirmation=follow_up.confirmation)

    messages.append({"role": "assistant", "content": text})
    _set_history(user_id, [m for m in messages if m["role"] != "system"])
    return AgentResult(text=text)


async def cancel_pending(user_id: int, action: pending.PendingAction) -> AgentResult:
    """User pressed ❌. Drop the staged action; mark cancellation in history so
    the LLM doesn't re-propose the same write on the next turn."""
    history = _get_history(user_id)
    history.append({"role": "assistant", "content": "(User cancelled the pending action.)"})
    _set_history(user_id, history)
    return AgentResult(text="❌ Cancelled. Kuch save nahi hua.")
