"""Privacy, delivery-state, and job tests for the public Daily Pick bell."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from telegram.error import RetryAfter, TimedOut

from renaiss_bot.database.market_queries import (
    claim_daily_pick_result_bell,
    get_latest_complete_result_cohort,
)
from renaiss_bot.jobs import publish_daily_pick_result_bell_job
from renaiss_bot.services.result_bell import build_daily_pick_result_bell


def _cohort(*, crowd_support: int = 4, other_support: int = 2) -> dict:
    total = crowd_support + other_support
    return {
        "pick_date": date(2026, 7, 10),
        "participant_count": total,
        "cards": [
            {
                "pick_date": date(2026, 7, 10),
                "board_card_id": 7,
                "card_name": "Charizard ex",
                "grade": "RAW",
                "support_count": crowd_support,
                "median_move_pct": 5.25,
                "participant_count": total,
                "price_source": "renaiss-index-api",
                "min_confidence_score": 0.91,
                "min_source_count": 3,
                "latest_price_updated_at": datetime(2026, 7, 11, 1, tzinfo=timezone.utc),
                "asset_url": "https://index.renaissos.com/cards/charizard-ex",
                "user_id": 123456,
                "username": "secret_trainer",
            },
            {
                "pick_date": date(2026, 7, 10),
                "board_card_id": 8,
                "card_name": "Pikachu",
                "grade": "RAW",
                "support_count": other_support,
                "median_move_pct": 7.5,
                "participant_count": total,
                "price_source": "renaiss-index-api",
                "min_confidence_score": 0.9,
                "min_source_count": 3,
                "latest_price_updated_at": datetime(2026, 7, 11, 2, tzinfo=timezone.utc),
                "asset_url": "https://index.renaissos.com/cards/pikachu",
            },
        ],
    }


def test_result_bell_publishes_only_privacy_safe_crowd_aggregate():
    text, metrics, reason = build_daily_pick_result_bell(_cohort())

    assert reason is None and text is not None
    assert "Crowd Pick" in text
    assert "Charizard ex" in text
    assert "4 picks (67%)" in text
    assert "median 24h reference change <b>+5.25%</b>" in text
    assert "renaiss-index-api" in text
    assert "confidence ≥ 0.91" in text
    assert "evidence ≥ 3 sources" in text
    assert "Renaiss card record" in text
    assert "user_id" not in text and "username" not in text
    assert "123456" not in text and "secret_trainer" not in text
    assert metrics["crowd_pick"]["support_count"] == 4


def test_result_bell_never_links_an_unapproved_https_host():
    cohort = _cohort()
    cohort["cards"][0]["asset_url"] = "https://example.com/fake-renaiss-record"

    text, _, reason = build_daily_pick_result_bell(cohort)

    assert reason is None and text is not None
    assert "example.com" not in text
    assert "Renaiss card record" not in text


def test_result_bell_uses_privacy_limited_public_fallback_for_small_or_tied_cohorts():
    small_text, _, small_reason = build_daily_pick_result_bell(
        _cohort(crowd_support=2, other_support=2)
    )
    tied_text, _, tied_reason = build_daily_pick_result_bell(
        _cohort(crowd_support=3, other_support=3)
    )

    assert small_text is not None and small_reason == "fewer_than_6_participants"
    assert tied_text is not None and tied_reason == "crowd_pick_tie"
    assert "Card-level crowd stats stayed private" in small_text
    assert "Charizard" not in small_text and "4 settled" not in small_text


def test_result_bell_hides_cards_when_no_bucket_has_three_supporters():
    cohort = _cohort(crowd_support=2, other_support=2)
    cohort["participant_count"] = 6
    cohort["cards"].append(
        {
            "board_card_id": 9,
            "card_name": "Secret Mew",
            "support_count": 2,
            "median_move_pct": 1.0,
        }
    )

    text, _, reason = build_daily_pick_result_bell(cohort)

    assert text is not None and reason == "no_card_with_3_supporters"
    assert "Secret Mew" not in text and "Charizard ex" not in text


class _Context:
    def __init__(self, value):
        self.value = value

    async def __aenter__(self):
        return self.value

    async def __aexit__(self, *args):
        return None


class _Connection:
    def __init__(self, *, rows=None, row=None):
        self.fetch = AsyncMock(return_value=rows or [])
        self.fetchrow = AsyncMock(return_value=row)
        self.execute = AsyncMock(return_value="UPDATE 1")

    def transaction(self):
        return _Context(self)


class _Pool:
    def __init__(self, connection):
        self.connection = connection

    def acquire(self):
        return _Context(self.connection)


async def test_complete_cohort_query_enforces_group_and_snapshot_integrity(monkeypatch):
    connection = _Connection(rows=_cohort()["cards"])
    monkeypatch.setattr(
        "renaiss_bot.database.market_queries.get_db",
        AsyncMock(return_value=_Pool(connection)),
    )

    result = await get_latest_complete_result_cohort(
        chat_id=-1001,
        before_date=date(2026, 7, 11),
        as_of=datetime(2026, 7, 11, 22, tzinfo=timezone.utc),
    )

    assert result is not None and result["participant_count"] == 6
    sql = connection.fetch.await_args.args[0]
    assert "p.community_chat_id = $1" in sql
    assert "counts.total_count = counts.settled_count" in sql
    assert "invalid_snapshot.board_card_id <> invalid_pick.board_card_id" in sql
    assert "invalid_snapshot.pick_eligible IS NOT TRUE" in sql
    assert "invalid_snapshot.confidence_score IS NULL" in sql
    assert "invalid_snapshot.source_count IS NULL" in sql
    assert "invalid_snapshot.asset_url NOT LIKE 'https://%'" in sql
    assert "invalid_snapshot.fmv_usd::text" in sql
    assert "invalid_snapshot.price_updated_at < invalid_pick.settles_at" in sql
    assert "p.pick_date >= $4" in sql


async def test_result_bell_claim_recovers_only_pre_send_state(monkeypatch):
    connection = _Connection(row={"id": 9, "state": "claimed"})
    monkeypatch.setattr(
        "renaiss_bot.database.market_queries.get_db",
        AsyncMock(return_value=_Pool(connection)),
    )

    claimed = await claim_daily_pick_result_bell(
        chat_id=-1001,
        bell_date=date(2026, 7, 11),
        attempt_token="attempt-1",
        lease_owner="worker-1",
    )

    assert claimed is not None and claimed["id"] == 9
    recovery_sql = "\n".join(call.args[0] for call in connection.execute.await_args_list)
    assert "state = 'pending'" in recovery_sql
    assert "state = 'delivery_unknown'" in recovery_sql
    claim_sql = connection.fetchrow.await_args.args[0]
    assert "FOR UPDATE SKIP LOCKED" in claim_sql
    assert "LIMIT 1\n                FOR UPDATE SKIP LOCKED" in claim_sql
    assert "state IN ('pending', 'retryable')" in claim_sql
    assert "AND chat_id = $1" in claim_sql
    assert connection.fetchrow.await_args.args[1:] == (
        -1001,
        date(2026, 7, 11),
        "attempt-1",
        "worker-1",
    )


async def _run_bell_job(monkeypatch, *, send_side_effect=None):
    text, _, _ = build_daily_pick_result_bell(_cohort())
    bot = SimpleNamespace(
        username="renaiss_test_bot",
        send_message=AsyncMock(
            side_effect=send_side_effect,
            return_value=SimpleNamespace(message_id=77),
        ),
    )
    queue = SimpleNamespace(run_once=Mock())
    monkeypatch.setattr("renaiss_bot.jobs.daily_pick_enabled", lambda: True)
    monkeypatch.setattr("renaiss_bot.jobs._result_bell_window_open", lambda _now=None: True)
    monkeypatch.setattr("renaiss_bot.jobs.official_chat_id", lambda: -1001)
    monkeypatch.setattr(
        "renaiss_bot.jobs.get_latest_complete_result_cohort",
        AsyncMock(return_value=_cohort()),
    )
    monkeypatch.setattr(
        "renaiss_bot.jobs.enqueue_daily_pick_result_bell",
        AsyncMock(return_value={"state": "pending", "event_key": "bell:1"}),
    )
    monkeypatch.setattr(
        "renaiss_bot.jobs.claim_daily_pick_result_bell",
        AsyncMock(return_value={"id": 9}),
    )
    monkeypatch.setattr(
        "renaiss_bot.jobs.begin_daily_pick_result_bell_delivery",
        AsyncMock(
            return_value={
                "id": 9,
                "chat_id": -1001,
                "event_key": "bell:1",
                "message_text": text,
                "cohort_pick_date": date(2026, 7, 10),
            }
        ),
    )
    sent = AsyncMock(return_value=True)
    failed = AsyncMock(return_value=True)
    event = AsyncMock(return_value=True)
    monkeypatch.setattr("renaiss_bot.jobs.mark_daily_pick_result_bell_sent", sent)
    monkeypatch.setattr("renaiss_bot.jobs.mark_daily_pick_result_bell_failed", failed)
    monkeypatch.setattr("renaiss_bot.jobs.log_event", event)

    await publish_daily_pick_result_bell_job(SimpleNamespace(bot=bot, job_queue=queue))
    return bot, sent, failed, event, queue


async def test_result_bell_job_marks_success_after_telegram_delivery(monkeypatch):
    bot, sent, failed, event, queue = await _run_bell_job(monkeypatch)

    bot.send_message.assert_awaited_once()
    sent.assert_awaited_once()
    failed.assert_not_awaited()
    assert sent.await_args.kwargs["telegram_message_id"] == 77
    assert event.await_args.args[0] == "daily_pick_result_published"
    queue.run_once.assert_not_called()


async def test_result_bell_timeout_becomes_unknown_and_is_not_retried(monkeypatch):
    bot, sent, failed, event, queue = await _run_bell_job(
        monkeypatch,
        send_side_effect=TimedOut("ambiguous send"),
    )

    bot.send_message.assert_awaited_once()
    sent.assert_not_awaited()
    event.assert_not_awaited()
    assert failed.await_args.kwargs["state"] == "delivery_unknown"
    queue.run_once.assert_not_called()


async def test_result_bell_retry_after_uses_durable_retry_state(monkeypatch):
    monkeypatch.setenv("PTB_TIMEDELTA", "1")
    bot, sent, failed, event, queue = await _run_bell_job(
        monkeypatch,
        send_side_effect=RetryAfter(60),
    )

    bot.send_message.assert_awaited_once()
    sent.assert_not_awaited()
    event.assert_not_awaited()
    assert failed.await_args.kwargs["state"] == "retryable"
    assert failed.await_args.kwargs["error_code"] == "telegram_retry_after"
    assert failed.await_args.kwargs["next_attempt_at"] is not None
    queue.run_once.assert_not_called()


async def test_result_bell_job_blocks_cross_chat_delivery(monkeypatch):
    text, _, _ = build_daily_pick_result_bell(_cohort())
    bot = SimpleNamespace(username="renaiss_test_bot", send_message=AsyncMock())
    monkeypatch.setattr("renaiss_bot.jobs.daily_pick_enabled", lambda: True)
    monkeypatch.setattr("renaiss_bot.jobs._result_bell_window_open", lambda _now=None: True)
    monkeypatch.setattr("renaiss_bot.jobs.official_chat_id", lambda: -1001)
    monkeypatch.setattr(
        "renaiss_bot.jobs.get_latest_complete_result_cohort",
        AsyncMock(return_value=None),
    )
    claim = AsyncMock(return_value={"id": 9})
    monkeypatch.setattr("renaiss_bot.jobs.claim_daily_pick_result_bell", claim)
    monkeypatch.setattr(
        "renaiss_bot.jobs.begin_daily_pick_result_bell_delivery",
        AsyncMock(
            return_value={
                "id": 9,
                "chat_id": -2002,
                "event_key": "bell:other-chat",
                "message_text": text,
                "cohort_pick_date": date(2026, 7, 10),
            }
        ),
    )
    failed = AsyncMock(return_value=True)
    monkeypatch.setattr("renaiss_bot.jobs.mark_daily_pick_result_bell_failed", failed)

    await publish_daily_pick_result_bell_job(SimpleNamespace(bot=bot, job_queue=None))

    assert claim.await_args.kwargs["chat_id"] == -1001
    bot.send_message.assert_not_awaited()
    assert failed.await_args.kwargs["state"] == "dead"
    assert failed.await_args.kwargs["error_code"] == "chat_id_mismatch"


async def test_result_bell_job_limits_cohort_age_to_two_days(monkeypatch):
    bot = SimpleNamespace(username="renaiss_test_bot", send_message=AsyncMock())
    monkeypatch.setattr("renaiss_bot.jobs.daily_pick_enabled", lambda: True)
    monkeypatch.setattr("renaiss_bot.jobs._result_bell_window_open", lambda _now=None: True)
    monkeypatch.setattr("renaiss_bot.jobs.official_chat_id", lambda: -1001)
    cohort_query = AsyncMock(return_value=None)
    monkeypatch.setattr(
        "renaiss_bot.jobs.get_latest_complete_result_cohort",
        cohort_query,
    )
    monkeypatch.setattr(
        "renaiss_bot.jobs.claim_daily_pick_result_bell",
        AsyncMock(return_value=None),
    )

    await publish_daily_pick_result_bell_job(SimpleNamespace(bot=bot, job_queue=None))

    before_date = cohort_query.await_args.kwargs["before_date"]
    assert cohort_query.await_args.kwargs["oldest_date"] == before_date - timedelta(days=2)
