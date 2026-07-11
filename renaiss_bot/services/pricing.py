"""Pricing and fallback decisions."""

from __future__ import annotations

import asyncio
import os

from renaiss_bot.services.client import fetch_official_price
from renaiss_bot.services.models import CardIdentity, RenaissPrice
from renaiss_bot.services.referral import build_search_url


def _cache_timeout_seconds() -> float:
    try:
        return min(
            3.0,
            max(0.05, float(os.getenv("RENAISS_PRICE_CACHE_TIMEOUT_SECONDS", "0.5"))),
        )
    except ValueError:
        return 0.5


async def _store_snapshot(card: CardIdentity, price: RenaissPrice) -> None:
    try:
        from renaiss_bot.database.queries import log_price_snapshot

        await asyncio.wait_for(
            log_price_snapshot(card=card, price=price),
            timeout=_cache_timeout_seconds(),
        )
    except Exception:
        pass


async def _recent_snapshot(card: CardIdentity) -> RenaissPrice | None:
    try:
        from renaiss_bot.database.queries import get_recent_price_snapshot

        return await asyncio.wait_for(
            get_recent_price_snapshot(card),
            timeout=_cache_timeout_seconds(),
        )
    except Exception:
        return None


async def fetch_price(card: CardIdentity, *, timeout_seconds: float = 2.5) -> RenaissPrice:
    cached = await _recent_snapshot(card)
    # Old pilot builds persisted hard-coded demo prices. Never replay them.
    if cached is not None and not cached.source.startswith("demo"):
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

    price = RenaissPrice(
        status="search_only",
        source="renaiss-search",
        referral_url=build_search_url(card.card_name, card.category),
        market_status="search",
    )
    await _store_snapshot(card, price)
    return price
