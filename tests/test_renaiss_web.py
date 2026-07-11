"""Standalone Renaiss web companion tests."""

from __future__ import annotations

import base64
import inspect
import json
import logging
import os
import time
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse

import pytest
from aiohttp import CookieJar, web
from aiohttp.test_utils import TestClient, TestServer
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from renaiss_bot.web import app as web_app
from renaiss_bot.web import queries
from renaiss_bot.web.app import (
    RATE_LIMITER_KEY,
    QuerySafeAccessLogger,
    RequestRateLimiter,
    _production_startup_issues,
    create_app,
    rate_limits,
)
from renaiss_bot.web.auth import (
    AuthError,
    OIDCConfig,
    create_oidc_authorization,
    issue_session,
    read_session,
    validate_telegram_id_token,
    verify_oidc_flow,
)


def _b64(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


@pytest.fixture
def web_secret(monkeypatch):
    monkeypatch.setenv("RENAISS_WEB_SESSION_SECRET", "s" * 48)


@pytest.fixture
async def preview_client(web_secret):
    client = TestClient(TestServer(create_app(preview=True)), cookie_jar=CookieJar(unsafe=True))
    await client.start_server()
    try:
        yield client
    finally:
        await client.close()


async def test_preview_serves_only_prefixed_app_and_security_headers(preview_client):
    response = await preview_client.get("/renaiss")
    assert response.status == 200
    text = await response.text()
    assert "Renaiss" in text
    assert "/renaiss/static/app.js?v=20260711-5" in text
    assert 'data-copy="/mycards"' in text
    assert 'data-copy="/market"' not in text
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert "default-src 'self'" in response.headers["Content-Security-Policy"]

    static = await preview_client.get("/renaiss/static/app.js")
    assert static.status == 200
    assert (await static.text()).startswith("(function ()")
    assert (await preview_client.get("/api/collection")).status == 404


async def test_legacy_collection_path_redirects_without_losing_query(preview_client):
    response = await preview_client.get(
        "/renaiss/collection/?lang=en", allow_redirects=False
    )
    assert response.status == 308
    assert response.headers["Location"] == "/renaiss?lang=en"


async def test_preview_login_exposes_owned_collection_and_me(preview_client):
    config = await (await preview_client.get("/renaiss/api/config")).json()
    assert config["preview"] is True
    assert config["telegram_login_url"] == "/renaiss/api/auth/preview"

    anonymous = await (await preview_client.get("/renaiss/api/collection")).json()
    assert anonymous["authenticated"] is False
    assert anonymous["summary"]["catalog_total"] == 5
    assert not any(card["owned"] for card in anonymous["cards"])
    assert all("market_price_usd" not in card for card in anonymous["cards"])
    assert all("obtained_at" not in card for card in anonymous["cards"])

    login = await preview_client.post("/renaiss/api/auth/preview")
    assert login.status == 200
    assert "HttpOnly" in login.headers["Set-Cookie"]
    assert "Path=/renaiss" in login.headers["Set-Cookie"]
    login_user = (await login.json())["user"]
    assert login_user == {"display_name": "Preview Collector"}

    me = await (await preview_client.get("/renaiss/api/auth/me")).json()
    assert me["ok"] is True
    assert me["user"]["display_name"] == "Preview Collector"
    assert "id" not in me["user"]
    assert "username" not in me["user"]

    collection = await (await preview_client.get("/renaiss/api/collection")).json()
    assert collection["authenticated"] is True
    assert collection["summary"]["owned_in_catalog"] == 3
    assert collection["summary"]["completion_pct"] == 60.0
    assert sum(bool(card["owned"]) for card in collection["cards"]) == 3

    leaderboard = await (
        await preview_client.get("/renaiss/api/leaderboard")
    ).json()
    assert leaderboard["available"] is True
    assert leaderboard["metric"] == "weekly_lucky_catches"
    assert all("lucky_catches" in row for row in leaderboard["rows"])
    assert any(row["is_me"] for row in leaderboard["rows"])


async def test_preview_health_is_explicitly_non_database(preview_client):
    live = await (await preview_client.get("/renaiss/livez")).json()
    ready_response = await preview_client.get("/renaiss/readyz")
    ready = await ready_response.json()
    assert live == {"ok": True}
    assert ready_response.status == 200
    assert ready == {"ok": True, "preview": True, "db": False}


def test_signed_session_rejects_tampering(web_secret):
    token = issue_session({"id": 123, "display_name": "Collector"}, now=1_000)
    assert read_session(token, now=1_001)["id"] == 123
    with pytest.raises(AuthError):
        read_session(token[:-1] + ("A" if token[-1] != "A" else "B"), now=1_001)


def test_preview_never_reuses_an_inherited_production_session_secret(monkeypatch):
    production_secret = "production-session-secret-" + "s" * 32
    monkeypatch.setenv("RENAISS_WEB_SESSION_SECRET", production_secret)
    production_token = issue_session(
        {"id": 123, "display_name": "Collector"}, now=1_000
    )

    create_app(preview=True)

    assert os.environ["RENAISS_WEB_SESSION_SECRET"] != production_secret
    with pytest.raises(AuthError):
        read_session(production_token, now=1_001)


def test_oidc_authorization_uses_state_nonce_and_pkce(web_secret):
    config = OIDCConfig(
        client_id="123456",
        client_secret="secret",
        redirect_uri="https://tgpoke.com/renaiss/api/auth/telegram/callback",
    )
    location, cookie = create_oidc_authorization(config, now=1_000)
    params = parse_qs(urlparse(location).query)
    assert params["client_id"] == ["123456"]
    assert params["scope"] == ["openid profile"]
    assert params["code_challenge_method"] == ["S256"]
    assert params["nonce"][0]
    flow = verify_oidc_flow(cookie, params["state"][0], now=1_001)
    assert flow["nonce"] == params["nonce"][0]
    assert flow["verifier"]
    with pytest.raises(AuthError):
        verify_oidc_flow(cookie, "wrong-state", now=1_001)


def test_telegram_id_token_validates_rs256_claims_and_nonce(web_secret):
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public_numbers = private_key.public_key().public_numbers()
    now = int(time.time())
    header = {"alg": "RS256", "kid": "key-1", "typ": "JWT"}
    claims = {
        "iss": "https://oauth.telegram.org",
        "aud": "123456",
        "sub": "987654321",
        "id": 987654321,
        "iat": now,
        "exp": now + 600,
        "nonce": "nonce-1",
        "name": "Renaiss Collector",
        "preferred_username": "collector",
    }
    encoded_header = _b64(json.dumps(header, separators=(",", ":")).encode())

    def signed_token(payload):
        encoded_claims = _b64(
            json.dumps(payload, separators=(",", ":")).encode()
        )
        signing_input = f"{encoded_header}.{encoded_claims}".encode("ascii")
        signature = private_key.sign(
            signing_input, padding.PKCS1v15(), hashes.SHA256()
        )
        return f"{encoded_header}.{encoded_claims}.{_b64(signature)}"

    token = signed_token(claims)
    jwks = {
        "keys": [
            {
                "kid": "key-1",
                "kty": "RSA",
                "alg": "RS256",
                "n": _b64(public_numbers.n.to_bytes((public_numbers.n.bit_length() + 7) // 8, "big")),
                "e": _b64(public_numbers.e.to_bytes((public_numbers.e.bit_length() + 7) // 8, "big")),
            }
        ]
    }
    user = validate_telegram_id_token(
        token, jwks, client_id="123456", nonce="nonce-1", now=now
    )
    assert user == {
        "id": 987654321,
        "display_name": "Renaiss Collector",
        "username": "collector",
    }
    with pytest.raises(AuthError):
        validate_telegram_id_token(
            token, jwks, client_id="123456", nonce="replayed", now=now
        )

    no_profile_id = dict(claims)
    no_profile_id.pop("id")
    with pytest.raises(AuthError):
        validate_telegram_id_token(
            signed_token(no_profile_id),
            jwks,
            client_id="123456",
            nonce="nonce-1",
            now=now,
        )

    no_subject = dict(claims)
    no_subject.pop("sub")
    with pytest.raises(AuthError):
        validate_telegram_id_token(
            signed_token(no_subject),
            jwks,
            client_id="123456",
            nonce="nonce-1",
            now=now,
        )

    multiple_audiences = dict(claims, aud=["123456", "another-client"])
    with pytest.raises(AuthError):
        validate_telegram_id_token(
            signed_token(multiple_audiences),
            jwks,
            client_id="123456",
            nonce="nonce-1",
            now=now,
        )
    multiple_audiences["azp"] = "123456"
    assert validate_telegram_id_token(
        signed_token(multiple_audiences),
        jwks,
        client_id="123456",
        nonce="nonce-1",
        now=now,
    )["id"] == 987654321


def test_production_startup_fails_closed_without_oidc_or_session_secret(monkeypatch):
    for name in (
        "DATABASE_URL",
        "RENAISS_WEB_SESSION_SECRET",
        "RENAISS_TELEGRAM_OIDC_CLIENT_ID",
        "RENAISS_TELEGRAM_OIDC_CLIENT_SECRET",
        "RENAISS_TELEGRAM_OIDC_REDIRECT_URI",
        "RENAISS_EXPECTED_BOT_ID",
        "RENAISS_ENV_FILE",
        "RENAISS_WEB_PREVIEW",
    ):
        monkeypatch.delenv(name, raising=False)
    issues = _production_startup_issues()
    assert "DATABASE_URL is missing" in issues
    assert "Telegram OIDC is not configured" in issues
    assert any("SESSION_SECRET" in issue for issue in issues)
    assert any("external web-only env" in issue for issue in issues)


def test_production_startup_accepts_only_external_env_path(monkeypatch, tmp_path):
    monkeypatch.delenv("RENAISS_WEB_PREVIEW", raising=False)
    monkeypatch.setenv("RENAISS_ENV_FILE", "relative-web.env")
    relative_issues = _production_startup_issues()
    assert "RENAISS_ENV_FILE must be an absolute path" in relative_issues

    env_file = tmp_path / "web.env"
    env_file.write_text("RENAISS_WEB_PREVIEW=0\n", encoding="utf-8")
    monkeypatch.setenv("RENAISS_ENV_FILE", str(env_file))
    external_issues = _production_startup_issues()
    assert not any("RENAISS_ENV_FILE must" in issue for issue in external_issues)


def test_production_startup_requires_database_fingerprint(monkeypatch):
    monkeypatch.setenv(
        "DATABASE_URL", "postgresql://reader:secret@db.example.com:5432/renaiss"
    )
    monkeypatch.delenv("RENAISS_EXPECTED_DATABASE_FINGERPRINT", raising=False)
    monkeypatch.delenv("RENAISS_WEB_PREVIEW", raising=False)

    issues = _production_startup_issues()

    assert "RENAISS_EXPECTED_DATABASE_FINGERPRINT is missing" in issues


def test_production_startup_refuses_unrelated_service_secrets(monkeypatch):
    monkeypatch.delenv("RENAISS_WEB_PREVIEW", raising=False)
    monkeypatch.setenv("RENAISS_BOT_TOKEN", "must-not-reach-public-web")
    monkeypatch.setenv("POKARD_API_KEY", "must-not-reach-public-web")

    issues = _production_startup_issues()

    secret_issue = next(
        issue for issue in issues if "unrelated bot/API secrets" in issue
    )
    assert "RENAISS_BOT_TOKEN" in secret_issue
    assert "POKARD_API_KEY" in secret_issue
    assert "must-not-reach-public-web" not in secret_issue


def test_preview_refuses_public_host_and_production_tunnel_port(monkeypatch):
    monkeypatch.setenv("RENAISS_WEB_PREVIEW", "1")
    monkeypatch.setenv("RENAISS_WEB_HOST", "0.0.0.0")
    monkeypatch.setenv("RENAISS_WEB_PORT", "18082")
    issues = _production_startup_issues()
    assert any("loopback-only" in issue for issue in issues)
    assert any("production Tunnel port" in issue for issue in issues)


def test_catalog_image_urls_are_allowlisted(monkeypatch):
    monkeypatch.setenv("RENAISS_IMAGE_ALLOWED_HOSTS", "images.pokemontcg.io")
    assert (
        queries.sanitize_image_url("https://images.pokemontcg.io/sv8/238_hires.png")
        == "https://images.pokemontcg.io/sv8/238_hires.png"
    )
    assert queries.sanitize_image_url("https://tracker.example/card.png") is None
    assert queries.sanitize_image_url("http://images.pokemontcg.io/card.png") is None


def test_access_logger_never_records_callback_query_secrets(caplog):
    logger = logging.getLogger("renaiss-web-access-test")
    access = QuerySafeAccessLogger(logger, "")
    request = SimpleNamespace(
        method="GET",
        path="/renaiss/api/auth/telegram/callback",
        path_qs="/renaiss/api/auth/telegram/callback?code=secret-code&state=secret-state",
        rel_url=SimpleNamespace(
            raw_path="/renaiss/api/auth/telegram/callback\nforged-log-line"
        ),
    )
    with caplog.at_level(logging.INFO, logger=logger.name):
        access.log(request, SimpleNamespace(status=302), 0.05)
    assert "callback" in caplog.text
    assert "secret-code" not in caplog.text
    assert "secret-state" not in caplog.text
    assert "callback?forged-log-line" in caplog.text
    assert "\nforged-log-line" not in caplog.text


def test_read_api_rate_limiter_is_bounded():
    limiter = RequestRateLimiter()
    assert all(
        limiter.allow("203.0.113.1", "read-api", limit=3, seconds=60)
        for _ in range(3)
    )
    assert not limiter.allow("203.0.113.1", "read-api", limit=3, seconds=60)


async def test_login_start_and_callback_have_separate_shared_ip_bursts():
    limiter = RequestRateLimiter()
    app = {RATE_LIMITER_KEY: limiter}

    async def handler(_request):
        return SimpleNamespace(status=200)

    def request(path):
        return SimpleNamespace(
            path=path,
            headers={"CF-Connecting-IP": "203.0.113.7"},
            remote="203.0.113.7",
            app=app,
        )

    for _ in range(60):
        await rate_limits(request("/renaiss/api/auth/telegram/start"), handler)
    with pytest.raises(web.HTTPTooManyRequests) as caught:
        await rate_limits(request("/renaiss/api/auth/telegram/start"), handler)
    assert getattr(caught.value, "status", None) == 429

    response = await rate_limits(
        request("/renaiss/api/auth/telegram/callback"), handler
    )
    assert response.status == 200


@pytest.mark.parametrize(
    ("row", "expected"),
    (
        ({"can_select": True, "can_write": False, "can_create": False}, True),
        ({"can_select": True, "can_write": True, "can_create": False}, False),
        ({"can_select": True, "can_write": False, "can_create": True}, False),
        ({"can_select": False, "can_write": False, "can_create": False}, False),
    ),
)
async def test_database_role_gate_requires_select_only(monkeypatch, row, expected):
    captured = {}

    class Connection:
        async def fetchrow(self, sql, tables):
            captured["sql"] = sql
            captured["tables"] = tables
            return row

    class Acquire:
        async def __aenter__(self):
            return Connection()

        async def __aexit__(self, *_args):
            return None

    class Pool:
        def acquire(self):
            return Acquire()

    async def fake_get_db():
        return Pool()

    monkeypatch.setattr(queries, "get_db", fake_get_db)

    assert await queries.database_role_is_read_only() is expected
    assert captured["tables"] == [
        "public.renaiss_catalog_cards",
        "public.renaiss_user_cards",
        "public.renaiss_events",
    ]
    assert "INSERT" in captured["sql"]
    assert "CREATE" in captured["sql"]


async def test_production_app_startup_refuses_writable_database_role(monkeypatch):
    async def writable_role():
        return False

    async def enough_cards():
        return 100

    monkeypatch.setattr(web_app, "database_role_is_read_only", writable_role)
    monkeypatch.setattr(web_app, "active_catalog_count", enough_cards)

    with pytest.raises(RuntimeError, match="SELECT-only"):
        await web_app._startup({web_app.PREVIEW_KEY: False})


async def test_production_app_startup_requires_real_catalog(monkeypatch):
    async def read_only_role():
        return True

    async def too_few_cards():
        return queries.MIN_PUBLIC_CATALOG_CARDS - 1

    monkeypatch.setattr(web_app, "database_role_is_read_only", read_only_role)
    monkeypatch.setattr(web_app, "active_catalog_count", too_few_cards)

    with pytest.raises(RuntimeError, match="catalog"):
        await web_app._startup({web_app.PREVIEW_KEY: False})


def test_web_queries_do_not_use_generic_cards_or_market_values():
    source = inspect.getsource(queries).lower()
    assert "from cards" not in source
    assert "market_price_usd" not in source
    assert "renaiss_catalog_cards" in source
    assert "renaiss_user_cards" in source
    assert "from public.renaiss_catalog_cards" in source
    assert "from public.renaiss_user_cards" in source
    assert "from public.renaiss_events" in source
    assert "event_name = 'catch_won'" in source
