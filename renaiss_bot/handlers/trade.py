"""Trading Lv2: sell cards to the NPC market for virtual $ (keep vs sell)."""

from __future__ import annotations

import logging
from html import escape

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from renaiss_bot.database.queries import (
    SELL_RATE,
    get_cash,
    get_sellable_cards,
    sell_card,
)

logger = logging.getLogger(__name__)

SELL_PREFIX = "renaiss:sell:"


def _sell_keyboard(cards: list[dict]) -> InlineKeyboardMarkup:
    rows = []
    for c in cards:
        market = float(c.get("market_price_usd") or 0)
        proceeds = round(market * SELL_RATE)
        label = f"{c['card_name'][:18]} · sell ${proceeds:,}"
        rows.append([InlineKeyboardButton(label, callback_data=f"{SELL_PREFIX}{c['local_card_id']}")])
    return InlineKeyboardMarkup(rows)


async def cmd_sell(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_message or not update.effective_user:
        return
    user_id = update.effective_user.id
    cash = await get_cash(user_id)
    cards = await get_sellable_cards(user_id)
    if not cards:
        await update.effective_message.reply_text(
            f"💵 Cash: <b>${cash:,.0f}</b>\nNo priced cards to sell yet. Catch some with <code>c</code>.",
            parse_mode="HTML",
        )
        return
    text = (
        f"💵 Cash: <b>${cash:,.0f}</b>\n"
        f"────────────\n"
        f"Sell a card to the market (you get {int(SELL_RATE * 100)}% of market value).\n"
        f"<i>Keep it for your net worth, or sell for trading capital?</i>"
    )
    await update.effective_message.reply_text(text, parse_mode="HTML", reply_markup=_sell_keyboard(cards))


async def on_sell(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query or not query.data or not update.effective_user:
        return
    local_card_id = query.data.removeprefix(SELL_PREFIX)
    result = await sell_card(update.effective_user.id, local_card_id)
    if result is None:
        await query.answer("You no longer hold that card.", show_alert=False)
        return

    cash = await get_cash(update.effective_user.id)
    await query.answer(f"Sold for ${result['proceeds']:,.0f}!", show_alert=False)
    # 남은 카드로 목록 갱신
    cards = await get_sellable_cards(update.effective_user.id)
    text = (
        f"✅ Sold <b>{escape(result['card_name'])}</b> for <b>${result['proceeds']:,.0f}</b> "
        f"(market ${result['market_usd']:,.0f})\n"
        f"💵 Cash: <b>${cash:,.0f}</b>"
    )
    try:
        if cards:
            text += "\n────────────\nSell more, or keep the rest for your net worth."
            await query.edit_message_text(text, parse_mode="HTML", reply_markup=_sell_keyboard(cards))
        else:
            await query.edit_message_text(text, parse_mode="HTML")
    except Exception as exc:
        logger.debug("Sell edit skipped: %s", exc)
