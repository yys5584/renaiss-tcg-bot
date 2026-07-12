"""Small standalone aiohttp redirect service for measurable Renaiss links."""

from __future__ import annotations

import asyncio
import logging
import math
import os

from aiohttp import web

from renaiss_bot.database.connection import get_db
from renaiss_bot.database.queries import log_referral_click
from renaiss_bot.runtime import load_runtime_environment
from renaiss_bot.services.tracking import (
    TrackingTokenError,
    resolve_tracking_token,
    tracking_configured,
    validate_fallback_destination,
)

logger = logging.getLogger(__name__)


def _database_timeout_seconds() -> float:
    try:
        value = float(os.getenv("RENAISS_CLICK_DB_TIMEOUT_SECONDS", "2"))
    except ValueError:
        value = 2.0
    if not math.isfinite(value):
        value = 2.0
    return min(5.0, max(0.1, value))


def _production_startup_issues() -> list[str]:
    """Reject unsafe tracker configuration before binding a listening socket."""
    issues = []
    if os.getenv("RENAISS_SKIP_DB", "").strip().lower() in {"1", "true", "yes"}:
        issues.append("RENAISS_SKIP_DB cannot be enabled for the click tracker")
    if not os.getenv("DATABASE_URL", "").strip():
        issues.append("DATABASE_URL is missing")
    if not tracking_configured():
        issues.append(
            "RENAISS_CLICK_TRACKER_PUBLIC_BASE_URL/SECRET are not production-safe"
        )
    try:
        port = int(os.getenv("RENAISS_CLICK_TRACKER_PORT", "8090"))
    except ValueError:
        port = 0
    if not 1 <= port <= 65535:
        issues.append("RENAISS_CLICK_TRACKER_PORT must be between 1 and 65535")
    return issues


async def livez(request: web.Request) -> web.Response:
    """Report process liveness without depending on external services."""
    return web.json_response({"ok": True})


async def readyz(request: web.Request) -> web.Response:
    """Report whether the tracker can currently reach its required database."""
    async def check_database() -> None:
        pool = await get_db()
        async with pool.acquire() as conn:
            await conn.fetchval("SELECT 1")

    try:
        await asyncio.wait_for(
            check_database(),
            timeout=_database_timeout_seconds(),
        )
    except Exception:
        return web.json_response({"ok": False, "db": False}, status=503)
    return web.json_response({"ok": True, "db": True})


async def health(request: web.Request) -> web.Response:
    """Backward-compatible readiness endpoint."""
    return await readyz(request)


async def redirect(request: web.Request) -> web.StreamResponse:
    token = request.match_info["token"]
    try:
        click = await asyncio.wait_for(
            resolve_tracking_token(token),
            timeout=_database_timeout_seconds(),
        )
    except TrackingTokenError as exc:
        raise web.HTTPBadRequest(text="Invalid or expired tracking link.") from exc
    except Exception as exc:
        # The signed destination contains no user identity and keeps old links usable
        # during a temporary database outage.
        try:
            destination = validate_fallback_destination(
                token,
                str(request.query.get("to") or ""),
                str(request.query.get("exp") or ""),
                str(request.query.get("sig") or ""),
            )
        except TrackingTokenError as fallback_exc:
            raise web.HTTPServiceUnavailable(text="Tracking service unavailable.") from fallback_exc
        logger.warning(
            "Referral registry unavailable; redirecting without analytics (error=%s).",
            type(exc).__name__,
        )
        raise web.HTTPFound(
            location=destination,
            headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"},
        )
    try:
        await asyncio.wait_for(
            log_referral_click(
                user_id=click.user_id,
                chat_id=click.chat_id,
                local_card_id=click.local_card_id,
                source=click.source,
                tracking_token_hash=click.token_hash,
                destination_url=click.destination_url,
            ),
            timeout=_database_timeout_seconds(),
        )
    except Exception as exc:
        # Analytics must never prevent the user from reaching Renaiss.
        logger.warning(
            "Referral click persistence failed (error=%s).",
            type(exc).__name__,
        )
    raise web.HTTPFound(
        location=click.destination_url,
        headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"},
    )


def create_app() -> web.Application:
    app = web.Application(client_max_size=1024)
    app.router.add_get("/livez", livez)
    app.router.add_get("/readyz", readyz)
    app.router.add_get("/health", health)
    app.router.add_get("/r/{token}", redirect)
    return app


def main() -> None:
    load_runtime_environment()
    logging.basicConfig(
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        level=getattr(logging, os.getenv("LOG_LEVEL", "INFO").upper(), logging.INFO),
    )
    issues = _production_startup_issues()
    if issues:
        logger.error("Renaiss click tracker startup refused: %s", "; ".join(issues))
        raise SystemExit(2)
    host = os.getenv("RENAISS_CLICK_TRACKER_HOST", "127.0.0.1")
    port = int(os.getenv("RENAISS_CLICK_TRACKER_PORT", "8090"))
    web.run_app(create_app(), host=host, port=port, access_log=logger)


if __name__ == "__main__":
    main()
