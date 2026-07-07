"""Shared service models."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
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
    fmv_usd: float | None = None
    change_7d_pct: float | None = None
    market_status: str = "unknown"
    price_updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    price_range_min_usd: float | None = None
    price_range_max_usd: float | None = None


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
