"""Operator reconciliation tests for ambiguous Result Bell deliveries."""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from renaiss_bot.database.market_queries import (
    ResultBellReconciliationError,
    get_daily_pick_result_bell_reconciliation,
    reconcile_daily_pick_result_bell,
)
from renaiss_bot.tools.reconcile_result_bell import build_parser, run


class _Context:
    def __init__(self, value):
        self.value = value

    async def __aenter__(self):
        return self.value

    async def __aexit__(self, *args):
        return None


class _Connection:
    def __init__(self, rows):
        self.fetchrow = AsyncMock(side_effect=rows)

    def transaction(self):
        return _Context(self)


class _Pool:
    def __init__(self, connection):
        self.connection = connection

    def acquire(self):
        return _Context(self.connection)


def _unknown_row(*, expires_at: datetime, state: str = "delivery_unknown") -> dict:
    return {
        "id": 9,
        "event_key": "daily-result-bell:-1001:2026-07-10",
        "chat_id": -1001,
        "bell_date": date(2026, 7, 11),
        "cohort_pick_date": date(2026, 7, 10),
        "state": state,
        "expires_at": expires_at,
        "next_attempt_at": None,
        "attempt_count": 1,
        "attempted_at": datetime(2026, 7, 11, 12, 5, tzinfo=timezone.utc),
        "telegram_message_id": None,
        "last_error_code": "TimedOut",
        "last_error": "ambiguous send",
        "updated_at": datetime(2026, 7, 11, 12, 6, tzinfo=timezone.utc),
        "sent_at": None,
    }


async def test_reconciliation_preview_keeps_chat_cohort_and_error_visible(monkeypatch):
    row = _unknown_row(expires_at=datetime.now(timezone.utc) + timedelta(hours=1))
    connection = _Connection([row])
    monkeypatch.setattr(
        "renaiss_bot.database.market_queries.get_db",
        AsyncMock(return_value=_Pool(connection)),
    )

    result = await get_daily_pick_result_bell_reconciliation(9)

    assert result is not None
    assert result["chat_id"] == -1001
    assert result["cohort_pick_date"] == date(2026, 7, 10)
    assert result["last_error_code"] == "TimedOut"
    sql = connection.fetchrow.await_args.args[0]
    assert "last_error_code" in sql and "last_error" in sql


async def test_operator_mark_sent_is_fenced_and_audited_in_one_transaction(monkeypatch):
    now = datetime(2026, 7, 11, 13, tzinfo=timezone.utc)
    before = _unknown_row(expires_at=now - timedelta(minutes=1))
    after = {**before, "state": "sent", "telegram_message_id": 777, "sent_at": now}
    connection = _Connection([before, after, {"id": 501}])
    monkeypatch.setattr(
        "renaiss_bot.database.market_queries.get_db",
        AsyncMock(return_value=_Pool(connection)),
    )

    result = await reconcile_daily_pick_result_bell(
        outbox_id=9,
        action="mark-sent",
        operator_name="alice",
        telegram_message_id=777,
        note="confirmed in official chat",
        as_of=now,
    )

    assert result["state"] == "sent"
    assert result["audit_event_id"] == 501
    lock_sql = connection.fetchrow.await_args_list[0].args[0]
    update_sql = connection.fetchrow.await_args_list[1].args[0]
    audit_call = connection.fetchrow.await_args_list[2]
    assert "FOR UPDATE" in lock_sql
    assert "state = 'delivery_unknown'" in update_sql
    assert "telegram_message_id = $2" in update_sql
    assert "INSERT INTO renaiss_events" in audit_call.args[0]
    assert "ON CONFLICT" not in audit_call.args[0]
    assert audit_call.args[1] == "daily-result-bell:9:attempt-1:operator-mark-sent"
    metadata = json.loads(audit_call.args[3])
    assert metadata["operator"] == "alice"
    assert metadata["from_state"] == "delivery_unknown"
    assert metadata["to_state"] == "sent"
    assert metadata["previous_error_code"] == "TimedOut"


async def test_operator_retry_is_allowed_only_before_original_expiry(monkeypatch):
    now = datetime(2026, 7, 11, 13, tzinfo=timezone.utc)
    before = _unknown_row(expires_at=now + timedelta(minutes=20))
    after = {**before, "state": "retryable", "next_attempt_at": now}
    connection = _Connection([before, after, {"id": 502}])
    monkeypatch.setattr(
        "renaiss_bot.database.market_queries.get_db",
        AsyncMock(return_value=_Pool(connection)),
    )

    result = await reconcile_daily_pick_result_bell(
        outbox_id=9,
        action="retry",
        operator_name="alice",
        note="confirmed absent",
        as_of=now,
    )

    assert result["state"] == "retryable"
    update_sql = connection.fetchrow.await_args_list[1].args[0]
    assert "state = 'delivery_unknown'" in update_sql
    assert "expires_at > $2" in update_sql
    assert connection.fetchrow.await_args_list[1].args[2] == now


async def test_operator_retry_rejects_expired_or_non_unknown_rows(monkeypatch):
    now = datetime(2026, 7, 11, 13, tzinfo=timezone.utc)
    for row, code in (
        (_unknown_row(expires_at=now), "window_expired"),
        (_unknown_row(expires_at=now + timedelta(hours=1), state="sent"), "invalid_state"),
    ):
        connection = _Connection([row])
        monkeypatch.setattr(
            "renaiss_bot.database.market_queries.get_db",
            AsyncMock(return_value=_Pool(connection)),
        )
        with pytest.raises(ResultBellReconciliationError) as exc_info:
            await reconcile_daily_pick_result_bell(
                outbox_id=9,
                action="retry",
                operator_name="alice",
                as_of=now,
            )
        assert exc_info.value.code == code
        assert connection.fetchrow.await_count == 1


async def test_cli_requires_message_id_and_prints_reconciled_context(monkeypatch, capsys):
    parser = build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["mark-sent", "9", "--operator", "alice"])

    reconciled = {
        **_unknown_row(expires_at=datetime.now(timezone.utc) + timedelta(hours=1)),
        "state": "sent",
        "telegram_message_id": 777,
        "audit_event_id": 501,
    }
    action = AsyncMock(return_value=reconciled)
    monkeypatch.setattr(
        "renaiss_bot.tools.reconcile_result_bell.reconcile_daily_pick_result_bell",
        action,
    )
    args = SimpleNamespace(
        action="mark-sent",
        outbox_id=9,
        operator="alice",
        note="confirmed",
        telegram_message_id=777,
    )

    assert await run(args) == 0
    output = capsys.readouterr().out
    assert "chat: -1001" in output
    assert "cohort pick date: 2026-07-10" in output
    assert "last error: TimedOut | ambiguous send" in output
    assert "audit event id: 501" in output
