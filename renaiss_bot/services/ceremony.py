"""Nightly ranking ceremony window (22:00 KST).

While the ceremony is active the official group goes read-only for bot
interactions so the ranking announcement keeps the stage; spawns are already
silenced separately by the spawn quiet hours.
"""

from __future__ import annotations

import os
from datetime import datetime
from zoneinfo import ZoneInfo

KST = ZoneInfo("Asia/Seoul")
CEREMONY_START_HOUR_KST = 22


def announcements_enabled() -> bool:
    return os.getenv("RENAISS_RANKING_ANNOUNCE_ENABLED", "1").strip().lower() not in {
        "0",
        "false",
        "no",
    }


def ceremony_minutes() -> int:
    try:
        value = int(os.getenv("RENAISS_CEREMONY_MINUTES", "15"))
    except ValueError:
        return 15
    return max(1, min(60, value))


def ceremony_active(now: datetime | None = None) -> bool:
    """True inside the daily 22:00 KST announcement window."""
    if not announcements_enabled():
        return False
    current = (now or datetime.now(KST)).astimezone(KST)
    if current.hour != CEREMONY_START_HOUR_KST:
        return False
    return current.minute < ceremony_minutes()
