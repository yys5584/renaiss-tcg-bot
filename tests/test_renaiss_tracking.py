"""Opaque outbound tracking links and redirect behavior."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from urllib.parse import parse_qs, unquote, urlparse

import pytest
from aiohttp import web

from renaiss_bot.referral_server import redirect
from renaiss_bot.services.tracking import (
    TrackingTokenError,
    build_tracked_url,
    destination_allowed,
    resolve_tracking_token,
)


class FakeConnection:
    def __init__(self):
        self.rows = {}

    async def execute(self, sql, *args):
        if "INSERT INTO renaiss_referral_links" in sql:
            self.rows[args[0]] = {
                "destination_url": args[1],
                "user_id": args[2],
                "chat_id": args[3],
                "local_card_id": args[4],
                "source": args[5],
            }
        return "INSERT 0 1"

    async def fetchrow(self, sql, token_hash):
        return self.rows.get(token_hash)


class Acquire:
    def __init__(self, connection):
        self.connection = connection

    async def __aenter__(self):
        return self.connection

    async def __aexit__(self, *args):
        return None


class FakePool:
    def __init__(self):
        self.connection = FakeConnection()

    def acquire(self):
        return Acquire(self.connection)


def _configure(monkeypatch):
    monkeypatch.setenv("RENAISS_CLICK_TRACKER_PUBLIC_BASE_URL", "https://click.example.com")
    monkeypatch.setenv("RENAISS_CLICK_TRACKER_SECRET", "s" * 32)


def _token(url: str) -> str:
    return unquote(urlparse(url).path.removeprefix("/r/"))


async def test_tracking_round_trip_uses_short_opaque_token(monkeypatch):
    _configure(monkeypatch)
    pool = FakePool()

    async def get_pool():
        return pool

    monkeypatch.setattr("renaiss_bot.services.tracking.get_db", get_pool)
    url = await build_tracked_url(
        "https://index.renaissos.com/cards/charizard",
        user_id=7,
        chat_id=-1001,
        local_card_id="card-4",
        source="telegram_price",
    )
    assert url is not None and url.startswith("https://click.example.com/r/")
    assert len(url) <= 256
    assert "1001" not in url and "card-4" not in url
    click = await resolve_tracking_token(_token(url))
    assert click.destination_url == "https://index.renaissos.com/cards/charizard"
    assert click.user_id == 7
    assert click.chat_id == -1001
    assert click.local_card_id == "card-4"
    assert click.source == "telegram_price"


async def test_tracking_falls_back_when_not_configured(monkeypatch):
    monkeypatch.delenv("RENAISS_CLICK_TRACKER_PUBLIC_BASE_URL", raising=False)
    monkeypatch.delenv("RENAISS_CLICK_TRACKER_SECRET", raising=False)
    destination = "https://www.renaiss.xyz/ref/moonyu"
    assert await build_tracked_url(
        destination,
        user_id=1,
        chat_id=2,
        local_card_id=None,
        source="test",
    ) == destination


async def test_tracking_rejects_non_renaiss_destination(monkeypatch):
    _configure(monkeypatch)
    assert not destination_allowed("https://evil.example/steal")
    assert await build_tracked_url(
        "https://evil.example/steal",
        user_id=1,
        chat_id=2,
        local_card_id=None,
        source="test",
    ) is None


async def test_tracking_db_failure_falls_back_to_direct_link(monkeypatch):
    _configure(monkeypatch)

    async def fail_db():
        raise RuntimeError("db unavailable")

    monkeypatch.setattr("renaiss_bot.services.tracking.get_db", fail_db)
    destination = "https://index.renaissos.com/cards/charizard"
    assert await build_tracked_url(
        destination,
        user_id=1,
        chat_id=2,
        local_card_id=None,
        source="test",
    ) == destination


async def test_tracking_db_stall_quickly_falls_back_to_direct_link(monkeypatch):
    _configure(monkeypatch)
    monkeypatch.setenv("RENAISS_TRACKING_CREATE_TIMEOUT_SECONDS", "0.01")

    async def never_returns():
        await asyncio.Event().wait()

    monkeypatch.setattr("renaiss_bot.services.tracking.get_db", never_returns)
    destination = "https://index.renaissos.com/cards/charizard"

    assert await asyncio.wait_for(
        build_tracked_url(
            destination,
            user_id=1,
            chat_id=2,
            local_card_id=None,
            source="test",
        ),
        timeout=0.2,
    ) == destination


async def test_unknown_tracking_token_is_rejected(monkeypatch):
    _configure(monkeypatch)
    pool = FakePool()

    async def get_pool():
        return pool

    monkeypatch.setattr("renaiss_bot.services.tracking.get_db", get_pool)
    with pytest.raises(TrackingTokenError, match="expired"):
        await resolve_tracking_token("unknown-token")


async def test_redirect_still_reaches_renaiss_when_click_logging_fails(monkeypatch):
    click = SimpleNamespace(
        destination_url="https://index.renaissos.com/cards/charizard",
        user_id=7,
        chat_id=-1001,
        local_card_id="card-4",
        source="telegram_price",
        token_hash="hash",
    )

    async def resolve(token):
        return click

    async def fail_log(**kwargs):
        raise RuntimeError("db unavailable")

    monkeypatch.setattr("renaiss_bot.referral_server.resolve_tracking_token", resolve)
    monkeypatch.setattr("renaiss_bot.referral_server.log_referral_click", fail_log)
    with pytest.raises(web.HTTPFound) as raised:
        await redirect(SimpleNamespace(match_info={"token": "opaque"}))
    assert raised.value.location == "https://index.renaissos.com/cards/charizard"


async def test_redirect_uses_signed_destination_during_registry_outage(monkeypatch):
    _configure(monkeypatch)
    pool = FakePool()

    async def get_pool():
        return pool

    monkeypatch.setattr("renaiss_bot.services.tracking.get_db", get_pool)
    url = await build_tracked_url(
        "https://index.renaissos.com/cards/charizard",
        user_id=7,
        chat_id=-1001,
        local_card_id="card-4",
        source="telegram_price",
    )
    parsed = urlparse(url or "")
    query = {key: values[0] for key, values in parse_qs(parsed.query).items()}

    async def fail_resolve(token):
        raise RuntimeError("db unavailable")

    monkeypatch.setattr("renaiss_bot.referral_server.resolve_tracking_token", fail_resolve)
    with pytest.raises(web.HTTPFound) as raised:
        await redirect(
            SimpleNamespace(
                match_info={"token": _token(url or "")},
                query=query,
            )
        )
    assert raised.value.location == "https://index.renaissos.com/cards/charizard"


async def test_redirect_times_out_registry_lookup_and_uses_signed_fallback(monkeypatch):
    destination = "https://index.renaissos.com/cards/charizard"
    monkeypatch.setenv("RENAISS_CLICK_DB_TIMEOUT_SECONDS", "0.1")

    async def never_resolves(token):
        await asyncio.Event().wait()

    monkeypatch.setattr(
        "renaiss_bot.referral_server.resolve_tracking_token",
        never_resolves,
    )
    monkeypatch.setattr(
        "renaiss_bot.referral_server.validate_fallback_destination",
        lambda token, to, exp, sig: destination,
    )

    with pytest.raises(web.HTTPFound) as raised:
        await redirect(
            SimpleNamespace(
                match_info={"token": "opaque"},
                query={"to": destination, "exp": "1", "sig": "signed"},
            )
        )

    assert raised.value.location == destination


async def test_redirect_times_out_click_logging_without_blocking_destination(monkeypatch):
    destination = "https://index.renaissos.com/cards/charizard"
    monkeypatch.setenv("RENAISS_CLICK_DB_TIMEOUT_SECONDS", "0.1")
    click = SimpleNamespace(
        destination_url=destination,
        user_id=7,
        chat_id=-1001,
        local_card_id="card-4",
        source="telegram_price",
        token_hash="hash",
    )

    async def resolve(token):
        return click

    async def never_logs(**kwargs):
        await asyncio.Event().wait()

    monkeypatch.setattr("renaiss_bot.referral_server.resolve_tracking_token", resolve)
    monkeypatch.setattr("renaiss_bot.referral_server.log_referral_click", never_logs)

    with pytest.raises(web.HTTPFound) as raised:
        await redirect(SimpleNamespace(match_info={"token": "opaque"}))

    assert raised.value.location == destination
