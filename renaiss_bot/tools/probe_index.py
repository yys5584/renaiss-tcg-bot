"""Renaiss Index API 원본 응답 덤프 — 매칭 튜닝용. 카드명으로 후보 전부 출력."""

from __future__ import annotations

import asyncio
import json
import os
import sys
from urllib.parse import urlencode

import aiohttp
from dotenv import load_dotenv


async def probe(name: str) -> None:
    base = (os.getenv("RENAISS_API_BASE_URL") or "").rstrip("/")
    path = os.getenv("RENAISS_API_SEARCH_PATH", "/v1/search")
    key = os.getenv("RENAISS_API_KEY", "").strip()
    headers = {"Accept": "application/json"}
    if key:
        headers["Authorization"] = f"Bearer {key}"
    url = f"{base}{path}?{urlencode({'q': name, 'limit': 12})}"
    async with aiohttp.ClientSession(headers=headers) as s:
        async with s.get(url) as r:
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
    load_dotenv()
    names = sys.argv[1:] or ["Alakazam", "Blastoise"]
    for n in names:
        try:
            await probe(n)
        except Exception as e:
            print(f"probe {n} failed: {e}")


if __name__ == "__main__":
    asyncio.run(main())
