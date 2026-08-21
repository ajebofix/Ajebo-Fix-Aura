from __future__ import annotations

from datetime import datetime

from extensions import db
from models import (
    Car,
    CarOwnership,
    Consultation,
    User,
    VehicleAssessment,
    VehicleAssessmentRisk,
    VehicleAssessmentTreatmentOption,
)


PASSWORD = "Password123"


def _user(*, suffix: int, role: str = "user") -> User:
    user = User(
        name=f"Assessment Draft {role} {suffix}",
        email=f"assessment-draft-{role}-{suffix}@example.com",
        phone_number=f"+234897200{suffix:04d}",
        role=role,
        is_active=True,
        email_verified_at=datetime(2026, 8, 21, 3, 0, 0),
    )
    user.set_password(PASSWORD)
    db.session.add(user)
    db.session.flush()
    return user


def _assessment_fixture(*, suffix: int = 1):
    owner = _user(suffix=suffix)
    advisor = _user(suffix=suffix + 100, role="admin")

    car = Car(
        brand="Mercedes-Benz",
        model="GLE 450 4MATIC",
        year=2021,
        vin=f"W1NADRAFT{suffix:08d}",
        current_mileage=64000,
    )
    db.session.add(car)
    db.session.flush()

    ownership = CarOwnership(
        user_id=owner.id,
        car_id=car.id,
        plate_number=f"AD-{suffix:03d}-LA",
        mileage_at_transfer=64000,
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
        scheduled_for=datetime(2026, 8, 31, 9, 10, 0),
        started_at=datetime(2026, 8, 21, 3, 13, 0),
    )
    db.session.add(consultation)
    db.session.flush()

    assessment = VehicleAssessment(
        consultation_id=consultation.id,
        car_id=car.id,
        advisor_id=advisor.id,
        status="draft",
        is_finalized=False,
        vin=car.vin,
        mileage_at_assessment=64000,
        engine_status="healthy",
        transmission_status="healthy",
        suspension_status="attention",
        electrical_status="healthy",
        cooling_status="healthy",
        cost_consequence_analysis="Existing cost framing must survive partial posts.",
        professional_recommendation="Existing recommendation.",
    )
    db.session.add(assessment)
    db.session.flush()

    db.session.add(
        VehicleAssessmentRisk(
            assessment_id=assessment.id,
            description="Existing risk",
            likely_cause="Existing cause",
            consequence_if_ignored="Existing consequence",
            urgency="monitoring",
        )
    )
    db.session.add(
        VehicleAssessmentTreatmentOption(
            assessment_id=assessment.id,
            option_code="A",
            title="Existing option",
            description="Existing option description",
        )
    )
    db.session.commit()

    return advisor.email, assessment.id


def _csrf(client) -> str:
    with client.session_transaction() as flask_session:
        return str(flask_session["_csrf_token"])


def _login(client, email: str) -> None:
    client.get("/auth/login")
    response = client.post(
        "/auth/login",
        data={
            "email": email,
            "password": PASSWORD,
            "csrf_token": _csrf(client),
        },
        follow_redirects=False,
    )
    assert response.status_code in {302, 303}


def test_runtime_replaces_legacy_assessment_edit_endpoint(app):
    assert (
        app.view_functions["admin.admin_edit_assessment"].__module__
        == "services.assessment_draft_cutover"
    )


def test_current_bracketed_template_fields_persist_risks_and_treatments(app):
    client = app.test_client()

    with app.app_context():
        advisor_email, assessment_id = _assessment_fixture()

    _login(client, advisor_email)

    response = client.post(
        f"/admin/assessments/{assessment_id}/edit",
        data={
            "engine_status": "healthy",
            "transmission_status": "healthy",
            "suspension_status": "attention",
            "electrical_status": "healthy",
            "cooling_status": "healthy",
            "risk_description[]": ["Front stabiliser linkage wear"],
            "risk_cause[]": ["Linkage joint wear"],
            "risk_consequence[]": ["Noise and reduced suspension control"],
            "risk_urgency[]": ["immediate"],
            "treatment_title[]": ["Replace stabiliser linkages", "", ""],
            "treatment_description[]": [
                "Replace the affected front stabiliser linkages.",
                "",
                "",
            ],
            "treatment_code[]": ["A", "B", "C"],
            "professional_recommendation": (
                "The stabiliser linkages are the only defective parts; replace them promptly."
            ),
        },
        follow_redirects=False,
    )

    assert response.status_code in {302, 303}
    assert response.headers["Location"].endswith(f"/admin/assessments/{assessment_id}/edit")

    with app.app_context():
        assessment = db.session.get(VehicleAssessment, assessment_id)
        risks = VehicleAssessmentRisk.query.filter_by(
            assessment_id=assessment_id
        ).all()
        options = VehicleAssessmentTreatmentOption.query.filter_by(
            assessment_id=assessment_id
        ).all()

        assert len(risks) == 1
        assert risks[0].description == "Front stabiliser linkage wear"
        assert risks[0].likely_cause == "Linkage joint wear"
        assert risks[0].consequence_if_ignored == "Noise and reduced suspension control"
        assert risks[0].urgency == "immediate"

        assert len(options) == 1
        assert options[0].option_code == "A"
        assert options[0].title == "Replace stabiliser linkages"
        assert options[0].description == "Replace the affected front stabiliser linkages."

        assert assessment.professional_recommendation.startswith(
            "The stabiliser linkages are the only defective parts"
        )
        assert assessment.cost_consequence_analysis == (
            "Existing cost framing must survive partial posts."
        )

    page = client.get(f"/admin/assessments/{assessment_id}/edit")
    html = page.get_data(as_text=True)
    assert "Front stabiliser linkage wear" in html
    assert "Replace stabiliser linkages" in html


def test_missing_child_groups_do_not_delete_existing_draft_rows(app):
    client = app.test_client()

    with app.app_context():
        advisor_email, assessment_id = _assessment_fixture(suffix=2)

    _login(client, advisor_email)

    response = client.post(
        f"/admin/assessments/{assessment_id}/edit",
        data={
            "engine_status": "healthy",
            "transmission_status": "healthy",
            "suspension_status": "monitoring",
            "electrical_status": "healthy",
            "cooling_status": "healthy",
            "professional_recommendation": "Updated scalar recommendation.",
        },
        follow_redirects=False,
    )

    assert response.status_code in {302, 303}

    with app.app_context():
        assessment = db.session.get(VehicleAssessment, assessment_id)
        risks = VehicleAssessmentRisk.query.filter_by(
            assessment_id=assessment_id
        ).all()
        options = VehicleAssessmentTreatmentOption.query.filter_by(
            assessment_id=assessment_id
        ).all()

        assert [risk.description for risk in risks] == ["Existing risk"]
        assert [option.title for option in options] == ["Existing option"]
        assert assessment.cost_consequence_analysis == (
            "Existing cost framing must survive partial posts."
        )
        assert assessment.professional_recommendation == "Updated scalar recommendation."
