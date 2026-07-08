"""Flex card: turn a user's best pull into a shareable brag with a luck story."""

from __future__ import annotations

from html import escape

from renaiss_bot.services.pack_rules import FREE_PACK_LUCKY_PROBS

# 자랑 리액션 보상 (초안 — 운영 보며 조절)
PROP_REWARD_FLEXER = 20     # props 1개당 자랑한 사람 RP
PROP_REWARD_CAP = 10        # 한 자랑당 최대 집계 props 수 (200 RP 상한)
PROP_REWARD_TAPPER = 10     # props 누른 사람 RP (자랑당 1회)


def grade_rarity_phrase(grade: str) -> str | None:
    """등급의 럭키슬롯 확률 → '1-in-N pull' 문구. 흔한 등급은 None (스토리 생략)."""
    prob = FREE_PACK_LUCKY_PROBS.get((grade or "").strip().upper())
    if not prob or prob <= 0:
        return None
    one_in = round(1 / prob)
    if one_in < 20:
        return None  # 너무 흔하면 자랑거리 아님
    return f"roughly a 1-in-{one_in:,} pull"


def _format_money(value) -> str:
    try:
        amount = float(value or 0)
    except (TypeError, ValueError):
        amount = 0.0
    if amount <= 0:
        return "-"
    if amount < 10:
        return f"${amount:,.2f}"
    return f"${amount:,.0f}"


def build_flex_caption(
    *,
    display_name: str,
    card_name: str,
    grade: str,
    market_usd,
    portfolio_usd,
    change_7d_pct=None,
) -> str:
    lines = [
        f"🎉 <b>{escape(display_name)}</b> is flexing:",
        f"<b>{escape(card_name)}</b> · {escape(grade or '-')}",
    ]
    rarity = grade_rarity_phrase(grade)
    if rarity:
        lines.append(f"🎲 {rarity}")
    if market_usd and float(market_usd) > 0:
        lines.append(f"💰 Market value: <b>{_format_money(market_usd)}</b>")
    if portfolio_usd and float(portfolio_usd) > 0:
        tail = f" ({change_7d_pct:+.1f}% 7d)" if change_7d_pct is not None else ""
        lines.append(f"📦 Top card in a {_format_money(portfolio_usd)} collection{tail}")
    lines.append("")
    lines.append("👏 Tap <b>Props</b> below to cheer — both of you earn RP.")
    return "\n".join(lines)
