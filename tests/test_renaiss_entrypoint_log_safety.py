"""Entrypoint failures must not copy credential-bearing exception text to logs."""

from __future__ import annotations

import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiohttp import web

import renaiss_bot.main as telegram_main
import renaiss_bot.referral_server as referral_server
from renaiss_bot.adapters.discord import main as discord_main


SENTINEL = "SENTINEL-credential-value\r\nFORGED-LOG-LINE"


@pytest.fixture(autouse=True)
def _disable_instance_guard_database_calls(monkeypatch):
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


def _assert_secret_was_redacted(caplog):
    assert "SENTINEL-credential-value" not in caplog.text
    assert "FORGED-LOG-LINE" not in caplog.text
    assert "RuntimeError" in caplog.text


async def test_telegram_command_setup_log_redacts_bot_api_exception(monkeypatch, caplog):
    application = SimpleNamespace(
        bot=SimpleNamespace(
            set_my_commands=AsyncMock(
                side_effect=RuntimeError(
                    f"https://api.telegram.org/bot{SENTINEL}/setMyCommands"
                )
            )
        )
    )
    monkeypatch.setattr(telegram_main, "daily_pick_enabled", lambda: False)

    with caplog.at_level(logging.WARNING, logger=telegram_main.__name__):
        await telegram_main._configure_commands(application)

    _assert_secret_was_redacted(caplog)


async def test_telegram_database_startup_log_and_error_chain_redact_dsn(
    monkeypatch,
    caplog,
):
    application = SimpleNamespace(bot=SimpleNamespace(id=999), job_queue=object())
    monkeypatch.delenv("RENAISS_EXPECTED_BOT_ID", raising=False)
    monkeypatch.delenv("RENAISS_SKIP_DB", raising=False)
    monkeypatch.setenv("DATABASE_URL", "postgresql://configured.invalid/renaiss")
    monkeypatch.setattr(
        "renaiss_bot.database.connection.get_db",
        AsyncMock(side_effect=RuntimeError(f"connection failed: {SENTINEL}")),
    )

    with caplog.at_level(logging.CRITICAL, logger=telegram_main.__name__):
        with pytest.raises(RuntimeError, match="DB initialization failed") as raised:
            await telegram_main.post_init(application)

    _assert_secret_was_redacted(caplog)
    assert raised.value.__cause__ is None


async def test_telegram_shutdown_logs_redact_cleanup_exception(monkeypatch, caplog):
    monkeypatch.setattr(
        "renaiss_bot.renderers.playwright_render.close_renderer",
        AsyncMock(side_effect=RuntimeError(SENTINEL)),
    )
    monkeypatch.setattr(
        "renaiss_bot.database.connection.close_db",
        AsyncMock(side_effect=RuntimeError(SENTINEL)),
    )

    with caplog.at_level(logging.DEBUG, logger=telegram_main.__name__):
        await telegram_main.post_shutdown(SimpleNamespace())

    _assert_secret_was_redacted(caplog)


async def test_telegram_chat_probe_error_chain_redacts_bot_api_url(monkeypatch):
    application = SimpleNamespace(
        bot=SimpleNamespace(
            id=999,
            get_chat=AsyncMock(
                side_effect=RuntimeError(
                    f"https://api.telegram.org/bot{SENTINEL}/getChat"
                )
            ),
        ),
        job_queue=object(),
    )
    monkeypatch.delenv("RENAISS_EXPECTED_BOT_ID", raising=False)
    monkeypatch.delenv("RENAISS_SKIP_DB", raising=False)
    monkeypatch.setenv("DATABASE_URL", "postgresql://configured.invalid/renaiss")
    monkeypatch.setenv("RENAISS_OFFICIAL_CHAT_ID", "-1001")
    monkeypatch.setattr(
        "renaiss_bot.database.connection.get_db",
        AsyncMock(return_value=object()),
    )
    monkeypatch.setattr("renaiss_bot.database.schema.create_tables", AsyncMock())

    with pytest.raises(RuntimeError, match="chat is not accessible") as raised:
        await telegram_main.post_init(application)

    assert SENTINEL not in str(raised.value)
    assert raised.value.__cause__ is None
    assert raised.value.__suppress_context__ is True


async def test_discord_database_startup_log_and_error_chain_redact_dsn(
    monkeypatch,
    caplog,
):
    monkeypatch.delenv("RENAISS_SKIP_DB", raising=False)
    monkeypatch.setenv("DATABASE_URL", "postgresql://configured.invalid/renaiss")
    monkeypatch.setattr(
        discord_main,
        "get_db",
        AsyncMock(side_effect=RuntimeError(f"connection failed: {SENTINEL}")),
    )

    with caplog.at_level(logging.CRITICAL, logger=discord_main.__name__):
        with pytest.raises(RuntimeError, match="DB initialization failed") as raised:
            await discord_main._init_db()

    _assert_secret_was_redacted(caplog)
    assert raised.value.__cause__ is None


async def test_referral_registry_failure_log_redacts_database_exception(
    monkeypatch,
    caplog,
):
    destination = "https://index.renaissos.com/cards/example"

    async def fail_resolution(token):
        raise RuntimeError(f"database URL: {SENTINEL}")

    monkeypatch.setattr(referral_server, "resolve_tracking_token", fail_resolution)
    monkeypatch.setattr(
        referral_server,
        "validate_fallback_destination",
        lambda token, to, exp, sig: destination,
    )

    request = SimpleNamespace(
        match_info={"token": "opaque"},
        query={"to": destination, "exp": "1", "sig": "signed"},
    )
    with caplog.at_level(logging.WARNING, logger=referral_server.__name__):
        with pytest.raises(web.HTTPFound):
            await referral_server.redirect(request)

    _assert_secret_was_redacted(caplog)
