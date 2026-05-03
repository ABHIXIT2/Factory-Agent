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
