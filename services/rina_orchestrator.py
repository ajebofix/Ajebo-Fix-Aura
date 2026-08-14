"""Authority-first orchestration for Wave 1.3 A.J. Rina.

The active Rina route passes through this trusted request -> context -> memory ->
provider -> audit boundary. Authority and vehicle scope are resolved before any
provider call and remain authoritative when the provider is unavailable.
"""

from __future__ import annotations

import uuid
from typing import Final

from rina.providers.base import (
    RinaLanguageProvider,
    RinaProviderError,
)
from rina.providers.openai_provider import OpenAIRinaProvider
from services.rina_audit import record_rina_audit
from services.rina_authority import RinaAuthorityError
from services.rina_context_resolver import (
    RinaContextResolutionError,
    resolve_rina_vehicle_context,
)
from services.rina_contracts import (
    PROVIDER_STATUS_NOT_CALLED,
    PROVIDER_STATUS_OK,
    PROVIDER_STATUS_REJECTED,
    PROVIDER_STATUS_UNAVAILABLE,
    RINA_STATE_ABSTAINED,
    RINA_STATE_ANSWERED,
    RINA_STATE_AUTHORITY_DENIED,
    RINA_STATE_ESCALATION_REQUIRED,
    RINA_STATE_PROVIDER_UNAVAILABLE,
    RINA_STATE_VEHICLE_REQUIRED,
    RinaRequest,
    RinaResponse,
)
from services.rina_memory_service import load_rina_memory_bundle
from services.rina_provider_context import build_rina_provider_context
from services.rina_runtime_flags import (
    rina_openai_provider_enabled,
    rina_orchestration_enabled,
)


_PROVIDER_STATUS_DISABLED: Final = "disabled"
_DEFAULT_MEMORY_POLICY: Final = "vehicle_scoped_minimized_v1"

_DRIVING_SAFETY_QUESTIONS: Final = (
    "safe to drive",
    "safe for me to drive",
    "can i drive",
    "should i drive",
    "okay to drive",
    "ok to drive",
)


def _new_request_id() -> str:
    return uuid.uuid4().hex


def _clean_conversation_id(value: str | None, request_id: str) -> str:
    clean = (value or "").strip()
    if not clean:
        return request_id
    return clean[:64]


def _requires_driving_safety_escalation(message: str) -> bool:
    normalized = " ".join((message or "").lower().split())
    return any(phrase in normalized for phrase in _DRIVING_SAFETY_QUESTIONS)


def _fallback_message(authority: str) -> str:
    relationship = {
        "driver": "the assigned driver",
        "owner": "the vehicle owner",
        "advisor": "the scoped advisor",
        "administrator": "the administrator",
    }.get(authority, "an authorised Aura user")

    return (
        f"I recognise this session as {relationship} for the selected vehicle, "
        "but I can't use Rina's language service right now. The Aura vehicle "
        "record is still intact; anything time-sensitive should go through "
        "advisor review."
    )


def _audit_response(
    *,
    response: RinaResponse,
    user_id: int | None,
    outcome: str,
    provider: str | None,
    provider_model: str | None,
    provider_request_id: str | None,
    evidence_refs: tuple[dict[str, object], ...] = (),
    channel: str,
    context_version: int | None,
    failure_class: str | None = None,
    provider_attempted: bool,
    commit: bool,
) -> None:
    metadata = {
        "channel": channel,
        "context_version": context_version,
        "memory_policy": _DEFAULT_MEMORY_POLICY,
        "provider_attempted": provider_attempted,
    }
    if failure_class:
        metadata["failure_class"] = failure_class

    record_rina_audit(
        request_id=response.request_id,
        user_id=user_id,
        car_id=response.car_id if response.car_id > 0 else None,
        authority=response.authority or None,
        state=response.state,
        outcome=outcome,
        provider_status=response.provider_status,
        provider=provider,
        provider_model=provider_model,
        provider_request_id=provider_request_id,
        evidence_refs=evidence_refs,
        metadata=metadata,
        commit=commit,
    )


def _provider_for_runtime() -> RinaLanguageProvider | None:
    if not rina_openai_provider_enabled():
        return None
    return OpenAIRinaProvider()


def orchestrate_rina(
    *,
    user_id: int,
    car_id: int | None,
    message: str,
    channel: str = "in_app",
    conversation_id: str | None = None,
    provider: RinaLanguageProvider | None = None,
    request_id: str | None = None,
    audit_commit: bool = True,
) -> RinaResponse:
    """Produce one Rina response without allowing provider-side authority drift."""

    resolved_request_id = (request_id or _new_request_id()).strip()[:64]
    if not resolved_request_id:
        resolved_request_id = _new_request_id()

    if car_id is None:
        response = RinaResponse(
            request_id=resolved_request_id,
            car_id=0,
            authority="",
            state=RINA_STATE_VEHICLE_REQUIRED,
            message="Select a vehicle before asking Rina about its health record.",
            uncertainty="no active vehicle was supplied",
            escalation=None,
            actions=(),
            evidence_refs=(),
            provider_status=PROVIDER_STATUS_NOT_CALLED,
        )
        _audit_response(
            response=response,
            user_id=user_id,
            outcome="vehicle_required",
            provider=None,
            provider_model=None,
            provider_request_id=None,
            channel=channel,
            context_version=None,
            provider_attempted=False,
            commit=audit_commit,
        )
        return response

    try:
        context = resolve_rina_vehicle_context(user_id=user_id, car_id=car_id)
    except (RinaAuthorityError, RinaContextResolutionError):
        response = RinaResponse(
            request_id=resolved_request_id,
            car_id=0,
            authority="",
            state=RINA_STATE_AUTHORITY_DENIED,
            message="Rina can't use that vehicle context for this account.",
            uncertainty="vehicle authority could not be proven",
            escalation=None,
            actions=(),
            evidence_refs=(),
            provider_status=PROVIDER_STATUS_NOT_CALLED,
        )
        _audit_response(
            response=response,
            user_id=user_id,
            outcome="authority_denied",
            provider=None,
            provider_model=None,
            provider_request_id=None,
            channel=channel,
            context_version=None,
            provider_attempted=False,
            commit=audit_commit,
        )
        return response

    clean_message = (message or "").strip()
    if not clean_message:
        response = RinaResponse(
            request_id=resolved_request_id,
            car_id=context.car_id,
            authority=context.authority,
            state=RINA_STATE_ABSTAINED,
            message="Tell me what you want to understand about the selected vehicle.",
            uncertainty="no question was supplied",
            escalation=None,
            actions=(),
            evidence_refs=(),
            provider_status=PROVIDER_STATUS_NOT_CALLED,
        )
        _audit_response(
            response=response,
            user_id=user_id,
            outcome="abstained",
            provider=None,
            provider_model=None,
            provider_request_id=None,
            channel=channel,
            context_version=context.context_version,
            provider_attempted=False,
            commit=audit_commit,
        )
        return response

    if not rina_orchestration_enabled():
        response = RinaResponse(
            request_id=resolved_request_id,
            car_id=context.car_id,
            authority=context.authority,
            state=RINA_STATE_PROVIDER_UNAVAILABLE,
            message=_fallback_message(context.authority),
            uncertainty="the Wave 1.3 orchestration rollout flag is disabled",
            escalation=None,
            actions=(),
            evidence_refs=(),
            provider_status=_PROVIDER_STATUS_DISABLED,
        )
        _audit_response(
            response=response,
            user_id=user_id,
            outcome="feature_disabled",
            provider=None,
            provider_model=None,
            provider_request_id=None,
            channel=channel,
            context_version=context.context_version,
            failure_class="feature_disabled",
            provider_attempted=False,
            commit=audit_commit,
        )
        return response

    rina_request = RinaRequest(
        request_id=resolved_request_id,
        user_id=user_id,
        car_id=context.car_id,
        authority=context.authority,
        channel=channel,
        message=clean_message,
        conversation_id=_clean_conversation_id(
            conversation_id,
            resolved_request_id,
        ),
        context_version=context.context_version,
        memory_policy=_DEFAULT_MEMORY_POLICY,
        allowed_actions=context.allowed_actions,
        denied_actions=context.denied_actions,
    )

    memory = load_rina_memory_bundle(
        user_id=user_id,
        car_id=context.car_id,
        conversation_id=(
            rina_request.conversation_id if conversation_id else None
        ),
    )
    provider_context = build_rina_provider_context(
        rina_request=rina_request,
        context=context,
        memory=memory,
    )

    if _requires_driving_safety_escalation(clean_message):
        response = RinaResponse(
            request_id=resolved_request_id,
            car_id=context.car_id,
            authority=context.authority,
            state=RINA_STATE_ESCALATION_REQUIRED,
            message=(
                "I can't establish whether this vehicle is safe to drive from "
                "recorded data alone. That needs advisor review before I make "
                "a stronger claim."
            ),
            uncertainty="driving safety cannot be established from recorded context alone",
            escalation="advisor_review",
            actions=(),
            evidence_refs=provider_context.evidence_refs,
            provider_status=PROVIDER_STATUS_NOT_CALLED,
        )
        _audit_response(
            response=response,
            user_id=user_id,
            outcome="escalation_required",
            provider=None,
            provider_model=None,
            provider_request_id=None,
            evidence_refs=response.evidence_refs,
            channel=channel,
            context_version=context.context_version,
            provider_attempted=False,
            commit=audit_commit,
        )
        return response

    active_provider = provider
    if active_provider is None:
        try:
            active_provider = _provider_for_runtime()
        except RinaProviderError as exc:
            response = RinaResponse(
                request_id=resolved_request_id,
                car_id=context.car_id,
                authority=context.authority,
                state=RINA_STATE_PROVIDER_UNAVAILABLE,
                message=_fallback_message(context.authority),
                uncertainty="the configured language provider is unavailable",
                escalation=None,
                actions=(),
                evidence_refs=provider_context.evidence_refs,
                provider_status=exc.provider_status,
            )
            _audit_response(
                response=response,
                user_id=user_id,
                outcome="provider_failed",
                provider="openai",
                provider_model=None,
                provider_request_id=None,
                evidence_refs=response.evidence_refs,
                channel=channel,
                context_version=context.context_version,
                failure_class=exc.failure_class,
                provider_attempted=False,
                commit=audit_commit,
            )
            return response

    if active_provider is None:
        response = RinaResponse(
            request_id=resolved_request_id,
            car_id=context.car_id,
            authority=context.authority,
            state=RINA_STATE_PROVIDER_UNAVAILABLE,
            message=_fallback_message(context.authority),
            uncertainty="the language-provider rollout flag is disabled",
            escalation=None,
            actions=(),
            evidence_refs=provider_context.evidence_refs,
            provider_status=_PROVIDER_STATUS_DISABLED,
        )
        _audit_response(
            response=response,
            user_id=user_id,
            outcome="feature_disabled",
            provider="openai",
            provider_model=None,
            provider_request_id=None,
            evidence_refs=response.evidence_refs,
            channel=channel,
            context_version=context.context_version,
            failure_class="feature_disabled",
            provider_attempted=False,
            commit=audit_commit,
        )
        return response

    provider_name = getattr(active_provider, "provider_name", "provider")
    provider_model = getattr(active_provider, "model", None)

    try:
        result = active_provider.generate(provider_context.request)
    except RinaProviderError as exc:
        provider_status = (
            PROVIDER_STATUS_REJECTED
            if exc.provider_status == PROVIDER_STATUS_REJECTED
            else PROVIDER_STATUS_UNAVAILABLE
        )
        response = RinaResponse(
            request_id=resolved_request_id,
            car_id=context.car_id,
            authority=context.authority,
            state=RINA_STATE_PROVIDER_UNAVAILABLE,
            message=_fallback_message(context.authority),
            uncertainty="the language provider could not complete this request",
            escalation=None,
            actions=(),
            evidence_refs=provider_context.evidence_refs,
            provider_status=provider_status,
        )
        _audit_response(
            response=response,
            user_id=user_id,
            outcome="provider_failed",
            provider=str(provider_name),
            provider_model=(str(provider_model) if provider_model else None),
            provider_request_id=None,
            evidence_refs=response.evidence_refs,
            channel=channel,
            context_version=context.context_version,
            failure_class=exc.failure_class,
            provider_attempted=True,
            commit=audit_commit,
        )
        return response

    response = RinaResponse(
        request_id=resolved_request_id,
        car_id=context.car_id,
        authority=context.authority,
        state=RINA_STATE_ANSWERED,
        message=result.text,
        uncertainty=provider_context.uncertainty,
        escalation=None,
        actions=(),
        evidence_refs=provider_context.evidence_refs,
        provider_status=PROVIDER_STATUS_OK,
    )
    _audit_response(
        response=response,
        user_id=user_id,
        outcome="answered",
        provider=result.provider,
        provider_model=result.model,
        provider_request_id=result.provider_request_id,
        evidence_refs=response.evidence_refs,
        channel=channel,
        context_version=context.context_version,
        provider_attempted=True,
        commit=audit_commit,
    )
    return response
