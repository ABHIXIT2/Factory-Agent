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
import pathlib
import threading
import re as _re

try:
    from rapidfuzz import process as _fuzz_process, fuzz as _fuzz
    _RAPIDFUZZ = True
except ImportError:
    _RAPIDFUZZ = False

from postgrest.exceptions import APIError as _APIError

from src import db
from src.utils import (
    format_amount, sanitize_name_fragment, validate_iso_date,
    validate_positive_number, validate_positive_int, validate_enum, truncate,
    redact_secrets,
)
from src.config import (
    MAX_BALANCES_RETURNED, MAX_SALES_RETURNED, MAX_CUSTOMERS_RETURNED,
    MAX_PRODUCTION_RETURNED, MAX_CASH_FLOW_RETURNED, MAX_LEDGER_RETURNED,
)

logger = logging.getLogger(__name__)


# ============================================================================
# Helpers
# ============================================================================

def _normalize_optional_text(value: Any, max_chars: int) -> str | None:
    """Optional text fields: empty/whitespace -> SQL NULL, never ''. Silent."""
    if value is None:
        return None
    s = str(value).strip()
    if not s:
        return None
    return truncate(s, max_chars)


# ============================================================================
# Async dispatch
# ============================================================================

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
    except _APIError as exc:
        # Database constraint errors — map to user-friendly messages
        from src import logger as log_utils
        log_utils.log_db_error(tool_name, tool_input.get("user_id"), exc, tool_input)
        return _map_api_error(exc, tool_name, tool_input)
    except Exception as exc:
        # Unknown errors: log redacted details, return generic message.
        exc_text = f"{type(exc).__name__}: {exc}"
        logger.warning("Tool %s failed: %s", tool_name, redact_secrets(exc_text))
        return _err("internal error — please retry")


def _ok(**fields) -> str:
    return json.dumps({"ok": True, **fields}, default=str)


def _err(message: str, detail: str | None = None) -> str:
    payload: dict[str, Any] = {"ok": False, "error": message}
    if detail:
        payload["detail"] = detail
    return json.dumps(payload)


def _to_thread(fn: Callable, *args, **kwargs) -> Awaitable:
    return asyncio.to_thread(fn, *args, **kwargs)


# ============================================================================
# Error Mapping & Tracking
# ============================================================================

PG_ERROR_MAP = {
    "22003": "numeric_overflow",
    "23503": "customer_not_found",
    "23505": "duplicate_record",
    "23514": "invalid_value",
    "42501": "permission_denied",
}

_UNHANDLED_LOG_PATH = pathlib.Path(__file__).parent.parent / "errors" / "unhandled_errors.md"
_seen_unhandled: set[str] = set()
_unhandled_lock = threading.Lock()


def _load_seen_unhandled() -> None:
    """Parse existing markdown file to populate _seen_unhandled on startup."""
    if not _UNHANDLED_LOG_PATH.exists():
        return
    try:
        content = _UNHANDLED_LOG_PATH.read_text(encoding="utf-8")
        for match in _re.finditer(r"<!-- key:([^>]+) -->", content):
            _seen_unhandled.add(match.group(1))
    except Exception:
        pass  # If file is unreadable, just start fresh


_load_seen_unhandled()


def _map_api_error(exc: _APIError, tool_name: str, args: dict[str, Any]) -> str:
    """Map PostgreSQL APIError to user-friendly error response."""
    code = getattr(exc, "code", None)
    error_type = PG_ERROR_MAP.get(code, "db_error")
    detail: str | None = None

    if code == "22003":
        # Numeric overflow — try to give context based on tool
        try:
            qty = args.get("qty_kg", 0)
            rate = args.get("rate_per_kg", 0)
            if qty and rate:
                attempted = float(qty) * float(rate)
                max_bill = 9_999_999_999.99
                detail = (
                    f"total_bill exceeds max ₹{max_bill:,.2f} "
                    f"(attempted ₹{attempted:,.2f} = {qty} kg × ₹{rate}/kg). "
                    f"Reduce qty or rate."
                )
        except (TypeError, ValueError):
            pass
        if not detail:
            detail = "A numeric value exceeds the system's maximum limit. Try smaller numbers."

    elif code == "23503":
        # FK violation — try to extract the bad FK value
        details_str = getattr(exc, "details", "") or ""
        customer_match = _re.search(r"Key \(customer_id\)=\((\d+)\)", details_str)
        if customer_match:
            bad_id = customer_match.group(1)
            detail = f"customer_id {bad_id} does not exist. Use search_customer to find existing customers."
        else:
            detail = "A referenced record does not exist. Check that the customer/sale/reference exists."

    elif code == "23505":
        # Unique violation
        detail = "This record already exists (duplicate entry detected)."

    elif code == "23514":
        # Check violation
        detail = "An invalid value was provided for a constrained field. Check the allowed options."

    elif code == "42501":
        # Permission denied (RLS)
        detail = "This operation is not allowed for your account (permission denied)."

    elif code and code.startswith("08"):
        # Connection error
        error_type = "db_connection_error"
        detail = "Could not reach the database. Please try again shortly."

    if not detail and code:
        detail = f"{getattr(exc, 'message', str(exc))}"

    # Track unmapped errors
    if code not in PG_ERROR_MAP and code:
        _track_unhandled_error(tool_name, exc)

    return _err(error_type, detail)


def _track_unhandled_error(tool_name: str, exc: _APIError) -> None:
    """Track unhandled error types in markdown file."""
    code = getattr(exc, "code", None) or type(exc).__name__
    key = f"{tool_name}:{code}"

    with _unhandled_lock:
        if key in _seen_unhandled:
            return
        _seen_unhandled.add(key)
        _append_unhandled_entry(tool_name, exc, key)


def _append_unhandled_entry(tool_name: str, exc: _APIError, key: str) -> None:
    """Append a new unhandled error entry to the markdown file."""
    try:
        _UNHANDLED_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        code = getattr(exc, "code", None) or type(exc).__name__
        message = getattr(exc, "message", str(exc))
        timestamp = __import__("datetime").datetime.now().strftime("%Y-%m-%d")

        entry = (
            f"## {timestamp} | {tool_name} | {code}\n"
            f"<!-- key:{key} -->\n"
            f"- **Error type**: `{type(exc).__name__}`\n"
            f"- **PG code**: `{code}`\n"
            f"- **Tool**: `{tool_name}`\n"
            f"- **Message**: {message}\n"
            f"- **Suggested action**: TODO — investigate and add mapping in tools.py PG_ERROR_MAP\n\n"
        )

        with _UNHANDLED_LOG_PATH.open("a", encoding="utf-8") as f:
            f.write(entry)
    except Exception:
        pass  # Silently fail — don't break tool execution if tracking fails


# Helper for optional date parameters
def _opt_date(d: dict[str, Any], key: str) -> str | None:
    """Extract and validate an optional ISO date from dict."""
    val = d.get(key)
    return validate_iso_date(val, key) if val else None


# ----------------------------------------------------------------------------
# Tool handlers
# ----------------------------------------------------------------------------

async def _search_customer(d: dict[str, Any]) -> str:
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

    if not results:
        return _ok(results=[], count=0, not_found=True)
    return _ok(results=results, count=len(results))


async def _create_customer(d: dict[str, Any]) -> str:
    shop_name = (d.get("shop_name") or "").strip()
    if not shop_name:
        raise ValueError("shop_name is required")
    owner_name = (d.get("owner_name") or "").strip()
    if not owner_name:
        raise ValueError("owner_name is required")
    owner_phone = (d.get("owner_phone") or "").strip()
    if not owner_phone:
        raise ValueError("owner_phone is required")
    address = (d.get("address") or "").strip()
    if not address:
        raise ValueError("address is required")
    credit_limit = validate_positive_number(
        d.get("credit_limit", 0), "credit_limit", allow_zero=True
    )
    result = await _to_thread(
        db.create_customer,
        shop_name=truncate(shop_name, 200),
        owner_name=truncate(owner_name, 200),
        owner_phone=truncate(owner_phone, 20),
        address=truncate(address, 500),
        credit_limit=credit_limit,
        user_id=d.get("user_id"),
    )
    return _ok(**result)


async def _save_sale(d: dict[str, Any]) -> str:
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
    original_message = (d.get("original_message") or "").strip()
    if not original_message:
        raise ValueError("original_message is required")

    result = await _to_thread(
        db.save_sale,
        customer_id=customer_id,
        qty_kg=qty_kg,
        rate_per_kg=rate_per_kg,
        sale_date=sale_date,
        payment_status=payment_status,
        payment_mode=payment_mode,
        notes=_normalize_optional_text(d.get("notes"), 1000),
        original_message=truncate(original_message, 4000),
        user_id=d.get("user_id"),
    )
    return _ok(**result)


async def _record_payment(d: dict[str, Any]) -> str:
    customer_id = validate_positive_int(d.get("customer_id"), "customer_id")
    amount = validate_positive_number(d.get("amount"), "amount")
    payment_date = validate_iso_date(d.get("payment_date"), "payment_date")
    payment_mode = d.get("payment_mode")
    if payment_mode is not None:
        validate_enum(payment_mode, {"cash", "online"}, "payment_mode")
    original_message = (d.get("original_message") or "").strip()
    if not original_message:
        raise ValueError("original_message is required")

    result = await _to_thread(
        db.record_payment,
        customer_id=customer_id,
        amount=amount,
        payment_date=payment_date,
        payment_mode=payment_mode,
        notes=_normalize_optional_text(d.get("notes"), 1000),
        original_message=truncate(original_message, 4000),
        user_id=d.get("user_id"),
    )
    result["formatted_balance"] = format_amount(result.get("new_balance", 0))
    return _ok(**result)


async def _get_customer_balance(d: dict[str, Any]) -> str:
    customer_id = validate_positive_int(d.get("customer_id"), "customer_id")
    balance = await _to_thread(db.get_customer_balance, customer_id)
    if balance.get("not_found"):
        return _ok(not_found=True)
    return _ok(
        customer_id=balance.get("id"),
        outstanding_balance=balance.get("outstanding_balance", 0),
        credit_limit=balance.get("credit_limit"),
        formatted=format_amount(balance.get("outstanding_balance", 0)),
    )


async def _get_all_balances(d: dict[str, Any]) -> str:
    sort_by = validate_enum(
        d.get("sort_by", "outstanding_desc"),
        {"outstanding_desc", "outstanding_asc"},
        "sort_by",
    )
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


async def _save_production(d: dict[str, Any]) -> str:
    prod_date = validate_iso_date(d.get("prod_date"), "prod_date")
    total_kg = validate_positive_number(d.get("total_produced_kg"), "total_produced_kg")
    total_packets = validate_positive_int(
        d.get("total_packets"), "total_packets", allow_zero=True
    )
    original_message = (d.get("original_message") or "").strip()
    if not original_message:
        raise ValueError("original_message is required")
    result = await _to_thread(
        db.save_production,
        prod_date=prod_date,
        total_produced_kg=total_kg,
        total_packets=total_packets,
        notes=_normalize_optional_text(d.get("notes"), 1000),
        original_message=truncate(original_message, 4000),
        user_id=d.get("user_id"),
    )
    return _ok(**result)


async def _save_cash_flow(d: dict[str, Any]) -> str:
    flow_date = validate_iso_date(d.get("flow_date"), "flow_date")
    flow_type = validate_enum(d.get("flow_type"), {"in", "out"}, "flow_type")
    category = (d.get("category") or "").strip()
    description = (d.get("description") or "").strip()
    if not category:
        raise ValueError("category is required")
    if not description:
        raise ValueError("description is required")
    amount = validate_positive_number(d.get("amount"), "amount")
    party = (d.get("party") or "").strip()
    if not party:
        raise ValueError("party is required")
    original_message = (d.get("original_message") or "").strip()
    if not original_message:
        raise ValueError("original_message is required")
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
        party=truncate(party, 200),
        payment_mode=payment_mode,
        notes=_normalize_optional_text(d.get("notes"), 1000),
        original_message=truncate(original_message, 4000),
        user_id=d.get("user_id"),
    )
    return _ok(**result)


async def _query_sales(d: dict[str, Any]) -> str:
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


async def _get_customer(d: dict[str, Any]) -> str:
    customer_id = validate_positive_int(d.get("customer_id"), "customer_id")
    row = await _to_thread(db.get_customer, customer_id)
    if row is None:
        return _ok(not_found=True)
    return _ok(customer=row)


async def _query_customers(d: dict[str, Any]) -> str:
    name_fragment = d.get("name_fragment")
    if name_fragment:
        name_fragment = sanitize_name_fragment(name_fragment)
    min_balance = d.get("min_balance")
    if min_balance is not None:
        min_balance = validate_positive_number(min_balance, "min_balance", allow_zero=True)
    max_balance = d.get("max_balance")
    if max_balance is not None:
        max_balance = validate_positive_number(max_balance, "max_balance", allow_zero=True)
    sort_by = validate_enum(
        d.get("sort_by", "shop_name"),
        {"shop_name", "outstanding_desc", "outstanding_asc"},
        "sort_by",
    )
    limit = min(
        validate_positive_int(d.get("limit", MAX_CUSTOMERS_RETURNED), "limit"),
        MAX_CUSTOMERS_RETURNED,
    )
    rows = await _to_thread(
        db.query_customers,
        name_fragment=name_fragment,
        min_balance=min_balance,
        max_balance=max_balance,
        sort_by=sort_by,
        limit=limit,
    )
    return _ok(customers=rows, count=len(rows))


async def _query_production(d: dict[str, Any]) -> str:
    limit = min(
        validate_positive_int(d.get("limit", MAX_PRODUCTION_RETURNED), "limit"),
        MAX_PRODUCTION_RETURNED,
    )
    rows = await _to_thread(
        db.query_production, _opt_date(d, "date_from"), _opt_date(d, "date_to"), limit,
    )
    total_kg = sum(float(r.get("total_produced_kg") or 0) for r in rows)
    return _ok(production=rows, count=len(rows), total_kg=total_kg)


async def _query_cash_flow(d: dict[str, Any]) -> str:
    flow_type = d.get("flow_type")
    if flow_type is not None:
        flow_type = validate_enum(flow_type, {"in", "out"}, "flow_type")
    category = (d.get("category") or "").strip().lower() or None
    limit = min(
        validate_positive_int(d.get("limit", MAX_CASH_FLOW_RETURNED), "limit"),
        MAX_CASH_FLOW_RETURNED,
    )
    rows = await _to_thread(
        db.query_cash_flow,
        _opt_date(d, "date_from"), _opt_date(d, "date_to"),
        flow_type, category, limit,
    )
    total_in = sum(float(r.get("amount") or 0) for r in rows if r.get("flow_type") == "in")
    total_out = sum(float(r.get("amount") or 0) for r in rows if r.get("flow_type") == "out")
    return _ok(cash_flows=rows, count=len(rows), total_in=total_in, total_out=total_out)


async def _query_credit_ledger(d: dict[str, Any]) -> str:
    customer_id = d.get("customer_id")
    if customer_id is not None:
        customer_id = validate_positive_int(customer_id, "customer_id")
    transaction_type = d.get("transaction_type")
    if transaction_type is not None:
        transaction_type = validate_enum(
            transaction_type, {"sale_credited", "payment_received"}, "transaction_type",
        )
    limit = min(
        validate_positive_int(d.get("limit", MAX_LEDGER_RETURNED), "limit"),
        MAX_LEDGER_RETURNED,
    )
    rows = await _to_thread(
        db.query_credit_ledger,
        customer_id, transaction_type,
        _opt_date(d, "date_from"), _opt_date(d, "date_to"), limit,
    )
    total_debit = sum(float(r.get("debit_amount") or 0) for r in rows)
    total_credit = sum(float(r.get("credit_amount") or 0) for r in rows)
    return _ok(ledger=rows, count=len(rows), total_debit=total_debit, total_credit=total_credit)


_DELETE_ALLOWED_TABLES = {"sales", "credit_ledger", "production_log", "cash_flow"}


async def _delete_record(d: dict[str, Any]) -> str:
    table = validate_enum(d.get("table"), _DELETE_ALLOWED_TABLES, "table")
    record_id = validate_positive_int(d.get("record_id"), "record_id")
    reason = _normalize_optional_text(d.get("reason"), 500)
    result = await _to_thread(
        db.soft_delete,
        table=table, record_id=record_id,
        user_id=d.get("user_id"), reason=reason,
    )
    return _ok(**result)


async def _get_cash_position(d: dict[str, Any]) -> str:
    position = await _to_thread(
        db.get_cash_position, _opt_date(d, "date_from"), _opt_date(d, "date_to"),
    )
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
    "get_customer": _get_customer,
    "query_customers": _query_customers,
    "get_customer_balance": _get_customer_balance,
    "get_all_balances": _get_all_balances,
    "save_production": _save_production,
    "query_production": _query_production,
    "save_cash_flow": _save_cash_flow,
    "query_cash_flow": _query_cash_flow,
    "query_credit_ledger": _query_credit_ledger,
    "query_sales": _query_sales,
    "delete_record": _delete_record,
    "get_cash_position": _get_cash_position,
}
