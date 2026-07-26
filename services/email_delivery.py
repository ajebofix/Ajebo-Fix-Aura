"""Transactional email delivery for Aura.

Railway may restrict outbound SMTP on lower plans, so Aura sends account emails
through Resend's HTTPS API instead of opening SMTP connections. The module keeps
provider configuration and error handling outside route and security modules.
"""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from typing import Any

import requests
from flask import current_app

_RESEND_API_URL = "https://api.resend.com/emails"
_DEFAULT_TIMEOUT_SECONDS = 10


@dataclass(frozen=True)
class EmailDeliveryResult:
    """Stable result returned by Aura's transactional-email provider."""

    success: bool
    provider_message_id: str | None = None
    error_code: str | None = None


def build_email_idempotency_key(purpose: str, *parts: object) -> str:
    """Build a privacy-safe, deterministic key for one logical email."""

    material = ":".join([purpose, *(str(part) for part in parts)])
    digest = hashlib.sha256(material.encode("utf-8")).hexdigest()
    return f"aura-{purpose}-{digest}"[:256]


def _setting(name: str, default: Any = None) -> Any:
    configured = current_app.config.get(name)
    if configured not in (None, ""):
        return configured

    environment_value = os.getenv(name)
    if environment_value not in (None, ""):
        return environment_value

    return default


def _timeout_seconds() -> int:
    configured = _setting("RESEND_TIMEOUT", _DEFAULT_TIMEOUT_SECONDS)
    try:
        return max(1, int(configured))
    except (TypeError, ValueError):
        return _DEFAULT_TIMEOUT_SECONDS


def _provider_error_code(response: requests.Response) -> str:
    try:
        payload = response.json()
    except ValueError:
        return "provider_error"

    if not isinstance(payload, dict):
        return "provider_error"

    value = payload.get("name") or payload.get("statusCode") or payload.get("error")
    return str(value or "provider_error")[:80]


def send_transactional_email(
    *,
    to: str,
    subject: str,
    text: str,
    idempotency_key: str | None = None,
) -> EmailDeliveryResult:
    """Send one plain-text transactional email through Resend over HTTPS."""

    if current_app.config.get("MAIL_SUPPRESS_SEND"):
        current_app.logger.info("Transactional email delivery suppressed")
        return EmailDeliveryResult(success=True, provider_message_id="suppressed")

    api_key = _setting("RESEND_API_KEY")
    sender = _setting("RESEND_FROM_EMAIL") or _setting("MAIL_DEFAULT_SENDER")
    reply_to = _setting("RESEND_REPLY_TO") or _setting("MAIL_USERNAME")

    if not api_key or not sender:
        current_app.logger.warning(
            "Resend email configuration is incomplete",
            extra={"provider": "resend"},
        )
        return EmailDeliveryResult(
            success=False,
            error_code="configuration_incomplete",
        )

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    if idempotency_key:
        headers["Idempotency-Key"] = idempotency_key[:256]

    payload: dict[str, Any] = {
        "from": sender,
        "to": [to],
        "subject": subject,
        "text": text,
    }
    if reply_to:
        payload["reply_to"] = reply_to

    try:
        response = requests.post(
            _setting("RESEND_API_URL", _RESEND_API_URL),
            json=payload,
            headers=headers,
            timeout=_timeout_seconds(),
        )
    except requests.RequestException:
        current_app.logger.exception(
            "Resend email request failed",
            extra={"provider": "resend"},
        )
        return EmailDeliveryResult(success=False, error_code="network_error")

    if not 200 <= response.status_code < 300:
        error_code = _provider_error_code(response)
        current_app.logger.warning(
            "Resend rejected transactional email",
            extra={
                "provider": "resend",
                "status_code": response.status_code,
                "error_code": error_code,
            },
        )
        return EmailDeliveryResult(success=False, error_code=error_code)

    try:
        response_payload = response.json()
    except ValueError:
        response_payload = {}

    message_id = (
        str(response_payload.get("id"))
        if isinstance(response_payload, dict) and response_payload.get("id")
        else None
    )

    if not message_id:
        current_app.logger.warning(
            "Resend response did not include a message id",
            extra={"provider": "resend"},
        )
        return EmailDeliveryResult(
            success=False,
            error_code="missing_message_id",
        )

    return EmailDeliveryResult(
        success=True,
        provider_message_id=message_id,
    )
