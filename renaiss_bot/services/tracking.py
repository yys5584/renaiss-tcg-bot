"""Opaque, database-backed click-tracking URLs for Renaiss outbound links."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import os
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from urllib.parse import quote, urlencode, urlparse

from renaiss_bot.database.connection import get_db


class TrackingTokenError(ValueError):
    pass


def _create_timeout_seconds() -> float:
    try:
        return min(
            3.0,
            max(0.05, float(os.getenv("RENAISS_TRACKING_CREATE_TIMEOUT_SECONDS", "0.5"))),
        )
    except ValueError:
        return 0.5


@dataclass(frozen=True)
class TrackingClick:
    destination_url: str
    user_id: int | None
    chat_id: int | None
    local_card_id: str | None
    source: str
    token_hash: str


def _public_base_url() -> str:
    return os.getenv("RENAISS_CLICK_TRACKER_PUBLIC_BASE_URL", "").strip().rstrip("/")


def _secret() -> bytes:
    return os.getenv("RENAISS_CLICK_TRACKER_SECRET", "").encode("utf-8")


def _allowed_hosts() -> set[str]:
    raw = os.getenv(
        "RENAISS_CLICK_ALLOWED_HOSTS",
        "renaiss.xyz,www.renaiss.xyz,index.renaissos.com",
    )
    return {host.strip().lower() for host in raw.split(",") if host.strip()}


def _token_ttl_days() -> int:
    try:
        return max(1, min(90, int(os.getenv("RENAISS_CLICK_TOKEN_TTL_DAYS", "30"))))
    except ValueError:
        return 30


def destination_allowed(url: str) -> bool:
    try:
        parsed = urlparse(url)
    except ValueError:
        return False
    return (
        parsed.scheme == "https"
        and parsed.hostname is not None
        and parsed.hostname.lower() in _allowed_hosts()
        and not parsed.username
        and not parsed.password
    )


def tracking_configured() -> bool:
    base = _public_base_url()
    try:
        parsed = urlparse(base)
    except ValueError:
        return False
    return (
        parsed.scheme == "https"
        and bool(parsed.hostname)
        and not parsed.username
        and not parsed.password
        and not parsed.query
        and not parsed.fragment
        and len(_secret()) >= 32
    )


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("ascii")).hexdigest()


def _fallback_signature(token: str, destination_url: str, expires_at: int) -> str:
    payload = f"{token}\n{destination_url}\n{expires_at}".encode("utf-8")
    return hmac.new(_secret(), payload, hashlib.sha256).hexdigest()


def validate_fallback_destination(
    token: str,
    destination_url: str,
    expires_at_raw: str,
    signature: str,
) -> str:
    if not destination_allowed(destination_url) or not _secret():
        raise TrackingTokenError("invalid fallback")
    try:
        expires_at = int(expires_at_raw)
    except ValueError as exc:
        raise TrackingTokenError("invalid fallback expiry") from exc
    if expires_at < int(datetime.now(timezone.utc).timestamp()):
        raise TrackingTokenError("expired fallback")
    expected = _fallback_signature(token, destination_url, expires_at)
    if not hmac.compare_digest(signature, expected):
        raise TrackingTokenError("invalid fallback signature")
    return destination_url


async def build_tracked_url(
    destination_url: str | None,
    *,
    user_id: int | None,
    chat_id: int | None,
    local_card_id: str | None,
    source: str,
) -> str | None:
    """Create a short opaque redirect URL, falling back on DB/config failure."""
    if not destination_url:
        return None
    if not destination_allowed(destination_url):
        return None
    if not tracking_configured():
        return destination_url
    token = secrets.token_urlsafe(16)
    expires_at = datetime.now(timezone.utc) + timedelta(days=_token_ttl_days())
    expires_at_unix = int(expires_at.timestamp())
    async def persist() -> None:
        pool = await get_db()
        async with pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO renaiss_referral_links (
                    token_hash, destination_url, user_id, chat_id,
                    local_card_id, source, expires_at
                ) VALUES ($1,$2,$3,$4,$5,$6,$7)
                """,
                _token_hash(token),
                destination_url,
                user_id,
                chat_id,
                local_card_id,
                source[:64],
                expires_at,
            )

    try:
        await asyncio.wait_for(persist(), timeout=_create_timeout_seconds())
    except Exception:
        return destination_url
    query = urlencode(
        {
            "to": destination_url,
            "exp": expires_at_unix,
            "sig": _fallback_signature(token, destination_url, expires_at_unix),
        }
    )
    tracked_url = f"{_public_base_url()}/r/{quote(token, safe='')}?{query}"
    # Telegram URL buttons are limited; a direct link is better than a broken CTA.
    return tracked_url if len(tracked_url) <= 256 else destination_url


async def resolve_tracking_token(token: str) -> TrackingClick:
    if not token or len(token) > 128 or any(char not in "-_0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ" for char in token):
        raise TrackingTokenError("invalid token")
    token_hash = _token_hash(token)
    pool = await get_db()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT destination_url, user_id, chat_id, local_card_id, source
            FROM renaiss_referral_links
            WHERE token_hash = $1 AND expires_at > now()
            """,
            token_hash,
        )
    if row is None:
        raise TrackingTokenError("invalid or expired token")
    destination = str(row["destination_url"] or "")
    if not destination_allowed(destination):
        raise TrackingTokenError("destination not allowed")
    return TrackingClick(
        destination_url=destination,
        user_id=row["user_id"],
        chat_id=row["chat_id"],
        local_card_id=row["local_card_id"],
        source=str(row["source"] or "unknown")[:64],
        token_hash=token_hash,
    )


async def cleanup_expired_tracking_links() -> int:
    pool = await get_db()
    async with pool.acquire() as conn:
        result = await conn.execute(
            "DELETE FROM renaiss_referral_links WHERE expires_at <= now()"
        )
    try:
        return int(result.rsplit(" ", 1)[-1])
    except (ValueError, IndexError):
        return 0
