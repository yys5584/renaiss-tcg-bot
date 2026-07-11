"""Detached Ed25519 verification for trusted catalog manifests."""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets
from dataclasses import dataclass, field

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.serialization import load_pem_public_key
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey


class ManifestSignatureError(ValueError):
    pass


MANIFEST_SIGNATURE_CONTEXT = b"renaiss-collector-catalog-manifest:v1\x00"
_CAPABILITY_KEY = secrets.token_bytes(32)


@dataclass(frozen=True)
class VerifiedManifest:
    data: bytes
    sha256: str
    signer_key_sha256: str
    _capability: str = field(repr=False, compare=False)


def _capability_for(data: bytes, signer_key_sha256: str) -> str:
    return hmac.new(
        _CAPABILITY_KEY,
        hashlib.sha256(data).digest() + signer_key_sha256.encode("ascii"),
        hashlib.sha256,
    ).hexdigest()


def assert_verified_manifest(manifest: VerifiedManifest) -> None:
    """Reject hand-constructed objects that bypass detached-signature verification."""
    actual_sha256 = hashlib.sha256(manifest.data).hexdigest()
    expected_capability = _capability_for(manifest.data, manifest.signer_key_sha256)
    if manifest.sha256 != actual_sha256 or not hmac.compare_digest(
        manifest._capability,
        expected_capability,
    ):
        raise ManifestSignatureError("manifest verification capability is invalid")


def _parse_expected_manifest_sha256(raw: str) -> str:
    digest = raw.strip().lower()
    if len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
        raise ManifestSignatureError("expected manifest SHA-256 is missing or invalid")
    return digest


def parse_trusted_key_fingerprints(raw: str) -> set[str]:
    fingerprints = set()
    for item in raw.split(","):
        fingerprint = item.strip().lower()
        if not fingerprint:
            continue
        if len(fingerprint) != 64 or any(char not in "0123456789abcdef" for char in fingerprint):
            raise ManifestSignatureError("trusted manifest key fingerprint is invalid")
        fingerprints.add(fingerprint)
    return fingerprints


def configured_trusted_key_fingerprints() -> set[str]:
    """Return the current trust store, failing closed on malformed configuration."""
    try:
        return parse_trusted_key_fingerprints(
            os.getenv("RENAISS_MANIFEST_PUBLIC_KEY_SHA256", "")
        )
    except ManifestSignatureError:
        return set()


def verify_manifest(
    data: bytes,
    *,
    signature_base64: str,
    public_key_pem: bytes,
    expected_public_key_sha256: str,
    expected_manifest_sha256: str,
) -> VerifiedManifest:
    """Verify the exact manifest bytes and return immutable provenance."""
    try:
        signature = base64.b64decode(signature_base64.strip(), validate=True)
    except (ValueError, TypeError) as exc:
        raise ManifestSignatureError("manifest signature is not valid base64") from exc
    try:
        public_key = load_pem_public_key(public_key_pem)
    except (TypeError, ValueError) as exc:
        raise ManifestSignatureError("manifest public key is invalid") from exc
    if not isinstance(public_key, Ed25519PublicKey):
        raise ManifestSignatureError("manifest public key must be Ed25519")
    public_der = public_key.public_bytes(
        encoding=serialization.Encoding.DER,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    key_fingerprint = hashlib.sha256(public_der).hexdigest()
    trusted_fingerprints = parse_trusted_key_fingerprints(expected_public_key_sha256)
    if key_fingerprint not in trusted_fingerprints:
        raise ManifestSignatureError("manifest public key fingerprint is not trusted")
    manifest_sha256 = hashlib.sha256(data).hexdigest()
    if manifest_sha256 != _parse_expected_manifest_sha256(expected_manifest_sha256):
        raise ManifestSignatureError("manifest SHA-256 is not the operator-approved release")
    try:
        public_key.verify(signature, MANIFEST_SIGNATURE_CONTEXT + data)
    except InvalidSignature as exc:
        raise ManifestSignatureError("manifest signature verification failed") from exc
    return VerifiedManifest(
        data=data,
        sha256=manifest_sha256,
        signer_key_sha256=key_fingerprint,
        _capability=_capability_for(data, key_fingerprint),
    )
