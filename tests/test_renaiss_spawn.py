"""Renaiss 스폰 시세 밴드 단위 테스트 (DB/telegram 불필요)."""

from __future__ import annotations

import random

from renaiss_bot.services.models import CardIdentity
from renaiss_bot.services.spawn import (
    BAND_MIN_USD,
    cards_in_band,
    pick_spawn_card,
    roll_band,
)


def _card(name, usd):
    return CardIdentity(category="pokemon_tcg", card_name=name, grade="R", market_price_usd=usd)


POOL = [
    _card("cheap-a", 5.0),
    _card("cheap-b", 40.0),
    _card("mid-a", 120.0),
    _card("mid-b", 300.0),
    _card("grail-a", 600.0),
    _card("grail-b", 1200.0),
]


def test_cards_in_band_common():
    got = {c.card_name for c in cards_in_band(POOL, "common")}
    assert got == {"cheap-a", "cheap-b"}


def test_cards_in_band_rare():
    got = {c.card_name for c in cards_in_band(POOL, "rare")}
    assert got == {"mid-a", "mid-b"}


def test_cards_in_band_grail():
    got = {c.card_name for c in cards_in_band(POOL, "grail")}
    assert got == {"grail-a", "grail-b"}


def test_bands_are_disjoint_and_cover():
    all_banded = []
    for band in ("common", "rare", "grail"):
        all_banded += [c.card_name for c in cards_in_band(POOL, band)]
    assert sorted(all_banded) == sorted(c.card_name for c in POOL)


def test_band_thresholds():
    assert BAND_MIN_USD["common"] == 0.0
    assert BAND_MIN_USD["rare"] == 100.0
    assert BAND_MIN_USD["grail"] == 500.0


def test_pick_spawn_card_in_band():
    rng = random.Random(1)
    card = pick_spawn_card(POOL, "grail", rng=rng)
    assert card is not None and card.market_price_usd >= 500


def test_pick_spawn_card_fallback_when_band_empty():
    # grail 없는 풀 → rare 로 폴백
    pool = [_card("only-mid", 150.0)]
    card = pick_spawn_card(pool, "grail", rng=random.Random(0))
    assert card is not None and card.card_name == "only-mid"


def test_roll_band_rush_shifts_toward_rare():
    # 통계적으로 rush 가 레어+그레일 비중을 높인다
    rng = random.Random(42)
    normal = [roll_band(rush=False, rng=rng) for _ in range(3000)]
    rush = [roll_band(rush=True, rng=rng) for _ in range(3000)]
    normal_rare = sum(1 for b in normal if b != "common")
    rush_rare = sum(1 for b in rush if b != "common")
    assert rush_rare > normal_rare


def test_roll_band_returns_valid():
    rng = random.Random(7)
    for _ in range(100):
        assert roll_band(rng=rng) in {"common", "rare", "grail"}
