from __future__ import annotations

import asyncio

from renaiss_bot.database import event_queries


async def test_event_log_times_out_without_blocking_gameplay(monkeypatch):
    async def stalled_db():
        await asyncio.Event().wait()

    monkeypatch.setenv("RENAISS_EVENT_TIMEOUT_SECONDS", "0.01")
    monkeypatch.setattr(event_queries, "get_db", stalled_db)

    result = await asyncio.wait_for(
        event_queries.log_event("catch_entered", event_key="spawn:test:catch:1"),
        timeout=0.2,
    )

    assert result is False


async def test_event_presence_times_out_as_unknown(monkeypatch):
    async def stalled_db():
        await asyncio.Event().wait()

    monkeypatch.setenv("RENAISS_EVENT_TIMEOUT_SECONDS", "0.01")
    monkeypatch.setattr(event_queries, "get_db", stalled_db)

    result = await asyncio.wait_for(event_queries.event_exists("user:1:first-c"), timeout=0.2)

    assert result is None


async def test_related_events_use_one_pool_checkout_and_one_batch(monkeypatch):
    class Context:
        async def __aenter__(self):
            return connection

        async def __aexit__(self, *args):
            return None

    class Connection:
        def __init__(self):
            self.rows = None

        async def executemany(self, query, rows):
            self.rows = rows

    class Pool:
        def __init__(self):
            self.checkouts = 0

        def acquire(self):
            self.checkouts += 1
            return Context()

    connection = Connection()
    pool = Pool()

    async def get_pool():
        return pool

    monkeypatch.setattr(event_queries, "get_db", get_pool)

    result = await event_queries.log_events(
        [
            {"event_name": "first_c_attempted", "event_key": "user:1:first-c-attempt"},
            {"event_name": "catch_entered", "event_key": "spawn:s:catch:1"},
            {"event_name": "first_c_entered", "event_key": "user:1:first-c"},
        ]
    )

    assert result is True
    assert pool.checkouts == 1
    assert len(connection.rows) == 3
