"""Renaiss partner-auth and exact structural lookup tests."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock
from urllib.parse import parse_qs, urlparse

import pytest

import renaiss_bot.services.client as client_module
from renaiss_bot.services.client import (
    RenaissAPICooldown,
    RenaissAPIUnavailable,
    _ensure_partner_api_available,
    _card_detail_candidate_matches,
    _card_detail_url_from_href,
    _headers,
    _normalized_card_detail_payload,
    _structural_url,
    exact_partner_contract_enabled,
    fetch_official_price,
    parse_renaiss_price_payload,
)
from renaiss_bot.services.models import CardIdentity
from renaiss_bot.services.market import market_card_eligible


def _card() -> CardIdentity:
    return CardIdentity(
        category="pokemon_tcg",
        card_name="Charizard",
        set_code="BS",
        set_name="Base Set",
        collector_number="4/102",
        language="English",
        grade="RAW",
        metadata={"variation": ""},
    )


def _structural_payload(body: dict) -> dict:
    return {
        "valuation_method": "median",
        **body,
        "identity": {
            "set_name": "Base Set",
            "set_code": "BS",
            "collector_number": "4/102",
            "language": "en",
            "variation": "",
        },
    }


def test_partner_auth_uses_key_and_secret_headers(monkeypatch):
    monkeypatch.setenv("RENAISS_API_KEY", "key-id")
    monkeypatch.setenv("RENAISS_API_SECRET", "server-secret")
    monkeypatch.delenv("RENAISS_API_AUTH_HEADER", raising=False)
    headers = _headers()
    assert headers["X-Api-Key"] == "key-id"
    assert headers["X-Api-Secret"] == "server-secret"
    assert "Authorization" not in headers


async def test_all_partner_calls_honor_shared_database_cooldown(monkeypatch):
    monkeypatch.setattr(client_module, "_partner_api_blocked_until", 0.0)
    cooldown = AsyncMock(return_value=17)
    monkeypatch.setattr(
        "renaiss_bot.database.api_queries.get_partner_api_cooldown_seconds",
        cooldown,
    )

    with pytest.raises(RenaissAPICooldown) as exc_info:
        await _ensure_partner_api_available()

    assert exc_info.value.retry_after_seconds == 17
    cooldown.assert_awaited_once()


async def test_partner_calls_fail_closed_when_shared_cooldown_cannot_be_read(monkeypatch):
    monkeypatch.setattr(client_module, "_partner_api_blocked_until", 0.0)
    monkeypatch.setattr(
        "renaiss_bot.database.api_queries.get_partner_api_cooldown_seconds",
        AsyncMock(side_effect=RuntimeError("db down")),
    )

    with pytest.raises(RenaissAPIUnavailable):
        await _ensure_partner_api_available()


async def test_partner_request_slot_waits_on_cross_instance_gate(monkeypatch):
    claim = AsyncMock(side_effect=[250, 0])
    sleep = AsyncMock()
    monkeypatch.setattr(
        "renaiss_bot.database.api_queries.claim_partner_api_request_slot",
        claim,
    )
    monkeypatch.setattr(client_module.asyncio, "sleep", sleep)
    monkeypatch.setenv("RENAISS_API_MIN_INTERVAL_MS", "500")
    monkeypatch.setenv("RENAISS_API_QUEUE_WAIT_SECONDS", "2")

    await client_module._acquire_partner_request_slot(timeout_seconds=2.5)

    assert claim.await_count == 2
    assert claim.await_args_list[0].kwargs["min_interval_ms"] == 500
    sleep.assert_awaited_once_with(0.25)


async def test_partner_request_slot_fails_closed_when_gate_database_fails(monkeypatch):
    monkeypatch.setattr(
        "renaiss_bot.database.api_queries.claim_partner_api_request_slot",
        AsyncMock(side_effect=RuntimeError("db unavailable")),
    )

    with pytest.raises(RenaissAPIUnavailable):
        await client_module._acquire_partner_request_slot(timeout_seconds=2.5)


def test_incomplete_partner_credentials_are_not_sent(monkeypatch):
    monkeypatch.setenv("RENAISS_API_KEY", "key-id")
    monkeypatch.delenv("RENAISS_API_SECRET", raising=False)
    monkeypatch.delenv("RENAISS_API_AUTH_HEADER", raising=False)
    assert _headers() == {"Accept": "application/json"}


def test_structural_url_uses_exact_tuple_and_bcp47_language(monkeypatch):
    monkeypatch.setenv("RENAISS_API_BASE_URL", "https://api.renaissos.com")
    monkeypatch.setenv("RENAISS_API_ITEM_BY_NO_PATH", "/v1/index/item-by-no")
    url = _structural_url(_card())
    assert url is not None
    parsed = urlparse(url)
    params = parse_qs(parsed.query, keep_blank_values=True)
    assert parsed.path == "/v1/index/item-by-no"
    assert params == {
        "set_name": ["Base Set"],
        "item_no": ["4/102"],
        "variation": [""],
        "language": ["en"],
    }


def test_structural_url_requires_set_name(monkeypatch):
    monkeypatch.setenv("RENAISS_API_ITEM_BY_NO_PATH", "/v1/index/item-by-no")
    card = CardIdentity(
        category="pokemon_tcg",
        card_name="Charizard",
        set_code="BS",
        collector_number="4/102",
        language="English",
    )
    assert _structural_url(card) is None


def test_exact_contract_is_closed_until_approved_fixture_version(monkeypatch):
    monkeypatch.delenv("RENAISS_API_EXACT_CONTRACT", raising=False)
    assert not exact_partner_contract_enabled()
    monkeypatch.setenv("RENAISS_API_EXACT_CONTRACT", "unverified-v2")
    assert not exact_partner_contract_enabled()
    monkeypatch.setenv("RENAISS_API_EXACT_CONTRACT", "card-detail-v1")
    assert exact_partner_contract_enabled()


def test_card_detail_candidate_requires_full_structured_identity(monkeypatch):
    monkeypatch.setenv("RENAISS_API_DEFAULT_RAW_GRADE", "RAW A")
    card = CardIdentity(
        category="pokemon_tcg",
        card_name="Lickitung",
        set_code="SMP",
        set_name="Movie Special Pack",
        collector_number="22",
        language="Japanese",
        grade="R",
        metadata={"variation": "Common", "market_grade": "RAW A"},
    )
    candidate = {
        "name": "Lickitung",
        "setName": "Movie Special Pack",
        "setCode": "SMP",
        "cardNumber": "22",
        "variation": "Common",
        "language": "Japanese",
        "company": "RAW",
        "grade": "A",
        "href": "/card/pokemon/movie-special-pack/22-lickitung-raw-A",
    }
    assert _card_detail_candidate_matches(card, candidate)
    assert not _card_detail_candidate_matches(card, {**candidate, "cardNumber": "23"})


def test_card_detail_href_is_strictly_converted(monkeypatch):
    monkeypatch.setenv("RENAISS_API_BASE_URL", "https://api.renaissos.com")
    assert _card_detail_url_from_href(
        "/card/pokemon/base-set/4-charizard-psa-10-abcd1234"
    ) == (
        "https://api.renaissos.com/v1/cards/pokemon/base-set/"
        "4-charizard-psa-10-abcd1234"
    )
    assert _card_detail_url_from_href("https://evil.example/card/pokemon/a/b") is None
    assert _card_detail_url_from_href("/card/pokemon/a/b?token=secret") is None


def test_card_detail_normalization_pins_median_and_latest_observation():
    payload = {
        "name": "Charizard",
        "updatedAt": "2026-07-07T08:24:23Z",
        "lastSaleAt": "2026-07-10T21:00:00Z",
        "methods": [
            {"method": "mean", "priceUsdCents": 42977},
            {
                "method": "median",
                "priceUsdCents": 42042,
                "confidence": "prime",
                "sourceCount": 2,
                "observationCount": 51,
            },
        ],
    }
    normalized = _normalized_card_detail_payload(payload)
    assert normalized is not None
    assert normalized["best_estimate"] == 420.42
    assert normalized["valuation_method"] == "median"
    assert normalized["sourceCount"] == 2
    assert normalized["priceUpdatedAt"].startswith("2026-07-10T21:00:00")


@pytest.mark.parametrize(
    "url",
    [
        "http://api.renaissos.com",
        "https://api.renaissos.com:444",
        "https://user:secret@api.renaissos.com",
        "https://evil.example",
    ],
)
def test_partner_api_base_rejects_unsafe_origins(monkeypatch, url):
    monkeypatch.setenv("RENAISS_API_BASE_URL", url)
    monkeypatch.setenv("RENAISS_API_ALLOWED_HOSTS", "api.renaissos.com")
    assert client_module._api_base() == ""


def test_structural_response_maps_reference_fields_and_freshness():
    before = datetime.now(timezone.utc) - timedelta(days=2, seconds=2)
    price = parse_renaiss_price_payload(
        _card(),
        _structural_payload({
            "prices": [
                {
                    "grading_company": "RAW",
                    "grade": "RAW",
                    "best_estimate": 51.25,
                    "confidence_tier": "high",
                    "confidence_score": 0.91,
                    "source_count": 4,
                    "observation_count": 19,
                    "freshness_days": 2,
                }
            ]
        }),
        exact_identity=True,
    )
    assert price is not None
    assert price.status == "exact"
    assert price.fmv_usd == 51.25
    assert price.confidence == "high"
    assert price.confidence_score == 0.91
    assert price.source_count == 4
    assert price.observation_count == 19
    assert price.valuation_method == "median"
    assert price.source_identity_key is not None
    assert price.price_updated_at is not None
    assert before <= price.price_updated_at <= datetime.now(timezone.utc) - timedelta(days=2)


def _eligible_structural_payload() -> dict:
    return _structural_payload(
        {
            "prices": [
                {
                    "grading_company": "RAW",
                    "grade": "RAW",
                    "best_estimate": 51.25,
                    "confidence_tier": "high",
                    "confidence_score": 0.91,
                    "source_count": 4,
                    "freshness_days": 0,
                    "url": "https://index.renaissos.com/cards/charizard",
                }
            ]
        }
    )


def test_structural_price_requires_explicit_median_method_for_scoring():
    payload = _eligible_structural_payload()
    payload.pop("valuation_method")

    price = parse_renaiss_price_payload(_card(), payload, exact_identity=True)

    assert price is not None and price.status == "exact"
    assert price.valuation_method is None
    assert not market_card_eligible(_card(), price)


def test_structural_price_rejects_explicit_nonmedian_method_for_scoring():
    payload = _eligible_structural_payload()
    payload["valuation_method"] = "mean"

    price = parse_renaiss_price_payload(_card(), payload, exact_identity=True)

    assert price is not None and price.status == "exact"
    assert price.valuation_method == "mean"
    assert not market_card_eligible(_card(), price)


def test_structural_price_treats_conflicting_method_evidence_as_unknown():
    payload = _eligible_structural_payload()
    payload["prices"][0]["valuation_method"] = "mean"

    price = parse_renaiss_price_payload(_card(), payload, exact_identity=True)

    assert price is not None and price.status == "exact"
    assert price.valuation_method is None
    assert not market_card_eligible(_card(), price)


def test_structural_response_preserves_unknown_freshness():
    price = parse_renaiss_price_payload(
        _card(),
        _structural_payload({
            "prices": [
                {
                    "grading_company": "RAW",
                    "grade": "RAW",
                    "best_estimate": 51.25,
                    "confidence_tier": "high",
                    "confidence_score": 0.91,
                    "source_count": 4,
                    "observation_count": 19,
                }
            ]
        }),
        exact_identity=True,
    )
    assert price is not None
    assert price.status == "exact"
    assert price.price_updated_at is None


def test_exact_fmv_does_not_treat_item_or_sale_timestamp_as_price_freshness():
    now = datetime.now(timezone.utc).isoformat()
    for field in ("updated_at", "lastSaleAt", "timestamp"):
        price = parse_renaiss_price_payload(
            _card(),
            _structural_payload({
                "prices": [
                    {
                        "grading_company": "RAW",
                        "grade": "RAW",
                        "best_estimate": 51.25,
                        "confidence_tier": "high",
                        "confidence_score": 0.91,
                        "source_count": 4,
                        "url": "https://index.renaissos.com/cards/charizard",
                        field: now,
                    }
                ]
            }),
            exact_identity=True,
        )

        assert price is not None and price.status == "exact"
        assert price.price_updated_at is None
        assert not market_card_eligible(_card(), price)


def test_zero_freshness_days_is_an_explicit_current_mark():
    before = datetime.now(timezone.utc) - timedelta(seconds=1)
    price = parse_renaiss_price_payload(
        _card(),
        _structural_payload({
            "prices": [
                {
                    "grading_company": "RAW",
                    "grade": "RAW",
                    "best_estimate": 51.25,
                    "freshness_days": 0,
                }
            ]
        }),
        exact_identity=True,
    )
    assert price is not None and price.price_updated_at is not None
    assert before <= price.price_updated_at <= datetime.now(timezone.utc)


def test_naive_api_timestamp_is_normalized_to_utc():
    price = parse_renaiss_price_payload(
        _card(),
        _structural_payload({
            "prices": [
                {
                    "grading_company": "RAW",
                    "grade": "RAW",
                    "best_estimate": 51.25,
                    "price_updated_at": "2026-07-10T12:30:00",
                }
            ]
        }),
        exact_identity=True,
    )
    assert price is not None
    assert price.price_updated_at == datetime(2026, 7, 10, 12, 30, tzinfo=timezone.utc)


def test_search_response_never_claims_exact_identity_from_text_score():
    price = parse_renaiss_price_payload(
        _card(),
        {
            "name": "Charizard",
            "setCode": "BS",
            "cardNumber": "4/102",
            "company": "RAW",
            "grade": "RAW",
            "best_estimate": 95,
        },
    )
    assert price is not None
    assert price.status == "candidate"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("set_name", "Jungle"),
        ("collector_number", "5/102"),
        ("language", "ja"),
    ],
)
def test_structural_response_must_echo_matching_identity(field, value):
    payload = _structural_payload(
        {
            "prices": [
                {
                    "grading_company": "RAW",
                    "grade": "RAW",
                    "best_estimate": 95,
                }
            ]
        }
    )
    payload["identity"][field] = value

    price = parse_renaiss_price_payload(_card(), payload, exact_identity=True)

    assert price is not None
    assert price.status == "candidate"


def test_structural_response_rejects_conflicting_price_item_identity():
    payload = _structural_payload(
        {
            "prices": [
                {
                    "set_name": "Jungle",
                    "collector_number": "5/64",
                    "language": "ja",
                    "variation": "foil",
                    "grading_company": "RAW",
                    "grade": "RAW",
                    "best_estimate": 95,
                }
            ]
        }
    )

    price = parse_renaiss_price_payload(_card(), payload, exact_identity=True)

    assert price is not None
    assert price.status == "candidate"
    assert not market_card_eligible(_card(), price)


def test_structural_response_requires_explicit_variation_and_unique_price_tier():
    missing_variation = _structural_payload(
        {"prices": [{"grading_company": "RAW", "grade": "RAW", "best_estimate": 95}]}
    )
    missing_variation["identity"].pop("variation")
    duplicate_tier = _structural_payload(
        {
            "prices": [
                {"grading_company": "RAW", "grade": "RAW", "best_estimate": 95},
                {"grading_company": "RAW", "grade": "RAW", "best_estimate": 96},
            ]
        }
    )

    for payload in (missing_variation, duplicate_tier):
        price = parse_renaiss_price_payload(_card(), payload, exact_identity=True)
        assert price is not None
        assert price.status == "candidate"
        assert not market_card_eligible(_card(), price)


@pytest.mark.parametrize("returned_variation", ["", None])
def test_nonempty_requested_variation_cannot_match_empty_response(returned_variation):
    card = _card()
    object.__setattr__(card, "metadata", {"variation": "unlimited"})
    payload = _structural_payload(
        {
            "prices": [
                {
                    "grading_company": "RAW",
                    "grade": "RAW",
                    "best_estimate": 95,
                }
            ]
        }
    )
    payload["identity"]["variation"] = returned_variation

    price = parse_renaiss_price_payload(card, payload, exact_identity=True)

    assert price is not None
    assert price.status == "candidate"
    assert not market_card_eligible(card, price)


def test_structural_listing_or_last_sale_is_not_a_scored_exact_fmv():
    now = datetime.now(timezone.utc).isoformat()
    for field in ("listed_price_usd", "last_sale_usd"):
        price = parse_renaiss_price_payload(
            _card(),
            {
                "name": "Charizard",
                field: 95,
                "url": "https://index.renaissos.com/cards/charizard",
                "confidence": "high",
                "confidence_score": 0.95,
                "source_count": 4,
                "price_updated_at": now,
            },
            exact_identity=True,
        )

        assert price is not None and price.fmv_usd == 95
        assert price.status == "candidate"
        assert not market_card_eligible(_card(), price)


@pytest.mark.parametrize("non_finite", ["NaN", "Infinity", "-Infinity"])
def test_api_parser_rejects_non_finite_exact_evidence(non_finite):
    price = parse_renaiss_price_payload(
        _card(),
        {
            "name": "Charizard",
            "best_estimate": non_finite,
            "url": "https://index.renaissos.com/cards/charizard",
            "confidence": "high",
            "confidence_score": non_finite,
            "source_count": 4,
            "price_updated_at": datetime.now(timezone.utc).isoformat(),
        },
        exact_identity=True,
    )

    assert price is None or not market_card_eligible(_card(), price)


def test_api_parser_rejects_boolean_numbers_and_fractional_counts():
    price = parse_renaiss_price_payload(
        _card(),
        {
            "name": "Charizard",
            "best_estimate": True,
            "url": "https://index.renaissos.com/cards/charizard",
            "confidence": "high",
            "confidence_score": True,
            "source_count": 2.9,
            "price_updated_at": datetime.now(timezone.utc).isoformat(),
        },
        exact_identity=True,
    )

    assert price is None or not market_card_eligible(_card(), price)


def test_api_parser_never_exposes_negative_fmv_as_a_price():
    price = parse_renaiss_price_payload(
        _card(),
        {
            "name": "Charizard",
            "best_estimate": -95,
            "url": "https://index.renaissos.com/cards/charizard",
            "confidence": "high",
            "confidence_score": 0.9,
            "source_count": 3,
            "price_updated_at": datetime.now(timezone.utc).isoformat(),
        },
        exact_identity=True,
    )

    assert price is None or price.fmv_usd is None
    assert price is None or price.status == "candidate"


async def test_mock_response_is_labeled_non_competitive(monkeypatch):
    monkeypatch.setenv(
        "RENAISS_API_MOCK_JSON",
        '{"name":"Charizard","best_estimate":95}',
    )
    price = await fetch_official_price(_card())
    assert price is not None
    assert price.source == "renaiss-index-api-mock"
    assert price.status == "candidate"


def test_structural_response_uses_raw_tier_for_in_game_rarity():
    card = CardIdentity(
        category="pokemon_tcg",
        card_name="Charizard",
        set_name="Base Set",
        collector_number="4/102",
        language="English",
        grade="R",
    )
    payload = _structural_payload({
        "prices": [
            {"grading_company": "PSA", "grade": "10", "best_estimate": 900},
            {"grading_company": "RAW", "grade": "RAW", "best_estimate": 95},
        ]
    })
    price = parse_renaiss_price_payload(card, payload, exact_identity=True)
    assert price is not None
    assert price.fmv_usd == 95
    assert price.grading_company == "RAW"


@pytest.mark.parametrize(
    ("requested_grade", "returned_grade"),
    [("PSA 1", "10"), ("PSA 9", "9.5")],
)
def test_structural_response_never_uses_numeric_grade_substrings(
    requested_grade,
    returned_grade,
):
    card = CardIdentity(
        category="pokemon_tcg",
        card_name="Charizard",
        set_name="Base Set",
        collector_number="4/102",
        language="English",
        grade=requested_grade,
        metadata={"variation": ""},
    )
    payload = _structural_payload(
        {
            "prices": [
                {
                    "grading_company": "PSA",
                    "grade": returned_grade,
                    "best_estimate": 900,
                }
            ]
        }
    )

    price = parse_renaiss_price_payload(card, payload, exact_identity=True)

    assert price is not None
    assert price.status == "candidate"


def test_structural_response_accepts_only_exact_numeric_grade_tier():
    card = CardIdentity(
        category="pokemon_tcg",
        card_name="Charizard",
        set_name="Base Set",
        collector_number="4/102",
        language="English",
        grade="PSA 10",
        metadata={"variation": ""},
    )
    price = parse_renaiss_price_payload(
        card,
        _structural_payload(
            {
                "prices": [
                    {
                        "grading_company": "PSA",
                        "grade": "10",
                        "best_estimate": 900,
                    }
                ]
            }
        ),
        exact_identity=True,
    )

    assert price is not None
    assert price.status == "exact"


@pytest.mark.parametrize(
    ("requested_grade", "returned_company", "returned_grade"),
    [
        ("BGS 10", "BGS", "Black Label 10"),
        ("PSA 10", "PSA", "10 OC"),
    ],
)
def test_structural_response_rejects_distinct_grade_qualifiers(
    requested_grade,
    returned_company,
    returned_grade,
):
    card = CardIdentity(
        category="pokemon_tcg",
        card_name="Charizard",
        set_name="Base Set",
        collector_number="4/102",
        language="English",
        grade=requested_grade,
        metadata={"variation": ""},
    )
    price = parse_renaiss_price_payload(
        card,
        _structural_payload(
            {
                "prices": [
                    {
                        "grading_company": returned_company,
                        "grade": returned_grade,
                        "best_estimate": 900,
                    }
                ]
            }
        ),
        exact_identity=True,
    )

    assert price is not None
    assert price.status == "candidate"
