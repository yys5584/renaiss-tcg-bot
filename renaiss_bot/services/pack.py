"""Pack opening facade."""

from __future__ import annotations

from renaiss_bot.services.card_pool import best_card, build_pack, load_card_pool
from renaiss_bot.services.categories import get_category
from renaiss_bot.services.models import PackOpenResult
from renaiss_bot.services.pack_rules import clamp_pack_count, normalize_pack_type
from renaiss_bot.services.pricing import fetch_price


async def open_pack(
    user_id: int | None,
    category_key: str = "pokemon_tcg",
    *,
    pack_type: str = "free",
    count: int = 1,
) -> PackOpenResult:
    category = get_category(category_key)
    if not category.enabled:
        raise ValueError(f"card category is not enabled: {category.key}")
    normalized_pack_type = normalize_pack_type(pack_type)
    pack_count = clamp_pack_count(count)
    pool, pool_source = await load_card_pool(user_id, category.key)

    all_cards = []
    lucky_grades = []
    for _ in range(pack_count):
        cards, lucky_grade = build_pack(pool, category.key, normalized_pack_type)
        all_cards.extend(cards)
        lucky_grades.append(lucky_grade)

    best = best_card(all_cards)
    best_price = await fetch_price(best)
    return PackOpenResult(
        category=category.key,
        cards=all_cards,
        best_card=best,
        best_price=best_price,
        pack_type=normalized_pack_type,
        pack_count=pack_count,
        pool_source=pool_source,
        lucky_grades=lucky_grades,
    )


async def open_demo_pack(user_id: int | None, category_key: str = "pokemon_tcg") -> PackOpenResult:
    return await open_pack(user_id, category_key, pack_type="free", count=1)
