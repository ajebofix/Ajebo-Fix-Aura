"""Gulf pilot admin integration: no public writes, guarded customer jobs and payments."""
from datetime import datetime, timedelta
import os
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from io import BytesIO
from types import SimpleNamespace

from PIL import Image

from extensions import db
from models import User
from partnerships.models import GulfJob, GulfAudit, GulfPhoto, GulfPayment, GulfTechnician, GulfShift, GulfReadinessRecord, GulfReadinessHistory


def _make_user(role="admin", suffix="1"):
    row = User(
        name="Test operator",
        email=f"gulf-{role}-{suffix}@example.com",
        phone_number=f"+2348112233{int(suffix):04d}",
        role=role,
        is_active=True,
        email_verified_at=datetime.utcnow(),
    )
    row.set_password("Password123")
    db.session.add(row)
    db.session.commit()
    return row


def _sign_in(client, user):
    client.get("/auth/login")
    with client.session_transaction() as s:
        csrf = s["_csrf_token"]
    r = client.post("/auth/login", data={
        "csrf_token": csrf, "email": user.email, "password": "Password123",
    })
    assert r.status_code in (302, 303)


def _token(client):
    client.get("/admin/partners/gulf/")
    with client.session_transaction() as s:
        return s["_csrf_token"]


def _post(client, url, values=None):
    form = {"csrf_token": _token(client), **(values or {})}
    return client.post(url, data=form, follow_redirects=True)


def _job(client, suffix="001"):
    return _post(client, "/admin/partners/gulf/jobs", {
        "voucher": "GLF-LAG-" + suffix,
        "customer_name": "Pilot Guest",
        "phone": "+2348110000099",
        "plate": "SUR 123 XY",
        "make": "Toyota", "model": "Corolla",
        "model_year": "2018", "service_class": "car",
    })


def _action(client, action, extra=None, job_id=1):
    return _post(client, f"/admin/partners/gulf/jobs/{job_id}/action",
                 {"action": action, **(extra or {})})


def _next_monday():
    now = datetime.now()
    days = (7 - now.weekday()) % 7 + 7
    return (now + timedelta(days=days)).date()


def _set_up_slot(client):
    monday = _next_monday()
    start = monday.isoformat() + "T10:00"
    end = monday.isoformat() + "T11:30"
    _post(client, "/admin/partners/gulf/technicians", {
        "name": "Contractor A", "phone": "+2348110000012",
        "service_classes": ["car", "suv"], "brand_specialties": ["toyota", "lexus"],
    })
    _post(client, "/admin/partners/gulf/shifts", {
        "technician_id": "1", "starts_at": monday.isoformat() + "T09:00",
        "ends_at": monday.isoformat() + "T16:00", "accepted": "1",
    })
    return start, end


def _verify_and_approve(client):
    _action(client, "verify", {"reference": "EBICE-MANUAL-TEST-1"})
    _action(client, "approve_oil", {
        "oil_spec": "Manual checked API SP", "oil_sku": "Test 5W-20",
        "litres": "4.2", "filter_part": "TEST-FILTER-1", "human_approved": "1",
    })


def _dispatch(client):
    for status in ("requested", "dispatched", "accepted"):
        payload = {"dispatch_state": status, "reference": "YABA-T-1"}
        if status == "accepted":
            payload["checked_product"] = "1"
        _action(client, "dispatch", payload)


def _activate(app):
    app.config.update(
        AURA_GULF_CAMPAIGN_ACTIVE=True,
        AURA_GULF_PARTNER_SIGNED=True,
        AURA_GULF_FILTERS_APPROVED=True,
        AURA_GULF_RATES_APPROVED=True,
        AURA_GULF_CAR_FEE_NGN="25000",
        AURA_GULF_SUV_FEE_NGN="30000",
        AURA_GULF_CHECKLIST_ITEMS=[f"Approved check {n}" for n in range(1, 11)],
    )


def _image_bytes():
    out = BytesIO()
    Image.new("RGB", (64, 64), "white").save(out, format="JPEG")
    out.seek(0)
    return out


class FakeEvidenceStorage:
    def __init__(self):
        self.photos = {}

    def put_bytes(self, *, object_key, payload, content_type):
        self.photos[object_key] = payload

    def get_bytes(self, *, object_key, max_bytes):
        return SimpleNamespace(payload=self.photos[object_key])


def test_feature_is_off_by_default_and_no_public_portal(app, client):
    app.config["AURA_GULF_PILOT_ENABLED"] = False
    assert client.get("/admin/partners/gulf/").status_code == 404
    assert client.get("/admin/partners/gulf/jobs/1").status_code == 404


def test_only_admin_can_access_gulf_private_records(app, client):
    app.config["AURA_GULF_PILOT_ENABLED"] = True
    assert client.get("/admin/partners/gulf/").status_code in (302, 401)
    with app.app_context():
        user = _make_user("user", "2")
        _sign_in(client, user)
    non_admin = client.get("/admin/partners/gulf/")
    assert non_admin.status_code in (302, 403)
    assert "/admin/partners/gulf/" not in non_admin.headers.get("Location", "")
    assert client.post("/admin/partners/gulf/jobs", data={
        "voucher": "GLF-LAG-TRAP",
    }).status_code == 400  # CSRF protects all mutating routes


def test_intake_is_private_encrypted_deduplicated_and_unregistered(app, client):
    app.config["AURA_GULF_PILOT_ENABLED"] = True
    with app.app_context():
        admin = _make_user()
        before_user_count = User.query.count()
        _sign_in(client, admin)
    r = _job(client)
    assert r.status_code == 200
    assert "Pilot Guest" in r.get_data(as_text=True)
    assert "Service #1" in r.get_data(as_text=True)
    with app.app_context():
        row = GulfJob.query.one()
        assert "Pilot Guest" not in row.name_cipher
        assert "GLF-LAG" not in row.voucher_cipher
        assert "SUR 123" not in row.plate_cipher
        assert User.query.count() == before_user_count
        assert row.voucher_tail == "AG-001"
    _job(client)
    with app.app_context():
        assert GulfJob.query.count() == 1
        assert GulfAudit.query.filter_by(action="request_created").count() == 1


def test_no_confirm_before_authorised_partner_activation(app, client):
    app.config["AURA_GULF_PILOT_ENABLED"] = True
    with app.app_context():
        _sign_in(client, _make_user())
    _job(client)
    start, end = _set_up_slot(client)
    _verify_and_approve(client)
    _action(client, "hold", {
        "technician_id": "1", "bay": "1", "starts_at": start,
        "ends_at": end, "commission_ngn": "8000",
    })
    _dispatch(client)
    _action(client, "confirm", {"customer_confirmed": "1"})
    with app.app_context():
        row = GulfJob.query.one()
        assert row.state == "tentative"
        assert row.customer_confirmed is False
        assert row.labour_fee_kobo is None


def test_successful_end_to_end_with_private_evidence_and_partial_payments(app, client):
    app.config["AURA_GULF_PILOT_ENABLED"] = True
    _activate(app)
    store = FakeEvidenceStorage()
    app.extensions["evidence_storage_provider"] = store
    with app.app_context():
        _sign_in(client, _make_user())
    _job(client)
    start, end = _set_up_slot(client)
    _verify_and_approve(client)
    _action(client, "hold", {
        "technician_id": "1", "bay": "1", "starts_at": start,
        "ends_at": end, "commission_ngn": "8000",
    })
    _dispatch(client)
    _action(client, "confirm", {"customer_confirmed": "1"})
    with app.app_context():
        row = GulfJob.query.one()
        assert row.state == "confirmed"
        assert row.labour_fee_kobo == 2500000
        assert row.commission_kobo == 800000
    _action(client, "start")
    checks = {f"check_{i}": "observed_ok" for i in range(10)}
    _action(client, "complete", {
        **checks, "odometer": "85000",
        "oil_batch": "GULF-TEST-BATCH", "work_result": "Level checked; reset recorded.",
        "level_and_leak_checked": "1",
    })
    _action(client, "handover", {"handover_confirmed": "1"})
    _action(client, "claim")  # evidence missing: must NOT submit
    with app.app_context():
        assert GulfJob.query.one().claim_state == "unsubmitted"

    for kind in ("odometer", "sealed_oil", "filters"):
        resp = _post(client, "/admin/partners/gulf/jobs/1/photos", {
            "kind": kind,
            "image": (_image_bytes(), kind + ".jpg", "image/jpeg"),
        })
        assert resp.status_code == 200

    with app.app_context():
        assert GulfPhoto.query.count() == 3
    photo = client.get("/admin/partners/gulf/photos/1")
    assert photo.status_code == 200
    assert photo.mimetype == "image/jpeg"

    _action(client, "claim")
    _action(client, "claim")  # can't create second claim
    _action(client, "payment", {"amount_ngn": "10000", "reference": "BANK-1"})
    _action(client, "commission", {"amount_ngn": "8000", "reference": "WORKER-1"})
    with app.app_context():
        job = GulfJob.query.one()
        assert job.state == "handed_over"
        assert job.claim_state == "partial"
        assert job.paid_kobo == 1000000
        assert job.commission_paid_kobo == 800000
        assert GulfAudit.query.filter_by(job_id=job.id, action="claim").count() == 1
        assert GulfPayment.query.count() == 2
    _action(client, "payment", {"amount_ngn": "10000", "reference": "BANK-1"})
    with app.app_context():
        assert GulfJob.query.one().paid_kobo == 1000000
        assert GulfPayment.query.count() == 2
    _action(client, "payment", {"amount_ngn": "15000", "reference": "BANK-2"})
    with app.app_context():
        assert GulfJob.query.one().claim_state == "paid"
        assert GulfPayment.query.count() == 3
    export = client.get("/admin/partners/gulf/settlement.csv")
    assert export.status_code == 200
    assert "Pilot Guest" not in export.get_data(as_text=True)
    assert "GLF-LAG-001" not in export.get_data(as_text=True)


def test_workshop_existing_work_blocks_double_booking(app, client):
    app.config["AURA_GULF_PILOT_ENABLED"] = True
    with app.app_context():
        _sign_in(client, _make_user())
    _job(client)
    start, end = _set_up_slot(client)
    _verify_and_approve(client)
    _post(client, "/admin/partners/gulf/busy", {
        "technician_id": "1", "bay": "1",
        "starts_at": start, "ends_at": end,
        "reason": "Existing Ajebo Fix vehicle", "is_oil_change": "1",
    })
    _action(client, "hold", {
        "technician_id": "1", "bay": "1", "starts_at": start,
        "ends_at": end, "commission_ngn": "8000",
    })
    with app.app_context():
        assert GulfJob.query.one().state == "new"


def test_wrong_dispatch_sequence_does_not_mark_oil_received(app, client):
    app.config["AURA_GULF_PILOT_ENABLED"] = True
    with app.app_context():
        _sign_in(client, _make_user())
    _job(client)
    _verify_and_approve(client)
    _action(client, "dispatch", {
        "dispatch_state": "accepted", "reference": "PROHIBITED",
        "checked_product": "1",
    })
    with app.app_context():
        assert GulfJob.query.one().products_received_at is None
        assert GulfJob.query.one().dispatch_state == "not_requested"


def test_repeat_registration_is_flagged_without_exposing_plain_plate(app, client):
    app.config["AURA_GULF_PILOT_ENABLED"] = True
    with app.app_context():
        _sign_in(client, _make_user())
    _job(client, "001")
    _job(client, "002")
    with app.app_context():
        rows = GulfJob.query.order_by(GulfJob.id).all()
        assert len(rows) == 2
        assert rows[0].repeat_plate_review is False
        assert rows[1].repeat_plate_review is True
        assert rows[1].repeat_plate_cleared_at is None
        assert "SUR 123" not in rows[1].plate_digest
    _action(client, "clear_repeat", {"reference": "EBICE-REVIEW-002"}, job_id=2)
    with app.app_context():
        row = db.session.get(GulfJob, 2)
        assert row.repeat_plate_reference == "EBICE-REVIEW-002"
        assert row.repeat_plate_cleared_at is not None


def test_staff_can_release_old_external_work_without_touching_service_records(app, client):
    app.config["AURA_GULF_PILOT_ENABLED"] = True
    with app.app_context():
        _sign_in(client, _make_user())
    day = _next_monday().isoformat()
    _post(client, "/admin/partners/gulf/busy", {
        "bay": "1", "starts_at": day + "T10:00", "ends_at": day + "T11:30",
        "reason": "Existing Ajebo Fix vehicle", "is_oil_change": "1",
    })
    from partnerships.models import GulfBusySlot
    with app.app_context():
        assert GulfBusySlot.query.count() == 1
    r = _post(client, "/admin/partners/gulf/busy/1/release")
    assert r.status_code == 200
    with app.app_context():
        assert GulfBusySlot.query.count() == 0
        assert GulfAudit.query.filter_by(action="existing_work_block_released").count() == 1


@pytest.mark.skipif(
    not os.environ.get("GULF_CI_POSTGRES_URL"),
    reason="PostgreSQL locking integration requires disposable CI database",
)
def test_concurrent_bookings_cannot_claim_same_bay(app):
    """Two operators booking same bay must not both obtain reservations."""
    a, b = app.test_client(), app.test_client()
    with app.app_context():
        admin = _make_user()
        _sign_in(a, admin)
        _sign_in(b, admin)
    _job(a, "301")
    _job(a, "302")
    _action(a, "clear_repeat", {"reference": "EBICE-REVIEW-302"}, job_id=2)
    start, end = _set_up_slot(a)
    day = _next_monday()
    _post(a, "/admin/partners/gulf/day-plan", {
        "day": day.isoformat(), "daily_cap": "6",
        "concurrent_cap": "2", "reason": "Available staff and service positions verified",
    })
    _post(a, "/admin/partners/gulf/technicians", {
        "name": "Contractor B", "service_classes": ["car", "suv"],
        "brand_specialties": ["toyota"],
    })
    _post(a, "/admin/partners/gulf/shifts", {
        "technician_id": "2",
        "starts_at": day.isoformat() + "T09:00",
        "ends_at": day.isoformat() + "T16:00",
        "accepted": "1",
    })
    for job_id in (1, 2):
        _action(a, "verify", {"reference": f"EBICE-V-{job_id}"}, job_id=job_id)
        _action(a, "approve_oil", {
            "oil_spec": "Test approved oil spec",
            "oil_sku": "Approved SKU",
            "litres": "4.1", "filter_part": "TEST-FILTER",
            "human_approved": "1",
        }, job_id=job_id)

    barrier = Barrier(2)

    def reserve(client, job_id, technician_id):
        barrier.wait(timeout=10)
        response = _action(
            client, "hold",
            {
                "technician_id": str(technician_id), "bay": "1",
                "starts_at": start, "ends_at": end,
                "commission_ngn": "8000",
            },
            job_id=job_id,
        )
        return response.status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        f1 = pool.submit(reserve, a, 1, 1)
        f2 = pool.submit(reserve, b, 2, 2)
        assert f1.result(timeout=20) == 200
        assert f2.result(timeout=20) == 200

    with app.app_context():
        rows = GulfJob.query.order_by(GulfJob.id).all()
        assert sum(job.state == "tentative" for job in rows) == 1
        assert sum(job.state == "new" for job in rows) == 1
        assert all(job.state != "confirmed" for job in rows)


def test_gulf_mobile_dashboard_collapses_new_request_and_keeps_admin_controls(app, client):
    """The intake form is reachable without occupying the entire phone screen."""
    app.config["AURA_GULF_PILOT_ENABLED"] = True
    with app.app_context():
        _sign_in(client, _make_user())
    page = client.get("/admin/partners/gulf/")
    assert page.status_code == 200
    html = page.get_data(as_text=True)
    assert '<details class="gulf-card gulf-intake">' in html
    assert "New Gulf booking request" in html
    assert 'name="voucher"' in html
    assert 'name="csrf_token"' in html
    assert "Technician availability and capacity" in html
    assert ".gulf-wrap .gulf-card h2" in html


def test_gulf_mobile_job_card_uses_readable_scoped_heading_style(app, client):
    """The individual job card retains its fields with legible headings."""
    app.config["AURA_GULF_PILOT_ENABLED"] = True
    with app.app_context():
        _sign_in(client, _make_user())
    _job(client)
    page = client.get("/admin/partners/gulf/jobs/1")
    assert page.status_code == 200
    html = page.get_data(as_text=True)
    assert ".gulf-main .gulf-box h2" in html
    assert "Record Ebice voucher verification" in html
    assert "Technician and calendar reservation" in html
    assert "Private campaign photo evidence" in html


def test_technician_can_register_cars_suvs_and_mercedes_specialisation(app, client):
    app.config["AURA_GULF_PILOT_ENABLED"] = True
    with app.app_context():
        _sign_in(client, _make_user())
    result = _post(client, "/admin/partners/gulf/technicians", {
        "name": "Qualified Mercedes Technician",
        "phone": "+2348110000044",
        "service_classes": ["car", "suv"],
        "brand_specialties": ["mercedes"],
    })
    assert result.status_code == 200
    assert "Qualified Mercedes Technician" in result.get_data(as_text=True)
    with app.app_context():
        row = GulfTechnician.query.one()
        assert row.qualified_classes == "car,suv"
        assert row.brand_specialties == "mercedes"
        assert row.is_intern is False


def test_technician_form_rejects_invalid_categories_without_creating_worker(app, client):
    app.config["AURA_GULF_PILOT_ENABLED"] = True
    with app.app_context():
        _sign_in(client, _make_user())
    result = _post(client, "/admin/partners/gulf/technicians", {
        "name": "Incorrect List",
        "service_classes": ["car", "mercedes"],
        "brand_specialties": ["mercedes"],
    })
    assert "Select at least one service class" in result.get_data(as_text=True)
    with app.app_context():
        assert GulfTechnician.query.count() == 0


def test_brand_skill_mismatch_blocks_assignment_even_when_class_matches(app, client):
    app.config["AURA_GULF_PILOT_ENABLED"] = True
    with app.app_context():
        _sign_in(client, _make_user())
    _job(client)
    start, end = _set_up_slot(client)  # Toyota specialist
    _verify_and_approve(client)
    with app.app_context():
        row = GulfJob.query.one()
        row.make = "Mercedes-Benz"
        db.session.commit()
    response = _action(client, "hold", {
        "technician_id": "1", "bay": "1", "starts_at": start,
        "ends_at": end, "commission_ngn": "8000",
    })
    assert "lacks the recorded brand specialisation" in response.get_data(as_text=True)
    with app.app_context():
        assert GulfJob.query.one().state == "new"


def test_admin_technician_form_has_separate_brand_checkboxes(app, client):
    app.config["AURA_GULF_PILOT_ENABLED"] = True
    with app.app_context():
        _sign_in(client, _make_user())
    response = client.get("/admin/partners/gulf/")
    html = response.get_data(as_text=True)
    assert response.status_code == 200
    assert 'name="service_classes"' in html
    assert 'name="brand_specialties"' in html
    assert 'value="mercedes"' in html
    assert 'name="skills"' not in html


def test_pending_staff_window_requires_explicit_worker_acceptance(app, client):
    app.config["AURA_GULF_PILOT_ENABLED"] = True
    with app.app_context():
        _sign_in(client, _make_user())
    _job(client)
    future = _next_monday().isoformat()
    _post(client, "/admin/partners/gulf/technicians", {
        "name": "Flexible Contractor", "service_classes": ["car"],
        "brand_specialties": ["toyota"],
    })
    window = {
        "technician_id": "1",
        "starts_at": future + "T09:00",
        "ends_at": future + "T16:00",
    }
    r = _post(client, "/admin/partners/gulf/shifts", window)
    assert r.status_code == 200
    with app.app_context():
        shift = GulfShift.query.one()
        assert not shift.accepted
        sid = shift.id
    _verify_and_approve(client)
    attempt = _action(client, "hold", {
        "technician_id": "1", "bay": "1", "starts_at": future + "T10:00",
        "ends_at": future + "T11:30", "commission_ngn": "8000",
    })
    assert "has not explicitly accepted" in attempt.get_data(as_text=True)
    # Just clicking without confirmation must not grant availability.
    _post(client, f"/admin/partners/gulf/shifts/{sid}/accept", {})
    with app.app_context():
        assert not db.session.get(GulfShift, sid).accepted
    confirm = _post(client, f"/admin/partners/gulf/shifts/{sid}/accept", {
        "confirmed_with_worker": "1",
    })
    assert confirm.status_code == 200
    with app.app_context():
        assert db.session.get(GulfShift, sid).accepted


def test_staff_board_shows_pending_and_does_not_infer_availability(app, client):
    app.config["AURA_GULF_PILOT_ENABLED"] = True
    with app.app_context():
        _sign_in(client, _make_user())
    _post(client, "/admin/partners/gulf/technicians", {
        "name": "Unscheduled Worker", "service_classes": ["car"],
        "brand_specialties": ["toyota"],
    })
    page = client.get("/admin/partners/gulf/").get_data(as_text=True)
    assert "21-day workforce preparation" in page
    assert "Accepted lead technicians" in page
    assert "Awaiting response" in page
    assert "No qualified lead has accepted a work window" in page
    assert "No availability declarations" in page


def test_duplicate_technician_window_is_rejected(app, client):
    app.config["AURA_GULF_PILOT_ENABLED"] = True
    with app.app_context():
        _sign_in(client, _make_user())
    _post(client, "/admin/partners/gulf/technicians", {
        "name": "Flexible Worker", "service_classes": ["car"],
        "brand_specialties": ["mercedes"],
    })
    future = _next_monday().isoformat()
    form = {
        "technician_id": "1", "starts_at": future + "T09:00",
        "ends_at": future + "T16:00",
    }
    _post(client, "/admin/partners/gulf/shifts", form)
    response = _post(client, "/admin/partners/gulf/shifts", {
        **form, "starts_at": future + "T10:00",
    })
    assert "overlapping availability" in response.get_data(as_text=True)
    with app.app_context():
        assert GulfShift.query.count() == 1


def test_unaccepted_intern_does_not_count_as_staffing(app, client):
    app.config["AURA_GULF_PILOT_ENABLED"] = True
    with app.app_context():
        _sign_in(client, _make_user())
    _post(client, "/admin/partners/gulf/technicians", {
        "name": "Support Intern", "service_classes": ["car"],
        "brand_specialties": ["mercedes"], "is_intern": "1",
    })
    future = _next_monday().isoformat()
    _post(client, "/admin/partners/gulf/shifts", {
        "technician_id": "1", "starts_at": future + "T09:00",
        "ends_at": future + "T16:00", "accepted": "1",
    })
    page = client.get("/admin/partners/gulf/").get_data(as_text=True)
    assert "Accepted interns: 1" in page
    assert "No qualified lead has accepted a work window" in page


def test_readiness_defaults_are_visible_and_do_not_create_staff_shifts(app, client):
    app.config["AURA_GULF_PILOT_ENABLED"] = True
    with app.app_context():
        _sign_in(client, _make_user())
    response = client.get("/admin/partners/gulf/readiness")
    html = response.get_data(as_text=True)
    assert response.status_code == 200
    assert "Campaign preparation, not campaign activation" in html
    assert "Campaign labour pricing" in html
    assert "Sakura supplier and stock" in html
    assert "Sunday, 1 November 2026" in html
    assert "Customer confirmation remains disabled" in html
    with app.app_context():
        assert GulfReadinessRecord.query.count() == 0
        assert GulfShift.query.count() == 0
    home = client.get("/admin/partners/gulf/").get_data(as_text=True)
    assert "Open Pilot Readiness Dashboard" in home


def test_readiness_requires_evidence_and_human_attestation_for_confirmed(app, client):
    app.config["AURA_GULF_PILOT_ENABLED"] = True
    with app.app_context():
        _sign_in(client, _make_user())
    endpoint = "/admin/partners/gulf/readiness/agreement"
    r = _post(client, endpoint, {"status": "confirmed"})
    assert "requires a written reference" in r.get_data(as_text=True)
    r = _post(client, endpoint, {"status": "confirmed",
                                  "evidence_reference": "LETTER-10-2026"})
    assert "personally reviewed" in r.get_data(as_text=True)
    with app.app_context():
        assert GulfReadinessRecord.query.count() == 0
        assert GulfReadinessHistory.query.count() == 0
    r = _post(client, endpoint, {"status": "confirmed",
                                  "evidence_reference": "SIGNED-REFERENCE-1",
                                  "human_attested": "1"})
    assert r.status_code == 200
    with app.app_context():
        rec = GulfReadinessRecord.query.one()
        assert rec.key == "agreement"
        assert rec.status == "confirmed"
        assert GulfReadinessHistory.query.count() == 1
        assert not app.config.get("AURA_GULF_CAMPAIGN_ACTIVE", False)
        assert not app.config.get("AURA_GULF_PARTNER_SIGNED", False)
    view = client.get("/admin/partners/gulf/readiness").get_data(as_text=True)
    assert "Documented" in view
    assert "Signed partner approval flag" in view
    assert "Not authorised" in view


def test_readiness_revision_is_audited_without_touching_launch_controls(app, client):
    app.config["AURA_GULF_PILOT_ENABLED"] = True
    with app.app_context():
        _sign_in(client, _make_user())
    endpoint = "/admin/partners/gulf/readiness/labour_rates"
    _post(client, endpoint, {"status": "in_progress",
                             "internal_note": "Rates still need agreement"})
    _post(client, endpoint, {"status": "waiting_on_partner",
                             "internal_note": "Awaiting written response"})
    with app.app_context():
        rec = GulfReadinessRecord.query.one()
        assert rec.status == "waiting_on_partner"
        assert GulfReadinessHistory.query.count() == 2
        history = GulfReadinessHistory.query.order_by(GulfReadinessHistory.id).all()
        assert history[0].to_status == "in_progress"
        assert history[1].from_status == "in_progress"
        assert history[1].to_status == "waiting_on_partner"
        assert not app.config.get("AURA_GULF_RATES_APPROVED", False)


def test_readiness_bad_keys_and_non_admin_denied(app, client):
    app.config["AURA_GULF_PILOT_ENABLED"] = True
    with app.app_context():
        _sign_in(client, _make_user())
    r = _post(client, "/admin/partners/gulf/readiness/not_a_real_item",
              {"status": "confirmed", "evidence_reference": "made-up", "human_attested": "1"})
    assert "Unknown Gulf preparation requirement" in r.get_data(as_text=True)
    with app.app_context():
        assert GulfReadinessRecord.query.count() == 0


def test_readiness_notes_are_escaped_in_html(app, client):
    app.config["AURA_GULF_PILOT_ENABLED"] = True
    with app.app_context():
        _sign_in(client, _make_user())
    _post(client, "/admin/partners/gulf/readiness/dispatch", {
        "status": "in_progress", "internal_note": "<script>alert('x')</script>"
    })
    body = client.get("/admin/partners/gulf/readiness").get_data(as_text=True)
    assert "<script>alert('x')</script>" not in body
    assert "&lt;script&gt;" in body
