"""Query helpers for standalone Renaiss bot KPI logging."""

from __future__ import annotations

import json
import logging
import math
import os
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from renaiss_bot.database.connection import get_db
from renaiss_bot.services.models import CardIdentity, PackOpenResult, RenaissPrice
from renaiss_bot.services.pack_rules import CARDS_PER_PACK
from renaiss_bot.services.market import market_card_eligible

logger = logging.getLogger(__name__)

KST = ZoneInfo("Asia/Seoul")


@dataclass(frozen=True)
class SpawnAwardResult:
    event_id: int
    created: bool


class SpawnAwardConflict(RuntimeError):
    """An idempotency key was reused for a different spawn award."""


@dataclass(frozen=True)
class PackOpenReservation:
    request_id: str
    status: str
    allowed_count: int
    used_before: int
    quota_date: date
    created: bool


class PackOpenReservationError(RuntimeError):
    pass


@dataclass(frozen=True)
class FirstCStarterGrant:
    event_id: int
    created: bool


STARTER_CATEGORY = "other_renaiss_cards"
STARTER_LOCAL_CARD_ID = "renaiss:starter:welcome:v1"
STARTER_CARD_NAME = "Renaiss Welcome Card"


def _today_kst() -> date:
    return datetime.now(KST).date()


def _snapshot_ttl_minutes() -> int:
    try:
        return max(0, int(os.getenv("RENAISS_PRICE_CACHE_TTL_MINUTES", "10")))
    except ValueError:
        return 10


def _bounded_env_int(name: str, default: int, *, minimum: int, maximum: int) -> int:
    try:
        return min(maximum, max(minimum, int(os.getenv(name, str(default)))))
    except (TypeError, ValueError):
        return default


def _local_card_id(card: CardIdentity) -> str:
    return card.local_card_id or f"{card.category}:{card.card_name}:{card.grade}:{card.set_code}:{card.collector_number}"


def _dt(value) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    return None


def _collection_snapshot_value(
    card: CardIdentity,
    price: RenaissPrice | None = None,
) -> float | None:
    if price is not None and market_card_eligible(card, price):
        value = price.fmv_usd
    else:
        value = card.market_price_usd
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if math.isfinite(number) and number > 0 else None


async def log_price_snapshot(*, card: CardIdentity, price: RenaissPrice) -> None:
    try:
        pool = await get_db()
        async with pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO renaiss_price_snapshots (
                    category, card_name, local_card_id, renaiss_asset_id, match_status,
                    fmv_usd, change_7d_pct, market_status, source, asset_url,
                    referral_url, image_url, grade_label, grading_company, confidence,
                    confidence_score, source_count, observation_count, valuation_method,
                    price_updated_at
                )
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, $16, $17, $18, $19, $20)
                """,
                card.category,
                card.card_name,
                _local_card_id(card),
                price.renaiss_asset_id,
                price.status,
                price.fmv_usd,
                price.change_7d_pct,
                price.market_status,
                price.source,
                price.asset_url,
                price.referral_url,
                price.image_url,
                price.grade_label,
                price.grading_company,
                price.confidence,
                price.confidence_score,
                price.source_count,
                price.observation_count,
                price.valuation_method,
                price.price_updated_at,
            )
    except Exception as exc:
        logger.debug("Renaiss price snapshot skipped: %s", exc)


async def get_recent_price_snapshot(card: CardIdentity) -> RenaissPrice | None:
    ttl = _snapshot_ttl_minutes()
    if ttl <= 0:
        return None
    try:
        pool = await get_db()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT *
                FROM renaiss_price_snapshots
                WHERE local_card_id = $1
                  AND category = $2
                  AND created_at >= now() - ($3::int * interval '1 minute')
                ORDER BY created_at DESC
                LIMIT 1
                """,
                _local_card_id(card),
                card.category,
                ttl,
            )
        if not row:
            return None
        status = row["match_status"] or "search_only"
        if status not in {"exact", "candidate", "search_only", "missing", "api_error"}:
            status = "search_only"
        return RenaissPrice(
            status=status,
            source=f"{row['source'] or 'snapshot'}-cache",
            renaiss_asset_id=row["renaiss_asset_id"],
            asset_url=row["asset_url"],
            referral_url=row["referral_url"],
            image_url=row["image_url"],
            grade_label=row["grade_label"],
            grading_company=row["grading_company"],
            confidence=row["confidence"],
            confidence_score=(
                float(row["confidence_score"])
                if row["confidence_score"] is not None
                else None
            ),
            source_count=row["source_count"],
            observation_count=row["observation_count"],
            valuation_method=row["valuation_method"],
            fmv_usd=float(row["fmv_usd"]) if row["fmv_usd"] is not None else None,
            change_7d_pct=float(row["change_7d_pct"]) if row["change_7d_pct"] is not None else None,
            market_status=row["market_status"] or "cached",
            price_updated_at=_dt(row["price_updated_at"]),
        )
    except Exception as exc:
        logger.debug("Renaiss recent price snapshot skipped: %s", exc)
        return None


async def log_referral_click(
    *,
    user_id: int | None,
    chat_id: int | None,
    local_card_id: str | None,
    source: str,
    tracking_token_hash: str,
    destination_url: str,
) -> bool:
    """Record a redirect hit; repeated hits remain visible for funnel analysis."""
    pool = await get_db()
    async with pool.acquire() as conn:
        result = await conn.execute(
            """
            INSERT INTO renaiss_referral_clicks (
                user_id, chat_id, local_card_id, source,
                tracking_token_hash, destination_url
            ) VALUES ($1,$2,$3,$4,$5,$6)
            """,
            user_id,
            chat_id,
            local_card_id,
            source,
            tracking_token_hash,
            destination_url,
        )
    return result == "INSERT 0 1"


async def log_pack_event(
    *,
    user_id: int | None,
    chat_id: int | None,
    category: str,
    best_card: CardIdentity,
    price: RenaissPrice,
    pack_type: str = "free",
    pack_count: int = 1,
    card_count: int = 0,
    pool_source: str | None = None,
    source: str = "command",
) -> None:
    try:
        pool = await get_db()
        async with pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO renaiss_pack_events (
                    user_id, chat_id, category, pack_type, pack_count, card_count,
                    pool_source, best_local_card_id, match_status, fmv_usd, source, card_name
                )
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12)
                """,
                user_id,
                chat_id,
                category,
                pack_type,
                pack_count,
                card_count,
                pool_source,
                best_card.local_card_id,
                price.status,
                price.fmv_usd,
                source,
                best_card.card_name,
            )
    except Exception as exc:
        logger.debug("Renaiss pack event log skipped: %s", exc)


def _reservation_from_row(row, *, created: bool) -> PackOpenReservation:
    return PackOpenReservation(
        request_id=str(row["request_id"]),
        status=str(row["status"]),
        allowed_count=int(row["pack_count"]),
        used_before=int(row["used_before"]),
        quota_date=row["quota_date"],
        created=created,
    )


async def reserve_command_free_packs(
    *,
    request_id: str,
    user_id: int,
    chat_id: int | None,
    platform: str,
    category: str,
    requested_count: int,
    daily_limit: int,
    lease_minutes: int = 5,
) -> PackOpenReservation:
    """Reserve daily free quota under a short per-user PostgreSQL lock."""
    if not request_id or len(request_id) > 200 or requested_count <= 0 or daily_limit <= 0:
        raise PackOpenReservationError("invalid pack reservation")
    pool = await get_db()
    async with pool.acquire() as conn, conn.transaction():
        await conn.fetchval(
            "SELECT pg_advisory_xact_lock(hashtextextended($1, 0))",
            f"renaiss-pack-open:{user_id}",
        )
        existing = await conn.fetchrow(
            """
            SELECT request_id, user_id, chat_id, platform, category, pack_count,
                   used_before, quota_date, status
            FROM renaiss_pack_open_requests
            WHERE request_id = $1
            """,
            request_id,
        )
        if existing is not None:
            if (
                existing["user_id"] != user_id
                or existing["chat_id"] != chat_id
                or existing["platform"] != platform
                or existing["category"] != category
            ):
                raise PackOpenReservationError("pack request id belongs to another request")
            return _reservation_from_row(existing, created=False)

        await conn.execute(
            """
            UPDATE renaiss_pack_open_requests
            SET status = 'expired', updated_at = clock_timestamp()
            WHERE user_id = $1
              AND status = 'reserved'
              AND reserved_until <= clock_timestamp()
            """,
            user_id,
        )
        quota_date = await conn.fetchval(
            "SELECT (clock_timestamp() AT TIME ZONE 'Asia/Seoul')::date"
        )
        legacy_completed_count = await conn.fetchval(
            """
            SELECT COALESCE(SUM(pack_count), 0)::int
            FROM renaiss_pack_events
            WHERE user_id = $1
              AND source = 'command'
              AND pack_type = 'free'
              AND request_id IS NULL
              AND (created_at AT TIME ZONE 'Asia/Seoul')::date = $2
            """,
            user_id,
            quota_date,
        )
        ledger_count = await conn.fetchval(
            """
            SELECT COALESCE(SUM(pack_count), 0)::int
            FROM renaiss_pack_open_requests
            WHERE user_id = $1
              AND quota_date = $2
              AND (
                    status = 'completed'
                    OR (status = 'reserved' AND reserved_until > clock_timestamp())
              )
            """,
            user_id,
            quota_date,
        )
        used_before = int(legacy_completed_count or 0) + int(ledger_count or 0)
        allowed_count = min(requested_count, max(0, daily_limit - used_before))
        if allowed_count <= 0:
            return PackOpenReservation(
                request_id=request_id,
                status="quota_full",
                allowed_count=0,
                used_before=used_before,
                quota_date=quota_date,
                created=False,
            )
        row = await conn.fetchrow(
            """
            INSERT INTO renaiss_pack_open_requests (
                request_id, user_id, chat_id, platform, category, pack_type,
                pack_count, used_before, quota_date, status, reserved_until
            )
            VALUES ($1,$2,$3,$4,$5,'free',$6,$7,$8,'reserved',
                    clock_timestamp() + ($9::int * interval '1 minute'))
            RETURNING request_id, pack_count, used_before, quota_date, status
            """,
            request_id,
            user_id,
            chat_id,
            platform,
            category,
            allowed_count,
            used_before,
            quota_date,
            max(1, lease_minutes),
        )
    return _reservation_from_row(row, created=True)


async def cancel_pack_open_reservation(request_id: str) -> bool:
    pool = await get_db()
    async with pool.acquire() as conn:
        result = await conn.execute(
            """
            UPDATE renaiss_pack_open_requests
            SET status = 'failed', updated_at = clock_timestamp()
            WHERE request_id = $1 AND status = 'reserved'
            """,
            request_id,
        )
    return result == "UPDATE 1"


async def finalize_command_free_pack(
    *,
    request_id: str,
    user_id: int,
    chat_id: int | None,
    result: PackOpenResult,
) -> bool:
    """Commit cards, usage event, and reservation completion atomically."""
    pool = await get_db()
    async with pool.acquire() as conn, conn.transaction():
        await conn.fetchval(
            "SELECT pg_advisory_xact_lock(hashtextextended($1, 0))",
            f"renaiss-pack-open:{user_id}",
        )
        reservation = await conn.fetchrow(
            """
            SELECT request_id, user_id, chat_id, category, pack_count, status,
                   reserved_until > clock_timestamp() AS lease_active
            FROM renaiss_pack_open_requests
            WHERE request_id = $1
            FOR UPDATE
            """,
            request_id,
        )
        if reservation is None:
            raise PackOpenReservationError("pack reservation not found")
        if reservation["user_id"] != user_id or reservation["chat_id"] != chat_id:
            raise PackOpenReservationError("pack reservation owner mismatch")
        if reservation["status"] == "completed":
            return False
        if reservation["status"] != "reserved" or not reservation["lease_active"]:
            raise PackOpenReservationError("pack reservation is not active")
        if reservation["category"] != result.category or int(reservation["pack_count"]) != result.pack_count:
            raise PackOpenReservationError("pack result does not match reservation")
        if result.pack_type != "free" or len(result.cards) != result.pack_count * CARDS_PER_PACK:
            raise PackOpenReservationError("pack result card count does not match reservation")
        if any(card.category != result.category for card in result.cards):
            raise PackOpenReservationError("pack result contains a mismatched category")

        merged: dict[str, tuple[list, int]] = {}
        best_card_id = result.best_card.local_card_id or (
            f"{result.best_card.category}:{result.best_card.card_name}:{result.best_card.grade}"
        )
        for card in result.cards:
            card_id = card.local_card_id or f"{card.category}:{card.card_name}:{card.grade}"
            snapshot_price = _collection_snapshot_value(
                card,
                result.best_price if card_id == best_card_id else None,
            )
            fields = [
                user_id,
                result.category,
                card_id,
                card.card_name,
                card.grade,
                card.set_code,
                card.collector_number,
                card.image_url,
                snapshot_price,
            ]
            if card_id in merged:
                merged[card_id] = (merged[card_id][0], merged[card_id][1] + 1)
            else:
                merged[card_id] = (fields, 1)
        rows = [(*fields, count) for fields, count in merged.values()]
        if not rows:
            raise PackOpenReservationError("pack result contains no cards")
        await conn.executemany(
            """
            INSERT INTO renaiss_user_cards (
                user_id, category, local_card_id, card_name, grade, set_code,
                collector_number, image_url, market_price_usd, quantity
            )
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10)
            ON CONFLICT (user_id, category, local_card_id)
            DO UPDATE SET
                quantity = renaiss_user_cards.quantity + EXCLUDED.quantity,
                market_price_usd = COALESCE(
                    EXCLUDED.market_price_usd,
                    renaiss_user_cards.market_price_usd
                ),
                updated_at = now()
            """,
            rows,
        )
        await conn.execute(
            """
            INSERT INTO renaiss_pack_events (
                user_id, chat_id, category, pack_type, pack_count, card_count,
                pool_source, best_local_card_id, match_status, fmv_usd, source,
                card_name, request_id
            ) VALUES ($1,$2,$3,'free',$4,$5,$6,$7,$8,$9,'command',$10,$11)
            """,
            user_id,
            chat_id,
            result.category,
            result.pack_count,
            len(result.cards),
            result.pool_source,
            result.best_card.local_card_id,
            result.best_price.status,
            result.best_price.fmv_usd,
            result.best_card.card_name,
            request_id,
        )
        updated = await conn.fetchrow(
            """
            UPDATE renaiss_pack_open_requests
            SET status = 'completed', updated_at = clock_timestamp()
            WHERE request_id = $1 AND status = 'reserved'
            RETURNING request_id
            """,
            request_id,
        )
        if updated is None:
            raise PackOpenReservationError("pack reservation completion lost")
    return True


async def count_command_free_packs_today(user_id: int | None) -> int:
    """오늘(KST) 명령으로 연 프리팩 수 — 일일 무료 게이트 판정용.
    보상 지급(drop/quiz)은 source 가 달라 집계에서 빠진다."""
    if user_id is None:
        return 0
    try:
        pool = await get_db()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT COALESCE(SUM(pack_count), 0)::int AS n
                FROM renaiss_pack_events
                WHERE user_id = $1
                  AND source = 'command'
                  AND pack_type = 'free'
                  AND (created_at AT TIME ZONE 'Asia/Seoul')::date = $2
                """,
                user_id,
                _today_kst(),
            )
        return int(row["n"]) if row else 0
    except Exception as exc:
        logger.debug("Renaiss free pack count skipped: %s", exc)
        return 0


async def get_points(user_id: int | None) -> int:
    if user_id is None:
        return 0
    try:
        pool = await get_db()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT points FROM renaiss_user_points WHERE user_id = $1",
                user_id,
            )
        return int(row["points"]) if row else 0
    except Exception as exc:
        logger.debug("Renaiss points fetch skipped: %s", exc)
        return 0


async def spend_points(user_id: int | None, amount: int, *, source: str) -> bool:
    """RP 차감 (원자적 — 잔액 부족이면 False, 아무 변화 없음)."""
    if user_id is None or amount <= 0:
        return False
    try:
        pool = await get_db()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                UPDATE renaiss_user_points
                SET points = points - $2, updated_at = now()
                WHERE user_id = $1 AND points >= $2
                RETURNING points
                """,
                user_id,
                amount,
            )
            if row is None:
                return False
            await conn.execute(
                "INSERT INTO renaiss_point_events (user_id, amount, source) VALUES ($1, $2, $3)",
                user_id,
                -amount,
                source,
            )
        return True
    except Exception as exc:
        logger.warning("Renaiss points spend failed user=%s: %s", user_id, exc)
        return False


async def register_pack_cards(
    *,
    user_id: int | None,
    chat_id: int | None,
    result: PackOpenResult,
) -> None:
    if user_id is None:
        return
    try:
        # 같은 카드가 한 팩에 여러 장이면 개수를 합쳐서 1행으로 (executemany 는
        # 같은 PK 를 한 배치에서 두 번 만나면 ON CONFLICT 가 중복 에러를 낸다)
        merged: dict[str, tuple[list, int]] = {}
        for card in result.cards:
            card_id = card.local_card_id or f"{card.category}:{card.card_name}:{card.grade}"
            best_card_id = result.best_card.local_card_id or (
                f"{result.best_card.category}:{result.best_card.card_name}:{result.best_card.grade}"
            )
            snapshot_price = _collection_snapshot_value(
                card,
                result.best_price if card_id == best_card_id else None,
            )
            if card_id in merged:
                merged[card_id] = (merged[card_id][0], merged[card_id][1] + 1)
            else:
                merged[card_id] = (
                    [
                        user_id,
                        result.category,
                        card_id,
                        card.card_name,
                        card.grade,
                        card.set_code,
                        card.collector_number,
                        card.image_url,
                        snapshot_price,
                    ],
                    1,
                )
        rows = [(*fields, count) for fields, count in merged.values()]

        pool = await get_db()
        async with pool.acquire() as conn:
            await conn.executemany(
                """
                INSERT INTO renaiss_user_cards (
                    user_id, category, local_card_id, card_name, grade, set_code,
                    collector_number, image_url, market_price_usd, quantity
                )
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
                ON CONFLICT (user_id, category, local_card_id)
                DO UPDATE SET
                    quantity = renaiss_user_cards.quantity + EXCLUDED.quantity,
                    market_price_usd = COALESCE(
                        EXCLUDED.market_price_usd,
                        renaiss_user_cards.market_price_usd
                    ),
                    updated_at = now()
                """,
                rows,
            )
    except Exception as exc:
        logger.debug("Renaiss user card register skipped chat=%s: %s", chat_id, exc)


async def register_single_card(*, user_id: int, category: str, card) -> None:
    """스폰에서 잡은 카드 1장을 유저 컬렉션에 등록 (중복이면 수량+1)."""
    try:
        card_id = card.local_card_id or f"{card.category}:{card.card_name}:{card.grade}"
        pool = await get_db()
        async with pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO renaiss_user_cards (
                    user_id, category, local_card_id, card_name, grade, set_code,
                    collector_number, image_url, market_price_usd, quantity
                )
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, 1)
                ON CONFLICT (user_id, category, local_card_id)
                DO UPDATE SET
                    quantity = renaiss_user_cards.quantity + 1,
                    updated_at = now()
                """,
                user_id,
                category,
                card_id,
                card.card_name,
                card.grade,
                card.set_code,
                card.collector_number,
                card.image_url,
                card.market_price_usd,
            )
    except Exception as exc:
        logger.debug("Renaiss single card register skipped user=%s: %s", user_id, exc)


async def award_spawn_card(
    *,
    award_key: str,
    spawn_token: str,
    user_id: int,
    winner_name: str,
    chat_id: int,
    category: str,
    card: CardIdentity,
    price: RenaissPrice,
) -> SpawnAwardResult:
    """Atomically and idempotently award one spawn card.

    Unlike the legacy best-effort helpers, failures deliberately propagate so
    callers never announce a collection write that did not commit.
    """
    card_id = card.local_card_id or f"{card.category}:{card.card_name}:{card.grade}"
    snapshot_price = _collection_snapshot_value(card, price)
    metadata = {
        "category": category,
        "local_card_id": card_id,
        "card_name": card.card_name,
        "winner_name": winner_name,
        "fmv_usd": price.fmv_usd,
    }
    pool = await get_db()
    async with pool.acquire() as conn, conn.transaction():
        event = await conn.fetchrow(
            """
            INSERT INTO renaiss_events (
                event_name, event_key, user_id, chat_id, session_id, metadata
            ) VALUES ('catch_won', $1, $2, $3, $4, $5::jsonb)
            ON CONFLICT (event_key) DO NOTHING
            RETURNING id
            """,
            award_key,
            user_id,
            chat_id,
            spawn_token,
            json.dumps(metadata, ensure_ascii=False, default=str),
        )
        if event is None:
            existing = await conn.fetchrow(
                """
                SELECT id, event_name, user_id, chat_id, session_id, metadata
                FROM renaiss_events
                WHERE event_key = $1
                """,
                award_key,
            )
            if existing is None:
                raise SpawnAwardConflict("spawn award key disappeared after conflict")
            existing_metadata = existing["metadata"]
            if isinstance(existing_metadata, str):
                try:
                    existing_metadata = json.loads(existing_metadata)
                except json.JSONDecodeError:
                    existing_metadata = {}
            matches = (
                existing["event_name"] == "catch_won"
                and existing["user_id"] == user_id
                and existing["chat_id"] == chat_id
                and existing["session_id"] == spawn_token
                and isinstance(existing_metadata, dict)
                and existing_metadata.get("category") == category
                and existing_metadata.get("local_card_id") == card_id
            )
            if not matches:
                raise SpawnAwardConflict("spawn award key belongs to another award")
            return SpawnAwardResult(event_id=int(existing["id"]), created=False)

        await conn.execute(
            """
            INSERT INTO renaiss_user_cards (
                user_id, category, local_card_id, card_name, grade, set_code,
                collector_number, image_url, market_price_usd, quantity
            )
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, 1)
            ON CONFLICT (user_id, category, local_card_id)
            DO UPDATE SET
                quantity = renaiss_user_cards.quantity + 1,
                market_price_usd = COALESCE(
                    EXCLUDED.market_price_usd,
                    renaiss_user_cards.market_price_usd
                ),
                updated_at = now()
            """,
            user_id,
            category,
            card_id,
            card.card_name,
            card.grade,
            card.set_code,
            card.collector_number,
            card.image_url,
            snapshot_price,
        )
        await conn.execute(
            """
            INSERT INTO renaiss_pack_events (
                user_id, chat_id, category, pack_type, pack_count, card_count,
                pool_source, best_local_card_id, match_status, fmv_usd, source, card_name
            )
            VALUES ($1, $2, $3, 'spawn', 1, 1, 'spawn', $4, $5, $6, 'spawn', $7)
            """,
            user_id,
            chat_id,
            category,
            card_id,
            price.status,
            price.fmv_usd,
            card.card_name,
        )
    return SpawnAwardResult(event_id=int(event["id"]), created=True)


async def grant_first_c_starter(
    *,
    user_id: int,
    chat_id: int,
) -> FirstCStarterGrant:
    """Grant one lifetime, collection-only tutorial card atomically."""
    if user_id <= 0 or chat_id == 0:
        raise ValueError("invalid first-c starter owner")
    event_key = f"renaiss:first-c-starter:user:{user_id}"
    metadata = {
        "category": STARTER_CATEGORY,
        "local_card_id": STARTER_LOCAL_CARD_ID,
        "card_name": STARTER_CARD_NAME,
        "collection_only": True,
        "scored_eligible": False,
        "fmv_usd": None,
    }
    pool = await get_db()
    async with pool.acquire() as conn, conn.transaction():
        event = await conn.fetchrow(
            """
            INSERT INTO renaiss_events (
                event_name, event_key, user_id, chat_id, metadata
            ) VALUES ('first_c_starter_granted', $1, $2, $3, $4::jsonb)
            ON CONFLICT (event_key) DO NOTHING
            RETURNING id
            """,
            event_key,
            user_id,
            chat_id,
            json.dumps(metadata, ensure_ascii=False),
        )
        if event is None:
            existing = await conn.fetchrow(
                """
                SELECT id, event_name, user_id
                FROM renaiss_events
                WHERE event_key = $1
                """,
                event_key,
            )
            if (
                existing is None
                or existing["event_name"] != "first_c_starter_granted"
                or existing["user_id"] != user_id
            ):
                raise SpawnAwardConflict("first-c starter key belongs to another event")
            return FirstCStarterGrant(event_id=int(existing["id"]), created=False)

        inserted = await conn.fetchrow(
            """
            INSERT INTO renaiss_user_cards (
                user_id, category, local_card_id, card_name, grade, set_code,
                collector_number, image_url, market_price_usd, quantity, is_tutorial
            ) VALUES ($1,$2,$3,$4,'STARTER','RENAISS','WELCOME',NULL,NULL,1,TRUE)
            ON CONFLICT (user_id, category, local_card_id) DO NOTHING
            RETURNING user_id
            """,
            user_id,
            STARTER_CATEGORY,
            STARTER_LOCAL_CARD_ID,
            STARTER_CARD_NAME,
        )
        if inserted is None:
            existing_card = await conn.fetchrow(
                """
                SELECT card_name, grade, set_code, collector_number,
                       market_price_usd, quantity, is_tutorial
                FROM renaiss_user_cards
                WHERE user_id = $1 AND category = $2 AND local_card_id = $3
                FOR UPDATE
                """,
                user_id,
                STARTER_CATEGORY,
                STARTER_LOCAL_CARD_ID,
            )
            if (
                existing_card is None
                or existing_card["card_name"] != STARTER_CARD_NAME
                or existing_card["grade"] != "STARTER"
                or existing_card["set_code"] != "RENAISS"
                or existing_card["collector_number"] != "WELCOME"
                or existing_card["market_price_usd"] is not None
                or int(existing_card["quantity"]) != 1
                or existing_card["is_tutorial"] is not True
            ):
                raise SpawnAwardConflict("first-c starter card identity conflict")
    return FirstCStarterGrant(event_id=int(event["id"]), created=True)


async def get_portfolio_values(user_ids: list[int]) -> dict[int, float]:
    """유저별 누적 컬렉션 시세($) 한 번에. 잡기 결과에 '내 누적시세' 표시용."""
    if not user_ids:
        return {}
    try:
        pool = await get_db()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT user_id, COALESCE(SUM(quantity * COALESCE(market_price_usd, 0)), 0)::float AS total
                FROM renaiss_user_cards
                WHERE user_id = ANY($1::bigint[])
                GROUP BY user_id
                """,
                user_ids,
            )
        return {int(r["user_id"]): float(r["total"] or 0) for r in rows}
    except Exception as exc:
        logger.debug("Renaiss portfolio values skipped: %s", exc)
        return {}


async def add_drop_points(user_id: int | None, amount: int, *, source: str = "drop") -> None:
    if user_id is None or amount == 0:
        return
    try:
        pool = await get_db()
        async with pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO renaiss_user_points (user_id, points)
                VALUES ($1, $2)
                ON CONFLICT (user_id)
                DO UPDATE SET
                    points = renaiss_user_points.points + EXCLUDED.points,
                    updated_at = now()
                """,
                user_id,
                int(amount),
            )
            await conn.execute(
                """
                INSERT INTO renaiss_point_events (user_id, amount, source)
                VALUES ($1, $2, $3)
                """,
                user_id,
                int(amount),
                source,
            )
    except Exception as exc:
        logger.debug("Renaiss point add skipped user=%s amount=%s: %s", user_id, amount, exc)


# ── 포트폴리오 일일 스냅샷 (7d 변동 계산용) ──────────────────────────


async def upsert_portfolio_snapshot(user_id: int | None) -> None:
    """오늘(KST) 스냅샷을 해당 유저의 현재 보유 시세 합으로 갱신."""
    if user_id is None:
        return
    try:
        pool = await get_db()
        async with pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO renaiss_portfolio_snapshots (
                    user_id, snapshot_date, total_value_usd, unique_cards, total_cards
                )
                SELECT
                    $1,
                    $2::date,
                    COALESCE(SUM(quantity * COALESCE(market_price_usd, 0)) FILTER (
                        WHERE is_tutorial IS NOT TRUE
                    ), 0),
                    COUNT(*) FILTER (WHERE is_tutorial IS NOT TRUE)::int,
                    COALESCE(SUM(quantity) FILTER (
                        WHERE is_tutorial IS NOT TRUE
                    ), 0)::int
                FROM renaiss_user_cards
                WHERE user_id = $1
                ON CONFLICT (user_id, snapshot_date)
                DO UPDATE SET
                    total_value_usd = EXCLUDED.total_value_usd,
                    unique_cards = EXCLUDED.unique_cards,
                    total_cards = EXCLUDED.total_cards,
                    created_at = now()
                """,
                user_id,
                _today_kst(),
            )
    except Exception as exc:
        logger.debug("Renaiss portfolio snapshot skipped user=%s: %s", user_id, exc)


async def snapshot_all_portfolios() -> int:
    """카드 보유 전체 유저의 오늘(KST) 스냅샷 일괄 기록. 기록한 유저 수 반환."""
    try:
        pool = await get_db()
        async with pool.acquire() as conn:
            result = await conn.execute(
                """
                INSERT INTO renaiss_portfolio_snapshots (
                    user_id, snapshot_date, total_value_usd, unique_cards, total_cards
                )
                SELECT
                    user_id,
                    $1::date,
                    COALESCE(SUM(quantity * COALESCE(market_price_usd, 0)) FILTER (
                        WHERE is_tutorial IS NOT TRUE
                    ), 0),
                    COUNT(*) FILTER (WHERE is_tutorial IS NOT TRUE)::int,
                    COALESCE(SUM(quantity) FILTER (
                        WHERE is_tutorial IS NOT TRUE
                    ), 0)::int
                FROM renaiss_user_cards
                GROUP BY user_id
                ON CONFLICT (user_id, snapshot_date)
                DO UPDATE SET
                    total_value_usd = EXCLUDED.total_value_usd,
                    unique_cards = EXCLUDED.unique_cards,
                    total_cards = EXCLUDED.total_cards,
                    created_at = now()
                """,
                _today_kst(),
            )
        try:
            return int(result.split()[-1])
        except (ValueError, IndexError, AttributeError):
            return 0
    except Exception as exc:
        logger.debug("Renaiss snapshot_all skipped: %s", exc)
        return 0


async def get_portfolio_value_days_ago(user_id: int | None, *, days: int = 7) -> float | None:
    """N일 전(또는 그 이전 가장 가까운) 스냅샷 총액. 없으면 보유 기간 내 가장 오래된 것."""
    if user_id is None:
        return None
    try:
        target = _today_kst() - timedelta(days=days)
        pool = await get_db()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT total_value_usd
                FROM renaiss_portfolio_snapshots
                WHERE user_id = $1 AND snapshot_date <= $2
                ORDER BY snapshot_date DESC
                LIMIT 1
                """,
                user_id,
                target,
            )
            if row is None:
                row = await conn.fetchrow(
                    """
                    SELECT total_value_usd
                    FROM renaiss_portfolio_snapshots
                    WHERE user_id = $1 AND snapshot_date < $2
                    ORDER BY snapshot_date ASC
                    LIMIT 1
                    """,
                    user_id,
                    _today_kst(),
                )
        if row is None:
            return None
        return float(row["total_value_usd"] or 0)
    except Exception as exc:
        logger.debug("Renaiss portfolio history skipped user=%s: %s", user_id, exc)
        return None


# ── 데일리 가격 퀴즈 ─────────────────────────────────────────────


async def create_quiz_round(
    *,
    chat_id: int,
    category: str,
    card_name: str,
    local_card_id: str | None,
    card_image_url: str | None,
    correct_price_usd: float,
    options: list[float],
    correct_index: int,
    price_source: str | None,
    referral_url: str | None,
    closes_at: datetime,
) -> int | None:
    try:
        pool = await get_db()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                INSERT INTO renaiss_quiz_rounds (
                    chat_id, category, card_name, local_card_id, card_image_url,
                    correct_price_usd, options_json, correct_index,
                    price_source, referral_url, closes_at
                )
                VALUES ($1, $2, $3, $4, $5, $6, $7::jsonb, $8, $9, $10, $11)
                RETURNING id
                """,
                chat_id,
                category,
                card_name,
                local_card_id,
                card_image_url,
                correct_price_usd,
                json.dumps(options),
                correct_index,
                price_source,
                referral_url,
                closes_at,
            )
        return int(row["id"]) if row else None
    except Exception as exc:
        logger.warning("Renaiss quiz round create failed: %s", exc)
        return None


async def set_quiz_message(round_id: int, message_id: int) -> None:
    try:
        pool = await get_db()
        async with pool.acquire() as conn:
            await conn.execute(
                "UPDATE renaiss_quiz_rounds SET message_id = $2 WHERE id = $1",
                round_id,
                message_id,
            )
    except Exception as exc:
        logger.debug("Renaiss quiz message update skipped: %s", exc)


async def get_quiz_round(round_id: int) -> dict | None:
    try:
        pool = await get_db()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM renaiss_quiz_rounds WHERE id = $1",
                round_id,
            )
        if row is None:
            return None
        data = dict(row)
        raw_options = data.get("options_json")
        if isinstance(raw_options, str):
            data["options_json"] = json.loads(raw_options)
        return data
    except Exception as exc:
        logger.debug("Renaiss quiz round fetch skipped: %s", exc)
        return None


async def record_quiz_answer(
    *,
    round_id: int,
    user_id: int,
    display_name: str,
    choice_index: int,
    is_correct: bool,
) -> bool:
    """첫 답만 기록 (변경 불가). 새로 기록되면 True."""
    try:
        pool = await get_db()
        async with pool.acquire() as conn:
            result = await conn.execute(
                """
                INSERT INTO renaiss_quiz_answers (
                    round_id, user_id, display_name, choice_index, is_correct
                )
                VALUES ($1, $2, $3, $4, $5)
                ON CONFLICT (round_id, user_id) DO NOTHING
                """,
                round_id,
                user_id,
                display_name,
                choice_index,
                is_correct,
            )
        return result.endswith("1")
    except Exception as exc:
        logger.debug("Renaiss quiz answer skipped: %s", exc)
        return False


async def close_quiz_round(round_id: int) -> bool:
    """open -> revealed 전환. 이미 닫혀 있으면 False (중복 정산 방지)."""
    try:
        pool = await get_db()
        async with pool.acquire() as conn:
            result = await conn.execute(
                """
                UPDATE renaiss_quiz_rounds
                SET status = 'revealed', revealed_at = now()
                WHERE id = $1 AND status = 'open'
                """,
                round_id,
            )
        return result.endswith("1")
    except Exception as exc:
        logger.warning("Renaiss quiz close failed: %s", exc)
        return False


async def list_quiz_answers(round_id: int) -> list[dict]:
    try:
        pool = await get_db()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT user_id, display_name, choice_index, is_correct, answered_at
                FROM renaiss_quiz_answers
                WHERE round_id = $1
                ORDER BY answered_at ASC
                """,
                round_id,
            )
        return [dict(row) for row in rows]
    except Exception as exc:
        logger.debug("Renaiss quiz answers fetch skipped: %s", exc)
        return []


async def get_correct_answer_dates(user_ids: list[int], *, days: int = 60) -> dict[int, set]:
    """유저별 정답 날짜(KST) 집합 — 스트릭 계산용."""
    if not user_ids:
        return {}
    try:
        pool = await get_db()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT user_id, (answered_at AT TIME ZONE 'Asia/Seoul')::date AS answer_date
                FROM renaiss_quiz_answers
                WHERE user_id = ANY($1::bigint[])
                  AND is_correct
                  AND answered_at >= now() - ($2::int * interval '1 day')
                GROUP BY user_id, answer_date
                """,
                user_ids,
                days,
            )
        result: dict[int, set] = {}
        for row in rows:
            result.setdefault(int(row["user_id"]), set()).add(row["answer_date"])
        return result
    except Exception as exc:
        logger.debug("Renaiss quiz streak dates skipped: %s", exc)
        return {}


async def get_quiz_round_number(round_id: int, chat_id: int) -> int:
    """이 방 기준 몇 번째 퀴즈인지 (#N 표기용)."""
    try:
        pool = await get_db()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT COUNT(*)::int AS n
                FROM renaiss_quiz_rounds
                WHERE chat_id = $1 AND id <= $2
                """,
                chat_id,
                round_id,
            )
        return int(row["n"]) if row else 1
    except Exception as exc:
        logger.debug("Renaiss quiz round number skipped: %s", exc)
        return 1


async def get_flex_card(user_id: int | None) -> dict | None:
    """자랑용 최고가 카드 1장 (이미지 URL 포함)."""
    if user_id is None:
        return None
    pool = await get_db()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT category, local_card_id, card_name, grade, set_code,
                   collector_number, image_url, market_price_usd
            FROM renaiss_user_cards
            WHERE user_id = $1 AND is_tutorial IS NOT TRUE
            ORDER BY
                CASE COALESCE(grade, '')
                    WHEN 'MUR' THEN 8 WHEN 'UR' THEN 7 WHEN 'SAR' THEN 6
                    WHEN 'SR' THEN 5 WHEN 'AR' THEN 4 WHEN 'RR' THEN 3
                    WHEN 'R' THEN 2 ELSE 1
                END DESC,
                updated_at DESC
            LIMIT 1
            """,
            user_id,
        )
    return dict(row) if row else None


async def reserve_daily_flex(
    *,
    user_id: int,
    chat_id: int,
    reservation_token: str,
    card: dict,
) -> dict:
    """Atomically enforce both the per-user cooldown and room noise budget."""
    if user_id <= 0 or chat_id == 0 or not reservation_token or len(reservation_token) > 128:
        raise ValueError("invalid flex reservation")
    pool = await get_db()
    user_cooldown_seconds = _bounded_env_int(
        "RENAISS_FLEX_USER_COOLDOWN_SECONDS", 60, minimum=5, maximum=86400
    )
    room_daily_limit = _bounded_env_int(
        "RENAISS_FLEX_ROOM_DAILY_LIMIT", 60, minimum=1, maximum=500
    )
    room_cooldown_seconds = _bounded_env_int(
        "RENAISS_FLEX_ROOM_COOLDOWN_SECONDS", 60, minimum=10, maximum=3600
    )
    reservation_ttl_seconds = _bounded_env_int(
        "RENAISS_FLEX_RESERVATION_TTL_SECONDS", 300, minimum=60, maximum=1800
    )
    async with pool.acquire() as conn, conn.transaction():
        await conn.fetchval(
            """
            SELECT pg_advisory_xact_lock(
                hashtextextended('renaiss-flex-chat:' || ($1::bigint)::text, 0)
            )
            """,
            chat_id,
        )
        await conn.execute(
            """
            UPDATE renaiss_flex_daily_slots
            SET state = 'dead', last_error = 'reservation expired before delivery',
                updated_at = clock_timestamp()
            WHERE chat_id = $1 AND state = 'reserved'
              AND updated_at <= clock_timestamp() - ($2::int * interval '1 second')
            """,
            chat_id,
            reservation_ttl_seconds,
        )
        await conn.execute(
            """
            DELETE FROM renaiss_flex_daily_slots
            WHERE user_id = $1
              AND flex_date = (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Seoul')::date
              AND state = 'dead'
            """,
            user_id,
        )
        last_user_flex = await conn.fetchval(
            """
            SELECT GREATEST(
                (
                    SELECT MAX(COALESCE(sent_at, updated_at, created_at))
                    FROM renaiss_flex_daily_slots
                    WHERE user_id = $1 AND state <> 'dead'
                ),
                (SELECT MAX(flexed_at) FROM renaiss_flex_posts WHERE user_id = $1)
            )
            """,
            user_id,
        )
        if last_user_flex is not None:
            if last_user_flex.tzinfo is None:
                last_user_flex = last_user_flex.replace(tzinfo=timezone.utc)
            user_elapsed = (
                datetime.now(timezone.utc) - last_user_flex.astimezone(timezone.utc)
            ).total_seconds()
            user_retry_after = max(0, int(user_cooldown_seconds - user_elapsed + 0.999))
            if user_retry_after > 0:
                return {
                    "state": "user_cooldown",
                    "retry_after_seconds": user_retry_after,
                }

        room = await conn.fetchrow(
            """
            WITH slots AS (
                SELECT chat_id, message_id, created_at, sent_at
                FROM renaiss_flex_daily_slots
                WHERE chat_id = $1
                  AND flex_date = (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Seoul')::date
                  AND state IN ('reserved', 'sent', 'delivery_unknown')
            ), legacy_orphans AS (
                SELECT post.message_id, post.flexed_at
                FROM renaiss_flex_posts post
                WHERE post.chat_id = $1
                  AND (post.flexed_at AT TIME ZONE 'Asia/Seoul')::date =
                      (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Seoul')::date
                  AND NOT EXISTS (
                      SELECT 1 FROM slots
                      WHERE slots.chat_id = post.chat_id
                        AND slots.message_id = post.message_id
                  )
            )
            SELECT
                (SELECT COUNT(*) FROM slots) +
                    (SELECT COUNT(*) FROM legacy_orphans) AS total_posts,
                GREATEST(
                    (SELECT MAX(COALESCE(sent_at, created_at)) FROM slots),
                    (SELECT MAX(flexed_at) FROM legacy_orphans)
                ) AS latest_at
            """,
            chat_id,
        )
        total_posts = int(room["total_posts"] if room else 0)
        if total_posts >= room_daily_limit:
            return {"state": "room_limit", "room_daily_limit": room_daily_limit}
        latest_at = room["latest_at"] if room else None
        if latest_at is not None:
            if latest_at.tzinfo is None:
                latest_at = latest_at.replace(tzinfo=timezone.utc)
            elapsed = (
                datetime.now(timezone.utc) - latest_at.astimezone(timezone.utc)
            ).total_seconds()
            retry_after = max(0, int(room_cooldown_seconds - elapsed + 0.999))
            if retry_after > 0:
                return {
                    "state": "room_cooldown",
                    "retry_after_seconds": retry_after,
                }

        row = await conn.fetchrow(
            """
            INSERT INTO renaiss_flex_daily_slots (
                user_id, flex_date, reservation_token, chat_id, state,
                local_card_id, card_name, grade, market_price_usd
            )
            SELECT
                $1,
                (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Seoul')::date,
                $2,$3,'reserved',$4,$5,$6,$7
            RETURNING user_id, flex_date, reservation_token, state
            """,
            user_id,
            reservation_token,
            chat_id,
            card.get("local_card_id"),
            card.get("card_name"),
            card.get("grade"),
            card.get("market_price_usd"),
        )
    return dict(row) if row is not None else {"state": "error"}


async def complete_daily_flex(
    *,
    reservation_token: str,
    message_id: int,
) -> bool:
    """Acknowledge delivery and write the legacy flex history atomically."""
    if not reservation_token or message_id <= 0:
        raise ValueError("invalid flex completion")
    pool = await get_db()
    async with pool.acquire() as conn, conn.transaction():
        completed = await conn.fetchrow(
            """
            UPDATE renaiss_flex_daily_slots
            SET state = 'sent', message_id = $2, sent_at = clock_timestamp(),
                updated_at = clock_timestamp()
            WHERE reservation_token = $1 AND state = 'reserved'
            RETURNING user_id, chat_id, local_card_id, card_name, grade, market_price_usd
            """,
            reservation_token,
            message_id,
        )
        if completed is None:
            return False
        await conn.execute(
            """
            INSERT INTO renaiss_flex_posts (
                user_id, chat_id, message_id, local_card_id, card_name, grade,
                market_price_usd
            ) VALUES ($1,$2,$3,$4,$5,$6,$7)
            """,
            completed["user_id"],
            completed["chat_id"],
            message_id,
            completed["local_card_id"],
            completed["card_name"],
            completed["grade"],
            completed["market_price_usd"],
        )
    return True


async def mark_daily_flex_failed(
    *,
    reservation_token: str,
    state: str,
    error: str,
) -> bool:
    """Keep ambiguous sends blocked; only explicit Telegram failures become dead."""
    if state not in {"delivery_unknown", "dead"}:
        raise ValueError("invalid flex failure state")
    pool = await get_db()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            UPDATE renaiss_flex_daily_slots
            SET state = $2, last_error = $3, updated_at = clock_timestamp()
            WHERE reservation_token = $1 AND state = 'reserved'
            RETURNING user_id
            """,
            reservation_token,
            state,
            error[:500],
        )
    return row is not None


async def release_daily_flex_reservation(*, reservation_token: str) -> bool:
    """Release only a definitely-unsent reservation so the user can retry."""
    pool = await get_db()
    async with pool.acquire() as conn:
        result = await conn.execute(
            """
            DELETE FROM renaiss_flex_daily_slots
            WHERE reservation_token = $1 AND state = 'reserved'
            """,
            reservation_token,
        )
    return result == "DELETE 1"


async def flexed_today(user_id: int | None) -> bool:
    if user_id is None:
        return True
    try:
        pool = await get_db()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT 1 FROM renaiss_flex_posts
                WHERE user_id = $1
                  AND (flexed_at AT TIME ZONE 'Asia/Seoul')::date = $2
                LIMIT 1
                """,
                user_id,
                _today_kst(),
            )
        return row is not None
    except Exception as exc:
        logger.debug("Renaiss flexed_today check skipped: %s", exc)
        return False


async def record_flex(
    *,
    user_id: int,
    chat_id: int,
    message_id: int | None,
    card: dict,
) -> None:
    try:
        pool = await get_db()
        async with pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO renaiss_flex_posts (
                    user_id, chat_id, message_id, local_card_id, card_name, grade, market_price_usd
                )
                VALUES ($1, $2, $3, $4, $5, $6, $7)
                """,
                user_id,
                chat_id,
                message_id,
                card.get("local_card_id"),
                card.get("card_name"),
                card.get("grade"),
                card.get("market_price_usd"),
            )
    except Exception as exc:
        logger.debug("Renaiss flex record skipped: %s", exc)


async def add_flex_prop(*, chat_id: int, message_id: int, tapper_user_id: int) -> int | None:
    """props 기록. 신규면 현재까지 총 props 수 반환, 이미 눌렀으면 None."""
    pool = await get_db()
    async with pool.acquire() as conn:
        inserted = await conn.execute(
            """
            INSERT INTO renaiss_flex_props (chat_id, message_id, tapper_user_id)
            VALUES ($1, $2, $3)
            ON CONFLICT (chat_id, message_id, tapper_user_id) DO NOTHING
            """,
            chat_id,
            message_id,
            tapper_user_id,
        )
        if not inserted.endswith("1"):
            return None
        row = await conn.fetchrow(
            "SELECT COUNT(*)::int AS n FROM renaiss_flex_props WHERE chat_id = $1 AND message_id = $2",
            chat_id,
            message_id,
        )
    return int(row["n"]) if row else 1


async def list_open_quiz_rounds() -> list[dict]:
    """재시작 복구용: 아직 정산 안 된 라운드 목록."""
    try:
        pool = await get_db()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT id, chat_id, closes_at FROM renaiss_quiz_rounds WHERE status = 'open'"
            )
        return [dict(row) for row in rows]
    except Exception as exc:
        logger.debug("Renaiss open quiz rounds fetch skipped: %s", exc)
        return []


async def weekly_quiz_leaderboard(*, limit: int = 5) -> list[dict]:
    """이번 주(월~일 KST) 정답 수 순위."""
    try:
        today = _today_kst()
        week_start = today - timedelta(days=today.weekday())
        pool = await get_db()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT
                    user_id,
                    MAX(display_name) AS display_name,
                    COUNT(*) FILTER (WHERE is_correct)::int AS correct_count,
                    COUNT(*)::int AS answer_count
                FROM renaiss_quiz_answers
                WHERE (answered_at AT TIME ZONE 'Asia/Seoul')::date >= $1
                GROUP BY user_id
                HAVING COUNT(*) FILTER (WHERE is_correct) > 0
                ORDER BY correct_count DESC, MIN(answered_at) ASC
                LIMIT $2
                """,
                week_start,
                limit,
            )
        return [dict(row) for row in rows]
    except Exception as exc:
        logger.debug("Renaiss quiz leaderboard skipped: %s", exc)
        return []


async def get_collection_summary(user_id: int | None) -> dict[str, int] | None:
    if user_id is None:
        return None
    try:
        pool = await get_db()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT
                    COUNT(*)::int AS unique_cards,
                    COALESCE(SUM(quantity), 0)::int AS total_quantity,
                    COUNT(DISTINCT category)::int AS categories
                FROM renaiss_user_cards
                WHERE user_id = $1 AND is_tutorial IS NOT TRUE
                """,
                user_id,
            )
        if not row or int(row["unique_cards"] or 0) <= 0:
            return None
        return {
            "unique_cards": int(row["unique_cards"] or 0),
            "total_quantity": int(row["total_quantity"] or 0),
            "categories": int(row["categories"] or 0),
        }
    except Exception as exc:
        logger.debug("Renaiss collection summary skipped: %s", exc)
        return None


async def get_collection_detail(user_id: int | None, *, limit: int = 5) -> dict | None:
    if user_id is None:
        return None
    try:
        pool = await get_db()
        async with pool.acquire() as conn:
            summary = await conn.fetchrow(
                """
            SELECT
                    COUNT(*) FILTER (WHERE is_tutorial IS NOT TRUE)::int AS unique_cards,
                    COALESCE(SUM(quantity) FILTER (
                        WHERE is_tutorial IS NOT TRUE
                    ), 0)::int AS total_quantity,
                    COUNT(DISTINCT category) FILTER (
                        WHERE is_tutorial IS NOT TRUE
                    )::int AS categories,
                    COALESCE(MAX(market_price_usd) FILTER (
                        WHERE is_tutorial IS NOT TRUE
                    ), 0)::numeric AS max_market_price,
                    COUNT(*)::int AS all_unique_cards
                FROM renaiss_user_cards
                WHERE user_id = $1
                """,
                user_id,
            )
            if not summary or int(summary["all_unique_cards"] or 0) <= 0:
                return None
            grade_rows = await conn.fetch(
                """
                SELECT COALESCE(grade, '-') AS grade, COALESCE(SUM(quantity), 0)::int AS count
                FROM renaiss_user_cards
                WHERE user_id = $1 AND is_tutorial IS NOT TRUE
                GROUP BY grade
                ORDER BY
                    CASE COALESCE(grade, '-')
                        WHEN 'MUR' THEN 1
                        WHEN 'UR' THEN 2
                        WHEN 'SAR' THEN 3
                        WHEN 'SR' THEN 4
                        WHEN 'AR' THEN 5
                        WHEN 'RR' THEN 6
                        WHEN 'R' THEN 7
                        ELSE 8
                    END,
                    grade ASC
                """,
                user_id,
            )
            top_rows = await conn.fetch(
                """
                SELECT card_name, grade, quantity, market_price_usd
                FROM renaiss_user_cards
                WHERE user_id = $1 AND is_tutorial IS NOT TRUE
                ORDER BY market_price_usd DESC NULLS LAST, quantity DESC, updated_at DESC
                LIMIT $2
                """,
                user_id,
                limit,
            )
            recent_rows = await conn.fetch(
                """
                SELECT card_name, grade, quantity, updated_at, is_tutorial
                FROM renaiss_user_cards
                WHERE user_id = $1
                ORDER BY updated_at DESC
                LIMIT $2
                """,
                user_id,
                limit,
            )
        return {
            "unique_cards": int(summary["unique_cards"] or 0),
            "total_quantity": int(summary["total_quantity"] or 0),
            "categories": int(summary["categories"] or 0),
            "max_market_price": float(summary["max_market_price"] or 0),
            "grades": [dict(row) for row in grade_rows],
            "top_cards": [dict(row) for row in top_rows],
            "recent_cards": [dict(row) for row in recent_rows],
        }
    except Exception as exc:
        logger.debug("Renaiss collection detail skipped: %s", exc)
        return None


async def get_catch_ranking(*, period: str, limit: int = 10) -> dict:
    """KST-boundary catch_won aggregates for the group ranking announcements.

    ``period`` is ``"day"`` (since KST midnight) or ``"week"`` (since KST
    Monday).  Counts public catch wins only — never asset totals — so the
    announcement stays aligned with the leaderboard philosophy.
    """
    if period not in {"day", "week"}:
        raise ValueError("period must be 'day' or 'week'")
    limit = max(1, min(20, int(limit)))
    pool = await get_db()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            WITH wins AS (
                SELECT user_id, metadata, created_at
                FROM renaiss_events
                WHERE event_name = 'catch_won'
                  AND user_id IS NOT NULL
                  AND created_at >= (
                      date_trunc($1, now() AT TIME ZONE 'Asia/Seoul')
                      AT TIME ZONE 'Asia/Seoul'
                  )
            ), per_user AS (
                SELECT
                    user_id,
                    COUNT(*)::int AS catches,
                    MAX(COALESCE((metadata->>'fmv_usd')::numeric, 0))::numeric AS best_fmv
                FROM wins
                GROUP BY user_id
            )
            SELECT
                p.user_id,
                p.catches,
                p.best_fmv,
                (
                    SELECT w.metadata->>'winner_name'
                    FROM wins w
                    WHERE w.user_id = p.user_id
                    ORDER BY w.created_at DESC
                    LIMIT 1
                ) AS winner_name,
                DENSE_RANK() OVER (ORDER BY p.catches DESC)::int AS rank
            FROM per_user p
            ORDER BY rank ASC, p.user_id ASC
            LIMIT $2
            """,
            period,
            limit,
        )
        best = await conn.fetchrow(
            """
            SELECT
                metadata->>'card_name' AS card_name,
                metadata->>'winner_name' AS winner_name,
                COALESCE((metadata->>'fmv_usd')::numeric, 0)::numeric AS fmv_usd
            FROM renaiss_events
            WHERE event_name = 'catch_won'
              AND user_id IS NOT NULL
              AND created_at >= (
                  date_trunc($1, now() AT TIME ZONE 'Asia/Seoul')
                  AT TIME ZONE 'Asia/Seoul'
              )
            ORDER BY COALESCE((metadata->>'fmv_usd')::numeric, 0) DESC, created_at ASC
            LIMIT 1
            """,
            period,
        )
    ranking_rows = [
        {
            "rank": int(row["rank"] or 0),
            "user_id": int(row["user_id"]),
            "winner_name": str(row["winner_name"] or "Collector"),
            "catches": int(row["catches"] or 0),
            "best_fmv": float(row["best_fmv"] or 0),
        }
        for row in rows
    ]
    best_catch = None
    if best is not None and float(best["fmv_usd"] or 0) > 0:
        best_catch = {
            "card_name": str(best["card_name"] or "-"),
            "winner_name": str(best["winner_name"] or "Collector"),
            "fmv_usd": float(best["fmv_usd"] or 0),
        }
    return {
        "period": period,
        "rows": ranking_rows,
        "best_catch": best_catch,
        "total_catches": sum(row["catches"] for row in ranking_rows),
    }
