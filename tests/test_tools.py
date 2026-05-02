"""Tests for src/tools.py — input validation, dispatch, error envelopes."""

import json
from unittest.mock import patch

import pytest

from src import tools


def _decode(s: str) -> dict:
    return json.loads(s)


# ---------------- dispatch ----------------

async def test_unknown_tool_returns_safe_error():
    result = _decode(await tools.execute_tool("nope", {}))
    assert result == {"ok": False, "error": "Unknown tool: nope"}


# ---------------- search_customer ----------------

async def test_search_customer_sanitizes_and_calls_db():
    with patch.object(tools.db, "search_customer", return_value=[{"id": 1, "shop_name": "Sharma"}]) as mock:
        result = _decode(await tools.execute_tool(
            "search_customer", {"name_fragment": "Sharma%"}
        ))
    mock.assert_called_once_with("Sharma")  # `%` stripped
    assert result["ok"] is True
    assert result["count"] == 1


async def test_search_customer_rejects_empty():
    result = _decode(await tools.execute_tool("search_customer", {"name_fragment": ""}))
    assert result["ok"] is False
    assert "non-empty" in result["error"]


# ---------------- save_sale validation ----------------

async def test_save_sale_rejects_negative_qty():
    result = _decode(await tools.execute_tool("save_sale", {
        "customer_id": 1, "qty_kg": -10, "rate_per_kg": 100,
        "sale_date": "2024-01-15", "payment_status": "paid",
        "original_message": "test",
    }))
    assert result["ok"] is False
    assert "qty_kg" in result["error"]


async def test_save_sale_rejects_zero_rate():
    result = _decode(await tools.execute_tool("save_sale", {
        "customer_id": 1, "qty_kg": 50, "rate_per_kg": 0,
        "sale_date": "2024-01-15", "payment_status": "paid",
        "original_message": "test",
    }))
    assert result["ok"] is False
    assert "rate_per_kg" in result["error"]


async def test_save_sale_rejects_bad_date():
    result = _decode(await tools.execute_tool("save_sale", {
        "customer_id": 1, "qty_kg": 50, "rate_per_kg": 100,
        "sale_date": "yesterday", "payment_status": "paid",
        "original_message": "test",
    }))
    assert result["ok"] is False
    assert "sale_date" in result["error"]


async def test_save_sale_rejects_unknown_payment_status():
    result = _decode(await tools.execute_tool("save_sale", {
        "customer_id": 1, "qty_kg": 50, "rate_per_kg": 100,
        "sale_date": "2024-01-15", "payment_status": "partial",
        "original_message": "test",
    }))
    assert result["ok"] is False
    assert "payment_status" in result["error"]


async def test_save_sale_happy_path():
    with patch.object(tools.db, "save_sale", return_value={"success": True, "sale_id": 42, "total_bill": 5000.0}) as mock:
        result = _decode(await tools.execute_tool("save_sale", {
            "customer_id": 1, "qty_kg": 50, "rate_per_kg": 100,
            "sale_date": "2024-01-15", "payment_status": "credited",
            "original_message": "test",
        }))
    assert result["ok"] is True
    assert result["sale_id"] == 42
    mock.assert_called_once()


# ---------------- record_payment ----------------

async def test_record_payment_validates_amount():
    result = _decode(await tools.execute_tool("record_payment", {
        "customer_id": 1, "amount": -50, "payment_date": "2024-01-15",
        "original_message": "test",
    }))
    assert result["ok"] is False


async def test_record_payment_happy_path():
    with patch.object(tools.db, "record_payment",
                      return_value={"success": True, "ledger_id": 99, "new_balance": 1234.5}):
        result = _decode(await tools.execute_tool("record_payment", {
            "customer_id": 1, "amount": 1000, "payment_date": "2024-01-15",
            "payment_mode": "cash", "original_message": "test",
        }))
    assert result["ok"] is True
    assert result["formatted_balance"] == "₹1,234.50"


# ---------------- save_cash_flow ----------------

async def test_cash_flow_requires_category():
    result = _decode(await tools.execute_tool("save_cash_flow", {
        "flow_date": "2024-01-15", "flow_type": "in",
        "category": "", "description": "x", "amount": 100,
        "original_message": "test",
    }))
    assert result["ok"] is False
    assert "category" in result["error"]


async def test_cash_flow_rejects_bad_type():
    result = _decode(await tools.execute_tool("save_cash_flow", {
        "flow_date": "2024-01-15", "flow_type": "wrong",
        "category": "rent", "description": "x", "amount": 100,
        "original_message": "test",
    }))
    assert result["ok"] is False
    assert "flow_type" in result["error"]


# ---------------- error redaction ----------------

async def test_db_exception_returns_generic_error():
    def boom(*a, **kw):
        raise RuntimeError("supersecret credentials abc123")
    with patch.object(tools.db, "search_customer", side_effect=boom):
        result = _decode(await tools.execute_tool(
            "search_customer", {"name_fragment": "Sharma"}
        ))
    assert result["ok"] is False
    assert "supersecret" not in result["error"]
    assert "internal error" in result["error"]
