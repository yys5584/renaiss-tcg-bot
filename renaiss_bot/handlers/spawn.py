"""Official-room blind market spawns: catch with `c`, then guess the FMV.

A card appears on the configured group cadence with its value hidden. Catchers enter a random
winner draw while the whole room can guess the Renaiss reference price.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import random
import secrets
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from html import escape
from io import BytesIO
from time import monotonic

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.error import BadRequest, NetworkError, TimedOut
from telegram.ext import ContextTypes

from renaiss_bot.database.event_queries import (
    event_exists,
    log_event,
    log_events,
    release_spawn_dispatch,
    reserve_spawn_dispatch,
)
from renaiss_bot.database.market_queries import register_market_reveal
from renaiss_bot.database.queries import award_spawn_card, grant_first_c_starter
from renaiss_bot.renderers.overlay import overlay_cache_key, render_overlay_card
from renaiss_bot.services.client import fetch_official_price
from renaiss_bot.services.market import (
    VERIFIED_PRICE_SOURCES,
    exact_price_lookup_configured,
    market_card_eligible,
)
from renaiss_bot.services.media_cache import get_telegram_file_id, remember_telegram_photo
from renaiss_bot.services.models import RenaissPrice
from renaiss_bot.services.price_evidence import catalog_reference_price
from renaiss_bot.services.quiz import build_price_options, format_distribution, format_price_option
from renaiss_bot.services.spawn import Spawn, price_band, roll_spawn
from renaiss_bot.services.tracking import build_tracked_url

logger = logging.getLogger(__name__)

CATCH_WINDOW_SECONDS = 40
REVEAL_SUSPENSE_SECONDS = 1.5
COHORT_EXPERIMENT_NAME = "first-c-insight-v1"


def _starter_grant_timeout_seconds() -> float:
    try:
        configured = float(os.getenv("RENAISS_STARTER_GRANT_TIMEOUT_SECONDS", "1.0"))
    except ValueError:
        configured = 1.0
    return min(3.0, max(0.05, configured))


def _configured_interval(name: str, default: int) -> int:
    try:
        return max(60, int(os.getenv(name, str(default))))
    except ValueError:
        return default


def spawn_interval_bounds() -> tuple[int, int]:
    legacy = os.getenv("RENAISS_SPAWN_INTERVAL_SECONDS", "").strip()
    if legacy:
        fixed = _configured_interval("RENAISS_SPAWN_INTERVAL_SECONDS", 7200)
        return fixed, fixed
    # Public conversation is the product, so unattended defaults stay sparse.
    # A pilot may tighten this deliberately after measuring group noise.
    minimum = _configured_interval("RENAISS_SPAWN_INTERVAL_MIN_SECONDS", 7200)
    maximum = _configured_interval("RENAISS_SPAWN_INTERVAL_MAX_SECONDS", 14400)
    return minimum, max(minimum, maximum)


def next_spawn_delay(*, rng: random.Random | None = None) -> int:
    minimum, maximum = spawn_interval_bounds()
    return (rng or random.Random()).randint(minimum, maximum)


def first_spawn_delay() -> int:
    """Short launch warm-up; later rounds use the sparse randomized cadence."""
    try:
        return min(300, max(10, int(os.getenv("RENAISS_FIRST_SPAWN_DELAY_SECONDS", "60"))))
    except ValueError:
        return 60


def spawn_daily_cap() -> int:
    try:
        return max(1, min(48, int(os.getenv("RENAISS_SPAWN_DAILY_CAP", "6"))))
    except ValueError:
        return 6


def spawn_quiet_hours() -> tuple[int, int]:
    """Return the half-open KST quiet window; equal hours disable it."""
    try:
        start = int(os.getenv("RENAISS_SPAWN_QUIET_START_HOUR_KST", "0"))
        end = int(os.getenv("RENAISS_SPAWN_QUIET_END_HOUR_KST", "9"))
    except ValueError:
        return 0, 9
    if not (0 <= start <= 23 and 0 <= end <= 23):
        return 0, 9
    return start, end


def _assign_cohort_variant(
    *,
    spawn_token: str,
    guess_capable: bool,
) -> tuple[str | None, str | None]:
    """Pre-assign a verified round; disabled or unsafe config keeps normal gameplay."""
    enabled = os.getenv("RENAISS_COHORT_EXPERIMENT_ENABLED", "").strip().lower()
    if not guess_capable or enabled not in {"1", "true", "yes", "on"}:
        return None, None
    salt = os.getenv("RENAISS_COHORT_EXPERIMENT_SALT", "").strip()
    if len(salt) < 16:
        logger.error(
            "Cohort experiment stays off: RENAISS_COHORT_EXPERIMENT_SALT must be at least 16 characters."
        )
        return None, None
    digest = hashlib.sha256(
        f"{COHORT_EXPERIMENT_NAME}:{salt}:{spawn_token}".encode("utf-8")
    ).digest()
    assignment_id = f"{COHORT_EXPERIMENT_NAME}-{digest.hex()[:16]}"
    variant = "catch-only" if digest[0] % 2 == 0 else "insight-layer"
    return assignment_id, variant


def official_chat_id() -> int | None:
    raw = os.getenv("RENAISS_OFFICIAL_CHAT_ID", "").strip()
    if not raw:
        return None
    try:
        chat_id = int(raw)
    except ValueError:
        return None
    return chat_id if chat_id < 0 else None


@dataclass
class ActiveSpawn:
    chat_id: int
    spawn: Spawn
    started_at: float
    message_id: int | None = None
    catchers: dict[int, str] = field(default_factory=dict)
    price_options: list[float] = field(default_factory=list)
    correct_price_index: int | None = None
    verified_price: RenaissPrice | None = None
    guesses: dict[int, int] = field(default_factory=dict)
    token: str = field(default_factory=lambda: secrets.token_hex(16))
    guess_capable: bool = False
    assignment_id: str | None = None
    variant: str | None = None
    closing: bool = False
    telemetry_failure_count: int = 0


_active: dict[int, ActiveSpawn] = {}
_locks: dict[int, asyncio.Lock] = {}
_spawning: set[int] = set()
_first_c_feedback_users: set[int] = set()


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


def _catalog_reference_price(spawn: Spawn) -> RenaissPrice:
    return catalog_reference_price(spawn.card, market_usd=spawn.market_usd or None)


def _identity_line(spawn: Spawn) -> str:
    card = spawn.card
    set_label = card.set_name or card.set_code or "Unknown set"
    number = f" #{card.collector_number}" if card.collector_number else ""
    language = f" · {card.language}" if card.language else ""
    return f"{escape(set_label)}{escape(number)}{escape(language)}"


def _price_value_line(price: RenaissPrice) -> str:
    value = f"${float(price.fmv_usd):,.0f}" if price.fmv_usd and price.fmv_usd >= 1 else "-"
    is_renaiss = price.status == "exact" and price.source in VERIFIED_PRICE_SOURCES
    label = "Renaiss reference FMV" if is_renaiss else "Collection reference value"
    return f"💵 {label}: <b>{value}</b>"


def _price_evidence_lines(
    card,
    price: RenaissPrice,
    *,
    now: datetime | None = None,
) -> list[str]:
    current = now or datetime.now(timezone.utc)
    source_label = (
        "Renaiss OS Index"
        if price.source in VERIFIED_PRICE_SOURCES
        else "Local catalog"
    )
    status = price.status
    confidence = (
        f" · {escape(str(price.confidence))} confidence"
        if price.confidence
        else ""
    )
    freshness = "freshness unverified"
    if price.status == "exact" and price.source in VERIFIED_PRICE_SOURCES:
        updated = price.price_updated_at
        if updated is not None and updated.tzinfo is None:
            updated = updated.replace(tzinfo=timezone.utc)
        if updated is not None:
            age_seconds = max(0, int((current - updated.astimezone(timezone.utc)).total_seconds()))
            if age_seconds < 3600:
                freshness = f"updated {age_seconds // 60}m ago"
            elif age_seconds < 86400:
                freshness = f"updated {age_seconds // 3600}h ago"
            else:
                freshness = f"updated {age_seconds // 86400}d ago"
    evidence = f"🔎 {source_label} · {status}{confidence} · {freshness}"
    gate = (
        "✅ Verified for scored results"
        if market_card_eligible(card, price)
        else "🧪 Collection-only reference · excluded from scored results"
    )
    return [evidence, gate]


def _band_header(spawn: Spawn) -> str:
    if spawn.band == "grail":
        return "🔥 <b>GRAIL SPAWN</b> — everyone grab it!"
    if spawn.band == "rare":
        return "💎 <b>Rare spawn</b> approaching!"
    return "✨ A card appeared!"


def _guess_keyboard(active: ActiveSpawn) -> InlineKeyboardMarkup | None:
    if active.correct_price_index is None or not active.price_options:
        return None
    buttons = [
        InlineKeyboardButton(
            format_price_option(value),
            callback_data=f"renaiss:spawn_guess:{active.token}:{index}",
        )
        for index, value in enumerate(active.price_options)
    ]
    return InlineKeyboardMarkup([buttons[:2], buttons[2:]])


def _spawn_text(active: ActiveSpawn) -> str:
    spawn = active.spawn
    remaining = max(0, CATCH_WINDOW_SECONDS - int(monotonic() - active.started_at))
    action = "Type <code>c</code> to catch!"
    if active.price_options:
        action += " Then guess the market price below."
    if active.variant == "catch-only":
        price_line = "🔒 Catch-only pilot round · verified reference appears at reveal."
    elif active.price_options:
        price_line = "🔒 Verified Renaiss reference FMV is hidden until reveal."
    else:
        price_line = "🧪 Verified FMV guess unavailable — collection catch only."
    lines = [
        "🕵️ <b>BLIND MARKET SPAWN</b>",
        f"<b>{escape(spawn.card.card_name)}</b> · {escape(spawn.card.grade or '-')}",
        _identity_line(spawn),
        price_line,
        "",
        action,
        f"⏳ {remaining}s left · 👥 {len(active.catchers)} catching · 🧠 {len(active.guesses)} guessed",
    ]
    return "\n".join(lines)


def _spawn_window_open(active: ActiveSpawn) -> bool:
    return monotonic() - active.started_at < CATCH_WINDOW_SECONDS


def _guess_distribution_lines(active: ActiveSpawn) -> list[str]:
    if active.correct_price_index is None or not active.price_options:
        return []
    counts = [0] * len(active.price_options)
    for choice_index in active.guesses.values():
        if 0 <= choice_index < len(counts):
            counts[choice_index] += 1
    return [
        "",
        "🧠 <b>Market guesses</b>",
        *format_distribution(active.price_options, counts, active.correct_price_index),
    ]


def _spawn_event_metadata(active: ActiveSpawn) -> dict:
    return {
        "category": active.spawn.card.category,
        "local_card_id": active.spawn.card.local_card_id,
        "guess_capable": active.guess_capable,
        "has_price_guess": bool(active.price_options),
        "experiment_name": COHORT_EXPERIMENT_NAME if active.assignment_id else None,
        "assignment_id": active.assignment_id,
        "variant": active.variant,
        "message_id": active.message_id,
        "closes_at": (
            datetime.now(timezone.utc) + timedelta(seconds=CATCH_WINDOW_SECONDS)
        ).isoformat(),
    }


async def spawn_tick(context: ContextTypes.DEFAULT_TYPE) -> None:
    """설정된 주기로 공식방에 스폰. 이미 진행 중이면 스킵."""
    chat_id = official_chat_id()
    if chat_id is None:
        return
    async with _lock(chat_id):
        if _active.get(chat_id) is not None or chat_id in _spawning:
            return
        _spawning.add(chat_id)

    dispatch_token = secrets.token_hex(16)
    dispatch_held = False
    posted_with_anchor = False
    try:
        quiet_start, quiet_end = spawn_quiet_hours()
        try:
            reservation = await reserve_spawn_dispatch(
                chat_id=chat_id,
                lease_token=dispatch_token,
                daily_cap=spawn_daily_cap(),
                quiet_start_hour=quiet_start,
                quiet_end_hour=quiet_end,
                lease_seconds=CATCH_WINDOW_SECONDS + 30,
                minimum_interval_seconds=spawn_interval_bounds()[0],
            )
        except Exception as exc:
            logger.error("Spawn dispatch gate unavailable; failing closed: %s", exc)
            return
        if not reservation.acquired:
            logger.info(
                "Spawn tick skipped by dispatch gate reason=%s date=%s count=%s.",
                reservation.reason,
                reservation.spawn_date,
                reservation.dispatched_count,
            )
            return
        dispatch_held = True
        spawn = await roll_spawn("pokemon_tcg")
        if spawn is None:
            logger.debug("Spawn tick: empty pool, skipped.")
            return

        reference_price = _catalog_reference_price(spawn)
        if exact_price_lookup_configured():
            try:
                official_price = await fetch_official_price(spawn.card, timeout_seconds=5.0)
            except Exception as exc:
                logger.warning("Spawn exact-price lookup failed card=%s: %s", spawn.card.card_name, exc)
            else:
                if official_price is not None:
                    reference_price = official_price
        verified_price = reference_price if market_card_eligible(spawn.card, reference_price) else None
        if verified_price and verified_price.fmv_usd and verified_price.fmv_usd > 0:
            spawn = Spawn(
                card=spawn.card,
                band=price_band(float(verified_price.fmv_usd)),
                market_usd=verified_price.fmv_usd,
            )
        guess_capable = bool(verified_price and spawn.market_usd > 0)
        assignment_id, variant = _assign_cohort_variant(
            spawn_token=dispatch_token,
            guess_capable=guess_capable,
        )
        active = ActiveSpawn(
            chat_id=chat_id,
            spawn=spawn,
            started_at=monotonic(),
            verified_price=verified_price,
            token=dispatch_token,
            guess_capable=guess_capable,
            assignment_id=assignment_id,
            variant=variant,
        )
        if guess_capable and variant != "catch-only":
            active.price_options, active.correct_price_index = build_price_options(spawn.market_usd)
        async with _lock(chat_id):
            # A reservation prevents two slow API lookups from posting two spawns.
            if _active.get(chat_id) is not None:
                return
            _active[chat_id] = active

        try:
            message = await context.bot.send_message(
                chat_id=chat_id,
                text=_spawn_text(active),
                parse_mode="HTML",
                reply_markup=_guess_keyboard(active),
            )
            active.message_id = message.message_id
            posted_logged = await log_event(
                "spawn_posted",
                event_key=f"spawn:{active.token}:posted",
                chat_id=chat_id,
                session_id=active.token,
                metadata=_spawn_event_metadata(active),
            )
            if not posted_logged:
                posted_logged = await event_exists(f"spawn:{active.token}:posted") is True
            if not posted_logged:
                logger.error(
                    "Spawn prompt has no recovery anchor; cancelling session=%s",
                    active.token,
                )
                try:
                    await context.bot.edit_message_text(
                        chat_id=chat_id,
                        message_id=active.message_id,
                        text="This round was cancelled before entries opened. Watch for the next spawn.",
                        reply_markup=None,
                    )
                except Exception:
                    logger.warning(
                        "Untracked spawn prompt could not be cancelled session=%s",
                        active.token,
                    )
                async with _lock(chat_id):
                    if _active.get(chat_id) is active:
                        _active.pop(chat_id, None)
                return
            posted_with_anchor = True
        except Exception as exc:
            logger.warning("Spawn post failed: %s", exc)
            async with _lock(chat_id):
                if _active.get(chat_id) is active:
                    _active.pop(chat_id, None)
            return

        context.job_queue.run_once(
            _resolve_job,
            when=CATCH_WINDOW_SECONDS,
            data=chat_id,
            name=f"renaiss_spawn_resolve_{chat_id}_{active.message_id}",
            job_kwargs={"misfire_grace_time": None},
        )
    finally:
        if dispatch_held and not posted_with_anchor:
            try:
                await release_spawn_dispatch(chat_id=chat_id, lease_token=dispatch_token)
            except Exception as exc:
                logger.warning("Spawn dispatch lease release failed chat=%s: %s", chat_id, exc)
        async with _lock(chat_id):
            _spawning.discard(chat_id)


async def catch_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """`c` — 진행 중인 스폰의 랜덤 1인 포획 추첨에 참가한다."""
    if not update.effective_chat or not update.effective_user:
        return
    if update.effective_chat.type == "private":
        return
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id

    no_active_spawn = False
    already_catching = False
    refresh_active: ActiveSpawn | None = None
    async with _lock(chat_id):
        active = _active.get(chat_id)
        if not active or active.closing or not _spawn_window_open(active):
            no_active_spawn = True
        elif user_id in active.catchers:
            already_catching = True
        else:
            active.catchers[user_id] = _display_name(update)
            refresh_active = active

    starter_created = False
    if chat_id == official_chat_id():
        try:
            starter = await asyncio.wait_for(
                grant_first_c_starter(user_id=user_id, chat_id=chat_id),
                timeout=_starter_grant_timeout_seconds(),
            )
            starter_created = starter.created
        except asyncio.TimeoutError:
            starter_created = (
                await event_exists(f"renaiss:first-c-starter:user:{user_id}") is True
            )
            if not starter_created:
                logger.warning(
                    "First-c starter grant timed out without confirmation user=%s chat=%s",
                    user_id,
                    chat_id,
                )
        except Exception as exc:
            # A tutorial-card write must never remove a catch already accepted in memory.
            logger.warning("First-c starter grant failed user=%s chat=%s: %s", user_id, chat_id, exc)

    if refresh_active is not None:
        async with _lock(chat_id):
            if _active.get(chat_id) is refresh_active and not refresh_active.closing:
                refresh_text = _spawn_text(refresh_active)
                refresh_keyboard = _guess_keyboard(refresh_active)
                refresh_message_id = refresh_active.message_id
            else:
                refresh_message_id = None
        if refresh_message_id is not None:
            try:
                await context.bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=refresh_message_id,
                    text=refresh_text,
                    parse_mode="HTML",
                    reply_markup=refresh_keyboard,
                )
            except Exception:
                pass

    if no_active_spawn:
        if chat_id != official_chat_id() or user_id in _first_c_feedback_users:
            return
        _first_c_feedback_users.add(user_id)
        first_event_key = f"user:{user_id}:first-c-attempt"
        first_attempt = await log_event(
            "first_c_attempted",
            event_key=first_event_key,
            user_id=user_id,
            chat_id=chat_id,
        )
        if not first_attempt and not starter_created:
            already_recorded = await event_exists(first_event_key)
            if already_recorded is True:
                return
        if update.effective_message:
            starter_text = (
                "🎁 <b>First-c starter unlocked:</b> Renaiss Welcome Card was added "
                "to your collection.\n"
                "Tutorial collectible · no FMV or scored use · in-game only; "
                "no physical card or NFT ownership.\n\n"
                if starter_created
                else ""
            )
            await update.effective_message.reply_text(
                starter_text
                + "You found the catch key. No spawn is active right now — "
                "watch for the next BLIND MARKET SPAWN and type c again.",
                parse_mode="HTML",
                disable_notification=True,
            )
        return

    if starter_created and update.effective_message:
        await update.effective_message.reply_text(
            "🎁 <b>First-c starter unlocked:</b> Renaiss Welcome Card was added "
            "to your collection.\n"
            "Tutorial collectible · no FMV or scored use · in-game only; "
            "no physical card or NFT ownership.",
            parse_mode="HTML",
            disable_notification=True,
        )
    if already_catching:
        return

    recorded = await log_events(
        [
            {
                "event_name": "first_c_attempted",
                "event_key": f"user:{user_id}:first-c-attempt",
                "user_id": user_id,
                "chat_id": chat_id,
                "session_id": active.token,
            },
            {
                "event_name": "catch_entered",
                "event_key": f"spawn:{active.token}:catch:{user_id}",
                "user_id": user_id,
                "chat_id": chat_id,
                "session_id": active.token,
            },
            {
                "event_name": "first_c_entered",
                "event_key": f"user:{user_id}:first-c",
                "user_id": user_id,
                "chat_id": chat_id,
                "session_id": active.token,
            },
        ]
    )
    if not recorded:
        async with _lock(chat_id):
            if _active.get(chat_id) is active:
                active.telemetry_failure_count += 1


async def on_spawn_guess(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """공개방 스폰의 FMV 4지선다 추측을 한 번만 기록한다."""
    query = update.callback_query
    user = update.effective_user
    chat = update.effective_chat
    if not query or not user or not chat:
        return

    parts = (query.data or "").split(":")
    if len(parts) != 4:
        await query.answer("This market guess is no longer available.")
        return
    token = parts[2]
    try:
        choice_index = int(parts[3])
    except ValueError:
        await query.answer("Invalid price option.")
        return

    accepted = False
    async with _lock(chat.id):
        active = _active.get(chat.id)
        if (
            not active
            or active.closing
            or not _spawn_window_open(active)
            or active.token != token
            or not 0 <= choice_index < len(active.price_options)
        ):
            feedback = "This market guess is closed."
        elif user.id in active.guesses:
            locked = active.price_options[active.guesses[user.id]]
            feedback = f"Already locked: {format_price_option(locked)}"
        else:
            active.guesses[user.id] = choice_index
            selected = active.price_options[choice_index]
            feedback = f"Guess locked: {format_price_option(selected)}"
            accepted = True

    await query.answer(feedback)
    if not accepted:
        return

    await log_event(
        "price_guess_locked",
        event_key=f"spawn:{active.token}:guess:{user.id}",
        user_id=user.id,
        chat_id=chat.id,
        session_id=active.token,
        metadata={
            "choice_index": choice_index,
            "selected_fmv_usd": active.price_options[choice_index],
        },
    )

    # 마감과 동시에 들어온 콜백이 추첨 문구를 이전 프롬프트로 되돌리지 않게 확인한다.
    async with _lock(chat.id):
        if _active.get(chat.id) is not active or active.closing:
            return
        try:
            await context.bot.edit_message_text(
                chat_id=chat.id,
                message_id=active.message_id,
                text=_spawn_text(active),
                parse_mode="HTML",
                reply_markup=_guess_keyboard(active),
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


def _miss_line(catchers: dict[int, str], winner_id: int) -> str:
    others = [name for uid, name in catchers.items() if uid != winner_id]
    if not others:
        return ""
    shown = ", ".join(escape(name) for name in others[:8])
    tail = f" +{len(others) - 8} more" if len(others) > 8 else ""
    return f"\n😅 Missed it: {shown}{tail}"


async def _resolve(context: ContextTypes.DEFAULT_TYPE, active: ActiveSpawn) -> None:
    spawn = active.spawn
    catchers = dict(active.catchers)
    price = active.verified_price or _catalog_reference_price(spawn)
    verified = market_card_eligible(spawn.card, price)
    tracked_url = await build_tracked_url(
        price.referral_url or price.asset_url,
        user_id=None,
        chat_id=active.chat_id,
        local_card_id=spawn.card.local_card_id or None,
        source="telegram_spawn_reveal",
    )
    result_keyboard = (
        InlineKeyboardMarkup([[InlineKeyboardButton("View on Renaiss", url=tracked_url)]])
        if tracked_url
        else None
    )

    winner_id: int | None = None
    drawn_user_id: int | None = None
    award_failed = False
    winner_line: str
    if catchers:
        # 도전자 중 랜덤 1명 당첨 (가챠 뽑기) — 전원 획득이 아니라 "잡는 것 자체가 베팅".
        drawn_user_id, winner_name = random.choice(list(catchers.items()))
        try:
            await context.bot.edit_message_text(
                chat_id=active.chat_id,
                message_id=active.message_id,
                text=f"🎲 <b>Drawing a winner...</b> ({len(catchers)} challengers)",
                parse_mode="HTML",
                reply_markup=None,
            )
        except Exception:
            pass
        await asyncio.sleep(REVEAL_SUSPENSE_SECONDS)

        award_key = f"spawn:{active.chat_id}:{active.message_id or active.token}:award"
        award_result = None
        award_error: Exception | None = None
        for attempt in range(2):
            try:
                award_result = await award_spawn_card(
                    award_key=award_key,
                    spawn_token=active.token,
                    user_id=drawn_user_id,
                    winner_name=winner_name,
                    chat_id=active.chat_id,
                    category=spawn.card.category,
                    card=spawn.card,
                    price=price,
                )
                break
            except Exception as exc:
                award_error = exc
                if attempt == 0:
                    logger.warning(
                        "Spawn award response was uncertain; retrying the idempotent key "
                        "session=%s user=%s: %s",
                        active.token,
                        drawn_user_id,
                        exc,
                    )
        if award_result is None:
            award_failed = True
            logger.error(
                "Spawn award could not be verified session=%s user=%s: %s",
                active.token,
                drawn_user_id,
                award_error,
            )
            winner_line = (
                "⚠️ The collection award could not be verified, so no successful catch "
                f"is being announced yet. Reference: <code>{escape(active.token)}</code>"
            )
        else:
            winner_id = drawn_user_id
            winner_line = (
                f"🏆 Caught by <b>{escape(winner_name)}</b> · added to their in-game collection"
                + _miss_line(catchers, winner_id)
            )
    else:
        winner_line = "💨 Nobody entered the catch — the card got away."

    evidence_lines = _price_evidence_lines(spawn.card, price)
    caption = "\n".join(
        [
            _band_header(spawn) if verified else "✨ <b>CARD REVEAL</b>",
            "────────────",
            f"<b>{escape(spawn.card.card_name)}</b> · {escape(spawn.card.grade or '-')}",
            _identity_line(spawn),
            _price_value_line(price),
            *evidence_lines,
            winner_line,
            "In-game collectible only · no physical card or NFT ownership.",
            *_guess_distribution_lines(active),
        ]
    )

    # 그레일·레어는 슬랩 라벨 이미지로 크게, 일반은 텍스트
    image_payload = None
    render_key = overlay_cache_key(spawn.card, price)
    if spawn.is_headline:
        try:
            image_payload = await get_telegram_file_id(render_key)
            if not image_payload:
                image_payload = await render_overlay_card(spawn.card, price)
        except Exception:
            image_payload = None

    reveal_posted = False
    reveal_delivery_unknown = False
    if image_payload:
        try:
            if isinstance(image_payload, bytes):
                photo = BytesIO(image_payload)
                photo.name = "renaiss_spawn.png"
            else:
                photo = image_payload
            sent_message = await context.bot.send_photo(
                chat_id=active.chat_id,
                photo=photo,
                caption=caption,
                parse_mode="HTML",
                reply_markup=result_keyboard,
            )
            if isinstance(image_payload, bytes):
                await remember_telegram_photo(render_key, sent_message)
            reveal_posted = True
            await _clear_prompt(context, active, caught=winner_id is not None)
        except BadRequest as exc:
            logger.debug("Spawn photo reveal failed; falling back to text: %s", exc)
        except (TimedOut, NetworkError) as exc:
            reveal_delivery_unknown = True
            logger.error("Spawn photo reveal delivery is ambiguous session=%s: %s", active.token, exc)
        except Exception as exc:
            reveal_delivery_unknown = True
            logger.error("Spawn photo reveal failed ambiguously session=%s: %s", active.token, exc)
    if not reveal_posted and not reveal_delivery_unknown:
        try:
            await context.bot.edit_message_text(
                chat_id=active.chat_id,
                message_id=active.message_id,
                text=caption,
                parse_mode="HTML",
                reply_markup=result_keyboard,
            )
            reveal_posted = True
        except Exception as exc:
            logger.warning("Spawn reveal delivery failed session=%s: %s", active.token, exc)

    # Market Board is post-reveal only. Never expose a hidden value through /market first.
    if reveal_posted:
        try:
            await register_market_reveal(card=spawn.card, price=price)
        except Exception as exc:
            logger.error(
                "Post-reveal Market Board registration failed session=%s: %s",
                active.token,
                exc,
            )

    if award_failed:
        terminal_event = "spawn_award_failed"
        terminal_suffix = "award-failed"
    elif reveal_posted:
        terminal_event = "spawn_revealed"
        terminal_suffix = "revealed"
    elif reveal_delivery_unknown:
        terminal_event = "spawn_reveal_delivery_unknown"
        terminal_suffix = "reveal-delivery-unknown"
    else:
        terminal_event = "spawn_reveal_failed"
        terminal_suffix = "reveal-failed"
    terminal_logged = await log_event(
        terminal_event,
        event_key=f"spawn:{active.token}:{terminal_suffix}",
        user_id=winner_id,
        chat_id=active.chat_id,
        session_id=active.token,
        metadata={
            "category": spawn.card.category,
            "local_card_id": spawn.card.local_card_id,
            "price_status": price.status,
            "price_source": price.source,
            "fmv_usd": price.fmv_usd,
            "catcher_count": len(catchers),
            "guess_count": len(active.guesses),
            "delivery_succeeded": reveal_posted,
            "award_succeeded": winner_id is not None,
            "drawn_user_id": drawn_user_id,
            "correct_guess_count": sum(
                1
                for choice in active.guesses.values()
                if choice == active.correct_price_index
            ),
            "telemetry_failure_count": active.telemetry_failure_count,
        },
    )
    if not terminal_logged:
        logger.error(
            "Spawn terminal event was not persisted session=%s event=%s; "
            "restart recovery will reconcile from the award record.",
            active.token,
            terminal_event,
        )


async def _clear_prompt(
    context: ContextTypes.DEFAULT_TYPE,
    active: ActiveSpawn,
    *,
    caught: bool,
) -> None:
    try:
        outcome = "caught" if caught else "revealed"
        await context.bot.edit_message_text(
            chat_id=active.chat_id,
            message_id=active.message_id,
            text=f"✅ <b>{escape(active.spawn.card.card_name)}</b> {outcome} — result posted below.",
            parse_mode="HTML",
            reply_markup=None,
        )
    except Exception:
        pass
