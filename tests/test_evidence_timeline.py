from __future__ import annotations

from datetime import datetime
import hashlib

from evidence.models import VehicleEvidence
from evidence.review import link_evidence_to_reported_concern, review_evidence
from extensions import db
from models import Car, CarDriver, CarFault, CarOwnership, User, VehicleEvent
from services.concern_progression import get_reported_concern_progression
from services.evidence_timeline import (
    EvidenceTimelineAccessError,
    get_advisor_evidence_timeline,
    get_client_safe_evidence_timeline,
)


PASSWORD = "Password123"


def _user(*, suffix: int, role: str = "user") -> User:
    user = User(
        name=f"Evidence Timeline User {suffix}",
        email=f"evidence-timeline-{suffix}@example.com",
        phone_number=f"+234877000{suffix:04d}",
        role=role,
        is_active=True,
        email_verified_at=datetime(2026, 8, 16, 22, 0, 0),
    )
    user.set_password(PASSWORD)
    db.session.add(user)
    db.session.flush()
    return user


def _owned_car(owner: User, *, suffix: int) -> Car:
    car = Car(
        brand="Mercedes-Benz",
        model="GLE 450",
        year=2024,
        vin=f"W1NEVIDTIMELINE{suffix:03d}",
        current_mileage=13000,
    )
    db.session.add(car)
    db.session.flush()
    db.session.add(
        CarOwnership(
            user_id=owner.id,
            car_id=car.id,
            plate_number=f"ET-{suffix:03d}-LA",
            mileage_at_transfer=13000,
            is_active=True,
        )
    )
    db.session.commit()
    return car


def _assign_driver(car: Car, driver: User) -> None:
    db.session.add(CarDriver(car_id=car.id, user_id=driver.id, is_active=True))
    db.session.commit()


def _evidence(
    *,
    car: Car,
    uploader: User,
    suffix: int,
    visibility: str = "client",
    purpose: str = "concern_support",
) -> VehicleEvidence:
    payload = f"safe-evidence-timeline-{suffix}".encode()
    evidence = VehicleEvidence(
        car_id=car.id,
        uploaded_by_user_id=uploader.id,
        evidence_type="image",
        purpose=purpose,
        source_channel="web",
        visibility=visibility,
        review_status="pending_review",
        storage_provider="test-private",
        storage_state="available",
        object_key=f"evidence/{suffix:02x}/{suffix:032x}.jpg",
        safe_display_name=f"vehicle-evidence-{suffix}.jpg",
        content_type="image/jpeg",
        byte_size=len(payload),
        sha256=hashlib.sha256(payload).hexdigest(),
        consent_basis="explicit_web_upload",
        lawful_purpose="vehicle_care",
        uploaded_at=datetime(2026, 8, 16, 22, suffix % 60, 0),
    )
    db.session.add(evidence)
    db.session.commit()
    return evidence


def _concern(*, car: Car, reporter: User, suffix: int) -> CarFault:
    concern = CarFault(
        car_id=car.id,
        title=f"Timeline concern {suffix}",
        category="observation",
        description="Controlled concern used for evidence timeline projection tests.",
        status="reported",
        reported_by=reporter.id,
        source="client",
        reported_at=datetime(2026, 8, 16, 22, 20, 0),
    )
    db.session.add(concern)
    db.session.commit()
    return concern


def test_owner_projection_contains_only_safe_client_visible_reviewed_records(app):
    with app.app_context():
        owner = _user(suffix=1)
        driver = _user(suffix=2, role="driver")
        advisor = _user(suffix=3, role="admin")
        car = _owned_car(owner, suffix=1)
        _assign_driver(car, driver)

        owner_evidence = _evidence(car=car, uploader=owner, suffix=1)
        driver_evidence = _evidence(
            car=car,
            uploader=driver,
            suffix=2,
            purpose="driver_observation",
        )
        advisor_only = _evidence(
            car=car,
            uploader=advisor,
            suffix=3,
            visibility="advisor",
        )

        review_evidence(
            reviewer_user_id=advisor.id,
            evidence_id=owner_evidence.id,
            decision="accepted",
            reason_code="advisor_verified",
        )
        review_evidence(
            reviewer_user_id=advisor.id,
            evidence_id=driver_evidence.id,
            decision="rejected",
            reason_code="insufficient_quality",
        )
        review_evidence(
            reviewer_user_id=advisor.id,
            evidence_id=advisor_only.id,
            decision="accepted",
            reason_code="sufficient_for_record",
        )

        projection = get_client_safe_evidence_timeline(
            car_id=car.id,
            viewer_user_id=owner.id,
        )
        payload = projection.to_dict()

        assert projection.viewer_authority == "owner"
        assert {item["evidence_id"] for item in payload["records"]} == {
            owner_evidence.id,
            driver_evidence.id,
        }
        by_id = {item["evidence_id"]: item for item in payload["records"]}
        assert by_id[owner_evidence.id]["uploaded_by_self"] is True
        assert by_id[driver_evidence.id]["uploaded_by_self"] is False
        assert (
            by_id[driver_evidence.id]["review_summary"]
            == "The submitted evidence could not be used because the media quality was insufficient."
        )
        serialized = str(payload).lower()
        for forbidden in (
            "object_key",
            "safe_display_name",
            "sha256",
            "storage_provider",
            "storage_state",
            "review_reason_code",
            "uploaded_by_user_id",
        ):
            assert forbidden not in serialized
        assert "mechanical diagnosis" in payload["safety_note"].lower()


def test_driver_projection_is_limited_to_driver_own_client_evidence(app):
    with app.app_context():
        owner = _user(suffix=4)
        driver_one = _user(suffix=5, role="driver")
        driver_two = _user(suffix=6, role="driver")
        advisor = _user(suffix=7, role="admin")
        car = _owned_car(owner, suffix=2)
        _assign_driver(car, driver_one)
        _assign_driver(car, driver_two)

        own = _evidence(car=car, uploader=driver_one, suffix=4)
        other_driver = _evidence(car=car, uploader=driver_two, suffix=5)
        owner_media = _evidence(car=car, uploader=owner, suffix=6)
        for evidence in (own, other_driver, owner_media):
            review_evidence(
                reviewer_user_id=advisor.id,
                evidence_id=evidence.id,
                decision="accepted",
                reason_code="advisor_verified",
            )

        projection = get_client_safe_evidence_timeline(
            car_id=car.id,
            viewer_user_id=driver_one.id,
        )
        assert projection.viewer_authority == "driver"
        assert [item.evidence_id for item in projection.records] == [own.id]
        assert projection.records[0].uploaded_by_self is True


def test_advisor_projection_contains_professional_visibility_and_controlled_codes(app):
    with app.app_context():
        owner = _user(suffix=8)
        advisor = _user(suffix=9, role="admin")
        car = _owned_car(owner, suffix=3)
        client_evidence = _evidence(car=car, uploader=owner, suffix=7)
        internal_evidence = _evidence(
            car=car,
            uploader=advisor,
            suffix=8,
            visibility="internal",
            purpose="assessment_evidence",
        )
        review_evidence(
            reviewer_user_id=advisor.id,
            evidence_id=client_evidence.id,
            decision="accepted",
            reason_code="advisor_verified",
        )
        review_evidence(
            reviewer_user_id=advisor.id,
            evidence_id=internal_evidence.id,
            decision="rejected",
            reason_code="not_relevant",
        )

        projection = get_advisor_evidence_timeline(
            car_id=car.id,
            viewer_user_id=advisor.id,
        )
        payload = projection.to_dict()
        assert projection.viewer_authority == "advisor"
        assert {item["visibility"] for item in payload["records"]} == {
            "client",
            "internal",
        }
        assert {item["review_reason_code"] for item in payload["records"]} == {
            "advisor_verified",
            "not_relevant",
        }
        assert all("uploaded_by_user_id" in item for item in payload["records"])
        serialized = str(payload).lower()
        assert "object_key" not in serialized
        assert "sha256" not in serialized
        assert "storage_provider" not in serialized


def test_projection_links_only_canonical_same_vehicle_reported_concern_links(app):
    with app.app_context():
        owner = _user(suffix=10)
        advisor = _user(suffix=11, role="admin")
        car = _owned_car(owner, suffix=4)
        evidence = _evidence(car=car, uploader=owner, suffix=9)
        concern = _concern(car=car, reporter=owner, suffix=1)
        review_evidence(
            reviewer_user_id=advisor.id,
            evidence_id=evidence.id,
            decision="accepted",
            reason_code="advisor_verified",
        )
        link_evidence_to_reported_concern(
            reviewer_user_id=advisor.id,
            evidence_id=evidence.id,
            concern_id=concern.id,
        )

        projection = get_client_safe_evidence_timeline(
            car_id=car.id,
            viewer_user_id=owner.id,
        )
        assert len(projection.records) == 1
        assert len(projection.records[0].linked_concerns) == 1
        linked = projection.records[0].linked_concerns[0]
        assert linked.concern_id == concern.id
        assert linked.title == concern.title
        assert linked.status == "reported"
        assert linked.link_event_id > 0


def test_reviewed_row_without_canonical_review_event_is_not_projected(app):
    with app.app_context():
        owner = _user(suffix=12)
        advisor = _user(suffix=13, role="admin")
        car = _owned_car(owner, suffix=5)
        evidence = _evidence(car=car, uploader=owner, suffix=10)
        evidence.review_status = "accepted"
        evidence.reviewed_by_user_id = advisor.id
        evidence.reviewed_at = datetime(2026, 8, 16, 22, 40, 0)
        evidence.review_reason_code = "advisor_verified"
        db.session.commit()

        projection = get_client_safe_evidence_timeline(
            car_id=car.id,
            viewer_user_id=owner.id,
        )
        assert projection.records == ()


def test_outsider_cannot_read_vehicle_evidence_timeline(app):
    with app.app_context():
        owner = _user(suffix=14)
        outsider = _user(suffix=15)
        car = _owned_car(owner, suffix=6)
        try:
            get_client_safe_evidence_timeline(
                car_id=car.id,
                viewer_user_id=outsider.id,
            )
        except EvidenceTimelineAccessError:
            pass
        else:
            raise AssertionError("Outsider unexpectedly received evidence timeline")


def test_evidence_governance_events_never_change_concern_progression(app):
    with app.app_context():
        owner = _user(suffix=16)
        advisor = _user(suffix=17, role="admin")
        car = _owned_car(owner, suffix=7)
        concern = _concern(car=car, reporter=owner, suffix=2)

        before = get_reported_concern_progression(
            car_id=car.id,
            concern_id=concern.id,
            viewer_user_id=owner.id,
        )
        assert before.progression == "insufficient_evidence"
        before_event_ids = tuple(item.event_id for item in before.timeline)
        assert before_event_ids
        assert all(
            event.subject_type == "reported_concern"
            for event in VehicleEvent.query.filter(
                VehicleEvent.id.in_(before_event_ids)
            ).all()
        )

        evidence = _evidence(car=car, uploader=owner, suffix=11)
        review_evidence(
            reviewer_user_id=advisor.id,
            evidence_id=evidence.id,
            decision="accepted",
            reason_code="advisor_verified",
        )
        link_evidence_to_reported_concern(
            reviewer_user_id=advisor.id,
            evidence_id=evidence.id,
            concern_id=concern.id,
        )

        after = get_reported_concern_progression(
            car_id=car.id,
            concern_id=concern.id,
            viewer_user_id=owner.id,
        )
        assert after.progression == before.progression
        assert after.recurrence == before.recurrence
        assert tuple(item.event_id for item in after.timeline) == before_event_ids
        assert VehicleEvent.query.filter_by(
            subject_type="vehicle_evidence",
            subject_id=evidence.id,
        ).count() == 2
