"""LLM provider clients: Groq, Google, rate-limit fallback, usage tracking."""

import logging
import sys
from datetime import date, datetime
from typing import Any

from groq import Groq
import groq as groq_sdk
import openai
from openai import AsyncOpenAI
import asyncio

from src.config import (
    GROQ_API_KEY, GOOGLE_AI_STUDIO_KEY, GOOGLE_MODEL, GOOGLE_FALLBACK_ENABLED,
    GROQ_MODEL, GROQ_MODEL_FAST, GROQ_MAX_TOKENS, GROQ_TEMPERATURE,
    GROQ_DAILY_TOKEN_LIMIT, GOOGLE_DAILY_TOKEN_LIMIT,
)

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

# Daily usage tracking
from dataclasses import dataclass, field
from threading import Lock


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


async def call_llm(
    messages: list[dict[str, Any]], model: str | None = None
) -> Any:
    """Call Groq LLM with Google Gemini fallback on rate-limit."""
    from src.config import SYSTEM_PROMPT, TOOLS

    chosen_model = model or GROQ_MODEL

    # Google is primary when enabled; Groq is fallback
    if GOOGLE_FALLBACK_ENABLED:
        try:
            return await _call_google(messages)
        except openai.RateLimitError:
            _warn_rate_limit("gemini")
        except (openai.APIError, openai.APIConnectionError) as e:
            logger.warning("Google API error: %s, falling back to Groq", type(e).__name__)

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
        raise

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
    from src.config import TOOLS

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
