"""
Tools: bridge between agent and db. Validates LLM-supplied arguments before
hitting the DB and returns compact JSON strings (single-encoded) for the LLM.

Each tool returns a JSON-string envelope: {"ok": bool, ...}.
On validation/DB error, returns {"ok": false, "error": "<safe message>"}.
"""

import logging
import json
from typing import Any
from collections.abc import Awaitable, Callable
import asyncio

try:
    from rapidfuzz import process as _fuzz_process, fuzz as _fuzz
    _RAPIDFUZZ = True
except ImportError:
    _RAPIDFUZZ = False

from src import db
from src.utils import (
    format_amount, sanitize_name_fragment, validate_iso_date,
    validate_positive_number, validate_positive_int, validate_enum, truncate,
    redact_secrets,
)
from src.config import MAX_BALANCES_RETURNED, MAX_SALES_RETURNED

logger = logging.getLogger(__name__)


# ----------------------------------------------------------------------------
# Async dispatch
# ----------------------------------------------------------------------------

async def execute_tool(tool_name: str, tool_input: dict[str, Any]) -> str:
    """
    Execute a tool by name. Runs blocking DB work in a thread pool so it
    doesn't stall the asyncio event loop.

    Returns a JSON string suitable to send back to the LLM as a tool result.
    """
    handler = _TOOLS.get(tool_name)
    if handler is None:
        return _err(f"Unknown tool: {tool_name}")

    try:
        return await handler(tool_input)
    except ValueError as exc:
        # Validation errors are safe to echo back to the LLM (no secrets).
        logger.info("Tool %s rejected input: %s", tool_name, exc)
        return _err(str(exc))
    except Exception as exc:
        # Unknown errors: log redacted details, return generic message.
        exc_text = f"{type(exc).__name__}: {exc}"
        logger.warning("Tool %s failed: %s", tool_name, redact_secrets(exc_text))
        return _err("internal error — please retry")


def _ok(**fields) -> str:
    return json.dumps({"ok": True, **fields}, default=str)


def _err(message: str) -> str:
    return json.dumps({"ok": False, "error": message})


def _to_thread(fn: Callable, *args, **kwargs) -> Awaitable:
    return asyncio.to_thread(fn, *args, **kwargs)


# ----------------------------------------------------------------------------
# Tool handlers
# ----------------------------------------------------------------------------

async def _search_customer(d: Dict) -> str:
    name = sanitize_name_fragment(d.get("name_fragment", ""))
    results = await _to_thread(db.search_customer, name)

    # Fuzzy re-rank results if rapidfuzz is available
    if _RAPIDFUZZ and len(results) > 1:
        scored = _fuzz_process.extract(
            name,
            [r["shop_name"] for r in results],
            scorer=_fuzz.token_set_ratio,
            limit=5,
            score_cutoff=60,
        )
        score_map = {s[0]: s[1] for s in scored}
        results = [r for r in results if r["shop_name"] in score_map]
        results.sort(key=lambda r: score_map.get(r["shop_name"], 0), reverse=True)

    # Check if we have an ambiguous match (multiple close results)
    if len(results) > 1:
        top_score = score_map.get(results[0]["shop_name"], 0) if _RAPIDFUZZ else 100
        next_score = score_map.get(results[1]["shop_name"], 0) if _RAPIDFUZZ else 100
        # If top match is NOT isolated (>=95% and next <90%), flag for UI selection
        is_isolated = top_score >= 95 and next_score < 90
        if not is_isolated:
            # Return flag for bot to show selection UI instead of LLM reasoning
            return _ok(
                selection_required=True,
                customer_options=[
                    {"id": r["id"], "shop_name": r["shop_name"]}
                    for r in results[:5]
                    if r.get("shop_name")  # filter blank names
                ],
            )

    return _ok(results=results, count=len(results))


async def _create_customer(d: Dict) -> str:
    shop_name = (d.get("shop_name") or "").strip()
    if not shop_name:
        raise ValueError("shop_name is required")
    credit_limit = validate_positive_number(
        d.get("credit_limit", 0), "credit_limit", allow_zero=True
    )
    result = await _to_thread(
        db.create_customer,
        shop_name=truncate(shop_name, 200),
        owner_name=truncate(d.get("owner_name"), 200),
        owner_phone=truncate(d.get("owner_phone"), 20),
        address=truncate(d.get("address"), 500),
        credit_limit=credit_limit,
        user_id=d.get("user_id"),
    )
    return _ok(**result)


async def _save_sale(d: Dict) -> str:
    customer_id = validate_positive_int(d.get("customer_id"), "customer_id")
    qty_kg = validate_positive_number(d.get("qty_kg"), "qty_kg")
    rate_per_kg = validate_positive_number(d.get("rate_per_kg"), "rate_per_kg")
    sale_date = validate_iso_date(d.get("sale_date"), "sale_date")
    payment_status = validate_enum(
        d.get("payment_status"), {"paid", "credited"}, "payment_status"
    )
    payment_mode = d.get("payment_mode")
    if payment_mode is not None:
        validate_enum(payment_mode, {"cash", "online"}, "payment_mode")

    result = await _to_thread(
        db.save_sale,
        customer_id=customer_id,
        qty_kg=qty_kg,
        rate_per_kg=rate_per_kg,
        sale_date=sale_date,
        payment_status=payment_status,
        payment_mode=payment_mode,
        notes=truncate(d.get("notes"), 1000),
        original_message=truncate(d.get("original_message", ""), 4000),
        user_id=d.get("user_id"),
    )
    return _ok(**result)


async def _record_payment(d: Dict) -> str:
    customer_id = validate_positive_int(d.get("customer_id"), "customer_id")
    amount = validate_positive_number(d.get("amount"), "amount")
    payment_date = validate_iso_date(d.get("payment_date"), "payment_date")
    payment_mode = d.get("payment_mode")
    if payment_mode is not None:
        validate_enum(payment_mode, {"cash", "online"}, "payment_mode")

    result = await _to_thread(
        db.record_payment,
        customer_id=customer_id,
        amount=amount,
        payment_date=payment_date,
        payment_mode=payment_mode,
        notes=truncate(d.get("notes"), 1000),
        original_message=truncate(d.get("original_message", ""), 4000),
        user_id=d.get("user_id"),
    )
    result["formatted_balance"] = format_amount(result.get("new_balance", 0))
    return _ok(**result)


async def _get_customer_balance(d: Dict) -> str:
    customer_id = validate_positive_int(d.get("customer_id"), "customer_id")
    balance = await _to_thread(db.get_customer_balance, customer_id)
    return _ok(
        balance=balance,
        formatted=format_amount(balance.get("outstanding_balance", 0)),
    )


async def _get_all_balances(d: Dict) -> str:
    sort_by = d.get("sort_by", "outstanding_desc")
    if sort_by not in {"outstanding_desc", "outstanding_asc"}:
        sort_by = "outstanding_desc"
    limit = min(
        validate_positive_int(d.get("limit", MAX_BALANCES_RETURNED), "limit"),
        MAX_BALANCES_RETURNED,
    )
    balances = await _to_thread(db.get_all_balances, sort_by, limit)
    total_outstanding = sum(float(r.get("outstanding_balance") or 0) for r in balances)
    formatted = [
        {
            "shop_name": r.get("shop_name"),
            "outstanding_balance": r.get("outstanding_balance") or 0,
            "credit_limit": r.get("credit_limit"),
        }
        for r in balances
    ]
    return _ok(
        balances=formatted,
        count=len(formatted),
        total_outstanding=total_outstanding,
        formatted_total=format_amount(total_outstanding),
    )


async def _save_production(d: Dict) -> str:
    prod_date = validate_iso_date(d.get("prod_date"), "prod_date")
    total_kg = validate_positive_number(d.get("total_produced_kg"), "total_produced_kg")
    total_packets = validate_positive_int(
        d.get("total_packets"), "total_packets", allow_zero=True
    )
    result = await _to_thread(
        db.save_production,
        prod_date=prod_date,
        total_produced_kg=total_kg,
        total_packets=total_packets,
        notes=truncate(d.get("notes"), 1000),
        original_message=truncate(d.get("original_message", ""), 4000),
        user_id=d.get("user_id"),
    )
    return _ok(**result)


async def _save_cash_flow(d: Dict) -> str:
    flow_date = validate_iso_date(d.get("flow_date"), "flow_date")
    flow_type = validate_enum(d.get("flow_type"), {"in", "out"}, "flow_type")
    category = (d.get("category") or "").strip()
    description = (d.get("description") or "").strip()
    if not category:
        raise ValueError("category is required")
    if not description:
        raise ValueError("description is required")
    amount = validate_positive_number(d.get("amount"), "amount")
    payment_mode = d.get("payment_mode")
    if payment_mode is not None:
        validate_enum(payment_mode, {"cash", "online"}, "payment_mode")

    result = await _to_thread(
        db.save_cash_flow,
        flow_date=flow_date,
        flow_type=flow_type,
        category=truncate(category, 100),
        description=truncate(description, 500),
        amount=amount,
        party=truncate(d.get("party"), 200),
        payment_mode=payment_mode,
        notes=truncate(d.get("notes"), 1000),
        original_message=truncate(d.get("original_message", ""), 4000),
        user_id=d.get("user_id"),
    )
    return _ok(**result)


async def _query_sales(d: Dict) -> str:
    customer_id = d.get("customer_id")
    if customer_id is not None:
        customer_id = validate_positive_int(customer_id, "customer_id")
    date_from = d.get("date_from")
    if date_from:
        date_from = validate_iso_date(date_from, "date_from")
    date_to = d.get("date_to")
    if date_to:
        date_to = validate_iso_date(date_to, "date_to")
    limit = min(
        validate_positive_int(d.get("limit", MAX_SALES_RETURNED), "limit"),
        MAX_SALES_RETURNED,
    )

    sales = await _to_thread(db.query_sales, customer_id, date_from, date_to, limit)
    return _ok(sales=sales, count=len(sales))


async def _get_cash_position(_d: Dict) -> str:
    position = await _to_thread(db.get_cash_position)
    position = dict(position)
    position["formatted_in"] = format_amount(position.get("total_in", 0))
    position["formatted_out"] = format_amount(position.get("total_out", 0))
    position["formatted_net"] = format_amount(position.get("net_cash", 0))
    return _ok(**position)


_TOOLS: dict[str, Callable[[dict[str, Any]], Awaitable[str]]] = {
    "search_customer": _search_customer,
    "create_customer": _create_customer,
    "save_sale": _save_sale,
    "record_payment": _record_payment,
    "get_customer_balance": _get_customer_balance,
    "get_all_balances": _get_all_balances,
    "save_production": _save_production,
    "save_cash_flow": _save_cash_flow,
    "query_sales": _query_sales,
    "get_cash_position": _get_cash_position,
}
