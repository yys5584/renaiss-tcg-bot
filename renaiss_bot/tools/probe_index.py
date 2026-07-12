"""Renaiss Index API 원본 응답 덤프 — 매칭 튜닝용. 카드명으로 후보 전부 출력."""

from __future__ import annotations

import asyncio
import json
import sys

import aiohttp

from renaiss_bot.runtime import load_runtime_environment
from renaiss_bot.services.client import (
    _api_base,
    _headers,
    _partner_request_guard,
    _record_partner_rate_limit,
    _search_url,
)
from renaiss_bot.services.models import CardIdentity


async def probe(name: str) -> None:
    if not _api_base():
        raise RuntimeError("RENAISS_API_BASE_URL is missing or not allowlisted HTTPS")
    card = CardIdentity(category="pokemon_tcg", card_name=name)
    timeout_seconds = 5.0
    async with _partner_request_guard(timeout_seconds=timeout_seconds):
        timeout = aiohttp.ClientTimeout(total=timeout_seconds)
        async with aiohttp.ClientSession(timeout=timeout, headers=_headers()) as s:
            async with s.get(_search_url(card), allow_redirects=False) as r:
                if r.status == 429:
                    await _record_partner_rate_limit(r)
                r.raise_for_status()
                data = await r.json(content_type=None)
    # 후보 리스트 추출
    items = data
    if isinstance(data, dict):
        for k in ("results", "items", "assets", "cards", "data", "list"):
            if isinstance(data.get(k), list):
                items = data[k]
                break
    print(f"\n===== q={name!r} → {len(items) if isinstance(items, list) else '?'} candidates =====")
    if not isinstance(items, list):
        print(json.dumps(data, indent=2)[:800])
        return
    for it in items[:12]:
        if not isinstance(it, dict):
            continue
        keys = {k: it.get(k) for k in (
            "name", "cardName", "title", "setCode", "set", "number", "cardNumber",
            "gradeLabel", "grade", "company", "confidence", "priceUsdCents", "fmvUsdCents",
            "deltaPct", "lastSaleUsdCents",
        ) if it.get(k) is not None}
        print(json.dumps(keys, ensure_ascii=False))


async def main() -> None:
    load_runtime_environment()
    names = sys.argv[1:] or ["Alakazam", "Blastoise"]
    for n in names:
        try:
            await probe(n)
        except Exception as exc:
            print(f"probe {n!r} failed (error={type(exc).__name__}).")


if __name__ == "__main__":
    asyncio.run(main())
