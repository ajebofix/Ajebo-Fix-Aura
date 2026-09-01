"""Advisor-reviewed Treatment Outcome recording for Aura Wave 2.3C.

Treatment Outcomes are additive professional observations. This service never
commits independently: the outer caller owns the transaction so the durable
outcome row, governed EvidenceLink rows, evidence.linked events and canonical
treatment.outcome_recorded event succeed or roll back together.

Recording an outcome never mutates Treatment Plan state, Reported Concern state
or Vehicle Health.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Iterable

from evidence.models import EvidenceLink
from extensions import db
from models import TreatmentPlan
from security.access import resolve_vehicle_authority
from services.treatment_evidence_linking import (
    TreatmentEvidenceLinkConflict,
    link_accepted_evidence_to_treatment_subject,
)
from services.treatment_event_emission import (
    TREATMENT_OUTCOME_DIRECTIONS,
    emit_treatment_plan_event,
)
from treatment.models import TreatmentAction, TreatmentOutcome


class TreatmentOutcomeRecordingError(ValueError):
    """Base safe failure for Treatment Outcome recording."""


class TreatmentOutcomeAuthorityError(TreatmentOutcomeRecordingError):
    """Raised when the actor lacks professional authority for the vehicle."""


class TreatmentOutcomeStateError(TreatmentOutcomeRecordingError):
    """Raised when the parent Treatment Plan cannot accept an outcome."""


class TreatmentOutcomeScopeError(TreatmentOutcomeRecordingError):
    """Raised when plan/action/evidence vehicle scope disagrees."""


class TreatmentOutcomeIdempotencyConflict(TreatmentOutcomeRecordingError):
    """Raised when a recording key is replayed with different semantics."""


class TreatmentOutcomeProvenanceError(TreatmentOutcomeRecordingError):
    """Raised when outcome provenance is insufficient or unsafe."""


class TreatmentOutcomeRecordingService:
    """Canonical append-only Treatment Outcome recorder."""

    _PLAN_STATES = frozenset({"in_progress", "monitoring", "completed"})
    _PROVENANCE_KINDS = frozenset(
        {"reviewed_evidence", "professional_observation", "insufficient_evidence"}
    )
    _VISIBILITIES = frozenset({"client", "advisor"})
    _OBSERVATION_SOURCES = frozenset(
        {"advisor_inspection", "road_test", "measurement", "client_follow_up"}
    )

    @staticmethod
    def _now(value: datetime | None = None) -> datetime:
        result = value or datetime.now(timezone.utc)
        if result.tzinfo is None:
            result = result.replace(tzinfo=timezone.utc)
        return result.astimezone(timezone.utc)

    @staticmethod
    def _naive_utc(value: datetime) -> datetime:
        return TreatmentOutcomeRecordingService._now(value).replace(tzinfo=None)

    @staticmethod
    def _normalise_required_text(value: str, *, field: str, max_length: int | None = None) -> str:
        text = (value or "").strip()
        if not text:
            raise TreatmentOutcomeRecordingError(f"{field} is required")
        if max_length is not None and len(text) > max_length:
            raise TreatmentOutcomeRecordingError(
                f"{field} must be {max_length} characters or fewer"
            )
        return text

    @staticmethod
    def _normalise_optional_text(value: str | None) -> str | None:
        text = (value or "").strip()
        return text or None

    @staticmethod
    def _normalise_evidence_ids(values: Iterable[int] | None) -> tuple[int, ...]:
        normalized: set[int] = set()
        for raw in values or ():
            try:
                value = int(raw)
            except (TypeError, ValueError) as exc:
                raise TreatmentOutcomeProvenanceError(
                    "Supporting evidence identifiers must be integers"
                ) from exc
            if value <= 0:
                raise TreatmentOutcomeProvenanceError(
                    "Supporting evidence identifiers must be positive"
                )
            normalized.add(value)
        return tuple(sorted(normalized))

    @classmethod
    def _normalise_provenance_data(
        cls,
        *,
        provenance_kind: str,
        provenance_data: dict | None,
    ) -> dict | None:
        if provenance_kind != "professional_observation":
            if provenance_data not in (None, {}):
                raise TreatmentOutcomeProvenanceError(
                    "Structured provenance data is only accepted for professional observations"
                )
            return None

        if not isinstance(provenance_data, dict):
            raise TreatmentOutcomeProvenanceError(
                "Professional observation provenance must be a small structured object"
            )
        if set(provenance_data) - {"observation_source", "reference"}:
            raise TreatmentOutcomeProvenanceError(
                "Professional observation provenance contains unsupported fields"
            )

        source = str(provenance_data.get("observation_source") or "").strip().lower()
        if source not in cls._OBSERVATION_SOURCES:
            raise TreatmentOutcomeProvenanceError(
                "Select a supported professional observation source"
            )

        normalized = {"observation_source": source}
        reference = str(provenance_data.get("reference") or "").strip()
        if reference:
            if len(reference) > 160:
                raise TreatmentOutcomeProvenanceError(
                    "Professional observation reference must be 160 characters or fewer"
                )
            normalized["reference"] = reference
        return normalized

    @staticmethod
    def _load_plan_for_update(plan_id: int) -> TreatmentPlan:
        plan = TreatmentPlan.query.filter_by(id=plan_id).with_for_update().first()
        if plan is None:
            raise TreatmentOutcomeRecordingError("Treatment Plan not found")
        return plan

    @staticmethod
    def _require_advisor(*, car_id: int, actor_user_id: int) -> str:
        authority = resolve_vehicle_authority(actor_user_id, car_id)
        if authority not in {"advisor", "administrator"}:
            raise TreatmentOutcomeAuthorityError(
                "Treatment Outcome recording requires advisor authority"
            )
        return authority

    @classmethod
    def _action_scope(
        cls,
        *,
        plan: TreatmentPlan,
        treatment_action_id: int | None,
    ) -> TreatmentAction | None:
        if treatment_action_id is None:
            return None
        action = (
            TreatmentAction.query.filter_by(id=treatment_action_id)
            .with_for_update()
            .first()
        )
        if action is None:
            raise TreatmentOutcomeScopeError("Treatment Action not found")
        if action.treatment_plan_id != plan.id or action.car_id != plan.car_id:
            raise TreatmentOutcomeScopeError(
                "Treatment Outcome Action must belong to the same Treatment Plan and vehicle"
            )
        return action

    @staticmethod
    def _existing_evidence_ids(outcome_id: int) -> tuple[int, ...]:
        return tuple(
            evidence_id
            for (evidence_id,) in (
                db.session.query(EvidenceLink.evidence_id)
                .filter_by(
                    subject_type="treatment_outcome",
                    subject_id=outcome_id,
                    relationship_type="supports",
                )
                .order_by(EvidenceLink.evidence_id.asc())
                .all()
            )
        )

    @classmethod
    def record(
        cls,
        *,
        plan_id: int,
        actor_user_id: int,
        recording_key: str,
        progression_direction: str,
        summary: str,
        provenance_kind: str,
        evidence_ids: Iterable[int] | None = None,
        treatment_action_id: int | None = None,
        advisor_note: str | None = None,
        visibility: str = "client",
        provenance_data: dict | None = None,
        observed_at: datetime | None = None,
        occurred_at: datetime | None = None,
        source: str = "advisor.treatment_outcome_record",
    ) -> TreatmentOutcome:
        """Record/reconcile one evidence-backed professional outcome.

        The caller must commit or roll back the outer transaction.
        """

        plan = cls._load_plan_for_update(plan_id)
        cls._require_advisor(car_id=plan.car_id, actor_user_id=actor_user_id)
        if plan.status not in cls._PLAN_STATES:
            raise TreatmentOutcomeStateError(
                f"cannot record Treatment Outcome while parent plan is {plan.status!r}"
            )

        action = cls._action_scope(
            plan=plan,
            treatment_action_id=treatment_action_id,
        )

        key = cls._normalise_required_text(
            recording_key,
            field="Treatment Outcome recording_key",
            max_length=128,
        )
        direction = (progression_direction or "").strip().lower()
        if direction not in TREATMENT_OUTCOME_DIRECTIONS:
            raise TreatmentOutcomeRecordingError(
                "Treatment Outcome progression direction is invalid"
            )
        normalized_summary = cls._normalise_required_text(
            summary,
            field="Treatment Outcome summary",
        )
        normalized_advisor_note = cls._normalise_optional_text(advisor_note)
        normalized_visibility = (visibility or "client").strip().lower()
        if normalized_visibility not in cls._VISIBILITIES:
            raise TreatmentOutcomeRecordingError(
                "Treatment Outcome visibility must be client or advisor"
            )

        normalized_provenance_kind = (provenance_kind or "").strip().lower()
        if normalized_provenance_kind not in cls._PROVENANCE_KINDS:
            raise TreatmentOutcomeProvenanceError(
                "Treatment Outcome provenance kind is invalid"
            )
        if (
            normalized_provenance_kind == "insufficient_evidence"
            and direction != "insufficient_evidence"
        ):
            raise TreatmentOutcomeProvenanceError(
                "Insufficient-evidence provenance cannot support a positive/negative outcome direction"
            )

        normalized_evidence_ids = cls._normalise_evidence_ids(evidence_ids)
        if normalized_provenance_kind == "reviewed_evidence" and not normalized_evidence_ids:
            raise TreatmentOutcomeProvenanceError(
                "Reviewed-evidence outcomes require at least one accepted evidence record"
            )
        normalized_provenance_data = cls._normalise_provenance_data(
            provenance_kind=normalized_provenance_kind,
            provenance_data=provenance_data,
        )

        observed = cls._now(observed_at)
        recorded = cls._now(occurred_at)

        existing = (
            TreatmentOutcome.query.filter_by(
                treatment_plan_id=plan.id,
                recording_key=key,
            )
            .with_for_update()
            .first()
        )
        if existing is not None:
            same_semantics = (
                existing.car_id == plan.car_id
                and existing.treatment_action_id == (action.id if action else None)
                and existing.progression_direction == direction
                and existing.summary == normalized_summary
                and existing.advisor_note == normalized_advisor_note
                and existing.visibility == normalized_visibility
                and existing.provenance_kind == normalized_provenance_kind
                and (existing.provenance_data or None) == normalized_provenance_data
                and existing.observed_at == cls._naive_utc(observed)
                and cls._existing_evidence_ids(existing.id) == normalized_evidence_ids
            )
            if not same_semantics:
                raise TreatmentOutcomeIdempotencyConflict(
                    "Treatment Outcome recording_key was replayed with different semantics"
                )
            outcome = existing
        else:
            outcome = TreatmentOutcome(
                treatment_plan_id=plan.id,
                treatment_action_id=(action.id if action else None),
                car_id=plan.car_id,
                recorded_by_user_id=actor_user_id,
                recording_key=key,
                progression_direction=direction,
                summary=normalized_summary,
                advisor_note=normalized_advisor_note,
                visibility=normalized_visibility,
                provenance_kind=normalized_provenance_kind,
                provenance_data=normalized_provenance_data,
                observed_at=cls._naive_utc(observed),
                created_at=cls._naive_utc(recorded),
            )
            db.session.add(outcome)
            db.session.flush()

            for evidence_id in normalized_evidence_ids:
                try:
                    link_accepted_evidence_to_treatment_subject(
                        actor_user_id=actor_user_id,
                        evidence_id=evidence_id,
                        subject_type="treatment_outcome",
                        subject_id=outcome.id,
                        relationship_type="supports",
                    )
                except TreatmentEvidenceLinkConflict as exc:
                    raise TreatmentOutcomeProvenanceError(str(exc)) from exc

        evidence_refs = [
            {"type": "vehicle_evidence", "id": evidence_id}
            for evidence_id in normalized_evidence_ids
        ]
        emit_treatment_plan_event(
            car_id=plan.car_id,
            plan_id=plan.id,
            event_type="treatment.outcome_recorded",
            actor_user_id=actor_user_id,
            occurred_at=recorded,
            source=source,
            title="Treatment outcome recorded",
            previous_state=plan.status,
            new_state=plan.status,
            progression_direction=direction,
            idempotency_key=f"treatment-outcome:{outcome.id}:recorded:{key}",
            visibility=outcome.visibility,
            evidence_refs=evidence_refs,
            data={
                "outcome_id": outcome.id,
                "treatment_action_id": outcome.treatment_action_id,
                "provenance_kind": outcome.provenance_kind,
            },
            description=(
                "Advisor-reviewed treatment outcome recorded as a separate professional fact. "
                "No Treatment Plan, Reported Concern or Vehicle Health state was changed."
            ),
        )
        return outcome
