from __future__ import annotations

import logging

import services.rina_chat_engine as rina_chat_engine


def _base_context() -> dict:
    return {
        "vehicle_identity": "Private Mercedes-Benz",
        "health_score": 77,
        "alerts": [],
        "events": [],
        "consultations": {},
        "admin_summary": {},
        "health_status": "attention",
        "risk_reasons": ["private concern details"],
        "guidance": {},
        "care_context": {},
        "escalation": {"level": "monitor"},
        "intent": "general",
    }


def test_openai_authentication_failure_never_logs_key_or_context(
    monkeypatch,
    caplog,
    capsys,
):
    class FakeAuthenticationError(Exception):
        pass

    def reject_request(_context):
        raise FakeAuthenticationError(
            "Incorrect API key provided: sk-proj-sensitive-production-key"
        )

    monkeypatch.setattr(
        rina_chat_engine,
        "AuthenticationError",
        FakeAuthenticationError,
    )
    monkeypatch.setattr(rina_chat_engine, "generate_rina_response", reject_request)
    monkeypatch.setattr(rina_chat_engine, "update_user_behavior", lambda _intent: None)
    monkeypatch.setattr(
        rina_chat_engine,
        "get_user_behavior_profile",
        lambda: {},
    )
    monkeypatch.setattr(
        rina_chat_engine,
        "get_user_role_context",
        lambda _user_id, _car_id: "owner",
    )

    caplog.set_level(logging.WARNING, logger=rina_chat_engine.__name__)

    response = rina_chat_engine.RinaChatEngine.respond(
        user_id=12,
        car_id=34,
        message="How is my car doing?",
        context=_base_context(),
    )

    captured = capsys.readouterr()

    assert response == (
        "Private Mercedes-Benz is under observation (score: 77).\n\n"
        "A scheduled review is recommended."
    )
    assert "invalid_api_key" in caplog.text
    assert "sk-proj-sensitive-production-key" not in caplog.text
    assert "private concern details" not in caplog.text
    assert "How is my car doing?" not in caplog.text
    assert captured.out == ""
