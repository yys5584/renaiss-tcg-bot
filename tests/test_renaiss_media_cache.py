from __future__ import annotations

from types import SimpleNamespace

from renaiss_bot.services import media_cache


class _Context:
    def __init__(self, value):
        self.value = value

    async def __aenter__(self):
        return self.value

    async def __aexit__(self, *args):
        return None


class _Connection:
    def __init__(self):
        self.file_id = None
        self.execute_args = None

    async def fetchval(self, query, render_key):
        return self.file_id

    async def execute(self, query, *args):
        self.execute_args = args


class _Pool:
    def __init__(self, connection):
        self.connection = connection

    def acquire(self):
        return _Context(self.connection)


async def test_telegram_file_id_cache_round_trip(monkeypatch):
    connection = _Connection()
    pool = _Pool(connection)

    async def get_pool():
        return pool

    monkeypatch.setattr(media_cache, "get_db", get_pool)
    render_key = "a" * 64

    assert await media_cache.store_telegram_file_id(render_key, "telegram-file", "unique")
    assert connection.execute_args == (render_key, "telegram-file", "unique")
    connection.file_id = "telegram-file"
    assert await media_cache.get_telegram_file_id(render_key) == "telegram-file"


async def test_remember_telegram_photo_uses_largest_size(monkeypatch):
    stored = []

    async def store(*args):
        stored.append(args)
        return True

    monkeypatch.setattr(media_cache, "store_telegram_file_id", store)
    message = SimpleNamespace(
        photo=[
            SimpleNamespace(file_id="small", file_unique_id="same"),
            SimpleNamespace(file_id="large", file_unique_id="same"),
        ]
    )

    assert await media_cache.remember_telegram_photo("b" * 64, message)
    assert stored == [("b" * 64, "large", "same")]


async def test_media_cache_rejects_unbounded_identifiers():
    assert await media_cache.get_telegram_file_id("not-a-hash") is None
    assert not await media_cache.store_telegram_file_id("c" * 64, "x" * 513)
