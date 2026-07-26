from __future__ import annotations

import pytest
from sqlalchemy.exc import IntegrityError

from extensions import db
from models import User
from profiles.models import ClientProfile
from security.field_encryption import ProfileEncryptionError


def _user(*, email: str = "client@example.com", phone: str = "+2348000000001"):
    user = User(
        name="Femi Adebayo",
        email=email,
        phone_number=phone,
        role="user",
    )
    user.set_password("SecurePass123")
    db.session.add(user)
    db.session.flush()
    return user


def test_sensitive_profile_fields_are_encrypted_at_rest(app):
    user = _user()
    profile = ClientProfile(
        user_id=user.id,
        occupation="Managing Director",
        preferred_communication="whatsapp",
        care_preference="Preventive management and calm progress updates",
    )
    profile.home_address = "12 Private Street, Lagos"
    profile.office_address = "4 Executive Avenue, Lagos"
    profile.emergency_contact_name = "Trusted Contact"
    profile.emergency_contact_phone = "+2348000000002"

    db.session.add(profile)
    db.session.commit()
    db.session.expire_all()

    stored = ClientProfile.query.filter_by(user_id=user.id).one()

    assert stored.home_address == "12 Private Street, Lagos"
    assert stored.office_address == "4 Executive Avenue, Lagos"
    assert stored.emergency_contact_name == "Trusted Contact"
    assert stored.emergency_contact_phone == "+2348000000002"

    assert "12 Private Street" not in stored.home_address_ciphertext
    assert "4 Executive Avenue" not in stored.office_address_ciphertext
    assert "Trusted Contact" not in stored.emergency_contact_name_ciphertext
    assert "+2348000000002" not in stored.emergency_contact_phone_ciphertext
    assert stored.home_address_ciphertext.startswith("test-v1:")


def test_client_profile_is_one_to_one_with_user(app):
    user = _user()
    db.session.add(ClientProfile(user_id=user.id))
    db.session.commit()

    db.session.add(ClientProfile(user_id=user.id))
    with pytest.raises(IntegrityError):
        db.session.commit()
    db.session.rollback()


def test_invalid_profile_enum_is_rejected_by_database(app):
    user = _user()
    db.session.add(
        ClientProfile(
            user_id=user.id,
            gender="not-a-valid-value",
        )
    )

    with pytest.raises(IntegrityError):
        db.session.commit()
    db.session.rollback()


def test_user_relationship_exposes_profile_without_sensitive_repr(app):
    user = _user()
    profile = ClientProfile(user_id=user.id, city="Lagos")
    db.session.add(profile)
    db.session.commit()

    assert user.client_profile is profile
    assert repr(profile) == f"<ClientProfile user_id={user.id}>"
    assert "Lagos" not in repr(profile)


def test_encryption_fails_closed_without_configured_key(app):
    app.config["PROFILE_ENCRYPTION_KEY"] = None
    app.config["PROFILE_ENCRYPTION_KEYS"] = None

    profile = ClientProfile()
    with pytest.raises(ProfileEncryptionError):
        profile.home_address = "This must never be stored as plaintext"
