"""The schema preparation command must be explicit and fail closed."""

from __future__ import annotations

from unittest.mock import AsyncMock, Mock

import pytest

from renaiss_bot.tools import prepare_database


def _production_like_env(monkeypatch) -> None:
    dsn = "postgresql://operator:secret@db/renaiss"
    monkeypatch.setenv("DATABASE_URL", dsn)
    monkeypatch.setenv(
        "RENAISS_EXPECTED_DATABASE_FINGERPRINT",
        prepare_database.database_target_fingerprint(dsn),
    )
    monkeypatch.delenv("RENAISS_SKIP_DB", raising=False)
    monkeypatch.delenv("RENAISS_API_MOCK_JSON", raising=False)


async def test_prepare_requires_explicit_apply_before_database_access(
    monkeypatch,
    capsys,
):
    _production_like_env(monkeypatch)
    get_db = AsyncMock()
    monkeypatch.setattr(prepare_database, "get_db", get_db)

    assert await prepare_database.prepare_database(apply=False) == 2

    get_db.assert_not_awaited()
    assert "--apply is required" in capsys.readouterr().out


@pytest.mark.parametrize(
    ("name", "value", "expected"),
    [
        ("RENAISS_SKIP_DB", "1", "RENAISS_SKIP_DB must be disabled"),
        ("RENAISS_API_MOCK_JSON", "secret mock payload", "must be unset"),
    ],
)
async def test_prepare_rejects_mock_or_skip_modes_before_database_access(
    monkeypatch,
    capsys,
    name,
    value,
    expected,
):
    _production_like_env(monkeypatch)
    monkeypatch.setenv(name, value)
    get_db = AsyncMock()
    monkeypatch.setattr(prepare_database, "get_db", get_db)

    assert await prepare_database.prepare_database(apply=True) == 2

    get_db.assert_not_awaited()
    output = capsys.readouterr().out
    assert expected in output
    assert "secret mock payload" not in output


async def test_prepare_requires_matching_non_secret_database_target_fingerprint(
    monkeypatch,
    capsys,
):
    _production_like_env(monkeypatch)
    get_db = AsyncMock()
    monkeypatch.setattr(prepare_database, "get_db", get_db)

    monkeypatch.delenv("RENAISS_EXPECTED_DATABASE_FINGERPRINT")
    assert await prepare_database.prepare_database(apply=True) == 2
    assert "RENAISS_EXPECTED_DATABASE_FINGERPRINT is missing" in capsys.readouterr().out

    monkeypatch.setenv("RENAISS_EXPECTED_DATABASE_FINGERPRINT", "0" * 64)
    assert await prepare_database.prepare_database(apply=True) == 2
    assert "does not match" in capsys.readouterr().out
    get_db.assert_not_awaited()


def test_database_target_fingerprint_excludes_credentials_and_normalizes_default_port():
    first = prepare_database.database_target_fingerprint(
        "postgresql://alice:first-secret@DB.EXAMPLE/renaiss?sslmode=require"
    )
    second = prepare_database.database_target_fingerprint(
        "postgres://bob:second-secret@db.example:5432/renaiss"
    )

    assert first == second
    assert "first-secret" not in first
    assert len(first) == 64
    assert prepare_database.database_targets_match(
        "postgresql://one:a@db.example/renaiss",
        "postgresql://two:b@DB.EXAMPLE:5432/renaiss?sslmode=require",
    )
    assert not prepare_database.database_targets_match(
        "postgresql://one:a@db.example/renaiss",
        "postgresql://one:a@db.example/renaiss_test",
    )


def test_print_target_fingerprint_does_not_apply_schema(monkeypatch, capsys):
    dsn = "postgresql://operator:secret@db/renaiss"
    monkeypatch.setenv("DATABASE_URL", dsn)
    monkeypatch.setattr(prepare_database, "load_runtime_environment", Mock())

    with pytest.raises(SystemExit) as exc_info:
        prepare_database.main(["--print-target-fingerprint"])

    assert exc_info.value.code == 0
    assert capsys.readouterr().out.strip() == prepare_database.database_target_fingerprint(dsn)


async def test_prepare_applies_existing_schema_function_and_closes_pool(
    monkeypatch,
    capsys,
):
    _production_like_env(monkeypatch)
    pool = object()
    get_db = AsyncMock(return_value=pool)
    create_tables = AsyncMock()
    close_db = AsyncMock()
    monkeypatch.setattr(prepare_database, "get_db", get_db)
    monkeypatch.setattr(prepare_database, "create_tables", create_tables)
    monkeypatch.setattr(prepare_database, "close_db", close_db)

    assert await prepare_database.prepare_database(apply=True) == 0

    get_db.assert_awaited_once_with()
    create_tables.assert_awaited_once_with(pool)
    close_db.assert_awaited_once_with()
    assert "[PASS]" in capsys.readouterr().out


async def test_prepare_closes_pool_and_never_echoes_provider_exception(
    monkeypatch,
    capsys,
):
    _production_like_env(monkeypatch)
    secret = "postgresql://operator:do-not-print@db/renaiss"
    monkeypatch.setattr(
        prepare_database,
        "get_db",
        AsyncMock(side_effect=RuntimeError(secret)),
    )
    close_db = AsyncMock()
    monkeypatch.setattr(prepare_database, "close_db", close_db)

    assert await prepare_database.prepare_database(apply=True) == 1

    close_db.assert_awaited_once_with()
    output = capsys.readouterr().out
    assert "RuntimeError" in output
    assert secret not in output
    assert "do-not-print" not in output


def test_main_loads_canonical_environment_and_redacts_load_failure(
    monkeypatch,
    capsys,
):
    load_environment = Mock(
        side_effect=RuntimeError("DATABASE_URL=postgresql://user:do-not-print@db")
    )
    monkeypatch.setattr(
        prepare_database,
        "load_runtime_environment",
        load_environment,
    )

    with pytest.raises(SystemExit) as exc_info:
        prepare_database.main(["--apply"])

    assert exc_info.value.code == 2
    load_environment.assert_called_once_with()
    output = capsys.readouterr().out
    assert "RuntimeError" in output
    assert "do-not-print" not in output
