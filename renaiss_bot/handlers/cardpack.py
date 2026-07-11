"""Pack-opening and private collection commands."""

from __future__ import annotations

import asyncio
import logging
import os
from html import escape
from io import BytesIO

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, LinkPreviewOptions, Update
from telegram.error import BadRequest, NetworkError, TimedOut
from telegram.ext import ContextTypes

from renaiss_bot.database.queries import (
    cancel_pack_open_reservation,
    finalize_command_free_pack,
    reserve_command_free_packs,
)
from renaiss_bot.renderers.overlay import overlay_cache_key, render_overlay_card
from renaiss_bot.services.categories import get_category, resolve_category_key
from renaiss_bot.services.captions import format_pack_caption
from renaiss_bot.services.features import private_free_packs_enabled
from renaiss_bot.services.market import daily_pick_enabled
from renaiss_bot.services.media_cache import (
    delete_telegram_file_id,
    get_telegram_file_id,
    remember_telegram_photo,
)
from renaiss_bot.services.pack import open_pack
from renaiss_bot.services.pack_rules import (
    DAILY_FREE_PACKS,
    MAX_BATCH_PACKS,
)
from renaiss_bot.services.portfolio import (
    PortfolioStats,
    get_portfolio_stats,
)
from renaiss_bot.services.tracking import build_tracked_url

_PACK_TYPE_ARGS = {"free", "normal", "standard", "premium", "bp", "paid"}
_GRADE_ORDER = ("MUR", "UR", "SAR", "SR", "AR", "RR", "R", "U", "C", "-")
logger = logging.getLogger(__name__)


def _price_keyboard(url: str | None) -> InlineKeyboardMarkup:
    rows = []
    if url:
        rows.append([InlineKeyboardButton("View on Renaiss", url=url)])
    rows.append([InlineKeyboardButton("My Collection", callback_data="renaiss:mycards")])
    return InlineKeyboardMarkup(rows)


def _portfolio_keyboard() -> InlineKeyboardMarkup | None:
    rows = []
    if private_free_packs_enabled():
        rows.append([InlineKeyboardButton("Open Pack", callback_data="renaiss:open")])
    if daily_pick_enabled():
        rows.append([InlineKeyboardButton("Daily Market Pick", callback_data="renaiss:market")])
    return InlineKeyboardMarkup(rows) if rows else None


async def _private_pack_closed_notice(update: Update) -> None:
    if update.effective_message:
        await update.effective_message.reply_text(
            "Private free packs are closed during the collector-market pilot. "
            "Join blind community spawns with <code>c</code>; cards you already collected remain available in <code>/mycards</code>.\n\n"
            "In-game collection only · no physical card or NFT ownership.",
            parse_mode="HTML",
        )


async def _dm_only_prompt(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    *,
    payload: str,
    label: str,
) -> None:
    if not update.effective_message:
        return
    username = getattr(getattr(context, "bot", None), "username", None)
    keyboard = None
    if username:
        keyboard = InlineKeyboardMarkup(
            [[InlineKeyboardButton(label, url=f"https://t.me/{username}?start={payload}")]]
        )
    await update.effective_message.reply_text(
        "Packs and collection details stay in private chat so the group can focus on blind catches.",
        reply_markup=keyboard,
    )


def _parse_open_args(args: list[str]) -> tuple[str, str, int, bool]:
    category = "pokemon_tcg"
    pack_type = "free"
    count = 1
    pay_with_rp = False
    for arg in args:
        normalized = arg.strip().lower()
        if not normalized:
            continue
        resolved_category = resolve_category_key(normalized)
        if resolved_category:
            category = resolved_category
        elif normalized == "rp":
            pay_with_rp = True
        elif normalized in _PACK_TYPE_ARGS:
            pack_type = normalized
        elif normalized.isdigit():
            count = min(max(int(normalized), 1), MAX_BATCH_PACKS)
    return category, pack_type, count, pay_with_rp


def _format_money(value) -> str:
    try:
        amount = float(value or 0)
    except (TypeError, ValueError):
        amount = 0
    if amount <= 0:
        return "-"
    if amount < 100:
        return f"${amount:,.2f}".rstrip("0").rstrip(".")
    return f"${amount:,.0f}"


def _grade_line(grades: list[dict]) -> str:
    counts = {str(row.get("grade") or "-"): int(row.get("count") or 0) for row in grades}
    parts = [f"{grade} {counts[grade]}" for grade in _GRADE_ORDER if counts.get(grade)]
    if not parts:
        parts = [
            f"{escape(str(row.get('grade') or '-'))} {int(row.get('count') or 0)}"
            for row in grades[:6]
        ]
    return " · ".join(parts) if parts else "-"


def _card_row(idx: int, row: dict) -> str:
    name = escape(str(row.get("card_name") or "-"))
    grade = escape(str(row.get("grade") or "-"))
    quantity = int(row.get("quantity") or 0)
    quantity_text = f" ×{quantity}" if quantity > 1 else ""
    parts = [f"{idx}. <b>{name}</b>{quantity_text} · {grade}"]
    set_code = str(row.get("set_code") or "").strip()
    if set_code:
        parts.append(escape(set_code.upper()))
    category = str(row.get("category") or "")
    if category and category != "pokemon_tcg":
        parts.append(escape(category.replace("_", " ").title()))
    price = _format_money(row.get("market_price_usd"))
    if price != "-":
        parts.append(f"<b>{price}</b>")
    return " · ".join(parts)


def _top_card_rows(rows: list[dict]) -> list[str]:
    return [_card_row(idx, row) for idx, row in enumerate(rows, 1)]


def _recent_card_rows(rows: list[dict]) -> list[str]:
    return [_card_row(idx, row) for idx, row in enumerate(rows, 1)]


def _category_rows(rows: list[dict]) -> str:
    parts = []
    for row in rows[:4]:
        label = escape(str(row.get("category") or "-").replace("_", " ").title())
        count = int(row.get("count") or 0)
        parts.append(f"{label} {count}")
    return " · ".join(parts) if parts else "-"


def _collection_web_url() -> str:
    return os.getenv(
        "RENAISS_COLLECTION_WEB_URL", "https://tgpoke.com/renaiss/mycards"
    ).strip()


def _portfolio_text(stats: PortfolioStats) -> str:
    achievement_count = len(stats.unlocked_achievements)
    total_achievements = len(stats.achievements)
    lines = ["🎴 <b>Renaiss Collection</b>", ""]

    value = _format_money(stats.total_value_usd)
    if value != "-":
        pending = (
            f" · {stats.unpriced_cards} awaiting a verified price"
            if stats.unpriced_cards > 0
            else ""
        )
        lines.append(f"💰 Est. value <b>{value}</b>{pending}")
    else:
        lines.append("💰 Est. value <b>pending</b> — verified prices update twice a day.")
    lines.append(
        f"📚 <b>{stats.total_cards}</b> cards · <b>{stats.unique_cards}</b> unique"
        f" · <b>{stats.sets}</b> sets"
    )
    if stats.season_pool_total > 0:
        percent = stats.owned_in_pool / stats.season_pool_total * 100
        bar_filled = min(10, round(percent / 10))
        if stats.owned_in_pool > 0:
            bar_filled = max(1, bar_filled)
        bar = "▰" * bar_filled + "▱" * (10 - bar_filled)
        lines.append(
            f"📈 Season pool {bar} <b>{stats.owned_in_pool} / {stats.season_pool_total}</b>"
            f" ({percent:.1f}%)"
        )
    grade_line = _grade_line(stats.grade_counts)
    if grade_line != "-":
        lines.append(f"🏅 {escape(grade_line)}")
    if len(stats.category_counts) > 1:
        lines.append(f"🗂 {_category_rows(stats.category_counts)}")

    lines.extend(["", f"🏆 <b>Achievements</b> {achievement_count}/{total_achievements}"])
    unlocked_titles = [escape(item.title) for item in stats.unlocked_achievements[:5]]
    if unlocked_titles:
        lines.append("✅ " + " · ".join(unlocked_titles))
    else:
        lines.append("None yet — join a group spawn with <code>c</code>.")

    top_rows = _top_card_rows(stats.top_cards)
    if top_rows:
        lines.extend(["", "⭐ <b>Top cards</b>", *top_rows])

    recent_rows = _recent_card_rows(stats.recent_cards)
    if recent_rows:
        lines.extend(["", "🕘 <b>Recent adds</b>", *recent_rows])

    lines.extend(
        [
            "",
            f'🔗 <a href="{escape(_collection_web_url(), quote=True)}">'
            "Open the full web collection</a>",
            "<i>In-game collection only · no physical card or NFT ownership.</i>",
        ]
    )
    return "\n".join(lines)


async def cmd_open(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not private_free_packs_enabled():
        await _private_pack_closed_notice(update)
        return
    args = list(getattr(context, "args", None) or [])
    category, pack_type, count, pay_with_rp = _parse_open_args(args)
    user_id = update.effective_user.id if update.effective_user else None
    chat_id = update.effective_chat.id if update.effective_chat else None

    if not get_category(category).enabled:
        if update.effective_message:
            await update.effective_message.reply_text(
                "That card category is planned but is not open for packs yet."
            )
        return

    if update.effective_chat and getattr(update.effective_chat, "type", "private") != "private":
        await _dm_only_prompt(
            update,
            context,
            payload=f"open_{category}",
            label="Open Pack in DM",
        )
        return

    if pay_with_rp or pack_type in {"premium", "bp", "paid"}:
        if update.effective_message:
            await update.effective_message.reply_text(
                "Premium and RP packs are not part of the current Renaiss pilot. "
                "Use <code>/open</code> for your daily free packs or join group spawns with <code>c</code>.",
                parse_mode="HTML",
            )
        return

    if user_id is None or update.effective_message is None:
        return
    update_id = getattr(update, "update_id", None)
    callback_id = getattr(getattr(update, "callback_query", None), "id", None)
    message_id = getattr(update.effective_message, "message_id", None)
    request_token = update_id if update_id is not None else callback_id or message_id
    if request_token is None:
        await update.effective_message.reply_text(
            "Pack opening is temporarily unavailable because this request could not be identified. "
            "No pack was opened."
        )
        return
    request_id = f"telegram:{request_token}:open"
    try:
        reservation = await reserve_command_free_packs(
            request_id=request_id,
            user_id=user_id,
            chat_id=chat_id,
            platform="telegram",
            category=category,
            requested_count=count,
            daily_limit=DAILY_FREE_PACKS,
        )
    except Exception as exc:
        logger.warning("Free pack reservation failed user=%s: %s", user_id, exc)
        await update.effective_message.reply_text(
            "Pack opening is temporarily unavailable because your daily quota could not be verified. "
            "No pack was opened.",
        )
        return
    if not reservation.created:
        if reservation.status == "quota_full":
            text = (
                f"📦 Daily free packs used ({DAILY_FREE_PACKS}/{DAILY_FREE_PACKS}).\n"
                "Join the next blind group spawn with <code>c</code>."
            )
        elif reservation.status == "completed":
            text = "This pack request was already completed. Check <code>/mycards</code>."
        elif reservation.status == "reserved":
            text = "This pack request is already being processed."
        else:
            text = "That pack request expired. Send a new <code>/open</code> command."
        await update.effective_message.reply_text(text, parse_mode="HTML")
        return
    try:
        result = await open_pack(
            user_id=user_id,
            category_key=category,
            pack_type="free",
            count=reservation.allowed_count,
        )
    except Exception as exc:
        logger.warning("Free pack generation failed user=%s: %s", user_id, exc)
        try:
            await cancel_pack_open_reservation(request_id)
        except Exception:
            logger.warning("Free pack reservation cancellation failed request=%s", request_id)
        await update.effective_message.reply_text("Pack opening failed. No pack was recorded.")
        return
    try:
        await finalize_command_free_pack(
            request_id=request_id,
            user_id=user_id,
            chat_id=chat_id,
            result=result,
        )
    except Exception as exc:
        logger.exception("Free pack finalization failed request=%s: %s", request_id, exc)
        try:
            confirmation = await reserve_command_free_packs(
                request_id=request_id,
                user_id=user_id,
                chat_id=chat_id,
                platform="telegram",
                category=category,
                requested_count=count,
                daily_limit=DAILY_FREE_PACKS,
            )
        except Exception:
            confirmation = None
        if confirmation is None or confirmation.status != "completed":
            await update.effective_message.reply_text(
                "The pack result could not be safely saved, so no successful opening is being announced."
            )
            return
        logger.warning("Recovered completed pack after uncertain finalize request=%s", request_id)
    free_left = max(
        0,
        DAILY_FREE_PACKS - reservation.used_before - reservation.allowed_count,
    )
    wallet_line = f"\n📦 Free packs left today: <b>{free_left}/{DAILY_FREE_PACKS}</b>"
    caption = format_pack_caption(result) + wallet_line
    render_key = overlay_cache_key(result.best_card, result.best_price)

    async def cached_or_rendered_photo():
        cached_file_id = await get_telegram_file_id(render_key)
        if cached_file_id:
            return cached_file_id
        return await render_overlay_card(result.best_card, result.best_price)

    tracked_result, render_result = await asyncio.gather(
        build_tracked_url(
            result.best_price.referral_url,
            user_id=user_id,
            chat_id=chat_id,
            local_card_id=result.best_card.local_card_id or None,
            source="telegram_pack",
        ),
        cached_or_rendered_photo(),
        return_exceptions=True,
    )
    if isinstance(tracked_result, BaseException):
        logger.warning("Pack tracking link failed request=%s: %s", request_id, tracked_result)
        tracked_url = result.best_price.referral_url
    else:
        tracked_url = tracked_result
    if isinstance(render_result, BaseException):
        logger.warning("Pack result render failed request=%s: %s", request_id, render_result)
        image_payload = None
    else:
        image_payload = render_result
    if image_payload:
        if isinstance(image_payload, bytes):
            photo = BytesIO(image_payload)
            photo.name = "renaiss_pack_result.png"
        else:
            photo = image_payload
        try:
            sent_message = await update.effective_message.reply_photo(
                photo=photo,
                caption=caption,
                parse_mode="HTML",
                reply_markup=_price_keyboard(tracked_url),
            )
            if isinstance(image_payload, bytes):
                await remember_telegram_photo(render_key, sent_message)
            return
        except BadRequest as exc:
            if isinstance(image_payload, str):
                await delete_telegram_file_id(render_key)
            logger.warning("Pack photo was rejected; using text request=%s: %s", request_id, exc)
        except (TimedOut, NetworkError) as exc:
            logger.error(
                "Pack photo delivery is ambiguous; no automatic duplicate request=%s: %s",
                request_id,
                exc,
            )
            return
        except Exception as exc:
            logger.error(
                "Pack photo delivery failed ambiguously; no automatic duplicate request=%s: %s",
                request_id,
                exc,
            )
            return
    await update.effective_message.reply_text(
        caption,
        parse_mode="HTML",
        reply_markup=_price_keyboard(tracked_url),
    )


async def cmd_pack(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not private_free_packs_enabled():
        await _private_pack_closed_notice(update)
        return
    if update.effective_message:
        await update.effective_message.reply_text(
            "<b>Renaiss Packs</b>\n"
            "------------\n"
            f"- <code>/open</code>: open a free pack ({DAILY_FREE_PACKS}/day)\n"
            f"- <code>/open 5</code>: open multiple packs at once (max {MAX_BATCH_PACKS})\n"
            "- <code>/open one_piece_tcg</code>: One Piece packs\n"
            "- <code>c</code>: enter the current blind group spawn",
            parse_mode="HTML",
        )


async def cmd_mycards(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_message:
        return
    if update.effective_chat and getattr(update.effective_chat, "type", "private") != "private":
        await _dm_only_prompt(
            update,
            context,
            payload="mycards",
            label="View Collection in DM",
        )
        return

    uid = update.effective_user.id if update.effective_user else None
    try:
        stats = await get_portfolio_stats(uid)
    except Exception as exc:
        logger.warning("Collection lookup failed user=%s: %s", uid, exc)
        await update.effective_message.reply_text(
            "Your collection is temporarily unavailable because it could not be verified."
        )
        return

    if not stats:
        next_step = "Join a blind group spawn with <code>c</code>."
        if private_free_packs_enabled():
            next_step = (
                "Join a blind group spawn with <code>c</code>, or use the optional "
                "private <code>/open</code> experiment."
            )
        await update.effective_message.reply_text(
            "<b>Renaiss Collection</b>\n"
            "------------\n"
            f"No cards are saved yet. {next_step}\n\n"
            "In-game collection only · no physical card or NFT ownership.",
            parse_mode="HTML",
            reply_markup=_portfolio_keyboard(),
        )
        return

    await update.effective_message.reply_text(
        _portfolio_text(stats),
        parse_mode="HTML",
        reply_markup=_portfolio_keyboard(),
        link_preview_options=LinkPreviewOptions(is_disabled=True),
    )
