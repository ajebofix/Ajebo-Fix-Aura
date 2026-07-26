"""Client profile persistence for Aura.

Authentication identity remains in ``User``. Optional personal, communication,
and care-preference information lives in this one-to-one extension so the
security boundary stays explicit.
"""

from __future__ import annotations

from datetime import datetime

from extensions import db
from models import User
from security.field_encryption import decrypt_profile_value, encrypt_profile_value


GENDER_VALUES = (
    "female",
    "male",
    "non_binary",
    "prefer_not_to_say",
)

COMMUNICATION_VALUES = (
    "whatsapp",
    "phone",
    "email",
    "sms",
)


class ClientProfile(db.Model):
    __tablename__ = "client_profiles"

    __table_args__ = (
        db.CheckConstraint(
            "gender IS NULL OR gender IN "
            "('female', 'male', 'non_binary', 'prefer_not_to_say')",
            name="ck_client_profiles_gender",
        ),
        db.CheckConstraint(
            "preferred_communication IS NULL OR preferred_communication IN "
            "('whatsapp', 'phone', 'email', 'sms')",
            name="ck_client_profiles_preferred_communication",
        ),
    )

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(
        db.Integer,
        db.ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )

    profile_photo_key = db.Column(db.String(255), nullable=True)

    occupation = db.Column(db.String(120), nullable=True)
    organisation = db.Column(db.String(120), nullable=True)
    gender = db.Column(db.String(30), nullable=True)

    city = db.Column(db.String(120), nullable=True)
    state_region = db.Column(db.String(120), nullable=True)
    country = db.Column(
        db.String(120),
        nullable=False,
        default="Nigeria",
        server_default="Nigeria",
    )

    home_address_ciphertext = db.Column(db.Text, nullable=True)
    office_address_ciphertext = db.Column(db.Text, nullable=True)

    preferred_communication = db.Column(db.String(30), nullable=True)
    preferred_communication_time = db.Column(db.String(120), nullable=True)
    care_preference = db.Column(db.Text, nullable=True)
    preferred_language = db.Column(db.String(80), nullable=True)
    timezone = db.Column(
        db.String(80),
        nullable=False,
        default="Africa/Lagos",
        server_default="Africa/Lagos",
    )

    emergency_contact_name_ciphertext = db.Column(db.Text, nullable=True)
    emergency_contact_phone_ciphertext = db.Column(db.Text, nullable=True)

    marketing_consent = db.Column(
        db.Boolean,
        nullable=False,
        default=False,
        server_default="false",
    )

    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(
        db.DateTime,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        nullable=False,
    )

    user = db.relationship("User", back_populates="client_profile")

    @property
    def home_address(self) -> str | None:
        return decrypt_profile_value(self.home_address_ciphertext)

    @home_address.setter
    def home_address(self, value: str | None) -> None:
        self.home_address_ciphertext = encrypt_profile_value(value)

    @property
    def office_address(self) -> str | None:
        return decrypt_profile_value(self.office_address_ciphertext)

    @office_address.setter
    def office_address(self, value: str | None) -> None:
        self.office_address_ciphertext = encrypt_profile_value(value)

    @property
    def emergency_contact_name(self) -> str | None:
        return decrypt_profile_value(self.emergency_contact_name_ciphertext)

    @emergency_contact_name.setter
    def emergency_contact_name(self, value: str | None) -> None:
        self.emergency_contact_name_ciphertext = encrypt_profile_value(value)

    @property
    def emergency_contact_phone(self) -> str | None:
        return decrypt_profile_value(self.emergency_contact_phone_ciphertext)

    @emergency_contact_phone.setter
    def emergency_contact_phone(self, value: str | None) -> None:
        self.emergency_contact_phone_ciphertext = encrypt_profile_value(value)

    def __repr__(self) -> str:
        return f"<ClientProfile user_id={self.user_id}>"


# The project currently keeps User in a monolithic models.py. Register this
# relationship here so the new domain can remain isolated until the planned
# model split, matching the existing compatibility approach used by security
# modules.
if not hasattr(User, "client_profile"):
    User.client_profile = db.relationship(
        "ClientProfile",
        back_populates="user",
        uselist=False,
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
