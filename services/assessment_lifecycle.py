"""Advisor-governed Vehicle Assessment lifecycle for Aura Wave 2.2B.

VehicleAssessment remains authoritative for current professional-record state.
Canonical VehicleEvents record only durable lifecycle milestones. This service
never commits independently: routes/coordinators own the outer transaction so
assessment mutation, canonical event emission, and compatibility side effects
succeed or fail together.

Wave 2.2B3 adds immutable correction/addendum records. A correction never
reopens or rewrites the finalized VehicleAssessment; it appends a separately
attributed professional record and emits one minimal canonical event.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from typing import Any

from extensions import db
from models import (
    Consultation,
    TreatmentPlan,
    VehicleAssessment,
    VehicleAssessmentRisk,
    VehicleAssessmentTreatmentOption,
    VehicleEvent,
)
from models_assessment_addendum import (
    ADDENDUM_CATEGORIES,
    ADDENDUM_VISIBILITIES,
    VehicleAssessmentAddendum,
)
from security.access import resolve_vehicle_authority
from services.event_emission import emit_vehicle_event


ASSESSMENT_DRAFT = "draft"
ASSESSMENT_FINALIZED = "finalized"
CONSULTATION_IN_PROGRESS = "in_progress"

_ALLOWED_SCALAR_FIELDS = frozenset(
    {
        "engine_status",
        "transmission_status",
        "suspension_status",
        "electrical_status",
        "cooling_status",
        "cost_consequence_analysis",
        "professional_recommendation",
    }
)
_REQUIRED_FINALIZATION_FIELDS = (
    "engine_status",
    "transmission_status",
    "suspension_status",
    "electrical_status",
    "cooling_status",
)
_ALLOWED_RISK_URGENCIES = frozenset({"immediate", "monitoring", "preventive"})
_ALLOWED_TREATMENT_CODES = frozenset({"A", "B", "C"})
_ALLOWED_ADDENDUM_CATEGORIES = frozenset(ADDENDUM_CATEGORIES)
_ALLOWED_ADDENDUM_VISIBILITIES = frozenset(ADDENDUM_VISIBILITIES)


class AssessmentLifecycleError(ValueError):
    """Raised when an assessment lifecycle action is invalid or unauthorized."""


def _utcnow_naive() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _normalise_datetime(value: datetime, *, field_name: str) -> datetime:
    if not isinstance(value, datetime):
        raise AssessmentLifecycleError(f"{field_name} must be a datetime")
    if value.tzinfo is not None:
        return value.astimezone(timezone.utc).replace(tzinfo=None)
    return value


def _iso_token(value: datetime) -> str:
    return value.isoformat(timespec="microseconds")


def _require_advisor(actor_user_id: int, car_id: int) -> None:
    authority = resolve_vehicle_authority(actor_user_id, car_id)
    if authority not in {"advisor", "administrator"}:
        raise AssessmentLifecycleError(
            "Vehicle Assessment lifecycle actions require advisor authority"
        )


def _load_consultation(consultation_id: int) -> Consultation:
    consultation = db.session.get(Consultation, consultation_id)
    if consultation is None:
        raise AssessmentLifecycleError("Consultation does not exist")
    return consultation


def _load_assessment(assessment_id: int) -> VehicleAssessment:
    assessment = db.session.get(VehicleAssessment, assessment_id)
    if assessment is None:
        raise AssessmentLifecycleError("Vehicle Assessment does not exist")
    return assessment


def _require_scope_consistency(
    assessment: VehicleAssessment,
    consultation: Consultation,
) -> None:
    if assessment.consultation_id != consultation.id:
        raise AssessmentLifecycleError(
            "Assessment and Consultation identifiers are inconsistent"
        )
    if assessment.car_id != consultation.car_id:
        raise AssessmentLifecycleError(
            "Assessment belongs to a different vehicle than its Consultation"
        )


def _require_active_consultation(consultation: Consultation) -> None:
    if consultation.status != CONSULTATION_IN_PROGRESS:
        raise AssessmentLifecycleError(
            "Vehicle Assessment work requires an active Consultation"
        )


def _clean_text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def _prepare_risks(
    rows: Sequence[Mapping[str, Any]],
    *,
    assessment_id: int,
) -> list[VehicleAssessmentRisk]:
    prepared: list[VehicleAssessmentRisk] = []

    for row in rows:
        description = _clean_text(row.get("description"))
        likely_cause = _clean_text(row.get("likely_cause"))
        consequence = _clean_text(row.get("consequence_if_ignored"))
        urgency = _clean_text(row.get("urgency")) or "monitoring"

        if not description:
            raise AssessmentLifecycleError("Each assessment risk requires a description")
        if urgency not in _ALLOWED_RISK_URGENCIES:
            raise AssessmentLifecycleError(
                f"Invalid assessment risk urgency: {urgency!r}"
            )

        prepared.append(
            VehicleAssessmentRisk(
                assessment_id=assessment_id,
                description=description,
                likely_cause=likely_cause,
                consequence_if_ignored=consequence,
                urgency=urgency,
            )
        )

    return prepared


def _prepare_treatment_options(
    rows: Sequence[Mapping[str, Any]],
    *,
    assessment_id: int,
) -> list[VehicleAssessmentTreatmentOption]:
    prepared: list[VehicleAssessmentTreatmentOption] = []

    for row in rows:
        option_code = _clean_text(row.get("option_code")).upper()
        title = _clean_text(row.get("title"))
        description = _clean_text(row.get("description"))

        if option_code not in _ALLOWED_TREATMENT_CODES:
            raise AssessmentLifecycleError(
                "Each treatment option requires an A/B/C option code"
            )
        if not title or not description:
            raise AssessmentLifecycleError(
                "Each treatment option requires a title and description"
            )

        prepared.append(
            VehicleAssessmentTreatmentOption(
                assessment_id=assessment_id,
                option_code=option_code,
                title=title,
                description=description,
            )
        )

    return prepared


def _ensure_legacy_treatment_plan(
    assessment: VehicleAssessment,
    *,
    actor_user_id: int,
) -> TreatmentPlan:
    existing = TreatmentPlan.query.filter_by(assessment_id=assessment.id).first()
    if existing is not None:
        return existing

    plan = TreatmentPlan(
        car_id=assessment.car_id,
        consultation_id=assessment.consultation_id,
        assessment_id=assessment.id,
        advisor_id=actor_user_id,
        title="Vehicle Treatment Plan",
        internal_instructions=assessment.professional_recommendation,
        client_summary="A professional treatment pathway has been created for this vehicle.",
        status="approved",
    )
    db.session.add(plan)
    db.session.flush()
    return plan


def _finalization_event_for(assessment: VehicleAssessment) -> VehicleEvent | None:
    return (
        VehicleEvent.query.filter_by(
            subject_type="vehicle_assessment",
            subject_id=assessment.id,
            event_type="assessment.finalized",
        )
        .order_by(VehicleEvent.occurred_at.desc(), VehicleEvent.id.desc())
        .first()
    )


class AssessmentLifecycleService:
    """Own Assessment creation/resume, draft persistence, finalization and addenda."""

    @staticmethod
    def start_or_resume(
        *,
        consultation_id: int,
        actor_user_id: int,
        occurred_at: datetime | None = None,
        source: str = "assessment.start",
    ) -> VehicleAssessment:
        consultation = _load_consultation(consultation_id)
        _require_advisor(actor_user_id, consultation.car_id)
        _require_active_consultation(consultation)

        existing = VehicleAssessment.query.filter_by(consultation_id=consultation.id).first()
        if existing is not None:
            _require_scope_consistency(existing, consultation)
            if existing.status == ASSESSMENT_DRAFT and not existing.is_finalized:
                return existing
            raise AssessmentLifecycleError(
                "This Consultation already has a finalized Vehicle Assessment"
            )

        car = consultation.car
        if car is None:
            raise AssessmentLifecycleError("Consultation vehicle does not exist")
        if car.current_mileage is None:
            raise AssessmentLifecycleError(
                "Current vehicle mileage is required before starting an assessment"
            )

        event_time = _normalise_datetime(
            occurred_at or _utcnow_naive(), field_name="occurred_at"
        )
        assessment = VehicleAssessment(
            consultation_id=consultation.id,
            car_id=consultation.car_id,
            advisor_id=actor_user_id,
            vin=car.vin,
            mileage_at_assessment=car.current_mileage,
            engine_number=car.engine_number,
            engine_type=car.engine_type,
            transmission=car.transmission_type,
            status=ASSESSMENT_DRAFT,
            is_finalized=False,
            created_at=event_time,
        )
        db.session.add(assessment)
        db.session.flush()

        emit_vehicle_event(
            car_id=assessment.car_id,
            event_type="assessment.created",
            subject_type="vehicle_assessment",
            subject_id=assessment.id,
            actor_type="user",
            actor_user_id=actor_user_id,
            visibility="advisor",
            source=source[:50],
            occurred_at=event_time,
            title="Vehicle Assessment created",
            description="A professional Vehicle Assessment working record was created.",
            progression_direction="not_applicable",
            idempotency_key=f"assessment:{assessment.id}:created:{_iso_token(event_time)}",
            previous_state=None,
            new_state=ASSESSMENT_DRAFT,
            evidence_refs=[
                {"type": "vehicle_assessment", "id": assessment.id},
                {"type": "consultation", "id": consultation.id},
            ],
            data={"consultation_id": consultation.id},
            mileage=assessment.mileage_at_assessment,
        )
        return assessment

    @staticmethod
    def save_draft(
        *,
        assessment_id: int,
        actor_user_id: int,
        scalar_updates: Mapping[str, Any] | None = None,
        risks: Sequence[Mapping[str, Any]] | None = None,
        treatment_options: Sequence[Mapping[str, Any]] | None = None,
    ) -> VehicleAssessment:
        assessment = _load_assessment(assessment_id)
        consultation = _load_consultation(assessment.consultation_id)
        _require_advisor(actor_user_id, assessment.car_id)
        _require_scope_consistency(assessment, consultation)
        _require_active_consultation(consultation)

        if assessment.status != ASSESSMENT_DRAFT or assessment.is_finalized:
            raise AssessmentLifecycleError(
                "Finalized Vehicle Assessments cannot be edited through ordinary workflow"
            )

        scalar_updates = scalar_updates or {}
        unknown_fields = set(scalar_updates) - _ALLOWED_SCALAR_FIELDS
        if unknown_fields:
            raise AssessmentLifecycleError(
                "Draft update attempted to mutate unsupported or frozen fields: "
                + ", ".join(sorted(unknown_fields))
            )

        prepared_risks = (
            _prepare_risks(risks, assessment_id=assessment.id)
            if risks is not None else None
        )
        prepared_treatments = (
            _prepare_treatment_options(treatment_options, assessment_id=assessment.id)
            if treatment_options is not None else None
        )

        if prepared_risks is not None:
            VehicleAssessmentRisk.query.filter_by(
                assessment_id=assessment.id
            ).delete(synchronize_session=False)
            db.session.add_all(prepared_risks)

        if prepared_treatments is not None:
            VehicleAssessmentTreatmentOption.query.filter_by(
                assessment_id=assessment.id
            ).delete(synchronize_session=False)
            db.session.add_all(prepared_treatments)

        for field_name, value in scalar_updates.items():
            setattr(assessment, field_name, value)

        db.session.flush()
        return assessment

    @staticmethod
    def finalize(
        *,
        assessment_id: int,
        actor_user_id: int,
        finalized_at: datetime | None = None,
        source: str = "assessment.finalize",
    ) -> VehicleAssessment:
        assessment = _load_assessment(assessment_id)
        consultation = _load_consultation(assessment.consultation_id)
        _require_advisor(actor_user_id, assessment.car_id)
        _require_scope_consistency(assessment, consultation)
        _require_active_consultation(consultation)

        if assessment.status != ASSESSMENT_DRAFT or assessment.is_finalized:
            raise AssessmentLifecycleError(
                "Vehicle Assessment is already finalized and cannot be finalized again"
            )

        missing = [
            field_name for field_name in _REQUIRED_FINALIZATION_FIELDS
            if not _clean_text(getattr(assessment, field_name, None))
        ]
        if missing:
            raise AssessmentLifecycleError(
                "All five system statuses are required before finalizing"
            )

        event_time = _normalise_datetime(
            finalized_at or _utcnow_naive(), field_name="finalized_at"
        )
        assessment.status = ASSESSMENT_FINALIZED
        assessment.is_finalized = True
        assessment.finalized_at = event_time
        assessment.finalized_by = actor_user_id

        emit_vehicle_event(
            car_id=assessment.car_id,
            event_type="assessment.finalized",
            subject_type="vehicle_assessment",
            subject_id=assessment.id,
            actor_type="user",
            actor_user_id=actor_user_id,
            visibility="client",
            source=source[:50],
            occurred_at=event_time,
            title="Vehicle Assessment finalized",
            description=(
                "The professional Vehicle Health Assessment was finalized and added "
                "to the vehicle care record."
            ),
            progression_direction="not_applicable",
            idempotency_key=f"assessment:{assessment.id}:finalized:{_iso_token(event_time)}",
            previous_state=ASSESSMENT_DRAFT,
            new_state=ASSESSMENT_FINALIZED,
            evidence_refs=[
                {"type": "vehicle_assessment", "id": assessment.id},
                {"type": "consultation", "id": consultation.id},
            ],
            data={"consultation_id": consultation.id},
            mileage=assessment.mileage_at_assessment,
        )
        _ensure_legacy_treatment_plan(assessment, actor_user_id=actor_user_id)
        return assessment

    @staticmethod
    def add_correction(
        *,
        assessment_id: int,
        actor_user_id: int,
        category: str,
        reason: str,
        visibility: str,
        client_text: str | None = None,
        internal_text: str | None = None,
        idempotency_key: str,
        occurred_at: datetime | None = None,
        source: str = "assessment.correction",
    ) -> VehicleAssessmentAddendum:
        assessment = _load_assessment(assessment_id)
        _require_advisor(actor_user_id, assessment.car_id)
        if assessment.status != ASSESSMENT_FINALIZED or not assessment.is_finalized:
            raise AssessmentLifecycleError(
                "Corrections can only be added to a finalized Vehicle Assessment"
            )

        clean_category = _clean_text(category).lower()
        clean_reason = _clean_text(reason)
        clean_visibility = _clean_text(visibility).lower()
        clean_client_text = _clean_text(client_text) or None
        clean_internal_text = _clean_text(internal_text) or None
        clean_key = _clean_text(idempotency_key)

        if clean_category not in _ALLOWED_ADDENDUM_CATEGORIES:
            raise AssessmentLifecycleError("Invalid assessment correction category")
        if not clean_reason:
            raise AssessmentLifecycleError("Assessment correction reason is required")
        if len(clean_reason) > 240:
            raise AssessmentLifecycleError(
                "Assessment correction reason must be 240 characters or fewer"
            )
        if clean_visibility not in _ALLOWED_ADDENDUM_VISIBILITIES:
            raise AssessmentLifecycleError("Invalid assessment correction visibility")
        if not clean_client_text and not clean_internal_text:
            raise AssessmentLifecycleError(
                "Assessment correction requires client-visible or internal text"
            )
        if clean_visibility == "client" and not clean_client_text:
            raise AssessmentLifecycleError(
                "Client-visible assessment corrections require client-visible text"
            )
        if not clean_key or len(clean_key) > 128:
            raise AssessmentLifecycleError(
                "Assessment correction idempotency key is required and must be 128 characters or fewer"
            )

        existing = VehicleAssessmentAddendum.query.filter_by(
            idempotency_key=clean_key
        ).first()
        if existing is not None:
            if not all((
                existing.assessment_id == assessment.id,
                existing.created_by == actor_user_id,
                existing.category == clean_category,
                existing.reason == clean_reason,
                existing.visibility == clean_visibility,
                existing.client_text == clean_client_text,
                existing.internal_text == clean_internal_text,
            )):
                raise AssessmentLifecycleError(
                    "Assessment correction idempotency key was reused with different content"
                )
            return existing

        event_time = _normalise_datetime(
            occurred_at or _utcnow_naive(), field_name="occurred_at"
        )
        addendum = VehicleAssessmentAddendum(
            assessment_id=assessment.id,
            created_by=actor_user_id,
            category=clean_category,
            reason=clean_reason,
            visibility=clean_visibility,
            client_text=clean_client_text,
            internal_text=clean_internal_text,
            idempotency_key=clean_key,
            created_at=event_time,
        )
        db.session.add(addendum)
        db.session.flush()

        finalized_event = _finalization_event_for(assessment)
        emit_vehicle_event(
            car_id=assessment.car_id,
            event_type="assessment.corrected",
            subject_type="vehicle_assessment",
            subject_id=assessment.id,
            actor_type="user",
            actor_user_id=actor_user_id,
            visibility=clean_visibility,
            source=source[:50],
            occurred_at=event_time,
            title="Vehicle Assessment addendum recorded",
            description=(
                "An attributed professional addendum was added to the finalized "
                "Vehicle Assessment. The original assessment remains unchanged."
            ),
            progression_direction="not_applicable",
            idempotency_key=f"assessment:{assessment.id}:corrected:addendum:{addendum.id}",
            previous_state=ASSESSMENT_FINALIZED,
            new_state=ASSESSMENT_FINALIZED,
            correction_of_event_id=finalized_event.id if finalized_event else None,
            evidence_refs=[
                {"type": "vehicle_assessment", "id": assessment.id},
                {"type": "assessment_addendum", "id": addendum.id},
            ],
            data={
                "addendum_id": addendum.id,
                "category": clean_category,
                "visibility": clean_visibility,
            },
            mileage=assessment.mileage_at_assessment,
        )
        return addendum
