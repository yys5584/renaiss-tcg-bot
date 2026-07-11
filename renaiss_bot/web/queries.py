"""Read-only collection queries owned by the standalone Renaiss service."""

from __future__ import annotations

import os
import re
from datetime import date, datetime
from typing import Any
from urllib.parse import urlparse

from renaiss_bot.database.connection import get_db
from renaiss_bot.web.auth import collector_pseudonym


GRADES = ("C", "U", "R", "RR", "AR", "SR", "SAR", "UR", "MUR")
MIN_PUBLIC_CATALOG_CARDS = 10
CATALOG_CATEGORY = "pokemon_tcg"
_GRADE_SQL = "'C', 'U', 'R', 'RR', 'AR', 'SR', 'SAR', 'UR', 'MUR'"
_GRADE_ORDER_SQL = """
CASE UPPER(COALESCE(d.grade, 'R'))
    WHEN 'MUR' THEN 9 WHEN 'UR' THEN 8 WHEN 'SAR' THEN 7
    WHEN 'SR' THEN 6 WHEN 'AR' THEN 5 WHEN 'RR' THEN 4
    WHEN 'R' THEN 3 WHEN 'U' THEN 2 ELSE 1
END
"""
_HOST_PATTERN = re.compile(r"^[a-z0-9](?:[a-z0-9.-]{0,251}[a-z0-9])?$")


def allowed_image_hosts() -> tuple[str, ...]:
    raw = os.getenv("RENAISS_IMAGE_ALLOWED_HOSTS", "images.pokemontcg.io")
    hosts = []
    for value in raw.split(","):
        host = value.strip().lower().rstrip(".")
        if host and ".." not in host and _HOST_PATTERN.fullmatch(host):
            hosts.append(host)
    return tuple(dict.fromkeys(hosts))


def sanitize_image_url(value: Any) -> str | None:
    if not value:
        return None
    try:
        parsed = urlparse(str(value))
        hostname = parsed.hostname
        port = parsed.port
    except ValueError:
        return None
    if parsed.scheme != "https" or not hostname:
        return None
    if parsed.username or parsed.password or port not in {None, 443}:
        return None
    if hostname.lower().rstrip(".") not in allowed_image_hosts():
        return None
    return parsed.geturl()


def _json_value(value: Any) -> Any:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return value


def _record_dict(row) -> dict[str, Any]:
    result = {key: _json_value(value) for key, value in dict(row).items()}
    if "image_url" in result:
        result["image_url"] = sanitize_image_url(result["image_url"])
    return result


def _empty_collection(*, authenticated: bool) -> dict[str, Any]:
    return {
        "ok": True,
        "available": False,
        "authenticated": authenticated,
        "summary": {
            "catalog_total": 0,
            "sets_total": 0,
            "owned_in_catalog": 0,
            "archived_owned": 0,
            "total_quantity": 0,
            "completion_pct": 0,
        },
        "cards": [],
        "sets": [],
        "grades": list(GRADES),
        "page": 1,
        "per_page": 24,
        "total_filtered": 0,
        "has_more": False,
    }


async def active_catalog_count() -> int:
    pool = await get_db()
    async with pool.acquire() as conn:
        value = await conn.fetchval(
            """
            SELECT COUNT(*)::int
            FROM public.renaiss_catalog_cards
            WHERE is_active = TRUE
              AND category = $1
              AND UPPER(COALESCE(grade, 'R')) IN ("""
            + _GRADE_SQL
            + ")",
            CATALOG_CATEGORY,
        )
    return int(value or 0)


async def database_role_is_read_only() -> bool:
    """Require SELECT-only access to the three tables exposed by this service."""
    pool = await get_db()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT
                COALESCE(bool_and(has_table_privilege(table_name, 'SELECT')), FALSE)
                    AS can_select,
                COALESCE(bool_or(
                    has_table_privilege(table_name, 'INSERT')
                    OR has_table_privilege(table_name, 'UPDATE')
                    OR has_table_privilege(table_name, 'DELETE')
                    OR has_table_privilege(table_name, 'TRUNCATE')
                    OR has_table_privilege(table_name, 'REFERENCES')
                    OR has_table_privilege(table_name, 'TRIGGER')
                ), TRUE) AS can_write,
                has_schema_privilege(current_user, 'public', 'CREATE') AS can_create
            FROM unnest($1::text[]) AS exposed(table_name)
            """,
            [
                "public.renaiss_catalog_cards",
                "public.renaiss_user_cards",
                "public.renaiss_events",
            ],
        )
    return bool(row and row["can_select"] and not row["can_write"] and not row["can_create"])


def _catalog_cte() -> str:
    return (
        """
        WITH master AS (
            SELECT
                category,
                local_card_id::text AS local_card_id,
                card_name,
                COALESCE(NULLIF(grade, ''), 'R') AS grade,
                set_code,
                set_name,
                collector_number,
                rarity,
                language,
                image_url
            FROM public.renaiss_catalog_cards
            WHERE is_active = TRUE
              AND category = 'pokemon_tcg'
              AND UPPER(COALESCE(grade, 'R')) IN ("""
        + _GRADE_SQL
        + """
              )
        ), dex AS (
            SELECT
                m.category,
                m.local_card_id,
                m.card_name,
                m.grade,
                m.set_code,
                m.set_name,
                m.collector_number,
                m.rarity,
                m.language,
                m.image_url,
                'catalog'::text AS source_kind,
                COALESCE(owned.quantity, 0)::int AS quantity,
                owned.updated_at AS obtained_at
            FROM master m
            LEFT JOIN LATERAL (
                SELECT
                    COALESCE(SUM(uc.quantity), 0)::int AS quantity,
                    MAX(uc.updated_at) AS updated_at
                FROM public.renaiss_user_cards uc
                WHERE uc.user_id = $1
                  AND uc.category = m.category
                  AND uc.is_tutorial IS NOT TRUE
                  AND (
                      uc.local_card_id::text = m.local_card_id
                      OR (
                          LOWER(BTRIM(uc.card_name)) = LOWER(BTRIM(m.card_name))
                          AND LOWER(BTRIM(COALESCE(uc.set_code, ''))) =
                              LOWER(BTRIM(COALESCE(m.set_code, '')))
                          AND LOWER(BTRIM(COALESCE(uc.collector_number, ''))) =
                              LOWER(BTRIM(COALESCE(m.collector_number, '')))
                      )
                  )
            ) owned ON TRUE

            UNION ALL

            SELECT
                uc.category,
                uc.local_card_id::text,
                uc.card_name,
                COALESCE(NULLIF(uc.grade, ''), 'R'),
                uc.set_code,
                NULL::text,
                uc.collector_number,
                COALESCE(NULLIF(uc.grade, ''), 'R'),
                NULL::text,
                uc.image_url,
                'archived'::text,
                uc.quantity::int,
                uc.updated_at
            FROM public.renaiss_user_cards uc
            WHERE $1 <> 0
              AND uc.user_id = $1
              AND uc.category = 'pokemon_tcg'
              AND uc.is_tutorial IS NOT TRUE
              AND NOT EXISTS (
                  SELECT 1 FROM master m
                  WHERE m.local_card_id = uc.local_card_id::text
                     OR (
                          LOWER(BTRIM(m.card_name)) = LOWER(BTRIM(uc.card_name))
                          AND LOWER(BTRIM(COALESCE(m.set_code, ''))) =
                              LOWER(BTRIM(COALESCE(uc.set_code, '')))
                          AND LOWER(BTRIM(COALESCE(m.collector_number, ''))) =
                              LOWER(BTRIM(COALESCE(uc.collector_number, '')))
                     )
              )
        )
        """
    )


async def get_collection(
    user_id: int | None,
    *,
    page: int = 1,
    per_page: int = 24,
    search: str = "",
    grade: str = "all",
    set_code: str = "all",
    owned: str = "all",
    sort: str = "set",
) -> dict[str, Any]:
    authenticated = user_id is not None
    db_user_id = int(user_id or 0)
    page = max(1, int(page))
    per_page = max(12, min(60, int(per_page)))
    search = str(search or "").strip()[:80]
    grade = str(grade or "all").strip().upper()
    set_code = str(set_code or "all").strip()[:40]
    owned = str(owned or "all").strip().lower()
    sort = str(sort or "set").strip().lower()
    if grade not in {"ALL", *GRADES}:
        grade = "ALL"
    if owned not in {"all", "owned", "missing", "archived", "mine"}:
        owned = "all"
    if not authenticated and owned in {"owned", "archived", "mine"}:
        owned = "all"
    if sort not in {"set", "name", "grade", "recent"}:
        sort = "set"

    pool = await get_db()
    async with pool.acquire() as conn:
        catalog_total = int(
            await conn.fetchval(
                """
                SELECT COUNT(*)::int
                FROM public.renaiss_catalog_cards
                WHERE is_active = TRUE
                  AND category = 'pokemon_tcg'
                  AND UPPER(COALESCE(grade, 'R')) IN ("""
                + _GRADE_SQL
                + ")"
            )
            or 0
        )
        if catalog_total < MIN_PUBLIC_CATALOG_CARDS:
            return _empty_collection(authenticated=authenticated)

        if sort == "grade":
            order_sql = f"{_GRADE_ORDER_SQL} DESC, d.card_name ASC"
        elif sort == "recent":
            order_sql = "d.obtained_at DESC NULLS LAST, d.card_name ASC"
        elif sort == "name":
            order_sql = "d.card_name ASC, d.set_code ASC NULLS LAST"
        else:
            order_sql = (
                "d.set_code ASC NULLS LAST, d.collector_number ASC NULLS LAST, "
                "d.card_name ASC"
            )

        cte = _catalog_cte()
        rows = await conn.fetch(
            cte
            + f"""
            SELECT
                d.category,
                d.local_card_id,
                d.card_name,
                d.grade,
                d.set_code,
                d.set_name,
                d.collector_number,
                d.rarity,
                d.language,
                d.image_url,
                d.source_kind,
                d.quantity,
                d.quantity > 0 AS owned,
                d.obtained_at,
                COUNT(*) OVER()::int AS total_filtered
            FROM dex d
            WHERE (
                $2 = ''
                OR d.card_name ILIKE '%' || $2 || '%'
                OR COALESCE(d.set_name, '') ILIKE '%' || $2 || '%'
                OR COALESCE(d.set_code, '') ILIKE '%' || $2 || '%'
                OR COALESCE(d.collector_number, '') ILIKE '%' || $2 || '%'
            )
              AND ($3 = 'ALL' OR UPPER(d.grade) = $3)
              AND ($4 = 'all' OR COALESCE(d.set_code, '') = $4)
              AND (
                  $5 = 'all'
                  OR ($5 = 'owned' AND d.quantity > 0 AND d.source_kind = 'catalog')
                  OR ($5 = 'missing' AND d.quantity = 0 AND d.source_kind = 'catalog')
                  OR ($5 = 'archived' AND d.quantity > 0 AND d.source_kind = 'archived')
                  OR ($5 = 'mine' AND d.quantity > 0)
              )
            ORDER BY {order_sql}
            LIMIT $6 OFFSET $7
            """,
            db_user_id,
            search,
            grade,
            set_code,
            owned,
            per_page,
            (page - 1) * per_page,
        )
        summary = await conn.fetchrow(
            cte
            + """
            SELECT
                COUNT(*) FILTER (WHERE source_kind = 'catalog')::int AS catalog_total,
                COUNT(DISTINCT NULLIF(set_code, ''))
                    FILTER (WHERE source_kind = 'catalog')::int AS sets_total,
                COUNT(*) FILTER (
                    WHERE source_kind = 'catalog' AND quantity > 0
                )::int AS owned_in_catalog,
                COUNT(*) FILTER (
                    WHERE source_kind = 'archived' AND quantity > 0
                )::int AS archived_owned,
                COALESCE(SUM(quantity) FILTER (WHERE quantity > 0), 0)::int
                    AS total_quantity
            FROM dex
            """,
            db_user_id,
        )
        set_rows = await conn.fetch(
            """
            SELECT
                set_code,
                MAX(NULLIF(set_name, '')) AS set_name,
                COUNT(*)::int AS card_count
            FROM public.renaiss_catalog_cards
            WHERE is_active = TRUE
              AND category = 'pokemon_tcg'
              AND UPPER(COALESCE(grade, 'R')) IN ("""
            + _GRADE_SQL
            + """
              )
              AND NULLIF(set_code, '') IS NOT NULL
            GROUP BY set_code
            ORDER BY COALESCE(MAX(NULLIF(set_name, '')), set_code), set_code
            LIMIT 100
            """
        )

    summary_data = _record_dict(summary) if summary else {}
    owned_count = int(summary_data.get("owned_in_catalog") or 0)
    summary_data["completion_pct"] = round(owned_count / catalog_total * 100, 1)
    total_filtered = int(rows[0]["total_filtered"] or 0) if rows else 0
    cards = []
    for row in rows:
        card = _record_dict(row)
        card.pop("total_filtered", None)
        card.pop("obtained_at", None)
        cards.append(card)
    return {
        "ok": True,
        "available": True,
        "authenticated": authenticated,
        "summary": summary_data,
        "cards": cards,
        "sets": [_record_dict(row) for row in set_rows],
        "grades": list(GRADES),
        "page": page,
        "per_page": per_page,
        "total_filtered": total_filtered,
        "has_more": page * per_page < total_filtered,
    }


async def get_weekly_lucky_leaderboard(
    current_user_id: int | None,
    *,
    limit: int = 20,
) -> dict[str, Any]:
    limit = max(3, min(50, int(limit)))
    pool = await get_db()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            WITH summary AS (
                SELECT
                    user_id,
                    COUNT(*)::int AS lucky_catches
                FROM public.renaiss_events
                WHERE event_name = 'catch_won'
                  AND user_id IS NOT NULL
                  AND created_at >= (
                      date_trunc('week', now() AT TIME ZONE 'Asia/Seoul')
                      AT TIME ZONE 'Asia/Seoul'
                  )
                GROUP BY user_id
            ), ranked AS (
                SELECT
                    user_id,
                    lucky_catches,
                    DENSE_RANK() OVER (ORDER BY lucky_catches DESC)::int AS rank
                FROM summary
            )
            SELECT user_id, lucky_catches, rank
            FROM ranked
            ORDER BY rank ASC, user_id ASC
            LIMIT $1
            """,
            limit,
        )
    result = []
    for row in rows:
        user_id = int(row["user_id"])
        result.append(
            {
                "rank": int(row["rank"] or 0),
                "display_name": collector_pseudonym(user_id),
                "lucky_catches": int(row["lucky_catches"] or 0),
                "is_me": current_user_id is not None and user_id == int(current_user_id),
            }
        )
    return {
        "ok": True,
        "available": True,
        "metric": "weekly_lucky_catches",
        "reset_timezone": "Asia/Seoul",
        "rows": result,
    }
