"""Short-lived cleanup for user command messages in Telegram groups."""

from __future__ import annotations

import logging
import os

from telegram import Update
from telegram.error import TelegramError
from telegram.ext import ContextTypes


logger = logging.getLogger(__name__)


def command_delete_delay_seconds() -> int:
    """Keep commands visible briefly, then remove them without blocking a handler."""
    try:
        return min(
            300,
            max(10, int(os.getenv("RENAISS_COMMAND_DELETE_DELAY_SECONDS", "60"))),
        )
    except (TypeError, ValueError):
        return 60


def _is_group_command(update: Update) -> bool:
    chat = update.effective_chat
    message = update.effective_message
    text = (getattr(message, "text", None) or "").strip()
    if not chat or chat.type not in {"group", "supergroup"} or not message:
        return False
    lowered = text.lower()
    return (
        lowered in {"c", "force"}
        or lowered.startswith("trade ")
        or lowered == "trade"
        or text.startswith("/")
    )


async def delete_group_command_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Delete one previously scheduled command; failure must not affect the bot."""
    job = getattr(context, "job", None)
    data = getattr(job, "data", None)
    if not isinstance(data, dict):
        return
    chat_id = data.get("chat_id")
    message_id = data.get("message_id")
    if not isinstance(chat_id, int) or not isinstance(message_id, int):
        return
    try:
        await context.bot.delete_message(chat_id=chat_id, message_id=message_id)
    except TelegramError as exc:
        # Missing admin permission, an already-deleted message, or a transient Telegram
        # failure must never break command handling or scheduled game jobs.
        logger.debug(
            "Telegram command cleanup skipped (error=%s).",
            type(exc).__name__,
        )
    except Exception as exc:
        logger.warning(
            "Telegram command cleanup failed safely (error=%s).",
            type(exc).__name__,
        )


async def schedule_group_command_delete(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """Schedule deletion of a group `c` or slash command after the grace period."""
    if not _is_group_command(update):
        return
    message = update.effective_message
    chat = update.effective_chat
    job_queue = getattr(context, "job_queue", None)
    if job_queue is None:
        logger.warning("Telegram command cleanup was not scheduled: JobQueue unavailable.")
        return
    job_queue.run_once(
        delete_group_command_job,
        when=command_delete_delay_seconds(),
        data={"chat_id": chat.id, "message_id": message.message_id},
        name=f"renaiss_command_cleanup_{chat.id}_{message.message_id}",
        job_kwargs={"misfire_grace_time": 300},
    )
