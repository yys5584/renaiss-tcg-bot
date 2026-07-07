"""Small asyncpg connection manager for the standalone Renaiss bot."""

from __future__ import annotations

import asyncio
import logging
import os
import ssl

import asyncpg

logger = logging.getLogger(__name__)
_pool: asyncpg.Pool | None = None


def _make_ssl():
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


async def get_db() -> asyncpg.Pool:
    global _pool
    if _pool is None or _pool._closed:
        dsn = os.getenv("DATABASE_URL")
        if not dsn:
            raise RuntimeError("DATABASE_URL not set")
        _pool = await asyncio.wait_for(
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
    return _pool


async def close_db() -> None:
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None

