"""Card matching helpers."""

from __future__ import annotations

from renaiss_bot.services.models import CardIdentity


def match_key(card: CardIdentity) -> str:
    parts = (
        card.category,
        card.card_name,
        card.set_code,
        card.collector_number,
        card.language,
        card.grade,
    )
    return "|".join(str(part or "").strip().lower() for part in parts)

