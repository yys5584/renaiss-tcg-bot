"""P2P 카드 교환 핸들러 테스트 (DB/telegram 불필요)."""

from __future__ import annotations

from time import monotonic
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from renaiss_bot.database.queries import TradeConflict
from renaiss_bot.handlers.trade import (
    _offers,
    on_trade_callback,
    trade_offer_handler,
)

CARD_A = {
    "local_card_id": "catalog:pokemon_tcg:renaiss-aaaaaaaaaaaaaaaaaaaaaaaa",
    "category": "pokemon_tcg",
    "card_name": "Charizard",
    "grade": "UR",
    "market_price_usd": 900.0,
    "quantity": 1,
}
CARD_B = {
    "local_card_id": "catalog:one_piece_tcg:renaiss-bbbbbbbbbbbbbbbbbbbbbbbb",
    "category": "one_piece_tcg",
    "card_name": "Shanks",
    "grade": "SR",
    "market_price_usd": 250.0,
    "quantity": 2,
}


@pytest.fixture(autouse=True)
def _clean_offers():
    _offers.clear()
    yield
    _offers.clear()


@pytest.fixture(autouse=True)
def _quiet_side_effects(monkeypatch):
    monkeypatch.setattr(
        "renaiss_bot.handlers.trade.log_event", AsyncMock(return_value=True)
    )
    monkeypatch.setattr(
        "renaiss_bot.handlers.trade.get_portfolio_values",
        AsyncMock(return_value={1: 1200.0, 2: 800.0}),
    )


def _offer_update(*, text="trade Charizard", with_reply=True, same_user=False, bot_target=False):
    initiator = SimpleNamespace(id=1, full_name="Alice", is_bot=False)
    target = SimpleNamespace(
        id=1 if same_user else 2, full_name="Bob", is_bot=bot_target
    )
    reply = SimpleNamespace(from_user=target) if with_reply else None
    message = SimpleNamespace(
        text=text,
        reply_to_message=reply,
        reply_text=AsyncMock(return_value=SimpleNamespace(message_id=77)),
    )
    return SimpleNamespace(
        effective_message=message,
        effective_chat=SimpleNamespace(id=-500, type="supergroup"),
        effective_user=initiator,
    )


def _context():
    queue = SimpleNamespace(run_once=lambda *a, **k: None)
    return SimpleNamespace(bot=SimpleNamespace(), job_queue=queue)


async def test_offer_requires_reply(monkeypatch):
    find = AsyncMock()
    monkeypatch.setattr("renaiss_bot.handlers.trade.find_owned_card", find)
    update = _offer_update(with_reply=False)
    await trade_offer_handler(update, _context())
    find.assert_not_awaited()
    assert "Reply to the collector" in update.effective_message.reply_text.await_args.args[0]
    assert not _offers


async def test_offer_blocks_self_trade(monkeypatch):
    find = AsyncMock()
    monkeypatch.setattr("renaiss_bot.handlers.trade.find_owned_card", find)
    update = _offer_update(same_user=True)
    await trade_offer_handler(update, _context())
    find.assert_not_awaited()
    assert "yourself" in update.effective_message.reply_text.await_args.args[0]


async def test_offer_posts_partner_picker(monkeypatch):
    monkeypatch.setattr(
        "renaiss_bot.handlers.trade.find_owned_card", AsyncMock(return_value=CARD_A)
    )
    monkeypatch.setattr(
        "renaiss_bot.handlers.trade.list_tradeable_cards",
        AsyncMock(return_value=[CARD_B]),
    )
    update = _offer_update()
    await trade_offer_handler(update, _context())
    assert len(_offers) == 1
    offer = next(iter(_offers.values()))
    assert offer["from_id"] == 1 and offer["to_id"] == 2
    assert offer["card"] == CARD_A
    text = update.effective_message.reply_text.await_args.args[0]
    assert "TRADE OFFER" in text and "Charizard" in text


def _callback_update(token: str, action: str, *, user_id: int):
    query = SimpleNamespace(
        data=f"renaiss:trade:{token}:{action}",
        answer=AsyncMock(),
        edit_message_text=AsyncMock(),
    )
    return SimpleNamespace(
        callback_query=query,
        effective_user=SimpleNamespace(id=user_id),
    ), query


def _seed_offer(token="tok123"):
    _offers[token] = {
        "chat_id": -500,
        "message_id": 77,
        "from_id": 1,
        "from_name": "Alice",
        "to_id": 2,
        "to_name": "Bob",
        "card": CARD_A,
        "partner_cards": {CARD_B["local_card_id"][-24:]: CARD_B},
        "expires_at": monotonic() + 60,
    }
    return token


async def test_only_partner_can_pick(monkeypatch):
    execute = AsyncMock()
    monkeypatch.setattr("renaiss_bot.handlers.trade.execute_card_trade", execute)
    token = _seed_offer()
    suffix = CARD_B["local_card_id"][-24:]
    update, query = _callback_update(token, f"pick:{suffix}", user_id=1)  # 발신자가 누름
    await on_trade_callback(update, _context())
    execute.assert_not_awaited()
    assert "Only the offered collector" in query.answer.await_args.args[0]
    assert token in _offers  # 제안은 유지


async def test_pick_executes_atomic_swap_once(monkeypatch):
    execute = AsyncMock(
        return_value={"initiator_gave": CARD_A, "partner_gave": CARD_B}
    )
    monkeypatch.setattr("renaiss_bot.handlers.trade.execute_card_trade", execute)
    token = _seed_offer()
    suffix = CARD_B["local_card_id"][-24:]
    update, query = _callback_update(token, f"pick:{suffix}", user_id=2)
    await on_trade_callback(update, _context())
    execute.assert_awaited_once_with(
        initiator_id=1,
        partner_id=2,
        initiator_card_id=CARD_A["local_card_id"],
        partner_card_id=CARD_B["local_card_id"],
    )
    assert token not in _offers  # 단일 사용
    result_text = query.edit_message_text.await_args.args[0]
    assert "TRADE COMPLETE" in result_text
    assert "Charizard" in result_text and "Shanks" in result_text

    # 같은 버튼을 또 눌러도(더블클릭) 재실행되지 않는다
    update2, query2 = _callback_update(token, f"pick:{suffix}", user_id=2)
    await on_trade_callback(update2, _context())
    execute.assert_awaited_once()
    assert "expired" in query2.answer.await_args.args[0]


async def test_conflict_cancels_without_partial_swap(monkeypatch):
    monkeypatch.setattr(
        "renaiss_bot.handlers.trade.execute_card_trade",
        AsyncMock(side_effect=TradeConflict("gone")),
    )
    token = _seed_offer()
    suffix = CARD_B["local_card_id"][-24:]
    update, query = _callback_update(token, f"pick:{suffix}", user_id=2)
    await on_trade_callback(update, _context())
    assert "already used" in query.answer.await_args.args[0]
    assert token not in _offers


async def test_either_party_can_decline():
    token = _seed_offer()
    update, query = _callback_update(token, "cancel", user_id=1)
    await on_trade_callback(update, _context())
    assert token not in _offers
    assert "declined" in query.answer.await_args.args[0]
