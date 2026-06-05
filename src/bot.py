"""
Telegram bot handlers: commands, text routing, confirmation callbacks.

User-facing errors are generic — full tracebacks go to logs only.
"""

import asyncio
import contextlib
import logging

from telegram import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.error import TelegramError
from telegram.ext import (
    Application, CallbackQueryHandler, CommandHandler, ContextTypes,
    MessageHandler, filters,
)

from src.config import TELEGRAM_BOT_TOKEN
from src.agent import (
    AgentResult, agent_loop, cancel_pending, clear_history,
    continue_after_confirmation,
)
from src.session import inject_selected_customer
from src.messages import render
from src.utils import detect_user_lang
from src import db, pending, selection
from src import logger as log_utils

logger = logging.getLogger(__name__)


# ----------------------------------------------------------------------------
# Commands
# ----------------------------------------------------------------------------

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    user_id = user.id
    first_name = user.first_name or ""

    try:
        await asyncio.to_thread(db.init_user, user_id, user.username or "", first_name)
    except Exception:
        logger.exception("init_user failed for %s", user_id)

    welcome_text = render("commands", "start", "hi-Hind", first_name=first_name)
    await update.message.reply_text(welcome_text, parse_mode="Markdown")
    logger.info("User %s (%s) started bot", user_id, first_name)


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    help_text = render("commands", "help", "hi-Hind")
    await update.message.reply_text(help_text, parse_mode="Markdown")


async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    is_connected = await asyncio.to_thread(db.test_connection)
    key = "status_connected" if is_connected else "status_disconnected"
    status_text = render("commands", key, "hi-Hind")
    await update.message.reply_text(status_text)


async def clear_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    clear_history(user_id)
    clear_text = render("system", "history_cleared", "hi-Hind")
    await update.message.reply_text(clear_text)


# ----------------------------------------------------------------------------
# Confirmation rendering
# ----------------------------------------------------------------------------

def _confirm_keyboard(token: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Haan, save", callback_data=f"cf:y:{token}"),
        InlineKeyboardButton("❌ Nahi", callback_data=f"cf:n:{token}"),
    ]])


def _selection_keyboard(token: str, options: list[dict[str, str]]) -> InlineKeyboardMarkup:
    """Build a keyboard with one button per row for customer selection."""
    buttons = [
        [InlineKeyboardButton(f"{i}. {opt['shop_name']}", callback_data=f"sel:{token}:{i-1}")]
        for i, opt in enumerate(options, 1)
    ]
    return InlineKeyboardMarkup(buttons)


async def _send_agent_result(update: Update, result: AgentResult) -> None:
    """Send the agent's response, attaching confirmation or selection buttons if present."""
    text = result.text
    reply_markup = None
    if result.confirmation is not None:
        if getattr(result.confirmation, '_is_selection', False):
            # This is a customer selection prompt, not a write confirmation
            sel = selection.peek(result.confirmation.token)
            if sel:
                options = [{"shop_name": opt.shop_name} for opt in sel.customer_options]
                reply_markup = _selection_keyboard(result.confirmation.token, options)
        else:
            # Regular write confirmation
            reply_markup = _confirm_keyboard(result.confirmation.token)

    target = update.effective_message
    try:
        await target.reply_text(
            text,
            parse_mode="Markdown",
            disable_web_page_preview=True,
            reply_markup=reply_markup,
        )
    except Exception as exc:
        logger.debug("Markdown send failed: %s, retrying plain", exc)
        await target.reply_text(
            text, disable_web_page_preview=True, reply_markup=reply_markup,
        )


# ----------------------------------------------------------------------------
# Text handler
# ----------------------------------------------------------------------------

async def handle_text_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    user_id = user.id
    first_name = user.first_name or ""
    user_message = (update.message.text or "")[:4000]

    with contextlib.suppress(TelegramError):
        await context.bot.send_chat_action(chat_id=user_id, action="typing")

    try:
        result = await agent_loop(
            user_message=user_message,
            user_id=user_id,
            username=user.username or "",
            first_name=first_name,
        )

        if result.confirmation is None and "labbu" in user_message.lower():
            result = AgentResult(text=result.text + " ❤️", confirmation=None)

        await _send_agent_result(update, result)

    except Exception:
        logger.exception("Error handling message from %s", user_id)
        await update.message.reply_text("❌ Sorry, something went wrong. Please try again.")


async def _handle_expired_token(
    query: CallbackQuery,
    context: ContextTypes.DEFAULT_TYPE,
    user_id: int,
    message: str,
) -> None:
    """Handle expired or already-consumed tokens uniformly."""
    try:
        await query.edit_message_reply_markup(reply_markup=None)
        await query.edit_message_text(
            (query.message.text_markdown_v2 or query.message.text or "") + f"\n\n{message}"
        )
    except TelegramError as exc:
        logger.debug("Failed to edit expired-token message: %s", exc)
        await context.bot.send_message(chat_id=user_id, text=message)


# Callback handler for selection buttons (customer selection)

async def selection_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle customer selection from fuzzy search results."""
    query = update.callback_query
    user_id = query.from_user.id

    try:
        await query.answer()
    except Exception:
        pass

    data = query.data or ""
    parts = data.split(":", 2)
    if len(parts) != 3 or parts[0] != "sel":
        return

    token, index_str = parts[1], parts[2]
    try:
        index = int(index_str)
    except (ValueError, IndexError):
        return

    sel_peek = selection.peek(token)
    if sel_peek is None:
        expired_msg = render("system", "selection_expired", "hi-Hind")
        await _handle_expired_token(query, context, user_id, expired_msg)
        return

    # Validate index before consuming the token so a bad tap can be retried
    if index < 0 or index >= len(sel_peek.customer_options):
        user_lang = sel_peek.extras.get("user_lang", "hi-Hind")
        invalid_msg = render("system", "invalid_selection", user_lang)
        await context.bot.send_message(chat_id=user_id, text=invalid_msg)
        return

    sel = selection.pop(token, user_id)
    if sel is None:
        expired_msg = render("system", "selection_expired", "hi-Hind")
        await _handle_expired_token(query, context, user_id, expired_msg)
        return

    selected_customer = sel.customer_options[index]

    # Strip buttons immediately
    with contextlib.suppress(TelegramError):
        await query.edit_message_reply_markup(reply_markup=None)

    # Inject selected customer into history so LLM uses the correct customer_id
    inject_selected_customer(user_id, selected_customer.id, selected_customer.shop_name)

    # Re-run the original message — LLM now sees the customer in history
    try:
        result = await agent_loop(
            user_message=sel.original_message,
            user_id=user_id,
            username=sel.username,
            first_name=sel.first_name,
        )
        await _send_agent_result(update, result)
    except Exception:
        logger.exception("Selection follow-up failed for user %s", user_id)
        user_lang = sel.extras.get("user_lang", "hi-Hind")
        error_msg = render("system", "generic_error", user_lang)
        await context.bot.send_message(chat_id=user_id, text=error_msg)


# ----------------------------------------------------------------------------
# Callback handler for confirmation buttons
# ----------------------------------------------------------------------------

async def confirmation_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    user_id = query.from_user.id

    with contextlib.suppress(TelegramError):
        await query.answer()

    data = query.data or ""
    parts = data.split(":", 2)
    if len(parts) != 3 or parts[0] != "cf":
        return

    decision, token = parts[1], parts[2]
    action = pending.pop(token, user_id)

    if action is None:
        expired_msg = render("system", "confirmation_expired", "hi-Hind")
        await _handle_expired_token(query, context, user_id, expired_msg)
        return

    # Strip buttons immediately so the user can't double-click.
    with contextlib.suppress(TelegramError):
        await query.edit_message_reply_markup(reply_markup=None)

    try:
        if decision == "y":
            result = await continue_after_confirmation(user_id, action)
        else:
            result = await cancel_pending(user_id, action)
    except Exception:
        logger.exception("Confirmation handling failed for user %s", user_id)
        user_lang = (action.extras or {}).get("user_lang", "hi-Hind")
        error_msg = render("system", "generic_error", user_lang)
        await context.bot.send_message(chat_id=user_id, text=error_msg)
        return

    markup = _confirm_keyboard(result.confirmation.token) if result.confirmation else None
    try:
        await context.bot.send_message(
            chat_id=user_id,
            text=result.text,
            parse_mode="Markdown",
            disable_web_page_preview=True,
            reply_markup=markup,
        )
    except Exception:
        await context.bot.send_message(chat_id=user_id, text=result.text, reply_markup=markup)


# ----------------------------------------------------------------------------
# Errors / wiring
# ----------------------------------------------------------------------------

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.error("Update %s caused error %s", update, context.error, exc_info=context.error)


def build_app() -> Application:
    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("status", status_command))
    app.add_handler(CommandHandler("clear", clear_command))

    app.add_handler(CallbackQueryHandler(selection_callback, pattern=r"^sel:"))
    app.add_handler(CallbackQueryHandler(confirmation_callback, pattern=r"^cf:(y|n):"))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text_message))

    app.add_error_handler(error_handler)
    return app
