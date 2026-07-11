"""Standalone runtime paths must never depend on an adjacent repository or cwd."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import Mock

import pytest

import renaiss_bot.runtime as runtime


def test_runtime_env_defaults_to_standalone_root_from_any_cwd(monkeypatch, tmp_path):
    monkeypatch.delenv("RENAISS_ENV_FILE", raising=False)
    monkeypatch.chdir(tmp_path)

    assert runtime.runtime_env_path() == runtime.PROJECT_ROOT / ".env"


def test_relative_runtime_env_override_is_resolved_from_standalone_root(monkeypatch):
    monkeypatch.setenv("RENAISS_ENV_FILE", "ops/renaiss.production.env")

    assert runtime.runtime_env_path() == (
        runtime.PROJECT_ROOT / "ops" / "renaiss.production.env"
    ).resolve()


def test_runtime_environment_preserves_service_injected_values(monkeypatch):
    monkeypatch.delenv("RENAISS_ENV_FILE", raising=False)
    loader = Mock()
    monkeypatch.setattr(runtime, "load_dotenv", loader)

    selected = runtime.load_runtime_environment()

    assert selected == runtime.PROJECT_ROOT / ".env"
    loader.assert_called_once_with(dotenv_path=selected, override=False)


def test_explicit_runtime_env_must_be_loadable(monkeypatch, tmp_path):
    missing = tmp_path / "missing.env"
    monkeypatch.setenv("RENAISS_ENV_FILE", str(missing))

    with pytest.raises(RuntimeError, match="could not be loaded"):
        runtime.load_runtime_environment()


def test_explicit_runtime_env_loads_without_overriding_service_values(
    monkeypatch,
    tmp_path,
):
    env_file = tmp_path / "runtime.env"
    env_file.write_text(
        "RENAISS_RUNTIME_TEST_VALUE=from-file\n"
        "RENAISS_RUNTIME_TEST_INJECTED=from-file\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("RENAISS_ENV_FILE", str(env_file))
    monkeypatch.setenv("RENAISS_RUNTIME_TEST_INJECTED", "from-service")
    monkeypatch.delenv("RENAISS_RUNTIME_TEST_VALUE", raising=False)

    runtime.load_runtime_environment()

    assert os.environ["RENAISS_RUNTIME_TEST_VALUE"] == "from-file"
    assert os.environ["RENAISS_RUNTIME_TEST_INJECTED"] == "from-service"


def test_importing_entrypoints_does_not_load_runtime_env(tmp_path):
    env_file = tmp_path / "import.env"
    env_file.write_text("RENAISS_IMPORT_SENTINEL=loaded\n", encoding="utf-8")
    env = os.environ.copy()
    env["RENAISS_ENV_FILE"] = str(env_file)
    env.pop("RENAISS_IMPORT_SENTINEL", None)
    code = (
        "import os; "
        "import renaiss_bot.main; "
        "import renaiss_bot.adapters.discord.main; "
        "import renaiss_bot.referral_server; "
        "print(os.getenv('RENAISS_IMPORT_SENTINEL', 'missing'))"
    )

    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=runtime.PROJECT_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
        check=True,
    )

    assert result.stdout.strip() == "missing"


def test_runtime_provenance_matches_explicit_launcher_paths(monkeypatch):
    monkeypatch.setenv("RENAISS_PYTHON_EXE", sys.executable)
    monkeypatch.setenv("RENAISS_ROOT", str(runtime.PROJECT_ROOT))
    monkeypatch.delenv("RENAISS_ENV_FILE", raising=False)

    provenance = runtime.collect_runtime_provenance()

    assert provenance.interpreter == Path(sys.executable).resolve()
    assert provenance.interpreter_expectation == "matched"
    assert provenance.root_expectation == "matched"
    assert provenance.expectations_match
    assert provenance.env_selection == "default"


def test_runtime_provenance_fails_closed_on_expected_path_drift(
    monkeypatch,
    tmp_path,
):
    monkeypatch.setenv("RENAISS_PYTHON_EXE", str(tmp_path / "wrong-python"))
    monkeypatch.setenv("RENAISS_ROOT", str(tmp_path / "wrong-root"))

    provenance = runtime.collect_runtime_provenance()

    assert provenance.interpreter_expectation == "mismatch"
    assert provenance.root_expectation == "mismatch"
    assert not provenance.expectations_match
