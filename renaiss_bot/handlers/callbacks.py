"""Callback query handlers."""

from __future__ import annotations

from telegram import Update
from telegram.ext import ContextTypes

from renaiss_bot.handlers.cardpack import cmd_mycards, cmd_open, cmd_rank
from renaiss_bot.handlers.start import cmd_sets
from renaiss_bot.services.categories import resolve_category_key


async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query:
        return
    await query.answer()
    data = query.data or ""
    if data == "renaiss:open":
        context.args = []
        await cmd_open(update, context)
    elif data.startswith("renaiss:open:"):
        category = resolve_category_key(data.removeprefix("renaiss:open:"))
        context.args = [category] if category else []
        await cmd_open(update, context)
    elif data == "renaiss:sets":
        await cmd_sets(update, context)
    elif data == "renaiss:mycards":
        await cmd_mycards(update, context)
    elif data == "renaiss:rank":
        await cmd_rank(update, context)
