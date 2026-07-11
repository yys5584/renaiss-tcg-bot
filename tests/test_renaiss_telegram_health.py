"""Loopback health must stay optional, local, and tied to PTB readiness."""

from __future__ import annotations

import asyncio
import json
import logging
from types import SimpleNamespace
from unittest.mock import ANY, AsyncMock, Mock

import pytest

import renaiss_bot.main as telegram_main
import renaiss_bot.telegram_health as telegram_health
from renaiss_bot.services import instance_guard


def _payload(response):
    return json.loads(response.text)


def test_health_listener_is_disabled_by_default_and_ignores_port(monkeypatch):
    monkeypatch.delenv("RENAISS_TELEGRAM_HEALTH_ENABLED", raising=False)
    monkeypatch.setenv("RENAISS_TELEGRAM_HEALTH_PORT", "not-a-port")

    assert not telegram_health.health_enabled()
    assert telegram_health.health_configuration_issues() == []


@pytest.mark.parametrize("port", ["not-a-port", "0", "65536"])
def test_enabled_health_listener_rejects_invalid_port(monkeypatch, port):
    monkeypatch.setenv("RENAISS_TELEGRAM_HEALTH_ENABLED", "1")
    monkeypatch.setenv("RENAISS_TELEGRAM_HEALTH_PORT", port)

    issues = telegram_health.health_configuration_issues()

    assert len(issues) == 1
    assert "RENAISS_TELEGRAM_HEALTH_PORT" in issues[0]


def test_health_listener_rejects_boolean_typo(monkeypatch):
    monkeypatch.setenv("RENAISS_TELEGRAM_HEALTH_ENABLED", "treu")

    assert telegram_health.health_configuration_issues() == [
        "RENAISS_TELEGRAM_HEALTH_ENABLED must be a boolean value"
    ]


async def test_liveness_and_readiness_are_distinct_without_external_calls():
    polling = False
    server = telegram_health.TelegramHealthServer(
        port=18081,
        polling_ready=lambda: polling,
    )

    live = await server.livez(SimpleNamespace())
    starting = await server.readyz(SimpleNamespace())
    server.mark_startup_ready()
    initialized_not_polling = await server.readyz(SimpleNamespace())
    polling = True
    ready = await server.readyz(SimpleNamespace())
    server.mark_not_ready()
    stopping = await server.readyz(SimpleNamespace())

    assert live.status == 200
    assert _payload(live) == {"ok": True}
    assert live.headers["Cache-Control"] == "no-store"
    assert starting.status == 503
    assert initialized_not_polling.status == 503
    assert ready.status == 200
    assert _payload(ready) == {"ok": True, "ready": True}
    assert stopping.status == 503


async def test_readiness_fails_when_application_database_probe_fails():
    database_ready = AsyncMock(return_value=False)
    server = telegram_health.TelegramHealthServer(
        port=18081,
        polling_ready=lambda: True,
        database_ready=database_ready,
    )
    server.mark_startup_ready()

    response = await server.readyz(SimpleNamespace())

    assert response.status == 503
    assert _payload(response) == {"ok": False, "ready": False}
    database_ready.assert_awaited_once_with()


async def test_listener_binds_fixed_ipv4_loopback_and_cleans_runner(monkeypatch):
    events = []
    captured = {}

    class Runner:
        def __init__(self, app, *, access_log):
            assert access_log is None
            self.app = app
            captured["app"] = app

        async def setup(self):
            events.append("setup")

        async def cleanup(self):
            events.append("cleanup")

    class Site:
        def __init__(
            self,
            runner,
            *,
            host,
            port,
            reuse_address,
            shutdown_timeout,
        ):
            assert isinstance(runner, Runner)
            events.append((host, port, reuse_address, shutdown_timeout))

        async def start(self):
            events.append("start")

    monkeypatch.setattr(telegram_health.web, "AppRunner", Runner)
    monkeypatch.setattr(telegram_health.web, "TCPSite", Site)
    monkeypatch.setenv("RENAISS_TELEGRAM_HEALTH_HOST", "0.0.0.0")
    server = telegram_health.TelegramHealthServer(
        port=18081,
        polling_ready=lambda: False,
    )

    await server.start()
    await server.close()

    assert (telegram_health.LOOPBACK_HOST, 18081, False, 2.0) in events
    assert "start" in events
    assert events[-1] == "cleanup"
    assert {
        resource.canonical for resource in captured["app"].router.resources()
    } == {"/livez", "/readyz"}


async def test_listener_cleanup_failure_keeps_state_for_retry():
    cleanup = AsyncMock(side_effect=[RuntimeError("first cleanup failed"), None])
    runner = SimpleNamespace(cleanup=cleanup)
    server = telegram_health.TelegramHealthServer(
        port=18081,
        polling_ready=lambda: False,
    )
    server._runner = runner
    server._site = object()
    server.mark_startup_ready()

    with pytest.raises(RuntimeError, match="first cleanup failed"):
        await server.close()

    assert server._runner is runner
    assert not server.ready()

    await server.close()

    assert server._runner is None
    assert server._site is None
    assert cleanup.await_count == 2


def test_telegram_startup_includes_health_configuration_gate(monkeypatch):
    monkeypatch.setenv("RENAISS_TELEGRAM_HEALTH_ENABLED", "1")
    monkeypatch.setenv("RENAISS_TELEGRAM_HEALTH_PORT", "public")

    issues = telegram_main._production_startup_issues()

    assert any("RENAISS_TELEGRAM_HEALTH_PORT" in issue for issue in issues)


async def test_health_startup_failure_redacts_listener_exception(
    monkeypatch,
    caplog,
):
    sentinel = "secret-listener-detail"
    monkeypatch.setenv("RENAISS_TELEGRAM_HEALTH_ENABLED", "1")
    monkeypatch.setenv("RENAISS_TELEGRAM_HEALTH_PORT", "18081")
    monkeypatch.setattr(
        telegram_health.TelegramHealthServer,
        "start",
        AsyncMock(side_effect=RuntimeError(sentinel)),
    )
    application = SimpleNamespace(bot_data={})

    with caplog.at_level(logging.ERROR, logger=telegram_health.__name__):
        with pytest.raises(RuntimeError, match="listener startup failed") as exc_info:
            await telegram_health.start_telegram_health(application)

    assert sentinel not in str(exc_info.value)
    assert sentinel not in caplog.text
    assert "RuntimeError" in caplog.text


async def test_application_health_tracks_active_polling_and_closes(monkeypatch):
    monkeypatch.setenv("RENAISS_TELEGRAM_HEALTH_ENABLED", "true")
    monkeypatch.setenv("RENAISS_TELEGRAM_HEALTH_PORT", "18081")
    start = AsyncMock()
    close = AsyncMock()
    monkeypatch.setattr(telegram_health.TelegramHealthServer, "start", start)
    monkeypatch.setattr(telegram_health.TelegramHealthServer, "close", close)
    application = SimpleNamespace(
        bot_data={
            instance_guard.BOT_DATA_KEY: instance_guard.TelegramInstanceGuard(
                lock_name="renaiss:telegram_poller:999",
                connection=SimpleNamespace(is_closed=lambda: False),
                loop=asyncio.get_running_loop(),
                startup_task=None,
            )
        },
        running=False,
        updater=SimpleNamespace(running=False),
    )

    server = await telegram_health.start_telegram_health(application)
    assert server is not None
    server.mark_startup_ready()
    assert not server.ready()

    application.running = True
    application.updater.running = True
    assert server.ready()
    application.bot_data[instance_guard.BOT_DATA_KEY].lost = True
    assert not server.ready()

    await telegram_health.stop_telegram_health(application)

    start.assert_awaited_once_with()
    close.assert_awaited_once_with()
    assert instance_guard.BOT_DATA_KEY in application.bot_data


async def test_post_init_marks_ready_only_after_all_runtime_gates(monkeypatch):
    order = []
    health_server = SimpleNamespace(
        mark_startup_ready=Mock(side_effect=lambda: order.append("ready"))
    )

    async def start_health(application):
        order.append("health")
        return health_server

    async def initialize(application):
        order.append("runtime")

    monkeypatch.setattr(telegram_main, "start_telegram_health", start_health)
    monkeypatch.setattr(telegram_main, "_initialize_telegram_runtime", initialize)

    await telegram_main.post_init(SimpleNamespace())

    assert order == ["health", "runtime", "ready"]


async def test_post_init_failure_closes_health_without_masking_error(monkeypatch):
    health_server = SimpleNamespace(mark_startup_ready=Mock())
    stop = AsyncMock()
    monkeypatch.setattr(
        telegram_main,
        "start_telegram_health",
        AsyncMock(return_value=health_server),
    )
    monkeypatch.setattr(
        telegram_main,
        "_initialize_telegram_runtime",
        AsyncMock(side_effect=RuntimeError("startup gate failed")),
    )
    monkeypatch.setattr(telegram_main, "stop_telegram_health", stop)

    with pytest.raises(RuntimeError, match="startup gate failed"):
        await telegram_main.post_init(SimpleNamespace())

    stop.assert_awaited_once_with(ANY)
    health_server.mark_startup_ready.assert_not_called()


async def test_post_shutdown_closes_health_before_other_resources(monkeypatch):
    order = []

    async def stop_health(application):
        order.append("health")

    async def close_renderer():
        order.append("renderer")

    async def stop_guard(application):
        order.append("guard")

    async def close_db():
        order.append("database")

    monkeypatch.setattr(telegram_main, "stop_telegram_health", stop_health)
    monkeypatch.setattr(
        telegram_main,
        "stop_telegram_instance_guard",
        stop_guard,
    )
    monkeypatch.setattr(
        "renaiss_bot.renderers.playwright_render.close_renderer",
        close_renderer,
    )
    monkeypatch.setattr("renaiss_bot.database.connection.close_db", close_db)

    await telegram_main.post_shutdown(SimpleNamespace())

    assert order == ["health", "guard", "renderer", "database"]
