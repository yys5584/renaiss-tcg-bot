"""Start and category commands."""

from __future__ import annotations

import os

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from renaiss_bot.services.categories import list_categories
from renaiss_bot.services.features import private_free_packs_enabled
from renaiss_bot.services.market import daily_pick_enabled
from renaiss_bot.services.tracking import build_tracked_url


def _home_keyboard(
    user_id: int | None = None,
    chat_id: int | None = None,
    renaiss_url: str | None = None,
    group_url: str | None = None,
) -> InlineKeyboardMarkup:
    collection_row = [InlineKeyboardButton("My Collection", callback_data="renaiss:mycards")]
    if daily_pick_enabled():
        collection_row.append(
            InlineKeyboardButton("Daily Market Pick", callback_data="renaiss:market")
        )
    rows = [collection_row]
    if group_url:
        rows.append([InlineKeyboardButton("Join the Community Game Room", url=group_url)])
    rows.append(
        [
                InlineKeyboardButton("Supported Sets", callback_data="renaiss:sets"),
                InlineKeyboardButton(
                    "Renaiss",
                    url=renaiss_url or "https://www.renaiss.xyz/ref/moonyu",
                ),
        ]
    )
    return InlineKeyboardMarkup(rows)


def _official_group_url() -> str | None:
    value = os.getenv("RENAISS_OFFICIAL_GROUP_URL", "").strip()
    if value.startswith(("https://t.me/", "https://telegram.me/")):
        return value
    return None


def _sets_keyboard() -> InlineKeyboardMarkup | None:
    if not private_free_packs_enabled():
        return None
    rows = []
    for category in list_categories():
        if category.enabled:
            rows.append([InlineKeyboardButton(f"Open {category.label}", callback_data=f"renaiss:open:{category.key}")])
    return InlineKeyboardMarkup(rows) if rows else None


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if context.args:
        payload = context.args[0].lower()
        if payload == "market":
            from renaiss_bot.handlers.market import cmd_market

            await cmd_market(update, context)
            return
        if payload == "mycards":
            from renaiss_bot.handlers.cardpack import cmd_mycards

            await cmd_mycards(update, context)
            return
        if payload == "price":
            if update.effective_message:
                await update.effective_message.reply_text(
                    "Send <code>/price Charizard</code> or "
                    "<code>/price one_piece_tcg Luffy</code> here in private chat.",
                    parse_mode="HTML",
                )
            return
        if payload == "open" or payload.startswith("open_"):
            from renaiss_bot.handlers.cardpack import cmd_open

            context.args = [payload.removeprefix("open_")] if payload != "open" else []
            await cmd_open(update, context)
            return
    market_line = (
        "- Pick one verified card and compare its next 24h reference mark\n"
        if daily_pick_enabled()
        else ""
    )
    market_command = (
        "- /market: choose today's Daily Market Pick\n"
        if daily_pick_enabled()
        else ""
    )
    pack_line = (
        "- /open: optional private pack collection\n"
        if private_free_packs_enabled()
        else ""
    )
    text = (
        "<b>Renaiss Collaboration Collector Game</b>\n"
        "------------\n"
        "Catch blind spawns together, guess the hidden price, and reveal the Renaiss reference. "
        "Your collection page is an optional extra.\n\n"
        "<b>Core Loop</b>\n"
        "- Join blind group spawns with <code>c</code>\n"
        "- Guess the hidden Renaiss reference price\n"
        f"{market_line}\n"
        "<b>Commands</b>\n"
        "- c: enter the current blind group spawn\n"
        "- /mycards: view your Renaiss collection\n"
        f"{market_command}"
        "- /price Charizard: check Market Value\n"
        f"{pack_line}\n"
        "In-game collection only · no physical card or NFT ownership.\n"
        "Community collaboration project · not the official Renaiss website or product."
    )
    if not _official_group_url():
        text += "\n\nUse <code>c</code> in the community game room where you found this bot."
    if update.effective_message:
        user_id = update.effective_user.id if update.effective_user else None
        chat_id = update.effective_chat.id if update.effective_chat else None
        renaiss_url = await build_tracked_url(
            "https://www.renaiss.xyz/ref/moonyu",
            user_id=user_id,
            chat_id=chat_id,
            local_card_id=None,
            source="telegram_home",
        )
        await update.effective_message.reply_text(
            text,
            parse_mode="HTML",
            reply_markup=_home_keyboard(
                user_id,
                chat_id,
                renaiss_url,
                _official_group_url(),
            ),
        )


async def cmd_sets(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    lines = ["<b>Supported Card Categories</b>", "------------"]
    for category in list_categories():
        state = "enabled" if category.enabled else "planned"
        lines.append(f"- <b>{category.label}</b> <code>{category.key}</code>: {state}")
        lines.append(f"  {category.description}")
    if update.effective_message:
        await update.effective_message.reply_text(
            "\n".join(lines),
            parse_mode="HTML",
            reply_markup=_sets_keyboard(),
        )
