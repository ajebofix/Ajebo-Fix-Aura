"""Verify Aura Wave 2.3B Treatment Plan lifecycle on PostgreSQL."""

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
from extensions import db  # noqa: E402
from models import (  # noqa: E402
    Car,
    CarFault,
    CarOwnership,
    Consultation,
    TreatmentPlan,
    User,
    VehicleAssessment,
    VehicleEvent,
)
from services.assessment_lifecycle import AssessmentLifecycleService  # noqa: E402
from services.treatment_plan_lifecycle import (  # noqa: E402
    TreatmentPlanAuthorityError,
    TreatmentPlanLifecycleService,
)
from services.vehicle_intelligence import calculate_vehicle_health  # noqa: E402


def _raw_event(
    *,
    car_id: int,
    ownership_id: int,
    actor_user_id: int,
    event_type: str,
    subject_type: str | None,
    subject_id: int | None,
    progression_direction: str | None,
    previous_state: str | None,
    new_state: str | None,
) -> VehicleEvent:
    now = datetime(2026, 8, 29, 12, 0, 0)
    return VehicleEvent(
        car_id=car_id,
        ownership_id=ownership_id,
        event_type=event_type,
        severity="low",
        event_date=now.date(),
        title="PostgreSQL Treatment Plan contract probe",
        description=None,
        mileage=26000,
        source="verify.treatment_events",
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


def _expect_constraint_failure(row: VehicleEvent, label: str) -> None:
    db.session.add(row)
    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        return
    raise SystemExit(f"PostgreSQL accepted invalid Treatment Plan event: {label}")


def _treatment_events(plan_id: int) -> list[VehicleEvent]:
    return (
        VehicleEvent.query.filter_by(
            subject_type="treatment_plan",
            subject_id=plan_id,
        )
        .order_by(VehicleEvent.occurred_at.asc(), VehicleEvent.id.asc())
        .all()
    )


def main() -> None:
    with app.app_context():
        if db.engine.dialect.name != "postgresql":
            raise SystemExit("This verifier must run against PostgreSQL")

        inspector = inspect(db.engine)
        checks = {
            item["name"]
            for item in inspector.get_check_constraints("vehicle_events")
        }
        required_checks = {
            "ck_vehicle_events_canonical_subject_event",
            "ck_vehicle_events_evidence_contract",
            "ck_vehicle_events_consultation_contract",
            "ck_vehicle_events_assessment_contract",
            "ck_vehicle_events_treatment_plan_contract",
        }
        missing = required_checks - checks
        if missing:
            raise SystemExit(
                "Wave 2.3B VehicleEvent PostgreSQL CHECK constraints are missing: "
                f"{sorted(missing)}"
            )

        treatment_indexes = {
            item["name"]
            for item in inspector.get_indexes("treatment_plans")
        }
        if "uq_treatment_plans_assessment_id" not in treatment_indexes:
            raise SystemExit(
                "Wave 2.3B one-plan-per-assessment unique index is missing"
            )

        token = uuid.uuid4().hex[:10]
        now = datetime(2026, 8, 29, 10, 0, 0)
        owner = User(
            name="Treatment PostgreSQL Owner",
            email=f"treatment-pg-owner-{token}@example.com",
            phone_number=f"+23481{token[:8]}",
            role="user",
            is_active=True,
            email_verified_at=now,
        )
        owner.set_password("Password123")
        advisor = User(
            name="Treatment PostgreSQL Advisor",
            email=f"treatment-pg-advisor-{token}@example.com",
            phone_number=f"+23482{token[:8]}",
            role="admin",
            is_active=True,
            email_verified_at=now,
        )
        advisor.set_password("Password123")
        unrelated = User(
            name="Treatment PostgreSQL Unrelated",
            email=f"treatment-pg-unrelated-{token}@example.com",
            phone_number=f"+23483{token[:8]}",
            role="user",
            is_active=True,
            email_verified_at=now,
        )
        unrelated.set_password("Password123")
        db.session.add_all([owner, advisor, unrelated])
        db.session.flush()

        car = Car(
            brand="Mercedes-Benz",
            model="GLE 450",
            year=2024,
            vin=f"W1NTPPG{token.upper()}",
            engine_number=f"M256-{token[:6]}",
            engine_type="M256",
            transmission_type="9G-TRONIC",
            current_mileage=26000,
        )
        db.session.add(car)
        db.session.flush()
        ownership = CarOwnership(
            user_id=owner.id,
            car_id=car.id,
            plate_number=f"TP-{token[:3].upper()}-LA",
            mileage_at_transfer=25000,
            is_active=True,
        )
        db.session.add(ownership)
        db.session.flush()
        consultation = Consultation(
            car_id=car.id,
            ownership_id=ownership.id,
            advisor_id=advisor.id,
            client_id=owner.id,
            status="in_progress",
            scheduled_for=datetime(2026, 8, 29, 10, 5, 0),
            started_at=datetime(2026, 8, 29, 10, 6, 0),
        )
        db.session.add(consultation)
        db.session.commit()

        # Prove the real Wave 2.3B coordinator boundary: assessment finalization,
        # compatibility-plan creation, canonical conversion to proposed and both
        # canonical event families commit together.
        assessment = AssessmentLifecycleService.start_or_resume(
            consultation_id=consultation.id,
            actor_user_id=advisor.id,
            occurred_at=datetime(2026, 8, 29, 10, 10, 0),
            source="verify.treatment_assessment_start",
        )
        AssessmentLifecycleService.save_draft(
            assessment_id=assessment.id,
            actor_user_id=advisor.id,
            scalar_updates={
                "engine_status": "stable",
                "transmission_status": "stable",
                "suspension_status": "attention",
                "electrical_status": "stable",
                "cooling_status": "stable",
                "professional_recommendation": (
                    "INTERNAL VERIFIER DETAIL: replace only after advisor coordination."
                ),
            },
        )
        AssessmentLifecycleService.finalize(
            assessment_id=assessment.id,
            actor_user_id=advisor.id,
            finalized_at=datetime(2026, 8, 29, 10, 20, 0),
            source="verify.treatment_assessment_finalize",
        )
        plan = TreatmentPlanLifecycleService.canonicalize_new_assessment_plan(
            assessment_id=assessment.id,
            actor_user_id=advisor.id,
            occurred_at=datetime(2026, 8, 29, 10, 20, 0),
            source="verify.treatment_assessment_finalize",
        )
        db.session.commit()

        assessment_id = assessment.id
        plan_id = plan.id
        car_id = car.id
        ownership_id = ownership.id
        owner_id = owner.id
        advisor_id = advisor.id

        persisted_assessment = db.session.get(VehicleAssessment, assessment_id)
        persisted_plan = db.session.get(TreatmentPlan, plan_id)
        if (
            persisted_assessment is None
            or persisted_assessment.status != "finalized"
            or not persisted_assessment.is_finalized
        ):
            raise SystemExit("Assessment finalization did not persist atomically")
        if persisted_plan is None or persisted_plan.status != "proposed":
            raise SystemExit("New assessment Treatment Plan was not persisted as proposed")
        if TreatmentPlan.query.filter_by(assessment_id=assessment_id).count() != 1:
            raise SystemExit("Assessment did not retain exactly one Treatment Plan")

        proposed_events = _treatment_events(plan_id)
        if [event.event_type for event in proposed_events] != ["treatment.proposed"]:
            raise SystemExit(
                "Unexpected proposal event chain: "
                f"{[event.event_type for event in proposed_events]!r}"
            )
        proposed = proposed_events[0]
        if proposed.previous_state is not None or proposed.new_state != "proposed":
            raise SystemExit("treatment.proposed persisted unsafe state semantics")
        if proposed.actor_authority != "advisor" or proposed.visibility != "client":
            raise SystemExit("treatment.proposed persisted unsafe authority/visibility")
        if "INTERNAL VERIFIER DETAIL" in str(proposed.data) or (
            "INTERNAL VERIFIER DETAIL" in (proposed.description or "")
        ):
            raise SystemExit("Advisor-only Treatment Plan content leaked into event")

        concern = CarFault(
            car_id=car_id,
            reported_by=owner_id,
            category="other",
            description="Verify treatment completion does not resolve this concern",
            status="monitoring",
        )
        db.session.add(concern)
        db.session.commit()
        concern_id = concern.id
        health_before = calculate_vehicle_health(car, ownership)

        # Explicit owner authorization, including a replay/double-submit.
        TreatmentPlanLifecycleService.authorize(
            plan_id=plan_id,
            actor_user_id=owner_id,
            occurred_at=datetime(2026, 8, 29, 10, 30, 0),
            source="verify.treatment_authorize",
            operation_key="owner-consent-1",
        )
        db.session.commit()
        TreatmentPlanLifecycleService.authorize(
            plan_id=plan_id,
            actor_user_id=owner_id,
            occurred_at=datetime(2026, 8, 29, 10, 31, 0),
            source="verify.treatment_authorize_retry",
            operation_key="owner-consent-retry",
        )
        db.session.commit()
        if VehicleEvent.query.filter_by(
            subject_type="treatment_plan",
            subject_id=plan_id,
            event_type="treatment.authorized",
        ).count() != 1:
            raise SystemExit("Owner authorization replay created a duplicate event")

        # An unrelated account must not gain plan authority.
        try:
            TreatmentPlanLifecycleService.defer(
                plan_id=plan_id,
                actor_user_id=unrelated.id,
                occurred_at=datetime(2026, 8, 29, 10, 32, 0),
            )
        except TreatmentPlanAuthorityError:
            db.session.rollback()
        else:
            raise SystemExit("Unrelated user was able to mutate Treatment Plan")

        TreatmentPlanLifecycleService.schedule(
            plan_id=plan_id,
            actor_user_id=advisor_id,
            occurred_at=datetime(2026, 8, 29, 10, 40, 0),
            source="verify.treatment_schedule",
            operation_key="schedule-1",
        )
        db.session.commit()
        TreatmentPlanLifecycleService.start(
            plan_id=plan_id,
            actor_user_id=advisor_id,
            occurred_at=datetime(2026, 8, 29, 10, 50, 0),
            source="verify.treatment_start",
            operation_key="start-1",
        )
        db.session.commit()
        TreatmentPlanLifecycleService.start_monitoring(
            plan_id=plan_id,
            actor_user_id=advisor_id,
            occurred_at=datetime(2026, 8, 29, 11, 0, 0),
            source="verify.treatment_monitor",
            operation_key="monitor-1",
        )
        db.session.commit()
        TreatmentPlanLifecycleService.complete(
            plan_id=plan_id,
            actor_user_id=advisor_id,
            occurred_at=datetime(2026, 8, 29, 11, 10, 0),
            source="verify.treatment_complete",
            operation_key="complete-1",
        )
        db.session.commit()

        events = _treatment_events(plan_id)
        expected_types = [
            "treatment.proposed",
            "treatment.authorized",
            "treatment.scheduled",
            "treatment.started",
            "treatment.monitoring_started",
            "treatment.completed",
        ]
        if [event.event_type for event in events] != expected_types:
            raise SystemExit(
                "Unexpected Treatment Plan event chain: "
                f"{[event.event_type for event in events]!r}"
            )
        expected_transitions = [
            (None, "proposed"),
            ("proposed", "authorized"),
            ("authorized", "scheduled"),
            ("scheduled", "in_progress"),
            ("in_progress", "monitoring"),
            ("monitoring", "completed"),
        ]
        if [(e.previous_state, e.new_state) for e in events] != expected_transitions:
            raise SystemExit("Treatment Plan canonical transition chain is incorrect")
        if [event.actor_authority for event in events] != [
            "advisor",
            "owner",
            "advisor",
            "advisor",
            "advisor",
            "advisor",
        ]:
            raise SystemExit("Treatment Plan event authority chain is incorrect")
        if any(event.progression_direction != "not_applicable" for event in events):
            raise SystemExit("Treatment workflow event changed vehicle-health progression")
        if any(event.visibility != "client" for event in events):
            raise SystemExit("Expected client-safe Treatment Plan event visibility")
        if any("INTERNAL VERIFIER DETAIL" in str(event.data) for event in events):
            raise SystemExit("Internal Treatment Plan detail leaked into canonical payload")

        final_plan = db.session.get(TreatmentPlan, plan_id)
        final_concern = db.session.get(CarFault, concern_id)
        if final_plan is None or final_plan.status != "completed":
            raise SystemExit("Treatment Plan did not persist completed state")
        if final_concern is None or final_concern.status != "monitoring":
            raise SystemExit("Treatment completion incorrectly resolved Reported Concern")
        health_after = calculate_vehicle_health(car, ownership)
        if health_after != health_before:
            raise SystemExit(
                "Treatment completion changed calculated Vehicle Health without its own contract"
            )

        # Database-level one-plan-per-assessment protection.
        duplicate = TreatmentPlan(
            car_id=car_id,
            consultation_id=consultation.id,
            assessment_id=assessment_id,
            advisor_id=advisor_id,
            title="Duplicate Treatment Plan probe",
            status="proposed",
        )
        db.session.add(duplicate)
        try:
            db.session.commit()
        except IntegrityError:
            db.session.rollback()
        else:
            raise SystemExit("PostgreSQL accepted a second plan for one assessment")

        # A historical approved plan may take a real future transition without
        # fabricated proposal/authorization history.
        legacy_consultation = Consultation(
            car_id=car_id,
            ownership_id=ownership_id,
            advisor_id=advisor_id,
            client_id=owner_id,
            status="completed",
            scheduled_for=datetime(2026, 8, 28, 9, 0, 0),
            started_at=datetime(2026, 8, 28, 9, 1, 0),
            completed_at=datetime(2026, 8, 28, 9, 30, 0),
        )
        db.session.add(legacy_consultation)
        db.session.flush()
        legacy_assessment = VehicleAssessment(
            car_id=car_id,
            consultation_id=legacy_consultation.id,
            vin=car.vin,
            mileage_at_assessment=25500,
            status="finalized",
            is_finalized=True,
            engine_status="stable",
            transmission_status="stable",
            suspension_status="attention",
            electrical_status="stable",
            cooling_status="stable",
            finalized_at=datetime(2026, 8, 28, 9, 25, 0),
            finalized_by=advisor_id,
        )
        db.session.add(legacy_assessment)
        db.session.flush()
        legacy_plan = TreatmentPlan(
            car_id=car_id,
            consultation_id=legacy_consultation.id,
            assessment_id=legacy_assessment.id,
            advisor_id=advisor_id,
            title="Historical compatibility plan",
            status="approved",
        )
        db.session.add(legacy_plan)
        db.session.commit()
        legacy_plan_id = legacy_plan.id

        TreatmentPlanLifecycleService.start(
            plan_id=legacy_plan_id,
            actor_user_id=advisor_id,
            occurred_at=datetime(2026, 8, 29, 11, 20, 0),
            source="verify.legacy_treatment_start",
            operation_key="legacy-start-1",
        )
        db.session.commit()
        legacy_events = _treatment_events(legacy_plan_id)
        if len(legacy_events) != 1:
            raise SystemExit("Legacy approved plan acquired synthetic history")
        if (
            legacy_events[0].event_type != "treatment.started"
            or legacy_events[0].previous_state != "approved"
            or legacy_events[0].new_state != "in_progress"
        ):
            raise SystemExit("Legacy approved plan did not preserve real source state")

        # PostgreSQL must fail closed on invalid subject/state/progression rows.
        _expect_constraint_failure(
            _raw_event(
                car_id=car_id,
                ownership_id=ownership_id,
                actor_user_id=advisor_id,
                event_type="treatment.started",
                subject_type="consultation",
                subject_id=plan_id,
                progression_direction="not_applicable",
                previous_state="scheduled",
                new_state="in_progress",
            ),
            "treatment event paired with consultation subject",
        )
        _expect_constraint_failure(
            _raw_event(
                car_id=car_id,
                ownership_id=ownership_id,
                actor_user_id=advisor_id,
                event_type="treatment.started",
                subject_type="treatment_plan",
                subject_id=plan_id,
                progression_direction="not_applicable",
                previous_state=None,
                new_state="in_progress",
            ),
            "treatment started with NULL previous state",
        )
        _expect_constraint_failure(
            _raw_event(
                car_id=car_id,
                ownership_id=ownership_id,
                actor_user_id=advisor_id,
                event_type="treatment.completed",
                subject_type="treatment_plan",
                subject_id=plan_id,
                progression_direction="stable",
                previous_state="monitoring",
                new_state="completed",
            ),
            "treatment lifecycle event with mechanical progression",
        )
        _expect_constraint_failure(
            _raw_event(
                car_id=car_id,
                ownership_id=ownership_id,
                actor_user_id=advisor_id,
                event_type="treatment.escalated",
                subject_type="treatment_plan",
                subject_id=plan_id,
                progression_direction="not_applicable",
                previous_state="monitoring",
                new_state="completed",
            ),
            "treatment escalation that mutates plan state",
        )

        legacy_subjectless = _raw_event(
            car_id=car_id,
            ownership_id=ownership_id,
            actor_user_id=advisor_id,
            event_type="legacy.service_record",
            subject_type=None,
            subject_id=None,
            progression_direction=None,
            previous_state=None,
            new_state=None,
        )
        db.session.add(legacy_subjectless)
        db.session.commit()
        if legacy_subjectless.id is None:
            raise SystemExit("Legacy subject-less VehicleEvent compatibility was broken")

        print(
            "Wave 2.3B Treatment Plan lifecycle verified on PostgreSQL: "
            "assessment proposal -> owner authorization -> schedule -> start -> "
            "monitoring -> completion, with legacy compatibility, one-plan-per-assessment, "
            "client-safe canonical events and fail-closed PostgreSQL constraints."
        )


if __name__ == "__main__":
    main()
