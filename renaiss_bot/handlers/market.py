"""DM-first Daily Market Pick command and callback."""

from __future__ import annotations

import asyncio
import logging
from html import escape

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from renaiss_bot.database.event_queries import log_event
from renaiss_bot.database.market_queries import (
    MarketPickError,
    get_daily_pick,
    get_latest_daily_pick_result,
    list_market_board,
    lock_daily_pick,
)
from renaiss_bot.services.emoji import icon
from renaiss_bot.services.market import daily_pick_enabled, decision_window_open, today_kst
from renaiss_bot.services.tracking import build_tracked_url

logger = logging.getLogger(__name__)

MARKET_PREFIX = "renaiss:market:"
MARKET_CLOSED_TEXT = (
    "Daily Market Pick is being validated and is not open yet. "
    "Blind catches and market guesses are still available in the group."
)


def _money(value) -> str:
    return f"${float(value or 0):,.2f}".replace(".00", "")


def _market_text(
    board: list[dict],
    pick: dict | None,
    *,
    is_open: bool,
    notice: str | None = None,
    latest_result: dict | None = None,
) -> str:
    status = "🟢 PICK OPEN until 24:00 KST" if is_open else "🔒 PICK LOCKED · opens 21:00 KST"
    lines = [
        "<b>🎯 Daily Market Pick</b>",
        f"<code>{today_kst().isoformat()}</code> · {status}",
        "Choose one verified card. No money, positions, buying, selling, or fees.",
    ]
    if notice:
        lines.extend(["", f"<b>{escape(notice)}</b>"])

    if pick:
        current = pick.get("current_fmv_usd")
        move = ""
        if current is not None and float(pick["entry_fmv_usd"]) > 0:
            pct = (float(current) / float(pick["entry_fmv_usd"]) - 1) * 100
            move = f" · latest observed {_money(current)} ({pct:+.2f}%)"
        lines.extend(
            [
                "",
                "<b>My locked pick</b>",
                f"• {escape(str(pick['card_name']))} · starting reference "
                f"{_money(pick['entry_fmv_usd'])}{move}",
            ]
        )
    else:
        lines.extend(["", "You have not made today's pick yet."])

    if latest_result:
        result_value = float(latest_result["result_fmv_usd"])
        entry_value = float(latest_result["entry_fmv_usd"])
        move_pct = latest_result.get("result_move_pct")
        if move_pct is None and entry_value > 0:
            move_pct = (result_value / entry_value - 1) * 100
        move = f" ({float(move_pct):+.2f}%)" if move_pct is not None else ""
        confidence = str(latest_result.get("result_confidence") or "unrated")
        source_count = latest_result.get("result_source_count")
        source_evidence = f" | {source_count} sources" if source_count is not None else ""
        updated_at = latest_result.get("result_price_updated_at")
        freshness = (
            f" | mark {updated_at.date().isoformat()}"
            if getattr(updated_at, "date", None)
            else " | freshness unavailable"
        )
        result_link = ""
        raw_result_url = str(latest_result.get("asset_url") or "")
        if raw_result_url.startswith("https://"):
            result_link = (
                f' | <a href="{escape(raw_result_url, quote=True)}">Renaiss OS Index</a>'
            )
        lines.extend(
            [
                "",
                "<b>Latest 24h result</b>",
                f"<code>{escape(str(latest_result['pick_date']))}</code> | "
                f"{escape(str(latest_result['card_name']))}",
                f"Starting reference {_money(entry_value)} → 24h reference "
                f"{_money(result_value)}{move}",
                f"Verified result | {escape(confidence)} confidence{source_evidence}"
                f"{freshness}{result_link}",
            ]
        )

    lines.extend(["", "<b>Market Board</b>"])
    if not board:
        lines.append("No cards revealed this week yet. Join a blind spawn with <code>c</code>.")
    for item in board:
        gate = f"{icon('check')} pickable" if item.get("pick_eligible") else "🧪 watch only"
        source = ""
        raw_source_url = str(item.get("asset_url") or "")
        if raw_source_url.startswith("https://"):
            source_url = escape(raw_source_url, quote=True)
            source = f' · <a href="{source_url}">Renaiss OS Index</a>'
        lines.append(
            f"• <b>{escape(str(item['card_name']))}</b> · {_money(item['fmv_usd'])} · {gate}{source}"
        )

    lines.extend(
        [
            "",
            "Your first pick is final for the day. The first valid Renaiss mark after 24h shows the result.",
            "Renaiss values are experimental reference data, not investment advice.",
        ]
    )
    return "\n".join(lines)


def _market_keyboard(
    board: list[dict],
    pick: dict | None,
    *,
    is_open: bool,
) -> InlineKeyboardMarkup | None:
    if not is_open or pick is not None:
        return None
    rows = []
    for item in board:
        if item.get("pick_eligible"):
            rows.append(
                [
                    InlineKeyboardButton(
                        f"Pick {str(item['card_name'])[:28]}",
                        callback_data=f"{MARKET_PREFIX}pick:{int(item['id'])}",
                    )
                ]
            )
    return InlineKeyboardMarkup(rows) if rows else None


async def _load_view(
    user_id: int,
    *,
    notice: str | None = None,
    admission_open: bool = True,
) -> tuple[str, InlineKeyboardMarkup | None, dict | None]:
    board, pick, latest_result = await asyncio.gather(
        list_market_board(limit=10),
        get_daily_pick(user_id),
        get_latest_daily_pick_result(user_id),
    )
    tracked_urls = await asyncio.gather(
        *(
            build_tracked_url(
                item.get("asset_url"),
                user_id=user_id,
                chat_id=user_id,
                local_card_id=item.get("local_card_id"),
                source="telegram_daily_pick",
            )
            for item in board
        )
    )
    tracked_board = []
    for item, tracked_url in zip(board, tracked_urls):
        tracked_item = dict(item)
        tracked_item["asset_url"] = tracked_url
        tracked_board.append(tracked_item)
    tracked_result = None
    if latest_result:
        tracked_result = dict(latest_result)
        tracked_result["asset_url"] = await build_tracked_url(
            latest_result.get("asset_url"),
            user_id=user_id,
            chat_id=user_id,
            local_card_id=latest_result.get("local_card_id"),
            source="telegram_daily_pick_result",
        )
    is_open = admission_open and decision_window_open()
    return (
        _market_text(
            tracked_board,
            pick,
            is_open=is_open,
            notice=notice,
            latest_result=tracked_result,
        ),
        _market_keyboard(tracked_board, pick, is_open=is_open),
        tracked_result,
    )


async def _log_result_viewed(*, user_id: int, chat_id: int, result: dict | None) -> None:
    if not result:
        return
    await log_event(
        "daily_pick_result_viewed",
        event_key=f"daily-pick:{result['pick_date']}:result-view:{user_id}",
        user_id=user_id,
        chat_id=chat_id,
        metadata={
            "board_card_id": result.get("board_card_id"),
            "settlement_snapshot_id": result.get("settlement_snapshot_id"),
            "result_move_pct": result.get("result_move_pct"),
        },
    )


async def _dm_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_message:
        return
    username = context.bot.username
    keyboard = None
    if username:
        keyboard = InlineKeyboardMarkup(
            [[InlineKeyboardButton("Open Daily Market Pick", url=f"https://t.me/{username}?start=market")]]
        )
    await update.effective_message.reply_text(
        "Choose your Daily Market Pick in a private chat.",
        reply_markup=keyboard,
    )


async def _verified_official_community_id(
    context: ContextTypes.DEFAULT_TYPE,
    *,
    user_id: int,
) -> int | None:
    """Attribute a public cohort only after Telegram confirms current membership."""
    from renaiss_bot.handlers.spawn import official_chat_id

    community_id = official_chat_id()
    if community_id is None:
        return None
    try:
        member = await context.bot.get_chat_member(
            chat_id=community_id,
            user_id=user_id,
        )
    except Exception as exc:
        logger.info(
            "Daily Pick community membership verification failed user=%s chat=%s: %s",
            user_id,
            community_id,
            exc,
        )
        raise RuntimeError("official-group membership could not be verified") from exc

    status = str(getattr(member, "status", "")).strip().lower()
    if status in {"creator", "administrator", "member"}:
        return community_id
    if status == "restricted" and bool(getattr(member, "is_member", False)):
        return community_id
    return None


async def cmd_market(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_message or not update.effective_user or not update.effective_chat:
        return
    if update.effective_chat.type != "private":
        await _dm_prompt(update, context)
        return
    admission_open = daily_pick_enabled()
    await log_event(
        "daily_pick_viewed",
        event_key=f"daily-pick:{today_kst().isoformat()}:view:{update.effective_user.id}",
        user_id=update.effective_user.id,
        chat_id=update.effective_chat.id,
    )
    try:
        text, keyboard, latest_result = await _load_view(
            update.effective_user.id,
            notice=None if admission_open else MARKET_CLOSED_TEXT,
            admission_open=admission_open,
        )
    except Exception as exc:
        logger.warning("Daily Market Pick view failed user=%s: %s", update.effective_user.id, exc)
        await update.effective_message.reply_text(
            "Daily Market Pick is not available yet. Please try again shortly."
        )
        return
    await update.effective_message.reply_text(text, parse_mode="HTML", reply_markup=keyboard)
    await _log_result_viewed(
        user_id=update.effective_user.id,
        chat_id=update.effective_chat.id,
        result=latest_result,
    )


_ERROR_TEXT = {
    "pick_closed": "Today's pick window is open from 21:00 to 24:00 KST.",
    "card_not_eligible": "That card is watch-only until its Renaiss price is verified.",
    "already_picked": "Your first Daily Market Pick is already locked for today.",
}


async def on_market(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    user = update.effective_user
    chat = update.effective_chat
    if not query or not user or not chat:
        return
    if not daily_pick_enabled():
        await query.answer("Daily Market Pick is not open yet.", show_alert=True)
        return
    if chat.type != "private":
        await query.answer("Open Daily Market Pick in a private chat.")
        await _dm_prompt(update, context)
        return

    data = query.data or ""
    notice = None
    if data == "renaiss:market":
        await query.answer()
    else:
        parts = data.split(":")
        if len(parts) != 4 or parts[2] != "pick":
            await query.answer("Invalid Daily Pick action.")
            return
        await query.answer("Checking today's pick…")
        try:
            community_id = await _verified_official_community_id(
                context,
                user_id=user.id,
            )
            result = await lock_daily_pick(
                user_id=user.id,
                board_card_id=int(parts[3]),
                community_chat_id=community_id,
            )
            notice = (
                f"Locked: {result['card_name']} with starting reference "
                f"{_money(result['entry_fmv_usd'])}."
            )
            if community_id is None:
                notice += " This pick stays private because community game-room membership was not verified."
            await log_event(
                "daily_pick_locked",
                event_key=f"daily-pick:{result['pick_date'].isoformat()}:lock:{user.id}",
                user_id=user.id,
                chat_id=chat.id,
                metadata={
                    "board_card_id": int(parts[3]),
                    "entry_fmv_usd": result["entry_fmv_usd"],
                    "community_chat_id": community_id,
                },
            )
        except MarketPickError as exc:
            notice = _ERROR_TEXT.get(exc.code, "Daily Pick rejected.")
        except Exception as exc:
            logger.warning("Daily Market Pick failed user=%s: %s", user.id, exc)
            notice = "Daily Pick failed. Please try again."

    try:
        text, keyboard, latest_result = await _load_view(user.id, notice=notice)
        await query.edit_message_text(text, parse_mode="HTML", reply_markup=keyboard)
        await _log_result_viewed(user_id=user.id, chat_id=chat.id, result=latest_result)
    except Exception as exc:
        logger.warning("Daily Market Pick refresh failed user=%s: %s", user.id, exc)
