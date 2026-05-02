"""
Database layer: all Supabase/PostgreSQL operations.

Sync supabase-py client wrapped in retries; agent layer offloads calls to a
thread pool via asyncio.to_thread so the event loop stays unblocked.
"""

import logging
import threading
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any
import json

from supabase import create_client, Client
from tenacity import (
    retry, stop_after_attempt, wait_exponential, retry_if_exception_type,
)

from src.config import (
    SUPABASE_URL, SUPABASE_KEY, MAX_BALANCES_RETURNED, MAX_SALES_RETURNED,
)

logger = logging.getLogger(__name__)


class DatabaseError(Exception):
    """Generic, user-safe DB error. Real details are logged, not surfaced."""


# ----------------------------------------------------------------------------
# Client (lazy, thread-safe)
# ----------------------------------------------------------------------------

_db: Optional[Client] = None
_db_lock = threading.Lock()


def get_db() -> Client:
    global _db
    if _db is None:
        with _db_lock:
            if _db is None:
                _db = create_client(SUPABASE_URL, SUPABASE_KEY)
                logger.info("Connected to Supabase")
    return _db


def reset_db() -> None:
    """Drop the cached client (used on connection errors before retry)."""
    global _db
    with _db_lock:
        _db = None


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# Retry transient network/connection errors; let logical errors bubble up.
_retry = retry(
    reraise=True,
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=0.3, min=0.3, max=3.0),
    retry=retry_if_exception_type((ConnectionError, TimeoutError, OSError)),
)


# ----------------------------------------------------------------------------
# USERS
# ----------------------------------------------------------------------------

@_retry
def init_user(user_id: int, username: Optional[str] = None,
              first_name: Optional[str] = None) -> None:
    """Upsert a Telegram user row. Idempotent — safe on every /start."""
    db = get_db()
    db.table("users").upsert({
        "user_id": user_id,
        "username": username,
        "first_name": first_name,
        "created_at": _utcnow_iso(),
    }).execute()
    logger.info("User %s initialized", user_id)


# ----------------------------------------------------------------------------
# CUSTOMERS
# ----------------------------------------------------------------------------

@_retry
def search_customer(name_fragment: str) -> List[Dict[str, Any]]:
    """Fuzzy search by normalized shop name. Caller must pre-sanitize input."""
    db = get_db()
    needle = name_fragment.lower().strip()
    query = (
        db.table("customers")
        .select("id, shop_name, shop_name_normalized")
        .ilike("shop_name_normalized", f"%{needle}%")
        .eq("is_deleted", False)
        .limit(20)
        .execute()
    )
    results = [
        {"id": row["id"], "shop_name": row["shop_name"], "score": 0.9}
        for row in (query.data or [])
    ]
    logger.debug("search_customer(%r) -> %d hits", name_fragment, len(results))
    return results


@_retry
def create_customer(
    shop_name: str,
    owner_name: Optional[str] = None,
    owner_phone: Optional[str] = None,
    address: Optional[str] = None,
    credit_limit: float = 0,
    user_id: Optional[int] = None,
) -> Dict[str, Any]:
    """Insert a new customer row; returns {success, customer_id}."""
    db = get_db()
    result = db.table("customers").insert({
        "shop_name": shop_name,
        "shop_name_normalized": shop_name.lower().strip(),
        "owner_name": owner_name,
        "owner_phone": owner_phone,
        "address": address,
        "credit_limit": credit_limit,
        "created_by": user_id,
    }).execute()
    customer_id = result.data[0]["id"]
    logger.info("Created customer %s: %s", customer_id, shop_name)
    return {"success": True, "customer_id": customer_id}


@_retry
def list_customers(limit: int = 100) -> List[Dict[str, Any]]:
    """Return up to `limit` non-deleted customers (id, shop_name, credit_limit)."""
    db = get_db()
    result = (
        db.table("customers")
        .select("id, shop_name, credit_limit")
        .eq("is_deleted", False)
        .limit(limit)
        .execute()
    )
    return result.data or []


# ----------------------------------------------------------------------------
# SALES
# ----------------------------------------------------------------------------

@_retry
def save_sale(
    customer_id: int,
    qty_kg: float,
    rate_per_kg: float,
    sale_date: str,
    payment_status: str,
    payment_mode: Optional[str] = None,
    notes: Optional[str] = None,
    original_message: str = "",
    user_id: Optional[int] = None,
) -> Dict[str, Any]:
    """Insert sale, optional credit_ledger row, and audit_log entry."""
    db = get_db()
    sale_result = db.table("sales").insert({
        "customer_id": customer_id,
        "quantity_kg": qty_kg,
        "rate_per_kg": rate_per_kg,
        "sale_date": sale_date,
        "payment_status": payment_status,
        "payment_mode": payment_mode,
        "notes": notes,
        "recorded_by": user_id,
        "original_message": original_message,
        "confirmed_at": _utcnow_iso(),
    }).execute()

    sale_id = sale_result.data[0]["id"]
    total_bill = qty_kg * rate_per_kg

    if payment_status == "credited":
        db.table("credit_ledger").insert({
            "customer_id": customer_id,
            "sale_id": sale_id,
            "transaction_date": sale_date,
            "transaction_type": "sale_credited",
            "debit_amount": total_bill,
            "credit_amount": 0,
            "recorded_by": user_id,
            "original_message": f"Auto from sale_id={sale_id}",
        }).execute()

    db.table("audit_log").insert({
        "action_type": "add_sale",
        "table_affected": "sales",
        "record_id": sale_id,
        "user_id": user_id,
        "original_message": original_message,
        "extracted_data": json.dumps({
            "customer_id": customer_id,
            "qty_kg": qty_kg,
            "rate_per_kg": rate_per_kg,
            "total_bill": total_bill,
            "payment_status": payment_status,
        }),
    }).execute()

    logger.info("Saved sale %s: %skg @ ₹%s", sale_id, qty_kg, rate_per_kg)
    return {"success": True, "sale_id": sale_id, "total_bill": total_bill}


@_retry
def query_sales(
    customer_id: Optional[int] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    limit: int = MAX_SALES_RETURNED,
) -> List[Dict[str, Any]]:
    """Return non-deleted sales rows with the customer's shop_name joined in."""
    db = get_db()
    query = db.table("sales").select("*, customers(shop_name)").eq("is_deleted", False)
    if customer_id:
        query = query.eq("customer_id", customer_id)
    if date_from:
        query = query.gte("sale_date", date_from)
    if date_to:
        query = query.lte("sale_date", date_to)
    result = query.order("sale_date", desc=True).limit(limit).execute()
    return result.data or []


# ----------------------------------------------------------------------------
# CREDIT LEDGER & BALANCES
# ----------------------------------------------------------------------------

@_retry
def get_customer_balance(customer_id: int) -> Dict[str, Any]:
    """Read from the customer_balance view (already excludes soft-deleted ledger rows)."""
    db = get_db()
    result = (
        db.table("customer_balance")
        .select("id, shop_name, credit_limit, outstanding_balance")
        .eq("id", customer_id)
        .execute()
    )
    if result.data:
        return result.data[0]
    return {"shop_name": "Unknown", "outstanding_balance": 0, "credit_limit": 0}


@_retry
def get_all_balances(
    sort_by: str = "outstanding_desc",
    limit: int = MAX_BALANCES_RETURNED,
) -> List[Dict[str, Any]]:
    """Top-N customers by outstanding balance. `limit` is hard-capped by config."""
    db = get_db()
    query = db.table("customer_balance").select(
        "id, shop_name, credit_limit, outstanding_balance"
    )
    if sort_by == "outstanding_desc":
        query = query.order("outstanding_balance", desc=True)
    elif sort_by == "outstanding_asc":
        query = query.order("outstanding_balance", desc=False)
    result = query.limit(limit).execute()
    return result.data or []


@_retry
def record_payment(
    customer_id: int,
    amount: float,
    payment_date: str,
    payment_mode: Optional[str] = None,
    notes: Optional[str] = None,
    original_message: str = "",
    user_id: Optional[int] = None,
) -> Dict[str, Any]:
    """Insert a payment row in credit_ledger + audit_log; returns new balance."""
    db = get_db()
    ledger_result = db.table("credit_ledger").insert({
        "customer_id": customer_id,
        "sale_id": None,
        "transaction_date": payment_date,
        "transaction_type": "payment_received",
        "debit_amount": 0,
        "credit_amount": amount,
        "payment_mode": payment_mode,
        "notes": notes,
        "recorded_by": user_id,
        "original_message": original_message,
    }).execute()

    ledger_id = ledger_result.data[0]["id"] if ledger_result.data else None

    db.table("audit_log").insert({
        "action_type": "add_payment",
        "table_affected": "credit_ledger",
        "record_id": ledger_id,
        "user_id": user_id,
        "original_message": original_message,
        "extracted_data": json.dumps({
            "customer_id": customer_id,
            "amount": amount,
            "payment_date": payment_date,
        }),
    }).execute()

    new_balance = get_customer_balance(customer_id)
    logger.info("Payment recorded: ₹%s from customer %s", amount, customer_id)
    return {
        "success": True,
        "ledger_id": ledger_id,
        "new_balance": new_balance.get("outstanding_balance", 0),
    }


# ----------------------------------------------------------------------------
# PRODUCTION LOG
# ----------------------------------------------------------------------------

@_retry
def save_production(
    prod_date: str,
    total_produced_kg: float,
    total_packets: int,
    notes: Optional[str] = None,
    original_message: str = "",
    user_id: Optional[int] = None,
) -> Dict[str, Any]:
    db = get_db()
    result = db.table("production_log").insert({
        "prod_date": prod_date,
        "total_produced_kg": total_produced_kg,
        "total_packets": total_packets,
        "batch_notes": notes,
        "recorded_by": user_id,
        "original_message": original_message,
    }).execute()
    prod_id = result.data[0]["id"]

    db.table("audit_log").insert({
        "action_type": "add_production",
        "table_affected": "production_log",
        "record_id": prod_id,
        "user_id": user_id,
        "original_message": original_message,
        "extracted_data": json.dumps({
            "prod_date": prod_date,
            "total_produced_kg": total_produced_kg,
            "total_packets": total_packets,
        }),
    }).execute()

    logger.info("Production saved: %skg, %s packets", total_produced_kg, total_packets)
    return {"success": True, "id": prod_id}


@_retry
def query_production(
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    limit: int = 100,
) -> List[Dict[str, Any]]:
    db = get_db()
    query = db.table("production_log").select("*").eq("is_deleted", False)
    if date_from:
        query = query.gte("prod_date", date_from)
    if date_to:
        query = query.lte("prod_date", date_to)
    result = query.order("prod_date", desc=True).limit(limit).execute()
    return result.data or []


# ----------------------------------------------------------------------------
# CASH FLOW
# ----------------------------------------------------------------------------

@_retry
def save_cash_flow(
    flow_date: str,
    flow_type: str,
    category: str,
    description: str,
    amount: float,
    party: Optional[str] = None,
    payment_mode: Optional[str] = None,
    notes: Optional[str] = None,
    original_message: str = "",
    user_id: Optional[int] = None,
) -> Dict[str, Any]:
    db = get_db()
    result = db.table("cash_flow").insert({
        "flow_date": flow_date,
        "flow_type": flow_type,
        "category": category,
        "description": description,
        "amount": amount,
        "party": party,
        "payment_mode": payment_mode,
        "notes": notes,
        "recorded_by": user_id,
        "original_message": original_message,
    }).execute()
    cf_id = result.data[0]["id"]

    db.table("audit_log").insert({
        "action_type": "add_cash_flow",
        "table_affected": "cash_flow",
        "record_id": cf_id,
        "user_id": user_id,
        "original_message": original_message,
        "extracted_data": json.dumps({
            "flow_date": flow_date,
            "flow_type": flow_type,
            "amount": amount,
            "category": category,
        }),
    }).execute()

    logger.info("Cash flow saved: %s ₹%s", flow_type, amount)
    return {"success": True, "id": cf_id}


@_retry
def get_cash_position() -> Dict[str, float]:
    db = get_db()
    result = db.table("cash_position").select("total_in, total_out, net_cash").execute()
    if result.data:
        return result.data[0]
    return {"total_in": 0, "total_out": 0, "net_cash": 0}


# ----------------------------------------------------------------------------
# SOFT DELETE (per SECURITY.md/SCHEMA.md is_deleted contract)
# ----------------------------------------------------------------------------

_SOFT_DELETE_TABLES = {"sales", "credit_ledger", "production_log", "cash_flow", "customers"}


@_retry
def soft_delete(table: str, record_id: int, user_id: Optional[int] = None,
                reason: Optional[str] = None) -> Dict[str, Any]:
    if table not in _SOFT_DELETE_TABLES:
        raise ValueError(f"soft_delete not supported for table {table!r}")
    db = get_db()
    db.table(table).update({
        "is_deleted": True,
        "deleted_at": _utcnow_iso(),
        "deleted_by": user_id,
    }).eq("id", record_id).execute()

    db.table("audit_log").insert({
        "action_type": "soft_delete",
        "table_affected": table,
        "record_id": record_id,
        "user_id": user_id,
        "original_message": reason or "",
        "extracted_data": json.dumps({"reason": reason}),
    }).execute()

    logger.info("Soft-deleted %s id=%s by user %s", table, record_id, user_id)
    return {"success": True}


# ----------------------------------------------------------------------------
# HEALTH
# ----------------------------------------------------------------------------

def test_connection() -> bool:
    try:
        db = get_db()
        db.table("users").select("user_id", count="exact").limit(1).execute()
        logger.info("Database connection test passed")
        return True
    except Exception as exc:
        logger.error("Database connection failed: %s", exc, exc_info=True)
        return False
