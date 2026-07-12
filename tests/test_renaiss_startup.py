"""Startup must fail closed before competitive handlers/jobs are advertised."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

import renaiss_bot.main as telegram_main
import renaiss_bot.referral_server as referral_server
import renaiss_bot.services.market as market_service
from renaiss_bot.adapters.discord import main as discord_main


def test_telegram_logging_replaces_stale_root_handlers(monkeypatch):
    configured = {}
    monkeypatch.setattr(
        telegram_main.logging,
        "basicConfig",
        lambda **kwargs: configured.update(kwargs),
    )

    telegram_main._configure_logging()

    assert configured["force"] is True
    assert isinstance(configured["handlers"][0], telegram_main._FdStderrHandler)


def test_fd_stderr_handler_writes_to_service_capture_fd(monkeypatch):
    writes = []
    monkeypatch.setattr(
        telegram_main.os,
        "write",
        lambda fd, payload: writes.append((fd, payload)) or len(payload),
    )
    handler = telegram_main._FdStderrHandler()
    handler.setFormatter(telegram_main.logging.Formatter("%(message)s"))

    handler.emit(
        telegram_main.logging.LogRecord(
            "renaiss-test",
            telegram_main.logging.INFO,
            __file__,
            1,
            "service-ready",
            (),
            None,
        )
    )

    assert writes == [(2, b"service-ready\n")]


@pytest.fixture(autouse=True)
def _disable_real_runtime_env_loading(monkeypatch):
    monkeypatch.setattr(telegram_main, "load_runtime_environment", Mock())
    monkeypatch.setattr(discord_main, "load_runtime_environment", Mock())
    monkeypatch.setattr(referral_server, "load_runtime_environment", Mock())
    monkeypatch.setattr(
        telegram_main,
        "start_telegram_instance_guard",
        AsyncMock(),
    )
    monkeypatch.setattr(
        telegram_main,
        "stop_telegram_instance_guard",
        AsyncMock(),
    )
    monkeypatch.setattr(
        telegram_main,
        "confirm_telegram_instance_guard",
        Mock(),
    )


def _application():
    return SimpleNamespace(
        bot=SimpleNamespace(
            id=999,
            set_my_commands=AsyncMock(),
            get_chat=AsyncMock(return_value=SimpleNamespace(type="supergroup")),
            get_chat_member=AsyncMock(
                return_value=SimpleNamespace(status="administrator")
            ),
        ),
        bot_data={},
        job_queue=object(),
    )


def _enable_daily_pick_env(monkeypatch):
    values = {
        "DATABASE_URL": "postgresql://example.invalid/renaiss",
        "RENAISS_OFFICIAL_CHAT_ID": "-1001",
        "RENAISS_DAILY_PICK_ENABLED": "1",
        "RENAISS_API_KEY": "key",
        "RENAISS_API_SECRET": "secret",
        "RENAISS_API_ITEM_BY_NO_PATH": "/cards/{item_no}",
        "RENAISS_API_EXACT_CONTRACT": "item-by-no-v1",
        "RENAISS_API_EXACT_VALUATION_METHOD": "median",
        "RENAISS_DAILY_PICK_PROBE_CARD_NAME": "Probe Card",
        "RENAISS_DAILY_PICK_PROBE_SET_NAME": "Probe Set",
        "RENAISS_DAILY_PICK_PROBE_ITEM_NO": "1/1",
    }
    for name, value in values.items():
        monkeypatch.setenv(name, value)
    monkeypatch.delenv("RENAISS_SKIP_DB", raising=False)
    monkeypatch.delenv("RENAISS_API_MOCK_JSON", raising=False)


async def test_command_discovery_hides_private_pack_experiment_by_default(monkeypatch):
    application = _application()
    monkeypatch.delenv("RENAISS_PRIVATE_FREE_PACKS_ENABLED", raising=False)
    monkeypatch.setattr(telegram_main, "daily_pick_enabled", lambda: False)

    await telegram_main._configure_commands(application)

    commands = application.bot.set_my_commands.await_args.args[0]
    names = {command.command for command in commands}
    assert "open" not in names
    assert "pack" not in names


async def test_command_discovery_shows_private_packs_only_after_opt_in(monkeypatch):
    application = _application()
    monkeypatch.setenv("RENAISS_PRIVATE_FREE_PACKS_ENABLED", "1")
    monkeypatch.setattr(telegram_main, "daily_pick_enabled", lambda: False)

    await telegram_main._configure_commands(application)

    commands = application.bot.set_my_commands.await_args.args[0]
    names = {command.command for command in commands}
    assert {"open", "pack"} <= names


def test_telegram_startup_rejects_insecure_database_tls(monkeypatch):
    monkeypatch.setenv("RENAISS_DB_SSL_INSECURE", "true")

    issues = telegram_main._production_startup_issues()

    assert "RENAISS_DB_SSL_INSECURE must be disabled for the Telegram bot" in issues


async def test_post_init_registers_jobs_only_after_schema_is_ready(monkeypatch):
    order = []
    pool = object()
    get_db = AsyncMock(return_value=pool)

    async def create_tables(received_pool):
        assert received_pool is pool
        order.append("schema")

    register_jobs = Mock(side_effect=lambda _application: order.append("jobs"))
    start_guard = AsyncMock(side_effect=lambda _application: order.append("guard"))
    monkeypatch.delenv("RENAISS_SKIP_DB", raising=False)
    monkeypatch.setenv("DATABASE_URL", "postgresql://example.invalid/renaiss")
    monkeypatch.setenv("RENAISS_OFFICIAL_CHAT_ID", "-1001")
    monkeypatch.setattr("renaiss_bot.database.connection.get_db", get_db)
    monkeypatch.setattr("renaiss_bot.database.schema.create_tables", create_tables)
    monkeypatch.setattr(telegram_main, "start_telegram_instance_guard", start_guard)
    monkeypatch.setattr("renaiss_bot.jobs.register_jobs", register_jobs)
    monkeypatch.setattr(
        "renaiss_bot.jobs.recover_unfinished_spawns",
        AsyncMock(side_effect=lambda _application: order.append("spawn-recovery")),
    )

    await telegram_main.post_init(_application())

    assert order[:3] == ["schema", "guard", "jobs"]


async def test_post_init_aborts_and_never_registers_jobs_when_schema_fails(monkeypatch):
    application = _application()
    register_jobs = Mock()
    monkeypatch.delenv("RENAISS_SKIP_DB", raising=False)
    monkeypatch.setenv("DATABASE_URL", "postgresql://example.invalid/renaiss")
    monkeypatch.setattr(
        "renaiss_bot.database.connection.get_db",
        AsyncMock(side_effect=RuntimeError("database unavailable")),
    )
    monkeypatch.setattr("renaiss_bot.jobs.register_jobs", register_jobs)

    with pytest.raises(RuntimeError, match="DB initialization failed"):
        await telegram_main.post_init(application)

    register_jobs.assert_not_called()
    application.bot.set_my_commands.assert_not_awaited()


async def test_post_init_rejects_duplicate_poller_before_chat_and_jobs(monkeypatch):
    application = _application()
    start_guard = AsyncMock(
        side_effect=RuntimeError("Another Renaiss Telegram poller holds the active lease")
    )
    stop_guard = AsyncMock()
    register_jobs = Mock()
    monkeypatch.delenv("RENAISS_SKIP_DB", raising=False)
    monkeypatch.setenv("DATABASE_URL", "postgresql://example.invalid/renaiss")
    monkeypatch.setenv("RENAISS_OFFICIAL_CHAT_ID", "-1001")
    monkeypatch.setattr(
        "renaiss_bot.database.connection.get_db",
        AsyncMock(return_value=object()),
    )
    monkeypatch.setattr("renaiss_bot.database.schema.create_tables", AsyncMock())
    monkeypatch.setattr(telegram_main, "start_telegram_instance_guard", start_guard)
    monkeypatch.setattr(telegram_main, "stop_telegram_instance_guard", stop_guard)
    monkeypatch.setattr("renaiss_bot.jobs.register_jobs", register_jobs)

    with pytest.raises(RuntimeError, match="Another Renaiss Telegram poller"):
        await telegram_main.post_init(application)

    start_guard.assert_awaited_once_with(application)
    stop_guard.assert_awaited_once_with(application)
    application.bot.get_chat.assert_not_awaited()
    application.bot.set_my_commands.assert_not_awaited()
    register_jobs.assert_not_called()


async def test_post_init_fails_readiness_when_spawn_recovery_is_incomplete(monkeypatch):
    application = _application()
    monkeypatch.delenv("RENAISS_SKIP_DB", raising=False)
    monkeypatch.setenv("DATABASE_URL", "postgresql://example.invalid/renaiss")
    monkeypatch.setenv("RENAISS_OFFICIAL_CHAT_ID", "-1001")
    monkeypatch.setattr(
        "renaiss_bot.database.connection.get_db",
        AsyncMock(return_value=object()),
    )
    monkeypatch.setattr("renaiss_bot.database.schema.create_tables", AsyncMock())
    monkeypatch.setattr("renaiss_bot.jobs.register_jobs", Mock())
    monkeypatch.setattr(
        "renaiss_bot.jobs.recover_unfinished_spawns",
        AsyncMock(side_effect=RuntimeError("one prompt remains")),
    )
    with pytest.raises(RuntimeError, match="spawn recovery readiness failed"):
        await telegram_main.post_init(application)


async def test_post_init_rejects_wrong_telegram_bot_before_database(monkeypatch):
    application = _application()
    get_db = AsyncMock()
    monkeypatch.setenv("RENAISS_EXPECTED_BOT_ID", "123")
    monkeypatch.setenv("DATABASE_URL", "postgresql://example.invalid/renaiss")
    monkeypatch.setattr("renaiss_bot.database.connection.get_db", get_db)

    with pytest.raises(RuntimeError, match="bot identity"):
        await telegram_main.post_init(application)

    get_db.assert_not_awaited()
    application.bot.set_my_commands.assert_not_awaited()


async def test_post_init_rejects_missing_job_queue_before_database(monkeypatch):
    application = _application()
    application.job_queue = None
    get_db = AsyncMock()
    monkeypatch.delenv("RENAISS_SKIP_DB", raising=False)
    monkeypatch.setenv("DATABASE_URL", "postgresql://example.invalid/renaiss")
    monkeypatch.setattr("renaiss_bot.database.connection.get_db", get_db)

    with pytest.raises(RuntimeError, match="JobQueue is required"):
        await telegram_main.post_init(application)

    get_db.assert_not_awaited()
    application.bot.get_chat.assert_not_awaited()
    application.bot.set_my_commands.assert_not_awaited()


async def test_post_init_aborts_when_public_chat_is_not_configured(monkeypatch):
    application = _application()
    register_jobs = Mock()
    monkeypatch.delenv("RENAISS_SKIP_DB", raising=False)
    monkeypatch.setenv("DATABASE_URL", "postgresql://example.invalid/renaiss")
    monkeypatch.delenv("RENAISS_OFFICIAL_CHAT_ID", raising=False)
    monkeypatch.delenv("RENAISS_QUIZ_CHAT_ID", raising=False)
    monkeypatch.setattr(
        "renaiss_bot.database.connection.get_db",
        AsyncMock(return_value=object()),
    )
    monkeypatch.setattr("renaiss_bot.database.schema.create_tables", AsyncMock())
    monkeypatch.setattr("renaiss_bot.jobs.register_jobs", register_jobs)

    with pytest.raises(RuntimeError, match="RENAISS_OFFICIAL_CHAT_ID"):
        await telegram_main.post_init(application)

    register_jobs.assert_not_called()
    application.bot.set_my_commands.assert_not_awaited()


async def test_post_init_rejects_positive_private_chat_id(monkeypatch):
    application = _application()
    monkeypatch.delenv("RENAISS_SKIP_DB", raising=False)
    monkeypatch.setenv("DATABASE_URL", "postgresql://example.invalid/renaiss")
    monkeypatch.setenv("RENAISS_OFFICIAL_CHAT_ID", "12345")
    monkeypatch.setattr("renaiss_bot.database.connection.get_db", AsyncMock(return_value=object()))
    monkeypatch.setattr("renaiss_bot.database.schema.create_tables", AsyncMock())

    with pytest.raises(RuntimeError, match="negative Telegram group"):
        await telegram_main.post_init(application)

    application.bot.get_chat.assert_not_awaited()
    application.bot.set_my_commands.assert_not_awaited()


async def test_daily_pick_startup_requires_admin_and_live_partner_probe(monkeypatch):
    application = _application()
    _enable_daily_pick_env(monkeypatch)
    monkeypatch.setattr("renaiss_bot.database.connection.get_db", AsyncMock(return_value=object()))
    monkeypatch.setattr("renaiss_bot.database.schema.create_tables", AsyncMock())
    monkeypatch.setattr(telegram_main, "daily_pick_requested", lambda: True)
    probe = AsyncMock(side_effect=RuntimeError("known tuple rejected"))
    monkeypatch.setattr(telegram_main, "verify_daily_pick_partner_ready", probe)
    register_jobs = Mock()
    monkeypatch.setattr("renaiss_bot.jobs.register_jobs", register_jobs)
    monkeypatch.setattr("renaiss_bot.jobs.recover_unfinished_spawns", AsyncMock())

    await telegram_main.post_init(application)

    application.bot.get_chat_member.assert_awaited_once_with(-1001, 999)
    probe.assert_awaited_once()
    application.bot.set_my_commands.assert_awaited_once()
    commands = application.bot.set_my_commands.await_args.args[0]
    assert "market" not in {command.command for command in commands}
    assert not market_service.daily_pick_enabled()
    register_jobs.assert_called_once()


async def test_daily_pick_startup_rejects_non_admin_bot(monkeypatch):
    application = _application()
    application.bot.get_chat_member.return_value = SimpleNamespace(status="member")
    _enable_daily_pick_env(monkeypatch)
    monkeypatch.setattr("renaiss_bot.database.connection.get_db", AsyncMock(return_value=object()))
    monkeypatch.setattr("renaiss_bot.database.schema.create_tables", AsyncMock())
    monkeypatch.setattr(telegram_main, "daily_pick_requested", lambda: True)
    probe = AsyncMock()
    monkeypatch.setattr(telegram_main, "verify_daily_pick_partner_ready", probe)
    monkeypatch.setattr("renaiss_bot.jobs.register_jobs", Mock())
    monkeypatch.setattr("renaiss_bot.jobs.recover_unfinished_spawns", AsyncMock())

    await telegram_main.post_init(application)

    probe.assert_not_awaited()
    application.bot.set_my_commands.assert_awaited_once()
    commands = application.bot.set_my_commands.await_args.args[0]
    assert "market" not in {command.command for command in commands}
    assert not market_service.daily_pick_enabled()


async def test_daily_pick_success_opens_command_then_next_start_closes_stale_latch(monkeypatch):
    application = _application()
    _enable_daily_pick_env(monkeypatch)
    monkeypatch.setattr(
        "renaiss_bot.database.connection.get_db",
        AsyncMock(return_value=object()),
    )
    monkeypatch.setattr("renaiss_bot.database.schema.create_tables", AsyncMock())
    monkeypatch.setattr("renaiss_bot.jobs.register_jobs", Mock())
    monkeypatch.setattr("renaiss_bot.jobs.recover_unfinished_spawns", AsyncMock())

    async def successful_probe():
        market_service._daily_pick_preflight_ready = True
        return SimpleNamespace(status="exact")

    monkeypatch.setattr(telegram_main, "verify_daily_pick_partner_ready", successful_probe)

    await telegram_main.post_init(application)

    first_commands = application.bot.set_my_commands.await_args_list[-1].args[0]
    assert "market" in {command.command for command in first_commands}
    assert market_service.daily_pick_enabled()

    monkeypatch.setenv("RENAISS_DAILY_PICK_ENABLED", "0")
    await telegram_main.post_init(application)

    second_commands = application.bot.set_my_commands.await_args_list[-1].args[0]
    assert "market" not in {command.command for command in second_commands}
    assert not market_service.daily_pick_enabled()


async def test_skip_db_mode_keeps_scheduled_jobs_closed(monkeypatch):
    application = _application()
    register_jobs = Mock()
    monkeypatch.setenv("RENAISS_SKIP_DB", "1")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setattr("renaiss_bot.jobs.register_jobs", register_jobs)

    await telegram_main.post_init(application)

    register_jobs.assert_not_called()
    application.bot.set_my_commands.assert_awaited_once()


async def test_telegram_shutdown_closes_renderer_and_database(monkeypatch):
    close_renderer = AsyncMock()
    close_db = AsyncMock()
    monkeypatch.setattr(
        "renaiss_bot.renderers.playwright_render.close_renderer",
        close_renderer,
    )
    monkeypatch.setattr("renaiss_bot.database.connection.close_db", close_db)

    await telegram_main.post_shutdown(_application())

    close_renderer.assert_awaited_once()
    close_db.assert_awaited_once()


def test_missing_tokens_exit_nonzero(monkeypatch):
    monkeypatch.delenv("RENAISS_BOT_TOKEN", raising=False)
    monkeypatch.delenv("RENAISS_DISCORD_TOKEN", raising=False)

    with pytest.raises(SystemExit) as telegram_exit:
        telegram_main.main()
    with pytest.raises(SystemExit) as discord_exit:
        discord_main.main()

    assert telegram_exit.value.code == 2
    assert discord_exit.value.code == 2


def test_entrypoints_reject_missing_database_before_network_clients(monkeypatch):
    monkeypatch.setenv("RENAISS_BOT_TOKEN", "telegram-token")
    monkeypatch.setenv("RENAISS_OFFICIAL_CHAT_ID", "-1001")
    monkeypatch.setenv("RENAISS_DISCORD_TOKEN", "discord-token")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("RENAISS_SKIP_DB", raising=False)
    telegram_builder = Mock()
    discord_run = Mock()
    monkeypatch.setattr(telegram_main.Application, "builder", telegram_builder)
    monkeypatch.setattr(discord_main.client, "run", discord_run)

    with pytest.raises(SystemExit) as telegram_exit:
        telegram_main.main()
    with pytest.raises(SystemExit) as discord_exit:
        discord_main.main()

    assert telegram_exit.value.code == 2
    assert discord_exit.value.code == 2
    telegram_builder.assert_not_called()
    discord_run.assert_not_called()


def test_telegram_entrypoint_rejects_invalid_official_chat_before_network(monkeypatch):
    monkeypatch.setenv("RENAISS_BOT_TOKEN", "telegram-token")
    monkeypatch.setenv("DATABASE_URL", "postgresql://example.invalid/renaiss")
    monkeypatch.setenv("RENAISS_OFFICIAL_CHAT_ID", "1234")
    monkeypatch.delenv("RENAISS_SKIP_DB", raising=False)
    telegram_builder = Mock()
    monkeypatch.setattr(telegram_main.Application, "builder", telegram_builder)

    with pytest.raises(SystemExit) as telegram_exit:
        telegram_main.main()

    assert telegram_exit.value.code == 2
    telegram_builder.assert_not_called()


def test_discord_entrypoint_rejects_skip_db_before_network(monkeypatch):
    monkeypatch.setenv("RENAISS_DISCORD_TOKEN", "discord-token")
    monkeypatch.setenv("DATABASE_URL", "postgresql://example.invalid/renaiss")
    monkeypatch.setenv("RENAISS_SKIP_DB", "1")
    discord_run = Mock()
    monkeypatch.setattr(discord_main.client, "run", discord_run)

    with pytest.raises(SystemExit) as discord_exit:
        discord_main.main()

    assert discord_exit.value.code == 2
    discord_run.assert_not_called()


def test_discord_entrypoint_rejects_implicit_global_sync(monkeypatch):
    monkeypatch.setenv("RENAISS_DISCORD_TOKEN", "discord-token")
    monkeypatch.setenv("RENAISS_EXPECTED_DISCORD_BOT_ID", "999")
    monkeypatch.setenv("DATABASE_URL", "postgresql://example.invalid/renaiss")
    monkeypatch.delenv("RENAISS_DISCORD_GUILD_ID", raising=False)
    monkeypatch.delenv("RENAISS_DISCORD_ALLOW_GLOBAL_SYNC", raising=False)
    monkeypatch.delenv("RENAISS_SKIP_DB", raising=False)
    monkeypatch.delenv("RENAISS_API_MOCK_JSON", raising=False)
    discord_run = Mock()
    monkeypatch.setattr(discord_main.client, "run", discord_run)

    with pytest.raises(SystemExit):
        discord_main.main()

    discord_run.assert_not_called()


def test_discord_identity_guard_rejects_wrong_logged_in_bot(monkeypatch):
    monkeypatch.setenv("RENAISS_EXPECTED_DISCORD_BOT_ID", "999")

    assert "does not match" in (
        discord_main._discord_bot_identity_issue(123) or ""
    )


async def test_discord_setup_rejects_stale_global_commands_before_database(monkeypatch):
    monkeypatch.setenv("RENAISS_DISCORD_GUILD_ID", "123456")
    monkeypatch.delenv("RENAISS_DISCORD_ALLOW_GLOBAL_SYNC", raising=False)
    monkeypatch.setattr(
        discord_main,
        "_discord_bot_identity_issue",
        lambda actual_bot_id, require_actual=False: None,
    )
    fetch_commands = AsyncMock(return_value=[SimpleNamespace(name="open")])
    monkeypatch.setattr(discord_main.client.tree, "fetch_commands", fetch_commands)
    init_db = AsyncMock()
    monkeypatch.setattr(discord_main, "_init_db", init_db)

    with pytest.raises(RuntimeError, match="global commands already exist"):
        await discord_main.client.setup_hook()

    fetch_commands.assert_awaited_once_with()
    init_db.assert_not_awaited()


def test_telegram_entrypoint_rejects_skip_db_before_network(monkeypatch):
    monkeypatch.setenv("RENAISS_BOT_TOKEN", "telegram-token")
    monkeypatch.setenv("RENAISS_SKIP_DB", "1")
    telegram_builder = Mock()
    monkeypatch.setattr(telegram_main.Application, "builder", telegram_builder)

    with pytest.raises(SystemExit) as telegram_exit:
        telegram_main.main()

    assert telegram_exit.value.code == 2
    telegram_builder.assert_not_called()


def test_telegram_entrypoint_rejects_missing_job_queue_before_polling(monkeypatch):
    application = SimpleNamespace(job_queue=None, run_polling=Mock())

    class Builder:
        def token(self, _token):
            return self

        def post_init(self, _callback):
            return self

        def post_shutdown(self, _callback):
            return self

        def concurrent_updates(self, _enabled):
            return self

        def build(self):
            return application

    monkeypatch.setenv("RENAISS_BOT_TOKEN", "111111:test-secret")
    monkeypatch.setenv("RENAISS_EXPECTED_BOT_ID", "111111")
    monkeypatch.setenv("RENAISS_OFFICIAL_CHAT_ID", "-1001")
    monkeypatch.setenv("DATABASE_URL", "postgresql://example.invalid/renaiss")
    monkeypatch.setenv(
        "RENAISS_TELEGRAM_LOCK_DATABASE_URL",
        "postgresql://direct.example.invalid/renaiss",
    )
    monkeypatch.delenv("RENAISS_SKIP_DB", raising=False)
    monkeypatch.delenv("RENAISS_API_MOCK_JSON", raising=False)
    monkeypatch.setattr(telegram_main.Application, "builder", Builder)
    register_handlers = Mock()
    monkeypatch.setattr(telegram_main, "register_handlers", register_handlers)

    with pytest.raises(SystemExit) as telegram_exit:
        telegram_main.main()

    assert telegram_exit.value.code == 2
    register_handlers.assert_not_called()
    application.run_polling.assert_not_called()


def test_telegram_entrypoint_returns_nonzero_after_fatal_guard_stop(monkeypatch):
    application = SimpleNamespace(bot_data={}, job_queue=object())

    def run_polling(**kwargs):
        assert kwargs == {"drop_pending_updates": True}
        application.bot_data["renaiss_telegram_fatal_exit_code"] = 75

    application.run_polling = Mock(side_effect=run_polling)

    class Builder:
        def token(self, _token):
            return self

        def post_init(self, _callback):
            return self

        def post_shutdown(self, _callback):
            return self

        def concurrent_updates(self, _enabled):
            return self

        def build(self):
            return application

    monkeypatch.setenv("RENAISS_BOT_TOKEN", "111111:test-secret")
    monkeypatch.setenv("RENAISS_EXPECTED_BOT_ID", "111111")
    monkeypatch.setenv("RENAISS_OFFICIAL_CHAT_ID", "-1001")
    monkeypatch.setenv("DATABASE_URL", "postgresql://example.invalid/renaiss")
    monkeypatch.setenv(
        "RENAISS_TELEGRAM_LOCK_DATABASE_URL",
        "postgresql://direct.example.invalid/renaiss",
    )
    monkeypatch.delenv("RENAISS_SKIP_DB", raising=False)
    monkeypatch.delenv("RENAISS_API_MOCK_JSON", raising=False)
    monkeypatch.setattr(telegram_main.Application, "builder", Builder)
    monkeypatch.setattr(telegram_main, "register_handlers", Mock())

    with pytest.raises(SystemExit) as telegram_exit:
        telegram_main.main()

    assert telegram_exit.value.code == 75
    application.run_polling.assert_called_once_with(drop_pending_updates=True)


def test_telegram_entrypoint_normalizes_startup_guard_cancellation_to_75(monkeypatch):
    application = SimpleNamespace(
        bot_data={"renaiss_telegram_fatal_exit_code": 75},
        job_queue=object(),
        run_polling=Mock(side_effect=asyncio.CancelledError()),
    )

    class Builder:
        def token(self, _token):
            return self

        def post_init(self, _callback):
            return self

        def post_shutdown(self, _callback):
            return self

        def concurrent_updates(self, _enabled):
            return self

        def build(self):
            return application

    monkeypatch.setenv("RENAISS_BOT_TOKEN", "111111:test-secret")
    monkeypatch.setenv("RENAISS_EXPECTED_BOT_ID", "111111")
    monkeypatch.setenv("RENAISS_OFFICIAL_CHAT_ID", "-1001")
    monkeypatch.setenv("DATABASE_URL", "postgresql://example.invalid/renaiss")
    monkeypatch.setenv(
        "RENAISS_TELEGRAM_LOCK_DATABASE_URL",
        "postgresql://direct.example.invalid/renaiss",
    )
    monkeypatch.delenv("RENAISS_SKIP_DB", raising=False)
    monkeypatch.delenv("RENAISS_API_MOCK_JSON", raising=False)
    monkeypatch.setattr(telegram_main.Application, "builder", Builder)
    monkeypatch.setattr(telegram_main, "register_handlers", Mock())

    with pytest.raises(SystemExit) as telegram_exit:
        telegram_main.main()

    assert telegram_exit.value.code == 75


def test_bot_entrypoints_reject_api_mock_before_network(monkeypatch):
    monkeypatch.setenv("RENAISS_BOT_TOKEN", "telegram-token")
    monkeypatch.setenv("RENAISS_OFFICIAL_CHAT_ID", "-1001")
    monkeypatch.setenv("RENAISS_DISCORD_TOKEN", "discord-token")
    monkeypatch.setenv("DATABASE_URL", "postgresql://example.invalid/renaiss")
    monkeypatch.setenv("RENAISS_API_MOCK_JSON", "{}")
    monkeypatch.delenv("RENAISS_SKIP_DB", raising=False)
    telegram_builder = Mock()
    discord_run = Mock()
    monkeypatch.setattr(telegram_main.Application, "builder", telegram_builder)
    monkeypatch.setattr(discord_main.client, "run", discord_run)

    with pytest.raises(SystemExit):
        telegram_main.main()
    with pytest.raises(SystemExit):
        discord_main.main()

    telegram_builder.assert_not_called()
    discord_run.assert_not_called()


def test_telegram_entrypoint_rejects_token_for_different_bot_id(monkeypatch):
    monkeypatch.setenv("RENAISS_BOT_TOKEN", "111111:test-secret")
    monkeypatch.setenv("RENAISS_EXPECTED_BOT_ID", "222222")
    monkeypatch.setenv("RENAISS_OFFICIAL_CHAT_ID", "-1001")
    monkeypatch.setenv("DATABASE_URL", "postgresql://example.invalid/renaiss")
    monkeypatch.delenv("RENAISS_SKIP_DB", raising=False)
    monkeypatch.delenv("RENAISS_API_MOCK_JSON", raising=False)
    telegram_builder = Mock()
    monkeypatch.setattr(telegram_main.Application, "builder", telegram_builder)

    with pytest.raises(SystemExit):
        telegram_main.main()

    telegram_builder.assert_not_called()


def test_referral_entrypoint_rejects_missing_config_before_socket_bind(monkeypatch):
    for name in (
        "DATABASE_URL",
        "RENAISS_CLICK_TRACKER_PUBLIC_BASE_URL",
        "RENAISS_CLICK_TRACKER_SECRET",
        "RENAISS_SKIP_DB",
    ):
        monkeypatch.delenv(name, raising=False)
    run_app = Mock()
    monkeypatch.setattr(referral_server.web, "run_app", run_app)

    with pytest.raises(SystemExit) as tracker_exit:
        referral_server.main()

    assert tracker_exit.value.code == 2
    run_app.assert_not_called()


def test_referral_entrypoint_binds_only_after_safe_local_config(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://example.invalid/renaiss")
    monkeypatch.setenv(
        "RENAISS_CLICK_TRACKER_PUBLIC_BASE_URL", "https://click.example.com"
    )
    monkeypatch.setenv("RENAISS_CLICK_TRACKER_SECRET", "s" * 32)
    monkeypatch.setenv("RENAISS_CLICK_TRACKER_HOST", "127.0.0.1")
    monkeypatch.setenv("RENAISS_CLICK_TRACKER_PORT", "18090")
    monkeypatch.delenv("RENAISS_SKIP_DB", raising=False)
    run_app = Mock()
    monkeypatch.setattr(referral_server.web, "run_app", run_app)

    referral_server.main()

    assert run_app.call_args.kwargs["host"] == "127.0.0.1"
    assert run_app.call_args.kwargs["port"] == 18090
