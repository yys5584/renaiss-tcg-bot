from datetime import datetime, timezone

from renaiss_bot.tools.prepare_database import database_target_fingerprint
from renaiss_bot.tools.sync_psa10_catalog import (
    candidate_row,
    is_positive_psa10,
    sitemap_set_slugs,
    staging_target_issue,
)


_DSN = "postgresql://user:pw@db.example.com:6543/postgres"


def test_sitemap_set_slugs_includes_both_games_and_only_detail_pages():
    payload = b"""<?xml version='1.0'?>
    <urlset xmlns='http://www.sitemaps.org/schemas/sitemap/0.9'>
      <url><loc>https://index.renaissos.com/card/pokemon/base</loc></url>
      <url><loc>https://index.renaissos.com/card/pokemon/base/1-pikachu</loc></url>
      <url><loc>https://index.renaissos.com/card/one-piece/op01/1-luffy</loc></url>
    </urlset>"""
    assert sitemap_set_slugs(payload) == {("pokemon", "base"), ("one-piece", "op01")}


def test_positive_psa10_requires_price_image_and_href():
    row = {
        "company": "PSA", "grade": "10 Gem Mint", "priceUsdCents": 1000,
        "href": "/card/pokemon/base/1-pikachu", "imageUrl": "https://example/card.png",
    }
    assert is_positive_psa10(row)
    assert not is_positive_psa10({**row, "priceUsdCents": 0})
    assert not is_positive_psa10({**row, "imageUrl": ""})
    assert not is_positive_psa10({**row, "company": "CGC"})


def test_candidate_row_stages_one_piece_inactive_ready_payload():
    row = {
        "game": "one-piece", "name": "Nami", "setName": "OP-01", "setCode": "OP01",
        "cardNumber": "016", "variation": "Parallel", "language": "English",
        "company": "PSA", "grade": "10 Gem Mint", "gradeLabel": "PSA 10",
        "priceUsdCents": 12500, "confidence": "medium", "lastSaleAt": "2026-07-10T00:00:00Z",
        "deltaPct": 1.2, "href": "/card/one-piece/op01/016-nami",
        "imageUrl": "https://example/nami.png",
    }
    candidate = candidate_row(row, synced_at=datetime(2026, 7, 11, tzinfo=timezone.utc))
    assert candidate["category"] == "one_piece_tcg"
    assert candidate["market_price_usd"] == 125.0
    assert candidate["grade"] == "SR"  # $125 falls in the $100-299 tier
    assert candidate["metadata"]["market_grade"] == "PSA 10 Gem Mint"
    assert candidate["metadata"]["price_status"] == "candidate"


def test_grade_for_price_tiers():
    from renaiss_bot.services.spawn import grade_for_price

    assert grade_for_price(12000) == "UR"
    assert grade_for_price(500) == "UR"
    assert grade_for_price(499.99) == "SAR"
    assert grade_for_price(300) == "SAR"
    assert grade_for_price(100) == "SR"
    assert grade_for_price(99.99) == "R"
    assert grade_for_price(30) == "R"
    assert grade_for_price(15.72) == "C"
    assert grade_for_price(None) == "C"


def test_staging_refused_without_matching_fingerprint(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", _DSN)
    monkeypatch.delenv("RENAISS_EXPECTED_DATABASE_FINGERPRINT", raising=False)
    assert staging_target_issue() is not None
    monkeypatch.setenv("RENAISS_EXPECTED_DATABASE_FINGERPRINT", "deadbeef")
    assert staging_target_issue() is not None


def test_staging_allowed_when_fingerprint_matches(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", _DSN)
    monkeypatch.setenv(
        "RENAISS_EXPECTED_DATABASE_FINGERPRINT", database_target_fingerprint(_DSN)
    )
    assert staging_target_issue() is None
