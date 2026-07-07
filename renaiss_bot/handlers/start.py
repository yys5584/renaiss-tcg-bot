"""Start and category commands."""

from __future__ import annotations

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from renaiss_bot.services.categories import list_categories


def _home_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("Pokemon Pack", callback_data="renaiss:open:pokemon_tcg"),
                InlineKeyboardButton("One Piece Pack", callback_data="renaiss:open:one_piece_tcg"),
            ],
            [
                InlineKeyboardButton("My Portfolio", callback_data="renaiss:mycards"),
                InlineKeyboardButton("Portfolio Rank", callback_data="renaiss:rank"),
            ],
            [
                InlineKeyboardButton("Supported Sets", callback_data="renaiss:sets"),
                InlineKeyboardButton("Renaiss", url="https://www.renaiss.xyz/ref/moonyu"),
            ],
        ]
    )


def _sets_keyboard() -> InlineKeyboardMarkup:
    rows = []
    for category in list_categories():
        if category.enabled:
            rows.append([InlineKeyboardButton(f"Open {category.label}", callback_data=f"renaiss:open:{category.key}")])
    return InlineKeyboardMarkup(rows)


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = (
        "<b>TGPOKE Renaiss Edition</b>\n"
        "------------\n"
        "Open TCG packs in Telegram, collect valuable cards, and track your portfolio with Renaiss Market Value.\n\n"
        "<b>Core Loop</b>\n"
        "- Build portfolio value with expensive cards\n"
        "- Increase diversity with more unique cards, sets, and categories\n"
        "- Unlock achievements automatically as your collection grows\n\n"
        "<b>Commands</b>\n"
        "- /open: open 1 Pokemon TCG pack\n"
        "- /open premium: open 1 premium Pokemon TCG pack\n"
        "- /open one_piece_tcg 10: open 10 One Piece packs\n"
        "- /mycards: view your Renaiss portfolio\n"
        "- /rank: view portfolio ranking\n"
        "- /price Charizard: check Market Value\n"
        "- d / f: call and join group drops"
    )
    if update.effective_message:
        await update.effective_message.reply_text(text, parse_mode="HTML", reply_markup=_home_keyboard())


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
