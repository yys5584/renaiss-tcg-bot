"""The raw probe must use the same credential and quota boundaries as runtime."""

from __future__ import annotations

from contextlib import asynccontextmanager

import renaiss_bot.tools.probe_index as probe_module


class _Response:
    status = 200

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return None

    def raise_for_status(self):
        return None

    async def json(self, *, content_type=None):
        return {"results": []}


class _Session:
    def __init__(self, capture, **kwargs):
        self.capture = capture
        self.capture["session_kwargs"] = kwargs

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return None

    def get(self, url, **kwargs):
        self.capture["url"] = url
        self.capture["get_kwargs"] = kwargs
        return _Response()


async def test_probe_uses_allowlisted_base_no_redirects_timeout_and_request_gate(
    monkeypatch,
):
    capture = {"gate": []}

    @asynccontextmanager
    async def gate(*, timeout_seconds):
        capture["gate"].append(timeout_seconds)
        yield

    monkeypatch.setattr(probe_module, "_api_base", lambda: "https://api.renaissos.com")
    monkeypatch.setattr(
        probe_module,
        "_search_url",
        lambda card: "https://api.renaissos.com/v1/search?q=Charizard",
    )
    monkeypatch.setattr(probe_module, "_partner_request_guard", gate)
    monkeypatch.setattr(
        probe_module.aiohttp,
        "ClientSession",
        lambda **kwargs: _Session(capture, **kwargs),
    )

    await probe_module.probe("Charizard")

    assert capture["gate"] == [5.0]
    assert capture["get_kwargs"] == {"allow_redirects": False}
    assert capture["session_kwargs"]["timeout"].total == 5.0
