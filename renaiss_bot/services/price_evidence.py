"""Convert catalog metadata into fail-closed price evidence."""

from __future__ import annotations

from datetime import datetime
from math import isfinite
from typing import Any

from renaiss_bot.services.manifest_signature import configured_trusted_key_fingerprints
from renaiss_bot.services.models import CardIdentity, RenaissPrice


def _optional_float(value: Any) -> float | None:
    try:
        if isinstance(value, bool):
            return None
        number = float(value) if value not in {None, ""} else None
        return number if number is not None and isfinite(number) else None
    except (TypeError, ValueError, OverflowError):
        return None


def _optional_int(value: Any) -> int | None:
    number = _optional_float(value)
    if (
        number is None
        or not number.is_integer()
        or number < 0
        or number > 2_147_483_647
    ):
        return None
    return int(number)


def catalog_provenance_issue(card: CardIdentity) -> str | None:
    """Explain why catalog metadata claiming exact evidence is not trusted."""
    metadata = card.metadata or {}
    if str(metadata.get("price_status") or "candidate") != "exact":
        return None
    signer_fingerprint = str(metadata.get("evidence_signer_key_sha256") or "").lower()
    if metadata.get("evidence_signature_verified") is True:
        if signer_fingerprint not in configured_trusted_key_fingerprints():
            return "manifest signer key is not currently trusted"
        # A detached signature authenticates the import file, but the current
        # board/snapshot schema cannot re-check signer revocation atomically at
        # pick and settlement time. Keep it collection-only until provenance is
        # persisted and enforced by those DB transitions.
        return "signed manifest evidence requires a live API refresh for scored use"
    if metadata.get("evidence_origin") == "official-api-import":
        return None
    return "exact catalog evidence lacks verified provenance"


def catalog_reference_price(
    card: CardIdentity,
    *,
    market_usd: float | None = None,
) -> RenaissPrice:
    """Build evidence from an imported catalog row without inventing freshness."""
    metadata = card.metadata or {}
    raw_status = str(metadata.get("price_status") or "candidate")
    raw_updated_at = str(metadata.get("price_updated_at") or "")
    try:
        price_updated_at = datetime.fromisoformat(raw_updated_at.replace("Z", "+00:00"))
    except ValueError:
        price_updated_at = None
    price_status = raw_status if raw_status in {"exact", "candidate"} else "candidate"
    trusted_catalog_evidence = catalog_provenance_issue(card) is None
    if price_status == "exact" and not trusted_catalog_evidence:
        price_status = "candidate"
    if price_status == "exact" and price_updated_at is None:
        price_status = "candidate"
    source_payload = metadata.get("source_payload")
    if not isinstance(source_payload, dict):
        source_payload = {}
    kwargs: dict[str, Any] = {
        "status": price_status,
        "source": str(metadata.get("price_source") or metadata.get("source") or "catalog"),
        "grade_label": str(
            metadata.get("market_grade") or source_payload.get("gradeLabel") or ""
        ).strip()
        or None,
        "grading_company": str(source_payload.get("company") or "").strip() or None,
        "confidence": str(metadata.get("price_confidence") or "") or None,
        "confidence_score": _optional_float(metadata.get("price_confidence_score")),
        "source_count": _optional_int(metadata.get("price_source_count")),
        "observation_count": _optional_int(metadata.get("price_observation_count")),
        "valuation_method": str(metadata.get("price_valuation_method") or "") or None,
        "fmv_usd": market_usd if market_usd is not None else card.market_price_usd,
        "asset_url": str(metadata.get("price_asset_url") or "") or None,
        "referral_url": str(metadata.get("price_referral_url") or "") or None,
        "image_url": card.image_url,
        "source_identity_key": str(metadata.get("price_source_identity_key") or "") or None,
    }
    if price_updated_at is not None:
        kwargs["price_updated_at"] = price_updated_at
    return RenaissPrice(**kwargs)
