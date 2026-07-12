"""Signed manifests can be fully gated offline before any database write."""

from __future__ import annotations

import base64
import hashlib
import json
from datetime import datetime, timezone

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from renaiss_bot.tools.manifest_preflight import run
from renaiss_bot.services.manifest_signature import MANIFEST_SIGNATURE_CONTEXT


def _write_signed_manifest(tmp_path, *, asset_url: str = "https://index.renaissos.com/cards/charizard"):
    payload = [
        {
            "name": "Charizard",
            "set_code": "BS",
            "set_name": "Base Set",
            "collector_number": "4/102",
            "language": "English",
            "grade": "RAW",
            "fmv_usd": 95,
            "price_status": "exact",
            "price_source": "renaiss-index-api:item-by-no",
            "price_confidence": "high",
            "price_confidence_score": 0.91,
            "price_source_count": 3,
            "price_observation_count": 19,
            "price_asset_url": asset_url,
            "price_updated_at": datetime.now(timezone.utc).isoformat(),
        }
    ]
    data = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    private_key = Ed25519PrivateKey.generate()
    public_key = private_key.public_key()
    public_pem = public_key.public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    public_der = public_key.public_bytes(
        encoding=serialization.Encoding.DER,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    fingerprint = hashlib.sha256(public_der).hexdigest()
    manifest_path = tmp_path / "manifest.json"
    signature_path = tmp_path / "manifest.sig"
    public_key_path = tmp_path / "manifest.pem"
    manifest_path.write_bytes(data)
    signature_path.write_text(
        base64.b64encode(private_key.sign(MANIFEST_SIGNATURE_CONTEXT + data)).decode("ascii"),
        encoding="ascii",
    )
    public_key_path.write_bytes(public_pem)
    return manifest_path, signature_path, public_key_path, fingerprint


def test_signed_manifest_preflight_passes_without_database(tmp_path, monkeypatch, capsys):
    manifest, signature, public_key, fingerprint = _write_signed_manifest(tmp_path)
    monkeypatch.setenv("RENAISS_MANIFEST_PUBLIC_KEY_SHA256", fingerprint)
    monkeypatch.setenv("RENAISS_MANIFEST_SHA256", hashlib.sha256(manifest.read_bytes()).hexdigest())

    code = run(
        path=manifest,
        signature_path=signature,
        public_key_path=public_key,
        category="pokemon_tcg",
        require_eligible=0,
    )

    assert code == 0
    output = capsys.readouterr().out
    assert "Manifest signature PASS" in output
    assert "verified eligible: 0" in output
    assert "ready for collection-only dry-run import" in output


def test_manifest_preflight_rejects_non_renaiss_source_url(tmp_path, monkeypatch, capsys):
    manifest, signature, public_key, fingerprint = _write_signed_manifest(
        tmp_path,
        asset_url="https://example.com/not-a-source",
    )
    monkeypatch.setenv("RENAISS_MANIFEST_PUBLIC_KEY_SHA256", fingerprint)
    monkeypatch.setenv("RENAISS_MANIFEST_SHA256", hashlib.sha256(manifest.read_bytes()).hexdigest())

    code = run(
        path=manifest,
        signature_path=signature,
        public_key_path=public_key,
        category="pokemon_tcg",
        require_eligible=1,
    )

    assert code == 1
    output = capsys.readouterr().out
    assert "verified eligible: 0" in output
    assert "source URL is missing or not allowed" in output
