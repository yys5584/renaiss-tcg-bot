"""Operator spawn controls: force / /spawnon / /spawnoff (DB/telegram 불필요)."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from renaiss_bot.handlers.spawn import (
    BURST_FLAG_KEY,
    burst_daily_cap,
    burst_interval_seconds,
    spawn_burst_active,
)
from renaiss_bot.handlers.spawn_admin import (
    SPAWN_LOOP_JOB_NAME,
    force_spawn_handler,
    spawn_off_handler,
    spawn_on_handler,
)

OFFICIAL_CHAT = -100777
ADMIN_ID = 11
MEMBER_ID = 22


class FakeJob:
    def __init__(self):
        self.removed = False

    def schedule_removal(self):
        self.removed = True


class FakeJobQueue:
    def __init__(self):
        self.scheduled = []
        self.existing = [FakeJob()]

    def get_jobs_by_name(self, name):
        return list(self.existing) if name == SPAWN_LOOP_JOB_NAME else []

    def run_once(self, callback, when, **kwargs):
        self.scheduled.append(SimpleNamespace(callback=callback, when=when, kwargs=kwargs))


def _context():
    application = SimpleNamespace(bot_data={})
    return SimpleNamespace(
        bot=SimpleNamespace(),
        application=application,
        job_queue=FakeJobQueue(),
    )


def _update(*, chat_id: int = OFFICIAL_CHAT, user_id: int = ADMIN_ID):
    message = SimpleNamespace(
        message_id=555,
        chat_id=chat_id,
        reply_text=AsyncMock(
            return_value=SimpleNamespace(chat_id=chat_id, message_id=556)
        ),
    )
    return SimpleNamespace(
        effective_chat=SimpleNamespace(id=chat_id, type="supergroup"),
        effective_user=SimpleNamespace(id=user_id),
        effective_message=message,
    )


@pytest.fixture(autouse=True)
def _official_chat(monkeypatch):
    monkeypatch.setenv("RENAISS_OFFICIAL_CHAT_ID", str(OFFICIAL_CHAT))
    monkeypatch.setenv("RENAISS_OPERATOR_USER_IDS", str(ADMIN_ID))


@pytest.fixture(autouse=True)
def _quiet_events(monkeypatch):
    monkeypatch.setattr(
        "renaiss_bot.handlers.spawn_admin.log_event", AsyncMock(return_value=True)
    )


async def test_force_spawn_runs_burst_tick_for_listed_operator(monkeypatch):
    tick = AsyncMock()
    monkeypatch.setattr("renaiss_bot.handlers.spawn_admin.spawn_tick", tick)
    await force_spawn_handler(_update(), _context())
    tick.assert_awaited_once()
    assert tick.await_args.kwargs["burst"] is True


@pytest.mark.parametrize(
    "update",
    [
        _update(user_id=MEMBER_ID),  # not on the allowlist
        _update(chat_id=-100999),  # not the official room
    ],
)
async def test_force_spawn_silently_ignores_unauthorized(monkeypatch, update):
    tick = AsyncMock()
    monkeypatch.setattr("renaiss_bot.handlers.spawn_admin.spawn_tick", tick)
    await force_spawn_handler(update, _context())
    tick.assert_not_awaited()
    update.effective_message.reply_text.assert_not_awaited()


async def test_empty_allowlist_disables_controls_even_for_group_admin(monkeypatch):
    monkeypatch.delenv("RENAISS_OPERATOR_USER_IDS", raising=False)
    tick = AsyncMock()
    monkeypatch.setattr("renaiss_bot.handlers.spawn_admin.spawn_tick", tick)
    update = _update()  # would-be operator, but no allowlist configured
    context = _context()
    await force_spawn_handler(update, context)
    await spawn_on_handler(update, context)
    tick.assert_not_awaited()
    assert context.application.bot_data == {}
    update.effective_message.reply_text.assert_not_awaited()


async def test_spawnon_sets_flag_and_replaces_loop_immediately():
    context = _context()
    update = _update()
    await spawn_on_handler(update, context)
    assert context.application.bot_data[BURST_FLAG_KEY] is True
    assert context.job_queue.existing[0].removed is True
    loop_jobs = [
        job
        for job in context.job_queue.scheduled
        if job.kwargs.get("name") == SPAWN_LOOP_JOB_NAME
    ]
    assert len(loop_jobs) == 1 and loop_jobs[0].when == 1
    update.effective_message.reply_text.assert_awaited_once()


async def test_spawnoff_clears_flag_and_restores_pilot_delay(monkeypatch):
    monkeypatch.setattr("renaiss_bot.handlers.spawn.next_spawn_delay", lambda: 4321)
    context = _context()
    context.application.bot_data[BURST_FLAG_KEY] = True
    update = _update()
    await spawn_off_handler(update, context)
    assert context.application.bot_data[BURST_FLAG_KEY] is False
    loop_jobs = [
        job
        for job in context.job_queue.scheduled
        if job.kwargs.get("name") == SPAWN_LOOP_JOB_NAME
    ]
    assert len(loop_jobs) == 1 and loop_jobs[0].when == 4321


async def test_spawn_loop_uses_burst_interval_when_flag_on(monkeypatch):
    from renaiss_bot.jobs import spawn_loop_job

    seen = {}

    async def fake_tick(context, **kwargs):
        seen.update(kwargs)

    monkeypatch.setattr("renaiss_bot.jobs.spawn_tick", fake_tick)
    monkeypatch.setenv("RENAISS_SPAWN_BURST_INTERVAL_SECONDS", "60")
    queue = FakeJobQueue()
    application = SimpleNamespace(bot_data={BURST_FLAG_KEY: True})
    await spawn_loop_job(SimpleNamespace(application=application, job_queue=queue))
    assert seen["burst"] is True
    assert queue.scheduled[-1].when == 60
    # /spawnoff mid-tick must win for the *next* delay
    seen.clear()
    application.bot_data[BURST_FLAG_KEY] = False
    monkeypatch.setattr("renaiss_bot.jobs.next_spawn_delay", lambda: 7200)
    await spawn_loop_job(SimpleNamespace(application=application, job_queue=queue))
    assert seen["burst"] is False
    assert queue.scheduled[-1].when == 7200


def test_burst_env_bounds(monkeypatch):
    monkeypatch.setenv("RENAISS_SPAWN_BURST_INTERVAL_SECONDS", "3")
    assert burst_interval_seconds() == 15
    monkeypatch.setenv("RENAISS_SPAWN_BURST_INTERVAL_SECONDS", "junk")
    assert burst_interval_seconds() == 60
    monkeypatch.setenv("RENAISS_SPAWN_BURST_DAILY_CAP", "999999")
    assert burst_daily_cap() == 5000
    assert spawn_burst_active(None) is False
    assert spawn_burst_active(SimpleNamespace(bot_data={})) is False
