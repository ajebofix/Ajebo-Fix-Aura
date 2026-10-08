"""Owner repair journey reveals only advisor-reviewed progress publications."""
from __future__ import annotations

from extensions import db
from models import AdvisorNote
from services.client_repair_progress import (
    ClientProgressPublicationError,
    client_published_progress,
    publish_client_progress,
)
from services.repair_progress import record_repair_progress
from test_rina_chat_cutover import _car, _own, _sign_in, _user, _csrf_token


def _login_as(client, user):
    # Use the real session/logout pathway when switching test identities.
    with client.session_transaction() as sess:
        already_signed_in = bool(sess.get("_user_id"))
    if already_signed_in:
        result = client.post(
            "/auth/logout", data={"csrf_token": _csrf_token(client)}
        )
        assert result.status_code == 302
    _sign_in(client, user)


def _setup(suffix=611):
    owner = _user(suffix=suffix)
    advisor = _user(suffix=suffix + 1, role="admin")
    stranger = _user(suffix=suffix + 2)
    driver = _user(suffix=suffix + 3, role="driver")
    car = _car(suffix=suffix)
    _own(owner=owner, car=car, suffix=suffix)
    db.session.commit()
    return owner, advisor, stranger, driver, car


def _private_note(advisor, car):
    progress = record_repair_progress(
        actor_user_id=advisor.id,
        car_id=car.id,
        milestone="dismantling",
        source="manual",
        summary=(
            "Parts have been delivered and dismantling has commenced. "
            "Private vendor margin ₦888888 must not appear in owner view."
        ),
    )
    return progress.note_id


def test_progress_requires_advisor_approval_before_client_visibility(app, client):
    owner, advisor, other, driver, car = _setup(611)
    note_id = _private_note(advisor, car)
    _login_as(client, owner)
    response = client.get(f"/cars/{car.id}/repair-progress")
    assert response.status_code == 200
    assert b"No repair-progress statements have been published" in response.data
    assert b"Private vendor margin" not in response.data

    _login_as(client, advisor)
    response = client.post(
        f"/admin/cars/{car.id}/repair-progress/{note_id}/publish-to-client",
        data={
            "csrf_token": _csrf_token(client),
            "client_summary": "Replacement parts have arrived. Dismantling has begun.",
        },
    )
    assert response.status_code == 302
    _login_as(client, owner)
    response = client.get(f"/cars/{car.id}/repair-progress")
    assert response.status_code == 200
    assert b"Replacement parts have arrived" in response.data
    assert b"Dismantling" in response.data
    assert b"Private vendor margin" not in response.data
    assert b"888888" not in response.data
    assert b"Internal classification tags" not in response.data
    assert b"Delivered" not in response.data

    _login_as(client, advisor)
    revoke = client.post(
        f"/admin/cars/{car.id}/repair-progress/{note_id}/revoke-client",
        data={"csrf_token": _csrf_token(client)},
    )
    assert revoke.status_code == 302
    _login_as(client, owner)
    response = client.get(f"/cars/{car.id}/repair-progress")
    assert b"Replacement parts have arrived" not in response.data
    assert b"No repair-progress statements have been published" in response.data
    assert db.session.get(AdvisorNote, note_id) is not None


def test_unrelated_owner_driver_and_admin_do_not_get_client_route(app, client):
    owner, advisor, stranger, driver, car = _setup(621)
    note_id = _private_note(advisor, car)
    publish_client_progress(
        car_id=car.id,
        note_id=note_id,
        actor_user_id=advisor.id,
        client_summary="Parts received; vehicle dismantling is underway.",
    )
    _login_as(client, stranger)
    assert client.get(f"/cars/{car.id}/repair-progress").status_code == 404
    _login_as(client, driver)
    assert client.get(f"/cars/{car.id}/repair-progress").status_code == 403
    _login_as(client, advisor)
    assert client.get(f"/cars/{car.id}/repair-progress").status_code == 403


def test_non_advisor_cannot_publish_and_unverified_owner_is_denied(app, client):
    owner, advisor, stranger, driver, car = _setup(631)
    note_id = _private_note(advisor, car)
    _login_as(client, owner)
    assert client.post(
        f"/admin/cars/{car.id}/repair-progress/{note_id}/publish-to-client",
        data={
            "csrf_token": _csrf_token(client),
            "client_summary": "Parts received and dismantling has started.",
        },
    ).status_code == 403
    owner.email_verified_at = None
    db.session.commit()
    assert client.get(f"/cars/{car.id}/repair-progress").status_code == 403


def test_source_car_mismatch_and_empty_summary_are_rejected(app):
    owner, advisor, stranger, driver, car = _setup(641)
    note_id = _private_note(advisor, car)
    another = _car(suffix=644)
    db.session.commit()
    try:
        publish_client_progress(
            car_id=another.id,
            note_id=note_id,
            actor_user_id=advisor.id,
            client_summary="This should never be released.",
        )
    except ClientProgressPublicationError:
        pass
    else:
        raise AssertionError("Cross-car repair progress must not be approved")

    try:
        publish_client_progress(
            car_id=car.id,
            note_id=note_id,
            actor_user_id=advisor.id,
            client_summary="",
        )
    except ClientProgressPublicationError:
        pass
    else:
        raise AssertionError("Empty client publication must not be accepted")
    assert client_published_progress(car_id=car.id) == []


def test_source_change_invalidates_existing_client_publication(app):
    owner, advisor, stranger, driver, car = _setup(651)
    note_id = _private_note(advisor, car)
    publish_client_progress(
        car_id=car.id,
        note_id=note_id,
        actor_user_id=advisor.id,
        client_summary="Dismantling is currently underway.",
    )
    assert len(client_published_progress(car_id=car.id)) == 1
    db.session.get(AdvisorNote, note_id).note += "Changed private source"
    db.session.commit()
    assert client_published_progress(car_id=car.id) == []
