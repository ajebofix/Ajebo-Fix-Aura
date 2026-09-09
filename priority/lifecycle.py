"""Transactional PriorityRequest lifecycle for Aura Wave 2.4D.

The service owns state legality and canonical event emission but never commits.
Callers coordinate PriorityRequest, Consultation linkage and VehicleEvent in one
outer transaction.
"""

from __future__ import annotations

from datetime import datetime
from uuid import uuid4

from sqlalchemy.exc import IntegrityError

from extensions import db
from models import Car, CarOwnership, Consultation
from priority.event_emission import emit_priority_event
from priority.models import PriorityRequest
from security.access import resolve_vehicle_authority
from services.feature_gateways import (
    FEATURE_EMERGENCY_REVIEW,
    FEATURE_PRIORITY_SCHEDULING,
    has_feature,
)
from services.vehicle_intelligence import calculate_vehicle_health


ACTIVE_PRIORITY_STATUSES = frozenset(
    {"requested", "under_review", "accepted", "deferred"}
)


class PriorityRequestError(ValueError):
    """Base domain error for priority-request lifecycle operations."""


class PriorityRequestAuthorityError(PriorityRequestError):
    """Raised when the actor lacks authority for the requested operation."""


def _utcnow() -> datetime:
    return datetime.utcnow()


def _active_ownership(car_id: int) -> CarOwnership:
    rows = CarOwnership.query.filter_by(car_id=car_id, is_active=True).all()
    if len(rows) != 1:
        raise PriorityRequestError(
            "priority requests require exactly one active vehicle ownership"
        )
    return rows[0]


def _feature_for_kind(request_kind: str) -> str:
    if request_kind == "priority":
        return FEATURE_PRIORITY_SCHEDULING
    if request_kind == "emergency_review":
        return FEATURE_EMERGENCY_REVIEW
    raise PriorityRequestError("unsupported priority request kind")


def _client_reason(reason_summary: str | None, request_kind: str) -> str:
    reason = (reason_summary or "").strip()
    if reason:
        return reason[:500]
    if request_kind == "emergency_review":
        return "Emergency review requested for advisor assessment."
    return "Priority scheduling requested for advisor coordination."


def _advisor_authority(actor_user_id: int, car_id: int) -> None:
    if resolve_vehicle_authority(actor_user_id, car_id) not in {
        "advisor",
        "administrator",
    }:
        raise PriorityRequestAuthorityError(
            "this priority transition requires advisor authority"
        )


class PriorityRequestLifecycleService:
    @staticmethod
    def create_request(
        *,
        car_id: int,
        actor_user_id: int,
        request_kind: str,
        request_source: str,
        reason_summary: str | None = None,
        occurred_at: datetime | None = None,
    ) -> PriorityRequest:
        car = db.session.get(Car, car_id)
        if car is None:
            raise PriorityRequestError("vehicle not found")

        ownership = _active_ownership(car_id)
        authority = resolve_vehicle_authority(actor_user_id, car_id)
        if request_source == "owner":
            if authority != "owner" or ownership.user_id != actor_user_id:
                raise PriorityRequestAuthorityError(
                    "only the active vehicle owner may submit this request"
                )
        elif request_source == "advisor":
            _advisor_authority(actor_user_id, car_id)
        elif request_source == "rina_structured_request":
            # Rina can structure explicit human intent, but the durable actor is
            # still the authenticated owner/advisor who confirmed the request.
            if authority not in {"owner", "advisor", "administrator"}:
                raise PriorityRequestAuthorityError(
                    "structured Rina priority intent requires owner/advisor confirmation"
                )
        else:
            raise PriorityRequestError("unsupported priority request source")

        feature = _feature_for_kind(request_kind)
        eligible = bool(has_feature(ownership, feature))
        if authority == "owner" and not eligible:
            raise PriorityRequestAuthorityError(
                "this vehicle is not currently entitled to that priority capability"
            )

        existing = (
            PriorityRequest.query.filter(
                PriorityRequest.car_id == car_id,
                PriorityRequest.ownership_id == ownership.id,
                PriorityRequest.request_kind == request_kind,
                PriorityRequest.status.in_(tuple(ACTIVE_PRIORITY_STATUSES)),
            )
            .order_by(PriorityRequest.id.desc())
            .first()
        )
        if existing is not None:
            return existing

        health = calculate_vehicle_health(car, ownership)
        now = occurred_at or _utcnow()
        request_row = PriorityRequest(
            car_id=car.id,
            ownership_id=ownership.id,
            requested_by_user_id=actor_user_id,
            request_source=request_source,
            request_kind=request_kind,
            status="requested",
            reason_summary=_client_reason(reason_summary, request_kind),
            eligibility_at_request=eligible,
            care_plan_snapshot=ownership.care_plan or "active_monitoring",
            health_status_snapshot=(
                health.get("label")
                or health.get("health_status")
                or "unknown"
            ),
            request_key=f"priority:{uuid4().hex}",
            requested_at=now,
        )

        try:
            with db.session.begin_nested():
                db.session.add(request_row)
                db.session.flush()
        except IntegrityError:
            # PostgreSQL's partial active-occurrence index is the concurrency
            # backstop. A racing duplicate converges on the existing request.
            existing = (
                PriorityRequest.query.filter(
                    PriorityRequest.car_id == car_id,
                    PriorityRequest.ownership_id == ownership.id,
                    PriorityRequest.request_kind == request_kind,
                    PriorityRequest.status.in_(tuple(ACTIVE_PRIORITY_STATUSES)),
                )
                .order_by(PriorityRequest.id.desc())
                .first()
            )
            if existing is None:
                raise
            return existing

        emit_priority_event(
            car_id=car.id,
            request_id=request_row.id,
            event_type="priority.requested",
            actor_user_id=actor_user_id,
            occurred_at=now,
            previous_state=None,
            new_state="requested",
            request_kind=request_kind,
            request_source=request_source,
            eligibility_at_request=eligible,
            idempotency_key=f"priority:{request_row.id}:requested",
        )
        return request_row

    @staticmethod
    def start_review(
        *, request_id: int, actor_user_id: int, note: str | None = None
    ) -> PriorityRequest:
        row = PriorityRequest.query.get(request_id)
        if row is None:
            raise PriorityRequestError("priority request not found")
        _advisor_authority(actor_user_id, row.car_id)
        if row.status not in {"requested", "deferred"}:
            raise PriorityRequestError("only requested/deferred priority items can enter review")
        previous = row.status
        now = _utcnow()
        row.status = "under_review"
        row.review_started_at = now
        row.reviewed_by_user_id = actor_user_id
        if note is not None:
            row.advisor_review_note = note.strip() or None
        emit_priority_event(
            car_id=row.car_id,
            request_id=row.id,
            event_type="priority.review_started",
            actor_user_id=actor_user_id,
            occurred_at=now,
            previous_state=previous,
            new_state="under_review",
            request_kind=row.request_kind,
            request_source=row.request_source,
            eligibility_at_request=row.eligibility_at_request,
            idempotency_key=f"priority:{row.id}:review:{previous}",
        )
        return row

    @staticmethod
    def accept(*, request_id: int, actor_user_id: int) -> PriorityRequest:
        row = PriorityRequest.query.get(request_id)
        if row is None:
            raise PriorityRequestError("priority request not found")
        _advisor_authority(actor_user_id, row.car_id)
        if row.status != "under_review":
            raise PriorityRequestError("priority request must be under review before acceptance")
        now = _utcnow()
        row.status = "accepted"
        row.accepted_at = now
        row.reviewed_by_user_id = actor_user_id
        emit_priority_event(
            car_id=row.car_id,
            request_id=row.id,
            event_type="priority.accepted",
            actor_user_id=actor_user_id,
            occurred_at=now,
            previous_state="under_review",
            new_state="accepted",
            request_kind=row.request_kind,
            request_source=row.request_source,
            eligibility_at_request=row.eligibility_at_request,
            idempotency_key=f"priority:{row.id}:accepted",
        )
        return row

    @staticmethod
    def defer(
        *, request_id: int, actor_user_id: int, note: str | None = None
    ) -> PriorityRequest:
        row = PriorityRequest.query.get(request_id)
        if row is None:
            raise PriorityRequestError("priority request not found")
        _advisor_authority(actor_user_id, row.car_id)
        if row.status not in {"requested", "under_review"}:
            raise PriorityRequestError("only requested/under-review priority items can be deferred")
        previous = row.status
        now = _utcnow()
        row.status = "deferred"
        row.deferred_at = now
        row.reviewed_by_user_id = actor_user_id
        if note is not None:
            row.advisor_review_note = note.strip() or None
        emit_priority_event(
            car_id=row.car_id,
            request_id=row.id,
            event_type="priority.deferred",
            actor_user_id=actor_user_id,
            occurred_at=now,
            previous_state=previous,
            new_state="deferred",
            request_kind=row.request_kind,
            request_source=row.request_source,
            eligibility_at_request=row.eligibility_at_request,
            idempotency_key=f"priority:{row.id}:deferred:{previous}",
        )
        return row

    @staticmethod
    def resolve(*, request_id: int, actor_user_id: int) -> PriorityRequest:
        row = PriorityRequest.query.get(request_id)
        if row is None:
            raise PriorityRequestError("priority request not found")
        _advisor_authority(actor_user_id, row.car_id)
        if row.status != "accepted":
            raise PriorityRequestError("only accepted priority requests can be resolved")
        now = _utcnow()
        row.status = "resolved"
        row.resolved_at = now
        row.resolved_by_user_id = actor_user_id
        emit_priority_event(
            car_id=row.car_id,
            request_id=row.id,
            event_type="priority.resolved",
            actor_user_id=actor_user_id,
            occurred_at=now,
            previous_state="accepted",
            new_state="resolved",
            request_kind=row.request_kind,
            request_source=row.request_source,
            eligibility_at_request=row.eligibility_at_request,
            idempotency_key=f"priority:{row.id}:resolved",
            consultation_id=row.consultation_id,
        )
        return row

    @staticmethod
    def cancel(*, request_id: int, actor_user_id: int) -> PriorityRequest:
        row = PriorityRequest.query.get(request_id)
        if row is None:
            raise PriorityRequestError("priority request not found")
        authority = resolve_vehicle_authority(actor_user_id, row.car_id)
        is_requesting_owner = (
            authority == "owner"
            and actor_user_id == row.requested_by_user_id
            and row.request_source in {"owner", "rina_structured_request"}
        )
        if authority not in {"advisor", "administrator"} and not is_requesting_owner:
            raise PriorityRequestAuthorityError(
                "priority cancellation requires the requesting owner or an advisor"
            )
        if row.status not in {"requested", "under_review", "deferred"}:
            raise PriorityRequestError("this priority request can no longer be cancelled")
        previous = row.status
        now = _utcnow()
        row.status = "cancelled"
        row.cancelled_at = now
        if authority in {"advisor", "administrator"}:
            row.resolved_by_user_id = actor_user_id
        emit_priority_event(
            car_id=row.car_id,
            request_id=row.id,
            event_type="priority.cancelled",
            actor_user_id=actor_user_id,
            occurred_at=now,
            previous_state=previous,
            new_state="cancelled",
            request_kind=row.request_kind,
            request_source=row.request_source,
            eligibility_at_request=row.eligibility_at_request,
            idempotency_key=f"priority:{row.id}:cancelled:{previous}",
        )
        return row

    @staticmethod
    def link_consultation(
        *, request_id: int, consultation_id: int, actor_user_id: int
    ) -> PriorityRequest:
        row = PriorityRequest.query.get(request_id)
        consultation = db.session.get(Consultation, consultation_id)
        if row is None or consultation is None:
            raise PriorityRequestError("priority request or consultation not found")
        _advisor_authority(actor_user_id, row.car_id)
        if row.status != "accepted":
            raise PriorityRequestError(
                "consultation linkage is available after priority acceptance"
            )
        if consultation.car_id != row.car_id:
            raise PriorityRequestError("consultation belongs to a different vehicle")
        if getattr(consultation, "ownership_id", None) not in {None, row.ownership_id}:
            raise PriorityRequestError("consultation belongs to a different ownership period")
        if row.consultation_id is not None and row.consultation_id != consultation.id:
            raise PriorityRequestError("priority request is already linked to another consultation")
        row.consultation_id = consultation.id
        return row
