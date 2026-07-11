"""Configurable Renaiss API client.

The official API shape can be wired through environment variables without changing
the Telegram handlers. The parser intentionally accepts several common response
shapes so the first SDK/API drop only needs small mapping adjustments here.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from contextlib import asynccontextmanager
from collections.abc import Mapping
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from math import ceil, isfinite
from time import monotonic
from typing import Any
from urllib.parse import urlencode, urlparse

import aiohttp

from renaiss_bot.services.models import CardIdentity, GradeOffer, RenaissPrice, card_identity_key
from renaiss_bot.services.referral import add_referral, build_search_url, renaiss_base_url

logger = logging.getLogger(__name__)
EXACT_PARTNER_CONTRACT = "card-detail-v1"
LEGACY_EXACT_PARTNER_CONTRACT = "item-by-no-v1"
_partner_request_semaphore: asyncio.Semaphore | None = None
_partner_request_semaphore_loop = None
_partner_request_semaphore_limit = 0


class RenaissAPICooldown(RuntimeError):
    def __init__(self, retry_after_seconds: int):
        self.retry_after_seconds = max(1, int(retry_after_seconds))
        super().__init__(f"Renaiss Partner API cooldown: {self.retry_after_seconds}s")


class RenaissAPIUnavailable(RuntimeError):
    """The shared quota guard cannot be verified, so external calls stay closed."""


_partner_api_blocked_until = 0.0

_LIST_KEYS = (
    "results",
    "items",
    "assets",
    "cards",
    "data",
    "list",
    "prices",
    "estimates",
    "tiers",
    "grades",
)
_NAME_KEYS = ("name", "title", "card_name", "display_name", "asset_name")
_ASSET_ID_KEYS = ("id", "asset_id", "item_id", "card_id", "slug")
_URL_KEYS = ("url", "asset_url", "permalink", "market_url", "listing_url", "href", "pageUrl")
_IMAGE_URL_KEYS = ("imageUrlLg", "imageUrl", "imageUrlThumb", "image_url", "image_url_lg", "image")
_PRICE_KEYS = (
    "best_estimate",
    "bestEstimate",
    "fmv_usd",
    "fmv",
    "fair_market_value",
    "market_value_usd",
    "price_usd",
    "current_price_usd",
    "listed_price_usd",
    "last_sale_usd",
)
_EXACT_FMV_KEYS = (
    "best_estimate",
    "bestEstimate",
    "fmv_usd",
    "fmv",
    "fair_market_value",
    "market_value_usd",
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
_EXACT_PRICE_UPDATED_AT_KEYS = (
    "price_updated_at",
    "priceUpdatedAt",
    "fmv_updated_at",
    "fmvUpdatedAt",
    "valuation_updated_at",
    "valuationUpdatedAt",
)
_BROAD_UPDATED_AT_KEYS = _EXACT_PRICE_UPDATED_AT_KEYS + (
    "updated_at",
    "updatedAt",
    "last_updated",
    "lastSaleAt",
    "timestamp",
)
_VALUATION_METHOD_KEYS = (
    "valuation_method",
    "valuationMethod",
    "price_method",
    "priceMethod",
    "method",
)


def _api_base() -> str:
    raw = os.getenv("RENAISS_API_BASE_URL", "https://api.renaissos.com").strip().rstrip("/")
    try:
        parsed = urlparse(raw)
        port = parsed.port
    except ValueError:
        return ""
    allowed_hosts = {
        host.strip().lower()
        for host in os.getenv(
            "RENAISS_API_ALLOWED_HOSTS",
            "api.renaissos.com",
        ).split(",")
        if host.strip()
    }
    if (
        parsed.scheme != "https"
        or parsed.hostname is None
        or parsed.hostname.lower() not in allowed_hosts
        or parsed.username
        or parsed.password
        or port not in {None, 443}
        or parsed.query
        or parsed.fragment
    ):
        return ""
    return raw


def _search_path() -> str:
    return os.getenv("RENAISS_API_SEARCH_PATH", "/v1/search").strip() or "/v1/search"


def _api_key() -> str:
    return os.getenv("RENAISS_API_KEY", "").strip()


def _api_secret() -> str:
    return os.getenv("RENAISS_API_SECRET", "").strip()


def exact_partner_contract_enabled() -> bool:
    """Keep structural exact scoring closed until an official fixture is approved."""
    return os.getenv("RENAISS_API_EXACT_CONTRACT", "").strip() in {
        EXACT_PARTNER_CONTRACT,
        LEGACY_EXACT_PARTNER_CONTRACT,
    }


def card_detail_contract_enabled() -> bool:
    return os.getenv("RENAISS_API_EXACT_CONTRACT", "").strip() == EXACT_PARTNER_CONTRACT


def _retry_after_seconds(value: str | None) -> int:
    if not value:
        return 300
    try:
        return min(86_400, max(1, int(float(value))))
    except (TypeError, ValueError, OverflowError):
        try:
            retry_at = parsedate_to_datetime(value)
        except (TypeError, ValueError, OverflowError):
            return 300
        if retry_at.tzinfo is None:
            retry_at = retry_at.replace(tzinfo=timezone.utc)
        return min(
            86_400,
            max(1, int((retry_at - datetime.now(timezone.utc)).total_seconds())),
        )


async def _ensure_partner_api_available() -> None:
    global _partner_api_blocked_until
    local_remaining = _partner_api_blocked_until - monotonic()
    if local_remaining > 0:
        raise RenaissAPICooldown(ceil(local_remaining))
    try:
        from renaiss_bot.database.api_queries import get_partner_api_cooldown_seconds

        remaining = await get_partner_api_cooldown_seconds()
    except Exception as exc:
        logger.warning("Partner API cooldown state could not be verified: %s", exc)
        raise RenaissAPIUnavailable(
            "Partner API cooldown state could not be verified"
        ) from exc
    if remaining > 0:
        _partner_api_blocked_until = monotonic() + remaining
        raise RenaissAPICooldown(remaining)


def _bounded_env_int(name: str, default: int, *, minimum: int, maximum: int) -> int:
    try:
        return min(maximum, max(minimum, int(os.getenv(name, str(default)))))
    except (TypeError, ValueError):
        return default


def _partner_request_limit() -> int:
    return _bounded_env_int(
        "RENAISS_API_MAX_CONCURRENCY",
        2,
        minimum=1,
        maximum=4,
    )


def _partner_min_interval_ms() -> int:
    return _bounded_env_int(
        "RENAISS_API_MIN_INTERVAL_MS",
        500,
        minimum=100,
        maximum=10_000,
    )


def _partner_queue_wait_seconds() -> float:
    try:
        value = float(os.getenv("RENAISS_API_QUEUE_WAIT_SECONDS", "2"))
    except (TypeError, ValueError):
        return 2.0
    return min(10.0, max(0.1, value))


def _partner_semaphore() -> asyncio.Semaphore:
    global _partner_request_semaphore
    global _partner_request_semaphore_loop
    global _partner_request_semaphore_limit
    loop = asyncio.get_running_loop()
    limit = _partner_request_limit()
    if (
        _partner_request_semaphore is None
        or _partner_request_semaphore_loop is not loop
        or _partner_request_semaphore_limit != limit
    ):
        _partner_request_semaphore = asyncio.Semaphore(limit)
        _partner_request_semaphore_loop = loop
        _partner_request_semaphore_limit = limit
    return _partner_request_semaphore


async def _acquire_partner_request_slot(*, timeout_seconds: float) -> None:
    deadline = monotonic() + min(_partner_queue_wait_seconds(), max(0.1, timeout_seconds))
    while True:
        try:
            from renaiss_bot.database.api_queries import claim_partner_api_request_slot

            wait_ms = await claim_partner_api_request_slot(
                min_interval_ms=_partner_min_interval_ms()
            )
        except Exception as exc:
            logger.warning("Partner API request gate could not be verified: %s", exc)
            raise RenaissAPIUnavailable(
                "Partner API request gate could not be verified"
            ) from exc
        if wait_ms <= 0:
            return
        remaining = deadline - monotonic()
        delay = wait_ms / 1000
        if remaining <= 0 or delay > remaining:
            raise RenaissAPICooldown(max(1, ceil(delay)))
        await asyncio.sleep(delay)


@asynccontextmanager
async def _partner_request_guard(*, timeout_seconds: float):
    """Bound in-process concurrency and cross-instance request start rate."""
    semaphore = _partner_semaphore()
    queue_wait = min(_partner_queue_wait_seconds(), max(0.1, timeout_seconds))
    try:
        await asyncio.wait_for(semaphore.acquire(), timeout=queue_wait)
    except TimeoutError as exc:
        raise RenaissAPICooldown(1) from exc
    try:
        await _ensure_partner_api_available()
        await _acquire_partner_request_slot(timeout_seconds=timeout_seconds)
        # Another worker may have observed a 429 while this request waited.
        await _ensure_partner_api_available()
        yield
    finally:
        semaphore.release()


async def _record_partner_rate_limit(response: aiohttp.ClientResponse) -> None:
    global _partner_api_blocked_until
    retry_after = _retry_after_seconds(response.headers.get("Retry-After"))
    _partner_api_blocked_until = max(
        _partner_api_blocked_until,
        monotonic() + retry_after,
    )
    try:
        from renaiss_bot.database.api_queries import extend_partner_api_cooldown

        await extend_partner_api_cooldown(
            retry_after_seconds=retry_after,
            reason="http_429",
        )
    except Exception as exc:
        logger.warning("Partner API cooldown could not be shared after HTTP 429: %s", exc)


def _item_by_no_path() -> str:
    """Return the optional structural lookup path.

    Renaiss documents ``/v1/index/item-by-no``, but that route is not present in
    the live public OpenAPI as of 2026-07-11. It therefore remains opt-in so the
    existing search-backed collector experience does not break on a 404.
    """
    return os.getenv("RENAISS_API_ITEM_BY_NO_PATH", "").strip()


def _float_or_none(value: Any) -> float | None:
    try:
        if value is None or value == "" or isinstance(value, bool):
            return None
        if isinstance(value, str):
            value = value.replace("$", "").replace(",", "").strip()
            if value.endswith("%"):
                value = value[:-1]
        number = float(value)
        return number if isfinite(number) else None
    except (TypeError, ValueError, OverflowError):
        return None


def _int_or_none(value: Any) -> int | None:
    amount = _float_or_none(value)
    if (
        amount is None
        or not amount.is_integer()
        or amount < 0
        or amount > 2_147_483_647
    ):
        return None
    return int(amount)


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
        if amount is not None and amount > 0:
            return amount
    for key in _PRICE_CENTS_KEYS:
        amount = _float_or_none(payload.get(key))
        if amount is not None and amount > 0:
            return amount / 100
    for nested_key in ("price", "prices", "market", "valuation", "fmv"):
        nested = payload.get(nested_key)
        if isinstance(nested, Mapping):
            amount = _price_from(nested)
            if amount is not None:
                return amount
    return None


def _exact_fmv_from(payload: Mapping[str, Any]) -> float | None:
    """Read only explicit valuation fields accepted for scored exact results."""
    for key in _EXACT_FMV_KEYS:
        amount = _float_or_none(payload.get(key))
        if amount is not None and amount > 0:
            return amount
    for nested_key in ("price", "prices", "market", "valuation", "fmv"):
        nested = payload.get(nested_key)
        if isinstance(nested, Mapping):
            amount = _exact_fmv_from(nested)
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


def _updated_at_from(
    payload: Mapping[str, Any],
    *,
    exact_evidence: bool = False,
) -> datetime | None:
    keys = _EXACT_PRICE_UPDATED_AT_KEYS if exact_evidence else _BROAD_UPDATED_AT_KEYS
    for key in keys:
        value = payload.get(key)
        if not value:
            continue
        if isinstance(value, datetime):
            parsed = value
        else:
            try:
                parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            except ValueError:
                continue
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    freshness_value = payload.get("freshness_days")
    if freshness_value is None:
        freshness_value = payload.get("freshnessDays")
    freshness_days = _float_or_none(freshness_value)
    if freshness_days is not None and freshness_days >= 0:
        from datetime import timedelta

        return datetime.now(timezone.utc) - timedelta(days=freshness_days)
    return None


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


def _normalize_set_identity(value: str | None) -> str:
    """Normalize official provider prefixes without weakening set identity."""
    normalized = _normalize(value)
    normalized = re.sub(
        r"^pokemon\s+[^\s]+\s+(?:en|english)\s+",
        "",
        normalized,
    )
    normalized = re.sub(
        r"^pokemon\s+(?:japanese|jp)\s+[^\s]+\s+",
        "",
        normalized,
    )
    return normalized


def _normalize_variation(value: str | None) -> str:
    """Collapse naming aliases used by different official Pokemon feeds."""
    normalized = _normalize(value)
    return {
        "art rare": "illustration rare",
        "special art rare": "special illustration rare",
    }.get(normalized, normalized)


def _market_grade(card: CardIdentity) -> str:
    metadata = card.metadata or {}
    explicit = str(
        metadata.get("market_grade")
        or metadata.get("grading_grade")
        or metadata.get("grade_label")
        or ""
    ).strip()
    if explicit:
        return explicit
    card_grade = card.grade.strip()
    if card_grade.upper().startswith(("RAW", "PSA", "BGS", "CGC", "SGC", "TAG")):
        return card_grade
    # CardIdentity.grade is usually the in-game rarity (R/SR/UR), not slab grade.
    return "RAW"


def _canonical_market_grade(
    value: str | None,
) -> tuple[str, float | None, tuple[str, ...]] | None:
    """Parse an exact grader/tier key without numeric substring matching."""
    normalized = _normalize(value)
    tokens = normalized.split()
    graders = {token for token in tokens if token in {"psa", "bgs", "cgc", "sgc", "tag"}}
    grade_pattern = r"(?<![\d.])(?:10(?:\.0+)?|[1-9](?:\.\d+)?)(?![\d.])"
    numeric_tokens = {
        float(match)
        for match in re.findall(grade_pattern, normalized)
    }
    if "raw" in tokens:
        qualifiers = tuple(token for token in tokens if token != "raw")
        if graders or numeric_tokens:
            return None
        if not qualifiers or (len(qualifiers) == 1 and qualifiers[0] in {"a", "b", "c", "d"}):
            return ("raw", None, qualifiers)
        return None
    if len(graders) != 1 or len(numeric_tokens) != 1:
        return None
    without_number = re.sub(grade_pattern, " ", normalized)
    qualifiers = tuple(
        token
        for token in without_number.split()
        if token not in graders
    )
    return (next(iter(graders)), next(iter(numeric_tokens)), qualifiers)


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

    grade = _normalize(
        " ".join(
            value
            for value in (
                _string_from(payload, ("grading_company", "gradingCompany", "company")),
                _string_from(payload, ("grade", "gradeLabel", "condition", "variant")),
            )
            if value
        )
    )
    target_grade = _canonical_market_grade(_market_grade(card))
    if target_grade is not None and _canonical_market_grade(grade) == target_grade:
        score += 4

    return score


def _identity_mappings(payload: Any, best: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    """Return response objects that may carry structural card identity."""
    mappings: list[Mapping[str, Any]] = [best]
    if isinstance(payload, Mapping):
        mappings.append(payload)
        for key in ("card", "item", "identity", "data"):
            nested = payload.get(key)
            if isinstance(nested, Mapping):
                mappings.append(nested)
                for nested_key in ("card", "item", "identity"):
                    child = nested.get(nested_key)
                    if isinstance(child, Mapping):
                        mappings.append(child)
    return mappings


def _identity_values(
    mappings: list[Mapping[str, Any]],
    keys: tuple[str, ...],
) -> set[str]:
    return {
        normalized
        for mapping in mappings
        if (value := _string_from(mapping, keys)) is not None
        if (normalized := _normalize(value))
    }


def _valuation_method_from(payload: Any, best: Mapping[str, Any]) -> str | None:
    """Return one explicit response-level or tier-level valuation method.

    A configured expectation or a generic ``best_estimate`` field is not
    evidence. Conflicting method labels also remain unknown.
    """
    mappings: list[Mapping[str, Any]] = [best]
    if isinstance(payload, Mapping):
        mappings.append(payload)
        for key in ("data", "price", "valuation"):
            nested = payload.get(key)
            if isinstance(nested, Mapping):
                mappings.append(nested)
    values = {
        normalized
        for mapping in mappings
        if (value := _string_from(mapping, _VALUATION_METHOD_KEYS)) is not None
        if (normalized := _normalize(value))
    }
    return next(iter(values)) if len(values) == 1 else None


def _price_tier_matches(card: CardIdentity, payload: Mapping[str, Any]) -> bool:
    returned_grade = _normalize(
        " ".join(
            value
            for value in (
                _string_from(payload, ("grading_company", "gradingCompany", "company")),
                _string_from(payload, ("grade", "gradeLabel", "condition")),
            )
            if value
        )
    )
    expected_grade = _canonical_market_grade(_market_grade(card))
    returned_key = _canonical_market_grade(returned_grade)
    return expected_grade is not None and returned_key == expected_grade


def _structural_identity_matches(
    card: CardIdentity,
    payload: Any,
    best: Mapping[str, Any],
) -> bool:
    """Require the structural response itself to prove the requested identity.

    Merely calling an item-by-number URL is not proof: a changed endpoint,
    ignored query parameter, or malformed response must remain collection-only.
    """
    mappings = _identity_mappings(payload, best)
    returned_set_names = {
        normalized
        for mapping in mappings
        if (value := _string_from(mapping, ("set_name", "setName"))) is not None
        if (normalized := _normalize_set_identity(value))
    }
    returned_set_codes = _identity_values(
        mappings,
        ("set_code", "setCode", "set", "series_code", "collection"),
    )
    expected_set_names = {_normalize_set_identity(card.set_name)} - {""}
    expected_set_codes = {_normalize(card.set_code)} - {""}
    returned_numbers = _identity_values(
        mappings,
        ("collector_number", "collectorNumber", "cardNumber", "number", "card_number", "item_no", "itemNumber"),
    )
    expected_number = _normalize(card.collector_number)
    returned_languages = _identity_values(
        mappings,
        ("language", "language_code", "languageCode", "lang"),
    )
    expected_language = _normalize(_language_tag(card.language))
    language_aliases = {expected_language, _normalize(card.language)} - {""}

    if returned_set_names and expected_set_names:
        if not returned_set_names.issubset(expected_set_names):
            return False
    elif not (
        returned_set_codes
        and expected_set_codes
        and returned_set_codes.issubset(expected_set_codes)
    ):
        return False
    if (
        not expected_number
        or not returned_numbers
        or returned_numbers != {expected_number}
    ):
        return False
    if (
        not language_aliases
        or not returned_languages
        or not returned_languages.issubset(language_aliases)
    ):
        return False

    expected_variation = _normalize_variation(
        str((card.metadata or {}).get("variation") or (card.metadata or {}).get("variant") or "")
    )
    variation_keys = ("variation", "variant", "printing", "finish")
    variation_declared = any(
        any(key in mapping for key in variation_keys) for mapping in mappings
    )
    returned_variations = {
        normalized
        for mapping in mappings
        if (value := _string_from(mapping, variation_keys)) is not None
        if (normalized := _normalize_variation(value))
    }
    if not variation_declared:
        return False
    if expected_variation and returned_variations != {expected_variation}:
        return False
    if not expected_variation and returned_variations:
        return False
    return _price_tier_matches(card, best)


def _direct_asset_url(payload: Mapping[str, Any]) -> str | None:
    url = _string_from(payload, _URL_KEYS)
    if url:
        if url.startswith("/"):
            return f"{renaiss_base_url()}{url}"
        return url
    asset_id = _string_from(payload, _ASSET_ID_KEYS)
    if asset_id:
        return f"{renaiss_base_url()}/assets/{asset_id}"
    return None


def _asset_url(payload: Mapping[str, Any], card: CardIdentity) -> str:
    direct = _direct_asset_url(payload)
    if direct:
        return direct
    return build_search_url(card.card_name, card.category)


def _image_url(payload: Mapping[str, Any]) -> str | None:
    return _string_from(payload, _IMAGE_URL_KEYS)


def parse_renaiss_price_payload(
    card: CardIdentity,
    payload: Any,
    *,
    exact_identity: bool = False,
    source: str = "renaiss-index-api",
) -> RenaissPrice | None:
    candidates = _iter_candidates(payload)
    if not candidates:
        return None

    best = max(candidates, key=lambda item: _candidate_score(card, item))
    exact_tiers = [
        item
        for item in candidates
        if _price_tier_matches(card, item) and _exact_fmv_from(item) is not None
    ]
    if exact_identity and len(exact_tiers) == 1:
        best = exact_tiers[0]
    score = _candidate_score(card, best)
    broad_price = _price_from(best)
    identity_verified = (
        exact_identity
        and len(exact_tiers) == 1
        and _structural_identity_matches(card, payload, best)
    )
    exact_fmv = _exact_fmv_from(best) if identity_verified else None
    fmv = exact_fmv if exact_fmv is not None else broad_price
    asset_url = _direct_asset_url(best)
    if exact_identity and asset_url is None and isinstance(payload, Mapping):
        asset_url = _direct_asset_url(payload)
        for key in ("card", "item", "identity"):
            nested_identity = payload.get(key)
            if asset_url is None and isinstance(nested_identity, Mapping):
                asset_url = _direct_asset_url(nested_identity)
    if not exact_identity and asset_url is None:
        asset_url = build_search_url(card.card_name, card.category)
    # Search results remain candidates even when their text score is high.  Only
    # the structural item-by-number route is allowed to assert exact identity.
    status = "exact" if identity_verified and exact_fmv is not None else "candidate"
    if fmv is None and score <= 0:
        return None

    return RenaissPrice(
        status=status,  # type: ignore[arg-type]
        source=source,
        renaiss_asset_id=_string_from(best, _ASSET_ID_KEYS),
        asset_url=asset_url,
        referral_url=add_referral(asset_url) if asset_url else None,
        image_url=_image_url(best),
        grade_label=_string_from(best, ("gradeLabel", "grade_label", "grade")),
        grading_company=_string_from(best, ("company", "gradingCompany", "grading_company")),
        confidence=_string_from(best, ("confidence", "confidence_tier", "confidenceTier")),
        confidence_score=_float_or_none(
            best.get("confidence_score") or best.get("confidenceScore")
        ),
        source_count=_int_or_none(best.get("source_count") or best.get("sourceCount")),
        observation_count=_int_or_none(
            best.get("observation_count") or best.get("observationCount")
        ),
        valuation_method=_valuation_method_from(payload, best),
        fmv_usd=fmv,
        change_7d_pct=_change_7d_from(best),
        market_status=_string_from(best, ("market_status", "status", "state")) or "Market Live",
        price_updated_at=_updated_at_from(
            best,
            exact_evidence=identity_verified and exact_fmv is not None,
        ),
        price_range_min_usd=_float_or_none(_nested(best, "range", "min_usd") or best.get("price_range_min_usd")),
        price_range_max_usd=_float_or_none(_nested(best, "range", "max_usd") or best.get("price_range_max_usd")),
        source_identity_key=card_identity_key(card) if identity_verified else None,
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

    async with _partner_request_guard(timeout_seconds=timeout_seconds):
        timeout = aiohttp.ClientTimeout(total=timeout_seconds)
        async with aiohttp.ClientSession(timeout=timeout, headers=_headers()) as session:
            async with session.get(_search_url(card), allow_redirects=False) as response:
                if response.status == 404:
                    return []
                if response.status == 429:
                    await _record_partner_rate_limit(response)
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
    secret = _api_secret()
    if key and secret:
        headers[os.getenv("RENAISS_API_KEY_HEADER", "X-Api-Key").strip() or "X-Api-Key"] = key
        headers[
            os.getenv("RENAISS_API_SECRET_HEADER", "X-Api-Secret").strip()
            or "X-Api-Secret"
        ] = secret
    elif key and os.getenv("RENAISS_API_AUTH_HEADER", "").strip():
        # Explicit compatibility escape hatch for a legacy/non-partner API.
        header_name = os.getenv("RENAISS_API_AUTH_HEADER", "").strip()
        prefix = os.getenv("RENAISS_API_AUTH_PREFIX", "Bearer").strip()
        headers[header_name] = f"{prefix} {key}".strip() if prefix else key
    return headers


_LANGUAGE_TAGS = {
    "english": "en",
    "japanese": "ja",
    "korean": "ko",
    "chinese": "zh",
    "french": "fr",
    "german": "de",
    "italian": "it",
    "spanish": "es",
    "portuguese": "pt",
}


def _language_tag(language: str) -> str:
    normalized = language.strip()
    if not normalized:
        return ""
    return _LANGUAGE_TAGS.get(normalized.lower(), normalized)


def _structural_url(card: CardIdentity) -> str | None:
    path = _item_by_no_path()
    set_name = card.set_name.strip()
    item_no = card.collector_number.strip()
    language = _language_tag(card.language)
    if not path or not set_name or not item_no or not language:
        return None
    if not path.startswith("/"):
        path = f"/{path}"
    variation = str(
        (card.metadata or {}).get("variation")
        or (card.metadata or {}).get("variant")
        or ""
    ).strip()
    params = {
        "set_name": set_name,
        "item_no": item_no,
        "variation": variation,
        "language": language,
    }
    return f"{_api_base()}{path}?{urlencode(params)}"


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


def _card_detail_candidate_matches(
    card: CardIdentity,
    candidate: Mapping[str, Any],
) -> bool:
    """Match every official structured identity field before following href."""
    if _normalize(_string_from(candidate, _NAME_KEYS)) != _normalize(card.card_name):
        return False
    returned_set_name = _normalize_set_identity(
        _string_from(candidate, ("setName", "set_name"))
    )
    returned_set_code = _normalize(_string_from(candidate, ("setCode", "set_code")))
    expected_set_name = _normalize_set_identity(card.set_name)
    expected_set_code = _normalize(card.set_code)
    if not (
        (returned_set_name and expected_set_name and returned_set_name == expected_set_name)
        or (returned_set_code and expected_set_code and returned_set_code == expected_set_code)
    ):
        return False
    returned_number = _normalize(
        _string_from(candidate, ("cardNumber", "collector_number", "number"))
    )
    if not returned_number or returned_number != _normalize(card.collector_number):
        return False
    returned_language = _normalize(
        _string_from(candidate, ("language", "languageCode", "lang"))
    )
    language_aliases = {_normalize(card.language), _normalize(_language_tag(card.language))} - {""}
    if not returned_language or returned_language not in language_aliases:
        return False
    expected_variation = _normalize_variation(
        str((card.metadata or {}).get("variation") or (card.metadata or {}).get("variant") or "")
    )
    returned_variation = _normalize_variation(
        _string_from(candidate, ("variation", "variant", "printing", "finish"))
    )
    if returned_variation != expected_variation:
        return False
    return _price_tier_matches(card, candidate)


def _card_detail_url_from_href(href: str | None) -> str | None:
    """Convert an official public card href into the authenticated detail route."""
    value = (href or "").strip()
    if not value:
        return None
    parsed = urlparse(value)
    if parsed.scheme or parsed.netloc:
        allowed_public_hosts = {"index.renaissos.com", "renaiss.xyz", "www.renaiss.xyz"}
        if (
            parsed.scheme != "https"
            or parsed.hostname is None
            or parsed.hostname.lower() not in allowed_public_hosts
            or parsed.username
            or parsed.password
            or parsed.port not in {None, 443}
            or parsed.query
            or parsed.fragment
        ):
            return None
        path = parsed.path
    else:
        if not value.startswith("/") or "?" in value or "#" in value:
            return None
        path = value
    if not path.startswith("/card/"):
        return None
    detail_path = path.replace("/card/", "/v1/cards/", 1)
    segments = [segment for segment in detail_path.split("/") if segment]
    if len(segments) != 5 or segments[:2] != ["v1", "cards"]:
        return None
    return f"{_api_base()}{detail_path}"


def _normalized_card_detail_payload(payload: Any) -> Mapping[str, Any] | None:
    """Pin the scored FMV to the one explicit official median method."""
    if not isinstance(payload, Mapping):
        return None
    methods = payload.get("methods")
    if not isinstance(methods, list):
        return None
    medians = [
        method
        for method in methods
        if isinstance(method, Mapping)
        and str(method.get("method") or "").strip().lower() == "median"
        and (_float_or_none(method.get("priceUsdCents")) or 0) > 0
    ]
    if len(medians) != 1:
        return None
    median = medians[0]
    normalized = dict(payload)
    normalized["best_estimate"] = float(median["priceUsdCents"]) / 100
    normalized["valuation_method"] = "median"
    for target, source in (
        ("confidence", "confidence"),
        ("sourceCount", "sourceCount"),
        ("observationCount", "observationCount"),
    ):
        if median.get(source) is not None:
            normalized[target] = median[source]
    timestamps = []
    for key in ("updatedAt", "lastSaleAt"):
        value = payload.get(key)
        if not value:
            continue
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            continue
        timestamps.append(parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc))
    if timestamps:
        normalized["priceUpdatedAt"] = max(timestamps).astimezone(timezone.utc).isoformat()
    return normalized


async def _fetch_card_detail_price(
    card: CardIdentity,
    session: aiohttp.ClientSession,
) -> RenaissPrice | None:
    metadata = card.metadata or {}
    stored_href = str(
        metadata.get("renaiss_href")
        or metadata.get("price_asset_url")
        or metadata.get("asset_url")
        or ""
    ).strip()
    detail_url = _card_detail_url_from_href(stored_href)
    if detail_url is None:
        async with session.get(_search_url(card), allow_redirects=False) as response:
            if response.status == 404:
                return None
            if response.status == 429:
                await _record_partner_rate_limit(response)
            response.raise_for_status()
            search_payload = await response.json(content_type=None)
        matches = [
            candidate
            for candidate in _iter_candidates(search_payload)
            if _card_detail_candidate_matches(card, candidate)
        ]
        detail_urls = {
            url
            for candidate in matches
            if (url := _card_detail_url_from_href(_string_from(candidate, ("href",))))
        }
        if len(detail_urls) != 1:
            return None
        detail_url = next(iter(detail_urls))
    async with session.get(detail_url, allow_redirects=False) as response:
        if response.status == 404:
            return None
        if response.status == 429:
            await _record_partner_rate_limit(response)
        response.raise_for_status()
        detail_payload = await response.json(content_type=None)
    normalized = _normalized_card_detail_payload(detail_payload)
    if normalized is None:
        return None
    return parse_renaiss_price_payload(
        card,
        normalized,
        exact_identity=True,
        source="renaiss-card-detail-api",
    )


async def fetch_official_price(
    card: CardIdentity,
    *,
    timeout_seconds: float = 2.5,
) -> RenaissPrice | None:
    mock = _mock_payload()
    if mock is not None:
        return parse_renaiss_price_payload(
            card,
            mock,
            source="renaiss-index-api-mock",
        )

    if not _api_base():
        return None

    structural_url = _structural_url(card)
    url = structural_url or _search_url(card)
    async with _partner_request_guard(timeout_seconds=timeout_seconds):
        timeout = aiohttp.ClientTimeout(total=timeout_seconds)
        async with aiohttp.ClientSession(timeout=timeout, headers=_headers()) as session:
            if card_detail_contract_enabled():
                return await _fetch_card_detail_price(card, session)
            async with session.get(url, allow_redirects=False) as response:
                if response.status == 404:
                    return None
                if response.status == 429:
                    await _record_partner_rate_limit(response)
                response.raise_for_status()
                payload = await response.json(content_type=None)
    return parse_renaiss_price_payload(
        card,
        payload,
        exact_identity=structural_url is not None and exact_partner_contract_enabled(),
    )
