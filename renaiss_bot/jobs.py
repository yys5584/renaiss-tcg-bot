"""Scheduled jobs for the standalone Renaiss bot (KST based)."""

from __future__ import annotations

import logging
from datetime import datetime, time, timezone
from zoneinfo import ZoneInfo

from telegram.ext import Application, ContextTypes

from renaiss_bot.database.queries import list_open_quiz_rounds, snapshot_all_portfolios
from renaiss_bot.handlers.quiz import close_quiz_job, post_daily_quiz, quiz_chat_id
from renaiss_bot.handlers.spawn import SPAWN_INTERVAL_SECONDS, official_chat_id, spawn_tick

logger = logging.getLogger(__name__)

KST = ZoneInfo("Asia/Seoul")
DAILY_QUIZ_TIME_KST = time(hour=21, minute=0, tzinfo=KST)
DAILY_SNAPSHOT_TIME_KST = time(hour=23, minute=55, tzinfo=KST)


async def snapshot_portfolios_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    count = await snapshot_all_portfolios()
    logger.info("Renaiss portfolio snapshot job done: %s users.", count)


async def recover_open_quiz_rounds(application: Application) -> None:
    """재시작으로 close 잡이 날아간 라운드를 다시 정산 스케줄에 올린다."""
    rounds = await list_open_quiz_rounds()
    if not rounds or application.job_queue is None:
        return
    now = datetime.now(timezone.utc)
    for round_data in rounds:
        closes_at = round_data.get("closes_at")
        delay = 5.0
        if closes_at is not None and closes_at > now:
            delay = max(5.0, (closes_at - now).total_seconds())
        application.job_queue.run_once(
            close_quiz_job,
            when=delay,
            data=int(round_data["id"]),
            name=f"renaiss_quiz_close_{round_data['id']}",
            job_kwargs={"misfire_grace_time": None},
        )
        logger.info("Recovered quiz round %s: close in %.0fs.", round_data["id"], delay)


def register_jobs(application: Application) -> None:
    job_queue = application.job_queue
    if job_queue is None:
        logger.warning("JobQueue unavailable; Renaiss scheduled jobs skipped.")
        return

    job_queue.run_daily(
        snapshot_portfolios_job,
        time=DAILY_SNAPSHOT_TIME_KST,
        name="renaiss_portfolio_snapshot",
        job_kwargs={"misfire_grace_time": None},
    )

    if quiz_chat_id() is not None:
        job_queue.run_daily(
            post_daily_quiz,
            time=DAILY_QUIZ_TIME_KST,
            name="renaiss_daily_quiz",
            job_kwargs={"misfire_grace_time": None},
        )
        logger.info("Daily quiz scheduled at 21:00 KST.")
    else:
        logger.info("Daily quiz not scheduled: RENAISS_QUIZ_CHAT_ID not set.")

    # 공식방 상시 스폰 (시즌1 아케이드): 1분 간격
    if official_chat_id() is not None:
        job_queue.run_repeating(
            spawn_tick,
            interval=SPAWN_INTERVAL_SECONDS,
            first=SPAWN_INTERVAL_SECONDS,
            name="renaiss_official_spawn",
            job_kwargs={"misfire_grace_time": None},
        )
        logger.info("Official-room spawn scheduled every %ss.", SPAWN_INTERVAL_SECONDS)
    else:
        logger.info("Official spawn not scheduled: RENAISS_OFFICIAL_CHAT_ID/QUIZ_CHAT_ID not set.")
