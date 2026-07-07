"""Daily price quiz: subject picking, answer options, streaks, share grid."""

from __future__ import annotations

import logging
import random
from dataclasses import dataclass
from datetime import date, timedelta

from renaiss_bot.services.card_pool import load_card_pool
from renaiss_bot.services.models import CardIdentity, RenaissPrice
from renaiss_bot.services.pricing import fetch_price

logger = logging.getLogger(__name__)

MIN_QUIZ_PRICE_USD = 5.0
_SUBJECT_ATTEMPTS = 8
_DISTRACTOR_FACTORS = (0.3, 0.45, 0.6, 1.6, 2.2, 3.2)


@dataclass(frozen=True)
class QuizSubject:
    card: CardIdentity
    price: RenaissPrice

    @property
    def correct_price_usd(self) -> float:
        return float(self.price.fmv_usd or 0)


def round_price(value: float) -> float:
    """가격을 '있을 법한' 단위로 반올림 (보기 4개가 자연스럽게 보이도록)."""
    if value <= 0:
        return 0.0
    if value < 10:
        return round(value, 1)
    if value < 100:
        return float(round(value))
    if value < 1000:
        return float(round(value / 5) * 5)
    return float(round(value / 50) * 50)


def build_price_options(
    correct: float,
    *,
    rng: random.Random | None = None,
) -> tuple[list[float], int]:
    """정답 1개 + 오답 3개를 섞어서 (보기 리스트, 정답 인덱스) 반환."""
    rng = rng or random.Random()
    correct_rounded = round_price(correct)
    options: list[float] = [correct_rounded]

    factors = list(_DISTRACTOR_FACTORS)
    rng.shuffle(factors)
    for factor in factors:
        candidate = round_price(correct * factor)
        if candidate <= 0:
            continue
        if all(abs(candidate - existing) > max(0.5, correct_rounded * 0.08) for existing in options):
            options.append(candidate)
        if len(options) >= 4:
            break

    # 극단값에서 팩터가 겹치면 배수로 강제 생성
    bump = 2.0
    while len(options) < 4:
        candidate = round_price(correct_rounded * bump)
        if candidate > 0 and all(abs(candidate - existing) > 0.5 for existing in options):
            options.append(candidate)
        bump += 1.0

    rng.shuffle(options)
    return options, options.index(correct_rounded)


def format_price_option(value: float) -> str:
    if value < 10:
        return f"${value:,.2f}".rstrip("0").rstrip(".")
    return f"${value:,.0f}"


def compute_streak(correct_dates: set[date], today: date) -> int:
    """오늘 포함 연속 정답 일수. 오늘 정답이 없으면 0 (놓쳐도 페널티는 없음 — 명예만 리셋)."""
    if today not in correct_dates:
        return 0
    streak = 0
    cursor = today
    while cursor in correct_dates:
        streak += 1
        cursor -= timedelta(days=1)
    return streak


def format_distribution(
    options: list[float],
    counts: list[int],
    correct_index: int,
    *,
    max_bar: int = 8,
) -> list[str]:
    """Wordle 식 정답 분포 그리드 (정답 줄은 🟩, 나머지는 ⬜)."""
    peak = max(counts) if counts and max(counts) > 0 else 1
    lines = []
    for index, value in enumerate(options):
        count = counts[index] if index < len(counts) else 0
        bar_length = round(count / peak * max_bar) if count > 0 else 0
        block = "🟩" if index == correct_index else "⬜"
        bar = block * max(bar_length, 1 if count > 0 else 0)
        marker = " ✅" if index == correct_index else ""
        lines.append(f"{format_price_option(value)}{marker} {bar} {count}")
    return lines


async def pick_quiz_subject(category: str = "pokemon_tcg") -> QuizSubject | None:
    """시세가 확실한 카드만 출제 (베타 API 빈 데이터 방어)."""
    pool, _source = await load_card_pool(None, category)
    priced = [
        card
        for card in pool
        if (card.market_price_usd or 0) >= MIN_QUIZ_PRICE_USD
    ]
    candidates = priced or [card for card in pool if (card.market_price_usd or 0) > 0]
    if not candidates:
        candidates = list(pool)
    if not candidates:
        return None

    rng = random.Random()
    for card in rng.sample(candidates, min(_SUBJECT_ATTEMPTS, len(candidates))):
        try:
            price = await fetch_price(card)
        except Exception as exc:
            logger.debug("Quiz price fetch failed for %s: %s", card.card_name, exc)
            continue
        if price.fmv_usd is not None and price.fmv_usd >= MIN_QUIZ_PRICE_USD and price.status in {"exact", "candidate"}:
            return QuizSubject(card=card, price=price)

    # Index 시세를 못 받으면 카탈로그 시세로 폴백 (그래도 출제는 매일 나가야 한다)
    fallback_cards = [card for card in candidates if (card.market_price_usd or 0) >= MIN_QUIZ_PRICE_USD]
    if fallback_cards:
        card = rng.choice(fallback_cards)
        price = RenaissPrice(
            status="candidate",
            source="catalog-fallback",
            fmv_usd=float(card.market_price_usd or 0),
            image_url=card.image_url,
            market_status="catalog",
        )
        return QuizSubject(card=card, price=price)
    return None
