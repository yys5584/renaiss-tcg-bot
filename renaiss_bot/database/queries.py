"""Query helpers for standalone Renaiss bot KPI logging."""

from __future__ import annotations

import json
import logging
import os
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from renaiss_bot.database.connection import get_db
from renaiss_bot.services.models import CardIdentity, PackOpenResult, RenaissPrice

logger = logging.getLogger(__name__)

KST = ZoneInfo("Asia/Seoul")


def _today_kst() -> date:
    return datetime.now(KST).date()


def _snapshot_ttl_minutes() -> int:
    try:
        return max(0, int(os.getenv("RENAISS_PRICE_CACHE_TTL_MINUTES", "10")))
    except ValueError:
        return 10


def _local_card_id(card: CardIdentity) -> str:
    return card.local_card_id or f"{card.category}:{card.card_name}:{card.grade}:{card.set_code}:{card.collector_number}"


def _dt(value) -> datetime:
    if isinstance(value, datetime):
        return value
    return datetime.now(timezone.utc)


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
                    price_updated_at
                )
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, $16)
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
            fmv_usd=float(row["fmv_usd"]) if row["fmv_usd"] is not None else None,
            change_7d_pct=float(row["change_7d_pct"]) if row["change_7d_pct"] is not None else None,
            market_status=row["market_status"] or "cached",
            price_updated_at=_dt(row["price_updated_at"]),
        )
    except Exception as exc:
        logger.debug("Renaiss recent price snapshot skipped: %s", exc)
        return None


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
                    pool_source, best_local_card_id, match_status, fmv_usd, source
                )
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)
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
            )
    except Exception as exc:
        logger.debug("Renaiss pack event log skipped: %s", exc)


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
                        card.market_price_usd,
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
                    updated_at = now()
                """,
                rows,
            )
    except Exception as exc:
        logger.debug("Renaiss user card register skipped chat=%s: %s", chat_id, exc)


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
                    COALESCE(SUM(quantity * COALESCE(market_price_usd, 0)), 0),
                    COUNT(*)::int,
                    COALESCE(SUM(quantity), 0)::int
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
                    COALESCE(SUM(quantity * COALESCE(market_price_usd, 0)), 0),
                    COUNT(*)::int,
                    COALESCE(SUM(quantity), 0)::int
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
    try:
        pool = await get_db()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT category, local_card_id, card_name, grade, set_code,
                       collector_number, image_url, market_price_usd
                FROM renaiss_user_cards
                WHERE user_id = $1
                ORDER BY market_price_usd DESC NULLS LAST, updated_at DESC
                LIMIT 1
                """,
                user_id,
            )
        return dict(row) if row else None
    except Exception as exc:
        logger.debug("Renaiss flex card fetch skipped: %s", exc)
        return None


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
    try:
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
    except Exception as exc:
        logger.debug("Renaiss flex prop skipped: %s", exc)
        return None


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
                WHERE user_id = $1
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
                    COUNT(*)::int AS unique_cards,
                    COALESCE(SUM(quantity), 0)::int AS total_quantity,
                    COUNT(DISTINCT category)::int AS categories,
                    COALESCE(MAX(market_price_usd), 0)::numeric AS max_market_price
                FROM renaiss_user_cards
                WHERE user_id = $1
                """,
                user_id,
            )
            if not summary or int(summary["unique_cards"] or 0) <= 0:
                return None
            grade_rows = await conn.fetch(
                """
                SELECT COALESCE(grade, '-') AS grade, COALESCE(SUM(quantity), 0)::int AS count
                FROM renaiss_user_cards
                WHERE user_id = $1
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
                WHERE user_id = $1
                ORDER BY market_price_usd DESC NULLS LAST, quantity DESC, updated_at DESC
                LIMIT $2
                """,
                user_id,
                limit,
            )
            recent_rows = await conn.fetch(
                """
                SELECT card_name, grade, quantity, updated_at
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
