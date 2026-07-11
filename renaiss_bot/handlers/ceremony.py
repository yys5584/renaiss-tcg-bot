"""Group-wide interaction gate for the nightly ranking ceremony."""

from __future__ import annotations

import logging

from telegram import Update
from telegram.ext import ApplicationHandlerStop, ContextTypes

from renaiss_bot.services.ceremony import ceremony_active

logger = logging.getLogger(__name__)

_CEREMONY_TOAST = "🏆 Ranking ceremony in progress — the room reopens in a few minutes!"


async def ceremony_gate(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Silence group commands, catches, and buttons while rankings post.

    Private chats stay fully interactive; only group-facing handlers are
    stopped so the announcement is the sole bot activity in the room.
    """
    if not ceremony_active():
        return
    chat = update.effective_chat
    if chat is None or chat.type == "private":
        return
    if update.callback_query is not None:
        try:
            await update.callback_query.answer(_CEREMONY_TOAST)
        except Exception:  # noqa: BLE001 - the stop below is what matters
            logger.debug("Ceremony toast failed", exc_info=True)
        raise ApplicationHandlerStop
    raise ApplicationHandlerStop
