"""Hard single-poller fence and event-loop fail-stop watchdog."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from renaiss_bot.database.connection import get_db, open_db_session
from renaiss_bot.database.instance_queries import (
    acquire_instance_lock,
    instance_lock_owner_backend_pid,
    probe_instance_lock_contender,
    probe_instance_lock_session,
    release_instance_lock,
)

logger = logging.getLogger(__name__)

BOT_DATA_KEY = "renaiss_telegram_instance_guard"
FATAL_EXIT_KEY = "renaiss_telegram_fatal_exit_code"
FATAL_EXIT_CODE = 75
HEARTBEAT_INTERVAL_SECONDS = 5.0
DATABASE_PING_INTERVAL_SECONDS = 10.0
DATABASE_PING_TIMEOUT_SECONDS = 5.0
GRACEFUL_STALE_SECONDS = 45.0
HARD_STALE_SECONDS = 60.0
WATCHDOG_POLL_SECONDS = 1.0
FENCE_LOSS_HARD_EXIT_SECONDS = 2.0

_hard_exit: Callable[[int], Any] = os._exit


class _ActivePollerError(RuntimeError):
    pass


@dataclass
class TelegramInstanceGuard:
    lock_name: str
    connection: Any
    loop: asyncio.AbstractEventLoop
    startup_task: asyncio.Task[Any] | None
    backend_pid: int | None = None
    last_loop_heartbeat: float = field(default_factory=time.monotonic)
    monitor_task: asyncio.Task[Any] | None = None
    stop_enforcer_task: asyncio.Task[Any] | None = None
    watchdog_thread: threading.Thread | None = None
    fence_loss_thread: threading.Thread | None = None
    watchdog_stop: threading.Event = field(default_factory=threading.Event)
    termination_listener: Callable[[Any], None] | None = None
    lost: bool = False
    stopping: bool = False


def _bot_data(application: Any) -> dict[str, Any]:
    bot_data = getattr(application, "bot_data", None)
    if not isinstance(bot_data, dict):
        raise RuntimeError("Telegram application bot_data is unavailable")
    return bot_data


def _connection_is_open(connection: Any) -> bool:
    try:
        return not bool(connection.is_closed())
    except Exception:
        return False


def telegram_instance_guard_ready(application: Any) -> bool:
    state = getattr(application, "bot_data", {}).get(BOT_DATA_KEY)
    return (
        isinstance(state, TelegramInstanceGuard)
        and not state.lost
        and not state.stopping
        and _connection_is_open(state.connection)
        and time.monotonic() - state.last_loop_heartbeat
        < GRACEFUL_STALE_SECONDS
    )


def telegram_instance_guard_exit_code(application: Any) -> int:
    try:
        return int(getattr(application, "bot_data", {}).get(FATAL_EXIT_KEY, 0) or 0)
    except (TypeError, ValueError):
        return FATAL_EXIT_CODE


def _mark_guard_lost(
    application: Any,
    state: TelegramInstanceGuard,
    reason: str,
) -> None:
    """Run on the application loop and request a nonzero graceful stop."""
    if state.stopping or state.lost:
        return
    state.lost = True
    try:
        _bot_data(application)[FATAL_EXIT_KEY] = FATAL_EXIT_CODE
    except RuntimeError:
        pass
    logger.critical("Telegram single-poller fence lost (%s); stopping.", reason)
    if reason in {
        "database_session_terminated",
        "database_session_unresponsive",
    }:
        _arm_fence_loss_fail_stop(state)

    startup_task = state.startup_task
    current_task = asyncio.current_task()
    if (
        startup_task is not None
        and not startup_task.done()
        and startup_task is not current_task
    ):
        startup_task.cancel()
    try:
        application.stop_running()
    except Exception as exc:
        logger.critical(
            "Telegram graceful stop request failed (error=%s).",
            type(exc).__name__,
        )
    if (
        (startup_task is None or startup_task.done())
        and (state.stop_enforcer_task is None or state.stop_enforcer_task.done())
    ):
        state.stop_enforcer_task = asyncio.create_task(
            _enforce_guard_stop(application, state),
            name="renaiss-telegram-stop-enforcer",
        )


async def _enforce_guard_stop(application: Any, state: TelegramInstanceGuard) -> None:
    """Bridge the narrow post_init-to-Application.start lifecycle gap."""
    while not state.stopping:
        try:
            application.stop_running()
        except Exception:
            pass
        if bool(getattr(application, "running", False)):
            return
        await asyncio.sleep(0.1)


def _fence_loss_fail_stop() -> None:
    time.sleep(FENCE_LOSS_HARD_EXIT_SECONDS)
    _hard_exit(FATAL_EXIT_CODE)


def _arm_fence_loss_fail_stop(state: TelegramInstanceGuard) -> None:
    thread = state.fence_loss_thread
    if thread is not None:
        return
    thread = threading.Thread(
        target=_fence_loss_fail_stop,
        name="renaiss-fence-loss-fail-stop",
        daemon=True,
    )
    state.fence_loss_thread = thread
    thread.start()


def _termination_listener(
    application: Any,
    state: TelegramInstanceGuard,
) -> Callable[[Any], None]:
    def terminated(_connection: Any) -> None:
        if state.stopping:
            return
        try:
            state.loop.call_soon_threadsafe(
                _mark_guard_lost,
                application,
                state,
                "database_session_terminated",
            )
        except RuntimeError:
            # A closed loop means the process is already exiting. The session
            # lock has been released by PostgreSQL regardless.
            pass

    return terminated


async def _guard_monitor(application: Any, state: TelegramInstanceGuard) -> None:
    elapsed_since_ping = 0.0
    try:
        while not state.stopping and not state.lost:
            state.last_loop_heartbeat = time.monotonic()
            await asyncio.sleep(HEARTBEAT_INTERVAL_SECONDS)
            state.last_loop_heartbeat = time.monotonic()
            elapsed_since_ping += HEARTBEAT_INTERVAL_SECONDS
            if elapsed_since_ping < DATABASE_PING_INTERVAL_SECONDS:
                continue
            elapsed_since_ping = 0.0
            try:
                responsive = await asyncio.wait_for(
                    probe_instance_lock_session(
                        state.connection,
                        lock_name=state.lock_name,
                        expected_backend_pid=state.backend_pid,
                    ),
                    timeout=DATABASE_PING_TIMEOUT_SECONDS,
                )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.critical(
                    "Telegram fence session probe failed (error=%s).",
                    type(exc).__name__,
                )
                responsive = False
            if not responsive:
                _mark_guard_lost(
                    application,
                    state,
                    "database_session_unresponsive",
                )
                return
    except asyncio.CancelledError:
        raise


async def _application_pool_sees_guard_lock(lock_name: str) -> bool:
    """Prove the app pool and dedicated lock DSN share one lock domain."""
    pool = await get_db()
    async with pool.acquire() as connection:
        return await probe_instance_lock_contender(
            connection,
            lock_name=lock_name,
        )


def _event_loop_watchdog(application: Any, state: TelegramInstanceGuard) -> None:
    graceful_requested = False
    while not state.watchdog_stop.wait(WATCHDOG_POLL_SECONDS):
        age = time.monotonic() - state.last_loop_heartbeat
        if age >= GRACEFUL_STALE_SECONDS and not graceful_requested:
            graceful_requested = True
            try:
                state.loop.call_soon_threadsafe(
                    _mark_guard_lost,
                    application,
                    state,
                    "event_loop_stalled",
                )
            except RuntimeError:
                pass
        if age >= HARD_STALE_SECONDS:
            # Do not write to stderr here: the bounded log pipe itself may be
            # the stalled resource. Task History's nonzero result is the
            # durable hard-fail signal.
            _hard_exit(FATAL_EXIT_CODE)
            return


async def _close_connection(connection: Any) -> None:
    if not _connection_is_open(connection):
        return
    try:
        await connection.close(timeout=5)
    except Exception:
        try:
            connection.terminate()
        except Exception:
            pass


async def start_telegram_instance_guard(application: Any) -> None:
    """Acquire a dedicated PostgreSQL session lock before polling starts."""
    bot_id = int(getattr(application.bot, "id", 0) or 0)
    if bot_id <= 0:
        raise RuntimeError("Telegram bot identity is required for the instance guard")
    bot_data = _bot_data(application)
    if BOT_DATA_KEY in bot_data:
        raise RuntimeError("Telegram instance guard is already active")

    connection = None
    state: TelegramInstanceGuard | None = None
    lock_name = f"renaiss:telegram_poller:{bot_id}"
    try:
        connection = await open_db_session(
            dsn_variable="RENAISS_TELEGRAM_LOCK_DATABASE_URL"
        )
        acquired = await acquire_instance_lock(connection, lock_name=lock_name)
        if not acquired:
            raise _ActivePollerError
        backend_pid = await instance_lock_owner_backend_pid(
            connection,
            lock_name=lock_name,
        )
        if backend_pid is None:
            raise RuntimeError(
                "dedicated database backend did not retain the advisory lock"
            )
        if not await _application_pool_sees_guard_lock(lock_name):
            raise RuntimeError(
                "application database and lock database do not share an advisory-lock domain"
            )

        state = TelegramInstanceGuard(
            lock_name=lock_name,
            connection=connection,
            loop=asyncio.get_running_loop(),
            startup_task=asyncio.current_task(),
            backend_pid=backend_pid,
        )
        bot_data[BOT_DATA_KEY] = state
        listener = _termination_listener(application, state)
        state.termination_listener = listener
        connection.add_termination_listener(listener)
        if not _connection_is_open(connection):
            raise RuntimeError("Telegram fence database session terminated during startup")
        state.monitor_task = asyncio.create_task(
            _guard_monitor(application, state),
            name="renaiss-telegram-instance-guard",
        )
        state.watchdog_thread = threading.Thread(
            target=_event_loop_watchdog,
            args=(application, state),
            name="renaiss-event-loop-watchdog",
            daemon=True,
        )
        state.watchdog_thread.start()
    except asyncio.CancelledError:
        if state is not None:
            await asyncio.shield(stop_telegram_instance_guard(application))
        elif connection is not None:
            await asyncio.shield(_close_connection(connection))
        raise
    except _ActivePollerError:
        if connection is not None:
            await _close_connection(connection)
        bot_data.pop(BOT_DATA_KEY, None)
        raise RuntimeError(
            "Another Renaiss Telegram poller holds the active lock"
        ) from None
    except Exception as exc:
        if state is not None:
            await stop_telegram_instance_guard(application)
        elif connection is not None:
            await _close_connection(connection)
        bot_data.pop(BOT_DATA_KEY, None)
        logger.critical(
            "Telegram single-poller lock acquisition failed (error=%s).",
            type(exc).__name__,
        )
        raise RuntimeError("Telegram single-poller lock acquisition failed") from None


def confirm_telegram_instance_guard(application: Any) -> None:
    """Recheck the lock immediately before returning from PTB post_init."""
    state = getattr(application, "bot_data", {}).get(BOT_DATA_KEY)
    if not isinstance(state, TelegramInstanceGuard) or not telegram_instance_guard_ready(
        application
    ):
        raise RuntimeError("Telegram single-poller fence was lost during startup")
    state.startup_task = None


async def stop_telegram_instance_guard(application: Any) -> None:
    bot_data = getattr(application, "bot_data", None)
    if not isinstance(bot_data, dict):
        return
    state = bot_data.get(BOT_DATA_KEY)
    if not isinstance(state, TelegramInstanceGuard):
        return

    state.stopping = True
    state.watchdog_stop.set()
    monitor = state.monitor_task
    if monitor is not None and monitor is not asyncio.current_task():
        monitor.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await monitor
    enforcer = state.stop_enforcer_task
    if enforcer is not None and enforcer is not asyncio.current_task():
        enforcer.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await enforcer

    listener = state.termination_listener
    if listener is not None:
        try:
            state.connection.remove_termination_listener(listener)
        except Exception:
            pass

    if _connection_is_open(state.connection):
        try:
            released = await asyncio.wait_for(
                release_instance_lock(
                    state.connection,
                    lock_name=state.lock_name,
                ),
                timeout=5,
            )
        except Exception as exc:
            logger.warning(
                "Telegram single-poller unlock failed (error=%s).",
                type(exc).__name__,
            )
        else:
            if not released and not state.lost:
                logger.warning("Telegram single-poller lock was not owned at shutdown.")
    await _close_connection(state.connection)

    thread = state.watchdog_thread
    if (
        thread is not None
        and thread is not threading.current_thread()
        and thread.ident is not None
    ):
        try:
            thread.join(timeout=2)
        except RuntimeError:
            pass
        else:
            if thread.is_alive():
                logger.warning("Telegram event-loop watchdog did not stop promptly.")
    bot_data.pop(BOT_DATA_KEY, None)
