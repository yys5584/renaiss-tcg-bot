from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

from renaiss_bot.database.catalog_queries import catalog_price_metadata
from renaiss_bot.jobs import _catalog_card, refresh_catalog_prices_job
from renaiss_bot.services.models import RenaissPrice


def _exact_price() -> RenaissPrice:
    return RenaissPrice(
        status="exact",
        source="renaiss-card-detail-api",
        fmv_usd=125.0,
        confidence="prime",
        confidence_score=0.9,
        source_count=3,
        observation_count=8,
        valuation_method="median",
        price_updated_at=datetime(2026, 7, 11, tzinfo=timezone.utc),
        source_identity_key="identity-key",
        image_url="https://images.example/card.png",
    )


def test_catalog_price_metadata_preserves_scored_evidence():
    metadata = catalog_price_metadata(_exact_price())

    assert metadata["price_status"] == "exact"
    assert metadata["price_valuation_method"] == "median"
    assert metadata["price_source_count"] == 3
    assert metadata["price_updated_at"] == "2026-07-11T00:00:00+00:00"
    assert metadata["evidence_origin"] == "official-api-import"


def test_catalog_row_preserves_runtime_identity_and_requests_psa10_market_grade():
    card = _catalog_card(
        {
            "local_card_id": "card-1",
            "category": "pokemon_tcg",
            "card_name": "Pikachu",
            "grade": "SAR",
            "set_code": "SV",
            "set_name": "Example",
            "collector_number": "25",
            "language": "English",
            "metadata": {"variation": "Illustration Rare", "market_grade": "PSA 10 Gem Mint"},
        }
    )

    assert card.grade == "SAR"
    assert card.metadata["market_grade"] == "PSA 10 Gem Mint"
    assert card.metadata["variation"] == "Illustration Rare"


async def test_catalog_refresh_persists_api_result_and_finishes_lease(monkeypatch):
    row = {
        "local_card_id": "card-1",
        "category": "pokemon_tcg",
        "card_name": "Pikachu",
        "grade": "SAR",
        "set_code": "SV",
        "set_name": "Example",
        "collector_number": "25",
        "language": "English",
        "metadata": {"variation": "Illustration Rare"},
    }
    finish = AsyncMock(return_value=True)
    update = AsyncMock(return_value=True)
    monkeypatch.setattr("renaiss_bot.jobs._catalog_refresh_enabled", lambda: True)
    monkeypatch.setattr("renaiss_bot.jobs.acquire_catalog_refresh_lease", AsyncMock(return_value=True))
    monkeypatch.setattr("renaiss_bot.jobs.list_catalog_cards_for_refresh", AsyncMock(return_value=[row]))
    monkeypatch.setattr("renaiss_bot.jobs.fetch_official_price", AsyncMock(return_value=_exact_price()))
    monkeypatch.setattr("renaiss_bot.jobs.update_catalog_cached_price", update)
    monkeypatch.setattr("renaiss_bot.jobs.finish_catalog_refresh_lease", finish)

    await refresh_catalog_prices_job(SimpleNamespace())

    update.assert_awaited_once()
    assert update.await_args.kwargs["local_card_id"] == "card-1"
    finish.assert_awaited_once()
    assert finish.await_args.kwargs["status"] == "completed"
