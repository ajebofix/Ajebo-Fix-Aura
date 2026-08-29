"""Advisor-governed Treatment Action lifecycle for Aura Wave 2.3C.

A Treatment Action is one concrete professional intervention within one
Treatment Plan. This service is the sole state-transition authority for the new
entity. It never commits; callers own the outer transaction so action mutation
and canonical VehicleEvent emission succeed or roll back together.
"""

from __future__ import annotations

from datetime import datetime, timezone

from models import TreatmentPlan
from security.access import resolve_vehicle_authority
from services.treatment_event_emission import emit_treatment_action_event
from treatment.models import TreatmentAction
from extensions import db


class TreatmentActionLifecycleError(ValueError):
    """Base error for illegal Treatment Action operations."""


class TreatmentActionAuthorityError(TreatmentActionLifecycleError):
    """Raised when an actor lacks professional object-level authority."""


class TreatmentActionStateError(TreatmentActionLifecycleError):
    """Raised when a requested action transition is illegal."""


class TreatmentActionScopeError(TreatmentActionLifecycleError):
    """Raised when Treatment Action/Plan/vehicle scope disagrees."""


class TreatmentActionIdempotencyConflict(TreatmentActionLifecycleError):
    """Raised when a creation key is replayed with different semantics."""


class TreatmentActionLifecycleService:
    """Canonical Treatment Action state machine."""

    PLANNED = "planned"
    SCHEDULED = "scheduled"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    DEFERRED = "deferred"
    CANCELLED = "cancelled"

    _NONTERMINAL_PLAN_STATES = frozenset(
        {
            "proposed",
            "authorized",
            "scheduled",
            "in_progress",
            "monitoring",
            "deferred",
            "approved",  # legacy Treatment Plan compatibility state
        }
    )

    @staticmethod
    def _now(occurred_at: datetime | None = None) -> datetime:
        value = occurred_at or datetime.now(timezone.utc)
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value

    @staticmethod
    def _normalise_datetime(value: datetime) -> datetime:
        if not isinstance(value, datetime):
            raise TreatmentActionStateError("scheduled_for must be a datetime")
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    @staticmethod
    def _normalise_key(value: str) -> str:
        key = (value or "").strip()
        if not key or len(key) > 128:
            raise TreatmentActionIdempotencyConflict(
                "Treatment Action creation_key is required and must be 128 characters or fewer"
            )
        return key

    @staticmethod
    def _normalise_title(value: str) -> str:
        title = (value or "").strip()
        if not title or len(title) > 255:
            raise TreatmentActionLifecycleError(
                "Treatment Action title is required and must be 255 characters or fewer"
            )
        return title

    @staticmethod
    def _normalise_optional(value: str | None) -> str | None:
        text = (value or "").strip()
        return text or None

    @staticmethod
    def _normalise_visibility(value: str) -> str:
        visibility = (value or "client").strip().lower()
        if visibility not in {"client", "advisor"}:
            raise TreatmentActionLifecycleError(
                "Treatment Action visibility must be client or advisor"
            )
        return visibility

    @staticmethod
    def _load_plan_for_update(plan_id: int) -> TreatmentPlan:
        plan = (
            TreatmentPlan.query.filter_by(id=plan_id)
            .with_for_update()
            .first()
        )
        if plan is None:
            raise TreatmentActionLifecycleError("Treatment Plan not found")
        return plan

    @staticmethod
    def _load_action_for_update(action_id: int) -> TreatmentAction:
        action = (
            TreatmentAction.query.filter_by(id=action_id)
            .with_for_update()
            .first()
        )
        if action is None:
            raise TreatmentActionLifecycleError("Treatment Action not found")
        return action

    @staticmethod
    def _require_advisor(*, car_id: int, actor_user_id: int) -> str:
        authority = resolve_vehicle_authority(actor_user_id, car_id)
        if authority not in {"advisor", "administrator"}:
            raise TreatmentActionAuthorityError(
                "Treatment Action mutation requires advisor authority"
            )
        return authority

    @classmethod
    def _require_nonterminal_plan(cls, plan: TreatmentPlan) -> None:
        if plan.status not in cls._NONTERMINAL_PLAN_STATES:
            raise TreatmentActionStateError(
                f"Treatment Action mutation is not allowed while parent plan is {plan.status!r}"
            )

    @classmethod
    def _action_scope(cls, action: TreatmentAction) -> TreatmentPlan:
        plan = db.session.get(TreatmentPlan, action.treatment_plan_id)
        if plan is None:
            raise TreatmentActionScopeError(
                "Treatment Action references a missing Treatment Plan"
            )
        if plan.car_id != action.car_id:
            raise TreatmentActionScopeError(
                "Treatment Action and Treatment Plan vehicle scope disagree"
            )
        return plan

    @staticmethod
    def _key(
        action: TreatmentAction,
        *,
        event_type: str,
        previous_state: str | None,
        new_state: str | None,
        occurred_at: datetime,
        operation_key: str | None = None,
    ) -> str:
        if operation_key:
            normalized = (operation_key or "").strip()
            if not normalized or len(normalized) > 128:
                raise TreatmentActionIdempotencyConflict(
                    "operation_key must be 128 characters or fewer"
                )
            return f"treatment-action:{action.id}:{event_type}:{normalized}"

        stamp = occurred_at.astimezone(timezone.utc).isoformat(timespec="microseconds")
        return (
            f"treatment-action:{action.id}:{event_type}:"
            f"{previous_state or 'none'}:{new_state or 'none'}:{stamp}"
        )

    @classmethod
    def create(
        cls,
        *,
        plan_id: int,
        actor_user_id: int,
        creation_key: str,
        title: str,
        client_summary: str | None = None,
        internal_instructions: str | None = None,
        visibility: str = "client",
        occurred_at: datetime | None = None,
        source: str = "advisor.treatment_action_create",
    ) -> TreatmentAction:
        """Create one planned action and its canonical creation event atomically."""

        plan = cls._load_plan_for_update(plan_id)
        cls._require_advisor(car_id=plan.car_id, actor_user_id=actor_user_id)
        cls._require_nonterminal_plan(plan)

        key = cls._normalise_key(creation_key)
        normalized_title = cls._normalise_title(title)
        normalized_client_summary = cls._normalise_optional(client_summary)
        normalized_internal = cls._normalise_optional(internal_instructions)
        normalized_visibility = cls._normalise_visibility(visibility)

        existing = (
            TreatmentAction.query.filter_by(
                treatment_plan_id=plan.id,
                creation_key=key,
            )
            .with_for_update()
            .first()
        )
        if existing is not None:
            cls._action_scope(existing)
            same_semantics = (
                existing.car_id == plan.car_id
                and existing.title == normalized_title
                and existing.client_summary == normalized_client_summary
                and existing.internal_instructions == normalized_internal
                and existing.visibility == normalized_visibility
            )
            if not same_semantics:
                raise TreatmentActionIdempotencyConflict(
                    "Treatment Action creation_key was replayed with different semantics"
                )
            return existing

        when = cls._now(occurred_at)
        action = TreatmentAction(
            treatment_plan_id=plan.id,
            car_id=plan.car_id,
            created_by_user_id=actor_user_id,
            creation_key=key,
            title=normalized_title,
            client_summary=normalized_client_summary,
            internal_instructions=normalized_internal,
            status=cls.PLANNED,
            visibility=normalized_visibility,
            created_at=when.astimezone(timezone.utc).replace(tzinfo=None),
            updated_at=when.astimezone(timezone.utc).replace(tzinfo=None),
        )
        db.session.add(action)
        db.session.flush()

        emit_treatment_action_event(
            car_id=action.car_id,
            action_id=action.id,
            event_type="treatment_action.created",
            actor_user_id=actor_user_id,
            occurred_at=when,
            source=source,
            title="Treatment action created",
            previous_state=None,
            new_state=cls.PLANNED,
            idempotency_key=f"treatment-action:{action.id}:created:{key}",
            visibility=action.visibility,
            data={"treatment_plan_id": plan.id},
        )
        return action

    @classmethod
    def schedule(
        cls,
        *,
        action_id: int,
        actor_user_id: int,
        scheduled_for: datetime,
        occurred_at: datetime | None = None,
        source: str = "advisor.treatment_action_schedule",
        operation_key: str | None = None,
    ) -> TreatmentAction:
        action = cls._load_action_for_update(action_id)
        plan = cls._action_scope(action)
        cls._require_advisor(car_id=action.car_id, actor_user_id=actor_user_id)

        target_schedule = cls._normalise_datetime(scheduled_for)
        if action.status == cls.SCHEDULED:
            persisted = action.scheduled_for
            if persisted is not None:
                if persisted.tzinfo is None:
                    persisted = persisted.replace(tzinfo=timezone.utc)
                else:
                    persisted = persisted.astimezone(timezone.utc)
            if persisted == target_schedule:
                return action
            raise TreatmentActionStateError(
                "Treatment Action is already scheduled; rescheduling is not contracted in Wave 2.3C"
            )

        if action.status not in {cls.PLANNED, cls.DEFERRED}:
            raise TreatmentActionStateError(
                f"cannot schedule Treatment Action from {action.status!r}"
            )
        if plan.status not in {"scheduled", "in_progress"}:
            raise TreatmentActionStateError(
                "parent Treatment Plan must be scheduled or in progress before an action is scheduled"
            )

        previous = action.status
        when = cls._now(occurred_at)
        action.status = cls.SCHEDULED
        action.scheduled_for = target_schedule.astimezone(timezone.utc).replace(tzinfo=None)
        action.updated_at = when.astimezone(timezone.utc).replace(tzinfo=None)
        db.session.flush()

        emit_treatment_action_event(
            car_id=action.car_id,
            action_id=action.id,
            event_type="treatment_action.scheduled",
            actor_user_id=actor_user_id,
            occurred_at=when,
            source=source,
            title="Treatment action scheduled",
            previous_state=previous,
            new_state=cls.SCHEDULED,
            idempotency_key=cls._key(
                action,
                event_type="treatment_action.scheduled",
                previous_state=previous,
                new_state=cls.SCHEDULED,
                occurred_at=when,
                operation_key=operation_key,
            ),
            visibility=action.visibility,
            data={
                "treatment_plan_id": plan.id,
                "scheduled_for": target_schedule.isoformat(),
            },
        )
        return action

    @classmethod
    def start(
        cls,
        *,
        action_id: int,
        actor_user_id: int,
        occurred_at: datetime | None = None,
        source: str = "advisor.treatment_action_start",
        operation_key: str | None = None,
    ) -> TreatmentAction:
        action = cls._load_action_for_update(action_id)
        plan = cls._action_scope(action)
        cls._require_advisor(car_id=action.car_id, actor_user_id=actor_user_id)

        if action.status == cls.IN_PROGRESS:
            return action
        if action.status != cls.SCHEDULED:
            raise TreatmentActionStateError(
                f"cannot start Treatment Action from {action.status!r}"
            )
        if plan.status != "in_progress":
            raise TreatmentActionStateError(
                "parent Treatment Plan must be in progress before an action starts"
            )

        previous = action.status
        when = cls._now(occurred_at)
        action.status = cls.IN_PROGRESS
        action.started_at = when.astimezone(timezone.utc).replace(tzinfo=None)
        action.updated_at = when.astimezone(timezone.utc).replace(tzinfo=None)
        db.session.flush()

        emit_treatment_action_event(
            car_id=action.car_id,
            action_id=action.id,
            event_type="treatment_action.started",
            actor_user_id=actor_user_id,
            occurred_at=when,
            source=source,
            title="Treatment action started",
            previous_state=previous,
            new_state=cls.IN_PROGRESS,
            idempotency_key=cls._key(
                action,
                event_type="treatment_action.started",
                previous_state=previous,
                new_state=cls.IN_PROGRESS,
                occurred_at=when,
                operation_key=operation_key,
            ),
            visibility=action.visibility,
            data={"treatment_plan_id": plan.id},
        )
        return action

    @classmethod
    def complete(
        cls,
        *,
        action_id: int,
        actor_user_id: int,
        occurred_at: datetime | None = None,
        source: str = "advisor.treatment_action_complete",
        operation_key: str | None = None,
    ) -> TreatmentAction:
        action = cls._load_action_for_update(action_id)
        plan = cls._action_scope(action)
        cls._require_advisor(car_id=action.car_id, actor_user_id=actor_user_id)

        if action.status == cls.COMPLETED:
            return action
        if action.status != cls.IN_PROGRESS:
            raise TreatmentActionStateError(
                f"cannot complete Treatment Action from {action.status!r}"
            )
        if plan.status not in {"in_progress", "monitoring"}:
            raise TreatmentActionStateError(
                "parent Treatment Plan must be in progress or monitoring before an action completes"
            )

        previous = action.status
        when = cls._now(occurred_at)
        action.status = cls.COMPLETED
        action.completed_at = when.astimezone(timezone.utc).replace(tzinfo=None)
        action.updated_at = when.astimezone(timezone.utc).replace(tzinfo=None)
        db.session.flush()

        emit_treatment_action_event(
            car_id=action.car_id,
            action_id=action.id,
            event_type="treatment_action.completed",
            actor_user_id=actor_user_id,
            occurred_at=when,
            source=source,
            title="Treatment action completed",
            previous_state=previous,
            new_state=cls.COMPLETED,
            idempotency_key=cls._key(
                action,
                event_type="treatment_action.completed",
                previous_state=previous,
                new_state=cls.COMPLETED,
                occurred_at=when,
                operation_key=operation_key,
            ),
            visibility=action.visibility,
            data={"treatment_plan_id": plan.id},
            description=(
                "The professional intervention action was recorded complete. "
                "No treatment outcome or Vehicle Health progression is implied."
            ),
        )
        return action

    @classmethod
    def defer(
        cls,
        *,
        action_id: int,
        actor_user_id: int,
        occurred_at: datetime | None = None,
        source: str = "advisor.treatment_action_defer",
        operation_key: str | None = None,
    ) -> TreatmentAction:
        action = cls._load_action_for_update(action_id)
        plan = cls._action_scope(action)
        cls._require_advisor(car_id=action.car_id, actor_user_id=actor_user_id)
        cls._require_nonterminal_plan(plan)

        if action.status == cls.DEFERRED:
            return action
        if action.status not in {cls.PLANNED, cls.SCHEDULED}:
            raise TreatmentActionStateError(
                f"cannot defer Treatment Action from {action.status!r}"
            )

        previous = action.status
        when = cls._now(occurred_at)
        action.status = cls.DEFERRED
        action.deferred_at = when.astimezone(timezone.utc).replace(tzinfo=None)
        action.updated_at = when.astimezone(timezone.utc).replace(tzinfo=None)
        db.session.flush()

        emit_treatment_action_event(
            car_id=action.car_id,
            action_id=action.id,
            event_type="treatment_action.deferred",
            actor_user_id=actor_user_id,
            occurred_at=when,
            source=source,
            title="Treatment action deferred",
            previous_state=previous,
            new_state=cls.DEFERRED,
            idempotency_key=cls._key(
                action,
                event_type="treatment_action.deferred",
                previous_state=previous,
                new_state=cls.DEFERRED,
                occurred_at=when,
                operation_key=operation_key,
            ),
            visibility=action.visibility,
            data={"treatment_plan_id": plan.id},
        )
        return action

    @classmethod
    def cancel(
        cls,
        *,
        action_id: int,
        actor_user_id: int,
        occurred_at: datetime | None = None,
        source: str = "advisor.treatment_action_cancel",
        operation_key: str | None = None,
    ) -> TreatmentAction:
        action = cls._load_action_for_update(action_id)
        plan = cls._action_scope(action)
        cls._require_advisor(car_id=action.car_id, actor_user_id=actor_user_id)
        cls._require_nonterminal_plan(plan)

        if action.status == cls.CANCELLED:
            return action
        if action.status not in {cls.PLANNED, cls.SCHEDULED, cls.DEFERRED}:
            raise TreatmentActionStateError(
                f"cannot cancel Treatment Action from {action.status!r}"
            )

        previous = action.status
        when = cls._now(occurred_at)
        action.status = cls.CANCELLED
        action.cancelled_at = when.astimezone(timezone.utc).replace(tzinfo=None)
        action.updated_at = when.astimezone(timezone.utc).replace(tzinfo=None)
        db.session.flush()

        emit_treatment_action_event(
            car_id=action.car_id,
            action_id=action.id,
            event_type="treatment_action.cancelled",
            actor_user_id=actor_user_id,
            occurred_at=when,
            source=source,
            title="Treatment action cancelled",
            previous_state=previous,
            new_state=cls.CANCELLED,
            idempotency_key=cls._key(
                action,
                event_type="treatment_action.cancelled",
                previous_state=previous,
                new_state=cls.CANCELLED,
                occurred_at=when,
                operation_key=operation_key,
            ),
            visibility=action.visibility,
            data={"treatment_plan_id": plan.id},
        )
        return action
