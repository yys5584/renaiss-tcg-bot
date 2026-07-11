"""Catalog imports must preserve structural identity and fail atomically."""

from __future__ import annotations

import json
import os
from unittest.mock import AsyncMock

import pytest

from renaiss_bot.tools.import_catalog_json import (
    _assert_no_structural_duplicate,
    _load_explicit_environment,
    _normalize_row,
    _resolve_generic_import_identity,
    import_cards,
)


def test_explicit_import_env_overrides_ambient_database(monkeypatch, tmp_path):
    env_file = tmp_path / "staging.env"
    env_file.write_text(
        "DATABASE_URL=postgresql://staging.invalid/renaiss\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("DATABASE_URL", "postgresql://production.invalid/renaiss")
    monkeypatch.setenv("RENAISS_DB_SSL_INSECURE", "1")
    monkeypatch.setenv("RENAISS_DB_POOL_MAX", "99")
    monkeypatch.setenv("RENAISS_MANIFEST_PUBLIC_KEY_SHA256", "ambient-fingerprint")

    _load_explicit_environment(str(env_file), require_database=True)

    assert os.environ["DATABASE_URL"] == "postgresql://staging.invalid/renaiss"
    assert "RENAISS_DB_SSL_INSECURE" not in os.environ
    assert "RENAISS_DB_POOL_MAX" not in os.environ
    assert "RENAISS_MANIFEST_PUBLIC_KEY_SHA256" not in os.environ


def test_explicit_import_env_cannot_borrow_ambient_database(monkeypatch, tmp_path):
    env_file = tmp_path / "missing-db.env"
    env_file.write_text("LOG_LEVEL=INFO\n", encoding="utf-8")
    monkeypatch.setenv("DATABASE_URL", "postgresql://production.invalid/renaiss")

    with pytest.raises(ValueError, match="must define DATABASE_URL"):
        _load_explicit_environment(str(env_file), require_database=True)


def test_explicit_import_env_rejects_ambient_interpolation(monkeypatch, tmp_path):
    env_file = tmp_path / "interpolated.env"
    env_file.write_text(
        "DATABASE_URL=postgresql://${DB_HOST}/renaiss\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("DB_HOST", "production.invalid")

    with pytest.raises(ValueError, match="interpolation is not allowed"):
        _load_explicit_environment(str(env_file), require_database=True)


def test_generated_ids_do_not_collide_across_reprints():
    base = _normalize_row(
        {"name": "Charizard", "set_name": "Base Set", "number": "4/102"},
        "pokemon_tcg",
    )
    reprint = _normalize_row(
        {"name": "Charizard", "set_name": "Celebrations", "number": "4/102"},
        "pokemon_tcg",
    )

    assert base["local_card_id"].startswith("catalog:pokemon_tcg:")
    assert base["local_card_id"] != reprint["local_card_id"]


def test_generated_id_is_stable_when_provider_rarity_is_corrected():
    rare = _normalize_row(
        {"name": "Charizard", "set_name": "Base Set", "number": "4/102", "grade": "R"},
        "pokemon_tcg",
    )
    double_rare = _normalize_row(
        {"name": "Charizard", "set_name": "Base Set", "number": "4/102", "grade": "RR"},
        "pokemon_tcg",
    )

    assert rare["local_card_id"] == double_rare["local_card_id"]


def test_explicit_ids_are_namespaced_by_category():
    pokemon = _normalize_row(
        {"id": "001", "name": "Charizard"},
        "pokemon_tcg",
    )
    one_piece = _normalize_row(
        {"id": "001", "name": "Luffy"},
        "one_piece_tcg",
    )

    assert pokemon["local_card_id"] == "catalog:pokemon_tcg:001"
    assert one_piece["local_card_id"] == "catalog:one_piece_tcg:001"
    assert pokemon["local_card_id"] != one_piece["local_card_id"]
    assert pokemon["metadata"]["source_local_card_id"] == "001"


def test_import_normalizes_explicit_price_evidence():
    row = _normalize_row(
        {
            "name": "Charizard",
            "set_name": "Base Set",
            "number": "4/102",
            "fmv_usd": 95,
            "price_status": "exact",
            "price_source": "renaiss-index-api:item-by-no",
            "confidence": "high",
            "confidence_score": 0.91,
            "source_count": 3,
            "observation_count": 19,
            "asset_url": "https://index.renaissos.com/cards/charizard",
            "price_updated_at": "2026-07-11T10:00:00Z",
        },
        "pokemon_tcg",
        trusted_evidence=True,
        manifest_sha256="abc123",
        signer_key_sha256="def456",
    )

    metadata = row["metadata"]
    assert metadata["price_status"] == "exact"
    assert metadata["price_source"] == "renaiss-index-api:item-by-no"
    assert metadata["price_confidence_score"] == 0.91
    assert metadata["price_source_count"] == 3
    assert metadata["price_updated_at"] == "2026-07-11T10:00:00Z"
    assert metadata["evidence_signature_verified"] is True
    assert metadata["evidence_manifest_sha256"] == "abc123"
    assert metadata["evidence_signer_key_sha256"] == "def456"


def test_unsigned_import_forces_price_evidence_to_collection_only():
    row = _normalize_row(
        {
            "name": "Charizard",
            "fmv_usd": 95,
            "price_status": "exact",
            "price_source": "renaiss-index-api:item-by-no",
            "metadata": {
                "evidence_signature_verified": True,
                "evidence_origin": "official-api-import",
            },
        },
        "pokemon_tcg",
    )

    assert row["market_price_usd"] == 95
    assert row["metadata"]["price_status"] == "candidate"
    assert row["metadata"]["price_source"] == "catalog-import"
    assert row["metadata"]["evidence_signature_verified"] is False
    assert "evidence_origin" not in row["metadata"]


def test_signed_import_replaces_payload_provenance_with_manifest_origin():
    row = _normalize_row(
        {
            "name": "Charizard",
            "metadata": {"evidence_origin": "official-api-import"},
        },
        "pokemon_tcg",
        trusted_evidence=True,
        manifest_sha256="abc123",
        signer_key_sha256="def456",
    )

    assert row["metadata"]["evidence_origin"] == "signed-manifest"


@pytest.mark.parametrize("non_finite", ["NaN", "Infinity", "-Infinity"])
def test_catalog_import_rejects_non_finite_market_price(non_finite):
    row = _normalize_row(
        {"name": "Charizard", "fmv_usd": non_finite},
        "pokemon_tcg",
        trusted_evidence=True,
        manifest_sha256="abc123",
        signer_key_sha256="def456",
    )

    assert row["market_price_usd"] is None


@pytest.mark.parametrize("invalid_price", [-1, -10_000, 0, True])
def test_catalog_import_rejects_non_positive_or_boolean_market_price(invalid_price):
    row = _normalize_row(
        {"name": "Charizard", "fmv_usd": invalid_price},
        "pokemon_tcg",
    )

    assert row["market_price_usd"] is None


async def test_dry_run_validates_without_database(tmp_path, monkeypatch):
    path = tmp_path / "cards.json"
    path.write_text(json.dumps([{"name": "Charizard"}]), encoding="utf-8")
    monkeypatch.setattr(
        "renaiss_bot.tools.import_catalog_json.get_db",
        lambda: (_ for _ in ()).throw(AssertionError("database must not be opened")),
    )

    assert await import_cards(path, "pokemon_tcg", dry_run=True) == 1


async def test_duplicate_explicit_ids_are_rejected_before_database(tmp_path):
    path = tmp_path / "cards.json"
    path.write_text(
        json.dumps([{"id": "same", "name": "A"}, {"id": "same", "name": "B"}]),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="duplicate local_card_id"):
        await import_cards(path, "pokemon_tcg", dry_run=True)


async def test_duplicate_structural_cards_with_different_ids_are_rejected(tmp_path):
    path = tmp_path / "cards.json"
    path.write_text(
        json.dumps(
            [
                {
                    "id": "legacy-a",
                    "name": "Charizard",
                    "set": "BS",
                    "number": "4/102",
                    "language": "English",
                    "grade": "R",
                },
                {
                    "id": "provider-b",
                    "name": " Charizard ",
                    "set": "bs",
                    "number": "4/102",
                    "language": "english",
                    "grade": "R",
                },
            ]
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="duplicate structural catalog identities"):
        await import_cards(path, "pokemon_tcg", dry_run=True)


async def test_database_legacy_alias_blocks_new_namespaced_import():
    connection = AsyncMock()
    connection.fetchrow.return_value = {"local_card_id": "001", "metadata": {}}
    row = _normalize_row(
        {
            "id": "001",
            "name": "Charizard",
            "set": "BS",
            "number": "4/102",
            "language": "English",
            "grade": "R",
        },
        "pokemon_tcg",
    )

    with pytest.raises(ValueError, match="duplicates 001"):
        await _assert_no_structural_duplicate(connection, row)


async def test_generated_import_reuses_legacy_generated_id_for_safe_upgrade():
    connection = AsyncMock()
    legacy_id = "catalog:pokemon_tcg:" + ("a" * 24)
    connection.fetchrow.return_value = {"local_card_id": legacy_id, "metadata": {}}
    row = _normalize_row(
        {
            "name": "Charizard",
            "set_name": "Base Set",
            "number": "4/102",
            "language": "English",
            "grade": "RR",
        },
        "pokemon_tcg",
    )

    await _resolve_generic_import_identity(connection, row)

    assert row["local_card_id"] == legacy_id


async def test_generated_import_cannot_overwrite_existing_explicit_provider_row():
    connection = AsyncMock()
    connection.fetchrow.return_value = {
        "local_card_id": "catalog:pokemon_tcg:" + ("b" * 24),
        "metadata": {"source_local_card_id": "provider-001"},
    }
    row = _normalize_row(
        {
            "name": "Charizard",
            "set_name": "Base Set",
            "number": "4/102",
            "language": "English",
            "grade": "R",
        },
        "pokemon_tcg",
    )

    with pytest.raises(ValueError, match="structural identity conflict"):
        await _resolve_generic_import_identity(connection, row)
