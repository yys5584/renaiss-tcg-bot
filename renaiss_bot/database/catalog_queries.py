"""DB-backed catalog price cache operations."""

from __future__ import annotations

import json
from typing import Any

from renaiss_bot.database.connection import get_db
from renaiss_bot.services.models import RenaissPrice


async def acquire_catalog_refresh_lease(*, owner: str, lease_seconds: int) -> bool:
    pool = await get_db()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO renaiss_job_leases (
                job_name, lease_owner, acquired_at, lease_expires_at,
                next_attempt_at, last_status, updated_at
            ) VALUES (
                'catalog_price_refresh', $1, clock_timestamp(),
                clock_timestamp() + ($2::int * interval '1 second'),
                '-infinity'::timestamptz, 'running', clock_timestamp()
            )
            ON CONFLICT (job_name) DO UPDATE
            SET lease_owner = EXCLUDED.lease_owner,
                acquired_at = EXCLUDED.acquired_at,
                lease_expires_at = EXCLUDED.lease_expires_at,
                last_status = 'running',
                updated_at = clock_timestamp()
            WHERE renaiss_job_leases.lease_expires_at <= clock_timestamp()
              AND renaiss_job_leases.next_attempt_at <= clock_timestamp()
            RETURNING job_name
            """,
            owner,
            min(14_400, max(300, int(lease_seconds))),
        )
    return row is not None


async def finish_catalog_refresh_lease(
    *, owner: str, status: str, retry_after_seconds: int = 0
) -> bool:
    pool = await get_db()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            UPDATE renaiss_job_leases
            SET lease_expires_at = clock_timestamp(),
                next_attempt_at = clock_timestamp() + ($2::int * interval '1 second'),
                last_status = $3,
                updated_at = clock_timestamp()
            WHERE job_name = 'catalog_price_refresh' AND lease_owner = $1
            RETURNING job_name
            """,
            owner,
            min(43_200, max(0, int(retry_after_seconds))),
            status[:64],
        )
    return row is not None


async def active_category_counts() -> dict[str, int]:
    """Active catalog card counts per category, for spawn category weighting."""
    pool = await get_db()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT category, count(*) AS n
            FROM renaiss_catalog_cards
            WHERE is_active = TRUE
            GROUP BY category
            """
        )
    return {str(row["category"]): int(row["n"]) for row in rows}


async def list_catalog_cards_for_refresh(*, limit: int) -> list[dict[str, Any]]:
    """Return the oldest active rows; successful updates rotate them to the back."""
    pool = await get_db()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT local_card_id, category, card_name, grade, set_code, set_name,
                   collector_number, language, image_url, metadata
            FROM renaiss_catalog_cards
            WHERE is_active = TRUE
            ORDER BY updated_at ASC, local_card_id ASC
            LIMIT $1
            """,
            max(1, min(5000, int(limit))),
        )
    return [dict(row) for row in rows]


def catalog_price_metadata(price: RenaissPrice) -> dict[str, Any]:
    return {
        "price_status": price.status,
        "price_source": price.source,
        "price_confidence": price.confidence,
        "price_confidence_score": price.confidence_score,
        "price_source_count": price.source_count,
        "price_observation_count": price.observation_count,
        "price_valuation_method": price.valuation_method,
        "price_updated_at": price.price_updated_at.isoformat()
        if price.price_updated_at
        else None,
        "price_asset_url": price.asset_url,
        "price_referral_url": price.referral_url,
        "price_source_identity_key": price.source_identity_key,
        "evidence_origin": "official-api-import",
    }


async def update_catalog_cached_price(*, local_card_id: str, price: RenaissPrice) -> bool:
    """Persist one live exact response without deleting the previous row on failures."""
    if price.status != "exact" or not price.fmv_usd or price.fmv_usd <= 0:
        return False
    pool = await get_db()
    metadata = catalog_price_metadata(price)
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            UPDATE renaiss_catalog_cards
            SET market_price_usd = $2,
                image_url = COALESCE($3, image_url),
                metadata = COALESCE(metadata, '{}'::jsonb) || $4::jsonb,
                updated_at = clock_timestamp()
            WHERE local_card_id = $1 AND is_active = TRUE
            RETURNING local_card_id
            """,
            local_card_id,
            price.fmv_usd,
            price.image_url,
            json.dumps(metadata),
        )
    return row is not None


async def mark_catalog_refresh_attempted(*, local_card_id: str) -> bool:
    """Rotate a structurally missing card without fabricating or erasing evidence."""
    pool = await get_db()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            UPDATE renaiss_catalog_cards
            SET metadata = COALESCE(metadata, '{}'::jsonb) || jsonb_build_object(
                    'price_refresh_attempted_at', clock_timestamp()
                ),
                updated_at = clock_timestamp()
            WHERE local_card_id = $1 AND is_active = TRUE
            RETURNING local_card_id
            """,
            local_card_id,
        )
    return row is not None
