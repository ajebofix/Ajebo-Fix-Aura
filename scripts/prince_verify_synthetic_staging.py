"""Run internal synthetic HTTP smoke checks against dedicated Railway staging.

No public domain needed; uses Flask's test client on the staging database.
Never connects to another Railway project or modifies live Aura data.
"""
from __future__ import annotations

import os
from sqlalchemy import inspect

from app import create_app
from extensions import db
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
                "prince_job_entries"} <= tables
        tenant = PrinceTenant.query.filter_by(slug="prince").one()
        assert tenant.active and tenant.intake_enabled
        assert tenant.approved_privacy_notice_version == "synthetic-staging-v1"
        assert PrinceMembership.query.filter_by(tenant_id=tenant.id, active=True).count() == 3
        customers = PrinceCustomer.query.filter_by(tenant_id=tenant.id).all()
        assert len(customers) == 2
        assert all(c.full_name.startswith("Demo Owner") for c in customers)
        assert all(c.phone_ciphertext and "DEMO CONTACT" not in c.phone_ciphertext
                   for c in customers)
        assert PrinceJob.query.filter_by(tenant_id=tenant.id).count() == 2

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
    jobs_a = op_a.get("/pilot/workshops/prince/jobs", base_url=BASE)
    assert jobs_a.status_code == 200
    assert len(jobs_a.json["jobs"]) == 1
    assert jobs_a.json["jobs"][0]["full_name"] == "Demo Owner Alpha"
    assert "phone_number" not in jobs_a.json["jobs"][0]
    history_a = op_a.get(
        f"/pilot/workshops/prince/jobs/{jobs_a.json['jobs'][0]['job_id']}/entries",
        base_url=BASE,
    )
    assert history_a.status_code == 200
    assert all(x["review_state"] == "unverified"
               for x in history_a.json["entries"])
    assert not any("phone_ciphertext" in x for x in history_a.json["entries"])
    assert op_a.get("/cars", base_url=BASE).status_code == 404
    page_a = op_a.get("/pilot/workshops/prince/operator", base_url=BASE)
    assert page_a.status_code == 200
    assert "Demo Owner Alpha" in page_a.get_data(as_text=True)
    assert "Demo Owner Beta" not in page_a.get_data(as_text=True)

    op_b = login("operator-b", "PRINCE_DEMO_OPERATOR_B_PASSWORD")
    jobs_b = op_b.get("/pilot/workshops/prince/jobs", base_url=BASE)
    assert jobs_b.status_code == 200
    assert len(jobs_b.json["jobs"]) == 1
    assert jobs_b.json["jobs"][0]["full_name"] == "Demo Owner Beta"
    job_a = jobs_a.json["jobs"][0]["job_id"]
    assert op_b.get(f"/pilot/workshops/prince/jobs/{job_a}", base_url=BASE).status_code == 404
    assert op_b.get(f"/pilot/workshops/prince/jobs/{job_a}/entries", base_url=BASE).status_code == 404

    principal = login("principal", "PRINCE_DEMO_PRINCIPAL_PASSWORD")
    jobs_all = principal.get("/pilot/workshops/prince/jobs", base_url=BASE)
    assert jobs_all.status_code == 200
    assert len(jobs_all.json["jobs"]) == 2
    assert principal.get("/admin", base_url=BASE).status_code == 404
    assert principal.get("/dashboard", base_url=BASE).status_code == 404
    print("Prince staged HTTP smoke passed: roles, tenant, redaction, append-only history and legacy-route denial.")


if __name__ == "__main__":
    verify()
