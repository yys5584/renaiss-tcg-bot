"""Standalone Renaiss Edition bot entrypoint."""

from __future__ import annotations

import logging
import os

from dotenv import load_dotenv
from telegram import BotCommand
from telegram.ext import Application

from renaiss_bot.handlers.register import register_handlers

load_dotenv()

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=getattr(logging, os.getenv("LOG_LEVEL", "INFO")),
)
for _logger_name in ("httpx", "httpcore"):
    logging.getLogger(_logger_name).setLevel(logging.WARNING)
logger = logging.getLogger(__name__)


async def _configure_commands(application: Application) -> None:
    commands = [
        BotCommand("start", "Renaiss Edition menu"),
        BotCommand("open", "Open TCG packs"),
        BotCommand("pack", "Pack guide"),
        BotCommand("mycards", "View portfolio"),
        BotCommand("sell", "Sell cards for cash"),
        BotCommand("flex", "Flex your best card"),
        BotCommand("rank", "Portfolio ranking"),
        BotCommand("price", "Check Market Value"),
        BotCommand("sets", "Supported categories"),
    ]
    try:
        await application.bot.set_my_commands(commands)
        logger.info("Renaiss command menu configured.")
    except Exception as exc:
        logger.warning("Renaiss command setup skipped: %s", exc)


async def post_init(application: Application) -> None:
    await _configure_commands(application)

    from renaiss_bot.jobs import register_jobs

    register_jobs(application)

    if os.getenv("RENAISS_SKIP_DB", "").strip().lower() in {"1", "true", "yes"}:
        logger.info("RENAISS_SKIP_DB is set; DB initialization skipped.")
        return
    if not os.getenv("DATABASE_URL"):
        logger.warning("DATABASE_URL not set; DB initialization skipped.")
        return
    try:
        from renaiss_bot.database.connection import get_db
        from renaiss_bot.database.schema import create_tables

        pool = await get_db()
        await create_tables(pool)
        logger.info("Renaiss DB tables ready.")
    except Exception as exc:
        logger.warning("Renaiss DB initialization skipped: %s", exc, exc_info=True)
        return

    try:
        from renaiss_bot.jobs import recover_open_quiz_rounds

        await recover_open_quiz_rounds(application)
    except Exception as exc:
        logger.warning("Renaiss quiz recovery skipped: %s", exc)


async def post_shutdown(application: Application) -> None:
    try:
        from renaiss_bot.database.connection import close_db

        await close_db()
    except Exception as exc:
        logger.debug("Renaiss DB close skipped: %s", exc)


def main() -> None:
    token = os.getenv("RENAISS_BOT_TOKEN")
    if not token:
        logger.error("RENAISS_BOT_TOKEN not set.")
        return

    app = (
        Application.builder()
        .token(token)
        .post_init(post_init)
        .post_shutdown(post_shutdown)
        .concurrent_updates(True)
        .build()
    )
    register_handlers(app)
    logger.info("Starting standalone Renaiss bot...")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
