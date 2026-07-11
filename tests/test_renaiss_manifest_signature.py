"""Only an Ed25519-signed manifest may carry competitive catalog evidence."""

from __future__ import annotations

import base64
import hashlib

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from renaiss_bot.services.manifest_signature import (
    MANIFEST_SIGNATURE_CONTEXT,
    ManifestSignatureError,
    VerifiedManifest,
    assert_verified_manifest,
    verify_manifest,
)


def _signed(data: bytes):
    private_key = Ed25519PrivateKey.generate()
    public_pem = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    signature = base64.b64encode(
        private_key.sign(MANIFEST_SIGNATURE_CONTEXT + data)
    ).decode("ascii")
    public_der = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.DER,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    fingerprint = hashlib.sha256(public_der).hexdigest()
    return public_pem, signature, fingerprint


def test_valid_manifest_signature_returns_exact_bytes_and_digest():
    data = b'[{"name":"Charizard"}]'
    public_pem, signature, fingerprint = _signed(data)

    verified = verify_manifest(
        data,
        signature_base64=signature,
        public_key_pem=public_pem,
        expected_public_key_sha256=f"{'0' * 64}, {fingerprint}",
        expected_manifest_sha256=hashlib.sha256(data).hexdigest(),
    )

    assert verified.data == data
    assert len(verified.sha256) == 64
    assert verified.signer_key_sha256 == fingerprint


def test_manifest_modification_invalidates_signature():
    data = b'[{"name":"Charizard"}]'
    public_pem, signature, fingerprint = _signed(data)

    with pytest.raises(ManifestSignatureError, match="verification failed"):
        verify_manifest(
            data + b" ",
            signature_base64=signature,
            public_key_pem=public_pem,
            expected_public_key_sha256=fingerprint,
            expected_manifest_sha256=hashlib.sha256(data + b" ").hexdigest(),
        )


def test_unpinned_public_key_is_rejected():
    data = b'[{"name":"Charizard"}]'
    public_pem, signature, _fingerprint = _signed(data)

    with pytest.raises(ManifestSignatureError, match="fingerprint is not trusted"):
        verify_manifest(
            data,
            signature_base64=signature,
            public_key_pem=public_pem,
            expected_public_key_sha256="0" * 64,
            expected_manifest_sha256=hashlib.sha256(data).hexdigest(),
        )


def test_malformed_trust_fingerprint_fails_closed():
    data = b'[{"name":"Charizard"}]'
    public_pem, signature, _fingerprint = _signed(data)

    with pytest.raises(ManifestSignatureError, match="fingerprint is invalid"):
        verify_manifest(
            data,
            signature_base64=signature,
            public_key_pem=public_pem,
            expected_public_key_sha256="not-a-fingerprint",
            expected_manifest_sha256=hashlib.sha256(data).hexdigest(),
        )


def test_manifest_release_digest_prevents_signed_rollback():
    data = b'[{"name":"Charizard"}]'
    public_pem, signature, fingerprint = _signed(data)

    with pytest.raises(ManifestSignatureError, match="operator-approved release"):
        verify_manifest(
            data,
            signature_base64=signature,
            public_key_pem=public_pem,
            expected_public_key_sha256=fingerprint,
            expected_manifest_sha256="0" * 64,
        )


def test_hand_constructed_verified_manifest_is_not_a_capability():
    data = b"[]"
    forged = VerifiedManifest(
        data=data,
        sha256=hashlib.sha256(data).hexdigest(),
        signer_key_sha256="0" * 64,
        _capability="0" * 64,
    )
    with pytest.raises(ManifestSignatureError, match="capability is invalid"):
        assert_verified_manifest(forged)
