"""Small asyncpg connection manager for the standalone Renaiss bot."""

from __future__ import annotations

import asyncio
import logging
import os
import ssl
from pathlib import Path
from typing import Any

import asyncpg

logger = logging.getLogger(__name__)
_pool: "_BoundedPool" | None = None
_pool_lock = asyncio.Lock()
_POOL_CLOSE_TIMEOUT_SECONDS = 10.0

_ENABLED_VALUES = frozenset({"1", "true", "yes", "on"})


def _acquire_timeout_seconds() -> float:
    try:
        configured = float(os.getenv("RENAISS_DB_ACQUIRE_TIMEOUT_SECONDS", "5"))
    except ValueError:
        configured = 5.0
    return min(30.0, max(0.1, configured))


class _BoundedPool:
    """Proxy every pool checkout through one production-safe wait limit."""

    def __init__(self, pool: asyncpg.Pool):
        self._pool = pool

    def acquire(self, *, timeout: float | None = None):
        return self._pool.acquire(
            timeout=_acquire_timeout_seconds() if timeout is None else timeout
        )

    def __getattr__(self, name: str) -> Any:
        return getattr(self._pool, name)


def database_tls_verification_disabled() -> bool:
    """Return whether the explicit local-only TLS escape hatch is enabled."""
    return (
        os.getenv("RENAISS_DB_SSL_INSECURE", "").strip().lower()
        in _ENABLED_VALUES
    )


def _make_ssl() -> ssl.SSLContext:
    raw_ca_file = os.getenv("RENAISS_DB_SSL_CA_FILE", "").strip()
    if raw_ca_file:
        ca_file = Path(raw_ca_file).expanduser()
        if not ca_file.is_absolute() or not ca_file.is_file():
            raise RuntimeError("RENAISS_DB_SSL_CA_FILE must be an existing absolute file")
        ctx = ssl.create_default_context(cafile=str(ca_file))
    else:
        ctx = ssl.create_default_context()
    insecure = database_tls_verification_disabled()
    if insecure:
        logger.warning(
            "RENAISS_DB_SSL_INSECURE is enabled; PostgreSQL certificate verification is disabled."
        )
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
    return ctx


async def get_db() -> _BoundedPool:
    global _pool
    if _pool is not None and not _pool._closed:
        return _pool
    async with _pool_lock:
        if _pool is not None and not _pool._closed:
            return _pool
        dsn = os.getenv("DATABASE_URL")
        if not dsn:
            raise RuntimeError("DATABASE_URL not set")
        raw_pool = await asyncio.wait_for(
            asyncpg.create_pool(
                dsn=dsn,
                min_size=1,
                max_size=int(os.getenv("RENAISS_DB_POOL_MAX", "4")),
                ssl=_make_ssl(),
                statement_cache_size=0,
                command_timeout=30,
            ),
            timeout=12,
        )
        _pool = _BoundedPool(raw_pool)
    return _pool


async def open_db_session(
    *,
    dsn_variable: str = "DATABASE_URL",
) -> asyncpg.Connection:
    """Open one dedicated, non-pooled session for process-lifetime fencing."""
    dsn = os.getenv(dsn_variable)
    if not dsn:
        raise RuntimeError(f"{dsn_variable} not set")
    return await asyncio.wait_for(
        asyncpg.connect(
            dsn=dsn,
            ssl=_make_ssl(),
            statement_cache_size=0,
            command_timeout=30,
        ),
        timeout=12,
    )


async def close_db() -> None:
    global _pool
    async with _pool_lock:
        pool = _pool
        if pool is None:
            return
        _pool = None
        try:
            await asyncio.wait_for(
                pool.close(),
                timeout=_POOL_CLOSE_TIMEOUT_SECONDS,
            )
        except TimeoutError:
            pool.terminate()
            raise RuntimeError("database pool close timed out") from None
