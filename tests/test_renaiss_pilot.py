"""Default pilot UX excludes the legacy Pokémon-bot economy."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import date, datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from renaiss_bot.database.queries import (
    PackOpenReservationError,
    PackOpenReservation,
    finalize_command_free_pack,
    reserve_command_free_packs,
)
from renaiss_bot.handlers.cardpack import cmd_open, cmd_pack
from renaiss_bot.services.card_pool import build_pack
from renaiss_bot.services.models import CardIdentity, PackOpenResult, RenaissPrice, card_identity_key
from renaiss_bot.services.pack_rules import CARDS_PER_PACK
from renaiss_bot.services.pack import open_pack
from renaiss_bot.services.portfolio import _build_achievements


@pytest.fixture(autouse=True)
def _enable_private_pack_experiment(monkeypatch):
    """Legacy pack tests opt in; production and unscoped tests remain closed."""
    monkeypatch.setenv("RENAISS_PRIVATE_FREE_PACKS_ENABLED", "1")


class FakeMessage:
    def __init__(self):
        self.replies = []
        self.reply_kwargs = []

    async def reply_text(self, text, **kwargs):
        self.replies.append(text)
        self.reply_kwargs.append(kwargs)

    async def reply_photo(self, **kwargs):
        self.replies.append(kwargs.get("caption"))


class _AsyncContext:
    def __init__(self, value):
        self.value = value

    async def __aenter__(self):
        return self.value

    async def __aexit__(self, *args):
        return None


class _PackConnection:
    def __init__(self, *, fetchvals=None, fetchrows=None):
        self.fetchval = AsyncMock(side_effect=fetchvals or [])
        self.fetchrow = AsyncMock(side_effect=fetchrows or [])
        self.execute = AsyncMock(return_value="UPDATE 1")
        self.executemany = AsyncMock()

    def transaction(self):
        return _AsyncContext(self)


class _PackPool:
    def __init__(self, connection):
        self.connection = connection

    def acquire(self):
        return _AsyncContext(self.connection)


def _pack_result() -> PackOpenResult:
    card = CardIdentity(
        category="pokemon_tcg",
        card_name="Charizard",
        grade="R",
        local_card_id="card-1",
        market_price_usd=50,
    )
    return PackOpenResult(
        category="pokemon_tcg",
        cards=[card for _ in range(10)],
        best_card=card,
        best_price=RenaissPrice(status="candidate", source="catalog", fmv_usd=50),
        pack_type="free",
        pack_count=1,
        pool_source="catalog",
    )


def test_small_catalog_still_builds_the_promised_pack_size():
    card = _pack_result().best_card

    cards, _ = build_pack([card], "pokemon_tcg", "free")

    assert len(cards) == CARDS_PER_PACK


async def test_disabled_category_fails_before_loading_any_pack_pool(monkeypatch):
    loader = AsyncMock()
    monkeypatch.setattr("renaiss_bot.services.pack.load_card_pool", loader)

    with pytest.raises(ValueError, match="not enabled"):
        await open_pack(user_id=7, category_key="other_renaiss_cards")

    loader.assert_not_awaited()


def test_malformed_negative_catalog_price_cannot_break_pack_weights():
    card = CardIdentity(
        category="pokemon_tcg",
        card_name="Malformed",
        grade="R",
        local_card_id="malformed-1",
        market_price_usd=-10_000,
    )

    cards, _ = build_pack([card], "pokemon_tcg", "free")

    assert len(cards) == CARDS_PER_PACK


async def test_telegram_disabled_category_does_not_reserve_quota(monkeypatch):
    message = FakeMessage()
    update = SimpleNamespace(
        effective_message=message,
        effective_user=SimpleNamespace(id=7),
        effective_chat=SimpleNamespace(id=11, type="private"),
    )
    reserve = AsyncMock()
    monkeypatch.setattr(
        "renaiss_bot.handlers.cardpack.reserve_command_free_packs",
        reserve,
    )

    await cmd_open(update, SimpleNamespace(args=["other"]))

    reserve.assert_not_awaited()
    assert "not open for packs" in message.replies[0]


async def test_private_pack_is_closed_by_default_before_database_work(monkeypatch):
    monkeypatch.delenv("RENAISS_PRIVATE_FREE_PACKS_ENABLED", raising=False)
    message = FakeMessage()
    update = SimpleNamespace(
        effective_message=message,
        effective_user=SimpleNamespace(id=7),
        effective_chat=SimpleNamespace(id=11, type="private"),
    )
    reserve = AsyncMock()
    monkeypatch.setattr(
        "renaiss_bot.handlers.cardpack.reserve_command_free_packs",
        reserve,
    )

    await cmd_open(update, SimpleNamespace(args=[]))

    reserve.assert_not_awaited()
    assert "closed during the collector-market pilot" in message.replies[0]
    assert "<code>c</code>" in message.replies[0]
    assert "no physical card or NFT ownership" in message.replies[0]


async def test_pack_guide_has_no_rp_premium_or_legacy_keys(monkeypatch):
    monkeypatch.delenv("RENAISS_PACK_ECONOMY_ENABLED", raising=False)
    message = FakeMessage()
    update = SimpleNamespace(effective_message=message)
    await cmd_pack(update, SimpleNamespace())
    text = message.replies[0]
    assert "RP" not in text
    assert "premium" not in text.lower()
    assert "<code>d</code>" not in text
    assert "<code>f</code>" not in text
    assert "<code>c</code>" in text


async def test_premium_pack_is_closed_before_database_work(monkeypatch):
    monkeypatch.delenv("RENAISS_PACK_ECONOMY_ENABLED", raising=False)
    message = FakeMessage()
    update = SimpleNamespace(
        effective_message=message,
        effective_user=SimpleNamespace(id=7),
        effective_chat=SimpleNamespace(id=11),
    )
    await cmd_open(update, SimpleNamespace(args=["premium"]))
    assert "not part of the current Renaiss pilot" in message.replies[0]
    assert "<code>c</code>" in message.replies[0]


async def test_premium_pack_stays_closed_even_with_legacy_flag(monkeypatch):
    monkeypatch.setenv("RENAISS_PACK_ECONOMY_ENABLED", "1")
    message = FakeMessage()
    update = SimpleNamespace(
        effective_message=message,
        effective_user=SimpleNamespace(id=7),
        effective_chat=SimpleNamespace(id=11),
    )

    await cmd_open(update, SimpleNamespace(args=["premium"]))

    assert "not part of the current Renaiss pilot" in message.replies[0]


async def test_group_open_routes_to_private_chat_without_reserving(monkeypatch):
    message = FakeMessage()
    update = SimpleNamespace(
        effective_message=message,
        effective_user=SimpleNamespace(id=7),
        effective_chat=SimpleNamespace(id=-1001, type="group"),
    )
    reserve = AsyncMock()
    monkeypatch.setattr(
        "renaiss_bot.handlers.cardpack.reserve_command_free_packs",
        reserve,
    )

    await cmd_open(
        update,
        SimpleNamespace(args=[], bot=SimpleNamespace(username="renaiss_test_bot")),
    )

    reserve.assert_not_awaited()
    assert "private chat" in message.replies[0]
    button = message.reply_kwargs[0]["reply_markup"].inline_keyboard[0][0]
    assert button.url == "https://t.me/renaiss_test_bot?start=open_pokemon_tcg"


async def test_free_pack_reservation_uses_user_lock_and_caps_remaining_quota(monkeypatch):
    quota_date = date(2026, 7, 11)
    inserted = {
        "request_id": "telegram:1:open",
        "pack_count": 1,
        "used_before": 4,
        "quota_date": quota_date,
        "status": "reserved",
    }
    connection = _PackConnection(
        fetchvals=[None, quota_date, 4, 0],
        fetchrows=[None, inserted],
    )
    monkeypatch.setattr(
        "renaiss_bot.database.queries.get_db",
        AsyncMock(return_value=_PackPool(connection)),
    )

    reservation = await reserve_command_free_packs(
        request_id="telegram:1:open",
        user_id=7,
        chat_id=11,
        platform="telegram",
        category="pokemon_tcg",
        requested_count=5,
        daily_limit=5,
    )

    assert reservation.created and reservation.allowed_count == 1
    assert reservation.used_before == 4
    lock_sql = connection.fetchval.await_args_list[0].args[0]
    assert "pg_advisory_xact_lock" in lock_sql
    insert_sql = connection.fetchrow.await_args_list[1].args[0]
    assert "renaiss_pack_open_requests" in insert_sql
    legacy_sql = connection.fetchval.await_args_list[2].args[0]
    ledger_sql = connection.fetchval.await_args_list[3].args[0]
    assert "request_id IS NULL" in legacy_sql
    assert "status = 'completed'" in ledger_sql


async def test_free_pack_finalize_writes_cards_event_and_completion_atomically(monkeypatch):
    reservation = {
        "request_id": "telegram:1:open",
        "user_id": 7,
        "chat_id": 11,
        "category": "pokemon_tcg",
        "pack_count": 1,
        "status": "reserved",
        "lease_active": True,
    }
    connection = _PackConnection(
        fetchvals=[None],
        fetchrows=[reservation, {"request_id": "telegram:1:open"}],
    )
    monkeypatch.setattr(
        "renaiss_bot.database.queries.get_db",
        AsyncMock(return_value=_PackPool(connection)),
    )

    assert await finalize_command_free_pack(
        request_id="telegram:1:open",
        user_id=7,
        chat_id=11,
        result=_pack_result(),
    )

    assert "pg_advisory_xact_lock" in connection.fetchval.await_args.args[0]
    connection.executemany.assert_awaited_once()
    assert "renaiss_pack_events" in connection.execute.await_args.args[0]
    assert connection.execute.await_args.args[-1] == "telegram:1:open"
    assert "status = 'completed'" in connection.fetchrow.await_args_list[1].args[0]


async def test_free_pack_best_card_stores_verified_acquisition_snapshot(monkeypatch):
    reservation = {
        "request_id": "telegram:2:open",
        "user_id": 7,
        "chat_id": 11,
        "category": "pokemon_tcg",
        "pack_count": 1,
        "status": "reserved",
        "lease_active": True,
    }
    connection = _PackConnection(
        fetchvals=[None],
        fetchrows=[reservation, {"request_id": "telegram:2:open"}],
    )
    monkeypatch.setattr(
        "renaiss_bot.database.queries.get_db",
        AsyncMock(return_value=_PackPool(connection)),
    )
    card = CardIdentity(
        category="pokemon_tcg",
        card_name="Charizard",
        grade="RAW",
        set_name="Base Set",
        collector_number="4/102",
        language="English",
        local_card_id="catalog:pokemon_tcg:charizard",
        market_price_usd=900,
    )
    result = PackOpenResult(
        category="pokemon_tcg",
        cards=[card for _ in range(CARDS_PER_PACK)],
        best_card=card,
        best_price=RenaissPrice(
            status="exact",
            source="renaiss-index-api:item-by-no",
            fmv_usd=20,
            confidence="high",
            confidence_score=0.95,
            source_count=3,
            valuation_method="median",
            asset_url="https://www.renaiss.xyz/assets/charizard",
                price_updated_at=datetime.now(timezone.utc),
                source_identity_key=card_identity_key(card),
        ),
        pack_type="free",
        pack_count=1,
        pool_source="catalog",
    )

    assert await finalize_command_free_pack(
        request_id="telegram:2:open",
        user_id=7,
        chat_id=11,
        result=result,
    )

    rows = connection.executemany.await_args.args[1]
    assert rows[0][8] == 20
    assert "market_price_usd = COALESCE" in connection.executemany.await_args.args[0]


async def test_free_pack_finalize_failure_propagates_before_success(monkeypatch):
    reservation = {
        "request_id": "telegram:1:open",
        "user_id": 7,
        "chat_id": 11,
        "category": "pokemon_tcg",
        "pack_count": 1,
        "status": "reserved",
        "lease_active": True,
    }
    connection = _PackConnection(fetchvals=[None], fetchrows=[reservation])
    connection.executemany.side_effect = RuntimeError("card write failed")
    monkeypatch.setattr(
        "renaiss_bot.database.queries.get_db",
        AsyncMock(return_value=_PackPool(connection)),
    )

    with pytest.raises(RuntimeError, match="card write failed"):
        await finalize_command_free_pack(
            request_id="telegram:1:open",
            user_id=7,
            chat_id=11,
            result=_pack_result(),
        )


async def test_free_pack_finalize_rejects_a_partial_card_batch(monkeypatch):
    reservation = {
        "request_id": "telegram:1:open",
        "user_id": 7,
        "chat_id": 11,
        "category": "pokemon_tcg",
        "pack_count": 1,
        "status": "reserved",
        "lease_active": True,
    }
    connection = _PackConnection(fetchvals=[None], fetchrows=[reservation])
    monkeypatch.setattr(
        "renaiss_bot.database.queries.get_db",
        AsyncMock(return_value=_PackPool(connection)),
    )
    full = _pack_result()
    partial = replace(full, cards=full.cards[:1])

    with pytest.raises(PackOpenReservationError, match="card count"):
        await finalize_command_free_pack(
            request_id="telegram:1:open",
            user_id=7,
            chat_id=11,
            result=partial,
        )

    connection.executemany.assert_not_awaited()
    connection.execute.assert_not_awaited()


async def test_completed_pack_finalize_retry_does_not_write_again(monkeypatch):
    completed = {
        "request_id": "telegram:1:open",
        "user_id": 7,
        "chat_id": 11,
        "category": "pokemon_tcg",
        "pack_count": 1,
        "status": "completed",
        "lease_active": False,
    }
    connection = _PackConnection(fetchvals=[None], fetchrows=[completed])
    monkeypatch.setattr(
        "renaiss_bot.database.queries.get_db",
        AsyncMock(return_value=_PackPool(connection)),
    )

    assert not await finalize_command_free_pack(
        request_id="telegram:1:open",
        user_id=7,
        chat_id=11,
        result=_pack_result(),
    )
    connection.executemany.assert_not_awaited()
    connection.execute.assert_not_awaited()


async def test_open_fails_closed_when_quota_cannot_be_verified(monkeypatch):
    message = FakeMessage()
    update = SimpleNamespace(
        update_id=101,
        effective_message=message,
        effective_user=SimpleNamespace(id=7),
        effective_chat=SimpleNamespace(id=11),
        callback_query=None,
    )
    open_mock = AsyncMock()
    monkeypatch.setattr(
        "renaiss_bot.handlers.cardpack.reserve_command_free_packs",
        AsyncMock(side_effect=RuntimeError("db down")),
    )
    monkeypatch.setattr("renaiss_bot.handlers.cardpack.open_pack", open_mock)

    await cmd_open(update, SimpleNamespace(args=[]))

    open_mock.assert_not_awaited()
    assert "quota could not be verified" in message.replies[0]
    assert "No pack was opened" in message.replies[0]


async def test_open_announces_only_after_atomic_finalize(monkeypatch):
    events = []
    message = FakeMessage()

    async def reply_text(text, **kwargs):
        events.append("reply")
        message.replies.append(text)

    message.reply_text = reply_text
    update = SimpleNamespace(
        update_id=102,
        effective_message=message,
        effective_user=SimpleNamespace(id=7),
        effective_chat=SimpleNamespace(id=11),
        callback_query=None,
    )
    reservation = PackOpenReservation(
        request_id="telegram:102:open",
        status="reserved",
        allowed_count=1,
        used_before=0,
        quota_date=date(2026, 7, 11),
        created=True,
    )

    async def generate(**kwargs):
        events.append("generate")
        return _pack_result()

    async def finalize(**kwargs):
        events.append("finalize")
        return True

    monkeypatch.setattr(
        "renaiss_bot.handlers.cardpack.reserve_command_free_packs",
        AsyncMock(return_value=reservation),
    )
    monkeypatch.setattr("renaiss_bot.handlers.cardpack.open_pack", generate)
    monkeypatch.setattr("renaiss_bot.handlers.cardpack.finalize_command_free_pack", finalize)
    tracking_started = asyncio.Event()
    rendering_started = asyncio.Event()

    async def track(*args, **kwargs):
        tracking_started.set()
        await rendering_started.wait()
        return None

    async def render(*args, **kwargs):
        rendering_started.set()
        await tracking_started.wait()
        return None

    monkeypatch.setattr("renaiss_bot.handlers.cardpack.build_tracked_url", track)
    monkeypatch.setattr("renaiss_bot.handlers.cardpack.render_overlay_card", render)

    await asyncio.wait_for(cmd_open(update, SimpleNamespace(args=[])), timeout=0.2)

    assert events == ["generate", "finalize", "reply"]
    assert tracking_started.is_set() and rendering_started.is_set()
    assert "Free packs left today" in message.replies[0]


async def test_open_recovers_a_completed_pack_after_uncertain_finalize(monkeypatch):
    message = FakeMessage()
    update = SimpleNamespace(
        update_id=103,
        effective_message=message,
        effective_user=SimpleNamespace(id=7),
        effective_chat=SimpleNamespace(id=11, type="private"),
        callback_query=None,
    )
    reserved = PackOpenReservation(
        request_id="telegram:103:open",
        status="reserved",
        allowed_count=1,
        used_before=0,
        quota_date=date(2026, 7, 11),
        created=True,
    )
    completed = replace(reserved, status="completed", created=False)
    monkeypatch.setattr(
        "renaiss_bot.handlers.cardpack.reserve_command_free_packs",
        AsyncMock(side_effect=[reserved, completed]),
    )
    monkeypatch.setattr(
        "renaiss_bot.handlers.cardpack.open_pack",
        AsyncMock(return_value=_pack_result()),
    )
    monkeypatch.setattr(
        "renaiss_bot.handlers.cardpack.finalize_command_free_pack",
        AsyncMock(side_effect=RuntimeError("commit acknowledgement lost")),
    )
    monkeypatch.setattr(
        "renaiss_bot.handlers.cardpack.build_tracked_url",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(
        "renaiss_bot.handlers.cardpack.render_overlay_card",
        AsyncMock(return_value=None),
    )

    await cmd_open(update, SimpleNamespace(args=[]))

    assert "Free packs left today" in message.replies[0]
    assert "could not be safely saved" not in message.replies[0]


async def test_open_reuses_telegram_file_id_without_rendering(monkeypatch):
    message = SimpleNamespace(
        message_id=44,
        reply_text=AsyncMock(),
        reply_photo=AsyncMock(return_value=SimpleNamespace(photo=[])),
    )
    update = SimpleNamespace(
        update_id=104,
        effective_message=message,
        effective_user=SimpleNamespace(id=7),
        effective_chat=SimpleNamespace(id=11, type="private"),
        callback_query=None,
    )
    reservation = PackOpenReservation(
        request_id="telegram:104:open",
        status="reserved",
        allowed_count=1,
        used_before=0,
        quota_date=date(2026, 7, 11),
        created=True,
    )
    render = AsyncMock(return_value=b"should-not-render")
    monkeypatch.setattr(
        "renaiss_bot.handlers.cardpack.reserve_command_free_packs",
        AsyncMock(return_value=reservation),
    )
    monkeypatch.setattr(
        "renaiss_bot.handlers.cardpack.open_pack",
        AsyncMock(return_value=_pack_result()),
    )
    monkeypatch.setattr(
        "renaiss_bot.handlers.cardpack.finalize_command_free_pack",
        AsyncMock(return_value=True),
    )
    monkeypatch.setattr(
        "renaiss_bot.handlers.cardpack.get_telegram_file_id",
        AsyncMock(return_value="telegram-cached-file"),
    )
    monkeypatch.setattr("renaiss_bot.handlers.cardpack.render_overlay_card", render)
    monkeypatch.setattr(
        "renaiss_bot.handlers.cardpack.build_tracked_url",
        AsyncMock(return_value=None),
    )

    await cmd_open(update, SimpleNamespace(args=[]))

    render.assert_not_awaited()
    assert message.reply_photo.await_args.kwargs["photo"] == "telegram-cached-file"


def test_collection_achievements_do_not_reward_accumulated_wealth():
    achievements = _build_achievements(
        {
            "total_value_usd": 1_000_000,
            "max_card_value_usd": 100_000,
            "total_cards": 1,
            "unique_cards": 1,
            "categories": 1,
            "sets": 1,
        }
    )

    titles = {achievement.title for achievement in achievements}
    assert titles == {
        "First Pull",
        "Collector I",
        "Collector II",
        "Collector III",
        "Cross-Market Collector",
        "Set Hunter",
    }
    assert all("$" not in title and "Portfolio" not in title for title in titles)
