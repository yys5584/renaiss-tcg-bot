"""Price lookup command."""

from __future__ import annotations

import asyncio
import os
from html import escape
from time import monotonic

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from renaiss_bot.services.categories import split_category_args
from renaiss_bot.services.market import VERIFIED_PRICE_SOURCES
from renaiss_bot.services.models import CardIdentity
from renaiss_bot.services.pricing import fetch_price
from renaiss_bot.services.tracking import build_tracked_url

_STATUS_LABELS = {
    "exact": "exact match",
    "candidate": "candidate match",
    "search_only": "search link",
    "missing": "not indexed",
    "api_error": "API error",
}
_price_gate_lock = asyncio.Lock()
_price_last_request: dict[int, float] = {}


def _price_cooldown_seconds() -> int:
    try:
        return max(3, min(60, int(os.getenv("RENAISS_PRICE_USER_COOLDOWN_SECONDS", "10"))))
    except (TypeError, ValueError, OverflowError):
        return 10


async def _claim_price_request(user_id: int) -> tuple[bool, int]:
    async with _price_gate_lock:
        now = monotonic()
        cooldown = _price_cooldown_seconds()
        elapsed = now - _price_last_request.get(user_id, 0.0)
        if elapsed < cooldown:
            return False, max(1, int(cooldown - elapsed + 0.999))
        _price_last_request[user_id] = now
        if len(_price_last_request) > 10_000:
            cutoff = now - cooldown * 2
            for stale_user in [uid for uid, seen in _price_last_request.items() if seen < cutoff]:
                _price_last_request.pop(stale_user, None)
        return True, 0


def _args_from_text(raw_text: str) -> list[str]:
    text = raw_text.strip()
    if text.startswith("/price"):
        text = text.removeprefix("/price").strip()
    return text.split()


def _price_keyboard(url: str | None) -> InlineKeyboardMarkup | None:
    if not url:
        return None
    return InlineKeyboardMarkup([[InlineKeyboardButton("View on Renaiss", url=url)]])


async def _dm_price_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    if not message:
        return
    username = getattr(getattr(context, "bot", None), "username", None)
    keyboard = None
    if username:
        keyboard = InlineKeyboardMarkup(
            [[InlineKeyboardButton("Check Price in DM", url=f"https://t.me/{username}?start=price")]]
        )
    await message.reply_text(
        "Price checks stay in private chat so blind spawn values remain hidden. "
        "Open the bot privately, then send your /price command again.",
        reply_markup=keyboard,
    )


async def cmd_price(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_message:
        return
    if update.effective_chat and getattr(update.effective_chat, "type", "private") != "private":
        await _dm_price_prompt(update, context)
        return

    raw_text = update.effective_message.text if update.effective_message else ""
    args = list(getattr(context, "args", []) or []) or _args_from_text(raw_text)
    category, query_args = split_category_args(args)
    query = " ".join(query_args).strip()

    if not query:
        if update.effective_message:
            await update.effective_message.reply_text(
                "Usage: <code>/price Charizard</code> or <code>/price one_piece_tcg Luffy</code>",
                parse_mode="HTML",
            )
        return

    if update.effective_user is not None:
        allowed, retry_after = await _claim_price_request(update.effective_user.id)
        if not allowed:
            await update.effective_message.reply_text(
                f"Please wait {retry_after}s before another price check."
            )
            return

    card = CardIdentity(category=category, card_name=query, grade="PSA 10")
    # Search results cannot prove that RAW and graded offers share the same
    # set/number/printing, so the old cross-reprint multiplier is intentionally
    # hidden until the Partner API exposes structural grade offers.
    price = await fetch_price(card)
    lines = [
        f"<b>{escape(card.card_name)}</b>",
        "------------",
        f"Category: <code>{escape(category)}</code>",
        f"Match: <code>{escape(_STATUS_LABELS.get(price.status, price.status))}</code>",
    ]
    if price.fmv_usd is not None:
        explicit_freshness = price.price_updated_at is not None
        value_label = (
            "Renaiss reference FMV"
            if price.status == "exact"
            and price.source in VERIFIED_PRICE_SOURCES
            and explicit_freshness
            else "Candidate reference value"
        )
        badge = icon("check") if value_label.startswith("Renaiss") else "🧪"
        lines.append(f"{icon('coin')} {value_label}: <b>${price.fmv_usd:,.2f}</b> {badge}")
    if price.grade_label:
        lines.append(f"Grade: <b>{escape(price.grade_label)}</b>")
    if price.grading_company:
        lines.append(f"Grader: <code>{escape(price.grading_company)}</code>")
    if price.change_7d_pct is not None:
        lines.append(f"7d change: <b>{price.change_7d_pct:+.1f}%</b>")
    if price.price_range_min_usd is not None and price.price_range_max_usd is not None:
        lines.append(
            f"Candidate range: ${price.price_range_min_usd:,.2f} - ${price.price_range_max_usd:,.2f}"
        )

    lines.append(f"Source: <code>{escape(price.source)}</code>")
    if price.confidence:
        confidence = escape(price.confidence)
        score = f" ({price.confidence_score:.2f})" if price.confidence_score is not None else ""
        lines.append(f"Confidence: <code>{confidence}{score}</code>")
    if price.source_count is not None:
        lines.append(f"Evidence sources: <b>{price.source_count}</b>")
    if price.price_updated_at is not None:
        lines.append(
            f"Price updated: <code>{escape(price.price_updated_at.isoformat())}</code>"
        )
    elif price.fmv_usd is not None:
        lines.append("Freshness: <code>unverified</code>")

    if price.status == "search_only":
        lines.append("No exact indexed card was found yet. The button opens the Renaiss referral link.")
    elif price.status == "candidate":
        lines.append("This is a candidate match. Set and card number may need manual review.")
    elif price.status == "api_error":
        lines.append("The Index API did not respond in time. The button falls back to the referral link.")

    lines.append("")
    lines.append("Renaiss Index API is beta data. Treat Market Value as an experimental reference.")

    if update.effective_message:
        tracked_url = await build_tracked_url(
            price.referral_url,
            user_id=update.effective_user.id if update.effective_user else None,
            chat_id=update.effective_chat.id if update.effective_chat else None,
            local_card_id=card.local_card_id or None,
            source="telegram_price",
        )
        await update.effective_message.reply_text(
            "\n".join(lines),
            parse_mode="HTML",
            reply_markup=_price_keyboard(tracked_url),
        )
