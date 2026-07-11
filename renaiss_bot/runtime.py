"""Canonical runtime paths for the standalone Renaiss repository."""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


PROJECT_ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class RuntimeProvenance:
    """Resolved runtime identity without retaining configured secret values."""

    interpreter: Path
    project_root: Path
    env_file: Path | None
    env_selection: str
    interpreter_expectation: str
    root_expectation: str
    env_ready: bool
    env_file_exists: bool

    @property
    def expectations_match(self) -> bool:
        return (
            self.interpreter_expectation in {"not configured", "matched"}
            and self.root_expectation in {"not configured", "matched"}
        )


def resolve_runtime_path(configured: str | os.PathLike[str]) -> Path:
    """Resolve an operator-supplied path from the canonical repository root."""
    path = Path(configured).expanduser()
    return path.resolve() if path.is_absolute() else (PROJECT_ROOT / path).resolve()


def runtime_env_path() -> Path:
    """Return the explicit env file, resolving relative paths from this repository."""
    configured = os.getenv("RENAISS_ENV_FILE", "").strip()
    if not configured:
        return PROJECT_ROOT / ".env"
    return resolve_runtime_path(configured)


def _path_expectation(variable: str, actual: Path) -> str:
    configured = os.getenv(variable, "").strip()
    if not configured:
        return "not configured"
    try:
        expected = resolve_runtime_path(configured)
    except (OSError, RuntimeError, ValueError):
        return "invalid"
    actual_key = os.path.normcase(str(actual.resolve()))
    expected_key = os.path.normcase(str(expected.resolve()))
    return "matched" if actual_key == expected_key else "mismatch"


def collect_runtime_provenance() -> RuntimeProvenance:
    """Resolve interpreter/root/env provenance for read-only release checks."""
    interpreter = Path(sys.executable).resolve()
    explicitly_configured = bool(os.getenv("RENAISS_ENV_FILE", "").strip())
    env_file: Path | None
    try:
        env_file = runtime_env_path()
        env_file_exists = env_file.is_file()
        env_ready = not explicitly_configured or env_file_exists
    except (OSError, RuntimeError, ValueError):
        env_file = None
        env_ready = False
        env_file_exists = False
    return RuntimeProvenance(
        interpreter=interpreter,
        project_root=PROJECT_ROOT,
        env_file=env_file,
        env_selection="explicit" if explicitly_configured else "default",
        interpreter_expectation=_path_expectation(
            "RENAISS_PYTHON_EXE",
            interpreter,
        ),
        root_expectation=_path_expectation("RENAISS_ROOT", PROJECT_ROOT),
        env_ready=env_ready,
        env_file_exists=env_file_exists,
    )


def load_runtime_environment() -> Path:
    """Load only the standalone env file without replacing service-injected values."""
    path = runtime_env_path()
    explicitly_configured = bool(os.getenv("RENAISS_ENV_FILE", "").strip())
    loaded = load_dotenv(dotenv_path=path, override=False)
    if explicitly_configured and not loaded:
        raise RuntimeError("the explicit RENAISS_ENV_FILE could not be loaded")
    return path
