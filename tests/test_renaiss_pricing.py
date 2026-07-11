"""Public price lookup must never invent a fallback market value."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock

import asyncio

from renaiss_bot.renderers.overlay import _price_text
from renaiss_bot.services.models import CardIdentity, RenaissPrice, card_identity_key
from renaiss_bot.services.pricing import fetch_price


async def test_known_demo_name_falls_back_to_search_without_fake_fmv(monkeypatch):
    monkeypatch.setattr(
        "renaiss_bot.services.pricing._recent_snapshot",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(
        "renaiss_bot.services.pricing.fetch_official_price",
        AsyncMock(return_value=None),
    )
    store = AsyncMock()
    monkeypatch.setattr("renaiss_bot.services.pricing._store_snapshot", store)

    price = await fetch_price(
        CardIdentity(category="pokemon_tcg", card_name="Charizard")
    )

    assert price.status == "search_only"
    assert price.source == "renaiss-search"
    assert price.fmv_usd is None
    store.assert_awaited_once()


async def test_legacy_demo_cache_is_ignored(monkeypatch):
    monkeypatch.setattr(
        "renaiss_bot.services.pricing._recent_snapshot",
        AsyncMock(
            return_value=RenaissPrice(
                status="exact",
                source="demo-cache",
                fmv_usd=430,
            )
        ),
    )
    monkeypatch.setattr(
        "renaiss_bot.services.pricing.fetch_official_price",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(
        "renaiss_bot.services.pricing._store_snapshot",
        AsyncMock(),
    )

    price = await fetch_price(
        CardIdentity(category="pokemon_tcg", card_name="Charizard")
    )

    assert price.status == "search_only"
    assert price.fmv_usd is None


def test_branded_overlay_hides_unverified_collection_values():
    card = CardIdentity(
        category="pokemon_tcg",
        card_name="Charizard",
        set_name="Base Set",
        collector_number="4/102",
    )
    candidate = RenaissPrice(status="candidate", source="collection", fmv_usd=430)
    exact = RenaissPrice(
        status="exact",
        source="renaiss-index-api:item-by-no",
        fmv_usd=430,
        confidence="high",
        confidence_score=0.9,
        source_count=3,
        valuation_method="median",
        asset_url="https://index.renaissos.com/cards/charizard",
        price_updated_at=datetime.now(timezone.utc),
        source_identity_key=card_identity_key(card),
    )

    assert _price_text(card, candidate) == "COLLECTION"
    assert _price_text(card, exact) == "$430.00"


async def test_price_cache_stall_does_not_block_api_result(monkeypatch):
    monkeypatch.setenv("RENAISS_PRICE_CACHE_TIMEOUT_SECONDS", "0.01")

    async def never_returns(*args, **kwargs):
        await asyncio.Event().wait()

    monkeypatch.setattr(
        "renaiss_bot.database.queries.get_recent_price_snapshot",
        never_returns,
    )
    monkeypatch.setattr(
        "renaiss_bot.database.queries.log_price_snapshot",
        never_returns,
    )
    official = RenaissPrice(
        status="candidate",
        source="renaiss-index-api",
        fmv_usd=10,
    )
    monkeypatch.setattr(
        "renaiss_bot.services.pricing.fetch_official_price",
        AsyncMock(return_value=official),
    )

    result = await asyncio.wait_for(
        fetch_price(CardIdentity(category="pokemon_tcg", card_name="Fast Card")),
        timeout=0.2,
    )

    assert result is official
