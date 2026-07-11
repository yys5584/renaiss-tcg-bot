"""The Telegram process must hold one hard PostgreSQL session fence."""

from __future__ import annotations

import asyncio
import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from renaiss_bot.database import instance_queries
from renaiss_bot.services import instance_guard


class _Connection:
    def __init__(self):
        self.closed = False
        self.fetchval = AsyncMock(return_value=True)
        self.add_termination_listener = Mock()
        self.remove_termination_listener = Mock()
        self.close = AsyncMock(side_effect=self._close)
        self.terminate = Mock(side_effect=self._terminate)

    def is_closed(self):
        return self.closed

    async def _close(self, *, timeout=None):
        self.closed = True

    def _terminate(self):
        self.closed = True


def _application():
    return SimpleNamespace(
        bot=SimpleNamespace(id=999),
        bot_data={},
        running=False,
        stop_running=Mock(),
    )


@pytest.fixture(autouse=True)
def _same_application_lock_domain(monkeypatch):
    monkeypatch.setattr(
        instance_guard,
        "_application_pool_sees_guard_lock",
        AsyncMock(return_value=True),
    )
    monkeypatch.setattr(
        instance_guard,
        "instance_lock_owner_backend_pid",
        AsyncMock(return_value=9001),
    )


async def test_instance_lock_queries_are_session_scoped_and_named():
    connection = _Connection()

    assert await instance_queries.acquire_instance_lock(
        connection,
        lock_name="renaiss:telegram_poller:999",
    )
    acquire_sql = connection.fetchval.await_args.args[0]
    assert "pg_try_advisory_lock" in acquire_sql
    assert "hashtextextended($1, 0)" in acquire_sql

    connection.fetchval.reset_mock()
    assert await instance_queries.release_instance_lock(
        connection,
        lock_name="renaiss:telegram_poller:999",
    )
    assert "pg_advisory_unlock" in connection.fetchval.await_args.args[0]

    connection.fetchval.reset_mock()
    connection.fetchval.return_value = 9001
    assert await instance_queries.probe_instance_lock_session(
        connection,
        lock_name="renaiss:telegram_poller:999",
        expected_backend_pid=9001,
    )
    ownership_sql = connection.fetchval.await_args.args[0]
    assert "pg_locks" in ownership_sql
    assert "pg_backend_pid()" in ownership_sql
    assert connection.fetchval.await_args.args[1] == "renaiss:telegram_poller:999"
    assert not await instance_queries.probe_instance_lock_session(
        connection,
        lock_name="renaiss:telegram_poller:999",
        expected_backend_pid=9002,
    )

    connection.fetchval.reset_mock()
    connection.fetchval.return_value = False
    assert await instance_queries.probe_instance_lock_contender(
        connection,
        lock_name="renaiss:telegram_poller:999",
    )
    assert "pg_try_advisory_xact_lock" in connection.fetchval.await_args.args[0]


@pytest.mark.parametrize("lock_name", ["", "x" * 129])
async def test_instance_lock_rejects_invalid_name_before_query(lock_name):
    connection = _Connection()

    with pytest.raises(ValueError, match="invalid instance lock name"):
        await instance_queries.acquire_instance_lock(
            connection,
            lock_name=lock_name,
        )

    connection.fetchval.assert_not_awaited()


async def test_guard_holds_dedicated_session_until_shutdown(monkeypatch):
    application = _application()
    connection = _Connection()
    open_session = AsyncMock(return_value=connection)
    acquire = AsyncMock(return_value=True)
    release = AsyncMock(return_value=True)
    monkeypatch.setattr(instance_guard, "open_db_session", open_session)
    monkeypatch.setattr(instance_guard, "acquire_instance_lock", acquire)
    monkeypatch.setattr(instance_guard, "release_instance_lock", release)

    await instance_guard.start_telegram_instance_guard(application)

    state = application.bot_data[instance_guard.BOT_DATA_KEY]
    assert state.lock_name == "renaiss:telegram_poller:999"
    assert state.backend_pid == 9001
    assert state.connection is connection
    assert state.monitor_task is not None
    assert state.watchdog_thread is not None
    assert state.watchdog_thread.is_alive()
    assert instance_guard.telegram_instance_guard_ready(application)
    open_session.assert_awaited_once_with(
        dsn_variable="RENAISS_TELEGRAM_LOCK_DATABASE_URL"
    )
    connection.add_termination_listener.assert_called_once()

    instance_guard.confirm_telegram_instance_guard(application)
    assert state.startup_task is None
    await instance_guard.stop_telegram_instance_guard(application)

    release.assert_awaited_once_with(
        connection,
        lock_name="renaiss:telegram_poller:999",
    )
    connection.remove_termination_listener.assert_called_once()
    connection.close.assert_awaited_once_with(timeout=5)
    assert instance_guard.BOT_DATA_KEY not in application.bot_data


async def test_guard_rejects_second_poller_and_closes_unused_session(monkeypatch):
    application = _application()
    connection = _Connection()
    monkeypatch.setattr(
        instance_guard,
        "open_db_session",
        AsyncMock(return_value=connection),
    )
    monkeypatch.setattr(
        instance_guard,
        "acquire_instance_lock",
        AsyncMock(return_value=False),
    )

    with pytest.raises(RuntimeError, match="Another Renaiss Telegram poller"):
        await instance_guard.start_telegram_instance_guard(application)

    connection.close.assert_awaited_once_with(timeout=5)
    assert not application.bot_data


async def test_guard_rejects_mismatched_application_database_lock_domain(monkeypatch):
    application = _application()
    connection = _Connection()
    monkeypatch.setattr(
        instance_guard,
        "open_db_session",
        AsyncMock(return_value=connection),
    )
    monkeypatch.setattr(
        instance_guard,
        "acquire_instance_lock",
        AsyncMock(return_value=True),
    )
    monkeypatch.setattr(
        instance_guard,
        "_application_pool_sees_guard_lock",
        AsyncMock(return_value=False),
    )

    with pytest.raises(RuntimeError, match="lock acquisition failed"):
        await instance_guard.start_telegram_instance_guard(application)

    connection.close.assert_awaited_once_with(timeout=5)
    assert not application.bot_data


async def test_guard_setup_failure_releases_session_and_redacts_detail(
    monkeypatch,
    caplog,
):
    application = _application()
    connection = _Connection()
    sentinel = "postgresql://user:do-not-print@db\r\nFORGED"
    connection.add_termination_listener.side_effect = RuntimeError(sentinel)
    monkeypatch.setattr(
        instance_guard,
        "open_db_session",
        AsyncMock(return_value=connection),
    )
    monkeypatch.setattr(
        instance_guard,
        "acquire_instance_lock",
        AsyncMock(return_value=True),
    )
    monkeypatch.setattr(
        instance_guard,
        "release_instance_lock",
        AsyncMock(return_value=True),
    )

    with caplog.at_level(logging.CRITICAL, logger=instance_guard.__name__):
        with pytest.raises(RuntimeError, match="lock acquisition failed") as raised:
            await instance_guard.start_telegram_instance_guard(application)

    assert sentinel not in str(raised.value)
    assert "do-not-print" not in caplog.text
    assert "FORGED" not in caplog.text
    assert "RuntimeError" in caplog.text
    connection.close.assert_awaited_once_with(timeout=5)
    assert instance_guard.BOT_DATA_KEY not in application.bot_data


async def test_database_probe_loss_requests_nonzero_graceful_stop(
    monkeypatch,
    caplog,
):
    application = _application()
    application.running = True
    connection = _Connection()
    state = instance_guard.TelegramInstanceGuard(
        lock_name="renaiss:telegram_poller:999",
        connection=connection,
        loop=asyncio.get_running_loop(),
        startup_task=None,
    )
    application.bot_data[instance_guard.BOT_DATA_KEY] = state
    monkeypatch.setattr(instance_guard, "HEARTBEAT_INTERVAL_SECONDS", 0)
    monkeypatch.setattr(instance_guard, "DATABASE_PING_INTERVAL_SECONDS", 0)
    sentinel = "postgresql://user:do-not-print@db\r\nFORGED"
    monkeypatch.setattr(
        instance_guard,
        "probe_instance_lock_session",
        AsyncMock(side_effect=RuntimeError(sentinel)),
    )
    fail_stop = Mock()
    monkeypatch.setattr(instance_guard, "_arm_fence_loss_fail_stop", fail_stop)

    with caplog.at_level(logging.CRITICAL, logger=instance_guard.__name__):
        await instance_guard._guard_monitor(application, state)
    await asyncio.sleep(0)

    assert state.lost
    assert application.bot_data[instance_guard.FATAL_EXIT_KEY] == 75
    assert application.stop_running.call_count >= 1
    assert "RuntimeError" in caplog.text
    assert "do-not-print" not in caplog.text
    assert "FORGED" not in caplog.text
    fail_stop.assert_called_once_with(state)


async def test_session_termination_cancels_startup_and_marks_fatal(monkeypatch):
    application = _application()
    connection = _Connection()

    async def startup_waiter():
        await asyncio.Event().wait()

    startup_task = asyncio.create_task(startup_waiter())
    state = instance_guard.TelegramInstanceGuard(
        lock_name="renaiss:telegram_poller:999",
        connection=connection,
        loop=asyncio.get_running_loop(),
        startup_task=startup_task,
    )
    application.bot_data[instance_guard.BOT_DATA_KEY] = state
    listener = instance_guard._termination_listener(application, state)
    fail_stop = Mock()
    monkeypatch.setattr(instance_guard, "_arm_fence_loss_fail_stop", fail_stop)

    listener(connection)
    await asyncio.sleep(0)
    with pytest.raises(asyncio.CancelledError):
        await startup_task

    assert state.lost
    assert startup_task.cancelled()
    assert application.bot_data[instance_guard.FATAL_EXIT_KEY] == 75
    fail_stop.assert_called_once_with(state)


def test_hard_watchdog_exits_nonzero_after_stale_loop(monkeypatch):
    application = _application()
    connection = _Connection()
    loop = SimpleNamespace(call_soon_threadsafe=Mock())

    class StopEvent:
        def wait(self, timeout):
            return False

    state = instance_guard.TelegramInstanceGuard(
        lock_name="renaiss:telegram_poller:999",
        connection=connection,
        loop=loop,
        startup_task=None,
        last_loop_heartbeat=0,
        watchdog_stop=StopEvent(),
    )
    hard_exit = Mock()
    monkeypatch.setattr(instance_guard.time, "monotonic", lambda: 61.0)
    monkeypatch.setattr(instance_guard, "_hard_exit", hard_exit)

    instance_guard._event_loop_watchdog(application, state)

    loop.call_soon_threadsafe.assert_called_once()
    hard_exit.assert_called_once_with(instance_guard.FATAL_EXIT_CODE)


def test_definitive_fence_loss_uses_short_fail_stop_deadline(monkeypatch):
    sleep = Mock()
    hard_exit = Mock()
    monkeypatch.setattr(instance_guard.time, "sleep", sleep)
    monkeypatch.setattr(instance_guard, "_hard_exit", hard_exit)

    instance_guard._fence_loss_fail_stop()

    sleep.assert_called_once_with(instance_guard.FENCE_LOSS_HARD_EXIT_SECONDS)
    hard_exit.assert_called_once_with(instance_guard.FATAL_EXIT_CODE)


def test_confirm_and_readiness_fail_after_session_loss():
    application = _application()
    connection = _Connection()
    state = instance_guard.TelegramInstanceGuard(
        lock_name="renaiss:telegram_poller:999",
        connection=connection,
        loop=asyncio.new_event_loop(),
        startup_task=None,
    )
    application.bot_data[instance_guard.BOT_DATA_KEY] = state
    state.lost = True

    assert not instance_guard.telegram_instance_guard_ready(application)
    with pytest.raises(RuntimeError, match="fence was lost"):
        instance_guard.confirm_telegram_instance_guard(application)
    state.loop.close()


def test_guard_fatal_exit_code_survives_guard_cleanup():
    application = _application()
    application.bot_data[instance_guard.FATAL_EXIT_KEY] = 75

    assert instance_guard.telegram_instance_guard_exit_code(application) == 75
