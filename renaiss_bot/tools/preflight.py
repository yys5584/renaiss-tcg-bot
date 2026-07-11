"""Read-only deployment gate for PostgreSQL and exact Renaiss Partner data."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import os
import re
import uuid
from dataclasses import dataclass
from urllib.parse import urlparse

from renaiss_bot.database.connection import (
    close_db,
    database_tls_verification_disabled,
    get_db,
    open_db_session,
)
from renaiss_bot.runtime import (
    PROJECT_ROOT,
    collect_runtime_provenance,
    load_runtime_environment,
)
from renaiss_bot.services.client import fetch_official_price
from renaiss_bot.services.market import (
    daily_pick_configuration_issues,
    daily_pick_probe_card,
    daily_pick_requested,
    exact_price_lookup_configured,
    market_card_eligible,
)
from renaiss_bot.services.models import CardIdentity, card_identity_key

REQUIRED_TABLES = (
    "renaiss_catalog_cards",
    "renaiss_market_board",
    "renaiss_market_price_snapshots",
    "renaiss_market_refresh_leases",
    "renaiss_job_leases",
    "renaiss_api_cooldowns",
    "renaiss_api_request_gates",
    "renaiss_market_picks",
    "renaiss_result_bell_outbox",
    "renaiss_spawn_dispatch",
    "renaiss_flex_daily_slots",
    "renaiss_user_cards",
    "renaiss_pack_events",
    "renaiss_pack_open_requests",
    "renaiss_events",
    "renaiss_referral_links",
    "renaiss_referral_clicks",
)

REQUIRED_COLUMNS = {
    "renaiss_catalog_cards": {
        "local_card_id",
        "category",
        "card_name",
        "grade",
        "is_active",
    },
    "renaiss_market_picks": {
        "user_id",
        "pick_date",
        "board_card_id",
        "entry_fmv_usd",
        "entry_price_updated_at",
        "community_chat_id",
        "picked_at",
        "settles_at",
        "settlement_snapshot_id",
        "settled_at",
    },
    "renaiss_result_bell_outbox": {
        "event_key",
        "chat_id",
        "bell_date",
        "cohort_pick_date",
        "state",
        "message_text",
        "metrics",
        "suppression_reason",
        "available_at",
        "expires_at",
        "next_attempt_at",
        "attempt_count",
        "attempt_token",
        "lease_owner",
        "lease_expires_at",
        "attempted_at",
        "telegram_message_id",
        "last_error_code",
        "last_error",
        "updated_at",
        "sent_at",
    },
    "renaiss_market_board": {
        "id",
        "week_start",
        "category",
        "local_card_id",
        "initial_fmv_usd",
        "pick_eligible",
        "valuation_method",
    },
    "renaiss_market_price_snapshots": {
        "id",
        "board_card_id",
        "fmv_usd",
        "pick_eligible",
        "price_updated_at",
        "asset_url",
        "valuation_method",
    },
    "renaiss_user_cards": {
        "user_id",
        "category",
        "local_card_id",
        "card_name",
        "quantity",
        "is_tutorial",
    },
    "renaiss_market_refresh_leases": {
        "board_card_id",
        "lease_token",
        "lease_until",
        "attempt_count",
        "last_status",
        "last_started_at",
        "last_completed_at",
    },
    "renaiss_job_leases": {
        "job_name",
        "lease_owner",
        "acquired_at",
        "lease_expires_at",
        "next_attempt_at",
        "last_status",
        "updated_at",
    },
    "renaiss_api_cooldowns": {
        "api_name",
        "blocked_until",
        "reason",
        "updated_at",
    },
    "renaiss_api_request_gates": {
        "api_name",
        "next_request_at",
        "updated_at",
    },
    "renaiss_pack_events": {"request_id"},
    "renaiss_pack_open_requests": {
        "request_id",
        "user_id",
        "chat_id",
        "platform",
        "category",
        "pack_type",
        "pack_count",
        "used_before",
        "quota_date",
        "status",
        "reserved_until",
        "updated_at",
    },
    "renaiss_flex_daily_slots": {
        "user_id",
        "flex_date",
        "reservation_token",
        "chat_id",
        "state",
        "message_id",
        "sent_at",
    },
    "renaiss_spawn_dispatch": {
        "chat_id",
        "spawn_date",
        "dispatched_count",
        "lease_token",
        "lease_expires_at",
        "last_dispatched_at",
    },
    "renaiss_events": {
        "id",
        "event_name",
        "event_key",
        "user_id",
        "chat_id",
        "session_id",
        "metadata",
        "created_at",
    },
}

# Runtime-critical type/null/default contracts. A legacy table with compatible
# names but incompatible shapes must not pass a production deployment gate.
REQUIRED_COLUMN_SHAPES = {
    ("renaiss_catalog_cards", "local_card_id"): ("text", "NO", ()),
    ("renaiss_catalog_cards", "is_active"): ("bool", "NO", ("true",)),
    ("renaiss_user_cards", "user_id"): ("int8", "NO", ()),
    ("renaiss_user_cards", "category"): ("text", "NO", ()),
    ("renaiss_user_cards", "local_card_id"): ("text", "NO", ()),
    ("renaiss_user_cards", "quantity"): ("int4", "NO", ("1",)),
    ("renaiss_user_cards", "is_tutorial"): ("bool", "NO", ("false",)),
    ("renaiss_market_board", "id"): ("int8", "NO", ("nextval",)),
    ("renaiss_market_board", "week_start"): ("date", "NO", ()),
    ("renaiss_market_board", "initial_fmv_usd"): ("numeric", "NO", ()),
    ("renaiss_market_board", "pick_eligible"): ("bool", "NO", ("false",)),
    ("renaiss_market_board", "valuation_method"): ("text", "YES", ()),
    ("renaiss_market_price_snapshots", "id"): ("int8", "NO", ("nextval",)),
    ("renaiss_market_price_snapshots", "board_card_id"): ("int8", "NO", ()),
    ("renaiss_market_price_snapshots", "fmv_usd"): ("numeric", "NO", ()),
    ("renaiss_market_price_snapshots", "pick_eligible"): (
        "bool",
        "NO",
        ("false",),
    ),
    ("renaiss_market_price_snapshots", "price_updated_at"): (
        "timestamptz",
        "YES",
        (),
    ),
    ("renaiss_market_price_snapshots", "valuation_method"): (
        "text",
        "YES",
        (),
    ),
    ("renaiss_market_refresh_leases", "board_card_id"): ("int8", "NO", ()),
    ("renaiss_market_refresh_leases", "lease_until"): (
        "timestamptz",
        "NO",
        ("now",),
    ),
    ("renaiss_job_leases", "job_name"): ("text", "NO", ()),
    ("renaiss_api_cooldowns", "api_name"): ("text", "NO", ()),
    ("renaiss_api_cooldowns", "blocked_until"): (
        "timestamptz",
        "NO",
        ("-infinity",),
    ),
    ("renaiss_api_request_gates", "api_name"): ("text", "NO", ()),
    ("renaiss_api_request_gates", "next_request_at"): (
        "timestamptz",
        "NO",
        ("-infinity",),
    ),
    ("renaiss_flex_daily_slots", "user_id"): ("int8", "NO", ()),
    ("renaiss_flex_daily_slots", "flex_date"): ("date", "NO", ()),
    ("renaiss_flex_daily_slots", "reservation_token"): ("text", "NO", ()),
    ("renaiss_flex_daily_slots", "state"): (
        "text",
        "NO",
        ("reserved",),
    ),
    ("renaiss_spawn_dispatch", "chat_id"): ("int8", "NO", ()),
    ("renaiss_spawn_dispatch", "dispatched_count"): ("int4", "NO", ("0",)),
    ("renaiss_pack_open_requests", "request_id"): ("text", "NO", ()),
    ("renaiss_pack_open_requests", "pack_count"): ("int4", "NO", ()),
    ("renaiss_pack_open_requests", "status"): (
        "text",
        "NO",
        ("reserved",),
    ),
    ("renaiss_events", "event_key"): ("text", "YES", ()),
    ("renaiss_events", "metadata"): ("jsonb", "NO", ("{}",)),
}

REQUIRED_INDEXES = {
    "idx_renaiss_pack_events_request": {
        "table": "renaiss_pack_events",
        "columns": ("request_id",),
        "unique": True,
        "predicate_terms": ("request_id", "is not null"),
    },
    "idx_renaiss_market_picks_chat_date": {
        "table": "renaiss_market_picks",
        "columns": ("community_chat_id", "pick_date"),
        "unique": False,
        "predicate_terms": (),
    },
    "idx_renaiss_market_refresh_leases_until": {
        "table": "renaiss_market_refresh_leases",
        "columns": ("lease_until",),
        "unique": False,
        "predicate_terms": (),
    },
    "idx_renaiss_result_bell_dispatch": {
        "table": "renaiss_result_bell_outbox",
        "columns": ("state", "available_at", "next_attempt_at"),
        "unique": False,
        "predicate_terms": ("state", "pending", "retryable", "claimed", "inflight"),
    },
    "idx_renaiss_flex_daily_slots_state": {
        "table": "renaiss_flex_daily_slots",
        "columns": ("state", "updated_at"),
        "unique": False,
        "predicate_terms": (),
    },
    "idx_renaiss_spawn_dispatch_lease": {
        "table": "renaiss_spawn_dispatch",
        "columns": ("lease_expires_at",),
        "unique": False,
        "predicate_terms": (),
    },
}

REQUIRED_CONSTRAINTS = {
    "catalog primary key": (
        "renaiss_catalog_cards",
        "p",
        "primarykey(local_card_id)",
    ),
    "user cards primary key": (
        "renaiss_user_cards",
        "p",
        "primarykey(user_id,category,local_card_id)",
    ),
    "market board primary key": (
        "renaiss_market_board",
        "p",
        "primarykey(id)",
    ),
    "market board weekly card": (
        "renaiss_market_board",
        "u",
        "unique(week_start,category,local_card_id)",
    ),
    "market snapshot primary key": (
        "renaiss_market_price_snapshots",
        "p",
        "primarykey(id)",
    ),
    "market snapshot board foreign key": (
        "renaiss_market_price_snapshots",
        "f",
        "foreignkey(board_card_id)referencesrenaiss_market_board(id)ondeletecascade",
    ),
    "market refresh primary key": (
        "renaiss_market_refresh_leases",
        "p",
        "primarykey(board_card_id)",
    ),
    "market refresh board foreign key": (
        "renaiss_market_refresh_leases",
        "f",
        "foreignkey(board_card_id)referencesrenaiss_market_board(id)ondeletecascade",
    ),
    "pack request primary key": (
        "renaiss_pack_open_requests",
        "p",
        "primarykey(request_id)",
    ),
    "job lease primary key": (
        "renaiss_job_leases",
        "p",
        "primarykey(job_name)",
    ),
    "API cooldown primary key": (
        "renaiss_api_cooldowns",
        "p",
        "primarykey(api_name)",
    ),
    "API request gate primary key": (
        "renaiss_api_request_gates",
        "p",
        "primarykey(api_name)",
    ),
    "flex daily primary key": (
        "renaiss_flex_daily_slots",
        "p",
        "primarykey(user_id,flex_date)",
    ),
    "flex reservation token": (
        "renaiss_flex_daily_slots",
        "u",
        "unique(reservation_token)",
    ),
    "spawn dispatch primary key": (
        "renaiss_spawn_dispatch",
        "p",
        "primarykey(chat_id)",
    ),
    "referral link primary key": (
        "renaiss_referral_links",
        "p",
        "primarykey(token_hash)",
    ),
    "market pick primary key": (
        "renaiss_market_picks",
        "p",
        "primarykey(user_id,pick_date)",
    ),
    "market pick board foreign key": (
        "renaiss_market_picks",
        "f",
        "foreignkey(board_card_id)referencesrenaiss_market_board(id)",
    ),
    "market pick settlement foreign key": (
        "renaiss_market_picks",
        "f",
        "foreignkey(settlement_snapshot_id)referencesrenaiss_market_price_snapshots(id)",
    ),
    "result bell primary key": (
        "renaiss_result_bell_outbox",
        "p",
        "primarykey(id)",
    ),
    "result bell event key": (
        "renaiss_result_bell_outbox",
        "u",
        "unique(event_key)",
    ),
    "result bell daily chat": (
        "renaiss_result_bell_outbox",
        "u",
        "unique(chat_id,bell_date)",
    ),
    "result bell cohort chat": (
        "renaiss_result_bell_outbox",
        "u",
        "unique(chat_id,cohort_pick_date)",
    ),
    "event idempotency key": (
        "renaiss_events",
        "u",
        "unique(event_key)",
    ),
    "event primary key": (
        "renaiss_events",
        "p",
        "primarykey(id)",
    ),
}

REQUIRED_CHECK_CONSTRAINTS = {
    "pack request positive count": (
        "renaiss_pack_open_requests",
        ("pack_count", ">", "0"),
    ),
    "pack request state domain": (
        "renaiss_pack_open_requests",
        ("status", "reserved", "completed", "failed", "expired"),
    ),
    "flex state domain": (
        "renaiss_flex_daily_slots",
        ("state", "reserved", "sent", "delivery_unknown", "dead"),
    ),
    "flex sent delivery evidence": (
        "renaiss_flex_daily_slots",
        ("state", "sent", "message_id", "sent_at"),
    ),
    "spawn nonnegative count": (
        "renaiss_spawn_dispatch",
        ("dispatched_count", ">=", "0"),
    ),
}


@dataclass(frozen=True)
class CheckResult:
    name: str
    ok: bool
    detail: str


def _single_line(value, *, limit: int = 80) -> str:
    text = " ".join(str(value or "").split())
    return text[:limit]


def _partner_release_configuration_issue() -> str | None:
    """Pin credentialed release probes to the approved Renaiss API origin."""
    raw_base = os.getenv(
        "RENAISS_API_BASE_URL", "https://api.renaissos.com"
    ).strip()
    try:
        parsed = urlparse(raw_base)
        port = parsed.port
    except ValueError:
        return "Partner API release origin is invalid"
    if (
        parsed.scheme != "https"
        or (parsed.hostname or "").lower() != "api.renaissos.com"
        or port not in {None, 443}
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        return "Partner API release origin is not the approved Renaiss origin"
    allowed_hosts = {
        host.strip().lower()
        for host in os.getenv(
            "RENAISS_API_ALLOWED_HOSTS", "api.renaissos.com"
        ).split(",")
        if host.strip()
    }
    if allowed_hosts != {"api.renaissos.com"}:
        return "Partner API release allowlist must contain only the approved host"
    key_header = os.getenv("RENAISS_API_KEY_HEADER", "X-Api-Key").strip()
    secret_header = os.getenv("RENAISS_API_SECRET_HEADER", "X-Api-Secret").strip()
    if key_header.lower() != "x-api-key" or secret_header.lower() != "x-api-secret":
        return "Partner API release credential headers are not approved"
    return None


def _env_selection_label(path) -> str:
    if path is None:
        return "invalid selection"
    try:
        relative = path.relative_to(PROJECT_ROOT)
    except ValueError:
        digest = hashlib.sha256(
            os.path.normcase(str(path)).encode("utf-8", errors="replace")
        ).hexdigest()[:12]
        return f"external-path sha256={digest}"
    return f"project:{relative.as_posix()}"


def _masked_database_target(
    variable: str = "DATABASE_URL",
) -> tuple[bool, str]:
    """Return a useful DB endpoint label without user, password, or query values."""
    raw = os.getenv(variable, "").strip()
    if not raw:
        return True, "unset"
    try:
        parsed = urlparse(raw)
        scheme = parsed.scheme.lower()
        port = parsed.port
        hostname = parsed.hostname or "local"
    except (TypeError, ValueError):
        return False, "configured target could not be safely identified"
    if scheme not in {"postgres", "postgresql"}:
        return False, "configured target is not a supported PostgreSQL URL"
    safe_host = _single_line(hostname, limit=120)
    if not safe_host:
        safe_host = "local"
    if ":" in safe_host and not safe_host.startswith("["):
        safe_host = f"[{safe_host}]"
    database = _single_line((parsed.path or "").lstrip("/") or "default", limit=120)
    port_label = f":{port}" if port is not None else ""
    return True, f"{scheme}://{safe_host}{port_label}/{database} (credentials masked)"


def runtime_provenance_checks(*, require_database: bool) -> list[CheckResult]:
    """Build non-secret, fail-closed release identity checks."""
    provenance = collect_runtime_provenance()
    interpreter_ok = provenance.interpreter_expectation in {
        "not configured",
        "matched",
    }
    root_ok = provenance.root_expectation in {"not configured", "matched"}
    env_label = _env_selection_label(provenance.env_file)
    partner_issue = _partner_release_configuration_issue()
    database_valid, database_label = _masked_database_target()
    lock_database_valid, lock_database_label = _masked_database_target(
        "RENAISS_TELEGRAM_LOCK_DATABASE_URL"
    )
    database_present = database_label != "unset"
    lock_database_present = lock_database_label != "unset"
    database_ok = database_valid and (database_present or not require_database)
    lock_database_ok = lock_database_valid and (
        lock_database_present or not require_database
    )
    database_tls_ok = not (
        require_database and database_tls_verification_disabled()
    )
    if not database_present and not require_database:
        database_label += "; DB check explicitly skipped"
    if not lock_database_present and not require_database:
        lock_database_label += "; Telegram lock DB check explicitly skipped"
    return [
        CheckResult(
            "Runtime interpreter",
            interpreter_ok,
            f"executable={_single_line(provenance.interpreter, limit=260)}; "
            f"RENAISS_PYTHON_EXE={provenance.interpreter_expectation}",
        ),
        CheckResult(
            "Runtime root",
            root_ok,
            f"root={_single_line(provenance.project_root, limit=260)}; "
            f"RENAISS_ROOT={provenance.root_expectation}",
        ),
        CheckResult(
            "Runtime env",
            provenance.env_ready,
            f"selection={provenance.env_selection}; {env_label}; "
            f"file={'present' if provenance.env_file_exists else 'absent'}; "
            f"selection ready={'yes' if provenance.env_ready else 'no'}",
        ),
        CheckResult(
            "Partner origin",
            partner_issue is None,
            partner_issue
            or "origin=https://api.renaissos.com:443; allowlist and credential headers approved",
        ),
        CheckResult(
            "Database target",
            database_ok,
            database_label
            if database_ok
            else (
                "DATABASE_URL is required for this gate"
                if not database_present
                else database_label
            ),
        ),
        CheckResult(
            "Telegram lock database target",
            lock_database_ok,
            (
                lock_database_label
                + "; must be a direct/session-mode PostgreSQL endpoint, not a "
                "transaction pooler"
            )
            if lock_database_ok and lock_database_present
            else (
                lock_database_label
                if lock_database_ok
                else (
                    "RENAISS_TELEGRAM_LOCK_DATABASE_URL direct/session endpoint "
                    "is required for this gate"
                    if not lock_database_present
                    else lock_database_label
                )
            ),
        ),
        CheckResult(
            "Database TLS verification",
            database_tls_ok,
            (
                "certificate and hostname verification required for production"
                if database_tls_ok
                else "RENAISS_DB_SSL_INSECURE must be disabled for a production database gate"
            ),
        ),
    ]


async def _check_database_runtime_capabilities(pool) -> CheckResult:
    """Confirm this endpoint/role can run startup DDL and normal product writes."""
    try:
        async with pool.acquire() as conn:
            runtime = await conn.fetchrow(
                """
                SELECT current_setting('transaction_read_only') = 'off' AS writable,
                       NOT pg_is_in_recovery() AS primary_endpoint,
                       current_schema() AS current_schema,
                       has_schema_privilege(current_user, 'public', 'USAGE') AS schema_usage,
                       has_schema_privilege(current_user, 'public', 'CREATE') AS schema_create
                """
            )
            table_rows = await conn.fetch(
                """
                SELECT requested.name,
                       has_table_privilege(
                           current_user,
                           format('public.%I', requested.name),
                           'SELECT,INSERT,UPDATE,DELETE'
                       ) AS dml,
                       pg_has_role(table_rel.relowner, 'USAGE') AS can_alter
                FROM unnest($1::text[]) AS requested(name)
                JOIN pg_class table_rel
                  ON table_rel.oid = to_regclass('public.' || requested.name)
                """,
                list(REQUIRED_TABLES),
            )
            sequence_rows = await conn.fetch(
                """
                WITH serial_columns AS (
                    SELECT table_name, column_name,
                           pg_get_serial_sequence(
                               format('public.%I', table_name),
                               column_name
                           ) AS sequence_name
                    FROM information_schema.columns
                    WHERE table_schema = 'public'
                      AND table_name = ANY($1::text[])
                      AND column_default LIKE 'nextval(%'
                )
                SELECT table_name, column_name,
                       has_sequence_privilege(
                           current_user,
                           sequence_name,
                           'USAGE,SELECT,UPDATE'
                       ) AS usable
                FROM serial_columns
                WHERE sequence_name IS NOT NULL
                """,
                list(REQUIRED_TABLES),
            )
    except Exception as exc:
        return CheckResult(
            "PostgreSQL runtime",
            False,
            f"capability probe failed ({type(exc).__name__})",
        )
    if not runtime or not runtime["writable"] or not runtime["primary_endpoint"]:
        return CheckResult(
            "PostgreSQL runtime",
            False,
            "a writable primary endpoint is required",
        )
    if str(runtime["current_schema"] or "") != "public":
        return CheckResult(
            "PostgreSQL runtime",
            False,
            "current_schema must resolve to public",
        )
    if not runtime["schema_usage"] or not runtime["schema_create"]:
        return CheckResult(
            "PostgreSQL runtime",
            False,
            "the runtime role needs USAGE and CREATE on schema public",
        )
    missing_table_rights = [
        str(row["name"])
        for row in table_rows
        if not row["dml"] or not row["can_alter"]
    ]
    if missing_table_rights:
        return CheckResult(
            "PostgreSQL runtime",
            False,
            "missing DML/ALTER capability on: " + ", ".join(missing_table_rights),
        )
    missing_sequence_rights = [
        f"{row['table_name']}.{row['column_name']}"
        for row in sequence_rows
        if not row["usable"]
    ]
    if missing_sequence_rights:
        return CheckResult(
            "PostgreSQL runtime",
            False,
            "missing sequence capability on: " + ", ".join(missing_sequence_rights),
        )
    return CheckResult(
        "PostgreSQL runtime",
        True,
        "writable primary, public schema, DDL, DML, and sequence rights verified",
    )


async def check_database() -> CheckResult:
    try:
        pool = await get_db()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT requested.name,
                       to_regclass('public.' || requested.name) IS NOT NULL AS present,
                       COALESCE(table_rel.relrowsecurity, FALSE) AS rls_enabled
                FROM unnest($1::text[]) AS requested(name)
                LEFT JOIN pg_class table_rel
                  ON table_rel.oid = to_regclass('public.' || requested.name)
                ORDER BY requested.name
                """,
                list(REQUIRED_TABLES),
            )
            column_rows = await conn.fetch(
                """
                SELECT table_name, column_name, is_nullable, udt_name, column_default
                FROM information_schema.columns
                WHERE table_schema = 'public'
                  AND table_name = ANY($1::text[])
                """,
                list(REQUIRED_COLUMNS),
            )
            index_rows = {
                index_name: await conn.fetchrow(
                    """
                    SELECT table_rel.relname AS table_name,
                           index_meta.indisunique AS is_unique,
                           index_meta.indisvalid AS is_valid,
                           index_meta.indisready AS is_ready,
                           pg_get_indexdef(index_meta.indexrelid) AS definition,
                           COALESCE(
                               pg_get_expr(index_meta.indpred, index_meta.indrelid),
                               ''
                           ) AS predicate
                    FROM pg_index index_meta
                    JOIN pg_class index_rel
                      ON index_rel.oid = index_meta.indexrelid
                    JOIN pg_class table_rel
                      ON table_rel.oid = index_meta.indrelid
                    JOIN pg_namespace namespace
                      ON namespace.oid = index_rel.relnamespace
                    WHERE namespace.nspname = 'public'
                      AND index_rel.relname = $1
                    """,
                    index_name,
                )
                for index_name in REQUIRED_INDEXES
            }
            constraint_presence = {
                label: await conn.fetchval(
                    """
                    SELECT EXISTS (
                        SELECT 1
                        FROM pg_constraint constraint_meta
                        JOIN pg_class table_rel
                          ON table_rel.oid = constraint_meta.conrelid
                        JOIN pg_namespace namespace
                          ON namespace.oid = table_rel.relnamespace
                        WHERE namespace.nspname = 'public'
                          AND table_rel.relname = $1
                          AND constraint_meta.contype::text = $2
                          AND constraint_meta.convalidated
                          AND regexp_replace(
                              replace(
                                  lower(pg_get_constraintdef(constraint_meta.oid)),
                                  '"',
                                  ''
                              ),
                              '[[:space:]]',
                              '',
                              'g'
                          ) = $3
                    )
                    """,
                    table_name,
                    constraint_type,
                    definition,
                )
                for label, (
                    table_name,
                    constraint_type,
                    definition,
                ) in REQUIRED_CONSTRAINTS.items()
            }
            check_constraint_presence = {
                label: await conn.fetchval(
                    """
                    SELECT EXISTS (
                        SELECT 1
                        FROM pg_constraint constraint_meta
                        JOIN pg_class table_rel
                          ON table_rel.oid = constraint_meta.conrelid
                        JOIN pg_namespace namespace
                          ON namespace.oid = table_rel.relnamespace
                        WHERE namespace.nspname = 'public'
                          AND table_rel.relname = $1
                          AND constraint_meta.contype = 'c'
                          AND constraint_meta.convalidated
                          AND NOT EXISTS (
                              SELECT 1
                              FROM unnest($2::text[]) AS required(term)
                              WHERE strpos(
                                  regexp_replace(
                                      replace(
                                          lower(pg_get_constraintdef(constraint_meta.oid)),
                                          '"',
                                          ''
                                      ),
                                      '[[:space:]]',
                                      '',
                                      'g'
                                  ),
                                  lower(regexp_replace(required.term, '[[:space:]]', '', 'g'))
                              ) = 0
                          )
                    )
                    """,
                    table_name,
                    list(required_terms),
                )
                for label, (
                    table_name,
                    required_terms,
                ) in REQUIRED_CHECK_CONSTRAINTS.items()
            }
            await conn.fetchval("SELECT 1")
    except Exception as exc:
        return CheckResult(
            "PostgreSQL",
            False,
            f"connection/query failed ({type(exc).__name__})",
        )
    missing = [str(row["name"]) for row in rows if not row["present"]]
    if missing:
        return CheckResult("PostgreSQL", False, f"missing tables: {', '.join(missing)}")
    rls_tables = [
        str(row["name"])
        for row in rows
        if bool(row.get("rls_enabled", False))
    ]
    if rls_tables:
        return CheckResult(
            "PostgreSQL",
            False,
            "unexpected row-level security on runtime-owned tables: "
            + ", ".join(rls_tables),
        )
    present_columns: dict[str, set[str]] = {}
    for row in column_rows:
        present_columns.setdefault(str(row["table_name"]), set()).add(str(row["column_name"]))
    missing_columns = [
        f"{table}.{column}"
        for table, required in REQUIRED_COLUMNS.items()
        for column in sorted(required - present_columns.get(table, set()))
    ]
    if missing_columns:
        return CheckResult(
            "PostgreSQL",
            False,
            f"missing migration columns: {', '.join(missing_columns)}",
        )
    column_metadata = {
        (str(row["table_name"]), str(row["column_name"])): row
        for row in column_rows
    }
    incompatible_columns = []
    for key, (udt_name, nullable, default_terms) in REQUIRED_COLUMN_SHAPES.items():
        row = column_metadata.get(key)
        if row is None:
            continue
        default = re.sub(
            r"\s+",
            "",
            str(row.get("column_default") or "").lower(),
        )
        if (
            str(row.get("udt_name") or "") != udt_name
            or str(row.get("is_nullable") or "") != nullable
            or any(term not in default for term in default_terms)
        ):
            incompatible_columns.append(f"{key[0]}.{key[1]}")
    if incompatible_columns:
        return CheckResult(
            "PostgreSQL",
            False,
            "incompatible migration columns: " + ", ".join(incompatible_columns),
        )
    column_nullability = {
        (str(row["table_name"]), str(row["column_name"])): str(row["is_nullable"])
        for row in column_rows
    }
    if column_nullability.get(
        ("renaiss_market_price_snapshots", "price_updated_at")
    ) != "YES":
        return CheckResult(
            "PostgreSQL",
            False,
            "migration incomplete: renaiss_market_price_snapshots.price_updated_at must be nullable",
        )
    missing_indexes = [name for name, row in index_rows.items() if row is None]
    if missing_indexes:
        return CheckResult(
            "PostgreSQL",
            False,
            f"missing migration indexes: {', '.join(missing_indexes)}",
        )
    invalid_indexes = []
    for name, contract in REQUIRED_INDEXES.items():
        row = index_rows[name]
        definition = re.sub(
            r"\s+",
            " ",
            str(row["definition"] or "").replace('"', "").lower(),
        ).strip()
        predicate = re.sub(
            r"\s+",
            " ",
            str(row["predicate"] or "").replace('"', "").lower(),
        ).strip()
        columns = "(" + ", ".join(contract["columns"]) + ")"
        if (
            str(row["table_name"]) != contract["table"]
            or bool(row["is_unique"]) is not contract["unique"]
            or not bool(row["is_valid"])
            or not bool(row["is_ready"])
            or columns not in definition
            or any(term not in predicate for term in contract["predicate_terms"])
        ):
            invalid_indexes.append(name)
    if invalid_indexes:
        return CheckResult(
            "PostgreSQL",
            False,
            "incompatible migration indexes: " + ", ".join(invalid_indexes),
        )
    missing_constraints = [
        label for label, present in constraint_presence.items() if not present
    ]
    missing_constraints.extend(
        label for label, present in check_constraint_presence.items() if not present
    )
    if missing_constraints:
        return CheckResult(
            "PostgreSQL",
            False,
            "missing migration constraints: " + ", ".join(missing_constraints),
        )
    runtime_result = await _check_database_runtime_capabilities(pool)
    if not runtime_result.ok:
        return runtime_result
    return CheckResult(
        "PostgreSQL",
        True,
        "required schema contracts and runtime write capabilities are present",
    )


async def _close_preflight_session(connection) -> str | None:
    """Close a probe session and report only a secret-safe error type."""
    try:
        if connection.is_closed():
            return None
    except Exception as exc:
        try:
            connection.terminate()
        except Exception as terminate_exc:
            return (
                "session state/cleanup failed "
                f"({type(exc).__name__}/{type(terminate_exc).__name__})"
            )
        return f"session state probe failed ({type(exc).__name__})"
    try:
        await asyncio.wait_for(connection.close(timeout=5), timeout=6)
    except Exception as exc:
        try:
            connection.terminate()
        except Exception as terminate_exc:
            return (
                "session cleanup failed "
                f"({type(exc).__name__}/{type(terminate_exc).__name__})"
            )
        return f"graceful session close failed ({type(exc).__name__})"
    try:
        if not connection.is_closed():
            connection.terminate()
            return "session remained open after graceful close"
    except Exception as exc:
        try:
            connection.terminate()
        except Exception as terminate_exc:
            return (
                "session close verification failed "
                f"({type(exc).__name__}/{type(terminate_exc).__name__})"
            )
        return f"session close verification failed ({type(exc).__name__})"
    return None


async def _probe_telegram_lock_domain(
    pool,
    session,
    lock_name: str,
) -> tuple[bool, bool, bool]:
    """Use transaction locks so probing an unsafe pooler cannot strand a lock."""
    async with session.transaction():
        owner = await session.fetchrow(
            """
            SELECT pg_backend_pid() AS backend_pid,
                   pg_try_advisory_xact_lock(
                       hashtextextended($1, 0)
                   ) AS acquired
            """,
            lock_name,
        )
        owner_acquired = bool(owner and owner["backend_pid"] and owner["acquired"])
        if not owner_acquired:
            return False, False, False
        async with pool.acquire() as contender:
            async with contender.transaction():
                contender_acquired = bool(
                    await contender.fetchval(
                        """
                        SELECT pg_try_advisory_xact_lock(
                            hashtextextended($1, 0)
                        )
                        """,
                        lock_name,
                    )
                )
    if contender_acquired:
        return True, True, False
    async with pool.acquire() as verifier:
        async with verifier.transaction():
            released_for_reacquire = bool(
                await verifier.fetchval(
                    """
                    SELECT pg_try_advisory_xact_lock(
                        hashtextextended($1, 0)
                    )
                    """,
                    lock_name,
                )
            )
    return True, False, released_for_reacquire


async def check_telegram_lock_database() -> CheckResult:
    """Smoke-test that both DSNs currently share one advisory-lock domain."""
    lock_name = f"renaiss:preflight:{uuid.uuid4().hex}"
    session = None
    result: CheckResult | None = None
    try:
        pool = await get_db()
        session = await open_db_session(
            dsn_variable="RENAISS_TELEGRAM_LOCK_DATABASE_URL"
        )
        (
            owner_acquired,
            contender_acquired,
            released_for_reacquire,
        ) = await asyncio.wait_for(
            _probe_telegram_lock_domain(pool, session, lock_name), timeout=15
        )
        if not owner_acquired:
            result = CheckResult(
                "Telegram lock PostgreSQL",
                False,
                "lock DSN could not acquire a unique transaction-scoped probe lock",
            )
        elif contender_acquired:
            result = CheckResult(
                "Telegram lock PostgreSQL",
                False,
                "DATABASE_URL and the lock DSN do not demonstrate one "
                "advisory-lock domain",
            )
        elif not released_for_reacquire:
            result = CheckResult(
                "Telegram lock PostgreSQL",
                False,
                "transaction-scoped probe lock was not released for reacquisition",
            )
        else:
            result = CheckResult(
                "Telegram lock PostgreSQL",
                True,
                "same advisory-lock domain observed; direct/session endpoint "
                "mode still requires operator verification",
            )
    except Exception as exc:
        result = CheckResult(
            "Telegram lock PostgreSQL",
            False,
            f"connection/query failed ({type(exc).__name__})",
        )
    finally:
        if session is not None:
            close_issue = await _close_preflight_session(session)
            if close_issue:
                result = CheckResult("Telegram lock PostgreSQL", False, close_issue)
    return result or CheckResult(
        "Telegram lock PostgreSQL",
        False,
        "probe did not produce a result",
    )


def _card_from_args(args: argparse.Namespace) -> CardIdentity:
    return CardIdentity(
        category=args.category,
        card_name=args.card_name,
        set_name=args.set_name,
        set_code=args.set_code,
        collector_number=args.item_no,
        language=args.language,
        grade=args.grade,
        metadata={"variation": args.variation},
    )


async def check_exact_price(args: argparse.Namespace) -> CheckResult:
    card = _card_from_args(args)
    try:
        price = await fetch_official_price(card, timeout_seconds=args.timeout)
    except Exception as exc:
        return CheckResult(
            "Renaiss exact price",
            False,
            f"request failed ({type(exc).__name__})",
        )
    if price is None:
        return CheckResult("Renaiss exact price", False, "no response/404 for structural tuple")
    details = (
        f"status={_single_line(price.status)} "
        f"confidence={_single_line(price.confidence or '-')} "
        f"score={price.confidence_score} sources={price.source_count} "
        f"method={_single_line(price.valuation_method or '-')} "
        f"fmv={price.fmv_usd} source_url={'yes' if price.asset_url else 'no'}"
    )
    if not market_card_eligible(card, price):
        return CheckResult("Renaiss exact price", False, f"Daily Pick gate rejected: {details}")
    return CheckResult("Renaiss exact price", True, details)


async def check_telegram_live(*, timeout_seconds: float = 10.0) -> CheckResult:
    """Verify bot identity and official-room membership without sending messages."""
    token = os.getenv("RENAISS_BOT_TOKEN", "").strip()
    raw_expected_bot_id = os.getenv("RENAISS_EXPECTED_BOT_ID", "").strip()
    raw_chat_id = os.getenv("RENAISS_OFFICIAL_CHAT_ID", "").strip()
    missing = []
    if not token:
        missing.append("RENAISS_BOT_TOKEN")
    try:
        expected_bot_id = int(raw_expected_bot_id)
    except ValueError:
        expected_bot_id = 0
    if expected_bot_id <= 0:
        missing.append("RENAISS_EXPECTED_BOT_ID")
    try:
        chat_id = int(raw_chat_id)
    except ValueError:
        chat_id = 0
    if chat_id >= 0:
        missing.append("RENAISS_OFFICIAL_CHAT_ID")
    if missing:
        return CheckResult(
            "Telegram live",
            False,
            "missing or invalid settings: " + ", ".join(missing),
        )

    from telegram import Bot

    async def probe():
        async with Bot(token=token) as bot:
            identity = bot.bot
            if int(identity.id) != expected_bot_id:
                return identity, None, None, None
            webhook = await bot.get_webhook_info()
            if str(getattr(webhook, "url", "")).strip():
                return identity, webhook, None, None
            chat = await bot.get_chat(chat_id)
            member = await bot.get_chat_member(chat_id, identity.id)
            return identity, webhook, chat, member

    try:
        timeout = min(30.0, max(2.0, float(timeout_seconds)))
        identity, webhook, chat, member = await asyncio.wait_for(
            probe(), timeout=timeout
        )
    except Exception as exc:
        return CheckResult(
            "Telegram live",
            False,
            f"read-only Bot API probe failed ({type(exc).__name__})",
        )
    if int(identity.id) != expected_bot_id:
        return CheckResult(
            "Telegram live",
            False,
            "getMe identity does not match RENAISS_EXPECTED_BOT_ID",
        )
    if str(getattr(webhook, "url", "")).strip():
        return CheckResult(
            "Telegram live",
            False,
            "an existing webhook conflicts with the standalone long-polling deployment",
        )
    chat_type = str(getattr(chat, "type", "")).lower()
    if chat_type not in {"group", "supergroup"}:
        return CheckResult(
            "Telegram live",
            False,
            "official chat is not a group or supergroup",
        )
    member_status = str(getattr(member, "status", "")).lower()
    privacy_disabled = bool(
        getattr(identity, "can_read_all_group_messages", False)
    )
    is_administrator = member_status in {"administrator", "creator", "owner"}
    daily_pick_requested = os.getenv("RENAISS_DAILY_PICK_ENABLED", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    if daily_pick_requested and not is_administrator:
        return CheckResult(
            "Telegram live",
            False,
            "official-room administrator status is required for Daily Pick membership checks",
        )
    if (
        not is_administrator
        and not privacy_disabled
    ):
        return CheckResult(
            "Telegram live",
            False,
            "plain-c delivery requires either official-room administrator status "
            "or BotFather privacy disabled",
        )
    return CheckResult(
        "Telegram live",
        True,
        "bot identity, official group, and plain-c visibility verified; "
        "plain-c delivery still requires a non-admin member E2E check",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--skip-db", action="store_true")
    parser.add_argument("--check-api", action="store_true")
    parser.add_argument(
        "--check-telegram-config",
        action="store_true",
        help="validate local Telegram startup settings without calling Telegram",
    )
    parser.add_argument(
        "--check-telegram-live",
        action="store_true",
        help="call read-only getMe/getChat/getChatMember checks; sends no messages",
    )
    parser.add_argument(
        "--require-daily-pick",
        action="store_true",
        help="fail unless the optional Daily Pick production configuration is complete",
    )
    parser.add_argument("--category", default="pokemon_tcg")
    parser.add_argument("--card-name", default="")
    parser.add_argument("--set-name", default="")
    parser.add_argument("--set-code", default="")
    parser.add_argument("--item-no", default="")
    parser.add_argument("--variation", default="")
    parser.add_argument("--language", default="English")
    parser.add_argument("--grade", default="RAW")
    parser.add_argument("--timeout", type=float, default=5.0)
    parser.add_argument("--telegram-timeout", type=float, default=10.0)
    return parser


async def run(args: argparse.Namespace) -> int:
    results = runtime_provenance_checks(require_database=not args.skip_db)
    if not all(result.ok for result in results):
        for result in results:
            mark = "PASS" if result.ok else "FAIL"
            print(f"[{mark}] {result.name}: {result.detail}")
        return 1
    daily_pick_required = args.require_daily_pick or daily_pick_requested()
    config_issues = []
    if daily_pick_required:
        config_issues = daily_pick_configuration_issues()
        results.append(
            CheckResult(
                "Daily Pick configuration",
                not config_issues,
                "; ".join(config_issues) if config_issues else "safe configuration present",
            )
        )
    else:
        results.append(
            CheckResult(
                "Daily Pick configuration",
                True,
                "not requested; optional gate skipped",
            )
        )
    if args.check_telegram_config:
        from renaiss_bot.main import _production_startup_issues

        telegram_issues = _production_startup_issues()
        results.append(
            CheckResult(
                "Telegram configuration",
                not telegram_issues,
                "; ".join(telegram_issues)
                if telegram_issues
                else "required local settings are present",
            )
        )
    if args.check_telegram_live:
        results.append(
            await check_telegram_live(timeout_seconds=args.telegram_timeout)
        )
    if not args.skip_db:
        results.append(await check_database())
        results.append(await check_telegram_lock_database())
    if args.check_api:
        missing_identity = [
            name
            for name, value in (
                ("--card-name", args.card_name),
                ("--set-name", args.set_name),
                ("--item-no", args.item_no),
                ("--language", args.language),
            )
            if not str(value).strip()
        ]
        api_gate_issue = None
        release_configuration_issue = _partner_release_configuration_issue()
        if release_configuration_issue:
            api_gate_issue = release_configuration_issue
        elif not exact_price_lookup_configured():
            api_gate_issue = "exact Partner configuration is incomplete or unapproved"
        elif daily_pick_required and config_issues:
            api_gate_issue = "Daily Pick configuration must pass before the live API call"
        elif daily_pick_required and card_identity_key(
            _card_from_args(args)
        ) != card_identity_key(daily_pick_probe_card()):
            api_gate_issue = (
                "CLI structural tuple does not match RENAISS_DAILY_PICK_PROBE_*"
            )
        if missing_identity:
            results.append(
                CheckResult(
                    "Renaiss exact price",
                    False,
                    f"missing arguments: {', '.join(missing_identity)}",
                )
            )
        elif api_gate_issue:
            results.append(
                CheckResult("Renaiss exact price", False, api_gate_issue)
            )
        else:
            results.append(await check_exact_price(args))
    for result in results:
        mark = "PASS" if result.ok else "FAIL"
        print(f"[{mark}] {result.name}: {result.detail}")
    return 0 if all(result.ok for result in results) else 1


def main() -> None:
    load_runtime_environment()
    args = build_parser().parse_args()

    async def execute() -> int:
        code = 1
        try:
            code = await run(args)
        except Exception as exc:
            print(f"[FAIL] Preflight execution: failed (error={type(exc).__name__})")
        finally:
            try:
                await close_db()
            except Exception as exc:
                print(f"[FAIL] Preflight DB cleanup: failed (error={type(exc).__name__})")
                code = 1
        return code

    code = asyncio.run(execute())
    raise SystemExit(code)


if __name__ == "__main__":
    main()
