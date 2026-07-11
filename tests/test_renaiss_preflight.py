"""Read-only deployment preflight tests."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from renaiss_bot.tools.preflight import (
    CheckResult,
    REQUIRED_CHECK_CONSTRAINTS,
    REQUIRED_COLUMN_SHAPES,
    REQUIRED_COLUMNS,
    REQUIRED_CONSTRAINTS,
    REQUIRED_INDEXES,
    REQUIRED_TABLES,
    _check_database_runtime_capabilities,
    build_parser,
    check_database,
    check_exact_price,
    check_telegram_lock_database,
    check_telegram_live,
    run,
    runtime_provenance_checks,
)


class _Acquire:
    def __init__(self, connection):
        self.connection = connection

    async def __aenter__(self):
        return self.connection

    async def __aexit__(self, *args):
        return None


class _Pool:
    def __init__(self, connection):
        self.connection = connection

    def acquire(self):
        return _Acquire(self.connection)


class _Transaction:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return None


def _complete_column_rows():
    rows = []
    for table, columns in REQUIRED_COLUMNS.items():
        for column in columns:
            shape = REQUIRED_COLUMN_SHAPES.get((table, column))
            if shape is None:
                udt_name = "text"
                nullable = (
                    "YES"
                    if (table, column)
                    == ("renaiss_market_price_snapshots", "price_updated_at")
                    else "NO"
                )
                default = None
            else:
                udt_name, nullable, default_terms = shape
                default = " ".join(default_terms) or None
            rows.append(
                {
                    "table_name": table,
                    "column_name": column,
                    "is_nullable": nullable,
                    "udt_name": udt_name,
                    "column_default": default,
                }
            )
    return rows


def _set_daily_pick_gate_env(monkeypatch):
    values = {
        "DATABASE_URL": "postgresql://example.invalid/renaiss",
        "RENAISS_API_KEY": "key",
        "RENAISS_API_SECRET": "secret",
        "RENAISS_API_BASE_URL": "https://api.renaissos.com",
        "RENAISS_API_ALLOWED_HOSTS": "api.renaissos.com",
        "RENAISS_API_KEY_HEADER": "X-Api-Key",
        "RENAISS_API_SECRET_HEADER": "X-Api-Secret",
        "RENAISS_API_ITEM_BY_NO_PATH": "/cards/{item_no}",
        "RENAISS_API_EXACT_CONTRACT": "item-by-no-v1",
        "RENAISS_API_EXACT_VALUATION_METHOD": "median",
        "RENAISS_DAILY_PICK_ENABLED": "1",
        "RENAISS_DAILY_PICK_PROBE_CATEGORY": "pokemon_tcg",
        "RENAISS_DAILY_PICK_PROBE_CARD_NAME": "Probe Card",
        "RENAISS_DAILY_PICK_PROBE_SET_NAME": "Probe Set",
        "RENAISS_DAILY_PICK_PROBE_SET_CODE": "PRS",
        "RENAISS_DAILY_PICK_PROBE_ITEM_NO": "1/1",
        "RENAISS_DAILY_PICK_PROBE_VARIATION": "standard",
        "RENAISS_DAILY_PICK_PROBE_LANGUAGE": "English",
        "RENAISS_DAILY_PICK_PROBE_GRADE": "RAW",
    }
    for name, value in values.items():
        monkeypatch.setenv(name, value)
    monkeypatch.delenv("RENAISS_SKIP_DB", raising=False)
    monkeypatch.delenv("RENAISS_API_MOCK_JSON", raising=False)


def test_runtime_provenance_masks_database_and_partner_credentials(monkeypatch):
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql://operator:SENTINEL-db-secret@db.example:5433/renaiss"
        "?sslkey=SENTINEL-client-key",
    )
    monkeypatch.setenv("RENAISS_API_KEY", "SENTINEL-partner-key")
    monkeypatch.setenv("RENAISS_API_SECRET", "SENTINEL-partner-secret")
    monkeypatch.setenv("RENAISS_API_BASE_URL", "https://api.renaissos.com")
    monkeypatch.setenv("RENAISS_API_ALLOWED_HOSTS", "api.renaissos.com")
    monkeypatch.delenv("RENAISS_PYTHON_EXE", raising=False)
    monkeypatch.delenv("RENAISS_ROOT", raising=False)
    monkeypatch.delenv("RENAISS_ENV_FILE", raising=False)

    results = runtime_provenance_checks(require_database=True)
    database = next(result for result in results if result.name == "Database target")
    partner = next(result for result in results if result.name == "Partner origin")
    rendered = "\n".join(result.detail for result in results)

    assert database.ok
    assert database.detail == (
        "postgresql://db.example:5433/renaiss (credentials masked)"
    )
    assert partner.ok
    assert "https://api.renaissos.com:443" in partner.detail
    assert "SENTINEL" not in rendered
    assert "operator" not in rendered
    assert "sslkey" not in rendered


def test_runtime_provenance_masks_external_env_path(monkeypatch, tmp_path):
    env_file = tmp_path / "SENTINEL-production-secret.env"
    env_file.write_text("# selected\n", encoding="utf-8")
    monkeypatch.setenv("RENAISS_ENV_FILE", str(env_file))
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("RENAISS_PYTHON_EXE", raising=False)
    monkeypatch.delenv("RENAISS_ROOT", raising=False)

    results = runtime_provenance_checks(require_database=False)
    env_result = next(result for result in results if result.name == "Runtime env")

    assert env_result.ok
    assert "selection=explicit" in env_result.detail
    assert "external-path sha256=" in env_result.detail
    assert "SENTINEL" not in env_result.detail


def test_runtime_provenance_rejects_insecure_database_tls_for_release(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://db.example/renaiss")
    monkeypatch.setenv(
        "RENAISS_TELEGRAM_LOCK_DATABASE_URL",
        "postgresql://db-direct.example/renaiss",
    )
    monkeypatch.setenv("RENAISS_DB_SSL_INSECURE", "1")

    results = runtime_provenance_checks(require_database=True)
    tls = next(
        result for result in results if result.name == "Database TLS verification"
    )

    assert not tls.ok
    assert "must be disabled" in tls.detail


async def test_preflight_interpreter_drift_stops_before_database(
    monkeypatch,
    tmp_path,
    capsys,
):
    monkeypatch.setenv("RENAISS_DAILY_PICK_ENABLED", "0")
    monkeypatch.setenv("DATABASE_URL", "postgresql://db.example/renaiss")
    monkeypatch.setenv(
        "RENAISS_PYTHON_EXE",
        str(tmp_path / "SENTINEL-wrong-python"),
    )
    monkeypatch.delenv("RENAISS_ROOT", raising=False)
    monkeypatch.delenv("RENAISS_ENV_FILE", raising=False)
    database_check = AsyncMock(return_value=CheckResult("PostgreSQL", True, "ok"))
    monkeypatch.setattr("renaiss_bot.tools.preflight.check_database", database_check)

    assert await run(build_parser().parse_args([])) == 1
    output = capsys.readouterr().out

    database_check.assert_not_awaited()
    assert "Runtime interpreter" in output
    assert "RENAISS_PYTHON_EXE=mismatch" in output
    assert "SENTINEL" not in output


async def test_database_runtime_gate_rejects_read_only_replica():
    connection = AsyncMock()
    connection.fetchrow.return_value = {
        "writable": False,
        "primary_endpoint": False,
        "current_schema": "public",
        "schema_usage": True,
        "schema_create": True,
    }
    connection.fetch.side_effect = [[], []]

    result = await _check_database_runtime_capabilities(_Pool(connection))

    assert not result.ok
    assert "writable primary" in result.detail


async def test_database_runtime_gate_accepts_complete_role_capabilities():
    connection = AsyncMock()
    connection.fetchrow.return_value = {
        "writable": True,
        "primary_endpoint": True,
        "current_schema": "public",
        "schema_usage": True,
        "schema_create": True,
    }
    connection.fetch.side_effect = [
        [{"name": table, "dml": True, "can_alter": True} for table in REQUIRED_TABLES],
        [{"table_name": "renaiss_events", "column_name": "id", "usable": True}],
    ]

    result = await _check_database_runtime_capabilities(_Pool(connection))

    assert result.ok


async def test_database_runtime_gate_does_not_echo_database_error():
    class FailingPool:
        def acquire(self):
            raise RuntimeError("postgresql://user:secret@example.invalid/db")

    result = await _check_database_runtime_capabilities(FailingPool())

    assert not result.ok
    assert "RuntimeError" in result.detail
    assert "secret" not in result.detail


async def test_telegram_lock_database_proves_shared_lock_domain(monkeypatch):
    contender = SimpleNamespace(
        transaction=Mock(side_effect=lambda: _Transaction()),
        fetchval=AsyncMock(side_effect=[False, True]),
    )
    session = SimpleNamespace(
        transaction=Mock(return_value=_Transaction()),
        fetchrow=AsyncMock(
            return_value={"backend_pid": 9001, "acquired": True}
        ),
        is_closed=Mock(side_effect=[False, True]),
        close=AsyncMock(),
        terminate=Mock(),
    )
    open_session = AsyncMock(return_value=session)
    monkeypatch.setattr(
        "renaiss_bot.tools.preflight.get_db",
        AsyncMock(return_value=_Pool(contender)),
    )
    monkeypatch.setattr(
        "renaiss_bot.tools.preflight.open_db_session",
        open_session,
    )

    result = await check_telegram_lock_database()

    assert result.ok
    assert "same advisory-lock domain observed" in result.detail
    assert "operator verification" in result.detail
    open_session.assert_awaited_once_with(
        dsn_variable="RENAISS_TELEGRAM_LOCK_DATABASE_URL"
    )
    assert "pg_try_advisory_xact_lock" in session.fetchrow.await_args.args[0]
    assert "pg_try_advisory_lock(" not in session.fetchrow.await_args.args[0]
    assert contender.fetchval.await_count == 2
    assert all(
        "pg_try_advisory_xact_lock" in call.args[0]
        for call in contender.fetchval.await_args_list
    )
    session.close.assert_awaited_once_with(timeout=5)
    session.terminate.assert_not_called()


async def test_telegram_lock_database_rejects_different_lock_domain(monkeypatch):
    contender = SimpleNamespace(
        transaction=Mock(return_value=_Transaction()),
        fetchval=AsyncMock(return_value=True),
    )
    session = SimpleNamespace(
        transaction=Mock(return_value=_Transaction()),
        fetchrow=AsyncMock(
            return_value={"backend_pid": 9001, "acquired": True}
        ),
        is_closed=Mock(side_effect=[False, True]),
        close=AsyncMock(),
        terminate=Mock(),
    )
    monkeypatch.setattr(
        "renaiss_bot.tools.preflight.get_db",
        AsyncMock(return_value=_Pool(contender)),
    )
    monkeypatch.setattr(
        "renaiss_bot.tools.preflight.open_db_session",
        AsyncMock(return_value=session),
    )

    result = await check_telegram_lock_database()

    assert not result.ok
    assert "do not demonstrate one advisory-lock domain" in result.detail
    assert contender.fetchval.await_count == 1
    session.close.assert_awaited_once_with(timeout=5)


async def test_telegram_lock_database_redacts_connection_and_cleanup_secrets(
    monkeypatch,
):
    sentinel = "postgresql://operator:SENTINEL-secret@example.invalid/renaiss"
    monkeypatch.setattr(
        "renaiss_bot.tools.preflight.get_db",
        AsyncMock(return_value=_Pool(SimpleNamespace())),
    )
    monkeypatch.setattr(
        "renaiss_bot.tools.preflight.open_db_session",
        AsyncMock(side_effect=RuntimeError(sentinel)),
    )

    result = await check_telegram_lock_database()

    assert not result.ok
    assert "RuntimeError" in result.detail
    assert "SENTINEL" not in result.detail


async def test_telegram_lock_database_terminates_after_secret_close_error(
    monkeypatch,
):
    sentinel = "postgresql://operator:SENTINEL-close-secret@example.invalid/renaiss"
    contender = SimpleNamespace(
        transaction=Mock(side_effect=lambda: _Transaction()),
        fetchval=AsyncMock(side_effect=[False, True]),
    )
    session = SimpleNamespace(
        transaction=Mock(return_value=_Transaction()),
        fetchrow=AsyncMock(
            return_value={"backend_pid": 9001, "acquired": True}
        ),
        is_closed=Mock(return_value=False),
        close=AsyncMock(side_effect=RuntimeError(sentinel)),
        terminate=Mock(),
    )
    monkeypatch.setattr(
        "renaiss_bot.tools.preflight.get_db",
        AsyncMock(return_value=_Pool(contender)),
    )
    monkeypatch.setattr(
        "renaiss_bot.tools.preflight.open_db_session",
        AsyncMock(return_value=session),
    )

    result = await check_telegram_lock_database()

    assert not result.ok
    assert "RuntimeError" in result.detail
    assert "SENTINEL" not in result.detail
    session.terminate.assert_called_once_with()


async def test_preflight_rejects_mock_as_competitive_data(monkeypatch):
    payload = {
        "results": [
            {
                "name": "Charizard",
                "setCode": "BS",
                "cardNumber": "4/102",
                "company": "RAW",
                "grade": "RAW",
                "priceUsdCents": 9500,
                "confidence": "high",
                "confidence_score": 0.9,
                "source_count": 3,
                "href": "https://index.renaissos.com/cards/charizard",
                "price_updated_at": datetime.now(timezone.utc).isoformat(),
            }
        ]
    }
    monkeypatch.setenv("RENAISS_API_MOCK_JSON", json.dumps(payload))
    args = build_parser().parse_args(
        [
            "--skip-db",
            "--check-api",
            "--card-name",
            "Charizard",
            "--set-name",
            "Base Set",
            "--set-code",
            "BS",
            "--item-no",
            "4/102",
        ]
    )
    assert await run(args) == 1


async def test_preflight_rejects_exact_mock_without_freshness(monkeypatch):
    payload = {
        "results": [
            {
                "name": "Charizard",
                "setCode": "BS",
                "cardNumber": "4/102",
                "company": "RAW",
                "grade": "RAW",
                "priceUsdCents": 9500,
                "confidence": "high",
                "confidence_score": 0.9,
                "source_count": 3,
                "href": "https://index.renaissos.com/cards/charizard",
            }
        ]
    }
    monkeypatch.setenv("RENAISS_API_MOCK_JSON", json.dumps(payload))
    args = build_parser().parse_args(
        [
            "--skip-db",
            "--check-api",
            "--card-name",
            "Charizard",
            "--set-name",
            "Base Set",
            "--set-code",
            "BS",
            "--item-no",
            "4/102",
        ]
    )
    assert await run(args) == 1


async def test_preflight_rejects_missing_structural_identity(monkeypatch):
    monkeypatch.setenv("RENAISS_API_MOCK_JSON", "{}")
    args = build_parser().parse_args(["--skip-db", "--check-api"])
    assert await run(args) == 1


async def test_market_preflight_rejects_tuple_drift_before_api(monkeypatch):
    _set_daily_pick_gate_env(monkeypatch)
    api_check = AsyncMock(
        return_value=CheckResult("Renaiss exact price", True, "unexpected")
    )
    monkeypatch.setattr("renaiss_bot.tools.preflight.check_exact_price", api_check)
    args = build_parser().parse_args(
        [
            "--skip-db",
            "--check-api",
            "--require-daily-pick",
            "--card-name",
            "Different Card",
            "--set-name",
            "Probe Set",
            "--set-code",
            "PRS",
            "--item-no",
            "1/1",
            "--variation",
            "standard",
        ]
    )

    assert await run(args) == 1
    api_check.assert_not_awaited()


async def test_market_preflight_uses_same_tuple_as_startup_probe(monkeypatch):
    _set_daily_pick_gate_env(monkeypatch)
    api_check = AsyncMock(
        return_value=CheckResult("Renaiss exact price", True, "verified")
    )
    monkeypatch.setattr("renaiss_bot.tools.preflight.check_exact_price", api_check)
    args = build_parser().parse_args(
        [
            "--skip-db",
            "--check-api",
            "--require-daily-pick",
            "--card-name",
            "Probe Card",
            "--set-name",
            "Probe Set",
            "--set-code",
            "PRS",
            "--item-no",
            "1/1",
            "--variation",
            "standard",
        ]
    )

    assert await run(args) == 0
    api_check.assert_awaited_once_with(args)


async def test_market_preflight_never_sends_credentials_to_env_extended_host(
    monkeypatch,
):
    _set_daily_pick_gate_env(monkeypatch)
    monkeypatch.setenv("RENAISS_API_BASE_URL", "https://evil.example")
    monkeypatch.setenv(
        "RENAISS_API_ALLOWED_HOSTS", "api.renaissos.com,evil.example"
    )
    api_check = AsyncMock(
        return_value=CheckResult("Renaiss exact price", True, "unexpected")
    )
    monkeypatch.setattr("renaiss_bot.tools.preflight.check_exact_price", api_check)
    args = build_parser().parse_args(
        [
            "--skip-db",
            "--check-api",
            "--require-daily-pick",
            "--card-name",
            "Probe Card",
            "--set-name",
            "Probe Set",
            "--set-code",
            "PRS",
            "--item-no",
            "1/1",
            "--variation",
            "standard",
        ]
    )

    assert await run(args) == 1
    api_check.assert_not_awaited()


async def test_core_preflight_does_not_require_optional_daily_pick(monkeypatch):
    monkeypatch.setenv("RENAISS_DAILY_PICK_ENABLED", "0")
    for name in (
        "DATABASE_URL",
        "RENAISS_API_KEY",
        "RENAISS_API_SECRET",
        "RENAISS_API_ITEM_BY_NO_PATH",
        "RENAISS_DAILY_PICK_PROBE_CARD_NAME",
        "RENAISS_DAILY_PICK_PROBE_SET_NAME",
        "RENAISS_DAILY_PICK_PROBE_ITEM_NO",
    ):
        monkeypatch.delenv(name, raising=False)

    args = build_parser().parse_args(["--skip-db"])

    assert await run(args) == 0


async def test_preflight_can_explicitly_require_daily_pick(monkeypatch):
    monkeypatch.setenv("RENAISS_DAILY_PICK_ENABLED", "0")
    for name in (
        "DATABASE_URL",
        "RENAISS_API_KEY",
        "RENAISS_API_SECRET",
        "RENAISS_API_ITEM_BY_NO_PATH",
        "RENAISS_DAILY_PICK_PROBE_CARD_NAME",
        "RENAISS_DAILY_PICK_PROBE_SET_NAME",
        "RENAISS_DAILY_PICK_PROBE_ITEM_NO",
    ):
        monkeypatch.delenv(name, raising=False)

    args = build_parser().parse_args(["--skip-db", "--require-daily-pick"])

    assert await run(args) == 1


async def test_preflight_can_check_telegram_config_without_network(monkeypatch):
    monkeypatch.setenv("RENAISS_DAILY_PICK_ENABLED", "0")
    monkeypatch.setenv("RENAISS_BOT_TOKEN", "123456:test-secret")
    monkeypatch.setenv("RENAISS_EXPECTED_BOT_ID", "123456")
    monkeypatch.setenv("RENAISS_OFFICIAL_CHAT_ID", "-100123456")
    monkeypatch.setenv("DATABASE_URL", "postgresql://example.invalid/renaiss")
    monkeypatch.setenv(
        "RENAISS_TELEGRAM_LOCK_DATABASE_URL",
        "postgresql://direct.example.invalid/renaiss",
    )
    monkeypatch.delenv("RENAISS_SKIP_DB", raising=False)
    monkeypatch.delenv("RENAISS_API_MOCK_JSON", raising=False)
    args = build_parser().parse_args(
        ["--skip-db", "--check-telegram-config"]
    )

    assert await run(args) == 0


async def test_preflight_telegram_config_fails_without_expected_bot_id(monkeypatch):
    monkeypatch.setenv("RENAISS_DAILY_PICK_ENABLED", "0")
    monkeypatch.setenv("RENAISS_BOT_TOKEN", "telegram-token")
    monkeypatch.setenv("RENAISS_OFFICIAL_CHAT_ID", "-100123456")
    monkeypatch.setenv("DATABASE_URL", "postgresql://example.invalid/renaiss")
    monkeypatch.delenv("RENAISS_EXPECTED_BOT_ID", raising=False)
    monkeypatch.delenv("RENAISS_SKIP_DB", raising=False)
    monkeypatch.delenv("RENAISS_API_MOCK_JSON", raising=False)
    args = build_parser().parse_args(
        ["--skip-db", "--check-telegram-config"]
    )

    assert await run(args) == 1


async def test_telegram_live_gate_rejects_missing_settings_without_api_call(monkeypatch):
    for name in (
        "RENAISS_BOT_TOKEN",
        "RENAISS_EXPECTED_BOT_ID",
        "RENAISS_OFFICIAL_CHAT_ID",
    ):
        monkeypatch.delenv(name, raising=False)

    result = await check_telegram_live()

    assert not result.ok
    assert "RENAISS_BOT_TOKEN" in result.detail


async def test_telegram_live_gate_is_read_only_and_sanitizes_success(monkeypatch):
    monkeypatch.setenv("RENAISS_BOT_TOKEN", "123456:test-secret")
    monkeypatch.setenv("RENAISS_EXPECTED_BOT_ID", "123456")
    monkeypatch.setenv("RENAISS_OFFICIAL_CHAT_ID", "-100123456")
    monkeypatch.setenv("RENAISS_DAILY_PICK_ENABLED", "0")

    class FakeBot:
        def __init__(self, *, token):
            assert token == "123456:test-secret"
            self.bot = SimpleNamespace(id=123456)
            self.calls = []

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def get_webhook_info(self):
            self.calls.append(("get_webhook_info",))
            return SimpleNamespace(url="")

        async def get_chat(self, chat_id):
            self.calls.append(("get_chat", chat_id))
            return SimpleNamespace(id=-100123456, type="supergroup")

        async def get_chat_member(self, chat_id, user_id):
            self.calls.append(("get_chat_member", chat_id, user_id))
            return SimpleNamespace(status="administrator")

    monkeypatch.setattr("telegram.Bot", FakeBot)

    result = await check_telegram_live(timeout_seconds=2)

    assert result.ok
    assert "test-secret" not in result.detail
    assert "plain-c" in result.detail


async def test_telegram_live_gate_rejects_migrated_chat_id(monkeypatch):
    monkeypatch.setenv("RENAISS_BOT_TOKEN", "123456:test-secret")
    monkeypatch.setenv("RENAISS_EXPECTED_BOT_ID", "123456")
    monkeypatch.setenv("RENAISS_OFFICIAL_CHAT_ID", "-5436768436")

    class MigratedChatBot:
        def __init__(self, *, token):
            self.bot = SimpleNamespace(
                id=123456,
                can_read_all_group_messages=True,
            )

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def get_webhook_info(self):
            return SimpleNamespace(url="")

        async def get_chat(self, chat_id):
            assert chat_id == -5436768436
            return SimpleNamespace(id=-1000000000042, type="supergroup")

        async def get_chat_member(self, chat_id, user_id):
            return SimpleNamespace(status="administrator")

    monkeypatch.setattr("telegram.Bot", MigratedChatBot)

    result = await check_telegram_live(timeout_seconds=2)

    assert not result.ok
    assert "migrated" in result.detail
    assert "-1000000000042" in result.detail


async def test_telegram_live_gate_does_not_echo_provider_error(monkeypatch):
    monkeypatch.setenv("RENAISS_BOT_TOKEN", "123456:test-secret")
    monkeypatch.setenv("RENAISS_EXPECTED_BOT_ID", "123456")
    monkeypatch.setenv("RENAISS_OFFICIAL_CHAT_ID", "-100123456")

    class FailingBot:
        def __init__(self, *, token):
            self.bot = SimpleNamespace(id=123456)

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def get_webhook_info(self):
            return SimpleNamespace(url="")

        async def get_chat(self, chat_id):
            raise RuntimeError("test-secret must never be printed")

        async def get_chat_member(self, chat_id, user_id):
            raise AssertionError("unreachable")

    monkeypatch.setattr("telegram.Bot", FailingBot)

    result = await check_telegram_live(timeout_seconds=2)

    assert not result.ok
    assert "RuntimeError" in result.detail
    assert "test-secret" not in result.detail


async def test_telegram_live_gate_requires_admin_for_daily_pick(monkeypatch):
    monkeypatch.setenv("RENAISS_BOT_TOKEN", "123456:test-secret")
    monkeypatch.setenv("RENAISS_EXPECTED_BOT_ID", "123456")
    monkeypatch.setenv("RENAISS_OFFICIAL_CHAT_ID", "-100123456")
    monkeypatch.setenv("RENAISS_DAILY_PICK_ENABLED", "1")

    class MemberBot:
        def __init__(self, *, token):
            self.bot = SimpleNamespace(id=123456)

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def get_webhook_info(self):
            return SimpleNamespace(url="")

        async def get_chat(self, chat_id):
            return SimpleNamespace(id=-100123456, type="supergroup")

        async def get_chat_member(self, chat_id, user_id):
            return SimpleNamespace(status="member")

    monkeypatch.setattr("telegram.Bot", MemberBot)

    result = await check_telegram_live(timeout_seconds=2)

    assert not result.ok
    assert "administrator" in result.detail


async def test_telegram_live_gate_accepts_botfather_privacy_disabled(monkeypatch):
    monkeypatch.setenv("RENAISS_BOT_TOKEN", "123456:test-secret")
    monkeypatch.setenv("RENAISS_EXPECTED_BOT_ID", "123456")
    monkeypatch.setenv("RENAISS_OFFICIAL_CHAT_ID", "-100123456")

    class PrivacyDisabledBot:
        def __init__(self, *, token):
            self.bot = SimpleNamespace(
                id=123456,
                can_read_all_group_messages=True,
            )

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def get_webhook_info(self):
            return SimpleNamespace(url="")

        async def get_chat(self, chat_id):
            return SimpleNamespace(id=-100123456, type="supergroup")

        async def get_chat_member(self, chat_id, user_id):
            return SimpleNamespace(status="member")

    monkeypatch.setattr("telegram.Bot", PrivacyDisabledBot)

    result = await check_telegram_live(timeout_seconds=2)

    assert result.ok
    assert "plain-c visibility verified" in result.detail


async def test_telegram_live_gate_short_circuits_wrong_identity(monkeypatch):
    monkeypatch.setenv("RENAISS_BOT_TOKEN", "123456:test-secret")
    monkeypatch.setenv("RENAISS_EXPECTED_BOT_ID", "123456")
    monkeypatch.setenv("RENAISS_OFFICIAL_CHAT_ID", "-100123456")

    class WrongBot:
        def __init__(self, *, token):
            self.bot = SimpleNamespace(id=999999)

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def get_webhook_info(self):
            raise AssertionError("identity mismatch must short-circuit")

        async def get_chat(self, chat_id):
            raise AssertionError("identity mismatch must short-circuit")

        async def get_chat_member(self, chat_id, user_id):
            raise AssertionError("identity mismatch must short-circuit")

    monkeypatch.setattr("telegram.Bot", WrongBot)

    result = await check_telegram_live(timeout_seconds=2)

    assert not result.ok
    assert "getMe identity" in result.detail


async def test_telegram_live_gate_rejects_existing_webhook_before_chat(monkeypatch):
    monkeypatch.setenv("RENAISS_BOT_TOKEN", "123456:test-secret")
    monkeypatch.setenv("RENAISS_EXPECTED_BOT_ID", "123456")
    monkeypatch.setenv("RENAISS_OFFICIAL_CHAT_ID", "-100123456")

    class WebhookBot:
        def __init__(self, *, token):
            self.bot = SimpleNamespace(id=123456)

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def get_webhook_info(self):
            return SimpleNamespace(url="https://example.invalid/secret-hook")

        async def get_chat(self, chat_id):
            raise AssertionError("webhook conflict must short-circuit")

        async def get_chat_member(self, chat_id, user_id):
            raise AssertionError("webhook conflict must short-circuit")

    monkeypatch.setattr("telegram.Bot", WebhookBot)

    result = await check_telegram_live(timeout_seconds=2)

    assert not result.ok
    assert "webhook" in result.detail
    assert "secret-hook" not in result.detail


async def test_database_preflight_rejects_missing_settlement_migration(monkeypatch):
    connection = AsyncMock()
    connection.fetch.side_effect = [
        [{"name": table, "present": True} for table in REQUIRED_TABLES],
        [],
    ]
    monkeypatch.setattr(
        "renaiss_bot.tools.preflight.get_db",
        AsyncMock(return_value=_Pool(connection)),
    )

    result = await check_database()

    assert not result.ok
    assert "renaiss_market_picks.settlement_snapshot_id" in result.detail
    assert "renaiss_market_picks.settled_at" in result.detail


async def test_database_preflight_does_not_echo_connection_secret(monkeypatch):
    monkeypatch.setattr(
        "renaiss_bot.tools.preflight.get_db",
        AsyncMock(
            side_effect=RuntimeError(
                "postgresql://user:secret-value@example.invalid/renaiss"
            )
        ),
    )

    result = await check_database()

    assert not result.ok
    assert "RuntimeError" in result.detail
    assert "secret-value" not in result.detail


async def test_partner_preflight_does_not_echo_provider_secret(monkeypatch):
    monkeypatch.setattr(
        "renaiss_bot.tools.preflight.fetch_official_price",
        AsyncMock(side_effect=RuntimeError("X-Api-Secret: secret-value")),
    )
    args = build_parser().parse_args(
        [
            "--skip-db",
            "--card-name",
            "Probe Card",
            "--set-name",
            "Probe Set",
            "--item-no",
            "1/1",
        ]
    )

    result = await check_exact_price(args)

    assert not result.ok
    assert "RuntimeError" in result.detail
    assert "secret-value" not in result.detail


async def test_database_preflight_rejects_wrong_index_contract(monkeypatch):
    connection = AsyncMock()
    column_rows = _complete_column_rows()
    connection.fetch.side_effect = [
        [{"name": table, "present": True} for table in REQUIRED_TABLES],
        column_rows,
    ]
    index_rows = []
    for name, contract in REQUIRED_INDEXES.items():
        index_rows.append(
            {
                "table_name": contract["table"],
                "is_unique": False
                if name == "idx_renaiss_pack_events_request"
                else contract["unique"],
                "is_valid": True,
                "is_ready": True,
                "definition": "CREATE INDEX "
                + name
                + " ON public."
                + contract["table"]
                + " USING btree ("
                + ", ".join(contract["columns"])
                + ")",
                "predicate": " ".join(contract["predicate_terms"]),
            }
        )
    connection.fetchrow.side_effect = index_rows
    connection.fetchval.return_value = 1
    monkeypatch.setattr(
        "renaiss_bot.tools.preflight.get_db",
        AsyncMock(return_value=_Pool(connection)),
    )

    result = await check_database()

    assert not result.ok
    assert "incompatible migration indexes" in result.detail
    assert "idx_renaiss_pack_events_request" in result.detail


async def test_database_preflight_rejects_missing_idempotency_constraint(monkeypatch):
    connection = AsyncMock()
    connection.fetch.side_effect = [
        [{"name": table, "present": True} for table in REQUIRED_TABLES],
        _complete_column_rows(),
    ]
    connection.fetchrow.side_effect = [
        {
            "table_name": contract["table"],
            "is_unique": contract["unique"],
            "is_valid": True,
            "is_ready": True,
            "definition": "CREATE INDEX "
            + name
            + " ON public."
            + contract["table"]
            + " USING btree ("
            + ", ".join(contract["columns"])
            + ")",
            "predicate": " ".join(contract["predicate_terms"]),
        }
        for name, contract in REQUIRED_INDEXES.items()
    ]
    connection.fetchval.side_effect = [
        *[
            label != "pack request primary key"
            for label in REQUIRED_CONSTRAINTS
        ],
        *([True] * len(REQUIRED_CHECK_CONSTRAINTS)),
        1,
    ]
    monkeypatch.setattr(
        "renaiss_bot.tools.preflight.get_db",
        AsyncMock(return_value=_Pool(connection)),
    )

    result = await check_database()

    assert not result.ok
    assert "missing migration constraints" in result.detail
    assert "pack request primary key" in result.detail


async def test_database_preflight_rejects_incompatible_legacy_column_type(monkeypatch):
    connection = AsyncMock()
    column_rows = _complete_column_rows()
    user_id = next(
        row
        for row in column_rows
        if row["table_name"] == "renaiss_user_cards"
        and row["column_name"] == "user_id"
    )
    user_id["udt_name"] = "text"
    connection.fetch.side_effect = [
        [{"name": table, "present": True} for table in REQUIRED_TABLES],
        column_rows,
    ]
    connection.fetchrow.return_value = None
    connection.fetchval.return_value = True
    monkeypatch.setattr(
        "renaiss_bot.tools.preflight.get_db",
        AsyncMock(return_value=_Pool(connection)),
    )

    result = await check_database()

    assert not result.ok
    assert "incompatible migration columns" in result.detail
    assert "renaiss_user_cards.user_id" in result.detail


async def test_database_preflight_rejects_unexpected_row_level_security(monkeypatch):
    connection = AsyncMock()
    table_rows = [
        {
            "name": table,
            "present": True,
            "rls_enabled": table == "renaiss_user_cards",
        }
        for table in REQUIRED_TABLES
    ]
    connection.fetch.side_effect = [table_rows, []]
    monkeypatch.setattr(
        "renaiss_bot.tools.preflight.get_db",
        AsyncMock(return_value=_Pool(connection)),
    )

    result = await check_database()

    assert not result.ok
    assert "row-level security" in result.detail
    assert "renaiss_user_cards" in result.detail
