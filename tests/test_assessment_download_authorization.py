from __future__ import annotations

from datetime import datetime

from extensions import db
from models import Car, CarOwnership, Consultation, User, VehicleAssessment


PASSWORD = "Password123"


def _user(*, suffix: int, role: str = "user") -> User:
    user = User(
        name=f"Assessment Download {role} {suffix}",
        email=f"assessment-download-{role}-{suffix}@example.com",
        phone_number=f"+234897300{suffix:04d}",
        role=role,
        is_active=True,
        email_verified_at=datetime(2026, 8, 21, 10, 0, 0),
    )
    user.set_password(PASSWORD)
    db.session.add(user)
    db.session.flush()
    return user


def _fixture(*, suffix: int = 1):
    owner = _user(suffix=suffix)
    advisor = _user(suffix=suffix + 100, role="admin")

    car = Car(
        brand="Mercedes-Benz",
        model="GLE 450 4MATIC",
        year=2021,
        vin=f"W1NDOWNLD{suffix:08d}",
        transmission_type="9G-TRONIC",
        current_mileage=64000,
    )
    db.session.add(car)
    db.session.flush()

    ownership = CarOwnership(
        user_id=owner.id,
        car_id=car.id,
        plate_number=f"DL-{suffix:03d}-LA",
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
        status="completed",
        scheduled_for=datetime(2026, 8, 21, 9, 0, 0),
        started_at=datetime(2026, 8, 21, 9, 5, 0),
        completed_at=datetime(2026, 8, 21, 10, 0, 0),
    )
    db.session.add(consultation)
    db.session.flush()

    assessment = VehicleAssessment(
        consultation_id=consultation.id,
        car_id=car.id,
        advisor_id=advisor.id,
        finalized_by=advisor.id,
        status="finalized",
        is_finalized=True,
        finalized_at=datetime(2026, 8, 21, 10, 0, 0),
        vin=car.vin,
        mileage_at_assessment=64000,
        engine_status="healthy",
        transmission_status="attention",
        suspension_status="attention",
        electrical_status="healthy",
        cooling_status="healthy",
        professional_recommendation="Advisor-approved assessment recommendation.",
    )
    db.session.add(assessment)
    db.session.commit()

    return owner.email, advisor.email, assessment.id, ownership.id


def _csrf_token_for(client) -> str:
    client.get("/auth/login")
    with client.session_transaction() as browser_session:
        return browser_session["_csrf_token"]


def _login(client, email: str) -> None:
    response = client.post(
        "/auth/login",
        data={
            "csrf_token": _csrf_token_for(client),
            "email": email,
            "password": PASSWORD,
        },
        follow_redirects=False,
    )
    assert response.status_code in {302, 303}


def _canonical_report_path(assessment_id: int) -> str:
    return f"/assessments/{assessment_id}/report"


def _pdf_report_path(assessment_id: int) -> str:
    return f"/assessments/{assessment_id}/report.pdf"


def _legacy_report_path(assessment_id: int) -> str:
    return f"/admin/assessments/{assessment_id}/download"


def test_active_owner_can_download_finalized_report_from_shared_profile_route(app):
    """Security-suite compatibility name; behavior now targets neutral route."""
    client = app.test_client()

    with app.app_context():
        owner_email, _, assessment_id, _ = _fixture()

    _login(client, owner_email)

    response = client.get(
        _canonical_report_path(assessment_id),
        follow_redirects=False,
    )

    assert response.status_code == 200
    assert response.mimetype == "text/html"
    assert "inline" in response.headers["Content-Disposition"]

    html = response.get_data(as_text=True)
    assert "Advisor-approved assessment recommendation." in html
    assert "9G-TRONIC" in html
    assert "LOW RISK" in html
    assert "30/100" in html
    assert _pdf_report_path(assessment_id) in html
    assert "Download PDF Report" in html
    assert "window.print()" not in html


def test_active_owner_can_download_real_pdf_report(app):
    client = app.test_client()

    with app.app_context():
        owner_email, _, assessment_id, _ = _fixture(suffix=7)

    _login(client, owner_email)
    response = client.get(_pdf_report_path(assessment_id), follow_redirects=False)

    assert response.status_code == 200
    assert response.mimetype == "application/pdf"
    assert response.data.startswith(b"%PDF-")
    assert len(response.data) > 3000
    assert "attachment" in response.headers["Content-Disposition"]
    assert "Ajebo_Fix_Vehicle_Health_Risk_Report_" in response.headers[
        "Content-Disposition"
    ]
    assert "no-store" in response.headers["Cache-Control"]


def test_advisor_keeps_direct_report_access(app):
    """Security-suite compatibility name; behavior now targets neutral route."""
    client = app.test_client()

    with app.app_context():
        _, advisor_email, assessment_id, _ = _fixture(suffix=2)

    _login(client, advisor_email)

    response = client.get(
        _canonical_report_path(assessment_id),
        follow_redirects=False,
    )

    assert response.status_code == 200
    assert response.mimetype == "text/html"


def test_advisor_can_download_real_pdf_report(app):
    client = app.test_client()

    with app.app_context():
        _, advisor_email, assessment_id, _ = _fixture(suffix=8)

    _login(client, advisor_email)
    response = client.get(_pdf_report_path(assessment_id), follow_redirects=False)

    assert response.status_code == 200
    assert response.mimetype == "application/pdf"
    assert response.data.startswith(b"%PDF-")


def test_unrelated_authenticated_user_cannot_receive_owner_report(app):
    client = app.test_client()

    with app.app_context():
        _, _, assessment_id, _ = _fixture(suffix=3)
        outsider = _user(suffix=999)
        outsider_email = outsider.email
        db.session.commit()

    _login(client, outsider_email)

    response = client.get(
        _canonical_report_path(assessment_id),
        follow_redirects=False,
    )

    assert response.status_code in {302, 303}
    assert "/dashboard" in response.headers["Location"]

    pdf_response = client.get(
        _pdf_report_path(assessment_id),
        follow_redirects=False,
    )
    assert pdf_response.status_code in {302, 303}
    assert "/dashboard" in pdf_response.headers["Location"]


def test_inactive_former_owner_cannot_receive_report(app):
    client = app.test_client()

    with app.app_context():
        owner_email, _, assessment_id, ownership_id = _fixture(suffix=4)
        ownership = db.session.get(CarOwnership, ownership_id)
        ownership.is_active = False
        db.session.commit()

    _login(client, owner_email)

    response = client.get(
        _canonical_report_path(assessment_id),
        follow_redirects=False,
    )

    assert response.status_code in {302, 303}
    assert "/dashboard" in response.headers["Location"]

    pdf_response = client.get(
        _pdf_report_path(assessment_id),
        follow_redirects=False,
    )
    assert pdf_response.status_code in {302, 303}
    assert "/dashboard" in pdf_response.headers["Location"]


def test_legacy_admin_prefixed_report_url_redirects_to_neutral_route(app):
    client = app.test_client()

    with app.app_context():
        owner_email, _, assessment_id, _ = _fixture(suffix=5)

    _login(client, owner_email)

    response = client.get(
        _legacy_report_path(assessment_id),
        follow_redirects=False,
    )

    assert response.status_code in {301, 302, 303, 307, 308}
    assert response.headers["Location"].endswith(
        _canonical_report_path(assessment_id)
    )
    assert "/admin/" not in response.headers["Location"]


def test_legacy_report_url_preserves_owner_authorization_after_redirect(app):
    client = app.test_client()

    with app.app_context():
        owner_email, _, assessment_id, _ = _fixture(suffix=6)

    _login(client, owner_email)

    response = client.get(
        _legacy_report_path(assessment_id),
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert response.request.path == _canonical_report_path(assessment_id)
    assert "Advisor-approved assessment recommendation." in response.get_data(
        as_text=True
    )


def test_neutral_routes_are_registered_without_admin_prefix(app):
    rules = {
        rule.endpoint: rule.rule
        for rule in app.url_map.iter_rules()
        if rule.endpoint
        in {
            "assessment_reports.assessment_report",
            "assessment_reports.assessment_report_pdf",
        }
    }

    assert rules["assessment_reports.assessment_report"] == (
        "/assessments/<int:assessment_id>/report"
    )
    assert rules["assessment_reports.assessment_report_pdf"] == (
        "/assessments/<int:assessment_id>/report.pdf"
    )
    assert all("/admin/" not in path for path in rules.values())
