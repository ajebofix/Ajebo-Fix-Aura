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


def _user(*, token: str, role: str, code: int) -> User:
    user = User(
        name=f"Wave 2.3C {role} {code}",
        email=f"wave23c-{code}-{token}@example.com",
        phone_number=f"+23489{int(token[:6], 16) % 1000000:06d}{code:02d}",
        role=role,
        is_active=True,
    )
    user.set_password("Password123")
    db.session.add(user)
    db.session.flush()
    return user


def _evidence(*, car_id: int, uploader_id: int, token: str) -> VehicleEvidence:
    evidence = VehicleEvidence(
        car_id=car_id,
        uploaded_by_user_id=uploader_id,
        evidence_type="image",
        purpose="treatment_evidence",
        source_channel="web",
        visibility="client",
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
        reviewed_at=datetime(2026, 9, 1, 18, 10, 0),
        review_reason_code="advisor_verified",
    )
    db.session.add(evidence)
    db.session.commit()
    return evidence


def _raw_event(
    *,
    car_id: int,
    ownership_id: int,
    advisor_id: int,
    event_type: str,
    subject_type: str,
    subject_id: int,
    progression_direction: str | None,
    previous_state: str | None,
    new_state: str | None,
) -> VehicleEvent:
    now = datetime(2026, 9, 2, 14, 0, 0)
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
        actor_user_id=advisor_id,
        actor_authority="advisor",
        visibility="client",
        previous_state=previous_state,
        new_state=new_state,
        progression_direction=progression_direction,
        evidence_refs=[],
        correction_of_event_id=None,
        created_by=advisor_id,
        is_deleted=False,
    )


def _must_reject(row, label: str) -> None:
    db.session.add(row)
    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        return
    raise SystemExit(f"PostgreSQL accepted invalid Wave 2.3C row: {label}")


def main() -> None:
    with app.app_context():
        if db.engine.dialect.name != "postgresql":
            raise SystemExit("This verifier must run against PostgreSQL")

        inspector = inspect(db.engine)
        tables = set(inspector.get_table_names())
        missing_tables = {"treatment_actions", "treatment_outcomes"} - tables
        if missing_tables:
            raise SystemExit(f"Missing Wave 2.3C tables: {sorted(missing_tables)}")

        checks = {
            item["name"]
            for item in inspector.get_check_constraints("vehicle_events")
        }
        required_checks = {
            "ck_vehicle_events_canonical_subject_event",
            "ck_vehicle_events_treatment_plan_contract",
            "ck_vehicle_events_treatment_action_contract",
        }
        missing_checks = required_checks - checks
        if missing_checks:
            raise SystemExit(f"Missing Wave 2.3C checks: {sorted(missing_checks)}")

        token = uuid.uuid4().hex[:10]
        owner = _user(token=token, role="user", code=1)
        advisor = _user(token=token, role="admin", code=2)
        unrelated = _user(token=token, role="user", code=3)

        car = Car(
            brand="Mercedes-Benz",
            model="GLE 450",
            year=2024,
            vin=f"W1N23C{token.upper()}",
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
        db.session.add_all(
            [
                ownership,
                CarOwnership(
                    user_id=unrelated.id,
                    car_id=other_car.id,
                    plate_number=f"X-{token[:3].upper()}-LA",
                    mileage_at_transfer=18000,
                    is_active=True,
                ),
            ]
        )
        db.session.flush()

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
            client_summary="Verified professional care pathway.",
            status="authorized",
        )
        db.session.add_all([legacy, plan])
        db.session.commit()

        # Merely upgrading must never synthesize intervention/outcome history.
        if TreatmentAction.query.count() or TreatmentOutcome.query.count():
            raise SystemExit("Wave 2.3C synthesized historical treatment history")

        action = TreatmentActionLifecycleService.create(
            plan_id=plan.id,
            actor_user_id=advisor.id,
            creation_key="verify-action-1",
            title="Front braking-system intervention",
            client_summary="Carry out the authorized braking-system intervention.",
            internal_instructions="VERIFIER PRIVATE DETAIL",
            visibility="client",
            occurred_at=datetime(2026, 9, 1, 18, 20, 0),
        )
        db.session.commit()

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

        if db.session.get(TreatmentPlan, plan.id).status != "in_progress":
            raise SystemExit("Treatment Action completion incorrectly completed parent plan")
        if db.session.get(TreatmentAction, action.id).status != "completed":
            raise SystemExit("Treatment Action completion did not persist")

        evidence = _evidence(car_id=car.id, uploader_id=owner.id, token=token)
        outcome = TreatmentOutcomeRecordingService.record(
            plan_id=plan.id,
            actor_user_id=advisor.id,
            recording_key="verify-outcome-1",
            progression_direction="improving",
            summary="Accepted evidence supports an improving observation.",
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
        if len(outcome_events) != 1 or outcome_events[0].progression_direction != "improving":
            raise SystemExit("Canonical treatment.outcome_recorded event is incorrect")

        foreign = _evidence(car_id=other_car.id, uploader_id=unrelated.id, token=f"x{token}")
        try:
            TreatmentOutcomeRecordingService.record(
                plan_id=plan.id,
                actor_user_id=advisor.id,
                recording_key="cross-vehicle-outcome",
                progression_direction="stable",
                summary="Cross-vehicle provenance must fail.",
                provenance_kind="reviewed_evidence",
                evidence_ids=[foreign.id],
            )
        except TreatmentOutcomeProvenanceError:
            db.session.rollback()
        else:
            raise SystemExit("Cross-vehicle evidence supported a Treatment Outcome")
        if TreatmentOutcome.query.filter_by(recording_key="cross-vehicle-outcome").count():
            raise SystemExit("Failed cross-vehicle outcome left a half-created row")

        car_id = car.id
        ownership_id = ownership.id
        advisor_id = advisor.id
        action_id = action.id
        plan_id = plan.id

        probes = [
            (
                "plan event paired to action subject",
                _raw_event(
                    car_id=car_id,
                    ownership_id=ownership_id,
                    advisor_id=advisor_id,
                    event_type="treatment.completed",
                    subject_type="treatment_action",
                    subject_id=action_id,
                    progression_direction="not_applicable",
                    previous_state="in_progress",
                    new_state="completed",
                ),
            ),
            (
                "action created with previous state",
                _raw_event(
                    car_id=car_id,
                    ownership_id=ownership_id,
                    advisor_id=advisor_id,
                    event_type="treatment_action.created",
                    subject_type="treatment_action",
                    subject_id=action_id,
                    progression_direction="not_applicable",
                    previous_state="planned",
                    new_state="planned",
                ),
            ),
            (
                "action started from planned",
                _raw_event(
                    car_id=car_id,
                    ownership_id=ownership_id,
                    advisor_id=advisor_id,
                    event_type="treatment_action.started",
                    subject_type="treatment_action",
                    subject_id=action_id,
                    progression_direction="not_applicable",
                    previous_state="planned",
                    new_state="in_progress",
                ),
            ),
            (
                "action transition NULL progression",
                _raw_event(
                    car_id=car_id,
                    ownership_id=ownership_id,
                    advisor_id=advisor_id,
                    event_type="treatment_action.started",
                    subject_type="treatment_action",
                    subject_id=action_id,
                    progression_direction=None,
                    previous_state="scheduled",
                    new_state="in_progress",
                ),
            ),
            (
                "action transition NULL new state",
                _raw_event(
                    car_id=car_id,
                    ownership_id=ownership_id,
                    advisor_id=advisor_id,
                    event_type="treatment_action.completed",
                    subject_type="treatment_action",
                    subject_id=action_id,
                    progression_direction="not_applicable",
                    previous_state="in_progress",
                    new_state=None,
                ),
            ),
            (
                "outcome with not_applicable progression",
                _raw_event(
                    car_id=car_id,
                    ownership_id=ownership_id,
                    advisor_id=advisor_id,
                    event_type="treatment.outcome_recorded",
                    subject_type="treatment_plan",
                    subject_id=plan_id,
                    progression_direction="not_applicable",
                    previous_state="in_progress",
                    new_state="in_progress",
                ),
            ),
            (
                "outcome mutating plan state",
                _raw_event(
                    car_id=car_id,
                    ownership_id=ownership_id,
                    advisor_id=advisor_id,
                    event_type="treatment.outcome_recorded",
                    subject_type="treatment_plan",
                    subject_id=plan_id,
                    progression_direction="stable",
                    previous_state="in_progress",
                    new_state="completed",
                ),
            ),
            (
                "outcome NULL progression",
                _raw_event(
                    car_id=car_id,
                    ownership_id=ownership_id,
                    advisor_id=advisor_id,
                    event_type="treatment.outcome_recorded",
                    subject_type="treatment_plan",
                    subject_id=plan_id,
                    progression_direction=None,
                    previous_state="in_progress",
                    new_state="in_progress",
                ),
            ),
        ]
        for label, row in probes:
            _must_reject(row, label)

        _must_reject(
            TreatmentAction(
                treatment_plan_id=plan_id,
                car_id=car_id,
                created_by_user_id=advisor_id,
                creation_key="bad-status",
                title="Bad status",
                status="diagnosed",
                visibility="client",
            ),
            "invalid TreatmentAction status",
        )
        _must_reject(
            TreatmentAction(
                treatment_plan_id=plan_id,
                car_id=car_id,
                created_by_user_id=advisor_id,
                creation_key="blank-title",
                title="   ",
                status="planned",
                visibility="client",
            ),
            "blank TreatmentAction title",
        )
        _must_reject(
            TreatmentOutcome(
                treatment_plan_id=plan_id,
                car_id=car_id,
                recorded_by_user_id=advisor_id,
                recording_key="bad-direction",
                progression_direction="fixed",
                summary="Bad direction",
                visibility="client",
                provenance_kind="professional_observation",
                provenance_data={"observation_source": "road_test"},
                observed_at=datetime(2026, 9, 2, 13, 0, 0),
            ),
            "invalid TreatmentOutcome progression",
        )
        _must_reject(
            TreatmentOutcome(
                treatment_plan_id=plan_id,
                car_id=car_id,
                recorded_by_user_id=advisor_id,
                recording_key="unsafe-provenance",
                progression_direction="improving",
                summary="Unsafe provenance",
                visibility="client",
                provenance_kind="insufficient_evidence",
                observed_at=datetime(2026, 9, 2, 13, 1, 0),
            ),
            "insufficient evidence claiming improvement",
        )

        print(
            "Wave 2.3C PostgreSQL negative verifier passed: malformed canonical events, "
            "unsafe domain rows, cross-vehicle provenance and synthetic history are rejected."
        )


if __name__ == "__main__":
    main()
