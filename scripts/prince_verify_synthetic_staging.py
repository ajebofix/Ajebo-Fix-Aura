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
        if not ({"prince_tenants", "prince_memberships", "prince_customers",
                "prince_vehicles", "prince_jobs", "prince_audit_events",
                "prince_job_entries", "prince_job_assignments", "prince_job_transitions",
                "prince_estimates", "prince_customer_authorizations"} <= tables):
            raise RuntimeError("Prince staging verification failed at check 1 (source 27).")
        tenant = PrinceTenant.query.filter_by(slug="prince").one()
        if not (tenant.active and tenant.intake_enabled):
            raise RuntimeError("Prince staging verification failed at check 2 (source 31).")
        if not (tenant.approved_privacy_notice_version == "synthetic-staging-v1"):
            raise RuntimeError("Prince staging verification failed at check 3 (source 32).")
        if not (PrinceMembership.query.filter_by(tenant_id=tenant.id, active=True).count() == 4):
            raise RuntimeError("Prince staging verification failed at check 4 (source 33).")
        customers = PrinceCustomer.query.filter_by(tenant_id=tenant.id).all()
        if not (len(customers) >= 2):
            raise RuntimeError("Prince staging verification failed at check 5 (source 35).")
        names = {c.full_name for c in customers}
        if not ({"Demo Owner Alpha", "Demo Owner Beta"} <= names):
            raise RuntimeError("Prince staging verification failed at check 6 (source 37).")
        if not (all(c.full_name.lower().startswith(("demo ", "synthetic "))
                   for c in customers)):
            raise RuntimeError("Prince staging verification failed at check 7 (source 38).")
        if not (all(
            (c.phone_ciphertext is None or "DEMO CONTACT" not in c.phone_ciphertext)
            for c in customers
        )):
            raise RuntimeError("Prince staging verification failed at check 8 (source 40).")
        seeded = [c for c in customers
                  if c.full_name in {"Demo Owner Alpha", "Demo Owner Beta"}]
        if not (len(seeded) == 2):
            raise RuntimeError("Prince staging verification failed at check 9 (source 46).")
        if not (all(c.phone_ciphertext for c in seeded)):
            raise RuntimeError("Prince staging verification failed at check 10 (source 47).")
        if not (PrinceJob.query.filter_by(tenant_id=tenant.id).count() >= 2):
            raise RuntimeError("Prince staging verification failed at check 11 (source 48).")
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
        if not (first.status_code == 200):
            raise RuntimeError("Prince staging verification failed at check 12 (source 84).")
        with client.session_transaction(base_url=BASE) as sess:
            token = sess["_csrf_token"]
        response = client.post("/prince-demo", base_url=BASE, data={
            "role": role, "portal_code": os.environ["PRINCE_STAGING_GATE_TOKEN"],
            "password": os.environ[password_env], "csrf_token": token,
        })
        if not (response.status_code == 303):
            raise RuntimeError("Prince staging verification failed at check 13 (source 91).")
        return client

    public = app.test_client()
    if not (public.get("/prince-demo", base_url=BASE).status_code == 200):
        raise RuntimeError("Prince staging verification failed at check 14 (source 95).")
    if not (public.get("/cars", base_url=BASE).status_code == 404):
        raise RuntimeError("Prince staging verification failed at check 15 (source 96).")
    if not (public.get("/pilot/workshops/prince/jobs", base_url=BASE).status_code == 404):
        raise RuntimeError("Prince staging verification failed at check 16 (source 97).")
    if not (public.get("/auth/login", base_url=BASE).status_code == 404):
        raise RuntimeError("Prince staging verification failed at check 17 (source 98).")

    op_a = login("operator-a", "PRINCE_DEMO_OPERATOR_A_PASSWORD")
    op_b = login("operator-b", "PRINCE_DEMO_OPERATOR_B_PASSWORD")
    for role, client in (("operator-a", op_a), ("operator-b", op_b)):
        listing = client.get("/pilot/workshops/prince/jobs", base_url=BASE)
        if not (listing.status_code == 200):
            raise RuntimeError("Prince staging verification failed at check 18 (source 104).")
        received = {job["job_id"] for job in listing.json["jobs"]}
        if not (received == expected_jobs[role]):
            raise RuntimeError("Prince staging verification failed at check 19 (source 106).")
        if not ("phone_ciphertext" not in str(listing.json)):
            raise RuntimeError("Prince staging verification failed at check 20 (source 107).")
        if not ("phone_number" not in str(listing.json)):
            raise RuntimeError("Prince staging verification failed at check 21 (source 108).")
        if not (client.get("/cars", base_url=BASE).status_code == 404):
            raise RuntimeError("Prince staging verification failed at check 22 (source 109).")
        if not (client.get("/pilot/workshops/prince/manage",
                          base_url=BASE).status_code == 404):
            raise RuntimeError("Prince staging verification failed at check 23 (source 110).")
        page = client.get("/pilot/workshops/prince/operator", base_url=BASE)
        if not (page.status_code == 200):
            raise RuntimeError("Prince staging verification failed at check 24 (source 113).")
        if not ("Load protected contacts" not in page.get_data(as_text=True)):
            raise RuntimeError("Prince staging verification failed at check 25 (source 114).")
        for job in listing.json["jobs"]:
            history = client.get(
                f"/pilot/workshops/prince/jobs/{job['job_id']}/entries",
                base_url=BASE,
            )
            if not (history.status_code == 200):
                raise RuntimeError("Prince staging verification failed at check 26 (source 120).")
            if not (client.get(
                f"/pilot/workshops/prince/jobs/{job['job_id']}/lifecycle", base_url=BASE,
            ).status_code == 404):
                raise RuntimeError("Prince staging verification failed at check 27 (source 121).")
            if not (all(x["review_state"] == "unverified"
                       for x in history.json["entries"])):
                raise RuntimeError("Prince staging verification failed at check 28 (source 124).")
        if alpha_assignee != operator_ids[role]:
            if not (client.get(
                f"/pilot/workshops/prince/jobs/{alpha_job_id}", base_url=BASE,
            ).status_code == 404):
                raise RuntimeError("Prince staging verification failed at check 29 (source 127).")

    principal = login("principal", "PRINCE_DEMO_PRINCIPAL_PASSWORD")
    jobs_all = principal.get("/pilot/workshops/prince/jobs", base_url=BASE)
    if not (jobs_all.status_code == 200):
        raise RuntimeError("Prince staging verification failed at check 30 (source 133).")
    if not (len(jobs_all.json["jobs"]) >= 2):
        raise RuntimeError("Prince staging verification failed at check 31 (source 134).")
    if not ({"Demo Owner Alpha", "Demo Owner Beta"} <= {
        x["full_name"] for x in jobs_all.json["jobs"]
    }):
        raise RuntimeError("Prince staging verification failed at check 32 (source 135).")
    if not (principal.get("/admin", base_url=BASE).status_code == 404):
        raise RuntimeError("Prince staging verification failed at check 33 (source 138).")
    if not (principal.get("/dashboard", base_url=BASE).status_code == 404):
        raise RuntimeError("Prince staging verification failed at check 34 (source 139).")
    management = principal.get("/pilot/workshops/prince/manage", base_url=BASE)
    if not (management.status_code == 200):
        raise RuntimeError("Prince staging verification failed at check 35 (source 141).")
    if "Principal management console" not in management.get_data(as_text=True):
        raise RuntimeError("Prince staging verification failed at check 36 (source 142).")
    if "Demo Owner Alpha" not in management.get_data(as_text=True):
        raise RuntimeError("Prince staging verification failed at check 37 (source 143).")
    if not ("phone_ciphertext" not in management.get_data(as_text=True)):
        raise RuntimeError("Prince staging verification failed at check 38 (source 144).")
    if "Job progression" not in management.get_data(as_text=True):
        raise RuntimeError("Prince staging verification failed at check 39 (source 145).")
    for job in jobs_all.json["jobs"]:
        state = principal.get(
            f"/pilot/workshops/prince/jobs/{job['job_id']}/lifecycle", base_url=BASE,
        )
        if not (state.status_code == 200):
            raise RuntimeError("Prince staging verification failed at check 40 (source 150).")
        if not (state.json["job_status"] == job["job_status"]):
            raise RuntimeError("Prince staging verification failed at check 41 (source 151).")
        if state.json["history"]:
            if not (state.json["history"][-1]["new_status"] == job["job_status"]):
                raise RuntimeError("Prince staging verification failed at check 42 (source 153).")
    stephen = login("stephen", "PRINCE_DEMO_STEPHEN_PASSWORD")
    stephen_jobs = stephen.get("/pilot/workshops/prince/jobs", base_url=BASE)
    if not (stephen_jobs.status_code == 200):
        raise RuntimeError("Prince staging verification failed at check 43 (source 156).")
    if not ({x["job_id"] for x in stephen_jobs.json["jobs"]} == {
        x["job_id"] for x in jobs_all.json["jobs"]
    }):
        raise RuntimeError("Prince staging verification failed at check 44 (source 157).")
    if not (stephen.get("/admin", base_url=BASE).status_code == 404):
        raise RuntimeError("Prince staging verification failed at check 45 (source 160).")
    if not (stephen.get(
        "/pilot/workshops/prince/manage", base_url=BASE,
    ).status_code == 200):
        raise RuntimeError("Prince staging verification failed at check 46 (source 161).")
    # Read-only estimate checks against the deployed schema and rendered pages.
    # Drafting and approval mutations are tested on disposable CI databases.
    for role, client in (("operator-a", op_a), ("operator-b", op_b)):
        for job in jobs_all.json["jobs"]:
            job_id = job["job_id"]
            expected = 200 if job_id in expected_jobs[role] else 404
            root = f"/pilot/workshops/prince/jobs/{job_id}"
            history = client.get(root + "/estimates", base_url=BASE)
            page = client.get(root + "/estimate", base_url=BASE)
            if history.status_code != expected or page.status_code != expected:
                raise RuntimeError("Prince estimate operator scope verification failed.")
            if expected == 200:
                if "Approve estimate internally" in page.get_data(as_text=True):
                    raise RuntimeError("Prince operator received an estimate approval control.")
                for version in history.json["versions"]:
                    if "approval_note" in version:
                        raise RuntimeError("Prince operator received a private approval note.")
    for job in jobs_all.json["jobs"]:
        root = f"/pilot/workshops/prince/jobs/{job['job_id']}"
        dad_history = principal.get(root + "/estimates", base_url=BASE)
        stephen_history = stephen.get(root + "/estimates", base_url=BASE)
        if dad_history.status_code != 200 or stephen_history.status_code != 200:
            raise RuntimeError("Prince principal estimate history unavailable.")
        if dad_history.json != stephen_history.json:
            raise RuntimeError("Prince principal estimate views differ.")
        for client in (principal, stephen):
            if client.get(root + "/estimate", base_url=BASE).status_code != 200:
                raise RuntimeError("Prince principal estimate page unavailable.")
    # Verify the current work authorization gate and private decision endpoints
    # without creating a customer decision or changing any existing demo job.
    for job in jobs_all.json["jobs"]:
        root = f"/pilot/workshops/prince/jobs/{job['job_id']}"
        state = job["work_authorization"]
        if state not in {"estimate_required", "not_recorded", "accepted", "declined", "withdrawn"}:
            raise RuntimeError("Prince job authorization state is invalid.")
        versions = principal.get(root + "/estimates", base_url=BASE).json["versions"]
        latest = versions[0] if versions else None
        if not latest or not latest["internally_approved"]:
            if state != "estimate_required":
                raise RuntimeError("Prince job has authorization without approved estimate.")
            for client in (principal, stephen):
                if client.get(root + "/estimates/1/print", base_url=BASE).status_code != 404:
                    raise RuntimeError("Prince draft estimate print unexpectedly accessible.")
            continue
        auth_url = root + f"/estimates/{latest['version']}/authorization"
        dad_response = principal.get(auth_url, base_url=BASE)
        stephen_response = stephen.get(auth_url, base_url=BASE)
        if dad_response.status_code != 200 or stephen_response.status_code != 200:
            raise RuntimeError("Prince principal authorization history unavailable.")
        if dad_response.json != stephen_response.json or dad_response.json["state"] != state:
            raise RuntimeError("Prince authorization state or principal parity mismatch.")
        for client in (op_a, op_b):
            if client.get(auth_url, base_url=BASE).status_code != 404:
                raise RuntimeError("Prince operator received confidential decision history.")
            if client.get(root + f"/estimates/{latest['version']}/print", base_url=BASE).status_code != 404:
                raise RuntimeError("Prince operator received printable quotation.")
        for client in (principal, stephen):
            if client.get(root + f"/estimates/{latest['version']}/print", base_url=BASE).status_code != 200:
                raise RuntimeError("Prince principal print preview unavailable.")
    print("Prince staged HTTP smoke passed: four roles, lifecycle, estimates, authorization, print access, assignments and privacy.")


if __name__ == "__main__":
    verify()
