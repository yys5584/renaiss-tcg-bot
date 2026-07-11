"""Database transport defaults must protect production credentials."""

from __future__ import annotations

import asyncio
import ssl
from unittest.mock import AsyncMock, Mock

import pytest

import renaiss_bot.database.connection as connection
from renaiss_bot.database.connection import _BoundedPool, _make_ssl


def test_database_tls_verification_is_enabled_by_default(monkeypatch):
    monkeypatch.delenv("RENAISS_DB_SSL_INSECURE", raising=False)

    context = _make_ssl()

    assert context.verify_mode == ssl.CERT_REQUIRED
    assert context.check_hostname is True


def test_database_tls_insecure_mode_requires_explicit_opt_in(monkeypatch):
    monkeypatch.setenv("RENAISS_DB_SSL_INSECURE", "1")

    context = _make_ssl()

    assert context.verify_mode == ssl.CERT_NONE
    assert context.check_hostname is False


def test_database_pool_checkout_has_a_bounded_default(monkeypatch):
    class RawPool:
        def __init__(self):
            self.timeout = None

        def acquire(self, *, timeout=None):
            self.timeout = timeout
            return object()

    monkeypatch.setenv("RENAISS_DB_ACQUIRE_TIMEOUT_SECONDS", "0.25")
    raw = RawPool()
    pool = _BoundedPool(raw)

    pool.acquire()

    assert raw.timeout == 0.25


def test_database_pool_checkout_timeout_is_clamped(monkeypatch):
    class RawPool:
        def __init__(self):
            self.timeout = None

        def acquire(self, *, timeout=None):
            self.timeout = timeout
            return object()

    monkeypatch.setenv("RENAISS_DB_ACQUIRE_TIMEOUT_SECONDS", "999")
    raw = RawPool()

    _BoundedPool(raw).acquire()

    assert raw.timeout == 30.0


async def test_dedicated_database_session_uses_production_transport(monkeypatch):
    session = object()
    connect = AsyncMock(return_value=session)
    ssl_context = object()
    monkeypatch.setenv("DATABASE_URL", "postgresql://configured.invalid/renaiss")
    monkeypatch.setattr(connection.asyncpg, "connect", connect)
    monkeypatch.setattr(connection, "_make_ssl", Mock(return_value=ssl_context))

    assert await connection.open_db_session() is session

    connect.assert_awaited_once_with(
        dsn="postgresql://configured.invalid/renaiss",
        ssl=ssl_context,
        statement_cache_size=0,
        command_timeout=30,
    )


async def test_dedicated_database_session_requires_dsn_before_connect(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    connect = AsyncMock()
    monkeypatch.setattr(connection.asyncpg, "connect", connect)

    with pytest.raises(RuntimeError, match="DATABASE_URL not set"):
        await connection.open_db_session()

    connect.assert_not_awaited()


async def test_dedicated_database_session_can_require_direct_lock_dsn(monkeypatch):
    session = object()
    connect = AsyncMock(return_value=session)
    monkeypatch.setenv(
        "RENAISS_TELEGRAM_LOCK_DATABASE_URL",
        "postgresql://direct-db.invalid/renaiss",
    )
    monkeypatch.setattr(connection.asyncpg, "connect", connect)
    monkeypatch.setattr(connection, "_make_ssl", Mock(return_value=object()))

    assert await connection.open_db_session(
        dsn_variable="RENAISS_TELEGRAM_LOCK_DATABASE_URL"
    ) is session
    assert connect.await_args.kwargs["dsn"] == "postgresql://direct-db.invalid/renaiss"


async def test_database_pool_close_is_bounded_and_terminates_on_timeout(monkeypatch):
    close_started = asyncio.Event()

    class HangingPool:
        _closed = False

        async def close(self):
            close_started.set()
            await asyncio.Event().wait()

        def terminate(self):
            self._closed = True

    pool = HangingPool()
    monkeypatch.setattr(connection, "_pool", pool)
    monkeypatch.setattr(connection, "_POOL_CLOSE_TIMEOUT_SECONDS", 0.01)

    with pytest.raises(RuntimeError, match="pool close timed out"):
        await connection.close_db()

    assert close_started.is_set()
    assert pool._closed
    assert connection._pool is None
