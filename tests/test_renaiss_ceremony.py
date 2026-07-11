"""Ranking ceremony window and group interaction gate tests."""

from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from telegram.ext import ApplicationHandlerStop

from renaiss_bot.database.queries import ranking_period_bounds
from renaiss_bot.handlers.ceremony import ceremony_gate
from renaiss_bot.services.ceremony import KST, ceremony_active


def _kst(*args) -> datetime:
    return datetime(*args, tzinfo=KST)


def test_ceremony_window_covers_start_and_respects_minutes(monkeypatch):
    monkeypatch.delenv("RENAISS_RANKING_ANNOUNCE_ENABLED", raising=False)
    monkeypatch.setenv("RENAISS_CEREMONY_MINUTES", "15")
    assert ceremony_active(_kst(2026, 7, 12, 22, 0))
    assert ceremony_active(_kst(2026, 7, 12, 22, 14))
    assert not ceremony_active(_kst(2026, 7, 12, 22, 15))
    assert not ceremony_active(_kst(2026, 7, 12, 21, 59))
    assert not ceremony_active(_kst(2026, 7, 12, 23, 0))


def test_ceremony_disabled_with_announcements(monkeypatch):
    monkeypatch.setenv("RENAISS_RANKING_ANNOUNCE_ENABLED", "0")
    assert not ceremony_active(_kst(2026, 7, 12, 22, 5))


async def test_gate_blocks_group_updates_during_ceremony(monkeypatch):
    monkeypatch.setattr("renaiss_bot.handlers.ceremony.ceremony_active", lambda: True)
    update = SimpleNamespace(
        effective_chat=SimpleNamespace(type="supergroup"),
        callback_query=None,
    )
    with pytest.raises(ApplicationHandlerStop):
        await ceremony_gate(update, SimpleNamespace())


async def test_gate_answers_and_blocks_group_buttons(monkeypatch):
    monkeypatch.setattr("renaiss_bot.handlers.ceremony.ceremony_active", lambda: True)
    query = SimpleNamespace(answer=AsyncMock())
    update = SimpleNamespace(
        effective_chat=SimpleNamespace(type="supergroup"),
        callback_query=query,
    )
    with pytest.raises(ApplicationHandlerStop):
        await ceremony_gate(update, SimpleNamespace())
    query.answer.assert_awaited_once()


async def test_gate_lets_private_chats_through(monkeypatch):
    monkeypatch.setattr("renaiss_bot.handlers.ceremony.ceremony_active", lambda: True)
    update = SimpleNamespace(
        effective_chat=SimpleNamespace(type="private"),
        callback_query=None,
    )
    await ceremony_gate(update, SimpleNamespace())  # no exception


async def test_gate_is_transparent_outside_ceremony(monkeypatch):
    monkeypatch.setattr("renaiss_bot.handlers.ceremony.ceremony_active", lambda: False)
    update = SimpleNamespace(
        effective_chat=SimpleNamespace(type="supergroup"),
        callback_query=None,
    )
    await ceremony_gate(update, SimpleNamespace())  # no exception


def test_ranking_period_bounds_kst_math():
    now = _kst(2026, 7, 13, 22, 0)  # Monday 22:00 KST
    day_start, day_end = ranking_period_bounds("day", now=now)
    assert day_start == _kst(2026, 7, 13, 0, 0) and day_end is None

    week_start, week_end = ranking_period_bounds("week", now=now)
    assert week_start == _kst(2026, 7, 13, 0, 0) and week_end is None

    last_start, last_end = ranking_period_bounds("last_week", now=now)
    assert last_start == _kst(2026, 7, 6, 0, 0)
    assert last_end == _kst(2026, 7, 13, 0, 0)

    with pytest.raises(ValueError):
        ranking_period_bounds("month", now=now)
