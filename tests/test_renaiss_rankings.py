"""Daily/weekly catch ranking announcement tests (DB-free)."""

from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

from renaiss_bot import jobs
from renaiss_bot.jobs import announce_daily_ranking_job, build_ranking_message


def _ranking(rows=None, best=None):
    rows = rows if rows is not None else []
    return {
        "period": "day",
        "rows": rows,
        "best_catch": best,
        "total_catches": sum(int(row.get("catches") or 0) for row in rows),
    }


def test_build_ranking_message_medals_and_escaping():
    ranking = _ranking(
        rows=[
            {"rank": 1, "winner_name": "<b>hax</b>", "catches": 4},
            {"rank": 2, "winner_name": "Mina", "catches": 2},
            {"rank": 3, "winner_name": "Leo", "catches": 1},
            {"rank": 4, "winner_name": "Quiet", "catches": 1},
        ],
        best={"card_name": "Charizard ex", "winner_name": "Mina", "fmv_usd": 1250.0},
    )
    text = build_ranking_message(ranking, title="Daily Catch Ranking", footer="note")
    assert "🥇 <b>&lt;b&gt;hax&lt;/b&gt;</b> — 4 catches" in text
    assert "🥈" in text and "🥉" in text
    assert " 4. <b>Quiet</b> — 1 catch" in text
    assert "🎣 Top catch: <b>Charizard ex</b> · $1,250 · Mina" in text
    assert text.endswith("<i>note</i>")
    assert "<b>hax</b>" not in text


def test_build_ranking_message_singular_catch_and_no_best():
    ranking = _ranking(rows=[{"rank": 1, "winner_name": "Solo", "catches": 1}])
    text = build_ranking_message(ranking, title="Daily")
    assert "1 catch" in text and "1 catches" not in text
    assert "Top catch" not in text


async def test_announce_skips_when_disabled(monkeypatch):
    monkeypatch.setenv("RENAISS_RANKING_ANNOUNCE_ENABLED", "0")
    bot = SimpleNamespace(send_message=AsyncMock())
    await announce_daily_ranking_job(SimpleNamespace(bot=bot))
    bot.send_message.assert_not_awaited()


async def test_announce_skips_without_official_chat(monkeypatch):
    monkeypatch.delenv("RENAISS_RANKING_ANNOUNCE_ENABLED", raising=False)
    monkeypatch.setattr(jobs, "official_chat_id", lambda: None)
    bot = SimpleNamespace(send_message=AsyncMock())
    await announce_daily_ranking_job(SimpleNamespace(bot=bot))
    bot.send_message.assert_not_awaited()


class _FrozenDatetime(datetime):
    _now = datetime(2026, 7, 8, 22, 0)  # Wednesday

    @classmethod
    def now(cls, tz=None):
        return cls._now.replace(tzinfo=tz) if tz else cls._now


async def test_announce_posts_once_per_day_via_event_key(monkeypatch):
    monkeypatch.delenv("RENAISS_RANKING_ANNOUNCE_ENABLED", raising=False)
    monkeypatch.setattr(jobs, "official_chat_id", lambda: -1001)
    monkeypatch.setattr(jobs, "datetime", _FrozenDatetime)
    ranking = _ranking(rows=[{"rank": 1, "winner_name": "Mina", "catches": 3}])
    monkeypatch.setattr(jobs, "get_catch_ranking", AsyncMock(return_value=ranking))
    log_event = AsyncMock(return_value=True)
    monkeypatch.setattr(jobs, "log_event", log_event)
    bot = SimpleNamespace(send_message=AsyncMock())

    await announce_daily_ranking_job(SimpleNamespace(bot=bot))

    bot.send_message.assert_awaited_once()
    args, kwargs = bot.send_message.await_args
    assert args[0] == -1001
    assert "Daily Catch Ranking" in args[1]
    assert log_event.await_args.kwargs["event_key"] == "daily-rank:-1001:2026-07-08"

    # Second run the same day: the event key is already claimed.
    log_event.return_value = False
    await announce_daily_ranking_job(SimpleNamespace(bot=bot))
    bot.send_message.assert_awaited_once()


class _FrozenSunday(_FrozenDatetime):
    _now = datetime(2026, 7, 12, 22, 0)  # Sunday


async def test_announce_adds_weekly_final_on_sunday(monkeypatch):
    monkeypatch.delenv("RENAISS_RANKING_ANNOUNCE_ENABLED", raising=False)
    monkeypatch.setattr(jobs, "official_chat_id", lambda: -1001)
    monkeypatch.setattr(jobs, "datetime", _FrozenSunday)
    daily = _ranking(rows=[{"rank": 1, "winner_name": "Mina", "catches": 3}])
    weekly = _ranking(rows=[{"rank": 1, "winner_name": "Leo", "catches": 9}])
    monkeypatch.setattr(
        jobs,
        "get_catch_ranking",
        AsyncMock(side_effect=[daily, weekly]),
    )
    monkeypatch.setattr(jobs, "log_event", AsyncMock(return_value=True))
    bot = SimpleNamespace(send_message=AsyncMock())

    await announce_daily_ranking_job(SimpleNamespace(bot=bot))

    assert bot.send_message.await_count == 2
    weekly_text = bot.send_message.await_args_list[1].args[1]
    assert "Weekly Final" in weekly_text
    assert "Leo" in weekly_text
