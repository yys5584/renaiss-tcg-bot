"""Standalone `/renaiss` web companion served behind the TGPoke domain."""

from __future__ import annotations

import asyncio
import ipaddress
import logging
import os
import secrets
import time
from pathlib import Path
from urllib.parse import urlparse

from aiohttp import web
from aiohttp.abc import AbstractAccessLogger

from renaiss_bot.database.connection import (
    close_db,
    database_tls_verification_disabled,
    get_db,
)
from renaiss_bot.runtime import PROJECT_ROOT, load_runtime_environment
from renaiss_bot.tools.prepare_database import database_mutation_target_issue
from renaiss_bot.web.auth import (
    AuthError,
    COOKIE_PATH,
    FLOW_COOKIE,
    FLOW_MAX_AGE_SECONDS,
    SESSION_COOKIE,
    SESSION_MAX_AGE_SECONDS,
    create_oidc_authorization,
    exchange_oidc_code,
    fetch_telegram_jwks,
    issue_session,
    oidc_config,
    read_session,
    validate_telegram_id_token,
    verify_oidc_flow,
)
from renaiss_bot.web.preview_data import (
    PREVIEW_USER_ID,
    preview_collection,
    preview_leaderboard,
)
from renaiss_bot.web.queries import (
    MIN_PUBLIC_CATALOG_CARDS,
    active_catalog_count,
    allowed_image_hosts,
    database_role_is_read_only,
    get_collection,
    get_weekly_lucky_leaderboard,
)


logger = logging.getLogger(__name__)
ROOT = Path(__file__).resolve().parent
INDEX_FILE = ROOT / "templates" / "index.html"
STATIC_DIR = ROOT / "static"
PREVIEW_KEY = web.AppKey("renaiss_web_preview", bool)
RATE_LIMITER_KEY = web.AppKey("renaiss_web_rate_limiter", object)
DATA_GATE_KEY = web.AppKey("renaiss_web_data_gate", object)
AUTH_GATE_KEY = web.AppKey("renaiss_web_auth_gate", object)
FORBIDDEN_WEB_ENV_VARS = (
    "RENAISS_BOT_TOKEN",
    "RENAISS_DISCORD_TOKEN",
    "RENAISS_API_KEY",
    "RENAISS_API_SECRET",
    "RENAISS_CLICK_TRACKER_SECRET",
    "RENAISS_TELEGRAM_LOCK_DATABASE_URL",
    "RENAISS_COHORT_EXPERIMENT_SALT",
    "POKARD_API_KEY",
)


class QuerySafeAccessLogger(AbstractAccessLogger):
    """Keep callback authorization codes and state values out of access logs."""

    def log(self, request, response, elapsed: float) -> None:
        raw_path = str(getattr(getattr(request, "rel_url", None), "raw_path", request.path))
        safe_path = "".join(
            character if ord(character) >= 32 and ord(character) != 127 else "?"
            for character in raw_path
        )
        self.logger.info(
            "%s %s %s %.3fs",
            request.method,
            safe_path,
            response.status,
            elapsed,
        )


class RequestRateLimiter:
    """Small per-process guard; Cloudflare limits remain the outer production layer."""

    def __init__(self) -> None:
        self._windows: dict[tuple[str, str], tuple[float, int]] = {}

    def allow(self, client: str, bucket: str, *, limit: int, seconds: int) -> bool:
        now = time.monotonic()
        key = (client, bucket)
        started, count = self._windows.get(key, (now, 0))
        if now - started >= seconds:
            started, count = now, 0
        if len(self._windows) > 10_000:
            cutoff = now - 600
            self._windows = {
                item_key: value
                for item_key, value in self._windows.items()
                if value[0] >= cutoff
            }
            if len(self._windows) > 10_000 and key not in self._windows:
                return False
        count += 1
        self._windows[key] = (started, count)
        return count <= limit


def _enabled(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in {"1", "true", "yes", "on"}


def _is_loopback_host(value: str) -> bool:
    return value.strip().lower() in {"127.0.0.1", "::1", "localhost"}


def _safe_telegram_url(value: str) -> str | None:
    try:
        parsed = urlparse(value.strip())
        hostname = parsed.hostname
        port = parsed.port
    except ValueError:
        return None
    if parsed.scheme != "https" or hostname not in {"t.me", "telegram.me"}:
        return None
    if parsed.username or parsed.password or port not in {None, 443}:
        return None
    if not parsed.path.strip("/"):
        return None
    return parsed.geturl()


def _production_startup_issues() -> list[str]:
    issues: list[str] = []
    preview = _enabled("RENAISS_WEB_PREVIEW")
    host = os.getenv("RENAISS_WEB_HOST", "127.0.0.1")
    if not _is_loopback_host(host):
        issues.append("RENAISS_WEB_HOST must stay loopback-only behind Cloudflare Tunnel")
    try:
        port = int(os.getenv("RENAISS_WEB_PORT", "18082"))
    except ValueError:
        port = 0
    if not 1 <= port <= 65535:
        issues.append("RENAISS_WEB_PORT must be between 1 and 65535")
    if preview:
        if port == 18082:
            issues.append("preview must not use the production Tunnel port 18082")
        return issues
    configured_env = os.getenv("RENAISS_ENV_FILE", "").strip()
    try:
        env_path = Path(configured_env).expanduser().resolve(strict=True)
    except (OSError, RuntimeError, ValueError):
        env_path = None
    if not configured_env:
        issues.append("RENAISS_ENV_FILE must select an external web-only env file")
    elif not Path(configured_env).expanduser().is_absolute():
        issues.append("RENAISS_ENV_FILE must be an absolute path")
    elif env_path is None or not env_path.is_file():
        issues.append("RENAISS_ENV_FILE must be an existing regular file")
    elif env_path == PROJECT_ROOT or PROJECT_ROOT in env_path.parents:
        issues.append("RENAISS_ENV_FILE must stay outside the source tree")
    exposed_secrets = [
        name for name in FORBIDDEN_WEB_ENV_VARS if os.getenv(name, "").strip()
    ]
    if exposed_secrets:
        issues.append(
            "public web process must not receive unrelated bot/API secrets: "
            + ", ".join(exposed_secrets)
        )
    database_url = os.getenv("DATABASE_URL", "").strip()
    if not database_url:
        issues.append("DATABASE_URL is missing")
    else:
        target_issue = database_mutation_target_issue(database_url)
        if target_issue:
            issues.append(target_issue)
    if database_tls_verification_disabled():
        issues.append("RENAISS_DB_SSL_INSECURE cannot be enabled for the public web service")
    if _enabled("RENAISS_SKIP_DB"):
        issues.append("RENAISS_SKIP_DB cannot be enabled for the public web service")
    if os.getenv("RENAISS_API_MOCK_JSON", "").strip():
        issues.append("RENAISS_API_MOCK_JSON must be unset for the public web service")
    if len(os.getenv("RENAISS_WEB_SESSION_SECRET", "").strip()) < 32:
        issues.append("RENAISS_WEB_SESSION_SECRET must be at least 32 characters")
    try:
        config = oidc_config()
    except AuthError as exc:
        issues.append(str(exc))
        config = None
    if config is None:
        issues.append("Telegram OIDC is not configured")
    if _safe_telegram_url(os.getenv("RENAISS_OFFICIAL_GROUP_URL", "")) is None:
        issues.append("RENAISS_OFFICIAL_GROUP_URL must be a valid HTTPS Telegram URL")
    expected_bot_id = os.getenv("RENAISS_EXPECTED_BOT_ID", "").strip()
    if not expected_bot_id.isdecimal():
        issues.append("RENAISS_EXPECTED_BOT_ID must be numeric")
    elif config is not None and config.client_id != expected_bot_id:
        issues.append("Telegram OIDC client id must match RENAISS_EXPECTED_BOT_ID")
    return issues


def _request_user(request: web.Request) -> dict | None:
    token = request.cookies.get(SESSION_COOKIE, "")
    if not token:
        return None
    try:
        return read_session(token)
    except AuthError:
        return None


def _public_user(user: dict | None) -> dict | None:
    if user is None:
        return None
    return {"display_name": str(user.get("display_name") or "Collector")[:120]}


def _set_session_cookie(response: web.StreamResponse, token: str, *, preview: bool) -> None:
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=SESSION_MAX_AGE_SECONDS,
        httponly=True,
        secure=not preview,
        samesite="Lax",
        path=COOKIE_PATH,
    )


def _clear_auth_cookies(response: web.StreamResponse) -> None:
    response.del_cookie(SESSION_COOKIE, path=COOKIE_PATH)
    response.del_cookie(FLOW_COOKIE, path=COOKIE_PATH)


def _client_address(request: web.Request) -> str:
    candidate = request.headers.get("CF-Connecting-IP", "").strip()
    try:
        return str(ipaddress.ip_address(candidate))
    except ValueError:
        return str(request.remote or "unknown")


@web.middleware
async def rate_limits(request: web.Request, handler):
    path = request.path
    rule: tuple[str, int, int] | None = None
    if path == "/renaiss/api/auth/telegram/start":
        rule = ("telegram-auth-start", 60, 300)
    elif path == "/renaiss/api/auth/telegram/callback":
        rule = ("telegram-auth-callback", 60, 300)
    elif path == "/renaiss/api/auth/preview":
        rule = ("preview-auth", 30, 60)
    elif path in {"/renaiss/api/collection", "/renaiss/api/leaderboard"}:
        rule = ("read-api", 120, 60)
    elif path == "/renaiss/readyz":
        rule = ("readiness", 30, 60)
    if rule is not None:
        bucket, limit, seconds = rule
        limiter = request.app[RATE_LIMITER_KEY]
        if not limiter.allow(
            _client_address(request), bucket, limit=limit, seconds=seconds
        ):
            raise web.HTTPTooManyRequests(
                text="Too many requests.", headers={"Retry-After": str(seconds)}
            )
    return await handler(request)


async def _run_data_query(request: web.Request, factory):
    gate = request.app[DATA_GATE_KEY]
    try:
        await asyncio.wait_for(gate.acquire(), timeout=1)
    except TimeoutError as exc:
        raise web.HTTPServiceUnavailable(
            text="Data service is busy.", headers={"Retry-After": "2"}
        ) from exc
    try:
        return await asyncio.wait_for(factory(), timeout=8)
    finally:
        gate.release()


@web.middleware
async def security_headers(request: web.Request, handler):
    raised = False
    try:
        response = await handler(request)
    except web.HTTPException as exc:
        response = exc
        raised = True
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
    response.headers.setdefault("Referrer-Policy", "same-origin")
    response.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
    response.headers.setdefault("Cross-Origin-Opener-Policy", "same-origin-allow-popups")
    response.headers.setdefault(
        "Content-Security-Policy",
        "default-src 'self'; script-src 'self'; style-src 'self'; "
        + "img-src 'self' data: "
        + " ".join(f"https://{host}" for host in allowed_image_hosts())
        + "; connect-src 'self'; font-src 'self'; "
        "object-src 'none'; base-uri 'none'; form-action 'self'; frame-ancestors 'self'",
    )
    if request.path.startswith("/renaiss/api/") or request.path.endswith(("/livez", "/readyz")):
        response.headers["Cache-Control"] = "no-store"
    elif request.path.startswith("/renaiss/static/"):
        response.headers["Cache-Control"] = (
            "no-store" if request.app[PREVIEW_KEY] else "public, max-age=300"
        )
    else:
        response.headers.setdefault("Cache-Control", "no-store")
    if raised:
        raise response
    return response


async def index(_request: web.Request) -> web.StreamResponse:
    return web.FileResponse(INDEX_FILE)


def _redirect_location(request: web.Request, target: str) -> str:
    return target + (f"?{request.query_string}" if request.query_string else "")


async def trailing_slash_redirect(request: web.Request) -> web.StreamResponse:
    raise web.HTTPPermanentRedirect(
        location=_redirect_location(request, request.path.rstrip("/"))
    )


async def legacy_collection_redirect(request: web.Request) -> web.StreamResponse:
    raise web.HTTPPermanentRedirect(
        location=_redirect_location(request, "/renaiss/pokedex")
    )


async def legacy_commands_redirect(request: web.Request) -> web.StreamResponse:
    raise web.HTTPPermanentRedirect(
        location=_redirect_location(request, "/renaiss/guide")
    )


async def livez(_request: web.Request) -> web.Response:
    return web.json_response({"ok": True})


async def readyz(request: web.Request) -> web.Response:
    if request.app[PREVIEW_KEY]:
        return web.json_response({"ok": True, "preview": True, "db": False})

    async def check() -> tuple[int, bool]:
        pool = await get_db()
        async with pool.acquire() as conn:
            await conn.fetchval("SELECT 1")
        return await active_catalog_count(), await database_role_is_read_only()

    try:
        count, role_ready = await asyncio.wait_for(check(), timeout=5)
        auth_ready = oidc_config() is not None
    except Exception:
        return web.json_response(
            {"ok": False, "db": False, "catalog": False, "auth": False}, status=503
        )
    catalog_ready = count >= MIN_PUBLIC_CATALOG_CARDS
    ready = auth_ready and catalog_ready and role_ready
    return web.json_response(
        {
            "ok": ready,
            "db": True,
            "catalog": catalog_ready,
            "catalog_count": count,
            "auth": auth_ready,
            "read_only_role": role_ready,
        },
        status=200 if ready else 503,
    )


async def api_config(request: web.Request) -> web.Response:
    preview = request.app[PREVIEW_KEY]
    try:
        configured = oidc_config() is not None
    except AuthError:
        configured = False
    return web.json_response(
        {
            "ok": True,
            "preview": preview,
            "telegram_login_available": preview or configured,
            "telegram_login_url": (
                "/renaiss/api/auth/preview"
                if preview
                else "/renaiss/api/auth/telegram/start" if configured else None
            ),
            "collaboration": True,
            "official_renaiss_website": False,
            "telegram_group_url": (
                "https://t.me/TG_Poke_bot"
                if preview
                else _safe_telegram_url(os.getenv("RENAISS_OFFICIAL_GROUP_URL", ""))
            ),
        }
    )


async def api_me(request: web.Request) -> web.Response:
    user = _request_user(request)
    return web.json_response({"ok": user is not None, "user": _public_user(user)})


async def auth_preview(request: web.Request) -> web.Response:
    if not request.app[PREVIEW_KEY]:
        raise web.HTTPNotFound()
    user = {
        "id": PREVIEW_USER_ID,
        "display_name": "Preview Collector",
        "username": "preview_collector",
    }
    response = web.json_response({"ok": True, "user": _public_user(user)})
    _set_session_cookie(response, issue_session(user), preview=True)
    return response


async def auth_oidc_start(request: web.Request) -> web.StreamResponse:
    try:
        config = oidc_config()
        if config is None:
            raise AuthError("Telegram OIDC unavailable")
        location, flow = create_oidc_authorization(config)
    except AuthError as exc:
        raise web.HTTPServiceUnavailable(text="Telegram login is not configured.") from exc
    response = web.HTTPFound(location=location)
    response.set_cookie(
        FLOW_COOKIE,
        flow,
        max_age=FLOW_MAX_AGE_SECONDS,
        httponly=True,
        secure=True,
        samesite="Lax",
        path=COOKIE_PATH,
    )
    raise response


async def auth_oidc_callback(request: web.Request) -> web.StreamResponse:
    response: web.HTTPFound
    try:
        if request.query.get("error"):
            raise AuthError("Telegram login cancelled")
        config = oidc_config()
        if config is None:
            raise AuthError("Telegram OIDC unavailable")
        flow = verify_oidc_flow(
            request.cookies.get(FLOW_COOKIE, ""), str(request.query.get("state") or "")
        )
        gate = request.app[AUTH_GATE_KEY]
        try:
            await asyncio.wait_for(gate.acquire(), timeout=1)
        except TimeoutError as exc:
            raise AuthError("Telegram login is busy") from exc
        try:
            token = await exchange_oidc_code(
                config,
                code=str(request.query.get("code") or ""),
                verifier=str(flow["verifier"]),
            )
            jwks = await fetch_telegram_jwks()
        finally:
            gate.release()
        user = validate_telegram_id_token(
            token,
            jwks,
            client_id=config.client_id,
            nonce=str(flow["nonce"]),
        )
        response = web.HTTPFound(location="/renaiss/mycards?auth=success")
        _set_session_cookie(response, issue_session(user), preview=False)
    except AuthError as exc:
        logger.warning("Telegram web login rejected (reason=%s)", type(exc).__name__)
        response = web.HTTPFound(location="/renaiss/login?auth=failed")
    response.del_cookie(FLOW_COOKIE, path=COOKIE_PATH)
    raise response


async def auth_logout(_request: web.Request) -> web.Response:
    response = web.json_response({"ok": True})
    _clear_auth_cookies(response)
    return response


def _int_query(request: web.Request, name: str, default: int) -> int:
    try:
        return int(request.query.get(name, str(default)) or default)
    except (TypeError, ValueError):
        return default


async def api_collection(request: web.Request) -> web.Response:
    user = _request_user(request)
    user_id = int(user["id"]) if user else None
    filters = {
        "page": min(100, max(1, _int_query(request, "page", 1))),
        "per_page": max(12, min(60, _int_query(request, "per_page", 24))),
        "search": str(request.query.get("q") or ""),
        "grade": str(request.query.get("grade") or "all"),
        "set_code": str(request.query.get("set") or "all"),
        "owned": str(request.query.get("owned") or "all"),
        "sort": str(request.query.get("sort") or "set"),
    }
    try:
        if request.app[PREVIEW_KEY]:
            payload = preview_collection(user_id, **filters)
        else:
            payload = await _run_data_query(
                request, lambda: get_collection(user_id, **filters)
            )
    except web.HTTPException:
        raise
    except Exception as exc:
        logger.error("Renaiss web collection unavailable (error=%s)", type(exc).__name__)
        return web.json_response(
            {"ok": False, "available": False, "error": "temporarily_unavailable"},
            status=503,
        )
    return web.json_response(payload)


async def api_leaderboard(request: web.Request) -> web.Response:
    user = _request_user(request)
    user_id = int(user["id"]) if user else None
    limit = max(3, min(50, _int_query(request, "limit", 20)))
    try:
        payload = (
            preview_leaderboard(user_id)
            if request.app[PREVIEW_KEY]
            else await _run_data_query(
                request, lambda: get_weekly_lucky_leaderboard(user_id, limit=limit)
            )
        )
    except web.HTTPException:
        raise
    except Exception as exc:
        logger.error("Renaiss web leaderboard unavailable (error=%s)", type(exc).__name__)
        return web.json_response(
            {"ok": False, "available": False, "error": "temporarily_unavailable"},
            status=503,
        )
    return web.json_response(payload)


async def _cleanup(app: web.Application) -> None:
    if not app[PREVIEW_KEY]:
        await close_db()


async def _startup(app: web.Application) -> None:
    if app[PREVIEW_KEY]:
        return
    if not await database_role_is_read_only():
        raise RuntimeError("Renaiss web database role is not SELECT-only")
    count = await active_catalog_count()
    if count < MIN_PUBLIC_CATALOG_CARDS:
        raise RuntimeError("Renaiss web catalog is below the production minimum")


def create_app(*, preview: bool | None = None) -> web.Application:
    if preview is None:
        preview = _enabled("RENAISS_WEB_PREVIEW")
    if preview:
        # Never let a preview cookie validate in production, even when the
        # process inherited a real service secret.
        os.environ["RENAISS_WEB_SESSION_SECRET"] = secrets.token_urlsafe(48)

    app = web.Application(
        client_max_size=64 * 1024,
        middlewares=[security_headers, rate_limits],
    )
    app[PREVIEW_KEY] = bool(preview)
    app[RATE_LIMITER_KEY] = RequestRateLimiter()
    app[DATA_GATE_KEY] = asyncio.Semaphore(4)
    app[AUTH_GATE_KEY] = asyncio.Semaphore(4)
    app.router.add_get("/renaiss", index)
    app.router.add_get("/renaiss/", trailing_slash_redirect)
    for page in ("pokedex", "mycards", "leaderboard", "guide", "login"):
        app.router.add_get(f"/renaiss/{page}", index)
        app.router.add_get(f"/renaiss/{page}/", trailing_slash_redirect)
    app.router.add_get("/renaiss/collection", legacy_collection_redirect)
    app.router.add_get("/renaiss/collection/", legacy_collection_redirect)
    app.router.add_get("/renaiss/commands", legacy_commands_redirect)
    app.router.add_get("/renaiss/commands/", legacy_commands_redirect)
    app.router.add_get("/renaiss/livez", livez)
    app.router.add_get("/renaiss/readyz", readyz)
    app.router.add_get("/renaiss/api/config", api_config)
    app.router.add_get("/renaiss/api/auth/me", api_me)
    app.router.add_post("/renaiss/api/auth/preview", auth_preview)
    app.router.add_get("/renaiss/api/auth/telegram/start", auth_oidc_start)
    app.router.add_get("/renaiss/api/auth/telegram/callback", auth_oidc_callback)
    app.router.add_post("/renaiss/api/auth/logout", auth_logout)
    app.router.add_get("/renaiss/api/collection", api_collection)
    app.router.add_get("/renaiss/api/leaderboard", api_leaderboard)
    app.router.add_static("/renaiss/static/", STATIC_DIR, show_index=False)
    app.on_startup.append(_startup)
    app.on_cleanup.append(_cleanup)
    return app


def main() -> None:
    load_runtime_environment()
    logging.basicConfig(
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        level=getattr(logging, os.getenv("LOG_LEVEL", "INFO").upper(), logging.INFO),
    )
    issues = _production_startup_issues()
    if issues:
        logger.error("Renaiss web startup refused: %s", "; ".join(issues))
        raise SystemExit(2)
    host = os.getenv("RENAISS_WEB_HOST", "127.0.0.1")
    port = int(os.getenv("RENAISS_WEB_PORT", "18082"))
    web.run_app(
        create_app(),
        host=host,
        port=port,
        access_log=logger,
        access_log_class=QuerySafeAccessLogger,
    )


if __name__ == "__main__":
    main()
