"""Bounded best-effort cache for Telegram-hosted rendered media."""

from __future__ import annotations

import asyncio
import logging
import os
import re
from collections import OrderedDict

from renaiss_bot.database.connection import get_db

logger = logging.getLogger(__name__)
_RENDER_KEY = re.compile(r"^[0-9a-f]{64}$")
_MEMORY_FILE_IDS: OrderedDict[tuple[int, str], str] = OrderedDict()


def _timeout_seconds() -> float:
    try:
        configured = float(os.getenv("RENAISS_MEDIA_CACHE_TIMEOUT_SECONDS", "0.35"))
    except ValueError:
        configured = 0.35
    return min(2.0, max(0.05, configured))


def _valid_key(render_key: str) -> bool:
    return bool(_RENDER_KEY.fullmatch(render_key))


def _bot_id() -> int:
    try:
        return max(0, int(os.getenv("RENAISS_EXPECTED_BOT_ID", "0")))
    except ValueError:
        return 0


def _remember_memory(render_key: str, file_id: str) -> None:
    key = (_bot_id(), render_key)
    _MEMORY_FILE_IDS[key] = file_id
    _MEMORY_FILE_IDS.move_to_end(key)
    while len(_MEMORY_FILE_IDS) > 256:
        _MEMORY_FILE_IDS.popitem(last=False)


async def get_telegram_file_id(render_key: str) -> str | None:
    if not _valid_key(render_key):
        return None
    memory_key = (_bot_id(), render_key)
    cached = _MEMORY_FILE_IDS.get(memory_key)
    if cached:
        _MEMORY_FILE_IDS.move_to_end(memory_key)
        return cached

    async def lookup() -> str | None:
        pool = await get_db()
        async with pool.acquire() as conn:
            value = await conn.fetchval(
                """
                SELECT telegram_file_id
                FROM renaiss_telegram_media_cache
                WHERE bot_id = $1 AND render_key = $2
                """,
                _bot_id(),
                render_key,
            )
        file_id = str(value) if value else None
        if file_id:
            _remember_memory(render_key, file_id)
        return file_id

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
    _remember_memory(render_key, telegram_file_id)

    async def persist() -> None:
        pool = await get_db()
        async with pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO renaiss_telegram_media_cache (
                    bot_id, render_key, telegram_file_id, telegram_file_unique_id
                ) VALUES ($1,$2,$3,$4)
                ON CONFLICT (bot_id, render_key) DO UPDATE
                SET telegram_file_id = EXCLUDED.telegram_file_id,
                    telegram_file_unique_id = EXCLUDED.telegram_file_unique_id,
                    updated_at = clock_timestamp()
                """,
                _bot_id(),
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


async def delete_telegram_file_id(render_key: str) -> bool:
    if not _valid_key(render_key):
        return False

    _MEMORY_FILE_IDS.pop((_bot_id(), render_key), None)

    async def remove() -> str:
        pool = await get_db()
        async with pool.acquire() as conn:
            return await conn.execute(
                """
                DELETE FROM renaiss_telegram_media_cache
                WHERE bot_id = $1 AND render_key = $2
                """,
                _bot_id(),
                render_key,
            )

    try:
        return await asyncio.wait_for(remove(), timeout=_timeout_seconds()) == "DELETE 1"
    except Exception as exc:
        logger.debug("Telegram media cache delete skipped key=%s: %s", render_key, exc)
        return False


async def remember_telegram_photo(render_key: str, message) -> bool:
    photos = getattr(message, "photo", None) or []
    if not photos:
        return False
    largest = photos[-1]
    file_id = str(getattr(largest, "file_id", "") or "")
    unique_id = str(getattr(largest, "file_unique_id", "") or "") or None
    return await store_telegram_file_id(render_key, file_id, unique_id)
