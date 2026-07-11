"""Optional loopback-only health listener for the Telegram polling process."""

from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import Awaitable, Callable, MutableMapping
from typing import Any

from aiohttp import web

from renaiss_bot.services.instance_guard import telegram_instance_guard_ready


logger = logging.getLogger(__name__)

LOOPBACK_HOST = "127.0.0.1"
DEFAULT_HEALTH_PORT = 18081
_SERVER_KEY = "renaiss.telegram_health.server"
_ENABLED_VALUES = frozenset({"1", "true", "yes", "on"})
_DISABLED_VALUES = frozenset({"0", "false", "no", "off"})
_DATABASE_READY_TIMEOUT_SECONDS = 2.0


def health_enabled() -> bool:
    """Keep the listener closed unless an operator explicitly enables it."""
    return (
        os.getenv("RENAISS_TELEGRAM_HEALTH_ENABLED", "").strip().lower()
        in _ENABLED_VALUES
    )


def _configured_port() -> int:
    raw = os.getenv("RENAISS_TELEGRAM_HEALTH_PORT", str(DEFAULT_HEALTH_PORT)).strip()
    try:
        port = int(raw)
    except ValueError as exc:
        raise ValueError("RENAISS_TELEGRAM_HEALTH_PORT must be an integer") from exc
    if not 1 <= port <= 65535:
        raise ValueError("RENAISS_TELEGRAM_HEALTH_PORT must be between 1 and 65535")
    return port


def health_configuration_issues() -> list[str]:
    """Validate listener settings without opening a socket."""
    raw_enabled = os.getenv("RENAISS_TELEGRAM_HEALTH_ENABLED", "").strip().lower()
    if raw_enabled and raw_enabled not in _ENABLED_VALUES | _DISABLED_VALUES:
        return [
            "RENAISS_TELEGRAM_HEALTH_ENABLED must be a boolean value"
        ]
    if not health_enabled():
        return []
    try:
        _configured_port()
    except ValueError as exc:
        return [str(exc)]
    return []


def _application_is_polling(application: Any) -> bool:
    updater = getattr(application, "updater", None)
    return bool(getattr(application, "running", False)) and bool(
        getattr(updater, "running", False)
    )


class TelegramHealthServer:
    """Small aiohttp listener sharing the PTB event loop and lifecycle."""

    def __init__(
        self,
        *,
        port: int,
        polling_ready: Callable[[], bool],
        database_ready: Callable[[], Awaitable[bool]] | None = None,
    ) -> None:
        self.port = port
        self._polling_ready = polling_ready
        self._database_ready = database_ready
        self._startup_ready = False
        self._runner: web.AppRunner | None = None
        self._site: web.TCPSite | None = None

    def mark_startup_ready(self) -> None:
        self._startup_ready = True

    def mark_not_ready(self) -> None:
        self._startup_ready = False

    def ready(self) -> bool:
        if not self._startup_ready:
            return False
        try:
            return bool(self._polling_ready())
        except Exception:
            return False

    async def livez(self, request: web.Request) -> web.Response:
        """Report that the event loop can still serve a local request."""
        return web.json_response(
            {"ok": True},
            headers={"Cache-Control": "no-store"},
        )

    async def readyz(self, request: web.Request) -> web.Response:
        """Report startup, active polling/fence, and a live application DB."""
        ready = self.ready()
        if ready and self._database_ready is not None:
            try:
                ready = bool(
                    await asyncio.wait_for(
                        self._database_ready(),
                        timeout=_DATABASE_READY_TIMEOUT_SECONDS,
                    )
                )
            except Exception:
                ready = False
        return web.json_response(
            {"ok": ready, "ready": ready},
            status=200 if ready else 503,
            headers={"Cache-Control": "no-store"},
        )

    async def start(self) -> None:
        if self._runner is not None:
            raise RuntimeError("Telegram health listener is already started")
        app = web.Application(client_max_size=1024)
        app.router.add_get("/livez", self.livez)
        app.router.add_get("/readyz", self.readyz)
        runner = web.AppRunner(app, access_log=None)
        try:
            await runner.setup()
            # Deliberately no host setting: this surface can never bind publicly.
            site = web.TCPSite(
                runner,
                host=LOOPBACK_HOST,
                port=self.port,
                reuse_address=False,
                shutdown_timeout=2.0,
            )
            await site.start()
        except BaseException:
            try:
                await runner.cleanup()
            except Exception as exc:
                logger.warning(
                    "Telegram health listener partial cleanup failed (error=%s).",
                    type(exc).__name__,
                )
            raise
        self._runner = runner
        self._site = site
        logger.info(
            "Telegram health listener started on loopback port %s.",
            self.port,
        )

    async def close(self) -> None:
        self.mark_not_ready()
        runner = self._runner
        if runner is not None:
            await runner.cleanup()
            self._runner = None
            self._site = None


def _bot_data(application: Any) -> MutableMapping[str, Any]:
    bot_data = getattr(application, "bot_data", None)
    if not isinstance(bot_data, MutableMapping):
        raise RuntimeError("Telegram Application.bot_data is unavailable")
    return bot_data


async def _application_database_ready() -> bool:
    from renaiss_bot.database.connection import get_db

    pool = await get_db()
    async with pool.acquire(timeout=1.0) as connection:
        return bool(await connection.fetchval("SELECT 1"))


async def start_telegram_health(application: Any) -> TelegramHealthServer | None:
    """Start the optional listener before database/chat startup gates."""
    if not health_enabled():
        return None
    bot_data = _bot_data(application)
    if _SERVER_KEY in bot_data:
        raise RuntimeError("Telegram health listener is already registered")
    try:
        port = _configured_port()
    except ValueError:
        raise RuntimeError("Telegram health listener configuration is invalid") from None
    server = TelegramHealthServer(
        port=port,
        polling_ready=lambda: (
            _application_is_polling(application)
            and telegram_instance_guard_ready(application)
        ),
        database_ready=_application_database_ready,
    )
    try:
        await server.start()
    except Exception as exc:
        logger.error(
            "Telegram health listener startup failed (error=%s).",
            type(exc).__name__,
        )
        raise RuntimeError("Telegram health listener startup failed") from None
    try:
        bot_data[_SERVER_KEY] = server
    except Exception:
        try:
            await server.close()
        finally:
            raise RuntimeError(
                "Telegram health listener could not be registered"
            ) from None
    return server


async def stop_telegram_health(application: Any) -> None:
    """Make readiness false and release the loopback socket."""
    bot_data = getattr(application, "bot_data", None)
    if not isinstance(bot_data, MutableMapping):
        return
    server = bot_data.get(_SERVER_KEY)
    if isinstance(server, TelegramHealthServer):
        await server.close()
        bot_data.pop(_SERVER_KEY, None)
