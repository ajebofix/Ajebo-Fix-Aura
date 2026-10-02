"""Authority-first A.J. Rina chat routes for Wave 1.3.

The chat surface no longer guesses a vehicle from free text, selects the first
vehicle, reads user-wide chat history, invokes the legacy Rina engine, or stores
broad context blobs in Flask session. Session state contains only short-lived
vehicle/conversation identifiers and is re-authorized on every request.
"""

from __future__ import annotations

import re
import uuid

from flask import (
    Blueprint,
    abort,
    current_app,
    jsonify,
    render_template,
    request,
    session,
)
from flask_login import current_user, login_required
from sqlalchemy import or_

from evidence.models import EvidenceExtraction, VehicleEvidence
from extensions import db
from historical_ingestion.whatsapp_bundle import restart_whatsapp_bundle_analysis
from historical_ingestion.reconciliation import (
    HistoricalReconciliationError,
    apply_reconciliation,
)
from models import (
    AdvisorNote,
    Car,
    CarDriver,
    CarOwnership,
    Consultation,
    TreatmentPlan,
    User,
    VehicleAssessment,
)
from services.rina_advisor_360 import build_rina_historical_copilot_context
from services.rina_historical_intelligence_bridge import (
    discover_intelligence_episode_choices,
    stage_intelligence_episode_candidate,
)
from services.rina_historical_review import (
    already_applied as historical_review_already_applied,
    candidate_prompt as historical_candidate_prompt,
    discover_review_choices,
    interpret_turn as interpret_historical_review_turn,
    review_choices_prompt,
    review_preview as historical_review_preview,
    summarize_state as summarize_historical_review,
    update_review_from_interpretation,
    validate_ready_to_apply as validate_historical_review_ready,
)
from services.rina_audit import record_rina_audit
from services.rina_authority import (
    ACTION_APPLY_ADVISOR_APPROVED_HISTORY,
    ACTION_PREPARE_HISTORICAL_RECORDS,
    RinaAuthorityError,
    resolve_rina_authority,
)
from services.rina_contracts import (
    RINA_STATE_ABSTAINED,
    RINA_STATE_ANSWERED,
    RINA_STATE_AUTHORITY_DENIED,
    RINA_STATE_ESCALATION_REQUIRED,
    RINA_STATE_PROVIDER_UNAVAILABLE,
    RINA_STATE_VEHICLE_REQUIRED,
)
from services.rina_context_resolver import (
    RinaContextResolutionError,
    resolve_rina_vehicle_context,
)
from services.rina_material_summary import (
    MATERIAL_ADVISOR_REVIEW,
    MATERIAL_BOOKING_REQUEST,
    record_rina_material_summary,
)
from services.rina_memory_service import (
    load_rina_chat_history,
    save_rina_chat_turn,
)
from rina.providers.base import RinaProviderError
from services.rina_orchestrator import orchestrate_rina
from services.rina_speaker import account_help, describe_speaker, speaker_identity

chat_bp = Blueprint("chat", __name__)

_SESSION_CAR_KEY = "rina_active_car_id"
_SESSION_CONVERSATION_KEY = "rina_conversation_id"
_SESSION_HISTORY_REVIEW_EXTRACTION_KEY = "rina_history_review_extraction_id"
_SESSION_HISTORY_REVIEW_CANDIDATE_KEY = "rina_history_review_candidate_id"
_SESSION_HISTORY_REVIEW_PHASE_KEY = "rina_history_review_phase"
_SESSION_HISTORY_REVIEW_CHOICES_KEY = "rina_history_review_choices"
_SESSION_HISTORY_INTELLIGENCE_CHOICES_KEY = "rina_history_intelligence_choices"
_BOOKING_PATTERN = re.compile(
    r"\b(book|consult|consultation|appointment|schedule|reserve|assessment)\b",
    re.IGNORECASE,
)


def detect_intent(message: str) -> str:
    """Return the small UI intent contract still used for the booking CTA."""

    return "booking" if _BOOKING_PATTERN.search(message or "") else "general"


def _normalise_chat_command(message: str) -> str:
    return " ".join(str(message or "").strip().lower().split())


def _historical_review_start_requested(message: str) -> bool:
    text = _normalise_chat_command(message)
    if not text:
        return False
    direct_phrases = (
        "record the previous jobs",
        "record previous jobs",
        "record the historical",
        "record historical",
        "add previous jobs",
        "add the previous jobs",
        "clean up the history",
        "clean up history",
        "review the previous jobs",
        "review previous jobs",
        "review historical services",
        "record past services",
        "record the past services",
        "reconcile the history",
        "reconcile historical",
    )
    if any(phrase in text for phrase in direct_phrases):
        return True
    action_words = ("record", "review", "reconcile", "add", "clean up")
    history_words = ("previous", "historical", "past", "history")
    work_words = ("job", "jobs", "service", "services", "work", "repair", "repairs")
    return (
        any(word in text for word in action_words)
        and any(word in text for word in history_words)
        and any(word in text for word in work_words)
    )


def _explicit_historical_apply_confirmation(message: str) -> bool:
    text = _normalise_chat_command(message).strip(" .!?")
    if not text or len(text) > 120:
        return False

    # Final durable-write authorization must be a short, affirmative command.
    # Never treat explanatory text such as "do not write until I confirm and
    # record it" as authorization merely because it contains both words.
    if any(
        marker in text
        for marker in (
            "do not ",
            "don't ",
            "dont ",
            "not yet",
            "until i confirm",
            "before i confirm",
            "wait for",
        )
    ):
        return False

    return text in {
        "confirm and record",
        "confirm & record",
        "confirm and record it",
        "yes confirm and record",
        "yes confirm and record it",
        "record it",
        "record them",
        "yes record it",
        "yes record them",
        "go ahead and record it",
        "go ahead and record them",
        "apply approved history",
        "apply the approved history",
    }


def _historical_review_cancel_requested(message: str) -> bool:
    text = _normalise_chat_command(message).strip(" .!?")
    return text in {
        "cancel",
        "cancel review",
        "stop",
        "stop review",
        "exit review",
        "leave review",
        "don't record",
        "do not record",
    }


def _historical_review_choice_index(message: str, choice_count: int) -> int | None:
    text = _normalise_chat_command(message).strip(" .!?")
    if text.isdigit():
        value = int(text)
        return value - 1 if 1 <= value <= choice_count else None
    words = {
        "first": 0,
        "second": 1,
        "third": 2,
        "fourth": 3,
        "fifth": 4,
    }
    for word, index in words.items():
        if word in text and index < choice_count:
            return index
    return None


def _historical_intelligence_choice_index(message: str, choices) -> int | None:
    numeric = _historical_review_choice_index(message, len(choices))
    if numeric is not None:
        return numeric

    text = _normalise_chat_command(message)
    if not text:
        return None

    month_numbers = {
        "january": "01",
        "february": "02",
        "march": "03",
        "april": "04",
        "may": "05",
        "june": "06",
        "july": "07",
        "august": "08",
        "september": "09",
        "october": "10",
        "november": "11",
        "december": "12",
    }
    matched: list[int] = []
    for index, item in enumerate(choices):
        haystack = " ".join(
            str(value or "").lower()
            for value in (
                getattr(item, "title", None),
                getattr(item, "date_start", None),
                getattr(item, "date_end", None),
                getattr(item, "summary", None),
            )
        )
        title_words = {
            word
            for word in _normalise_chat_command(getattr(item, "title", "")).split()
            if len(word) >= 4
        }
        month_match = any(
            month in text
            and (
                month in haystack
                or f"-{number}-" in haystack
            )
            for month, number in month_numbers.items()
        )
        title_match = bool(title_words) and sum(
            1 for word in title_words if word in text
        ) >= min(2, len(title_words))
        if month_match or title_match:
            matched.append(index)

    return matched[0] if len(matched) == 1 else None


def _historical_intelligence_choices_prompt(choices) -> str:
    lines = [
        "I found historical episodes for this vehicle that need advisor review or "
        "clarification. Choose the one you want to work through with me:"
    ]
    for index, item in enumerate(choices, start=1):
        date_bits = [value for value in (item.date_start, item.date_end) if value]
        date_label = (
            date_bits[0]
            if len(date_bits) == 1 or (len(date_bits) == 2 and date_bits[0] == date_bits[1])
            else " to ".join(date_bits)
            if date_bits
            else "date not established"
        )
        lines.append(
            f"{index}. **{item.title}** — {date_label} · {item.comparison}"
        )
    lines.append(
        "These are candidate episodes from imported evidence, not durable vehicle "
        "history. Reply with the number to review one, or say **cancel review**."
    )
    return "\n".join(lines)


def _start_staged_intelligence_review(*, context, choice_key: str) -> dict[str, object]:
    extraction = stage_intelligence_episode_candidate(
        context=context,
        choice_key=choice_key,
        actor_user_id=current_user.id,
    )
    state = summarize_historical_review(
        extraction_id=extraction.id,
        actor_user_id=current_user.id,
        car_id=context.car_id,
    )
    if state.all_reviewed:
        _history_review_bind(
            extraction_id=extraction.id,
            candidate_id=None,
            phase="awaiting_apply_confirmation",
        )
        return {
            "reply": historical_review_preview(state),
            "phase": "awaiting_apply_confirmation",
            "extraction_id": extraction.id,
            "candidate_id": None,
            "provider_status": "not_called",
            "provider": None,
            "provider_model": None,
            "provider_request_id": None,
        }

    _history_review_bind(
        extraction_id=extraction.id,
        candidate_id=state.next_candidate_id,
        phase="reviewing",
    )
    return {
        "reply": historical_candidate_prompt(state, state.next_candidate_id),
        "phase": "reviewing",
        "extraction_id": extraction.id,
        "candidate_id": state.next_candidate_id,
        "provider_status": "not_called",
        "provider": None,
        "provider_model": None,
        "provider_request_id": None,
    }


def _history_review_session_active() -> bool:
    phase = str(session.get(_SESSION_HISTORY_REVIEW_PHASE_KEY) or "")
    return (
        _coerce_car_id(session.get(_SESSION_HISTORY_REVIEW_EXTRACTION_KEY)) is not None
        or phase in {"choose_episode", "choose_intelligence_episode"}
    )


def _history_review_bind(
    *,
    extraction_id: int,
    candidate_id: str | None,
    phase: str,
) -> None:
    session[_SESSION_HISTORY_REVIEW_EXTRACTION_KEY] = int(extraction_id)
    if candidate_id:
        session[_SESSION_HISTORY_REVIEW_CANDIDATE_KEY] = str(candidate_id)[:64]
    else:
        session.pop(_SESSION_HISTORY_REVIEW_CANDIDATE_KEY, None)
    session[_SESSION_HISTORY_REVIEW_PHASE_KEY] = str(phase)[:40]
    session.pop(_SESSION_HISTORY_REVIEW_CHOICES_KEY, None)
    session.pop(_SESSION_HISTORY_INTELLIGENCE_CHOICES_KEY, None)


def _history_review_choice_prompt(context) -> dict[str, object]:
    choices = discover_review_choices(context)
    if not choices:
        intelligence_choices = discover_intelligence_episode_choices(context)
        if len(intelligence_choices) == 1:
            return _start_staged_intelligence_review(
                context=context,
                choice_key=intelligence_choices[0].choice_key,
            )
        if len(intelligence_choices) > 1:
            session[_SESSION_HISTORY_INTELLIGENCE_CHOICES_KEY] = [
                item.choice_key for item in intelligence_choices
            ]
            session[_SESSION_HISTORY_REVIEW_PHASE_KEY] = "choose_intelligence_episode"
            session.pop(_SESSION_HISTORY_REVIEW_EXTRACTION_KEY, None)
            session.pop(_SESSION_HISTORY_REVIEW_CANDIDATE_KEY, None)
            session.pop(_SESSION_HISTORY_REVIEW_CHOICES_KEY, None)
            return {
                "reply": _historical_intelligence_choices_prompt(
                    intelligence_choices
                ),
                "phase": "choose_intelligence_episode",
                "extraction_id": None,
                "candidate_id": None,
                "provider_status": "not_called",
                "provider": None,
                "provider_model": None,
                "provider_request_id": None,
            }

        backlog = build_rina_historical_copilot_context(context) or {}
        unprepared = [
            row
            for row in (backlog.get("reconciliation_backlog") or [])
            if isinstance(row, dict)
            and row.get("state") == "reconciliation_not_prepared"
        ]
        if unprepared:
            return {
                "reply": (
                    "I found a formal historical episode with case attribution, but its "
                    "reconciliation checklist has not been prepared yet. I won't convert "
                    "that attribution into durable history without the governed "
                    "reconciliation step."
                ),
                "phase": None,
                "extraction_id": None,
                "candidate_id": None,
                "provider_status": "not_called",
                "provider": None,
                "provider_model": None,
                "provider_request_id": None,
            }
        return {
            "reply": (
                "I don't have a source-supported completed-work episode that is eligible "
                "for historical recording on this selected vehicle. I won't invent one "
                "or pull work across from another vehicle."
            ),
            "phase": None,
            "extraction_id": None,
            "candidate_id": None,
            "provider_status": "not_called",
            "provider": None,
            "provider_model": None,
            "provider_request_id": None,
        }

    if len(choices) > 1:
        session[_SESSION_HISTORY_REVIEW_CHOICES_KEY] = [
            int(item["extraction_id"]) for item in choices
        ]
        session[_SESSION_HISTORY_REVIEW_PHASE_KEY] = "choose_episode"
        session.pop(_SESSION_HISTORY_REVIEW_EXTRACTION_KEY, None)
        session.pop(_SESSION_HISTORY_REVIEW_CANDIDATE_KEY, None)
        return {
            "reply": review_choices_prompt(choices),
            "phase": "choose_episode",
            "extraction_id": None,
            "candidate_id": None,
            "provider_status": "not_called",
            "provider": None,
            "provider_model": None,
            "provider_request_id": None,
        }

    extraction_id = int(choices[0]["extraction_id"])
    state = summarize_historical_review(
        extraction_id=extraction_id,
        actor_user_id=current_user.id,
        car_id=context.car_id,
    )
    if historical_review_already_applied(extraction_id) is not None:
        _clear_historical_review_binding()
        return {
            "reply": (
                "That historical reconciliation has already been applied to durable "
                "vehicle history, so I won't duplicate it."
            ),
            "phase": None,
            "extraction_id": extraction_id,
            "candidate_id": None,
            "provider_status": "not_called",
            "provider": None,
            "provider_model": None,
            "provider_request_id": None,
        }

    if state.all_reviewed:
        _history_review_bind(
            extraction_id=extraction_id,
            candidate_id=None,
            phase="awaiting_apply_confirmation",
        )
        return {
            "reply": historical_review_preview(state),
            "phase": "awaiting_apply_confirmation",
            "extraction_id": extraction_id,
            "candidate_id": None,
            "provider_status": "not_called",
            "provider": None,
            "provider_model": None,
            "provider_request_id": None,
        }

    _history_review_bind(
        extraction_id=extraction_id,
        candidate_id=state.next_candidate_id,
        phase="reviewing",
    )
    return {
        "reply": historical_candidate_prompt(state, state.next_candidate_id),
        "phase": "reviewing",
        "extraction_id": extraction_id,
        "candidate_id": state.next_candidate_id,
        "provider_status": "not_called",
        "provider": None,
        "provider_model": None,
        "provider_request_id": None,
    }


def _handle_historical_review_turn(*, context, message: str) -> dict[str, object]:
    if _historical_review_cancel_requested(message):
        _clear_historical_review_binding()
        return {
            "reply": (
                "Historical review closed. I did not apply any new durable vehicle "
                "history. Any saved advisor draft remains available for later review."
            ),
            "phase": None,
            "extraction_id": None,
            "candidate_id": None,
            "provider_status": "not_called",
            "provider": None,
            "provider_model": None,
            "provider_request_id": None,
        }

    phase = str(session.get(_SESSION_HISTORY_REVIEW_PHASE_KEY) or "")
    if phase == "choose_intelligence_episode":
        choice_keys = [
            str(item)
            for item in (
                session.get(_SESSION_HISTORY_INTELLIGENCE_CHOICES_KEY) or []
            )
            if str(item).strip()
        ]
        live_choices = [
            item
            for item in discover_intelligence_episode_choices(context)
            if item.choice_key in set(choice_keys)
        ]
        index = _historical_intelligence_choice_index(message, live_choices)
        if index is None:
            return {
                "reply": (
                    "Reply with the number, month or name of the historical episode "
                    "you want to review, or say **cancel review**."
                ),
                "phase": "choose_intelligence_episode",
                "extraction_id": None,
                "candidate_id": None,
                "provider_status": "not_called",
                "provider": None,
                "provider_model": None,
                "provider_request_id": None,
            }
        return _start_staged_intelligence_review(
            context=context,
            choice_key=live_choices[index].choice_key,
        )

    if phase == "choose_episode":
        choice_ids = [
            int(item)
            for item in (session.get(_SESSION_HISTORY_REVIEW_CHOICES_KEY) or [])
            if str(item).isdigit()
        ]
        index = _historical_review_choice_index(message, len(choice_ids))
        if index is None:
            return {
                "reply": (
                    "Reply with the number of the historical episode you want to review, "
                    "or say **cancel review**."
                ),
                "phase": "choose_episode",
                "extraction_id": None,
                "candidate_id": None,
                "provider_status": "not_called",
                "provider": None,
                "provider_model": None,
                "provider_request_id": None,
            }
        extraction_id = choice_ids[index]
        state = summarize_historical_review(
            extraction_id=extraction_id,
            actor_user_id=current_user.id,
            car_id=context.car_id,
        )
        if state.all_reviewed:
            _history_review_bind(
                extraction_id=extraction_id,
                candidate_id=None,
                phase="awaiting_apply_confirmation",
            )
            return {
                "reply": historical_review_preview(state),
                "phase": "awaiting_apply_confirmation",
                "extraction_id": extraction_id,
                "candidate_id": None,
                "provider_status": "not_called",
                "provider": None,
                "provider_model": None,
                "provider_request_id": None,
            }
        _history_review_bind(
            extraction_id=extraction_id,
            candidate_id=state.next_candidate_id,
            phase="reviewing",
        )
        return {
            "reply": historical_candidate_prompt(state, state.next_candidate_id),
            "phase": "reviewing",
            "extraction_id": extraction_id,
            "candidate_id": state.next_candidate_id,
            "provider_status": "not_called",
            "provider": None,
            "provider_model": None,
            "provider_request_id": None,
        }

    extraction_id = _coerce_car_id(
        session.get(_SESSION_HISTORY_REVIEW_EXTRACTION_KEY)
    )
    if extraction_id is None:
        return _history_review_choice_prompt(context)

    state = summarize_historical_review(
        extraction_id=extraction_id,
        actor_user_id=current_user.id,
        car_id=context.car_id,
    )

    if _explicit_historical_apply_confirmation(message):
        if not state.all_reviewed:
            _history_review_bind(
                extraction_id=extraction_id,
                candidate_id=state.next_candidate_id,
                phase="reviewing",
            )
            return {
                "reply": historical_review_preview(state),
                "phase": "reviewing",
                "extraction_id": extraction_id,
                "candidate_id": state.next_candidate_id,
                "provider_status": "not_called",
                "provider": None,
                "provider_model": None,
                "provider_request_id": None,
            }

        state = validate_historical_review_ready(
            extraction_id=extraction_id,
            actor_user_id=current_user.id,
            car_id=context.car_id,
        )
        if state.confirmed_count == 0:
            _clear_historical_review_binding()
            return {
                "reply": (
                    "There are no advisor-confirmed completed items to write. I closed "
                    "the review without changing durable vehicle history."
                ),
                "phase": None,
                "extraction_id": extraction_id,
                "candidate_id": None,
                "provider_status": "not_called",
                "provider": None,
                "provider_model": None,
                "provider_request_id": None,
                "applied_plan_id": None,
            }

        plan = apply_reconciliation(
            extraction_id=extraction_id,
            actor_user_id=current_user.id,
        )
        plan_id = plan.id if plan is not None else None
        _clear_historical_review_binding()
        return {
            "reply": (
                f"Recorded. I applied {state.confirmed_count} advisor-confirmed "
                "historical item(s) to this vehicle's durable history. The source "
                "evidence and advisor review remain linked for audit. I did not write "
                "items you marked not done or uncertain."
            ),
            "phase": None,
            "extraction_id": extraction_id,
            "candidate_id": None,
            "provider_status": "not_called",
            "provider": None,
            "provider_model": None,
            "provider_request_id": None,
            "applied_plan_id": plan_id,
        }

    current_candidate_id = str(
        session.get(_SESSION_HISTORY_REVIEW_CANDIDATE_KEY) or ""
    ).strip() or state.next_candidate_id

    interpretation = interpret_historical_review_turn(
        message=message,
        state=state,
        current_candidate_id=current_candidate_id,
        phase=phase or "reviewing",
    )
    interpreted = interpretation.payload

    if interpreted.get("intent") == "cancel":
        _clear_historical_review_binding()
        return {
            "reply": (
                "Historical review closed. Nothing new was written to durable vehicle "
                "history."
            ),
            "phase": None,
            "extraction_id": extraction_id,
            "candidate_id": None,
            "provider_status": "ok",
            "provider": interpretation.provider,
            "provider_model": interpretation.model,
            "provider_request_id": interpretation.provider_request_id,
        }

    if interpreted.get("intent") in {"show_draft", "question", "no_change"} and not (
        interpreted.get("changes") or interpreted.get("additions")
    ):
        reply = historical_review_preview(state)
        note = str(interpreted.get("assistant_note") or "").strip()
        if note and interpreted.get("intent") == "question":
            reply = f"{note}\n\n{reply}"
        return {
            "reply": reply,
            "phase": (
                "awaiting_apply_confirmation" if state.all_reviewed else "reviewing"
            ),
            "extraction_id": extraction_id,
            "candidate_id": state.next_candidate_id,
            "provider_status": "ok",
            "provider": interpretation.provider,
            "provider_model": interpretation.model,
            "provider_request_id": interpretation.provider_request_id,
        }

    updated = update_review_from_interpretation(
        extraction_id=extraction_id,
        actor_user_id=current_user.id,
        car_id=context.car_id,
        interpretation=interpreted,
    )

    if updated.all_reviewed:
        _history_review_bind(
            extraction_id=extraction_id,
            candidate_id=None,
            phase="awaiting_apply_confirmation",
        )
        reply = historical_review_preview(updated)
        phase = "awaiting_apply_confirmation"
        candidate_id = None
    else:
        _history_review_bind(
            extraction_id=extraction_id,
            candidate_id=updated.next_candidate_id,
            phase=(
                "awaiting_date"
                if updated.pending_date_candidate_id == updated.next_candidate_id
                else "reviewing"
            ),
        )
        reply = historical_candidate_prompt(updated, updated.next_candidate_id)
        phase = str(session.get(_SESSION_HISTORY_REVIEW_PHASE_KEY) or "reviewing")
        candidate_id = updated.next_candidate_id

    return {
        "reply": reply,
        "phase": phase,
        "extraction_id": extraction_id,
        "candidate_id": candidate_id,
        "provider_status": "ok",
        "provider": interpretation.provider,
        "provider_model": interpretation.model,
        "provider_request_id": interpretation.provider_request_id,
    }


def _coerce_car_id(value) -> int | None:
    try:
        car_id = int(value)
    except (TypeError, ValueError):
        return None
    return car_id if car_id > 0 else None


def _new_conversation_id() -> str:
    return uuid.uuid4().hex


def _clear_historical_review_binding() -> None:
    session.pop(_SESSION_HISTORY_REVIEW_EXTRACTION_KEY, None)
    session.pop(_SESSION_HISTORY_REVIEW_CANDIDATE_KEY, None)
    session.pop(_SESSION_HISTORY_REVIEW_PHASE_KEY, None)
    session.pop(_SESSION_HISTORY_REVIEW_CHOICES_KEY, None)
    session.pop(_SESSION_HISTORY_INTELLIGENCE_CHOICES_KEY, None)


def _clear_rina_binding() -> None:
    session.pop(_SESSION_CAR_KEY, None)
    session.pop(_SESSION_CONVERSATION_KEY, None)
    _clear_historical_review_binding()


def _validated_session_car_id() -> int | None:
    car_id = _coerce_car_id(session.get(_SESSION_CAR_KEY))
    if car_id is None:
        _clear_rina_binding()
        return None

    try:
        resolve_rina_authority(user_id=current_user.id, car_id=car_id)
    except RinaAuthorityError:
        _clear_rina_binding()
        return None

    return car_id


def _conversation_id_for(car_id: int) -> str:
    active_car_id = _validated_session_car_id()
    conversation_id = str(session.get(_SESSION_CONVERSATION_KEY) or "").strip()

    if active_car_id == car_id and conversation_id:
        return conversation_id[:64]

    return _new_conversation_id()


def _bind_rina_vehicle(*, car_id: int, conversation_id: str | None = None) -> str:
    previous_car_id = _coerce_car_id(session.get(_SESSION_CAR_KEY))
    if previous_car_id != car_id:
        conversation_id = None
        _clear_historical_review_binding()

    resolved_conversation_id = (conversation_id or "").strip()[
        :64
    ] or _new_conversation_id()
    session[_SESSION_CAR_KEY] = car_id
    session[_SESSION_CONVERSATION_KEY] = resolved_conversation_id
    return resolved_conversation_id


def _vehicle_choice(car: Car) -> dict[str, object]:
    """Return an authority-checked, disambiguated vehicle choice."""

    authority = resolve_rina_authority(user_id=current_user.id, car_id=car.id)
    ownership = (
        CarOwnership.query.filter_by(car_id=car.id, is_active=True)
        .order_by(CarOwnership.id.desc())
        .first()
    )
    owner = (
        db.session.get(User, ownership.user_id)
        if ownership is not None and ownership.user_id
        else None
    )
    client_name = (owner.name or "").strip() if owner is not None else ""
    plate_number = (
        (ownership.plate_number or "").strip() if ownership is not None else ""
    )
    vin = (car.vin or "").strip().upper()
    vin_tail = vin[-6:] if vin else ""

    detail_parts = [
        part
        for part in (
            client_name,
            plate_number,
            f"VIN …{vin_tail}" if vin_tail else "",
        )
        if part
    ]
    context_label = " · ".join([car.rina_display_name, *detail_parts])

    return {
        "car_id": car.id,
        "label": car.rina_display_name,
        "authority": authority.authority,
        "client_name": client_name or None,
        "plate_number": plate_number or None,
        "vin_tail": vin_tail or None,
        "context_label": context_label,
    }


def _professional_vehicle_search(
    query: str,
    *,
    limit: int = 20,
) -> list[dict[str, object]]:
    """Search only the professional vehicle scope that Rina can re-authorize."""

    if current_user.role not in {"admin", "advisor"}:
        return []

    clean_query = " ".join(str(query or "").strip().split())[:120]
    if len(clean_query) < 2:
        return []

    pattern = (
        "%"
        + clean_query.replace("\\", "\\\\")
        .replace("%", "\\%")
        .replace("_", "\\_")
        + "%"
    )

    search = Car.query.outerjoin(
        CarOwnership, CarOwnership.car_id == Car.id
    ).outerjoin(User, User.id == CarOwnership.user_id)

    search = search.filter(
        or_(
            Car.brand.ilike(pattern, escape="\\"),
            Car.model.ilike(pattern, escape="\\"),
            Car.vin.ilike(pattern, escape="\\"),
            db.and_(
                CarOwnership.is_active.is_(True),
                or_(
                    CarOwnership.plate_number.ilike(pattern, escape="\\"),
                    User.name.ilike(pattern, escape="\\"),
                ),
            ),
        )
    )

    if current_user.role == "advisor":
        search = search.filter(
            or_(
                *[
                    Car.id.in_(
                        db.session.query(model.car_id).filter(
                            model.advisor_id == current_user.id
                        )
                    )
                    for model in (
                        Consultation,
                        VehicleAssessment,
                        TreatmentPlan,
                        AdvisorNote,
                    )
                ]
            )
        )

    choices: list[dict[str, object]] = []
    for candidate in search.distinct().order_by(Car.id).limit(limit).all():
        try:
            choices.append(_vehicle_choice(candidate))
        except RinaAuthorityError:
            continue

    choices.sort(
        key=lambda item: (
            str(item.get("client_name") or "").lower(),
            str(item["label"]).lower(),
            int(item["car_id"]),
        )
    )
    return choices


def _authorized_vehicle_choices(
    *,
    explicit_car_id: int | None = None,
) -> list[dict[str, object]]:
    """Return selectable Rina vehicles without exposing broad admin fleet data."""

    car_ids: set[int] = set()

    for ownership in CarOwnership.query.filter_by(
        user_id=current_user.id,
        is_active=True,
    ).all():
        if ownership.car_id:
            car_ids.add(int(ownership.car_id))

    for assignment in CarDriver.query.filter_by(
        user_id=current_user.id,
        is_active=True,
    ).all():
        if assignment.car_id:
            car_ids.add(int(assignment.car_id))

    # Advisor/administrator pages may explicitly name a vehicle without making
    # the Rina selector a broad fleet browser. The authority service still has
    # to prove access before that vehicle is returned.
    if explicit_car_id is not None:
        try:
            resolve_rina_authority(
                user_id=current_user.id,
                car_id=explicit_car_id,
            )
        except RinaAuthorityError:
            pass
        else:
            car_ids.add(explicit_car_id)

    cars = Car.query.filter(Car.id.in_(car_ids)).all() if car_ids else []
    choices = [_vehicle_choice(car) for car in cars]
    choices.sort(key=lambda item: (str(item["label"]).lower(), int(item["car_id"])))
    return choices


@chat_bp.get("/chat/context")
@login_required
def chat_context():
    """Return only the vehicle choices this user can explicitly bind to Rina."""

    page_car_id = _coerce_car_id(request.args.get("car_id"))
    active_car_id = _validated_session_car_id()
    choices = _authorized_vehicle_choices(explicit_car_id=page_car_id or active_car_id)
    choice_ids = {int(item["car_id"]) for item in choices}

    return (
        jsonify(
            {
                "vehicles": choices,
                "speaker": speaker_identity(current_user.id),
                "account_welcome": describe_speaker(speaker_identity(current_user.id)),
                "active_car_id": (
                    active_car_id if active_car_id in choice_ids else None
                ),
                "page_car_id": page_car_id if page_car_id in choice_ids else None,
                "conversation_id": (
                    session.get(_SESSION_CONVERSATION_KEY)
                    if active_car_id in choice_ids
                    else None
                ),
            }
        ),
        200,
    )


@chat_bp.get("/chat/vehicle-search")
@login_required
def chat_vehicle_search():
    """Bounded professional search for an explicit Rina vehicle context."""

    if current_user.role not in {"admin", "advisor"}:
        return jsonify({"error": "Professional vehicle search is not available."}), 403

    query = str(request.args.get("q") or "").strip()[:120]
    return (
        jsonify(
            {
                "query": query,
                "vehicles": _professional_vehicle_search(query, limit=20),
            }
        ),
        200,
    )


@chat_bp.get("/chat/historical-copilot")
@login_required
def chat_historical_copilot():
    """Read-only supervised historical backlog for an explicit vehicle."""

    car_id = _coerce_car_id(request.args.get("car_id"))
    if car_id is None:
        return jsonify({"error": "A valid vehicle is required."}), 400

    try:
        context = resolve_rina_vehicle_context(
            user_id=current_user.id,
            car_id=car_id,
        )
    except (RinaAuthorityError, RinaContextResolutionError):
        return jsonify({"error": "That vehicle is not available to this account."}), 403

    if (
        context.authority not in {"advisor", "administrator"}
        or ACTION_PREPARE_HISTORICAL_RECORDS not in context.allowed_actions
    ):
        return jsonify({"error": "Historical Copilot requires advisor access."}), 403

    backlog = build_rina_historical_copilot_context(context) or {}
    owner_user_id = backlog.get("owner_user_id")
    client_vehicle_url = (
        f"/admin/clients/{owner_user_id}/vehicles/new" if owner_user_id else None
    )
    return (
        jsonify(
            {
                "car_id": car_id,
                "backlog": backlog,
                "review_url": f"/admin/cars/{car_id}/historical-records",
                "client_vehicle_url": client_vehicle_url,
                "policy": (
                    "Rina prepares candidate history; an advisor reviews and "
                    "authorizes durable changes."
                ),
            }
        ),
        200,
    )


@chat_bp.post("/chat/historical-copilot/rebuild")
@login_required
def chat_historical_copilot_rebuild():
    """Re-run whole-corpus Historical Intelligence without re-uploading the ZIP."""

    data = request.get_json(silent=True) or {}
    car_id = _coerce_car_id(data.get("car_id"))
    if car_id is None:
        return jsonify({"error": "A valid vehicle is required."}), 400

    try:
        context = resolve_rina_vehicle_context(
            user_id=current_user.id,
            car_id=car_id,
        )
    except (RinaAuthorityError, RinaContextResolutionError):
        return jsonify({"error": "That vehicle is not available to this account."}), 403

    if (
        context.authority not in {"advisor", "administrator"}
        or ACTION_PREPARE_HISTORICAL_RECORDS not in context.allowed_actions
    ):
        return jsonify({"error": "Historical Copilot requires advisor access."}), 403

    source = (
        VehicleEvidence.query.filter(
            VehicleEvidence.car_id == car_id,
            VehicleEvidence.evidence_type == "archive",
            VehicleEvidence.historical_source_type == "whatsapp_conversation",
            VehicleEvidence.storage_state == "available",
            VehicleEvidence.deleted_at.is_(None),
            VehicleEvidence.review_status != "superseded",
            ~VehicleEvidence.bundle_parent_items.any(),
        )
        .order_by(VehicleEvidence.uploaded_at.desc(), VehicleEvidence.id.desc())
        .first()
    )
    if source is None:
        return jsonify({"error": "No active WhatsApp case bundle is available."}), 404

    try:
        result = restart_whatsapp_bundle_analysis(
            evidence_id=source.id,
            actor_user_id=current_user.id,
        )
        record_rina_audit(
            request_id=_new_conversation_id(),
            user_id=current_user.id,
            car_id=car_id,
            authority=context.authority,
            state="answered",
            outcome="answered",
            action_family="historical_rebuild",
            provider_status="not_called",
            evidence_refs=[{"type": "vehicle_evidence", "id": source.id}],
            metadata={
                "channel": "advisor_workspace",
                "historical_intelligence_version": 2,
            },
            commit=False,
        )
        db.session.commit()
    except Exception:
        db.session.rollback()
        current_app.logger.exception(
            "rina_historical_intelligence_rebuild_failed car_id=%s actor_id=%s",
            car_id,
            current_user.id,
        )
        return jsonify({"error": "Aura could not start the historical reconstruction."}), 500

    return (
        jsonify(
            {
                "car_id": car_id,
                "evidence_id": source.id,
                "extraction_id": result.extraction_id,
                "status": result.status,
                "phase": result.phase,
                "reused_existing": result.reused_existing,
                "status_url": (
                    f"/admin/cars/{car_id}/historical-records/"
                    f"{source.id}/analysis-status"
                ),
                "message": (
                    "Rina is rebuilding the full historical intelligence from the "
                    "already-imported WhatsApp bundle."
                ),
            }
        ),
        202 if result.status == "processing" else 200,
    )


@chat_bp.post("/chat/historical-copilot/apply-reconciliation")
@login_required
def chat_historical_copilot_apply_reconciliation():
    """Apply only an advisor-reviewed reconciliation after explicit confirmation."""

    data = request.get_json(silent=True) or {}
    car_id = _coerce_car_id(data.get("car_id"))
    extraction_id = _coerce_car_id(data.get("extraction_id"))
    confirmed = data.get("confirm") is True

    if car_id is None or extraction_id is None or not confirmed:
        return jsonify({"error": "Explicit advisor confirmation is required."}), 400

    try:
        context = resolve_rina_vehicle_context(
            user_id=current_user.id,
            car_id=car_id,
        )
    except (RinaAuthorityError, RinaContextResolutionError):
        return jsonify({"error": "That vehicle is not available to this account."}), 403

    if (
        context.authority not in {"advisor", "administrator"}
        or ACTION_APPLY_ADVISOR_APPROVED_HISTORY not in context.allowed_actions
    ):
        return jsonify({"error": "Historical Copilot requires advisor access."}), 403

    extraction = db.session.get(EvidenceExtraction, extraction_id)
    if (
        extraction is None
        or extraction.evidence is None
        or extraction.evidence.car_id != car_id
    ):
        return jsonify({"error": "Approved history does not match this vehicle."}), 409

    try:
        plan = apply_reconciliation(
            extraction_id=extraction_id,
            actor_user_id=current_user.id,
        )
        record_rina_audit(
            request_id=_new_conversation_id(),
            user_id=current_user.id,
            car_id=car_id,
            authority=context.authority,
            state="answered",
            outcome="answered",
            action_family="historical_apply",
            provider_status="not_called",
            evidence_refs=[
                {"type": "historical_reconciliation", "id": extraction_id}
            ],
            metadata={"channel": "advisor_workspace"},
            commit=False,
        )
        db.session.commit()
    except HistoricalReconciliationError as exc:
        db.session.rollback()
        return jsonify({"error": str(exc)}), 400
    except Exception:
        db.session.rollback()
        current_app.logger.exception(
            "rina_historical_copilot_apply_failed car_id=%s extraction_id=%s actor_id=%s",
            car_id,
            extraction_id,
            current_user.id,
        )
        return jsonify({"error": "Aura could not apply the approved history."}), 500

    if plan is None:
        return (
            jsonify(
                {
                    "car_id": car_id,
                    "extraction_id": extraction_id,
                    "state": "nothing_confirmed_to_apply",
                    "plan_id": None,
                }
            ),
            200,
        )

    return (
        jsonify(
            {
                "car_id": car_id,
                "extraction_id": extraction_id,
                "state": "applied",
                "plan_id": plan.id,
            }
        ),
        200,
    )


@chat_bp.post("/chat/select-vehicle")
@login_required
def select_chat_vehicle():
    """Bind Rina to one explicitly selected, re-authorized vehicle."""

    data = request.get_json(silent=True) or {}
    car_id = _coerce_car_id(data.get("car_id"))

    if car_id is None and data.get("clear") is True:
        _clear_rina_binding()
        return (
            jsonify(
                {
                    "car_id": None,
                    "conversation_id": None,
                    "authority": None,
                    "label": "Advisor overview",
                }
            ),
            200,
        )

    if car_id is None:
        return jsonify({"error": "A valid vehicle is required."}), 400

    try:
        authority = resolve_rina_authority(user_id=current_user.id, car_id=car_id)
    except RinaAuthorityError:
        return jsonify({"error": "That vehicle is not available to Rina."}), 403

    car = db.session.get(Car, car_id)
    if car is None:
        return jsonify({"error": "Vehicle not found."}), 404

    conversation_id = _bind_rina_vehicle(car_id=car_id)
    choice = _vehicle_choice(car)
    return (
        jsonify(
            {
                "car_id": car_id,
                "conversation_id": conversation_id,
                "authority": authority.authority,
                "label": choice["label"],
                "context_label": choice["context_label"],
            }
        ),
        200,
    )


@chat_bp.post("/chat")
@login_required
def chat():
    data = request.get_json(silent=True) or {}
    message = str(data.get("message") or "").strip()
    explicit_car_id = _coerce_car_id(data.get("car_id"))
    car_id = explicit_car_id or _validated_session_car_id()
    intent = detect_intent(message)

    conversation_id = (
        _conversation_id_for(car_id) if car_id is not None else _new_conversation_id()
    )

    historical_review_requested = (
        car_id is not None
        and (
            _history_review_session_active()
            or _historical_review_start_requested(message)
        )
    )

    if historical_review_requested:
        try:
            context = resolve_rina_vehicle_context(
                user_id=current_user.id,
                car_id=car_id,
            )
        except (RinaAuthorityError, RinaContextResolutionError):
            _clear_historical_review_binding()
            return (
                jsonify(
                    {
                        "reply": "That vehicle is not available to this account.",
                        "intent": "historical_review",
                        "car_id": None,
                        "authority": None,
                        "state": RINA_STATE_AUTHORITY_DENIED,
                        "conversation_id": None,
                        "uncertainty": "vehicle authority could not be proven",
                        "escalation": None,
                        "evidence_refs": [],
                    }
                ),
                403,
            )

        if (
            context.authority not in {"advisor", "administrator"}
            or ACTION_PREPARE_HISTORICAL_RECORDS not in context.allowed_actions
        ):
            _clear_historical_review_binding()
            return (
                jsonify(
                    {
                        "reply": (
                            "Historical recording through Rina requires advisor access "
                            "to this vehicle."
                        ),
                        "intent": "historical_review",
                        "car_id": context.car_id,
                        "authority": context.authority,
                        "state": RINA_STATE_AUTHORITY_DENIED,
                        "conversation_id": None,
                        "uncertainty": "historical write authority is not available",
                        "escalation": None,
                        "evidence_refs": [],
                    }
                ),
                403,
            )

        if (
            _explicit_historical_apply_confirmation(message)
            and ACTION_APPLY_ADVISOR_APPROVED_HISTORY not in context.allowed_actions
        ):
            return (
                jsonify(
                    {
                        "reply": (
                            "You can review this historical draft, but this account "
                            "cannot authorize the durable write."
                        ),
                        "intent": "historical_review",
                        "car_id": context.car_id,
                        "authority": context.authority,
                        "state": RINA_STATE_AUTHORITY_DENIED,
                        "conversation_id": conversation_id,
                        "uncertainty": "durable historical write authority is not available",
                        "escalation": None,
                        "evidence_refs": [],
                    }
                ),
                403,
            )

        history_request_id = _new_conversation_id()
        try:
            history_result = _handle_historical_review_turn(
                context=context,
                message=message,
            )
            conversation_id = _bind_rina_vehicle(
                car_id=context.car_id,
                conversation_id=conversation_id,
            )

            reply = str(history_result.get("reply") or "").strip()
            extraction_id = _coerce_car_id(history_result.get("extraction_id"))
            evidence_refs = (
                [{"type": "historical_reconciliation", "id": extraction_id}]
                if extraction_id is not None
                else []
            )
            applied_plan_id = _coerce_car_id(history_result.get("applied_plan_id"))
            if applied_plan_id is not None:
                evidence_refs.append(
                    {"type": "treatment_plan", "id": applied_plan_id}
                )

            if message:
                save_rina_chat_turn(
                    user_id=current_user.id,
                    car_id=context.car_id,
                    conversation_id=conversation_id,
                    role="user",
                    content=message,
                    channel="in_app",
                    commit=False,
                )
                save_rina_chat_turn(
                    user_id=current_user.id,
                    car_id=context.car_id,
                    conversation_id=conversation_id,
                    role="assistant",
                    content=reply,
                    channel="in_app",
                    commit=False,
                )

            record_rina_audit(
                request_id=history_request_id,
                user_id=current_user.id,
                car_id=context.car_id,
                authority=context.authority,
                state=RINA_STATE_ANSWERED,
                outcome="answered",
                action_family=(
                    "historical_apply"
                    if applied_plan_id is not None
                    else "historical_chat_review"
                ),
                provider_status=str(
                    history_result.get("provider_status") or "not_called"
                ),
                provider=(
                    str(history_result.get("provider"))
                    if history_result.get("provider")
                    else None
                ),
                provider_model=(
                    str(history_result.get("provider_model"))
                    if history_result.get("provider_model")
                    else None
                ),
                provider_request_id=(
                    str(history_result.get("provider_request_id"))
                    if history_result.get("provider_request_id")
                    else None
                ),
                evidence_refs=evidence_refs,
                metadata={
                    "channel": "in_app",
                    "context_version": context.context_version,
                    "provider_attempted": (
                        history_result.get("provider_status") == "ok"
                    ),
                },
                commit=False,
            )
            db.session.commit()
            return (
                jsonify(
                    {
                        "reply": reply,
                        "intent": "historical_review",
                        "car_id": context.car_id,
                        "authority": context.authority,
                        "state": RINA_STATE_ANSWERED,
                        "conversation_id": conversation_id,
                        "uncertainty": None,
                        "escalation": None,
                        "evidence_refs": evidence_refs,
                        "historical_review": {
                            "phase": history_result.get("phase"),
                            "extraction_id": extraction_id,
                            "candidate_id": history_result.get("candidate_id"),
                            "applied_plan_id": applied_plan_id,
                        },
                    }
                ),
                200,
            )
        except RinaProviderError as exc:
            db.session.rollback()
            reply = (
                "I couldn't safely interpret that historical correction. I preserved "
                "the current draft and changed nothing. You do not need to rewrite the "
                "whole history; retry, or give me only the specific fact that needs "
                "changing."
            )
            record_rina_audit(
                request_id=history_request_id,
                user_id=current_user.id,
                car_id=context.car_id,
                authority=context.authority,
                state=RINA_STATE_PROVIDER_UNAVAILABLE,
                outcome="provider_failed",
                action_family="historical_chat_review",
                provider_status=getattr(exc, "provider_status", "unavailable"),
                provider="openai",
                evidence_refs=(),
                metadata={
                    "channel": "in_app",
                    "context_version": context.context_version,
                    "provider_attempted": True,
                    "failure_class": getattr(exc, "failure_class", "provider_error"),
                },
                commit=True,
            )
            return (
                jsonify(
                    {
                        "reply": reply,
                        "intent": "historical_review",
                        "car_id": context.car_id,
                        "authority": context.authority,
                        "state": RINA_STATE_PROVIDER_UNAVAILABLE,
                        "conversation_id": conversation_id,
                        "uncertainty": "the correction was not safely interpreted",
                        "escalation": None,
                        "evidence_refs": [],
                    }
                ),
                503,
            )
        except HistoricalReconciliationError as exc:
            db.session.rollback()
            return (
                jsonify(
                    {
                        "reply": (
                            "I couldn't update that historical draft safely. "
                            + str(exc)
                        ),
                        "intent": "historical_review",
                        "car_id": context.car_id,
                        "authority": context.authority,
                        "state": RINA_STATE_ABSTAINED,
                        "conversation_id": conversation_id,
                        "uncertainty": "historical reconciliation validation failed",
                        "escalation": None,
                        "evidence_refs": [],
                    }
                ),
                400,
            )
        except Exception:
            db.session.rollback()
            current_app.logger.exception(
                "Rina conversational historical review failed user_id=%s car_id=%s",
                current_user.id,
                car_id,
            )
            return (
                jsonify(
                    {
                        "reply": (
                            "I couldn't update that historical draft safely. Nothing "
                            "new was written to durable vehicle history."
                        ),
                        "intent": "historical_review",
                        "car_id": context.car_id,
                        "authority": context.authority,
                        "state": RINA_STATE_PROVIDER_UNAVAILABLE,
                        "conversation_id": conversation_id,
                        "uncertainty": "the historical review transaction did not complete",
                        "escalation": None,
                        "evidence_refs": [],
                    }
                ),
                503,
            )

    try:
        response = orchestrate_rina(
            user_id=current_user.id,
            car_id=car_id,
            message=message,
            channel="in_app",
            conversation_id=conversation_id,
            audit_commit=False,
        )

        if response.state not in {
            RINA_STATE_AUTHORITY_DENIED,
            RINA_STATE_VEHICLE_REQUIRED,
        }:
            conversation_id = _bind_rina_vehicle(
                car_id=response.car_id,
                conversation_id=conversation_id,
            )

            if message:
                save_rina_chat_turn(
                    user_id=current_user.id,
                    car_id=response.car_id,
                    conversation_id=conversation_id,
                    role="user",
                    content=message,
                    channel="in_app",
                    commit=False,
                )

                save_rina_chat_turn(
                    user_id=current_user.id,
                    car_id=response.car_id,
                    conversation_id=conversation_id,
                    role="assistant",
                    content=response.message,
                    channel="in_app",
                    commit=False,
                )

                if intent == "booking":
                    record_rina_material_summary(
                        user_id=current_user.id,
                        car_id=response.car_id,
                        conversation_id=conversation_id,
                        material_type=MATERIAL_BOOKING_REQUEST,
                        commit=False,
                    )

                if response.state == RINA_STATE_ESCALATION_REQUIRED:
                    record_rina_material_summary(
                        user_id=current_user.id,
                        car_id=response.car_id,
                        conversation_id=conversation_id,
                        material_type=MATERIAL_ADVISOR_REVIEW,
                        commit=False,
                    )

        db.session.commit()

    except Exception:
        db.session.rollback()
        current_app.logger.exception(
            "Rina chat transaction failed for user_id=%s car_id=%s",
            current_user.id,
            car_id,
        )
        return (
            jsonify(
                {
                    "reply": (
                        "Rina couldn't complete that request safely. Please try "
                        "again shortly."
                    ),
                    "intent": intent,
                    "car_id": car_id,
                    "authority": None,
                    "state": "provider_unavailable",
                    "conversation_id": None,
                    "uncertainty": "the chat transaction did not complete",
                    "escalation": None,
                    "evidence_refs": [],
                }
            ),
            503,
        )

    status_code = 403 if response.state == RINA_STATE_AUTHORITY_DENIED else 200
    return (
        jsonify(
            {
                "reply": response.message,
                "intent": intent,
                "car_id": response.car_id if response.car_id > 0 else None,
                "authority": response.authority or None,
                "state": response.state,
                "conversation_id": (
                    conversation_id
                    if response.state
                    not in {RINA_STATE_AUTHORITY_DENIED, RINA_STATE_VEHICLE_REQUIRED}
                    else None
                ),
                "uncertainty": response.uncertainty,
                "escalation": response.escalation,
                "evidence_refs": list(response.evidence_refs),
            }
        ),
        status_code,
    )


@chat_bp.get("/chat/history")
@login_required
def chat_history():
    explicit_car_id = _coerce_car_id(request.args.get("car_id"))
    car_id = explicit_car_id or _validated_session_car_id()

    if car_id is None:
        return jsonify({"messages": [], "state": RINA_STATE_VEHICLE_REQUIRED}), 200

    try:
        resolve_rina_authority(user_id=current_user.id, car_id=car_id)
        history = load_rina_chat_history(
            user_id=current_user.id,
            car_id=car_id,
            limit=20,
        )
    except RinaAuthorityError:
        if _coerce_car_id(session.get(_SESSION_CAR_KEY)) == car_id:
            _clear_rina_binding()
        return jsonify({"messages": [], "state": RINA_STATE_AUTHORITY_DENIED}), 403
    except Exception:
        current_app.logger.exception(
            "Rina chat history failed for user_id=%s car_id=%s",
            current_user.id,
            car_id,
        )
        return jsonify({"messages": [], "state": "unavailable"}), 503

    return (
        jsonify(
            {
                "messages": [
                    {
                        "role": item.role,
                        "message": item.content,
                        "timestamp": (
                            item.timestamp.isoformat() if item.timestamp else None
                        ),
                    }
                    for item in history
                ],
                "state": "answered",
                "car_id": car_id,
            }
        ),
        200,
    )


@chat_bp.get("/chat/workspace")
@login_required
def rina_workspace():
    """An explicit, authenticated entry point for account help and vehicle review."""
    if not current_user.is_active:
        abort(403)
    car_id = _coerce_car_id(request.args.get("car_id"))
    car = None
    if car_id is not None:
        try:
            resolve_rina_authority(user_id=current_user.id, car_id=car_id)
        except RinaAuthorityError:
            abort(403)
        car = db.session.get(Car, car_id)

    query = str(request.args.get("q") or "").strip()[:120]
    matches = []
    professional = current_user.role in {"admin", "advisor"}
    if professional and len(query) >= 2:
        # Search is explicit and bounded; dedicated advisors see only linked vehicles.
        pattern = (
            "%"
            + query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            + "%"
        )
        search = Car.query.outerjoin(
            CarOwnership, CarOwnership.car_id == Car.id
        ).outerjoin(User, User.id == CarOwnership.user_id)
        search = search.filter(
            or_(
                Car.brand.ilike(pattern, escape="\\"),
                Car.model.ilike(pattern, escape="\\"),
                Car.vin.ilike(pattern, escape="\\"),
                db.and_(
                    CarOwnership.is_active.is_(True),
                    or_(
                        CarOwnership.plate_number.ilike(pattern, escape="\\"),
                        User.name.ilike(pattern, escape="\\"),
                    ),
                ),
            )
        )
        if current_user.role == "advisor":
            search = search.filter(
                or_(
                    *[
                        Car.id.in_(
                            db.session.query(model.car_id).filter(
                                model.advisor_id == current_user.id
                            )
                        )
                        for model in (
                            Consultation,
                            VehicleAssessment,
                            TreatmentPlan,
                            AdvisorNote,
                        )
                    ]
                )
            )
        for candidate in search.distinct().order_by(Car.id).limit(20).all():
            try:
                resolve_rina_authority(user_id=current_user.id, car_id=candidate.id)
            except RinaAuthorityError:
                continue
            matches.append(candidate)
    return render_template(
        "chat/workspace.html",
        car=car,
        query=query,
        matches=matches,
        professional=professional,
    )


@chat_bp.post("/chat/account")
@login_required
def chat_account():
    """Account-only help never restores a vehicle binding or reads vehicle memory."""
    try:
        identity = speaker_identity(current_user.id)
    except RinaAuthorityError:
        abort(403)
    data = request.get_json(silent=True) or {}
    message = str(data.get("message") or "").strip()[:2000]
    reply = account_help(identity, message)
    record_rina_audit(
        request_id=_new_conversation_id(),
        user_id=current_user.id,
        car_id=None,
        authority=None,
        state="answered",
        outcome="answered",
        action_family="account_help",
        provider_status="not_called",
        metadata={"channel": "in_app", "provider_attempted": False},
    )
    return jsonify(
        reply=reply,
        state="answered",
        car_id=None,
        authority=None,
        intent="general",
        conversation_id=None,
    )
