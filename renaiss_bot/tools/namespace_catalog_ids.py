"""Rename legacy `renaiss-<hash>` catalog ids to the provider namespace.

Rewrites every `renaiss-…` local card id to `catalog:{category}:renaiss-…`
across the catalog and all referencing tables in one guarded transaction, so
the release-gate identity audit passes without touching card data.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from renaiss_bot.database.connection import close_db, get_db
from renaiss_bot.tools.sync_psa10_catalog import (
    load_explicit_environment,
    staging_target_issue,
)

# (table, column) pairs that may reference a catalog card id.
REFERENCING = (
    ("renaiss_card_links", "local_card_id"),
    ("renaiss_flex_daily_slots", "local_card_id"),
    ("renaiss_flex_posts", "local_card_id"),
    ("renaiss_market_board", "local_card_id"),
    ("renaiss_pack_events", "best_local_card_id"),
    ("renaiss_price_snapshots", "local_card_id"),
    ("renaiss_quiz_rounds", "local_card_id"),
    ("renaiss_referral_clicks", "local_card_id"),
    ("renaiss_referral_links", "local_card_id"),
    ("renaiss_user_cards", "local_card_id"),
)


async def migrate() -> dict:
    pool = await get_db()
    summary: dict[str, int] = {}
    async with pool.acquire() as conn, conn.transaction():
        await conn.fetchval(
            "SELECT pg_advisory_xact_lock(hashtextextended('renaiss-psa10-catalog-sync', 0))"
        )
        await conn.execute(
            """
            CREATE TEMP TABLE _id_mapping ON COMMIT DROP AS
            SELECT local_card_id AS old_id,
                   'catalog:' || category || ':' || local_card_id AS new_id
            FROM renaiss_catalog_cards
            WHERE local_card_id LIKE 'renaiss-%'
            """
        )
        summary["mapping"] = await conn.fetchval("SELECT count(*) FROM _id_mapping")
        for table, column in REFERENCING:
            result = await conn.execute(
                f"""
                UPDATE {table} AS t
                SET {column} = m.new_id
                FROM _id_mapping AS m
                WHERE t.{column} = m.old_id
                """
            )
            summary[table] = int(result.split()[-1])
        result = await conn.execute(
            """
            UPDATE renaiss_catalog_cards AS c
            SET local_card_id = m.new_id
            FROM _id_mapping AS m
            WHERE c.local_card_id = m.old_id
            """
        )
        summary["renaiss_catalog_cards"] = int(result.split()[-1])
        leftovers = 0
        for table, column in (("renaiss_catalog_cards", "local_card_id"),) + REFERENCING:
            leftovers += await conn.fetchval(
                f"SELECT count(*) FROM {table} WHERE {column} LIKE 'renaiss-%'"
            )
        if leftovers:
            raise RuntimeError(f"{leftovers} legacy ids would remain; rolled back")
    return summary


async def async_main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    load_explicit_environment(args.env_file)
    if not args.apply:
        raise SystemExit("--apply is required; this tool only renames ids")
    issue = staging_target_issue()
    if issue:
        raise SystemExit(f"refusing to rename catalog ids: {issue}")
    try:
        summary = await migrate()
        print("FINAL " + json.dumps(summary, sort_keys=True))
        return 0
    finally:
        await close_db()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(async_main()))
