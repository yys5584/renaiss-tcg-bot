"""Daily Market Pick rules and view tests (DB/API independent)."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import aiohttp
import pytest
from zoneinfo import ZoneInfo

from renaiss_bot.handlers.market import _log_result_viewed, _market_keyboard, _market_text
from renaiss_bot.database.market_queries import (
    acquire_market_refresh_job_lease,
    claim_due_pick_cards,
    finish_market_refresh_job_lease,
    get_latest_daily_pick_result,
    record_market_price_snapshot,
    settle_due_daily_picks,
)
from renaiss_bot.handlers.start import _home_keyboard
from renaiss_bot.jobs import (
    _daily_pick_refresh_seconds,
    _http_retry_after_seconds,
    _result_bell_window_open,
    publish_daily_pick_result_bell_job,
    refresh_daily_pick_prices_job,
    register_jobs,
)
from renaiss_bot.services.market import (
    daily_pick_enabled,
    decision_window_open,
    exact_price_lookup_configured,
    market_card_eligible,
    today_kst,
    week_start_kst,
)
from renaiss_bot.services.models import CardIdentity, RenaissPrice, card_identity_key

KST = ZoneInfo("Asia/Seoul")


def test_week_starts_on_monday_kst():
    assert week_start_kst(datetime(2026, 7, 11, 12, tzinfo=KST)).isoformat() == "2026-07-06"


def test_today_uses_kst_boundary():
    utc = datetime(2026, 7, 11, 16, 0, tzinfo=timezone.utc)
    assert today_kst(utc).isoformat() == "2026-07-12"


def test_result_bell_window_opens_at_2105_kst():
    assert not _result_bell_window_open(datetime(2026, 7, 11, 12, 4, tzinfo=timezone.utc))
    assert _result_bell_window_open(datetime(2026, 7, 11, 12, 5, tzinfo=timezone.utc))
    assert _result_bell_window_open(datetime(2026, 7, 11, 14, 59, tzinfo=timezone.utc))
    assert not _result_bell_window_open(datetime(2026, 7, 11, 15, 0, tzinfo=timezone.utc))


def test_refresh_intervals_and_retry_after_are_bounded(monkeypatch):
    monkeypatch.setenv("RENAISS_DAILY_PICK_REFRESH_SECONDS", str(10**100))
    assert _daily_pick_refresh_seconds() == 86_400
    assert _http_retry_after_seconds("inf") == 0
    assert _http_retry_after_seconds(str(10**100)) == 86_400


def test_pick_window_is_21_to_midnight_kst():
    assert not decision_window_open(datetime(2026, 7, 11, 20, 59, tzinfo=KST))
    assert decision_window_open(datetime(2026, 7, 11, 21, 0, tzinfo=KST))
    assert decision_window_open(datetime(2026, 7, 11, 23, 59, tzinfo=KST))
    assert not decision_window_open(datetime(2026, 7, 12, 0, 0, tzinfo=KST))


def _card() -> CardIdentity:
    return CardIdentity(
        category="pokemon_tcg",
        card_name="Charizard ex",
        set_code="SV4a",
        collector_number="349/190",
    )


def test_pick_gate_requires_exact_identifiable_renaiss_price():
    card = _card()
    exact = RenaissPrice(
        status="exact",
        source="renaiss-index-api",
        confidence="high",
        confidence_score=0.9,
        source_count=3,
        valuation_method="median",
        asset_url="https://index.renaissos.com/cards/charizard",
        fmv_usd=430,
        price_updated_at=datetime.now(timezone.utc),
        source_identity_key=card_identity_key(card),
    )
    candidate = RenaissPrice(status="candidate", source="catalog", fmv_usd=430)
    demo = RenaissPrice(status="exact", source="demo", fmv_usd=430)
    mock = RenaissPrice(
        status="exact",
        source="renaiss-index-api-mock",
        confidence="high",
        confidence_score=0.9,
        source_count=3,
        valuation_method="median",
        asset_url="https://index.renaissos.com/cards/charizard",
        fmv_usd=430,
        price_updated_at=datetime.now(timezone.utc),
    )
    assert market_card_eligible(card, exact)
    assert not market_card_eligible(_card(), candidate)
    assert not market_card_eligible(_card(), demo)
    assert not market_card_eligible(_card(), mock)
    assert not market_card_eligible(CardIdentity(category="pokemon_tcg", card_name="Unknown"), exact)


def test_exact_source_url_cannot_be_reused_for_another_card_identity():
    card = _card()
    other = CardIdentity(
        category=card.category,
        card_name="Blastoise ex",
        set_code=card.set_code,
        collector_number="350/190",
    )
    price = RenaissPrice(
        status="exact",
        source="renaiss-index-api:item-by-no",
        confidence="high",
        confidence_score=0.9,
        source_count=3,
        valuation_method="median",
        asset_url="https://index.renaissos.com/cards/charizard",
        fmv_usd=430,
        price_updated_at=datetime.now(timezone.utc),
        source_identity_key=card_identity_key(card),
    )

    assert market_card_eligible(card, price)
    assert not market_card_eligible(other, price)


@pytest.mark.parametrize("method", [None, "", "mean", "average"])
def test_pick_gate_requires_explicit_median_valuation_method(method):
    card = _card()
    price = RenaissPrice(
        status="exact",
        source="renaiss-index-api:item-by-no",
        confidence="high",
        confidence_score=0.9,
        source_count=3,
        valuation_method=method,
        asset_url="https://index.renaissos.com/cards/charizard",
        fmv_usd=430,
        price_updated_at=datetime.now(timezone.utc),
        source_identity_key=card_identity_key(card),
    )

    assert not market_card_eligible(card, price)


def test_pick_gate_accepts_official_categorical_confidence_without_invented_score():
    card = _card()
    price = RenaissPrice(
        status="exact",
        source="renaiss-card-detail-api",
        confidence="prime",
        confidence_score=None,
        source_count=2,
        observation_count=51,
        valuation_method="median",
        asset_url="https://index.renaissos.com/card/pokemon/base-set/charizard",
        fmv_usd=420.42,
        price_updated_at=datetime.now(timezone.utc),
        source_identity_key=card_identity_key(card),
    )

    assert market_card_eligible(card, price)


def test_pick_gate_rejects_stale_or_low_confidence_price():
    stale = RenaissPrice(
        status="exact",
        source="renaiss-index-api",
        confidence="high",
        confidence_score=0.9,
        source_count=3,
        valuation_method="median",
        asset_url="https://index.renaissos.com/cards/charizard",
        fmv_usd=430,
        price_updated_at=datetime.now(timezone.utc) - timedelta(hours=49),
    )
    low = RenaissPrice(
        status="exact",
        source="renaiss-index-api",
        confidence="low",
        confidence_score=0.2,
        source_count=1,
        asset_url="https://index.renaissos.com/cards/charizard",
        fmv_usd=430,
    )
    assert not market_card_eligible(_card(), stale)
    assert not market_card_eligible(_card(), low)


def test_pick_gate_rejects_missing_freshness_timestamp():
    missing_freshness = RenaissPrice(
        status="exact",
        source="renaiss-index-api",
        confidence="high",
        confidence_score=0.9,
        source_count=3,
        asset_url="https://index.renaissos.com/cards/charizard",
        fmv_usd=430,
    )
    assert missing_freshness.price_updated_at is None
    assert not market_card_eligible(_card(), missing_freshness)


def test_pick_gate_rejects_public_tier_without_numeric_evidence():
    public = RenaissPrice(
        status="exact",
        source="renaiss-index-api",
        confidence="high",
        fmv_usd=430,
    )
    one_source = RenaissPrice(
        status="exact",
        source="renaiss-index-api",
        confidence="high",
        confidence_score=0.9,
        source_count=1,
        asset_url="https://index.renaissos.com/cards/charizard",
        fmv_usd=430,
    )
    assert not market_card_eligible(_card(), public)
    assert not market_card_eligible(_card(), one_source)


@pytest.mark.parametrize("non_finite", [float("nan"), float("inf"), float("-inf")])
def test_pick_gate_rejects_non_finite_numeric_evidence(non_finite):
    invalid_fmv = RenaissPrice(
        status="exact",
        source="renaiss-index-api",
        confidence="high",
        confidence_score=0.9,
        source_count=3,
        asset_url="https://index.renaissos.com/cards/charizard",
        fmv_usd=non_finite,
        price_updated_at=datetime.now(timezone.utc),
    )
    invalid_confidence = RenaissPrice(
        status="exact",
        source="renaiss-index-api",
        confidence="high",
        confidence_score=non_finite,
        source_count=3,
        asset_url="https://index.renaissos.com/cards/charizard",
        fmv_usd=430,
        price_updated_at=datetime.now(timezone.utc),
    )

    assert not market_card_eligible(_card(), invalid_fmv)
    assert not market_card_eligible(_card(), invalid_confidence)


def test_pick_gate_rejects_boolean_numeric_evidence():
    invalid = RenaissPrice(
        status="exact",
        source="renaiss-index-api",
        confidence="high",
        confidence_score=True,
        source_count=3,
        asset_url="https://index.renaissos.com/cards/charizard",
        fmv_usd=True,
        price_updated_at=datetime.now(timezone.utc),
    )

    assert not market_card_eligible(_card(), invalid)


def test_pick_gate_requires_exact_source_page_for_attribution():
    missing_url = RenaissPrice(
        status="exact",
        source="renaiss-index-api",
        confidence="high",
        confidence_score=0.9,
        source_count=3,
        fmv_usd=430,
    )
    assert not market_card_eligible(_card(), missing_url)


def test_pick_gate_rejects_non_renaiss_https_source_page():
    malicious_url = RenaissPrice(
        status="exact",
        source="renaiss-index-api",
        confidence="high",
        confidence_score=0.9,
        source_count=3,
        asset_url="https://example.com/fake-renaiss-source",
        fmv_usd=430,
        price_updated_at=datetime.now(timezone.utc),
    )

    assert not market_card_eligible(_card(), malicious_url)


@pytest.mark.parametrize(
    "url",
    [
        "https://index.renaissos.com/",
        "https://index.renaissos.com:444/cards/charizard",
        "https://user:secret@index.renaissos.com/cards/charizard",
    ],
)
def test_pick_gate_rejects_noncanonical_source_origins(url):
    price = RenaissPrice(
        status="exact",
        source="renaiss-index-api",
        confidence="high",
        confidence_score=0.9,
        source_count=3,
        asset_url=url,
        fmv_usd=430,
        price_updated_at=datetime.now(timezone.utc),
    )
    assert not market_card_eligible(_card(), price)


def test_pick_gate_rejects_confidence_score_above_one():
    price = RenaissPrice(
        status="exact",
        source="renaiss-index-api",
        confidence="high",
        confidence_score=1.01,
        source_count=3,
        asset_url="https://index.renaissos.com/cards/charizard",
        fmv_usd=430,
        price_updated_at=datetime.now(timezone.utc),
    )
    assert not market_card_eligible(_card(), price)


def _board():
    return [
        {
            "id": 7,
            "card_name": "Charizard ex",
            "fmv_usd": 430.0,
            "pick_eligible": True,
            "asset_url": "https://index.renaissos.com/cards/charizard-ex",
        },
        {"id": 8, "card_name": "Candidate Card", "fmv_usd": 90.0, "pick_eligible": False},
    ]


def _pick():
    return {
        "board_card_id": 7,
        "card_name": "Charizard ex",
        "entry_fmv_usd": 430.0,
        "current_fmv_usd": 451.5,
    }


def _result():
    return {
        "pick_date": date(2026, 7, 10),
        "board_card_id": 7,
        "settlement_snapshot_id": 33,
        "card_name": "Charizard ex",
        "entry_fmv_usd": 430.0,
        "result_fmv_usd": 451.5,
        "result_move_pct": 5.0,
        "result_confidence": "high",
        "result_source_count": 3,
        "result_price_updated_at": datetime(2026, 7, 11, tzinfo=timezone.utc),
        "asset_url": "https://index.renaissos.com/cards/charizard-ex",
    }


def test_market_view_explicitly_excludes_trading_and_money():
    text = _market_text(_board(), None, is_open=True)
    assert "No money, positions, buying, selling, or fees" in text
    assert "Candidate Card" in text and "watch only" in text
    assert "not investment advice" in text


def test_pick_keyboard_only_lists_verified_cards():
    keyboard = _market_keyboard(_board(), None, is_open=True)
    assert keyboard is not None
    buttons = [button for row in keyboard.inline_keyboard for button in row]
    assert [button.callback_data for button in buttons] == ["renaiss:market:pick:7"]


def test_first_daily_pick_is_final_in_view():
    assert _market_keyboard(_board(), _pick(), is_open=True) is None
    text = _market_text(_board(), _pick(), is_open=True)
    assert "My locked pick" in text
    assert "+5.00%" in text


def test_latest_result_is_separate_from_todays_pick():
    text = _market_text(
        _board(),
        _pick(),
        is_open=True,
        latest_result=_result(),
    )
    assert "My locked pick" in text
    assert "Latest 24h result" in text
    assert "2026-07-10" in text
    assert "Starting reference $430 → 24h reference $451.50 (+5.00%)" in text
    assert "3 sources" in text


class _AsyncContext:
    def __init__(self, value):
        self.value = value

    async def __aenter__(self):
        return self.value

    async def __aexit__(self, *args):
        return None


class _MarketConnection:
    def __init__(self, *, row=None, rows=None):
        self.fetchrow = AsyncMock(return_value=row)
        self.fetch = AsyncMock(return_value=rows or [])
        self.fetchval = AsyncMock(return_value=None)
        self.execute = AsyncMock(return_value="UPDATE 1")

    def transaction(self):
        return _AsyncContext(self)


class _MarketPool:
    def __init__(self, connection):
        self.connection = connection

    def acquire(self):
        return _AsyncContext(self.connection)


async def test_latest_result_query_is_not_limited_to_today(monkeypatch):
    connection = _MarketConnection(row=_result())
    monkeypatch.setattr(
        "renaiss_bot.database.market_queries.get_db",
        AsyncMock(return_value=_MarketPool(connection)),
    )

    result = await get_latest_daily_pick_result(
        123,
        now=datetime(2026, 7, 11, 22, tzinfo=KST),
    )

    assert result is not None and result["result_fmv_usd"] == 451.5
    sql = connection.fetchrow.await_args.args[0]
    assert "p.pick_date < $2" in sql
    assert "p.settlement_snapshot_id" in sql
    assert connection.fetchrow.await_args.args[2] == date(2026, 7, 11)


async def test_settlement_sql_persists_first_mark_and_event_once(monkeypatch):
    row = {**_result(), "user_id": 123, "settlement_event_count": 1}
    connection = _MarketConnection(rows=[row])
    monkeypatch.setattr(
        "renaiss_bot.database.market_queries.get_db",
        AsyncMock(return_value=_MarketPool(connection)),
    )

    settled = await settle_due_daily_picks(
        board_card_id=7,
        as_of=datetime(2026, 7, 11, 22, tzinfo=timezone.utc),
    )

    assert settled[0]["settlement_snapshot_id"] == 33
    sql = connection.fetch.await_args.args[0]
    assert "ORDER BY s.captured_at ASC, s.id ASC" in sql
    assert "FOR UPDATE OF p SKIP LOCKED" in sql
    assert "SET settlement_snapshot_id = candidates.snapshot_id" in sql
    assert "'daily_pick_settled'" in sql
    assert "ON CONFLICT (event_key) DO NOTHING" in sql


async def test_refreshed_snapshot_dedup_is_serialized_per_board(monkeypatch):
    connection = _MarketConnection(row={"id": 99})
    monkeypatch.setattr(
        "renaiss_bot.database.market_queries.get_db",
        AsyncMock(return_value=_MarketPool(connection)),
    )
    price = RenaissPrice(
        status="exact",
        source="renaiss-index-api",
        fmv_usd=430,
        confidence="high",
        confidence_score=0.9,
        source_count=3,
        valuation_method="median",
        asset_url="https://index.renaissos.com/cards/charizard",
        price_updated_at=datetime.now(timezone.utc),
    )

    assert await record_market_price_snapshot(
        board_card_id=7,
        card=_card(),
        price=price,
    )

    assert "pg_advisory_xact_lock" in connection.fetchval.await_args.args[0]
    assert connection.fetchval.await_args.args[1] == "renaiss-market-snapshot:7"
    snapshot_sql = connection.fetchrow.await_args.args[0]
    assert "confidence IS NOT DISTINCT FROM $4" in snapshot_sql
    assert "pick_eligible = $5" in snapshot_sql
    assert "source_count IS NOT DISTINCT FROM $7" in snapshot_sql
    assert "valuation_method IS NOT DISTINCT FROM $9" in snapshot_sql
    assert "price_updated_at IS NOT DISTINCT FROM $10" in snapshot_sql


async def test_due_card_claim_uses_expiring_database_lease(monkeypatch):
    connection = _MarketConnection(
        rows=[
            {
                "board_card_id": 7,
                "card_name": "Charizard ex",
                "earliest_unsettled_at": datetime.now(timezone.utc),
                "pending_pick_count": 2,
            }
        ]
    )
    monkeypatch.setattr(
        "renaiss_bot.database.market_queries.get_db",
        AsyncMock(return_value=_MarketPool(connection)),
    )

    claimed = await claim_due_pick_cards(
        worker_token="worker-1",
        limit=8,
        lease_seconds=180,
    )

    assert claimed[0]["board_card_id"] == 7
    sql = connection.fetch.await_args.args[0]
    assert "renaiss_market_refresh_leases" in sql
    assert "lease.lease_until <= now()" in sql
    assert "ON CONFLICT (board_card_id) DO UPDATE" in sql
    assert "WHERE renaiss_market_refresh_leases.lease_until <= now()" in sql


async def test_global_refresh_lease_shares_cadence_and_fences_owner(monkeypatch):
    acquire_connection = _MarketConnection(row={"acquired_at": datetime.now(timezone.utc)})
    monkeypatch.setattr(
        "renaiss_bot.database.market_queries.get_db",
        AsyncMock(return_value=_MarketPool(acquire_connection)),
    )

    assert await acquire_market_refresh_job_lease(
        lease_owner="worker-1",
        lease_seconds=180,
    )
    acquire_sql = acquire_connection.fetchrow.await_args.args[0]
    assert "renaiss_job_leases.next_attempt_at <= clock_timestamp()" in acquire_sql
    assert "RETURNING acquired_at" in acquire_sql

    finish_connection = _MarketConnection(row={"job_name": "daily_pick_price_refresh"})
    monkeypatch.setattr(
        "renaiss_bot.database.market_queries.get_db",
        AsyncMock(return_value=_MarketPool(finish_connection)),
    )
    assert await finish_market_refresh_job_lease(
        lease_owner="worker-1",
        cadence_seconds=3600,
        retry_after_seconds=60,
        status="rate_limited",
    )
    finish_sql = finish_connection.fetchrow.await_args.args[0]
    assert "GREATEST" in finish_sql
    assert "AND lease_owner = $1" in finish_sql
    assert "AND lease_expires_at > clock_timestamp()" not in finish_sql


async def test_result_view_uses_one_stable_event_key(monkeypatch):
    event = AsyncMock(return_value=True)
    monkeypatch.setattr("renaiss_bot.handlers.market.log_event", event)

    await _log_result_viewed(user_id=123, chat_id=123, result=_result())

    assert event.await_args.args[0] == "daily_pick_result_viewed"
    assert event.await_args.kwargs["event_key"] == "daily-pick:2026-07-10:result-view:123"
    assert event.await_args.kwargs["metadata"]["settlement_snapshot_id"] == 33


def test_register_jobs_connects_daily_pick_refresh_once(monkeypatch):
    class Queue:
        def __init__(self):
            self.repeating = []
            self.daily = []
            self.once = []

        def run_repeating(self, *args, **kwargs):
            self.repeating.append((args, kwargs))

        def run_daily(self, *args, **kwargs):
            self.daily.append((args, kwargs))

        def run_once(self, *args, **kwargs):
            self.once.append((args, kwargs))

    queue = Queue()
    monkeypatch.setattr("renaiss_bot.jobs.daily_pick_enabled", lambda: True)
    monkeypatch.setattr("renaiss_bot.jobs.daily_pick_configuration_issues", lambda: [])
    monkeypatch.setattr("renaiss_bot.jobs.official_chat_id", lambda: None)

    register_jobs(SimpleNamespace(job_queue=queue))

    refresh_jobs = [
        call for call in queue.repeating if call[1].get("name") == "renaiss_daily_pick_price_refresh"
    ]
    assert len(refresh_jobs) == 1
    assert refresh_jobs[0][0][0] is refresh_daily_pick_prices_job
    bell_jobs = [
        call
        for call in queue.repeating
        if call[1].get("name") == "renaiss_daily_pick_result_bell"
    ]
    assert len(bell_jobs) == 1
    assert bell_jobs[0][0][0] is publish_daily_pick_result_bell_job
    assert bell_jobs[0][1]["interval"] == 300


def test_register_jobs_fails_closed_without_job_queue():
    with pytest.raises(RuntimeError, match="JobQueue is required"):
        register_jobs(SimpleNamespace(job_queue=None))


def test_register_jobs_keeps_obligation_drains_when_admission_is_closed(monkeypatch):
    queue = SimpleNamespace(
        repeating=[],
        run_repeating=lambda *args, **kwargs: queue.repeating.append((args, kwargs)),
        run_daily=lambda *args, **kwargs: None,
        run_once=lambda *args, **kwargs: None,
    )
    monkeypatch.setattr("renaiss_bot.jobs.daily_pick_enabled", lambda: False)
    monkeypatch.setattr("renaiss_bot.jobs.daily_pick_requested", lambda: False)
    monkeypatch.setattr("renaiss_bot.jobs.official_chat_id", lambda: None)

    register_jobs(SimpleNamespace(job_queue=queue))

    names = {kwargs.get("name") for _, kwargs in queue.repeating}
    assert "renaiss_daily_pick_price_refresh" in names
    assert "renaiss_daily_pick_result_bell" in names


def test_pick_without_post_24h_mark_does_not_show_a_result():
    pending = {**_pick(), "current_fmv_usd": None}
    text = _market_text(_board(), pending, is_open=False)
    assert "latest observed $" not in text
    assert "after 24h" in text


def test_locked_window_has_no_pick_keyboard():
    assert _market_keyboard(_board(), None, is_open=False) is None


def test_daily_pick_is_closed_by_default(monkeypatch):
    monkeypatch.delenv("RENAISS_DAILY_PICK_ENABLED", raising=False)
    assert not daily_pick_enabled()


def test_daily_pick_flag_alone_cannot_bypass_data_gates(monkeypatch):
    monkeypatch.setenv("RENAISS_DAILY_PICK_ENABLED", "1")
    monkeypatch.delenv("RENAISS_API_MOCK_JSON", raising=False)
    monkeypatch.delenv("RENAISS_API_KEY", raising=False)
    monkeypatch.delenv("RENAISS_API_SECRET", raising=False)
    monkeypatch.delenv("RENAISS_API_ITEM_BY_NO_PATH", raising=False)
    assert not daily_pick_enabled()


def test_daily_pick_mock_cannot_bypass_production_gates(monkeypatch):
    monkeypatch.setenv("RENAISS_DAILY_PICK_ENABLED", "1")
    monkeypatch.setenv("RENAISS_API_MOCK_JSON", "{}")
    monkeypatch.setenv("DATABASE_URL", "postgresql://example.invalid/renaiss")
    monkeypatch.setenv("RENAISS_API_KEY", "partner-key")
    monkeypatch.setenv("RENAISS_API_SECRET", "partner-secret")
    monkeypatch.setenv("RENAISS_API_ITEM_BY_NO_PATH", "/v1/index/item-by-no")
    monkeypatch.setenv("RENAISS_API_EXACT_CONTRACT", "item-by-no-v1")
    monkeypatch.setenv("RENAISS_API_EXACT_VALUATION_METHOD", "median")
    assert not daily_pick_enabled()


def test_exact_lookup_requires_approved_median_contract(monkeypatch):
    monkeypatch.delenv("RENAISS_API_MOCK_JSON", raising=False)
    monkeypatch.setenv("RENAISS_API_KEY", "partner-key")
    monkeypatch.setenv("RENAISS_API_SECRET", "partner-secret")
    monkeypatch.setenv("RENAISS_API_ITEM_BY_NO_PATH", "/v1/index/item-by-no")
    monkeypatch.setenv("RENAISS_API_EXACT_CONTRACT", "item-by-no-v1")
    monkeypatch.delenv("RENAISS_API_EXACT_VALUATION_METHOD", raising=False)
    assert not exact_price_lookup_configured()
    monkeypatch.setenv("RENAISS_API_EXACT_VALUATION_METHOD", "mean")
    assert not exact_price_lookup_configured()
    monkeypatch.setenv("RENAISS_API_EXACT_VALUATION_METHOD", "median")
    assert exact_price_lookup_configured()


def test_home_menu_hides_daily_market_pick_until_ready(monkeypatch):
    monkeypatch.delenv("RENAISS_DAILY_PICK_ENABLED", raising=False)
    callbacks = {
        button.callback_data
        for row in _home_keyboard().inline_keyboard
        for button in row
        if button.callback_data
    }
    assert "renaiss:market" not in callbacks


def test_home_menu_links_to_daily_market_pick_when_ready(monkeypatch):
    monkeypatch.setattr("renaiss_bot.services.market._daily_pick_preflight_ready", True)
    monkeypatch.setenv("RENAISS_DAILY_PICK_ENABLED", "1")
    monkeypatch.delenv("RENAISS_API_MOCK_JSON", raising=False)
    monkeypatch.setenv("DATABASE_URL", "postgresql://example.invalid/renaiss")
    monkeypatch.delenv("RENAISS_SKIP_DB", raising=False)
    monkeypatch.setenv("RENAISS_API_KEY", "partner-key")
    monkeypatch.setenv("RENAISS_API_SECRET", "partner-secret")
    monkeypatch.setenv("RENAISS_API_ITEM_BY_NO_PATH", "/v1/index/item-by-no")
    monkeypatch.setenv("RENAISS_API_EXACT_CONTRACT", "item-by-no-v1")
    monkeypatch.setenv("RENAISS_API_EXACT_VALUATION_METHOD", "median")
    monkeypatch.setenv("RENAISS_DAILY_PICK_PROBE_CARD_NAME", "Charizard")
    monkeypatch.setenv("RENAISS_DAILY_PICK_PROBE_SET_NAME", "Base Set")
    monkeypatch.setenv("RENAISS_DAILY_PICK_PROBE_ITEM_NO", "4/102")
    callbacks = {
        button.callback_data
        for row in _home_keyboard().inline_keyboard
        for button in row
        if button.callback_data
    }
    assert "renaiss:market" in callbacks


def _mock_refresh_job_lease(monkeypatch):
    acquire = AsyncMock(return_value=True)
    renew = AsyncMock(return_value=True)
    finish = AsyncMock(return_value=True)
    monkeypatch.setattr("renaiss_bot.jobs.acquire_market_refresh_job_lease", acquire)
    monkeypatch.setattr("renaiss_bot.jobs.renew_market_refresh_job_lease", renew)
    monkeypatch.setattr("renaiss_bot.jobs.finish_market_refresh_job_lease", finish)
    return acquire, renew, finish


def _enable_daily_pick_job(monkeypatch):
    monkeypatch.setenv("RENAISS_DAILY_PICK_ENABLED", "1")
    monkeypatch.delenv("RENAISS_API_MOCK_JSON", raising=False)
    monkeypatch.setenv("DATABASE_URL", "postgresql://example.invalid/renaiss")
    monkeypatch.delenv("RENAISS_SKIP_DB", raising=False)
    monkeypatch.setenv("RENAISS_API_KEY", "partner-key")
    monkeypatch.setenv("RENAISS_API_SECRET", "partner-secret")
    monkeypatch.setenv("RENAISS_API_ITEM_BY_NO_PATH", "/v1/index/item-by-no")
    monkeypatch.setenv("RENAISS_API_EXACT_CONTRACT", "item-by-no-v1")
    monkeypatch.setenv("RENAISS_API_EXACT_VALUATION_METHOD", "median")


async def test_closed_admission_still_settles_persisted_obligations(monkeypatch):
    monkeypatch.delenv("RENAISS_DAILY_PICK_ENABLED", raising=False)
    monkeypatch.delenv("RENAISS_API_KEY", raising=False)
    monkeypatch.delenv("RENAISS_API_SECRET", raising=False)
    monkeypatch.delenv("RENAISS_API_ITEM_BY_NO_PATH", raising=False)
    settle = AsyncMock(return_value=[{"pick_date": date(2026, 7, 10)}])
    acquire = AsyncMock()
    monkeypatch.setattr("renaiss_bot.jobs.settle_due_daily_picks", settle)
    monkeypatch.setattr("renaiss_bot.jobs.acquire_market_refresh_job_lease", acquire)

    await refresh_daily_pick_prices_job(SimpleNamespace())

    settle.assert_awaited_once_with(limit=500)
    acquire.assert_not_awaited()
    monkeypatch.setenv("RENAISS_DAILY_PICK_PROBE_CARD_NAME", "Charizard")
    monkeypatch.setenv("RENAISS_DAILY_PICK_PROBE_SET_NAME", "Base Set")
    monkeypatch.setenv("RENAISS_DAILY_PICK_PROBE_ITEM_NO", "4/102")


async def test_refresh_job_still_settles_but_skips_api_when_lease_is_owned(monkeypatch):
    _enable_daily_pick_job(monkeypatch)
    settle = AsyncMock(return_value=[{"pick_date": date(2026, 7, 10)}])
    acquire = AsyncMock(return_value=False)
    claim_cards = AsyncMock()
    fetch = AsyncMock()
    finish = AsyncMock()
    monkeypatch.setattr("renaiss_bot.jobs.settle_due_daily_picks", settle)
    monkeypatch.setattr("renaiss_bot.jobs.acquire_market_refresh_job_lease", acquire)
    monkeypatch.setattr("renaiss_bot.jobs.claim_due_pick_cards", claim_cards)
    monkeypatch.setattr("renaiss_bot.jobs.fetch_official_price", fetch)
    monkeypatch.setattr("renaiss_bot.jobs.finish_market_refresh_job_lease", finish)

    await refresh_daily_pick_prices_job(SimpleNamespace())

    settle.assert_awaited_once_with(limit=500)
    acquire.assert_awaited_once()
    claim_cards.assert_not_awaited()
    fetch.assert_not_awaited()
    finish.assert_not_awaited()


async def test_refresh_job_stops_before_api_when_lease_heartbeat_is_lost(monkeypatch):
    _, renew, finish = _mock_refresh_job_lease(monkeypatch)
    renew.return_value = False
    _enable_daily_pick_job(monkeypatch)
    due_row = {
        "board_card_id": 7,
        "category": "pokemon_tcg",
        "local_card_id": "card-7",
        "card_name": "Charizard ex",
        "set_code": "SV4a",
        "set_name": "Shiny Treasure ex",
        "collector_number": "349/190",
        "variation": "",
        "language": "Japanese",
        "grade": "RAW",
        "image_url": None,
        "earliest_unsettled_at": datetime.now(timezone.utc) - timedelta(minutes=5),
        "pending_pick_count": 1,
    }
    monkeypatch.setattr(
        "renaiss_bot.jobs.claim_due_pick_cards",
        AsyncMock(return_value=[due_row, due_row]),
    )
    fetch = AsyncMock()
    monkeypatch.setattr("renaiss_bot.jobs.fetch_official_price", fetch)
    settle = AsyncMock(return_value=[])
    monkeypatch.setattr("renaiss_bot.jobs.settle_due_daily_picks", settle)
    release = AsyncMock(return_value=2)
    monkeypatch.setattr("renaiss_bot.jobs.release_market_refresh_leases", release)

    await refresh_daily_pick_prices_job(SimpleNamespace())

    fetch.assert_not_awaited()
    renew.assert_awaited_once()
    assert settle.await_count == 2
    assert release.await_args.kwargs["status"] == "lease_lost_with_failures"
    assert finish.await_args.kwargs["status"] == "lease_lost_with_failures"


async def test_due_pick_refresh_records_a_new_official_mark(monkeypatch):
    _, renew_lease, finish_lease = _mock_refresh_job_lease(monkeypatch)
    monkeypatch.setenv("RENAISS_DAILY_PICK_ENABLED", "1")
    monkeypatch.delenv("RENAISS_API_MOCK_JSON", raising=False)
    monkeypatch.setenv("DATABASE_URL", "postgresql://example.invalid/renaiss")
    monkeypatch.delenv("RENAISS_SKIP_DB", raising=False)
    monkeypatch.setenv("RENAISS_API_KEY", "partner-key")
    monkeypatch.setenv("RENAISS_API_SECRET", "partner-secret")
    monkeypatch.setenv("RENAISS_API_ITEM_BY_NO_PATH", "/v1/index/item-by-no")
    monkeypatch.setenv("RENAISS_API_EXACT_CONTRACT", "item-by-no-v1")
    monkeypatch.setenv("RENAISS_API_EXACT_VALUATION_METHOD", "median")
    settles_at = datetime.now(timezone.utc) - timedelta(minutes=5)
    row = {
        "board_card_id": 7,
        "category": "pokemon_tcg",
        "local_card_id": "card-7",
        "card_name": "Charizard ex",
        "set_code": "SV4a",
        "set_name": "Shiny Treasure ex",
        "collector_number": "349/190",
        "variation": "",
        "language": "Japanese",
        "grade": "RAW",
        "image_url": None,
        "earliest_unsettled_at": settles_at,
        "pending_pick_count": 2,
    }
    price = RenaissPrice(
        status="exact",
        source="renaiss-index-api",
        fmv_usd=451.5,
        confidence="high",
        confidence_score=0.9,
        source_count=3,
        asset_url="https://index.renaissos.com/cards/charizard-ex",
        price_updated_at=datetime.now(timezone.utc),
    )
    list_due = AsyncMock(return_value=[row])
    fetch = AsyncMock(return_value=price)
    record = AsyncMock(return_value=True)
    event = AsyncMock(return_value=True)
    monkeypatch.setattr("renaiss_bot.jobs.claim_due_pick_cards", list_due)
    monkeypatch.setattr("renaiss_bot.jobs.fetch_official_price", fetch)
    monkeypatch.setattr("renaiss_bot.jobs.record_market_price_snapshot", record)
    monkeypatch.setattr("renaiss_bot.jobs.log_event", event)
    settle = AsyncMock(return_value=[])
    monkeypatch.setattr("renaiss_bot.jobs.settle_due_daily_picks", settle)
    release = AsyncMock(return_value=1)
    monkeypatch.setattr("renaiss_bot.jobs.release_market_refresh_leases", release)

    await refresh_daily_pick_prices_job(SimpleNamespace())

    record.assert_awaited_once()
    saved_card = record.await_args.kwargs["card"]
    assert saved_card.set_name == "Shiny Treasure ex"
    assert saved_card.metadata["variation"] == ""
    assert event.await_args.kwargs["metadata"]["newer_than_settlement"] is True
    assert settle.await_count == 3
    release.assert_awaited_once()
    renew_lease.assert_awaited_once()
    finish_lease.assert_awaited_once()


async def test_due_pick_refresh_never_treats_unknown_freshness_as_newer(monkeypatch):
    _mock_refresh_job_lease(monkeypatch)
    monkeypatch.setenv("RENAISS_DAILY_PICK_ENABLED", "1")
    monkeypatch.delenv("RENAISS_API_MOCK_JSON", raising=False)
    monkeypatch.setenv("DATABASE_URL", "postgresql://example.invalid/renaiss")
    monkeypatch.delenv("RENAISS_SKIP_DB", raising=False)
    monkeypatch.setenv("RENAISS_API_KEY", "partner-key")
    monkeypatch.setenv("RENAISS_API_SECRET", "partner-secret")
    monkeypatch.setenv("RENAISS_API_ITEM_BY_NO_PATH", "/v1/index/item-by-no")
    monkeypatch.setenv("RENAISS_API_EXACT_CONTRACT", "item-by-no-v1")
    monkeypatch.setenv("RENAISS_API_EXACT_VALUATION_METHOD", "median")
    row = {
        "board_card_id": 7,
        "category": "pokemon_tcg",
        "local_card_id": "card-7",
        "card_name": "Charizard ex",
        "set_code": "SV4a",
        "set_name": "Shiny Treasure ex",
        "collector_number": "349/190",
        "variation": "",
        "language": "Japanese",
        "grade": "RAW",
        "image_url": None,
        "earliest_unsettled_at": datetime.now(timezone.utc) - timedelta(minutes=5),
        "pending_pick_count": 1,
    }
    price = RenaissPrice(
        status="exact",
        source="renaiss-index-api",
        fmv_usd=451.5,
        confidence="high",
        confidence_score=0.9,
        source_count=3,
        asset_url="https://index.renaissos.com/cards/charizard-ex",
        price_updated_at=None,
    )
    event = AsyncMock(return_value=True)
    monkeypatch.setattr("renaiss_bot.jobs.claim_due_pick_cards", AsyncMock(return_value=[row]))
    monkeypatch.setattr("renaiss_bot.jobs.fetch_official_price", AsyncMock(return_value=price))
    monkeypatch.setattr(
        "renaiss_bot.jobs.record_market_price_snapshot",
        AsyncMock(return_value=True),
    )
    monkeypatch.setattr("renaiss_bot.jobs.log_event", event)
    monkeypatch.setattr(
        "renaiss_bot.jobs.settle_due_daily_picks",
        AsyncMock(return_value=[]),
    )
    monkeypatch.setattr(
        "renaiss_bot.jobs.release_market_refresh_leases",
        AsyncMock(return_value=1),
    )

    await refresh_daily_pick_prices_job(SimpleNamespace())

    assert event.await_args.kwargs["metadata"]["newer_than_settlement"] is False
    assert event.await_args.kwargs["event_key"].endswith(":unknown")


async def test_due_pick_refresh_stops_batch_on_rate_limit(monkeypatch):
    _, _, finish_lease = _mock_refresh_job_lease(monkeypatch)
    monkeypatch.setenv("RENAISS_DAILY_PICK_ENABLED", "1")
    monkeypatch.delenv("RENAISS_API_MOCK_JSON", raising=False)
    monkeypatch.setenv("DATABASE_URL", "postgresql://example.invalid/renaiss")
    monkeypatch.delenv("RENAISS_SKIP_DB", raising=False)
    monkeypatch.setenv("RENAISS_API_KEY", "partner-key")
    monkeypatch.setenv("RENAISS_API_SECRET", "partner-secret")
    monkeypatch.setenv("RENAISS_API_ITEM_BY_NO_PATH", "/v1/index/item-by-no")
    monkeypatch.setenv("RENAISS_API_EXACT_CONTRACT", "item-by-no-v1")
    monkeypatch.setenv("RENAISS_API_EXACT_VALUATION_METHOD", "median")
    row = {
        "board_card_id": 7,
        "category": "pokemon_tcg",
        "local_card_id": "card-7",
        "card_name": "Charizard ex",
        "set_code": "SV4a",
        "set_name": "Shiny Treasure ex",
        "collector_number": "349/190",
        "variation": "",
        "language": "Japanese",
        "grade": "RAW",
        "image_url": None,
        "earliest_unsettled_at": datetime.now(timezone.utc) - timedelta(minutes=5),
        "pending_pick_count": 1,
    }
    error = aiohttp.ClientResponseError(
        SimpleNamespace(real_url="https://api.renaissos.com"),
        (),
        status=429,
        headers={"Retry-After": "60"},
    )
    fetch = AsyncMock(side_effect=error)
    record = AsyncMock()
    monkeypatch.setattr("renaiss_bot.jobs.claim_due_pick_cards", AsyncMock(return_value=[row, row]))
    monkeypatch.setattr("renaiss_bot.jobs.fetch_official_price", fetch)
    monkeypatch.setattr("renaiss_bot.jobs.record_market_price_snapshot", record)
    settle = AsyncMock(return_value=[])
    monkeypatch.setattr("renaiss_bot.jobs.settle_due_daily_picks", settle)
    release = AsyncMock(return_value=2)
    monkeypatch.setattr("renaiss_bot.jobs.release_market_refresh_leases", release)

    await refresh_daily_pick_prices_job(SimpleNamespace())

    assert fetch.await_count == 1
    record.assert_not_awaited()
    assert settle.await_count == 2
    assert release.await_args.kwargs["status"] == "rate_limited_with_failures"
    assert finish_lease.await_args.kwargs["retry_after_seconds"] == 60
