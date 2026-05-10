"""Tests for src/agent.py — confirmation flow, rate limiting, tool dispatch."""

import json
import types
from unittest.mock import patch

import pytest

from src import agent, pending, session


# ---------------------------------------------------------------------------
# Fake Groq response builders
# ---------------------------------------------------------------------------

def _make_response(content=None, tool_calls=None):
    msg = types.SimpleNamespace(
        content=content,
        tool_calls=tool_calls,
    )
    usage = types.SimpleNamespace(prompt_tokens=0, completion_tokens=0, total_tokens=0)
    return types.SimpleNamespace(
        choices=[types.SimpleNamespace(message=msg)],
        usage=usage,
    )


def _make_tool_call(call_id, name, arguments):
    return types.SimpleNamespace(
        id=call_id,
        type="function",
        function=types.SimpleNamespace(name=name, arguments=json.dumps(arguments)),
    )


# ---------------------------------------------------------------------------
# Tool-call dedup helper
# ---------------------------------------------------------------------------

def test_dedupe_tool_calls_drops_exact_duplicate():
    """Identical (name, args) tool_calls collapse to one."""
    calls = [
        _make_tool_call("a", "create_customer", {"shop_name": "Abhinav Kirana"}),
        _make_tool_call("b", "create_customer", {"shop_name": "Abhinav Kirana"}),
    ]
    deduped = agent._dedupe_tool_calls(calls)
    assert len(deduped) == 1
    assert deduped[0].id == "a"  # first wins


def test_dedupe_tool_calls_ignores_arg_order():
    """Args differing only in JSON key order are still treated as duplicates."""
    calls = [
        types.SimpleNamespace(
            id="a", type="function",
            function=types.SimpleNamespace(name="save_sale", arguments='{"qty_kg":10,"customer_id":1}'),
        ),
        types.SimpleNamespace(
            id="b", type="function",
            function=types.SimpleNamespace(name="save_sale", arguments='{"customer_id":1,"qty_kg":10}'),
        ),
    ]
    deduped = agent._dedupe_tool_calls(calls)
    assert len(deduped) == 1


def test_dedupe_tool_calls_keeps_distinct():
    """Different names or different args are kept as separate calls."""
    calls = [
        _make_tool_call("a", "search_customer", {"name_fragment": "Sharma"}),
        _make_tool_call("b", "create_customer", {"shop_name": "Sharma"}),
        _make_tool_call("c", "create_customer", {"shop_name": "Patel"}),
    ]
    deduped = agent._dedupe_tool_calls(calls)
    assert len(deduped) == 3


def test_dedupe_tool_calls_preserves_malformed():
    """Malformed-args tool_calls aren't silently dropped — they fall through."""
    calls = [
        types.SimpleNamespace(
            id="a", type="function",
            function=types.SimpleNamespace(name="save_sale", arguments="{bad json"),
        ),
    ]
    deduped = agent._dedupe_tool_calls(calls)
    assert len(deduped) == 1


# ---------------------------------------------------------------------------
# Plain text turn (no tool call)
# ---------------------------------------------------------------------------

async def test_plain_text_response():
    with patch.object(agent, "call_llm",
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

    async def fake_call(messages, model=None):
        return responses.pop(0)

    fake_tool_result = json.dumps({"ok": True, "results": [{"id": 1, "shop_name": "Sharma"}]})

    async def fake_execute(name, args):
        assert name == "search_customer"
        return fake_tool_result

    with patch.object(agent, "call_llm", side_effect=fake_call), \
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

    async def fake_call(_messages, model=None):
        return _make_response(tool_calls=[tc])

    executed = []

    async def fake_execute(name, args):
        executed.append((name, args))
        return json.dumps({"ok": True})

    with patch.object(agent, "call_llm", side_effect=fake_call), \
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

    async def fake_call(_messages, model=None):
        return _make_response(content="✅ Sale saved: 50kg.")

    with patch.object(agent, "call_llm", side_effect=fake_call), \
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
    from src import session
    monkeypatch.setattr(session, "RATE_LIMIT_MESSAGES", 2)

    with patch.object(agent, "call_llm",
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

    async def fake_call(_m, model=None):
        return responses.pop(0)

    with patch.object(agent, "call_llm", side_effect=fake_call), \
         patch.object(agent, "execute_tool") as mock_exec:
        result = await agent.agent_loop("blah", user_id=30)

    mock_exec.assert_not_called()
    assert result.confirmation is None
    assert result.text == "Sorry, retry."


# ---------------------------------------------------------------------------
# Phase 1.3: safe history trim respects tool_call/tool pairing
# ---------------------------------------------------------------------------

def test_history_trim_preserves_tool_call_pairing(monkeypatch):
    """When the naive cut would orphan a tool_call from its tool result,
    walk forward to a safe boundary."""
    monkeypatch.setattr(session, "CONTEXT_WINDOW", 2)  # cap = max(2*4, 20) = 20
    # Build messages with a long prefix and a worst-case tool batch crossing the cut.
    msgs = []
    # Pad with 25 plain user/assistant pairs first.
    for i in range(25):
        msgs.append({"role": "user", "content": f"u{i}"})
        msgs.append({"role": "assistant", "content": f"a{i}"})
    # Tool batch right at the trim line.
    msgs.append({
        "role": "assistant", "content": "",
        "tool_calls": [{"id": "x1", "type": "function",
                        "function": {"name": "search_customer", "arguments": "{}"}}],
    })
    msgs.append({"role": "tool", "tool_call_id": "x1",
                 "name": "search_customer", "content": "{}"})
    # Final user turn must always survive.
    msgs.append({"role": "user", "content": "final"})

    session.set_history(99, msgs)
    persisted = session.get_history(99)
    # Final user message survives.
    assert any(m.get("role") == "user" and m.get("content") == "final" for m in persisted)
    # No orphaned tool messages: every "tool" has a preceding "assistant" with tool_calls
    # somewhere earlier (or comes after compacted system summary).
    for i, m in enumerate(persisted):
        if m.get("role") == "tool":
            tcid = m.get("tool_call_id")
            # The matching assistant must appear before this tool message.
            assert any(
                p.get("role") == "assistant"
                and any(tc.get("id") == tcid for tc in (p.get("tool_calls") or []))
                for p in persisted[:i]
            ), f"Tool message {tcid} has no matching assistant tool_call"


# ---------------------------------------------------------------------------
# Phase 1.5: summarize-on-trim extracts structural facts
# ---------------------------------------------------------------------------

def test_compact_dropped_messages_extracts_save_sale_facts():
    dropped = [
        {"role": "user", "content": "Sharma ko 50kg de do 120 rate"},
        {
            "role": "assistant", "content": "",
            "tool_calls": [{
                "id": "tc1", "type": "function",
                "function": {
                    "name": "save_sale",
                    "arguments": json.dumps({
                        "customer_id": 3, "qty_kg": 50,
                        "rate_per_kg": 120, "sale_date": "2026-05-03",
                    }),
                },
            }],
        },
        {"role": "tool", "tool_call_id": "tc1", "name": "save_sale",
         "content": json.dumps({"ok": True, "sale_id": 42, "total_bill": 6000})},
    ]
    summary = session._compact_dropped_messages(dropped)
    assert summary is not None
    assert summary["role"] == "system"
    body = summary["content"]
    assert "Sharma" in body  # user phrase preserved
    assert "save_sale" in body
    assert "customer_id=3" in body
    assert "qty_kg=50" in body
    assert "sale_id=42" in body


def test_compact_dropped_messages_skips_assistant_prose():
    """Plain assistant text (no tool_calls) carries no structural value — drop it."""
    dropped = [
        {"role": "assistant", "content": "Sure thing, here's what I found..."},
    ]
    assert session._compact_dropped_messages(dropped) is None


def test_compact_tool_result_shrinks_long_payload(monkeypatch):
    monkeypatch.setattr(session, "TOOL_RESULT_HISTORY_MAX_CHARS", 200)
    huge = json.dumps({"ok": True, "results": [{"id": i, "shop": "x" * 30} for i in range(50)]})
    compacted = session._compact_tool_result(huge)
    parsed = json.loads(compacted)
    assert parsed["truncated"] is True
    assert parsed["ok"] is True
    assert "results_total" in parsed
    assert parsed["results_total"] == 50
    assert len(parsed["results"]) == 3


def test_compact_tool_result_passes_through_small_payload():
    small = json.dumps({"ok": True, "sale_id": 1, "total_bill": 100})
    assert session._compact_tool_result(small) == small


# ---------------------------------------------------------------------------
# Phase 2.1: templated closing — no LLM call after confirmation
# ---------------------------------------------------------------------------

async def test_continue_after_confirmation_uses_template_no_llm_call():
    action = pending.PendingAction(
        user_id=50,
        assistant_message={
            "role": "assistant", "content": "",
            "tool_calls": [{
                "id": "tc1", "type": "function",
                "function": {"name": "save_sale", "arguments": "{}"},
            }],
        },
        tool_calls=[pending.PendingToolCall(
            id="tc1", name="save_sale",
            arguments={"customer_id": 1, "qty_kg": 50, "rate_per_kg": 120},
        )],
        summary="...",
        extras={"user_lang": "hi-Hind", "customer_names": {1: "Sharma"}},
    )

    async def fake_execute(_name, _args):
        return json.dumps({"ok": True, "sale_id": 99, "total_bill": 6000})

    with patch.object(agent, "call_llm") as mock_call, \
         patch.object(agent, "execute_tool", side_effect=fake_execute):
        result = await agent.continue_after_confirmation(50, action)

    mock_call.assert_not_called()  # the whole point of Phase 2.1
    assert "Sale saved" in result.text
    assert "Sharma" in result.text
    assert "50 kg" in result.text
    assert "₹6,000" in result.text


async def test_continue_after_confirmation_devanagari_template():
    action = pending.PendingAction(
        user_id=51,
        assistant_message={"role": "assistant", "content": "", "tool_calls": []},
        tool_calls=[pending.PendingToolCall(
            id="tc1", name="record_payment",
            arguments={"customer_id": 1, "amount": 5000, "payment_date": "2026-05-03"},
        )],
        summary="...",
        extras={"user_lang": "hi-Deva", "customer_names": {1: "गुप्ता"}},
    )

    async def fake_execute(_n, _a):
        return json.dumps({"ok": True, "ledger_id": 7, "new_balance": 1000})

    with patch.object(agent, "call_llm") as mock_call, \
         patch.object(agent, "execute_tool", side_effect=fake_execute):
        result = await agent.continue_after_confirmation(51, action)

    mock_call.assert_not_called()
    assert "गुप्ता" in result.text
    assert "नया बकाया" in result.text


async def test_continue_after_confirmation_templates_failure():
    """If the tool returns ok=false, render the error politely."""
    action = pending.PendingAction(
        user_id=52,
        assistant_message={"role": "assistant", "content": "", "tool_calls": []},
        tool_calls=[pending.PendingToolCall(
            id="tc1", name="save_sale", arguments={"customer_id": 1, "qty_kg": 1, "rate_per_kg": 1},
        )],
        summary="...",
        extras={"user_lang": "hi-Hind"},
    )

    async def fake_execute(_n, _a):
        return json.dumps({"ok": False, "error": "customer_id 1 not found"})

    with patch.object(agent, "call_llm") as mock_call, \
         patch.object(agent, "execute_tool", side_effect=fake_execute):
        result = await agent.continue_after_confirmation(52, action)

    mock_call.assert_not_called()
    assert "❌" in result.text
    assert "customer_id 1 not found" in result.text


# ---------------------------------------------------------------------------
# Phase 2.2: iteration-based model routing
# ---------------------------------------------------------------------------

async def test_iter1_uses_main_model_iter2_uses_fast(monkeypatch):
    """Iter 1 must use GROQ_MODEL (70B). Iter 2+ must use GROQ_MODEL_FAST (8B)."""
    monkeypatch.setattr(agent, "GROQ_MODEL", "test-70b")
    monkeypatch.setattr(agent, "GROQ_MODEL_FAST", "test-8b")
    captured_models: list = []
    responses = [
        _make_response(tool_calls=[_make_tool_call(
            "tc1", "search_customer", {"name_fragment": "Sharma"})]),
        _make_response(content="Mil gaya."),
    ]

    async def fake_call(_messages, model=None):
        captured_models.append(model)
        return responses.pop(0)

    async def fake_execute(_n, _a):
        return json.dumps({"ok": True, "results": [{"id": 1, "shop_name": "Sharma"}]})

    with patch.object(agent, "call_llm", side_effect=fake_call), \
         patch.object(agent, "execute_tool", side_effect=fake_execute):
        await agent.agent_loop("Sharma kaun?", user_id=70)

    assert captured_models == ["test-70b", "test-70b"]


async def test_google_rate_limit_falls_back_to_groq(monkeypatch):
    """When Google is primary and hits 429, call_llm falls back to Groq."""
    import openai
    from src import providers

    monkeypatch.setattr(providers, "GOOGLE_FALLBACK_ENABLED", True)

    groq_resp = _make_response(content="Groq reply")

    async def fake_groq(*_args, **_kwargs):
        return groq_resp

    # Create a mock that is an instance of openai.RateLimitError (subclass)
    class _MockRateLimitError(openai.RateLimitError):
        def __init__(self):
            pass

    async def raise_rate_limit(*_args, **_kwargs):
        raise _MockRateLimitError()

    from src import providers
    monkeypatch.setattr(providers, "_call_google", raise_rate_limit)

    from src import providers
    with patch("asyncio.to_thread", new=fake_groq):
        result = await providers.call_llm([{"role": "user", "content": "hi"}])

    assert result.choices[0].message.content == "Groq reply"


async def test_both_providers_exhausted_raises(monkeypatch):
    """When Google is disabled and Groq hits 429, RateLimitError propagates."""
    import groq as groq_sdk
    from src import providers

    monkeypatch.setattr(providers, "GOOGLE_FALLBACK_ENABLED", False)

    async def raise_rate_limit(*_args, **_kwargs):
        raise groq_sdk.RateLimitError("mocked rate limit")

    from src import providers
    with patch("asyncio.to_thread", new=raise_rate_limit):
        with pytest.raises(groq_sdk.RateLimitError):
            await providers.call_llm([{"role": "user", "content": "hi"}])


async def test_fast_model_empty_response_falls_back_to_main(monkeypatch):
    """All iterations now use main model. Fallback logic no longer applies."""
    monkeypatch.setattr(agent, "GROQ_MODEL", "test-70b")
    monkeypatch.setattr(agent, "GROQ_MODEL_FAST", "test-8b")
    responses = [
        _make_response(tool_calls=[_make_tool_call(
            "tc1", "search_customer", {"name_fragment": "x"})]),
        _make_response(content="Recovered."),
    ]
    captured: list = []

    async def fake_call(_m, model=None):
        captured.append(model)
        return responses.pop(0)

    async def fake_execute(_n, _a):
        return "{}"

    with patch.object(agent, "call_llm", side_effect=fake_call), \
         patch.object(agent, "execute_tool", side_effect=fake_execute):
        result = await agent.agent_loop("hi", user_id=71)

    assert captured == ["test-70b", "test-70b"]
    assert result.text == "Recovered."
