"""
Utilities: date parsing, currency formatting, table rendering, input validation.
"""

from datetime import datetime, date, timedelta
from typing import Any
import re

from src.config import TIMEZONE


# ============================================================================
# DATE PARSING
# ============================================================================

class AmbiguousDateError(ValueError):
    """Raised when a date phrase is ambiguous (e.g. Hindi 'kal' = yesterday OR tomorrow)."""


def today_iso() -> str:
    return datetime.now(TIMEZONE).date().isoformat()


def parse_date_flexible(text: str) -> str | None:
    """
    Parse natural-language dates to ISO YYYY-MM-DD.

    Returns None if unparseable. Raises AmbiguousDateError when the phrase
    is genuinely ambiguous so the caller can ask for clarification.
    """
    if not text:
        return None
    text = text.lower().strip()
    today = datetime.now(TIMEZONE).date()

    if text in ("aaj", "आज", "today"):
        return today.isoformat()
    if text in ("yesterday",):
        return (today - timedelta(days=1)).isoformat()
    if text in ("tomorrow",):
        return (today + timedelta(days=1)).isoformat()
    if text in ("kal", "कल"):
        raise AmbiguousDateError("'kal' is ambiguous — could mean yesterday or tomorrow")
    if text in ("parso", "परसों"):
        raise AmbiguousDateError("'parso' is ambiguous — could mean day-before or day-after")

    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        try:
            return date.fromisoformat(text).isoformat()
        except ValueError:
            return None

    m = re.fullmatch(r"(\d{1,2})[-/](\d{1,2})[-/](\d{4})", text)
    if m:
        d, mo, y = (int(x) for x in m.groups())
        try:
            return date(y, mo, d).isoformat()
        except ValueError:
            return None

    return None


# ============================================================================
# SECRET REDACTION (never expose GROQ_API_KEY, TELEGRAM_BOT_TOKEN in logs)
# ============================================================================

def redact_secrets(text: str) -> str:
    """Redact common secret patterns from error messages before logging."""
    if not isinstance(text, str):
        return text
    # Redact Telegram bot tokens: digits:alphanumeric
    text = re.sub(r"\d{7,}:[A-Za-z0-9_-]{20,}", "[REDACTED_TOKEN]", text)
    # Redact API keys (common pattern: sk-* or gsk-* or similar)
    text = re.sub(r"(sk-[A-Za-z0-9]{20,}|gsk-[A-Za-z0-9]{20,})", "[REDACTED_KEY]", text)
    # Redact Supabase URLs (project-specific)
    text = re.sub(r"(https?://[a-z0-9]+-[a-z0-9]+\.supabase\.co)", "[REDACTED_URL]", text)
    return text


# ============================================================================
# VALIDATION HELPERS (used by tools.py before DB writes)
# ============================================================================

_FORBIDDEN_NAME_RE = re.compile(r"[\x00-\x1f\x7f%]")


def validate_iso_date(value: str, field: str = "date") -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field} must be a string in YYYY-MM-DD format")
    try:
        return date.fromisoformat(value).isoformat()
    except ValueError as exc:
        raise ValueError(f"{field} must be ISO YYYY-MM-DD (got {value!r})") from exc


def validate_positive_number(value: object, field: str, *, allow_zero: bool = False) -> float:
    try:
        n = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be a number (got {value!r})") from exc
    if n != n or n in (float("inf"), float("-inf")):
        raise ValueError(f"{field} must be finite")
    if allow_zero:
        if n < 0:
            raise ValueError(f"{field} must be >= 0 (got {n})")
    else:
        if n <= 0:
            raise ValueError(f"{field} must be > 0 (got {n})")
    return n


def validate_positive_int(value: object, field: str, *, allow_zero: bool = False) -> int:
    if isinstance(value, float) and value != int(value):
        raise ValueError(f"{field} must be an integer (got {value!r})")
    try:
        n = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be an integer (got {value!r})") from exc
    if allow_zero:
        if n < 0:
            raise ValueError(f"{field} must be >= 0 (got {n})")
    else:
        if n <= 0:
            raise ValueError(f"{field} must be > 0 (got {n})")
    return n


def validate_enum(value: str, allowed: set[str], field: str) -> str:
    if value not in allowed:
        raise ValueError(f"{field} must be one of {sorted(allowed)} (got {value!r})")
    return value


def sanitize_name_fragment(value: str) -> str:
    """Trim, length-cap, and reject names with control chars or % wildcards."""
    if not isinstance(value, str):
        raise ValueError("name_fragment must be a string")
    v = value.strip()
    if not v:
        raise ValueError("name_fragment must be non-empty")
    if len(v) > 100:
        raise ValueError("name_fragment too long (max 100 chars)")
    # Strip Postgres LIKE wildcards
    v = v.replace("%", "").replace("_", " ")
    # Reject control chars and any residual %
    if _FORBIDDEN_NAME_RE.search(v):
        raise ValueError("name_fragment contains invalid characters")
    if not v:
        raise ValueError("name_fragment must be non-empty")
    return v


def truncate(value: str | None, max_len: int) -> str | None:
    if value is None:
        return None
    return str(value)[:max_len]


# ============================================================================
# CURRENCY / FORMATTING
# ============================================================================

def format_amount(amount) -> str:
    if amount is None:
        return "₹0.00"
    try:
        return f"₹{float(amount):,.2f}"
    except (TypeError, ValueError):
        return "₹0.00"


# Devanagari script range covers Hindi, Marathi, Sanskrit etc.
_DEVANAGARI_RE = re.compile(r"[ऀ-ॿ]")


def detect_user_lang(text: str | None) -> str:
    """Return 'hi-Deva' if the text contains any Devanagari character,
    else 'hi-Latn' (covers Hinglish + plain English; both render fine in
    Roman-script templates)."""
    if not text:
        return "hi-Latn"
    return "hi-Deva" if _DEVANAGARI_RE.search(text) else "hi-Latn"


