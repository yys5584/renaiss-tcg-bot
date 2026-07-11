"""Integrated release gate remains strict and stage-aware."""

from __future__ import annotations

from unittest.mock import AsyncMock, Mock

from renaiss_bot.tools.preflight import CheckResult
from renaiss_bot.tools.release_gate import (
    GateResult,
    _preflight_args,
    build_parser,
    execute,
    preflight_run,
)


def test_release_gate_requires_an_explicit_stage():
    parser = build_parser()
    args = parser.parse_args(["--stage", "collection"])
    assert args.stage == "collection"
    assert args.min_eligible == 10
    assert not args.allow_dirty
    assert not args.skip_test_suite


async def test_collection_gate_skips_api_and_pilot_but_checks_catalog(
    monkeypatch,
    capsys,
):
    monkeypatch.setenv("DATABASE_URL", "postgresql://db.example/renaiss")
    monkeypatch.setenv(
        "RENAISS_TELEGRAM_LOCK_DATABASE_URL",
        "postgresql://db-direct.example/renaiss",
    )
    args = build_parser().parse_args(["--stage", "collection"])
    preflight = AsyncMock(return_value=0)
    catalog = AsyncMock(return_value=0)
    pilot = AsyncMock(return_value=0)
    monkeypatch.setattr(
        "renaiss_bot.tools.release_gate._worktree_gate",
        lambda **_: [GateResult("scope", True, "ok")],
    )
    monkeypatch.setattr(
        "renaiss_bot.tools.release_gate._test_gate",
        lambda **_: GateResult("tests", True, "ok"),
    )
    monkeypatch.setattr("renaiss_bot.tools.release_gate.preflight_run", preflight)
    monkeypatch.setattr("renaiss_bot.tools.release_gate.catalog_execute", catalog)
    monkeypatch.setattr("renaiss_bot.tools.release_gate.pilot_execute", pilot)
    monkeypatch.setattr("renaiss_bot.tools.release_gate.close_db", AsyncMock())

    assert await execute(args) == 0
    assert preflight.await_args.args[0].check_api is False
    assert preflight.await_args.args[0].check_telegram_live is True
    assert preflight.await_args.args[0].telegram_timeout == 10.0
    assert preflight.await_args.args[0].require_daily_pick is False
    assert catalog.await_args.kwargs["require_eligible"] == 0
    assert catalog.await_args.kwargs["require_total"] == 10
    assert catalog.await_args.kwargs["require_resolved_source"] == "renaiss_catalog"
    pilot.assert_not_awaited()
    output = capsys.readouterr().out
    assert "Provenance / Runtime interpreter" in output
    assert "Provenance / Runtime root" in output
    assert "Provenance / Runtime env" in output
    assert "Provenance / Partner origin" in output
    assert "postgresql://db.example/renaiss (credentials masked)" in output


async def test_graduation_gate_requires_api_catalog_and_ready_pilot(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://db.example/renaiss")
    monkeypatch.setenv(
        "RENAISS_TELEGRAM_LOCK_DATABASE_URL",
        "postgresql://db-direct.example/renaiss",
    )
    args = build_parser().parse_args(
        [
            "--stage", "graduation",
            "--min-eligible", "20", "--card-name", "Card", "--set-name", "Set",
            "--item-no", "1/1",
        ]
    )
    preflight = AsyncMock(return_value=0)
    catalog = AsyncMock(return_value=0)
    pilot = AsyncMock(return_value=2)
    monkeypatch.setattr(
        "renaiss_bot.tools.release_gate._worktree_gate",
        lambda **_: [GateResult("scope", True, "ok")],
    )
    monkeypatch.setattr(
        "renaiss_bot.tools.release_gate._test_gate",
        lambda **_: GateResult("tests", True, "ok"),
    )
    monkeypatch.setattr("renaiss_bot.tools.release_gate.preflight_run", preflight)
    monkeypatch.setattr("renaiss_bot.tools.release_gate.catalog_execute", catalog)
    monkeypatch.setattr("renaiss_bot.tools.release_gate.pilot_execute", pilot)
    monkeypatch.setattr("renaiss_bot.tools.release_gate.close_db", AsyncMock())

    assert await execute(args) == 1
    assert preflight.await_args.args[0].check_api is True
    assert preflight.await_args.args[0].require_daily_pick is True
    assert catalog.await_args.kwargs["require_eligible"] == 20
    assert catalog.await_args.kwargs["require_total"] == 10
    assert catalog.await_args.kwargs["require_resolved_source"] == "renaiss_catalog"
    assert pilot.await_args.kwargs["require_ready"] is True


async def test_market_gate_cannot_reduce_required_catalog_to_zero(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://db.example/renaiss")
    monkeypatch.setenv(
        "RENAISS_TELEGRAM_LOCK_DATABASE_URL",
        "postgresql://db-direct.example/renaiss",
    )
    args = build_parser().parse_args(
        ["--stage", "market", "--min-eligible", "0"]
    )
    monkeypatch.setattr(
        "renaiss_bot.tools.release_gate._worktree_gate",
        lambda **_: [GateResult("scope", True, "ok")],
    )
    monkeypatch.setattr(
        "renaiss_bot.tools.release_gate._test_gate",
        lambda **_: GateResult("tests", True, "ok"),
    )
    monkeypatch.setattr("renaiss_bot.tools.release_gate.preflight_run", AsyncMock(return_value=0))
    catalog = AsyncMock(return_value=0)
    monkeypatch.setattr("renaiss_bot.tools.release_gate.catalog_execute", catalog)
    monkeypatch.setattr("renaiss_bot.tools.release_gate.close_db", AsyncMock())

    assert await execute(args) == 0
    assert catalog.await_args.kwargs["require_eligible"] == 1
    assert catalog.await_args.kwargs["require_total"] == 10


async def test_release_gate_root_drift_stops_before_subprocess_or_preflight(
    monkeypatch,
    tmp_path,
    capsys,
):
    monkeypatch.setenv("DATABASE_URL", "postgresql://db.example/renaiss")
    monkeypatch.setenv("RENAISS_ROOT", str(tmp_path / "SENTINEL-wrong-root"))
    monkeypatch.delenv("RENAISS_PYTHON_EXE", raising=False)
    monkeypatch.delenv("RENAISS_ENV_FILE", raising=False)
    worktree = Mock(return_value=[])
    test_gate = Mock(return_value=GateResult("tests", True, "ok"))
    preflight = AsyncMock(return_value=0)
    monkeypatch.setattr("renaiss_bot.tools.release_gate._worktree_gate", worktree)
    monkeypatch.setattr("renaiss_bot.tools.release_gate._test_gate", test_gate)
    monkeypatch.setattr("renaiss_bot.tools.release_gate.preflight_run", preflight)
    args = build_parser().parse_args(
        ["--stage", "collection", "--allow-dirty", "--skip-test-suite"]
    )

    assert await execute(args) == 1
    output = capsys.readouterr().out

    worktree.assert_not_called()
    test_gate.assert_not_called()
    preflight.assert_not_awaited()
    assert "RENAISS_ROOT=mismatch" in output
    assert "SENTINEL" not in output


def test_postgres_test_gate_never_falls_back_to_production_database(monkeypatch):
    from renaiss_bot.tools import release_gate

    monkeypatch.delenv("RENAISS_TEST_DATABASE_URL", raising=False)
    monkeypatch.setenv("DATABASE_URL", "postgresql://production.example/renaiss")
    result = release_gate._test_gate(skip_test_suite=False)
    assert result.ok is False
    assert "RENAISS_TEST_DATABASE_URL is required" in result.detail


def test_release_bypass_flags_are_always_nonpassing(monkeypatch):
    from renaiss_bot.tools import release_gate

    monkeypatch.setattr(
        release_gate,
        "_run_command",
        lambda name, command, **kwargs: GateResult(name, True, "ok"),
    )

    scope_results = release_gate._worktree_gate(allow_dirty=True)
    test_result = release_gate._test_gate(skip_test_suite=True)

    assert not next(result for result in scope_results if result.name == "Release scope").ok
    assert "cannot pass" in scope_results[-1].detail
    assert not test_result.ok
    assert "cannot pass" in test_result.detail


async def test_release_execute_cannot_pass_with_inspection_bypasses(
    monkeypatch,
    capsys,
):
    from renaiss_bot.tools import release_gate

    monkeypatch.setenv("DATABASE_URL", "postgresql://db.example/renaiss")
    monkeypatch.setenv(
        "RENAISS_TELEGRAM_LOCK_DATABASE_URL",
        "postgresql://db-direct.example/renaiss",
    )
    monkeypatch.delenv("RENAISS_DB_SSL_INSECURE", raising=False)
    monkeypatch.setattr(
        release_gate,
        "_run_command",
        lambda name, command, **kwargs: GateResult(name, True, "ok"),
    )
    monkeypatch.setattr(release_gate, "preflight_run", AsyncMock(return_value=0))
    monkeypatch.setattr(release_gate, "catalog_execute", AsyncMock(return_value=0))
    monkeypatch.setattr(release_gate, "close_db", AsyncMock())
    args = build_parser().parse_args(
        ["--stage", "collection", "--allow-dirty", "--skip-test-suite"]
    )

    assert await execute(args) == 1
    output = capsys.readouterr().out
    assert output.count("inspection-only and cannot pass a release gate") == 2


def test_postgres_test_gate_overrides_database_url_with_isolated_test_db(monkeypatch):
    from renaiss_bot.tools import release_gate

    monkeypatch.setenv("DATABASE_URL", "postgresql://production.example/renaiss")
    monkeypatch.setenv("RENAISS_TEST_DATABASE_URL", "postgresql://test.example/renaiss")
    monkeypatch.setenv("RENAISS_BOT_TOKEN", "telegram-secret")
    monkeypatch.setenv("RENAISS_API_SECRET", "partner-secret")
    monkeypatch.setenv("POKARD_API_KEY", "pokard-secret")
    monkeypatch.setenv("RENAISS_ENV_FILE", "secret-production.env")
    captured = {}

    def fake_run(name, command, *, env=None):
        captured.update(env or {})
        return GateResult(name, True, "ok")

    monkeypatch.setattr(release_gate, "_run_command", fake_run)
    assert release_gate._test_gate(skip_test_suite=False).ok
    assert captured["DATABASE_URL"] == "postgresql://test.example/renaiss"
    assert captured["RENAISS_TEST_DATABASE_URL"] == "postgresql://test.example/renaiss"
    assert "RENAISS_BOT_TOKEN" not in captured
    assert "RENAISS_API_SECRET" not in captured
    assert "POKARD_API_KEY" not in captured
    assert "RENAISS_ENV_FILE" not in captured


async def test_release_preflight_namespace_matches_real_preflight_contract(monkeypatch):
    args = build_parser().parse_args(
        ["--stage", "collection", "--allow-dirty", "--skip-test-suite"]
    )
    preflight_args = _preflight_args(args)
    preflight_args.skip_db = True
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
    telegram_live = AsyncMock(
        return_value=CheckResult("Telegram live", True, "read-only probe passed")
    )
    monkeypatch.setattr(
        "renaiss_bot.tools.preflight.check_telegram_live",
        telegram_live,
    )

    assert await preflight_run(preflight_args) == 0
    telegram_live.assert_awaited_once_with(timeout_seconds=10.0)
