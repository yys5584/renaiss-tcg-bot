"""Optional isolated PostgreSQL harness for concurrency and DDL smoke tests."""

from __future__ import annotations

import asyncio
import os
import subprocess
import uuid

import asyncpg
import pytest

from renaiss_bot.tools.prepare_database import database_targets_match


def pytest_addoption(parser):
    parser.addoption(
        "--postgres-integration",
        action="store_true",
        default=False,
        help="run isolated PostgreSQL integration tests",
    )


def _require_postgres() -> bool:
    return os.getenv("RENAISS_REQUIRE_POSTGRES_TESTS", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _skip_or_fail(message: str):
    if _require_postgres():
        pytest.fail(message)
    pytest.skip(message)


def _docker_available() -> bool:
    try:
        result = subprocess.run(
            ["docker", "info", "--format", "{{.ServerVersion}}"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (FileNotFoundError, subprocess.SubprocessError):
        return False
    return result.returncode == 0 and bool(result.stdout.strip())


async def _wait_for_postgres(dsn: str, *, attempts: int = 40) -> None:
    last_error = None
    for _ in range(attempts):
        try:
            conn = await asyncpg.connect(dsn, ssl=False, timeout=2)
        except Exception as exc:  # readiness necessarily spans connection failures
            last_error = exc
            await asyncio.sleep(0.5)
        else:
            await conn.close()
            return
    raise RuntimeError(f"PostgreSQL did not become ready: {last_error}")


@pytest.fixture
async def postgres_pool(request, monkeypatch):
    if not request.config.getoption("--postgres-integration"):
        _skip_or_fail("use --postgres-integration to run real PostgreSQL tests")

    dsn = os.getenv("RENAISS_TEST_DATABASE_URL", "").strip()
    container_name = None
    if not dsn:
        if not _docker_available():
            _skip_or_fail(
                "RENAISS_TEST_DATABASE_URL is unset and the Docker daemon is unavailable"
            )
        container_name = f"renaiss-pg-test-{uuid.uuid4().hex[:10]}"
        password = uuid.uuid4().hex
        result = subprocess.run(
            [
                "docker",
                "run",
                "--rm",
                "-d",
                "--name",
                container_name,
                "-e",
                f"POSTGRES_PASSWORD={password}",
                "-e",
                "POSTGRES_DB=renaiss_test",
                "-p",
                "127.0.0.1::5432",
                "postgres:16-alpine",
            ],
            capture_output=True,
            text=True,
            timeout=180,
            check=False,
        )
        if result.returncode != 0:
            _skip_or_fail(f"could not start PostgreSQL test container: {result.stderr.strip()}")
        port_result = subprocess.run(
            ["docker", "port", container_name, "5432/tcp"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        if port_result.returncode != 0:
            subprocess.run(["docker", "rm", "-f", container_name], capture_output=True)
            _skip_or_fail("could not resolve PostgreSQL test container port")
        port = port_result.stdout.strip().rsplit(":", 1)[-1]
        dsn = f"postgresql://postgres:{password}@127.0.0.1:{port}/renaiss_test"

    for production_name in ("DATABASE_URL", "RENAISS_TELEGRAM_LOCK_DATABASE_URL"):
        production_dsn = os.getenv(production_name, "").strip()
        if production_dsn and database_targets_match(dsn, production_dsn):
            _skip_or_fail(
                f"RENAISS_TEST_DATABASE_URL must not target the same database as {production_name}"
            )

    schema_name = f"renaiss_test_{uuid.uuid4().hex}"
    admin = None
    pool = None
    try:
        await _wait_for_postgres(dsn)
        admin = await asyncpg.connect(dsn, ssl=False)
        await admin.execute(f'CREATE SCHEMA "{schema_name}"')
        pool = await asyncpg.create_pool(
            dsn,
            min_size=1,
            max_size=12,
            ssl=False,
            server_settings={"search_path": schema_name},
        )

        from renaiss_bot.database.schema import create_tables
        import renaiss_bot.database.api_queries as api_queries
        import renaiss_bot.database.catalog_queries as catalog_queries
        import renaiss_bot.database.event_queries as event_queries
        import renaiss_bot.database.market_queries as market_queries
        import renaiss_bot.database.queries as queries

        await create_tables(pool)

        async def get_test_db():
            return pool

        monkeypatch.setattr(queries, "get_db", get_test_db)
        monkeypatch.setattr(market_queries, "get_db", get_test_db)
        monkeypatch.setattr(api_queries, "get_db", get_test_db)
        monkeypatch.setattr(event_queries, "get_db", get_test_db)
        monkeypatch.setattr(catalog_queries, "get_db", get_test_db)
        yield pool
    finally:
        if pool is not None:
            await pool.close()
        if admin is not None:
            await admin.execute(f'DROP SCHEMA IF EXISTS "{schema_name}" CASCADE')
            await admin.close()
        if container_name:
            subprocess.run(
                ["docker", "rm", "-f", container_name],
                capture_output=True,
                timeout=30,
                check=False,
            )
