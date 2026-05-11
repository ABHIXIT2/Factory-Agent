"""LLM provider clients: Cerebras, Google, Groq, rate-limit fallback, usage tracking."""

import logging
import sys
from datetime import date, datetime
from typing import Any

from groq import Groq
import groq as groq_sdk
import openai
from openai import AsyncOpenAI
import asyncio
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception, RetryError

from src.config import (
    GROQ_API_KEY, GOOGLE_AI_STUDIO_KEY, GOOGLE_MODEL, GOOGLE_PRIMARY_ENABLED,
    CEREBRAS_API_KEY, CEREBRAS_MODEL, CEREBRAS_PRIMARY_ENABLED,
    GROQ_MODEL, GROQ_MAX_TOKENS, GROQ_TEMPERATURE,
    GROQ_DAILY_TOKEN_LIMIT, GOOGLE_DAILY_TOKEN_LIMIT, CEREBRAS_DAILY_TOKEN_LIMIT,
    TOOLS,
)

logger = logging.getLogger(__name__)

_client = Groq(api_key=GROQ_API_KEY)

_google_client: AsyncOpenAI | None = (
    AsyncOpenAI(
        api_key=GOOGLE_AI_STUDIO_KEY,
        base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
    )
    if GOOGLE_PRIMARY_ENABLED
    else None
)

_cerebras_client: AsyncOpenAI | None = (
    AsyncOpenAI(
        api_key=CEREBRAS_API_KEY,
        base_url="https://api.cerebras.ai/v1",
    )
    if CEREBRAS_PRIMARY_ENABLED
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
_cerebras_day = _DailyStats()
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
    _provider_color = {"groq": "36", "google": "32", "cerebras": "35"}
    base_code = _provider_color.get(provider, "37")
    bar_code = "33" if pct > 80 else base_code
    _provider_label = {
        "groq": "◆ GROQ    ",
        "google": "◆ GEMINI  ",
        "cerebras": "◆ CEREBRAS",
    }
    label = _color(_provider_label.get(provider, f"◆ {provider.upper()}"), base_code)
    line = (
        f"{_color(ts, '2')} {label} {_color(model, '1')}  "
        f"prompt={prompt:,} compl={completion:,} total={total:,}  "
        f"day={_color(f'{day_total:,}/{day_limit:,}', bar_code)}  "
        f"[{_color(bar, bar_code)}] {pct}%"
    )
    sys.stderr.write(line + "\n")
    sys.stderr.flush()


def _warn_rate_limit(provider: str) -> None:
    """Silent rate limit handling - fallback is transparent."""
    pass


def _is_retryable_rate_limit(exc: BaseException) -> bool:
    """Retry per-minute / transient rate limits. Skip per-day (TPD) limits — they
    won't recover within retry window, and retrying just burns more quota."""
    if not isinstance(exc, groq_sdk.RateLimitError):
        return False
    msg = str(exc).lower()
    if "per day" in msg or "tpd" in msg:
        return False
    return True


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=1, max=4),
    retry=retry_if_exception(_is_retryable_rate_limit),
)
def _call_groq_with_retry(messages: list[dict[str, Any]], model: str, temperature: float) -> Any:
    """Call Groq with retry logic for transient rate limits. Runs in thread context."""
    return _client.chat.completions.create(
        model=model,
        messages=messages,
        tools=TOOLS,
        tool_choice="auto",
        max_tokens=GROQ_MAX_TOKENS,
        temperature=temperature,
    )


async def call_llm(
    messages: list[dict[str, Any]], model: str | None = None
) -> Any:
    """Fallback chain: Cerebras → Gemini → Groq. Each step is skipped if not configured
    or falls through on rate-limit / transient API error."""
    chosen_model = model or GROQ_MODEL

    # Cerebras is primary when enabled
    if CEREBRAS_PRIMARY_ENABLED:
        try:
            return await _call_cerebras(messages)
        except openai.RateLimitError:
            logger.info("Provider fallback: cerebras_rate_limit → gemini")
            _warn_rate_limit("cerebras")
        except (openai.APIError, openai.APIConnectionError) as e:
            logger.info("Provider fallback: cerebras_api_error → gemini (error: %s)", type(e).__name__)

    # Gemini next
    if GOOGLE_PRIMARY_ENABLED:
        try:
            return await _call_google(messages)
        except openai.RateLimitError:
            logger.info("Provider fallback: gemini_rate_limit → groq")
            _warn_rate_limit("gemini")
        except (openai.APIError, openai.APIConnectionError) as e:
            logger.info("Provider fallback: gemini_api_error → groq (error: %s)", type(e).__name__)

    # Groq: final fallback
    logger.info("Calling Groq with retry logic enabled")
    try:
        response = await asyncio.to_thread(
            _call_groq_with_retry,
            messages,
            chosen_model,
            GROQ_TEMPERATURE,
        )
    except RetryError as exc:
        # Unwrap so callers see the underlying RateLimitError (or similar)
        underlying = exc.last_attempt.exception()
        if underlying is not None:
            raise underlying from exc
        raise

    finish_reason = getattr(response.choices[0], "finish_reason", None)
    if finish_reason == "length":
        logger.warning(
            "Response truncated by max_tokens=%d for model=%s. "
            "Consider raising GROQ_MAX_TOKENS.",
            GROQ_MAX_TOKENS, chosen_model
        )

    usage = getattr(response, "usage", None)
    if usage is not None:
        p = getattr(usage, "prompt_tokens", 0) or 0
        c = getattr(usage, "completion_tokens", 0) or 0
        t = getattr(usage, "total_tokens", 0) or 0
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
        with _usage_lock:
            _reset_if_new_day(_google_day)
            _google_day.tokens += t
            _google_day.calls += 1
            day_total = _google_day.tokens
        _print_usage("google", chosen_model, p, c, t, day_total, GOOGLE_DAILY_TOKEN_LIMIT)
    return response


async def _call_cerebras(messages: list[dict[str, Any]], model: str | None = None):
    chosen_model = model or CEREBRAS_MODEL
    response = await _cerebras_client.chat.completions.create(
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
        with _usage_lock:
            _reset_if_new_day(_cerebras_day)
            _cerebras_day.tokens += t
            _cerebras_day.calls += 1
            day_total = _cerebras_day.tokens
        _print_usage("cerebras", chosen_model, p, c, t, day_total, CEREBRAS_DAILY_TOKEN_LIMIT)
    return response
