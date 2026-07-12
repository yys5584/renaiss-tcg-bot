"""Recompute the price-tier collection grade for every catalog card.

Applies ``grade_for_price`` to all positive-priced ``renaiss_catalog_cards``
rows so the renderer's tier styling matches the market price. Runs behind the
same fail-closed database fingerprint guard as staging.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from renaiss_bot.database.connection import close_db, get_db
from renaiss_bot.services.spawn import GRADE_MIN_USD
from renaiss_bot.tools.sync_psa10_catalog import (
    load_explicit_environment,
    staging_target_issue,
)


def _tier_case_sql() -> str:
    branches = "\n".join(
        f"WHEN market_price_usd >= {minimum} THEN '{grade}'"
        for grade, minimum in GRADE_MIN_USD
    )
    return f"CASE\n{branches}\nELSE 'C' END"


async def regrade() -> list[dict]:
    pool = await get_db()
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.fetchval(
                "SELECT pg_advisory_xact_lock(hashtextextended('renaiss-psa10-catalog-sync', 0))"
            )
            await conn.execute(
                f"""
                UPDATE renaiss_catalog_cards
                SET grade = {_tier_case_sql()},
                    updated_at = clock_timestamp()
                WHERE market_price_usd > 0
                """
            )
        rows = await conn.fetch(
            """
            SELECT grade,
                   count(*) FILTER (WHERE is_active) AS active,
                   count(*) AS total
            FROM renaiss_catalog_cards
            GROUP BY grade
            ORDER BY min(CASE WHEN market_price_usd > 0 THEN market_price_usd END) DESC NULLS LAST
            """
        )
    return [dict(row) for row in rows]


async def async_main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    load_explicit_environment(args.env_file)
    if not args.apply:
        raise SystemExit("--apply is required; this tool only mutates grades")
    issue = staging_target_issue()
    if issue:
        raise SystemExit(f"refusing to regrade the catalog: {issue}")
    try:
        summary = await regrade()
        print("FINAL " + json.dumps(summary, ensure_ascii=False))
        return 0
    finally:
        await close_db()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(async_main()))
