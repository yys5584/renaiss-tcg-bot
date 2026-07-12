"""Season collection manifest selection tests."""

from __future__ import annotations

import pytest

from renaiss_bot.tools.build_season_pool import (
    rank_collection_candidates,
    sitemap_pokemon_set_counts,
)


def test_sitemap_counts_only_pokemon_card_pages():
    payload = b"""<?xml version='1.0' encoding='UTF-8'?>
    <urlset xmlns='http://www.sitemaps.org/schemas/sitemap/0.9'>
      <url><loc>https://index.renaissos.com/card/pokemon/base-set/4-charizard-a</loc></url>
      <url><loc>https://index.renaissos.com/card/pokemon/base-set/5-pikachu-b</loc></url>
      <url><loc>https://index.renaissos.com/card/one-piece/op01/1-luffy-c</loc></url>
      <url><loc>https://index.renaissos.com/methodology</loc></url>
    </urlset>"""

    assert sitemap_pokemon_set_counts(payload) == {"base-set": 2}


def test_rank_collection_candidates_keeps_positive_unique_psa10_only():
    rows = [
        {"href": "/a", "company": "PSA", "grade": "10 Gem Mint", "priceUsdCents": 5000, "name": "A"},
        {"href": "/b", "company": "PSA", "grade": "10", "priceUsdCents": 9000, "name": "B"},
        {"href": "/c", "company": "CGC", "grade": "10", "priceUsdCents": 99900, "name": "C"},
        {"href": "/d", "company": "PSA", "grade": "9", "priceUsdCents": 99900, "name": "D"},
        {"href": "/e", "company": "PSA", "grade": "10", "priceUsdCents": 0, "name": "E"},
        {"href": "/a", "company": "PSA", "grade": "10 Gem Mint", "priceUsdCents": 5000, "name": "A"},
    ]

    cards = rank_collection_candidates(rows, size=2)

    assert [card["card_name"] for card in cards] == ["B", "A"]
    assert cards[-1]["api_reference_usd"] == 50.0
    assert all(card["pick_eligible"] is False for card in cards)


def test_rank_collection_candidates_fails_instead_of_padding_with_zero_prices():
    with pytest.raises(RuntimeError, match="only 0 positive-priced"):
        rank_collection_candidates(
            [{"href": "/zero", "company": "PSA", "grade": "10", "priceUsdCents": 0}],
            size=1,
        )
