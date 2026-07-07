"""TGPOKE-style d/f collection drop flow for the Renaiss bot."""

from __future__ import annotations

import asyncio
import random
from dataclasses import dataclass, field
from html import escape
from io import BytesIO
from time import monotonic

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Message, Update
from telegram.ext import ContextTypes

from renaiss_bot.renderers.overlay import render_overlay_card
from renaiss_bot.services.card_pool import best_card, build_pack, load_card_pool
from renaiss_bot.services.pricing import fetch_price

DROP_SECONDS = 10
NON_WINNER_POINTS = 50


@dataclass
class DropParticipant:
    user_id: int
    display_name: str


@dataclass
class ActiveDrop:
    chat_id: int
    caller_id: int
    card_name: str
    started_at: float
    message_id: int | None = None
    participants: dict[int, DropParticipant] = field(default_factory=dict)
    closing: bool = False


_active_drops: dict[int, ActiveDrop] = {}
_drop_locks: dict[int, asyncio.Lock] = {}


def _lock(chat_id: int) -> asyncio.Lock:
    lock = _drop_locks.get(chat_id)
    if lock is None:
        lock = asyncio.Lock()
        _drop_locks[chat_id] = lock
    return lock


def _display_name(update: Update) -> str:
    user = update.effective_user
    if not user:
        return "Trainer"
    return user.full_name or user.username or user.first_name or "Trainer"


def _status_text(drop: ActiveDrop) -> str:
    remaining = max(0, DROP_SECONDS - int(monotonic() - drop.started_at))
    return (
        f"✨ <b>{escape(drop.card_name)}</b> appeared!\n\n"
        "🍖 Type <code>f</code> to feed and join.\n"
        "👣 Type <code>d</code> to call the next drop.\n"
        f"⏳ Time left: <b>{remaining}s</b>\n"
        f"👥 Joined: <b>{len(drop.participants)}</b>\n"
        "📦 A card pack winner will be announced when the drop ends.\n"
        f"💰 Non-winners get RP +{NON_WINNER_POINTS}"
    )


def _price_keyboard(url: str | None) -> InlineKeyboardMarkup:
    rows = []
    if url:
        rows.append([InlineKeyboardButton("View on Renaiss", url=url)])
    rows.append([InlineKeyboardButton("My Collection", callback_data="renaiss:mycards")])
    return InlineKeyboardMarkup(rows)


async def _pick_featured_card(user_id: int | None):
    pool, _source = await load_card_pool(user_id, "pokemon_tcg")
    cards, _lucky_grade = build_pack(pool, "pokemon_tcg", "free")
    return best_card(cards)


async def call_drop_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_chat or not update.effective_message or not update.effective_user:
        return
    if update.effective_chat.type == "private":
        return

    chat_id = update.effective_chat.id
    user_id = update.effective_user.id

    async with _lock(chat_id):
        existing = _active_drops.get(chat_id)
        if existing and not existing.closing:
            return

        featured = await _pick_featured_card(user_id)
        drop = ActiveDrop(
            chat_id=chat_id,
            caller_id=user_id,
            card_name=featured.card_name,
            started_at=monotonic(),
        )
        drop.participants[user_id] = DropParticipant(user_id=user_id, display_name=_display_name(update))
        _active_drops[chat_id] = drop

        message = await update.effective_message.reply_text(
            _status_text(drop),
            parse_mode="HTML",
        )
        drop.message_id = message.message_id
        asyncio.create_task(_finish_drop_after_delay(context, chat_id))


async def feed_drop_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_chat or not update.effective_message or not update.effective_user:
        return
    if update.effective_chat.type == "private":
        return

    chat_id = update.effective_chat.id
    user_id = update.effective_user.id

    async with _lock(chat_id):
        drop = _active_drops.get(chat_id)
        if not drop or drop.closing:
            return
        if user_id in drop.participants:
            return

        drop.participants[user_id] = DropParticipant(user_id=user_id, display_name=_display_name(update))
        try:
            await context.bot.edit_message_text(
                chat_id=chat_id,
                message_id=drop.message_id,
                text=_status_text(drop),
                parse_mode="HTML",
            )
        except Exception:
            pass

        try:
            await update.effective_message.reply_text(
                f"🍖 <b>{escape(_display_name(update))}</b> brought out a small Poffin. "
                f"<b>{escape(drop.card_name)}</b> comes closer.",
                parse_mode="HTML",
            )
        except Exception:
            pass


async def _finish_drop_after_delay(context: ContextTypes.DEFAULT_TYPE, chat_id: int) -> None:
    await asyncio.sleep(DROP_SECONDS)
    async with _lock(chat_id):
        drop = _active_drops.get(chat_id)
        if not drop or drop.closing:
            return
        drop.closing = True

    try:
        await _finish_drop(context, drop)
    finally:
        async with _lock(chat_id):
            current = _active_drops.get(chat_id)
            if current is drop:
                _active_drops.pop(chat_id, None)


async def _finish_drop(context: ContextTypes.DEFAULT_TYPE, drop: ActiveDrop) -> Message | None:
    participants = list(drop.participants.values())
    if not participants:
        return await context.bot.send_message(
            chat_id=drop.chat_id,
            text=f"⌛ <b>{escape(drop.card_name)}</b> left. No participants.",
            parse_mode="HTML",
        )

    winner = random.choice(participants)
    # 우승자 팩 개봉(API+DB)과 나머지 포인트 지급(DB)은 독립 → 병렬
    result, _ = await asyncio.gather(
        _open_winner_pack(winner.user_id, drop.chat_id),
        _reward_non_winners(participants, winner.user_id),
    )
    names = ", ".join(escape(p.display_name) for p in participants[:12])
    if len(participants) > 12:
        names += f" +{len(participants) - 12}"

    caption = (
        "📦 <b>Drop result</b>\n"
        "────────────\n"
        f"Winner: <b>{escape(winner.display_name)}</b>\n"
        f"Best pull: <b>{escape(result.best_card.card_name)}</b> {escape(result.best_card.grade)}\n"
        f"Participants: {names}\n"
        f"Non-winners: RP +{NON_WINNER_POINTS}"
    )
    if result.best_price.fmv_usd is not None:
        caption += f"\nMarket Value: <b>${result.best_price.fmv_usd:,.0f}</b>"
        if result.best_price.change_7d_pct is not None:
            caption += f" / 7d {result.best_price.change_7d_pct:+.1f}%"

    try:
        image_bytes = await render_overlay_card(result.best_card, result.best_price)
    except Exception:
        image_bytes = None
    if image_bytes:
        photo = BytesIO(image_bytes)
        photo.name = "renaiss_drop_reward.png"
        return await context.bot.send_photo(
            chat_id=drop.chat_id,
            photo=photo,
            caption=caption,
            parse_mode="HTML",
            reply_markup=_price_keyboard(result.best_price.referral_url),
        )
    return await context.bot.send_message(
        chat_id=drop.chat_id,
        text=caption,
        parse_mode="HTML",
        reply_markup=_price_keyboard(result.best_price.referral_url),
    )


async def _open_winner_pack(user_id: int, chat_id: int | None):
    from renaiss_bot.services.pack import open_pack

    result = await open_pack(user_id=user_id, category_key="pokemon_tcg", pack_type="free", count=1)
    try:
        from renaiss_bot.database.queries import log_pack_event, register_pack_cards

        await register_pack_cards(user_id=user_id, chat_id=chat_id, result=result)
        await log_pack_event(
            user_id=user_id,
            chat_id=chat_id,
            category=result.category,
            best_card=result.best_card,
            price=result.best_price,
            pack_type=result.pack_type,
            pack_count=result.pack_count,
            card_count=len(result.cards),
            pool_source=result.pool_source,
            source="drop_win",
        )
    except Exception:
        pass
    return result


async def _reward_non_winners(participants: list[DropParticipant], winner_id: int) -> None:
    try:
        from renaiss_bot.database.queries import add_drop_points

        await asyncio.gather(
            *(
                add_drop_points(participant.user_id, NON_WINNER_POINTS, source="drop_non_winner")
                for participant in participants
                if participant.user_id != winner_id
            ),
            return_exceptions=True,
        )
    except Exception:
        pass
