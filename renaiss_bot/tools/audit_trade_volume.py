"""Audit 90-day trade volume for staged PSA 10 catalog candidates.

Fetch phase reads the candidate cache produced by ``sync_psa10_catalog`` and
records each card's 90-day grade-scoped trade ``total`` in a resumable JSONL
file. Apply phase merges the audited counts into ``renaiss_catalog_cards``
metadata without touching ``is_active``, behind the same fail-closed database
fingerprint guard as staging.
"""

from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import time

import aiohttp

from renaiss_bot.database.connection import close_db, get_db
from renaiss_bot.services.client import _api_base, _headers
from renaiss_bot.tools.sync_psa10_catalog import (
    load_explicit_environment,
    staging_target_issue,
)

TRADE_WINDOW_DAYS = 90
_CARD_PREFIX = "/card"


def trades_api_url(href: str) -> str | None:
    """Map a set-listing asset href to its trades endpoint, or None."""
    cleaned = str(href or "").strip()
    if not cleaned.startswith(f"{_CARD_PREFIX}/") or "?" in cleaned or "#" in cleaned:
        return None
    return f"{_api_base()}/v1/cards{cleaned[len(_CARD_PREFIX):]}/trades"


def load_progress(path: Path) -> dict[str, dict]:
    """Read prior JSONL results so an interrupted fetch resumes, not restarts."""
    output: dict[str, dict] = {}
    if not path.is_file():
        return output
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            card_id = row.get("local_card_id")
            if isinstance(card_id, str) and card_id:
                output[card_id] = row
    return output


async def fetch_totals(
    *,
    candidates: list[dict],
    progress_file: Path,
    request_interval: float,
) -> dict[str, int]:
    done = load_progress(progress_file)
    status_counts: dict[str, int] = {}
    timeout = aiohttp.ClientTimeout(total=30)
    progress_file.parent.mkdir(parents=True, exist_ok=True)
    next_request = 0.0
    async with aiohttp.ClientSession(timeout=timeout, headers=_headers()) as session:
        with progress_file.open("a", encoding="utf-8") as sink:
            for index, row in enumerate(candidates, start=1):
                card_id = row["local_card_id"]
                if card_id in done:
                    continue
                url = trades_api_url(row["metadata"]["price_asset_url"])
                record: dict = {"local_card_id": card_id}
                if url is None:
                    record["error"] = "unmapped-href"
                else:
                    wait = next_request - time.monotonic()
                    if wait > 0:
                        await asyncio.sleep(wait)
                    next_request = time.monotonic() + max(0.1, request_interval)
                    for attempt in range(3):
                        try:
                            async with session.get(
                                url,
                                params={
                                    "window": TRADE_WINDOW_DAYS,
                                    "scope": "grade",
                                    "limit": 1,
                                },
                                allow_redirects=False,
                            ) as response:
                                key = str(response.status)
                                if response.status == 429:
                                    retry_after = response.headers.get("Retry-After")
                                    try:
                                        delay = min(120.0, max(1.0, float(retry_after)))
                                    except (TypeError, ValueError):
                                        delay = 5.0 * (attempt + 1)
                                    await asyncio.sleep(delay)
                                    continue
                                if response.status == 404:
                                    record["error"] = "not-found"
                                    break
                                response.raise_for_status()
                                payload = await response.json(content_type=None)
                                total = payload.get("total") if isinstance(payload, dict) else None
                                if isinstance(total, int) and total >= 0:
                                    record["trade_count_90d"] = total
                                else:
                                    record["error"] = "missing-total"
                                break
                        except aiohttp.ClientError as exc:
                            key = type(exc).__name__
                            if attempt == 2:
                                record["error"] = f"request-failed:{key}"
                            else:
                                await asyncio.sleep(2.0 * (attempt + 1))
                    status_counts[key] = status_counts.get(key, 0) + 1
                done[card_id] = record
                sink.write(json.dumps(record, ensure_ascii=False) + "\n")
                sink.flush()
                if index % 250 == 0:
                    print(
                        json.dumps(
                            {
                                "cards_done": index,
                                "cards_total": len(candidates),
                                "http_status": status_counts,
                            }
                        ),
                        flush=True,
                    )
    audited = {
        card_id: row["trade_count_90d"]
        for card_id, row in done.items()
        if isinstance(row.get("trade_count_90d"), int)
    }
    errors = {card_id: row["error"] for card_id, row in done.items() if "error" in row}
    print(
        json.dumps(
            {
                "audited": len(audited),
                "errors": len(errors),
                "http_status": status_counts,
            },
            sort_keys=True,
        ),
        flush=True,
    )
    return audited


async def apply_totals(progress_file: Path) -> dict[str, int]:
    audited_at = datetime.now(timezone.utc).isoformat()
    rows = [
        (
            json.dumps(
                {
                    "trade_count_90d": record["trade_count_90d"],
                    "trade_window_days": TRADE_WINDOW_DAYS,
                    "trade_audited_at": audited_at,
                },
                ensure_ascii=False,
            ),
            card_id,
        )
        for card_id, record in load_progress(progress_file).items()
        if isinstance(record.get("trade_count_90d"), int)
    ]
    if not rows:
        raise SystemExit("no audited trade counts found in the progress file")
    pool = await get_db()
    async with pool.acquire() as conn, conn.transaction():
        await conn.fetchval(
            "SELECT pg_advisory_xact_lock(hashtextextended('renaiss-psa10-catalog-sync', 0))"
        )
        result = await conn.executemany(
            """
            UPDATE renaiss_catalog_cards
            SET metadata = COALESCE(metadata, '{}'::jsonb) || $1::jsonb,
                updated_at = clock_timestamp()
            WHERE local_card_id = $2
            """,
            rows,
        )
    return {"updated": len(rows)}


async def async_main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, required=True)
    parser.add_argument("--cache-file", type=Path, required=True,
                        help="Candidate cache produced by sync_psa10_catalog")
    parser.add_argument("--progress-file", type=Path, required=True,
                        help="Resumable JSONL audit results")
    parser.add_argument("--apply", action="store_true",
                        help="Merge audited counts into renaiss_catalog_cards metadata")
    parser.add_argument("--request-interval", type=float, default=0.6)
    args = parser.parse_args()
    load_explicit_environment(args.env_file)
    if args.apply:
        issue = staging_target_issue()
        if issue:
            raise SystemExit(f"refusing to apply trade audit: {issue}")
        summary = await apply_totals(args.progress_file)
        try:
            print("FINAL " + json.dumps({"mode": "apply", **summary}, sort_keys=True))
        finally:
            await close_db()
        return 0
    cached = json.loads(args.cache_file.read_text(encoding="utf-8"))
    candidates = cached["rows"]
    await fetch_totals(
        candidates=candidates,
        progress_file=args.progress_file,
        request_interval=args.request_interval,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(async_main()))
