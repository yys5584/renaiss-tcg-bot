"""Product-boundary tests for private price checks and public Pick cohorts."""

from __future__ import annotations

from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock

from renaiss_bot.handlers.cardpack import cmd_mycards
from renaiss_bot.handlers.market import on_market
from renaiss_bot.handlers.price import cmd_price
from renaiss_bot.handlers.start import cmd_start
from renaiss_bot.services.models import RenaissPrice


async def test_telegram_collection_db_error_is_not_reported_as_empty(monkeypatch):
    monkeypatch.setattr(
        "renaiss_bot.handlers.cardpack.get_portfolio_stats",
        AsyncMock(side_effect=RuntimeError("database unavailable")),
    )
    message = SimpleNamespace(reply_text=AsyncMock())
    update = SimpleNamespace(
        effective_message=message,
        effective_chat=SimpleNamespace(id=123, type="private"),
        effective_user=SimpleNamespace(id=123),
    )

    await cmd_mycards(update, SimpleNamespace())

    response = message.reply_text.await_args.args[0]
    assert "temporarily unavailable" in response
    assert "No cards" not in response


def _price_update(*, chat_type: str):
    message = SimpleNamespace(
        text="/price Charizard",
        reply_text=AsyncMock(),
    )
    return SimpleNamespace(
        effective_message=message,
        effective_chat=SimpleNamespace(id=-1001 if chat_type != "private" else 123, type=chat_type),
        effective_user=SimpleNamespace(id=123),
    )


async def test_group_price_routes_to_dm_before_any_price_api_call(monkeypatch):
    fetch_price = AsyncMock()
    tracked_url = AsyncMock()
    monkeypatch.setattr("renaiss_bot.handlers.price.fetch_price", fetch_price)
    monkeypatch.setattr("renaiss_bot.handlers.price.build_tracked_url", tracked_url)
    update = _price_update(chat_type="supergroup")
    context = SimpleNamespace(
        args=["Charizard"],
        bot=SimpleNamespace(username="renaiss_test_bot"),
    )

    await cmd_price(update, context)

    fetch_price.assert_not_awaited()
    tracked_url.assert_not_awaited()
    reply = update.effective_message.reply_text.await_args
    assert "private chat" in reply.args[0]
    assert "blind spawn values remain hidden" in reply.args[0]
    button = reply.kwargs["reply_markup"].inline_keyboard[0][0]
    assert button.text == "Check Price in DM"
    assert button.url == "https://t.me/renaiss_test_bot?start=price"


async def test_private_price_keeps_existing_lookup_flow(monkeypatch):
    from renaiss_bot.handlers.price import _price_last_request

    _price_last_request.clear()
    fetch_price = AsyncMock(
        return_value=RenaissPrice(
            status="search_only",
            source="renaiss-search",
            referral_url="https://www.renaiss.xyz/search?q=Charizard",
        )
    )
    tracked_url = AsyncMock(return_value="https://tracker.example/price")
    monkeypatch.setattr("renaiss_bot.handlers.price.fetch_price", fetch_price)
    monkeypatch.setattr("renaiss_bot.handlers.price.build_tracked_url", tracked_url)
    update = _price_update(chat_type="private")
    context = SimpleNamespace(
        args=["Charizard"],
        bot=SimpleNamespace(username="renaiss_test_bot"),
    )

    await cmd_price(update, context)

    fetch_price.assert_awaited_once()
    tracked_url.assert_awaited_once()
    reply = update.effective_message.reply_text.await_args
    assert "Charizard" in reply.args[0]
    assert reply.kwargs["reply_markup"].inline_keyboard[0][0].url == (
        "https://tracker.example/price"
    )
    _price_last_request.clear()


async def test_private_price_is_throttled_per_user_before_more_api_calls(monkeypatch):
    from renaiss_bot.handlers.price import _price_last_request

    _price_last_request.clear()
    fetch_price = AsyncMock(
        return_value=RenaissPrice(status="search_only", source="renaiss-search")
    )
    monkeypatch.setattr("renaiss_bot.handlers.price.fetch_price", fetch_price)
    monkeypatch.setattr(
        "renaiss_bot.handlers.price.build_tracked_url",
        AsyncMock(return_value=None),
    )
    update = _price_update(chat_type="private")
    update.effective_user.id = 124
    context = SimpleNamespace(args=["Charizard"], bot=SimpleNamespace(username="bot"))

    await cmd_price(update, context)
    await cmd_price(update, context)

    fetch_price.assert_awaited_once()
    assert "Please wait" in update.effective_message.reply_text.await_args.args[0]
    _price_last_request.clear()


async def test_price_deep_link_prompts_for_a_private_price_command():
    update = _price_update(chat_type="private")
    update.effective_message.text = "/start price"
    context = SimpleNamespace(
        args=["price"],
        bot=SimpleNamespace(username="renaiss_test_bot"),
    )

    await cmd_start(update, context)

    reply = update.effective_message.reply_text.await_args
    assert "/price Charizard" in reply.args[0]
    assert reply.kwargs["parse_mode"] == "HTML"


async def test_home_copy_is_collaboration_only_and_hides_closed_packs(monkeypatch):
    monkeypatch.delenv("RENAISS_PRIVATE_FREE_PACKS_ENABLED", raising=False)
    update = _price_update(chat_type="private")
    update.effective_message.text = "/start"

    await cmd_start(update, SimpleNamespace(args=[], bot=SimpleNamespace(username="bot")))

    text = update.effective_message.reply_text.await_args.args[0]
    assert "Community collaboration project" in text
    assert "not the official Renaiss website or product" in text
    assert "no physical card or NFT ownership" in text
    assert "/open" not in text


def _market_update():
    query = SimpleNamespace(
        data="renaiss:market:pick:7",
        answer=AsyncMock(),
        edit_message_text=AsyncMock(),
    )
    return SimpleNamespace(
        callback_query=query,
        effective_user=SimpleNamespace(id=123),
        effective_chat=SimpleNamespace(id=123, type="private"),
    )


async def _run_market_pick(monkeypatch, *, member=None, membership_error=None):
    monkeypatch.setattr("renaiss_bot.handlers.market.daily_pick_enabled", lambda: True)
    monkeypatch.setattr("renaiss_bot.handlers.spawn.official_chat_id", lambda: -1001)
    lock = AsyncMock(
        return_value={
            "card_name": "Charizard ex",
            "entry_fmv_usd": 430.0,
            "pick_date": date(2026, 7, 11),
        }
    )
    load_view = AsyncMock(return_value=("market view", None, None))
    monkeypatch.setattr("renaiss_bot.handlers.market.lock_daily_pick", lock)
    monkeypatch.setattr("renaiss_bot.handlers.market._load_view", load_view)
    monkeypatch.setattr("renaiss_bot.handlers.market.log_event", AsyncMock(return_value=True))
    get_chat_member = AsyncMock(
        side_effect=membership_error,
        return_value=member,
    )
    context = SimpleNamespace(bot=SimpleNamespace(get_chat_member=get_chat_member))
    update = _market_update()

    await on_market(update, context)
    return lock, load_view, get_chat_member


async def test_daily_pick_joins_public_cohort_only_for_verified_current_member(monkeypatch):
    lock, load_view, get_chat_member = await _run_market_pick(
        monkeypatch,
        member=SimpleNamespace(status="member"),
    )

    get_chat_member.assert_awaited_once_with(chat_id=-1001, user_id=123)
    assert lock.await_args.kwargs["community_chat_id"] == -1001
    assert "stays private" not in load_view.await_args.kwargs["notice"]


async def test_daily_pick_for_non_member_stays_private_but_is_still_locked(monkeypatch):
    lock, load_view, _ = await _run_market_pick(
        monkeypatch,
        member=SimpleNamespace(status="left"),
    )

    assert lock.await_count == 1
    assert lock.await_args.kwargs["community_chat_id"] is None
    assert "This pick stays private" in load_view.await_args.kwargs["notice"]


async def test_daily_pick_membership_error_does_not_lock_the_irreversible_pick(monkeypatch):
    lock, load_view, _ = await _run_market_pick(
        monkeypatch,
        membership_error=RuntimeError("Telegram unavailable"),
    )

    lock.assert_not_awaited()
    assert "Daily Pick failed" in load_view.await_args.kwargs["notice"]
