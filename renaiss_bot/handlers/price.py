"""Price lookup command."""

from __future__ import annotations

import asyncio
from html import escape

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from renaiss_bot.services.categories import split_category_args
from renaiss_bot.services.grading import fetch_grading_premium, format_grading_premium
from renaiss_bot.services.models import CardIdentity
from renaiss_bot.services.pricing import fetch_price

_STATUS_LABELS = {
    "exact": "exact match",
    "candidate": "candidate match",
    "search_only": "search link",
    "missing": "not indexed",
    "api_error": "API error",
}


def _args_from_text(raw_text: str) -> list[str]:
    text = raw_text.strip()
    if text.startswith("/price"):
        text = text.removeprefix("/price").strip()
    return text.split()


def _price_keyboard(url: str | None) -> InlineKeyboardMarkup | None:
    if not url:
        return None
    return InlineKeyboardMarkup([[InlineKeyboardButton("View on Renaiss", url=url)]])


async def cmd_price(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
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

    card = CardIdentity(category=category, card_name=query, grade="PSA 10")
    # 시세 조회와 그레이딩 프리미엄은 서로 독립 API 호출 → 병렬
    price, premium = await asyncio.gather(
        fetch_price(card),
        fetch_grading_premium(card),
        return_exceptions=False,
    )
    lines = [
        f"<b>{escape(card.card_name)}</b>",
        "------------",
        f"Category: <code>{escape(category)}</code>",
        f"Match: <code>{escape(_STATUS_LABELS.get(price.status, price.status))}</code>",
    ]
    if price.fmv_usd is not None:
        lines.append(f"Market Value: <b>${price.fmv_usd:,.2f}</b>")
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

    if premium is not None:
        lines.append(f"Grading guide: <b>{escape(format_grading_premium(premium))}</b>")
    lines.append(f"Source: <code>{escape(price.source)}</code>")

    if price.status == "search_only":
        lines.append("No exact indexed card was found yet. The button opens the Renaiss referral link.")
    elif price.status == "candidate":
        lines.append("This is a candidate match. Set and card number may need manual review.")
    elif price.status == "api_error":
        lines.append("The Index API did not respond in time. The button falls back to the referral link.")

    lines.append("")
    lines.append("Renaiss Index API is beta data. Treat Market Value as an experimental reference.")

    if update.effective_message:
        await update.effective_message.reply_text(
            "\n".join(lines),
            parse_mode="HTML",
            reply_markup=_price_keyboard(price.referral_url),
        )
