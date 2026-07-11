"""Operator-only spawn controls for the official collaboration room.

``force`` posts one immediate spawn; ``/spawnon``/``/spawnoff`` toggle the
burst loop. Authorization is a fixed ``RENAISS_OPERATOR_USER_IDS`` allowlist —
group admin status deliberately grants nothing, and an empty allowlist
disables every control (fail closed). Non-operators get complete silence so
the commands do not advertise themselves. Every action is recorded in the
event ledger. Burst state lives in ``Application.bot_data`` only, so a
process restart always returns to the pilot schedule.
"""

from __future__ import annotations

import logging
import os

from telegram import Update
from telegram.ext import ContextTypes

from renaiss_bot.database.event_queries import log_event
from renaiss_bot.handlers.message_cleanup import (
    command_delete_delay_seconds,
    delete_group_command_job,
)
from renaiss_bot.handlers.spawn import (
    BURST_FLAG_KEY,
    burst_interval_seconds,
    official_chat_id,
    spawn_tick,
)

logger = logging.getLogger(__name__)

SPAWN_LOOP_JOB_NAME = "renaiss_official_spawn"


def operator_user_ids() -> frozenset[int]:
    """Positive Telegram user ids allowed to drive spawn controls."""
    ids: set[int] = set()
    for part in os.getenv("RENAISS_OPERATOR_USER_IDS", "").replace(";", ",").split(","):
        part = part.strip()
        if part.isdigit() and int(part) > 0:
            ids.add(int(part))
    return frozenset(ids)


async def _is_authorized_operator(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> bool:
    chat = update.effective_chat
    user = update.effective_user
    return (
        chat is not None
        and user is not None
        and chat.id == official_chat_id()
        and user.id in operator_user_ids()
    )


def _schedule_reply_cleanup(context: ContextTypes.DEFAULT_TYPE, message) -> None:
    job_queue = getattr(context, "job_queue", None)
    if job_queue is None or message is None:
        return
    job_queue.run_once(
        delete_group_command_job,
        when=command_delete_delay_seconds(),
        data={"chat_id": message.chat_id, "message_id": message.message_id},
        name=f"renaiss_command_cleanup_{message.chat_id}_{message.message_id}",
        job_kwargs={"misfire_grace_time": 300},
    )


def _reschedule_spawn_loop(context: ContextTypes.DEFAULT_TYPE, *, delay: float) -> None:
    """Replace the single spawn loop timer without ever doubling it."""
    from renaiss_bot.jobs import spawn_loop_job

    job_queue = context.job_queue
    for job in job_queue.get_jobs_by_name(SPAWN_LOOP_JOB_NAME):
        job.schedule_removal()
    job_queue.run_once(
        spawn_loop_job,
        when=delay,
        name=SPAWN_LOOP_JOB_NAME,
        job_kwargs={"misfire_grace_time": None},
    )


async def force_spawn_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """``force`` — 운영자가 즉시 스폰 1회를 게시한다."""
    if not await _is_authorized_operator(update, context):
        return
    message = update.effective_message
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    await log_event(
        "force_spawn_used",
        event_key=f"force-spawn:{chat_id}:{message.message_id if message else 0}",
        user_id=user_id,
        chat_id=chat_id,
    )
    await spawn_tick(context, burst=True)


async def spawn_on_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """``/spawnon`` — burst 루프 시작 (기본 60초 간격)."""
    if not await _is_authorized_operator(update, context):
        return
    application = context.application
    interval = burst_interval_seconds()
    already_on = bool(application.bot_data.get(BURST_FLAG_KEY))
    application.bot_data[BURST_FLAG_KEY] = True
    _reschedule_spawn_loop(context, delay=1)
    message = update.effective_message
    chat_id = update.effective_chat.id
    if message is not None:
        reply = await message.reply_text(
            f"⚡ Burst mode {'already ' if already_on else ''}ON — spawning about every "
            f"{interval}s until /spawnoff (a bot restart also returns to the pilot schedule).",
            disable_notification=True,
        )
        _schedule_reply_cleanup(context, reply)
    await log_event(
        "spawn_burst_started",
        event_key=f"spawn-burst:{chat_id}:{message.message_id if message else 0}",
        user_id=update.effective_user.id,
        chat_id=chat_id,
        metadata={"interval_seconds": interval, "already_on": already_on},
    )


async def spawn_off_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """``/spawnoff`` — burst 루프 중지, 파일럿 스케줄 복귀."""
    if not await _is_authorized_operator(update, context):
        return
    application = context.application
    was_on = bool(application.bot_data.get(BURST_FLAG_KEY))
    application.bot_data[BURST_FLAG_KEY] = False
    from renaiss_bot.handlers.spawn import next_spawn_delay

    _reschedule_spawn_loop(context, delay=next_spawn_delay())
    message = update.effective_message
    chat_id = update.effective_chat.id
    if message is not None:
        reply = await message.reply_text(
            "💤 Burst mode OFF — back to the pilot spawn schedule."
            if was_on
            else "💤 Burst mode was not on.",
            disable_notification=True,
        )
        _schedule_reply_cleanup(context, reply)
    await log_event(
        "spawn_burst_stopped",
        event_key=f"spawn-burst-stop:{chat_id}:{message.message_id if message else 0}",
        user_id=update.effective_user.id,
        chat_id=chat_id,
        metadata={"was_on": was_on},
    )
