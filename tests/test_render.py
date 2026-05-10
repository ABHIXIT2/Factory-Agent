"""Tests for src/render.py — confirmation summaries and closing messages."""

import json

from src.render import (
	_extract_customer_names, _build_summary, _render_closing,
	_customer_label, _parse_tool_result,
)
from src import pending


def test_extract_customer_names_from_search():
	"""Extract customer names from search_customer tool results."""
	messages = [
		{"role": "tool", "name": "search_customer", "content": json.dumps({
			"ok": True, "results": [
				{"id": 1, "shop_name": "Sharma"},
				{"id": 2, "shop_name": "Patel"},
			],
		})},
	]
	names = _extract_customer_names(messages)
	assert names == {1: "Sharma", 2: "Patel"}


def test_extract_customer_names_from_create():
	"""Extract customer names from create_customer tool results."""
	messages = [
		{"role": "tool", "name": "create_customer", "content": json.dumps({
			"ok": True, "customer_id": 99, "shop_name": "New Shop",
		})},
	]
	names = _extract_customer_names(messages)
	assert names == {99: "New Shop"}


def test_extract_customer_names_mixed():
	"""Merge names from both search and create results."""
	messages = [
		{"role": "tool", "name": "search_customer", "content": json.dumps({
			"ok": True, "results": [{"id": 1, "shop_name": "A"}],
		})},
		{"role": "tool", "name": "create_customer", "content": json.dumps({
			"ok": True, "customer_id": 2, "shop_name": "B",
		})},
	]
	names = _extract_customer_names(messages)
	assert names == {1: "A", 2: "B"}


def test_extract_customer_names_ignores_failed_create():
	"""Don't extract names from failed create_customer."""
	messages = [
		{"role": "tool", "name": "create_customer", "content": json.dumps({
			"ok": False, "error": "shop already exists",
		})},
	]
	names = _extract_customer_names(messages)
	assert names == {}


def test_customer_label_with_name():
	"""Resolve customer_id to bold shop name when known."""
	label = _customer_label({"customer_id": 1}, {1: "Sharma"})
	assert "*Sharma*" in label


def test_customer_label_without_name():
	"""Fall back to raw ID when customer not in names."""
	label = _customer_label({"customer_id": 999}, {})
	assert "999" in label


def test_build_summary_single_write():
	"""Build confirmation summary for a single write tool."""
	call = pending.PendingToolCall(
		id="tc1",
		name="save_sale",
		arguments={"customer_id": 1, "qty_kg": 10, "rate_per_kg": 50, "sale_date": "2025-05-03", "payment_status": "cash"},
	)
	summary = _build_summary([call], {1: "Sharma"})
	assert "Sale confirm" in summary
	assert "Sharma" in summary
	assert "10 kg" in summary


def test_build_summary_multiple_writes():
	"""Build summary for multiple write tools."""
	calls = [
		pending.PendingToolCall(
			id="tc1", name="save_sale",
			arguments={"customer_id": 1, "qty_kg": 5, "rate_per_kg": 100, "sale_date": "2025-05-03", "payment_status": "cash"},
		),
		pending.PendingToolCall(
			id="tc2", name="record_payment",
			arguments={"customer_id": 1, "amount": 500, "payment_date": "2025-05-03", "payment_mode": "cash"},
		),
	]
	summary = _build_summary(calls, {1: "Sharma"})
	assert "Sale confirm" in summary
	assert "Payment confirm" in summary


def test_parse_tool_result_valid_json():
	"""Parse valid JSON tool result."""
	result = _parse_tool_result('{"ok": true, "sale_id": 42}')
	assert result == {"ok": True, "sale_id": 42}


def test_parse_tool_result_invalid_json():
	"""Return empty dict on invalid JSON."""
	result = _parse_tool_result("{bad json")
	assert result == {}


def test_parse_tool_result_empty_string():
	"""Return empty dict for empty string."""
	result = _parse_tool_result("")
	assert result == {}


def test_render_closing_success_path():
	"""Render closing message after successful tool execution."""
	call = pending.PendingToolCall(
		id="tc1", name="save_sale",
		arguments={"customer_id": 1, "qty_kg": 10, "rate_per_kg": 50},
	)
	tool_result = json.dumps({
		"ok": True, "sale_id": 123, "total_bill": 500,
	})
	closing = _render_closing(call, tool_result, {1: "Sharma"}, "hi-Hind")
	assert "✅ Sale saved" in closing
	assert "Sharma" in closing


def test_render_closing_error_path():
	"""Render error message when tool result has ok=false."""
	call = pending.PendingToolCall(
		id="tc1", name="save_sale",
		arguments={"customer_id": 1, "qty_kg": 10, "rate_per_kg": 50},
	)
	tool_result = json.dumps({
		"ok": False, "error": "customer not found",
	})
	closing = _render_closing(call, tool_result, {}, "hi-Hind")
	assert "❌ Save nahi ho paya" in closing
	assert "customer not found" in closing


def test_render_closing_unknown_tool():
	"""Fall back to generic success for unknown tools."""
	call = pending.PendingToolCall(
		id="tc1", name="some_unknown_tool",
		arguments={},
	)
	closing = _render_closing(call, "{}", {}, "hi-Hind")
	assert closing == "✅ Saved."
