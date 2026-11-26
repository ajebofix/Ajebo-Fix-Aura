"""Disposable database coverage for initial-only live staff provisioning."""
import pytest

from extensions import db
from models import User
from partnerships.prince_models import PrinceMembership, PrinceTenant
from scripts.prince_provision_live_staff import (
    ENVIRONMENT_ID, PROJECT_ID, provision_staff,
)


@pytest.fixture
def scope(app, monkeypatch):
    for key, value in {
        "RAILWAY_PROJECT_ID": PROJECT_ID,
        "RAILWAY_ENVIRONMENT_ID": ENVIRONMENT_ID,
        "PRINCE_LIVE_EXPECTED_PROJECT_ID": PROJECT_ID,
        "PRINCE_LIVE_EXPECTED_ENVIRONMENT_ID": ENVIRONMENT_ID,
        "PRINCE_STAGING_SYNTHETIC_SEED": "false",
    }.items():
        monkeypatch.setenv(key, value)
    app.config.update(APP_ENV="prince-live-pilot", PRINCE_LIVE_PILOT_LOCKDOWN=True,
                      PRINCE_STAGING_LOCKDOWN=False, AURA_PRINCE_PILOT_API_ENABLED=False,
                      PRINCE_LIVE_WORKSHOP_APPROVED=False,
                      PRINCE_LIVE_AUTHORIZATION_APPROVED=False,
                      PRINCE_LIVE_FINANCE_APPROVED=False)
    return dict(name="Fictional rehearsal staff", email="test-staff@example.org",
                role="principal", password="Synthetic-test-password-123",
                identity_attested=True)


def test_provision_creates_individual_staff_with_intake_closed(scope):
    user_id = provision_staff(**scope)
    user = db.session.get(User, user_id)
    tenant = PrinceTenant.query.filter_by(slug="prince").one()
    member = PrinceMembership.query.filter_by(user_id=user_id).one()
    assert user.check_password(scope["password"])
    assert user.email_verified_at is not None
    assert member.role == "principal" and member.active
    assert tenant.active and not tenant.intake_enabled
    assert tenant.approved_privacy_notice_version is None
    with pytest.raises(ValueError):
        provision_staff(**scope)
    assert User.query.filter_by(email=scope["email"]).count() == 1


@pytest.mark.parametrize("change", [
    {"identity_attested": False}, {"role": "admin"}, {"password": "short"},
    {"email": "prince-demo-principal@example.invalid"},
])
def test_invalid_staff_refused(scope, change):
    with pytest.raises(ValueError):
        provision_staff(**{**scope, **change})
    assert PrinceTenant.query.filter_by(slug="prince").count() == 0


def test_wrong_environment_refused(scope, monkeypatch):
    monkeypatch.setenv("RAILWAY_PROJECT_ID", "wrong")
    with pytest.raises(ValueError):
        provision_staff(**scope)


def test_approved_workshop_refused(scope, app):
    app.config["PRINCE_LIVE_WORKSHOP_APPROVED"] = True
    with pytest.raises(ValueError):
        provision_staff(**scope)
