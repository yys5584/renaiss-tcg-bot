"""Canonical market identity mapping for local catalog cards."""

from __future__ import annotations

from renaiss_bot.services.card_pool import catalog_row_to_card


def _row(**overrides):
    row = {
        "category": "pokemon_tcg",
        "local_card_id": "catalog:pokemon_tcg:sv7-160-dachsbun-ex",
        "card_name": "Dachsbun ex",
        "grade": "SR",
        "rarity": "SR",
        "set_code": "sv7",
        "set_name": "Stellar Crown",
        "collector_number": "sv7-160",
        "language": "English",
        "image_url": "https://images.example/dachsbun.png",
        "market_price_usd": None,
        "metadata": {"price_source": "catalog-import"},
    }
    row.update(overrides)
    return row


def test_catalog_card_maps_provider_identity_to_renaiss_exact_fields(monkeypatch):
    monkeypatch.setenv("RENAISS_API_DEFAULT_MARKET_GRADE", "PSA 10 Gem Mint")
    card = catalog_row_to_card(_row())
    assert card.local_card_id.endswith("sv7-160-dachsbun-ex")
    assert card.collector_number == "160"
    assert card.grade == "SR"
    assert card.metadata["source_collector_number"] == "sv7-160"
    assert card.metadata["variation"] == "Ultra Rare"
    assert card.metadata["market_grade"] == "PSA 10 Gem Mint"


def test_explicit_market_identity_is_never_overwritten(monkeypatch):
    monkeypatch.setenv("RENAISS_API_DEFAULT_MARKET_GRADE", "PSA 10 Gem Mint")
    card = catalog_row_to_card(
        _row(
            metadata={
                "variation": "Alternate Art",
                "market_grade": "BGS 10 Black Label",
            }
        )
    )
    assert card.metadata["variation"] == "Alternate Art"
    assert card.metadata["market_grade"] == "BGS 10 Black Label"


def test_native_collector_number_is_preserved():
    card = catalog_row_to_card(
        _row(set_code="BS", collector_number="4/102", grade="R", rarity="R")
    )
    assert card.collector_number == "4/102"
    assert "source_collector_number" not in card.metadata
    assert "variation" not in card.metadata
