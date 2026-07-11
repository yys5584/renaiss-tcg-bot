"""Card pool loading and local pack selection for the standalone Renaiss bot."""

from __future__ import annotations

import json
import logging
import math
import os
import random
from collections.abc import Iterable, Mapping
from typing import Any

from renaiss_bot.database.connection import get_db
from renaiss_bot.services.models import CardIdentity
from renaiss_bot.services.pack_rules import (
    CARDS_PER_PACK,
    HIT_CARD_GRADES,
    fallback_grades,
    normalize_grade,
    select_lucky_grade,
    slot_grade,
    sort_key_for_best,
)

logger = logging.getLogger(__name__)

_PRICE_KEYS = (
    "psa10_market_usd",
    "psa10_usd",
    "psa10_price_usd",
    "psa_10_market_usd",
    "psa_10_usd",
    "psa_10_price_usd",
    "graded_psa10_usd",
    "gem_mint_10_usd",
    "market_usd",
    "price_usd",
    "value_usd",
)


def _metadata(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            return {}
    return {}


def _float_or_none(value: Any) -> float | None:
    try:
        if value is None or value == "" or isinstance(value, bool):
            return None
        number = float(value)
        return number if math.isfinite(number) and number > 0 else None
    except (TypeError, ValueError, OverflowError):
        return None


def _extract_price_usd(metadata: Mapping[str, Any]) -> float | None:
    for key in _PRICE_KEYS:
        amount = _float_or_none(metadata.get(key))
        if amount is not None:
            return amount
    nested = metadata.get("price") or metadata.get("psa10_price")
    if isinstance(nested, Mapping):
        return _extract_price_usd(nested)
    return None


def _number_from_metadata(meta: Mapping[str, Any], fallback: str = "") -> str:
    for key in ("number", "collector_number", "card_number"):
        value = meta.get(key)
        if value:
            return str(value)
    return fallback


def _card_name(row: Mapping[str, Any]) -> str:
    meta = _metadata(row.get("metadata"))
    return str(meta.get("en_name") or row.get("species_name") or row.get("card_name") or "Unknown Card")


def _row_to_card(row: Mapping[str, Any], *, category: str = "pokemon_tcg") -> CardIdentity:
    meta = _metadata(row.get("metadata"))
    card_name = _card_name(row)
    grade = normalize_grade(str(row.get("grade") or meta.get("grade") or "R"))
    set_code = str(row.get("series_code") or meta.get("set_id") or meta.get("series") or "")
    collector_number = _number_from_metadata(meta, str(row.get("illustration_id") or ""))
    image_url = row.get("image_url") or meta.get("display_image_url") or meta.get("pokard_image_url")
    return CardIdentity(
        category=category,
        card_name=card_name,
        set_code=set_code,
        set_name=str(row.get("series_name") or meta.get("set_name") or ""),
        collector_number=collector_number,
        language=str(meta.get("language") or "Japanese"),
        rarity=str(meta.get("original_rarity") or grade),
        grade=grade,
        local_card_id=str(row.get("card_id") or meta.get("card_id") or ""),
        image_url=str(image_url) if image_url else None,
        species_id=int(row["species_id"]) if row.get("species_id") is not None else None,
        species_name=str(row.get("species_name") or ""),
        already_owned=bool(row.get("already_owned")),
        market_price_usd=_extract_price_usd(meta),
        metadata=meta,
    )


def _sample_pokemon_cards() -> list[CardIdentity]:
    raw = [
        ("sample-charizard-sar", "Charizard ex", "SV4a", "349/190", "SAR", "SAR", 430.0),
        ("sample-pikachu-ar", "Pikachu", "SV", "173/165", "AR", "AR", 128.0),
        ("sample-mewtwo-sr", "Mewtwo ex", "SV", "RR", "RR", "RR", 74.0),
        ("sample-rayquaza-ur", "Rayquaza VMAX", "S7R", "083/067", "UR", "UR", 260.0),
        ("sample-eevee-ar", "Eevee", "SV5a", "078/066", "AR", "AR", 55.0),
        ("sample-lucario-sr", "Lucario VSTAR", "SLL", "226/S-P", "SR", "SR", 90.0),
        ("sample-gardevoir-sar", "Gardevoir ex", "SV1S", "101/078", "SAR", "SAR", 180.0),
        ("sample-snorlax-r", "Snorlax", "SV2a", "143/165", "R", "R", 18.0),
        ("sample-gyarados-rr", "Gyarados ex", "SV1S", "014/078", "RR", "RR", 34.0),
        ("sample-dragonite-r", "Dragonite", "S7R", "050/067", "R", "R", 22.0),
        ("sample-mew-ur", "Mew ex", "SV2a", "208/165", "UR", "UR", 240.0),
        ("sample-greninja-sar", "Greninja ex", "SV5a", "090/066", "SAR", "SAR", 210.0),
    ]
    return [
        CardIdentity(
            category="pokemon_tcg",
            local_card_id=card_id,
            card_name=name,
            set_code=set_code,
            collector_number=number,
            rarity=rarity,
            grade=grade,
            market_price_usd=price,
            metadata={"market_usd": price, "source": "sample"},
        )
        for card_id, name, set_code, number, rarity, grade, price in raw
    ]


def _sample_one_piece_cards() -> list[CardIdentity]:
    raw = [
        ("sample-op-luffy-sec", "Monkey.D.Luffy", "OP05", "OP05-119", "SEC", "SAR", 390.0),
        ("sample-op-nami-sp", "Nami", "OP01", "OP01-016", "SP", "SR", 180.0),
        ("sample-op-zoro-sec", "Roronoa Zoro", "OP06", "OP06-118", "SEC", "SAR", 220.0),
        ("sample-op-law-sr", "Trafalgar Law", "OP05", "OP05-069", "SR", "SR", 95.0),
        ("sample-op-boa-sp", "Boa Hancock", "OP07", "OP07-051", "SP", "SR", 150.0),
        ("sample-op-shanks-manga", "Shanks", "OP01", "OP01-120", "MANGA", "MUR", 900.0),
        ("sample-op-ace-manga", "Portgas.D.Ace", "OP02", "OP02-013", "MANGA", "UR", 640.0),
        ("sample-op-yamato-sec", "Yamato", "OP01", "OP01-121", "SEC", "SAR", 260.0),
        ("sample-op-robin-r", "Nico Robin", "OP09", "OP09-062", "R", "R", 18.0),
        ("sample-op-sanji-r", "Sanji", "OP06", "OP06-119", "R", "R", 24.0),
    ]
    return [
        CardIdentity(
            category="one_piece_tcg",
            local_card_id=card_id,
            card_name=name,
            set_code=set_code,
            collector_number=number,
            rarity=rarity,
            grade=grade,
            market_price_usd=price,
            metadata={"market_usd": price, "source": "sample"},
        )
        for card_id, name, set_code, number, rarity, grade, price in raw
    ]


def sample_cards(category: str) -> list[CardIdentity]:
    if category == "pokemon_tcg":
        return _sample_pokemon_cards()
    if category == "one_piece_tcg":
        return _sample_one_piece_cards()
    return []


def catalog_row_to_card(row: Mapping[str, Any]) -> CardIdentity:
    meta = _metadata(row.get("metadata"))
    price = _float_or_none(row.get("market_price_usd")) or _extract_price_usd(meta)
    return CardIdentity(
        category=str(row.get("category") or "other_renaiss_cards"),
        local_card_id=str(row.get("local_card_id") or ""),
        card_name=str(row.get("card_name") or "Unknown Card"),
        set_code=str(row.get("set_code") or meta.get("set_code") or ""),
        set_name=str(row.get("set_name") or meta.get("set_name") or ""),
        collector_number=str(row.get("collector_number") or meta.get("collector_number") or ""),
        language=str(row.get("language") or meta.get("language") or "Japanese"),
        rarity=str(row.get("rarity") or meta.get("rarity") or row.get("grade") or "R"),
        grade=normalize_grade(str(row.get("grade") or meta.get("grade") or "R")),
        image_url=str(row.get("image_url") or meta.get("image_url") or "") or None,
        already_owned=bool(row.get("already_owned")),
        market_price_usd=price,
        metadata=meta,
    )


async def _load_catalog_pool(user_id: int | None, category: str) -> tuple[list[CardIdentity], str] | None:
    if os.getenv("RENAISS_SKIP_DB", "").strip().lower() in {"1", "true", "yes"}:
        return None
    if not os.getenv("DATABASE_URL"):
        return None
    try:
        pool = await get_db()
        async with pool.acquire() as conn:
            owned_ids: set[str] = set()
            if user_id is not None:
                try:
                    owned_rows = await conn.fetch(
                        """
                        SELECT local_card_id
                        FROM renaiss_user_cards
                        WHERE user_id = $1 AND category = $2
                        """,
                        user_id,
                        category,
                    )
                    owned_ids = {str(row["local_card_id"]) for row in owned_rows}
                except Exception:
                    owned_ids = set()
            rows = await conn.fetch(
                """
                SELECT
                    local_card_id,
                    category,
                    card_name,
                    grade,
                    set_code,
                    set_name,
                    collector_number,
                    rarity,
                    language,
                    image_url,
                    market_price_usd,
                    metadata
                FROM renaiss_catalog_cards
                WHERE category = $1
                  AND is_active = TRUE
                  AND grade = ANY($2::text[])
                ORDER BY grade DESC, card_name ASC
                LIMIT 5000
                """,
                category,
                list(HIT_CARD_GRADES),
            )
        cards = []
        for row in rows:
            payload = dict(row)
            payload["already_owned"] = str(payload.get("local_card_id") or "") in owned_ids
            cards.append(catalog_row_to_card(payload))
        if len(cards) >= CARDS_PER_PACK:
            return cards, "renaiss_catalog"
    except Exception as exc:
        logger.info("Renaiss catalog pool load skipped category=%s: %s", category, exc)
    return None


async def load_card_pool(user_id: int | None, category: str) -> tuple[list[CardIdentity], str]:
    catalog = await _load_catalog_pool(user_id, category)
    if catalog is not None:
        return catalog

    if os.getenv("RENAISS_SKIP_DB", "").strip().lower() in {"1", "true", "yes"}:
        return sample_cards(category), "sample"
    if not os.getenv("DATABASE_URL"):
        return sample_cards(category), "sample"
    if category != "pokemon_tcg":
        return [], "unavailable"

    try:
        pool = await get_db()
        async with pool.acquire() as conn:
            owned_ids: set[str] = set()
            if user_id is not None:
                try:
                    owned_rows = await conn.fetch(
                        """
                        SELECT local_card_id
                        FROM renaiss_user_cards
                        WHERE user_id = $1 AND category = 'pokemon_tcg'
                        """,
                        user_id,
                    )
                    owned_ids = {str(row["local_card_id"]) for row in owned_rows}
                except Exception:
                    owned_ids = set()
            rows = await conn.fetch(
                """
                SELECT
                    c.card_id,
                    c.species_id,
                    c.species_name,
                    c.grade,
                    c.series_code,
                    c.series_name,
                    c.illustration_id,
                    c.image_url,
                    c.type1,
                    c.type2,
                    c.metadata
                FROM cards c
                WHERE c.is_active = TRUE
                  AND c.card_type = 'pokemon'
                  AND c.grade = ANY($1::text[])
                ORDER BY c.grade DESC, c.card_id ASC
                LIMIT 5000
                """,
                list(HIT_CARD_GRADES),
            )
        cards = []
        for row in rows:
            payload = dict(row)
            payload["already_owned"] = str(payload.get("card_id") or "") in owned_ids
            cards.append(_row_to_card(payload, category=category))
        if len(cards) >= CARDS_PER_PACK:
            return cards, "database"
        logger.warning("Renaiss DB card pool too small: %s", len(cards))
    except Exception as exc:
        logger.info("Renaiss card pool DB load skipped: %s", exc)

    return [], "unavailable"


def _weighted_pick(candidates: Iterable[CardIdentity], used_ids: set[str], used_species: set[int]) -> CardIdentity | None:
    available = [card for card in candidates if card.local_card_id not in used_ids]
    if not available:
        return None
    preferred = [
        card
        for card in available
        if card.species_id is None or card.species_id not in used_species
    ]
    weighted = preferred or available
    weights = []
    for card in weighted:
        price = _float_or_none(card.market_price_usd) or 0.0
        weight = (1.0 if not card.already_owned else 0.5) * (
            1.0 + min(price, 500.0) / 2000.0
        )
        weights.append(max(0.01, weight))
    return random.choices(weighted, weights=weights, k=1)[0]


def pick_card_for_grade(pool: list[CardIdentity], grade: str, used_ids: set[str], used_species: set[int]) -> CardIdentity | None:
    for candidate_grade in fallback_grades(grade):
        candidates = [card for card in pool if normalize_grade(card.grade) == candidate_grade]
        selected = _weighted_pick(candidates, used_ids, used_species)
        if selected:
            return selected
    return _weighted_pick(pool, used_ids, used_species)


def build_pack(pool: list[CardIdentity], category: str, pack_type: str) -> tuple[list[CardIdentity], str]:
    if not pool:
        raise ValueError(f"card pool is unavailable for {category}")
    pack_pool = pool
    used_ids: set[str] = set()
    used_species: set[int] = set()
    cards: list[CardIdentity] = []
    lucky_grade = "R"

    for slot_num in range(1, CARDS_PER_PACK + 1):
        grade = slot_grade(pack_type, slot_num)
        if grade == "LUCKY":
            grade = select_lucky_grade(pack_type)
            lucky_grade = grade
        selected = pick_card_for_grade(pack_pool, grade, used_ids, used_species)
        if selected is None:
            # A small pilot catalog may not contain ten unique cards. Fill the
            # promised slot count with a repeat instead of silently shrinking a pack.
            selected = _weighted_pick(pack_pool, set(), set())
        if selected is None:
            continue
        used_ids.add(selected.local_card_id)
        if selected.species_id is not None:
            used_species.add(selected.species_id)
        cards.append(selected)

    if not cards:
        cards = sample_cards(category)[:CARDS_PER_PACK]
    return cards, lucky_grade


def best_card(cards: list[CardIdentity]) -> CardIdentity:
    return max(cards, key=sort_key_for_best)
