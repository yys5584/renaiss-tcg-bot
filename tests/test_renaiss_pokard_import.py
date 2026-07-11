"""POKARD imports must retain the structural tuple needed by Renaiss lookup."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock
from urllib.parse import parse_qs, urlparse

from renaiss_bot.services.client import _structural_url
from renaiss_bot.tools.import_from_pokard import _INSERT, _card_identity, _card_row, run


class _AsyncContext:
    def __init__(self, value):
        self.value = value

    async def __aenter__(self):
        return self.value

    async def __aexit__(self, *args):
        return None


class _Connection:
    def __init__(self):
        self.execute = AsyncMock()

    def transaction(self):
        return _AsyncContext(self)


class _Pool:
    def __init__(self):
        self.connection = _Connection()

    def acquire(self):
        return _AsyncContext(self.connection)


def test_pokard_identity_can_use_structural_partner_lookup(monkeypatch):
    monkeypatch.setenv("RENAISS_API_BASE_URL", "https://api.renaissos.com")
    monkeypatch.setenv("RENAISS_API_ITEM_BY_NO_PATH", "/v1/index/item-by-no")
    card = _card_identity(
        {
            "name": "Charizard",
            "set_id": "BS",
            "set_name": "Base Set",
            "number": "4/102",
            "language": "English",
            "rarity": "Rare Holo",
            "variation": "shadowless",
        }
    )

    url = _structural_url(card)
    assert url is not None
    params = parse_qs(urlparse(url).query, keep_blank_values=True)
    assert params["set_name"] == ["Base Set"]
    assert params["item_no"] == ["4/102"]
    assert params["language"] == ["en"]
    assert params["variation"] == ["shadowless"]


def test_pokard_fallback_id_distinguishes_variations():
    first = _card_row(
        {
            "name": "Charizard",
            "set_id": "BS",
            "number": "4/102",
            "variation": "unlimited",
        },
        None,
    )
    shadowless = _card_row(
        {
            "name": "Charizard",
            "set_id": "BS",
            "number": "4/102",
            "variation": "shadowless",
        },
        None,
    )

    assert first["local_card_id"] != shadowless["local_card_id"]
    assert first["local_card_id"].startswith("pokard:pokemon_tcg:")
    assert first["metadata"]["evidence_origin"] == "official-api-import"
    assert first["metadata"]["variation"] == "unlimited"
    assert shadowless["metadata"]["variation"] == "shadowless"


async def test_pokard_dry_run_never_opens_database(monkeypatch):
    session = SimpleNamespace(close=AsyncMock())
    monkeypatch.setattr("renaiss_bot.tools.import_from_pokard.make_session", lambda: session)
    monkeypatch.setattr(
        "renaiss_bot.tools.import_from_pokard.list_cards",
        AsyncMock(
            return_value={
                "cards": [
                    {
                        "name": "Charizard",
                        "set_id": "BS",
                        "set_name": "Base Set",
                        "number": "4/102",
                    }
                ]
            }
        ),
    )
    monkeypatch.setattr(
        "renaiss_bot.tools.import_from_pokard.fetch_official_price",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(
        "renaiss_bot.database.connection.get_db",
        AsyncMock(side_effect=AssertionError("dry-run must not open PostgreSQL")),
    )
    args = SimpleNamespace(
        dry_run=True,
        max_cards=1,
        limit=1,
        series=None,
        gen=None,
        sleep=0,
    )

    assert await run(args) == 0
    session.close.assert_awaited_once()


async def test_partner_error_aborts_batch_before_any_catalog_write(monkeypatch):
    session = SimpleNamespace(close=AsyncMock())
    pool = _Pool()
    monkeypatch.setattr("renaiss_bot.tools.import_from_pokard.make_session", lambda: session)
    monkeypatch.setattr(
        "renaiss_bot.tools.import_from_pokard.list_cards",
        AsyncMock(
            return_value={
                "cards": [
                    {
                        "name": "Charizard",
                        "set_id": "BS",
                        "set_name": "Base Set",
                        "number": "4/102",
                    }
                ]
            }
        ),
    )
    monkeypatch.setattr(
        "renaiss_bot.tools.import_from_pokard.fetch_official_price",
        AsyncMock(side_effect=RuntimeError("temporary Partner failure")),
    )
    monkeypatch.setattr("renaiss_bot.database.connection.get_db", AsyncMock(return_value=pool))
    monkeypatch.setattr("renaiss_bot.database.connection.close_db", AsyncMock())
    monkeypatch.setattr("renaiss_bot.database.schema.create_tables", AsyncMock())
    args = SimpleNamespace(
        dry_run=False,
        max_cards=1,
        limit=1,
        series=None,
        gen=None,
        sleep=0,
    )

    assert await run(args) == 1
    pool.connection.execute.assert_not_awaited()
    session.close.assert_awaited_once()


def test_unpriced_refresh_preserves_existing_price_evidence():
    assert "COALESCE(EXCLUDED.market_price_usd" in _INSERT
    assert "WHEN EXCLUDED.market_price_usd IS NULL" in _INSERT
    assert "'variation', EXCLUDED.metadata->>'variation'" in _INSERT
    assert "COALESCE(EXCLUDED.metadata->>'variation', '') <> ''" in _INSERT
