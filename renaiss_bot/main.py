"""Standalone Renaiss Edition bot entrypoint."""

from __future__ import annotations

import logging
import os

from telegram import BotCommand
from telegram.ext import Application

from renaiss_bot.database.connection import database_tls_verification_disabled
from renaiss_bot.handlers.register import register_handlers
from renaiss_bot.runtime import load_runtime_environment
from renaiss_bot.services.market import (
    close_daily_pick_admission,
    daily_pick_enabled,
    daily_pick_requested,
    verify_daily_pick_partner_ready,
)
from renaiss_bot.services.instance_guard import (
    confirm_telegram_instance_guard,
    start_telegram_instance_guard,
    stop_telegram_instance_guard,
    telegram_instance_guard_exit_code,
)
from renaiss_bot.telegram_health import (
    health_configuration_issues,
    start_telegram_health,
    stop_telegram_health,
)

logger = logging.getLogger(__name__)


class _FdStderrHandler(logging.Handler):
    """Write through fd 2 so the Windows service runner can capture live logs."""

    terminator = "\n"

    def emit(self, record: logging.LogRecord) -> None:
        try:
            payload = (self.format(record) + self.terminator).encode(
                "utf-8", errors="backslashreplace"
            )
            os.write(2, payload)
        except Exception:
            self.handleError(record)


def _configure_logging() -> None:
    handler = _FdStderrHandler()
    handler.setFormatter(
        logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
    )
    logging.basicConfig(
        level=getattr(logging, os.getenv("LOG_LEVEL", "INFO").upper(), logging.INFO),
        handlers=[handler],
        force=True,
    )
    for logger_name in ("httpx", "httpcore"):
        logging.getLogger(logger_name).setLevel(logging.WARNING)


def _production_startup_issues() -> list[str]:
    """Reject local configuration gaps before Telegram performs any API call."""
    issues = health_configuration_issues()
    token = os.getenv("RENAISS_BOT_TOKEN", "").strip()
    if not token:
        issues.append("RENAISS_BOT_TOKEN is missing")
    raw_expected_bot_id = os.getenv("RENAISS_EXPECTED_BOT_ID", "").strip()
    try:
        expected_bot_id = int(raw_expected_bot_id)
    except (TypeError, ValueError):
        expected_bot_id = 0
    if expected_bot_id <= 0:
        issues.append("RENAISS_EXPECTED_BOT_ID must be a positive integer")
    elif token:
        try:
            token_bot_id = int(token.partition(":")[0])
        except ValueError:
            token_bot_id = 0
        if token_bot_id != expected_bot_id:
            issues.append("RENAISS_BOT_TOKEN does not belong to RENAISS_EXPECTED_BOT_ID")
    if os.getenv("RENAISS_API_MOCK_JSON", "").strip():
        issues.append("RENAISS_API_MOCK_JSON must be unset for the Telegram bot")
    skip_db = os.getenv("RENAISS_SKIP_DB", "").strip().lower() in {
        "1",
        "true",
        "yes",
    }
    if skip_db:
        issues.append("RENAISS_SKIP_DB cannot be enabled for the Telegram bot")
        return issues
    if not os.getenv("DATABASE_URL", "").strip():
        issues.append("DATABASE_URL is missing")
    if not os.getenv("RENAISS_TELEGRAM_LOCK_DATABASE_URL", "").strip():
        issues.append(
            "RENAISS_TELEGRAM_LOCK_DATABASE_URL direct/session endpoint is missing"
        )
    if database_tls_verification_disabled():
        issues.append("RENAISS_DB_SSL_INSECURE must be disabled for the Telegram bot")
    raw_chat_id = os.getenv("RENAISS_OFFICIAL_CHAT_ID", "").strip()
    try:
        chat_id = int(raw_chat_id)
    except (TypeError, ValueError):
        chat_id = 0
    if chat_id >= 0:
        issues.append("RENAISS_OFFICIAL_CHAT_ID must be a negative group id")
    return issues


async def _configure_commands(application: Application) -> None:
    from renaiss_bot.services.features import private_free_packs_enabled

    commands = [
        BotCommand("start", "Collaboration game menu"),
        BotCommand("mycards", "View collection"),
        BotCommand("flex", "Flex your best card"),
        BotCommand("price", "Check Market Value"),
        BotCommand("sets", "Supported categories"),
    ]
    if private_free_packs_enabled():
        commands[1:1] = [
            BotCommand("open", "Open private TCG packs"),
            BotCommand("pack", "Private pack guide"),
        ]
    if daily_pick_enabled():
        commands.insert(2, BotCommand("market", "Choose today's verified card"))
    try:
        await application.bot.set_my_commands(commands)
        logger.info("Renaiss command menu configured.")
    except Exception as exc:
        logger.warning(
            "Renaiss command setup skipped (error=%s).",
            type(exc).__name__,
        )


async def _initialize_telegram_runtime(application: Application) -> None:
    raw_expected_bot_id = os.getenv("RENAISS_EXPECTED_BOT_ID", "").strip()
    if raw_expected_bot_id:
        try:
            expected_bot_id = int(raw_expected_bot_id)
        except ValueError as exc:
            raise RuntimeError("RENAISS_EXPECTED_BOT_ID must be a positive integer") from exc
        if expected_bot_id <= 0 or application.bot.id != expected_bot_id:
            raise RuntimeError("Telegram bot identity does not match RENAISS_EXPECTED_BOT_ID")
    if os.getenv("RENAISS_SKIP_DB", "").strip().lower() in {"1", "true", "yes"}:
        logger.warning(
            "RENAISS_SKIP_DB is set; database-backed commands and scheduled jobs stay closed."
        )
        await _configure_commands(application)
        return
    if application.job_queue is None:
        raise RuntimeError(
            "Telegram JobQueue is required for Renaiss scheduled jobs; "
            "install the python-telegram-bot job-queue dependencies."
        )
    if not os.getenv("DATABASE_URL"):
        raise RuntimeError(
            "DATABASE_URL is required for the Renaiss bot. "
            "Use RENAISS_SKIP_DB=1 only in isolated inspection scripts, not this bot entrypoint."
        )
    try:
        from renaiss_bot.database.connection import get_db
        from renaiss_bot.database.schema import create_tables

        pool = await get_db()
        await create_tables(pool)
        logger.info("Renaiss DB tables ready.")
    except Exception as exc:
        logger.critical(
            "Renaiss DB initialization failed; startup aborted (error=%s).",
            type(exc).__name__,
        )
        raise RuntimeError("Renaiss DB initialization failed") from None

    await start_telegram_instance_guard(application)

    from renaiss_bot.handlers.spawn import official_chat_id

    chat_id = official_chat_id()
    if chat_id is None:
        raise RuntimeError(
            "RENAISS_OFFICIAL_CHAT_ID must be a negative Telegram group or supergroup id."
        )
    try:
        chat = await application.bot.get_chat(chat_id)
    except Exception:
        raise RuntimeError(
            "The configured official Telegram chat is not accessible"
        ) from None
    if str(getattr(chat, "type", "")).lower() not in {"group", "supergroup"}:
        raise RuntimeError("RENAISS_OFFICIAL_CHAT_ID must resolve to a group or supergroup")

    if daily_pick_requested():
        try:
            member = await application.bot.get_chat_member(chat_id, application.bot.id)
            if str(getattr(member, "status", "")).lower() not in {
                "administrator",
                "creator",
                "owner",
            }:
                raise RuntimeError(
                    "the bot is not an administrator in the official chat"
                )
            await verify_daily_pick_partner_ready()
        except Exception as exc:
            # Admission closes, but the public c/guess/reveal loop must stay available.
            logger.error(
                "Daily Pick admission closed by startup preflight (error=%s).",
                type(exc).__name__,
            )

    await _configure_commands(application)

    from renaiss_bot.jobs import register_jobs

    register_jobs(application)

    from renaiss_bot.jobs import recover_unfinished_spawns

    try:
        await recover_unfinished_spawns(application)
    except Exception as exc:
        logger.critical(
            "Spawn restart recovery incomplete; startup aborted (error=%s).",
            type(exc).__name__,
        )
        raise RuntimeError("Renaiss spawn recovery readiness failed") from None
    confirm_telegram_instance_guard(application)


async def post_init(application: Application) -> None:
    close_daily_pick_admission()
    health_server = await start_telegram_health(application)
    try:
        await _initialize_telegram_runtime(application)
    except BaseException:
        try:
            await stop_telegram_instance_guard(application)
        except Exception as exc:
            logger.warning(
                "Telegram instance guard cleanup failed (error=%s).",
                type(exc).__name__,
            )
        if health_server is not None:
            try:
                await stop_telegram_health(application)
            except Exception as exc:
                logger.warning(
                    "Telegram health listener cleanup failed (error=%s).",
                    type(exc).__name__,
                )
        raise
    if health_server is not None:
        health_server.mark_startup_ready()


async def post_shutdown(application: Application) -> None:
    try:
        await stop_telegram_health(application)
    except Exception as exc:
        logger.debug(
            "Telegram health listener close skipped (error=%s).",
            type(exc).__name__,
        )
    try:
        await stop_telegram_instance_guard(application)
    except Exception as exc:
        logger.debug(
            "Telegram instance guard close skipped (error=%s).",
            type(exc).__name__,
        )
    try:
        from renaiss_bot.renderers.playwright_render import close_renderer

        await close_renderer()
    except Exception as exc:
        logger.debug("Renaiss renderer close skipped (error=%s).", type(exc).__name__)
    try:
        from renaiss_bot.database.connection import close_db

        await close_db()
    except Exception as exc:
        logger.debug("Renaiss DB close skipped (error=%s).", type(exc).__name__)


def main() -> None:
    load_runtime_environment()
    _configure_logging()
    issues = _production_startup_issues()
    if issues:
        logger.error("Renaiss Telegram startup refused: %s", "; ".join(issues))
        raise SystemExit(2)
    token = os.environ["RENAISS_BOT_TOKEN"].strip()

    app = (
        Application.builder()
        .token(token)
        .post_init(post_init)
        .post_shutdown(post_shutdown)
        .concurrent_updates(True)
        .build()
    )
    if app.job_queue is None:
        logger.error(
            "Renaiss Telegram startup refused: Telegram JobQueue dependency is unavailable."
        )
        raise SystemExit(2)
    register_handlers(app)
    logger.info("Starting standalone Renaiss bot...")
    try:
        app.run_polling(drop_pending_updates=True)
    except BaseException:
        fatal_exit_code = telegram_instance_guard_exit_code(app)
        if fatal_exit_code:
            raise SystemExit(fatal_exit_code) from None
        raise
    fatal_exit_code = telegram_instance_guard_exit_code(app)
    if fatal_exit_code:
        raise SystemExit(fatal_exit_code)


if __name__ == "__main__":
    main()
