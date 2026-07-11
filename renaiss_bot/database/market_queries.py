"""Persistent Market Board and one-pick-per-day challenge queries."""

from __future__ import annotations

import json
import logging
import math
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from renaiss_bot.database.connection import get_db
from renaiss_bot.services.market import (
    MAX_PRICE_AGE_HOURS,
    decision_window_open,
    market_card_eligible,
    today_kst,
    week_start_kst,
)
from renaiss_bot.services.models import CardIdentity, RenaissPrice

logger = logging.getLogger(__name__)


class MarketPickError(RuntimeError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


class ResultBellReconciliationError(RuntimeError):
    """A requested operator reconciliation is not a legal state transition."""

    def __init__(self, code: str, *, row: dict | None = None):
        super().__init__(code)
        self.code = code
        self.row = row


def _decimal(value: float) -> Decimal:
    if isinstance(value, bool) or not math.isfinite(value):
        raise ValueError("market numeric evidence must be finite")
    return Decimal(str(round(value, 8)))


def _local_card_id(card: CardIdentity) -> str:
    return card.local_card_id or (
        f"{card.category}:{card.card_name}:{card.grade}:{card.set_code}:{card.collector_number}"
    )


def _pick_payload(row) -> dict:
    item = dict(row)
    for key in ("entry_fmv_usd", "current_fmv_usd", "result_fmv_usd", "result_move_pct"):
        if key in item and item[key] is not None:
            item[key] = float(item[key])
    return item


async def register_market_reveal(*, card: CardIdentity, price: RenaissPrice) -> int | None:
    """Add a revealed spawn to this week's board and append an immutable price mark."""
    if (
        price.fmv_usd is None
        or isinstance(price.fmv_usd, bool)
        or not math.isfinite(price.fmv_usd)
        or price.fmv_usd <= 0
    ):
        return None
    week_start = week_start_kst()
    eligible = market_card_eligible(card, price)
    price_updated_at = price.price_updated_at
    if price_updated_at is not None and price_updated_at.tzinfo is None:
        price_updated_at = price_updated_at.replace(tzinfo=timezone.utc)
    try:
        pool = await get_db()
        async with pool.acquire() as conn, conn.transaction():
            row = await conn.fetchrow(
                """
                INSERT INTO renaiss_market_board (
                    week_start, category, local_card_id, card_name, set_code, set_name,
                    collector_number, variation, language, grade, image_url, asset_url,
                    initial_fmv_usd,
                    price_source, confidence, confidence_score, source_count,
                    observation_count, valuation_method, pick_eligible
                )
                VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17,$18,$19,$20)
                ON CONFLICT (week_start, category, local_card_id)
                DO UPDATE SET
                    card_name = EXCLUDED.card_name,
                    set_code = EXCLUDED.set_code,
                    set_name = EXCLUDED.set_name,
                    collector_number = EXCLUDED.collector_number,
                    variation = EXCLUDED.variation,
                    language = EXCLUDED.language,
                    grade = EXCLUDED.grade,
                    image_url = COALESCE(EXCLUDED.image_url, renaiss_market_board.image_url),
                    asset_url = COALESCE(EXCLUDED.asset_url, renaiss_market_board.asset_url),
                    price_source = EXCLUDED.price_source,
                    confidence = EXCLUDED.confidence,
                    confidence_score = EXCLUDED.confidence_score,
                    source_count = EXCLUDED.source_count,
                    observation_count = EXCLUDED.observation_count,
                    valuation_method = EXCLUDED.valuation_method,
                    pick_eligible = EXCLUDED.pick_eligible
                RETURNING id
                """,
                week_start,
                card.category,
                _local_card_id(card),
                card.card_name,
                card.set_code,
                card.set_name,
                card.collector_number,
                str((card.metadata or {}).get("variation") or ""),
                card.language,
                card.grade,
                card.image_url,
                price.asset_url,
                _decimal(float(price.fmv_usd)),
                price.source,
                price.confidence,
                price.confidence_score,
                price.source_count,
                price.observation_count,
                price.valuation_method,
                eligible,
            )
            board_card_id = int(row["id"])
            await conn.execute(
                """
                INSERT INTO renaiss_market_price_snapshots (
                    board_card_id, fmv_usd, price_source, confidence, pick_eligible,
                    confidence_score, source_count, observation_count, valuation_method,
                    price_updated_at, asset_url
                ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11)
                """,
                board_card_id,
                _decimal(float(price.fmv_usd)),
                price.source,
                price.confidence,
                eligible,
                price.confidence_score,
                price.source_count,
                price.observation_count,
                price.valuation_method,
                price_updated_at,
                price.asset_url,
            )
        return board_card_id
    except Exception as exc:
        logger.warning("Market reveal register skipped card=%s: %s", card.card_name, exc)
        return None


async def list_market_board(*, limit: int = 10) -> list[dict]:
    pool = await get_db()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT
                b.*,
                latest.fmv_usd,
                latest.captured_at AS price_captured_at,
                latest.price_updated_at,
                latest.snapshot_pick_eligible
            FROM renaiss_market_board b
            JOIN LATERAL (
                SELECT
                    fmv_usd,
                    captured_at,
                    price_updated_at,
                    pick_eligible AS snapshot_pick_eligible
                FROM renaiss_market_price_snapshots s
                WHERE s.board_card_id = b.id
                ORDER BY captured_at DESC, id DESC
                LIMIT 1
            ) latest ON TRUE
            WHERE b.week_start = $1
            ORDER BY b.revealed_at DESC
            LIMIT $2
            """,
            week_start_kst(),
            limit,
        )
    now = datetime.now(timezone.utc)
    payload = []
    for row in rows:
        item = dict(row)
        updated_at = item.get("price_updated_at")
        fresh = bool(
            updated_at
            and timedelta(0)
            <= now - updated_at.astimezone(timezone.utc)
            <= timedelta(hours=MAX_PRICE_AGE_HOURS)
        )
        item["pick_eligible"] = bool(item.get("snapshot_pick_eligible")) and fresh
        payload.append(item)
    return payload


async def get_daily_pick(user_id: int, *, now: datetime | None = None) -> dict | None:
    pool = await get_db()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT
                p.pick_date,
                p.board_card_id,
                p.entry_fmv_usd,
                p.entry_price_updated_at,
                p.picked_at,
                p.settles_at,
                p.settlement_snapshot_id,
                p.settled_at,
                b.card_name,
                b.grade,
                settled.fmv_usd AS current_fmv_usd,
                settled.price_updated_at AS current_price_updated_at
            FROM renaiss_market_picks p
            JOIN renaiss_market_board b ON b.id = p.board_card_id
            LEFT JOIN renaiss_market_price_snapshots settled
              ON settled.id = p.settlement_snapshot_id
            WHERE p.user_id = $1 AND p.pick_date = $2
            """,
            user_id,
            today_kst(now),
    )
    if row is None:
        return None
    return _pick_payload(row)


async def get_latest_daily_pick_result(
    user_id: int,
    *,
    now: datetime | None = None,
) -> dict | None:
    """Return the user's latest persisted result, independently of today's pick."""
    pool = await get_db()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT
                p.pick_date,
                p.board_card_id,
                p.entry_fmv_usd,
                p.entry_price_updated_at,
                p.picked_at,
                p.settles_at,
                p.settlement_snapshot_id,
                p.settled_at,
                b.card_name,
                b.grade,
                b.local_card_id,
                settled.asset_url AS asset_url,
                settled.fmv_usd AS result_fmv_usd,
                settled.price_source AS result_price_source,
                settled.confidence AS result_confidence,
                settled.confidence_score AS result_confidence_score,
                settled.source_count AS result_source_count,
                settled.price_updated_at AS result_price_updated_at,
                settled.captured_at AS result_captured_at,
                (settled.fmv_usd / NULLIF(p.entry_fmv_usd, 0) - 1) * 100
                    AS result_move_pct
            FROM renaiss_market_picks p
            JOIN renaiss_market_board b ON b.id = p.board_card_id
            JOIN renaiss_market_price_snapshots settled
              ON settled.id = p.settlement_snapshot_id
            WHERE p.user_id = $1
              AND p.pick_date < $2
            ORDER BY p.pick_date DESC
            LIMIT 1
            """,
            user_id,
            today_kst(now),
        )
    return _pick_payload(row) if row is not None else None


async def settle_due_daily_picks(
    *,
    board_card_id: int | None = None,
    as_of: datetime | None = None,
    limit: int = 500,
) -> list[dict]:
    """Persist each due pick's first qualifying mark exactly once.

    The settlement row and its product event commit in the same transaction.
    Later price snapshots cannot rewrite the recorded result.
    """
    settled_at = as_of or datetime.now(timezone.utc)
    if settled_at.tzinfo is None:
        settled_at = settled_at.replace(tzinfo=timezone.utc)
    pool = await get_db()
    async with pool.acquire() as conn, conn.transaction():
        rows = await conn.fetch(
            """
            WITH candidates AS MATERIALIZED (
                SELECT
                    p.user_id,
                    p.pick_date,
                    eligible.id AS snapshot_id
                FROM renaiss_market_picks p
                JOIN LATERAL (
                    SELECT s.id
                    FROM renaiss_market_price_snapshots s
                    WHERE s.board_card_id = p.board_card_id
                      AND s.pick_eligible = TRUE
                      AND s.captured_at >= p.settles_at
                      AND s.price_updated_at >= p.settles_at
                    ORDER BY s.captured_at ASC, s.id ASC
                    LIMIT 1
                ) eligible ON TRUE
                WHERE p.settlement_snapshot_id IS NULL
                  AND p.settles_at <= $1
                  AND ($2::bigint IS NULL OR p.board_card_id = $2)
                ORDER BY p.settles_at, p.user_id, p.pick_date
                LIMIT $3
                FOR UPDATE OF p SKIP LOCKED
            ), settled AS (
                UPDATE renaiss_market_picks p
                SET settlement_snapshot_id = candidates.snapshot_id,
                    settled_at = $1
                FROM candidates
                WHERE p.user_id = candidates.user_id
                  AND p.pick_date = candidates.pick_date
                  AND p.settlement_snapshot_id IS NULL
                RETURNING p.*
            ), settlement_events AS (
                INSERT INTO renaiss_events (
                    event_name, event_key, user_id, metadata
                )
                SELECT
                    'daily_pick_settled',
                    'daily-pick:' || settled.pick_date::text || ':settled:' || settled.user_id::text,
                    settled.user_id,
                    jsonb_build_object(
                        'pick_date', settled.pick_date,
                        'board_card_id', settled.board_card_id,
                        'settlement_snapshot_id', settled.settlement_snapshot_id,
                        'entry_fmv_usd', settled.entry_fmv_usd,
                        'result_fmv_usd', snapshot.fmv_usd,
                        'result_move_pct',
                            (snapshot.fmv_usd / NULLIF(settled.entry_fmv_usd, 0) - 1) * 100,
                        'settles_at', settled.settles_at,
                        'result_price_updated_at', snapshot.price_updated_at
                    )
                FROM settled
                JOIN renaiss_market_price_snapshots snapshot
                  ON snapshot.id = settled.settlement_snapshot_id
                ON CONFLICT (event_key) DO NOTHING
                RETURNING id
            )
            SELECT
                settled.pick_date,
                settled.user_id,
                settled.board_card_id,
                settled.entry_fmv_usd,
                settled.picked_at,
                settled.settles_at,
                settled.settlement_snapshot_id,
                settled.settled_at,
                board.card_name,
                board.grade,
                snapshot.fmv_usd AS result_fmv_usd,
                snapshot.price_source AS result_price_source,
                snapshot.confidence AS result_confidence,
                snapshot.price_updated_at AS result_price_updated_at,
                snapshot.captured_at AS result_captured_at,
                (snapshot.fmv_usd / NULLIF(settled.entry_fmv_usd, 0) - 1) * 100
                    AS result_move_pct,
                event_count.count AS settlement_event_count
            FROM settled
            JOIN renaiss_market_board board ON board.id = settled.board_card_id
            JOIN renaiss_market_price_snapshots snapshot
              ON snapshot.id = settled.settlement_snapshot_id
            CROSS JOIN (SELECT COUNT(*)::int AS count FROM settlement_events) event_count
            ORDER BY settled.pick_date, settled.user_id
            """,
            settled_at,
            board_card_id,
            max(1, limit),
        )
    return [_pick_payload(row) for row in rows]


async def lock_daily_pick(
    *,
    user_id: int,
    board_card_id: int,
    community_chat_id: int | None = None,
    now: datetime | None = None,
) -> dict:
    """Lock one verified card for the user today; repeated callbacks are idempotently rejected."""
    if not decision_window_open(now):
        raise MarketPickError("pick_closed")
    pick_date = today_kst(now)
    week_start = week_start_kst(now)
    pool = await get_db()
    async with pool.acquire() as conn, conn.transaction():
        board = await conn.fetchrow(
            """
            SELECT b.id, b.card_name, latest.fmv_usd, latest.price_updated_at
            FROM renaiss_market_board b
            JOIN LATERAL (
                SELECT fmv_usd, price_updated_at
                FROM renaiss_market_price_snapshots s
                WHERE s.board_card_id = b.id
                  AND s.pick_eligible = TRUE
                  AND s.price_updated_at >= now() - ($3::int * interval '1 hour')
                ORDER BY captured_at DESC, id DESC
                LIMIT 1
            ) latest ON TRUE
            WHERE b.id = $1 AND b.week_start = $2 AND b.pick_eligible = TRUE
            """,
            board_card_id,
            week_start,
            MAX_PRICE_AGE_HOURS,
        )
        if board is None:
            raise MarketPickError("card_not_eligible")
        inserted = await conn.fetchrow(
            """
            INSERT INTO renaiss_market_picks (
                user_id, pick_date, board_card_id, entry_fmv_usd,
                entry_price_updated_at, community_chat_id, settles_at
            ) VALUES ($1,$2,$3,$4,$5,$6, now() + interval '24 hours')
            ON CONFLICT (user_id, pick_date) DO NOTHING
            RETURNING board_card_id
            """,
            user_id,
            pick_date,
            board_card_id,
            board["fmv_usd"],
            board["price_updated_at"],
            community_chat_id,
        )
        if inserted is None:
            raise MarketPickError("already_picked")
    return {
        "card_name": board["card_name"],
        "entry_fmv_usd": float(board["fmv_usd"]),
        "pick_date": pick_date,
    }


async def list_due_pick_cards(*, limit: int = 8) -> list[dict]:
    """Return cards whose T+24 result still needs a genuinely newer price mark."""
    pool = await get_db()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT
                b.id AS board_card_id,
                b.category,
                b.local_card_id,
                b.card_name,
                b.set_code,
                b.set_name,
                b.collector_number,
                b.variation,
                b.language,
                b.grade,
                b.image_url,
                MIN(p.settles_at) AS earliest_unsettled_at,
                COUNT(*)::int AS pending_pick_count
            FROM renaiss_market_picks p
            JOIN renaiss_market_board b ON b.id = p.board_card_id
            WHERE p.settles_at <= now()
              AND p.settlement_snapshot_id IS NULL
              AND NOT EXISTS (
                  SELECT 1
                  FROM renaiss_market_price_snapshots s
                  WHERE s.board_card_id = p.board_card_id
                    AND s.pick_eligible = TRUE
                    AND s.captured_at >= p.settles_at
                    AND s.price_updated_at >= p.settles_at
              )
            GROUP BY b.id
            ORDER BY MIN(p.settles_at), b.id
            LIMIT $1
            """,
            max(1, limit),
        )
    return [dict(row) for row in rows]


async def claim_due_pick_cards(
    *,
    worker_token: str,
    limit: int = 8,
    lease_seconds: int = 180,
) -> list[dict]:
    """Lease due cards so multiple bot instances do not duplicate API calls."""
    if not worker_token or len(worker_token) > 128:
        raise ValueError("invalid market refresh worker token")
    pool = await get_db()
    async with pool.acquire() as conn, conn.transaction():
        rows = await conn.fetch(
            """
            WITH due AS MATERIALIZED (
                SELECT
                    b.id AS board_card_id,
                    MIN(p.settles_at) AS earliest_unsettled_at
                FROM renaiss_market_picks p
                JOIN renaiss_market_board b ON b.id = p.board_card_id
                LEFT JOIN renaiss_market_refresh_leases lease
                  ON lease.board_card_id = b.id
                WHERE p.settles_at <= now()
                  AND p.settlement_snapshot_id IS NULL
                  AND (lease.board_card_id IS NULL OR lease.lease_until <= now())
                  AND NOT EXISTS (
                      SELECT 1
                      FROM renaiss_market_price_snapshots s
                      WHERE s.board_card_id = p.board_card_id
                        AND s.pick_eligible = TRUE
                        AND s.captured_at >= p.settles_at
                        AND s.price_updated_at >= p.settles_at
                  )
                GROUP BY b.id
                ORDER BY MIN(p.settles_at), b.id
                LIMIT $1
            ), claimed AS (
                INSERT INTO renaiss_market_refresh_leases (
                    board_card_id, lease_token, lease_until, attempt_count,
                    last_status, last_started_at, updated_at
                )
                SELECT
                    due.board_card_id,
                    $2,
                    now() + ($3::int * interval '1 second'),
                    1,
                    'claimed',
                    now(),
                    now()
                FROM due
                ON CONFLICT (board_card_id) DO UPDATE
                SET lease_token = EXCLUDED.lease_token,
                    lease_until = EXCLUDED.lease_until,
                    attempt_count = renaiss_market_refresh_leases.attempt_count + 1,
                    last_status = 'claimed',
                    last_started_at = now(),
                    updated_at = now()
                WHERE renaiss_market_refresh_leases.lease_until <= now()
                RETURNING board_card_id
            )
            SELECT
                b.id AS board_card_id,
                b.category,
                b.local_card_id,
                b.card_name,
                b.set_code,
                b.set_name,
                b.collector_number,
                b.variation,
                b.language,
                b.grade,
                b.image_url,
                MIN(p.settles_at) AS earliest_unsettled_at,
                COUNT(*)::int AS pending_pick_count
            FROM claimed
            JOIN renaiss_market_board b ON b.id = claimed.board_card_id
            JOIN renaiss_market_picks p ON p.board_card_id = b.id
            WHERE p.settles_at <= now()
              AND p.settlement_snapshot_id IS NULL
            GROUP BY b.id
            ORDER BY MIN(p.settles_at), b.id
            """,
            max(1, limit),
            worker_token,
            min(86_400, max(30, lease_seconds)),
        )
    return [dict(row) for row in rows]


async def acquire_market_refresh_job_lease(
    *,
    lease_owner: str,
    lease_seconds: int = 180,
) -> bool:
    """Acquire the shared API batch lease with cross-instance cooldown."""
    pool = await get_db()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO renaiss_job_leases (
                job_name, lease_owner, acquired_at, lease_expires_at,
                next_attempt_at, last_status, updated_at
            ) VALUES (
                'daily_pick_price_refresh', $1, clock_timestamp(),
                clock_timestamp() + ($2::int * interval '1 second'),
                '-infinity'::timestamptz, 'running', clock_timestamp()
            )
            ON CONFLICT (job_name) DO UPDATE
            SET lease_owner = EXCLUDED.lease_owner,
                acquired_at = EXCLUDED.acquired_at,
                lease_expires_at = EXCLUDED.lease_expires_at,
                last_status = 'running',
                updated_at = clock_timestamp()
            WHERE renaiss_job_leases.lease_expires_at <= clock_timestamp()
              AND renaiss_job_leases.next_attempt_at <= clock_timestamp()
            RETURNING acquired_at
            """,
            lease_owner,
            min(86_400, max(30, lease_seconds)),
        )
    return row is not None


async def renew_market_refresh_job_lease(
    *,
    lease_owner: str,
    lease_seconds: int = 180,
) -> bool:
    pool = await get_db()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            UPDATE renaiss_job_leases
            SET lease_expires_at = clock_timestamp() + ($2::int * interval '1 second'),
                updated_at = clock_timestamp()
            WHERE job_name = 'daily_pick_price_refresh'
              AND lease_owner = $1
              AND lease_expires_at > clock_timestamp()
            RETURNING job_name
            """,
            lease_owner,
            min(86_400, max(30, lease_seconds)),
        )
    return row is not None


async def finish_market_refresh_job_lease(
    *,
    lease_owner: str,
    cadence_seconds: int,
    retry_after_seconds: int = 0,
    status: str,
) -> bool:
    """Fence completion and share normal cadence or 429 backoff across workers."""
    pool = await get_db()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            UPDATE renaiss_job_leases
            SET lease_expires_at = clock_timestamp(),
                next_attempt_at = GREATEST(
                    acquired_at + ($2::int * interval '1 second'),
                    clock_timestamp() + ($3::int * interval '1 second')
                ),
                last_status = $4,
                updated_at = clock_timestamp()
            WHERE job_name = 'daily_pick_price_refresh'
              AND lease_owner = $1
            RETURNING job_name
            """,
            lease_owner,
            min(86_400, max(300, cadence_seconds)),
            min(86_400, max(0, retry_after_seconds)),
            status[:64],
        )
    return row is not None


async def release_market_refresh_leases(
    *,
    worker_token: str,
    status: str,
) -> int:
    """Release every lease still owned by this refresh invocation."""
    pool = await get_db()
    async with pool.acquire() as conn:
        result = await conn.execute(
            """
            UPDATE renaiss_market_refresh_leases
            SET lease_token = NULL,
                lease_until = now(),
                last_status = $2,
                last_completed_at = now(),
                updated_at = now()
            WHERE lease_token = $1
            """,
            worker_token,
            status[:64],
        )
    try:
        return int(result.rsplit(" ", 1)[-1])
    except (ValueError, IndexError):
        return 0


async def get_latest_complete_result_cohort(
    *,
    chat_id: int,
    before_date: date | None = None,
    oldest_date: date | None = None,
    as_of: datetime | None = None,
) -> dict | None:
    """Return card-only aggregates for the latest fully settled community cohort."""
    cutoff_date = before_date or today_kst(as_of)
    cutoff_time = as_of or datetime.now(timezone.utc)
    if cutoff_time.tzinfo is None:
        cutoff_time = cutoff_time.replace(tzinfo=timezone.utc)
    pool = await get_db()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            WITH cohort_counts AS (
                SELECT
                    p.pick_date,
                    COUNT(*)::int AS total_count,
                    COUNT(p.settlement_snapshot_id)::int AS settled_count,
                    MAX(p.settles_at) AS max_settles_at
                FROM renaiss_market_picks p
                WHERE p.community_chat_id = $1
                  AND p.pick_date < $2
                  AND ($4::date IS NULL OR p.pick_date >= $4)
                GROUP BY p.pick_date
            ), latest_complete AS (
                SELECT counts.pick_date
                FROM cohort_counts counts
                WHERE counts.total_count = counts.settled_count
                  AND counts.max_settles_at <= $3
                  AND NOT EXISTS (
                      SELECT 1
                      FROM renaiss_market_picks invalid_pick
                      JOIN renaiss_market_price_snapshots invalid_snapshot
                        ON invalid_snapshot.id = invalid_pick.settlement_snapshot_id
                      JOIN renaiss_market_board invalid_board
                        ON invalid_board.id = invalid_pick.board_card_id
                      WHERE invalid_pick.community_chat_id = $1
                        AND invalid_pick.pick_date = counts.pick_date
                        AND (
                            invalid_snapshot.pick_eligible IS NOT TRUE
                            OR invalid_snapshot.price_updated_at IS NULL
                            OR invalid_snapshot.confidence_score IS NULL
                            OR invalid_snapshot.source_count IS NULL
                            OR invalid_snapshot.asset_url IS NULL
                            OR invalid_snapshot.asset_url NOT LIKE 'https://%'
                            OR invalid_snapshot.board_card_id <> invalid_pick.board_card_id
                            OR invalid_snapshot.fmv_usd <= 0
                            OR invalid_pick.entry_fmv_usd <= 0
                            OR invalid_snapshot.fmv_usd::text IN ('NaN', 'Infinity', '-Infinity')
                            OR invalid_pick.entry_fmv_usd::text IN ('NaN', 'Infinity', '-Infinity')
                            OR invalid_snapshot.confidence_score::text IN ('NaN', 'Infinity', '-Infinity')
                            OR invalid_snapshot.captured_at < invalid_pick.settles_at
                            OR invalid_snapshot.price_updated_at < invalid_pick.settles_at
                        )
                  )
                ORDER BY counts.pick_date DESC
                LIMIT 1
            ), individual_results AS (
                SELECT
                    pick.pick_date,
                    pick.user_id,
                    pick.board_card_id,
                    board.card_name,
                    board.grade,
                    snapshot.asset_url,
                    snapshot.price_source,
                    snapshot.confidence_score,
                    snapshot.source_count,
                    snapshot.price_updated_at,
                    ((snapshot.fmv_usd / NULLIF(pick.entry_fmv_usd, 0)) - 1) * 100
                        AS move_pct
                FROM latest_complete latest
                JOIN renaiss_market_picks pick
                  ON pick.pick_date = latest.pick_date
                 AND pick.community_chat_id = $1
                JOIN renaiss_market_board board ON board.id = pick.board_card_id
                JOIN renaiss_market_price_snapshots snapshot
                  ON snapshot.id = pick.settlement_snapshot_id
            ), card_stats AS (
                SELECT
                    pick_date,
                    board_card_id,
                    MAX(card_name) AS card_name,
                    MAX(grade) AS grade,
                    MAX(asset_url) AS asset_url,
                    MIN(price_source) AS price_source,
                    COUNT(DISTINCT price_source)::int AS price_source_count,
                    MIN(confidence_score) AS min_confidence_score,
                    MIN(source_count)::int AS min_source_count,
                    MIN(price_updated_at) AS earliest_price_updated_at,
                    MAX(price_updated_at) AS latest_price_updated_at,
                    COUNT(DISTINCT user_id)::int AS support_count,
                    percentile_cont(0.5) WITHIN GROUP (
                        ORDER BY move_pct::double precision
                    ) AS median_move_pct
                FROM individual_results
                GROUP BY pick_date, board_card_id
            )
            SELECT
                stats.*,
                SUM(stats.support_count) OVER()::int AS participant_count
            FROM card_stats stats
            ORDER BY stats.support_count DESC, stats.board_card_id
            """,
            chat_id,
            cutoff_date,
            cutoff_time,
            oldest_date,
        )
    if not rows:
        return None
    cards = []
    for row in rows:
        item = dict(row)
        item["median_move_pct"] = float(item["median_move_pct"])
        cards.append(item)
    return {
        "pick_date": cards[0]["pick_date"],
        "participant_count": int(cards[0]["participant_count"]),
        "cards": cards,
    }


async def enqueue_daily_pick_result_bell(
    *,
    chat_id: int,
    bell_date: date,
    cohort_pick_date: date,
    message_text: str | None,
    metrics: dict,
    suppression_reason: str | None = None,
    available_at: datetime | None = None,
) -> dict | None:
    """Create one immutable publication intent for this chat/day and cohort."""
    state = "suppressed" if message_text is None else "pending"
    if state == "suppressed" and not suppression_reason:
        raise ValueError("suppressed result bells require a reason")
    event_key = f"daily-result-bell:{chat_id}:{cohort_pick_date.isoformat()}"
    pool = await get_db()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO renaiss_result_bell_outbox (
                event_key, chat_id, bell_date, cohort_pick_date, state,
                message_text, metrics, suppression_reason, available_at, expires_at
            )
            VALUES (
                $1,$2,$3,$4,$5,$6,$7::jsonb,$8,COALESCE($9::timestamptz, now()),
                (($3::date + 1)::timestamp AT TIME ZONE 'Asia/Seoul')
            )
            ON CONFLICT DO NOTHING
            RETURNING *
            """,
            event_key,
            chat_id,
            bell_date,
            cohort_pick_date,
            state,
            message_text,
            json.dumps(metrics, ensure_ascii=False, default=str),
            suppression_reason,
            available_at,
        )
    return dict(row) if row is not None else None


async def claim_daily_pick_result_bell(
    *,
    chat_id: int,
    bell_date: date,
    attempt_token: str,
    lease_owner: str,
) -> dict | None:
    """Claim one current-window bell and recover only pre-send stale claims."""
    pool = await get_db()
    async with pool.acquire() as conn, conn.transaction():
        await conn.execute(
            """
            UPDATE renaiss_result_bell_outbox
            SET state = 'pending', attempt_token = NULL, lease_owner = NULL,
                lease_expires_at = NULL, updated_at = now()
            WHERE state = 'claimed' AND lease_expires_at < now() AND expires_at > now()
            """
        )
        await conn.execute(
            """
            UPDATE renaiss_result_bell_outbox
            SET state = 'delivery_unknown', last_error_code = 'inflight_lease_expired',
                lease_owner = NULL, lease_expires_at = NULL, updated_at = now()
            WHERE state = 'inflight' AND lease_expires_at < now()
            """
        )
        await conn.execute(
            """
            UPDATE renaiss_result_bell_outbox
            SET state = 'dead', last_error_code = 'window_expired', updated_at = now()
            WHERE state IN ('pending', 'retryable', 'claimed') AND expires_at <= now()
            """
        )
        row = await conn.fetchrow(
            """
            WITH candidate AS MATERIALIZED (
                SELECT id
                FROM renaiss_result_bell_outbox
                WHERE state IN ('pending', 'retryable')
                  AND chat_id = $1
                  AND bell_date = $2
                  AND available_at <= now()
                  AND now() < expires_at
                  AND (next_attempt_at IS NULL OR next_attempt_at <= now())
                ORDER BY available_at, id
                LIMIT 1
                FOR UPDATE SKIP LOCKED
            )
            UPDATE renaiss_result_bell_outbox outbox
            SET state = 'claimed', attempt_token = $3, lease_owner = $4,
                lease_expires_at = now() + interval '30 seconds', updated_at = now()
            FROM candidate
            WHERE outbox.id = candidate.id
            RETURNING outbox.*
            """,
            chat_id,
            bell_date,
            attempt_token,
            lease_owner,
        )
    return dict(row) if row is not None else None


async def begin_daily_pick_result_bell_delivery(
    *,
    outbox_id: int,
    attempt_token: str,
) -> dict | None:
    pool = await get_db()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            UPDATE renaiss_result_bell_outbox
            SET state = 'inflight', attempt_count = attempt_count + 1,
                attempted_at = now(), lease_expires_at = now() + interval '90 seconds',
                updated_at = now()
            WHERE id = $1 AND state = 'claimed' AND attempt_token = $2
            RETURNING *
            """,
            outbox_id,
            attempt_token,
        )
    return dict(row) if row is not None else None


async def mark_daily_pick_result_bell_sent(
    *,
    outbox_id: int,
    attempt_token: str,
    telegram_message_id: int,
) -> bool:
    pool = await get_db()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            UPDATE renaiss_result_bell_outbox
            SET state = 'sent', telegram_message_id = $3, sent_at = now(),
                lease_owner = NULL, lease_expires_at = NULL, updated_at = now()
            WHERE id = $1 AND state = 'inflight' AND attempt_token = $2
            RETURNING id
            """,
            outbox_id,
            attempt_token,
            telegram_message_id,
        )
    return row is not None


async def mark_daily_pick_result_bell_failed(
    *,
    outbox_id: int,
    attempt_token: str,
    state: str,
    error_code: str,
    error: str,
    next_attempt_at: datetime | None = None,
) -> bool:
    if state not in {"retryable", "delivery_unknown", "dead"}:
        raise ValueError("invalid result bell failure state")
    pool = await get_db()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            UPDATE renaiss_result_bell_outbox
            SET state = $3, last_error_code = $4, last_error = $5,
                next_attempt_at = $6, lease_owner = NULL, lease_expires_at = NULL,
                updated_at = now()
            WHERE id = $1 AND state = 'inflight' AND attempt_token = $2
            RETURNING id
            """,
            outbox_id,
            attempt_token,
            state,
            error_code[:64],
            error[:500],
            next_attempt_at,
        )
    return row is not None


_RESULT_BELL_RECONCILIATION_COLUMNS = """
    id, event_key, chat_id, bell_date, cohort_pick_date, state,
    expires_at, next_attempt_at, attempt_count, attempted_at,
    telegram_message_id, last_error_code, last_error, updated_at, sent_at
"""


async def get_daily_pick_result_bell_reconciliation(outbox_id: int) -> dict | None:
    """Return the privacy-safe fields an operator needs before reconciliation."""
    if outbox_id <= 0:
        raise ValueError("invalid result bell outbox id")
    pool = await get_db()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            f"""
            SELECT {_RESULT_BELL_RECONCILIATION_COLUMNS}
            FROM renaiss_result_bell_outbox
            WHERE id = $1
            """,
            outbox_id,
        )
    return dict(row) if row is not None else None


async def reconcile_daily_pick_result_bell(
    *,
    outbox_id: int,
    action: str,
    operator_name: str,
    telegram_message_id: int | None = None,
    note: str | None = None,
    as_of: datetime | None = None,
) -> dict:
    """Reconcile one ambiguous delivery with a fenced, audited transition.

    ``mark-sent`` is legal after an operator finds the Telegram message and
    supplies its id. ``retry`` is legal only while the original bell window is
    still open. The outbox update and audit event commit together.
    """
    if outbox_id <= 0:
        raise ResultBellReconciliationError("invalid_outbox_id")
    normalized_action = action.strip().lower()
    if normalized_action not in {"mark-sent", "retry"}:
        raise ResultBellReconciliationError("invalid_action")
    operator = operator_name.strip()
    if not operator or len(operator) > 128:
        raise ResultBellReconciliationError("invalid_operator")
    operator_note = (note or "").strip()
    if len(operator_note) > 1000:
        raise ResultBellReconciliationError("note_too_long")
    if normalized_action == "mark-sent":
        if telegram_message_id is None or telegram_message_id <= 0:
            raise ResultBellReconciliationError("telegram_message_id_required")
    elif telegram_message_id is not None:
        raise ResultBellReconciliationError("telegram_message_id_not_allowed")

    current = as_of or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    else:
        current = current.astimezone(timezone.utc)

    pool = await get_db()
    async with pool.acquire() as conn, conn.transaction():
        locked = await conn.fetchrow(
            f"""
            SELECT {_RESULT_BELL_RECONCILIATION_COLUMNS}
            FROM renaiss_result_bell_outbox
            WHERE id = $1
            FOR UPDATE
            """,
            outbox_id,
        )
        if locked is None:
            raise ResultBellReconciliationError("not_found")
        previous = dict(locked)
        if previous["state"] != "delivery_unknown":
            raise ResultBellReconciliationError("invalid_state", row=previous)

        if normalized_action == "retry":
            expires_at = previous["expires_at"]
            if expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=timezone.utc)
            else:
                expires_at = expires_at.astimezone(timezone.utc)
            if expires_at <= current:
                raise ResultBellReconciliationError("window_expired", row=previous)
            updated = await conn.fetchrow(
                f"""
                UPDATE renaiss_result_bell_outbox
                SET state = 'retryable', next_attempt_at = $2,
                    attempt_token = NULL, lease_owner = NULL,
                    lease_expires_at = NULL, updated_at = $2
                WHERE id = $1
                  AND state = 'delivery_unknown'
                  AND expires_at > $2
                RETURNING {_RESULT_BELL_RECONCILIATION_COLUMNS}
                """,
                outbox_id,
                current,
            )
            target_state = "retryable"
        else:
            updated = await conn.fetchrow(
                f"""
                UPDATE renaiss_result_bell_outbox
                SET state = 'sent', telegram_message_id = $2, sent_at = $3,
                    next_attempt_at = NULL, attempt_token = NULL,
                    lease_owner = NULL, lease_expires_at = NULL, updated_at = $3
                WHERE id = $1 AND state = 'delivery_unknown'
                RETURNING {_RESULT_BELL_RECONCILIATION_COLUMNS}
                """,
                outbox_id,
                telegram_message_id,
                current,
            )
            target_state = "sent"
        if updated is None:
            raise ResultBellReconciliationError("state_changed", row=previous)

        audit_metadata = {
            "outbox_id": outbox_id,
            "action": normalized_action,
            "operator": operator,
            "note": operator_note or None,
            "from_state": "delivery_unknown",
            "to_state": target_state,
            "chat_id": previous["chat_id"],
            "bell_date": previous["bell_date"],
            "cohort_pick_date": previous["cohort_pick_date"],
            "previous_error_code": previous.get("last_error_code"),
            "previous_error": previous.get("last_error"),
            "telegram_message_id": telegram_message_id,
            "expires_at": previous["expires_at"],
        }
        audit = await conn.fetchrow(
            """
            INSERT INTO renaiss_events (
                event_name, event_key, chat_id, metadata
            ) VALUES (
                'daily_pick_result_reconciled', $1, $2, $3::jsonb
            )
            RETURNING id
            """,
            (
                f"daily-result-bell:{outbox_id}:attempt-"
                f"{int(previous.get('attempt_count') or 0)}:operator-{normalized_action}"
            ),
            previous["chat_id"],
            json.dumps(audit_metadata, ensure_ascii=False, default=str),
        )
        if audit is None:
            raise ResultBellReconciliationError("audit_event_failed", row=previous)

    result = dict(updated)
    result["audit_event_id"] = int(audit["id"])
    return result


async def record_market_price_snapshot(
    *,
    board_card_id: int,
    card: CardIdentity,
    price: RenaissPrice,
) -> bool:
    """Record a refreshed mark once; stale or non-exact marks remain ineligible."""
    if (
        price.fmv_usd is None
        or isinstance(price.fmv_usd, bool)
        or not math.isfinite(price.fmv_usd)
        or price.fmv_usd <= 0
    ):
        return False
    eligible = market_card_eligible(card, price)
    updated_at = price.price_updated_at
    if updated_at is not None and updated_at.tzinfo is None:
        updated_at = updated_at.replace(tzinfo=timezone.utc)
    pool = await get_db()
    async with pool.acquire() as conn, conn.transaction():
        await conn.fetchval(
            "SELECT pg_advisory_xact_lock(hashtextextended($1, 0))",
            f"renaiss-market-snapshot:{board_card_id}",
        )
        inserted = await conn.fetchrow(
            """
            INSERT INTO renaiss_market_price_snapshots (
                board_card_id, fmv_usd, price_source, confidence, pick_eligible,
                confidence_score, source_count, observation_count, valuation_method,
                price_updated_at, asset_url
            )
            SELECT $1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11
             WHERE NOT EXISTS (
                 SELECT 1
                 FROM renaiss_market_price_snapshots
                 WHERE board_card_id = $1
                   AND fmv_usd = $2
                   AND price_source = $3
                   AND confidence IS NOT DISTINCT FROM $4
                   AND pick_eligible = $5
                   AND confidence_score IS NOT DISTINCT FROM $6
                   AND source_count IS NOT DISTINCT FROM $7
                   AND observation_count IS NOT DISTINCT FROM $8
                   AND valuation_method IS NOT DISTINCT FROM $9
                   AND price_updated_at IS NOT DISTINCT FROM $10
                   AND asset_url IS NOT DISTINCT FROM $11
             )
            RETURNING id
            """,
            board_card_id,
            _decimal(float(price.fmv_usd)),
            price.source,
            price.confidence,
            eligible,
            price.confidence_score,
            price.source_count,
            price.observation_count,
            price.valuation_method,
            updated_at,
            price.asset_url,
        )
        await conn.execute(
            """
            UPDATE renaiss_market_board
            SET price_source = $2,
                confidence = $3,
                confidence_score = $4,
                source_count = $5,
                observation_count = $6,
                valuation_method = $7,
                pick_eligible = $8
            WHERE id = $1
            """,
            board_card_id,
            price.source,
            price.confidence,
            price.confidence_score,
            price.source_count,
            price.observation_count,
            price.valuation_method,
            eligible,
        )
    return inserted is not None
