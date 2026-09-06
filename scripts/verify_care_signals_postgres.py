"""Verify Aura Wave 2.4C care-signal contracts on PostgreSQL."""

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
from models import Car, CarOwnership, User, VehicleEvent, VehicleHealthAlert  # noqa: E402
from services.care_signal_event_emission import (  # noqa: E402
    CareSignalEventAuthorityError,
    emit_care_signal_event,
)
from services.care_signal_lifecycle import (  # noqa: E402
    CareSignalAuthorityError,
    CareSignalLifecycleService,
)


def _user(*, token: str, role: str, code: int) -> User:
    user = User(
        name=f"Wave 2.4C {role} {code}",
        email=f"wave24c-{code}-{token}@example.com",
        phone_number=f"+23487{int(token[:6], 16) % 1000000:06d}{code:02d}",
        role=role,
        is_active=True,
    )
    user.set_password("Password123")
    db.session.add(user)
    db.session.flush()
    return user


def _raw_care_event(
    *,
    car_id: int,
    ownership_id: int,
    signal_id: int,
    event_type: str = "care_signal.raised",
    actor_type: str = "system",
    actor_user_id: int | None = None,
    actor_authority: str = "system",
    created_by: int | None = None,
    visibility: str = "client",
    previous_state: str | None = None,
    new_state: str | None = "new",
    progression_direction: str = "not_applicable",
) -> VehicleEvent:
    now = datetime(2026, 9, 6, 12, 0, 0)
    return VehicleEvent(
        car_id=car_id,
        ownership_id=ownership_id,
        event_type=event_type,
        severity="high",
        event_date=now.date(),
        title="Wave 2.4C PostgreSQL negative probe",
        description=None,
        mileage=27000,
        source="verify.care_signals",
        data={
            "alert_type": "low_health_status",
            "severity": "high",
            "source_classification": "negative_probe",
        },
        fingerprint=hashlib.sha256(uuid.uuid4().bytes).hexdigest(),
        schema_version=1,
        occurred_at=now,
        recorded_at=now,
        subject_type="vehicle_health_alert",
        subject_id=signal_id,
        actor_type=actor_type,
        actor_user_id=actor_user_id,
        actor_authority=actor_authority,
        visibility=visibility,
        previous_state=previous_state,
        new_state=new_state,
        progression_direction=progression_direction,
        evidence_refs=[],
        correction_of_event_id=None,
        created_by=created_by,
        is_deleted=False,
    )


def _must_reject_event(row: VehicleEvent, label: str) -> None:
    db.session.add(row)
    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        return
    raise SystemExit(f"PostgreSQL accepted invalid Wave 2.4C event: {label}")


def _must_reject_alert(row: VehicleHealthAlert, label: str) -> None:
    db.session.add(row)
    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        return
    raise SystemExit(f"PostgreSQL accepted invalid Wave 2.4C alert: {label}")


def main() -> None:
    with app.app_context():
        if db.engine.dialect.name != "postgresql":
            raise SystemExit("This verifier must run against PostgreSQL")

        inspector = inspect(db.engine)
        event_checks = {
            item["name"]
            for item in inspector.get_check_constraints("vehicle_events")
        }
        required_event_checks = {
            "ck_vehicle_events_canonical_subject_event",
            "ck_vehicle_events_care_signal_contract",
            "ck_vehicle_events_system_actor_scope",
        }
        missing_event_checks = required_event_checks - event_checks
        if missing_event_checks:
            raise SystemExit(
                f"Missing Wave 2.4C event checks: {sorted(missing_event_checks)}"
            )

        alert_checks = {
            item["name"]
            for item in inspector.get_check_constraints("vehicle_health_alerts")
        }
        if "ck_vehicle_health_alert_state_contract" not in alert_checks:
            raise SystemExit("Missing Wave 2.4C alert-state CHECK")

        alert_indexes = {
            item["name"]: item
            for item in inspector.get_indexes("vehicle_health_alerts")
        }
        active_index = alert_indexes.get("uq_vehicle_health_alert_active_occurrence")
        if active_index is None or not active_index.get("unique"):
            raise SystemExit("Missing recurrence-safe active-occurrence unique index")

        event_columns = {
            column["name"]: column
            for column in inspector.get_columns("vehicle_events")
        }
        if not event_columns["created_by"]["nullable"]:
            raise SystemExit("Wave 2.4C did not permit null created_by for system events")

        # Migration must never manufacture canonical care-signal history.
        if VehicleEvent.query.filter_by(subject_type="vehicle_health_alert").count():
            raise SystemExit("Wave 2.4C synthesized care-signal canonical history")

        token = uuid.uuid4().hex[:10]
        owner = _user(token=token, role="user", code=1)
        advisor = _user(token=token, role="admin", code=2)
        car = Car(
            brand="Mercedes-Benz",
            model="GLE 450",
            year=2024,
            vin=f"W1N24C{token.upper()}",
            current_mileage=27000,
        )
        db.session.add(car)
        db.session.flush()
        ownership = CarOwnership(
            user_id=owner.id,
            car_id=car.id,
            plate_number=f"C-{token[:3].upper()}-LA",
            mileage_at_transfer=26000,
            is_active=True,
        )
        db.session.add(ownership)
        db.session.commit()

        first = CareSignalLifecycleService.raise_signal(
            car_id=car.id,
            alert_type="low_health_status",
            severity="high",
            message=(
                "Vehicle health status indicates elevated risk. "
                "An advisor review is recommended."
            ),
            source_classification="deterministic_rule:verifier",
            actor_type="system",
            actor_user_id=None,
            occurred_at=datetime(2026, 9, 6, 8, 0, 0),
        )
        db.session.commit()

        raised_event = VehicleEvent.query.filter_by(
            subject_type="vehicle_health_alert",
            subject_id=first.id,
            event_type="care_signal.raised",
        ).one()
        if raised_event.actor_type != "system":
            raise SystemExit("Care-signal raise did not preserve system actor type")
        if raised_event.actor_user_id is not None or raised_event.created_by is not None:
            raise SystemExit("System care-signal event used a fake human identity")
        if raised_event.actor_authority != "system":
            raise SystemExit("System care-signal event has incorrect authority")
        if raised_event.visibility != "client":
            raise SystemExit("Care-signal raised visibility is incorrect")
        if raised_event.progression_direction != "not_applicable":
            raise SystemExit("Care-signal event was misclassified as health progression")
        if first.message in str(raised_event.data):
            raise SystemExit("Care-signal message leaked into canonical event payload")

        replay = CareSignalLifecycleService.raise_signal(
            car_id=car.id,
            alert_type="low_health_status",
            severity="high",
            message=(
                "Vehicle health status indicates elevated risk. "
                "An advisor review is recommended."
            ),
            source_classification="deterministic_rule:verifier",
            actor_type="system",
            actor_user_id=None,
            occurred_at=datetime(2026, 9, 6, 8, 30, 0),
        )
        db.session.commit()
        if replay.id != first.id:
            raise SystemExit("Active deterministic care-signal replay was not idempotent")
        if VehicleEvent.query.filter_by(
            subject_type="vehicle_health_alert",
            subject_id=first.id,
        ).count() != 1:
            raise SystemExit("Active care-signal replay created duplicate canonical history")

        CareSignalLifecycleService.acknowledge(
            signal_id=first.id,
            actor_user_id=advisor.id,
            occurred_at=datetime(2026, 9, 6, 9, 0, 0),
        )
        db.session.commit()
        CareSignalLifecycleService.resolve(
            signal_id=first.id,
            actor_type="system",
            actor_user_id=None,
            source_classification="deterministic_rule:verifier",
            occurred_at=datetime(2026, 9, 6, 10, 0, 0),
        )
        db.session.commit()

        first = db.session.get(VehicleHealthAlert, first.id)
        if first is None or first.status != "resolved" or first.is_active:
            raise SystemExit("Care-signal resolution state was not persisted correctly")

        second = CareSignalLifecycleService.raise_signal(
            car_id=car.id,
            alert_type="low_health_status",
            severity="high",
            message=(
                "Vehicle health status indicates elevated risk. "
                "An advisor review is recommended."
            ),
            source_classification="deterministic_rule:verifier",
            actor_type="system",
            actor_user_id=None,
            occurred_at=datetime(2026, 9, 7, 8, 0, 0),
        )
        db.session.commit()
        if second.id == first.id:
            raise SystemExit("Resolved care-signal occurrence was silently reopened")
        occurrences = VehicleHealthAlert.query.filter_by(
            car_id=car.id,
            ownership_id=ownership.id,
            alert_type="low_health_status",
        ).count()
        if occurrences != 2:
            raise SystemExit("Recurrence did not preserve both care-signal occurrences")

        duplicate_active = VehicleHealthAlert(
            car_id=car.id,
            ownership_id=ownership.id,
            alert_type="low_health_status",
            severity="high",
            status="new",
            message="Duplicate active occurrence bypass probe.",
            is_active=True,
            created_at=datetime(2026, 9, 7, 9, 0, 0),
        )
        _must_reject_alert(duplicate_active, "duplicate active occurrence")

        malformed_state = VehicleHealthAlert(
            car_id=car.id,
            ownership_id=ownership.id,
            alert_type="declining_health_trajectory",
            severity="moderate",
            status="resolved",
            message="Malformed resolved storage probe.",
            is_active=True,
            created_at=datetime(2026, 9, 7, 9, 0, 0),
            resolved_at=datetime(2026, 9, 7, 9, 5, 0),
        )
        _must_reject_alert(malformed_state, "resolved row left active")

        car_id = car.id
        ownership_id = ownership.id
        signal_id = second.id
        probes = [
            (
                "system acknowledgement",
                _raw_care_event(
                    car_id=car_id,
                    ownership_id=ownership_id,
                    signal_id=signal_id,
                    event_type="care_signal.acknowledged",
                    actor_type="system",
                    actor_user_id=None,
                    actor_authority="system",
                    created_by=None,
                    visibility="advisor",
                    previous_state="new",
                    new_state="acknowledged",
                ),
            ),
            (
                "provider raise",
                _raw_care_event(
                    car_id=car_id,
                    ownership_id=ownership_id,
                    signal_id=signal_id,
                    actor_type="provider",
                    actor_user_id=None,
                    actor_authority="provider",
                    created_by=None,
                ),
            ),
            (
                "system impersonating human",
                _raw_care_event(
                    car_id=car_id,
                    ownership_id=ownership_id,
                    signal_id=signal_id,
                    actor_type="system",
                    actor_user_id=advisor.id,
                    actor_authority="system",
                    created_by=advisor.id,
                ),
            ),
            (
                "wrong raised visibility",
                _raw_care_event(
                    car_id=car_id,
                    ownership_id=ownership_id,
                    signal_id=signal_id,
                    visibility="advisor",
                ),
            ),
            (
                "illegal resolved transition",
                _raw_care_event(
                    car_id=car_id,
                    ownership_id=ownership_id,
                    signal_id=signal_id,
                    event_type="care_signal.resolved",
                    previous_state="resolved",
                    new_state="resolved",
                ),
            ),
            (
                "health progression on care signal",
                _raw_care_event(
                    car_id=car_id,
                    ownership_id=ownership_id,
                    signal_id=signal_id,
                    progression_direction="deteriorating",
                ),
            ),
        ]
        for label, probe in probes:
            _must_reject_event(probe, label)

        try:
            emit_care_signal_event(
                car_id=car.id,
                signal_id=second.id,
                event_type="care_signal.resolved",
                actor_type="provider",
                actor_user_id=None,
                occurred_at=datetime(2026, 9, 7, 11, 0, 0),
                previous_state="new",
                new_state="resolved",
                alert_type=second.alert_type,
                severity=second.severity,
                source_classification="negative_probe",
                idempotency_key=f"provider-negative:{second.id}",
            )
        except CareSignalEventAuthorityError:
            db.session.rollback()
        else:
            raise SystemExit("Provider actor passed the canonical care-signal emitter")

        try:
            CareSignalLifecycleService.raise_signal(
                car_id=car.id,
                alert_type="elevated_risk_indicator",
                severity="moderate",
                message="Pseudo-predictive rule must stay disabled.",
                source_classification="deterministic_rule:negative_probe",
                actor_type="system",
                actor_user_id=None,
                occurred_at=datetime(2026, 9, 7, 12, 0, 0),
            )
        except CareSignalAuthorityError:
            db.session.rollback()
        else:
            raise SystemExit("Unapproved system care-signal type was accepted")

        print("Wave 2.4C PostgreSQL care-signal verification passed.")


if __name__ == "__main__":
    main()
