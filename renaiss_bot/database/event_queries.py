"""Best-effort, persistent product-event instrumentation."""

from __future__ import annotations

import asyncio
import json
import logging
import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

from renaiss_bot.database.connection import get_db

logger = logging.getLogger(__name__)


def _event_timeout_seconds() -> float:
    try:
        configured = float(os.getenv("RENAISS_EVENT_TIMEOUT_SECONDS", "1.0"))
    except ValueError:
        configured = 1.0
    return min(3.0, max(0.05, configured))


@dataclass(frozen=True)
class SpawnDispatchReservation:
    acquired: bool
    reason: str
    spawn_date: date
    dispatched_count: int


def _hour_in_quiet_window(hour: int, start_hour: int, end_hour: int) -> bool:
    """Return whether a KST hour is inside a half-open, possibly wrapping window."""
    if start_hour == end_hour:
        return False
    if start_hour < end_hour:
        return start_hour <= hour < end_hour
    return hour >= start_hour or hour < end_hour


async def reserve_spawn_dispatch(
    *,
    chat_id: int,
    lease_token: str,
    daily_cap: int,
    quiet_start_hour: int,
    quiet_end_hour: int,
    lease_seconds: int,
    minimum_interval_seconds: int = 0,
) -> SpawnDispatchReservation:
    """Atomically reserve one public spawn across every bot instance.

    The hard-cap slot is consumed before external API or Telegram calls. Failed
    attempts can under-fill a day, but a crash or response loss cannot exceed
    the public-message cap.
    """
    if chat_id == 0 or not lease_token or len(lease_token) > 128:
        raise ValueError("invalid spawn dispatch reservation")
    # Season-1-style official rooms can legitimately exceed 2,000 rounds/day.
    # Keep a finite guard, but do not silently clamp the configured profile to 48.
    cap = max(1, min(5000, int(daily_cap)))
    lease_for = max(30, min(3600, int(lease_seconds)))
    minimum_interval = max(0, min(86_400, int(minimum_interval_seconds)))
    start = max(0, min(23, int(quiet_start_hour)))
    end = max(0, min(23, int(quiet_end_hour)))
    pool = await get_db()
    async with pool.acquire() as conn, conn.transaction():
        await conn.fetchval(
            "SELECT pg_advisory_xact_lock(hashtextextended($1, 0))",
            f"renaiss-spawn-dispatch:{chat_id}",
        )
        clock = await conn.fetchrow(
            """
            SELECT
                observed_at,
                (observed_at AT TIME ZONE 'Asia/Seoul')::date AS spawn_date,
                EXTRACT(HOUR FROM observed_at AT TIME ZONE 'Asia/Seoul')::int AS kst_hour
            FROM (SELECT clock_timestamp() AS observed_at) clock
            """
        )
        current = clock["observed_at"]
        spawn_date = clock["spawn_date"]
        existing = await conn.fetchrow(
            """
            SELECT spawn_date, dispatched_count, lease_expires_at, last_dispatched_at
            FROM renaiss_spawn_dispatch
            WHERE chat_id = $1
            FOR UPDATE
            """,
            chat_id,
        )
        count = (
            int(existing["dispatched_count"])
            if existing is not None and existing["spawn_date"] == spawn_date
            else 0
        )
        if _hour_in_quiet_window(int(clock["kst_hour"]), start, end):
            return SpawnDispatchReservation(False, "quiet_hours", spawn_date, count)
        if count >= cap:
            return SpawnDispatchReservation(False, "daily_cap", spawn_date, count)
        if existing is not None and existing["lease_expires_at"] > current:
            return SpawnDispatchReservation(False, "lease_busy", spawn_date, count)
        if (
            existing is not None
            and existing["last_dispatched_at"] is not None
            and existing["last_dispatched_at"]
            + timedelta(seconds=minimum_interval)
            > current
        ):
            return SpawnDispatchReservation(False, "cadence", spawn_date, count)
        row = await conn.fetchrow(
            """
            INSERT INTO renaiss_spawn_dispatch (
                chat_id, spawn_date, dispatched_count, lease_token,
                lease_expires_at, last_dispatched_at, updated_at
            ) VALUES (
                $1, $2, 1, $3,
                $4::timestamptz + ($5::int * interval '1 second'),
                $4::timestamptz, $4::timestamptz
            )
            ON CONFLICT (chat_id) DO UPDATE
            SET spawn_date = EXCLUDED.spawn_date,
                dispatched_count = CASE
                    WHEN renaiss_spawn_dispatch.spawn_date = EXCLUDED.spawn_date
                    THEN renaiss_spawn_dispatch.dispatched_count + 1
                    ELSE 1
                END,
                lease_token = EXCLUDED.lease_token,
                lease_expires_at = EXCLUDED.lease_expires_at,
                last_dispatched_at = EXCLUDED.last_dispatched_at,
                updated_at = EXCLUDED.updated_at
            RETURNING spawn_date, dispatched_count
            """,
            chat_id,
            spawn_date,
            lease_token,
            current,
            lease_for,
        )
        return SpawnDispatchReservation(
            True,
            "acquired",
            row["spawn_date"],
            int(row["dispatched_count"]),
        )


async def release_spawn_dispatch(*, chat_id: int, lease_token: str) -> bool:
    """Release only the reservation owned by this invocation."""
    pool = await get_db()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            UPDATE renaiss_spawn_dispatch
            SET lease_token = NULL, lease_expires_at = clock_timestamp(),
                updated_at = clock_timestamp()
            WHERE chat_id = $1 AND lease_token = $2
            RETURNING chat_id
            """,
            chat_id,
            lease_token,
        )
    return row is not None


async def event_exists(event_key: str) -> bool | None:
    """Return event presence, or ``None`` when analytics storage is unavailable."""
    try:
        async def lookup() -> bool:
            pool = await get_db()
            async with pool.acquire() as conn:
                return bool(
                    await conn.fetchval(
                        "SELECT EXISTS (SELECT 1 FROM renaiss_events WHERE event_key = $1)",
                        event_key,
                    )
                )

        return await asyncio.wait_for(lookup(), timeout=_event_timeout_seconds())
    except Exception as exc:
        logger.warning("Renaiss event presence check skipped key=%s: %s", event_key, exc)
        return None


async def log_event(
    event_name: str,
    *,
    event_key: str | None = None,
    user_id: int | None = None,
    chat_id: int | None = None,
    session_id: str | None = None,
    metadata: Mapping[str, Any] | None = None,
) -> bool:
    """Persist an event without allowing analytics failure to break gameplay."""
    try:
        payload = json.dumps(dict(metadata or {}), ensure_ascii=False, default=str)
        async def persist():
            pool = await get_db()
            async with pool.acquire() as conn:
                return await conn.fetchrow(
                    """
                    INSERT INTO renaiss_events (
                        event_name, event_key, user_id, chat_id, session_id, metadata
                    ) VALUES ($1,$2,$3,$4,$5,$6::jsonb)
                    ON CONFLICT (event_key) DO NOTHING
                    RETURNING id
                    """,
                    event_name,
                    event_key,
                    user_id,
                    chat_id,
                    session_id,
                    payload,
                )

        row = await asyncio.wait_for(persist(), timeout=_event_timeout_seconds())
        return row is not None
    except Exception as exc:
        logger.warning("Renaiss event log skipped event=%s: %s", event_name, exc)
        return False


async def log_events(events: Sequence[Mapping[str, Any]]) -> bool:
    """Persist a small related event set with one bounded pool checkout."""
    if not events:
        return True
    try:
        rows = [
            (
                str(event["event_name"]),
                event.get("event_key"),
                event.get("user_id"),
                event.get("chat_id"),
                event.get("session_id"),
                json.dumps(dict(event.get("metadata") or {}), ensure_ascii=False, default=str),
            )
            for event in events
        ]

        async def persist() -> None:
            pool = await get_db()
            async with pool.acquire() as conn:
                await conn.executemany(
                    """
                    INSERT INTO renaiss_events (
                        event_name, event_key, user_id, chat_id, session_id, metadata
                    ) VALUES ($1,$2,$3,$4,$5,$6::jsonb)
                    ON CONFLICT (event_key) DO NOTHING
                    """,
                    rows,
                )

        await asyncio.wait_for(persist(), timeout=_event_timeout_seconds())
        return True
    except Exception as exc:
        logger.warning("Renaiss event batch skipped count=%s: %s", len(events), exc)
        return False


async def list_unfinished_spawns(*, limit: int = 50, after_id: int = 0) -> list[dict]:
    """Page through every prompt without a confirmed terminal event."""
    pool = await get_db()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT
                posted.id AS posted_event_id,
                posted.session_id,
                posted.chat_id,
                posted.metadata,
                posted.created_at,
                award.user_id AS award_user_id,
                award.metadata AS award_metadata
            FROM renaiss_events posted
            LEFT JOIN LATERAL (
                SELECT winner.user_id, winner.metadata
                FROM renaiss_events winner
                WHERE winner.session_id = posted.session_id
                  AND winner.event_name = 'catch_won'
                ORDER BY winner.created_at DESC, winner.id DESC
                LIMIT 1
            ) award ON TRUE
            WHERE posted.event_name = 'spawn_posted'
              AND posted.id > $2
              AND NOT EXISTS (
                  SELECT 1
                  FROM renaiss_events terminal
                  WHERE terminal.session_id = posted.session_id
                    AND (
                        terminal.event_name = 'spawn_revealed'
                        OR (
                            terminal.event_name = 'spawn_cancelled'
                            AND terminal.metadata @> '{"prompt_closed": true}'::jsonb
                        )
                    )
              )
            ORDER BY posted.id
            LIMIT $1
            """,
            max(1, limit),
            max(0, after_id),
        )
    result = []
    for row in rows:
        item = dict(row)
        metadata = item.get("metadata")
        if isinstance(metadata, str):
            try:
                metadata = json.loads(metadata)
            except json.JSONDecodeError:
                metadata = {}
        item["metadata"] = metadata if isinstance(metadata, dict) else {}
        award_metadata = item.get("award_metadata")
        if isinstance(award_metadata, str):
            try:
                award_metadata = json.loads(award_metadata)
            except json.JSONDecodeError:
                award_metadata = {}
        item["award_metadata"] = award_metadata if isinstance(award_metadata, dict) else {}
        result.append(item)
    return result
