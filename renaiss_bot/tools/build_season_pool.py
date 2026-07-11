"""Build a read-only, API-sourced collection candidate manifest."""

from __future__ import annotations

import argparse
import asyncio
from collections import Counter
from datetime import datetime, timezone
import json
from pathlib import Path
from urllib.parse import urlparse
import xml.etree.ElementTree as ET

import aiohttp

from renaiss_bot.runtime import PROJECT_ROOT, load_runtime_environment
from renaiss_bot.services.client import (
    _api_base,
    _headers,
    _partner_request_guard,
    _record_partner_rate_limit,
)


SITEMAP_URL = "https://index.renaissos.com/sitemap.xml"


def sitemap_pokemon_set_counts(payload: bytes) -> Counter[str]:
    root = ET.fromstring(payload)
    namespace = {"s": "http://www.sitemaps.org/schemas/sitemap/0.9"}
    counts: Counter[str] = Counter()
    for node in root.findall("s:url", namespace):
        location = node.findtext("s:loc", default="", namespaces=namespace)
        parts = [part for part in urlparse(location).path.split("/") if part]
        if len(parts) == 4 and parts[:2] == ["card", "pokemon"]:
            counts[parts[2]] += 1
    return counts


def rank_collection_candidates(rows: list[dict], *, size: int) -> list[dict]:
    """Select unique, positive-priced PSA 10 assets without scoring approval."""
    unique: dict[str, dict] = {}
    for row in rows:
        href = str(row.get("href") or "").strip()
        company = str(row.get("company") or "").strip().upper()
        grade = str(row.get("grade") or "").strip()
        price_cents = row.get("priceUsdCents")
        if (
            not href
            or company != "PSA"
            or not grade.lower().startswith("10")
            or isinstance(price_cents, bool)
            or not isinstance(price_cents, (int, float))
            or price_cents <= 0
        ):
            continue
        unique[href] = row
    ranked = sorted(
        unique.values(),
        key=lambda item: float(item.get("priceUsdCents") or 0),
        reverse=True,
    )
    if len(ranked) < size:
        raise RuntimeError(
            f"only {len(ranked)} positive-priced PSA 10 assets were available; requested {size}"
        )
    output = []
    for rank, row in enumerate(ranked[:size], start=1):
        output.append(
            {
                "rank": rank,
                "category": "pokemon_tcg",
                "card_name": row.get("name"),
                "set_name": row.get("setName"),
                "set_code": row.get("setCode"),
                "collector_number": row.get("cardNumber"),
                "variation": row.get("variation") or "",
                "language": row.get("language") or "",
                "company": row.get("company"),
                "grade": row.get("grade"),
                "grade_label": row.get("gradeLabel"),
                "api_reference_usd": round(float(row["priceUsdCents"]) / 100, 2),
                "confidence": row.get("confidence"),
                "last_sale_at": row.get("lastSaleAt"),
                "image_url": row.get("imageUrl"),
                "image_url_thumb": row.get("imageUrlThumb"),
                "renaiss_href": row.get("href"),
                # Set listing is discovery evidence, not scored detail evidence.
                "pick_eligible": False,
            }
        )
    return output


async def build_manifest(*, size: int, set_limit: int) -> dict:
    timeout = aiohttp.ClientTimeout(total=20)
    async with aiohttp.ClientSession(timeout=timeout, headers=_headers()) as session:
        async with session.get(SITEMAP_URL, allow_redirects=False) as response:
            response.raise_for_status()
            sitemap = await response.read()
        set_counts = sitemap_pokemon_set_counts(sitemap)
        selected_sets = [slug for slug, _ in set_counts.most_common(set_limit)]
        rows: list[dict] = []
        set_summary = []
        for slug in selected_sets:
            async with _partner_request_guard(timeout_seconds=20):
                async with session.get(
                    f"{_api_base()}/v1/sets/pokemon/{slug}",
                    allow_redirects=False,
                ) as response:
                    if response.status == 429:
                        await _record_partner_rate_limit(response)
                    response.raise_for_status()
                    payload = await response.json(content_type=None)
            cards = payload.get("cards") if isinstance(payload, dict) else None
            cards = cards if isinstance(cards, list) else []
            rows.extend(item for item in cards if isinstance(item, dict))
            set_summary.append(
                {
                    "slug": slug,
                    "set_name": payload.get("setName") if isinstance(payload, dict) else None,
                    "api_rows": len(cards),
                }
            )
    cards = rank_collection_candidates(rows, size=size)
    return {
        "schema_version": 1,
        "manifest_type": "price-ranked-collection-candidate",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source": "renaiss-set-listing-api",
        "selection": {
            "strategy": "price-baseline-before-liquidity-audit",
            "game": "pokemon",
            "company": "PSA",
            "grade_prefix": "10",
            "positive_price_required": True,
            "requested_size": size,
            "set_limit": set_limit,
            "price_floor_usd": cards[-1]["api_reference_usd"],
            "scored_use_allowed": False,
        },
        "sets": set_summary,
        "cards": cards,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--size", type=int, default=1000)
    parser.add_argument("--set-limit", type=int, default=20)
    parser.add_argument(
        "--output",
        type=Path,
        default=PROJECT_ROOT / "renaiss_bot" / "catalog" / "season0_top1000.json",
    )
    return parser


async def _async_main() -> int:
    args = _parser().parse_args()
    if args.size < 1 or args.set_limit < 1:
        raise SystemExit("size and set-limit must be positive")
    load_runtime_environment()
    manifest = await build_manifest(size=args.size, set_limit=args.set_limit)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "output": str(args.output),
                "cards": len(manifest["cards"]),
                "price_floor_usd": manifest["selection"]["price_floor_usd"],
                "pick_eligible": False,
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_async_main()))
