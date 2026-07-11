"""Liveness and readiness contracts for the standalone click tracker."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

import renaiss_bot.referral_server as referral_server


class _Acquire:
    def __init__(self, connection):
        self.connection = connection

    async def __aenter__(self):
        return self.connection

    async def __aexit__(self, *args):
        return None


class _Connection:
    async def fetchval(self, query):
        assert query == "SELECT 1"
        return 1


class _Pool:
    def acquire(self):
        return _Acquire(_Connection())


def _payload(response):
    return json.loads(response.text)


async def test_livez_does_not_touch_database(monkeypatch):
    async def unexpected_database_call():
        raise AssertionError("liveness must not depend on the database")

    monkeypatch.setattr(referral_server, "get_db", unexpected_database_call)

    response = await referral_server.livez(SimpleNamespace())

    assert response.status == 200
    assert _payload(response) == {"ok": True}


@pytest.mark.parametrize("handler", [referral_server.readyz, referral_server.health])
async def test_readiness_endpoints_report_database_success(monkeypatch, handler):
    async def get_pool():
        return _Pool()

    monkeypatch.setattr(referral_server, "get_db", get_pool)

    response = await handler(SimpleNamespace())

    assert response.status == 200
    assert _payload(response) == {"ok": True, "db": True}


@pytest.mark.parametrize("handler", [referral_server.readyz, referral_server.health])
async def test_readiness_endpoints_fail_closed_on_database_error(monkeypatch, handler):
    async def fail_database():
        raise RuntimeError("database unavailable")

    monkeypatch.setattr(referral_server, "get_db", fail_database)

    response = await handler(SimpleNamespace())

    assert response.status == 503
    assert _payload(response) == {"ok": False, "db": False}


def test_tracker_registers_liveness_and_readiness_routes():
    app = referral_server.create_app()
    paths = {resource.canonical for resource in app.router.resources()}

    assert {"/livez", "/readyz", "/health"} <= paths
