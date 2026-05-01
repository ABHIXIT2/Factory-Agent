"""
Configuration: system prompt, tool schemas, constants.
"""

import os
from datetime import datetime
import pytz

# ============================================================================
# ENVIRONMENT
# ============================================================================

GROQ_API_KEY = os.getenv("GROQ_API_KEY")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

# Validate critical env vars
if not all([GROQ_API_KEY, TELEGRAM_BOT_TOKEN, SUPABASE_URL, SUPABASE_KEY]):
    raise ValueError("❌ Missing required environment variables. Check .env file.")

# ============================================================================
# CONSTANTS
# ============================================================================

TIMEZONE = pytz.timezone("Asia/Kolkata")
TODAY_ISO = datetime.now(TIMEZONE).strftime("%Y-%m-%d")

# Agent loop limits
MAX_ITERATIONS = 10
CONTEXT_WINDOW = 5  # Keep last 5 messages in conversation history

# ============================================================================
# SYSTEM PROMPT
# ============================================================================

SYSTEM_PROMPT = f"""You are a helpful factory operations assistant for a Namkeen factory.

Your job is to help manage:
1. Sales log (shop-wise daily sales)
2. Customer credit ledger (tracking who owes what)
3. Production log (daily production tracking)
4. Cash flow log (all cash movements)

Today's date: {TODAY_ISO}
Timezone: Asia/Kolkata

IMPORTANT RULES:
- Detect the user's language (Hindi, Hinglish, or English) and respond in the same language.
- ALWAYS ask the user to confirm before saving ANY data.
- Never assume customer names — always search and confirm first.
- If required fields are missing, ask follow-up questions (one at a time).
- Every transaction must include: original user message, timestamp, who confirmed it.

CONFIRMATION FORMAT (always show before saving):
When showing a confirmation, format it as:
📋 Confirm karo:
[field]: [value]
[field]: [value]
...

[✅ Haan, sahi hai] [❌ Galat hai]

Wait for the user's response before saving.

TOOLS (use these to interact with the database):
- search_customer(name_fragment) - Find a customer by partial name
- create_customer(shop_name, owner_name, owner_phone, address, credit_limit) - Create new customer
- save_sale(customer_id, qty_kg, rate_per_kg, sale_date, payment_status, payment_mode, notes, original_message, user_id) - Record a sale
- record_payment(customer_id, amount, payment_date, payment_mode, notes, original_message, user_id) - Record customer payment
- get_customer_balance(customer_id) - Check one customer's balance
- get_all_balances(sort_by) - Get all outstanding balances
- save_production(prod_date, total_produced_kg, total_packets, notes, original_message, user_id) - Record production
- save_cash_flow(flow_date, flow_type, category, description, amount, party, payment_mode, notes, original_message, user_id) - Record cash in/out
- query_sales(customer_id, date_from, date_to) - Find sales
- get_cash_position() - Show total cash in/out

RESPONSE RULES:
- Keep responses short and natural (1-3 sentences).
- Use emojis: ✅ for success, ❌ for errors, 📋 for confirmations, ₹ for amounts.
- For Hindi: use Hinglish (Roman script) unless user wrote in Devanagari; match their style.
- Never make assumptions—ask if unclear.
- After saving, confirm what was saved: "✅ Sale saved: [details]. [Customer] ka baqaya: ₹[amount]."
"""

# ============================================================================
# TOOL SCHEMAS (for Groq API)
# ============================================================================

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "search_customer",
            "description": "Search for a customer by shop name. Always call this first when customer is mentioned.",
            "parameters": {
                "type": "object",
                "properties": {
                    "name_fragment": {
                        "type": "string",
                        "description": "Part of shop name (e.g., 'Sharma', 'Gupta')"
                    }
                },
                "required": ["name_fragment"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "create_customer",
            "description": "Create a new customer",
            "parameters": {
                "type": "object",
                "properties": {
                    "shop_name": {"type": "string"},
                    "owner_name": {"type": "string"},
                    "owner_phone": {"type": "string"},
                    "address": {"type": "string"},
                    "credit_limit": {"type": "number"}
                },
                "required": ["shop_name"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "save_sale",
            "description": "Record a sale transaction",
            "parameters": {
                "type": "object",
                "properties": {
                    "customer_id": {"type": "integer"},
                    "qty_kg": {"type": "number"},
                    "rate_per_kg": {"type": "number"},
                    "sale_date": {"type": "string", "description": "YYYY-MM-DD"},
                    "payment_status": {"type": "string", "enum": ["paid", "credited"]},
                    "payment_mode": {"type": "string", "enum": ["cash", "online", None]},
                    "notes": {"type": "string"},
                    "original_message": {"type": "string"},
                    "user_id": {"type": "integer"}
                },
                "required": ["customer_id", "qty_kg", "rate_per_kg", "sale_date", "payment_status", "original_message", "user_id"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "record_payment",
            "description": "Record a customer payment",
            "parameters": {
                "type": "object",
                "properties": {
                    "customer_id": {"type": "integer"},
                    "amount": {"type": "number"},
                    "payment_date": {"type": "string", "description": "YYYY-MM-DD"},
                    "payment_mode": {"type": "string", "enum": ["cash", "online"]},
                    "notes": {"type": "string"},
                    "original_message": {"type": "string"},
                    "user_id": {"type": "integer"}
                },
                "required": ["customer_id", "amount", "payment_date", "original_message", "user_id"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_customer_balance",
            "description": "Get a customer's outstanding balance",
            "parameters": {
                "type": "object",
                "properties": {
                    "customer_id": {"type": "integer"}
                },
                "required": ["customer_id"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_all_balances",
            "description": "Get all customers' outstanding balances",
            "parameters": {
                "type": "object",
                "properties": {
                    "sort_by": {"type": "string", "enum": ["outstanding_desc", "outstanding_asc"], "description": "Sort order"}
                }
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "save_production",
            "description": "Record production log entry",
            "parameters": {
                "type": "object",
                "properties": {
                    "prod_date": {"type": "string", "description": "YYYY-MM-DD"},
                    "total_produced_kg": {"type": "number"},
                    "total_packets": {"type": "integer"},
                    "notes": {"type": "string"},
                    "original_message": {"type": "string"},
                    "user_id": {"type": "integer"}
                },
                "required": ["prod_date", "total_produced_kg", "total_packets", "original_message", "user_id"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "save_cash_flow",
            "description": "Record cash flow (income or expense)",
            "parameters": {
                "type": "object",
                "properties": {
                    "flow_date": {"type": "string", "description": "YYYY-MM-DD"},
                    "flow_type": {"type": "string", "enum": ["in", "out"]},
                    "category": {"type": "string"},
                    "description": {"type": "string"},
                    "amount": {"type": "number"},
                    "party": {"type": "string"},
                    "payment_mode": {"type": "string", "enum": ["cash", "online"]},
                    "notes": {"type": "string"},
                    "original_message": {"type": "string"},
                    "user_id": {"type": "integer"}
                },
                "required": ["flow_date", "flow_type", "category", "description", "amount", "original_message", "user_id"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "query_sales",
            "description": "Query sales records",
            "parameters": {
                "type": "object",
                "properties": {
                    "customer_id": {"type": "integer"},
                    "date_from": {"type": "string", "description": "YYYY-MM-DD"},
                    "date_to": {"type": "string", "description": "YYYY-MM-DD"}
                }
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_cash_position",
            "description": "Get total cash in/out and net position",
            "parameters": {
                "type": "object",
                "properties": {}
            }
        }
    }
]
