"""P2P card barter, ported from TGPoke's reply-based group trade.

Reply to someone with ``trade <card name>`` to offer one of your cards; the
partner picks one of theirs from buttons and the swap runs atomically. No
money, balances, or fees are involved — cards move one-for-one.
"""

from __future__ import annotations

import logging
import secrets
from html import escape
from time import monotonic

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from renaiss_bot.database.event_queries import log_event
from renaiss_bot.database.queries import (
    TradeConflict,
    execute_card_trade,
    find_owned_card,
    get_portfolio_values,
    list_tradeable_cards,
)
from renaiss_bot.services.emoji import grade_bar, icon
from renaiss_bot.services.spawn import tier_display

logger = logging.getLogger(__name__)

TRADE_OFFER_TTL_SECONDS = 120
_offers: dict[str, dict] = {}


def _card_label(card: dict) -> str:
    price = float(card.get("market_price_usd") or 0)
    price_text = f" · ${price:,.0f}" if price >= 1 else ""
    return f"{card['card_name']} · {tier_display(card.get('grade'))}{price_text}"


def _card_line(card: dict) -> str:
    bar = grade_bar(card.get("grade"))
    tier = escape(tier_display(card.get("grade")))
    price = float(card.get("market_price_usd") or 0)
    price_text = f" · <b>${price:,.0f}</b>" if price >= 1 else ""
    prefix = f"{bar} " if bar else ""
    return f"{prefix}<b>{escape(str(card['card_name']))}</b> · {tier}{price_text}"


def _display_name(user) -> str:
    name = (getattr(user, "full_name", None) or getattr(user, "first_name", None) or "Collector").strip()
    return name[:32] or "Collector"


def _pick_keyboard(token: str, cards: list[dict]) -> InlineKeyboardMarkup:
    rows = []
    pair = []
    for card in cards:
        pair.append(
            InlineKeyboardButton(
                _card_label(card)[:56],
                callback_data=f"renaiss:trade:{token}:pick:{card['local_card_id'][-24:]}",
            )
        )
        if len(pair) == 2:
            rows.append(pair)
            pair = []
    if pair:
        rows.append(pair)
    rows.append(
        [InlineKeyboardButton("✖ Decline", callback_data=f"renaiss:trade:{token}:cancel")]
    )
    return InlineKeyboardMarkup(rows)


def _expire_offer(token: str) -> dict | None:
    offer = _offers.pop(token, None)
    return offer


async def trade_offer_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """``trade <card>`` — 답장 대상에게 1:1 카드 교환을 제안한다."""
    message = update.effective_message
    chat = update.effective_chat
    user = update.effective_user
    if not message or not chat or not user:
        return
    if getattr(chat, "type", "") not in {"group", "supergroup"}:
        return
    reply = getattr(message, "reply_to_message", None)
    target = getattr(reply, "from_user", None) if reply else None
    if target is None:
        await message.reply_text(
            "Reply to the collector you want to trade with: "
            "<code>trade Charizard</code>",
            parse_mode="HTML",
            disable_notification=True,
        )
        return
    if target.id == user.id:
        await message.reply_text("You cannot trade with yourself.", disable_notification=True)
        return
    if getattr(target, "is_bot", False):
        await message.reply_text("Bots do not hold collections.", disable_notification=True)
        return

    query = (message.text or "")
    query = query.split(maxsplit=1)[1] if len(query.split(maxsplit=1)) > 1 else ""
    offered = await find_owned_card(user.id, query)
    if offered is None:
        await message.reply_text(
            f"No card in your collection matches <code>{escape(query or '?')}</code>. "
            "Check /mycards.",
            parse_mode="HTML",
            disable_notification=True,
        )
        return
    partner_cards = await list_tradeable_cards(target.id)
    if not partner_cards:
        await message.reply_text(
            f"{escape(_display_name(target))} has no tradeable cards yet.",
            parse_mode="HTML",
            disable_notification=True,
        )
        return

    # 발신자당 활성 제안 1개: 이전 제안은 대체한다.
    for token, offer in list(_offers.items()):
        if offer["from_id"] == user.id and offer["chat_id"] == chat.id:
            _offers.pop(token, None)

    token = secrets.token_hex(8)
    text = (
        f"{icon('exchange')} <b>TRADE OFFER</b>\n"
        f"<b>{escape(_display_name(user))}</b> offers:\n"
        f"{_card_line(offered)}\n\n"
        f"<b>{escape(_display_name(target))}</b> — pick a card to trade back, "
        f"or decline. ({TRADE_OFFER_TTL_SECONDS}s)"
    )
    sent = await message.reply_text(
        text,
        parse_mode="HTML",
        reply_markup=_pick_keyboard(token, partner_cards),
        disable_notification=True,
    )
    _offers[token] = {
        "chat_id": chat.id,
        "message_id": sent.message_id,
        "from_id": user.id,
        "from_name": _display_name(user),
        "to_id": target.id,
        "to_name": _display_name(target),
        "card": offered,
        "partner_cards": {card["local_card_id"][-24:]: card for card in partner_cards},
        "expires_at": monotonic() + TRADE_OFFER_TTL_SECONDS,
    }
    job_queue = getattr(context, "job_queue", None)
    if job_queue is not None:
        job_queue.run_once(
            _expire_offer_job,
            when=TRADE_OFFER_TTL_SECONDS,
            data=token,
            name=f"renaiss_trade_expire_{token}",
            job_kwargs={"misfire_grace_time": 60},
        )


async def _expire_offer_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    token = getattr(getattr(context, "job", None), "data", None)
    if not isinstance(token, str):
        return
    offer = _expire_offer(token)
    if offer is None:
        return
    try:
        await context.bot.edit_message_text(
            chat_id=offer["chat_id"],
            message_id=offer["message_id"],
            text=f"{icon('exchange')} Trade offer from "
            f"<b>{escape(offer['from_name'])}</b> expired.",
            parse_mode="HTML",
            reply_markup=None,
        )
    except Exception:
        pass


async def on_trade_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    user = update.effective_user
    if not query or not user:
        return
    parts = (query.data or "").split(":")
    # renaiss:trade:<token>:pick:<card_suffix> | renaiss:trade:<token>:cancel
    if len(parts) < 4:
        await query.answer("This trade is no longer available.")
        return
    token, action = parts[2], parts[3]
    offer = _offers.get(token)
    if offer is None or monotonic() > offer["expires_at"]:
        _offers.pop(token, None)
        await query.answer("This trade offer has expired.")
        return

    if action == "cancel":
        if user.id not in (offer["from_id"], offer["to_id"]):
            await query.answer("Only the traders can decline this offer.")
            return
        _offers.pop(token, None)
        await query.answer("Trade declined.")
        try:
            await query.edit_message_text(
                f"{icon('exchange')} Trade between <b>{escape(offer['from_name'])}</b> "
                f"and <b>{escape(offer['to_name'])}</b> was declined.",
                parse_mode="HTML",
                reply_markup=None,
            )
        except Exception:
            pass
        return

    if action != "pick" or len(parts) != 5:
        await query.answer("This trade is no longer available.")
        return
    if user.id != offer["to_id"]:
        await query.answer("Only the offered collector can pick a card.")
        return
    picked = offer["partner_cards"].get(parts[4])
    if picked is None:
        await query.answer("That card is no longer available.")
        return

    # 단일 사용: 실행 전에 제안을 회수해 더블클릭 경쟁을 차단한다.
    if _offers.pop(token, None) is None:
        await query.answer("This trade offer has expired.")
        return
    try:
        result = await execute_card_trade(
            initiator_id=offer["from_id"],
            partner_id=offer["to_id"],
            initiator_card_id=offer["card"]["local_card_id"],
            partner_card_id=picked["local_card_id"],
        )
    except TradeConflict:
        await query.answer("A card in this trade was already used.")
        try:
            await query.edit_message_text(
                f"{icon('exchange')} Trade cancelled — a card was no longer available.",
                parse_mode="HTML",
                reply_markup=None,
            )
        except Exception:
            pass
        return
    except Exception as exc:
        logger.error("Trade execution failed token=%s: %s", token, exc)
        await query.answer("Trade failed safely; nothing was exchanged.")
        return

    await query.answer("Trade complete!")
    totals = {}
    try:
        totals = await get_portfolio_values([offer["from_id"], offer["to_id"]])
    except Exception:
        totals = {}

    def _total_line(name: str, user_id: int) -> str:
        total = totals.get(user_id)
        if total and total >= 1:
            return f"\n{icon('container')} {escape(name)}: <b>${total:,.0f}</b>"
        return ""

    text = (
        f"{icon('exchange')} <b>TRADE COMPLETE</b>\n"
        f"<b>{escape(offer['from_name'])}</b> ⇄ <b>{escape(offer['to_name'])}</b>\n"
        f"{_card_line(result['initiator_gave'])}\n"
        f"{_card_line(result['partner_gave'])}"
        f"{_total_line(offer['from_name'], offer['from_id'])}"
        f"{_total_line(offer['to_name'], offer['to_id'])}"
    )
    try:
        await query.edit_message_text(text, parse_mode="HTML", reply_markup=None)
    except Exception:
        pass
    await log_event(
        "trade_completed",
        event_key=f"trade:{token}",
        user_id=offer["from_id"],
        chat_id=offer["chat_id"],
        metadata={
            "partner_id": offer["to_id"],
            "initiator_gave": result["initiator_gave"]["local_card_id"],
            "partner_gave": result["partner_gave"]["local_card_id"],
        },
    )
