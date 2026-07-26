"""Application-layer encryption for sensitive Aura profile fields.

Ciphertexts are prefixed with a key version so keys can be rotated without
making existing profile data unreadable. Encryption keys must be supplied by
secret/environment configuration and must never be committed to the repository.
"""

from __future__ import annotations

import json
from collections.abc import Mapping

from cryptography.fernet import Fernet, InvalidToken
from flask import current_app


class ProfileEncryptionError(RuntimeError):
    """Raised when profile encryption configuration or ciphertext is invalid."""


def _configured_keys() -> tuple[str, dict[str, str]]:
    """Return the active version and configured Fernet keys.

    Preferred configuration:

    PROFILE_ENCRYPTION_KEYS='{"v1":"...","v2":"..."}'
    PROFILE_ENCRYPTION_ACTIVE_KEY_VERSION='v2'

    A single-key compatibility configuration is also supported through
    PROFILE_ENCRYPTION_KEY and PROFILE_ENCRYPTION_KEY_VERSION.
    """

    raw_keys = current_app.config.get("PROFILE_ENCRYPTION_KEYS")
    active_version = str(
        current_app.config.get("PROFILE_ENCRYPTION_ACTIVE_KEY_VERSION")
        or current_app.config.get("PROFILE_ENCRYPTION_KEY_VERSION")
        or "v1"
    ).strip()

    keys: Mapping[str, object]
    if raw_keys:
        if isinstance(raw_keys, str):
            try:
                decoded = json.loads(raw_keys)
            except json.JSONDecodeError as exc:
                raise ProfileEncryptionError(
                    "PROFILE_ENCRYPTION_KEYS must be valid JSON."
                ) from exc
        elif isinstance(raw_keys, Mapping):
            decoded = raw_keys
        else:
            raise ProfileEncryptionError(
                "PROFILE_ENCRYPTION_KEYS must be a JSON object or mapping."
            )
        keys = decoded
    else:
        single_key = current_app.config.get("PROFILE_ENCRYPTION_KEY")
        keys = {active_version: single_key} if single_key else {}

    normalised = {
        str(version).strip(): str(key).strip()
        for version, key in keys.items()
        if str(version).strip() and key
    }

    if not normalised:
        raise ProfileEncryptionError(
            "Profile encryption is not configured. Set PROFILE_ENCRYPTION_KEYS "
            "or PROFILE_ENCRYPTION_KEY in secret storage."
        )
    if active_version not in normalised:
        raise ProfileEncryptionError(
            "The active profile encryption key version is not configured."
        )

    for version, key in normalised.items():
        try:
            Fernet(key.encode("utf-8"))
        except (TypeError, ValueError) as exc:
            raise ProfileEncryptionError(
                f"Profile encryption key {version!r} is not a valid Fernet key."
            ) from exc

    return active_version, normalised


def encrypt_profile_value(value: str | None) -> str | None:
    """Encrypt a non-empty profile value and prefix its key version."""

    if value is None:
        return None

    plaintext = str(value).strip()
    if not plaintext:
        return None

    active_version, keys = _configured_keys()
    token = Fernet(keys[active_version].encode("utf-8")).encrypt(
        plaintext.encode("utf-8")
    )
    return f"{active_version}:{token.decode('utf-8')}"


def decrypt_profile_value(ciphertext: str | None) -> str | None:
    """Decrypt a versioned profile value.

    Invalid or unknown ciphertext fails closed rather than returning the stored
    bytes to a template, log, or API response.
    """

    if not ciphertext:
        return None

    try:
        version, token = ciphertext.split(":", 1)
    except ValueError as exc:
        raise ProfileEncryptionError(
            "Sensitive profile data is missing its encryption key version."
        ) from exc

    _, keys = _configured_keys()
    key = keys.get(version)
    if not key:
        raise ProfileEncryptionError(
            f"No profile encryption key is available for version {version!r}."
        )

    try:
        plaintext = Fernet(key.encode("utf-8")).decrypt(token.encode("utf-8"))
    except InvalidToken as exc:
        raise ProfileEncryptionError(
            "Sensitive profile data could not be decrypted."
        ) from exc

    return plaintext.decode("utf-8")
