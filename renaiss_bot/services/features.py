"""Operator-controlled product feature gates."""

from __future__ import annotations

import os


def _env_enabled(name: str, *, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def private_free_packs_enabled() -> bool:
    """Keep high-volume private packs opt-in during the public-loop pilot."""
    return _env_enabled("RENAISS_PRIVATE_FREE_PACKS_ENABLED")


def pack_economy_enabled() -> bool:
    """Legacy RP purchases/rewards are retired from the Renaiss product."""
    return False


def daily_quiz_enabled() -> bool:
    """The duplicate legacy quiz is retired from the collector-market pilot."""
    return False
