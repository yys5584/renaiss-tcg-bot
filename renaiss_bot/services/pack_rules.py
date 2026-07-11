"""Standalone Renaiss cardpack odds copied from the Season 2 cardpack shape."""

from __future__ import annotations

import random
from collections.abc import Mapping
from dataclasses import dataclass

# ── 팩 경제 (2026-07-07 도입, 수치는 초안 — 운영 보며 조절) ──
DAILY_FREE_PACKS = 5          # 하루 무료 프리팩 (명령 개봉 기준, 드랍/퀴즈 보상은 별도)
EXTRA_FREE_PACK_RP = 100      # 무료 소진 후 추가 프리팩 1개당 RP
PREMIUM_PACK_RP = 500         # 프리미엄팩 1개당 RP

GRADE_ORDER = {
    "C": 0,
    "U": 1,
    "R": 2,
    "RR": 3,
    "AR": 4,
    "SR": 5,
    "SAR": 6,
    "UR": 7,
    "MUR": 8,
}

ALL_GRADES = tuple(GRADE_ORDER)
HIT_CARD_GRADES = ("R", "RR", "AR", "SR", "SAR", "UR", "MUR")
CARDS_PER_PACK = 10
MAX_BATCH_PACKS = 30

FREE_PACK_SLOT_GRADES = {
    1: "R",
    2: "R",
    3: "R",
    4: "R",
    5: "R",
    6: "R",
    7: "RR",
    8: "RR",
    9: "AR",
    10: "LUCKY",
}

PREMIUM_PACK_SLOT_GRADES = {
    1: "R",
    2: "R",
    3: "RR",
    4: "RR",
    5: "AR",
    6: "AR",
    7: "AR",
    8: "SR",
    9: "SAR",
    10: "LUCKY",
}

FREE_PACK_LUCKY_PROBS = {
    "R": 0.929,
    "RR": 0.04,
    "AR": 0.025,
    "SR": 0.004,
    "SAR": 0.001,
    "UR": 0.0007,
    "MUR": 0.0003,
}

PREMIUM_PACK_LUCKY_PROBS = {
    "R": 0.8713,
    "RR": 0.03,
    "AR": 0.082,
    "SR": 0.01,
    "SAR": 0.0034,
    "UR": 0.00263,
    "MUR": 0.00067,
}

MISSING_GRADE_FALLBACKS = {
    "MUR": ("UR", "SAR", "SR", "AR", "RR", "R"),
    "UR": ("SAR", "SR", "AR", "RR", "R"),
    "SAR": ("SR", "AR", "RR", "R"),
}


@dataclass(frozen=True)
class PackOpenPlan:
    """게이트 판정 결과: 몇 팩을 열 수 있고 RP 를 얼마나 쓰는지."""

    allowed_count: int
    rp_cost: int
    free_quota_used: int
    error: str | None = None  # 'no_free_left' | 'insufficient_rp'


def plan_pack_open(
    pack_type: str,
    requested: int,
    *,
    free_used_today: int,
    rp_balance: int,
    pay_with_rp: bool = False,
) -> PackOpenPlan:
    requested = clamp_pack_count(requested)

    if normalize_pack_type(pack_type) == "premium":
        affordable = rp_balance // PREMIUM_PACK_RP
        allowed = min(requested, affordable)
        if allowed <= 0:
            return PackOpenPlan(0, 0, 0, error="insufficient_rp")
        return PackOpenPlan(allowed, allowed * PREMIUM_PACK_RP, 0)

    if pay_with_rp:
        affordable = rp_balance // EXTRA_FREE_PACK_RP
        allowed = min(requested, affordable)
        if allowed <= 0:
            return PackOpenPlan(0, 0, 0, error="insufficient_rp")
        return PackOpenPlan(allowed, allowed * EXTRA_FREE_PACK_RP, 0)

    remaining = max(0, DAILY_FREE_PACKS - max(0, free_used_today))
    allowed = min(requested, remaining)
    if allowed <= 0:
        return PackOpenPlan(0, 0, 0, error="no_free_left")
    return PackOpenPlan(allowed, 0, allowed)


def normalize_pack_type(value: str | None) -> str:
    raw = (value or "free").strip().lower()
    if raw in {"premium", "bp", "paid"}:
        return "premium"
    return "free"


def normalize_grade(value: str | None) -> str:
    grade = (value or "R").strip().upper()
    return grade if grade in GRADE_ORDER else "R"


def grade_rank(grade: str | None) -> int:
    return GRADE_ORDER.get(normalize_grade(grade), 0)


def slot_grade(pack_type: str, slot_num: int) -> str:
    table = PREMIUM_PACK_SLOT_GRADES if normalize_pack_type(pack_type) == "premium" else FREE_PACK_SLOT_GRADES
    return table.get(slot_num, "R")


def select_lucky_grade(pack_type: str) -> str:
    probs = PREMIUM_PACK_LUCKY_PROBS if normalize_pack_type(pack_type) == "premium" else FREE_PACK_LUCKY_PROBS
    roll = random.random()
    cumulative = 0.0
    for grade, prob in probs.items():
        cumulative += prob
        if roll < cumulative:
            return grade
    return next(iter(probs))


def fallback_grades(grade: str) -> tuple[str, ...]:
    grade = normalize_grade(grade)
    return (grade, *MISSING_GRADE_FALLBACKS.get(grade, ()))


def clamp_pack_count(count: int | None) -> int:
    return max(1, min(int(count or 1), MAX_BATCH_PACKS))


def sort_key_for_best(card: Mapping[str, object] | object) -> tuple[int, float, str]:
    if isinstance(card, Mapping):
        grade = card.get("grade")
        price = card.get("market_price_usd") or 0
        name = str(card.get("card_name") or "")
    else:
        grade = getattr(card, "grade", None)
        price = getattr(card, "market_price_usd", None) or 0
        name = str(getattr(card, "card_name", "") or "")
    try:
        price_float = float(price)
    except (TypeError, ValueError):
        price_float = 0.0
    return grade_rank(str(grade or "")), price_float, name
