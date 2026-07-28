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
_DEFAULT_ADMIN_TEMPLATE = "admin_booking_alert_v1"
_REQUEST_TIMEOUT_SECONDS = 15
_PROVIDER_LOG_VALUE_LIMIT = 300
_MAX_TEMPLATE_TEXT_LENGTH = 1024


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


def _template_parameter_names(scope: str) -> list[str]:
    raw = _first_environment_value(
        f"WHATSAPP_{scope.upper()}_TEMPLATE_PARAMETER_NAMES"
    )
    if not raw:
        return []

    return [item.strip() for item in raw.split(",") if item.strip()]


def _normalise_template_text(value: Any, *, field_name: str) -> str:
    text = re.sub(r"[\r\n\t]+", " ", str(value or ""))
    text = re.sub(r" {2,}", " ", text).strip()

    if not text:
        raise ValueError(f"WhatsApp template field '{field_name}' cannot be empty.")

    return text[:_MAX_TEMPLATE_TEXT_LENGTH]


def _text_parameters(
    *,
    scope: str,
    values: list[tuple[str, Any]],
) -> list[dict[str, str]]:
    parameter_names = _template_parameter_names(scope)
    if parameter_names and len(parameter_names) != len(values):
        raise WhatsAppConfigurationError(
            f"WHATSAPP_{scope.upper()}_TEMPLATE_PARAMETER_NAMES must contain "
            f"exactly {len(values)} comma-separated names."
        )

    parameters: list[dict[str, str]] = []
    for index, (field_name, raw_value) in enumerate(values):
        parameter = {
            "type": "text",
            "text": _normalise_template_text(
                raw_value,
                field_name=field_name,
            ),
        }
        if parameter_names:
            parameter["parameter_name"] = parameter_names[index]
        parameters.append(parameter)

    return parameters


def _compact_log_value(value: Any) -> str:
    if value is None:
        return ""

    compact = re.sub(r"\s+", " ", str(value)).strip()
    return compact[:_PROVIDER_LOG_VALUE_LIMIT]


def _message_contract(payload: dict[str, Any]) -> dict[str, Any]:
    template = payload.get("template")
    if not isinstance(template, dict):
        return {
            "message_type": payload.get("type"),
            "template_name": "",
            "language": "",
            "body_parameter_count": 0,
            "named_parameter_count": 0,
            "recipient_digits": len(str(payload.get("to") or "")),
        }

    language = template.get("language")
    language_code = language.get("code") if isinstance(language, dict) else ""
    body_parameters: list[dict[str, Any]] = []

    components = template.get("components")
    if isinstance(components, list):
        for component in components:
            if not isinstance(component, dict) or component.get("type") != "body":
                continue
            parameters = component.get("parameters")
            if isinstance(parameters, list):
                body_parameters.extend(
                    item for item in parameters if isinstance(item, dict)
                )

    return {
        "message_type": payload.get("type"),
        "template_name": template.get("name", ""),
        "language": language_code,
        "body_parameter_count": len(body_parameters),
        "named_parameter_count": sum(
            1 for item in body_parameters if item.get("parameter_name")
        ),
        "recipient_digits": len(str(payload.get("to") or "")),
    }


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

    contract = _message_contract(payload)
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
        logger.warning(
            "WhatsApp network request failed error=%s template=%s language=%s",
            exc.__class__.__name__,
            contract["template_name"],
            contract["language"],
        )
        return _error_result(
            error_code="network_error",
            message="The WhatsApp provider could not be reached.",
        )

    response_payload = _safe_response_payload(response)
    if response.ok:
        logger.info(
            "WhatsApp message accepted by Meta type=%s template=%s language=%s "
            "body_parameters=%s named_parameters=%s recipient_digits=%s",
            contract["message_type"],
            contract["template_name"],
            contract["language"],
            contract["body_parameter_count"],
            contract["named_parameter_count"],
            contract["recipient_digits"],
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
        "trace_id=%s type=%s template=%s language=%s body_parameters=%s "
        "named_parameters=%s recipient_digits=%s message=%s details=%s",
        response.status_code,
        provider_code,
        provider_subcode,
        trace_id,
        contract["message_type"],
        contract["template_name"],
        contract["language"],
        contract["body_parameter_count"],
        contract["named_parameter_count"],
        contract["recipient_digits"],
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
        parameters = _text_parameters(
            scope="booking",
            values=[
                ("client_name", name),
                ("vehicle", vehicle),
            ],
        )
    except (ValueError, WhatsAppConfigurationError) as exc:
        logger.warning("WhatsApp booking confirmation skipped: %s", exc)
        return _error_result(
            error_code="configuration_error",
            message=str(exc),
        )

    template_name = (
        _first_environment_value("WHATSAPP_BOOKING_TEMPLATE")
        or _DEFAULT_BOOKING_TEMPLATE
    )

    payload = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": recipient,
        "type": "template",
        "template": {
            "name": template_name,
            "language": {"code": _template_language("booking")},
            "components": [
                {
                    "type": "body",
                    "parameters": parameters,
                }
            ],
        },
    }
    return _send_message(payload)


def _admin_template_uses_parameters() -> bool:
    value = _first_environment_value("WHATSAPP_ADMIN_TEMPLATE_USES_PARAMETERS")
    if value is None:
        return True
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
        try:
            parameters = _text_parameters(
                scope="admin",
                values=[
                    ("client_name", user),
                    ("vehicle", vehicle),
                    ("preferred_time", time),
                ],
            )
        except (ValueError, WhatsAppConfigurationError) as exc:
            logger.warning("WhatsApp admin alert skipped: %s", exc)
            return _error_result(
                error_code="configuration_error",
                message=str(exc),
            )

        template["components"] = [
            {
                "type": "body",
                "parameters": parameters,
            }
        ]

    return _send_message(
        {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
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
            "recipient_type": "individual",
            "to": recipient,
            "type": "text",
            "text": {"body": body},
        }
    )
