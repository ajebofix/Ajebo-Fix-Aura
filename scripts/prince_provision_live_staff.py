"""Interactive, initial-only staff provisioning in the dedicated Prince pilot.

Run in the approved private service shell, never in pre-deploy or CI.
No customer records, automatic email verification, demo passwords or resets.
The administrator must independently verify the staff identity/email first.
"""
from datetime import datetime
from getpass import getpass
import os
import re
import secrets

from flask import current_app
from sqlalchemy import func

from extensions import db
from models import User
from partnerships.prince_live_portal import _dedicated_environment
from partnerships.prince_models import PrinceCustomer, PrinceMembership, PrinceTenant


PROJECT_ID = "1bd12284-d667-4655-8484-3c1e03136432"
ENVIRONMENT_ID = "7b3c3fc4-2b24-4e4a-ba37-6fde7293bcd1"


def ensure_initial_scope():
    if (os.getenv("RAILWAY_PROJECT_ID") != PROJECT_ID
            or os.getenv("RAILWAY_ENVIRONMENT_ID") != ENVIRONMENT_ID
            or not _dedicated_environment()
            or not current_app.config.get("PRINCE_LIVE_PILOT_LOCKDOWN", False)
            or any(current_app.config.get(flag, False) for flag in (
                "PRINCE_LIVE_WORKSHOP_APPROVED", "PRINCE_LIVE_AUTHORIZATION_APPROVED",
                "PRINCE_LIVE_FINANCE_APPROVED",
            ))):
        raise ValueError("Initial provisioning requires the exact locked live-pilot environment.")
    if PrinceCustomer.query.first() is not None:
        raise ValueError("Initial staff provisioning refuses a populated customer database.")


def provision_staff(*, name, email, role, password, identity_attested):
    ensure_initial_scope()
    name = name.strip()
    email = email.strip().lower()
    if (identity_attested is not True or role not in {"principal", "operator"}
            or not 2 <= len(name) <= 120 or any(ord(ch) < 32 for ch in name)
            or not 3 <= len(email) <= 120
            or re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", email) is None
            or email.startswith("prince-demo-")
            or email.endswith((".invalid", ".test", ".example", ".localhost"))
            or not 16 <= len(password) <= 128 or password.isspace()):
        raise ValueError("Invalid staff details or missing independent identity attestation.")
    tenant = PrinceTenant.query.filter_by(slug="prince").one_or_none()
    if tenant is not None and (tenant.display_name != "Prince Automobile Clinic"
                               or tenant.intake_enabled
                               or tenant.approved_privacy_notice_version is not None):
        raise ValueError("Unexpected workshop configuration; refusing changes.")
    if User.query.filter(func.lower(User.email) == email).first() is not None:
        raise ValueError("Account already exists; no overwrite, reactivation or reset.")
    if tenant is None:
        tenant = PrinceTenant(slug="prince", display_name="Prince Automobile Clinic",
                             active=True, intake_enabled=False)
        db.session.add(tenant)
        db.session.flush()
    elif not tenant.active:
        raise ValueError("Inactive workshop; refusing reactivation.")
    # Legacy User model requires a unique non-null phone field. The staff
    # portal never uses this internal random identifier as a phone number.
    user = User(name=name, email=email, phone_number="staff-" + secrets.token_hex(6),
                role="user", is_active=True, email_verified_at=datetime.utcnow())
    user.set_password(password)
    db.session.add(user)
    db.session.flush()
    db.session.add(PrinceMembership(tenant_id=tenant.id, user_id=user.id,
                                    role=role, active=True))
    db.session.commit()
    return user.id


def main():
    from app import create_app

    app = create_app()
    with app.app_context():
        try:
            ensure_initial_scope()
            name = input("Approved staff name: ")
            email = input("Independently verified staff email: ")
            role = input("Approved role (principal/operator): ").strip()
            attested = input("Identity and control of email independently verified? Type VERIFIED: ")
            password = getpass("Individual staff password (minimum 16 characters): ")
            if password != getpass("Confirm password: "):
                raise ValueError("Passwords do not match.")
            provision_staff(name=name, email=email, role=role, password=password,
                            identity_attested=attested == "VERIFIED")
            print("Staff account and membership created. Intake remains disabled.")
        except Exception:
            db.session.rollback()
            print("Provisioning refused or failed; no credentials or personal values logged.")
            raise SystemExit(1) from None


if __name__ == "__main__":
    main()
