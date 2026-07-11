"""Shared service models."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
import hashlib
import json
from typing import Any, Literal

MatchStatus = Literal["exact", "candidate", "search_only", "missing", "api_error"]


@dataclass(frozen=True)
class CardIdentity:
    category: str
    card_name: str
    set_code: str = ""
    set_name: str = ""
    collector_number: str = ""
    language: str = "Japanese"
    rarity: str = ""
    grade: str = "RAW"
    local_card_id: str = ""
    image_url: str | None = None
    species_id: int | None = None
    species_name: str = ""
    already_owned: bool = False
    market_price_usd: float | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


def card_identity_key(card: CardIdentity) -> str:
    """Return a stable key binding exact evidence to the requested card tuple."""
    metadata = card.metadata or {}
    payload = {
        "category": card.category.strip().lower(),
        "card_name": " ".join(card.card_name.strip().lower().split()),
        "set_code": card.set_code.strip().lower(),
        "set_name": " ".join(card.set_name.strip().lower().split()),
        "collector_number": card.collector_number.strip().lower(),
        "language": card.language.strip().lower(),
        "grade": card.grade.strip().lower(),
        "variation": str(metadata.get("variation") or metadata.get("variant") or "")
        .strip()
        .lower(),
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class RenaissPrice:
    status: MatchStatus
    source: str
    renaiss_asset_id: str | None = None
    asset_url: str | None = None
    referral_url: str | None = None
    image_url: str | None = None
    grade_label: str | None = None
    grading_company: str | None = None
    confidence: str | None = None
    confidence_score: float | None = None
    source_count: int | None = None
    observation_count: int | None = None
    # The scored product contract requires an explicit valuation methodology.
    # Never infer this from a generic headline/best-estimate field.
    valuation_method: str | None = None
    fmv_usd: float | None = None
    change_7d_pct: float | None = None
    market_status: str = "unknown"
    # Unknown freshness must remain unknown.  Scored features require an
    # explicit timestamp supplied by the price source.
    price_updated_at: datetime | None = None
    price_range_min_usd: float | None = None
    price_range_max_usd: float | None = None
    # Set only after an endpoint-specific structural response proves the tuple.
    source_identity_key: str | None = None


@dataclass(frozen=True)
class GradeOffer:
    grade_label: str | None
    grading_company: str | None
    fmv_usd: float
    asset_url: str | None = None


@dataclass(frozen=True)
class GradingPremium:
    raw_usd: float
    graded_label: str
    graded_usd: float

    @property
    def multiplier(self) -> float:
        return self.graded_usd / self.raw_usd if self.raw_usd > 0 else 0.0


@dataclass(frozen=True)
class PackOpenResult:
    category: str
    cards: list[CardIdentity]
    best_card: CardIdentity
    best_price: RenaissPrice
    pack_type: str = "free"
    pack_count: int = 1
    pool_source: str = "demo"
    lucky_grades: list[str] = field(default_factory=list)
