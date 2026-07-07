"""Portfolio scoring for the standalone Renaiss collector game."""

from __future__ import annotations

import asyncio
import math
from dataclasses import dataclass

from renaiss_bot.database.connection import get_db


@dataclass(frozen=True)
class Achievement:
    key: str
    title: str
    description: str
    points: int
    unlocked: bool


@dataclass(frozen=True)
class PortfolioStats:
    user_id: int
    total_value_usd: float
    unique_cards: int
    total_cards: int
    categories: int
    sets: int
    priced_cards: int
    unpriced_cards: int
    max_card_value_usd: float
    top_cards: list[dict]
    recent_cards: list[dict]
    grade_counts: list[dict]
    category_counts: list[dict]
    achievements: list[Achievement]
    value_score: int
    diversity_score: int
    achievement_score: int
    renaiss_score: int
    value_7d_ago_usd: float | None = None

    @property
    def change_7d_usd(self) -> float | None:
        if self.value_7d_ago_usd is None:
            return None
        return self.total_value_usd - self.value_7d_ago_usd

    @property
    def change_7d_pct(self) -> float | None:
        if self.value_7d_ago_usd is None or self.value_7d_ago_usd <= 0:
            return None
        return (self.total_value_usd - self.value_7d_ago_usd) / self.value_7d_ago_usd * 100

    @property
    def unlocked_achievements(self) -> list[Achievement]:
        return [achievement for achievement in self.achievements if achievement.unlocked]


def _value_score(total_value_usd: float) -> int:
    if total_value_usd <= 0:
        return 0
    return min(1000, int(math.log10(total_value_usd + 1) * 250))


def _diversity_score(unique_cards: int, categories: int, sets: int) -> int:
    card_score = min(500, unique_cards * 5)
    category_score = min(200, max(0, categories - 1) * 100)
    set_score = min(300, sets * 12)
    return card_score + category_score + set_score


def _build_achievements(stats: dict) -> list[Achievement]:
    total_value = float(stats.get("total_value_usd") or 0)
    unique_cards = int(stats.get("unique_cards") or 0)
    total_cards = int(stats.get("total_cards") or 0)
    categories = int(stats.get("categories") or 0)
    sets = int(stats.get("sets") or 0)
    max_value = float(stats.get("max_card_value_usd") or 0)

    specs = [
        ("first_pull", "First Pull", "Open your first Renaiss pack.", 25, total_cards >= 1),
        ("value_100", "$100 Portfolio", "Reach $100 total collection Market Value.", 50, total_value >= 100),
        ("value_1000", "$1K Portfolio", "Reach $1,000 total collection Market Value.", 100, total_value >= 1000),
        ("value_5000", "$5K Portfolio", "Reach $5,000 total collection Market Value.", 200, total_value >= 5000),
        ("value_10000", "$10K Portfolio", "Reach $10,000 total collection Market Value.", 350, total_value >= 10000),
        ("unique_10", "Collector I", "Collect 10 unique cards.", 50, unique_cards >= 10),
        ("unique_50", "Collector II", "Collect 50 unique cards.", 150, unique_cards >= 50),
        ("unique_100", "Collector III", "Collect 100 unique cards.", 300, unique_cards >= 100),
        ("multi_category", "Cross-Market Collector", "Collect cards from at least 2 categories.", 150, categories >= 2),
        ("set_hunter", "Set Hunter", "Collect cards from at least 5 sets.", 100, sets >= 5),
        ("premium_hit", "Premium Hit", "Own a card worth at least $100 Market Value.", 100, max_value >= 100),
        ("grail_hit", "Grail Hit", "Own a card worth at least $500 Market Value.", 250, max_value >= 500),
    ]
    return [
        Achievement(key=key, title=title, description=description, points=points, unlocked=unlocked)
        for key, title, description, points, unlocked in specs
    ]


def _achievement_score(achievements: list[Achievement]) -> int:
    return sum(achievement.points for achievement in achievements if achievement.unlocked)


def _renaiss_score(value_score: int, diversity_score: int, achievement_score: int) -> int:
    return int((value_score * 0.60) + (diversity_score * 0.25) + (achievement_score * 0.15))


async def get_portfolio_stats(user_id: int | None, *, limit: int = 5) -> PortfolioStats | None:
    if user_id is None:
        return None

    pool = await get_db()
    async with pool.acquire() as conn:
        summary = await conn.fetchrow(
            """
            SELECT
                COUNT(*)::int AS unique_cards,
                COALESCE(SUM(quantity), 0)::int AS total_cards,
                COUNT(DISTINCT category)::int AS categories,
                COUNT(DISTINCT NULLIF(set_code, ''))::int AS sets,
                COUNT(*) FILTER (WHERE market_price_usd IS NOT NULL AND market_price_usd > 0)::int AS priced_cards,
                COUNT(*) FILTER (WHERE market_price_usd IS NULL OR market_price_usd <= 0)::int AS unpriced_cards,
                COALESCE(SUM(quantity * COALESCE(market_price_usd, 0)), 0)::numeric AS total_value_usd,
                COALESCE(MAX(market_price_usd), 0)::numeric AS max_card_value_usd
            FROM renaiss_user_cards
            WHERE user_id = $1
            """,
            user_id,
        )
        if not summary or int(summary["unique_cards"] or 0) <= 0:
            return None

        top_rows = await conn.fetch(
            """
            SELECT card_name, category, grade, quantity, set_code, collector_number, market_price_usd
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
            SELECT card_name, category, grade, quantity, set_code, updated_at
            FROM renaiss_user_cards
            WHERE user_id = $1
            ORDER BY updated_at DESC
            LIMIT $2
            """,
            user_id,
            limit,
        )
        grade_rows = await conn.fetch(
            """
            SELECT COALESCE(grade, '-') AS grade, COALESCE(SUM(quantity), 0)::int AS count
            FROM renaiss_user_cards
            WHERE user_id = $1
            GROUP BY grade
            ORDER BY count DESC, grade ASC
            LIMIT 8
            """,
            user_id,
        )
        category_rows = await conn.fetch(
            """
            SELECT category, COALESCE(SUM(quantity), 0)::int AS count,
                   COALESCE(SUM(quantity * COALESCE(market_price_usd, 0)), 0)::numeric AS value_usd
            FROM renaiss_user_cards
            WHERE user_id = $1
            GROUP BY category
            ORDER BY value_usd DESC, count DESC
            """,
            user_id,
        )

    from renaiss_bot.database.queries import get_portfolio_value_days_ago, upsert_portfolio_snapshot

    # 조회 시마다 오늘 스냅샷을 갱신해서, 일일 잡이 못 돌아도 이력이 쌓이게 한다.
    # 오늘 upsert 와 과거 조회는 서로 다른 날짜 행이라 병렬로 돌려도 안전.
    _, value_7d_ago = await asyncio.gather(
        upsert_portfolio_snapshot(user_id),
        get_portfolio_value_days_ago(user_id, days=7),
    )

    stats_dict = dict(summary)
    achievements = _build_achievements(stats_dict)
    value_score = _value_score(float(summary["total_value_usd"] or 0))
    diversity_score = _diversity_score(
        int(summary["unique_cards"] or 0),
        int(summary["categories"] or 0),
        int(summary["sets"] or 0),
    )
    achievement_score = _achievement_score(achievements)
    return PortfolioStats(
        user_id=user_id,
        total_value_usd=float(summary["total_value_usd"] or 0),
        unique_cards=int(summary["unique_cards"] or 0),
        total_cards=int(summary["total_cards"] or 0),
        categories=int(summary["categories"] or 0),
        sets=int(summary["sets"] or 0),
        priced_cards=int(summary["priced_cards"] or 0),
        unpriced_cards=int(summary["unpriced_cards"] or 0),
        max_card_value_usd=float(summary["max_card_value_usd"] or 0),
        top_cards=[dict(row) for row in top_rows],
        recent_cards=[dict(row) for row in recent_rows],
        grade_counts=[dict(row) for row in grade_rows],
        category_counts=[dict(row) for row in category_rows],
        achievements=achievements,
        value_score=value_score,
        diversity_score=diversity_score,
        achievement_score=achievement_score,
        renaiss_score=_renaiss_score(value_score, diversity_score, achievement_score),
        value_7d_ago_usd=value_7d_ago,
    )


async def get_portfolio_rankings(*, limit: int = 10) -> list[dict]:
    pool = await get_db()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            WITH summary AS (
                SELECT
                    user_id,
                    COUNT(*)::int AS unique_cards,
                    COALESCE(SUM(quantity), 0)::int AS total_cards,
                    COUNT(DISTINCT category)::int AS categories,
                    COUNT(DISTINCT NULLIF(set_code, ''))::int AS sets,
                    COALESCE(SUM(quantity * COALESCE(market_price_usd, 0)), 0)::numeric AS total_value_usd,
                    COALESCE(MAX(market_price_usd), 0)::numeric AS max_card_value_usd
                FROM renaiss_user_cards
                GROUP BY user_id
            )
            SELECT *
            FROM summary
            ORDER BY total_value_usd DESC, unique_cards DESC, total_cards DESC
            LIMIT $1
            """,
            limit,
        )
    ranked = []
    for row in rows:
        stats = dict(row)
        achievements = _build_achievements(stats)
        value_score = _value_score(float(stats["total_value_usd"] or 0))
        diversity_score = _diversity_score(
            int(stats["unique_cards"] or 0),
            int(stats["categories"] or 0),
            int(stats["sets"] or 0),
        )
        achievement_score = _achievement_score(achievements)
        stats["renaiss_score"] = _renaiss_score(value_score, diversity_score, achievement_score)
        stats["achievement_count"] = sum(1 for achievement in achievements if achievement.unlocked)
        ranked.append(stats)
    return ranked
