"""Explicit, fail-closed PostgreSQL schema preparation for operators."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import hmac
import os
from collections.abc import Sequence
from urllib.parse import unquote, urlsplit

from renaiss_bot.database.connection import close_db, get_db
from renaiss_bot.database.schema import create_tables
from renaiss_bot.runtime import load_runtime_environment


_ENABLED_VALUES = frozenset({"1", "true", "yes", "on"})
_CLOSE_TIMEOUT_SECONDS = 10.0
_FINGERPRINT_ENV = "RENAISS_EXPECTED_DATABASE_FINGERPRINT"


def _env_enabled(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in _ENABLED_VALUES


def database_target_fingerprint(dsn: str) -> str:
    """Hash the non-secret network target encoded by a PostgreSQL URL.

    The fingerprint deliberately excludes usernames, passwords, and query
    parameters.  Operators can therefore pin the intended host/database
    without logging credentials or copying a full DSN into a command line.
    """
    try:
        parsed = urlsplit(dsn)
        hostname = (parsed.hostname or "").strip().lower()
        port = parsed.port or 5432
    except ValueError as exc:
        raise ValueError("invalid PostgreSQL target") from exc
    database = unquote(parsed.path.lstrip("/")).strip()
    if parsed.scheme not in {"postgres", "postgresql"} or not hostname or not database:
        raise ValueError("invalid PostgreSQL target")
    canonical = f"{hostname}:{port}/{database}"
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def database_targets_match(first_dsn: str, second_dsn: str) -> bool:
    """Compare database network targets while ignoring credentials."""
    try:
        return database_target_fingerprint(first_dsn) == database_target_fingerprint(
            second_dsn
        )
    except ValueError:
        return False


def database_mutation_target_issue(dsn: str) -> str | None:
    """Return why a mutation target is not pinned to the operator's digest."""
    expected = os.getenv(_FINGERPRINT_ENV, "").strip().lower()
    if not expected:
        return f"{_FINGERPRINT_ENV} is missing"
    if len(expected) != 64 or any(character not in "0123456789abcdef" for character in expected):
        return f"{_FINGERPRINT_ENV} must be a SHA-256 hex digest"
    try:
        actual = database_target_fingerprint(dsn)
    except ValueError:
        return "DATABASE_URL target is not a valid PostgreSQL URL"
    if not hmac.compare_digest(actual, expected):
        return f"DATABASE_URL does not match {_FINGERPRINT_ENV}"
    return None


def configuration_issues(*, apply: bool) -> list[str]:
    """Return mutation blockers without exposing connection details."""
    issues: list[str] = []
    if not apply:
        issues.append("--apply is required for schema changes")
    if _env_enabled("RENAISS_SKIP_DB"):
        issues.append("RENAISS_SKIP_DB must be disabled")
    if os.getenv("RENAISS_API_MOCK_JSON", "").strip():
        issues.append("RENAISS_API_MOCK_JSON must be unset")
    dsn = os.getenv("DATABASE_URL", "").strip()
    if not dsn:
        issues.append("DATABASE_URL is missing")
    else:
        target_issue = database_mutation_target_issue(dsn)
        if target_issue:
            issues.append(target_issue)
    return issues


async def prepare_database(*, apply: bool) -> int:
    """Apply the idempotent schema transaction and close the process pool."""
    issues = configuration_issues(apply=apply)
    if issues:
        print(f"[FAIL] Database preparation refused: {'; '.join(issues)}")
        return 2

    operation_error: str | None = None
    cleanup_error: str | None = None
    try:
        pool = await get_db()
        await create_tables(pool)
    except Exception as exc:
        # Provider exception text can include a DSN, password, or server detail.
        operation_error = type(exc).__name__
    finally:
        try:
            await asyncio.wait_for(close_db(), timeout=_CLOSE_TIMEOUT_SECONDS)
        except Exception as exc:
            cleanup_error = type(exc).__name__

    if operation_error is not None:
        detail = f"schema preparation failed ({operation_error})"
        if cleanup_error is not None:
            detail += f"; connection cleanup failed ({cleanup_error})"
        print(f"[FAIL] Database preparation: {detail}")
        return 1
    if cleanup_error is not None:
        print(
            "[FAIL] Database preparation: "
            f"connection cleanup failed ({cleanup_error})"
        )
        return 1

    print("[PASS] Database preparation: schema transaction completed")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="explicitly authorize idempotent schema changes to DATABASE_URL",
    )
    parser.add_argument(
        "--print-target-fingerprint",
        action="store_true",
        help="print the non-secret DATABASE_URL target fingerprint and exit",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    try:
        load_runtime_environment()
    except Exception as exc:
        # Do not echo dotenv/parser exception strings: they may include secrets.
        print(
            "[FAIL] Database preparation: "
            f"runtime environment load failed ({type(exc).__name__})"
        )
        raise SystemExit(2) from None
    if args.print_target_fingerprint:
        try:
            fingerprint = database_target_fingerprint(os.getenv("DATABASE_URL", ""))
        except ValueError:
            print("[FAIL] Database preparation: DATABASE_URL target is invalid")
            raise SystemExit(2) from None
        print(fingerprint)
        raise SystemExit(0)
    raise SystemExit(asyncio.run(prepare_database(apply=args.apply)))


if __name__ == "__main__":
    main()
