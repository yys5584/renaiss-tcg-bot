"""TGPoke premium custom emoji, ported for the Renaiss bot.

Every helper returns Telegram HTML; callers must send with parse_mode="HTML".
Unknown keys and unregistered ids fall back to plain unicode so messages stay
readable for clients that cannot render custom emoji.
"""

from __future__ import annotations

# TGPoke Season 2 card-grade color bars. Keys match the internal catalog
# grades, so the same bar identifies a tier in both products.
GRADE_BAR_CUSTOM_EMOJI = {
    "C": "6123053802657423440",
    "U": "6123032778792509840",
    "R": "6123120864276782816",
    "RR": "6122856040888278920",
    "AR": "6122820113486846917",
    "SR": "6123133259552399954",
    "SAR": "6120489926225043826",
    "UR": "6120628056668249993",
    "MUR": "6120416224586243350",
}
GRADE_BAR_FALLBACK = {
    "C": "◼️",
    "U": "🟩",
    "R": "🟦",
    "RR": "🟪",
    "AR": "🟧",
    "SR": "🟨",
    "SAR": "💠",
    "UR": "🟥",
    "MUR": "👑",
}

ICON_CUSTOM_EMOJI = {
    "gotcha": "6120697360260537438",
    "check": "6123100982873169029",
    "coin": "6120606199579681344",
    "bolt": "6123139525909683295",
    "game": "6120527034742480025",
    "crystal": "6122963758668062684",
}
ICON_FALLBACK = {
    "gotcha": "🏆",
    "check": "✅",
    "coin": "💵",
    "bolt": "⚡",
    "game": "🎮",
    "crystal": "💎",
}


def _tag(emoji_id: str, fallback: str) -> str:
    if emoji_id:
        return f'<tg-emoji emoji-id="{emoji_id}">{fallback}</tg-emoji>'
    return fallback


def grade_bar(grade: str | None) -> str:
    """Colored tier bar for a known internal grade, or empty string."""
    key = str(grade or "").strip().upper()
    if key not in GRADE_BAR_FALLBACK:
        return ""
    return _tag(GRADE_BAR_CUSTOM_EMOJI.get(key, ""), GRADE_BAR_FALLBACK[key])


def icon(name: str) -> str:
    return _tag(ICON_CUSTOM_EMOJI.get(name, ""), ICON_FALLBACK.get(name, "•"))
