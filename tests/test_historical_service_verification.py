from __future__ import annotations

from datetime import datetime
import hashlib

import pytest

from extensions import db
from models import Car, CarOwnership, User, VehicleEvent
from services.historical_service_verification import (
    HistoricalServiceVerificationAuthorityError,
    HistoricalServiceVerificationError,
    HistoricalServiceVerificationService,
)


def _user(*, suffix: int, role: str = "user") -> User:
    user = User(
        name=f"Historical review {role} {suffix}",
        email=f"historical-review-{role}-{suffix}@example.com",
        phone_number=f"+2348899{suffix:06d}",
        role=role,
        is_active=True,
    )
    user.set_password("Password123")
    db.session.add(user)
    db.session.flush()
    return user


def _vehicle(*, suffix: int):
    owner = _user(suffix=suffix)
    advisor = _user(suffix=suffix + 1000, role="admin")
    car = Car(
        brand="Mercedes-Benz",
        model="GLE 450",
        year=2021,
        vin=f"W1NVERIFY{suffix:08d}",
        current_mileage=64100,
        vehicle_identity_source="manual",
    )
    db.session.add(car)
    db.session.flush()
    ownership = CarOwnership(
        user_id=owner.id,
        car_id=car.id,
        plate_number=f"VR-{suffix:03d}-LA",
        mileage_at_transfer=0,
        is_active=True,
    )
    db.session.add(ownership)
    db.session.commit()
    return owner, advisor, car, ownership


def _service(
    *,
    car: Car,
    ownership: CarOwnership,
    advisor: User,
    historical: bool = True,
) -> VehicleEvent:
    occurred_at = datetime(2025, 11, 15, 9, 0, 0)
    token = f"verification|{car.id}|{historical}|{VehicleEvent.query.count()}"
    event = VehicleEvent(
        car_id=car.id,
        ownership_id=ownership.id,
        event_type="service",
        title="Routine service",
        description="Existing historical service evidence.",
        mileage=34000,
        source="admin_historical" if historical else "admin_current",
        data=(
            {
                "record_mode": "historical",
                "entered_by_role": "advisor",
                "information_source": "client_provided",
                "information_source_label": "Client-provided history",
                "verification_status": "unverified",
                "verification_status_label": "Unverified",
            }
            if historical
            else {"record_mode": "current", "entered_by_role": "advisor"}
        ),
        fingerprint=hashlib.sha256(token.encode()).hexdigest(),
        created_by=advisor.id,
        is_deleted=False,
        created_at=occurred_at,
        schema_version=1,
        occurred_at=occurred_at,
        recorded_at=occurred_at,
        subject_type="service_record",
        actor_type="user",
        actor_user_id=advisor.id,
        actor_authority="advisor",
        visibility="internal",
        progression_direction="not_applicable",
    )
    db.session.add(event)
    db.session.flush()
    event.subject_id = event.id
    db.session.commit()
    return event


def test_review_upgrades_existing_historical_service_and_preserves_fact(app):
    with app.app_context():
        _owner, advisor, car, ownership = _vehicle(suffix=1)
        event = _service(car=car, ownership=ownership, advisor=advisor)
        original = (event.title, event.description, event.mileage, event.created_at)
        reviewed_at = datetime(2026, 9, 13, 12, 0, 0)

        reviewed, changed = HistoricalServiceVerificationService.review(
            service_event_id=event.id,
            actor_user_id=advisor.id,
            information_source="client_provided",
            verification_status="advisor_confirmed",
            reviewed_at=reviewed_at,
        )
        db.session.commit()

        assert changed is True
        assert (reviewed.title, reviewed.description, reviewed.mileage, reviewed.created_at) == original
        assert reviewed.data["verification_status"] == "advisor_confirmed"
        assert reviewed.data["verification_status_label"] == "Advisor-confirmed from available evidence"
        assert reviewed.data["verification_reviewed_by_user_id"] == advisor.id
        assert reviewed.data["verification_reviewed_at"] == "2026-09-13T12:00:00Z"
        assert len(reviewed.data["verification_history"]) == 1
        assert reviewed.data["verification_history"][0]["previous_verification_status"] == "unverified"
        assert reviewed.data["verification_history"][0]["verification_status"] == "advisor_confirmed"

        replay, changed = HistoricalServiceVerificationService.review(
            service_event_id=event.id,
            actor_user_id=advisor.id,
            information_source="client_provided",
            verification_status="advisor_confirmed",
            reviewed_at=datetime(2026, 9, 13, 13, 0, 0),
        )
        db.session.commit()

        assert replay.id == event.id
        assert changed is False
        assert len(replay.data["verification_history"]) == 1


def test_review_rejects_current_service_and_non_advisor(app):
    with app.app_context():
        owner, advisor, car, ownership = _vehicle(suffix=2)
        current_event = _service(
            car=car,
            ownership=ownership,
            advisor=advisor,
            historical=False,
        )

        with pytest.raises(HistoricalServiceVerificationError):
            HistoricalServiceVerificationService.review(
                service_event_id=current_event.id,
                actor_user_id=advisor.id,
                information_source="workshop_record",
                verification_status="document_reviewed",
            )

        historical_event = _service(car=car, ownership=ownership, advisor=advisor)
        with pytest.raises(HistoricalServiceVerificationAuthorityError):
            HistoricalServiceVerificationService.review(
                service_event_id=historical_event.id,
                actor_user_id=owner.id,
                information_source="client_provided",
                verification_status="advisor_confirmed",
            )


def test_route_saves_review_and_requests_maintenance_reevaluation(app, client, monkeypatch):
    import services.historical_service_verification_routes as routes

    with app.app_context():
        _owner, advisor, car, ownership = _vehicle(suffix=3)
        event = _service(car=car, ownership=ownership, advisor=advisor)
        car_id = car.id
        event_id = event.id
        advisor_id = advisor.id

    reevaluations = []
    signal_refreshes = []
    monkeypatch.setattr(
        routes.MaintenanceReevaluationService,
        "safe_evaluate_car",
        staticmethod(lambda **kwargs: reevaluations.append(kwargs) or ()),
    )
    monkeypatch.setattr(
        routes.CareSignalService,
        "evaluate",
        staticmethod(lambda car_id, **kwargs: signal_refreshes.append((car_id, kwargs)) or None),
    )

    with client.session_transaction() as browser_session:
        browser_session["_user_id"] = str(advisor_id)
        browser_session["_fresh"] = True

    response = client.post(
        f"/admin/cars/{car_id}/service-events/{event_id}/verification-review",
        data={
            "information_source": "client_provided",
            "verification_status": "advisor_confirmed",
        },
        follow_redirects=False,
    )

    assert response.status_code == 302
    assert reevaluations == [
        {"car_id": car_id, "trigger": "service_verification_changed"}
    ]
    assert signal_refreshes == [
        (car_id, {"trigger": "service_verification_changed"})
    ]

    with app.app_context():
        reviewed = VehicleEvent.query.get(event_id)
        assert reviewed.data["verification_status"] == "advisor_confirmed"
        assert len(reviewed.data["verification_history"]) == 1
