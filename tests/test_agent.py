"""Tests for src/agent.py — confirmation flow, rate limiting, tool dispatch."""

import json
import types
from unittest.mock import patch

import pytest

from src import agent, pending


# ---------------------------------------------------------------------------
# Fake Groq response builders
# ---------------------------------------------------------------------------

def _make_response(content=None, tool_calls=None):
    msg = types.SimpleNamespace(
        content=content,
        tool_calls=tool_calls,
    )
    return types.SimpleNamespace(choices=[types.SimpleNamespace(message=msg)])


def _make_tool_call(call_id, name, arguments):
    return types.SimpleNamespace(
        id=call_id,
        type="function",
        function=types.SimpleNamespace(name=name, arguments=json.dumps(arguments)),
    )


# ---------------------------------------------------------------------------
# Plain text turn (no tool call)
# ---------------------------------------------------------------------------

async def test_plain_text_response():
    with patch.object(agent, "_call_groq",
                      return_value=_make_response(content="Hi there!")):
        result = await agent.agent_loop("hello", user_id=1)
    assert result.text == "Hi there!"
    assert result.confirmation is None


# ---------------------------------------------------------------------------
# Read-only tool: executes inline, second call returns text
# ---------------------------------------------------------------------------

async def test_read_only_tool_executes_inline():
    responses = [
        _make_response(tool_calls=[_make_tool_call(
            "tc1", "search_customer", {"name_fragment": "Sharma"}
        )]),
        _make_response(content="Mil gaya: Sharma General Store"),
    ]

    async def fake_call(messages):
        return responses.pop(0)

    fake_tool_result = json.dumps({"ok": True, "results": [{"id": 1, "shop_name": "Sharma"}]})

    async def fake_execute(name, args):
        assert name == "search_customer"
        return fake_tool_result

    with patch.object(agent, "_call_groq", side_effect=fake_call), \
         patch.object(agent, "execute_tool", side_effect=fake_execute):
        result = await agent.agent_loop("Sharma kaun hai?", user_id=2)

    assert result.confirmation is None
    assert "Sharma" in result.text


# ---------------------------------------------------------------------------
# Write tool: should be deferred — returns Confirmation, no DB call yet
# ---------------------------------------------------------------------------

async def test_write_tool_triggers_confirmation():
    tc = _make_tool_call("tc1", "save_sale", {
        "customer_id": 1, "qty_kg": 50, "rate_per_kg": 120,
        "sale_date": "2024-01-15", "payment_status": "credited",
        "original_message": "test",
    })

    async def fake_call(_messages):
        return _make_response(tool_calls=[tc])

    executed = []

    async def fake_execute(name, args):
        executed.append((name, args))
        return json.dumps({"ok": True})

    with patch.object(agent, "_call_groq", side_effect=fake_call), \
         patch.object(agent, "execute_tool", side_effect=fake_execute):
        result = await agent.agent_loop("Sharma ko 50kg de do", user_id=3)

    assert result.confirmation is not None
    assert result.confirmation.token
    assert "confirm kijiye" in result.confirmation.summary
    assert executed == []  # nothing executed yet


# ---------------------------------------------------------------------------
# After confirm: executes the staged tool call
# ---------------------------------------------------------------------------

async def test_continue_after_confirmation_runs_tool():
    action = pending.PendingAction(
        user_id=4,
        assistant_message={
            "role": "assistant",
            "content": "",
            "tool_calls": [{
                "id": "tc1", "type": "function",
                "function": {"name": "save_sale", "arguments": "{}"},
            }],
        },
        tool_calls=[pending.PendingToolCall(
            id="tc1", name="save_sale",
            arguments={"customer_id": 1, "qty_kg": 50},
        )],
        summary="...",
    )

    executed = []

    async def fake_execute(name, args):
        executed.append((name, args))
        return json.dumps({"ok": True, "sale_id": 99})

    async def fake_call(_messages):
        return _make_response(content="✅ Sale saved: 50kg.")

    with patch.object(agent, "_call_groq", side_effect=fake_call), \
         patch.object(agent, "execute_tool", side_effect=fake_execute):
        result = await agent.continue_after_confirmation(4, action)

    assert executed == [("save_sale", {"customer_id": 1, "qty_kg": 50})]
    assert "saved" in result.text.lower()


# ---------------------------------------------------------------------------
# Cancel: tool not executed, history records cancellation
# ---------------------------------------------------------------------------

async def test_cancel_does_not_execute_tool():
    action = pending.PendingAction(
        user_id=5,
        assistant_message={"role": "assistant", "content": "", "tool_calls": []},
        tool_calls=[pending.PendingToolCall(id="tc1", name="save_sale", arguments={})],
        summary="...",
    )

    with patch.object(agent, "execute_tool") as mock_exec:
        result = await agent.cancel_pending(5, action)

    mock_exec.assert_not_called()
    assert "Cancelled" in result.text


# ---------------------------------------------------------------------------
# Pending store: cross-user token replay rejected
# ---------------------------------------------------------------------------

def test_pending_token_cross_user_rejected():
    action = pending.PendingAction(
        user_id=10,
        assistant_message={"role": "assistant", "content": "", "tool_calls": []},
        tool_calls=[],
        summary="x",
    )
    token = pending.put(action)
    # Foreign-user pop returns None AND consumes the token (DoS, not privilege escalation)
    assert pending.pop(token, user_id=999) is None
    assert pending.pop(token, user_id=10) is None  # already consumed


# ---------------------------------------------------------------------------
# Rate limiter
# ---------------------------------------------------------------------------

async def test_rate_limiter_blocks_after_threshold(monkeypatch):
    monkeypatch.setattr(agent, "RATE_LIMIT_MESSAGES", 2)

    with patch.object(agent, "_call_groq",
                      return_value=_make_response(content="ok")):
        r1 = await agent.agent_loop("a", user_id=20)
        r2 = await agent.agent_loop("b", user_id=20)
        r3 = await agent.agent_loop("c", user_id=20)

    assert r1.text == "ok"
    assert r2.text == "ok"
    assert "Bahut messages" in r3.text or "ruk" in r3.text


# ---------------------------------------------------------------------------
# Bad JSON in tool arguments doesn't crash the loop
# ---------------------------------------------------------------------------

async def test_bad_tool_args_handled_gracefully():
    bad_tc = types.SimpleNamespace(
        id="tc1", type="function",
        function=types.SimpleNamespace(name="search_customer", arguments="not json {"),
    )
    responses = [
        _make_response(tool_calls=[bad_tc]),
        _make_response(content="Sorry, retry."),
    ]

    async def fake_call(_m):
        return responses.pop(0)

    with patch.object(agent, "_call_groq", side_effect=fake_call), \
         patch.object(agent, "execute_tool") as mock_exec:
        result = await agent.agent_loop("blah", user_id=30)

    mock_exec.assert_not_called()
    assert result.confirmation is None
    assert result.text == "Sorry, retry."
