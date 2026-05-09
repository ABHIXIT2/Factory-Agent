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
if GOOGLE_AI_STUDIO_KEY and GOOGLE_AI_STUDIO_KEY.lower() in ("none", "test", "fake", "placeholder"):
    raise ValueError("GOOGLE_AI_STUDIO_KEY is set to a placeholder. Set a real key or leave empty.")
GOOGLE_MODEL: str = os.getenv("GOOGLE_MODEL", "gemini-2.0-flash")
GOOGLE_PRIMARY_ENABLED: bool = bool(GOOGLE_AI_STUDIO_KEY)

# Daily token budgets — used only for the terminal usage bars; not enforced.
GROQ_DAILY_TOKEN_LIMIT: int = int(os.getenv("GROQ_DAILY_TOKEN_LIMIT", "100000"))
GOOGLE_DAILY_TOKEN_LIMIT: int = int(os.getenv("GOOGLE_DAILY_TOKEN_LIMIT", "1000000"))

# Agent loop limits
MAX_ITERATIONS: int = int(os.getenv("MAX_ITERATIONS", "5"))
CONTEXT_WINDOW: int = int(os.getenv("CONTEXT_WINDOW", "10"))
HISTORY_COMPACT_THRESHOLD: int = int(os.getenv("HISTORY_COMPACT_THRESHOLD", "6"))

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
MAX_CUSTOMERS_RETURNED: int = int(os.getenv("MAX_CUSTOMERS_RETURNED", "50"))
MAX_PRODUCTION_RETURNED: int = int(os.getenv("MAX_PRODUCTION_RETURNED", "20"))
MAX_CASH_FLOW_RETURNED: int = int(os.getenv("MAX_CASH_FLOW_RETURNED", "30"))
MAX_LEDGER_RETURNED: int = int(os.getenv("MAX_LEDGER_RETURNED", "30"))

# ============================================================================
# SYSTEM PROMPT
# ============================================================================


def _today_iso() -> str:
    return datetime.now(TIMEZONE).strftime("%Y-%m-%d")


def _load_system_prompt() -> str:
    """Load system prompt template from file."""
    import pathlib
    prompt_file = pathlib.Path(__file__).parent.parent / "prompts" / "system_prompt.md"
    if not prompt_file.exists():
        logger.warning("system_prompt.md not found at %s, using fallback", prompt_file)
        return ""
    return prompt_file.read_text(encoding="utf-8").strip()


_SYSTEM_PROMPT_TEMPLATE = _load_system_prompt()


def get_system_prompt() -> str:
    """Generate system prompt with today's date (called per LLM turn, not at module load)."""
    today_iso = _today_iso()
    return _SYSTEM_PROMPT_TEMPLATE.format(
        today_iso=today_iso,
        timezone_name=TIMEZONE_NAME,
    )
def _load_tool_descriptions() -> dict[str, str]:
    """Load all tool descriptions from prompts/tool_descriptions.md once."""
    import pathlib
    desc_file = pathlib.Path(__file__).parent.parent / "prompts" / "tool_descriptions.md"
    result = {}
    if not desc_file.exists():
        logger.warning("Tool descriptions file not found at %s", desc_file)
        return result
    content = desc_file.read_text(encoding="utf-8")
    current_tool = None
    for line in content.split("\n"):
        line = line.strip()
        if line.startswith("## "):
            current_tool = line[3:].strip()
            result[current_tool] = None
        elif line and not line.startswith("#") and current_tool and result.get(current_tool) is None:
            result[current_tool] = line
    return result

_TOOL_DESCRIPTIONS = _load_tool_descriptions()

def _get_tool_description(tool_name: str) -> str:
    """Get a tool description by name."""
    return _TOOL_DESCRIPTIONS.get(tool_name, f"{tool_name}.")


TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "search_customer",
            "description": _get_tool_description("search_customer"),
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
            "description": _get_tool_description("create_customer"),
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
            "description": _get_tool_description("save_sale"),
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
            "description": _get_tool_description("record_payment"),
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
            "name": "query_credit_ledger",
            "description": _get_tool_description("query_credit_ledger"),
            "parameters": {
                "type": "object",
                "properties": {
                    "customer_id": {"type": "integer", "minimum": 1},
                    "transaction_type": {"type": "string", "enum": ["sale_credited", "payment_received"]},
                    "date_from": {"type": "string", "description": "YYYY-MM-DD"},
                    "date_to": {"type": "string", "description": "YYYY-MM-DD"},
                    "limit": {"type": "integer", "minimum": 1, "maximum": MAX_LEDGER_RETURNED},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_customer",
            "description": _get_tool_description("get_customer"),
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
            "name": "query_customers",
            "description": _get_tool_description("query_customers"),
            "parameters": {
                "type": "object",
                "properties": {
                    "name_fragment": {"type": "string", "maxLength": 100},
                    "min_balance": {"type": "number", "minimum": 0},
                    "max_balance": {"type": "number", "minimum": 0},
                    "sort_by": {"type": "string", "enum": ["shop_name", "outstanding_desc", "outstanding_asc"]},
                    "limit": {"type": "integer", "minimum": 1, "maximum": MAX_CUSTOMERS_RETURNED},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_customer_balance",
            "description": _get_tool_description("get_customer_balance"),
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
            "description": _get_tool_description("get_all_balances"),
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
            "description": _get_tool_description("save_production"),
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
            "description": _get_tool_description("save_cash_flow"),
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
            "description": _get_tool_description("query_sales"),
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
            "name": "query_production",
            "description": _get_tool_description("query_production"),
            "parameters": {
                "type": "object",
                "properties": {
                    "date_from": {"type": "string", "description": "YYYY-MM-DD"},
                    "date_to": {"type": "string", "description": "YYYY-MM-DD"},
                    "limit": {"type": "integer", "minimum": 1, "maximum": MAX_PRODUCTION_RETURNED},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "query_cash_flow",
            "description": _get_tool_description("query_cash_flow"),
            "parameters": {
                "type": "object",
                "properties": {
                    "date_from": {"type": "string", "description": "YYYY-MM-DD"},
                    "date_to": {"type": "string", "description": "YYYY-MM-DD"},
                    "flow_type": {"type": "string", "enum": ["in", "out"]},
                    "category": {"type": "string", "maxLength": 100},
                    "limit": {"type": "integer", "minimum": 1, "maximum": MAX_CASH_FLOW_RETURNED},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_cash_position",
            "description": _get_tool_description("get_cash_position"),
            "parameters": {
                "type": "object",
                "properties": {
                    "date_from": {"type": "string", "description": "YYYY-MM-DD"},
                    "date_to": {"type": "string", "description": "YYYY-MM-DD"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "delete_record",
            "description": _get_tool_description("delete_record"),
            "parameters": {
                "type": "object",
                "properties": {
                    "table": {"type": "string", "enum": ["sales", "credit_ledger", "production_log", "cash_flow"]},
                    "record_id": {"type": "integer", "minimum": 1},
                    "reason": {"type": "string", "maxLength": 500},
                },
                "required": ["table", "record_id"],
            },
        },
    },
]
