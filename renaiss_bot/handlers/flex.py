"""Flex card handlers: /flex to brag a best pull, Props button to cheer."""

from __future__ import annotations

import logging
from html import escape
from io import BytesIO

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from renaiss_bot.database.queries import (
    add_drop_points,
    add_flex_prop,
    flexed_today,
    get_flex_card,
    get_portfolio_value_days_ago,
    record_flex,
)
from renaiss_bot.renderers.overlay import render_overlay_card
from renaiss_bot.services.flex import (
    PROP_REWARD_CAP,
    PROP_REWARD_FLEXER,
    PROP_REWARD_TAPPER,
    build_flex_caption,
)
from renaiss_bot.services.models import CardIdentity, RenaissPrice

logger = logging.getLogger(__name__)

FLEX_PROPS_PREFIX = "renaiss:props:"


def _props_keyboard(flexer_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("👏 Props", callback_data=f"{FLEX_PROPS_PREFIX}{flexer_id}")]]
    )


def _display_name(update: Update) -> str:
    user = update.effective_user
    if not user:
        return "Trainer"
    return user.full_name or user.username or user.first_name or "Trainer"


async def cmd_flex(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_message or not update.effective_user:
        return
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id if update.effective_chat else user_id

    if await flexed_today(user_id):
        await update.effective_message.reply_text(
            "🎉 You already flexed today — come back tomorrow!",
            parse_mode="HTML",
        )
        return

    card = await get_flex_card(user_id)
    if not card:
        await update.effective_message.reply_text(
            "You have no cards yet. Open a pack with <code>/open</code> first.",
            parse_mode="HTML",
        )
        return

    from renaiss_bot.services.portfolio import get_portfolio_stats

    stats = await get_portfolio_stats(user_id)
    portfolio_usd = stats.total_value_usd if stats else 0.0
    change_7d = stats.change_7d_pct if stats else None
    if change_7d is None:
        # get_portfolio_stats already snapshots; fetch 7d-ago as a fallback signal
        prior = await get_portfolio_value_days_ago(user_id, days=7)
        if prior and prior > 0:
            change_7d = (portfolio_usd - prior) / prior * 100

    caption = build_flex_caption(
        display_name=_display_name(update),
        card_name=str(card.get("card_name") or "-"),
        grade=str(card.get("grade") or "-"),
        market_usd=card.get("market_price_usd"),
        portfolio_usd=portfolio_usd,
        change_7d_pct=change_7d,
    )

    card_identity = CardIdentity(
        category=str(card.get("category") or "pokemon_tcg"),
        card_name=str(card.get("card_name") or "-"),
        grade=str(card.get("grade") or "RAW"),
        set_code=str(card.get("set_code") or ""),
        collector_number=str(card.get("collector_number") or ""),
        image_url=card.get("image_url"),
        market_price_usd=float(card.get("market_price_usd") or 0) or None,
    )
    price = RenaissPrice(
        status="candidate",
        source="portfolio",
        fmv_usd=float(card.get("market_price_usd") or 0) or None,
        image_url=card.get("image_url"),
    )

    try:
        image_bytes = await render_overlay_card(card_identity, price)
    except Exception as exc:
        logger.debug("Flex render skipped: %s", exc)
        image_bytes = None

    keyboard = _props_keyboard(user_id)
    if image_bytes:
        photo = BytesIO(image_bytes)
        photo.name = "renaiss_flex.png"
        message = await update.effective_message.reply_photo(
            photo=photo, caption=caption, parse_mode="HTML", reply_markup=keyboard
        )
    else:
        message = await update.effective_message.reply_text(
            caption, parse_mode="HTML", reply_markup=keyboard
        )

    await record_flex(
        user_id=user_id,
        chat_id=chat_id,
        message_id=message.message_id if message else None,
        card=card,
    )


async def on_flex_props(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query or not query.data or not update.effective_user:
        return

    try:
        flexer_id = int(query.data.removeprefix(FLEX_PROPS_PREFIX))
    except ValueError:
        await query.answer()
        return

    tapper = update.effective_user
    if tapper.id == flexer_id:
        await query.answer("You can't cheer your own flex 😄", show_alert=False)
        return

    message = query.message
    if not message:
        await query.answer()
        return
    chat_id = message.chat_id
    message_id = message.message_id

    total_props = await add_flex_prop(chat_id=chat_id, message_id=message_id, tapper_user_id=tapper.id)
    if total_props is None:
        await query.answer("You already gave props 👏", show_alert=False)
        return

    # 자랑한 사람 보상은 상한까지만, 누른 사람은 1회
    if total_props <= PROP_REWARD_CAP:
        await add_drop_points(flexer_id, PROP_REWARD_FLEXER, source="flex_props_received")
    await add_drop_points(tapper.id, PROP_REWARD_TAPPER, source="flex_props_given")
    await query.answer(f"👏 Props sent! +{PROP_REWARD_TAPPER} RP", show_alert=False)
