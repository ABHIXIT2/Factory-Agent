"""Output rendering: confirmation summaries and closing messages for write tools.

Pure formatters — no LLM, no session state, no database access.
Used by agent_loop to render confirmation cards and closing messages.
"""

import json
import logging
from typing import Any

from src import pending
from src.utils import format_amount

logger = logging.getLogger(__name__)


# Confirmation summary formatters

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


def _summarize_save_sale(args: dict[str, Any], names: dict[int, str], lang: str = "hi-Hind") -> str:
    qty = args.get("qty_kg", 0) or 0
    rate = args.get("rate_per_kg", 0) or 0
    total = float(qty) * float(rate)

    if lang == "hi-Deva":
        lines = [
            "📦 *बिक्री की पुष्टि कीजिए:*",
            "─────────────────",
            _customer_label(args, names),
            f"मात्रा: *{qty} किग्रा* @ *₹{rate}/किग्रा*",
            f"कुल: *{format_amount(total)}*",
            f"तारीख: `{args.get('sale_date')}`",
            f"भुगतान: *{args.get('payment_status')}*"
            + (f" ({args.get('payment_mode')})" if args.get('payment_mode') else ""),
        ]
    elif lang == "en":
        lines = [
            "📦 *Confirm sale:*",
            "─────────────────",
            _customer_label(args, names),
            f"Quantity: *{qty} kg* @ *₹{rate}/kg*",
            f"Total: *{format_amount(total)}*",
            f"Date: `{args.get('sale_date')}`",
            f"Payment Status: *{args.get('payment_status')}*"
            + (f" ({args.get('payment_mode')})" if args.get('payment_mode') else ""),
        ]
    else:  # hi-Hind (default)
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
        if lang == "hi-Deva":
            lines.append(f"नोट: {args['notes']}")
        else:
            lines.append(f"Notes: {args['notes']}")

    return "\n".join(lines)


def _summarize_record_payment(args: dict[str, Any], names: dict[int, str], lang: str = "hi-Hind") -> str:
    if lang == "hi-Deva":
        lines = [
            "💳 *भुगतान की पुष्टि कीजिए:*",
            "─────────────────",
            _customer_label(args, names),
            f"राशि: *{format_amount(args.get('amount', 0))}*",
            f"तारीख: `{args.get('payment_date')}`",
            f"विधि: *{args.get('payment_mode') or 'नकद'}*",
        ]
    elif lang == "en":
        lines = [
            "💳 *Confirm payment:*",
            "─────────────────",
            _customer_label(args, names),
            f"Amount: *{format_amount(args.get('amount', 0))}*",
            f"Date: `{args.get('payment_date')}`",
            f"Mode: *{args.get('payment_mode') or 'cash'}*",
        ]
    else:  # hi-Hind (default)
        lines = [
            "💳 *Payment confirm kijiye:*",
            "─────────────────",
            _customer_label(args, names),
            f"Amount: *{format_amount(args.get('amount', 0))}*",
            f"Date: `{args.get('payment_date')}`",
            f"Mode: *{args.get('payment_mode') or 'cash'}*",
        ]

    if args.get("notes"):
        if lang == "hi-Deva":
            lines.append(f"नोट: {args['notes']}")
        else:
            lines.append(f"Notes: {args['notes']}")

    return "\n".join(lines)


def _summarize_save_production(args: dict[str, Any], _names: dict[int, str], lang: str = "hi-Hind") -> str:
    if lang == "hi-Deva":
        lines = [
            "🏭 *उत्पादन की पुष्टि कीजिए:*",
            "─────────────────",
            f"तारीख: `{args.get('prod_date')}`",
            f"मात्रा: *{args.get('total_produced_kg')} किग्रा*",
            f"पैकेट: *{args.get('total_packets')}*",
        ]
    elif lang == "en":
        lines = [
            "🏭 *Confirm production:*",
            "─────────────────",
            f"Date: `{args.get('prod_date')}`",
            f"Quantity: *{args.get('total_produced_kg')} kg*",
            f"Packets: *{args.get('total_packets')}*",
        ]
    else:  # hi-Hind (default)
        lines = [
            "🏭 *Production confirm kijiye:*",
            "─────────────────",
            f"Date: `{args.get('prod_date')}`",
            f"Qty: *{args.get('total_produced_kg')} kg*",
            f"Packets: *{args.get('total_packets')}*",
        ]

    if args.get("notes"):
        if lang == "hi-Deva":
            lines.append(f"नोट: {args['notes']}")
        else:
            lines.append(f"Notes: {args['notes']}")

    return "\n".join(lines)


def _summarize_save_cash_flow(args: dict[str, Any], _names: dict[int, str], lang: str = "hi-Hind") -> str:
    flow_type = args.get('flow_type', '?').upper()
    emoji = "💰" if flow_type == "IN" else "💸"

    if lang == "hi-Deva":
        flow_label = "जमा" if flow_type == "IN" else "खर्च"
        lines = [
            f"{emoji} *कैश {flow_label} की पुष्टि कीजिए:*",
            "─────────────────",
            f"तारीख: `{args.get('flow_date')}`",
            f"राशि: *{format_amount(args.get('amount', 0))}*",
            f"श्रेणी: *{args.get('category')}*",
            f"विवरण: {args.get('description')}",
            f"पक्ष: {args.get('party') or '–'}",
        ]
    elif lang == "en":
        lines = [
            f"{emoji} *Confirm cash {flow_type.lower()}:*",
            "─────────────────",
            f"Date: `{args.get('flow_date')}`",
            f"Amount: *{format_amount(args.get('amount', 0))}*",
            f"Category: *{args.get('category')}*",
            f"Description: {args.get('description')}",
            f"Party: {args.get('party') or '–'}",
        ]
    else:  # hi-Hind (default)
        lines = [
            f"{emoji} *Cash {flow_type} confirm kijiye:*",
            "─────────────────",
            f"Date: `{args.get('flow_date')}`",
            f"Amount: *{format_amount(args.get('amount', 0))}*",
            f"Category: *{args.get('category')}*",
            f"Description: {args.get('description')}",
            f"Party: {args.get('party') or '–'}",
        ]

    if args.get("notes"):
        if lang == "hi-Deva":
            lines.append(f"नोट: {args['notes']}")
        else:
            lines.append(f"Notes: {args['notes']}")

    return "\n".join(lines)


def _summarize_create_customer(args: dict[str, Any], _names: dict[int, str], lang: str = "hi-Hind") -> str:
    if lang == "hi-Deva":
        lines = [
            "👤 *नया ग्राहक की पुष्टि कीजिए:*",
            "─────────────────",
            f"दुकान: *{args.get('shop_name')}*",
            f"मालिक: {args.get('owner_name') or '–'}",
            f"फोन: {args.get('owner_phone') or '–'}",
            f"साख सीमा: *{format_amount(args.get('credit_limit', 0))}*",
        ]
    elif lang == "en":
        lines = [
            "👤 *Confirm new customer:*",
            "─────────────────",
            f"Shop: *{args.get('shop_name')}*",
            f"Owner: {args.get('owner_name') or '–'}",
            f"Phone: {args.get('owner_phone') or '–'}",
            f"Credit Limit: *{format_amount(args.get('credit_limit', 0))}*",
        ]
    else:  # hi-Hind (default)
        lines = [
            "👤 *New customer confirm kijiye:*",
            "─────────────────",
            f"Shop: *{args.get('shop_name')}*",
            f"Owner: {args.get('owner_name') or '–'}",
            f"Phone: {args.get('owner_phone') or '–'}",
            f"Credit limit: *{format_amount(args.get('credit_limit', 0))}*",
        ]

    if args.get("address"):
        if lang == "hi-Deva":
            lines.append(f"पता: {args['address']}")
        else:
            lines.append(f"Address: {args['address']}")

    return "\n".join(lines)


_SUMMARIZERS = {
    "save_sale": _summarize_save_sale,
    "record_payment": _summarize_record_payment,
    "save_production": _summarize_save_production,
    "save_cash_flow": _summarize_save_cash_flow,
    "create_customer": _summarize_create_customer,
}


def _load_confirmation_card_text() -> str:
    """Load confirmation card preamble from prompts/ui_strings/confirmation_card.md."""
    import pathlib
    card_file = pathlib.Path(__file__).parent.parent / "prompts" / "ui_strings" / "confirmation_card.md"
    if not card_file.exists():
        return "⬇️ नीचे दिए बटन से confirm kijiye:"
    return card_file.read_text(encoding="utf-8").strip()


_CONFIRMATION_CARD_TEXT = _load_confirmation_card_text()


def _build_summary(
    write_calls: list[pending.PendingToolCall],
    customer_names: dict[int, str] | None = None,
    user_lang: str = "hi-Hind",
) -> str:
    names = customer_names or {}
    parts = []
    for call in write_calls:
        fn = _SUMMARIZERS.get(call.name)
        if fn:
            # Pass language to summarizers that support it
            try:
                parts.append(fn(call.arguments, names, user_lang))
            except TypeError:
                # Fallback for summarizers that don't support lang parameter yet
                parts.append(fn(call.arguments, names))
        else:
            parts.append(f"📋 {call.name}: {call.arguments}")
    parts.append(f"\n{_CONFIRMATION_CARD_TEXT}")
    return "\n\n".join(parts)


# Closing messages after confirmation

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
    elif lang == "en":
        return (f"✅ Sale saved: *{qty} kg* @ *₹{rate}/kg* = *{format_amount(total)}*. "
                f"Customer: *{customer}*.")
    else:  # hi-Hind
        return (f"✅ Sale saved: *{qty} kg* @ *₹{rate}/kg* = *{format_amount(total)}*. "
                f"Customer: *{customer}*.")


def _close_record_payment(args, result, names, lang):
    amount = args.get("amount", 0)
    new_bal = result.get("new_balance", 0)
    customer = _customer_text(args, names)
    if lang == "hi-Deva":
        return (f"💳 भुगतान सेव हो गया: *{format_amount(amount)}* — {customer} से। "
                f"नया बकाया: *{format_amount(new_bal)}*.")
    elif lang == "en":
        return (f"💳 Payment saved: *{format_amount(amount)}* from *{customer}*. "
                f"New balance: *{format_amount(new_bal)}*.")
    else:  # hi-Hind
        return (f"💳 Payment saved: *{format_amount(amount)}* from *{customer}*. "
                f"Naya baqaya: *{format_amount(new_bal)}*.")


def _close_create_customer(args, result, _names, lang):
    shop = args.get("shop_name", "?")
    cid = result.get("customer_id", "?")
    if lang == "hi-Deva":
        return f"👤 नया ग्राहक जुड़ गया: *{shop}* (id: `{cid}`)."
    elif lang == "en":
        return f"👤 New customer added: *{shop}* (id: `{cid}`)."
    else:  # hi-Hind
        return f"👤 New customer added: *{shop}* (id: `{cid}`)."


def _close_save_production(args, _result, _names, lang):
    kg = args.get("total_produced_kg", 0)
    packets = args.get("total_packets", 0)
    date_iso = args.get("prod_date", "?")
    if lang == "hi-Deva":
        return f"🏭 उत्पादन सेव हो गया: *{kg} किलो*, *{packets}* पैकेट (`{date_iso}`)."
    elif lang == "en":
        return f"🏭 Production saved: *{kg} kg*, *{packets}* packets (`{date_iso}`)."
    else:  # hi-Hind
        return f"🏭 Production saved: *{kg} kg*, *{packets}* packets (`{date_iso}`)."


def _close_save_cash_flow(args, _result, _names, lang):
    flow = (args.get("flow_type") or "?").lower()
    amount = args.get("amount", 0)
    category = args.get("category", "?")
    if lang == "hi-Deva":
        verb = "जमा" if flow == "in" else "खर्च"
        return f"💰 कैश {verb}: *{format_amount(amount)}* ({category})."
    elif lang == "en":
        verb = "in" if flow == "in" else "out"
        return f"💰 Cash {verb}: *{format_amount(amount)}* ({category})."
    else:  # hi-Hind
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
        err = parsed.get("detail") or parsed.get("error") or "kuch gadbad"
        return f"❌ Save nahi ho paya: {err}"
    try:
        return fn(call.arguments or {}, parsed, customer_names, user_lang)
    except Exception:
        logger.exception("closing-template render failed for %s", call.name)
        return "✅ Saved."
