"""
Tools: execution layer for agent to call database operations.
Each tool wraps db.py functions and handles response formatting.
"""

import logging
import json
from typing import Any, Dict
from src import db
from src.utils import format_amount

logger = logging.getLogger(__name__)


def execute_tool(tool_name: str, tool_input: Dict[str, Any]) -> str:
    """
    Execute a tool and return result as JSON string.
    This is called by agent.py when LLM requests a tool call.
    """
    try:
        if tool_name == "search_customer":
            return _search_customer(tool_input)
        elif tool_name == "create_customer":
            return _create_customer(tool_input)
        elif tool_name == "save_sale":
            return _save_sale(tool_input)
        elif tool_name == "record_payment":
            return _record_payment(tool_input)
        elif tool_name == "get_customer_balance":
            return _get_customer_balance(tool_input)
        elif tool_name == "get_all_balances":
            return _get_all_balances(tool_input)
        elif tool_name == "save_production":
            return _save_production(tool_input)
        elif tool_name == "save_cash_flow":
            return _save_cash_flow(tool_input)
        elif tool_name == "query_sales":
            return _query_sales(tool_input)
        elif tool_name == "get_cash_position":
            return _get_cash_position(tool_input)
        else:
            return json.dumps({"error": f"Unknown tool: {tool_name}"})
    except Exception as e:
        logger.error(f"Error executing tool {tool_name}: {e}")
        return json.dumps({"error": str(e)})


# ============================================================================
# TOOL IMPLEMENTATIONS
# ============================================================================

def _search_customer(input_data: Dict) -> str:
    """Search for customers."""
    name = input_data.get("name_fragment", "")
    results = db.search_customer(name)
    return json.dumps({"results": results, "count": len(results)})


def _create_customer(input_data: Dict) -> str:
    """Create a new customer."""
    result = db.create_customer(
        shop_name=input_data.get("shop_name"),
        owner_name=input_data.get("owner_name"),
        owner_phone=input_data.get("owner_phone"),
        address=input_data.get("address"),
        credit_limit=input_data.get("credit_limit", 0),
        user_id=input_data.get("user_id")
    )
    return json.dumps(result)


def _save_sale(input_data: Dict) -> str:
    """Save a sale."""
    result = db.save_sale(
        customer_id=input_data.get("customer_id"),
        qty_kg=input_data.get("qty_kg"),
        rate_per_kg=input_data.get("rate_per_kg"),
        sale_date=input_data.get("sale_date"),
        payment_status=input_data.get("payment_status"),
        payment_mode=input_data.get("payment_mode"),
        notes=input_data.get("notes"),
        original_message=input_data.get("original_message"),
        user_id=input_data.get("user_id")
    )
    return json.dumps(result)


def _record_payment(input_data: Dict) -> str:
    """Record a payment."""
    result = db.record_payment(
        customer_id=input_data.get("customer_id"),
        amount=input_data.get("amount"),
        payment_date=input_data.get("payment_date"),
        payment_mode=input_data.get("payment_mode"),
        notes=input_data.get("notes"),
        original_message=input_data.get("original_message"),
        user_id=input_data.get("user_id")
    )
    return json.dumps(result)


def _get_customer_balance(input_data: Dict) -> str:
    """Get a customer's balance."""
    balance = db.get_customer_balance(input_data.get("customer_id"))
    return json.dumps(balance)


def _get_all_balances(input_data: Dict) -> str:
    """Get all customers' balances."""
    sort_by = input_data.get("sort_by", "outstanding_desc")
    balances = db.get_all_balances(sort_by)

    # Format for display
    formatted = []
    total_outstanding = 0
    for row in balances:
        outstanding = row.get("outstanding_balance", 0)
        total_outstanding += outstanding
        formatted.append({
            "shop_name": row.get("shop_name"),
            "outstanding_balance": outstanding,
            "credit_limit": row.get("credit_limit"),
            "formatted": f"{row.get('shop_name')} — {format_amount(outstanding)}"
        })

    return json.dumps({
        "balances": formatted,
        "total_outstanding": total_outstanding,
        "formatted_total": format_amount(total_outstanding)
    })


def _save_production(input_data: Dict) -> str:
    """Save production log."""
    result = db.save_production(
        prod_date=input_data.get("prod_date"),
        total_produced_kg=input_data.get("total_produced_kg"),
        total_packets=input_data.get("total_packets"),
        notes=input_data.get("notes"),
        original_message=input_data.get("original_message"),
        user_id=input_data.get("user_id")
    )
    return json.dumps(result)


def _save_cash_flow(input_data: Dict) -> str:
    """Save cash flow."""
    result = db.save_cash_flow(
        flow_date=input_data.get("flow_date"),
        flow_type=input_data.get("flow_type"),
        category=input_data.get("category"),
        description=input_data.get("description"),
        amount=input_data.get("amount"),
        party=input_data.get("party"),
        payment_mode=input_data.get("payment_mode"),
        notes=input_data.get("notes"),
        original_message=input_data.get("original_message"),
        user_id=input_data.get("user_id")
    )
    return json.dumps(result)


def _query_sales(input_data: Dict) -> str:
    """Query sales."""
    sales = db.query_sales(
        customer_id=input_data.get("customer_id"),
        date_from=input_data.get("date_from"),
        date_to=input_data.get("date_to")
    )
    return json.dumps({"sales": sales, "count": len(sales)})


def _get_cash_position(input_data: Dict) -> str:
    """Get cash position."""
    position = db.get_cash_position()
    position["formatted_in"] = format_amount(position.get("total_in", 0))
    position["formatted_out"] = format_amount(position.get("total_out", 0))
    position["formatted_net"] = format_amount(position.get("net_cash", 0))
    return json.dumps(position)
