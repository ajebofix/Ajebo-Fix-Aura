from __future__ import annotations

import ast
from pathlib import Path

from app import create_app
from rina.audit_models import RinaAIAuditEvent
from services.rina_runtime_flags import rina_orchestration_enabled


ROOT = Path(__file__).resolve().parents[1]


def _source(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_active_chat_uses_wave_1_3_orchestration_not_legacy_engine():
    source = _source("routes/chat.py")

    assert "orchestrate_rina" in source
    assert "resolve_rina_authority" in source
    assert "load_rina_chat_history" in source
    assert "save_rina_chat_turn" in source

    prohibited = (
        "RinaChatEngine",
        "RinaContextService",
        "rina.ai_brain",
        "rina.memory",
        "calculate_vehicle_health",
        "_match_vehicle_from_message",
    )
    for token in prohibited:
        assert token not in source


def test_active_chat_does_not_restore_legacy_broad_context_or_first_vehicle():
    source = _source("routes/chat.py")

    assert '"rina_active_car_id"' in source
    assert '"rina_conversation_id"' in source
    assert 'session.get("active_vehicle_id")' not in source
    assert 'session.get("selected_vehicle_id")' not in source
    assert 'session["rina_context"]' not in source
    assert 'session["rina_context_full"]' not in source
    assert "ownerships[0]" not in source
    assert ".first().car" not in source


def test_dashboard_default_is_presentation_only_and_explicit_selection_binds_rina():
    source = _source("dashboard/routes.py")

    # Dashboard presentation is allowed to choose a default card.
    assert 'session["active_vehicle_id"]' in source
    # That GET path must not rebuild the old broad Rina session context.
    assert 'session["rina_context"] =' not in source
    assert 'session["rina_context_full"] =' not in source
    # Explicit selection must be re-authorized and then establish the short-lived
    # Rina binding for the exact selected vehicle. The general dashboard supports
    # proved owner/driver relationships; professional scope stays in its workflow.
    assert "resolve_rina_authority" in source
    assert "AUTHORITY_OWNER" in source
    assert "AUTHORITY_DRIVER" in source
    assert 'session["rina_active_car_id"] = vehicle_id' in source
    # Legacy broad context is removed rather than reused.
    assert 'session.pop("rina_context", None)' in source
    assert 'session.pop("rina_context_full", None)' in source


def test_rina_template_is_explicit_vehicle_csrf_and_dom_safe():
    source = _source("templates/components/rina_chat.html")

    assert 'id="rina-vehicle-select"' in source
    assert "/chat/select-vehicle" in source
    assert "car_id: selectedCarId" in source
    assert "X-CSRF-Token" in source
    assert "textContent" in source
    assert "innerHTML" not in source


def test_provider_context_does_not_include_raw_advisor_notes():
    source = _source("services/rina_provider_context.py")

    assert "memory.summaries" in source
    assert "advisor_memory" not in source
    assert "AdvisorNote" not in source
    assert "allowed_actions" in source
    assert "Never switch vehicles" in source
    assert "Do not make a mechanical diagnosis" in source


def test_orchestrator_does_not_directly_import_openai_or_emit_actions():
    source = _source("services/rina_orchestrator.py")

    assert "import openai" not in source
    assert "from openai" not in source
    assert "OpenAIRinaProvider" in source
    # Wave 1.3 provider responses remain text-only; every active response contract
    # is constructed with no executable actions.
    assert "actions=()," in source
    assert "ACTION_APPROVE_TREATMENT" not in source
    assert "ACTION_APPROVE_ASSESSMENT" not in source


def test_openai_adapter_uses_responses_without_tools_or_provider_storage():
    source = _source("rina/providers/openai_provider.py")

    assert ".responses.create(" in source
    assert "store=False" in source
    assert "tools=" not in source
    assert "timeout=self.timeout_seconds" in source
    assert "max_retries=self.max_retries" in source


def test_audit_schema_has_no_prompt_message_response_or_chain_of_thought_columns():
    columns = set(RinaAIAuditEvent.__table__.columns.keys())
    prohibited = {
        "prompt",
        "message",
        "response",
        "response_body",
        "chain_of_thought",
        "reasoning",
        "api_key",
        "secret",
        "password",
    }

    assert prohibited.isdisjoint(columns)
    assert {
        "request_id",
        "user_id",
        "car_id",
        "authority",
        "state",
        "outcome",
        "provider_status",
        "evidence_refs",
        "audit_metadata",
    }.issubset(columns)


def test_material_summary_service_never_accepts_raw_message_text():
    source = _source("services/rina_material_summary.py")

    assert "message" not in source
    assert "emotional_state=None" in source
    assert "urgency_level=None" in source
    assert "provenance=\"rules\"" in source
    assert "client_summary" in source


def test_legacy_rina_modules_are_not_registered_by_application_startup():
    tree = ast.parse(_source("app.py"))
    imported_modules: set[str] = set()

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported_modules.add(node.module)

    prohibited = {
        "services.rina_chat_engine",
        "services.rina_context_service",
        "rina.ai_brain",
        "rina.memory",
    }
    assert prohibited.isdisjoint(imported_modules)


def test_chat_route_registration_and_runtime_default_are_live():
    app = create_app()
    rules = {rule.rule for rule in app.url_map.iter_rules()}

    assert "/chat" in rules
    assert "/chat/context" in rules
    assert "/chat/select-vehicle" in rules
    assert "/chat/history" in rules
    assert rina_orchestration_enabled() is True


def test_release_document_names_all_deprecated_active_path_components():
    source = _source("docs/AURA_WAVE_1_3_RINA_RELEASE_AND_DEPRECATION.md")

    for token in (
        "services/rina_chat_engine.py",
        "services/rina_context_service.py",
        "rina/ai_brain.py",
        "rina/memory.py",
        "UserMemory",
        "ChatSession",
    ):
        assert token in source
