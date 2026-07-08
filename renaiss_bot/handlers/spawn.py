"""Season-1 style spawn handlers: official room auto-spawns, catch with `c`.

A card appears in the official room every minute. Everyone who types `c` during
the window catches a copy. Rare/grail spawns (high market value) get a loud
announcement — the crowd rushes.
"""

from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass, field
from html import escape
from io import BytesIO
from time import monotonic

from telegram import Update
from telegram.ext import ContextTypes

from renaiss_bot.database.queries import get_portfolio_values, register_single_card
from renaiss_bot.renderers.overlay import render_overlay_card
from renaiss_bot.services.models import RenaissPrice
from renaiss_bot.services.spawn import Spawn, roll_spawn

logger = logging.getLogger(__name__)

CATCH_WINDOW_SECONDS = 40
SPAWN_INTERVAL_SECONDS = 60


def official_chat_id() -> int | None:
    raw = (os.getenv("RENAISS_OFFICIAL_CHAT_ID") or os.getenv("RENAISS_QUIZ_CHAT_ID") or "").strip()
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError:
        return None


@dataclass
class ActiveSpawn:
    chat_id: int
    spawn: Spawn
    started_at: float
    message_id: int | None = None
    catchers: dict[int, str] = field(default_factory=dict)
    closing: bool = False


_active: dict[int, ActiveSpawn] = {}
_locks: dict[int, asyncio.Lock] = {}


def _lock(chat_id: int) -> asyncio.Lock:
    lock = _locks.get(chat_id)
    if lock is None:
        lock = asyncio.Lock()
        _locks[chat_id] = lock
    return lock


def _display_name(update: Update) -> str:
    user = update.effective_user
    if not user:
        return "Trainer"
    return user.full_name or user.username or user.first_name or "Trainer"


def _band_header(spawn: Spawn) -> str:
    if spawn.band == "grail":
        return "🔥 <b>GRAIL SPAWN</b> — everyone grab it!"
    if spawn.band == "rare":
        return "💎 <b>Rare spawn</b> approaching!"
    return "✨ A card appeared!"


def _spawn_text(active: ActiveSpawn) -> str:
    spawn = active.spawn
    remaining = max(0, CATCH_WINDOW_SECONDS - int(monotonic() - active.started_at))
    value = f"${spawn.market_usd:,.0f}" if spawn.market_usd >= 1 else ""
    lines = [
        _band_header(spawn),
        f"<b>{escape(spawn.card.card_name)}</b> · {escape(spawn.card.grade or '-')}"
        + (f" · {value}" if value else ""),
        "",
        "Type <code>c</code> to catch!",
        f"⏳ {remaining}s left · 👥 {len(active.catchers)} catching",
    ]
    return "\n".join(lines)


async def spawn_tick(context: ContextTypes.DEFAULT_TYPE) -> None:
    """1분마다 공식방에 스폰. 이미 진행 중이면 스킵."""
    chat_id = official_chat_id()
    if chat_id is None:
        return
    async with _lock(chat_id):
        existing = _active.get(chat_id)
        if existing and not existing.closing:
            return

    spawn = await roll_spawn("pokemon_tcg")
    if spawn is None:
        logger.debug("Spawn tick: empty pool, skipped.")
        return

    active = ActiveSpawn(chat_id=chat_id, spawn=spawn, started_at=monotonic())
    async with _lock(chat_id):
        _active[chat_id] = active

    try:
        message = await context.bot.send_message(
            chat_id=chat_id, text=_spawn_text(active), parse_mode="HTML"
        )
        active.message_id = message.message_id
    except Exception as exc:
        logger.warning("Spawn post failed: %s", exc)
        async with _lock(chat_id):
            _active.pop(chat_id, None)
        return

    context.job_queue.run_once(
        _resolve_job,
        when=CATCH_WINDOW_SECONDS,
        data=chat_id,
        name=f"renaiss_spawn_resolve_{chat_id}_{active.message_id}",
        job_kwargs={"misfire_grace_time": None},
    )


async def catch_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """`c` — 진행 중인 스폰을 잡는다 (창 안에 c 친 전원이 획득)."""
    if not update.effective_chat or not update.effective_user:
        return
    if update.effective_chat.type == "private":
        return
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id

    async with _lock(chat_id):
        active = _active.get(chat_id)
        if not active or active.closing:
            return
        if user_id in active.catchers:
            return
        active.catchers[user_id] = _display_name(update)
        try:
            await context.bot.edit_message_text(
                chat_id=chat_id,
                message_id=active.message_id,
                text=_spawn_text(active),
                parse_mode="HTML",
            )
        except Exception:
            pass


async def _resolve_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = context.job.data if context.job else None
    if chat_id is None:
        return
    async with _lock(chat_id):
        active = _active.get(chat_id)
        if not active or active.closing:
            return
        active.closing = True
    try:
        await _resolve(context, active)
    finally:
        async with _lock(chat_id):
            if _active.get(chat_id) is active:
                _active.pop(chat_id, None)


async def _resolve(context: ContextTypes.DEFAULT_TYPE, active: ActiveSpawn) -> None:
    spawn = active.spawn
    catchers = dict(active.catchers)

    if not catchers:
        try:
            await context.bot.edit_message_text(
                chat_id=active.chat_id,
                message_id=active.message_id,
                text=f"💨 <b>{escape(spawn.card.card_name)}</b> got away — nobody caught it.",
                parse_mode="HTML",
            )
        except Exception:
            pass
        return

    # 잡은 전원에게 카드 1장씩 등록 (병렬)
    await asyncio.gather(
        *(register_single_card(user_id=uid, category=spawn.card.category, card=spawn.card) for uid in catchers),
        return_exceptions=True,
    )

    # 등록 후 각자 누적 시세 조회 → "이 카드 시세 + 내 누적시세" 노출 (Lv1 가치 발견)
    totals = await get_portfolio_values(list(catchers.keys()))
    value = f"${spawn.market_usd:,.0f}" if spawn.market_usd >= 1 else "-"
    catch_lines = []
    for uid, name in list(catchers.items())[:12]:
        total = totals.get(uid, 0.0)
        catch_lines.append(f"· {escape(name)} → <b>${total:,.0f}</b>")
    tail = f"\n… +{len(catchers) - 12} more" if len(catchers) > 12 else ""
    caption = (
        f"{_band_header(spawn)}\n"
        "────────────\n"
        f"<b>{escape(spawn.card.card_name)}</b> · {escape(spawn.card.grade or '-')} · {value}\n"
        f"Caught by {len(catchers)} — new collection value:\n"
        + "\n".join(catch_lines)
        + tail
    )

    # 그레일·레어는 슬랩 라벨 이미지로 크게, 일반은 텍스트
    image_bytes = None
    if spawn.is_headline:
        try:
            price = RenaissPrice(
                status="candidate",
                source="spawn",
                fmv_usd=spawn.market_usd or None,
                image_url=spawn.card.image_url,
            )
            image_bytes = await render_overlay_card(spawn.card, price)
        except Exception:
            image_bytes = None

    try:
        if image_bytes:
            photo = BytesIO(image_bytes)
            photo.name = "renaiss_spawn.png"
            await context.bot.send_photo(chat_id=active.chat_id, photo=photo, caption=caption, parse_mode="HTML")
            await _clear_prompt(context, active)
        else:
            await context.bot.edit_message_text(
                chat_id=active.chat_id, message_id=active.message_id, text=caption, parse_mode="HTML"
            )
    except Exception as exc:
        logger.debug("Spawn resolve post skipped: %s", exc)


async def _clear_prompt(context: ContextTypes.DEFAULT_TYPE, active: ActiveSpawn) -> None:
    try:
        await context.bot.edit_message_text(
            chat_id=active.chat_id,
            message_id=active.message_id,
            text=f"✅ <b>{escape(active.spawn.card.card_name)}</b> caught!",
            parse_mode="HTML",
        )
    except Exception:
        pass
