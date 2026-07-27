"""WhatsApp Cloud API delivery for Aura.

The booking flow must never submit a malformed Meta request. Configuration is
resolved at send time so Railway variables are read by the active process, and
missing values fail closed before any network request is attempted.
"""

from __future__ import annotations

import logging
import os
import re
from typing import Any

import requests
from dotenv import load_dotenv

load_dotenv(override=False)

logger = logging.getLogger(__name__)

_GRAPH_ROOT = "https://graph.facebook.com"
_DEFAULT_GRAPH_VERSION = "v23.0"
_DEFAULT_LANGUAGE = "en"
_DEFAULT_BOOKING_TEMPLATE = "booking_confirmation"
_DEFAULT_ADMIN_TEMPLATE = "admin_booking_alert"
_REQUEST_TIMEOUT_SECONDS = 15
_PROVIDER_LOG_VALUE_LIMIT = 300


class WhatsAppConfigurationError(RuntimeError):
    """Raised when a required WhatsApp Cloud API setting is unavailable."""


def _first_environment_value(*names: str) -> str | None:
    for name in names:
        value = os.getenv(name, "").strip()
        if value:
            return value
    return None


def _required_environment_value(*names: str) -> str:
    value = _first_environment_value(*names)
    if value:
        return value

    preferred_name = names[0]
    raise WhatsAppConfigurationError(
        f"Missing required Railway variable: {preferred_name}."
    )


def _normalise_graph_version(value: str | None) -> str:
    version = (value or _DEFAULT_GRAPH_VERSION).strip()
    return version if version.startswith("v") else f"v{version}"


def _normalise_phone_number(value: str, *, country_code: str = "234") -> str:
    digits = re.sub(r"\D", "", value or "")
    if digits.startswith("0"):
        digits = f"{country_code}{digits[1:]}"

    if len(digits) < 8 or len(digits) > 15:
        raise ValueError("WhatsApp recipient must be a valid international number.")

    return digits


def _template_language(scope: str) -> str:
    return (
        _first_environment_value(
            f"WHATSAPP_{scope.upper()}_TEMPLATE_LANGUAGE",
            "WHATSAPP_TEMPLATE_LANGUAGE",
        )
        or _DEFAULT_LANGUAGE
    )


def _compact_log_value(value: Any) -> str:
    if value is None:
        return ""

    compact = re.sub(r"\s+", " ", str(value)).strip()
    return compact[:_PROVIDER_LOG_VALUE_LIMIT]


def _gateway_settings() -> dict[str, str]:
    return {
        "token": _required_environment_value(
            "WHATSAPP_TOKEN",
            "META_WHATSAPP_TOKEN",
        ),
        "phone_number_id": _required_environment_value(
            "WHATSAPP_PHONE_NUMBER_ID",
            "META_WHATSAPP_PHONE_NUMBER_ID",
        ),
        "graph_version": _normalise_graph_version(
            _first_environment_value("WHATSAPP_GRAPH_API_VERSION")
        ),
    }


def _safe_response_payload(response: requests.Response) -> dict[str, Any]:
    try:
        payload = response.json()
    except ValueError:
        payload = {"message": response.text[:500]}

    return payload if isinstance(payload, dict) else {"data": payload}


def _error_result(
    *,
    error_code: str,
    message: str,
    status_code: int | None = None,
    provider_code: Any = None,
    provider_subcode: Any = None,
) -> dict[str, Any]:
    return {
        "success": False,
        "provider": "meta_whatsapp",
        "error_code": error_code,
        "message": message,
        "status_code": status_code,
        "provider_code": provider_code,
        "provider_subcode": provider_subcode,
    }


def _send_message(payload: dict[str, Any]) -> dict[str, Any]:
    try:
        settings = _gateway_settings()
    except WhatsAppConfigurationError as exc:
        logger.warning("WhatsApp delivery skipped: %s", exc)
        return _error_result(
            error_code="configuration_error",
            message=str(exc),
        )

    url = (
        f"{_GRAPH_ROOT}/{settings['graph_version']}/"
        f"{settings['phone_number_id']}/messages"
    )
    headers = {
        "Authorization": f"Bearer {settings['token']}",
        "Content-Type": "application/json",
    }

    try:
        response = requests.post(
            url,
            headers=headers,
            json=payload,
            timeout=_REQUEST_TIMEOUT_SECONDS,
        )
    except requests.RequestException as exc:
        logger.warning("WhatsApp network request failed: %s", exc.__class__.__name__)
        return _error_result(
            error_code="network_error",
            message="The WhatsApp provider could not be reached.",
        )

    response_payload = _safe_response_payload(response)
    if response.ok:
        logger.info(
            "WhatsApp message accepted by Meta",
            extra={"message_type": payload.get("type")},
        )
        return {
            "success": True,
            "provider": "meta_whatsapp",
            "status_code": response.status_code,
            "data": response_payload,
        }

    provider_error = response_payload.get("error")
    if not isinstance(provider_error, dict):
        provider_error = {}

    provider_code = provider_error.get("code")
    provider_subcode = provider_error.get("error_subcode")
    trace_id = provider_error.get("fbtrace_id")
    provider_message = _compact_log_value(provider_error.get("message"))

    error_data = provider_error.get("error_data")
    provider_details = ""
    if isinstance(error_data, dict):
        provider_details = _compact_log_value(error_data.get("details"))

    logger.warning(
        "WhatsApp Cloud API rejected a message status=%s code=%s subcode=%s "
        "trace_id=%s message=%s details=%s",
        response.status_code,
        provider_code,
        provider_subcode,
        trace_id,
        provider_message,
        provider_details,
    )

    return _error_result(
        error_code="provider_error",
        message=provider_error.get("message", "WhatsApp delivery was rejected."),
        status_code=response.status_code,
        provider_code=provider_code,
        provider_subcode=provider_subcode,
    )


def send_booking_confirmation(phone: str, name: str, vehicle: str) -> dict[str, Any]:
    """Send the client-facing booking confirmation template."""

    try:
        recipient = _normalise_phone_number(phone)
    except ValueError as exc:
        return _error_result(error_code="invalid_recipient", message=str(exc))

    template_name = (
        _first_environment_value("WHATSAPP_BOOKING_TEMPLATE")
        or _DEFAULT_BOOKING_TEMPLATE
    )

    payload = {
        "messaging_product": "whatsapp",
        "to": recipient,
        "type": "template",
        "template": {
            "name": template_name,
            "language": {"code": _template_language("booking")},
            "components": [
                {
                    "type": "body",
                    "parameters": [
                        {"type": "text", "text": name},
                        {"type": "text", "text": vehicle},
                    ],
                }
            ],
        },
    }
    return _send_message(payload)


def _admin_template_uses_parameters() -> bool:
    value = (
        _first_environment_value("WHATSAPP_ADMIN_TEMPLATE_USES_PARAMETERS")
        or "0"
    )
    return value.lower() in {"1", "true", "yes", "on"}


def send_template_admin(user: str, vehicle: str, time: str) -> dict[str, Any]:
    """Send the approved admin-booking template to Aura's admin recipient."""

    try:
        recipient = _normalise_phone_number(
            _required_environment_value("WHATSAPP_ADMIN_PHONE_NUMBER")
        )
    except (WhatsAppConfigurationError, ValueError) as exc:
        logger.warning("WhatsApp admin alert skipped: %s", exc)
        return _error_result(
            error_code="configuration_error",
            message=str(exc),
        )

    template_name = (
        _first_environment_value("WHATSAPP_ADMIN_TEMPLATE")
        or _DEFAULT_ADMIN_TEMPLATE
    )
    template: dict[str, Any] = {
        "name": template_name,
        "language": {"code": _template_language("admin")},
    }

    if _admin_template_uses_parameters():
        template["components"] = [
            {
                "type": "body",
                "parameters": [
                    {"type": "text", "text": user},
                    {"type": "text", "text": vehicle},
                    {"type": "text", "text": time},
                ],
            }
        ]

    return _send_message(
        {
            "messaging_product": "whatsapp",
            "to": recipient,
            "type": "template",
            "template": template,
        }
    )


def notify_admin_new_booking(
    user: str,
    vehicle: str,
    time: str,
) -> dict[str, Any]:
    """Notify the configured Aura administrator about a new booking."""

    return send_template_admin(user, vehicle, time)


def send_text_admin(user: str, vehicle: str, time: str) -> dict[str, Any]:
    """Send a free-form admin alert when an open service window permits it."""

    try:
        recipient = _normalise_phone_number(
            _required_environment_value("WHATSAPP_ADMIN_PHONE_NUMBER")
        )
    except (WhatsAppConfigurationError, ValueError) as exc:
        logger.warning("WhatsApp admin text skipped: %s", exc)
        return _error_result(
            error_code="configuration_error",
            message=str(exc),
        )

    body = f"NEW Booking\n\nClient: {user}\nVehicle: {vehicle}\nTime: {time}"
    return _send_message(
        {
            "messaging_product": "whatsapp",
            "to": recipient,
            "type": "text",
            "text": {"body": body},
        }
    )
