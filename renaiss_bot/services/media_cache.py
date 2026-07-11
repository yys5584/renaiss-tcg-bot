"""Bounded best-effort cache for Telegram-hosted rendered media."""

from __future__ import annotations

import asyncio
import logging
import os
import re

from renaiss_bot.database.connection import get_db

logger = logging.getLogger(__name__)
_RENDER_KEY = re.compile(r"^[0-9a-f]{64}$")


def _timeout_seconds() -> float:
    try:
        configured = float(os.getenv("RENAISS_MEDIA_CACHE_TIMEOUT_SECONDS", "0.35"))
    except ValueError:
        configured = 0.35
    return min(2.0, max(0.05, configured))


def _valid_key(render_key: str) -> bool:
    return bool(_RENDER_KEY.fullmatch(render_key))


async def get_telegram_file_id(render_key: str) -> str | None:
    if not _valid_key(render_key):
        return None

    async def lookup() -> str | None:
        pool = await get_db()
        async with pool.acquire() as conn:
            value = await conn.fetchval(
                """
                SELECT telegram_file_id
                FROM renaiss_telegram_media_cache
                WHERE render_key = $1
                """,
                render_key,
            )
        return str(value) if value else None

    try:
        return await asyncio.wait_for(lookup(), timeout=_timeout_seconds())
    except Exception as exc:
        logger.debug("Telegram media cache lookup skipped key=%s: %s", render_key, exc)
        return None


async def store_telegram_file_id(
    render_key: str,
    telegram_file_id: str,
    telegram_file_unique_id: str | None = None,
) -> bool:
    if (
        not _valid_key(render_key)
        or not telegram_file_id
        or len(telegram_file_id) > 512
        or (telegram_file_unique_id is not None and len(telegram_file_unique_id) > 512)
    ):
        return False

    async def persist() -> None:
        pool = await get_db()
        async with pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO renaiss_telegram_media_cache (
                    render_key, telegram_file_id, telegram_file_unique_id
                ) VALUES ($1,$2,$3)
                ON CONFLICT (render_key) DO UPDATE
                SET telegram_file_id = EXCLUDED.telegram_file_id,
                    telegram_file_unique_id = EXCLUDED.telegram_file_unique_id,
                    updated_at = clock_timestamp()
                """,
                render_key,
                telegram_file_id,
                telegram_file_unique_id,
            )

    try:
        await asyncio.wait_for(persist(), timeout=_timeout_seconds())
        return True
    except Exception as exc:
        logger.debug("Telegram media cache store skipped key=%s: %s", render_key, exc)
        return False


async def remember_telegram_photo(render_key: str, message) -> bool:
    photos = getattr(message, "photo", None) or []
    if not photos:
        return False
    largest = photos[-1]
    file_id = str(getattr(largest, "file_id", "") or "")
    unique_id = str(getattr(largest, "file_unique_id", "") or "") or None
    return await store_telegram_file_id(render_key, file_id, unique_id)
