"""Purpose-derived Ed25519 identity for Aura -> Billing (no shared token).

The private signing key is deterministically derived inside the Aura process
from its existing, private SECRET_KEY. Only the *public* key is published.
Never expose signature material via browser markup, logs or repository files.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import time

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

_CONTEXT = b"aura.billing.bridge.request.signing.ed25519.v1"


def _private_key() -> Ed25519PrivateKey:
    secret = os.getenv("SECRET_KEY", "")
    if not secret:
        raise RuntimeError("Private Aura application secret is unavailable")
    seed = hmac.new(secret.encode("utf-8"), _CONTEXT, hashlib.sha256).digest()
    return Ed25519PrivateKey.from_private_bytes(seed)


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def public_key_document() -> dict:
    key = _private_key().public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    return {
        "kty": "OKP",
        "crv": "Ed25519",
        "kid": "aura-billing-v1",
        "x": _b64url(key),
    }


def sign_billing_request(payload: dict) -> tuple[bytes, dict[str, str]]:
    body = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    timestamp = str(int(time.time()))
    nonce = secrets.token_hex(16)
    signature = _private_key().sign(
        timestamp.encode("ascii") + b"." + nonce.encode("ascii") + b"." + body
    )
    return body, {
        "X-Aura-Key-Id": "aura-billing-v1",
        "X-Aura-Timestamp": timestamp,
        "X-Aura-Nonce": nonce,
        "X-Aura-Signature": _b64url(signature),
    }
