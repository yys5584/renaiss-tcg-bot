"""Import Renaiss catalog cards from a JSON file.

Expected input: a JSON array of objects. Common key aliases are accepted:
name/card_name/title, id/local_card_id/card_id, set/set_code, number/collector_number,
price_usd/fmv_usd/market_price_usd.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from renaiss_bot.database.connection import close_db, get_db
from renaiss_bot.database.schema import create_tables
from renaiss_bot.services.pack_rules import normalize_grade


def _first(payload: dict[str, Any], *keys: str, default: Any = None) -> Any:
    for key in keys:
        value = payload.get(key)
        if value not in (None, ""):
            return value
    return default


def _float_or_none(value: Any) -> float | None:
    try:
        if value in (None, ""):
            return None
        return float(str(value).replace("$", "").replace(",", "").strip())
    except (TypeError, ValueError):
        return None


def _normalize_row(raw: dict[str, Any], category: str) -> dict[str, Any]:
    card_name = str(_first(raw, "card_name", "name", "title", "display_name", default="")).strip()
    if not card_name:
        raise ValueError("card_name/name is required")
    local_card_id = str(
        _first(raw, "local_card_id", "id", "asset_id", "card_id", default=f"{category}:{card_name}")
    ).strip()
    metadata = dict(raw.get("metadata") or {})
    metadata.setdefault("source_payload", raw)
    return {
        "local_card_id": local_card_id,
        "category": str(raw.get("category") or category),
        "card_name": card_name,
        "grade": normalize_grade(str(_first(raw, "grade", "rarity", default="R"))),
        "set_code": _first(raw, "set_code", "set", "series_code", "collection"),
        "set_name": _first(raw, "set_name", "series_name", "collection_name"),
        "collector_number": _first(raw, "collector_number", "number", "card_number", "item_number"),
        "rarity": _first(raw, "rarity", "grade"),
        "language": str(raw.get("language") or "Japanese"),
        "image_url": _first(raw, "image_url", "image", "thumbnail_url", "display_image_url"),
        "market_price_usd": _float_or_none(
            _first(raw, "market_price_usd", "fmv_usd", "price_usd", "value_usd")
        ),
        "metadata": metadata,
    }


async def import_cards(path: Path, category: str) -> int:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise ValueError("JSON root must be a list")

    rows = [_normalize_row(item, category) for item in payload if isinstance(item, dict)]
    pool = await get_db()
    await create_tables(pool)
    async with pool.acquire() as conn:
        for row in rows:
            await conn.execute(
                """
                INSERT INTO renaiss_catalog_cards (
                    local_card_id, category, card_name, grade, set_code, set_name,
                    collector_number, rarity, language, image_url, market_price_usd, metadata
                )
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12::jsonb)
                ON CONFLICT (local_card_id)
                DO UPDATE SET
                    category = EXCLUDED.category,
                    card_name = EXCLUDED.card_name,
                    grade = EXCLUDED.grade,
                    set_code = EXCLUDED.set_code,
                    set_name = EXCLUDED.set_name,
                    collector_number = EXCLUDED.collector_number,
                    rarity = EXCLUDED.rarity,
                    language = EXCLUDED.language,
                    image_url = EXCLUDED.image_url,
                    market_price_usd = EXCLUDED.market_price_usd,
                    metadata = EXCLUDED.metadata,
                    is_active = TRUE,
                    updated_at = now()
                """,
                row["local_card_id"],
                row["category"],
                row["card_name"],
                row["grade"],
                row["set_code"],
                row["set_name"],
                row["collector_number"],
                row["rarity"],
                row["language"],
                row["image_url"],
                row["market_price_usd"],
                json.dumps(row["metadata"], ensure_ascii=False),
            )
    return len(rows)


async def main_async() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("path", type=Path)
    parser.add_argument("--category", default="one_piece_tcg")
    parser.add_argument("--env", default=".env")
    args = parser.parse_args()

    load_dotenv(dotenv_path=args.env)
    try:
        count = await import_cards(args.path, args.category)
        print(f"imported {count} catalog cards")
        return 0
    finally:
        await close_db()


def main() -> int:
    return asyncio.run(main_async())


if __name__ == "__main__":
    raise SystemExit(main())
