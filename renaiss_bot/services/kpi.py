"""KPI hooks for standalone Renaiss bot events."""

from __future__ import annotations

import logging

from renaiss_bot.services.models import CardIdentity, RenaissPrice

logger = logging.getLogger(__name__)


async def log_price_impression(
    *,
    user_id: int | None,
    chat_id: int | None,
    card: CardIdentity,
    price: RenaissPrice,
    source: str,
) -> None:
    logger.info(
        "renaiss price impression user=%s chat=%s card=%s status=%s source=%s",
        user_id,
        chat_id,
        card.local_card_id or card.card_name,
        price.status,
        source,
    )

