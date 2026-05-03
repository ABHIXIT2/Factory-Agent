"""
Configuration: env vars, system prompt, tool schemas, runtime constants.

IMPORTANT: Never log GROQ_API_KEY, TELEGRAM_BOT_TOKEN, SUPABASE_URL, or
SUPABASE_KEY to stdout/files. See SECRETS.md for security practices.
"""

import os
import logging
from datetime import datetime
import pytz
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

# ============================================================================
# ENVIRONMENT
# ============================================================================

_REQUIRED_ENV = ("GROQ_API_KEY", "TELEGRAM_BOT_TOKEN", "SUPABASE_URL", "SUPABASE_KEY")
_missing = [k for k in _REQUIRED_ENV if not os.getenv(k)]
if _missing:
    raise ValueError(
        f"Missing required environment variables: {', '.join(_missing)}. "
        f"Check your .env file."
    )

GROQ_API_KEY: str = os.getenv("GROQ_API_KEY") or ""
TELEGRAM_BOT_TOKEN: str = os.getenv("TELEGRAM_BOT_TOKEN") or ""
SUPABASE_URL: str = os.getenv("SUPABASE_URL") or ""
SUPABASE_KEY: str = os.getenv("SUPABASE_KEY") or ""

# Validate that secrets don't look like placeholders (dev/test detection)
if TELEGRAM_BOT_TOKEN.lower() in ("none", "test", "fake", "placeholder", ""):
    raise ValueError("TELEGRAM_BOT_TOKEN is empty or a placeholder. Set a real token.")
if GROQ_API_KEY.lower() in ("none", "test", "fake", "placeholder", ""):
    raise ValueError("GROQ_API_KEY is empty or a placeholder. Set a real key.")

# ============================================================================
# RUNTIME CONFIG (env-overridable)
# ============================================================================

TIMEZONE_NAME: str = os.getenv("TIMEZONE", "Asia/Kolkata")
TIMEZONE = pytz.timezone(TIMEZONE_NAME)

GROQ_MODEL: str = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")
GROQ_MODEL_FAST: str = os.getenv("GROQ_MODEL_FAST", "llama-3.1-8b-instant")
GROQ_MAX_TOKENS: int = int(os.getenv("GROQ_MAX_TOKENS", "512"))
GROQ_TEMPERATURE: float = float(os.getenv("GROQ_TEMPERATURE", "0.3"))

# Google Gemini (primary provider via OpenAI-compatible endpoint)
# Set GOOGLE_AI_STUDIO_KEY in .env to enable. When enabled, Google is primary, Groq is fallback.
GOOGLE_AI_STUDIO_KEY: str = os.getenv("GOOGLE_AI_STUDIO_KEY", "")
GOOGLE_MODEL: str = os.getenv("GOOGLE_MODEL", "gemini-2.0-flash")
GOOGLE_FALLBACK_ENABLED: bool = bool(GOOGLE_AI_STUDIO_KEY)

# Daily token budgets — used only for the terminal usage bars; not enforced.
GROQ_DAILY_TOKEN_LIMIT: int = int(os.getenv("GROQ_DAILY_TOKEN_LIMIT", "100000"))
GOOGLE_DAILY_TOKEN_LIMIT: int = int(os.getenv("GOOGLE_DAILY_TOKEN_LIMIT", "1000000"))

# Agent loop limits
MAX_ITERATIONS: int = int(os.getenv("MAX_ITERATIONS", "5"))
CONTEXT_WINDOW: int = int(os.getenv("CONTEXT_WINDOW", "10"))

# Per-message cap for tool results stored in persisted history. The full
# result is always shown to the LLM in-flight; we only compact the copy
# that gets written back to the session cache.
TOOL_RESULT_HISTORY_MAX_CHARS: int = int(os.getenv("TOOL_RESULT_HISTORY_MAX_CHARS", "1500"))

# Session cache (TTL in seconds, max entries)
SESSION_TTL_SECONDS: int = int(os.getenv("SESSION_TTL_SECONDS", "3600"))
SESSION_MAX_USERS: int = int(os.getenv("SESSION_MAX_USERS", "10000"))

# Rate limit per user (messages per window)
RATE_LIMIT_MESSAGES: int = int(os.getenv("RATE_LIMIT_MESSAGES", "20"))
RATE_LIMIT_WINDOW_SECONDS: int = int(os.getenv("RATE_LIMIT_WINDOW_SECONDS", "60"))

# Result-set limits
MAX_BALANCES_RETURNED: int = int(os.getenv("MAX_BALANCES_RETURNED", "20"))
MAX_SALES_RETURNED: int = int(os.getenv("MAX_SALES_RETURNED", "20"))

# ============================================================================
# SYSTEM PROMPT
# ============================================================================


def _today_iso() -> str:
    return datetime.now(TIMEZONE).strftime("%Y-%m-%d")


TODAY_ISO = _today_iso()


SYSTEM_PROMPT = f"""You are a helpful factory operations assistant for a Namkeen factory.

Your job is to help manage:
1. Sales log (shop-wise daily sales)
2. Customer credit ledger (tracking who owes what)
3. Production log (daily production tracking)
4. Cash flow log (all cash movements)

Today's date: {TODAY_ISO}
Timezone: {TIMEZONE_NAME}

IMPORTANT RULES:
- Detect the user's language (Hindi, Hinglish, or English) and respond in the same language.
- Never assume customer names — always search and confirm first.
- If required fields are missing, ask follow-up questions (All at a time).
- Every transaction must include: original user message, timestamp, who confirmed it.
- Do NOT render your own confirmation message or fake buttons. The system intercepts every write tool call and shows the user real ✅/❌ buttons automatically. Just emit the tool call — the harness handles confirmation.

RESPONSE RULES:
- Keep responses short and natural (1-3 sentences).
- Use emojis: ✅ for success, ❌ for errors, 📋 for confirmations, ₹ for amounts.
- For Hindi: use Hinglish (Roman script) unless user wrote in Devanagari; match their style.
- Never make assumptions—ask if unclear.
- After saving, confirm what was saved: "✅ Sale saved: [details]. [Customer] ka baqaya: ₹[amount]."
"""
# ============================================================================
# TOOL SCHEMAS (for Groq API)
#
# Note: numeric ranges enforced server-side in tools.py validators; we also
# describe them here so the LLM emits sensible values.
# ============================================================================

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "search_customer",
            "description": "Search for a customer by partial shop name. Always call before referencing a customer.",
            "parameters": {
                "type": "object",
                "properties": {
                    "name_fragment": {
                        "type": "string",
                        "description": "Part of shop name (e.g., 'Sharma', 'Gupta')",
                        "minLength": 1,
                        "maxLength": 100,
                    }
                },
                "required": ["name_fragment"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_customer",
            "description": "Create a new customer.",
            "parameters": {
                "type": "object",
                "properties": {
                    "shop_name": {"type": "string", "minLength": 1, "maxLength": 200},
                    "owner_name": {"type": "string", "maxLength": 200},
                    "owner_phone": {"type": "string", "maxLength": 20},
                    "address": {"type": "string", "maxLength": 500},
                    "credit_limit": {"type": "number", "minimum": 0},
                },
                "required": ["shop_name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "save_sale",
            "description": "Record a sale transaction.",
            "parameters": {
                "type": "object",
                "properties": {
                    "customer_id": {"type": "integer", "minimum": 1},
                    "qty_kg": {"type": "number", "exclusiveMinimum": 0},
                    "rate_per_kg": {"type": "number", "exclusiveMinimum": 0},
                    "sale_date": {"type": "string", "description": "YYYY-MM-DD"},
                    "payment_status": {"type": "string", "enum": ["paid", "credited"]},
                    "payment_mode": {"type": "string", "enum": ["cash", "online"]},
                    "notes": {"type": "string", "maxLength": 1000},
                    "original_message": {"type": "string", "maxLength": 4000},
                },
                "required": [
                    "customer_id", "qty_kg", "rate_per_kg", "sale_date",
                    "payment_status", "original_message",
                ],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "record_payment",
            "description": "Record a customer payment.",
            "parameters": {
                "type": "object",
                "properties": {
                    "customer_id": {"type": "integer", "minimum": 1},
                    "amount": {"type": "number", "exclusiveMinimum": 0},
                    "payment_date": {"type": "string", "description": "YYYY-MM-DD"},
                    "payment_mode": {"type": "string", "enum": ["cash", "online"]},
                    "notes": {"type": "string", "maxLength": 1000},
                    "original_message": {"type": "string", "maxLength": 4000},
                },
                "required": ["customer_id", "amount", "payment_date", "original_message"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_customer_balance",
            "description": "Get a customer's outstanding balance.",
            "parameters": {
                "type": "object",
                "properties": {"customer_id": {"type": "integer", "minimum": 1}},
                "required": ["customer_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_all_balances",
            "description": "Get top-N customer outstanding balances.",
            "parameters": {
                "type": "object",
                "properties": {
                    "sort_by": {
                        "type": "string",
                        "enum": ["outstanding_desc", "outstanding_asc"],
                    },
                    "limit": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": MAX_BALANCES_RETURNED,
                    },
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "save_production",
            "description": "Record production log entry.",
            "parameters": {
                "type": "object",
                "properties": {
                    "prod_date": {"type": "string", "description": "YYYY-MM-DD"},
                    "total_produced_kg": {"type": "number", "exclusiveMinimum": 0},
                    "total_packets": {"type": "integer", "minimum": 0},
                    "notes": {"type": "string", "maxLength": 1000},
                    "original_message": {"type": "string", "maxLength": 4000},
                },
                "required": [
                    "prod_date", "total_produced_kg", "total_packets", "original_message",
                ],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "save_cash_flow",
            "description": "Record cash flow (income or expense).",
            "parameters": {
                "type": "object",
                "properties": {
                    "flow_date": {"type": "string", "description": "YYYY-MM-DD"},
                    "flow_type": {"type": "string", "enum": ["in", "out"]},
                    "category": {"type": "string", "minLength": 1, "maxLength": 100},
                    "description": {"type": "string", "minLength": 1, "maxLength": 500},
                    "amount": {"type": "number", "exclusiveMinimum": 0},
                    "party": {"type": "string", "maxLength": 200},
                    "payment_mode": {"type": "string", "enum": ["cash", "online"]},
                    "notes": {"type": "string", "maxLength": 1000},
                    "original_message": {"type": "string", "maxLength": 4000},
                },
                "required": [
                    "flow_date", "flow_type", "category", "description",
                    "amount", "original_message",
                ],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "query_sales",
            "description": "Query sales records.",
            "parameters": {
                "type": "object",
                "properties": {
                    "customer_id": {"type": "integer", "minimum": 1},
                    "date_from": {"type": "string", "description": "YYYY-MM-DD"},
                    "date_to": {"type": "string", "description": "YYYY-MM-DD"},
                    "limit": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": MAX_SALES_RETURNED,
                    },
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_cash_position",
            "description": "Get total cash in/out and net position.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
]
