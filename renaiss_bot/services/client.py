"""Configurable Renaiss API client.

The official API shape can be wired through environment variables without changing
the Telegram handlers. The parser intentionally accepts several common response
shapes so the first SDK/API drop only needs small mapping adjustments here.
"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlencode

import aiohttp

from renaiss_bot.services.models import CardIdentity, GradeOffer, RenaissPrice
from renaiss_bot.services.referral import add_referral, build_search_url, renaiss_base_url

_LIST_KEYS = ("results", "items", "assets", "cards", "data", "list")
_NAME_KEYS = ("name", "title", "card_name", "display_name", "asset_name")
_ASSET_ID_KEYS = ("id", "asset_id", "item_id", "card_id", "slug")
_URL_KEYS = ("url", "asset_url", "permalink", "market_url", "listing_url", "href", "pageUrl")
_IMAGE_URL_KEYS = ("imageUrlLg", "imageUrl", "imageUrlThumb", "image_url", "image_url_lg", "image")
_PRICE_KEYS = (
    "fmv_usd",
    "fmv",
    "fair_market_value",
    "market_value_usd",
    "price_usd",
    "current_price_usd",
    "listed_price_usd",
    "last_sale_usd",
)
_PRICE_CENTS_KEYS = ("priceUsdCents", "usdCents")
_CHANGE_7D_KEYS = (
    "change_7d_pct",
    "price_change_7d_pct",
    "seven_day_change_pct",
    "change7d",
    "change_7d",
    "deltaPct",
    "d7",
)


def _api_base() -> str:
    return os.getenv("RENAISS_API_BASE_URL", "https://api.renaissos.com").strip().rstrip("/")


def _search_path() -> str:
    return os.getenv("RENAISS_API_SEARCH_PATH", "/v1/search").strip() or "/v1/search"


def _api_key() -> str:
    return os.getenv("RENAISS_API_KEY", "").strip()


def _float_or_none(value: Any) -> float | None:
    try:
        if value is None or value == "":
            return None
        if isinstance(value, str):
            value = value.replace("$", "").replace(",", "").strip()
            if value.endswith("%"):
                value = value[:-1]
        return float(value)
    except (TypeError, ValueError):
        return None


def _string_from(payload: Mapping[str, Any], keys: tuple[str, ...]) -> str | None:
    for key in keys:
        value = payload.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    return None


def _nested(payload: Mapping[str, Any], *keys: str) -> Any:
    current: Any = payload
    for key in keys:
        if not isinstance(current, Mapping):
            return None
        current = current.get(key)
    return current


def _price_from(payload: Mapping[str, Any]) -> float | None:
    for key in _PRICE_KEYS:
        amount = _float_or_none(payload.get(key))
        if amount is not None:
            return amount
    for key in _PRICE_CENTS_KEYS:
        amount = _float_or_none(payload.get(key))
        if amount is not None:
            return amount / 100
    for nested_key in ("price", "prices", "market", "valuation", "fmv"):
        nested = payload.get(nested_key)
        if isinstance(nested, Mapping):
            amount = _price_from(nested)
            if amount is not None:
                return amount
    return None


def _change_7d_from(payload: Mapping[str, Any]) -> float | None:
    for key in _CHANGE_7D_KEYS:
        amount = _float_or_none(payload.get(key))
        if amount is not None:
            return amount
    for nested_key in ("price", "prices", "market", "valuation", "history"):
        nested = payload.get(nested_key)
        if isinstance(nested, Mapping):
            amount = _change_7d_from(nested)
            if amount is not None:
                return amount
    deltas = payload.get("deltas")
    if isinstance(deltas, Mapping):
        amount = _float_or_none(deltas.get("d7"))
        if amount is not None:
            return amount
    return None


def _updated_at_from(payload: Mapping[str, Any]) -> datetime:
    for key in ("price_updated_at", "updated_at", "updatedAt", "last_updated", "lastSaleAt", "timestamp"):
        value = payload.get(key)
        if not value:
            continue
        if isinstance(value, datetime):
            return value
        try:
            return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            continue
    return datetime.now(timezone.utc)


def _iter_candidates(payload: Any) -> list[Mapping[str, Any]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, Mapping)]
    if not isinstance(payload, Mapping):
        return []
    for key in _LIST_KEYS:
        value = payload.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, Mapping)]
        if isinstance(value, Mapping):
            nested = _iter_candidates(value)
            if nested:
                return nested
    return [payload]


def _normalize(value: str | None) -> str:
    return " ".join((value or "").lower().replace("-", " ").split())


def _candidate_score(card: CardIdentity, payload: Mapping[str, Any]) -> int:
    name = _normalize(_string_from(payload, _NAME_KEYS))
    card_name = _normalize(card.card_name)
    score = 0
    if name == card_name:
        score += 6
    elif card_name and (card_name in name or name in card_name):
        score += 3

    set_code = _normalize(_string_from(payload, ("set_code", "setCode", "set", "series_code", "collection")))
    if card.set_code and set_code and _normalize(card.set_code) == set_code:
        score += 2

    number = _normalize(_string_from(payload, ("collector_number", "cardNumber", "number", "card_number", "item_number")))
    if card.collector_number and number and _normalize(card.collector_number) == number:
        score += 2

    grade = _normalize(_string_from(payload, ("grade", "gradeLabel", "condition", "variant")))
    if card.grade and grade and _normalize(card.grade) in grade:
        score += 1

    return score


def _asset_url(payload: Mapping[str, Any], card: CardIdentity) -> str:
    url = _string_from(payload, _URL_KEYS)
    if url:
        if url.startswith("/"):
            return f"{renaiss_base_url()}{url}"
        return url
    asset_id = _string_from(payload, _ASSET_ID_KEYS)
    if asset_id:
        return f"{renaiss_base_url()}/assets/{asset_id}"
    return build_search_url(card.card_name, card.category)


def _image_url(payload: Mapping[str, Any]) -> str | None:
    return _string_from(payload, _IMAGE_URL_KEYS)


def parse_renaiss_price_payload(card: CardIdentity, payload: Any) -> RenaissPrice | None:
    candidates = _iter_candidates(payload)
    if not candidates:
        return None

    best = max(candidates, key=lambda item: _candidate_score(card, item))
    score = _candidate_score(card, best)
    fmv = _price_from(best)
    asset_url = _asset_url(best, card)
    status = "exact" if score >= 8 else "candidate"
    if fmv is None and score <= 0:
        return None

    return RenaissPrice(
        status=status,  # type: ignore[arg-type]
        source="renaiss-index-api",
        renaiss_asset_id=_string_from(best, _ASSET_ID_KEYS),
        asset_url=asset_url,
        referral_url=add_referral(asset_url),
        image_url=_image_url(best),
        grade_label=_string_from(best, ("gradeLabel", "grade_label", "grade")),
        grading_company=_string_from(best, ("company", "gradingCompany", "grading_company")),
        confidence=_string_from(best, ("confidence",)),
        fmv_usd=fmv,
        change_7d_pct=_change_7d_from(best),
        market_status=_string_from(best, ("market_status", "status", "state")) or "Market Live",
        price_updated_at=_updated_at_from(best),
        price_range_min_usd=_float_or_none(_nested(best, "range", "min_usd") or best.get("price_range_min_usd")),
        price_range_max_usd=_float_or_none(_nested(best, "range", "max_usd") or best.get("price_range_max_usd")),
    )


def parse_grade_offers(card: CardIdentity, payload: Any) -> list[GradeOffer]:
    """Collect every priced candidate that plausibly matches the card name.

    Unlike parse_renaiss_price_payload (single best match), this keeps all
    grades so grading-premium comparisons can be computed."""
    offers: list[GradeOffer] = []
    for candidate in _iter_candidates(payload):
        name = _normalize(_string_from(candidate, _NAME_KEYS))
        card_name = _normalize(card.card_name)
        if not name or not card_name:
            continue
        if name != card_name and card_name not in name and name not in card_name:
            continue
        fmv = _price_from(candidate)
        if fmv is None or fmv <= 0:
            continue
        offers.append(
            GradeOffer(
                grade_label=_string_from(candidate, ("gradeLabel", "grade_label", "grade")),
                grading_company=_string_from(candidate, ("company", "gradingCompany", "grading_company")),
                fmv_usd=fmv,
                asset_url=_asset_url(candidate, card),
            )
        )
    return offers


async def fetch_grade_offers(
    card: CardIdentity,
    *,
    timeout_seconds: float = 2.5,
) -> list[GradeOffer]:
    mock = _mock_payload()
    if mock is not None:
        return parse_grade_offers(card, mock)

    if not _api_base():
        return []

    timeout = aiohttp.ClientTimeout(total=timeout_seconds)
    async with aiohttp.ClientSession(timeout=timeout, headers=_headers()) as session:
        async with session.get(_search_url(card)) as response:
            if response.status == 404:
                return []
            response.raise_for_status()
            payload = await response.json(content_type=None)
    return parse_grade_offers(card, payload)


def _mock_payload() -> Any | None:
    raw = os.getenv("RENAISS_API_MOCK_JSON", "").strip()
    if not raw:
        return None
    return json.loads(raw)


def _headers() -> dict[str, str]:
    headers = {"Accept": "application/json"}
    key = _api_key()
    if key:
        header_name = os.getenv("RENAISS_API_AUTH_HEADER", "Authorization").strip() or "Authorization"
        prefix = os.getenv("RENAISS_API_AUTH_PREFIX", "Bearer").strip()
        headers[header_name] = f"{prefix} {key}".strip() if prefix else key
    return headers


def _search_url(card: CardIdentity) -> str:
    base = _api_base()
    path = _search_path()
    if not path.startswith("/"):
        path = f"/{path}"
    params = {
        "q": card.card_name,
        "limit": os.getenv("RENAISS_API_SEARCH_LIMIT", "12").strip() or "12",
    }
    params = {key: value for key, value in params.items() if value}
    return f"{base}{path}?{urlencode(params)}"


async def fetch_official_price(
    card: CardIdentity,
    *,
    timeout_seconds: float = 2.5,
) -> RenaissPrice | None:
    mock = _mock_payload()
    if mock is not None:
        return parse_renaiss_price_payload(card, mock)

    if not _api_base():
        return None

    timeout = aiohttp.ClientTimeout(total=timeout_seconds)
    async with aiohttp.ClientSession(timeout=timeout, headers=_headers()) as session:
        async with session.get(_search_url(card)) as response:
            if response.status == 404:
                return None
            response.raise_for_status()
            payload = await response.json(content_type=None)
    return parse_renaiss_price_payload(card, payload)
