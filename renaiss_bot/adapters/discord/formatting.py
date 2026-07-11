"""Discord text formatting helpers."""

from __future__ import annotations

from collections import Counter

from renaiss_bot.services.market import VERIFIED_PRICE_SOURCES
from renaiss_bot.services.models import PackOpenResult, RenaissPrice

_GRADE_ORDER = ("MUR", "UR", "SAR", "SR", "AR", "RR", "R", "U", "C")
_STATUS_LABELS = {
    "exact": "exact match",
    "candidate": "candidate match",
    "search_only": "search link",
    "missing": "price unavailable",
    "api_error": "API error",
}


def _verified_reference(price: RenaissPrice) -> bool:
    return (
        price.status == "exact"
        and price.source in VERIFIED_PRICE_SOURCES
        and price.price_updated_at is not None
    )


def _price_line(price: RenaissPrice) -> str:
    if price.fmv_usd is not None:
        label = (
            "Renaiss reference FMV"
            if _verified_reference(price)
            else "Candidate reference FMV (unverified)"
        )
        line = f"{label}: **${price.fmv_usd:,.0f}**"
        if price.change_7d_pct is not None:
            line += f" / 7d {price.change_7d_pct:+.1f}%"
        return line
    if price.status == "candidate":
        return "Renaiss candidate price — verification required"
    if price.status == "search_only":
        return "Renaiss search link available"
    if price.status == "api_error":
        return "Renaiss API is temporarily unavailable"
    return "Renaiss price pending"


def _grade_summary(result: PackOpenResult) -> str:
    counts = Counter(card.grade for card in result.cards)
    parts = [f"{grade} {counts[grade]}" for grade in _GRADE_ORDER if counts.get(grade)]
    return ", ".join(parts) if parts else "-"


def format_pack_message(result: PackOpenResult) -> str:
    best = result.best_card
    pack_label = "Premium Pack" if result.pack_type == "premium" else "Standard Pack"
    return "\n".join(
        [
            "**Renaiss Collaboration pack opened**",
            "────────────",
            f"Pack: **{pack_label}** x{result.pack_count} / {len(result.cards)} cards",
            f"Top card: **{best.card_name}** {best.grade}",
            f"Set: {best.set_code or '-'} {best.collector_number or ''}".rstrip(),
            _price_line(result.best_price),
            f"Grade summary: {_grade_summary(result)}",
            f"Card pool: `{result.pool_source}`",
            "Collection-only experimental reference data; not investment advice.",
            "In-game collectible only; no physical card or NFT ownership.",
        ]
    )


def format_price_message(card_name: str, category: str, price: RenaissPrice) -> str:
    lines = [
        f"**{card_name}**",
        "────────────",
        f"Category: `{category}`",
        f"Match status: `{_STATUS_LABELS.get(price.status, price.status)}`",
    ]
    if price.fmv_usd is not None:
        label = (
            "Renaiss reference FMV"
            if _verified_reference(price)
            else "Candidate reference FMV (unverified)"
        )
        lines.append(f"{label}: **${price.fmv_usd:,.0f}**")
    if price.change_7d_pct is not None:
        lines.append(f"7-day change: **{price.change_7d_pct:+.1f}%**")
    if price.price_range_min_usd is not None and price.price_range_max_usd is not None:
        lines.append(f"Candidate range: ${price.price_range_min_usd:,.0f} - ${price.price_range_max_usd:,.0f}")
    lines.append(f"Source: `{price.source}`")
    if price.confidence:
        lines.append(f"Confidence: `{price.confidence}`")
    if price.price_updated_at is not None:
        lines.append(f"Price updated: `{price.price_updated_at.isoformat()}`")
    elif price.fmv_usd is not None:
        lines.append("Freshness: `unverified`")
    lines.append("Experimental reference data; not investment advice.")
    return "\n".join(lines)


def format_collection_message(detail: dict | None) -> str:
    if not detail:
        return (
            "No cards saved yet. Join a blind community spawn with `c` in Telegram.\n"
            "In-game collection only; no physical card or NFT ownership."
        )

    lines = [
        "**My Renaiss Collection**",
        "────────────",
        "In-game collection only; no physical card or NFT ownership.",
        "",
        f"Unique cards: **{detail['unique_cards']}**",
        f"Total copies: **{detail['total_quantity']}**",
        f"Categories: **{detail['categories']}**",
        f"Highest stored snapshot (not live FMV): **${float(detail.get('max_market_price') or 0):,.0f}**",
    ]

    grades = detail.get("grades") or []
    if grades:
        grade_text = ", ".join(f"{row.get('grade') or '-'} {int(row.get('count') or 0)}" for row in grades)
        lines.append(f"Grade mix: {grade_text}")

    top_cards = detail.get("top_cards") or []
    if top_cards:
        lines.extend(["", "**Top cards**"])
        for idx, row in enumerate(top_cards, 1):
            price = float(row.get("market_price_usd") or 0)
            price_text = f" / ${price:,.0f}" if price > 0 else ""
            quantity = int(row.get("quantity") or 0)
            qty_text = f" x{quantity}" if quantity > 1 else ""
            lines.append(f"{idx}. **{row.get('card_name') or '-'}** {row.get('grade') or '-'}{qty_text}{price_text}")

    recent_cards = detail.get("recent_cards") or []
    if recent_cards:
        lines.extend(["", "**Recent catches**"])
        for idx, row in enumerate(recent_cards, 1):
            quantity = int(row.get("quantity") or 0)
            qty_text = f" x{quantity}" if quantity > 1 else ""
            lines.append(f"{idx}. **{row.get('card_name') or '-'}** {row.get('grade') or '-'}{qty_text}")

    return "\n".join(lines)
