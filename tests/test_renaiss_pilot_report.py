"""Pilot KPI report formatting."""

from __future__ import annotations

from renaiss_bot.tools.pilot_report import evaluate_readiness, execute, load_report, print_report


class _AsyncContext:
    def __init__(self, value):
        self.value = value

    async def __aenter__(self):
        return self.value

    async def __aexit__(self, *args):
        return None


class _ReportConnection:
    def __init__(self):
        self.fetch_calls = []
        self.fetchrow_calls = []

    async def fetch(self, sql, *args):
        self.fetch_calls.append((sql, args))
        return []

    async def fetchrow(self, sql, *args):
        self.fetchrow_calls.append((sql, args))
        return {}


class _ReportPool:
    def __init__(self, connection):
        self.connection = connection

    def acquire(self):
        return _AsyncContext(self.connection)


async def test_retention_report_uses_only_cohorts_inside_requested_window(monkeypatch):
    connection = _ReportConnection()
    monkeypatch.setattr(
        "renaiss_bot.tools.pilot_report.get_db",
        lambda: _async_value(_ReportPool(connection)),
    )

    await load_report(14)

    retention_sql, args = next(
        (sql, args)
        for sql, args in connection.fetchrow_calls
        if "WITH first_seen AS" in sql
    )
    assert args == (14,)
    assert retention_sql.count("cohort_date >=") == 4
    assert "::date - $1::int" in retention_sql
async def test_retention_cohorts_use_persisted_random_assignment(monkeypatch):
    connection = _ReportConnection()
    monkeypatch.setattr(
        "renaiss_bot.tools.pilot_report.get_db",
        lambda: _async_value(_ReportPool(connection)),
    )

    await load_report(21)

    cohort_sql, args = next(
        (sql, args)
        for sql, args in connection.fetch_calls
        if "WITH first_c AS" in sql
    )
    assert args == (21,)
    assert "posted.metadata->>'assignment_id' IS NOT NULL" in cohort_sql
    assert "posted.metadata->>'variant' IN ('catch-only', 'insight-layer')" in cohort_sql
    assert "posted.metadata->>'guess_capable' = 'true'" in cohort_sql
    assert "'price_guess_locked', 'daily_pick_locked'" in cohort_sql
    assert "cohort.cohort_date + 7" in cohort_sql


async def _async_value(value):
    return value


def test_pilot_report_shows_funnel_clicks_retention_and_overdue(capsys):
    print_report(
        {
            "events": [
                {"event_name": "catch_entered", "events": 20, "users": 8},
                {"event_name": "price_guess_locked", "events": 12, "users": 6},
            ],
            "clicks": [
                {"source": "telegram_price", "clicks": 4, "unique_users": 3}
            ],
            "retention": {
                "d1_matured": 10,
                "d1_returned": 4,
                "d7_matured": 5,
                "d7_returned": 1,
            },
            "retention_cohorts": [
                {
                    "cohort": "catch-only",
                    "users": 12,
                    "insight_acted": 0,
                    "d1_matured": 12,
                    "d1_returned": 4,
                    "d7_matured": 10,
                    "d7_returned": 2,
                },
                {
                    "cohort": "insight-layer",
                    "users": 15,
                    "insight_acted": 9,
                    "d1_matured": 14,
                    "d1_returned": 8,
                    "d7_matured": 10,
                    "d7_returned": 4,
                },
            ],
            "spawn_funnel": {
                "exposed_rounds": 10,
                "rounds_with_catch": 7,
                "rounds_with_guess": 4,
                "revealed_rounds": 9,
            },
            "first_success": {
                "succeeded_users": 8,
                "within_3_minutes": 6,
                "median_seconds": 42,
            },
            "overdue": {
                "count": 2,
                "oldest_due": "2026-07-10T12:00:00Z",
                "ready_but_unsettled": 1,
                "needs_refresh": 1,
            },
            "result_bells": [
                {"state": "sent", "count": 2, "latest_at": "2026-07-11T12:05:00Z"},
                {"state": "suppressed", "count": 1, "latest_at": "2026-07-10T12:05:00Z"},
            ],
            "result_bell_unknown": [
                {
                    "id": 9,
                    "chat_id": -1001,
                    "cohort_pick_date": "2026-07-10",
                    "expires_at": "2026-07-12T00:00:00+09:00",
                    "last_error_code": "TimedOut",
                    "last_error": "ambiguous\nTelegram send",
                }
            ],
            "refresh_lease": {
                "last_status": "completed",
                "next_attempt_at": "2026-07-11T13:00:00Z",
                "lease_expires_at": "2026-07-11T12:01:00Z",
            },
            "api_cooldown": {
                "blocked_until": "2026-07-11T12:30:00Z",
                "reason": "http_429",
            },
        },
        14,
    )
    output = capsys.readouterr().out
    assert "catch_entered: 20 events / 8 users" in output
    assert "telegram_price: 4 clicks / 3 known users" in output
    assert "D1: 4/10 (40.0%)" in output
    assert "D7: 1/5 (20.0%)" in output
    assert "catch-only: users 12" in output
    assert "insight-layer: users 15 / same-day insight action 9/15 (60.0%)" in output
    assert "round conversion: exposed 10 / catch 7 (70.0%)" in output
    assert "first-c success: 8 users / <=3m 6 (75.0%) / median 42s" in output
    assert "overdue: 2" in output
    assert "ready but unsettled: 1" in output
    assert "needs price refresh: 1" in output
    assert "sent: 2" in output
    assert "suppressed: 1" in output
    assert "id=9 / chat=-1001 / cohort=2026-07-10" in output
    assert "error=TimedOut: ambiguous Telegram send" in output
    assert "status: completed" in output
    assert "blocked until: 2026-07-11T12:30:00Z / reason: http_429" in output


def test_readiness_passes_measurable_gates():
    report = {
        "measurement_contract_valid": True,
        "retention_cohorts": [
            {"cohort": "catch-only", "users": 20, "insight_acted": 0,
             "d7_matured": 20, "d7_returned": 4},
            {"cohort": "insight-layer", "users": 20, "insight_acted": 12,
             "d7_matured": 20, "d7_returned": 8},
        ],
        "first_success": {"succeeded_users": 20, "median_seconds": 90},
    }

    assert evaluate_readiness(report) == (True, [])


def test_readiness_fails_closed_with_actionable_reasons():
    report = {
        "retention_cohorts": [
            {"cohort": "catch-only", "users": 8, "insight_acted": 0,
             "d7_matured": 8, "d7_returned": 4},
            {"cohort": "insight-layer", "users": 8, "insight_acted": 2,
             "d7_matured": 7, "d7_returned": 2},
        ],
        "first_success": {"succeeded_users": 8, "median_seconds": 240},
    }

    ready, reasons = evaluate_readiness(report)
    assert ready is False
    assert any("D7 cohorts are not mature enough" in reason for reason in reasons)
    assert any("Insight-layer exposure has 8 users" in reason for reason in reasons)
    assert any("First-c success has 8 users" in reason for reason in reasons)
    assert any("measurement contract is not valid" in reason for reason in reasons)


async def test_require_ready_uses_distinct_not_ready_exit_code(monkeypatch, capsys):
    report = {
        "events": [],
        "clicks": [],
        "retention": {},
        "retention_cohorts": [],
        "spawn_funnel": {},
        "first_success": {},
        "overdue": {},
        "result_bells": [],
        "result_bell_unknown": [],
        "refresh_lease": {},
        "api_cooldown": {},
    }
    monkeypatch.setattr(
        "renaiss_bot.tools.pilot_report.load_report",
        lambda days: _async_value(report),
    )
    monkeypatch.setattr(
        "renaiss_bot.tools.pilot_report.close_db",
        lambda: _async_value(None),
    )

    assert await execute(14, require_ready=True) == 2
    assert "Pilot readiness\n- NOT READY" in capsys.readouterr().out
