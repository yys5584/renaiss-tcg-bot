"""Flex card: turn a user's best pull into a shareable brag with a luck story."""

from __future__ import annotations

from html import escape

from renaiss_bot.services.features import pack_economy_enabled

# 자랑 리액션 보상 (초안 — 운영 보며 조절)
PROP_REWARD_FLEXER = 20     # props 1개당 자랑한 사람 RP
PROP_REWARD_CAP = 10        # 한 자랑당 최대 집계 props 수 (200 RP 상한)
PROP_REWARD_TAPPER = 10     # props 누른 사람 RP (자랑당 1회)


def build_flex_caption(
    *,
    display_name: str,
    card_name: str,
    grade: str,
    market_usd,
) -> str:
    lines = [
        f"🎉 <b>{escape(display_name)}</b> is flexing:",
        f"<b>{escape(card_name)}</b> · {escape(grade or '-')}",
    ]
    # Collection rows do not preserve exact/fresh/source provenance. Flex the
    # card itself, never acquisition odds or a legacy dollar value that cannot
    # be verified.
    lines.append("")
    if pack_economy_enabled():
        lines.append("👏 Tap <b>Props</b> below to cheer — both of you earn RP.")
    else:
        lines.append("👏 Tap <b>Props</b> below to cheer this pull.")
    lines.append("In-game collectible only · no physical card or NFT ownership.")
    return "\n".join(lines)
