"""Pricing and fallback decisions."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from renaiss_bot.services.client import fetch_official_price
from renaiss_bot.services.models import CardIdentity, RenaissPrice
from renaiss_bot.services.referral import add_referral, build_search_url, renaiss_base_url

_DEMO_PRICES = {
    "charizard": (430.0, 12.4, "exact"),
    "charizard ex": (430.0, 12.4, "exact"),
    "리자몽": (430.0, 12.4, "exact"),
    "pikachu": (128.0, 7.1, "exact"),
    "피카츄": (128.0, 7.1, "exact"),
    "monkey.d.luffy": (390.0, 5.8, "candidate"),
    "luffy": (390.0, 5.8, "candidate"),
}


def _demo_asset_url(card: CardIdentity) -> str:
    return f"{renaiss_base_url()}/?search={card.card_name.replace(' ', '+')}"


async def _store_snapshot(card: CardIdentity, price: RenaissPrice) -> None:
    try:
        from renaiss_bot.database.queries import log_price_snapshot

        await log_price_snapshot(card=card, price=price)
    except Exception:
        pass


async def _recent_snapshot(card: CardIdentity) -> RenaissPrice | None:
    try:
        from renaiss_bot.database.queries import get_recent_price_snapshot

        return await get_recent_price_snapshot(card)
    except Exception:
        return None


async def fetch_price(card: CardIdentity, *, timeout_seconds: float = 2.5) -> RenaissPrice:
    cached = await _recent_snapshot(card)
    if cached is not None:
        return cached

    try:
        official = await asyncio.wait_for(
            fetch_official_price(card, timeout_seconds=timeout_seconds),
            timeout=timeout_seconds + 0.5,
        )
        if official is not None:
            await _store_snapshot(card, official)
            return official
    except Exception:
        price = RenaissPrice(
            status="api_error",
            source="renaiss-index-api",
            referral_url=build_search_url(card.card_name, card.category),
            market_status="api_error",
        )
        await _store_snapshot(card, price)
        return price

    key = card.card_name.strip().lower()
    demo = _DEMO_PRICES.get(key)
    if demo:
        fmv, change_7d, status = demo
        asset_url = _demo_asset_url(card)
        price = RenaissPrice(
            status=status,  # type: ignore[arg-type]
            source="demo",
            asset_url=asset_url,
            referral_url=add_referral(asset_url),
            fmv_usd=fmv,
            change_7d_pct=change_7d,
            market_status="Market Live" if status == "exact" else "candidate",
            price_updated_at=datetime.now(timezone.utc),
            price_range_min_usd=fmv * 0.9 if status == "candidate" else None,
            price_range_max_usd=fmv * 1.1 if status == "candidate" else None,
        )
        await _store_snapshot(card, price)
        return price

    price = RenaissPrice(
        status="search_only",
        source="renaiss-search",
        referral_url=build_search_url(card.card_name, card.category),
        market_status="search",
    )
    await _store_snapshot(card, price)
    return price
