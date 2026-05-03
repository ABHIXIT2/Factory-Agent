"""Tests for src/pending.py — pending action store."""

import pytest

from src import pending


@pytest.fixture
def action():
	"""Create a sample pending action."""
	return pending.PendingAction(
		user_id=1,
		assistant_message={"role": "assistant", "content": "test"},
		tool_calls=[
			pending.PendingToolCall(id="tc1", name="save_sale", arguments={"qty_kg": 10}),
		],
		summary="Test summary",
	)


def test_put_returns_token(action):
	"""put() returns an opaque token."""
	token = pending.put(action)
	assert isinstance(token, str)
	assert len(token) > 10  # token_urlsafe(12) is reasonably long


def test_pop_returns_action(action):
	"""pop() returns the action for the correct user."""
	token = pending.put(action)
	result = pending.pop(token, user_id=1)
	assert result is not None
	assert result.user_id == 1
	assert result.summary == "Test summary"


def test_pop_rejects_cross_user():
	"""pop() rejects tokens from other users."""
	action = pending.PendingAction(
		user_id=1,
		assistant_message={},
		tool_calls=[],
		summary="",
	)
	token = pending.put(action)
	result = pending.pop(token, user_id=999)
	assert result is None


def test_pop_is_one_shot(action):
	"""pop() removes the token — second pop() returns None."""
	token = pending.put(action)
	result1 = pending.pop(token, user_id=1)
	assert result1 is not None
	result2 = pending.pop(token, user_id=1)
	assert result2 is None


def test_peek_non_destructive(action):
	"""peek() returns the action without removing it."""
	token = pending.put(action)
	result1 = pending.peek(token)
	assert result1 is not None
	result2 = pending.peek(token)
	assert result2 is not None


def test_clear_user_removes_all(action):
	"""clear_user() removes all actions for a user."""
	token1 = pending.put(action)
	action2 = pending.PendingAction(user_id=1, assistant_message={}, tool_calls=[], summary="")
	token2 = pending.put(action2)
	action_other = pending.PendingAction(user_id=999, assistant_message={}, tool_calls=[], summary="")
	pending.put(action_other)

	cleared = pending.clear_user(1)
	assert cleared == 2

	assert pending.pop(token1, user_id=1) is None
	assert pending.pop(token2, user_id=1) is None
	assert pending.pop("fake_token", user_id=999) is None  # Other user's action unaffected
