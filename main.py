"""
Factory Agent — Namkeen Factory Operations Bot.

Entry point: validates the DB connection, builds the Telegram Application,
and starts long-polling. Logs are kept terse and secret-free (httpx INFO
logs are silenced because they include the bot token in request URLs).
"""

import logging
import signal
import sys

from src.bot import build_app
from src import db

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)

# httpx logs full URLs (which contain the bot token) at INFO. Mute it.
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

logger = logging.getLogger(__name__)


def _stop_signals():
    """
    POSIX has SIGINT + SIGTERM; Windows asyncio loops don't implement
    add_signal_handler for SIGTERM, so PTB warns and falls back. On Windows
    we let PTB handle Ctrl-C itself by passing None.
    """
    if sys.platform == "win32":
        return None
    return (signal.SIGINT, signal.SIGTERM)


def main() -> int:
    logger.info("Starting Labbu...")

    if not db.test_connection():
        logger.error("Database connection failed. Check env vars.")
        return 1

    app = build_app()
    logger.info("Bot configured. Starting polling...")

    try:
        app.run_polling(stop_signals=_stop_signals())
    except Exception:
        logger.exception("Fatal error in polling loop")
        return 2

    logger.info("Bot stopped.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
