"""PostgreSQL session-lock primitives for one active service process."""

from __future__ import annotations

from typing import Any


def _validate_lock_name(lock_name: str) -> None:
    if not lock_name or len(lock_name) > 128:
        raise ValueError("invalid instance lock name")


async def acquire_instance_lock(connection: Any, *, lock_name: str) -> bool:
    """Try to hold one advisory lock for the lifetime of ``connection``."""
    _validate_lock_name(lock_name)
    acquired = await connection.fetchval(
        "SELECT pg_try_advisory_lock(hashtextextended($1, 0))",
        lock_name,
    )
    return bool(acquired)


async def release_instance_lock(connection: Any, *, lock_name: str) -> bool:
    """Release one session lock count from the current connection only."""
    _validate_lock_name(lock_name)
    released = await connection.fetchval(
        "SELECT pg_advisory_unlock(hashtextextended($1, 0))",
        lock_name,
    )
    return bool(released)


async def instance_lock_owner_backend_pid(
    connection: Any,
    *,
    lock_name: str,
) -> int | None:
    """Return this connection's backend PID only while it owns the lock."""
    _validate_lock_name(lock_name)
    value = await connection.fetchval(
        """
        WITH expected AS (
            SELECT hashtextextended($1, 0) AS lock_key
        )
        SELECT held.pid
        FROM pg_locks AS held
        CROSS JOIN expected
        WHERE held.locktype = 'advisory'
          AND held.pid = pg_backend_pid()
          AND held.granted
          AND held.objsubid = 1
          AND held.classid::bigint =
              ((expected.lock_key >> 32) & 4294967295::bigint)
          AND held.objid::bigint =
              (expected.lock_key & 4294967295::bigint)
        LIMIT 1
        """,
        lock_name,
    )
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        return None
    return value


async def probe_instance_lock_session(
    connection: Any,
    *,
    lock_name: str,
    expected_backend_pid: int | None = None,
) -> bool:
    """Confirm the original backend still owns the named advisory lock."""
    owner_pid = await instance_lock_owner_backend_pid(
        connection,
        lock_name=lock_name,
    )
    return owner_pid is not None and (
        expected_backend_pid is None or owner_pid == expected_backend_pid
    )


async def probe_instance_lock_contender(connection: Any, *, lock_name: str) -> bool:
    """Return true only when this database sees another session's lock.

    A transaction-scoped contender is used so a wrong database target cannot
    leak a session advisory lock while startup is being refused.
    """
    _validate_lock_name(lock_name)
    acquired = await connection.fetchval(
        "SELECT pg_try_advisory_xact_lock(hashtextextended($1, 0))",
        lock_name,
    )
    return not bool(acquired)
