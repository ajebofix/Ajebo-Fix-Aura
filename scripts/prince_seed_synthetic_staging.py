"""Idempotently create synthetic Prince personas in the isolated Railway stage.

Run ONLY as the stage's pre-deploy command after Alembic migration.
No production users, real workshop customer records, emails or PINs.
"""
from __future__ import annotations

import os
import sys
from datetime import datetime

from app import create_app
from extensions import db
from models import User
from partnerships.prince_models import (
    PrinceAudit, PrinceCustomer, PrinceJob, PrinceMembership, PrinceTenant,
    PrinceVehicle,
)
from security.field_encryption import encrypt_profile_value

PROJECT_ID = "3ed38d6c-6411-4890-861b-70cea583add2"
ENVIRONMENT_ID = "aef601d6-38f3-4d16-99f0-aa908c87b3f2"
NOTICE = "synthetic-staging-v1"
PERSONAS = (
    ("principal", "Demo Workshop Principal", "prince-demo-principal@example.invalid",
     "demo-prince-pr", "principal", "PRINCE_DEMO_PRINCIPAL_PASSWORD"),
    ("stephen", "Demo Stephen Principal", "prince-demo-stephen@example.invalid",
     "demo-prince-st", "principal", "PRINCE_DEMO_STEPHEN_PASSWORD"),
    ("operator-a", "Demo Operator A", "prince-demo-operator-a@example.invalid",
     "demo-prince-oa", "operator", "PRINCE_DEMO_OPERATOR_A_PASSWORD"),
    ("operator-b", "Demo Operator B", "prince-demo-operator-b@example.invalid",
     "demo-prince-ob", "operator", "PRINCE_DEMO_OPERATOR_B_PASSWORD"),
)


def ensure_safe_scope():
    if (
        os.getenv("APP_ENV") != "staging"
        or os.getenv("RAILWAY_PROJECT_ID") != PROJECT_ID
        or os.getenv("RAILWAY_ENVIRONMENT_ID") != ENVIRONMENT_ID
        or os.getenv("PRINCE_STAGING_LOCKDOWN", "").lower() != "true"
        or os.getenv("AURA_PRINCE_PILOT_API_ENABLED", "").lower() != "true"
        or os.getenv("PRINCE_STAGING_SYNTHETIC_SEED", "").lower() != "true"
    ):
        raise SystemExit("Refusing to seed outside the exact isolated Prince staging scope.")
    if len(os.getenv("PRINCE_STAGING_GATE_TOKEN", "")) < 32:
        raise SystemExit("Missing long staging portal access secret.")
    for *_, key in PERSONAS:
        if len(os.getenv(key, "")) < 24:
            raise SystemExit("Missing strong individual synthetic demo password.")


def seed():
    ensure_safe_scope()
    app = create_app()
    with app.app_context():
        tenant = PrinceTenant.query.filter_by(slug="prince").first()
        if tenant is None:
            tenant = PrinceTenant(slug="prince", display_name="Prince Automobile Clinic")
            db.session.add(tenant)
            db.session.flush()
        elif tenant.display_name != "Prince Automobile Clinic":
            raise SystemExit("Unexpected workshop identity; refusing to overwrite.")
        # Training may legitimately add more demo customers over time.
        # Preserve those rows, but refuse any record that is not explicitly
        # marked fictional and covered by the synthetic-only notice.
        unexpected_customer = any(
            not owner.full_name.lower().startswith(("demo ", "synthetic "))
            or owner.privacy_notice_version != NOTICE
            for owner in PrinceCustomer.query.filter_by(tenant_id=tenant.id)
        )
        if unexpected_customer:
            raise SystemExit("Non-demo customer present; synthetic seeding refused.")
        tenant.active = True
        tenant.intake_enabled = True
        tenant.approved_privacy_notice_version = NOTICE

        accounts = {}
        for persona, full_name, email, phone, role, env_key in PERSONAS:
            user = User.query.filter_by(email=email).first()
            if user is None:
                user = User(
                    name=full_name, email=email, phone_number=phone,
                    role="user", is_active=True, email_verified_at=datetime.utcnow(),
                )
                # Required non-null value before the first database flush.
                user.set_password(os.environ[env_key])
                db.session.add(user)
                db.session.flush()
            elif user.phone_number != phone or user.role != "user":
                raise SystemExit("Demo identity collision; refusing to change other accounts.")
            user.name = full_name
            user.is_active = True
            user.email_verified_at = datetime.utcnow()
            # Rehash upon redeploy to permit intentional credential rotation.
            user.set_password(os.environ[env_key])
            membership = PrinceMembership.query.filter_by(
                tenant_id=tenant.id, user_id=user.id
            ).first()
            if membership is None:
                membership = PrinceMembership(
                    tenant_id=tenant.id, user_id=user.id, role=role, active=True
                )
                db.session.add(membership)
            else:
                membership.role = role
                membership.active = True
            accounts[persona] = user

        # Two unmistakably fictional records. Not a reconstruction of the
        # actual W221 owner, VIN, payments, repairs or diagnosis.
        samples = (
            ("Demo Owner Alpha", "Mercedes-Benz", "S-Class W221",
             "Cranks but does not start; fuel concern reported; diagnosis pending.",
             accounts["operator-a"]),
            ("Demo Owner Beta", "Toyota", "Camry",
             "Customer reports steering vibration; inspection pending.",
             accounts["operator-b"]),
        )
        for full_name, brand, model, concern, operator in samples:
            if PrinceCustomer.query.filter_by(
                tenant_id=tenant.id, full_name=full_name
            ).first():
                continue
            owner = PrinceCustomer(
                tenant_id=tenant.id, full_name=full_name,
                phone_ciphertext=encrypt_profile_value("DEMO CONTACT ONLY"),
                email_ciphertext=encrypt_profile_value("demo@example.invalid"),
                intake_by_user_id=accounts["principal"].id,
                privacy_notice_version=NOTICE, participation_confirmed=True,
            )
            db.session.add(owner)
            db.session.flush()
            vehicle = PrinceVehicle(
                tenant_id=tenant.id, customer_id=owner.id,
                brand=brand, model=model, vin=None
            )
            db.session.add(vehicle)
            db.session.flush()
            job = PrinceJob(
                tenant_id=tenant.id, customer_id=owner.id, vehicle_id=vehicle.id,
                assigned_user_id=operator.id, reported_concern=concern, status="draft"
            )
            db.session.add(job)
            db.session.flush()
            db.session.add(PrinceAudit(
                tenant_id=tenant.id, actor_user_id=accounts["principal"].id,
                action="synthetic_demo_job_seeded", target_kind="job",
                target_id=job.id
            ))
        db.session.commit()
        print("Prince synthetic stage seeded: 4 demo staff memberships, 2 fictional jobs. No real clients.")


if __name__ == "__main__":
    try:
        seed()
    except Exception as exc:
        # Avoid logging SQL values or passwords; only type and source position.
        last_frame = exc.__traceback__
        while last_frame and last_frame.tb_next:
            last_frame = last_frame.tb_next
        location = (
            f"{last_frame.tb_frame.f_code.co_name}:{last_frame.tb_lineno}"
            if last_frame else "unknown"
        )
        native_error = getattr(exc, "orig", None)
        diagnostics = getattr(native_error, "diag", None)
        table = getattr(diagnostics, "table_name", None) or "unknown"
        constraint = getattr(diagnostics, "constraint_name", None) or "unknown"
        sqlstate = getattr(native_error, "pgcode", None) or "unknown"
        print(
            f"Prince synthetic provisioning failed at {location} "
            f"({type(exc).__name__}; table={table}; constraint={constraint}; "
            f"sqlstate={sqlstate}); no field values logged.",
            file=sys.stderr,
        )
        raise SystemExit(1) from exc
