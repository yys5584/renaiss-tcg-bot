"""Operator-only CLI for ambiguous Daily Pick Result Bell deliveries."""

from __future__ import annotations

import argparse
import asyncio

from renaiss_bot.database.connection import close_db
from renaiss_bot.runtime import load_runtime_environment
from renaiss_bot.database.market_queries import (
    ResultBellReconciliationError,
    get_daily_pick_result_bell_reconciliation,
    reconcile_daily_pick_result_bell,
)


def _single_line(value, *, limit: int = 500) -> str:
    text = " ".join(str(value or "-").split())
    return text[:limit]


def _positive_int(raw: str) -> int:
    value = int(raw)
    if value <= 0:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return value


def _print_row(row: dict) -> None:
    error_code = row.get("last_error_code") or "-"
    error = _single_line(row.get("last_error"))
    print(f"id: {row.get('id')}")
    print(f"state: {row.get('state')}")
    print(f"chat: {row.get('chat_id')}")
    print(f"bell date: {row.get('bell_date')}")
    print(f"cohort pick date: {row.get('cohort_pick_date')}")
    print(f"expires: {row.get('expires_at')}")
    print(f"attempts: {int(row.get('attempt_count') or 0)}")
    print(f"last error: {error_code} | {error}")
    print(f"telegram message id: {row.get('telegram_message_id') or '-'}")
    if row.get("audit_event_id") is not None:
        print(f"audit event id: {row['audit_event_id']}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="action", required=True)

    show = subparsers.add_parser("show", help="Inspect one outbox row without changing it.")
    show.add_argument("outbox_id", type=_positive_int)

    mark_sent = subparsers.add_parser(
        "mark-sent",
        help="Confirm Telegram delivery using the observed message id.",
    )
    mark_sent.add_argument("outbox_id", type=_positive_int)
    mark_sent.add_argument("--telegram-message-id", type=_positive_int, required=True)
    mark_sent.add_argument("--operator", required=True)
    mark_sent.add_argument("--note", default="")

    retry = subparsers.add_parser(
        "retry",
        help="Retry a proven-unsent row while its original window remains open.",
    )
    retry.add_argument("outbox_id", type=_positive_int)
    retry.add_argument("--operator", required=True)
    retry.add_argument("--note", default="")
    return parser


async def run(args: argparse.Namespace) -> int:
    try:
        if args.action == "show":
            row = await get_daily_pick_result_bell_reconciliation(args.outbox_id)
            if row is None:
                print(f"Result Bell outbox row {args.outbox_id} was not found.")
                return 2
            _print_row(row)
            return 0

        row = await reconcile_daily_pick_result_bell(
            outbox_id=args.outbox_id,
            action=args.action,
            operator_name=args.operator,
            telegram_message_id=getattr(args, "telegram_message_id", None),
            note=args.note,
        )
    except ResultBellReconciliationError as exc:
        print(f"Reconciliation rejected: {exc.code}")
        if exc.row:
            _print_row(exc.row)
        return 2
    except Exception as exc:
        print(f"Reconciliation failed: {_single_line(exc)}")
        return 1
    _print_row(row)
    return 0


def main() -> None:
    load_runtime_environment()
    args = build_parser().parse_args()

    async def execute() -> int:
        try:
            return await run(args)
        finally:
            await close_db()

    raise SystemExit(asyncio.run(execute()))


if __name__ == "__main__":
    main()
