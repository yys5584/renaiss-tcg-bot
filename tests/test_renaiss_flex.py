"""Renaiss 플렉스 카드 스토리/캡션 단위 테스트 (DB 불필요)."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from renaiss_bot.handlers.flex import cmd_flex, on_flex_props
from renaiss_bot.database.queries import get_flex_card, reserve_daily_flex
from renaiss_bot.services.flex import build_flex_caption
from renaiss_bot.services.features import pack_economy_enabled


@pytest.mark.parametrize("grade", ["MUR", "UR", "SAR", "AR", "RR", "R"])
def test_flex_never_claims_acquisition_odds_without_provenance(grade):
    caption = build_flex_caption(
        display_name="Moonyu",
        card_name="Charizard ex",
        grade=grade,
        market_usd=430.0,
    )
    assert "1-in-" not in caption
    assert "roughly" not in caption


def test_build_flex_caption_includes_core_fields():
    caption = build_flex_caption(
        display_name="Moonyu",
        card_name="Charizard ex",
        grade="SAR",
        market_usd=430.0,
    )
    assert "Moonyu" in caption
    assert "Charizard ex" in caption
    assert "SAR" in caption
    assert "$430" not in caption
    assert "Collection reference" not in caption
    assert "Props" in caption
    assert "no physical card or NFT ownership" in caption


def test_legacy_pack_economy_is_off_by_default(monkeypatch):
    monkeypatch.delenv("RENAISS_PACK_ECONOMY_ENABLED", raising=False)
    assert not pack_economy_enabled()
    caption = build_flex_caption(
        display_name="Moonyu",
        card_name="Charizard ex",
        grade="SAR",
        market_usd=430.0,
    )
    assert "earn RP" not in caption


def test_legacy_pack_economy_cannot_be_reenabled(monkeypatch):
    monkeypatch.setenv("RENAISS_PACK_ECONOMY_ENABLED", "1")
    assert not pack_economy_enabled()


async def test_props_do_not_award_rp_in_default_pilot(monkeypatch):
    monkeypatch.delenv("RENAISS_PACK_ECONOMY_ENABLED", raising=False)
    add_prop = AsyncMock(return_value=1)
    add_points = AsyncMock()
    monkeypatch.setattr("renaiss_bot.handlers.flex.add_flex_prop", add_prop)
    monkeypatch.setattr("renaiss_bot.handlers.flex.add_drop_points", add_points)
    monkeypatch.setattr("renaiss_bot.handlers.flex.official_chat_id", lambda: -1001)
    query = SimpleNamespace(
        data="renaiss:props:10",
        message=SimpleNamespace(chat_id=-1001, message_id=42),
        answer=AsyncMock(),
    )
    update = SimpleNamespace(
        callback_query=query,
        effective_user=SimpleNamespace(id=20),
    )
    await on_flex_props(update, SimpleNamespace())
    add_points.assert_not_awaited()
    assert query.answer.await_args.args[0] == "👏 Props sent!"


def test_build_flex_caption_hides_zero_values():
    caption = build_flex_caption(
        display_name="Rook",
        card_name="Snorlax",
        grade="R",
        market_usd=0,
    )
    assert "Rook" in caption
    assert "Snorlax" in caption
    # 시세 0 이면 collection reference 줄이 없어야 한다
    assert "Collection reference" not in caption


def test_build_flex_caption_escapes_html():
    caption = build_flex_caption(
        display_name="<b>hax</b>",
        card_name="A & B <script>",
        grade="R",
        market_usd=10,
    )
    assert "<b>hax</b>" not in caption
    assert "&lt;b&gt;hax&lt;/b&gt;" in caption
    assert "&amp;" in caption


async def test_concurrent_flex_commands_reserve_once_before_public_send(monkeypatch):
    card = {
        "category": "pokemon_tcg",
        "local_card_id": "card-1",
        "card_name": "Charizard ex",
        "grade": "SAR",
        "set_code": "SV4a",
        "collector_number": "349/190",
        "image_url": None,
        "market_price_usd": 430,
    }
    reserve = AsyncMock(
        side_effect=[
            {"state": "reserved"},
            {"state": "user_cooldown", "retry_after_seconds": 42},
        ]
    )
    complete = AsyncMock(return_value=True)
    monkeypatch.setattr("renaiss_bot.handlers.flex.get_flex_card", AsyncMock(return_value=card))
    monkeypatch.setattr("renaiss_bot.handlers.flex.render_overlay_card", AsyncMock(return_value=None))
    monkeypatch.setattr("renaiss_bot.handlers.flex.reserve_daily_flex", reserve)
    monkeypatch.setattr("renaiss_bot.handlers.flex.complete_daily_flex", complete)
    monkeypatch.setattr("renaiss_bot.handlers.flex.official_chat_id", lambda: -1001)

    class Message:
        def __init__(self):
            self.sent = []
            self.delete = AsyncMock()

        async def reply_text(self, text, **kwargs):
            self.sent.append((text, kwargs))
            return SimpleNamespace(message_id=77)

    messages = [Message(), Message()]
    updates = [
        SimpleNamespace(
            effective_message=message,
            effective_user=SimpleNamespace(id=10, full_name="Rookie"),
            effective_chat=SimpleNamespace(id=-1001, type="supergroup"),
        )
        for message in messages
    ]

    private_send = AsyncMock()
    contexts = [SimpleNamespace(bot=SimpleNamespace(send_message=private_send)) for _ in updates]
    await asyncio.gather(
        *(cmd_flex(update, context) for update, context in zip(updates, contexts))
    )

    assert reserve.await_count == 2
    complete.assert_awaited_once()
    all_text = [text for message in messages for text, _ in message.sent]
    assert sum("is flexing" in text for text in all_text) == 1
    assert sum("ready again" in text for text in all_text) == 0
    assert "ready again in about 42s" in private_send.await_args.kwargs["text"]


async def test_cancelled_flex_render_releases_unsent_reservation(monkeypatch):
    card = {
        "category": "pokemon_tcg",
        "local_card_id": "card-1",
        "card_name": "Charizard ex",
        "grade": "SAR",
        "market_price_usd": 430,
    }
    release = AsyncMock(return_value=True)

    async def cancelled_render(*args, **kwargs):
        raise asyncio.CancelledError

    monkeypatch.setattr("renaiss_bot.handlers.flex.official_chat_id", lambda: -1001)
    monkeypatch.setattr("renaiss_bot.handlers.flex.get_flex_card", AsyncMock(return_value=card))
    monkeypatch.setattr(
        "renaiss_bot.handlers.flex.reserve_daily_flex",
        AsyncMock(return_value={"state": "reserved"}),
    )
    monkeypatch.setattr("renaiss_bot.handlers.flex.render_overlay_card", cancelled_render)
    monkeypatch.setattr("renaiss_bot.handlers.flex.release_daily_flex_reservation", release)
    update = SimpleNamespace(
        effective_message=SimpleNamespace(reply_text=AsyncMock()),
        effective_user=SimpleNamespace(id=10, full_name="Rookie"),
        effective_chat=SimpleNamespace(id=-1001, type="supergroup"),
    )

    with pytest.raises(asyncio.CancelledError):
        await cmd_flex(update, SimpleNamespace())

    release.assert_awaited_once()


@pytest.mark.parametrize(
    ("reservation", "expected"),
    [
        ({"state": "room_limit", "room_daily_limit": 12}, "full for today"),
        (
            {"state": "room_cooldown", "retry_after_seconds": 121},
            "breathing space",
        ),
    ],
)
async def test_flex_room_noise_gate_stops_before_render(
    monkeypatch,
    reservation,
    expected,
):
    card = {
        "category": "pokemon_tcg",
        "local_card_id": "card-1",
        "card_name": "Charizard ex",
        "grade": "SAR",
        "market_price_usd": 430,
    }
    render = AsyncMock()
    monkeypatch.setattr(
        "renaiss_bot.handlers.flex.get_flex_card",
        AsyncMock(return_value=card),
    )
    monkeypatch.setattr(
        "renaiss_bot.handlers.flex.reserve_daily_flex",
        AsyncMock(return_value=reservation),
    )
    monkeypatch.setattr("renaiss_bot.handlers.flex.render_overlay_card", render)
    monkeypatch.setattr("renaiss_bot.handlers.flex.official_chat_id", lambda: -1001)
    message = SimpleNamespace(reply_text=AsyncMock(), delete=AsyncMock())
    update = SimpleNamespace(
        effective_message=message,
        effective_user=SimpleNamespace(id=10, full_name="Rookie"),
        effective_chat=SimpleNamespace(id=-1001, type="supergroup"),
    )

    private_send = AsyncMock()
    await cmd_flex(
        update,
        SimpleNamespace(bot=SimpleNamespace(send_message=private_send)),
    )

    render.assert_not_awaited()
    message.reply_text.assert_not_awaited()
    message.delete.assert_awaited_once()
    assert expected in private_send.await_args.kwargs["text"]


async def test_flex_is_rejected_in_private_chat_before_database(monkeypatch):
    lookup = AsyncMock()
    monkeypatch.setattr("renaiss_bot.handlers.flex.get_flex_card", lookup)

    message = SimpleNamespace(reply_text=AsyncMock())
    update = SimpleNamespace(
        effective_message=message,
        effective_user=SimpleNamespace(id=10),
        effective_chat=SimpleNamespace(id=10, type="private"),
    )

    await cmd_flex(update, SimpleNamespace())

    lookup.assert_not_awaited()
    assert "group chats" in message.reply_text.await_args.args[0]


async def test_flex_runs_in_any_group_room(monkeypatch):
    """공식방이 아니어도 그룹이면 방별 예산으로 동작한다."""
    lookup = AsyncMock(return_value=None)  # 카드 없음 → 안내 후 종료 (DB 게이트 통과 증명)
    monkeypatch.setattr("renaiss_bot.handlers.flex.get_flex_card", lookup)
    monkeypatch.setattr("renaiss_bot.handlers.flex.official_chat_id", lambda: -1001)

    message = SimpleNamespace(reply_text=AsyncMock())
    update = SimpleNamespace(
        effective_message=message,
        effective_user=SimpleNamespace(id=10),
        effective_chat=SimpleNamespace(id=-2002, type="supergroup"),
    )

    await cmd_flex(update, SimpleNamespace())

    lookup.assert_awaited_once()
    assert "no cards yet" in message.reply_text.await_args.args[0]


async def test_props_database_failure_is_not_reported_as_duplicate(monkeypatch):
    monkeypatch.setattr("renaiss_bot.handlers.flex.official_chat_id", lambda: -1001)
    monkeypatch.setattr(
        "renaiss_bot.handlers.flex.add_flex_prop",
        AsyncMock(side_effect=RuntimeError("db down")),
    )
    query = SimpleNamespace(
        data="renaiss:props:10",
        message=SimpleNamespace(chat_id=-1001, message_id=42),
        answer=AsyncMock(),
    )
    update = SimpleNamespace(callback_query=query, effective_user=SimpleNamespace(id=20))

    await on_flex_props(update, SimpleNamespace())

    assert "temporarily unavailable" in query.answer.await_args.args[0]


class _Acquire:
    def __init__(self, value):
        self.value = value

    async def __aenter__(self):
        return self.value

    async def __aexit__(self, *args):
        return None


class _Pool:
    def __init__(self, connection):
        self.connection = connection

    def acquire(self):
        return _Acquire(self.connection)


async def test_flex_card_query_excludes_tutorial_collectible(monkeypatch):
    connection = SimpleNamespace(fetchrow=AsyncMock(return_value=None))
    monkeypatch.setattr(
        "renaiss_bot.database.queries.get_db",
        AsyncMock(return_value=_Pool(connection)),
    )

    assert await get_flex_card(10) is None
    assert "is_tutorial IS NOT TRUE" in connection.fetchrow.await_args.args[0]


async def test_flex_room_lock_binds_integer_chat_id_as_bigint(monkeypatch):
    connection = SimpleNamespace(
        execute=AsyncMock(),
        fetchval=AsyncMock(side_effect=[None, None]),
        fetchrow=AsyncMock(
            side_effect=[
                {"total_posts": 0, "latest_at": None},
                {
                    "user_id": 10,
                    "flex_date": "2026-07-11",
                    "reservation_token": "flex-token",
                    "state": "reserved",
                },
            ]
        ),
    )
    connection.transaction = lambda: _Acquire(None)
    monkeypatch.setattr(
        "renaiss_bot.database.queries.get_db",
        AsyncMock(return_value=_Pool(connection)),
    )

    result = await reserve_daily_flex(
        user_id=10,
        chat_id=-1008201,
        reservation_token="flex-token",
        card={"local_card_id": "card-1", "card_name": "Card 1"},
    )

    lock_call = connection.fetchval.await_args_list[0]
    assert "($1::bigint)::text" in lock_call.args[0]
    assert lock_call.args[1] == -1008201
    assert result["state"] == "reserved"
