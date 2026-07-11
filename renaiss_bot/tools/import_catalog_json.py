"""Import Renaiss catalog cards from a JSON file.

Expected input: a JSON array of objects. Common key aliases are accepted:
name/card_name/title, id/local_card_id/card_id, set/set_code, number/collector_number,
price_usd/fmv_usd/market_price_usd.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import math
import os
import re
from collections import Counter
from pathlib import Path
from typing import Any

from dotenv import dotenv_values

from renaiss_bot.database.connection import close_db, get_db
from renaiss_bot.runtime import load_runtime_environment, resolve_runtime_path
from renaiss_bot.database.schema import create_tables
from renaiss_bot.services.manifest_signature import (
    VerifiedManifest,
    assert_verified_manifest,
    verify_manifest,
)
from renaiss_bot.services.pack_rules import normalize_grade


_EXPLICIT_IMPORT_ENV_KEYS = frozenset(
    {
        "DATABASE_URL",
        "RENAISS_DB_SSL_INSECURE",
        "RENAISS_DB_POOL_MAX",
        "RENAISS_MANIFEST_PUBLIC_KEY_SHA256",
        "RENAISS_MANIFEST_SHA256",
    }
)


def _first(payload: dict[str, Any], *keys: str, default: Any = None) -> Any:
    for key in keys:
        value = payload.get(key)
        if value not in (None, ""):
            return value
    return default


def _float_or_none(value: Any) -> float | None:
    try:
        if value in (None, ""):
            return None
        number = float(str(value).replace("$", "").replace(",", "").strip())
        return number if math.isfinite(number) and number > 0 else None
    except (TypeError, ValueError, OverflowError):
        return None


def _normalized_identity_value(value: Any) -> str:
    return " ".join(str(value or "").strip().lower().split())


def _row_structural_identity(row: dict[str, Any]) -> tuple[str, ...] | None:
    metadata = row.get("metadata") or {}
    identity = (
        _normalized_identity_value(row.get("category")),
        _normalized_identity_value(row.get("card_name")),
        _normalized_identity_value(row.get("set_name"))
        or _normalized_identity_value(row.get("set_code")),
        _normalized_identity_value(row.get("collector_number")),
        _normalized_identity_value(row.get("language")),
        _normalized_identity_value(
            metadata.get("variation") or metadata.get("variant")
        ),
    )
    return identity if all(identity[:5]) else None


async def _find_structural_duplicate(conn, row: dict[str, Any]) -> dict[str, Any] | None:
    identity = _row_structural_identity(row)
    if identity is None:
        return None
    duplicate = await conn.fetchrow(
        """
        SELECT local_card_id, metadata
        FROM renaiss_catalog_cards
        WHERE is_active = TRUE
          AND local_card_id <> $1
          AND regexp_replace(lower(btrim(category)), '\\s+', ' ', 'g') = $2
          AND regexp_replace(lower(btrim(card_name)), '\\s+', ' ', 'g') = $3
          AND regexp_replace(
                lower(COALESCE(NULLIF(btrim(set_name), ''), btrim(set_code))),
                '\\s+', ' ', 'g'
              ) = $4
          AND regexp_replace(lower(btrim(collector_number)), '\\s+', ' ', 'g') = $5
          AND regexp_replace(lower(btrim(language)), '\\s+', ' ', 'g') = $6
          AND regexp_replace(
                lower(btrim(COALESCE(
                    metadata->>'variation',
                    metadata->>'variant',
                    metadata->'source_payload'->>'variation',
                    metadata->'source_payload'->>'variant',
                    ''
                ))),
                '\\s+', ' ', 'g'
              ) = $7
        LIMIT 1
        """,
        row["local_card_id"],
        *identity,
    )
    if duplicate is None:
        return None
    try:
        metadata = duplicate["metadata"]
    except (KeyError, TypeError):
        metadata = None
    if isinstance(metadata, str):
        try:
            metadata = json.loads(metadata)
        except json.JSONDecodeError:
            metadata = {}
    return {
        "local_card_id": str(duplicate["local_card_id"]),
        "metadata": metadata if isinstance(metadata, dict) else {},
    }


async def _assert_no_structural_duplicate(conn, row: dict[str, Any]) -> None:
    """Fence legacy/provider aliases before they can coexist as active cards."""
    duplicate = await _find_structural_duplicate(conn, row)
    if duplicate is not None:
        duplicate_id = duplicate["local_card_id"]
        raise ValueError(
            "catalog structural identity conflict: "
            f"{row['local_card_id']} duplicates {duplicate_id}"
        )


async def _resolve_generic_import_identity(conn, row: dict[str, Any]) -> None:
    """Reuse an older generated catalog ID while moving to the stable hash."""
    duplicate = await _find_structural_duplicate(conn, row)
    if duplicate is None:
        return
    duplicate_id = duplicate["local_card_id"]
    existing_metadata = duplicate["metadata"]
    incoming_metadata = row.get("metadata") or {}
    prefix = f"catalog:{row['category']}:"
    is_generated_input = not (row.get("metadata") or {}).get("source_local_card_id")
    generated_id = re.fullmatch(re.escape(prefix) + r"[0-9a-f]{24}", duplicate_id)
    existing_is_generated = (
        generated_id is not None
        and not existing_metadata.get("source_local_card_id")
    )
    existing_origin = str(existing_metadata.get("evidence_origin") or "unsigned")
    incoming_origin = str(incoming_metadata.get("evidence_origin") or "unsigned")
    if (
        is_generated_input
        and existing_is_generated
        and existing_origin == incoming_origin
    ):
        row["local_card_id"] = duplicate_id
        return
    raise ValueError(
        f"catalog structural identity conflict: {row['local_card_id']} duplicates {duplicate_id}"
    )


def _generated_local_card_id(
    raw: dict[str, Any],
    *,
    category: str,
    card_name: str,
) -> str:
    identity = {
        "category": category.strip().lower(),
        "card_name": card_name.strip().lower(),
        "set": str(_first(raw, "set_code", "set", "series_code", "collection", default="")).strip().lower(),
        "set_name": str(_first(raw, "set_name", "series_name", "collection_name", default="")).strip().lower(),
        "collector_number": str(
            _first(raw, "collector_number", "number", "card_number", "item_number", default="")
        ).strip().lower(),
        "variation": str(_first(raw, "variation", "variant", default="")).strip().lower(),
        "language": str(raw.get("language") or "Japanese").strip().lower(),
    }
    canonical = json.dumps(identity, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:24]
    return f"catalog:{category}:{digest}"


def _normalize_row(
    raw: dict[str, Any],
    category: str,
    *,
    trusted_evidence: bool = False,
    manifest_sha256: str | None = None,
    signer_key_sha256: str | None = None,
) -> dict[str, Any]:
    card_name = str(_first(raw, "card_name", "name", "title", "display_name", default="")).strip()
    if not card_name:
        raise ValueError("card_name/name is required")
    row_category = str(raw.get("category") or category).strip().lower()
    if not row_category:
        raise ValueError("category is required")
    explicit_id = _first(raw, "local_card_id", "id", "asset_id", "card_id")
    external_id = None
    if explicit_id not in (None, ""):
        external_id = str(explicit_id).strip()
        if not external_id:
            raise ValueError("explicit local card id cannot be blank")
        if len(external_id) > 256:
            raise ValueError("explicit local card id is too long")
        prefix = f"catalog:{row_category}:"
        local_card_id = external_id if external_id.startswith(prefix) else prefix + external_id
    else:
        local_card_id = _generated_local_card_id(
            raw,
            category=row_category,
            card_name=card_name,
        )
    metadata = dict(raw.get("metadata") or {})
    metadata.setdefault("source_payload", raw)
    metadata["variation"] = str(
        _first(
            raw,
            "variation",
            "variant",
            default=metadata.get("variation") or metadata.get("variant") or "",
        )
    ).strip()
    # Provenance is importer-owned. Never trust a marker supplied inside the JSON.
    metadata.pop("evidence_origin", None)
    if external_id is not None:
        metadata["source_local_card_id"] = external_id
    evidence_aliases = {
        "price_status": ("price_status", "match_status"),
        "price_source": ("price_source", "source"),
        "price_confidence": ("price_confidence", "confidence", "confidence_tier"),
        "price_confidence_score": ("price_confidence_score", "confidence_score"),
        "price_source_count": ("price_source_count", "source_count"),
        "price_observation_count": ("price_observation_count", "observation_count"),
        "price_asset_url": ("price_asset_url", "asset_url", "url", "href"),
        "price_referral_url": ("price_referral_url", "referral_url"),
        "price_updated_at": ("price_updated_at", "priceUpdatedAt", "last_price_update"),
    }
    if trusted_evidence:
        for metadata_key, aliases in evidence_aliases.items():
            value = _first(raw, *aliases)
            if value not in (None, ""):
                metadata.setdefault(metadata_key, value)
        metadata["evidence_signature_verified"] = True
        metadata["evidence_origin"] = "signed-manifest"
        metadata["evidence_manifest_sha256"] = manifest_sha256
        metadata["evidence_signer_key_sha256"] = signer_key_sha256
    else:
        for metadata_key in evidence_aliases:
            metadata.pop(metadata_key, None)
        metadata["price_status"] = "candidate"
        metadata["price_source"] = "catalog-import"
        metadata["evidence_signature_verified"] = False
    return {
        "local_card_id": local_card_id,
        "category": row_category,
        "card_name": card_name,
        "grade": normalize_grade(str(_first(raw, "grade", "rarity", default="R"))),
        "set_code": _first(raw, "set_code", "set", "series_code", "collection"),
        "set_name": _first(raw, "set_name", "series_name", "collection_name"),
        "collector_number": _first(raw, "collector_number", "number", "card_number", "item_number"),
        "rarity": _first(raw, "rarity", "grade"),
        "language": str(raw.get("language") or "Japanese"),
        "image_url": _first(raw, "image_url", "image", "thumbnail_url", "display_image_url"),
        "market_price_usd": _float_or_none(
            _first(raw, "market_price_usd", "fmv_usd", "price_usd", "value_usd")
        ),
        "metadata": metadata,
    }


async def import_cards(
    path: Path,
    category: str,
    *,
    dry_run: bool = False,
    verified_manifest: VerifiedManifest | None = None,
) -> int:
    raw_bytes = verified_manifest.data if verified_manifest is not None else path.read_bytes()
    if verified_manifest is not None:
        assert_verified_manifest(verified_manifest)
    rows = normalize_manifest_rows(
        raw_bytes,
        category,
        verified_manifest=verified_manifest,
    )
    if dry_run:
        return len(rows)
    pool = await get_db()
    await create_tables(pool)
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.fetchval(
                "SELECT pg_advisory_xact_lock(hashtextextended('renaiss-catalog-import', 0))"
            )
            for row in rows:
                await _resolve_generic_import_identity(conn, row)
                imported = await conn.fetchrow(
                    """
                    INSERT INTO renaiss_catalog_cards (
                        local_card_id, category, card_name, grade, set_code, set_name,
                        collector_number, rarity, language, image_url, market_price_usd, metadata
                    )
                    VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12::jsonb)
                    ON CONFLICT (local_card_id)
                    DO UPDATE SET
                        category = EXCLUDED.category,
                        card_name = EXCLUDED.card_name,
                        grade = EXCLUDED.grade,
                        set_code = EXCLUDED.set_code,
                        set_name = EXCLUDED.set_name,
                        collector_number = EXCLUDED.collector_number,
                        rarity = EXCLUDED.rarity,
                        language = EXCLUDED.language,
                        image_url = EXCLUDED.image_url,
                        market_price_usd = EXCLUDED.market_price_usd,
                        metadata = EXCLUDED.metadata,
                        is_active = TRUE,
                        updated_at = now()
                    WHERE renaiss_catalog_cards.category = EXCLUDED.category
                      AND renaiss_catalog_cards.card_name = EXCLUDED.card_name
                      AND COALESCE(
                            NULLIF(BTRIM(renaiss_catalog_cards.set_name), ''),
                            BTRIM(renaiss_catalog_cards.set_code),
                            ''
                          ) = COALESCE(
                            NULLIF(BTRIM(EXCLUDED.set_name), ''),
                            BTRIM(EXCLUDED.set_code),
                            ''
                          )
                      AND COALESCE(renaiss_catalog_cards.collector_number, '') =
                          COALESCE(EXCLUDED.collector_number, '')
                      AND renaiss_catalog_cards.language = EXCLUDED.language
                      AND COALESCE(
                            renaiss_catalog_cards.metadata->>'variation',
                            renaiss_catalog_cards.metadata->>'variant',
                            renaiss_catalog_cards.metadata->'source_payload'->>'variation',
                            renaiss_catalog_cards.metadata->'source_payload'->>'variant',
                            ''
                          ) = COALESCE(
                            EXCLUDED.metadata->>'variation',
                            EXCLUDED.metadata->>'variant',
                            EXCLUDED.metadata->'source_payload'->>'variation',
                            EXCLUDED.metadata->'source_payload'->>'variant',
                            ''
                          )
                    RETURNING local_card_id
                    """,
                    row["local_card_id"],
                    row["category"],
                    row["card_name"],
                    row["grade"],
                    row["set_code"],
                    row["set_name"],
                    row["collector_number"],
                    row["rarity"],
                    row["language"],
                    row["image_url"],
                    row["market_price_usd"],
                    json.dumps(row["metadata"], ensure_ascii=False),
                )
                if imported is None:
                    raise ValueError(
                        f"catalog id identity conflict: {row['local_card_id']}"
                    )
    return len(rows)


def normalize_manifest_rows(
    raw_bytes: bytes,
    category: str,
    *,
    verified_manifest: VerifiedManifest | None = None,
) -> list[dict[str, Any]]:
    """Validate and normalize exact manifest bytes without opening PostgreSQL."""
    if verified_manifest is not None and raw_bytes != verified_manifest.data:
        raise ValueError("verified manifest bytes do not match import bytes")
    if verified_manifest is not None:
        assert_verified_manifest(verified_manifest)
    payload = json.loads(raw_bytes.decode("utf-8"))
    if not isinstance(payload, list):
        raise ValueError("JSON root must be a list")
    if any(not isinstance(item, dict) for item in payload):
        raise ValueError("every JSON array item must be an object")
    rows = [
        _normalize_row(
            item,
            category,
            trusted_evidence=verified_manifest is not None,
            manifest_sha256=verified_manifest.sha256 if verified_manifest else None,
            signer_key_sha256=(
                verified_manifest.signer_key_sha256 if verified_manifest else None
            ),
        )
        for item in payload
    ]
    identifiers = [row["local_card_id"] for row in rows]
    duplicates = sorted(
        identifier for identifier, count in Counter(identifiers).items() if count > 1
    )
    if duplicates:
        raise ValueError(f"duplicate local_card_id values: {', '.join(duplicates[:10])}")
    structural_keys = [key for row in rows if (key := _row_structural_identity(row))]
    structural_duplicates = [
        key for key, count in Counter(structural_keys).items() if count > 1
    ]
    if structural_duplicates:
        raise ValueError("duplicate structural catalog identities in manifest")
    return rows


def _load_explicit_environment(
    configured_path: str,
    *,
    require_database: bool,
) -> Path:
    """Load an operator-selected env file deterministically for this write CLI."""
    path = resolve_runtime_path(configured_path)
    if not path.is_file():
        raise ValueError("the explicit --env file does not exist")
    values = dotenv_values(dotenv_path=path, interpolate=False)
    if not values:
        raise ValueError("the explicit --env file is empty or unreadable")
    interpolated_keys = sorted(
        str(key)
        for key, value in values.items()
        if isinstance(value, str) and re.search(r"\$\{[^}]+\}", value)
    )
    if interpolated_keys:
        raise ValueError(
            "explicit --env interpolation is not allowed for: "
            + ", ".join(interpolated_keys)
        )
    if require_database and not str(values.get("DATABASE_URL") or "").strip():
        raise ValueError("the explicit --env file must define DATABASE_URL for writes")
    for key in _EXPLICIT_IMPORT_ENV_KEYS:
        os.environ.pop(key, None)
    for key, value in values.items():
        if value is not None:
            os.environ[str(key)] = str(value)
    return path


async def main_async() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("path", type=Path)
    parser.add_argument("--category", default="one_piece_tcg")
    parser.add_argument("--env")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--signature", type=Path)
    parser.add_argument("--public-key", type=Path)
    args = parser.parse_args()

    if args.env:
        _load_explicit_environment(args.env, require_database=not args.dry_run)
    else:
        load_runtime_environment()
    try:
        if bool(args.signature) != bool(args.public_key):
            raise ValueError("--signature and --public-key must be provided together")
        verified_manifest = None
        if args.signature and args.public_key:
            manifest_bytes = args.path.read_bytes()
            verified_manifest = verify_manifest(
                manifest_bytes,
                signature_base64=args.signature.read_text(encoding="ascii"),
                public_key_pem=args.public_key.read_bytes(),
                expected_public_key_sha256=os.getenv(
                    "RENAISS_MANIFEST_PUBLIC_KEY_SHA256",
                    "",
                ),
                expected_manifest_sha256=os.getenv("RENAISS_MANIFEST_SHA256", ""),
            )
        count = await import_cards(
            args.path,
            args.category,
            dry_run=args.dry_run,
            verified_manifest=verified_manifest,
        )
        action = "validated" if args.dry_run else "imported"
        evidence = "signed-collection-only" if verified_manifest else "collection-only"
        print(f"{action} {count} catalog cards | evidence={evidence}")
        return 0
    finally:
        await close_db()


def main() -> int:
    return asyncio.run(main_async())


if __name__ == "__main__":
    raise SystemExit(main())
