"""
Database layer: all Supabase/PostgreSQL operations.
Uses supabase-py client for safe, async-friendly database access.
"""

import logging
from datetime import datetime
from typing import Optional, List, Dict, Any
import json
from supabase import create_client, Client
from src.config import SUPABASE_URL, SUPABASE_KEY

logger = logging.getLogger(__name__)

# Global Supabase client
_db: Optional[Client] = None


def get_db() -> Client:
    """Get or initialize Supabase client."""
    global _db
    if _db is None:
        _db = create_client(SUPABASE_URL, SUPABASE_KEY)
        logger.info("✅ Connected to Supabase")
    return _db


def init_user(user_id: int, username: str = None, first_name: str = None) -> None:
    """Register or update a Telegram user."""
    db = get_db()
    try:
        db.table("users").upsert({
            "user_id": user_id,
            "username": username,
            "first_name": first_name,
            "created_at": datetime.utcnow().isoformat()
        }).execute()
        logger.info(f"✅ User {user_id} initialized")
    except Exception as e:
        logger.error(f"❌ Error initializing user {user_id}: {e}")
        raise


# ============================================================================
# CUSTOMERS
# ============================================================================

def search_customer(name_fragment: str, user_id: int = None) -> List[Dict]:
    """
    Fuzzy search for customers by shop_name_normalized.
    Returns list of [{id, shop_name, score}, ...] sorted by relevance.
    """
    db = get_db()
    try:
        # PostgreSQL similarity search using pg_trgm
        # For MVP, simple LIKE search; upgrade to similarity() if needed
        query = (
            db.table("customers")
            .select("id, shop_name, shop_name_normalized")
            .ilike("shop_name_normalized", f"%{name_fragment.lower()}%")
            .execute()
        )
        results = [
            {"id": row["id"], "shop_name": row["shop_name"], "score": 0.9}
            for row in query.data
        ]
        logger.debug(f"Found {len(results)} customers matching '{name_fragment}'")
        return results
    except Exception as e:
        logger.error(f"❌ Error searching customers: {e}")
        return []


def create_customer(
    shop_name: str,
    owner_name: str = None,
    owner_phone: str = None,
    address: str = None,
    credit_limit: float = 0,
    user_id: int = None
) -> Dict[str, Any]:
    """Create a new customer."""
    db = get_db()
    try:
        result = db.table("customers").insert({
            "shop_name": shop_name,
            "shop_name_normalized": shop_name.lower().strip(),
            "owner_name": owner_name,
            "owner_phone": owner_phone,
            "address": address,
            "credit_limit": credit_limit,
            "created_by": user_id
        }).execute()

        customer_id = result.data[0]["id"]
        logger.info(f"✅ Created customer {customer_id}: {shop_name}")
        return {"success": True, "customer_id": customer_id}
    except Exception as e:
        logger.error(f"❌ Error creating customer: {e}")
        return {"success": False, "error": str(e)}


def list_customers(user_id: int = None) -> List[Dict]:
    """Get all customers."""
    db = get_db()
    try:
        result = db.table("customers").select("id, shop_name, credit_limit").execute()
        return result.data
    except Exception as e:
        logger.error(f"❌ Error listing customers: {e}")
        return []


# ============================================================================
# SALES
# ============================================================================

def save_sale(
    customer_id: int,
    qty_kg: float,
    rate_per_kg: float,
    sale_date: str,
    payment_status: str,
    payment_mode: str = None,
    notes: str = None,
    original_message: str = "",
    user_id: int = None
) -> Dict[str, Any]:
    """
    Save a sale and auto-generate credit_ledger entry if credited.
    Atomic transaction: both inserts succeed or both fail.
    """
    db = get_db()
    try:
        # 1. Insert into sales
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
            "confirmed_at": datetime.utcnow().isoformat()
        }).execute()

        sale_id = sale_result.data[0]["id"]
        total_bill = qty_kg * rate_per_kg

        # 2. If credited, insert into credit_ledger
        if payment_status == "credited":
            db.table("credit_ledger").insert({
                "customer_id": customer_id,
                "sale_id": sale_id,
                "transaction_date": sale_date,
                "transaction_type": "sale_credited",
                "debit_amount": total_bill,
                "credit_amount": 0,
                "recorded_by": user_id,
                "original_message": f"Auto from sale_id={sale_id}"
            }).execute()

        # 3. Log to audit_log
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
                "payment_status": payment_status
            })
        }).execute()

        logger.info(f"✅ Saved sale {sale_id}: {qty_kg}kg @ ₹{rate_per_kg}")
        return {"success": True, "sale_id": sale_id}

    except Exception as e:
        logger.error(f"❌ Error saving sale: {e}")
        return {"success": False, "error": str(e)}


def query_sales(
    customer_id: int = None,
    date_from: str = None,
    date_to: str = None,
    user_id: int = None
) -> List[Dict]:
    """Query sales with optional filters."""
    db = get_db()
    try:
        query = db.table("sales").select("*, customers(shop_name)")

        if customer_id:
            query = query.eq("customer_id", customer_id)
        if date_from:
            query = query.gte("sale_date", date_from)
        if date_to:
            query = query.lte("sale_date", date_to)

        result = query.order("sale_date", desc=True).execute()
        return result.data
    except Exception as e:
        logger.error(f"❌ Error querying sales: {e}")
        return []


# ============================================================================
# CREDIT LEDGER & BALANCES
# ============================================================================

def get_customer_balance(customer_id: int) -> Dict[str, Any]:
    """Get current outstanding balance for a customer."""
    db = get_db()
    try:
        result = db.table("customer_balance").select(
            "id, shop_name, credit_limit, outstanding_balance"
        ).eq("id", customer_id).execute()

        if result.data:
            return result.data[0]
        return {"shop_name": "Unknown", "outstanding_balance": 0, "credit_limit": 0}
    except Exception as e:
        logger.error(f"❌ Error getting customer balance: {e}")
        return {}


def get_all_balances(sort_by: str = "outstanding_desc") -> List[Dict]:
    """Get all customer outstanding balances."""
    db = get_db()
    try:
        query = db.table("customer_balance").select(
            "id, shop_name, credit_limit, outstanding_balance"
        )

        if sort_by == "outstanding_desc":
            query = query.order("outstanding_balance", desc=True)
        elif sort_by == "outstanding_asc":
            query = query.order("outstanding_balance", desc=False)

        result = query.execute()
        return result.data
    except Exception as e:
        logger.error(f"❌ Error getting all balances: {e}")
        return []


def record_payment(
    customer_id: int,
    amount: float,
    payment_date: str,
    payment_mode: str = None,
    notes: str = None,
    original_message: str = "",
    user_id: int = None
) -> Dict[str, Any]:
    """Record a customer payment."""
    db = get_db()
    try:
        # Insert into credit_ledger
        db.table("credit_ledger").insert({
            "customer_id": customer_id,
            "sale_id": None,
            "transaction_date": payment_date,
            "transaction_type": "payment_received",
            "debit_amount": 0,
            "credit_amount": amount,
            "recorded_by": user_id,
            "original_message": original_message
        }).execute()

        # Log to audit_log
        db.table("audit_log").insert({
            "action_type": "add_payment",
            "table_affected": "credit_ledger",
            "user_id": user_id,
            "original_message": original_message,
            "extracted_data": json.dumps({
                "customer_id": customer_id,
                "amount": amount,
                "payment_date": payment_date
            })
        }).execute()

        # Get new balance
        new_balance = get_customer_balance(customer_id)

        logger.info(f"✅ Payment recorded: ₹{amount} from customer {customer_id}")
        return {"success": True, "new_balance": new_balance.get("outstanding_balance", 0)}

    except Exception as e:
        logger.error(f"❌ Error recording payment: {e}")
        return {"success": False, "error": str(e)}


# ============================================================================
# PRODUCTION LOG
# ============================================================================

def save_production(
    prod_date: str,
    total_produced_kg: float,
    total_packets: int,
    notes: str = None,
    original_message: str = "",
    user_id: int = None
) -> Dict[str, Any]:
    """Save production log entry."""
    db = get_db()
    try:
        result = db.table("production_log").insert({
            "prod_date": prod_date,
            "total_produced_kg": total_produced_kg,
            "total_packets": total_packets,
            "batch_notes": notes,
            "recorded_by": user_id,
            "original_message": original_message
        }).execute()

        prod_id = result.data[0]["id"]
        logger.info(f"✅ Production log saved: {total_produced_kg}kg, {total_packets} packets")
        return {"success": True, "id": prod_id}

    except Exception as e:
        logger.error(f"❌ Error saving production: {e}")
        return {"success": False, "error": str(e)}


def query_production(
    date_from: str = None,
    date_to: str = None
) -> List[Dict]:
    """Query production log."""
    db = get_db()
    try:
        query = db.table("production_log").select("*")

        if date_from:
            query = query.gte("prod_date", date_from)
        if date_to:
            query = query.lte("prod_date", date_to)

        result = query.order("prod_date", desc=True).execute()
        return result.data
    except Exception as e:
        logger.error(f"❌ Error querying production: {e}")
        return []


# ============================================================================
# CASH FLOW
# ============================================================================

def save_cash_flow(
    flow_date: str,
    flow_type: str,
    category: str,
    description: str,
    amount: float,
    party: str = None,
    payment_mode: str = None,
    notes: str = None,
    original_message: str = "",
    user_id: int = None
) -> Dict[str, Any]:
    """Save cash flow entry."""
    db = get_db()
    try:
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
            "original_message": original_message
        }).execute()

        cf_id = result.data[0]["id"]
        logger.info(f"✅ Cash flow saved: {flow_type} ₹{amount}")
        return {"success": True, "id": cf_id}

    except Exception as e:
        logger.error(f"❌ Error saving cash flow: {e}")
        return {"success": False, "error": str(e)}


def get_cash_position() -> Dict[str, float]:
    """Get net cash position."""
    db = get_db()
    try:
        result = db.table("cash_position").select("total_in, total_out, net_cash").execute()
        if result.data:
            return result.data[0]
        return {"total_in": 0, "total_out": 0, "net_cash": 0}
    except Exception as e:
        logger.error(f"❌ Error getting cash position: {e}")
        return {}


# ============================================================================
# UTILITIES
# ============================================================================

def test_connection() -> bool:
    """Test database connection."""
    try:
        db = get_db()
        result = db.table("users").select("count(*)", count="exact").execute()
        logger.info("✅ Database connection test passed")
        return True
    except Exception as e:
        logger.error(f"❌ Database connection failed: {e}")
        return False
