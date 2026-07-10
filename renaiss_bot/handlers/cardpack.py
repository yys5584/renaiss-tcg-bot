"""Pack-opening, portfolio, and ranking commands."""

from __future__ import annotations

import asyncio
from html import escape
from io import BytesIO

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from renaiss_bot.renderers.overlay import render_overlay_card
from renaiss_bot.services.categories import resolve_category_key
from renaiss_bot.services.captions import format_pack_caption
from renaiss_bot.services.pack import open_pack
from renaiss_bot.services.pack_rules import (
    DAILY_FREE_PACKS,
    EXTRA_FREE_PACK_RP,
    MAX_BATCH_PACKS,
    PREMIUM_PACK_RP,
    plan_pack_open,
)
from renaiss_bot.services.portfolio import (
    SEASON_LABEL,
    PortfolioStats,
    get_daily_awards,
    get_portfolio_rankings,
    get_portfolio_stats,
)

_PACK_TYPE_ARGS = {"free", "normal", "standard", "premium", "bp", "paid"}
_GRADE_ORDER = ("MUR", "UR", "SAR", "SR", "AR", "RR", "R", "U", "C", "-")


def _price_keyboard(url: str | None) -> InlineKeyboardMarkup:
    rows = []
    if url:
        rows.append([InlineKeyboardButton("View on Renaiss", url=url)])
    rows.append([InlineKeyboardButton("My Portfolio", callback_data="renaiss:mycards")])
    return InlineKeyboardMarkup(rows)


def _portfolio_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("Open Pack", callback_data="renaiss:open")],
            [InlineKeyboardButton("Portfolio Rank", callback_data="renaiss:rank")],
        ]
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
    return f"${amount:,.0f}" if amount > 0 else "-"


def _grade_line(grades: list[dict]) -> str:
    counts = {str(row.get("grade") or "-"): int(row.get("count") or 0) for row in grades}
    parts = [f"{grade} {counts[grade]}" for grade in _GRADE_ORDER if counts.get(grade)]
    if not parts:
        parts = [
            f"{escape(str(row.get('grade') or '-'))} {int(row.get('count') or 0)}"
            for row in grades[:6]
        ]
    return ", ".join(parts) if parts else "-"


def _top_card_rows(rows: list[dict]) -> list[str]:
    lines = []
    for idx, row in enumerate(rows, 1):
        name = escape(str(row.get("card_name") or "-"))
        grade = escape(str(row.get("grade") or "-"))
        category = escape(str(row.get("category") or "-"))
        quantity = int(row.get("quantity") or 0)
        price = _format_money(row.get("market_price_usd"))
        quantity_text = f" x{quantity}" if quantity > 1 else ""
        lines.append(f"{idx}. <b>{name}</b> {grade}{quantity_text} / {price} / <code>{category}</code>")
    return lines


def _recent_card_rows(rows: list[dict]) -> list[str]:
    lines = []
    for idx, row in enumerate(rows, 1):
        name = escape(str(row.get("card_name") or "-"))
        grade = escape(str(row.get("grade") or "-"))
        category = escape(str(row.get("category") or "-"))
        quantity = int(row.get("quantity") or 0)
        quantity_text = f" x{quantity}" if quantity > 1 else ""
        lines.append(f"{idx}. <b>{name}</b> {grade}{quantity_text} / <code>{category}</code>")
    return lines


def _category_rows(rows: list[dict]) -> str:
    parts = []
    for row in rows[:4]:
        label = escape(str(row.get("category") or "-"))
        count = int(row.get("count") or 0)
        value = _format_money(row.get("value_usd"))
        parts.append(f"{label} {count} cards ({value})")
    return ", ".join(parts) if parts else "-"


def _achievement_lines(stats: PortfolioStats) -> list[str]:
    unlocked = stats.unlocked_achievements
    if not unlocked:
        return ["No achievements unlocked yet."]
    latest = sorted(unlocked, key=lambda item: item.points, reverse=True)[:5]
    return [f"- <b>{escape(item.title)}</b> +{item.points}" for item in latest]


def _change_7d_suffix(stats: PortfolioStats) -> str:
    change_usd = stats.change_7d_usd
    if change_usd is None:
        return ""
    suffix = f" (7d {change_usd:+,.2f}$"
    if stats.change_7d_pct is not None:
        suffix += f", {stats.change_7d_pct:+.1f}%"
    return suffix + ")"


def _portfolio_text(stats: PortfolioStats, rp_balance: int = 0, cash: float = 0.0) -> str:
    achievement_count = len(stats.unlocked_achievements)
    total_achievements = len(stats.achievements)
    net_worth = stats.total_value_usd + (cash or 0)
    lines = [
        "<b>Renaiss Portfolio</b>",
        "------------",
        f"Net worth: <b>{_format_money(net_worth)}</b>  (cards {_format_money(stats.total_value_usd)}{_change_7d_suffix(stats)} + cash ${cash:,.0f})",
        f"RP: <b>{rp_balance:,}</b>",
        f"Renaiss Score: <b>{stats.renaiss_score}</b>",
        (
            "Score split: "
            f"Value {stats.value_score} / Diversity {stats.diversity_score} / "
            f"Achievements {stats.achievement_score}"
        ),
        "",
        "<b>Collection</b>",
        f"Total cards: <b>{stats.total_cards}</b>",
        f"Unique cards: <b>{stats.unique_cards}</b>",
        f"Categories: <b>{stats.categories}</b> / Sets: <b>{stats.sets}</b>",
        f"Priced cards: <b>{stats.priced_cards}</b> / Unpriced: <b>{stats.unpriced_cards}</b>",
        f"Category mix: {escape(_category_rows(stats.category_counts))}",
        f"Grade mix: {escape(_grade_line(stats.grade_counts))}",
        "",
        f"<b>Achievements</b> {achievement_count}/{total_achievements}",
        *_achievement_lines(stats),
    ]

    top_rows = _top_card_rows(stats.top_cards)
    if top_rows:
        lines.extend(["", "<b>Most Valuable Cards</b>", *top_rows])

    recent_rows = _recent_card_rows(stats.recent_cards)
    if recent_rows:
        lines.extend(["", "<b>Recent Pulls</b>", *recent_rows])

    return "\n".join(lines)


async def cmd_open(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    args = list(getattr(context, "args", None) or [])
    category, pack_type, count, pay_with_rp = _parse_open_args(args)
    user_id = update.effective_user.id if update.effective_user else None

    from renaiss_bot.database.queries import count_command_free_packs_today, get_points, spend_points

    free_used, rp_balance = await asyncio.gather(
        count_command_free_packs_today(user_id),
        get_points(user_id),
    )
    plan = plan_pack_open(
        pack_type,
        count,
        free_used_today=free_used,
        rp_balance=rp_balance,
        pay_with_rp=pay_with_rp,
    )

    if plan.error is not None:
        if update.effective_message:
            if plan.error == "no_free_left":
                text = (
                    f"📦 Daily free packs used ({DAILY_FREE_PACKS}/{DAILY_FREE_PACKS}).\n"
                    f"Extra pack: <code>/open rp</code> ({EXTRA_FREE_PACK_RP} RP each) — your RP: <b>{rp_balance}</b>\n"
                    "Earn RP by joining drops (<code>f</code>) and winning the daily quiz."
                )
            else:
                needed = PREMIUM_PACK_RP if pack_type == "premium" else EXTRA_FREE_PACK_RP
                text = (
                    f"💰 Not enough RP (need {needed}, you have <b>{rp_balance}</b>).\n"
                    "Earn RP by joining drops (<code>f</code>) and winning the daily quiz."
                )
            await update.effective_message.reply_text(text, parse_mode="HTML")
        return

    if plan.rp_cost > 0:
        if not await spend_points(user_id, plan.rp_cost, source=f"open_{pack_type}"):
            if update.effective_message:
                await update.effective_message.reply_text(
                    f"💰 Not enough RP (need {plan.rp_cost}). Your balance may have just changed.",
                    parse_mode="HTML",
                )
            return

    result = await open_pack(
        user_id=user_id,
        category_key=category,
        pack_type=pack_type,
        count=plan.allowed_count,
    )
    if plan.rp_cost > 0:
        wallet_line = f"\n💰 Paid <b>{plan.rp_cost} RP</b> (balance {rp_balance - plan.rp_cost})"
    else:
        free_left = max(0, DAILY_FREE_PACKS - free_used - plan.allowed_count)
        wallet_line = f"\n📦 Free packs left today: <b>{free_left}/{DAILY_FREE_PACKS}</b>"
    caption = format_pack_caption(result) + wallet_line

    if update.effective_message:
        image_bytes = await render_overlay_card(result.best_card, result.best_price)
        if image_bytes:
            photo = BytesIO(image_bytes)
            photo.name = "renaiss_pack_result.png"
            await update.effective_message.reply_photo(
                photo=photo,
                caption=caption,
                parse_mode="HTML",
                reply_markup=_price_keyboard(result.best_price.referral_url),
            )
        else:
            await update.effective_message.reply_text(
                caption,
                parse_mode="HTML",
                reply_markup=_price_keyboard(result.best_price.referral_url),
            )

    try:
        from renaiss_bot.database.queries import log_pack_event, register_pack_cards

        chat_id = update.effective_chat.id if update.effective_chat else None
        await asyncio.gather(
            register_pack_cards(user_id=user_id, chat_id=chat_id, result=result),
            log_pack_event(
                user_id=user_id,
                chat_id=chat_id,
                category=result.category,
                best_card=result.best_card,
                price=result.best_price,
                pack_type=result.pack_type,
                pack_count=result.pack_count,
                card_count=len(result.cards),
                pool_source=result.pool_source,
                source="command",
            ),
        )
    except Exception:
        pass


async def cmd_pack(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_message:
        await update.effective_message.reply_text(
            "<b>Renaiss Packs</b>\n"
            "------------\n"
            f"- <code>/open</code>: open a free pack ({DAILY_FREE_PACKS}/day)\n"
            f"- <code>/open rp</code>: extra free pack ({EXTRA_FREE_PACK_RP} RP each)\n"
            f"- <code>/open premium</code>: premium pack ({PREMIUM_PACK_RP} RP)\n"
            f"- <code>/open 5</code>: open multiple packs at once (max {MAX_BATCH_PACKS})\n"
            "- <code>/open one_piece_tcg</code>: One Piece packs\n"
            "- <code>d</code>: call a group drop / <code>f</code>: join it\n"
            "\n"
            "Earn RP: join drops (+50), answer the daily quiz (+100).",
            parse_mode="HTML",
        )


async def cmd_mycards(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_message:
        return

    from renaiss_bot.database.queries import get_cash, get_points

    uid = update.effective_user.id if update.effective_user else None
    try:
        stats, rp_balance, cash = await asyncio.gather(
            get_portfolio_stats(uid),
            get_points(uid),
            get_cash(uid),
        )
    except Exception:
        stats, rp_balance, cash = None, 0, 0.0

    if not stats:
        await update.effective_message.reply_text(
            "<b>Renaiss Portfolio</b>\n"
            "------------\n"
            "No cards are saved yet. Open a pack with <code>/open</code> to start building your portfolio.",
            parse_mode="HTML",
            reply_markup=_portfolio_keyboard(),
        )
        return

    await update.effective_message.reply_text(
        _portfolio_text(stats, rp_balance, cash),
        parse_mode="HTML",
        reply_markup=_portfolio_keyboard(),
    )


def _daily_total_lines(rows: list[dict]) -> list[str]:
    lines = []
    for idx, row in enumerate(rows, 1):
        user_id = escape(str(row.get("user_id") or "-"))
        value = _format_money(row.get("total_value_usd"))
        lines.append(f"{idx}. <code>{user_id}</code> — <b>{value}</b>")
    return lines or ["No data yet."]


def _daily_jackpot_lines(rows: list[dict]) -> list[str]:
    lines = []
    for idx, row in enumerate(rows, 1):
        user_id = escape(str(row.get("user_id") or "-"))
        card_name = escape(str(row.get("card_name") or "-"))
        value = _format_money(row.get("fmv_usd"))
        lines.append(f"{idx}. <code>{user_id}</code> — <b>{card_name}</b> ({value})")
    return lines or ["No pulls yet today."]


def _daily_return_lines(rows: list[dict]) -> list[str]:
    lines = []
    for idx, row in enumerate(rows, 1):
        user_id = escape(str(row.get("user_id") or "-"))
        pct = float(row.get("pct") or 0)
        lines.append(f"{idx}. <code>{user_id}</code> — <b>{pct:+.1f}%</b>")
    return lines or ["Not enough history yet."]


async def cmd_rank(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_message:
        return

    try:
        season_rows = await get_portfolio_rankings(limit=10)
        awards = await get_daily_awards(limit=3)
    except Exception:
        season_rows, awards = [], {"total": [], "jackpot": [], "return": []}

    if not season_rows:
        await update.effective_message.reply_text(
            "<b>Renaiss Portfolio Rank</b>\n"
            "------------\n"
            "No ranked portfolios yet.",
            parse_mode="HTML",
        )
        return

    lines = [
        "<b>🏆 Today's Awards</b>",
        "------------",
        "💰 <b>Total King</b>",
        *_daily_total_lines(awards["total"]),
        "",
        "💎 <b>Jackpot King</b> (biggest single pull today)",
        *_daily_jackpot_lines(awards["jackpot"]),
        "",
        "📈 <b>Return King</b> (vs yesterday)",
        *_daily_return_lines(awards["return"]),
        "",
        f"<b>{escape(SEASON_LABEL)} Total Rank</b>",
        "------------",
    ]
    for idx, row in enumerate(season_rows, 1):
        user_id = escape(str(row.get("user_id") or "-"))
        value = _format_money(row.get("total_value_usd"))
        unique_cards = int(row.get("unique_cards") or 0)
        score = int(row.get("renaiss_score") or 0)
        achievement_count = int(row.get("achievement_count") or 0)
        lines.append(
            f"{idx}. <code>{user_id}</code> / <b>{value}</b> / "
            f"{unique_cards} unique / score {score} / achievements {achievement_count}"
        )

    await update.effective_message.reply_text("\n".join(lines), parse_mode="HTML")
