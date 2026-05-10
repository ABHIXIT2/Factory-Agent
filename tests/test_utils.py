"""Tests for src/utils.py — date parsing, validators, formatting."""

import pytest

from src.utils import (
    AmbiguousDateError, format_amount, parse_date_flexible,
    sanitize_name_fragment, truncate, validate_enum, validate_iso_date,
    validate_positive_int, validate_positive_number, redact_secrets,
    detect_user_lang,
)


# ---------------- parse_date_flexible ----------------

def test_parse_date_today_aliases():
    assert parse_date_flexible("today")
    assert parse_date_flexible("aaj")
    assert parse_date_flexible("आज")


def test_parse_date_iso_passthrough():
    assert parse_date_flexible("2024-12-31") == "2024-12-31"


def test_parse_date_dmy_slashes():
    assert parse_date_flexible("31/12/2024") == "2024-12-31"


def test_parse_date_invalid_returns_none():
    assert parse_date_flexible("not a date") is None
    assert parse_date_flexible("") is None
    assert parse_date_flexible("2024-13-99") is None


def test_parse_date_kal_is_ambiguous():
    with pytest.raises(AmbiguousDateError):
        parse_date_flexible("kal")
    with pytest.raises(AmbiguousDateError):
        parse_date_flexible("कल")


# ---------------- validate_iso_date ----------------

def test_validate_iso_date_ok():
    assert validate_iso_date("2024-01-15", "d") == "2024-01-15"


def test_validate_iso_date_rejects_garbage():
    with pytest.raises(ValueError):
        validate_iso_date("yesterday", "d")
    with pytest.raises(ValueError):
        validate_iso_date(20240115, "d")  # type: ignore


# ---------------- validate_positive_number ----------------

def test_positive_number_basic():
    assert validate_positive_number("12.5", "x") == 12.5
    assert validate_positive_number(7, "x") == 7.0


def test_positive_number_rejects_zero_negative_nan_inf():
    for bad in (0, -1, "-3.14", "nan", "inf", "abc", None):
        with pytest.raises(ValueError):
            validate_positive_number(bad, "x")


def test_positive_number_allow_zero():
    assert validate_positive_number(0, "x", allow_zero=True) == 0.0
    with pytest.raises(ValueError):
        validate_positive_number(-0.01, "x", allow_zero=True)


# ---------------- validate_positive_int ----------------

def test_positive_int_ok():
    assert validate_positive_int("5", "n") == 5


def test_positive_int_rejects_floats_and_zero():
    with pytest.raises(ValueError):
        validate_positive_int(0, "n")
    with pytest.raises(ValueError):
        validate_positive_int("abc", "n")


# ---------------- validate_enum ----------------

def test_enum_ok_and_reject():
    assert validate_enum("paid", {"paid", "credited"}, "s") == "paid"
    with pytest.raises(ValueError):
        validate_enum("partial", {"paid", "credited"}, "s")


# ---------------- sanitize_name_fragment ----------------

def test_sanitize_strips_like_wildcards():
    assert sanitize_name_fragment("Sharma%") == "Sharma"
    assert sanitize_name_fragment("Gup_ta") == "Gup ta"


def test_sanitize_rejects_empty_and_too_long():
    with pytest.raises(ValueError):
        sanitize_name_fragment("")
    with pytest.raises(ValueError):
        sanitize_name_fragment("x" * 101)


def test_sanitize_rejects_control_chars():
    with pytest.raises(ValueError):
        sanitize_name_fragment("ab\x00cd")


def test_sanitize_allows_unicode_names():
    assert sanitize_name_fragment("शर्मा") == "शर्मा"


# ---------------- truncate / format_amount ----------------

def test_truncate():
    assert truncate(None, 5) is None
    assert truncate("abcdefgh", 3) == "abc"


def test_format_amount():
    assert format_amount(1234.5) == "₹1,234.50"
    assert format_amount(None) == "₹0.00"
    assert format_amount("not a number") == "₹0.00"


# ---------------- redact_secrets ----------------

def test_redact_secrets_telegram_token():
    text = "Error calling 123456789:ABCdefGHIJKlmnOpQrsTUVWxyz"
    redacted = redact_secrets(text)
    assert "[REDACTED_TOKEN]" in redacted
    assert "123456789:ABC" not in redacted


def test_redact_secrets_groq_key():
    text = "Failed with key gsk-aBcDeFgHiJkLmNoPqRsTuVwXyZ12345"
    redacted = redact_secrets(text)
    assert "[REDACTED_KEY]" in redacted
    assert "gsk-aBcDeF" not in redacted


def test_redact_secrets_supabase_url():
    text = "Connected to https://myproject-abcd1234.supabase.co"
    redacted = redact_secrets(text)
    assert "[REDACTED_URL]" in redacted
    assert "supabase.co" not in redacted


def test_redact_secrets_leaves_normal_text():
    text = "Normal error message with no secrets"
    assert redact_secrets(text) == text


# ---------------- detect_user_lang ----------------

def test_detect_user_lang_devanagari():
    assert detect_user_lang("शर्मा को 50 किलो दे दो") == "hi-Deva"


def test_detect_user_lang_hinglish_roman():
    assert detect_user_lang("Sharma ko 50kg de do") == "hi-Hind"


def test_detect_user_lang_english():
    assert detect_user_lang("Show me the balances") == "en"


def test_detect_user_lang_empty_falls_back_to_latn():
    assert detect_user_lang("") == "hi-Hind"
    assert detect_user_lang(None) == "hi-Hind"


def test_detect_user_lang_mixed_picks_devanagari():
    # Even one Devanagari char tips the decision.
    assert detect_user_lang("Sharma को 50kg de do") == "hi-Deva"
