"""
Factory Agent — Namkeen Factory Operations Bot
Entry point: initializes database, Telegram bot, and agent loop.
"""

import asyncio
import logging
from src.bot import build_app
from src import db

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


async def main():
    """Main entry point."""
    logger.info("🏭 Starting Factory Agent...")

    # Test database connection
    if not db.test_connection():
        logger.error("❌ Database connection failed. Check env vars.")
        raise RuntimeError("Database connection failed")

    # Build and start bot
    app = build_app()
    logger.info("✅ Bot configured. Starting polling...")

    try:
        await app.run_polling()
    except KeyboardInterrupt:
        logger.info("🛑 Bot stopped by user.")
    except Exception as e:
        logger.error(f"💥 Fatal error: {e}", exc_info=True)
        raise


if __name__ == "__main__":
    asyncio.run(main())
