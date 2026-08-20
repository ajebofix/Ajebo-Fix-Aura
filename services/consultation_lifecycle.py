"""Explicit Consultation lifecycle service for Aura Wave 2.2A.

Domain models remain authoritative for current state. Canonical VehicleEvents
record durable lifecycle history. This service never commits independently:
callers own the transaction so the domain mutation and canonical event succeed
or fail together.
"""

from __future__ import annotations

from datetime import datetime, timezone

from extensions import db
from models import CarOwnership, Consultation, VehicleAssessment
from security.access import resolve_vehicle_authority
from services.event_emission import emit_vehicle_event


CONSULTATION_REQUESTED = "requested"
CONSULTATION_SCHEDULED = "scheduled"
CONSULTATION_IN_PROGRESS = "in_progress"
CONSULTATION_COMPLETED = "completed"
CONSULTATION_DEFERRED = "deferred"
CONSULTATION_CANCELLED = "cancelled"


class ConsultationLifecycleError(ValueError):
    """Raised when a requested consultation transition is not legal."""


def _utcnow_naive() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _normalise_datetime(value: datetime, *, field_name: str) -> datetime:
    if not isinstance(value, datetime):
        raise ConsultationLifecycleError(f"{field_name} must be a datetime")
    if value.tzinfo is not None:
        return value.astimezone(timezone.utc).replace(tzinfo=None)
    return value


def _active_ownership(car_id: int) -> CarOwnership:
    ownerships = (
        CarOwnership.query.filter_by(car_id=car_id, is_active=True)
        .order_by(CarOwnership.start_date.desc(), CarOwnership.id.desc())
        .all()
    )

    if not ownerships:
        raise ConsultationLifecycleError("Vehicle has no active ownership")
    if len(ownerships) > 1:
        raise ConsultationLifecycleError(
            "Vehicle has multiple active ownership records; resolve stewardship first"
        )
    return ownerships[0]


def _require_owner(actor_user_id: int, car_id: int) -> CarOwnership:
    ownership = _active_ownership(car_id)
    authority = resolve_vehicle_authority(actor_user_id, car_id)
    if authority != "owner" or ownership.user_id != actor_user_id:
        raise ConsultationLifecycleError(
            "Only the current vehicle owner may request this consultation"
        )
    return ownership


def _require_advisor(actor_user_id: int, car_id: int) -> None:
    authority = resolve_vehicle_authority(actor_user_id, car_id)
    if authority not in {"advisor", "administrator"}:
        raise ConsultationLifecycleError(
            "This consultation transition requires advisor authority"
        )


def _require_assigned_advisor(consultation: Consultation, actor_user_id: int) -> None:
    _require_advisor(actor_user_id, consultation.car_id)
    if consultation.advisor_id is not None and consultation.advisor_id != actor_user_id:
        raise ConsultationLifecycleError(
            "This consultation is assigned to another advisor"
        )


def _event_key(consultation: Consultation, event_name: str, token: str) -> str:
    return f"consultation:{consultation.id}:{event_name}:{token}"


def _iso_token(value: datetime) -> str:
    return value.isoformat(timespec="microseconds")


class ConsultationLifecycleService:
    """Own legal consultation state transitions and canonical event emission."""

    @staticmethod
    def request(
        *,
        car_id: int,
        actor_user_id: int,
        preferred_for: datetime,
        notes: str | None = None,
        occurred_at: datetime | None = None,
        source: str = "consultation.request",
    ) -> Consultation:
        """Create an owner consultation request in ``requested`` state.

        ``Consultation.scheduled_for`` is currently non-null in production. Until
        a later schema refinement separates preferred/requested time from an
        advisor-confirmed schedule, Wave 2.2A stores the owner's preferred time
        there while the explicit ``requested`` state prevents it being treated as
        an accepted appointment.
        """

        ownership = _require_owner(actor_user_id, car_id)
        preferred_for = _normalise_datetime(
            preferred_for,
            field_name="preferred_for",
        )
        event_time = _normalise_datetime(
            occurred_at or _utcnow_naive(),
            field_name="occurred_at",
        )

        consultation = Consultation(
            car_id=car_id,
            ownership_id=ownership.id,
            advisor_id=None,
            client_id=actor_user_id,
            status=CONSULTATION_REQUESTED,
            scheduled_for=preferred_for,
            notes=(notes or "").strip() or None,
            created_at=event_time,
        )
        db.session.add(consultation)
        db.session.flush()

        emit_vehicle_event(
            car_id=car_id,
            event_type="consultation.requested",
            subject_type="consultation",
            subject_id=consultation.id,
            actor_type="user",
            actor_user_id=actor_user_id,
            visibility="client",
            source=source[:50],
            occurred_at=event_time,
            title="Consultation requested",
            description="A private consultation request was recorded for this vehicle.",
            progression_direction="not_applicable",
            idempotency_key=_event_key(
                consultation,
                "requested",
                _iso_token(event_time),
            ),
            previous_state=None,
            new_state=CONSULTATION_REQUESTED,
            evidence_refs=[{"type": "consultation", "id": consultation.id}],
            data={"preferred_for": preferred_for.isoformat()},
            mileage=None,
        )

        return consultation

    @staticmethod
    def create_scheduled(
        *,
        car_id: int,
        actor_user_id: int,
        scheduled_for: datetime,
        notes: str | None = None,
        occurred_at: datetime | None = None,
        source: str = "consultation.advisor_schedule",
    ) -> Consultation:
        """Create a consultation directly in ``scheduled`` state as advisor."""

        ownership = _active_ownership(car_id)
        _require_advisor(actor_user_id, car_id)
        scheduled_for = _normalise_datetime(
            scheduled_for,
            field_name="scheduled_for",
        )
        event_time = _normalise_datetime(
            occurred_at or _utcnow_naive(),
            field_name="occurred_at",
        )

        consultation = Consultation(
            car_id=car_id,
            ownership_id=ownership.id,
            advisor_id=actor_user_id,
            client_id=ownership.user_id,
            status=CONSULTATION_SCHEDULED,
            scheduled_for=scheduled_for,
            notes=(notes or "").strip() or None,
            created_at=event_time,
        )
        db.session.add(consultation)
        db.session.flush()

        emit_vehicle_event(
            car_id=car_id,
            event_type="consultation.scheduled",
            subject_type="consultation",
            subject_id=consultation.id,
            actor_type="user",
            actor_user_id=actor_user_id,
            visibility="client",
            source=source[:50],
            occurred_at=event_time,
            title="Consultation scheduled",
            description="A private consultation was scheduled for this vehicle.",
            progression_direction="not_applicable",
            idempotency_key=_event_key(
                consultation,
                "scheduled",
                _iso_token(scheduled_for),
            ),
            previous_state=None,
            new_state=CONSULTATION_SCHEDULED,
            evidence_refs=[{"type": "consultation", "id": consultation.id}],
            data={"scheduled_for": scheduled_for.isoformat()},
            mileage=None,
        )

        return consultation

    @staticmethod
    def schedule(
        *,
        consultation_id: int,
        actor_user_id: int,
        scheduled_for: datetime,
        occurred_at: datetime | None = None,
        source: str = "consultation.schedule",
    ) -> Consultation:
        """Accept a requested/deferred consultation and schedule it."""

        consultation = db.session.get(Consultation, consultation_id)
        if consultation is None:
            raise ConsultationLifecycleError("Consultation does not exist")

        _require_advisor(actor_user_id, consultation.car_id)
        if consultation.status not in {CONSULTATION_REQUESTED, CONSULTATION_DEFERRED}:
            raise ConsultationLifecycleError(
                f"Cannot schedule consultation from state {consultation.status!r}"
            )

        scheduled_for = _normalise_datetime(
            scheduled_for,
            field_name="scheduled_for",
        )
        event_time = _normalise_datetime(
            occurred_at or _utcnow_naive(),
            field_name="occurred_at",
        )
        previous_state = consultation.status

        consultation.status = CONSULTATION_SCHEDULED
        consultation.scheduled_for = scheduled_for
        consultation.advisor_id = actor_user_id

        emit_vehicle_event(
            car_id=consultation.car_id,
            event_type="consultation.scheduled",
            subject_type="consultation",
            subject_id=consultation.id,
            actor_type="user",
            actor_user_id=actor_user_id,
            visibility="client",
            source=source[:50],
            occurred_at=event_time,
            title="Consultation scheduled",
            description="The consultation request was accepted and scheduled.",
            progression_direction="not_applicable",
            idempotency_key=_event_key(
                consultation,
                "scheduled",
                f"{previous_state}:{_iso_token(scheduled_for)}",
            ),
            previous_state=previous_state,
            new_state=CONSULTATION_SCHEDULED,
            evidence_refs=[{"type": "consultation", "id": consultation.id}],
            data={"scheduled_for": scheduled_for.isoformat()},
            mileage=None,
        )

        return consultation

    @staticmethod
    def start(
        *,
        consultation_id: int,
        actor_user_id: int,
        started_at: datetime | None = None,
        source: str = "consultation.start",
    ) -> Consultation:
        """Transition a scheduled consultation to ``in_progress``."""

        consultation = db.session.get(Consultation, consultation_id)
        if consultation is None:
            raise ConsultationLifecycleError("Consultation does not exist")

        _require_assigned_advisor(consultation, actor_user_id)
        if consultation.status != CONSULTATION_SCHEDULED:
            raise ConsultationLifecycleError(
                f"Cannot start consultation from state {consultation.status!r}"
            )

        event_time = _normalise_datetime(
            started_at or _utcnow_naive(),
            field_name="started_at",
        )

        consultation.status = CONSULTATION_IN_PROGRESS
        consultation.started_at = event_time
        if consultation.advisor_id is None:
            consultation.advisor_id = actor_user_id

        emit_vehicle_event(
            car_id=consultation.car_id,
            event_type="consultation.started",
            subject_type="consultation",
            subject_id=consultation.id,
            actor_type="user",
            actor_user_id=actor_user_id,
            visibility="client",
            source=source[:50],
            occurred_at=event_time,
            title="Consultation started",
            description="Professional review for this consultation has started.",
            progression_direction="not_applicable",
            idempotency_key=_event_key(
                consultation,
                "started",
                _iso_token(event_time),
            ),
            previous_state=CONSULTATION_SCHEDULED,
            new_state=CONSULTATION_IN_PROGRESS,
            evidence_refs=[{"type": "consultation", "id": consultation.id}],
            data={},
            mileage=None,
        )

        return consultation

    @staticmethod
    def complete(
        *,
        consultation_id: int,
        actor_user_id: int,
        summary: str | None = None,
        client_visible_summary: str | None = None,
        completed_at: datetime | None = None,
        source: str = "consultation.complete",
    ) -> Consultation:
        """Complete an active consultation after its assessment is finalized."""

        consultation = db.session.get(Consultation, consultation_id)
        if consultation is None:
            raise ConsultationLifecycleError("Consultation does not exist")

        _require_assigned_advisor(consultation, actor_user_id)
        if consultation.status != CONSULTATION_IN_PROGRESS:
            raise ConsultationLifecycleError(
                f"Cannot complete consultation from state {consultation.status!r}"
            )

        assessment = VehicleAssessment.query.filter_by(
            consultation_id=consultation.id
        ).first()
        if assessment is None:
            raise ConsultationLifecycleError(
                "Cannot complete consultation without an assessment"
            )
        if not (
            assessment.is_finalized
            or (assessment.status and assessment.status == "finalized")
        ):
            raise ConsultationLifecycleError(
                "Cannot complete consultation until the assessment is finalized"
            )

        event_time = _normalise_datetime(
            completed_at or _utcnow_naive(),
            field_name="completed_at",
        )

        consultation.status = CONSULTATION_COMPLETED
        consultation.completed_at = event_time

        clean_summary = (summary or "").strip()
        if clean_summary:
            consultation.summary = clean_summary

        clean_client_summary = (client_visible_summary or "").strip()
        if clean_client_summary:
            consultation.client_visible_summary = clean_client_summary

        emit_vehicle_event(
            car_id=consultation.car_id,
            event_type="consultation.completed",
            subject_type="consultation",
            subject_id=consultation.id,
            actor_type="user",
            actor_user_id=actor_user_id,
            visibility="client",
            source=source[:50],
            occurred_at=event_time,
            title="Consultation completed",
            description="The professional consultation was completed and recorded.",
            progression_direction="not_applicable",
            idempotency_key=_event_key(
                consultation,
                "completed",
                _iso_token(event_time),
            ),
            previous_state=CONSULTATION_IN_PROGRESS,
            new_state=CONSULTATION_COMPLETED,
            evidence_refs=[
                {"type": "consultation", "id": consultation.id},
                {"type": "vehicle_assessment", "id": assessment.id},
            ],
            data={"assessment_id": assessment.id},
            mileage=None,
        )

        return consultation
