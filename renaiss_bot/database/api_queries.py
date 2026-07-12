"""Shared Partner API cooldown state for every bot instance and call class."""

from __future__ import annotations

from renaiss_bot.database.connection import get_db


async def get_partner_api_cooldown_seconds() -> int:
    pool = await get_db()
    async with pool.acquire() as conn:
        value = await conn.fetchval(
            """
            SELECT GREATEST(
                0,
                CEIL(EXTRACT(EPOCH FROM (blocked_until - clock_timestamp())))
            )::int
            FROM renaiss_api_cooldowns
            WHERE api_name = 'renaiss_partner_api'
            """
        )
    return max(0, int(value or 0))


async def extend_partner_api_cooldown(*, retry_after_seconds: int, reason: str) -> None:
    seconds = min(86_400, max(1, int(retry_after_seconds)))
    pool = await get_db()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO renaiss_api_cooldowns (
                api_name, blocked_until, reason, updated_at
            ) VALUES (
                'renaiss_partner_api',
                clock_timestamp() + ($1::int * interval '1 second'),
                $2,
                clock_timestamp()
            )
            ON CONFLICT (api_name) DO UPDATE
            SET blocked_until = GREATEST(
                    renaiss_api_cooldowns.blocked_until,
                    EXCLUDED.blocked_until
                ),
                reason = EXCLUDED.reason,
                updated_at = clock_timestamp()
            """,
            seconds,
            reason[:128],
        )


async def claim_partner_api_request_slot(*, min_interval_ms: int) -> int:
    """Claim one cross-instance request start, or return milliseconds to retry."""
    interval_ms = min(10_000, max(100, int(min_interval_ms)))
    pool = await get_db()
    async with pool.acquire() as conn, conn.transaction():
        await conn.execute(
            """
            INSERT INTO renaiss_api_request_gates (api_name, next_request_at, updated_at)
            VALUES ('renaiss_partner_api', '-infinity'::timestamptz, clock_timestamp())
            ON CONFLICT (api_name) DO NOTHING
            """
        )
        claimed = await conn.fetchrow(
            """
            UPDATE renaiss_api_request_gates
            SET next_request_at = clock_timestamp() +
                    ($1::int * interval '1 millisecond'),
                updated_at = clock_timestamp()
            WHERE api_name = 'renaiss_partner_api'
              AND next_request_at <= clock_timestamp()
            RETURNING next_request_at
            """,
            interval_ms,
        )
        if claimed is not None:
            return 0
        remaining = await conn.fetchval(
            """
            SELECT GREATEST(
                1,
                CEIL(EXTRACT(EPOCH FROM (next_request_at - clock_timestamp())) * 1000)
            )::int
            FROM renaiss_api_request_gates
            WHERE api_name = 'renaiss_partner_api'
            """
        )
    return max(1, int(remaining or interval_ms))
