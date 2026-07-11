"""Stage every positive-priced PSA 10 Pokémon and One Piece API asset in PostgreSQL.

New candidates are inserted inactive. Existing rows keep their active state, so a
catalog discovery sync cannot silently change the live season pool.
"""

from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import time
from typing import Any
from urllib.parse import urlparse
import xml.etree.ElementTree as ET

import aiohttp
from dotenv import dotenv_values

from renaiss_bot.database.connection import close_db, get_db
from renaiss_bot.database.schema import create_tables
from renaiss_bot.services.client import _api_base, _headers
from renaiss_bot.services.spawn import grade_for_price
from renaiss_bot.tools.prepare_database import database_mutation_target_issue

SITEMAP_URL = "https://index.renaissos.com/sitemap.xml"
ALLOWED_ENV_KEYS = frozenset(
    {
        "DATABASE_URL",
        "RENAISS_EXPECTED_DATABASE_FINGERPRINT",
        "RENAISS_DB_SSL_INSECURE",
        "RENAISS_DB_SSL_CA_FILE",
        "RENAISS_DB_POOL_MAX",
        "RENAISS_DB_ACQUIRE_TIMEOUT_SECONDS",
        "RENAISS_API_BASE_URL",
        "RENAISS_API_KEY",
        "RENAISS_API_SECRET",
        "RENAISS_API_KEY_HEADER",
        "RENAISS_API_SECRET_HEADER",
    }
)


def load_explicit_environment(path: Path) -> None:
    values = dotenv_values(path)
    for key in ALLOWED_ENV_KEYS:
        value = values.get(key)
        if value not in {None, ""}:
            os.environ[key] = str(value)


def sitemap_set_slugs(payload: bytes) -> set[tuple[str, str]]:
    root = ET.fromstring(payload)
    namespace = {"s": "http://www.sitemaps.org/schemas/sitemap/0.9"}
    output: set[tuple[str, str]] = set()
    for node in root.findall("s:url", namespace):
        location = node.findtext("s:loc", default="", namespaces=namespace)
        parts = [part for part in urlparse(location).path.split("/") if part]
        if len(parts) == 4 and parts[0] == "card" and parts[1] in {"pokemon", "one-piece"}:
            output.add((parts[1], parts[2]))
    return output


def is_positive_psa10(row: dict[str, Any]) -> bool:
    price = row.get("priceUsdCents")
    return (
        str(row.get("company") or "").strip().upper() == "PSA"
        and str(row.get("grade") or "").strip().lower().startswith("10")
        and isinstance(price, (int, float))
        and not isinstance(price, bool)
        and price > 0
        and bool(str(row.get("href") or "").strip())
        and bool(str(row.get("imageUrl") or "").strip())
    )


def candidate_row(row: dict[str, Any], *, synced_at: datetime) -> dict[str, Any]:
    href = str(row["href"]).strip()
    game = str(row.get("game") or row.get("_endpoint_game") or "").strip()
    category = "one_piece_tcg" if game == "one-piece" else "pokemon_tcg"
    local_card_id = "renaiss-" + hashlib.sha256(href.encode("utf-8")).hexdigest()[:24]
    variation = str(row.get("variation") or "").strip()
    metadata = {
        "variation": variation,
        "market_grade": "PSA 10 Gem Mint",
        "price_status": "candidate",
        "price_source": "renaiss-set-listing-api",
        "price_confidence": row.get("confidence"),
        "price_asset_url": href,
        "price_updated_at": row.get("lastSaleAt"),
        "last_sale_at": row.get("lastSaleAt"),
        "delta_pct": row.get("deltaPct"),
        "catalog_candidate": True,
        "catalog_synced_at": synced_at.isoformat(),
        "source_payload": {
            "company": row.get("company"),
            "grade": row.get("grade"),
            "gradeLabel": row.get("gradeLabel"),
            "href": href,
        },
    }
    market_price_usd = round(float(row["priceUsdCents"]) / 100, 2)
    return {
        "local_card_id": local_card_id,
        "category": category,
        "card_name": str(row.get("name") or "Unknown Card"),
        "grade": grade_for_price(market_price_usd),
        "set_code": str(row.get("setCode") or ""),
        "set_name": str(row.get("setName") or ""),
        "collector_number": str(row.get("cardNumber") or ""),
        "rarity": variation or "PSA 10",
        "language": str(row.get("language") or "English"),
        "image_url": str(row.get("imageUrl") or ""),
        "market_price_usd": market_price_usd,
        "metadata": metadata,
    }


async def fetch_candidates(*, request_interval: float = 0.5) -> tuple[list[dict[str, Any]], dict]:
    timeout = aiohttp.ClientTimeout(total=30)
    synced_at = datetime.now(timezone.utc)
    status: dict[int, int] = {}
    raw_rows: list[dict[str, Any]] = []
    async with aiohttp.ClientSession(timeout=timeout, headers=_headers()) as session:
        cache_buster = int(time.time())
        async with session.get(f"{SITEMAP_URL}?catalog_sync={cache_buster}") as response:
            response.raise_for_status()
            sitemap = await response.read()
        sets = sorted(sitemap_set_slugs(sitemap))
        next_request = 0.0
        for index, (game, slug) in enumerate(sets, start=1):
            wait = next_request - time.monotonic()
            if wait > 0:
                await asyncio.sleep(wait)
            next_request = time.monotonic() + max(0.1, request_interval)
            async with session.get(f"{_api_base()}/v1/sets/{game}/{slug}") as response:
                status[response.status] = status.get(response.status, 0) + 1
                if response.status == 404:
                    continue
                response.raise_for_status()
                payload = await response.json(content_type=None)
            cards = payload.get("cards") if isinstance(payload, dict) else None
            if isinstance(cards, list):
                for item in cards:
                    if isinstance(item, dict):
                        enriched = dict(item)
                        enriched["_endpoint_game"] = game
                        raw_rows.append(enriched)
            if index % 100 == 0:
                print(json.dumps({"sets_done": index, "sets_total": len(sets), "rows": len(raw_rows)}))
    unique = {
        str(row.get("href") or "").strip(): row
        for row in raw_rows
        if is_positive_psa10(row)
    }
    candidates = [candidate_row(row, synced_at=synced_at) for row in unique.values()]
    candidates.sort(key=lambda row: (row["category"], row["local_card_id"]))
    summary = {
        "set_slugs": len(sets),
        "http_status": status,
        "raw_rows": len(raw_rows),
        "positive_psa10": len(candidates),
        "pokemon": sum(row["category"] == "pokemon_tcg" for row in candidates),
        "one_piece": sum(row["category"] == "one_piece_tcg" for row in candidates),
    }
    return candidates, summary


def staging_target_issue() -> str | None:
    """Return why staging must be refused, or None when the target is pinned.

    Staging mutates ``renaiss_catalog_cards``.  Reuse the same fail-closed
    fingerprint guard as ``prepare_database`` so ``--apply`` cannot write to an
    unpinned database even if the operator forgot the digest.
    """
    return database_mutation_target_issue(os.getenv("DATABASE_URL", ""))


async def stage_candidates(rows: list[dict[str, Any]]) -> int:
    pool = await get_db()
    await create_tables(pool)
    values = [
        (
            row["local_card_id"], row["category"], row["card_name"], row["grade"],
            row["set_code"], row["set_name"], row["collector_number"], row["rarity"],
            row["language"], row["image_url"], row["market_price_usd"],
            json.dumps(row["metadata"], ensure_ascii=False),
        )
        for row in rows
    ]
    async with pool.acquire() as conn, conn.transaction():
        await conn.fetchval(
            "SELECT pg_advisory_xact_lock(hashtextextended('renaiss-psa10-catalog-sync', 0))"
        )
        await conn.executemany(
            """
            INSERT INTO renaiss_catalog_cards (
                local_card_id, category, card_name, grade, set_code, set_name,
                collector_number, rarity, language, image_url, market_price_usd,
                metadata, is_active
            ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12::jsonb,FALSE)
            ON CONFLICT (local_card_id) DO UPDATE SET
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
                metadata = COALESCE(renaiss_catalog_cards.metadata, '{}'::jsonb) || EXCLUDED.metadata,
                is_active = renaiss_catalog_cards.is_active,
                updated_at = clock_timestamp()
            """,
            values,
        )
    return len(values)


async def async_main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, required=True)
    parser.add_argument("--apply", action="store_true", help="Stage rows in PostgreSQL")
    parser.add_argument("--request-interval", type=float, default=0.5)
    parser.add_argument("--cache-file", type=Path)
    parser.add_argument("--from-cache", action="store_true")
    args = parser.parse_args()
    load_explicit_environment(args.env_file)
    if args.apply:
        issue = staging_target_issue()
        if issue:
            raise SystemExit(f"refusing to stage PSA 10 candidates: {issue}")
    try:
        if args.from_cache:
            if args.cache_file is None or not args.cache_file.is_file():
                raise SystemExit("--from-cache requires an existing --cache-file")
            cached = json.loads(args.cache_file.read_text(encoding="utf-8"))
            rows = cached["rows"]
            summary = cached["summary"]
        else:
            rows, summary = await fetch_candidates(request_interval=args.request_interval)
            if args.cache_file is not None:
                args.cache_file.parent.mkdir(parents=True, exist_ok=True)
                args.cache_file.write_text(
                    json.dumps({"summary": summary, "rows": rows}, ensure_ascii=False),
                    encoding="utf-8",
                )
        summary["mode"] = "apply" if args.apply else "dry-run"
        if args.apply:
            summary["staged"] = await stage_candidates(rows)
        print("FINAL " + json.dumps(summary, ensure_ascii=False, sort_keys=True))
        return 0
    finally:
        await close_db()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(async_main()))
