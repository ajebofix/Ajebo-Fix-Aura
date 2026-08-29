"""Advisor-governed Treatment Plan lifecycle for Aura Wave 2.3.

This service owns legal TreatmentPlan state transitions. It never commits;
HTTP/coordinator code owns the outer transaction so plan mutation and canonical
VehicleEvent emission succeed or roll back together.
"""

from __future__ import annotations

from datetime import datetime, timezone

from models import TreatmentPlan, VehicleAssessment, db
from security.access import resolve_vehicle_authority
from services.treatment_event_emission import emit_treatment_plan_event


class TreatmentPlanLifecycleError(ValueError):
    """Base error for illegal Treatment Plan lifecycle operations."""


class TreatmentPlanAuthorityError(TreatmentPlanLifecycleError):
    """Raised when an actor lacks required object-level authority."""


class TreatmentPlanStateError(TreatmentPlanLifecycleError):
    """Raised when a requested Treatment Plan transition is illegal."""


class TreatmentPlanScopeError(TreatmentPlanLifecycleError):
    """Raised when linked Treatment Plan objects disagree on vehicle scope."""


class TreatmentPlanLifecycleService:
    """Canonical Treatment Plan state machine.

    New Wave 2.3 plans use ``proposed`` rather than the historical compatibility
    value ``approved``. Existing ``approved`` rows are never rewritten merely to
    make history look canonical; they are accepted only as explicit source
    states for future real transitions.
    """

    PROPOSED = "proposed"
    AUTHORIZED = "authorized"
    SCHEDULED = "scheduled"
    IN_PROGRESS = "in_progress"
    MONITORING = "monitoring"
    COMPLETED = "completed"
    DEFERRED = "deferred"
    CANCELLED = "cancelled"
    LEGACY_APPROVED = "approved"

    @staticmethod
    def _now(occurred_at: datetime | None = None) -> datetime:
        value = occurred_at or datetime.now(timezone.utc)
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value

    @staticmethod
    def _load_for_update(plan_id: int) -> TreatmentPlan:
        plan = (
            TreatmentPlan.query.filter_by(id=plan_id)
            .with_for_update()
            .first()
        )
        if plan is None:
            raise TreatmentPlanLifecycleError("Treatment Plan not found")
        return plan

    @staticmethod
    def _authority(plan: TreatmentPlan, actor_user_id: int) -> str:
        authority = resolve_vehicle_authority(actor_user_id, plan.car_id)
        if authority is None:
            raise TreatmentPlanAuthorityError(
                "actor has no proven authority for this vehicle"
            )
        return authority

    @classmethod
    def _require_advisor(cls, plan: TreatmentPlan, actor_user_id: int) -> str:
        authority = cls._authority(plan, actor_user_id)
        if authority not in {"advisor", "administrator"}:
            raise TreatmentPlanAuthorityError(
                "Treatment Plan mutation requires advisor authority"
            )
        return authority

    @classmethod
    def _require_owner(cls, plan: TreatmentPlan, actor_user_id: int) -> str:
        authority = cls._authority(plan, actor_user_id)
        if authority != "owner":
            raise TreatmentPlanAuthorityError(
                "Treatment Plan authorization requires the active owner"
            )
        return authority

    @staticmethod
    def _require_scope(plan: TreatmentPlan) -> None:
        if plan.assessment_id is not None:
            assessment = db.session.get(VehicleAssessment, plan.assessment_id)
            if assessment is None:
                raise TreatmentPlanScopeError(
                    "Treatment Plan references a missing Vehicle Assessment"
                )
            if assessment.car_id != plan.car_id:
                raise TreatmentPlanScopeError(
                    "Treatment Plan and Vehicle Assessment vehicle scope disagree"
                )
            if plan.consultation_id is not None and assessment.consultation_id != plan.consultation_id:
                raise TreatmentPlanScopeError(
                    "Treatment Plan and Vehicle Assessment consultation scope disagree"
                )

    @staticmethod
    def _key(
        plan: TreatmentPlan,
        *,
        event_type: str,
        previous_state: str | None,
        new_state: str | None,
        occurred_at: datetime,
        operation_key: str | None = None,
    ) -> str:
        if operation_key:
            return f"treatment-plan:{plan.id}:{event_type}:{operation_key}"
        stamp = occurred_at.astimezone(timezone.utc).isoformat(timespec="microseconds")
        return (
            f"treatment-plan:{plan.id}:{event_type}:"
            f"{previous_state or 'none'}:{new_state or 'none'}:{stamp}"
        )

    @classmethod
    def canonicalize_new_assessment_plan(
        cls,
        *,
        assessment_id: int,
        actor_user_id: int,
        occurred_at: datetime | None = None,
        source: str = "assessment.finalize",
    ) -> TreatmentPlan:
        """Convert only a newly-created B2 compatibility plan to ``proposed``.

        The coordinator must call this only when it established that no plan
        existed before assessment finalization. Historical ``approved`` rows
        must never be passed here for retrospective canonicalization.
        """

        assessment = db.session.get(VehicleAssessment, assessment_id)
        if assessment is None or not assessment.is_finalized:
            raise TreatmentPlanStateError(
                "a finalized Vehicle Assessment is required before treatment proposal"
            )

        plan = (
            TreatmentPlan.query.filter_by(assessment_id=assessment_id)
            .with_for_update()
            .first()
        )
        if plan is None:
            raise TreatmentPlanLifecycleError(
                "assessment finalization did not create a Treatment Plan"
            )

        cls._require_advisor(plan, actor_user_id)
        cls._require_scope(plan)

        if plan.status == cls.PROPOSED:
            return plan
        if plan.status != cls.LEGACY_APPROVED:
            raise TreatmentPlanStateError(
                "new assessment Treatment Plan must begin at the compatibility approved state"
            )

        when = cls._now(occurred_at)
        plan.status = cls.PROPOSED
        db.session.flush()

        emit_treatment_plan_event(
            car_id=plan.car_id,
            plan_id=plan.id,
            event_type="treatment.proposed",
            actor_user_id=actor_user_id,
            occurred_at=when,
            source=source,
            title="Treatment plan proposed",
            previous_state=None,
            new_state=cls.PROPOSED,
            idempotency_key=f"treatment-plan:{plan.id}:proposed:assessment:{assessment_id}",
            visibility="client",
            data={
                "assessment_id": assessment_id,
                "consultation_id": plan.consultation_id,
            },
        )
        return plan

    @classmethod
    def authorize(
        cls,
        *,
        plan_id: int,
        actor_user_id: int,
        occurred_at: datetime | None = None,
        source: str = "owner.treatment_authorize",
        operation_key: str | None = None,
    ) -> TreatmentPlan:
        plan = cls._load_for_update(plan_id)
        cls._require_owner(plan, actor_user_id)
        cls._require_scope(plan)

        if plan.status == cls.AUTHORIZED:
            return plan
        if plan.status not in {cls.PROPOSED, cls.DEFERRED}:
            raise TreatmentPlanStateError(
                f"cannot authorize Treatment Plan from {plan.status!r}"
            )

        previous = plan.status
        when = cls._now(occurred_at)
        plan.status = cls.AUTHORIZED
        db.session.flush()
        emit_treatment_plan_event(
            car_id=plan.car_id,
            plan_id=plan.id,
            event_type="treatment.authorized",
            actor_user_id=actor_user_id,
            occurred_at=when,
            source=source,
            title="Treatment plan authorized",
            previous_state=previous,
            new_state=cls.AUTHORIZED,
            idempotency_key=cls._key(
                plan,
                event_type="treatment.authorized",
                previous_state=previous,
                new_state=cls.AUTHORIZED,
                occurred_at=when,
                operation_key=operation_key,
            ),
            visibility="client",
            data={"consent_actor_class": "active_owner"},
        )
        return plan

    @classmethod
    def schedule(
        cls,
        *,
        plan_id: int,
        actor_user_id: int,
        occurred_at: datetime | None = None,
        source: str = "advisor.treatment_schedule",
        operation_key: str | None = None,
        preserved_owner_consent: bool = False,
    ) -> TreatmentPlan:
        plan = cls._load_for_update(plan_id)
        cls._require_advisor(plan, actor_user_id)
        cls._require_scope(plan)

        if plan.status == cls.SCHEDULED:
            return plan
        allowed = {cls.AUTHORIZED, cls.LEGACY_APPROVED}
        if preserved_owner_consent:
            allowed.add(cls.DEFERRED)
        if plan.status not in allowed:
            raise TreatmentPlanStateError(
                f"cannot schedule Treatment Plan from {plan.status!r}"
            )

        previous = plan.status
        when = cls._now(occurred_at)
        plan.status = cls.SCHEDULED
        db.session.flush()
        emit_treatment_plan_event(
            car_id=plan.car_id,
            plan_id=plan.id,
            event_type="treatment.scheduled",
            actor_user_id=actor_user_id,
            occurred_at=when,
            source=source,
            title="Treatment scheduled",
            previous_state=previous,
            new_state=cls.SCHEDULED,
            idempotency_key=cls._key(
                plan,
                event_type="treatment.scheduled",
                previous_state=previous,
                new_state=cls.SCHEDULED,
                occurred_at=when,
                operation_key=operation_key,
            ),
            visibility="client",
            data={"assessment_id": plan.assessment_id},
        )
        return plan

    @classmethod
    def start(
        cls,
        *,
        plan_id: int,
        actor_user_id: int,
        occurred_at: datetime | None = None,
        source: str = "advisor.treatment_start",
        operation_key: str | None = None,
    ) -> TreatmentPlan:
        plan = cls._load_for_update(plan_id)
        cls._require_advisor(plan, actor_user_id)
        cls._require_scope(plan)

        if plan.status == cls.IN_PROGRESS:
            return plan
        if plan.status not in {
            cls.AUTHORIZED,
            cls.SCHEDULED,
            cls.LEGACY_APPROVED,
            cls.MONITORING,
        }:
            raise TreatmentPlanStateError(
                f"cannot start Treatment Plan from {plan.status!r}"
            )

        previous = plan.status
        event_type = "treatment.started"
        if previous == cls.MONITORING:
            # Resuming active work is a real transition but not a second initial
            # treatment.started event under the Wave 2.3B event taxonomy.
            event_type = "treatment.started"

        when = cls._now(occurred_at)
        plan.status = cls.IN_PROGRESS
        db.session.flush()
        emit_treatment_plan_event(
            car_id=plan.car_id,
            plan_id=plan.id,
            event_type=event_type,
            actor_user_id=actor_user_id,
            occurred_at=when,
            source=source,
            title="Treatment started" if previous != cls.MONITORING else "Treatment resumed",
            previous_state=previous,
            new_state=cls.IN_PROGRESS,
            idempotency_key=cls._key(
                plan,
                event_type=event_type,
                previous_state=previous,
                new_state=cls.IN_PROGRESS,
                occurred_at=when,
                operation_key=operation_key,
            ),
            visibility="client",
            data={"assessment_id": plan.assessment_id},
        )
        return plan

    @classmethod
    def start_monitoring(
        cls,
        *,
        plan_id: int,
        actor_user_id: int,
        occurred_at: datetime | None = None,
        source: str = "advisor.treatment_monitor",
        operation_key: str | None = None,
    ) -> TreatmentPlan:
        plan = cls._load_for_update(plan_id)
        cls._require_advisor(plan, actor_user_id)
        cls._require_scope(plan)

        if plan.status == cls.MONITORING:
            return plan
        if plan.status != cls.IN_PROGRESS:
            raise TreatmentPlanStateError(
                f"cannot enter treatment monitoring from {plan.status!r}"
            )

        previous = plan.status
        when = cls._now(occurred_at)
        plan.status = cls.MONITORING
        db.session.flush()
        emit_treatment_plan_event(
            car_id=plan.car_id,
            plan_id=plan.id,
            event_type="treatment.monitoring_started",
            actor_user_id=actor_user_id,
            occurred_at=when,
            source=source,
            title="Treatment monitoring started",
            previous_state=previous,
            new_state=cls.MONITORING,
            idempotency_key=cls._key(
                plan,
                event_type="treatment.monitoring_started",
                previous_state=previous,
                new_state=cls.MONITORING,
                occurred_at=when,
                operation_key=operation_key,
            ),
            visibility="client",
            data={"assessment_id": plan.assessment_id},
        )
        return plan

    @classmethod
    def complete(
        cls,
        *,
        plan_id: int,
        actor_user_id: int,
        occurred_at: datetime | None = None,
        source: str = "advisor.treatment_complete",
        operation_key: str | None = None,
    ) -> TreatmentPlan:
        plan = cls._load_for_update(plan_id)
        cls._require_advisor(plan, actor_user_id)
        cls._require_scope(plan)

        if plan.status == cls.COMPLETED:
            return plan
        if plan.status not in {cls.IN_PROGRESS, cls.MONITORING}:
            raise TreatmentPlanStateError(
                f"cannot complete Treatment Plan from {plan.status!r}"
            )

        previous = plan.status
        when = cls._now(occurred_at)
        plan.status = cls.COMPLETED
        db.session.flush()
        emit_treatment_plan_event(
            car_id=plan.car_id,
            plan_id=plan.id,
            event_type="treatment.completed",
            actor_user_id=actor_user_id,
            occurred_at=when,
            source=source,
            title="Treatment completed",
            previous_state=previous,
            new_state=cls.COMPLETED,
            idempotency_key=cls._key(
                plan,
                event_type="treatment.completed",
                previous_state=previous,
                new_state=cls.COMPLETED,
                occurred_at=when,
                operation_key=operation_key,
            ),
            visibility="client",
            data={"assessment_id": plan.assessment_id},
            description=(
                "Operational treatment work was recorded complete. "
                "No vehicle-health outcome is implied by this event."
            ),
        )
        return plan

    @classmethod
    def defer(
        cls,
        *,
        plan_id: int,
        actor_user_id: int,
        occurred_at: datetime | None = None,
        source: str = "treatment.defer",
        operation_key: str | None = None,
    ) -> TreatmentPlan:
        plan = cls._load_for_update(plan_id)
        authority = cls._authority(plan, actor_user_id)
        if authority not in {"owner", "advisor", "administrator"}:
            raise TreatmentPlanAuthorityError(
                "Treatment Plan defer requires owner or advisor authority"
            )
        cls._require_scope(plan)

        if plan.status == cls.DEFERRED:
            return plan
        if plan.status not in {
            cls.PROPOSED,
            cls.AUTHORIZED,
            cls.SCHEDULED,
            cls.LEGACY_APPROVED,
        }:
            raise TreatmentPlanStateError(
                f"cannot defer Treatment Plan from {plan.status!r}"
            )

        previous = plan.status
        when = cls._now(occurred_at)
        plan.status = cls.DEFERRED
        db.session.flush()
        emit_treatment_plan_event(
            car_id=plan.car_id,
            plan_id=plan.id,
            event_type="treatment.deferred",
            actor_user_id=actor_user_id,
            occurred_at=when,
            source=source,
            title="Treatment deferred",
            previous_state=previous,
            new_state=cls.DEFERRED,
            idempotency_key=cls._key(
                plan,
                event_type="treatment.deferred",
                previous_state=previous,
                new_state=cls.DEFERRED,
                occurred_at=when,
                operation_key=operation_key,
            ),
            visibility="client",
            data={"disposition_source": authority},
        )
        return plan

    @classmethod
    def cancel(
        cls,
        *,
        plan_id: int,
        actor_user_id: int,
        occurred_at: datetime | None = None,
        source: str = "treatment.cancel",
        operation_key: str | None = None,
    ) -> TreatmentPlan:
        plan = cls._load_for_update(plan_id)
        authority = cls._authority(plan, actor_user_id)
        if authority not in {"owner", "advisor", "administrator"}:
            raise TreatmentPlanAuthorityError(
                "Treatment Plan cancellation requires owner or advisor authority"
            )
        cls._require_scope(plan)

        if plan.status == cls.CANCELLED:
            return plan
        if plan.status not in {
            cls.PROPOSED,
            cls.AUTHORIZED,
            cls.SCHEDULED,
            cls.DEFERRED,
            cls.LEGACY_APPROVED,
        }:
            raise TreatmentPlanStateError(
                f"cannot cancel Treatment Plan from {plan.status!r}"
            )

        previous = plan.status
        when = cls._now(occurred_at)
        plan.status = cls.CANCELLED
        db.session.flush()
        emit_treatment_plan_event(
            car_id=plan.car_id,
            plan_id=plan.id,
            event_type="treatment.cancelled",
            actor_user_id=actor_user_id,
            occurred_at=when,
            source=source,
            title="Treatment cancelled",
            previous_state=previous,
            new_state=cls.CANCELLED,
            idempotency_key=cls._key(
                plan,
                event_type="treatment.cancelled",
                previous_state=previous,
                new_state=cls.CANCELLED,
                occurred_at=when,
                operation_key=operation_key,
            ),
            visibility="client",
            data={"disposition_source": authority},
        )
        return plan

    @classmethod
    def escalate(
        cls,
        *,
        plan_id: int,
        actor_user_id: int,
        operation_key: str,
        occurred_at: datetime | None = None,
        source: str = "advisor.treatment_escalate",
        correlation_id: str | None = None,
        causation_id: str | None = None,
        visibility: str = "advisor",
    ) -> TreatmentPlan:
        plan = cls._load_for_update(plan_id)
        cls._require_advisor(plan, actor_user_id)
        cls._require_scope(plan)
        if plan.status not in {
            cls.PROPOSED,
            cls.AUTHORIZED,
            cls.SCHEDULED,
            cls.IN_PROGRESS,
            cls.MONITORING,
            cls.DEFERRED,
            cls.LEGACY_APPROVED,
        }:
            raise TreatmentPlanStateError(
                f"cannot escalate terminal Treatment Plan state {plan.status!r}"
            )

        when = cls._now(occurred_at)
        emit_treatment_plan_event(
            car_id=plan.car_id,
            plan_id=plan.id,
            event_type="treatment.escalated",
            actor_user_id=actor_user_id,
            occurred_at=when,
            source=source,
            title="Treatment escalated for professional review",
            previous_state=plan.status,
            new_state=plan.status,
            idempotency_key=cls._key(
                plan,
                event_type="treatment.escalated",
                previous_state=plan.status,
                new_state=plan.status,
                occurred_at=when,
                operation_key=operation_key,
            ),
            visibility=visibility,
            data={"assessment_id": plan.assessment_id},
            correlation_id=correlation_id,
            causation_id=causation_id,
        )
        return plan
