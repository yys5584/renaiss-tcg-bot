"""Small, explicitly enabled preview data set for local UI verification."""

from __future__ import annotations

from typing import Any


# Deliberately outside Telegram's identifier range while remaining PostgreSQL BIGINT-safe.
PREVIEW_USER_ID = 9_000_000_000_000_000_001

_CARDS = [
    {
        "category": "pokemon_tcg",
        "local_card_id": "preview:sv8:238",
        "card_name": "Pikachu ex",
        "grade": "SAR",
        "set_code": "SV8",
        "set_name": "Surging Sparks",
        "collector_number": "238/191",
        "rarity": "Special Illustration Rare",
        "language": "English",
        "image_url": "https://images.pokemontcg.io/sv8/238_hires.png",
        "market_price_usd": 385.0,
        "renaiss_url": "https://index.renaissos.com/card/pokemon/surging-sparks/238-pikachu-ex",
        "source_kind": "catalog",
    },
    {
        "category": "pokemon_tcg",
        "local_card_id": "preview:sv3pt5:199",
        "card_name": "Charizard ex",
        "grade": "SAR",
        "set_code": "SV3.5",
        "set_name": "151",
        "collector_number": "199/165",
        "rarity": "Special Illustration Rare",
        "language": "English",
        "image_url": "https://images.pokemontcg.io/sv3pt5/199_hires.png",
        "market_price_usd": 1240.0,
        "source_kind": "catalog",
    },
    {
        "category": "pokemon_tcg",
        "local_card_id": "preview:sv4pt5:232",
        "card_name": "Mew ex",
        "grade": "SAR",
        "set_code": "SV4.5",
        "set_name": "Paldean Fates",
        "collector_number": "232/091",
        "rarity": "Special Illustration Rare",
        "language": "English",
        "image_url": "https://images.pokemontcg.io/sv4pt5/232_hires.png",
        "source_kind": "catalog",
    },
    {
        "category": "pokemon_tcg",
        "local_card_id": "preview:swsh9:154",
        "card_name": "Charizard V",
        "grade": "SR",
        "set_code": "SWSH9",
        "set_name": "Brilliant Stars",
        "collector_number": "154/172",
        "rarity": "Full Art",
        "language": "English",
        "image_url": "https://images.pokemontcg.io/swsh9/154_hires.png",
        "source_kind": "catalog",
    },
    {
        "category": "pokemon_tcg",
        "local_card_id": "preview:sv2:203",
        "card_name": "Magikarp",
        "grade": "AR",
        "set_code": "SV2",
        "set_name": "Paldea Evolved",
        "collector_number": "203/193",
        "rarity": "Illustration Rare",
        "language": "English",
        "image_url": "https://images.pokemontcg.io/sv2/203_hires.png",
        "source_kind": "catalog",
    },
]


def preview_collection(user_id: int | None, **filters: Any) -> dict[str, Any]:
    authenticated = user_id is not None
    owned_ids = {card["local_card_id"] for card in _CARDS[:3]} if authenticated else set()
    cards = []
    search = str(filters.get("search") or "").strip().casefold()
    grade = str(filters.get("grade") or "all").upper()
    owned_filter = str(filters.get("owned") or "all").lower()
    for source in _CARDS:
        card = dict(source)
        card["quantity"] = 1 if card["local_card_id"] in owned_ids else 0
        card["owned"] = card["quantity"] > 0
        if search and search not in " ".join(
            str(card.get(key) or "").casefold()
            for key in ("card_name", "set_name", "set_code", "collector_number")
        ):
            continue
        if grade != "ALL" and card["grade"] != grade:
            continue
        if authenticated and owned_filter in {"owned", "mine"} and not card["owned"]:
            continue
        if owned_filter == "missing" and card["owned"]:
            continue
        cards.append(card)
    owned_count = len(owned_ids)
    return {
        "ok": True,
        "available": True,
        "authenticated": authenticated,
        "summary": {
            "catalog_total": len(_CARDS),
            "sets_total": len({card["set_code"] for card in _CARDS}),
            "owned_in_catalog": owned_count,
            "archived_owned": 0,
            "total_quantity": owned_count,
            "completion_pct": round(owned_count / len(_CARDS) * 100, 1),
            "is_preview": True,
        },
        "cards": cards,
        "sets": [
            {"set_code": card["set_code"], "set_name": card["set_name"], "card_count": 1}
            for card in _CARDS
        ],
        "grades": ["R", "RR", "AR", "SR", "SAR", "UR", "MUR"],
        "page": 1,
        "per_page": 24,
        "total_filtered": len(cards),
        "has_more": False,
    }


def preview_leaderboard(
    current_user_id: int | None, *, period: str = "day"
) -> dict[str, Any]:
    if period == "all":
        rows = [
            (1, "아우로라", 21, 9820.0),
            (2, "민트", 14, 4030.5),
            (3, "페이퍼", 11, 2244.0),
            (4, "프리뷰 수집가", 6, 780.0),
        ]
    else:
        rows = [
            (1, "아우로라", 3, 1420.0),
            (2, "페이퍼", 2, 355.5),
            (3, "프리뷰 수집가", 1, 96.0),
            (3, "민트", 1, 88.0),
        ]
    return {
        "ok": True,
        "available": True,
        "metric": "lucky_catches",
        "period": period,
        "reset_timezone": "Asia/Seoul",
        "rows": [
            {
                "rank": rank,
                "display_name": name,
                "lucky_catches": count,
                "caught_value_usd": value,
                "is_me": bool(current_user_id and name == "프리뷰 수집가"),
            }
            for rank, name, count, value in rows
        ],
        "is_preview": True,
    }
