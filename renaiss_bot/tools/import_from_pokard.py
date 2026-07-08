"""POKARD (card universe) + Renaiss Index (prices) → renaiss_catalog_cards.

두 API 를 합쳐 카탈로그를 채운다:
  1. POKARD /cards 로 카드 목록(이름/세트/번호/등급/이미지) 페이지네이션
  2. 카드마다 Renaiss Index API 로 시세 + 7일 변동 조회
  3. renaiss_catalog_cards 에 upsert

⚠️ 외부 크롤 신중 모드: 기본은 소량 검증 배치. 전량은 --max-cards 크게 + 사장님 컨펌 후.
⚠️ POKARD 는 VM IP 만 허용(로컬 403). VM 에서 실행.

CLI:
    python -m renaiss_bot.tools.import_from_pokard --dry-run --max-cards 20
    python -m renaiss_bot.tools.import_from_pokard --series base1 --max-cards 200 --sleep 0.5
환경변수: POKARD_API_BASE_URL, POKARD_API_KEY, RENAISS_API_BASE_URL, DATABASE_URL
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging

from renaiss_bot.services.models import CardIdentity
from renaiss_bot.services.pokard import PokardError, list_cards, make_session
from renaiss_bot.services.pricing import fetch_price

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("import_from_pokard")

# rarity → 르네이스 8등급(+MUR). 메인 임포터의 축약본.
RARITY_TO_GRADE = {
    "Common": "C",
    "Uncommon": "U",
    "Rare": "R", "Rare BREAK": "R", "Promo": "R",
    "Rare Holo": "RR", "Double Rare": "RR",
    "Illustration Rare": "AR", "Trainer Gallery Rare Holo": "AR",
    "Shiny Rare": "AR", "Rare Shiny": "AR",
    "Rare Holo EX": "SR", "Rare Holo GX": "SR", "Rare Holo V": "SR",
    "Rare Holo VMAX": "SR", "Rare Holo VSTAR": "SR", "Ultra Rare": "SR",
    "Special Illustration Rare": "SAR", "Rare Ultra": "SAR",
    "Rare Secret": "UR", "Rare Rainbow": "UR", "Hyper Rare": "UR",
}


def map_grade(rarity: str | None) -> str:
    return RARITY_TO_GRADE.get((rarity or "").strip(), "R")


def _card_row(pkd: dict, price) -> dict:
    set_id = str(pkd.get("set_id") or "")
    number = str(pkd.get("number") or "")
    name = str(pkd.get("name") or pkd.get("card_name") or "").strip()
    rarity = pkd.get("rarity")
    grade = map_grade(rarity)
    local_id = pkd.get("card_id") or f"pokemon_tcg:{name}:{grade}:{set_id}:{number}"
    return {
        "local_card_id": local_id,
        "category": "pokemon_tcg",
        "card_name": name,
        "grade": grade,
        "set_code": set_id,
        "set_name": str(pkd.get("set_name") or set_id),
        "collector_number": number,
        "rarity": rarity or "",
        "language": "English",
        "image_url": pkd.get("image_url") or (price.image_url if price else None),
        "market_price_usd": (price.fmv_usd if price else None),
        "metadata": {
            "pokard_card_id": pkd.get("card_id"),
            "original_rarity": rarity,
            "change_7d_pct": (price.change_7d_pct if price else None),
            "price_confidence": (price.confidence if price else None),
            "price_status": (price.status if price else "missing"),
        },
    }


_INSERT = """
INSERT INTO renaiss_catalog_cards (
    local_card_id, category, card_name, grade, set_code, set_name,
    collector_number, rarity, language, image_url, market_price_usd, metadata
) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12::jsonb)
ON CONFLICT (local_card_id) DO UPDATE SET
    card_name=EXCLUDED.card_name, grade=EXCLUDED.grade, set_code=EXCLUDED.set_code,
    set_name=EXCLUDED.set_name, collector_number=EXCLUDED.collector_number,
    rarity=EXCLUDED.rarity, image_url=EXCLUDED.image_url,
    market_price_usd=EXCLUDED.market_price_usd, metadata=EXCLUDED.metadata,
    is_active=TRUE, updated_at=now()
"""


async def run(args) -> int:
    from renaiss_bot.database.connection import close_db, get_db
    from renaiss_bot.database.schema import create_tables

    pool = None
    if not args.dry_run:
        pool = await get_db()
        await create_tables(pool)

    fetched = priced = written = 0
    session = make_session()
    try:
        offset = 0
        while fetched < args.max_cards:
            page_limit = min(args.limit, args.max_cards - fetched)
            try:
                data = await list_cards(
                    session,
                    series=[args.series] if args.series else None,
                    gen=args.gen,
                    supertype="Pokemon",
                    limit=page_limit,
                    offset=offset,
                )
            except PokardError as exc:
                log.error("POKARD fetch 실패: %s", exc)
                return 1
            cards = data.get("cards") or []
            if not cards:
                break
            for pkd in cards:
                fetched += 1
                name = pkd.get("name") or pkd.get("card_name") or ""
                identity = CardIdentity(
                    category="pokemon_tcg",
                    card_name=str(name),
                    set_code=str(pkd.get("set_id") or ""),
                    collector_number=str(pkd.get("number") or ""),
                )
                try:
                    price = await fetch_price(identity)
                except Exception as exc:
                    log.debug("price 실패 %s: %s", name, exc)
                    price = None
                if price and price.fmv_usd:
                    priced += 1
                row = _card_row(pkd, price)
                pval = f"${row['market_price_usd']:.2f}" if row["market_price_usd"] else "no-price"
                log.info("  %-32s %-4s %s", row["card_name"][:32], row["grade"], pval)
                if not args.dry_run:
                    async with pool.acquire() as conn:
                        await conn.execute(
                            _INSERT,
                            row["local_card_id"], row["category"], row["card_name"], row["grade"],
                            row["set_code"], row["set_name"], row["collector_number"], row["rarity"],
                            row["language"], row["image_url"], row["market_price_usd"],
                            json.dumps(row["metadata"], ensure_ascii=False),
                        )
                    written += 1
                await asyncio.sleep(args.sleep)  # rate-limit 안전
            offset += len(cards)
    finally:
        await session.close()
        if pool is not None:
            await close_db()

    log.info("완료: fetched=%d priced=%d written=%d (dry_run=%s)", fetched, priced, written, args.dry_run)
    return 0


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--dry-run", action="store_true", help="DB 미기록, fetch+출력만")
    p.add_argument("--max-cards", type=int, default=20, help="가져올 최대 카드 수 (기본 20=검증)")
    p.add_argument("--limit", type=int, default=50, help="페이지당 크기")
    p.add_argument("--series", default=None, help="세트 필터 (예: base1)")
    p.add_argument("--gen", type=int, default=None, help="세대 필터")
    p.add_argument("--sleep", type=float, default=0.5, help="카드당 sleep 초 (크롤 예의)")
    args = p.parse_args()
    return asyncio.run(run(args))


if __name__ == "__main__":
    raise SystemExit(main())
