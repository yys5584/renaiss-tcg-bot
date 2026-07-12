"""User-facing text builders for Renaiss pack results."""

from __future__ import annotations

from collections import Counter
from html import escape

from renaiss_bot.services.market import VERIFIED_PRICE_SOURCES
from renaiss_bot.services.models import PackOpenResult, RenaissPrice


def _price_line(price: RenaissPrice) -> str:
    if price.fmv_usd is not None:
        if price.status == "exact" and price.source in VERIFIED_PRICE_SOURCES:
            line = f"Renaiss reference FMV: <b>${price.fmv_usd:,.2f}</b>"
        else:
            line = f"Candidate reference value: <b>${price.fmv_usd:,.2f}</b> · unverified"
        if price.change_7d_pct is not None:
            line += f" / 7d <b>{price.change_7d_pct:+.1f}%</b>"
        return line
    if price.status == "candidate":
        return "Market Value: candidate match, needs review"
    if price.status == "search_only":
        return "Market Value: no exact match yet"
    if price.status == "missing":
        return "Market Value: not indexed yet"
    return "Market Value: temporarily unavailable"


def _grade_summary(result: PackOpenResult) -> str:
    counts = Counter(card.grade for card in result.cards)
    order = ("MUR", "UR", "SAR", "SR", "AR", "RR", "R", "U", "C")
    parts = [f"{grade} {counts[grade]}" for grade in order if counts.get(grade)]
    return ", ".join(parts) if parts else "-"


def format_pack_caption(result: PackOpenResult) -> str:
    best = result.best_card
    total_cards = len(result.cards)
    pack_label = "Premium Pack" if result.pack_type == "premium" else "Standard Pack"
    lines = [
        "<b>Renaiss Collaboration Pack Opened</b>",
        "------------",
        f"Pack: <b>{pack_label}</b> x{result.pack_count} / Cards {total_cards}",
        f"Best pull: <b>{escape(best.card_name)}</b> {escape(best.grade)}",
        f"Set: {escape(best.set_code or '-')} {escape(best.collector_number or '')}",
        _price_line(result.best_price),
        f"Grade: <b>{escape(result.best_price.grade_label or best.grade or '-')}</b>",
        f"Grader: <code>{escape(result.best_price.grading_company or '-')}</code>",
        f"Rarity summary: {escape(_grade_summary(result))}",
        f"Card pool: <code>{escape(result.pool_source)}</code>",
        f"Data source: <code>{escape(result.best_price.source)}</code>",
        "",
        "All displayed values are experimental references, not investment advice or scored results.",
        "In-game collectible only · no physical card or NFT ownership.",
    ]
    return "\n".join(lines)
