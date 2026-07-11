"""Catalog readiness audit stays aligned with the competitive price gate."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from renaiss_bot.services.models import CardIdentity, card_identity_key
from renaiss_bot.tools.catalog_audit import audit_cards, execute, print_report


def _card(
    *,
    updated_at: datetime,
    source: str = "renaiss-index-api:item-by-no",
    identity_suffix: str = "4",
) -> CardIdentity:
    card = CardIdentity(
        category="pokemon_tcg",
        local_card_id=f"catalog:pokemon_tcg:base-set-{identity_suffix}",
        card_name="Charizard",
        set_name="Base Set",
        set_code="BS",
        collector_number=f"{identity_suffix}/102",
        language="English",
        grade="RAW",
        market_price_usd=95,
        metadata={
            "evidence_origin": "official-api-import",
            "price_status": "exact",
            "price_source": source,
            "price_confidence": "high",
            "price_confidence_score": 0.91,
            "price_source_count": 3,
            "price_observation_count": 19,
            "price_valuation_method": "median",
            "price_asset_url": "https://index.renaissos.com/cards/charizard",
            "price_updated_at": updated_at.isoformat(),
        },
    )
    card.metadata["price_source_identity_key"] = card_identity_key(card)
    return card


def test_catalog_audit_counts_only_fresh_approved_evidence_as_eligible():
    now = datetime(2026, 7, 11, 12, tzinfo=timezone.utc)
    report = audit_cards(
        [
            _card(updated_at=now - timedelta(hours=2), identity_suffix="4"),
            _card(updated_at=now - timedelta(days=3), identity_suffix="5"),
            _card(
                updated_at=now - timedelta(hours=2),
                source="renaiss-index-api-mock",
                identity_suffix="6",
            ),
        ],
        now=now,
    )

    assert report["total"] == 3
    assert report["priced"] == 3
    assert report["eligible"] == 1
    assert report["collection_only"] == 2
    assert report["issue_counts"]["price timestamp is future-dated or stale"] == 1
    assert report["issue_counts"]["price source is not an approved Renaiss source"] == 1


def test_unsigned_catalog_metadata_cannot_become_eligible():
    now = datetime(2026, 7, 11, 12, tzinfo=timezone.utc)
    card = _card(updated_at=now)
    card.metadata.pop("evidence_origin")

    report = audit_cards([card], now=now)

    assert report["eligible"] == 0
    assert report["issue_counts"]["exact catalog evidence lacks verified provenance"] == 1
    assert report["issue_counts"]["price status is not exact"] == 1


def test_signed_catalog_evidence_is_rechecked_against_current_trust_store(monkeypatch):
    now = datetime(2026, 7, 11, 12, tzinfo=timezone.utc)
    fingerprint = "a" * 64
    card = _card(updated_at=now)
    card.metadata.pop("evidence_origin")
    card.metadata["evidence_signature_verified"] = True
    card.metadata["evidence_signer_key_sha256"] = fingerprint

    monkeypatch.setenv("RENAISS_MANIFEST_PUBLIC_KEY_SHA256", fingerprint)
    trusted_report = audit_cards([card], now=now)
    assert trusted_report["eligible"] == 0
    assert (
        trusted_report["issue_counts"][
            "signed manifest evidence requires a live API refresh for scored use"
        ]
        == 1
    )

    monkeypatch.setenv("RENAISS_MANIFEST_PUBLIC_KEY_SHA256", "b" * 64)
    report = audit_cards([card], now=now)
    assert report["eligible"] == 0
    assert report["issue_counts"]["manifest signer key is not currently trusted"] == 1
    assert report["issue_counts"]["price status is not exact"] == 1


def test_signed_evidence_cannot_bypass_revocation_with_official_origin(monkeypatch):
    now = datetime(2026, 7, 11, 12, tzinfo=timezone.utc)
    card = _card(updated_at=now)
    card.metadata["evidence_signature_verified"] = True
    card.metadata["evidence_signer_key_sha256"] = "a" * 64
    monkeypatch.setenv("RENAISS_MANIFEST_PUBLIC_KEY_SHA256", "b" * 64)

    report = audit_cards([card], now=now)

    assert report["eligible"] == 0
    assert report["issue_counts"]["manifest signer key is not currently trusted"] == 1


def test_catalog_audit_report_is_operator_readable(capsys):
    now = datetime(2026, 7, 11, 12, tzinfo=timezone.utc)
    print_report(audit_cards([_card(updated_at=now)], now=now), category="pokemon_tcg")

    output = capsys.readouterr().out
    assert "verified eligible: 1" in output
    assert "identity gate: clean" in output
    assert "resolved_source=catalog-table" in output
    assert "Eligible examples" in output
    assert "Charizard" in output


async def test_actual_pool_audit_exposes_sample_fallback(monkeypatch, capsys):
    sample = CardIdentity(
        category="pokemon_tcg",
        card_name="Sample Card",
        market_price_usd=10,
        metadata={"source": "sample"},
    )
    async def fake_pool(user_id, category):
        return [sample], "sample"

    monkeypatch.setattr("renaiss_bot.tools.catalog_audit.load_card_pool", fake_pool)
    monkeypatch.setattr(
        "renaiss_bot.tools.catalog_audit.close_db",
        lambda: _noop(),
    )

    assert await execute(
        category="pokemon_tcg",
        limit=100,
        require_eligible=1,
        pool_source="actual",
    ) == 1
    output = capsys.readouterr().out
    assert "resolved_source=sample" in output
    assert "verified eligible: 0" in output


async def test_release_catalog_contract_rejects_unavailable_runtime_pool(
    monkeypatch,
    capsys,
):
    async def unavailable_pool(user_id, category):
        return [], "unavailable"

    monkeypatch.setattr(
        "renaiss_bot.tools.catalog_audit.load_card_pool",
        unavailable_pool,
    )
    monkeypatch.setattr("renaiss_bot.tools.catalog_audit.close_db", _noop)

    assert await execute(
        category="pokemon_tcg",
        limit=100,
        require_eligible=0,
        pool_source="actual",
        require_total=10,
        require_resolved_source="renaiss_catalog",
    ) == 1
    output = capsys.readouterr().out
    assert "resolved_source=unavailable" in output
    assert "runtime card pool source 'unavailable'" in output


async def test_release_catalog_contract_requires_ten_real_catalog_cards(
    monkeypatch,
    capsys,
):
    cards = [
        CardIdentity(
            category="pokemon_tcg",
            local_card_id=f"catalog:pokemon_tcg:pilot-{index}",
            card_name=f"Pilot Card {index}",
            set_name="Pilot Set",
            collector_number=f"{index}/100",
            language="English",
            grade="R",
        )
        for index in range(9)
    ]

    async def undersized_pool(user_id, category):
        return cards, "renaiss_catalog"

    async def clean_identity_summary(*, category):
        return {"legacy_id_count": 0, "duplicate_identity_groups": 0}

    monkeypatch.setattr(
        "renaiss_bot.tools.catalog_audit.load_card_pool",
        undersized_pool,
    )
    monkeypatch.setattr(
        "renaiss_bot.tools.catalog_audit.load_catalog_identity_summary",
        clean_identity_summary,
    )
    monkeypatch.setattr("renaiss_bot.tools.catalog_audit.close_db", _noop)

    assert await execute(
        category="pokemon_tcg",
        limit=100,
        require_eligible=0,
        pool_source="actual",
        require_total=10,
        require_resolved_source="renaiss_catalog",
    ) == 1
    assert "runtime card pool total 9 < required 10" in capsys.readouterr().out


async def test_release_catalog_contract_accepts_ten_collection_only_cards(
    monkeypatch,
):
    cards = [
        CardIdentity(
            category="pokemon_tcg",
            local_card_id=f"catalog:pokemon_tcg:collection-{index}",
            card_name=f"Collection Card {index}",
            set_name="Collection Set",
            collector_number=f"{index}/100",
            language="English",
            grade="R",
        )
        for index in range(10)
    ]

    async def catalog_pool(user_id, category):
        return cards, "renaiss_catalog"

    async def clean_identity_summary(*, category):
        return {"legacy_id_count": 0, "duplicate_identity_groups": 0}

    monkeypatch.setattr(
        "renaiss_bot.tools.catalog_audit.load_card_pool",
        catalog_pool,
    )
    monkeypatch.setattr(
        "renaiss_bot.tools.catalog_audit.load_catalog_identity_summary",
        clean_identity_summary,
    )
    monkeypatch.setattr("renaiss_bot.tools.catalog_audit.close_db", _noop)

    assert await execute(
        category="pokemon_tcg",
        limit=100,
        require_eligible=0,
        pool_source="actual",
        require_total=10,
        require_resolved_source="renaiss_catalog",
    ) == 0


def test_catalog_audit_blocks_legacy_and_duplicate_active_identities():
    now = datetime(2026, 7, 11, 12, tzinfo=timezone.utc)
    legacy = _card(updated_at=now)
    object.__setattr__(legacy, "local_card_id", "base-set-4")
    duplicate = _card(updated_at=now)
    object.__setattr__(duplicate, "local_card_id", "pokard:pokemon_tcg:base-set-4")

    report = audit_cards([legacy, duplicate], now=now)

    assert report["identity_clean"] is False
    assert report["legacy_id_count"] == 1
    assert report["duplicate_identity_groups"] == 1
    assert report["eligible"] == 0
    assert report["issue_counts"]["active catalog id is not provider-namespaced"] == 1
    assert report["issue_counts"]["duplicate active structural catalog identity"] == 2


def test_catalog_duplicate_identity_ignores_provider_rarity_mapping():
    now = datetime(2026, 7, 11, 12, tzinfo=timezone.utc)
    catalog = _card(updated_at=now)
    pokard = _card(updated_at=now)
    object.__setattr__(pokard, "local_card_id", "pokard:pokemon_tcg:base-set-4")
    object.__setattr__(pokard, "grade", "RR")
    object.__setattr__(pokard, "set_code", "base1")

    report = audit_cards([catalog, pokard], now=now)

    assert report["identity_clean"] is False
    assert report["duplicate_identity_groups"] == 1


async def _noop():
    return None
