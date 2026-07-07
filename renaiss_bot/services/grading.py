"""Grading premium guide: RAW vs graded market value comparison."""

from __future__ import annotations

import asyncio
import logging

from renaiss_bot.services.client import fetch_grade_offers
from renaiss_bot.services.models import CardIdentity, GradeOffer, GradingPremium

logger = logging.getLogger(__name__)

_RAW_MARKERS = ("raw", "ungraded", "near mint", "nm", "lp", "mp", "hp")


def is_raw_offer(offer: GradeOffer) -> bool:
    label = (offer.grade_label or "").strip().lower()
    company = (offer.grading_company or "").strip().lower()
    if not label and not company:
        return True
    if company in {"", "raw", "none"} and (not label or any(marker in label for marker in _RAW_MARKERS)):
        return True
    return False


def compute_grading_premium(offers: list[GradeOffer]) -> GradingPremium | None:
    raw_offers = [offer for offer in offers if is_raw_offer(offer)]
    graded_offers = [offer for offer in offers if not is_raw_offer(offer)]
    if not raw_offers or not graded_offers:
        return None

    raw_usd = max(offer.fmv_usd for offer in raw_offers)
    top = max(graded_offers, key=lambda offer: offer.fmv_usd)
    if raw_usd <= 0 or top.fmv_usd <= raw_usd:
        return None

    label_parts = [part for part in (top.grading_company, top.grade_label) if part]
    graded_label = " ".join(label_parts) if label_parts else "Graded"
    return GradingPremium(raw_usd=raw_usd, graded_label=graded_label, graded_usd=top.fmv_usd)


def format_grading_premium(premium: GradingPremium) -> str:
    return (
        f"RAW ${premium.raw_usd:,.2f} → {premium.graded_label} "
        f"${premium.graded_usd:,.2f} ({premium.multiplier:.1f}x grading premium)"
    )


async def fetch_grading_premium(
    card: CardIdentity,
    *,
    timeout_seconds: float = 2.5,
) -> GradingPremium | None:
    try:
        offers = await asyncio.wait_for(
            fetch_grade_offers(card, timeout_seconds=timeout_seconds),
            timeout=timeout_seconds + 0.5,
        )
    except Exception as exc:
        logger.debug("Renaiss grade offers skipped: %s", exc)
        return None
    return compute_grading_premium(offers)
