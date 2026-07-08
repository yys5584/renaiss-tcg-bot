"""Season-1 style spawn: a card appears in the room, valued by market price band.

Official room spawns continuously; rare/grail spawns get a loud announcement.
Value band replaces the old 'shiny' hook — excitement axis is real price.
"""

from __future__ import annotations

import random
from dataclasses import dataclass

from renaiss_bot.services.card_pool import load_card_pool
from renaiss_bot.services.models import CardIdentity

# ── 시세 밴드 (수치 초안 — 운영 보며 조절) ──
# weight = 등장 빈도, min_usd = 이 밴드의 하한
BANDS = ("common", "rare", "grail")
BAND_MIN_USD = {"common": 0.0, "rare": 100.0, "grail": 500.0}
BAND_WEIGHT_NORMAL = {"common": 80, "rare": 17, "grail": 3}
# 강스(버스트) 중에는 레어·그레일 가중치가 확 오른다
BAND_WEIGHT_RUSH = {"common": 55, "rare": 33, "grail": 12}


@dataclass(frozen=True)
class Spawn:
    card: CardIdentity
    band: str
    market_usd: float

    @property
    def is_headline(self) -> bool:
        return self.band in {"rare", "grail"}


def roll_band(*, rush: bool = False, rng: random.Random | None = None) -> str:
    rng = rng or random.Random()
    weights = BAND_WEIGHT_RUSH if rush else BAND_WEIGHT_NORMAL
    return rng.choices(BANDS, weights=[weights[b] for b in BANDS], k=1)[0]


def _card_value(card: CardIdentity) -> float:
    try:
        return float(card.market_price_usd or 0)
    except (TypeError, ValueError):
        return 0.0


def cards_in_band(pool: list[CardIdentity], band: str) -> list[CardIdentity]:
    """해당 밴드 시세 구간의 카드. grail은 하한 이상, common은 rare 하한 미만."""
    lo = BAND_MIN_USD[band]
    if band == "grail":
        return [c for c in pool if _card_value(c) >= lo]
    hi = BAND_MIN_USD["rare"] if band == "common" else BAND_MIN_USD["grail"]
    return [c for c in pool if lo <= _card_value(c) < hi]


def pick_spawn_card(pool: list[CardIdentity], band: str, *, rng: random.Random | None = None) -> CardIdentity | None:
    rng = rng or random.Random()
    candidates = cards_in_band(pool, band)
    if not candidates:
        # 밴드에 카드 없으면 한 단계 아래로 폴백 (grail→rare→common)
        order = {"grail": "rare", "rare": "common"}
        fallback = order.get(band)
        if fallback:
            return pick_spawn_card(pool, fallback, rng=rng)
        return rng.choice(pool) if pool else None
    return rng.choice(candidates)


async def roll_spawn(category: str = "pokemon_tcg", *, rush: bool = False) -> Spawn | None:
    pool, _source = await load_card_pool(None, category)
    if not pool:
        return None
    rng = random.Random()
    band = roll_band(rush=rush, rng=rng)
    card = pick_spawn_card(pool, band, rng=rng)
    if card is None:
        return None
    value = _card_value(card)
    # 실제 뽑힌 카드 시세로 밴드 재보정 (폴백으로 밴드가 어긋났을 수 있음)
    if value >= BAND_MIN_USD["grail"]:
        band = "grail"
    elif value >= BAND_MIN_USD["rare"]:
        band = "rare"
    else:
        band = "common"
    return Spawn(card=card, band=band, market_usd=value)
