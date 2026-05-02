"""
Telegram bot handlers: commands, text routing, confirmation callbacks.

User-facing errors are generic — full tracebacks go to logs only.
"""

import asyncio
import logging

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application, CallbackQueryHandler, CommandHandler, ContextTypes,
    MessageHandler, filters,
)

from src.config import TELEGRAM_BOT_TOKEN
from src.agent import (
    AgentResult, agent_loop, cancel_pending, clear_history,
    continue_after_confirmation,
)
from src import db, pending

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

    welcome_text = (
        "🏭 *Labbu* activated!\n\n"
        f"नमस्ते *{first_name}*! I'm Labbu, your factory operations assistant.\n\n"
        "I can help you with:\n"
        "- 📦 Sales & customer credit\n"
        "- 💰 Production & cash flow\n"
        "- 💳 Outstanding balances\n\n"
        "Just send me a message in Hindi, Hinglish, or English.\n\n"
        "Type /help for available commands.\n"
    )
    await update.message.reply_text(welcome_text, parse_mode="Markdown")
    logger.info("User %s (%s) started bot", user_id, first_name)


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    help_text = (
        "*Available Commands*\n\n"
        "/start — Initialize bot\n"
        "/help — Show this help\n"
        "/status — Check database connection\n"
        "/clear — Clear conversation history\n\n"
        "*Quick Examples*\n\n"
        "\"Sharma को 50 kg दिया 120 रेट पर\"\n"
        "(Record sale: 50 kg @ ₹120 to Sharma)\n\n"
        "\"बकाया देखो\" or \"Show balances\"\n"
        "(Get all outstanding balances)\n\n"
        "\"Gupta की payment 5000 दी\"\n"
        "(Record payment: ₹5,000 from Gupta)\n"
    )
    await update.message.reply_text(help_text, parse_mode="Markdown")


async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    is_connected = await asyncio.to_thread(db.test_connection)
    status = "✅ Connected" if is_connected else "❌ Disconnected"
    await update.message.reply_text(f"Database: {status}")


async def clear_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    clear_history(user_id)
    await update.message.reply_text("✅ Conversation history cleared.")


# ----------------------------------------------------------------------------
# Confirmation rendering
# ----------------------------------------------------------------------------

def _confirm_keyboard(token: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Haan, save", callback_data=f"cf:y:{token}"),
        InlineKeyboardButton("❌ Nahi", callback_data=f"cf:n:{token}"),
    ]])


async def _send_agent_result(update: Update, result: AgentResult) -> None:
    """Send the agent's response, attaching confirmation buttons if present."""
    text = result.text
    reply_markup = None
    if result.confirmation is not None:
        reply_markup = _confirm_keyboard(result.confirmation.token)

    target = update.effective_message
    try:
        await target.reply_text(
            text,
            parse_mode="Markdown",
            disable_web_page_preview=True,
            reply_markup=reply_markup,
        )
    except Exception:
        logger.exception("Markdown send failed; retrying plain")
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

    logger.info("msg user=%s len=%s preview=%r", user_id, len(user_message), user_message[:60])

    try:
        await context.bot.send_chat_action(chat_id=user_id, action="typing")
    except Exception:
        pass

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


# ----------------------------------------------------------------------------
# Callback handler for confirmation buttons
# ----------------------------------------------------------------------------

async def confirmation_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    user_id = query.from_user.id

    try:
        await query.answer()
    except Exception:
        pass

    data = query.data or ""
    parts = data.split(":", 2)
    if len(parts) != 3 or parts[0] != "cf":
        return

    decision, token = parts[1], parts[2]
    action = pending.pop(token, user_id)

    if action is None:
        try:
            await query.edit_message_reply_markup(reply_markup=None)
            await query.edit_message_text(
                (query.message.text_markdown_v2 or query.message.text or "")
                + "\n\n⏱️ Confirmation expired ya already handled."
            )
        except Exception:
            await context.bot.send_message(
                chat_id=user_id, text="⏱️ Confirmation expired ya already handled."
            )
        return

    # Strip buttons immediately so the user can't double-click.
    try:
        await query.edit_message_reply_markup(reply_markup=None)
    except Exception:
        pass

    try:
        if decision == "y":
            result = await continue_after_confirmation(user_id, action)
        else:
            result = await cancel_pending(user_id, action)
    except Exception:
        logger.exception("Confirmation handling failed for user %s", user_id)
        await context.bot.send_message(
            chat_id=user_id, text="❌ Sorry, something went wrong. Please try again."
        )
        return

    try:
        await context.bot.send_message(
            chat_id=user_id,
            text=result.text,
            parse_mode="Markdown",
            disable_web_page_preview=True,
        )
    except Exception:
        await context.bot.send_message(chat_id=user_id, text=result.text)


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

    app.add_handler(CallbackQueryHandler(confirmation_callback, pattern=r"^cf:(y|n):"))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text_message))

    app.add_error_handler(error_handler)
    return app
