"""Tests for src/token_store.py — generic token store."""

from dataclasses import dataclass

import pytest

from src.token_store import TokenStore


@dataclass
class Payload:
	"""Minimal payload with user_id for testing TokenStore."""
	user_id: int
	data: str


@pytest.fixture
def store():
	"""Create a test TokenStore."""
	return TokenStore(ttl=3600, maxsize=100)


def test_put_returns_token(store):
	"""put() returns a token."""
	payload = Payload(user_id=1, data="test")
	token = store.put(payload)
	assert isinstance(token, str)
	assert len(token) > 10


def test_pop_returns_payload(store):
	"""pop() returns the payload for the correct user."""
	payload = Payload(user_id=1, data="test")
	token = store.put(payload)
	result = store.pop(token, user_id=1)
	assert result is not None
	assert result.user_id == 1
	assert result.data == "test"


def test_pop_rejects_wrong_user(store):
	"""pop() rejects tokens from other users."""
	payload = Payload(user_id=1, data="test")
	token = store.put(payload)
	result = store.pop(token, user_id=999)
	assert result is None


def test_pop_is_one_shot(store):
	"""pop() removes the token."""
	payload = Payload(user_id=1, data="test")
	token = store.put(payload)
	result1 = store.pop(token, user_id=1)
	assert result1 is not None
	result2 = store.pop(token, user_id=1)
	assert result2 is None


def test_peek_non_destructive(store):
	"""peek() returns payload without removing."""
	payload = Payload(user_id=1, data="test")
	token = store.put(payload)
	result1 = store.peek(token)
	assert result1 is not None
	result2 = store.peek(token)
	assert result2 is not None


def test_clear_user_removes_all(store):
	"""clear_user() removes all payloads for a user."""
	token1 = store.put(Payload(user_id=1, data="a"))
	token2 = store.put(Payload(user_id=1, data="b"))
	store.put(Payload(user_id=999, data="c"))  # Other user

	cleared = store.clear_user(1)
	assert cleared == 2

	assert store.pop(token1, user_id=1) is None
	assert store.pop(token2, user_id=1) is None


def test_clear_all(store):
	"""clear() removes all payloads."""
	store.put(Payload(user_id=1, data="a"))
	store.put(Payload(user_id=2, data="b"))

	store.clear()

	assert store.peek("any_token") is None
