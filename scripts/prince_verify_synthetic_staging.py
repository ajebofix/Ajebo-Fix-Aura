"""Run internal synthetic HTTP smoke checks against dedicated Railway staging.

No public domain needed; uses Flask's test client on the staging database.
Never connects to another Railway project or modifies live Aura data.
"""
from __future__ import annotations

import os
from sqlalchemy import inspect

from app import create_app
from extensions import db
from models import User
from partnerships.prince_models import (
    PrinceCustomer, PrinceJob, PrinceMembership, PrinceTenant,
)
from scripts.prince_seed_synthetic_staging import ensure_safe_scope

BASE = "https://prince-staging.test"


def verify():
    ensure_safe_scope()
    app = create_app()
    with app.app_context():
        tables = set(inspect(db.engine).get_table_names())
        assert {"prince_tenants", "prince_memberships", "prince_customers",
                "prince_vehicles", "prince_jobs", "prince_audit_events",
                "prince_job_entries", "prince_job_assignments", "prince_job_transitions"} <= tables
        tenant = PrinceTenant.query.filter_by(slug="prince").one()
        assert tenant.active and tenant.intake_enabled
        assert tenant.approved_privacy_notice_version == "synthetic-staging-v1"
        assert PrinceMembership.query.filter_by(tenant_id=tenant.id, active=True).count() == 4
        customers = PrinceCustomer.query.filter_by(tenant_id=tenant.id).all()
        assert len(customers) >= 2
        names = {c.full_name for c in customers}
        assert {"Demo Owner Alpha", "Demo Owner Beta"} <= names
        assert all(c.full_name.lower().startswith(("demo ", "synthetic "))
                   for c in customers)
        assert all(
            (c.phone_ciphertext is None or "DEMO CONTACT" not in c.phone_ciphertext)
            for c in customers
        )
        seeded = [c for c in customers
                  if c.full_name in {"Demo Owner Alpha", "Demo Owner Beta"}]
        assert len(seeded) == 2
        assert all(c.phone_ciphertext for c in seeded)
        assert PrinceJob.query.filter_by(tenant_id=tenant.id).count() >= 2
        # Rehearsals must not overwrite genuine trainee actions. Reassigning
        # fictional jobs is permitted and can change operator job counts.
        operator_ids = {
            role: User.query.filter_by(email=email).one().id
            for role, email in {
                "operator-a": "prince-demo-operator-a@example.invalid",
                "operator-b": "prince-demo-operator-b@example.invalid",
            }.items()
        }
        expected_jobs = {
            role: {
                job.id for job in (
                    PrinceJob.query.filter_by(
                        tenant_id=tenant.id, assigned_user_id=user_id,
                    ).order_by(PrinceJob.id.desc()).limit(50).all()
                )
            }
            for role, user_id in operator_ids.items()
        }
        alpha_job = (
            PrinceJob.query.join(
                PrinceCustomer,
                (PrinceCustomer.id == PrinceJob.customer_id)
                & (PrinceCustomer.tenant_id == PrinceJob.tenant_id),
            ).filter(
                PrinceJob.tenant_id == tenant.id,
                PrinceCustomer.full_name == "Demo Owner Alpha",
            ).one()
        )
        alpha_job_id = alpha_job.id
        alpha_assignee = alpha_job.assigned_user_id

    def login(role, password_env):
        client = app.test_client()
        first = client.get("/prince-demo", base_url=BASE)
        assert first.status_code == 200
        with client.session_transaction(base_url=BASE) as sess:
            token = sess["_csrf_token"]
        response = client.post("/prince-demo", base_url=BASE, data={
            "role": role, "portal_code": os.environ["PRINCE_STAGING_GATE_TOKEN"],
            "password": os.environ[password_env], "csrf_token": token,
        })
        assert response.status_code == 303, f"{role} login HTTP {response.status_code}"
        return client

    public = app.test_client()
    assert public.get("/prince-demo", base_url=BASE).status_code == 200
    assert public.get("/cars", base_url=BASE).status_code == 404
    assert public.get("/pilot/workshops/prince/jobs", base_url=BASE).status_code == 404
    assert public.get("/auth/login", base_url=BASE).status_code == 404

    op_a = login("operator-a", "PRINCE_DEMO_OPERATOR_A_PASSWORD")
    op_b = login("operator-b", "PRINCE_DEMO_OPERATOR_B_PASSWORD")
    for role, client in (("operator-a", op_a), ("operator-b", op_b)):
        listing = client.get("/pilot/workshops/prince/jobs", base_url=BASE)
        assert listing.status_code == 200
        received = {job["job_id"] for job in listing.json["jobs"]}
        assert received == expected_jobs[role], f"{role} assignment mismatch"
        assert "phone_ciphertext" not in str(listing.json)
        assert "phone_number" not in str(listing.json)
        assert client.get("/cars", base_url=BASE).status_code == 404
        assert client.get("/pilot/workshops/prince/manage",
                          base_url=BASE).status_code == 404
        page = client.get("/pilot/workshops/prince/operator", base_url=BASE)
        assert page.status_code == 200
        assert "Load protected contacts" not in page.get_data(as_text=True)
        for job in listing.json["jobs"]:
            history = client.get(
                f"/pilot/workshops/prince/jobs/{job['job_id']}/entries",
                base_url=BASE,
            )
            assert history.status_code == 200
            assert client.get(
                f"/pilot/workshops/prince/jobs/{job['job_id']}/lifecycle", base_url=BASE,
            ).status_code == 404
            assert all(x["review_state"] == "unverified"
                       for x in history.json["entries"])
        if alpha_assignee != operator_ids[role]:
            assert client.get(
                f"/pilot/workshops/prince/jobs/{alpha_job_id}", base_url=BASE,
            ).status_code == 404

    principal = login("principal", "PRINCE_DEMO_PRINCIPAL_PASSWORD")
    jobs_all = principal.get("/pilot/workshops/prince/jobs", base_url=BASE)
    assert jobs_all.status_code == 200
    assert len(jobs_all.json["jobs"]) >= 2
    assert {"Demo Owner Alpha", "Demo Owner Beta"} <= {
        x["full_name"] for x in jobs_all.json["jobs"]
    }
    assert principal.get("/admin", base_url=BASE).status_code == 404
    assert principal.get("/dashboard", base_url=BASE).status_code == 404
    management = principal.get("/pilot/workshops/prince/manage", base_url=BASE)
    assert management.status_code == 200
    assert "Principal management console" in management.get_data(as_text=True)
    assert "Demo Owner Alpha" in management.get_data(as_text=True)
    assert "phone_ciphertext" not in management.get_data(as_text=True)
    assert "Job progression" in management.get_data(as_text=True)
    for job in jobs_all.json["jobs"]:
        state = principal.get(
            f"/pilot/workshops/prince/jobs/{job['job_id']}/lifecycle", base_url=BASE,
        )
        assert state.status_code == 200
        assert state.json["job_status"] == job["job_status"]
        if state.json["history"]:
            assert state.json["history"][-1]["new_status"] == job["job_status"]
    stephen = login("stephen", "PRINCE_DEMO_STEPHEN_PASSWORD")
    stephen_jobs = stephen.get("/pilot/workshops/prince/jobs", base_url=BASE)
    assert stephen_jobs.status_code == 200
    assert {x["job_id"] for x in stephen_jobs.json["jobs"]} == {
        x["job_id"] for x in jobs_all.json["jobs"]
    }
    assert stephen.get("/admin", base_url=BASE).status_code == 404
    assert stephen.get(
        "/pilot/workshops/prince/manage", base_url=BASE,
    ).status_code == 200
    print("Prince staged HTTP smoke passed: dynamic operator assignments, principal management, tenant privacy and legacy route denial.")


if __name__ == "__main__":
    verify()
