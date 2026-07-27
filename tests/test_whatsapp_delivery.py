from __future__ import annotations

from typing import Any

from services import whatsapp


class FakeResponse:
    def __init__(
        self,
        status_code: int,
        payload: dict[str, Any],
        *,
        text: str = "",
    ) -> None:
        self.status_code = status_code
        self._payload = payload
        self.text = text
        self.ok = 200 <= status_code < 300

    def json(self) -> dict[str, Any]:
        return self._payload


def _clear_whatsapp_environment(monkeypatch) -> None:
    names = {
        "WHATSAPP_TOKEN",
        "META_WHATSAPP_TOKEN",
        "WHATSAPP_PHONE_NUMBER_ID",
        "META_WHATSAPP_PHONE_NUMBER_ID",
        "WHATSAPP_ADMIN_PHONE_NUMBER",
        "WHATSAPP_GRAPH_API_VERSION",
        "WHATSAPP_ADMIN_TEMPLATE",
        "WHATSAPP_BOOKING_TEMPLATE",
        "WHATSAPP_ADMIN_TEMPLATE_USES_PARAMETERS",
        "WHATSAPP_TEMPLATE_LANGUAGE",
        "WHATSAPP_ADMIN_TEMPLATE_LANGUAGE",
        "WHATSAPP_BOOKING_TEMPLATE_LANGUAGE",
    }
    for name in names:
        monkeypatch.delenv(name, raising=False)


def _set_valid_environment(monkeypatch) -> None:
    monkeypatch.setenv("WHATSAPP_TOKEN", "test-token")
    monkeypatch.setenv("WHATSAPP_PHONE_NUMBER_ID", "123456789012345")
    monkeypatch.setenv("WHATSAPP_ADMIN_PHONE_NUMBER", "+234 707 449 0640")
    monkeypatch.setenv("WHATSAPP_GRAPH_API_VERSION", "23.0")
    monkeypatch.setenv("WHATSAPP_TEMPLATE_LANGUAGE", "en")


def test_missing_phone_number_id_never_calls_meta(monkeypatch):
    _clear_whatsapp_environment(monkeypatch)
    monkeypatch.setenv("WHATSAPP_TOKEN", "test-token")
    monkeypatch.setenv("WHATSAPP_ADMIN_PHONE_NUMBER", "2347074490640")

    def unexpected_post(*_args, **_kwargs):
        raise AssertionError("Meta must not be called with incomplete configuration")

    monkeypatch.setattr(whatsapp.requests, "post", unexpected_post)

    result = whatsapp.notify_admin_new_booking(
        user="Femi Adebayo",
        vehicle="Mercedes-Benz GLE 450",
        time="2026-07-27T10:00:00",
    )

    assert result["success"] is False
    assert result["error_code"] == "configuration_error"
    assert "WHATSAPP_PHONE_NUMBER_ID" in result["message"]


def test_admin_booking_alert_uses_approved_static_template(monkeypatch):
    _clear_whatsapp_environment(monkeypatch)
    _set_valid_environment(monkeypatch)
    captured: dict[str, Any] = {}

    def fake_post(url, *, headers, json, timeout):
        captured.update(
            {
                "url": url,
                "headers": headers,
                "json": json,
                "timeout": timeout,
            }
        )
        return FakeResponse(
            200,
            {"messages": [{"id": "wamid.test"}]},
        )

    monkeypatch.setattr(whatsapp.requests, "post", fake_post)

    result = whatsapp.notify_admin_new_booking(
        user="Femi Adebayo",
        vehicle="Mercedes-Benz GLE 450",
        time="2026-07-27T10:00:00",
    )

    assert result["success"] is True
    assert captured["url"] == (
        "https://graph.facebook.com/v23.0/123456789012345/messages"
    )
    assert captured["headers"]["Authorization"] == "Bearer test-token"
    assert captured["json"]["to"] == "2347074490640"
    assert captured["json"]["template"] == {
        "name": "admin_booking_alert",
        "language": {"code": "en"},
    }
    assert captured["timeout"] == 15


def test_booking_confirmation_uses_two_body_parameters(monkeypatch):
    _clear_whatsapp_environment(monkeypatch)
    _set_valid_environment(monkeypatch)
    captured: dict[str, Any] = {}

    def fake_post(url, *, headers, json, timeout):
        captured["payload"] = json
        return FakeResponse(200, {"messages": [{"id": "wamid.test"}]})

    monkeypatch.setattr(whatsapp.requests, "post", fake_post)

    result = whatsapp.send_booking_confirmation(
        phone="08012345678",
        name="Femi Adebayo",
        vehicle="Mercedes-Benz GLE 450",
    )

    template = captured["payload"]["template"]
    parameters = template["components"][0]["parameters"]

    assert result["success"] is True
    assert captured["payload"]["to"] == "2348012345678"
    assert template["name"] == "booking_confirmation"
    assert template["language"] == {"code": "en"}
    assert [item["text"] for item in parameters] == [
        "Femi Adebayo",
        "Mercedes-Benz GLE 450",
    ]


def test_scoped_template_languages_override_global_language(monkeypatch):
    _clear_whatsapp_environment(monkeypatch)
    _set_valid_environment(monkeypatch)
    monkeypatch.setenv("WHATSAPP_TEMPLATE_LANGUAGE", "en")
    monkeypatch.setenv("WHATSAPP_ADMIN_TEMPLATE_LANGUAGE", "en_US")
    monkeypatch.setenv("WHATSAPP_BOOKING_TEMPLATE_LANGUAGE", "en_GB")
    captured: list[dict[str, Any]] = []

    def fake_post(url, *, headers, json, timeout):
        captured.append(json)
        return FakeResponse(200, {"messages": [{"id": "wamid.test"}]})

    monkeypatch.setattr(whatsapp.requests, "post", fake_post)

    whatsapp.notify_admin_new_booking(
        user="Femi Adebayo",
        vehicle="Mercedes-Benz GLE 450",
        time="2026-07-27T10:00:00",
    )
    whatsapp.send_booking_confirmation(
        phone="2348012345678",
        name="Femi Adebayo",
        vehicle="Mercedes-Benz GLE 450",
    )

    assert captured[0]["template"]["language"] == {"code": "en_US"}
    assert captured[1]["template"]["language"] == {"code": "en_GB"}


def test_dynamic_admin_template_includes_booking_parameters(monkeypatch):
    _clear_whatsapp_environment(monkeypatch)
    _set_valid_environment(monkeypatch)
    monkeypatch.setenv("WHATSAPP_ADMIN_TEMPLATE", "admin_booking_alert_v2")
    monkeypatch.setenv("WHATSAPP_ADMIN_TEMPLATE_USES_PARAMETERS", "true")
    captured: dict[str, Any] = {}

    def fake_post(url, *, headers, json, timeout):
        captured["payload"] = json
        return FakeResponse(200, {"messages": [{"id": "wamid.test"}]})

    monkeypatch.setattr(whatsapp.requests, "post", fake_post)

    result = whatsapp.notify_admin_new_booking(
        user="Femi Adebayo",
        vehicle="Mercedes-Benz GLE 450",
        time="2026-07-27T10:00:00",
    )

    parameters = captured["payload"]["template"]["components"][0]["parameters"]
    assert result["success"] is True
    assert [item["text"] for item in parameters] == [
        "Femi Adebayo",
        "Mercedes-Benz GLE 450",
        "2026-07-27T10:00:00",
    ]


def test_meta_400_is_returned_and_logs_safe_provider_details(monkeypatch, caplog):
    _clear_whatsapp_environment(monkeypatch)
    _set_valid_environment(monkeypatch)

    def fake_post(url, *, headers, json, timeout):
        return FakeResponse(
            400,
            {
                "error": {
                    "message": "(#132000) Number of parameters does not match.",
                    "type": "OAuthException",
                    "code": 132000,
                    "error_data": {
                        "messaging_product": "whatsapp",
                        "details": "body: number of localizable_params does not match",
                    },
                    "fbtrace_id": "trace-test",
                }
            },
        )

    monkeypatch.setattr(whatsapp.requests, "post", fake_post)
    caplog.set_level("WARNING", logger=whatsapp.__name__)

    result = whatsapp.notify_admin_new_booking(
        user="Femi Adebayo",
        vehicle="Mercedes-Benz GLE 450",
        time="2026-07-27T10:00:00",
    )

    assert result == {
        "success": False,
        "provider": "meta_whatsapp",
        "error_code": "provider_error",
        "message": "(#132000) Number of parameters does not match.",
        "status_code": 400,
        "provider_code": 132000,
        "provider_subcode": None,
    }
    assert "details=body: number of localizable_params does not match" in caplog.text


def test_invalid_admin_recipient_never_calls_meta(monkeypatch):
    _clear_whatsapp_environment(monkeypatch)
    monkeypatch.setenv("WHATSAPP_TOKEN", "test-token")
    monkeypatch.setenv("WHATSAPP_PHONE_NUMBER_ID", "123456789012345")
    monkeypatch.setenv("WHATSAPP_ADMIN_PHONE_NUMBER", "123")

    def unexpected_post(*_args, **_kwargs):
        raise AssertionError("Meta must not be called with an invalid recipient")

    monkeypatch.setattr(whatsapp.requests, "post", unexpected_post)

    result = whatsapp.notify_admin_new_booking(
        user="Femi Adebayo",
        vehicle="Mercedes-Benz GLE 450",
        time="2026-07-27T10:00:00",
    )

    assert result["success"] is False
    assert result["error_code"] == "configuration_error"
    assert "valid international number" in result["message"]
