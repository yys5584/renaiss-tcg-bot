"""Select the Season 0 Master Pool from audited catalog candidates.

Reads staged ``renaiss_catalog_cards`` candidates plus their audited 90-day
trade counts and fills the exclusive plan quotas in order: High Value 250,
High Volume 300, Cheap Active 250, Diversity 200. The output is a review
manifest only — nothing is activated. Activation is a separate, explicitly
approved step.
"""

from __future__ import annotations

import argparse
import asyncio
from collections import Counter
from datetime import datetime, timezone
import json
from pathlib import Path

from renaiss_bot.database.connection import close_db, get_db
from renaiss_bot.tools.sync_psa10_catalog import load_explicit_environment

QUOTAS = (
    ("high_value", 250),
    ("high_volume", 300),
    ("cheap_active", 250),
    ("diversity", 200),
)
POOL_SIZE = sum(size for _, size in QUOTAS)
MAX_PER_NAME = 12
MAX_PER_SET = 80
CHEAP_MIN_USD = 5.0
CHEAP_MAX_USD = 50.0


def canonical_identity(row: dict) -> tuple:
    return (
        row.get("category") or "",
        str(row.get("set_code") or "").strip().lower(),
        str(row.get("collector_number") or "").strip().lower(),
        str(row.get("card_name") or "").strip().lower(),
        str(row.get("variation") or "").strip().lower(),
        str(row.get("language") or "").strip().lower(),
    )


def _name_key(row: dict) -> str:
    return str(row.get("card_name") or "").strip().lower()


def _set_key(row: dict) -> tuple:
    return (row.get("category") or "", str(row.get("set_code") or "").strip().lower())


class _PoolState:
    def __init__(self, *, max_per_name: int, max_per_set: int) -> None:
        self.selected: list[dict] = []
        self.names: Counter = Counter()
        self.sets: Counter = Counter()
        self.identities: set[tuple] = set()
        self.max_per_name = max_per_name
        self.max_per_set = max_per_set

    def can_take(self, row: dict) -> bool:
        return (
            canonical_identity(row) not in self.identities
            and self.names[_name_key(row)] < self.max_per_name
            and self.sets[_set_key(row)] < self.max_per_set
        )

    def take(self, row: dict, quota: str) -> None:
        self.selected.append({**row, "quota": quota})
        self.names[_name_key(row)] += 1
        self.sets[_set_key(row)] += 1
        self.identities.add(canonical_identity(row))


def _fill(state: _PoolState, quota: str, size: int, ordered: list[dict]) -> None:
    taken = 0
    for row in ordered:
        if taken >= size:
            return
        if state.can_take(row):
            state.take(row, quota)
            taken += 1


def select_master_pool(
    rows: list[dict],
    *,
    quotas: tuple = QUOTAS,
    max_per_name: int = MAX_PER_NAME,
    max_per_set: int = MAX_PER_SET,
    already_active: list[dict] | None = None,
) -> list[dict]:
    """Fill the exclusive quotas deterministically from eligible candidates.

    ``rows`` must carry positive ``market_price_usd`` and integer
    ``trade_count_90d``; rows without an audited trade count only compete for
    the price-based quota. ``already_active`` rows (e.g. the verified seed)
    stay active on their own — their identities are excluded here and they
    count toward the shared name/set caps so the combined live pool keeps the
    duplication limits.
    """
    quota_sizes = dict(quotas)
    pool_size = sum(quota_sizes.values())
    eligible = [
        row
        for row in rows
        if isinstance(row.get("market_price_usd"), (int, float))
        and row["market_price_usd"] > 0
    ]
    audited = [row for row in eligible if isinstance(row.get("trade_count_90d"), int)]
    by_price = sorted(
        eligible, key=lambda r: (-float(r["market_price_usd"]), r["local_card_id"])
    )
    by_volume = sorted(
        audited,
        key=lambda r: (
            -r["trade_count_90d"],
            -float(r["market_price_usd"]),
            r["local_card_id"],
        ),
    )

    state = _PoolState(max_per_name=max_per_name, max_per_set=max_per_set)
    for row in already_active or ():
        state.names[_name_key(row)] += 1
        state.sets[_set_key(row)] += 1
        state.identities.add(canonical_identity(row))
    _fill(state, "high_value", quota_sizes["high_value"], by_price)
    _fill(state, "high_volume", quota_sizes["high_volume"], by_volume)
    cheap = [
        row
        for row in by_volume
        if CHEAP_MIN_USD <= float(row["market_price_usd"]) <= CHEAP_MAX_USD
        and row["trade_count_90d"] >= 1
    ]
    _fill(state, "cheap_active", quota_sizes["cheap_active"], cheap)

    # Diversity pass 1: only card names absent from the pool, most-traded first.
    remaining_quota = quota_sizes["diversity"]
    fresh_names = [row for row in by_volume if state.names[_name_key(row)] == 0]
    before = len(state.selected)
    _fill(state, "diversity", remaining_quota, fresh_names)
    # Diversity pass 2: relax to any candidate still under the shared caps.
    still_needed = remaining_quota - (len(state.selected) - before)
    if still_needed > 0:
        _fill(state, "diversity", still_needed, by_volume)

    if len(state.selected) != pool_size:
        raise RuntimeError(
            f"selected {len(state.selected)} cards; the quota plan requires {pool_size}"
        )
    return state.selected


async def load_candidates() -> tuple[list[dict], list[dict]]:
    """Return (inactive candidates, currently active rows)."""
    pool = await get_db()
    records = await pool.fetch(
        """
        SELECT local_card_id, category, card_name, set_code, set_name,
               collector_number, language, image_url, market_price_usd, metadata,
               is_active
        FROM renaiss_catalog_cards
        WHERE (metadata->>'catalog_candidate')::boolean IS TRUE
           OR is_active = TRUE
        """
    )
    rows = []
    active_rows = []
    for record in records:
        metadata = record["metadata"]
        if isinstance(metadata, str):
            metadata = json.loads(metadata)
        metadata = metadata or {}
        trade_count = metadata.get("trade_count_90d")
        row = {
            "local_card_id": record["local_card_id"],
            "category": record["category"],
            "card_name": record["card_name"],
            "set_code": record["set_code"],
            "set_name": record["set_name"],
            "collector_number": record["collector_number"],
            "language": record["language"],
            "variation": metadata.get("variation") or "",
            "image_url": record["image_url"],
            "market_price_usd": float(record["market_price_usd"] or 0),
            "trade_count_90d": trade_count if isinstance(trade_count, int) else None,
            "renaiss_href": metadata.get("price_asset_url"),
        }
        if record["is_active"]:
            active_rows.append(row)
        else:
            rows.append(row)
    return rows, active_rows


async def activate_selection(selected: list[dict]) -> int:
    """Flip the selected candidates to active inside one guarded transaction."""
    card_ids = [row["local_card_id"] for row in selected]
    pool = await get_db()
    async with pool.acquire() as conn, conn.transaction():
        await conn.fetchval(
            "SELECT pg_advisory_xact_lock(hashtextextended('renaiss-psa10-catalog-sync', 0))"
        )
        updated = await conn.fetch(
            """
            UPDATE renaiss_catalog_cards
            SET is_active = TRUE, updated_at = clock_timestamp()
            WHERE local_card_id = ANY($1::text[])
            RETURNING local_card_id
            """,
            card_ids,
        )
        if len(updated) != len(card_ids):
            raise RuntimeError(
                f"activation matched {len(updated)} of {len(card_ids)} selected cards; "
                "rolled back"
            )
    return len(updated)


def build_manifest(selected: list[dict], *, candidate_total: int) -> dict:
    quota_counts = Counter(row["quota"] for row in selected)
    category_counts = Counter(row["category"] for row in selected)
    prices = sorted(float(row["market_price_usd"]) for row in selected)
    volumes = sorted(
        (row["trade_count_90d"] for row in selected if isinstance(row["trade_count_90d"], int)),
        reverse=True,
    )
    return {
        "schema_version": 1,
        "manifest_type": "season0-master-pool",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source": "renaiss-catalog-staging+trade-audit",
        "selection": {
            "strategy": "quota-mix",
            "quotas": dict(QUOTAS),
            "max_per_name": MAX_PER_NAME,
            "max_per_set": MAX_PER_SET,
            "cheap_band_usd": [CHEAP_MIN_USD, CHEAP_MAX_USD],
            "candidate_total": candidate_total,
            "scored_use_allowed": False,
            "activation_applied": False,
        },
        "summary": {
            "quota_counts": dict(quota_counts),
            "category_counts": dict(category_counts),
            "price_floor_usd": prices[0],
            "price_ceiling_usd": prices[-1],
            "median_price_usd": prices[len(prices) // 2],
            "audited_cards": len(volumes),
            "zero_trade_cards": sum(1 for v in volumes if v == 0),
            "median_trade_count_90d": volumes[len(volumes) // 2] if volumes else None,
        },
        "cards": selected,
    }


async def async_main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--apply-activation",
        action="store_true",
        help="Activate the selected cards after writing the manifest",
    )
    args = parser.parse_args()
    load_explicit_environment(args.env_file)
    if args.apply_activation:
        from renaiss_bot.tools.sync_psa10_catalog import staging_target_issue

        issue = staging_target_issue()
        if issue:
            raise SystemExit(f"refusing to activate the master pool: {issue}")
    try:
        rows, active_rows = await load_candidates()
        selected = select_master_pool(rows, already_active=active_rows)
        manifest = build_manifest(selected, candidate_total=len(rows))
        manifest["selection"]["already_active_rows"] = len(active_rows)
        manifest["selection"]["activation_applied"] = bool(args.apply_activation)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        activated = None
        if args.apply_activation:
            activated = await activate_selection(selected)
        print(
            "FINAL "
            + json.dumps(
                {
                    "output": str(args.output),
                    "activated": activated,
                    "already_active_rows": len(active_rows),
                    **manifest["summary"],
                },
                ensure_ascii=False,
                sort_keys=True,
            )
        )
        return 0
    finally:
        await close_db()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(async_main()))
