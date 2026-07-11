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
from renaiss_bot.handlers.message_cleanup import (
    command_delete_delay_seconds,
    delete_group_command_job,
)
from renaiss_bot.database.queries import award_spawn_card, grant_first_c_starter
from renaiss_bot.renderers.overlay import (
    overlay_cache_key,
    prompt_render_key,
    render_overlay_card,
    render_prompt_card,
)
from renaiss_bot.services.market import (
    VERIFIED_PRICE_SOURCES,
    market_card_eligible,
)
from renaiss_bot.services.media_cache import (
    delete_telegram_file_id,
    get_telegram_file_id,
    remember_telegram_photo,
)
from renaiss_bot.services.models import RenaissPrice
from renaiss_bot.services.price_evidence import catalog_reference_price
from renaiss_bot.services.quiz import build_price_options, format_distribution, format_price_option
from renaiss_bot.services.emoji import grade_bar, icon
from renaiss_bot.services.spawn import Spawn, price_band, roll_spawn, tier_display
from renaiss_bot.services.tracking import build_tracked_url

logger = logging.getLogger(__name__)

REVEAL_SUSPENSE_SECONDS = 1.5
COHORT_EXPERIMENT_NAME = "first-c-insight-v1"


def _starter_grant_timeout_seconds() -> float:
    try:
        configured = float(os.getenv("RENAISS_STARTER_GRANT_TIMEOUT_SECONDS", "1.0"))
    except ValueError:
        configured = 1.0
    return min(3.0, max(0.05, configured))


def catch_window_seconds() -> int:
    """Return the configured entry window, including the 20s high-cadence profile."""
    try:
        return min(120, max(10, int(os.getenv("RENAISS_SPAWN_CATCH_WINDOW_SECONDS", "40"))))
    except ValueError:
        return 40


def _configured_interval(name: str, default: int) -> int:
    try:
        return max(15, int(os.getenv(name, str(default))))
    except ValueError:
        return default


def spawn_interval_bounds() -> tuple[int, int]:
    legacy = os.getenv("RENAISS_SPAWN_INTERVAL_SECONDS", "").strip()
    if legacy:
        fixed = _configured_interval("RENAISS_SPAWN_INTERVAL_SECONDS", 7200)
        fixed = max(catch_window_seconds() + 5, fixed)
        return fixed, fixed
    # Public conversation is the product, so unattended defaults stay sparse.
    # A pilot may tighten this deliberately after measuring group noise.
    minimum = max(
        catch_window_seconds() + 5,
        _configured_interval("RENAISS_SPAWN_INTERVAL_MIN_SECONDS", 7200),
    )
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


BURST_FLAG_KEY = "renaiss_spawn_burst_active"


def spawn_burst_active(application) -> bool:
    """Whether the operator burst mode is on for this process."""
    bot_data = getattr(application, "bot_data", None)
    return bool(bot_data.get(BURST_FLAG_KEY)) if isinstance(bot_data, dict) else False


def burst_interval_seconds() -> int:
    try:
        value = int(os.getenv("RENAISS_SPAWN_BURST_INTERVAL_SECONDS", "60"))
    except ValueError:
        value = 60
    return min(3600, max(15, value))


def burst_daily_cap() -> int:
    try:
        value = int(os.getenv("RENAISS_SPAWN_BURST_DAILY_CAP", "3000"))
    except ValueError:
        value = 3000
    return min(5000, max(1, value))


def spawn_daily_cap() -> int:
    try:
        return max(1, min(5000, int(os.getenv("RENAISS_SPAWN_DAILY_CAP", "6"))))
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


async def _roll_spawn_category(rng: random.Random | None = None) -> str:
    """Pick a spawn category weighted by active catalog size (DB-only, fail-safe)."""
    try:
        from renaiss_bot.database.catalog_queries import active_category_counts

        counts = await active_category_counts()
    except Exception:
        counts = {}
    weighted = [(category, count) for category, count in counts.items() if count > 0]
    if not weighted:
        return "pokemon_tcg"
    chooser = rng or random
    categories, weights = zip(*weighted)
    return chooser.choices(categories, weights=weights, k=1)[0]


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
    prompt_is_photo: bool = False
    receipt_message_ids: list[int] = field(default_factory=list)


_active: dict[int, ActiveSpawn] = {}
_locks: dict[int, asyncio.Lock] = {}
_spawning: set[int] = set()
_first_c_feedback_users: set[int] = set()


async def _edit_prompt_message(
    context: ContextTypes.DEFAULT_TYPE,
    active: "ActiveSpawn",
    *,
    text: str,
    reply_markup=None,
) -> None:
    """Edit the live prompt whether it was posted as a photo or as text."""
    if active.prompt_is_photo:
        await context.bot.edit_message_caption(
            chat_id=active.chat_id,
            message_id=active.message_id,
            caption=text,
            parse_mode="HTML",
            reply_markup=reply_markup,
        )
    else:
        await context.bot.edit_message_text(
            chat_id=active.chat_id,
            message_id=active.message_id,
            text=text,
            parse_mode="HTML",
            reply_markup=reply_markup,
        )


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


def _tier_badge(grade: str | None) -> str:
    """TGPoke 등급 바(커스텀 이모지) + Renaiss 티어 표기."""
    bar = grade_bar(grade)
    label = escape(tier_display(grade))
    return f"{bar} {label}" if bar else label


def _identity_line(spawn: Spawn) -> str:
    card = spawn.card
    set_label = card.set_name or card.set_code or "Unknown set"
    # 프로모 세트명은 문장 수준으로 길어질 수 있어 한 줄 가독성을 위해 자른다.
    if len(set_label) > 28:
        set_label = set_label[:27].rstrip() + "…"
    number = f" #{card.collector_number}" if card.collector_number else ""
    language = f" · {card.language}" if card.language else ""
    return f"{escape(set_label)}{escape(number)}{escape(language)}"


def _freshness_text(price: RenaissPrice, *, now: datetime | None = None) -> str | None:
    if price.status != "exact" or price.source not in VERIFIED_PRICE_SOURCES:
        return None
    updated = price.price_updated_at
    if updated is None:
        return None
    if updated.tzinfo is None:
        updated = updated.replace(tzinfo=timezone.utc)
    current = now or datetime.now(timezone.utc)
    age_seconds = max(0, int((current - updated.astimezone(timezone.utc)).total_seconds()))
    if age_seconds < 3600:
        return f"{age_seconds // 60}m ago"
    if age_seconds < 86400:
        return f"{age_seconds // 3600}h ago"
    return f"{age_seconds // 86400}d ago"


def _price_summary_line(
    card,
    price: RenaissPrice,
    *,
    now: datetime | None = None,
) -> str:
    """One compact line: value plus its trust level."""
    value = f"${float(price.fmv_usd):,.0f}" if price.fmv_usd and price.fmv_usd >= 1 else "-"
    if market_card_eligible(card, price):
        freshness = _freshness_text(price, now=now)
        suffix = f" · {freshness}" if freshness else ""
        return f"{icon('coin')} <b>{value}</b> {icon('check')} Renaiss FMV{suffix}"
    return f"{icon('coin')} <b>{value}</b> · 🧪 unverified"


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
    remaining = max(0, catch_window_seconds() - int(monotonic() - active.started_at))
    action = "Type <code>c</code> to catch!"
    if active.price_options:
        action += " Guess the price below."
    lines = [
        "🕵️ <b>BLIND MARKET SPAWN</b>",
        f"<b>{escape(spawn.card.card_name)}</b> · {_tier_badge(spawn.card.grade)}",
        _identity_line(spawn),
        action,
        f"⏳ {remaining}s · 👥 {len(active.catchers)} · 🧠 {len(active.guesses)}",
    ]
    return "\n".join(lines)


def _spawn_window_open(active: ActiveSpawn) -> bool:
    return monotonic() - active.started_at < catch_window_seconds()


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
            datetime.now(timezone.utc) + timedelta(seconds=catch_window_seconds())
        ).isoformat(),
    }


async def spawn_tick(context: ContextTypes.DEFAULT_TYPE, *, burst: bool = False) -> None:
    """설정된 주기로 공식방에 스폰. 이미 진행 중이면 스킵.

    ``burst``는 운영자 force/연속 모드다. 파일럿 일일 캡·조용시간·최소 간격 대신
    별도의 burst 상한을 쓰지만, 같은 DB dispatch gate를 통과하므로 다중 인스턴스
    중복 스폰과 무제한 게시는 여전히 차단된다.
    """
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
        quiet_start, quiet_end = (0, 0) if burst else spawn_quiet_hours()
        try:
            reservation = await reserve_spawn_dispatch(
                chat_id=chat_id,
                lease_token=dispatch_token,
                daily_cap=burst_daily_cap() if burst else spawn_daily_cap(),
                quiet_start_hour=quiet_start,
                quiet_end_hour=quiet_end,
                lease_seconds=catch_window_seconds() + 30,
                minimum_interval_seconds=0 if burst else spawn_interval_bounds()[0],
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
        spawn = await roll_spawn(await _roll_spawn_category())
        if spawn is None:
            # 선택된 카테고리 풀이 비어 있으면 기본 카테고리로 한 번 더 시도한다.
            spawn = await roll_spawn("pokemon_tcg")
        if spawn is None:
            logger.debug("Spawn tick: empty pool, skipped.")
            return

        # The public loop is DB-only. Twice-daily background refreshes persist
        # exact evidence; an API outage must never delay or stop a spawn.
        reference_price = _catalog_reference_price(spawn)
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
            # 등급(가격대)별 블라인드 프레임 이미지를 우선 시도하고, 실패 시 텍스트 프롬프트.
            message = None
            try:
                prompt_key = prompt_render_key(spawn.card.grade or "R", spawn.card.category)
                prompt_payload = await get_telegram_file_id(prompt_key)
                if not prompt_payload:
                    prompt_payload = await render_prompt_card(
                        spawn.card.grade or "R", spawn.card.category
                    )
                if prompt_payload:
                    if isinstance(prompt_payload, bytes):
                        prompt_photo = BytesIO(prompt_payload)
                        prompt_photo.name = "renaiss_spawn_prompt.png"
                    else:
                        prompt_photo = prompt_payload
                    message = await context.bot.send_photo(
                        chat_id=chat_id,
                        photo=prompt_photo,
                        caption=_spawn_text(active),
                        parse_mode="HTML",
                        reply_markup=_guess_keyboard(active),
                    )
                    active.prompt_is_photo = True
                    if isinstance(prompt_payload, bytes):
                        await remember_telegram_photo(prompt_key, message)
            except BadRequest:
                if isinstance(prompt_payload, str):
                    await delete_telegram_file_id(prompt_key)
                message = None
            except Exception:
                message = None
            if message is None:
                active.prompt_is_photo = False
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
                    await _edit_prompt_message(
                        context,
                        active,
                        text="This round was cancelled before entries opened. Watch for the next spawn.",
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
            when=catch_window_seconds(),
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
                entry_count = len(refresh_active.catchers)
            else:
                refresh_message_id = None
        if refresh_message_id is not None:
            try:
                await _edit_prompt_message(
                    context,
                    refresh_active,
                    text=refresh_text,
                    reply_markup=refresh_keyboard,
                )
            except Exception:
                pass
            # 시즌1식 참가 액션("포켓볼을 던졌다!")의 가챠 버전. 리빌 때 일괄
            # 삭제하고, 라운드가 비정상 종료돼도 남지 않게 60초 폴백 청소를 건다.
            if update.effective_message:
                try:
                    receipt = await update.effective_message.reply_text(
                        f"🎴 <b>{escape(_display_name(update))}</b> opened a pack! "
                        f"({entry_count} in)",
                        parse_mode="HTML",
                        disable_notification=True,
                    )
                    async with _lock(chat_id):
                        if _active.get(chat_id) is refresh_active:
                            refresh_active.receipt_message_ids.append(receipt.message_id)
                    job_queue = getattr(context, "job_queue", None)
                    if job_queue is not None:
                        job_queue.run_once(
                            delete_group_command_job,
                            when=command_delete_delay_seconds(),
                            data={"chat_id": chat_id, "message_id": receipt.message_id},
                            name=f"renaiss_command_cleanup_{chat_id}_{receipt.message_id}",
                            job_kwargs={"misfire_grace_time": 300},
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
            await _edit_prompt_message(
                context,
                active,
                text=_spawn_text(active),
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
    shown = ", ".join(escape(name) for name in others[:6])
    tail = f" +{len(others) - 6}" if len(others) > 6 else ""
    return f"\n😅 Missed: {shown}{tail}"


async def _resolve(context: ContextTypes.DEFAULT_TYPE, active: ActiveSpawn) -> None:
    spawn = active.spawn
    catchers = dict(active.catchers)
    price = active.verified_price or _catalog_reference_price(spawn)
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
            await _edit_prompt_message(
                context,
                active,
                text=f"🎲 <b>Drawing a winner...</b> ({len(catchers)} challengers)",
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
                f"{icon('gotcha')} <b>{escape(winner_name)}</b> caught it"
                + _miss_line(catchers, winner_id)
            )
    else:
        winner_line = "💨 Nobody caught it."

    band_emoji = {"grail": "🔥", "rare": "💎"}.get(spawn.band, "✨")
    caption = "\n".join(
        [
            _price_summary_line(spawn.card, price),
            f"{band_emoji} <b>{escape(spawn.card.card_name)}</b> · {_tier_badge(spawn.card.grade)}",
            _identity_line(spawn),
            winner_line,
            *_guess_distribution_lines(active),
        ]
    )

    # 시즌1처럼 모든 리빌은 등급 슬랩 이미지를 시도하고, 실패 시 텍스트로 폴백한다.
    image_payload = None
    render_key = overlay_cache_key(spawn.card, price)
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
            if isinstance(image_payload, str):
                await delete_telegram_file_id(render_key)
            logger.debug("Spawn photo reveal failed; falling back to text: %s", exc)
        except (TimedOut, NetworkError) as exc:
            reveal_delivery_unknown = True
            logger.error("Spawn photo reveal delivery is ambiguous session=%s: %s", active.token, exc)
        except Exception as exc:
            reveal_delivery_unknown = True
            logger.error("Spawn photo reveal failed ambiguously session=%s: %s", active.token, exc)
    if not reveal_posted and not reveal_delivery_unknown:
        try:
            await _edit_prompt_message(
                context,
                active,
                text=caption,
                reply_markup=result_keyboard,
            )
            reveal_posted = True
        except Exception as exc:
            logger.warning("Spawn reveal delivery failed session=%s: %s", active.token, exc)

    # 시즌1처럼 결과가 붙는 순간 참가 액션 메시지를 정리한다 (best-effort).
    for receipt_id in active.receipt_message_ids:
        try:
            await context.bot.delete_message(chat_id=active.chat_id, message_id=receipt_id)
        except Exception:
            pass

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
        await _edit_prompt_message(
            context,
            active,
            text=f"✅ <b>{escape(active.spawn.card.card_name)}</b> {outcome} — result posted below.",
        )
    except Exception:
        pass
