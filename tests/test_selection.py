"""Tests for src/selection.py — customer selection store."""

import pytest

from src import selection


@pytest.fixture
def sel():
	"""Create a sample selection."""
	return selection.PendingSelection(
		user_id=1,
		original_message="Sharma ko sale karo",
		username="testuser",
		first_name="Test",
		customer_options=[
			selection.CustomerOption(id=1, shop_name="Sharma"),
			selection.CustomerOption(id=2, shop_name="Sharma & Bros"),
		],
	)


def test_put_returns_token(sel):
	"""put() returns an opaque token."""
	token = selection.put(sel)
	assert isinstance(token, str)
	assert len(token) > 10


def test_pop_returns_selection(sel):
	"""pop() returns the selection for the correct user."""
	token = selection.put(sel)
	result = selection.pop(token, user_id=1)
	assert result is not None
	assert result.user_id == 1
	assert result.original_message == "Sharma ko sale karo"
	assert len(result.customer_options) == 2


def test_pop_rejects_cross_user():
	"""pop() rejects tokens from other users."""
	sel = selection.PendingSelection(
		user_id=1,
		original_message="test",
		username="",
		first_name="",
		customer_options=[],
	)
	token = selection.put(sel)
	result = selection.pop(token, user_id=999)
	assert result is None


def test_pop_is_one_shot(sel):
	"""pop() removes the token — second pop() returns None."""
	token = selection.put(sel)
	result1 = selection.pop(token, user_id=1)
	assert result1 is not None
	result2 = selection.pop(token, user_id=1)
	assert result2 is None


def test_peek_non_destructive(sel):
	"""peek() returns the selection without removing it."""
	token = selection.put(sel)
	result1 = selection.peek(token)
	assert result1 is not None
	result2 = selection.peek(token)
	assert result2 is not None


def test_clear_user_removes_all(sel):
	"""clear_user() removes all selections for a user."""
	token1 = selection.put(sel)
	sel2 = selection.PendingSelection(
		user_id=1, original_message="test2", username="", first_name="", customer_options=[],
	)
	token2 = selection.put(sel2)

	cleared = selection.clear_user(1)
	assert cleared == 2

	assert selection.pop(token1, user_id=1) is None
	assert selection.pop(token2, user_id=1) is None
