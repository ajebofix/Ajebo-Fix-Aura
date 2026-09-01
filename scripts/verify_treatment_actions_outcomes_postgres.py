"""Verify Aura Wave 2.3C Treatment Action/Outcome contracts on PostgreSQL."""

from __future__ import annotations

from datetime import datetime
import hashlib
from pathlib import Path
import sys
import uuid

from sqlalchemy import inspect
from sqlalchemy.exc import IntegrityError

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import app  # noqa: E402
from evidence.models import EvidenceLink, VehicleEvidence  # noqa: E402
from extensions import db  # noqa: E402
from models import Car, CarOwnership, TreatmentPlan, User, VehicleEvent  # noqa: E402
from services.treatment_action_lifecycle import (  # noqa: E402
    TreatmentActionAuthorityError,
    TreatmentActionLifecycleService,
)
from services.treatment_outcome_recording import (  # noqa: E402
    TreatmentOutcomeProvenanceError,
    TreatmentOutcomeRecordingService,
)
from services.treatment_plan_lifecycle import TreatmentPlanLifecycleService  # noqa: E402
from treatment.models import TreatmentAction, TreatmentOutcome  # noqa: E402


def _raw_event(
    *,
    car_id: int,
    ownership_id: int,
    actor_user_id: int,
    event_type: str,
    subject_type: str,
    subject_id: int,
    progression_direction: str | None,
    previous_state: str | None,
    new_state: str | None,
) -> VehicleEvent:
    now = datetime(2026, 9, 1, 18, 0, 0)
    return VehicleEvent(
        car_id=car_id,
        ownership_id=ownership_id,
        event_type=event_type,
        severity="low",
        event_date=now.date(),
        title="Wave 2.3C PostgreSQL negative probe",
        description=None,
        mileage=27000,
        source="verify.treatment_actions_outcomes",
        data={},
        fingerprint=hashlib.sha256(uuid.uuid4().bytes).hexdigest(),
        schema_version=1,
        occurred_at=now,
        recorded_at=now,
        subject_type=subject_type,
        subject_id=subject_id,
        actor_type="user",
        actor_user_id=actor_user_id,
        actor_authority="advisor",
        visibility="client",
        previous_state=previous_state,
        new_state=new_state,
        progression_direction=progression_direction,
        evidence_refs=[],
        correction_of_event_id=None,
        created_by=actor_user_id,
        is_deleted=False,
    )


def _expect_integrity_failure(row, label: str) -> None:
    db.session.add(row)
    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        return
    raise SystemExit(f"PostgreSQL accepted invalid Wave 2.3C row: {label}")


def _user(*, token: str, role: str, suffix: str) -> User:
    user = User(
        name=f"Wave 2.3C {role} {suffix}",
        email=f"wave23c-{suffix}-{token}@example.com",
        phone_number=f"+23489{token[:6]}{suffix[-2:]}",
        role=role,
        is_active=True,
    )
    user.set_password("Password123")
    db.session.add(user)
    db.session.flush()
    return user


def _evidence(*, car_id: int, uploader_id: int, token: str, visibility: str = "client") -> VehicleEvidence:
    now = datetime(2026, 9, 1, 18, 10, 0)
    evidence = VehicleEvidence(
        car_id=car_id,
        uploaded_by_user_id=uploader_id,
        evidence_type="image",
        purpose="treatment_evidence",
        source_channel="web",
        visibility=visibility,
        review_status="accepted",
        storage_provider="r2",
        storage_state="available",
        object_key=f"verify/treatment/{token}/{uuid.uuid4().hex}.jpg",
        safe_display_name="verified-treatment-evidence.jpg",
        content_type="image/jpeg",
        byte_size=1024,
        sha256=hashlib.sha256(uuid.uuid4().bytes).hexdigest(),
        consent_basis="client_submission",
        lawful_purpose="vehicle care evidence",
        reviewed_by_user_id=uploader_id,
        reviewed_at=now,
        review_reason_code="advisor_verified",
    )
    db.session.add(evidence)
    db.session.commit()
    return evidence


def main() -> None:
    with app.app_context():
        if db.engine.dialect.name != "postgresql":
            raise SystemExit("This verifier must run against PostgreSQL")

        inspector = inspect(db.engine)
        tables = set(inspector.get_table_names())
        for required in {"treatment_actions", "treatment_outcomes"}:
            if required not in tables:
                raise SystemExit(f"Missing Wave 2.3C table: {required}")

        checks = {
            item["name"]
            for item in inspector.get_check_constraints("vehicle_events")
        }
        required_checks = {
            "ck_vehicle_events_canonical_subject_event",
            "ck_vehicle_events_treatment_plan_contract",
            "ck_vehicle_events_treatment_action_contract",
        }
        missing = required_checks - checks
        if missing:
            raise SystemExit(f"Missing Wave 2.3C VehicleEvent checks: {sorted(missing)}")

        token = uuid.uuid4().hex[:10]
        owner = _user(token=token, role="user", suffix="owner")
        advisor = _user(token=token, role="admin", suffix="advisor")
        unrelated = _user(token=token, role="user", suffix="other")

        car = Car(
            brand="Mercedes-Benz",
            model="GLE 450",
            year=2024,
            vin=f"W1N23C{token.upper()}",
            transmission_type="9G-TRONIC",
            current_mileage=27000,
        )
        other_car = Car(
            brand="Mercedes-Benz",
            model="GLC 300",
            year=2023,
            vin=f"W1N23X{token.upper()}",
            current_mileage=19000,
        )
        db.session.add_all([car, other_car])
        db.session.flush()

        ownership = CarOwnership(
            user_id=owner.id,
            car_id=car.id,
            plate_number=f"C-{token[:3].upper()}-LA",
            mileage_at_transfer=26000,
            is_active=True,
        )
        other_ownership = CarOwnership(
            user_id=unrelated.id,
            car_id=other_car.id,
            plate_number=f"X-{token[:3].upper()}-LA",
            mileage_at_transfer=18000,
            is_active=True,
        )
        db.session.add_all([ownership, other_ownership])
        db.session.flush()

        # A legacy plan existing before this verifier must not magically create
        # TreatmentAction/Outcome history merely because the migration exists.
        legacy = TreatmentPlan(
            car_id=car.id,
            advisor_id=advisor.id,
            title="Historical compatibility plan",
            status="approved",
        )
        plan = TreatmentPlan(
            car_id=car.id,
            advisor_id=advisor.id,
            title="Wave 2.3C verifier plan",
            status="authorized",
            client_summary="Verified professional care pathway.",
        )
        db.session.add_all([legacy, plan])
        db.session.commit()
        if TreatmentAction.query.count() != 0 or TreatmentOutcome.query.count() != 0:
            raise SystemExit("Wave 2.3C migration synthesized historical treatment history")

        action = TreatmentActionLifecycleService.create(
            plan_id=plan.id,
            actor_user_id=advisor.id,
            creation_key="verify-action-1",
            title="Front braking system intervention",
            client_summary="Carry out the authorized braking-system intervention.",
            internal_instructions="VERIFIER INTERNAL DETAIL — owner must not receive this.",
            visibility="client",
            occurred_at=datetime(2026, 9, 1, 18, 20, 0),
        )
        db.session.commit()

        # Owner/unrelated accounts cannot author professional intervention facts.
        try:
            TreatmentActionLifecycleService.create(
                plan_id=plan.id,
                actor_user_id=owner.id,
                creation_key="blocked-owner-action",
                title="Blocked owner action",
            )
        except TreatmentActionAuthorityError:
            db.session.rollback()
        else:
            raise SystemExit("Owner was able to create a professional Treatment Action")

        TreatmentPlanLifecycleService.schedule(
            plan_id=plan.id,
            actor_user_id=advisor.id,
            occurred_at=datetime(2026, 9, 1, 18, 25, 0),
            operation_key="verify-plan-schedule",
        )
        TreatmentActionLifecycleService.schedule(
            action_id=action.id,
            actor_user_id=advisor.id,
            scheduled_for=datetime(2026, 9, 2, 9, 0, 0),
            occurred_at=datetime(2026, 9, 1, 18, 26, 0),
            operation_key="verify-action-schedule",
        )
        TreatmentPlanLifecycleService.start(
            plan_id=plan.id,
            actor_user_id=advisor.id,
            occurred_at=datetime(2026, 9, 2, 9, 0, 0),
            operation_key="verify-plan-start",
        )
        TreatmentActionLifecycleService.start(
            action_id=action.id,
            actor_user_id=advisor.id,
            occurred_at=datetime(2026, 9, 2, 9, 1, 0),
            operation_key="verify-action-start",
        )
        TreatmentActionLifecycleService.complete(
            action_id=action.id,
            actor_user_id=advisor.id,
            occurred_at=datetime(2026, 9, 2, 10, 0, 0),
            operation_key="verify-action-complete",
        )
        db.session.commit()

        persisted_plan = db.session.get(TreatmentPlan, plan.id)
        persisted_action = db.session.get(TreatmentAction, action.id)
        if persisted_plan is None or persisted_plan.status != "in_progress":
            raise SystemExit("Completing a Treatment Action incorrectly completed its parent plan")
        if persisted_action is None or persisted_action.status != "completed":
            raise SystemExit("Treatment Action lifecycle did not persist completed state")

        evidence = _evidence(
            car_id=car.id,
            uploader_id=owner.id,
            token=token,
            visibility="client",
        )
        outcome = TreatmentOutcomeRecordingService.record(
            plan_id=plan.id,
            actor_user_id=advisor.id,
            recording_key="verify-outcome-1",
            progression_direction="improving",
            summary="Accepted evidence supports an improving post-treatment observation.",
            provenance_kind="reviewed_evidence",
            evidence_ids=[evidence.id],
            treatment_action_id=action.id,
            visibility="client",
            observed_at=datetime(2026, 9, 2, 12, 0, 0),
            occurred_at=datetime(2026, 9, 2, 12, 5, 0),
        )
        db.session.commit()

        if EvidenceLink.query.filter_by(
            evidence_id=evidence.id,
            subject_type="treatment_outcome",
            subject_id=outcome.id,
            relationship_type="supports",
        ).count() != 1:
            raise SystemExit("Outcome did not persist exactly one governed evidence link")

        outcome_events = VehicleEvent.query.filter_by(
            subject_type="treatment_plan",
            subject_id=plan.id,
            event_type="treatment.outcome_recorded",
        ).all()
        if len(outcome_events) != 1:
            raise SystemExit("Expected exactly one canonical treatment.outcome_recorded event")
        if outcome_events[0].progression_direction != "improving":
            raise SystemExit("Treatment outcome event lost progression direction")

        # Cross-vehicle evidence must fail before a half-record can be committed.
        foreign_evidence = _evidence(
            car_id=other_car.id,
            uploader_id=unrelated.id,
            token=f"x{token}",
            visibility="client",
        )
        try:
            TreatmentOutcomeRecordingService.record(
                plan_id=plan.id,
                actor_user_id=advisor.id,
                recording_key="verify-cross-vehicle",
                progression_direction="stable",
                summary="This cross-vehicle provenance must fail closed.",
                provenance_kind="reviewed_evidence",
                evidence_ids=[foreign_evidence.id],
                visibility="client",
            )
        except TreatmentOutcomeProvenanceError:
            db.session.rollback()
        else:
            raise SystemExit("Cross-vehicle evidence supported a Treatment Outcome")
        if TreatmentOutcome.query.filter_by(
            treatment_plan_id=plan.id,
            recording_key="verify-cross-vehicle",
        ).count():
            raise SystemExit("Failed cross-vehicle outcome left a half-created row")

        car_id = car.id
        ownership_id = ownership.id
        advisor_id = advisor.id
        action_id = action.id
        plan_id = plan.id

        # Raw PostgreSQL negative probes: these bypass Python validators and prove
        # the database rejects malformed canonical envelopes, including NULLs.
        probes = [
            (
                _raw_event(
                    car_id=car_id,
                    ownership_id=ownership_id,
                    actor_user_id=advisor_id,
                    event_type="treatment.completed",
                    subject_type="treatment_action",
                    subject_id=action_id,
                    progression_direction="not_applicable",
                    previous_state="in_progress",
                    new_state="completed",
                ),
                "action subject paired with plan event",
            ),
            (
                _raw_event(
                    car_id=car_id,
                    ownership_id=ownership_id,
                    actor_user_id=advisor_id,
                    event_type="treatment_action.created",
                    subject_type="treatment_action",
                    subject_id=action_id,
                    progression_direction="not_applicable",
                    previous_state="planned",
                    new_state="planned",
                ),
                "created event with previous state",
            ),
            (
                _raw_event(
                    car_id=car_id,
                    ownership_id=ownership_id,
                    actor_user_id=advisor_id,
                    event_type="treatment_action.started",
                    subject_type="treatment_action",
                    subject_id=action_id,
                    progression_direction="not_applicable",
                    previous_state="planned",
                    new_state="in_progress",
                ),
                "started event from planned",
            ),
            (
                _raw_event(
                    car_id=car_id,
                    ownership_id=ownership_id,
                    actor_user_id=advisor_id,
                    event_type="treatment_action.started",
                    subject_type="treatment_action",
                    subject_id=action_id,
                    progression_direction=None,
                    previous_state="scheduled",
                    new_state="in_progress",
                ),
                "action transition with NULL progression",
            ),
            (
                _raw_event(
                    car_id=car_id,
                    ownership_id=ownership_id,
                    actor_user_id=advisor_id,
                    event_type="treatment_action.completed",
                    subject_type="treatment_action",
                    subject_id=action_id,
                    progression_direction="not_applicable",
                    previous_state="in_progress",
                    new_state=None,
                ),
                "action transition with NULL new_state",
            ),
            (
                _raw_event(
                    car_id=car_id,
                    ownership_id=ownership_id,
                    actor_user_id=advisor_id,
                    event_type="treatment.outcome_recorded",
                    subject_type="treatment_plan",
                    subject_id=plan_id,
                    progression_direction="not_applicable",
                    previous_state="in_progress",
                    new_state="in_progress",
                ),
                "outcome event with non-outcome progression",
            ),
            (
                _raw_event(
                    car_id=car_id,
                    ownership_id=ownership_id,
                    actor_user_id=advisor_id,
                    event_type="treatment.outcome_recorded",
                    subject_type="treatment_plan",
                    subject_id=plan_id,
                    progression_direction="stable",
                    previous_state="in_progress",
                    new_state="completed",
                ),
                "outcome event mutating parent state",
            ),
            (
                _raw_event(
                    car_id=car_id,
                    ownership_id=ownership_id,
                    actor_user_id=advisor_id,
                    event_type="treatment.outcome_recorded",
                    subject_type="treatment_plan",
                    subject_id=plan_id,
                    progression_direction=None,
                    previous_state="in_progress",
                    new_state="in_progress",
                ),
                "outcome event with NULL progression",
            ),
        ]
        for row, label in probes:
            _expect_integrity_failure(row, label)

        # Domain-table CHECK constraints must also reject malformed records.
        _expect_integrity_failure(
            TreatmentAction(
                treatment_plan_id=plan_id,
                car_id=car_id,
                created_by_user_id=advisor_id,
                creation_key="invalid-status-probe",
                title="Invalid status probe",
                status="diagnosed",
                visibility="client",
            ),
            "invalid TreatmentAction status",
        )
        _expect_integrity_failure(
            TreatmentAction(
                treatment_plan_id=plan_id,
                car_id=car_id,
                created_by_user_id=advisor_id,
                creation_key="blank-title-probe",
                title="   ",
                status="planned",
                visibility="client",
            ),
            "blank TreatmentAction title",
        )
        _expect_integrity_failure(
            TreatmentOutcome(
                treatment_plan_id=plan_id,
                car_id=car_id,
                recorded_by_user_id=advisor_id,
                recording_key="invalid-outcome-probe",
                progression_direction="fixed",
                summary="Invalid outcome direction probe",
                visibility="client",
                provenance_kind="professional_observation",
                provenance_data={"observation_source": "road_test"},
                observed_at=datetime(2026, 9, 2, 13, 0, 0),
            ),
            "invalid TreatmentOutcome progression",
        )
        _expect_integrity_failure(
            TreatmentOutcome(
                treatment_plan_id=plan_id,
                car_id=car_id,
                recorded_by_user_id=advisor_id,
                recording_key="unsafe-provenance-probe",
                progression_direction="improving",
                summary="Unsafe provenance probe",
                visibility="client",
                provenance_kind="insufficient_evidence",
                observed_at=datetime(2026, 9, 2, 13, 1, 0),
            ),
            "insufficient evidence claiming improvement",
        )

        print(
            "Wave 2.3C PostgreSQL negative verifier passed: canonical action/outcome "
            "constraints, authority, evidence scope, atomic rollback and no-history-synthesis are intact."
        )


if __name__ == "__main__":
    main()
