"""Private collection summaries for the standalone Renaiss collector game."""

from __future__ import annotations

from dataclasses import dataclass

from renaiss_bot.database.connection import get_db


@dataclass(frozen=True)
class Achievement:
    key: str
    title: str
    description: str
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

    @property
    def unlocked_achievements(self) -> list[Achievement]:
        return [achievement for achievement in self.achievements if achievement.unlocked]


def _build_achievements(stats: dict) -> list[Achievement]:
    unique_cards = int(stats.get("unique_cards") or 0)
    total_cards = int(stats.get("total_cards") or 0)
    categories = int(stats.get("categories") or 0)
    sets = int(stats.get("sets") or 0)

    specs = [
        ("first_pull", "First Pull", "Collect your first Renaiss card.", total_cards >= 1),
        ("unique_10", "Collector I", "Collect 10 unique cards.", unique_cards >= 10),
        ("unique_50", "Collector II", "Collect 50 unique cards.", unique_cards >= 50),
        ("unique_100", "Collector III", "Collect 100 unique cards.", unique_cards >= 100),
        ("multi_category", "Cross-Market Collector", "Collect cards from at least 2 categories.", categories >= 2),
        ("set_hunter", "Set Hunter", "Collect cards from at least 5 sets.", sets >= 5),
    ]
    return [
        Achievement(key=key, title=title, description=description, unlocked=unlocked)
        for key, title, description, unlocked in specs
    ]


async def get_portfolio_stats(user_id: int | None, *, limit: int = 5) -> PortfolioStats | None:
    if user_id is None:
        return None

    pool = await get_db()
    async with pool.acquire() as conn:
        summary = await conn.fetchrow(
            """
            SELECT
                COUNT(*) FILTER (WHERE is_tutorial IS NOT TRUE)::int AS unique_cards,
                COALESCE(SUM(quantity) FILTER (WHERE is_tutorial IS NOT TRUE), 0)::int AS total_cards,
                COUNT(DISTINCT category) FILTER (WHERE is_tutorial IS NOT TRUE)::int AS categories,
                COUNT(DISTINCT NULLIF(set_code, '')) FILTER (
                    WHERE is_tutorial IS NOT TRUE
                )::int AS sets,
                COUNT(*) FILTER (
                    WHERE is_tutorial IS NOT TRUE
                      AND market_price_usd IS NOT NULL
                      AND market_price_usd > 0
                )::int AS priced_cards,
                COUNT(*) FILTER (
                    WHERE is_tutorial IS NOT TRUE
                      AND (market_price_usd IS NULL OR market_price_usd <= 0)
                )::int AS unpriced_cards,
                COALESCE(SUM(quantity * COALESCE(market_price_usd, 0)) FILTER (
                    WHERE is_tutorial IS NOT TRUE
                ), 0)::numeric AS total_value_usd,
                COALESCE(MAX(market_price_usd) FILTER (
                    WHERE is_tutorial IS NOT TRUE
                ), 0)::numeric AS max_card_value_usd,
                COUNT(*)::int AS all_unique_cards
            FROM renaiss_user_cards
            WHERE user_id = $1
            """,
            user_id,
        )
        if not summary or int(summary["all_unique_cards"] or 0) <= 0:
            return None

        top_rows = await conn.fetch(
            """
            SELECT card_name, category, grade, quantity, set_code, collector_number, market_price_usd
            FROM renaiss_user_cards
            WHERE user_id = $1 AND is_tutorial IS NOT TRUE
            ORDER BY
                CASE COALESCE(grade, '')
                    WHEN 'MUR' THEN 8 WHEN 'UR' THEN 7 WHEN 'SAR' THEN 6
                    WHEN 'SR' THEN 5 WHEN 'AR' THEN 4 WHEN 'RR' THEN 3
                    WHEN 'R' THEN 2 ELSE 1
                END DESC,
                updated_at DESC
            LIMIT $2
            """,
            user_id,
            limit,
        )
        recent_rows = await conn.fetch(
            """
            SELECT card_name, category, grade, quantity, set_code, updated_at, is_tutorial
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
            WHERE user_id = $1 AND is_tutorial IS NOT TRUE
            GROUP BY grade
            ORDER BY count DESC, grade ASC
            LIMIT 8
            """,
            user_id,
        )
        category_rows = await conn.fetch(
            """
            SELECT category, COALESCE(SUM(quantity), 0)::int AS count
            FROM renaiss_user_cards
            WHERE user_id = $1 AND is_tutorial IS NOT TRUE
            GROUP BY category
            ORDER BY count DESC, category
            """,
            user_id,
        )

    stats_dict = dict(summary)
    achievements = _build_achievements(stats_dict)
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
    )
