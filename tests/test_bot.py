"""Tests for src/bot.py — Telegram callback handlers."""

import types
from unittest.mock import AsyncMock

from src import agent
from src.bot import _send_agent_result, _handle_expired_token


# Helpers to build fake Telegram objects

def _fake_user(user_id=1, first_name="Test", username="testuser"):
	return types.SimpleNamespace(id=user_id, first_name=first_name, username=username)


def _fake_message(text="test"):
	msg = AsyncMock()
	msg.text = text
	msg.reply_text = AsyncMock()
	return msg


def _fake_update(user_id=1, message_text="test"):
	message = _fake_message(text=message_text)
	update = types.SimpleNamespace(
		effective_user=_fake_user(user_id=user_id),
		message=message,
		effective_message=message,
	)
	return update


def _fake_callback_query(token, user_id=1):
	query = types.SimpleNamespace(
		from_user=_fake_user(user_id=user_id),
		data=f"cf:y:{token}",
		message=types.SimpleNamespace(
			text="test confirmation",
			text_markdown_v2="test confirmation",
		),
	)
	query.answer = AsyncMock()
	query.edit_message_reply_markup = AsyncMock()
	query.edit_message_text = AsyncMock()
	return query


def _fake_context():
	ctx = types.SimpleNamespace()
	ctx.bot = AsyncMock()
	ctx.bot.send_chat_action = AsyncMock()
	ctx.bot.send_message = AsyncMock()
	return ctx


# Tests

async def test_send_agent_result_plain_text():
	"""Result with no confirmation → plain reply."""
	update = _fake_update()
	result = agent.AgentResult(text="Done", confirmation=None)

	await _send_agent_result(update, result)

	assert update.message.reply_text.called


async def test_send_agent_result_with_confirmation():
	"""Result with confirmation → reply with keyboard."""
	update = _fake_update()
	confirmation = agent.Confirmation(token="abc123", summary="Confirm this?")
	result = agent.AgentResult(text="Confirm", confirmation=confirmation)

	await _send_agent_result(update, result)

	# Should have called reply_text with a keyboard markup
	assert update.message.reply_text.called
	call_kwargs = update.message.reply_text.call_args[1]
	assert "reply_markup" in call_kwargs


async def test_send_agent_result_markdown_fallback():
	"""Markdown send fails → retry with plain text."""
	update = _fake_update()
	result = agent.AgentResult(text="Test", confirmation=None)

	# First reply_text raises, second succeeds
	update.message.reply_text.side_effect = [Exception("Markdown failed"), None]

	await _send_agent_result(update, result)

	# Should have called reply_text twice
	assert update.message.reply_text.call_count == 2


async def test_handle_expired_token():
	"""_handle_expired_token edits message or sends fallback."""
	query = _fake_callback_query("fake")
	context = _fake_context()

	await _handle_expired_token(query, context, user_id=1, message="Token expired")

	# Should have tried to edit the message or send message
	assert query.edit_message_reply_markup.called or context.bot.send_message.called


async def test_handle_expired_token_fallback():
	"""_handle_expired_token falls back to send_message on edit failure."""
	query = _fake_callback_query("fake")
	from telegram.error import TelegramError
	query.edit_message_text.side_effect = TelegramError("Edit failed")
	context = _fake_context()

	await _handle_expired_token(query, context, user_id=1, message="Expired")

	# Should have fallen back to send_message
	assert context.bot.send_message.called
