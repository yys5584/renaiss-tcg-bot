"""Fail-closed release gate for collection, market, and graduation stages."""

from __future__ import annotations

import argparse
import asyncio
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from renaiss_bot.database.connection import close_db
from renaiss_bot.runtime import load_runtime_environment
from renaiss_bot.services.pack_rules import CARDS_PER_PACK
from renaiss_bot.tools.catalog_audit import execute as catalog_execute
from renaiss_bot.tools.pilot_report import execute as pilot_execute
from renaiss_bot.tools.preflight import (
    run as preflight_run,
    runtime_provenance_checks,
)

ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class GateResult:
    name: str
    ok: bool
    detail: str


def _run_command(
    name: str,
    command: list[str],
    *,
    env: dict[str, str] | None = None,
) -> GateResult:
    try:
        completed = subprocess.run(
            command,
            cwd=ROOT,
            env=env,
            check=False,
            text=True,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return GateResult(name, False, f"could not run (error={type(exc).__name__})")
    return GateResult(name, completed.returncode == 0, f"exit={completed.returncode}")


def _worktree_gate(*, allow_dirty: bool) -> list[GateResult]:
    results = [
        _run_command("Patch whitespace", ["git", "diff", "--check"]),
        _run_command("Staged patch whitespace", ["git", "diff", "--cached", "--check"]),
    ]
    if allow_dirty:
        results.append(
            GateResult(
                "Release scope",
                False,
                "--allow-dirty is inspection-only and cannot pass a release gate",
            )
        )
        return results
    try:
        completed = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        results.append(
            GateResult(
                "Release scope",
                False,
                f"could not inspect git (error={type(exc).__name__})",
            )
        )
        return results
    dirty_count = len([line for line in completed.stdout.splitlines() if line.strip()])
    results.append(
        GateResult(
            "Release scope",
            completed.returncode == 0 and dirty_count == 0,
            "clean worktree" if dirty_count == 0 else f"{dirty_count} changed paths are not frozen",
        )
    )
    return results


def _test_gate(*, skip_test_suite: bool) -> GateResult:
    if skip_test_suite:
        return GateResult(
            "PostgreSQL test suite",
            False,
            "--skip-test-suite is inspection-only and cannot pass a release gate",
        )
    if not os.getenv("RENAISS_TEST_DATABASE_URL", "").strip():
        return GateResult(
            "PostgreSQL test suite",
            False,
            "RENAISS_TEST_DATABASE_URL is required; production DATABASE_URL is never used for tests",
        )
    test_database_url = os.environ["RENAISS_TEST_DATABASE_URL"]
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith("RENAISS_") and not key.startswith("POKARD_")
    }
    env["RENAISS_REQUIRE_POSTGRES_TESTS"] = "1"
    env["RENAISS_TEST_DATABASE_URL"] = test_database_url
    env["DATABASE_URL"] = test_database_url
    return _run_command(
        "PostgreSQL test suite",
        [sys.executable, "-m", "pytest", "tests", "-q", "--postgres-integration"],
        env=env,
    )


def _preflight_args(args: argparse.Namespace) -> argparse.Namespace:
    market_stage = args.stage in {"market", "graduation"}
    return argparse.Namespace(
        skip_db=False,
        check_api=market_stage,
        check_telegram_config=True,
        check_telegram_live=True,
        require_daily_pick=market_stage,
        category=args.category,
        card_name=args.card_name,
        set_name=args.set_name,
        set_code=args.set_code,
        item_no=args.item_no,
        variation=args.variation,
        language=args.language,
        grade=args.grade,
        timeout=args.timeout,
        telegram_timeout=args.telegram_timeout,
    )


def _print_results(stage: str, results: list[GateResult]) -> None:
    print(f"\nRelease gate | stage={stage}")
    for result in results:
        print(f"[{'PASS' if result.ok else 'FAIL'}] {result.name}: {result.detail}")


async def execute(args: argparse.Namespace) -> int:
    provenance = runtime_provenance_checks(require_database=True)
    results = [
        GateResult(f"Provenance / {result.name}", result.ok, result.detail)
        for result in provenance
    ]
    if not all(result.ok for result in results):
        _print_results(args.stage, results)
        return 1

    results.extend(_worktree_gate(allow_dirty=args.allow_dirty))
    results.append(_test_gate(skip_test_suite=args.skip_test_suite))

    try:
        preflight_code = await preflight_run(_preflight_args(args))
    except Exception as exc:
        results.append(
            GateResult(
                "Runtime preflight",
                False,
                f"failed (error={type(exc).__name__})",
            )
        )
    else:
        results.append(
            GateResult(
                "Runtime preflight",
                preflight_code == 0,
                f"exit={preflight_code}",
            )
        )
    finally:
        try:
            await close_db()
        except Exception as exc:
            results.append(
                GateResult(
                    "Runtime DB cleanup",
                    False,
                    f"failed (error={type(exc).__name__})",
                )
            )

    minimum_eligible = (
        max(1, args.min_eligible) if args.stage in {"market", "graduation"} else 0
    )
    try:
        catalog_code = await catalog_execute(
            category=args.category,
            limit=args.catalog_limit,
            require_eligible=minimum_eligible,
            pool_source="actual",
            require_total=CARDS_PER_PACK,
            require_resolved_source="renaiss_catalog",
        )
    except Exception as exc:
        results.append(
            GateResult(
                "Runtime catalog",
                False,
                f"failed (error={type(exc).__name__})",
            )
        )
    else:
        results.append(
            GateResult("Runtime catalog", catalog_code == 0, f"exit={catalog_code}")
        )

    if args.stage == "graduation":
        try:
            pilot_code = await pilot_execute(
                args.pilot_days,
                require_ready=True,
                min_cohort_size=max(1, args.min_cohort_size),
                min_d7_uplift_pp=max(0.0, args.min_d7_uplift_pp),
            )
        except Exception as exc:
            results.append(
                GateResult(
                    "Pilot graduation",
                    False,
                    f"failed (error={type(exc).__name__})",
                )
            )
        else:
            results.append(
                GateResult(
                    "Pilot graduation",
                    pilot_code == 0,
                    f"exit={pilot_code}",
                )
            )

    _print_results(args.stage, results)
    return 0 if all(result.ok for result in results) else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stage", choices=("collection", "market", "graduation"), required=True)
    parser.add_argument(
        "--allow-dirty",
        action="store_true",
        help="inspect a dirty tree, but force a non-passing release result",
    )
    parser.add_argument(
        "--skip-test-suite",
        action="store_true",
        help="inspect without tests, but force a non-passing release result",
    )
    parser.add_argument("--category", default="pokemon_tcg")
    parser.add_argument("--catalog-limit", type=int, default=5000)
    parser.add_argument("--min-eligible", type=int, default=10)
    parser.add_argument("--card-name", default="")
    parser.add_argument("--set-name", default="")
    parser.add_argument("--set-code", default="")
    parser.add_argument("--item-no", default="")
    parser.add_argument("--variation", default="")
    parser.add_argument("--language", default="English")
    parser.add_argument("--grade", default="RAW")
    parser.add_argument("--timeout", type=float, default=5.0)
    parser.add_argument("--telegram-timeout", type=float, default=10.0)
    parser.add_argument("--pilot-days", type=int, default=30)
    parser.add_argument("--min-cohort-size", type=int, default=10)
    parser.add_argument("--min-d7-uplift-pp", type=float, default=5.0)
    return parser


def main() -> None:
    load_runtime_environment()
    args = build_parser().parse_args()
    raise SystemExit(asyncio.run(execute(args)))


if __name__ == "__main__":
    main()
