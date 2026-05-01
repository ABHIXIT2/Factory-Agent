"""
Utilities: date parsing, currency formatting, table rendering for Telegram.
"""

from datetime import datetime, timedelta
import re
import pytz

TIMEZONE = pytz.timezone("Asia/Kolkata")

# ============================================================================
# DATE PARSING
# ============================================================================

def parse_date_flexible(text: str) -> str:
    """
    Parse dates from natural language.
    Returns ISO format: YYYY-MM-DD
    Supports: "आज" (today), "कल" (ambiguous, needs clarification), etc.
    """
    text = text.lower().strip()
    today = datetime.now(TIMEZONE).date()

    # Hindi keywords
    if text in ["aaj", "आज"]:
        return today.isoformat()
    if text in ["kal", "कल"]:
        # Ambiguous! Return with flag for clarification
        return "AMBIGUOUS_KAL"
    if text in ["parso", "परसों"]:
        return (today + timedelta(days=2)).isoformat()
    if text in ["kal ho gaya", "कल हो गया"]:
        return (today - timedelta(days=1)).isoformat()

    # Try ISO format (YYYY-MM-DD)
    if re.match(r"\d{4}-\d{2}-\d{2}", text):
        return text

    # Try DD/MM/YYYY or DD-MM-YYYY
    match = re.match(r"(\d{1,2})[-/](\d{1,2})[-/](\d{4})", text)
    if match:
        day, month, year = match.groups()
        try:
            dt = datetime(int(year), int(month), int(day))
            return dt.date().isoformat()
        except ValueError:
            pass

    # Try relative: "today", "yesterday", "tomorrow"
    if text in ["today", "aaj"]:
        return today.isoformat()
    if text in ["yesterday", "kal", "कल"]:
        return (today - timedelta(days=1)).isoformat()
    if text in ["tomorrow"]:
        return (today + timedelta(days=1)).isoformat()

    # Default to today if unclear
    return today.isoformat()


# ============================================================================
# CURRENCY FORMATTING
# ============================================================================

def format_amount(amount: float) -> str:
    """Format amount as ₹X,XXX.XX"""
    if amount is None:
        return "₹0.00"
    return f"₹{amount:,.2f}"


# ============================================================================
# TABLE RENDERING FOR TELEGRAM
# ============================================================================

def render_balance_table(balances: list) -> str:
    """
    Render list of customer balances as Telegram-friendly text.
    Input: [{"shop_name": "...", "outstanding_balance": X}, ...]
    """
    if not balances:
        return "No customers found."

    lines = ["🔴 *Outstanding Balances*\n"]
    total = 0

    for i, row in enumerate(balances[:10], 1):  # Show top 10
        shop = row.get("shop_name", "Unknown")
        amount = row.get("outstanding_balance", 0)
        total += amount
        lines.append(f"{i}. {shop} — {format_amount(amount)}")

    lines.append(f"\n*Total Outstanding: {format_amount(total)}*")

    return "\n".join(lines)


def render_sales_table(sales: list) -> str:
    """Render sales records as text."""
    if not sales:
        return "No sales found."

    lines = ["📦 *Sales*\n"]

    for row in sales[:5]:  # Show last 5
        date = row.get("sale_date", "?")
        qty = row.get("quantity_kg", 0)
        rate = row.get("rate_per_kg", 0)
        total = qty * rate
        lines.append(f"{date}: {qty}kg @ ₹{rate} = {format_amount(total)}")

    return "\n".join(lines)


# ============================================================================
# PARSING STRUCTURED DATA FROM LLM
# ============================================================================

def extract_amount(text: str) -> float:
    """Extract numeric amount from text."""
    # Find number with optional decimals
    match = re.search(r"[\d,]+\.?\d*", text.replace(",", ""))
    if match:
        try:
            return float(match.group())
        except ValueError:
            pass
    return 0.0


def extract_quantity(text: str) -> float:
    """Extract quantity (kg) from text."""
    # Handle "50 kilo", "50kg", "पचास", etc.
    text = text.lower()

    # Direct number
    match = re.search(r"(\d+\.?\d*)\s*(?:kg|kilo|किलो)", text)
    if match:
        return float(match.group(1))

    # Try Hindi numerals (basic)
    hindi_nums = {
        "एक": 1, "दो": 2, "तीन": 3, "चार": 4, "पाँच": 5,
        "छह": 6, "सात": 7, "आठ": 8, "नौ": 9, "दस": 10,
        "बीस": 20, "तीस": 30, "चालीस": 40, "पचास": 50,
        "साठ": 60, "सत्तर": 70, "अस्सी": 80, "नब्बे": 90
    }

    for hindi, num in hindi_nums.items():
        if hindi in text:
            return float(num)

    return 0.0
