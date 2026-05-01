"""
Telegram bot handlers: message routing, commands, inline buttons, callback handlers.
"""

import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, MessageHandler, CommandHandler, CallbackQueryHandler, ContextTypes, filters
)
from src.config import TELEGRAM_BOT_TOKEN
from src.agent import agent_loop, clear_history
from src import db

logger = logging.getLogger(__name__)


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /start command."""
    user = update.effective_user
    user_id = user.id
    username = user.username or ""
    first_name = user.first_name or ""

    # Register user in database
    db.init_user(user_id, username, first_name)

    welcome_text = f"""🏭 *Factory Agent* activated!

नमस्ते *{first_name}*! I'm your factory operations assistant.

I can help you with:
- 📦 Sales & customer credit
- 💰 Production & cash flow
- 💳 Outstanding balances

Just send me a message in Hindi, Hinglish, or English.

Type /help for available commands.
"""
    await update.message.reply_text(welcome_text, parse_mode="Markdown")
    logger.info(f"✅ User {user_id} ({first_name}) started bot")


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /help command."""
    help_text = """*Available Commands*

/start — Initialize bot
/help — Show this help
/status — Check database connection
/clear — Clear conversation history

*Quick Examples*

"Sharma को 50 kg दिया 120 रेट पर"
(Record sale: 50 kg @ ₹120 to Sharma)

"बकाया देखो" or "Show balances"
(Get all outstanding balances)

"Gupta की payment 5000 दी"
(Record payment: ₹5,000 from Gupta)

Just send a message — I'll ask if I need more info.
"""
    await update.message.reply_text(help_text, parse_mode="Markdown")


async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /status command."""
    is_connected = db.test_connection()
    status = "✅ Connected" if is_connected else "❌ Disconnected"
    await update.message.reply_text(f"Database: {status}")
    logger.info(f"Status check: {status}")


async def clear_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /clear command — clears conversation history."""
    user_id = update.effective_user.id
    clear_history(user_id)
    await update.message.reply_text("✅ Conversation history cleared.")
    logger.info(f"Cleared history for user {user_id}")


async def handle_text_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Main text message handler.
    Sends to agent_loop for parsing, tool-calling, and response.
    """
    user = update.effective_user
    user_id = user.id
    username = user.username or ""
    first_name = user.first_name or ""
    user_message = update.message.text

    logger.info(f"Message from {user_id} ({first_name}): {user_message[:50]}")

    # Show typing indicator
    await context.bot.send_chat_action(chat_id=user_id, action="typing")

    try:
        # Call agent loop
        response = await agent_loop(
            user_message=user_message,
            user_id=user_id,
            username=username,
            first_name=first_name
        )

        # Send response
        # Use parse_mode="Markdown" to support bold, italic, links
        # Use disable_web_page_preview to suppress link previews in confirmations
        await update.message.reply_text(
            response,
            parse_mode="Markdown",
            disable_web_page_preview=True
        )

        logger.debug(f"Response sent to {user_id}: {response[:50]}...")

    except Exception as e:
        logger.error(f"Error handling message from {user_id}: {e}", exc_info=True)
        await update.message.reply_text(
            f"❌ Sorry, something went wrong. Please try again.\n\nError: {str(e)[:100]}"
        )


async def handle_voice_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Handle voice messages (v2 feature).
    For now, send a placeholder response.
    """
    user_id = update.effective_user.id
    await update.message.reply_text(
        "🎤 Voice messages will be supported in v2. For now, please send text."
    )
    logger.info(f"Voice message from {user_id} (v2 feature, not yet implemented)")


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Log errors.
    """
    logger.error(f"Update {update} caused error {context.error}")


def build_app() -> Application:
    """
    Build and configure the Telegram bot application.
    """
    # Create application
    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    # Command handlers
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("status", status_command))
    app.add_handler(CommandHandler("clear", clear_command))

    # Message handlers
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text_message))
    app.add_handler(MessageHandler(filters.VOICE, handle_voice_message))

    # Error handler
    app.add_error_handler(error_handler)

    return app
