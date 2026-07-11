"""Daily Market Pick rules shared by DB queries and Telegram handlers."""

from __future__ import annotations

import math
import os
from datetime import date, datetime, time, timedelta, timezone
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

from renaiss_bot.services.models import CardIdentity, RenaissPrice, card_identity_key

KST = ZoneInfo("Asia/Seoul")

MAX_PRICE_AGE_HOURS = 48
DECISION_OPEN = time(21, 0)
DECISION_CLOSE = time(23, 59, 59, 999999)
VERIFIED_PRICE_SOURCES = frozenset(
    {
        "renaiss-index-api",
        "renaiss-index-api:item-by-no",
    }
)
REQUIRED_VALUATION_METHOD = "median"
_daily_pick_preflight_ready = False


def _env_enabled(name: str, *, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_number(name: str, default: float) -> float:
    try:
        value = float(os.getenv(name, str(default)))
        return value if math.isfinite(value) else default
    except (TypeError, ValueError):
        return default


def _price_source_url_allowed(url: str | None) -> bool:
    if not url:
        return False
    try:
        parsed = urlparse(url)
        port = parsed.port
    except ValueError:
        return False
    allowed_hosts = {
        host.strip().lower()
        for host in os.getenv(
            "RENAISS_PRICE_SOURCE_ALLOWED_HOSTS",
            "renaiss.xyz,www.renaiss.xyz,index.renaissos.com",
        ).split(",")
        if host.strip()
    }
    return (
        parsed.scheme == "https"
        and parsed.hostname is not None
        and parsed.hostname.lower() in allowed_hosts
        and not parsed.username
        and not parsed.password
        and port in {None, 443}
        and parsed.path not in {"", "/"}
        and not parsed.fragment
    )


def daily_pick_requested() -> bool:
    return _env_enabled("RENAISS_DAILY_PICK_ENABLED")


def daily_pick_configuration_issues() -> list[str]:
    """Return unsafe production configuration gaps without exposing secrets."""
    issues = []
    if os.getenv("RENAISS_API_MOCK_JSON", "").strip():
        issues.append("RENAISS_API_MOCK_JSON must be unset")
    if not os.getenv("DATABASE_URL", "").strip():
        issues.append("DATABASE_URL is missing")
    if _env_enabled("RENAISS_SKIP_DB"):
        issues.append("RENAISS_SKIP_DB is enabled")
    if not os.getenv("RENAISS_API_KEY", "").strip():
        issues.append("RENAISS_API_KEY is missing")
    if not os.getenv("RENAISS_API_SECRET", "").strip():
        issues.append("RENAISS_API_SECRET is missing")
    if not os.getenv("RENAISS_API_ITEM_BY_NO_PATH", "").strip():
        issues.append("RENAISS_API_ITEM_BY_NO_PATH is missing")
    from renaiss_bot.services.client import EXACT_PARTNER_CONTRACT, exact_partner_contract_enabled

    if not exact_partner_contract_enabled():
        issues.append(
            f"RENAISS_API_EXACT_CONTRACT must equal {EXACT_PARTNER_CONTRACT} after fixture approval"
        )
    configured_method = os.getenv("RENAISS_API_EXACT_VALUATION_METHOD", "").strip().lower()
    if configured_method != REQUIRED_VALUATION_METHOD:
        issues.append(
            "RENAISS_API_EXACT_VALUATION_METHOD must equal median after fixture approval"
        )
    for name in (
        "RENAISS_DAILY_PICK_PROBE_CARD_NAME",
        "RENAISS_DAILY_PICK_PROBE_SET_NAME",
        "RENAISS_DAILY_PICK_PROBE_ITEM_NO",
    ):
        if not os.getenv(name, "").strip():
            issues.append(f"{name} is missing")
    return issues


def exact_price_lookup_configured() -> bool:
    """Whether exact Partner lookup can run independently of Daily Pick."""
    if os.getenv("RENAISS_API_MOCK_JSON", "").strip():
        return False
    from renaiss_bot.services.client import exact_partner_contract_enabled

    if not exact_partner_contract_enabled():
        return False
    if (
        os.getenv("RENAISS_API_EXACT_VALUATION_METHOD", "").strip().lower()
        != REQUIRED_VALUATION_METHOD
    ):
        return False
    return all(
        os.getenv(name, "").strip()
        for name in (
            "RENAISS_API_KEY",
            "RENAISS_API_SECRET",
            "RENAISS_API_ITEM_BY_NO_PATH",
        )
    )


def daily_pick_enabled() -> bool:
    """Open the pilot only when both its flag and exact-data gates are ready."""
    return (
        daily_pick_requested()
        and not daily_pick_configuration_issues()
        and _daily_pick_preflight_ready
    )


def close_daily_pick_admission() -> None:
    """Close new picks until this process completes a live Partner probe."""
    global _daily_pick_preflight_ready
    _daily_pick_preflight_ready = False


def daily_pick_probe_card() -> CardIdentity:
    """Build the operator-selected structural tuple used by the startup probe."""
    return CardIdentity(
        category=os.getenv("RENAISS_DAILY_PICK_PROBE_CATEGORY", "pokemon_tcg").strip()
        or "pokemon_tcg",
        card_name=os.getenv("RENAISS_DAILY_PICK_PROBE_CARD_NAME", "").strip(),
        set_name=os.getenv("RENAISS_DAILY_PICK_PROBE_SET_NAME", "").strip(),
        set_code=os.getenv("RENAISS_DAILY_PICK_PROBE_SET_CODE", "").strip(),
        collector_number=os.getenv("RENAISS_DAILY_PICK_PROBE_ITEM_NO", "").strip(),
        language=os.getenv("RENAISS_DAILY_PICK_PROBE_LANGUAGE", "English").strip()
        or "English",
        grade=os.getenv("RENAISS_DAILY_PICK_PROBE_GRADE", "RAW").strip() or "RAW",
        metadata={
            "variation": os.getenv("RENAISS_DAILY_PICK_PROBE_VARIATION", "").strip()
        },
    )


async def verify_daily_pick_partner_ready(*, timeout_seconds: float = 5.0) -> RenaissPrice:
    """Fail startup unless one known structural tuple passes every scored-data gate."""
    issues = daily_pick_configuration_issues()
    if issues:
        raise RuntimeError("Daily Pick configuration is incomplete: " + "; ".join(issues))
    from renaiss_bot.services.client import fetch_official_price

    card = daily_pick_probe_card()
    price = await fetch_official_price(card, timeout_seconds=timeout_seconds)
    if price is None:
        raise RuntimeError("Daily Pick Partner probe returned no structural price")
    evidence_issues = market_card_eligibility_issues(card, price)
    if evidence_issues:
        raise RuntimeError("Daily Pick Partner probe failed: " + "; ".join(evidence_issues))
    global _daily_pick_preflight_ready
    _daily_pick_preflight_ready = True
    return price


def week_start_kst(at: datetime | date | None = None) -> date:
    """Return the Monday date for the KST week containing ``at``."""
    if at is None:
        current = datetime.now(KST).date()
    elif isinstance(at, datetime):
        current = at.astimezone(KST).date() if at.tzinfo else at.replace(tzinfo=KST).date()
    else:
        current = at
    return current - timedelta(days=current.weekday())


def today_kst(at: datetime | date | None = None) -> date:
    if at is None:
        return datetime.now(KST).date()
    if isinstance(at, datetime):
        return at.astimezone(KST).date() if at.tzinfo else at.replace(tzinfo=KST).date()
    return at


def decision_window_open(at: datetime | None = None) -> bool:
    current = at or datetime.now(KST)
    if current.tzinfo is None:
        current = current.replace(tzinfo=KST)
    else:
        current = current.astimezone(KST)
    return DECISION_OPEN <= current.time() <= DECISION_CLOSE


def market_card_eligibility_issues(
    card: CardIdentity,
    price: RenaissPrice,
    *,
    now: datetime | None = None,
) -> list[str]:
    """Explain why a price cannot be used for guessing or scored results."""
    issues: list[str] = []
    if price.status != "exact":
        issues.append("price status is not exact")
    if (
        price.fmv_usd is None
        or isinstance(price.fmv_usd, bool)
        or not math.isfinite(price.fmv_usd)
        or price.fmv_usd <= 0
    ):
        issues.append("positive FMV is missing")
    if price.source not in VERIFIED_PRICE_SOURCES:
        issues.append("price source is not an approved Renaiss source")
    if (price.valuation_method or "").strip().lower() != REQUIRED_VALUATION_METHOD:
        issues.append("valuation method is not explicitly median")
    if price.source_identity_key != card_identity_key(card):
        issues.append("price evidence is not bound to this card identity")
    if not (card.set_name.strip() or card.set_code.strip()) or not card.collector_number.strip():
        issues.append("set and collector number identity is incomplete")
    confidence = (price.confidence or "").strip().lower()
    if confidence not in {"prime", "high", "medium"}:
        issues.append("confidence tier is below medium or missing")
    min_confidence = max(
        0.0,
        _env_number("RENAISS_DAILY_PICK_MIN_CONFIDENCE_SCORE", 0.7),
    )
    if (
        price.confidence_score is None
        or isinstance(price.confidence_score, bool)
        or not math.isfinite(price.confidence_score)
        or price.confidence_score > 1.0
        or price.confidence_score <= min_confidence
    ):
        issues.append("numeric confidence score is missing or below threshold")
    min_sources = max(1, int(_env_number("RENAISS_DAILY_PICK_MIN_SOURCE_COUNT", 2)))
    if (
        price.source_count is None
        or isinstance(price.source_count, bool)
        or not isinstance(price.source_count, int)
        or price.source_count < min_sources
    ):
        issues.append("source count is missing or below threshold")
    if not _price_source_url_allowed(price.asset_url):
        issues.append("exact Renaiss HTTPS source URL is missing or not allowed")
    updated_at = price.price_updated_at
    if updated_at is None:
        issues.append("price freshness timestamp is missing")
    else:
        if updated_at.tzinfo is None:
            updated_at = updated_at.replace(tzinfo=timezone.utc)
        current = now or datetime.now(timezone.utc)
        if current.tzinfo is None:
            current = current.replace(tzinfo=timezone.utc)
        age = current.astimezone(timezone.utc) - updated_at.astimezone(timezone.utc)
        if age < timedelta(0) or age > timedelta(hours=MAX_PRICE_AGE_HOURS):
            issues.append("price timestamp is future-dated or stale")
    return issues


def market_card_eligible(card: CardIdentity, price: RenaissPrice) -> bool:
    """Gate price guessing and Daily Pick to verified Partner API marks."""
    return not market_card_eligibility_issues(card, price)
