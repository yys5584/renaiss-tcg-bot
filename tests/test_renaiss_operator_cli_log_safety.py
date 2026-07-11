"""Operator CLIs must not print credential-bearing exception bodies."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import AsyncMock

from renaiss_bot.tools import (
    catalog_audit,
    manifest_preflight,
    pilot_report,
    probe_index,
    release_gate,
)


SENTINEL = "SENTINEL-database-or-partner-secret\r\nFORGED-STATUS-LINE"


def _assert_redacted(output: str) -> None:
    assert "SENTINEL-database-or-partner-secret" not in output
    assert "FORGED-STATUS-LINE" not in output
    assert "RuntimeError" in output


async def _raise_secret(*args, **kwargs):
    raise RuntimeError(SENTINEL)


async def _return_none(*args, **kwargs):
    return None


async def test_catalog_audit_redacts_query_failure(monkeypatch, capsys):
    monkeypatch.setattr(catalog_audit, "load_catalog_cards", _raise_secret)
    monkeypatch.setattr(catalog_audit, "close_db", _return_none)

    code = await catalog_audit.execute(
        category="pokemon_tcg",
        limit=10,
        require_eligible=0,
        pool_source="catalog",
    )

    assert code == 1
    _assert_redacted(capsys.readouterr().out)


async def test_catalog_audit_redacts_cleanup_failure_and_fails_gate(
    monkeypatch,
    capsys,
):
    monkeypatch.setattr(catalog_audit, "load_catalog_cards", AsyncMock(return_value=[]))
    monkeypatch.setattr(
        catalog_audit,
        "load_catalog_identity_summary",
        AsyncMock(return_value={"legacy_id_count": 0, "duplicate_identity_groups": 0}),
    )
    monkeypatch.setattr(catalog_audit, "close_db", _raise_secret)

    code = await catalog_audit.execute(
        category="pokemon_tcg",
        limit=10,
        require_eligible=0,
        pool_source="catalog",
    )

    assert code == 1
    _assert_redacted(capsys.readouterr().out)


def test_manifest_preflight_redacts_signature_or_file_failure(monkeypatch, capsys):
    monkeypatch.setattr(Path, "read_bytes", lambda self: b"[]")
    monkeypatch.setattr(Path, "read_text", lambda self, **kwargs: "signature")
    monkeypatch.setattr(manifest_preflight, "verify_manifest", lambda *a, **k: (_ for _ in ()).throw(RuntimeError(SENTINEL)))

    code = manifest_preflight.run(
        path=Path("manifest.json"),
        signature_path=Path("manifest.sig"),
        public_key_path=Path("manifest.pem"),
        category="pokemon_tcg",
        require_eligible=0,
    )

    assert code == 1
    _assert_redacted(capsys.readouterr().out)


async def test_pilot_report_redacts_query_and_cleanup_failures(monkeypatch, capsys):
    monkeypatch.setattr(pilot_report, "load_report", _raise_secret)
    monkeypatch.setattr(pilot_report, "close_db", _raise_secret)

    assert await pilot_report.execute(14) == 1
    output = capsys.readouterr().out
    _assert_redacted(output)
    assert "DB cleanup failed" in output


def test_release_gate_redacts_subprocess_spawn_failure(monkeypatch):
    monkeypatch.setattr(
        release_gate.subprocess,
        "run",
        lambda *args, **kwargs: (_ for _ in ()).throw(OSError(SENTINEL)),
    )

    result = release_gate._run_command("test", ["fixed-command"])

    assert result.ok is False
    assert "OSError" in result.detail
    assert "SENTINEL" not in result.detail
    assert "FORGED-STATUS-LINE" not in result.detail


async def test_release_gate_redacts_internal_stage_and_cleanup_failures(
    monkeypatch,
    capsys,
):
    monkeypatch.setenv("DATABASE_URL", "postgresql://db.example/renaiss")
    monkeypatch.setenv(
        "RENAISS_TELEGRAM_LOCK_DATABASE_URL",
        "postgresql://direct-db.example/renaiss",
    )
    args = release_gate.build_parser().parse_args(
        ["--stage", "collection", "--allow-dirty", "--skip-test-suite"]
    )
    monkeypatch.setattr(release_gate, "_worktree_gate", lambda **kwargs: [])
    monkeypatch.setattr(
        release_gate,
        "_test_gate",
        lambda **kwargs: release_gate.GateResult("tests", True, "ok"),
    )
    monkeypatch.setattr(release_gate, "preflight_run", _raise_secret)
    monkeypatch.setattr(release_gate, "close_db", _raise_secret)
    monkeypatch.setattr(release_gate, "catalog_execute", _raise_secret)

    assert await release_gate.execute(args) == 1
    _assert_redacted(capsys.readouterr().out)


async def test_probe_index_redacts_partner_request_failure(monkeypatch, capsys):
    monkeypatch.setattr(probe_index, "load_runtime_environment", lambda: None)
    monkeypatch.setattr(probe_index, "probe", _raise_secret)
    monkeypatch.setattr(sys, "argv", ["probe_index", "Charizard"])

    await probe_index.main()

    _assert_redacted(capsys.readouterr().out)
