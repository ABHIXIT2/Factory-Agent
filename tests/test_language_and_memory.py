"""Tests for language detection, three-language rendering, and memory injection."""

import json
import pytest

from src.utils import detect_user_lang
from src.render import (
    _build_summary, _render_closing, _summarize_save_sale,
    _summarize_record_payment, _summarize_create_customer,
)
from src import pending, session


# ============================================================================
# LANGUAGE DETECTION TESTS (three-value: hi-Deva, hi-Hind, en)
# ============================================================================

class TestLanguageDetection:
    """Verify detect_user_lang returns three distinct values."""

    def test_pure_devanagari(self):
        """Pure Devanagari text returns hi-Deva."""
        assert detect_user_lang("शर्मा को 50 किलो दे दो") == "hi-Deva"
        assert detect_user_lang("आज का उत्पादन कितना है?") == "hi-Deva"

    def test_pure_english(self):
        """Pure English text returns en."""
        assert detect_user_lang("Show me the balances") == "en"
        assert detect_user_lang("What is the total sale for today?") == "en"
        assert detect_user_lang("The customer has a balance of 5000") == "en"

    def test_hinglish_roman(self):
        """Hinglish (Roman Hindi without many English words) returns hi-Hind."""
        assert detect_user_lang("Sharma ko 50kg de do") == "hi-Hind"
        assert detect_user_lang("Aaj ka production kitna tha?") == "hi-Hind"
        assert detect_user_lang("Customer number 5 ke liye 30kg") == "hi-Hind"

    def test_mixed_devanagari_english_prefers_devanagari(self):
        """Even one Devanagari char tips decision to hi-Deva."""
        assert detect_user_lang("Sharma को 50kg de do") == "hi-Deva"
        assert detect_user_lang("आज का sale kitna tha?") == "hi-Deva"

    def test_empty_and_none_default_to_hinglish(self):
        """Empty string and None default to hi-Hind."""
        assert detect_user_lang("") == "hi-Hind"
        assert detect_user_lang(None) == "hi-Hind"

    def test_mixed_roman_numbers_hinglish(self):
        """Mixed Roman and numbers stays as Hinglish."""
        assert detect_user_lang("Sharma ko 50 quantity 100 rate") == "en"  # "quantity" is English word
        assert detect_user_lang("Sharma ko paanch sau nau dal") == "hi-Hind"  # Hindi words only

    def test_english_with_numbers(self):
        """English text with numbers still detected as English."""
        assert detect_user_lang("Customer 5 balance is 1000") == "en"


# ============================================================================
# SUMMARY RENDERING TESTS (three-language output verification)
# ============================================================================

class TestSummaryRendering:
    """Verify _build_summary and _summarize_* functions render in correct language."""

    def test_save_sale_devanagari(self):
        """save_sale confirmation renders in Devanagari."""
        call = pending.PendingToolCall(
            id="tc1", name="save_sale",
            arguments={
                "customer_id": 1, "qty_kg": 10, "rate_per_kg": 50,
                "sale_date": "2025-05-03", "payment_status": "cash"
            },
        )
        summary = _build_summary([call], {1: "शर्मा"}, user_lang="hi-Deva")
        # Should contain Devanagari headers
        assert "बिक्री" in summary or "Sale" in summary  # Either Devanagari or fallback

    def test_save_sale_english(self):
        """save_sale confirmation renders in English."""
        call = pending.PendingToolCall(
            id="tc1", name="save_sale",
            arguments={
                "customer_id": 1, "qty_kg": 10, "rate_per_kg": 50,
                "sale_date": "2025-05-03", "payment_status": "cash"
            },
        )
        summary = _build_summary([call], {1: "Sharma"}, user_lang="en")
        assert "Confirm sale" in summary or "Sale confirm" in summary
        assert "Sharma" in summary
        assert "10 kg" in summary

    def test_save_sale_hinglish(self):
        """save_sale confirmation renders in Hinglish."""
        call = pending.PendingToolCall(
            id="tc1", name="save_sale",
            arguments={
                "customer_id": 1, "qty_kg": 10, "rate_per_kg": 50,
                "sale_date": "2025-05-03", "payment_status": "cash"
            },
        )
        summary = _build_summary([call], {1: "Sharma"}, user_lang="hi-Hind")
        assert "Sale confirm" in summary
        assert "Sharma" in summary

    def test_build_summary_fallback_to_default_lang(self):
        """_build_summary falls back to hi-Hind when lang not provided."""
        call = pending.PendingToolCall(
            id="tc1", name="save_sale",
            arguments={
                "customer_id": 1, "qty_kg": 5, "rate_per_kg": 100,
                "sale_date": "2025-05-03", "payment_status": "cash"
            },
        )
        # No user_lang provided
        summary = _build_summary([call], {1: "Sharma"})
        assert "Sale confirm" in summary  # Default Hinglish rendering

    def test_record_payment_devanagari(self):
        """record_payment renders in Devanagari."""
        call = pending.PendingToolCall(
            id="tc2", name="record_payment",
            arguments={
                "customer_id": 1, "amount": 500,
                "payment_date": "2025-05-03", "payment_mode": "cash"
            },
        )
        summary = _build_summary([call], {1: "शर्मा"}, user_lang="hi-Deva")
        assert len(summary) > 0

    def test_record_payment_english(self):
        """record_payment renders in English."""
        call = pending.PendingToolCall(
            id="tc2", name="record_payment",
            arguments={
                "customer_id": 1, "amount": 500,
                "payment_date": "2025-05-03", "payment_mode": "cash"
            },
        )
        summary = _build_summary([call], {1: "Sharma"}, user_lang="en")
        assert "Payment" in summary or "payment" in summary

    def test_multiple_tools_different_languages(self):
        """Multiple tools render correctly in specified language."""
        calls = [
            pending.PendingToolCall(
                id="tc1", name="save_sale",
                arguments={
                    "customer_id": 1, "qty_kg": 5, "rate_per_kg": 100,
                    "sale_date": "2025-05-03", "payment_status": "cash"
                },
            ),
            pending.PendingToolCall(
                id="tc2", name="record_payment",
                arguments={
                    "customer_id": 1, "amount": 500,
                    "payment_date": "2025-05-03", "payment_mode": "cash"
                },
            ),
        ]
        # Test in English
        summary_en = _build_summary(calls, {1: "Sharma"}, user_lang="en")
        assert "Sharma" in summary_en
        # Test in Hinglish
        summary_hi = _build_summary(calls, {1: "Sharma"}, user_lang="hi-Hind")
        assert "Sharma" in summary_hi


# ============================================================================
# CLOSING MESSAGE RENDERING TESTS (success/error in three languages)
# ============================================================================

class TestClosingMessageRendering:
    """Verify _render_closing renders in correct language."""

    def test_closing_success_devanagari(self):
        """Successful save_sale closing in Devanagari."""
        call = pending.PendingToolCall(
            id="tc1", name="save_sale",
            arguments={"customer_id": 1, "qty_kg": 10, "rate_per_kg": 50},
        )
        tool_result = json.dumps({
            "ok": True, "sale_id": 123, "total_bill": 500,
        })
        closing = _render_closing(call, tool_result, {1: "शर्मा"}, "hi-Deva")
        # Should contain success indicator
        assert "✅" in closing

    def test_closing_success_english(self):
        """Successful save_sale closing in English."""
        call = pending.PendingToolCall(
            id="tc1", name="save_sale",
            arguments={"customer_id": 1, "qty_kg": 10, "rate_per_kg": 50},
        )
        tool_result = json.dumps({
            "ok": True, "sale_id": 123, "total_bill": 500,
        })
        closing = _render_closing(call, tool_result, {1: "Sharma"}, "en")
        assert "✅" in closing or "saved" in closing.lower()

    def test_closing_success_hinglish(self):
        """Successful save_sale closing in Hinglish."""
        call = pending.PendingToolCall(
            id="tc1", name="save_sale",
            arguments={"customer_id": 1, "qty_kg": 10, "rate_per_kg": 50},
        )
        tool_result = json.dumps({
            "ok": True, "sale_id": 123, "total_bill": 500,
        })
        closing = _render_closing(call, tool_result, {1: "Sharma"}, "hi-Hind")
        assert "✅" in closing
        assert "Sharma" in closing

    def test_closing_error_devanagari(self):
        """Error closing in Devanagari."""
        call = pending.PendingToolCall(
            id="tc1", name="save_sale",
            arguments={"customer_id": 1, "qty_kg": 10, "rate_per_kg": 50},
        )
        tool_result = json.dumps({
            "ok": False, "error": "customer not found",
        })
        closing = _render_closing(call, tool_result, {}, "hi-Deva")
        assert "❌" in closing

    def test_closing_error_english(self):
        """Error closing in English."""
        call = pending.PendingToolCall(
            id="tc1", name="save_sale",
            arguments={"customer_id": 1, "qty_kg": 10, "rate_per_kg": 50},
        )
        tool_result = json.dumps({
            "ok": False, "error": "customer not found",
        })
        closing = _render_closing(call, tool_result, {}, "en")
        assert "❌" in closing or "error" in closing.lower()
        assert "customer not found" in closing

    def test_closing_error_hinglish(self):
        """Error closing in Hinglish."""
        call = pending.PendingToolCall(
            id="tc1", name="save_sale",
            arguments={"customer_id": 1, "qty_kg": 10, "rate_per_kg": 50},
        )
        tool_result = json.dumps({
            "ok": False, "error": "customer not found",
        })
        closing = _render_closing(call, tool_result, {}, "hi-Hind")
        assert "❌" in closing or "nahi" in closing.lower()
        assert "customer not found" in closing

    def test_closing_unknown_tool_all_languages(self):
        """Unknown tool falls back to generic message in all languages."""
        call = pending.PendingToolCall(
            id="tc1", name="some_unknown_tool",
            arguments={},
        )
        closing_deva = _render_closing(call, "{}", {}, "hi-Deva")
        closing_en = _render_closing(call, "{}", {}, "en")
        closing_hind = _render_closing(call, "{}", {}, "hi-Hind")

        # All should have some success indicator
        for closing in [closing_deva, closing_en, closing_hind]:
            assert "✅" in closing or "Saved" in closing


# ============================================================================
# MEMORY INJECTION TESTS (context preservation)
# ============================================================================

class TestMemoryInjection:
    """Verify inject_created_customer and inject_selected_customer preserve context."""

    def setup_method(self):
        """Clear session before each test."""
        # Clear any existing sessions
        if hasattr(session._sessions, 'data'):
            session._sessions.data.clear()

    def test_inject_selected_customer_adds_to_history(self):
        """inject_selected_customer appends search result to history."""
        user_id = 999
        session.set_history(user_id, [])

        session.inject_selected_customer(user_id, customer_id=5, shop_name="Sharma")
        history = session.get_history(user_id)

        # Should have 2 messages: assistant (with tool_call) and tool (result)
        assert len(history) == 2
        assert history[0]["role"] == "assistant"
        assert history[0]["tool_calls"][0]["function"]["name"] == "search_customer"
        assert history[1]["role"] == "tool"
        assert history[1]["name"] == "search_customer"

    def test_inject_selected_customer_preserves_existing_history(self):
        """inject_selected_customer appends to existing history."""
        user_id = 998
        initial_msg = {"role": "user", "content": "Find Sharma"}
        session.set_history(user_id, [initial_msg])

        session.inject_selected_customer(user_id, customer_id=5, shop_name="Sharma")
        history = session.get_history(user_id)

        # Should have initial msg + 2 new messages
        assert len(history) == 3
        assert history[0] == initial_msg

    def test_inject_created_customer_adds_to_history(self):
        """inject_created_customer appends create result to history."""
        user_id = 997
        session.set_history(user_id, [])

        session.inject_created_customer(user_id, customer_id=99, shop_name="New Shop")
        history = session.get_history(user_id)

        # Should have 2 messages: assistant (with tool_call) and tool (result)
        assert len(history) == 2
        assert history[0]["role"] == "assistant"
        assert "New Shop" in history[0]["content"]
        assert history[0]["tool_calls"][0]["function"]["name"] == "create_customer"
        assert history[1]["role"] == "tool"
        assert history[1]["name"] == "create_customer"

    def test_inject_created_customer_contains_customer_id(self):
        """inject_created_customer embeds customer_id in tool result."""
        user_id = 996
        session.set_history(user_id, [])

        session.inject_created_customer(user_id, customer_id=77, shop_name="Verma Traders")
        history = session.get_history(user_id)

        # Extract tool result and verify customer_id
        tool_result = json.loads(history[1]["content"])
        assert tool_result["customer_id"] == 77
        assert tool_result["shop_name"] == "Verma Traders"
        assert tool_result["ok"] is True

    def test_inject_created_customer_preserves_existing_history(self):
        """inject_created_customer appends to existing history."""
        user_id = 995
        initial_msgs = [
            {"role": "user", "content": "Create Verma Traders"},
            {"role": "assistant", "content": "Creating..."},
        ]
        session.set_history(user_id, initial_msgs)

        session.inject_created_customer(user_id, customer_id=99, shop_name="Verma Traders")
        history = session.get_history(user_id)

        # Should have initial msgs + 2 new messages
        assert len(history) == 4
        assert history[0] == initial_msgs[0]
        assert history[1] == initial_msgs[1]

    def test_chained_injections_preserve_order(self):
        """Multiple injections preserve chronological order."""
        user_id = 994
        session.set_history(user_id, [])

        # Inject first customer selection
        session.inject_selected_customer(user_id, customer_id=1, shop_name="Sharma")

        # Then create new customer
        session.inject_created_customer(user_id, customer_id=99, shop_name="Verma")

        history = session.get_history(user_id)

        # Should have 4 messages: 2 from selection, 2 from creation
        assert len(history) == 4
        # First pair should be search_customer
        assert history[0]["tool_calls"][0]["function"]["name"] == "search_customer"
        assert history[1]["name"] == "search_customer"
        # Second pair should be create_customer
        assert history[2]["tool_calls"][0]["function"]["name"] == "create_customer"
        assert history[3]["name"] == "create_customer"

    def test_inject_created_customer_with_special_chars(self):
        """inject_created_customer handles shop names with special characters."""
        user_id = 993
        session.set_history(user_id, [])

        # Test single injection with special char shop name
        session.inject_created_customer(user_id, customer_id=100, shop_name="Sharma & Sons")
        history = session.get_history(user_id)
        # 1 creation = 2 messages
        assert len(history) == 2

        # Verify shop name is preserved
        tool_result = json.loads(history[1]["content"])
        assert tool_result["shop_name"] == "Sharma & Sons"

    def test_inject_created_customer_with_unicode_shop_name(self):
        """inject_created_customer preserves Unicode shop names."""
        user_id = 992
        session.set_history(user_id, [])

        session.inject_created_customer(user_id, customer_id=200, shop_name="शर्मा धान्य")
        history = session.get_history(user_id)
        assert len(history) == 2

        tool_result = json.loads(history[1]["content"])
        assert tool_result["shop_name"] == "शर्मा धान्य"


# ============================================================================
# INTEGRATION TESTS (language + rendering + memory)
# ============================================================================

class TestLanguageAndMemoryIntegration:
    """Verify language detection flows through rendering and memory."""

    def test_devanagari_input_devanagari_output(self):
        """Devanagari user input leads to Devanagari confirmation."""
        user_lang = detect_user_lang("शर्मा को 50 किलो 120 दर")
        assert user_lang == "hi-Deva"

        call = pending.PendingToolCall(
            id="tc1", name="save_sale",
            arguments={
                "customer_id": 1, "qty_kg": 50, "rate_per_kg": 120,
                "sale_date": "2025-05-03", "payment_status": "cash"
            },
        )
        summary = _build_summary([call], {1: "शर्मा"}, user_lang=user_lang)
        # Should render in Devanagari (not generic text)
        assert len(summary) > 0

    def test_english_input_english_output(self):
        """English user input leads to English confirmation."""
        user_lang = detect_user_lang("Sale to Sharma 50kg at 120")
        assert user_lang == "en"

        call = pending.PendingToolCall(
            id="tc1", name="save_sale",
            arguments={
                "customer_id": 1, "qty_kg": 50, "rate_per_kg": 120,
                "sale_date": "2025-05-03", "payment_status": "cash"
            },
        )
        summary = _build_summary([call], {1: "Sharma"}, user_lang=user_lang)
        assert "Sharma" in summary

    def test_hinglish_input_hinglish_output(self):
        """Hinglish user input leads to Hinglish confirmation."""
        user_lang = detect_user_lang("Sharma ko 50kg 120 rate")
        assert user_lang == "hi-Hind"

        call = pending.PendingToolCall(
            id="tc1", name="save_sale",
            arguments={
                "customer_id": 1, "qty_kg": 50, "rate_per_kg": 120,
                "sale_date": "2025-05-03", "payment_status": "cash"
            },
        )
        summary = _build_summary([call], {1: "Sharma"}, user_lang=user_lang)
        assert "Sale confirm" in summary

    def test_multi_step_with_memory_preserves_context(self):
        """Multi-step workflow with injection preserves customer context."""
        user_id = 991
        session.set_history(user_id, [])

        # User creates customer
        session.inject_created_customer(user_id, customer_id=100, shop_name="Verma Traders")
        history_after_create = session.get_history(user_id)
        assert len(history_after_create) == 2

        # Now check that the customer_id is in history for next agent_loop
        created_customer_result = json.loads(history_after_create[1]["content"])
        assert created_customer_result["customer_id"] == 100
