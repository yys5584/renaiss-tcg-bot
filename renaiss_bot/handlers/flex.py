"""Flex card handlers: /flex to brag a best pull, Props button to cheer."""

from __future__ import annotations

import asyncio
import logging
import secrets
from html import escape
from io import BytesIO

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.error import BadRequest, Forbidden, NetworkError, TimedOut
from telegram.ext import ContextTypes

from renaiss_bot.database.queries import (
    add_drop_points,
    add_flex_prop,
    complete_daily_flex,
    get_flex_card,
    mark_daily_flex_failed,
    release_daily_flex_reservation,
    reserve_daily_flex,
)
from renaiss_bot.renderers.overlay import overlay_cache_key, render_overlay_card
from renaiss_bot.handlers.spawn import official_chat_id
from renaiss_bot.services.flex import (
    PROP_REWARD_CAP,
    PROP_REWARD_FLEXER,
    PROP_REWARD_TAPPER,
    build_flex_caption,
)
from renaiss_bot.services.features import pack_economy_enabled, private_free_packs_enabled
from renaiss_bot.services.media_cache import get_telegram_file_id, remember_telegram_photo
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


async def _record_flex_failure(token: str, *, state: str, error: Exception | str) -> None:
    try:
        await mark_daily_flex_failed(
            reservation_token=token,
            state=state,
            error=str(error),
        )
    except Exception:
        logger.exception("Flex failure state could not be persisted token=%s", token)


async def _private_room_gate_notice(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    text: str,
) -> None:
    """Keep repeated room-limit feedback out of the public conversation."""
    user = update.effective_user
    bot = getattr(context, "bot", None)
    delivered_privately = False
    if user is not None and bot is not None:
        try:
            await bot.send_message(chat_id=user.id, text=text)
            delivered_privately = True
        except Exception as exc:
            logger.info("Private Flex gate notice skipped user=%s: %s", user.id, exc)
    message = update.effective_message
    if message is not None:
        if delivered_privately:
            try:
                await message.delete()
            except Exception:
                pass
        else:
            await message.reply_text(text, disable_notification=True)


async def cmd_flex(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_message or not update.effective_user:
        return
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id if update.effective_chat else user_id

    if official_chat_id() is None or chat_id != official_chat_id():
        await update.effective_message.reply_text(
            "Flex is available only in the community collaboration game room."
        )
        return

    try:
        card = await get_flex_card(user_id)
    except Exception as exc:
        logger.warning("Flex collection lookup failed user=%s: %s", user_id, exc)
        await update.effective_message.reply_text(
            "Flex is temporarily unavailable because your collection could not be verified."
        )
        return
    if not card:
        next_step = "Join a blind group spawn with <code>c</code>."
        if private_free_packs_enabled():
            next_step = (
                "Join a blind group spawn with <code>c</code>, or use the optional "
                "private <code>/open</code> experiment."
            )
        await update.effective_message.reply_text(
            f"You have no cards yet. {next_step}",
            parse_mode="HTML",
        )
        return

    reservation_token = secrets.token_hex(16)
    try:
        reservation = await reserve_daily_flex(
            user_id=user_id,
            chat_id=chat_id,
            reservation_token=reservation_token,
            card=card,
        )
    except asyncio.CancelledError:
        try:
            await asyncio.shield(
                release_daily_flex_reservation(reservation_token=reservation_token)
            )
        except Exception:
            logger.exception(
                "Cancelled Flex reservation could not be released token=%s",
                reservation_token,
            )
        raise
    except Exception as exc:
        logger.warning("Flex reservation failed user=%s: %s", user_id, exc)
        await update.effective_message.reply_text(
            "Flex is temporarily unavailable because today's limit could not be verified. "
            "No flex was posted."
        )
        return
    reservation_state = str(reservation.get("state") or "")
    if reservation_state == "user_already":
        await _private_room_gate_notice(
            update,
            context,
            "You already flexed today — come back tomorrow!",
        )
        return

    if reservation_state == "room_limit":
        await _private_room_gate_notice(
            update,
            context,
            "The community room's Flex board is full for today. Try again tomorrow.",
        )
        return
    if reservation_state == "room_cooldown":
        retry_after = max(1, int(reservation.get("retry_after_seconds") or 1))
        minutes = max(1, (retry_after + 59) // 60)
        await _private_room_gate_notice(
            update,
            context,
            f"Give the room a little breathing space. Try /flex again in about {minutes} min.",
        )
        return
    if reservation_state != "reserved":
        logger.error(
            "Unexpected Flex reservation state user=%s state=%s",
            user_id,
            reservation_state,
        )
        await update.effective_message.reply_text(
            "Flex is temporarily unavailable because today's room limit could not be verified."
        )
        return

    caption = build_flex_caption(
        display_name=_display_name(update),
        card_name=str(card.get("card_name") or "-"),
        grade=str(card.get("grade") or "-"),
        market_usd=card.get("market_price_usd"),
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
        source="collection",
        image_url=card.get("image_url"),
    )

    render_key = overlay_cache_key(card_identity, price)
    try:
        image_payload = await get_telegram_file_id(render_key)
        if not image_payload:
            image_payload = await render_overlay_card(card_identity, price)
    except asyncio.CancelledError:
        try:
            await asyncio.shield(
                release_daily_flex_reservation(reservation_token=reservation_token)
            )
        except Exception:
            logger.exception("Cancelled Flex could not release token=%s", reservation_token)
        raise
    except Exception as exc:
        logger.debug("Flex render skipped: %s", exc)
        image_payload = None

    keyboard = _props_keyboard(user_id)
    try:
        if image_payload:
            if isinstance(image_payload, bytes):
                photo = BytesIO(image_payload)
                photo.name = "renaiss_flex.png"
            else:
                photo = image_payload
            message = await update.effective_message.reply_photo(
                photo=photo, caption=caption, parse_mode="HTML", reply_markup=keyboard
            )
        else:
            message = await update.effective_message.reply_text(
                caption, parse_mode="HTML", reply_markup=keyboard
            )
    except asyncio.CancelledError:
        await asyncio.shield(
            _record_flex_failure(
                reservation_token,
                state="delivery_unknown",
                error="handler cancelled during Telegram delivery",
            )
        )
        raise
    except (BadRequest, Forbidden) as exc:
        try:
            await release_daily_flex_reservation(reservation_token=reservation_token)
        except Exception:
            logger.exception("Definite flex failure could not release token=%s", reservation_token)
        logger.warning("Flex delivery rejected user=%s: %s", user_id, exc)
        return
    except (TimedOut, NetworkError) as exc:
        await _record_flex_failure(
            reservation_token,
            state="delivery_unknown",
            error=exc,
        )
        logger.warning("Flex delivery is ambiguous user=%s: %s", user_id, exc)
        return
    except Exception as exc:
        await _record_flex_failure(
            reservation_token,
            state="delivery_unknown",
            error=exc,
        )
        logger.exception("Flex delivery failed ambiguously user=%s", user_id)
        return

    if message is None or not getattr(message, "message_id", None):
        await _record_flex_failure(
            reservation_token,
            state="delivery_unknown",
            error="Telegram returned no message id",
        )
        return
    try:
        completed = await complete_daily_flex(
            reservation_token=reservation_token,
            message_id=int(message.message_id),
        )
    except asyncio.CancelledError:
        await asyncio.shield(
            _record_flex_failure(
                reservation_token,
                state="delivery_unknown",
                error="handler cancelled while acknowledging delivery",
            )
        )
        raise
    except Exception as exc:
        logger.exception("Flex delivered but completion failed token=%s: %s", reservation_token, exc)
        return
    if not completed:
        logger.error("Flex delivered but completion was fenced token=%s", reservation_token)
    elif isinstance(image_payload, bytes):
        await remember_telegram_photo(render_key, message)


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

    if official_chat_id() is None or chat_id != official_chat_id():
        await query.answer("Props are available only in the community collaboration room.")
        return

    try:
        total_props = await add_flex_prop(
            chat_id=chat_id,
            message_id=message_id,
            tapper_user_id=tapper.id,
        )
    except Exception as exc:
        logger.warning("Flex props could not be verified chat=%s message=%s: %s", chat_id, message_id, exc)
        await query.answer("Props are temporarily unavailable. Try again.")
        return
    if total_props is None:
        await query.answer("You already gave props 👏", show_alert=False)
        return

    if pack_economy_enabled():
        # 레거시 경제가 명시적으로 켜진 경우에만 RP를 지급한다.
        if total_props <= PROP_REWARD_CAP:
            await add_drop_points(flexer_id, PROP_REWARD_FLEXER, source="flex_props_received")
        await add_drop_points(tapper.id, PROP_REWARD_TAPPER, source="flex_props_given")
        feedback = f"👏 Props sent! +{PROP_REWARD_TAPPER} RP"
    else:
        feedback = "👏 Props sent!"
    await query.answer(feedback, show_alert=False)
