"""Telegram OIDC and signed-cookie helpers for the Renaiss web companion."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import time
from dataclasses import dataclass
from typing import Any, Mapping
from urllib.parse import urlencode, urlparse

import aiohttp
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa


OIDC_ISSUER = "https://oauth.telegram.org"
OIDC_AUTHORIZATION_ENDPOINT = f"{OIDC_ISSUER}/auth"
OIDC_TOKEN_ENDPOINT = f"{OIDC_ISSUER}/token"
OIDC_JWKS_ENDPOINT = f"{OIDC_ISSUER}/.well-known/jwks.json"

SESSION_COOKIE = "renaiss_session"
FLOW_COOKIE = "renaiss_oidc_flow"
COOKIE_PATH = "/renaiss"
SESSION_MAX_AGE_SECONDS = 7 * 24 * 60 * 60
FLOW_MAX_AGE_SECONDS = 10 * 60


class AuthError(ValueError):
    """Raised for invalid or unverifiable authentication material."""


@dataclass(frozen=True)
class OIDCConfig:
    client_id: str
    client_secret: str
    redirect_uri: str


def _b64url_encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _b64url_decode(value: str) -> bytes:
    try:
        return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
    except (ValueError, TypeError) as exc:
        raise AuthError("invalid base64url value") from exc


def _session_secret() -> bytes:
    value = os.getenv("RENAISS_WEB_SESSION_SECRET", "").strip()
    if len(value) < 32:
        raise AuthError("RENAISS_WEB_SESSION_SECRET must be at least 32 characters")
    return value.encode("utf-8")


def _purpose_key(purpose: str) -> bytes:
    return hmac.new(_session_secret(), purpose.encode("ascii"), hashlib.sha256).digest()


def _sign_payload(payload: Mapping[str, Any], *, purpose: str) -> str:
    encoded = _b64url_encode(
        json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    )
    signature = hmac.new(
        _purpose_key(purpose), encoded.encode("ascii"), hashlib.sha256
    ).digest()
    return f"{encoded}.{_b64url_encode(signature)}"


def _verify_payload(
    token: str,
    *,
    purpose: str,
    max_age_seconds: int,
    now: int | None = None,
) -> dict[str, Any]:
    try:
        encoded, signature = token.split(".", 1)
    except (AttributeError, ValueError) as exc:
        raise AuthError("malformed signed payload") from exc
    expected = hmac.new(
        _purpose_key(purpose), encoded.encode("ascii"), hashlib.sha256
    ).digest()
    if not hmac.compare_digest(_b64url_encode(expected), signature):
        raise AuthError("invalid signed payload")
    try:
        payload = json.loads(_b64url_decode(encoded))
    except (json.JSONDecodeError, UnicodeDecodeError, TypeError) as exc:
        raise AuthError("invalid signed payload") from exc
    if not isinstance(payload, dict):
        raise AuthError("invalid signed payload")
    current = int(time.time()) if now is None else int(now)
    try:
        issued_at = int(payload["iat"])
    except (KeyError, TypeError, ValueError) as exc:
        raise AuthError("signed payload has no valid timestamp") from exc
    if issued_at > current + 60 or current - issued_at > max_age_seconds:
        raise AuthError("signed payload expired")
    return payload


def oidc_config() -> OIDCConfig | None:
    client_id = os.getenv("RENAISS_TELEGRAM_OIDC_CLIENT_ID", "").strip()
    client_secret = os.getenv("RENAISS_TELEGRAM_OIDC_CLIENT_SECRET", "").strip()
    redirect_uri = os.getenv("RENAISS_TELEGRAM_OIDC_REDIRECT_URI", "").strip()
    if not any((client_id, client_secret, redirect_uri)):
        return None
    if not all((client_id, client_secret, redirect_uri)):
        raise AuthError("Telegram OIDC client id, secret, and redirect URI are all required")
    try:
        parsed = urlparse(redirect_uri)
        port = parsed.port
    except ValueError as exc:
        raise AuthError("Telegram OIDC redirect URI is invalid") from exc
    if parsed.scheme != "https" or not parsed.netloc or port not in {None, 443}:
        raise AuthError("Telegram OIDC redirect URI must be an absolute HTTPS URL")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise AuthError("Telegram OIDC redirect URI must not contain credentials or parameters")
    if parsed.path != "/renaiss/api/auth/telegram/callback":
        raise AuthError("Telegram OIDC redirect URI must use the Renaiss callback path")
    return OIDCConfig(client_id, client_secret, redirect_uri)


def create_oidc_authorization(
    config: OIDCConfig,
    *,
    now: int | None = None,
) -> tuple[str, str]:
    current = int(time.time()) if now is None else int(now)
    state = secrets.token_urlsafe(32)
    verifier = secrets.token_urlsafe(64)
    nonce = secrets.token_urlsafe(32)
    challenge = _b64url_encode(hashlib.sha256(verifier.encode("ascii")).digest())
    flow = _sign_payload(
        {"iat": current, "state": state, "verifier": verifier, "nonce": nonce},
        purpose="oidc-flow-v1",
    )
    query = urlencode(
        {
            "client_id": config.client_id,
            "redirect_uri": config.redirect_uri,
            "response_type": "code",
            "scope": "openid profile",
            "state": state,
            "nonce": nonce,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        }
    )
    return f"{OIDC_AUTHORIZATION_ENDPOINT}?{query}", flow


def verify_oidc_flow(
    token: str,
    state: str,
    *,
    now: int | None = None,
) -> dict[str, Any]:
    flow = _verify_payload(
        token,
        purpose="oidc-flow-v1",
        max_age_seconds=FLOW_MAX_AGE_SECONDS,
        now=now,
    )
    if not state or not hmac.compare_digest(str(flow.get("state") or ""), state):
        raise AuthError("OIDC state mismatch")
    if not flow.get("verifier") or not flow.get("nonce"):
        raise AuthError("OIDC flow is incomplete")
    return flow


async def exchange_oidc_code(
    config: OIDCConfig,
    *,
    code: str,
    verifier: str,
) -> str:
    if not code or len(code) > 4096:
        raise AuthError("invalid authorization code")
    timeout = aiohttp.ClientTimeout(total=8)
    auth = aiohttp.BasicAuth(config.client_id, config.client_secret)
    data = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": config.redirect_uri,
        "client_id": config.client_id,
        "code_verifier": verifier,
    }
    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(OIDC_TOKEN_ENDPOINT, auth=auth, data=data) as response:
                if response.status != 200:
                    raise AuthError("Telegram token exchange failed")
                payload = await response.json(content_type=None)
    except AuthError:
        raise
    except (aiohttp.ClientError, TimeoutError, ValueError) as exc:
        raise AuthError("Telegram token exchange unavailable") from exc
    token = payload.get("id_token") if isinstance(payload, dict) else None
    if not isinstance(token, str) or not token:
        raise AuthError("Telegram token exchange returned no ID token")
    return token


async def fetch_telegram_jwks() -> dict[str, Any]:
    timeout = aiohttp.ClientTimeout(total=8)
    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(OIDC_JWKS_ENDPOINT) as response:
                if response.status != 200:
                    raise AuthError("Telegram JWKS request failed")
                payload = await response.json(content_type=None)
    except AuthError:
        raise
    except (aiohttp.ClientError, TimeoutError, ValueError) as exc:
        raise AuthError("Telegram JWKS unavailable") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("keys"), list):
        raise AuthError("Telegram JWKS response is invalid")
    return payload


def _json_jwt_segment(value: str) -> dict[str, Any]:
    try:
        result = json.loads(_b64url_decode(value))
    except (json.JSONDecodeError, UnicodeDecodeError, TypeError) as exc:
        raise AuthError("invalid ID token") from exc
    if not isinstance(result, dict):
        raise AuthError("invalid ID token")
    return result


def _rsa_public_key(jwk: Mapping[str, Any]):
    try:
        modulus = int.from_bytes(_b64url_decode(str(jwk["n"])), "big")
        exponent = int.from_bytes(_b64url_decode(str(jwk["e"])), "big")
    except (KeyError, TypeError, ValueError) as exc:
        raise AuthError("invalid Telegram signing key") from exc
    return rsa.RSAPublicNumbers(exponent, modulus).public_key()


def validate_telegram_id_token(
    token: str,
    jwks: Mapping[str, Any],
    *,
    client_id: str,
    nonce: str,
    now: int | None = None,
) -> dict[str, Any]:
    try:
        encoded_header, encoded_claims, encoded_signature = token.split(".")
    except (AttributeError, ValueError) as exc:
        raise AuthError("invalid ID token") from exc
    header = _json_jwt_segment(encoded_header)
    claims = _json_jwt_segment(encoded_claims)
    if header.get("alg") != "RS256" or not isinstance(header.get("kid"), str):
        raise AuthError("unsupported Telegram ID token algorithm")
    keys = jwks.get("keys") if isinstance(jwks, Mapping) else None
    if not isinstance(keys, list):
        raise AuthError("Telegram signing keys are unavailable")
    key_data = next(
        (
            key
            for key in keys
            if isinstance(key, Mapping)
            and key.get("kid") == header["kid"]
            and key.get("kty") == "RSA"
        ),
        None,
    )
    if key_data is None:
        raise AuthError("Telegram signing key not found")
    signing_input = f"{encoded_header}.{encoded_claims}".encode("ascii")
    try:
        _rsa_public_key(key_data).verify(
            _b64url_decode(encoded_signature),
            signing_input,
            padding.PKCS1v15(),
            hashes.SHA256(),
        )
    except Exception as exc:
        raise AuthError("invalid Telegram ID token signature") from exc

    current = int(time.time()) if now is None else int(now)
    if claims.get("iss") != OIDC_ISSUER:
        raise AuthError("invalid Telegram ID token issuer")
    audience = claims.get("aud")
    accepted_audience = audience == client_id or (
        isinstance(audience, list) and client_id in audience
    )
    if not accepted_audience:
        raise AuthError("invalid Telegram ID token audience")
    if isinstance(audience, list) and len(audience) > 1:
        if not hmac.compare_digest(str(claims.get("azp") or ""), client_id):
            raise AuthError("invalid Telegram ID token authorized party")
    subject = claims.get("sub")
    if not isinstance(subject, str) or not subject.strip():
        raise AuthError("Telegram ID token has no valid subject")
    try:
        issued_at = int(claims["iat"])
        expires_at = int(claims["exp"])
    except (KeyError, TypeError, ValueError) as exc:
        raise AuthError("invalid Telegram ID token timestamps") from exc
    if issued_at > current + 60 or issued_at < current - 3600:
        raise AuthError("Telegram ID token issued-at is invalid")
    if expires_at <= current - 30:
        raise AuthError("Telegram ID token expired")
    if not nonce or not hmac.compare_digest(str(claims.get("nonce") or ""), nonce):
        raise AuthError("Telegram ID token nonce mismatch")
    # `sub` is the OIDC subject and is not guaranteed to equal the Bot API
    # Telegram user id stored in renaiss_user_cards. The `profile` scope is
    # requested above, so fail closed unless its explicit `id` claim exists.
    raw_id = claims.get("id")
    try:
        user_id = int(raw_id)
    except (TypeError, ValueError) as exc:
        raise AuthError("Telegram ID token has no valid user id") from exc
    if user_id <= 0:
        raise AuthError("Telegram ID token has no valid user id")
    name = str(claims.get("name") or claims.get("given_name") or "Collector").strip()
    username = str(claims.get("preferred_username") or "").strip().lstrip("@")
    return {
        "id": user_id,
        "display_name": name[:120] or "Collector",
        "username": username[:64] or None,
    }


def issue_session(user: Mapping[str, Any], *, now: int | None = None) -> str:
    current = int(time.time()) if now is None else int(now)
    try:
        user_id = int(user["id"])
    except (KeyError, TypeError, ValueError) as exc:
        raise AuthError("cannot create session without a valid user id") from exc
    if user_id <= 0:
        raise AuthError("cannot create session without a valid user id")
    return _sign_payload(
        {
            "v": 1,
            "iat": current,
            "id": user_id,
            "display_name": str(user.get("display_name") or "Collector")[:120],
        },
        purpose="web-session-v1",
    )


def read_session(token: str, *, now: int | None = None) -> dict[str, Any]:
    payload = _verify_payload(
        token,
        purpose="web-session-v1",
        max_age_seconds=SESSION_MAX_AGE_SECONDS,
        now=now,
    )
    try:
        user_id = int(payload["id"])
    except (KeyError, TypeError, ValueError) as exc:
        raise AuthError("invalid session user") from exc
    if payload.get("v") != 1 or user_id <= 0:
        raise AuthError("invalid session user")
    return {
        "id": user_id,
        "display_name": str(payload.get("display_name") or "Collector")[:120],
    }


def collector_pseudonym(user_id: int) -> str:
    """Return a stable, non-reversible public label for a Telegram user id."""
    digest = hmac.new(
        _purpose_key("collector-pseudonym-v1"),
        str(int(user_id)).encode("ascii"),
        hashlib.sha256,
    ).hexdigest()[:10].upper()
    return f"Collector {digest}"
