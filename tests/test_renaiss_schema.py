"""Schema migration intent checks without requiring a local PostgreSQL daemon."""

from __future__ import annotations

import pytest

from renaiss_bot.database.schema import create_tables


class FakeConnection:
    def __init__(self):
        self.statements = []

    async def execute(self, statement, *args):
        self.statements.append(statement)
        return "OK"

    async def fetchval(self, statement, *args):
        self.statements.append(statement)
        return None

    def transaction(self):
        return Acquire(self)


class Acquire:
    def __init__(self, connection):
        self.connection = connection

    async def __aenter__(self):
        return self.connection

    async def __aexit__(self, *args):
        return None


class FakePool:
    def __init__(self):
        self.connection = FakeConnection()

    def acquire(self):
        return Acquire(self.connection)


async def test_schema_mutation_refuses_unpinned_ambient_database_url(monkeypatch):
    pool = FakePool()
    monkeypatch.setenv("DATABASE_URL", "postgresql://operator:secret@db/renaiss")
    monkeypatch.delenv("RENAISS_EXPECTED_DATABASE_FINGERPRINT", raising=False)

    with pytest.raises(RuntimeError, match="mutation target is not approved"):
        await create_tables(pool)

    assert pool.connection.statements == []


async def test_schema_is_additive_and_excludes_trading_tables():
    pool = FakePool()
    await create_tables(pool)
    sql = "\n".join(pool.connection.statements).lower()
    assert "create table if not exists renaiss_market_picks" in sql
    assert "pg_advisory_xact_lock" in sql
    assert "create table if not exists renaiss_market_refresh_leases" in sql
    assert "idx_renaiss_market_refresh_leases_until" in sql
    assert "create table if not exists renaiss_job_leases" in sql
    assert "create table if not exists renaiss_api_cooldowns" in sql
    assert "create table if not exists renaiss_api_request_gates" in sql
    assert "renaiss_catalog_positive_market_price" in sql
    assert "renaiss_user_cards_positive_market_price" in sql
    assert "next_attempt_at timestamptz" in sql
    assert "create table if not exists renaiss_pack_open_requests" in sql
    assert "request_id text primary key" in sql
    assert "idx_renaiss_pack_open_requests_quota" in sql
    assert "add column if not exists request_id text" in sql
    assert "idx_renaiss_pack_events_request" in sql
    assert "settlement_snapshot_id bigint" in sql
    assert "add column if not exists community_chat_id bigint" in sql
    assert "idx_renaiss_market_picks_chat_date" in sql
    assert "add column if not exists settlement_snapshot_id" in sql
    assert "add column if not exists settled_at" in sql
    assert "idx_renaiss_market_picks_unsettled_due" in sql
    assert "create table if not exists renaiss_result_bell_outbox" in sql
    assert "unique (chat_id, bell_date)" in sql
    assert "unique (chat_id, cohort_pick_date)" in sql
    assert "idx_renaiss_result_bell_dispatch" in sql
    assert "create table if not exists renaiss_flex_daily_slots" in sql
    assert "primary key (user_id, flex_date)" in sql
    assert "create table if not exists renaiss_events" in sql
    assert "create table if not exists renaiss_referral_links" in sql
    assert "alter column price_updated_at drop not null" in sql
    assert "add column if not exists asset_url text" in sql
    assert "add column if not exists is_tutorial boolean" in sql
    assert "renaiss:starter:welcome:v1" in sql
    assert "create table if not exists renaiss_telegram_media_cache" in sql
    assert "telegram_file_id text not null" in sql
    assert "drop table" not in sql
    assert "renaiss_user_cash" not in sql
    assert "renaiss_trade_log" not in sql
    assert "renaiss_positions" not in sql


async def test_market_board_create_statement_has_no_duplicate_columns():
    pool = FakePool()
    await create_tables(pool)
    statement = next(
        sql
        for sql in pool.connection.statements
        if "CREATE TABLE IF NOT EXISTS renaiss_market_board" in sql
    )
    body = statement.split("(", 1)[1].rsplit(")", 1)[0]
    columns = []
    for raw_line in body.splitlines():
        line = raw_line.strip().rstrip(",")
        if not line or line.upper().startswith(("UNIQUE ", "PRIMARY ", "FOREIGN ", "CHECK ")):
            continue
        columns.append(line.split()[0].lower())

    assert len(columns) == len(set(columns))
