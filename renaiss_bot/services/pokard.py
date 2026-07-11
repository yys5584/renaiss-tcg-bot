"""Lean POKARD API client (card universe source) — standalone for Renaiss.

Ported minimal subset of the main-repo client: paginated GET /cards with Bearer
auth and rate-limit handling. Prices come from the Renaiss Index API separately
(see services.client / services.pricing), NOT from POKARD.
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Any

import aiohttp

logger = logging.getLogger(__name__)


class PokardError(Exception):
    def __init__(self, code: str, message: str, status: int | None = None):
        super().__init__(f"[{code}] {message}")
        self.code = code
        self.status = status


def _base_url() -> str:
    return (os.getenv("POKARD_API_BASE_URL") or "").rstrip("/")


def _api_key() -> str:
    return os.getenv("POKARD_API_KEY", "").strip()


async def list_cards(
    session: aiohttp.ClientSession,
    *,
    series: list[str] | None = None,
    gen: int | None = None,
    supertype: str | None = None,
    q: str | None = None,
    limit: int = 100,
    offset: int = 0,
    timeout_seconds: float = 15.0,
) -> dict:
    """POKARD /cards 페이지네이션. 반환: {"cards": [...], "total": N, ...}."""
    base = _base_url()
    if not base:
        raise PokardError("NO_BASE", "POKARD_API_BASE_URL 미설정")

    params: dict[str, Any] = {"limit": limit, "offset": offset}
    if series:
        params["series"] = ",".join(series)
    if gen is not None:
        params["gen"] = gen
    if supertype:
        params["supertype"] = supertype
    if q:
        params["q"] = q

    url = f"{base}/cards"
    timeout = aiohttp.ClientTimeout(total=timeout_seconds)
    try:
        async with session.get(
            url,
            params=params,
            timeout=timeout,
            allow_redirects=False,
        ) as resp:
            if resp.status == 401:
                raise PokardError("UNAUTHORIZED", "POKARD_API_KEY 미설정/무효", 401)
            if resp.status == 403:
                raise PokardError("IP_NOT_ALLOWED", "로컬 IP 차단 — VM 에서 실행 필요", 403)
            if resp.status == 429:
                raise PokardError("RATE_LIMIT", "Rate limit — sleep 늘리기", 429)
            if resp.status != 200:
                text = await resp.text()
                raise PokardError("HTTP_ERROR", f"{resp.status}: {text[:200]}", resp.status)
            return await resp.json()
    except asyncio.TimeoutError:
        raise PokardError("TIMEOUT", f"{timeout_seconds}s 타임아웃")
    except aiohttp.ClientError as exc:
        raise PokardError("NETWORK", f"{exc.__class__.__name__}: {exc}")


def make_session() -> aiohttp.ClientSession:
    headers = {"User-Agent": "renaiss-bot/1.0 (pokard-client)"}
    key = _api_key()
    if key:
        headers["Authorization"] = f"Bearer {key}"
    return aiohttp.ClientSession(headers=headers)
